# MTPLX Resource Governor Project Status

## Current objective

Phase 4 — CLI and persisted config for resource-governor profiles
(`--resource-profile`, `--prefill-duty-cycle`, `--decode-duty-cycle`,
`--min-decode-tps`), plus effective-value reporting. Phases 0-3 are done
— see `docs/resource-governor/IMPLEMENTATION_NOTES.md`. See
`MTPLX_RESOURCE_GOVERNOR_CODEX_BRIEF.md` for the full spec.

## In progress

- [ ] Real inference smoke test — blocked on disk space. Pulling
      `Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed` (chosen over the 27B
      default because this dev machine is a 36GB M4 Max, not the 96GB M5
      Ultra target) failed at 49% with "not enough disk space"; the partial
      download (1.2GB) was removed. Needs several more GB free before
      retrying — see "Blocked / needs investigation" below.

## Next up

- [ ] Retry the small-model pull and run `mtplx run "hello" --model
      Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed` (or `mtplx chat`) once
      disk space allows, to close out the Phase 0 "run an existing
      inference workload" checklist item and give the (still-unread)
      reconnaissance follow-ups (`request_policy.py`, `cache_state.py`,
      `session_bank.py`, `kpi/runtime_kpis.py`) empirical grounding.
- [ ] Runtime-verify (not just statically infer) that mutating
      `state.args.max_active_requests`/`decode_batch_max` live actually
      changes admission behavior on a running server — these two keys are
      *not* currently in `DASHBOARD_MUTABLE_SETTINGS_KEYS`
      (`openai.py:15137-15155`), unlike `prefill_chunk_tokens`.
- [ ] Validate the decode governor hook against a real downloaded model
      once disk space allows (currently only validated against a toy MLX
      model — see "Completed" below).
- [ ] Hook `after_decode_step` into the `MTPLX_AR_PIPELINE` lane and the
      MTP (`generate_mtpk`) / batched (`batched_decode.py`) decode paths
      (Phase 1 only covers the classic/default AR loop).
- [ ] Hook `after_prefill_chunk` into MTPLX's other four chunked-prefill
      implementations — `_prefill_restored_prompt_suffix`
      (`generation.py:2548`, warm-restore suffix), `_prefill_with_hidden_sequence`
      (`generation.py:5656`, MTP-hidden-sequence path, one caller),
      `_prefill_committed_mtp_history_streaming` (`generation.py:5414`,
      committed/last_window MTP history policy) — Phase 2 only covers
      plain `_prefill()` (`generation.py:5317+`), the one `generate_ar`'s
      default `mtp_history_policy="cycle"` cold-start path actually uses
      (also shared by `generate_mtp1`/`generate_mtpa`, not `generate_mtpk`).
- [ ] Phase 4: CLI (`--resource-profile`, `--prefill-duty-cycle`,
      `--decode-duty-cycle`, `--min-decode-tps`) + persisted config +
      effective-value reporting/logging at startup.
- [ ] Runtime-verify the admin API against a real running `mtplx serve`
      process (so far only exercised via FastAPI `TestClient` against a
      fake/toy `ServerState`, not a live server process).

## Completed

- [x] Forked `youssofal/MTPLX` to `digitalgravy/MTPLX` (GitHub CLI installed
      via Homebrew, authenticated as `digitalgravy`).
- [x] Cloned fork into this working directory; added `upstream` remote
      (`https://github.com/youssofal/MTPLX.git`).
- [x] Created `feature/resource-governor` branch off `main`.
- [x] Recorded starting upstream commit: `e652d55e2652137a4abcf1312357abbf3eb9d692`
      (2026-09-01, "ci: fail on any AI attribution in history...").
- [x] Set up local dev environment: `uv venv --python 3.12.11 .venv`,
      `uv pip install -e ".[dev,server]"` — installs cleanly, MLX 0.32.2 +
      mlx-lm 0.31.3 present.
- [x] Ran CONTRIBUTING.md smoke test subset — 303/303 passed.
- [x] Full `pytest tests/` baseline run, twice (first run hit disk-full
      mid-run and produced spurious failures; rerun with ~2.3GB free is the
      real baseline). 64 failures, all triaged — see "Bugs discovered"
      below. None are governor-related; none block Phase 0.
