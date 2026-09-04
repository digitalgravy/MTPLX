# MTPLX Resource Governor

A runtime-adjustable QoS layer that paces MTPLX's own prefill and decode
scheduling so a shared Apple Silicon Mac stays responsive for other work
(gaming over Moonlight, other apps, a human at the keyboard) while MTPLX
keeps serving. Built for a Mac Studio that's simultaneously an always-on
LAN LLM server and an interactive desktop — see `ARCHITECTURE.md` for the
design rationale, and `../../MTPLX_RESOURCE_GOVERNOR_CODEX_BRIEF.md` for
the full original project spec.

**Not sure this is for you, or want a non-technical walkthrough?** Read
[`PLAIN_LANGUAGE_GUIDE.md`](PLAIN_LANGUAGE_GUIDE.md) instead — this file
assumes you're comfortable with flags, config files, and HTTP APIs.

## What it actually does today

- **Decode pacing**: yields between decode steps on the classic AR lane,
  the `MTPLX_AR_PIPELINE` lane, and MTP/speculative decode
  (`generate_mtpk`), targeting a configurable duty cycle (fraction of
  wall-clock time spent doing real work vs. yielding).
- **Prefill pacing**: same duty-cycle yielding between chunks, on every
  reachable chunked-prefill code path (cold prefill, warm SessionBank
  restore, MTP committed-history streaming).
- **Admission refusal**: `protect`/`pause` profiles return a clean
  `503` for new requests instead of admitting them.
- **Live profile switching**: change the active profile on an
  already-running server, no restart, no model reload.

**Not yet enforced**: a profile's `max_active_requests`/`decode_batch_max`/
`prefill_chunk_tokens` values are recorded and reported (see the admin API
below) but don't currently change the live scheduler's actual concurrency
or chunk size — only duty-cycle pacing and protect/pause admission
refusal are wired to real enforcement today. Don't rely on `interactive`
mode to *limit concurrency*; rely on it to *pace the work that runs*.

## Quick start

Start the server on a named profile:

```bash
mtplx serve --model <your-model> --resource-profile interactive
```

Or override individual fields on top of a profile:

```bash
mtplx serve --model <your-model> --resource-profile balanced --decode-duty-cycle 0.5
```

Switch profiles on a server that's already running, live:

```bash
curl -X POST http://127.0.0.1:8000/admin/resource-governor/profile \
  -H 'Content-Type: application/json' \
  -d '{"profile": "interactive"}'
```

Or use the companion CLI instead of raw `curl`:

```bash
./scripts/mtplx-qos interactive
./scripts/mtplx-qos status
./scripts/mtplx-qos auto        # Moonlight running -> interactive, else balanced
```

## Built-in profiles

| Profile       | prefill duty | decode duty | min decode tok/s | admits new requests |
|---------------|:---:|:---:|:---:|:---:|
| `max`         | 1.0 | 1.0 | — | yes |
| `balanced`    | 0.70 | 0.70 | — | yes |
| `interactive` | 0.40 | 0.40 | 15.0 | yes |
| `protect`     | 0.40 | 0.40 | — | **no** |
| `pause`       | 1.0 | 1.0 | — | **no** |

`1.0` duty cycle means no intentional pacing. These are **starting-point
hypotheses**, not tuned claims — see `BENCHMARKS.md`. Tune them for your
own hardware and workload rather than trusting the defaults blindly.

## CLI flags

Available on `mtplx serve`, `mtplx quickstart`, and `mtplx start`:

```
--resource-profile {max,balanced,interactive,protect,pause}
--prefill-duty-cycle FLOAT      # 0 < x <= 1.0, overrides the profile's value
--decode-duty-cycle FLOAT       # 0 < x <= 1.0, overrides the profile's value
--min-decode-tps FLOAT          # 0 disables the floor
```

An explicit override keeps the *name* of the selected profile (so
`GET /admin/resource-governor` still reports e.g. `"interactive"`) while
using your custom value for that one field. The effective configuration
is logged once at startup.

