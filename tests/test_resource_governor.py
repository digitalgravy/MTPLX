from __future__ import annotations

import time

import pytest

from mtplx.resource_governor import (
    BUILTIN_PROFILES,
    ResourceGovernor,
    ResourceProfile,
    resolve_profile,
)


# ---- ResourceProfile validation ---------------------------------------


def test_duty_cycle_must_be_in_zero_exclusive_one_inclusive():
    ResourceProfile(name="ok", prefill_duty_cycle=1.0, decode_duty_cycle=0.01)
    for bad in (0.0, -0.1, 1.1, 2.0):
        with pytest.raises(ValueError):
            ResourceProfile(name="bad", decode_duty_cycle=bad)
        with pytest.raises(ValueError):
            ResourceProfile(name="bad", prefill_duty_cycle=bad)


def test_min_decode_tps_must_be_nonnegative_or_none():
    ResourceProfile(name="ok", min_decode_tps=None)
    ResourceProfile(name="ok", min_decode_tps=0)
    with pytest.raises(ValueError):
        ResourceProfile(name="bad", min_decode_tps=-1)


def test_resolve_profile_unknown_name_raises():
    with pytest.raises(ValueError):
        resolve_profile("nonexistent")


def test_builtin_max_profile_has_no_intentional_pacing():
    profile = BUILTIN_PROFILES["max"]
    assert profile.prefill_duty_cycle == 1.0
    assert profile.decode_duty_cycle == 1.0


def test_builtin_protect_and_pause_refuse_admission():
    assert BUILTIN_PROFILES["protect"].admission_allowed is False
    assert BUILTIN_PROFILES["pause"].admission_allowed is False
    assert BUILTIN_PROFILES["max"].admission_allowed is True


# ---- ResourceGovernor: profile handling --------------------------------


def test_default_governor_starts_on_max_profile():
    gov = ResourceGovernor()
    assert gov.current_profile().name == "max"


def test_set_profile_accepts_name_or_object():
    gov = ResourceGovernor()
    gov.set_profile("interactive")
    assert gov.current_profile().name == "interactive"
    custom = ResourceProfile(name="custom", decode_duty_cycle=0.5)
    gov.set_profile(custom)
    assert gov.current_profile() is custom


def test_admission_allowed_reflects_profile_and_enabled_flag():
    gov = ResourceGovernor(profile="protect")
    assert gov.admission_allowed() is False
    gov.set_profile("max")
    assert gov.admission_allowed() is True
    gov.set_profile("protect")
    gov.set_enabled(False)
    # A disabled governor must not itself block admission.
    assert gov.admission_allowed() is True


# ---- duty-cycle math: disabled / duty=1.0 is a true no-op --------------


def test_duty_cycle_one_never_sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    gov = ResourceGovernor(profile="max")
    gov.after_decode_step(work_seconds=0.01, produced_tokens=1)
    assert calls == []


def test_disabled_governor_never_sleeps_even_on_low_duty_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    gov = ResourceGovernor(profile="interactive", enabled=False)
    gov.after_decode_step(work_seconds=0.01, produced_tokens=1)
    assert calls == []


def test_zero_work_seconds_never_sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    gov = ResourceGovernor(profile="interactive")
    gov.after_decode_step(work_seconds=0.0, produced_tokens=1)
    assert calls == []


# ---- duty-cycle math: correctness of the sleep formula -----------------


