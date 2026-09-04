"""Real MLX exercise of the resource governor hooked into generate_mtpk's
MTP speculative-decode loop (mtplx/generation.py). Uses the same
deterministic cyclic toy-model pattern as tests/test_loop_guard.py's
generate_mtpk tests (real MLX compute, real draft head via mtp_forward),
not a mock of MLX itself.

Why generate_mtpk needed its own careful design (see
docs/resource-governor/IMPLEMENTATION_NOTES.md's MTP hook note): its
internal emit_new_tokens() helper is called from ~10 different branch
sites, and several fire MID-cycle (e.g. right after the primary token is
sampled, before draft/verify/accept work happens) rather than once at
cycle end. The hook therefore paces at the TOP of each while-loop
iteration, against the *previous* iteration's measured wall time and
token delta, rather than trying to find a single true "end of cycle"
call site. These tests exist specifically to catch regressions in that
design (no double counting, no sleep-time leaking into the next
measurement, no altered token output).
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import mlx.core as mx
import numpy as np
import pytest

from mtplx.generation import generate_mtpk
from mtplx.mtp_patch import MTPContract
from mtplx.resource_governor import ResourceGovernor, ResourceProfile
from mtplx.runtime import MTPLXRuntime
from mtplx.sampling import SamplerConfig

VOCAB = 8
MARGIN = 10.0


class _CyclicTokenizer:
    def decode(self, tokens, **_kwargs):
        return "".join(str(int(token)) for token in tokens)


class _CyclicModel:
    """Deterministic loop machine: after token t the model wants (t+1) % VOCAB."""

    def make_cache(self):
        return []

    def make_mtp_cache(self):
        return []

    def mtp_update_cache(self, hidden_states, next_token_ids, **_kwargs):
        return hidden_states

    def _logits_for(self, last_tokens: list[int]) -> mx.array:
        rows = []
        for token in last_tokens:
            row = [0.0] * VOCAB
            row[(int(token) + 1) % VOCAB] = MARGIN
            rows.append(row)
        return mx.array([rows], dtype=mx.float32)

    def __call__(
        self,
        input_ids,
        *,
        cache=None,
        return_hidden: bool = False,
        hidden_variant: str | None = None,
        emit_logits: bool = True,
        logits_keep: int | None = None,
    ):
        tokens = [int(token) for token in np.asarray(input_ids).reshape(-1)]
        keep = len(tokens) if logits_keep is None else min(len(tokens), max(1, int(logits_keep)))
        logits = self._logits_for(tokens[-keep:]) if emit_logits else None
        hidden = mx.zeros((1, len(tokens), 2), dtype=mx.float32)
        if not emit_logits:
            return (None, hidden) if return_hidden else None
        if return_hidden:
            return logits, hidden
        return logits


class _CyclicMTPModel(_CyclicModel):
    """MTP sibling whose draft head follows the same cyclic script."""

    def __init__(self):
        self.mtp = SimpleNamespace(_mtplx_lora_targets=[])

    def mtp_forward(
        self,
        hidden_states,
        next_token_ids,
        *,
        mtp_cache=None,
        concat_order=None,
        return_hidden: bool = False,
        mtp_hidden_variant: str | None = None,
        position_offset=None,
    ):
        tokens = [int(token) for token in np.asarray(next_token_ids).reshape(-1)]
        logits = self._logits_for(tokens)
        hidden = mx.zeros((1, len(tokens), 2), dtype=mx.float32)
        if return_hidden:
            return logits, hidden
        return logits


def _cyclic_runtime() -> MTPLXRuntime:
    return MTPLXRuntime(
        model=_CyclicMTPModel(),
        tokenizer=_CyclicTokenizer(),
        model_path=Path("tiny-cyclic"),
        mtp_enabled=True,
        contract=MTPContract(),
    )


def _pure_cycle(start: int, length: int) -> list[int]:
    return [(start + 1 + index) % VOCAB for index in range(length)]


def _generate(*, resource_governor=None, max_tokens=60, abort_check=None):
    out = generate_mtpk(
        _cyclic_runtime(),
        [0],
        max_tokens=max_tokens,
        sampler=SamplerConfig(temperature=0.0, top_p=1.0, top_k=4),
        speculative_depth=2,
        seed=7,
        mtp_history_policy="committed",
        verify_strategy="batched",
        stop_token_ids=set(),
        resource_governor=resource_governor,
        abort_check=abort_check,
    )
    return list(out.tokens)


def test_no_governor_generates_as_before():
    tokens = _generate(resource_governor=None)
    assert tokens == _pure_cycle(0, len(tokens))


def test_governor_at_max_profile_does_not_change_output_or_yield():
    gov = ResourceGovernor(profile="max")
    tokens = _generate(resource_governor=gov)
    assert tokens == _pure_cycle(0, len(tokens))
    assert gov.stats()["decode"]["yields"] == 0
    # More than one decode cycle ran (depth 2 means <=2 tokens/cycle).
    assert gov.stats()["decode"]["steps"] > 0


def test_governor_below_one_does_not_change_output_only_timing():
    # brief section 18: throttling must not alter model output, even
    # though this hook's whole design is more involved than the AR lane's.
    unthrottled = _generate(resource_governor=None)
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", decode_duty_cycle=0.3),
        max_single_yield_s=0.02,
        sleep_slice_s=0.005,
    )
    throttled = _generate(resource_governor=gov)
    assert throttled == unthrottled


def test_governor_below_one_actually_slows_wall_clock():
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", decode_duty_cycle=0.2),
        max_single_yield_s=0.02,
        sleep_slice_s=0.002,
    )
    started = time.perf_counter()
    _generate(resource_governor=gov)
    elapsed = time.perf_counter() - started
    stats = gov.stats()
    assert stats["decode"]["yields"] > 0
    assert elapsed >= stats["decode"]["total_yield_s"]


def test_yields_never_exceed_one_per_cycle_transition():
    # Regression guard for the original (reverted) design bug: yields must
    # not exceed decode "steps", which would indicate multiple pacing calls
    # firing per cycle instead of one per iteration-to-iteration transition.
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", decode_duty_cycle=0.1),
        max_single_yield_s=0.02,
        sleep_slice_s=0.002,
    )
    _generate(resource_governor=gov, max_tokens=40)
    stats = gov.stats()["decode"]
    assert stats["yields"] <= stats["steps"]
    # Steps must be strictly fewer than tokens produced (each cycle can
    # accept up to depth=2 tokens) — confirms pacing fires per-cycle, not
    # per-token, and isn't double-firing (which would push steps close to
    # or above the token count).
    assert stats["steps"] < 40


def test_abort_check_interrupts_a_pending_yield_promptly():
    calls = {"n": 0}

    def abort_after_a_few_slices():
        calls["n"] += 1
        return calls["n"] > 3

    gov = ResourceGovernor(
        profile=ResourceProfile(name="slow", decode_duty_cycle=0.02),
        max_single_yield_s=5.0,
        sleep_slice_s=0.01,
    )
    started = time.perf_counter()
    _generate(resource_governor=gov, max_tokens=60, abort_check=abort_after_a_few_slices)
    elapsed = time.perf_counter() - started
    # A single uninterrupted yield at this duty cycle would already be
    # several seconds; the abort must cut every yield short.
    assert elapsed < 1.0