- [x] Deep code reconnaissance written to
      `docs/resource-governor/IMPLEMENTATION_NOTES.md` (~500 lines,
      file:line-cited), covering brief section 2's checklist and all of
      section 30's 15 questions (14 solidly answered; the MTP verify-cycle
      timing boundary — brief Q7 — remains genuinely open pending a closer
      read of `generate_mtpk`), and section 31's MLX-laziness investigation
      (MTPLX's
      own `prefill_rungs.py` docstring already documents the exact hazard
      the brief warned about). Key findings: `MTPContinuousScheduler`/
      `AdmissionPolicy` are confirmed unused in production (import-graph
      grep: `server/openai.py`/`cli.py` never import them; only
      `tests/test_batching_foundation.py` exercises them) — no live HTTP
      request is admitted/rejected by admission logic today, so Phase 6
      will need to actually wire enforcement in, not just hook into
      something already enforced; `/v1/mtplx/settings` is a live, no-restart
      mutable-config precedent already covering `prefill_chunk_tokens`;
      `_AuthRateLimitMiddleware` already covers every route including
      `/admin/*`, so no new auth code is needed; `memory_plan.py` is a
      complete, reusable memory-budgeting system; `MTPLX_EVAL_AUDIT` is a
      built-in per-eval timing/audit log useful for verifying yield
      placement empirically later.

- [x] Phase 1 — minimal decode governor implemented and tested:
  - `mtplx/resource_governor.py`: `ResourceProfile` (validated duty cycles,
    frozen dataclass) + `ResourceGovernor` (thread-safe profile
    get/set, `after_decode_step`/`after_prefill_chunk` duty-cycle pacing,
    `min_decode_tps` floor with EMA-smoothed engage/disengage to avoid
    oscillation, capped/clamped/non-negative sleep, interruptible
    small-slice yield polling `abort_check`, `admission_allowed()`,
    `stats()`). Built-in `max`/`balanced`/`interactive`/`protect`/`pause`
    profiles per brief section 9 (values are starting-point hypotheses,
    not tuned claims, per brief section 32). Zero MLX dependency — pure
    stdlib, keeps `test_no_mlx_imports.py`-style guarantees intact.
  - **Deliberate API deviation from the brief**, documented in the
    module docstring: `after_decode_step`/`after_prefill_chunk` are
    synchronous, not `async def` — the decode loop they hook
    (`generate_ar` in `mtplx/generation.py`) runs synchronously on
    MTPLX's single owner thread with no event loop at the call site;
    converting it to async would be exactly the invasive scheduler
    change the brief says to avoid.
  - Hooked into `generate_ar`'s classic/default AR decode loop
    (`generation.py:5951+`) via a new optional `resource_governor`
    keyword parameter (default `None` — existing call sites unchanged,
    zero behavior change when unset). The pacing call sits right after
    the loop's existing `_eval()`/`mx.async_eval()` dispatch, so on the
    shipped default (`_ar_sync_eval=True`) it's a real post-GPU-dispatch
    boundary, not a guess (see IMPLEMENTATION_NOTES.md Q3/Q5). Only the
    classic AR lane is hooked so far — the opt-in `MTPLX_AR_PIPELINE`
    lane, MTP (`generate_mtpk`), and batched decode are not yet wired
    (tracked in "Next up").
  - Tests: `tests/test_resource_governor.py` (25 unit tests, pure timing
    math — validation, duty-cycle formula, upper-bound cap, min-tps
    floor engage/disengage/oscillation-avoidance, cancellation slicing,
    stats shape) and `tests/test_resource_governor_ar_integration.py` (6
    tests running `generate_ar` against a toy MLX model — real MLX
    compute on this machine, same pattern as the existing
    `tests/test_async_decode.py`): confirms governor-off and
    governor-at-max-profile produce byte-identical output to the
    ungoverned baseline, confirms throttled output is *also* identical
    (brief section 18 — pacing must not change model output), confirms
    wall-clock time actually increases under throttling, and confirms
    `abort_check` cuts an in-progress yield short rather than blocking
    generation.
  - Ran the new tests plus ~48 existing test files that exercise
    `generate_ar`/`generation.py` (every test file found via
    `grep -rl generate_ar tests/`) — only the same pre-existing,
    already-documented RAM-preflight failure
    (`test_laguna_model.py`) appeared; nothing else regressed.
  - **Not yet done**: validation against a real downloaded model (still
    blocked on disk space — the toy-model integration tests are real MLX
    execution but not a production-scale model); the "real inference
    demonstrates the expected throughput/pacing difference" half of
    brief section 33's milestone-1 definition needs that. Duty-cycle
    values have not been benchmarked/tuned on real hardware (brief
    section 19 Test A-C) — not applicable until Phase 8.
