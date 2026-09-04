"""Real MLX exercise of the resource governor hooked into the two prefill
paths Phase 2 didn't originally cover: _prefill_committed_mtp_history_streaming
(the real MTP-committed-history default prefill path — see
docs/resource-governor/IMPLEMENTATION_NOTES.md section 5's follow-up note)
and _prefill_restored_prompt_suffix (warm SessionBank restore). Reuses the
exact fixture/mocking patterns tests/test_generation_sustained.py already
proves work for these two functions, adding only the resource_governor
wiring under test.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import mlx.core as mx
import pytest

from mtplx.generation import (
    _prefill_committed_mtp_history_streaming,
    restore_or_prefill_prompt_state,
)
from mtplx.mtp_patch import MTPContract
from mtplx.resource_governor import ResourceGovernor, ResourceProfile
from mtplx.runtime import MTPLXRuntime
from mtplx.sampling import SamplerConfig


class TinyTokenizer:
    def decode(self, tokens, **_kwargs):
        return "".join(str(int(token)) for token in tokens)


class TinyModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def make_cache(self):
        return []

    def make_mtp_cache(self):
        return []

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
        self.calls.append(
            {
                "tokens": int(input_ids.shape[1]),
                "return_hidden": bool(return_hidden),
                "emit_logits": bool(emit_logits),
            }
        )
        length = int(input_ids.shape[1])
        hidden = mx.zeros((1, length, 2), dtype=mx.float32)
        if not emit_logits:
            return (None, hidden) if return_hidden else None
        keep = length if logits_keep is None else min(length, max(1, int(logits_keep)))
        logits = mx.zeros((1, keep, 4), dtype=mx.float32) + mx.array(
            [0.0, 1.0, 0.0, 0.0], dtype=mx.float32
        )
        return (logits, hidden) if return_hidden else logits


def _runtime(model: TinyModel) -> MTPLXRuntime:
    return MTPLXRuntime(
        model=model,
        tokenizer=TinyTokenizer(),
        model_path=Path("tiny"),
        mtp_enabled=True,
        contract=MTPContract(),
    )


# ---- _prefill_committed_mtp_history_streaming ---------------------------
# This is the prefill path a real default-configured MTP request actually
# takes (mtp_history_policy="committed" + MTPLX_SUSTAINED_PREFILL, both true
# under the shipped "sustained" default profile) — see PROJECT_STATUS.md's
# MTP-hook entry for how this was discovered missing after the initial MTP
# decode hook.


def _run_committed_history_streaming(monkeypatch, *, resource_governor=None):
    monkeypatch.setenv("MTPLX_SUSTAINED_PREFILL", "1")
    monkeypatch.setenv("MTPLX_PREFILL_CHUNK_SIZE", "2")
    monkeypatch.setattr(
        "mtplx.generation._append_mtp_history", lambda *_a, **_k: 0.0
    )
    model = TinyModel()
    rt = _runtime(model)
    result = _prefill_committed_mtp_history_streaming(
        rt,
        [10, 11, 12, 13, 14],
        resource_governor=resource_governor,
    )
    return result, model


def test_committed_history_streaming_no_governor_unaffected(monkeypatch):
    (cache, logits, hidden, mtp_cache, target_time, history_time, base), model = (
        _run_committed_history_streaming(monkeypatch, resource_governor=None)
    )
    assert [call["tokens"] for call in model.calls] == [2, 2, 1]


def test_committed_history_streaming_paces_and_reports_steps(monkeypatch):
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", prefill_duty_cycle=0.3),
        max_single_yield_s=0.02,
        sleep_slice_s=0.005,
    )
    (result, model) = _run_committed_history_streaming(
        monkeypatch, resource_governor=gov
    )
    # Same chunking as the ungoverned baseline: pacing must not change what
    # gets forwarded, only add wall-clock yields between chunks.
    assert [call["tokens"] for call in model.calls] == [2, 2, 1]
    stats = gov.stats()["prefill"]
    assert stats["steps"] == 2  # two body chunks in the streaming loop
    assert stats["yields"] > 0


def test_committed_history_streaming_max_profile_is_a_no_op(monkeypatch):
    gov = ResourceGovernor(profile="max")
    _run_committed_history_streaming(monkeypatch, resource_governor=gov)
    assert gov.stats()["prefill"]["yields"] == 0
    assert gov.stats()["prefill"]["steps"] == 2


def test_committed_history_streaming_actually_slows_wall_clock(monkeypatch):
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", prefill_duty_cycle=0.2),
        max_single_yield_s=0.02,
        sleep_slice_s=0.005,
    )
    started = time.perf_counter()
    _run_committed_history_streaming(monkeypatch, resource_governor=gov)
    elapsed = time.perf_counter() - started
    stats = gov.stats()["prefill"]
    assert stats["yields"] > 0
    assert elapsed >= stats["total_yield_s"]


# ---- _prefill_restored_prompt_suffix (warm SessionBank restore) ---------


class _Bank:
    last_miss_reason = None

    def restore(self, *_args, **_kwargs):
        return SimpleNamespace(
            entry=SimpleNamespace(prefix_len=3),
            cache=[],
            logits=mx.zeros((1, 4), dtype=mx.float32),
            hidden=mx.zeros((1, 1, 2), dtype=mx.float32),
            mtp_history_cache=[],
            restore_mode="clone",
        )


def _run_warm_restore(monkeypatch, *, resource_governor=None):
    monkeypatch.setenv("MTPLX_SMALL_SUFFIX_FUSED_MAX", "0")  # force the chunked lane
    monkeypatch.setenv("MTPLX_SUSTAINED_PREFILL", "1")
    monkeypatch.setenv("MTPLX_PREFILL_CHUNK_SIZE", "2")
    monkeypatch.setattr(
        "mtplx.generation._append_mtp_history", lambda *_a, **_k: 0.0
    )
    model = TinyModel()
    rt = _runtime(model)
    prompt_state = restore_or_prefill_prompt_state(
        rt,
        [0, 1, 2, 3, 4, 5, 6],
        mtp_history_policy="committed",
        session_bank=_Bank(),
        resource_governor=resource_governor,
    )
    return prompt_state, model


def test_warm_restore_no_governor_unaffected(monkeypatch):
    prompt_state, model = _run_warm_restore(monkeypatch, resource_governor=None)
    assert prompt_state.cache_hit is True
    assert prompt_state.suffix_tokens == 4
    assert [call["tokens"] for call in model.calls] == [2, 1, 1]


def test_warm_restore_paces_and_reports_steps(monkeypatch):
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", prefill_duty_cycle=0.3),
        max_single_yield_s=0.02,
        sleep_slice_s=0.005,
    )
    prompt_state, model = _run_warm_restore(monkeypatch, resource_governor=gov)
    assert prompt_state.cache_hit is True
    assert [call["tokens"] for call in model.calls] == [2, 1, 1]
    stats = gov.stats()["prefill"]
    # Two governed steps for the two body chunks (matches the reference
    # test's restored_suffix_prefill_chunks == 2); the final 1-token
    # forward is deliberately left unhooked, matching plain _prefill()'s
    # own precedent of not pacing its small fixed-cost final-token step.
    assert stats["steps"] == 2
    assert stats["yields"] > 0


def test_warm_restore_max_profile_is_a_no_op(monkeypatch):
    gov = ResourceGovernor(profile="max")
    _run_warm_restore(monkeypatch, resource_governor=gov)
    assert gov.stats()["prefill"]["yields"] == 0


def test_warm_restore_actually_slows_wall_clock(monkeypatch):
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", prefill_duty_cycle=0.15),
        max_single_yield_s=0.02,
        sleep_slice_s=0.005,
    )
    started = time.perf_counter()
    _run_warm_restore(monkeypatch, resource_governor=gov)
    elapsed = time.perf_counter() - started
    stats = gov.stats()["prefill"]
    assert stats["yields"] > 0
    assert elapsed >= stats["total_yield_s"]


# ---- fused (single-shot) warm-restore path -------------------------------


def _run_warm_restore_fused(monkeypatch, *, resource_governor=None):
    # Default MTPLX_SMALL_SUFFIX_FUSED_MAX is large enough that a 4-token
    # suffix fuses into one forward instead of the chunked lane above.
    monkeypatch.setenv("MTPLX_SUSTAINED_PREFILL", "1")
    monkeypatch.setattr(
        "mtplx.generation._append_mtp_history", lambda *_a, **_k: 0.0
    )
    model = TinyModel()
    rt = _runtime(model)
    prompt_state = restore_or_prefill_prompt_state(
        rt,
        [0, 1, 2, 3, 4, 5, 6],
        mtp_history_policy="committed",
        session_bank=_Bank(),
        resource_governor=resource_governor,
    )
    return prompt_state, model


def test_warm_restore_fused_path_paces_as_one_unit(monkeypatch):
    gov = ResourceGovernor(
        profile=ResourceProfile(name="throttled", prefill_duty_cycle=0.3),
        max_single_yield_s=0.02,
        sleep_slice_s=0.005,
    )
    prompt_state, model = _run_warm_restore_fused(monkeypatch, resource_governor=gov)
    assert prompt_state.cache_hit is True
    # Fused lane: exactly one forward call for the whole 4-token suffix.
    assert len(model.calls) == 1
    stats = gov.stats()["prefill"]
    assert stats["steps"] == 1
    assert stats["yields"] > 0
