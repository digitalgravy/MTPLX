"""Real MLX exercise of the resource governor hooked into generate_ar's
MTPLX_AR_PIPELINE lane (mtplx/generation.py), a different code path from
the classic AR loop tests/test_resource_governor_ar_integration.py already
covers. Unlike tests/test_async_decode.py's pipeline test (which
deliberately refuses engagement via set_ar_pipeline_mode returning False,
so it exercises the *gating*, not the lane itself), this model accepts
engagement so the pipelined while-loop this hook lives in actually runs.
"""

from __future__ import annotations

import time
from pathlib import Path

import mlx.core as mx
import pytest

from mtplx.generation import generate_ar
from mtplx.mtp_patch import MTPContract
from mtplx.resource_governor import ResourceGovernor, ResourceProfile
from mtplx.runtime import MTPLXRuntime
from mtplx.sampling import SamplerConfig


@pytest.fixture(autouse=True)
def _arm_pipeline_lane(monkeypatch):
    monkeypatch.setenv("MTPLX_AR_PIPELINE", "1")


class TinyTokenizer:
    def decode(self, tokens, **_kwargs):
        return "".join(str(int(token)) for token in tokens)


class PipelineTinyModel:
    """Logits favor token index 1; accepts MTPLX_AR_PIPELINE engagement."""

    def __init__(self) -> None:
        self.pipeline_mode_calls: list[bool] = []

    def make_cache(self):
        return []

    def sanitize(self, weights):
        return weights

    def set_ar_pipeline_mode(self, value: bool) -> bool:
        self.pipeline_mode_calls.append(bool(value))
        return True

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
        length = int(input_ids.shape[1])
        hidden = mx.zeros((1, length, 2), dtype=mx.float32)
        if not emit_logits:
            return (None, hidden) if return_hidden else None
        keep = length if logits_keep is None else min(length, max(1, int(logits_keep)))
        logits = mx.zeros((1, keep, 4), dtype=mx.float32) + mx.array(
            [0.0, 4.0, 0.0, 0.0], dtype=mx.float32
        )
        return (logits, hidden) if return_hidden else logits


def _make_runtime(model: PipelineTinyModel) -> MTPLXRuntime:
    return MTPLXRuntime(
        model=model,
        tokenizer=TinyTokenizer(),
        model_path=Path("tiny-pipeline"),
        mtp_enabled=False,
        contract=MTPContract(),
    )


_SAMPLER = SamplerConfig(temperature=0.7, top_p=1.0, top_k=2)


def _generate(*, resource_governor=None, max_tokens=10, seed=7, abort_check=None):
    model = PipelineTinyModel()
    rt = _make_runtime(model)
    out = generate_ar(
        rt,
        [0],
        max_tokens=max_tokens,
        sampler=_SAMPLER,
        seed=seed,
        stop_token_ids=set(),
        resource_governor=resource_governor,
        abort_check=abort_check,
    )
    return list(out.tokens), model


def _lane_engaged(model: PipelineTinyModel) -> bool:
    # set_ar_pipeline_mode(True) to engage, then (False) on teardown — both
    # only happen if the pipelined while-loop actually ran, not just the
    # gating check (mirrors tests/test_async_decode.py's own signal for
    # this, but that test deliberately returns False to test gating alone;
    # here engagement is real).
    return model.pipeline_mode_calls == [True, False]


def test_pipeline_lane_actually_engages_in_this_fixture():
    # Sanity check on the fixture itself: if this ever stops engaging the
    # lane (e.g. a gating condition changes upstream), every other test in
    # this file would silently start testing the classic loop instead.
    _, model = _generate(resource_governor=None)
    assert _lane_engaged(model)


def test_no_governor_generates_as_before():
    tokens_a, _ = _generate(resource_governor=None)
    tokens_b, _ = _generate(resource_governor=None)
    assert tokens_a == tokens_b  # same seed -> deterministic


def test_governor_at_max_profile_does_not_change_output_or_yield():
    gov = ResourceGovernor(profile="max")
    unthrottled, _ = _generate(resource_governor=None)
    governed, model = _generate(resource_governor=gov)
    assert governed == unthrottled
    assert _lane_engaged(model)
    assert gov.stats()["decode"]["yields"] == 0
    assert gov.stats()["decode"]["steps"] > 0


def test_governor_below_one_does_not_change_output_only_timing():
    unthrottled, _ = _generate(resource_governor=None)
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", decode_duty_cycle=0.3),
        max_single_yield_s=0.02,
        sleep_slice_s=0.005,
    )
    throttled, model = _generate(resource_governor=gov)
    assert throttled == unthrottled
    assert _lane_engaged(model)


def test_governor_below_one_actually_slows_wall_clock():
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", decode_duty_cycle=0.2),
        max_single_yield_s=0.02,
        sleep_slice_s=0.002,
    )
    started = time.perf_counter()
    _generate(resource_governor=gov, max_tokens=12)
    elapsed = time.perf_counter() - started
    stats = gov.stats()
    assert stats["decode"]["yields"] > 0
    assert elapsed >= stats["decode"]["total_yield_s"]


def test_steps_track_tokens_one_to_one():
    # Unlike generate_mtpk's cycles, the pipelined lane commits exactly one
    # token per governed step — confirms the simpler direct-hook design.
    gov = ResourceGovernor(profile=ResourceProfile(name="p", decode_duty_cycle=0.5))
    tokens, _ = _generate(resource_governor=gov, max_tokens=10)
    stats = gov.stats()["decode"]
    # First token is sampled outside the governed loop (primer token before
    # the pipeline's while-loop starts), so steps == committed tokens - 1.
    assert stats["steps"] == len(tokens) - 1


def test_abort_check_interrupts_a_pending_yield_promptly():
    calls = {"n": 0}

    def abort_after_a_few_slices():
        calls["n"] += 1
        # Prefill's own _check_postcommit_abort calls abort_check a few
        # times before decode even starts (and *raises* if it returns True
        # there, unlike the governor's yield loop, which just returns) —
        # the threshold must clear that fixed handful of calls first, or
        # this test would fail on a prefill abort instead of exercising a
        # governor yield at all.
        return calls["n"] > 8

    gov = ResourceGovernor(
        profile=ResourceProfile(name="slow", decode_duty_cycle=0.02),
        max_single_yield_s=5.0,
        sleep_slice_s=0.01,
    )
    started = time.perf_counter()
    _generate(resource_governor=gov, max_tokens=12, abort_check=abort_after_a_few_slices)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
