"""Real MLX exercise of the resource governor hooked into _prefill's chunked
prefill loop (mtplx/generation.py), reached via generate_ar's cold-prefill
path (mtplx/generation.py:restore_or_prefill_prompt_state -> _prefill).
Same toy-model pattern as test_resource_governor_ar_integration.py and the
existing tests/test_async_decode.py.
"""

from __future__ import annotations

import time
from pathlib import Path

import mlx.core as mx
import pytest

from mtplx.generation import generate_ar, prefill_chunk_size_override
from mtplx.mtp_patch import MTPContract
from mtplx.resource_governor import ResourceGovernor, ResourceProfile
from mtplx.runtime import MTPLXRuntime
from mtplx.sampling import SamplerConfig


class TinyTokenizer:
    def decode(self, tokens, **_kwargs):
        return "".join(str(int(token)) for token in tokens)


class TinyModel:
    """Deterministic toy model: logits always favor token index 1."""

    def make_cache(self):
        return []

    def make_mtp_cache(self):
        return []

    def sanitize(self, weights):
        return weights

    def __call__(
        self,
        input_ids,
        *,
        cache=None,
        return_hidden: bool = False,
        hidden_variant: str | None = None,
        emit_logits: bool = True,
        logits_keep: int | None = None,
        input_embeddings=None,
    ):
        length = int(input_ids.shape[1])
        hidden = mx.zeros((1, length, 2), dtype=mx.float32)
        if not emit_logits:
            return (None, hidden) if return_hidden else None
        keep = length if logits_keep is None else min(length, max(1, int(logits_keep)))
        logits = mx.zeros((1, keep, 4), dtype=mx.float32) + mx.array(
            [0.0, 1.0, 0.0, 0.0], dtype=mx.float32
        )
        return (logits, hidden) if return_hidden else logits


def _make_runtime() -> MTPLXRuntime:
    return MTPLXRuntime(
        model=TinyModel(),
        tokenizer=TinyTokenizer(),
        model_path=Path("tiny"),
        mtp_enabled=False,
        contract=MTPContract(),
    )


# A 9-token prompt with a forced chunk size of 2 makes _prefill's chunk loop
# (over the 8-token body) run 4 separate chunks, so the governor hook fires
# more than once per prefill.
_PROMPT = list(range(9))


def _generate(monkeypatch, *, resource_governor=None, chunk_size=2, max_tokens=3):
    # Chunked prefill is gated behind MTPLX_SUSTAINED_PREFILL (off by
    # default) — see generation.py:_iter_prefill_chunk_spans, which returns
    # a single span for the whole body unless this is set, regardless of
    # any chunk_size override. Without it a governor test can only ever
    # observe one prefill "chunk" (the whole prompt).
    monkeypatch.setenv("MTPLX_SUSTAINED_PREFILL", "1")
    rt = _make_runtime()
    with prefill_chunk_size_override(chunk_size):
        out = generate_ar(
            rt,
            _PROMPT,
            max_tokens=max_tokens,
            sampler=SamplerConfig(temperature=0.0, top_p=1.0, top_k=4),
            stop_token_ids=set(),
            resource_governor=resource_governor,
        )
    return out.tokens


def test_no_governor_generates_as_before(monkeypatch):
    assert _generate(monkeypatch, resource_governor=None) == [1, 1, 1]


def test_governor_at_max_profile_does_not_change_output_or_yield(monkeypatch):
    gov = ResourceGovernor(profile="max")
    assert _generate(monkeypatch, resource_governor=gov) == [1, 1, 1]
    assert gov.stats()["prefill"]["yields"] == 0


def test_multi_chunk_prefill_paces_more_than_once(monkeypatch):
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", prefill_duty_cycle=0.3),
        max_single_yield_s=0.05,
        sleep_slice_s=0.005,
    )
    tokens = _generate(monkeypatch, resource_governor=gov)
    assert tokens == [1, 1, 1]
    stats = gov.stats()
    # 8-token body / chunk_size=2 = 4 chunks -> up to 4 pacing calls.
    assert stats["prefill"]["steps"] == 4
    assert stats["prefill"]["yields"] > 0


def test_prefill_pacing_does_not_change_output(monkeypatch):
    unthrottled = _generate(monkeypatch, resource_governor=None)
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", prefill_duty_cycle=0.25),
        max_single_yield_s=0.05,
        sleep_slice_s=0.005,
    )
    throttled = _generate(monkeypatch, resource_governor=gov)
    assert throttled == unthrottled


def test_prefill_pacing_actually_slows_wall_clock(monkeypatch):
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", prefill_duty_cycle=0.2),
        max_single_yield_s=0.05,
        sleep_slice_s=0.005,
    )
    started = time.perf_counter()
    _generate(monkeypatch, resource_governor=gov)
    elapsed = time.perf_counter() - started
    stats = gov.stats()
    assert stats["prefill"]["yields"] > 0
    assert elapsed >= stats["prefill"]["total_yield_s"]


def test_single_token_prompt_never_enters_chunk_loop(monkeypatch):
    # len(prompt_ids) == 1 skips _prefill's chunk loop entirely (no body) —
    # the governor must simply see zero prefill steps, not error.
    monkeypatch.setenv("MTPLX_SUSTAINED_PREFILL", "1")
    gov = ResourceGovernor(profile=ResourceProfile(name="p", prefill_duty_cycle=0.5))
    rt = _make_runtime()
    out = generate_ar(
        rt,
        [0],
        max_tokens=2,
        sampler=SamplerConfig(temperature=0.0, top_p=1.0, top_k=4),
        stop_token_ids=set(),
        resource_governor=gov,
    )
    assert out.tokens == [1, 1]
    assert gov.stats()["prefill"]["steps"] == 0