- [x] Phase 2 — prefill governor implemented and tested:
  - Threaded a new `resource_governor: ResourceGovernor | None = None`
    parameter from `generate_ar` → `restore_or_prefill_prompt_state` →
    `_prefill` (`generation.py`), and hooked `after_prefill_chunk` right
    after `_prefill`'s existing per-chunk `_eval(prefill)`/
    `_eval_cache_roots(cache)` sync call — same "pace after the existing
    sync point, don't add a new one" discipline as Phase 1's decode hook.
  - **Notable discovery, not assumed from the brief or the Phase 0
    recon**: MTPLX's chunked-prefill spans (`_iter_prefill_chunk_spans`)
    only actually chunk when `MTPLX_SUSTAINED_PREFILL` is truthy —
    otherwise the whole prompt body is treated as one span regardless of
    `--prefill-chunk-tokens`/the chunk-size override. This is **not** a
    dead code path in practice, though: `mtplx/profiles.py`'s
    `SUSTAINED_PROFILE` (`name="sustained"`, the literal
    `DEFAULT_PROFILE_NAME`) sets `MTPLX_SUSTAINED_PREFILL=1`, and
    `TURBO_PROFILE` inherits the same env block — so chunked prefill (and
    therefore this governor hook) is active under MTPLX's actual shipped
    default and fastest-decode profiles, just not under every profile.
    Worth remembering when writing Phase 4's config/CLI docs so the
    governor's own docs don't imply prefill pacing is universal when the
    underlying chunking itself is profile-gated.
  - Chose plain `_prefill()` (`generation.py:5317+`) over
    `_prefill_with_hidden_sequence()` (which Phase 0's IMPLEMENTATION_NOTES.md
    called "the representative case" for reconnaissance purposes) after
    tracing the actual call graph directly: `_prefill_with_hidden_sequence`
    has exactly one caller, reached only when MTP history policy is
    `committed`/`last_window` *and* sustained prefill is disabled — a
    narrow combination. `_prefill()` is what `generate_ar`'s default
    `mtp_history_policy="cycle"` cold-start path actually calls, and it's
    shared by `generate_mtp1`/`generate_mtpa` too — broader real coverage
    for the same amount of code touched. IMPLEMENTATION_NOTES.md's
    original citation isn't wrong for what it was read for (which sync
    call exists and where), just not the highest-value integration point;
    not correcting that doc, this supersedes it for hook-placement
    purposes.
  - Tests: `tests/test_resource_governor_prefill_integration.py` (6
    tests, same toy-MLX-model pattern as Phase 1, using
    `MTPLX_SUSTAINED_PREFILL=1` + a forced small chunk size to exercise
    multiple chunks per prefill) — confirms governor-off/max-profile are
    no-ops, confirms a multi-chunk prefill fires the pacing hook once per
    chunk, confirms throttled and unthrottled prefill produce identical
    output, confirms wall-clock time actually increases under throttling,
    and confirms a single-token prompt (no chunk loop at all) reports
    zero prefill steps without erroring.
  - Ran the new tests plus the same ~48-file regression batch used for
    Phase 1, plus `tests/test_prefill_chunk_defaults.py` and
    `tests/test_generation_sustained.py` directly (54 tests) — only the
    same pre-existing `test_laguna_model.py` RAM-preflight failure
    appeared; nothing else regressed.
  - Diff to `generation.py` for this phase: 4 small, additive edits (two
    new optional parameters threaded through, one `time.perf_counter()`
    capture split out, one new `if resource_governor is not None:`
    block) — no existing behavior changed when `resource_governor` is
    unset.
