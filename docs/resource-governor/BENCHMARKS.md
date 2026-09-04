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

## Real Moonlight test — community report (not this session's hardware)

**First actual Moonlight acceptance data for this project** (brief
section 20), reported by an external tester (not the M4 Max dev machine
above, and not the M5 Ultra target — tester's exact hardware wasn't
recorded, a gap to close on future reports). Commit: `0b649d8`. Date:
2026-09-04. Model: `Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed`.
Benchmark tool: `llama-benchy` (`0.4.1.dev1+ge9be34457`), an external
tool, not part of this repo — generation-latency mode. `pp` = prefill
tokens, `tg` = generated tokens; `t/s` and `peak t/s` are that tool's own
throughput figures, `ttfr`/`est_ppt`/`e2e_ttft` are its latency metrics
(milliseconds).

**`max` profile** (2026-09-04 13:54:03):

| test | t/s | peak t/s | ttfr (ms) | est_ppt (ms) | e2e_ttft (ms) |
|---|---:|---:|---:|---:|---:|
| pp2048 | 35197.71 ± 2728.22 | — | 59.93 ± 4.68 | 58.58 ± 4.68 | 5504.28 ± 34.81 |
| tg32 | 80.92 ± 9.76 | 83.53 ± 10.08 | — | — | — |
| pp2048 | 31641.11 ± 7984.84 | — | 71.54 ± 21.54 | 70.19 ± 21.54 | 5418.97 ± 33.85 |
| tg128 | 55.00 ± 2.18 | 55.67 ± 2.05 | — | — | — |
| pp8192 | 101048.19 ± 1727.65 | — | 82.46 ± 1.40 | 81.10 ± 1.40 | 23397.03 ± 86.25 |
| tg32 | 78.24 ± 4.52 | 80.76 ± 4.67 | — | — | — |
| pp8192 | 86448.74 ± 18544.52 | — | 101.49 ± 25.04 | 100.14 ± 25.04 | 24298.40 ± 804.86 |
| tg128 | 49.42 ± 6.72 | 50.00 ± 6.68 | — | — | — |

**`interactive` profile** (2026-09-04 14:02:32, same machine/model,
switched live via the admin API — no restart):

| test | t/s | peak t/s | ttfr (ms) | est_ppt (ms) | e2e_ttft (ms) |
|---|---:|---:|---:|---:|---:|
| pp2048 | 27210.86 ± 2543.57 | — | 81.11 ± 7.23 | 75.99 ± 7.23 | 9289.11 ± 1929.28 |
| tg32 | 25.87 ± 1.50 | 26.33 ± 1.25 | — | — | — |
| pp2048 | 25929.95 ± 647.74 | — | 84.21 ± 1.96 | 79.08 ± 1.96 | 9224.65 ± 1855.61 |
| tg128 | 18.82 ± 0.32 | 19.33 ± 0.47 | — | — | — |
| pp8192 | 109036.27 ± 1860.31 | — | 80.29 ± 1.29 | 75.16 ± 1.29 | 29771.46 ± 243.33 |
| tg32 | 22.97 ± 1.32 | 23.67 ± 1.25 | — | — | — |
| pp8192 | 108933.34 ± 2927.60 | — | 80.39 ± 2.02 | 75.27 ± 2.02 | 29976.33 ± 36.24 |
| tg128 | 18.46 ± 0.38 | 18.67 ± 0.47 | — | — | — |

Decode (`tg`) throughput drops to roughly a third under `interactive`
(e.g. tg128: 55.00 → 18.82, 49.42 → 18.46) — steeper than the configured
0.4 decode duty cycle alone would predict, consistent with real per-call
overhead and the tester's own hardware/thermal conditions rather than a
governor miscalibration; not broken down further here since the
`GET /admin/resource-governor` JSON wasn't captured alongside this run
(worth doing next time — see "How to add an entry" below).

**Moonlight, same two runs:**

| Profile | Result |
|---|---|
| `max` | **0.64 FPS — unplayable** |
| `interactive` | **90 FPS decode, 60 FPS rendered** |

This is the actual point of the whole project, working. A transient
33% frame-loss-to-jitter reading appeared on one run and was gone
(0.00%) on a retest; the tester attributed it to network conditions
rather than the server. Plausible — the governor only paces MLX/GPU
compute scheduling and has no network-shaping component at all (see
`ARCHITECTURE.md`'s "What this is not") — but not confirmed uncorrelated
with high confidence either; flagged as an open thread rather than
resolved, since it was reported as going away when the MTPLX server
wasn't active at all, which is a real correlation worth re-testing for
before dismissing it. If it recurs, checking whether MTPLX's LAN traffic
and the Moonlight stream share the same network link would be the first
thing to rule in or out.

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

Brief section 19's Test A-F remain unrun; none can be run meaningfully
on this 36GB dev machine (Test D/E in particular need context sizes and
sustained decode runs sized for a 96GB machine). Section 20's Moonlight
acceptance test has a first real result now (above) — encouraging, and
the actual point of the project working — but it's a single community
report on unspecified, non-target hardware, missing the
resolution/refresh/codec/bitrate details, the decode-vs-prefill-heavy
load split, and a captured `GET /admin/resource-governor` snapshot
alongside it. Treat it as strong early evidence, not the completed test.

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
- **Moonlight acceptance test, full version**: on the target M5 Ultra,
  with resolution/refresh/codec/bitrate recorded, dropped frames and
  frame pacing (not just FPS), decode/render/network latency broken out,
  a prefill-heavy load case (not just decode-heavy), and a captured
  `GET /admin/resource-governor` snapshot alongside the Moonlight numbers
  — see "Real Moonlight test" above for what's already been shown to
  work.

## How to add an entry

Run the mechanism against a real request (not a toy model — see
`tests/test_resource_governor_*_integration.py` for toy-model coverage,
which is a different kind of evidence), record the full environment
block above, and append a dated section here. Prefer pasting the raw
`GET /admin/resource-governor` JSON over hand-summarized numbers where
practical — it's harder to misremember/misquote.
