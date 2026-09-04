# Benchmarks

Every entry records hardware, model, quantization, sampler, token count,
profile, and date/commit, per this repo's own `CONTRIBUTING.md` discipline.
**None of the measurements below are from the M5 Ultra target machine** —
this fork has so far only been developed and tested on an Apple M4 Max
(36GB) dev machine. They demonstrate the mechanism works correctly and
give a rough sense of scale; they are not tuning data for the target
hardware, and the built-in profile numbers (`balanced` 0.70,
`interactive` 0.40) remain starting-point hypotheses until Test A-F and
the real Moonlight test below are run on the actual target Mac Studio.

## Environment (all entries below unless noted)

- Hardware: Apple M4 Max, 36GB unified memory
- macOS: 26.5.1
- MLX: 0.32.2
- Python: 3.12.11
- MTPLX: this fork, `feature/resource-governor` branch
- Model: `Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed` (native MTP, depth 3)
- Sampler: `temperature=0` (greedy) unless noted
- Date: 2026-09-04

## Decode pacing — AR mode (forced `generation_mode: "ar"`)

First real-model validation of the mechanism, before the MTP decode hook
existed (`generate_mtpk` wasn't paced yet at this point — this run
exercised the classic AR lane specifically).

| Profile | Request | Wall time | `decode.effective_duty_cycle` | `natural_tps_ema` | `delivered_tps_ema` |
|---|---|---:|---:|---:|---:|
| `max` | 150 tokens | 1.20s | ~1.0 | — | — |
| `interactive` | 150 tokens | 0.98s* | 0.40 | 95.3 | 38.1 |

\* Faster wall time than `max` despite heavier pacing — a 13-token prefix
cache hit on the second request (`cached_tokens: 13`), not evidence
pacing speeds anything up. Compare the governor-reported `tok_s` figures,
not naive wall-clock, when the prompt isn't byte-identical between runs.

`95.3 × 0.4 ≈ 38.1` — delivered throughput matches the duty-cycle formula
almost exactly.

## Decode pacing — MTP mode (default `generation_mode`)

After the MTP decode hook (`generate_mtpk`). Confirms the fix for the gap
found by the AR-only test above: a real request using the model's actual
default generation mode is now paced.

| Profile | Request | Wall time | `decode.effective_duty_cycle` | `natural_tps_ema` | `delivered_tps_ema` |
|---|---|---:|---:|---:|---:|
| `max` | 150 tokens | 1.03s | ~1.0 | — | — |
| `interactive` | 150 tokens | 3.11s | 0.40 | 139.3 | 55.7 |

`139.3 × 0.4 ≈ 55.7` — again matches the formula.

## Full-coverage confirmation (decode + prefill, fully default request)

After all decode/prefill hooks and the prefill-routing fix
(`_prefill_committed_mtp_history_streaming`). No `generation_mode`
override, no forced chunk-size env vars — a genuinely default request.

Profile: `interactive`. Request: 100-token completion.

```json
{
  "prefill": {
    "steps": 1, "yields": 1,
    "work_ms_ema": 371.0, "yield_ms_ema": 500.0,
    "effective_duty_cycle": 0.426
  },
  "decode": {
    "steps": 31, "yields": 31,
    "natural_tps_ema": 86.2, "delivered_tps_ema": 35.0,
    "effective_duty_cycle": 0.414
  }
}
```

`yield_ms_ema: 500.0` on prefill hit the configured
`max_single_yield_s` cap (0.5s default) — this prompt's single chunk
took 371ms of real work, and the uncapped formula would have wanted a
longer yield than the cap allows. Working as designed (brief section 5's
"enforce a configurable upper bound on one individual sleep").

## Admission enforcement

`protect` profile, `/v1/chat/completions`: `503` with
`{"code": "resource_governor_not_admitting", "profile": "protect"}`
before any generation work starts. `max` profile, same request: `200`
with real generated tokens. See `PROJECT_STATUS.md`'s Phase 5 entry.

## `mtplx-qos auto`

Live-tested against a real running server. Correctly detected an
actually-running `Moonlight AV1.app` process on the dev machine and
switched to `interactive`; separately confirmed the `balanced` fallback
and detector-crash resilience with mocked detectors (no real Moonlight
dependency needed for those cases).

## Not yet run — needed before the profile defaults are trustworthy

These are brief section 19's Test A-F and section 20's Moonlight
acceptance test. None have been run; none can be run meaningfully on
this 36GB dev machine (Test D/E in particular need context sizes and
sustained decode runs sized for a 96GB machine, and the Moonlight test
needs the actual target Mac Studio with a Windows PC streaming to it).

- **Test A — baseline (governor off/`max`)**: prompt TPS, decode TPS,
  TTFT, total completion time, GPU utilization if observable, memory
  footprint. Establishes the "no regression at `max`" claim (brief
  section 24, criterion 12) with real numbers, not just "no code path
  entered."
- **Test B — `balanced`**: verify actual duty cycle achieved vs.
  configured 0.70, quantify throughput impact.
- **Test C — `interactive`**: same, vs. configured 0.40.
- **Test D — long prefill**: 16K/32K/64K(/128K) contexts. Verify visible
  gaps between prefill chunks and that TTFT degradation under pacing is
  explainable by the duty cycle, not something else.
- **Test E — long decode**: thousands of tokens, stable-state duty-cycle
  behavior, `min_decode_tps` floor engagement on a genuinely slow model.
- **Test F — live profile transition mid-request**: `max → interactive →
  balanced → max` during one long generation; confirm no failure,
  corruption, model reload, or lost stream tokens.
- **Moonlight acceptance test**: real Moonlight session (target
  resolution/refresh/codec/bitrate) against `max` vs. `interactive` under
  both decode-heavy and prefill-heavy MTPLX load. Dropped frames, frame
  pacing, decode/render/network latency, subjective stutter, alongside
  MTPLX's own prompt TPS/decode TPS/TTFT. This is the actual point of the
  whole project (brief section 20) and hasn't happened yet.

## How to add an entry

Run the mechanism against a real request (not a toy model — see
`tests/test_resource_governor_*_integration.py` for toy-model coverage,
which is a different kind of evidence), record the full environment
block above, and append a dated section here. Prefer pasting the raw
`GET /admin/resource-governor` JSON over hand-summarized numbers where
practical — it's harder to misremember/misquote.