- [x] Phase 3 — runtime admin API and live profile switching implemented
  and tested:
  - `ServerState.__init__` (`server/openai.py`) now constructs
    `self.resource_governor = ResourceGovernor()`, defaulting to the
    `max` profile — zero intentional pacing until an operator explicitly
    switches it. The live `generate_ar(...)` call site inside
    `_run_generation` (`server/openai.py`, `_run_generation`'s AR branch)
    now passes `resource_governor=state.resource_governor`, and — for
    free, no extra plumbing needed — the same lambda already used for
    `abort_check` (`cancel_event.is_set() or _pressure_abort_requested(state)`)
    is what the governor's yields poll for cancellation-safety, so a
    client disconnect or shed-under-pressure event interrupts an
    in-progress governor sleep exactly like it already interrupts
    generation.
  - New routes in `create_app` (`server/openai.py`), added next to the
    existing `/admin/*` family: `GET /admin/resource-governor` (returns
    `ResourceGovernor.stats()`) and `POST /admin/resource-governor/profile`
    (body: `{"profile": "interactive"}`, validated by a Pydantic
    `Field(pattern=...)` built from `BUILTIN_PROFILES` so the two can't
    drift apart). **Deviated from the brief's illustrative `PUT`** for
    the profile-switch route — every other mutation endpoint in this
    file (`/v1/mtplx/settings`, fan mode, cache/session clear) is `POST`,
    and the brief itself says to follow existing MTPLX convention over
    its own illustrative example.
  - No new auth code needed, as Phase 0 recon predicted: the global
    `_AuthRateLimitMiddleware` already covers these new routes
    automatically. Verified this explicitly with a test rather than
    trusting the prediction (`test_resource_governor_admin_endpoints_require_auth`).
  - Structured logging on profile change, matching the brief's exact
    example format: `resource governor profile: max -> protect (source:
    runtime API)`, logged via the existing `mtplx.server.openai` logger.
    Only logs on an actual change (setting the same profile again is a
    no-op, no log spam).
  - Registered the two new routes in the existing
    `_mtplx_app_capabilities()` endpoint-discovery map and added a
    `"resource_governor": True` feature flag, matching how every other
    admin surface in that map is already discoverable by native app
    shells/the dashboard.
  - Tests: 5 new tests in `tests/test_server_openai.py` using the
    existing `_fake_state()`/`TestClient` pattern (added
    `resource_governor=ResourceGovernor()` to `_fake_state()`'s
    `SimpleNamespace`, one line, since ~50 other tests share that
    helper) — GET returns the default max-profile stats shape, POST
    switches the profile live (verified via both the HTTP response and
    direct in-process state inspection — "no restart" isn't just
    asserted, it's proven by reading `state.resource_governor` right
    after the request), an invalid profile name is rejected with 422 and
    leaves state unchanged, unauthenticated requests get 401 and don't
    mutate state, and the structured log line fires with the exact
    expected format on a real transition.
  - Ran the new tests plus the full `tests/test_server_openai.py` (365
    tests) and `tests/test_dashboard_endpoints.py`, then a ~75-file
    regression batch covering everything else that touches
    `server/openai.py` — only the same two already-documented
    pre-existing failure categories appeared (low-disk SSD guard,
    numerical precision in `test_graphbank_compiled_verify.py`); nothing
    new broke.
  - **Not yet done**: exercising the admin API against an actual running
    `mtplx serve` process end-to-end (only `TestClient` against a
    fake/toy state so far); CLI/config-driven profile selection at
    startup is explicitly Phase 4, not done here — right now the *only*
    way to change the profile is the new HTTP API, and it always starts
    at `max` on every server start.

## Blocked / needs investigation

- [ ] Real-hardware validation (Phase 8 Moonlight acceptance test, and
      generally any 96GB-scale memory-pressure testing) cannot happen on
      this dev machine (M4 Max, 36GB) — needs the actual target M5 Ultra
      Mac Studio.
- [ ] This dev machine's disk is chronically near-full (was down to 117MB
      free at one point this session, unrelated to this project). MTPLX
      itself disables SSD-tier features below 10GiB free, and model
      downloads need several GB of headroom. The real-inference smoke test
      is on hold until more space exists; not something to try to fix
      unilaterally (see LLM_NOTES.md).