def test_sleep_time_matches_duty_cycle_formula(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    gov = ResourceGovernor(
        profile=ResourceProfile(name="half", decode_duty_cycle=0.5),
        max_single_yield_s=10.0,
    )
    gov.after_decode_step(work_seconds=0.012, produced_tokens=1)
    # period = work / duty = 0.024s; sleep = period - work = 0.012s
    assert sum(calls) == pytest.approx(0.012, abs=1e-9)


def test_smaller_duty_cycle_yields_more(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    gov = ResourceGovernor(
        profile=ResourceProfile(name="quarter", decode_duty_cycle=0.25),
        max_single_yield_s=10.0,
    )
    gov.after_decode_step(work_seconds=0.01, produced_tokens=1)
    # period = 0.01 / 0.25 = 0.04s; sleep = 0.03s
    assert sum(calls) == pytest.approx(0.03, abs=1e-9)


def test_never_sleeps_negative(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    gov = ResourceGovernor(profile=ResourceProfile(name="p", decode_duty_cycle=0.999999))
    gov.after_decode_step(work_seconds=1e-6, produced_tokens=1)
    assert all(s >= 0 for s in calls)


# ---- upper bound on a single yield --------------------------------------


def test_single_yield_is_capped(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    gov = ResourceGovernor(
        profile=ResourceProfile(name="tiny-duty", decode_duty_cycle=0.001),
        max_single_yield_s=0.05,
    )
    gov.after_decode_step(work_seconds=1.0, produced_tokens=1)
    assert sum(calls) == pytest.approx(0.05, abs=1e-9)


# ---- minimum useful throughput floor ------------------------------------


def test_min_decode_tps_floor_shortens_yield(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    # A model that already runs at 10 tok/s naturally (work_seconds=0.1s/token).
    # decode_duty_cycle=0.4 alone would want a period of 0.25s (=1/4 the
    # natural rate = 4 tok/s), but min_decode_tps=8 must keep delivered
    # throughput at >= 8 tok/s, i.e. period <= 0.125s.
    profile = ResourceProfile(name="floored", decode_duty_cycle=0.4, min_decode_tps=8.0)
    gov = ResourceGovernor(profile=profile, max_single_yield_s=10.0)
    for _ in range(6):
        calls.clear()
        gov.after_decode_step(work_seconds=0.1, produced_tokens=1)
    # EMA has converged; floor should now be actively engaged and binding.
    period = 0.1 + sum(calls)
    delivered_tps = 1 / period
    assert delivered_tps == pytest.approx(8.0, abs=1e-6)
    # Floor should bind: delivered throughput is higher than the raw
    # duty-cycle-only target of 4 tok/s (period 0.1/0.4=0.25s -> 4 tok/s).
    assert delivered_tps > 1 / (0.1 / 0.4)


def test_min_decode_tps_floor_disabled_by_none(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    gov = ResourceGovernor(
        profile=ResourceProfile(name="p", decode_duty_cycle=0.5, min_decode_tps=None),
        max_single_yield_s=10.0,
    )
    gov.after_decode_step(work_seconds=0.01, produced_tokens=1)
    assert sum(calls) == pytest.approx(0.01, abs=1e-9)


def test_min_decode_tps_never_engages_below_natural_speed(monkeypatch):
    # A model that already runs at 5 tok/s naturally (slower than the floor)
    # must not be throttled further just because duty_cycle < 1.0.
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    profile = ResourceProfile(name="already-slow", decode_duty_cycle=0.4, min_decode_tps=15.0)
    gov = ResourceGovernor(profile=profile, max_single_yield_s=10.0)
    for _ in range(6):
        calls.clear()
        gov.after_decode_step(work_seconds=0.2, produced_tokens=1)  # 5 tok/s natural
    assert sum(calls) == pytest.approx(0.0, abs=1e-9)


def test_prefill_ignores_min_tps_floor(monkeypatch):
    # min_decode_tps only applies to decode; prefill pacing should be
    # unaffected even when the active profile sets it.
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    profile = ResourceProfile(name="p", prefill_duty_cycle=0.5, min_decode_tps=1_000_000)
    gov = ResourceGovernor(profile=profile, max_single_yield_s=10.0)
    gov.after_prefill_chunk(work_seconds=0.01, tokens=2048)
    assert sum(calls) == pytest.approx(0.01, abs=1e-9)


# ---- cancellation-awareness ---------------------------------------------


def test_abort_check_interrupts_yield_promptly(monkeypatch):
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    gov = ResourceGovernor(
        profile=ResourceProfile(name="p", decode_duty_cycle=0.1),
        max_single_yield_s=10.0,
        sleep_slice_s=0.01,
    )
    calls = {"n": 0}

    def abort_after_two_slices():
        calls["n"] += 1
        return calls["n"] > 2

    gov.after_decode_step(work_seconds=0.1, produced_tokens=1, abort_check=abort_after_two_slices)
    # period = 0.1 / 0.1 = 1.0s -> sleep_time = 0.9s -> would be 90 slices of
    # 0.01s uninterrupted; abort_check must cut it off after 2.
    assert len(sleeps) == 2


def test_no_abort_check_completes_full_yield(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    gov = ResourceGovernor(
        profile=ResourceProfile(name="p", decode_duty_cycle=0.5),
        max_single_yield_s=10.0,
        sleep_slice_s=0.01,
    )
    gov.after_decode_step(work_seconds=0.03, produced_tokens=1)
    assert sum(sleeps) == pytest.approx(0.03, abs=1e-9)


# ---- real (unmocked) timing sanity check --------------------------------


def test_real_wall_clock_pacing_is_observable():
    # No monkeypatching here: a small real sleep must actually elapse.
    gov = ResourceGovernor(
        profile=ResourceProfile(name="half", decode_duty_cycle=0.5),
        max_single_yield_s=1.0,
    )
    started = time.perf_counter()
    gov.after_decode_step(work_seconds=0.02, produced_tokens=1)
    elapsed = time.perf_counter() - started
    assert elapsed >= 0.015  # allow scheduler slack below the ~0.02s target


# ---- stats / observability ----------------------------------------------


def test_stats_shape_matches_expected_fields():
    gov = ResourceGovernor(profile="balanced")
    stats = gov.stats()
    assert stats["enabled"] is True
    assert stats["profile"] == "balanced"
    assert stats["prefill_duty_cycle"] == 0.70
    assert stats["decode_duty_cycle"] == 0.70
    for lane in ("prefill", "decode"):
        assert "work_ms_ema" in stats[lane]
        assert "yield_ms_ema" in stats[lane]
        assert "effective_duty_cycle" in stats[lane]
        assert "yields" in stats[lane]


def test_stats_yields_counter_increments(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    gov = ResourceGovernor(profile=ResourceProfile(name="p", decode_duty_cycle=0.5))
    assert gov.stats()["decode"]["yields"] == 0
    gov.after_decode_step(work_seconds=0.01, produced_tokens=1)
    assert gov.stats()["decode"]["yields"] == 1
    gov.after_decode_step(work_seconds=0.01, produced_tokens=1)
    assert gov.stats()["decode"]["yields"] == 2


def test_stats_effective_duty_cycle_converges_to_target(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    gov = ResourceGovernor(profile=ResourceProfile(name="p", decode_duty_cycle=0.5))
    for _ in range(50):
        gov.after_decode_step(work_seconds=0.01, produced_tokens=1)
    stats = gov.stats()
    assert stats["decode"]["effective_duty_cycle"] == pytest.approx(0.5, abs=0.02)