## Config file

`~/.mtplx/config.toml` (or wherever `$MTPLX_CONFIG` points, if you've set
that environment variable):

```toml
resource_profile = "interactive"
prefill_duty_cycle = 0.35
decode_duty_cycle = 0.35
min_decode_tps = 12
```

Same precedence as every other MTPLX config value: an explicit CLI flag
beats the config file, which beats the built-in profile default.

## Admin API

```
GET /admin/resource-governor
```

Returns the active profile and live telemetry:

```json
{
  "enabled": true,
  "profile": "interactive",
  "prefill_duty_cycle": 0.4,
  "decode_duty_cycle": 0.4,
  "min_decode_tps": 15.0,
  "effective_prefill_chunk_tokens": 1024,
  "effective_max_active_requests": 1,
  "prefill": {
    "steps": 1, "work_ms_ema": 371.0, "yield_ms_ema": 500.0,
    "effective_duty_cycle": 0.43, "yields": 1, "total_yield_s": 0.5
  },
  "decode": {
    "steps": 31, "work_ms_ema": 30.2, "yield_ms_ema": 42.8,
    "natural_tps_ema": 86.2, "delivered_tps_ema": 35.0,
    "effective_duty_cycle": 0.41, "yields": 31, "total_yield_s": 1.36
  }
}
```

```
POST /admin/resource-governor/profile
Content-Type: application/json

{"profile": "interactive"}
```

Uses the same authentication as every other MTPLX endpoint (`--api-key`/
`--api-key-file` if the server requires one — nothing extra to configure
for the governor specifically).

## `mtplx-qos` companion tool

`scripts/mtplx-qos` is a small, standalone, stdlib-only script — it does
not import the `mtplx` package, and doesn't need to run on the same
machine as the server (point `--url`/`$MTPLX_QOS_URL` at any reachable
`mtplx serve`). This is deliberate: MTPLX itself only knows *how* to
pace; deciding *which* profile should be active belongs outside it.

```bash
mtplx-qos max
mtplx-qos balanced
mtplx-qos interactive
mtplx-qos protect
mtplx-qos pause
mtplx-qos status

mtplx-qos auto                 # decide once, apply
mtplx-qos auto --dry-run       # decide once, print only
mtplx-qos auto --watch 10      # re-decide and re-apply every 10s
```

Current auto policy: a running Moonlight process → `interactive`,
otherwise → `balanced`. The detector list
(`INTERACTIVE_TRIGGER_DETECTORS` in the script) is a plain dict of
`name -> callable`; add an entry to react to other games or apps without
touching the decision logic.

## Correctness

Pacing changes timing only — deterministic sampling settings produce
identical output whether the governor is on or off. Every hook has a
test asserting this (see `tests/test_resource_governor_*.py`).

## Known limitations

- Concurrency (`max_active_requests`/`decode_batch_max`) and chunk-size
  fields are reported but not enforced (see above).
- Memory-pressure-based admission (stopping new work under real memory
  pressure, not just when a profile says so) is not implemented.
- The `MTPLX_AR_PIPELINE`/MTP/prefill hooks cover every code path
  reachable from a live `mtplx serve` request in AR and MTP generation
  modes. The narrower, model-family-specific A3B/whole-MoE batched decode
  lane (`a3b_mtp_batch.py`) is not hooked.
- Tuned on an Apple M4 Max (36GB) dev machine, not the M5 Ultra target —
  see `BENCHMARKS.md`.

## More

- `ARCHITECTURE.md` — why this exists and how it's built, one level up
  from the code.
- `IMPLEMENTATION_NOTES.md` — the detailed engineering log: exact source
  locations, design decisions, mistakes caught and corrected, real
  measurements. Read this before modifying the governor's code.
- `BENCHMARKS.md` — measured results so far, and what's still needed.
- `UPSTREAM_STATUS.md` — how this fork relates to `youssofal/MTPLX`.
- `PLAIN_LANGUAGE_GUIDE.md` — for people who want to use this without
  reading code.