- [x] Whether MTPLX already has a reusable thermal/profile abstraction —
      answered: `mtplx/thermal.py`/`fan_mode.py` are a good *pattern*
      precedent (CLI verb + status + HTTP mirror) but operate on a
      different axis (external fan-control tool) with nothing directly
      reusable at the implementation level. See IMPLEMENTATION_NOTES.md Q13.

## Bugs discovered

All found via the full `pytest tests/` baseline run on this dev machine
(M4 Max, 36GB RAM, MLX 0.32.2). None are in scope for this project to fix;
recorded for completeness per brief section 25's testing discipline.

- **48 failures — environment, not a bug**: MTPLX's own low-disk safety
  guard (`mtplx/cache_bank/cold_tier.py:668,1462`, "SessionBank SSD
  writes/spill disabled (low_disk): free disk below 10 GiB") correctly
  fires because this dev machine's disk was at ~2.3GB free during the run.
  Affects `test_cache_bank.py` (12), `test_ssd_spill.py` (9),
  `test_hf_loader.py` (7, via `_require_download_disk_headroom`),
  `test_cold_prefix_ram_shadow.py` (6), `test_idle_persistence_pump.py`
  (4), `test_cold_tier_min_useful_matched.py` (3),
  `test_cold_tier_foreground_yield.py` (3), `test_ssd_boundary_repersist.py`
  (2), `test_cold_tier_disk_usage_scan.py` (2). Would very likely pass on a
  machine with >10GiB free.
- **1 failure — environment, not a bug**: `test_laguna_model.py::
  test_laguna_s_2_1_ar_route_skips_qwen_performance_hooks` — the code's own
  `_preflight_laguna_system_memory()` (`mtplx/runtime.py:78`) correctly
  raises because Laguna-S-2.1 needs 85.3GiB unified memory and this machine
  has 36GB; the test doesn't mock the RAM-detection function. Would pass on
  the target M5 Ultra (96GB).
- **15 failures — genuine, pre-existing, out of scope**: numerical
  bit-exactness/precision mismatches, plausibly specific to this
  M4 Max + MLX 0.32.2 combination (untested whether they also fail on
  upstream's own CI/hardware). `test_sdpa_nax_tile.py` (8/8 parametrized
  cases fail — custom NAX-tile attention kernel deviates from the fp32
  reference by up to ~0.059 vs. a 0.02 tolerance). `test_graphbank_compiled_verify.py`
  (6) and `test_ccopy_bank_route.py` (1) — compiled-verify-bank output
  differs from the eager reference at the ~1e-6..1e-5 level (e.g.
  `-0.40190518` vs `-0.40190467`), failing a strict `np.array_equal`. These
  are upstream MTPLX correctness issues unrelated to the resource governor
  and should not be "fixed" as part of this project; flag to upstream
  separately if the user wants to.

## Benchmark backlog

- Test A-F from brief section 19 (baseline, balanced, interactive, long
  prefill, long decode, live profile transition) — not started, blocked on
  Phase 1+ implementation existing.
- Real Moonlight acceptance test (brief section 20) — blocked on target
  hardware (M5 Ultra) and on the governor being implemented.

## Upstream sync status

- Upstream remote: `https://github.com/youssofal/MTPLX.git`
- Fork remote (`origin`): `https://github.com/digitalgravy/MTPLX.git`
- Base commit for this branch: `e652d55e2652137a4abcf1312357abbf3eb9d692` (upstream `main`, 2026-09-01)
- `feature/resource-governor` branched directly from `main` at that commit; no rebases yet.

## Roadmap

Phases per brief section 23: 0 reconnaissance (in progress) → 1 minimal
decode governor → 2 prefill governor → 3 runtime profile switching → 4
profile/config integration → 5 scheduler/concurrency integration → 6 memory
protection → 7 `mtplx-qos` companion → 8 M5 Ultra tuning → 9 upstream
preparation.
