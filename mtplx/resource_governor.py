"""Cooperative duty-cycle QoS pacing for MTPLX inference (Phase 1: decode only).

See docs/resource-governor/ for the design rationale and
docs/resource-governor/IMPLEMENTATION_NOTES.md for exact hook locations.

This is mechanism, not policy: ``ResourceGovernor`` holds a QoS profile and
paces MTPLX's own prefill/decode scheduling loops by sleeping between
already-existing scheduling units (one decode step, one prefill chunk). It
has no knowledge of what's consuming the machine (Moonlight, another app,
a human at the keyboard) — that's an external policy controller's job.

Deliberate deviation from the brief's illustrative API: ``after_decode_step``
and ``after_prefill_chunk`` are synchronous methods, not ``async def``. The
decode/prefill loops in ``mtplx/generation.py`` that this module hooks run
synchronously on MTPLX's single owner thread (see
docs/resource-governor/IMPLEMENTATION_NOTES.md section 1, "Scheduler
creation" / "Sustained/chunked prefill") — there is no running event loop at
the call site, and converting those loops to async is exactly the kind of
invasive scheduler change the project brief says to avoid. Pacing is instead
a short, interruptible ``time.sleep`` loop that polls the same
``abort_check`` callback the generation loops already thread through for
cancellation (see IMPLEMENTATION_NOTES.md section 2, Q15) — cooperative with
respect to cancellation, even though it does block the calling OS thread for
the duration of one yield slice.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

__all__ = [
    "ResourceProfile",
    "ResourceGovernor",
    "BUILTIN_PROFILES",
    "resolve_profile",
]


@dataclass(frozen=True)
class ResourceProfile:
    """A named QoS profile. ``1.0`` duty cycle means no intentional pacing."""

    name: str
    prefill_duty_cycle: float = 1.0
    decode_duty_cycle: float = 1.0
    min_decode_tps: float | None = None
    prefill_chunk_tokens: int | None = None
    max_active_requests: int | None = None
    decode_batch_max: int | None = None
    admission_allowed: bool = True

    def __post_init__(self) -> None:
        for field_name in ("prefill_duty_cycle", "decode_duty_cycle"):
            value = getattr(self, field_name)
            if not (0 < value <= 1.0):
                raise ValueError(f"{field_name} must satisfy 0 < value <= 1.0, got {value!r}")
        if self.min_decode_tps is not None and self.min_decode_tps < 0:
            raise ValueError(f"min_decode_tps must be >= 0 or None, got {self.min_decode_tps!r}")
        if self.prefill_chunk_tokens is not None and self.prefill_chunk_tokens <= 0:
            raise ValueError("prefill_chunk_tokens must be > 0 or None")
        if self.max_active_requests is not None and self.max_active_requests <= 0:
            raise ValueError("max_active_requests must be > 0 or None")
        if self.decode_batch_max is not None and self.decode_batch_max <= 0:
            raise ValueError("decode_batch_max must be > 0 or None")


# Starting-point values only, per the brief (section 32) — not tuned claims.
# balanced/interactive numbers are hypotheses to be measured on real
# hardware (brief section 19-20), not promises about GPU/bandwidth share.
BUILTIN_PROFILES: dict[str, ResourceProfile] = {
    "max": ResourceProfile(name="max"),
    "balanced": ResourceProfile(
        name="balanced",
        prefill_duty_cycle=0.70,
        decode_duty_cycle=0.70,
        prefill_chunk_tokens=2048,
        max_active_requests=1,
    ),
    "interactive": ResourceProfile(
        name="interactive",
        prefill_duty_cycle=0.40,
        decode_duty_cycle=0.40,
        min_decode_tps=15.0,
        prefill_chunk_tokens=1024,
        max_active_requests=1,
        decode_batch_max=1,
    ),
    "protect": ResourceProfile(
        name="protect",
        prefill_duty_cycle=0.40,
        decode_duty_cycle=0.40,
        admission_allowed=False,
    ),
    "pause": ResourceProfile(
        name="pause",
        admission_allowed=False,
    ),
}


def resolve_profile(name: str) -> ResourceProfile:
    try:
        return BUILTIN_PROFILES[name]
    except KeyError:
        known = ", ".join(sorted(BUILTIN_PROFILES))
        raise ValueError(f"unknown resource profile {name!r}; known profiles: {known}") from None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class _Ema:
    """Exponential moving average; ``None`` until the first sample."""

    __slots__ = ("value", "alpha")

    def __init__(self, alpha: float = 0.15) -> None:
        self.value: float | None = None
        self.alpha = alpha

    def update(self, sample: float) -> float:
        if self.value is None:
            self.value = sample
        else:
            self.value = self.alpha * sample + (1 - self.alpha) * self.value
        return self.value


@dataclass
class _LaneStats:
    work_s_ema: _Ema = field(default_factory=_Ema)
    yield_s_ema: _Ema = field(default_factory=_Ema)
    natural_tps_ema: _Ema = field(default_factory=_Ema)
    delivered_tps_ema: _Ema = field(default_factory=_Ema)
    steps: int = 0
    yields: int = 0
    total_yield_s: float = 0.0

    def as_dict(self) -> dict:
        work_ms = (self.work_s_ema.value or 0.0) * 1000.0
        yield_ms = (self.yield_s_ema.value or 0.0) * 1000.0
        effective_duty_cycle = work_ms / (work_ms + yield_ms) if (work_ms + yield_ms) > 0 else None
        return {
            "steps": self.steps,
            "work_ms_ema": work_ms,
            "yield_ms_ema": yield_ms,
            "natural_tps_ema": self.natural_tps_ema.value,
            "delivered_tps_ema": self.delivered_tps_ema.value,
            "effective_duty_cycle": effective_duty_cycle,
            "yields": self.yields,
            "total_yield_s": self.total_yield_s,
        }


class ResourceGovernor:
    """Holds the active QoS profile and paces decode/prefill scheduling units.

    Thread-safety: ``set_profile``/``current_profile`` take an internal lock
    so a profile switch from an admin-API thread is picked up promptly by
    the generation thread's next scheduling unit (brief section 9/17), but
    ``after_decode_step``/``after_prefill_chunk`` are only ever called from
    MTPLX's single generation-owner thread and don't need to be re-entrant
    with each other.
    """

    def __init__(
        self,
        profile: ResourceProfile | str | None = None,
        *,
        enabled: bool = True,
        max_single_yield_s: float = 0.5,
        sleep_slice_s: float = 0.02,
    ) -> None:
        if max_single_yield_s <= 0:
            raise ValueError("max_single_yield_s must be > 0")
        if sleep_slice_s <= 0:
            raise ValueError("sleep_slice_s must be > 0")
        self._lock = threading.Lock()
        self._profile = _coerce_profile(profile) if profile is not None else BUILTIN_PROFILES["max"]
        self._enabled = enabled
        self._max_single_yield_s = max_single_yield_s
        self._sleep_slice_s = sleep_slice_s
        self._decode = _LaneStats()
        self._prefill = _LaneStats()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled

    def current_profile(self) -> ResourceProfile:
        with self._lock:
            return self._profile

    def set_profile(self, profile: ResourceProfile | str) -> None:
        resolved = _coerce_profile(profile)
        with self._lock:
            self._profile = resolved

    def admission_allowed(self) -> bool:
        """Whether new work should be admitted under the active profile.

        Phase 1 note: nothing in the live server consults this yet — brief
        section 14/section 30 Q12 found ``batching/admission.py``'s
        pressure-tier admission logic is not wired into the live HTTP path
        today, so wiring *this* method into request admission is Phase 5/6
        work, not implemented here. It exists now so profile semantics
        (``protect``/``pause`` refuse admission) are defined from the start.
        """
        with self._lock:
            return (not self._enabled) or self._profile.admission_allowed

    def after_decode_step(
        self,
        *,
        work_seconds: float,
        produced_tokens: int = 1,
        abort_check: Callable[[], bool] | None = None,
    ) -> None:
        profile = self.current_profile()
        self._pace(
            self._decode,
            work_seconds=work_seconds,
            produced_units=produced_tokens,
            duty_cycle=profile.decode_duty_cycle,
            min_tps=profile.min_decode_tps,
            abort_check=abort_check,
        )

    def after_prefill_chunk(
        self,
        *,
        work_seconds: float,
        tokens: int,
        abort_check: Callable[[], bool] | None = None,
    ) -> None:
        profile = self.current_profile()
        self._pace(
            self._prefill,
            work_seconds=work_seconds,
            produced_units=tokens,
            duty_cycle=profile.prefill_duty_cycle,
            min_tps=None,
            abort_check=abort_check,
        )

    def stats(self) -> dict:
        profile = self.current_profile()
        return {
            "enabled": self._enabled,
            "profile": profile.name,
            "prefill_duty_cycle": profile.prefill_duty_cycle,
            "decode_duty_cycle": profile.decode_duty_cycle,
            "min_decode_tps": profile.min_decode_tps,
            "effective_prefill_chunk_tokens": profile.prefill_chunk_tokens,
            "effective_max_active_requests": profile.max_active_requests,
            "prefill": self._prefill.as_dict(),
            "decode": self._decode.as_dict(),
        }

    # -- internals -----------------------------------------------------

    def _pace(
        self,
        lane: _LaneStats,
        *,
        work_seconds: float,
        produced_units: int,
        duty_cycle: float,
        min_tps: float | None,
        abort_check: Callable[[], bool] | None,
    ) -> None:
        lane.steps += 1
        work_seconds = max(0.0, work_seconds)
        lane.work_s_ema.update(work_seconds)

        if not self._enabled or duty_cycle >= 1.0 or work_seconds <= 0:
            lane.yield_s_ema.update(0.0)
            return

        # Core duty-cycle formula (brief section 5): spend `work_seconds` of
        # every `work_seconds / duty_cycle` wall-clock seconds doing real
        # work, and yield the rest. Stateless per call by design — brief
        # section 5 explicitly warns against accumulating timing debt
        # indefinitely, so each step's yield is computed only from that
        # step's own measured work, not from a running backlog.
        target_total_period = work_seconds / duty_cycle
        sleep_time = target_total_period - work_seconds

        if min_tps is not None and min_tps > 0 and produced_units > 0:
            # Minimum-useful-throughput floor (brief section 8). The
            # engage/disengage decision uses the EMA-smoothed natural TPS,
            # not this step's raw value, so one slow/fast outlier step
            # doesn't yank the floor on and off (brief: "avoid unstable
            # oscillation... use a rolling average").
            natural_tps_ema = lane.natural_tps_ema.update(produced_units / work_seconds)
            if natural_tps_ema is not None and natural_tps_ema > min_tps:
                max_total_period = produced_units / min_tps
                floor_sleep = max(0.0, max_total_period - work_seconds)
                sleep_time = min(sleep_time, floor_sleep)
            else:
                # Already at/under the floor naturally: no intentional
                # throttling makes sense here.
                sleep_time = 0.0

        sleep_time = _clamp(sleep_time, 0.0, self._max_single_yield_s)

        if sleep_time <= 0:
            lane.yield_s_ema.update(0.0)
            return

        lane.yields += 1
        lane.total_yield_s += sleep_time
        lane.yield_s_ema.update(sleep_time)
        self._yield_interruptible(sleep_time, abort_check)

        if produced_units > 0:
            actual_period = work_seconds + sleep_time
            if actual_period > 0:
                lane.delivered_tps_ema.update(produced_units / actual_period)

    def _yield_interruptible(self, seconds: float, abort_check: Callable[[], bool] | None) -> None:
        # Avoid CPU busy-waiting (brief section 5) while staying responsive
        # to cancellation (brief section 18): sleep in small slices and
        # re-check abort_check between them rather than one uninterruptible
        # time.sleep(seconds) call.
        remaining = seconds
        while remaining > 0:
            if abort_check is not None and bool(abort_check()):
                return
            slice_s = self._sleep_slice_s if self._sleep_slice_s < remaining else remaining
            time.sleep(slice_s)
            remaining -= slice_s


def _coerce_profile(profile: ResourceProfile | str) -> ResourceProfile:
    if isinstance(profile, ResourceProfile):
        return profile
    return resolve_profile(profile)
