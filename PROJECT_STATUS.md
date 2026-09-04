# MTPLX Resource Governor Project Status

## Current objective

**The v0/v1 feature set is complete, documented, and now has a first
real-world Moonlight result.** Every phase through Phase 7 (core
governor, AR/MTP/pipeline decode pacing, all reachable prefill
functions, runtime admin API, CLI/config integration, basic admission
enforcement, the `mtplx-qos` companion tool) is done and tested. Full
documentation suite is written (`README.md`, `ARCHITECTURE.md`,
`PLAIN_LANGUAGE_GUIDE.md`, `BENCHMARKS.md`, `UPSTREAM_STATUS.md`, plus
`IMPLEMENTATION_NOTES.md`). An external community tester installed the
fork, hit and helped debug three install/PATH gotchas plus one genuine
code bug (`--resource-profile` and friends were parsed but never
forwarded to the actual server subprocess — now fixed; see
"Install-flow issues found by a real user" below for all four), and then
ran an actual Moonlight session: **0.64 FPS (unplayable) at `max` →
90 FPS decode / 60 FPS rendered at `interactive`**. See
`BENCHMARKS.md`'s "Real Moonlight test" entry — this is the first actual
evidence the project's north-star claim holds, even on non-target
hardware. What remains is genuinely out of reach on this dev machine
(Phase 8: M5 Ultra tuning + the *full* Moonlight test with target
hardware and complete metrics) or deliberately deferred as
architecturally riskier (deeper memory-pressure admission, the narrow
A3B batched lane). Also newly done: a click-button admin-API UI
(`scripts/mtplx-qos-ui.html`) and a one-command launcher
(`scripts/mtplx-qos-run`) — see "Next up" for details.

## In progress

Nothing currently in progress.

## Install-flow issues found by a real user (2026-09-04)

An external tester (first person other than this session to install and
run the fork) hit four real problems in sequence — three environment/docs
issues, and one genuine code bug this session initially misdiagnosed as
a fourth environment issue before the tester pushed back with "it's got
the cli flag thing wrong" and fresh evidence:

1. **PATH shadowing**: official MTPLX installs put a launcher shim on
   `PATH` that always execs one specific, separately-managed Python
   environment. A plain `pip install` elsewhere never updates it. Fixed
   in docs by leading unconditionally with an isolated venv rather than
   trying to detect/patch the existing install.
2. **The shim's target location isn't consistent**: the curl installer
   uses `~/.mtplx/venv`; the Mac App (DMG) uses
   `~/Library/Application Support/MTPLX/runtime-venv` (app-managed, and
   risky to touch directly — could get silently reset by the app's own
   update logic). Confirmed both are real by reading `install_macos.sh`
   and the tester's own `cat "$(which mtplx)"` output. Docs no longer
   try to enumerate/guess every possible layout.
3. **zsh/bash command-hash caching**: even with a venv correctly
   activated (prompt showing it), a shell that already resolved `mtplx`
   earlier in the same session can keep using the stale cached location.
   `hash -r` fixes it; calling the venv's `mtplx` by absolute path
   sidesteps it entirely and always works.
4. **Real code bug — `--resource-profile` and friends were parsed but
   never forwarded to the actual server process.** `mtplx serve` doesn't
   run the FastAPI server in-process; `cmd_serve_public` spawns it as a
   subprocess with a hand-built argv (`sys.executable -m mtplx.server.openai
   ...`), and Phase 4 added the new flags to both CLI parsers but never
   added them to *this* forwarding list — so the flag parsed cleanly, the
   server "started just fine," and then always booted on `max` regardless
   of what was requested. The initial diagnosis (a stale server already
   bound to the port) was wrong and the tester correctly said so ("I
   never encounter an error where port 8000 is already bound... it starts
   just fine, but the [profile] reports [max] until manually changed").
   Found for real by reading `cmd_serve_public`'s subprocess-spawn code
   directly — it has an explicit comment warning about exactly this bug
   class ("the server runs as a subprocess with an explicitly rebuilt
   argv, so anything not forwarded here never reaches it") sitting right
   above the existing forwarding loop for sibling flags
   (`max_active_requests`, `decode_batch_max`, etc.) that should have been
   the template from the start. Two more instances of the identical bug
   found and fixed alongside it: `_with_batching_args` (the `mtplx start`
   → serve namespace handoff — same flags missing there too, would affect
   `start`/`start pi`/`start opencode`/etc.) and `_batching_command_suffix`
   (an advisory command string shown to users, cosmetic but same fix).
   Regression-tested by proving the new tests fail against the pre-fix
   code (not just pass against the fix) and confirmed live: a server
   started with `--resource-profile interactive` now reports
   `"profile":"interactive"` immediately, with `steps: 0` proving it's a
   genuinely fresh, correctly-configured process.

Items 1-3 are documented in `docs/resource-governor/README.md`'s Install
section and needed no code change. Item 4 needed a real fix — the lesson
from the wrong initial diagnosis: when an install/environment
explanation doesn't quite fit what the user is actually reporting, go
read the specific code path involved rather than defending the first
theory. Worth remembering for any future user-facing doc: the failure
mode that actually happens is rarely the one you'd predict from a clean
test.

## Next up

- [x] Basic web UI for the admin API (requested by user 2026-09-04, so
      switching profiles doesn't require `curl`) — done. Built as
      `scripts/mtplx-qos-ui.html`: a single self-contained static HTML
      file (vanilla JS, no build step, no dependency on the `mtplx`
      package), opened directly in a browser rather than served by a
      local webserver — decided in favor of "standalone file" since it
      needs zero extra process to keep alive and matches how `mtplx-qos`
      itself is already distributed (grab-the-one-file via curl). Calls
      the same two admin endpoints as `mtplx-qos`
      (`GET/POST /admin/resource-governor[/profile]`) via `fetch()`;
      server URL and API key are kept in the browser's own
      `localStorage`, nothing is sent anywhere else. Live-verified: ran
      a real `mtplx serve`, opened the page (served over a throwaway
      local `python3 -m http.server` for the browser test, since a raw
      `file://` page can't be driven by the browser-automation tool —
      the shipped file itself needs no server), pointed it at the
      running server, clicked `interactive`, and confirmed both the UI
      and `GET /admin/resource-governor` agreed on the new profile.
      Documented in `docs/resource-governor/README.md` ("Standalone
      control panel") and `PLAIN_LANGUAGE_GUIDE.md` (click-not-type
      path added ahead of the `mtplx-qos` CLI instructions).
- [x] One-command launcher (requested by user 2026-09-04, "a one-command
      run script that spins it all up for him") — done. Built as
      `scripts/mtplx-qos-run`: a bash script that starts `mtplx serve`
      (defaulting to `--resource-profile interactive`, overridable),
      polls `/health`, then opens `mtplx-qos-ui.html` via `?url=...`
      query params so the control panel is already connected with
      nothing to type (the HTML file was extended to read `?url=`/`?key=`
      on load and persist them to `localStorage`, same as a manual edit).
      Prefers this clone's own `.venv/bin/mtplx` over `PATH` resolution
      entirely, to sidestep the exact shadowing/hash-caching problems the
      external tester hit (see "Install-flow issues" above) rather than
      just documenting around them. Ctrl-C/`kill` stops the server via a
      trap that kills the child PID.
      Hit and fixed one real bug while testing: `EXTRA_ARGS=()` (the
      `--` passthrough array) plus `set -u` triggers "unbound variable"
      on macOS's stock bash 3.2 when expanded with `"${EXTRA_ARGS[@]}"`
      even after explicit empty-initialization — a documented bash <4.4
      quirk, not present in bash 4+. Fixed by guarding on
      `${#EXTRA_ARGS[@]} -gt 0` before expanding, which is safe on 3.2.
      Live-verified end-to-end (real model load, `/admin/resource-governor`
      correctly reporting `interactive` with `steps:0`) and the shutdown
      path specifically: sending SIGINT to the script while it's a
      *backgrounded* job of a non-interactive shell did nothing (that's
      correct, standard POSIX behavior — non-interactive shells set
      SIGINT to ignored for async commands, confirmed with an isolated
      repro script before concluding it wasn't a real bug), but sending
      SIGINT to it running as a normal foreground command killed the
      server cleanly (exit 130, as expected for Ctrl-C) — the realistic
      case, since nobody runs this via manual `&` backgrounding.
      Documented in `docs/resource-governor/README.md` ("One-command
      launcher" + "Quick start") and `PLAIN_LANGUAGE_GUIDE.md` (now the
      first, easiest option in "How to turn it on").
- [ ] Runtime-verify (not just statically infer) that mutating
      `state.args.max_active_requests`/`decode_batch_max` live actually
      changes admission behavior on a running server — these two keys are
      *not* currently in `DASHBOARD_MUTABLE_SETTINGS_KEYS`
      (`openai.py:15137-15155`), unlike `prefill_chunk_tokens`.
- [ ] Batched decode (`batched_decode.py`): the general
      `generate_greedy_batched()` entry point is confirmed **not** on the
      live serving path (no callers outside its own file and tests — same
      dead-scaffolding category as `MTPContinuousScheduler`). There is a
      live, narrower A3B/whole-MoE-specific batched lane
      (`install_a3b_mtp_batch_lane` in `a3b_mtp_batch.py`, imported by
      `server/openai.py`) reusing low-level primitives from
      `batched_decode.py` — deferred as an explicit follow-up given it's
      both model-family-narrow and comparably complex to the MTP hook
      (cycle-based, needs the same independent-verification discipline).
- [ ] Phase 8: M5 Ultra hardware tuning + real Moonlight acceptance test
      — needs the actual target machine, not this M4 Max dev machine.

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
  - **Not yet done at commit time**: exercising the admin API against an
    actual running `mtplx serve` process end-to-end (only `TestClient`
    against a fake/toy state so far); CLI/config-driven profile selection
    at startup was Phase 4, not this phase. **Both since done** — see the
    Phase 4 and real-model-validation entries below.
- [x] Phase 4 — CLI and persisted config integration:
  - New flags on `_add_batching_args` (`cli.py:703+`, covers `start`,
    `quickstart`, `serve` subcommands) and duplicated on the standalone
    server parser (`server/openai.py:parse_args`, which independently
    redefines `--max-active-requests`/etc. rather than sharing `cli.py`'s
    parser — matched that existing duplication for consistency):
    `--resource-profile {max,balanced,interactive,protect,pause}`,
    `--prefill-duty-cycle`, `--decode-duty-cycle`, `--min-decode-tps`.
  - New `config.py` keys (`resource_profile`, `prefill_duty_cycle`,
    `decode_duty_cycle`, `min_decode_tps`) added to `CONFIG_VALUE_KEYS`,
    `UserConfig`, `load_user_config`'s TOML parsing, and
    `_RUNTIME_DEFAULTS` — this last one is the existing generic
    CLI-explicit-beats-config precedence mechanism
    (`config.py:_apply_runtime_defaults`), so no new precedence logic was
    needed, just registering the new keys in it.
  - New `_resolve_resource_governor_profile(args)` helper
    (`server/openai.py`, right before `class ServerState`): resolves
    `--resource-profile` (default `max`) via `resolve_profile()` (raises
    a clean `ValueError` on a bad/stale config value rather than a raw
    `KeyError`), then applies any explicit
    `--prefill-duty-cycle`/`--decode-duty-cycle`/`--min-decode-tps`
    overrides on top via `dataclasses.replace` — the result keeps the
    base profile's *name* (so `GET /admin/resource-governor` still
    reports e.g. `"interactive"` even with one field tweaked), matching
    brief section 11's "explicit values override profile defaults"
    without inventing a fake "custom" profile identity.
    `ServerState.__init__` now calls this instead of always constructing
    a bare `ResourceGovernor()`, and logs the effective values at startup
    (`LOGGER.info`, one line, brief section 11's "effective configuration
    must be logged at startup").
  - Tests: 3 new `tests/test_config.py` tests (TOML round-trip +
    CLI-beats-config precedence) and 5 new `tests/test_server_openai.py`
    tests directly unit-testing `_resolve_resource_governor_profile` as a
    pure function (default max, named-profile selection, field-override-
    keeps-name, bad-config-value rejection, bad-override-value
    rejection) — chose this over constructing a real `ServerState`
    because that requires heavy model-load mocking already used
    elsewhere in the file for unrelated purposes; the resolution logic
    itself needs only `args` in and a `ResourceProfile` out.
  - Ran the new tests plus `test_config.py`/`test_config_profile_precedence.py`
    /`test_public_cli.py`/`test_no_mlx_imports.py` full files, the full
    `test_server_openai.py` (370 tests), and a 15-file batch covering
    everything else touching `cli.py`/`config.py` — all clean, no
    failures at all in this batch (not even the usual pre-existing ones,
    since disk space was healthy for this run — see below).
- [x] **Disk space was fixed by the user** (2026-09-04): jumped from
  723MB to 143GB free between two checks. The earlier chronic low-disk
  condition (documented extensively above and in LLM_NOTES.md) was
  external to this project and is now resolved. Retried and completed
  the `Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed` download (2.6GB) that
  had been blocked since Phase 0.
- [x] **Real-model validation, end-to-end, via a live `mtplx serve`
  process** (not `TestClient`, an actual server subprocess + `curl`):
  - `mtplx run` sanity check: the model generates correctly, ~93 tok/s
    baseline via MTP depth 3.
  - Started `mtplx serve --model Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed`,
    confirmed `GET /admin/resource-governor` starts at `max` with zero
    steps recorded, sent a `POST /v1/chat/completions` request (150
    tokens) at `max` (~1.2s), then `POST /admin/resource-governor/profile
    {"profile": "interactive"}` **live, no restart**, then the same
    request again.
  - **First attempt showed zero governor activity** (`"steps": 0` on both
    lanes) despite the profile switch succeeding — see the critical
    finding below; this was because the request used the model's default
    `generation_mode: "mtp"`, which isn't hooked.
  - Re-ran with `"generation_mode": "ar"` explicit in the request body:
    governor fired correctly — prefill: 1 step, `effective_duty_cycle`
    exactly `0.4` as configured, one ~122ms yield; decode: 29 steps,
    `effective_duty_cycle` exactly `0.4`, `natural_tps_ema` ≈95.3 tok/s
    (this model's real unthrottled decode speed on this M4 Max),
    `delivered_tps_ema` ≈38.1 tok/s (95.3 × 0.4 ≈ 38.1 — matches the
    formula almost exactly), 29 yields totaling ≈459ms. `min_decode_tps`
    floor (15.0) correctly did not engage since 38.1 > 15.
  - **This is real proof the core duty-cycle mechanism works correctly
    on genuine MLX inference against a real downloaded model** — not a
    toy model, not a mock. Satisfies brief section 25's "at least one
    real MLX inference workload must validate each prefill/decode hook"
    for the AR decode and plain-`_prefill` prefill hooks specifically.
  - **Critical finding, see "Current objective" above**: this model's
    *default* `generation_mode` is `"mtp"`. On a real server with no
    per-request override, **the governor currently does nothing at all**,
    because Phase 1/2/3 only wired `resource_governor` into the
    `generate_ar` call path in `_run_generation`, not the `generate_mtpk`
    (else) branch. This was known/documented as an explicit scope cut
    since Phase 1 ("classic AR lane only"), but its *practical
    significance* — that it means the governor is inert on most real
    default-configured MTPLX servers — only became concrete once tested
    against a real MTP-capable model's actual default behavior. Promoted
    to top priority.
- [x] **MTP decode hook (`generate_mtpk`) — done, gap closed.**
  - Forked out the investigation (tracing `generate_mtpk`'s ~5,300-line
    decode loop, resolving IMPLEMENTATION_NOTES.md's open Q6/Q7) to avoid
    loading all of it into context, then **independently re-verified the
    fork's key claim myself before writing any code** — good thing: the
    fork recommended hooking pacing inside `emit_new_tokens()` (a helper
    called from ~10 branch sites, looked like a single per-cycle
    convergence point), but reading the actual call sites showed several
    fire **mid-cycle** (e.g. immediately after the primary token is
    sampled, before that cycle's draft-forward/target-verify work has
    even happened — one site's own comment says as much). Hooking there
    as originally planned would have double-counted tokens and, worse,
    folded a prior governor sleep's duration into the next call's "work"
    measurement — a real correctness bug in speculative-decoding timing,
    not cosmetic.
  - **Redesigned before writing the real implementation**: pace at the
    top of the `while` loop against the *previous* iteration's measured
    wall time and token delta, before that iteration's own work begins,
    instead of inside `emit_new_tokens()`. This sidesteps the
    multi-call-per-cycle ambiguity entirely — "top of iteration N" is a
    single unambiguous point regardless of how many internal calls
    iteration N-1 made. Full reasoning recorded in
    `docs/resource-governor/IMPLEMENTATION_NOTES.md` section 5 (also
    resolves Q6/Q7 there) and inline code comments at the hook site.
  - Threaded a new `resource_governor` parameter through `generate_mtpk`
    (also passed into its own internal `restore_or_prefill_prompt_state`
    call, which — for free, reusing all of Phase 2's existing work —
    gives MTP-mode prefill pacing too, not just decode), and into
    `_run_generation`'s `generate_mtpk(...)` call site in
    `server/openai.py` (the branch next to the already-hooked
    `generate_ar` branch).
  - Tests: `tests/test_resource_governor_mtp_integration.py` (6 tests,
    real MLX compute via the same deterministic cyclic toy-model pattern
    `tests/test_loop_guard.py` already uses for `generate_mtpk`) —
    confirms governor-off/max-profile are no-ops, confirms throttled and
    unthrottled output are byte-identical, confirms wall-clock actually
    slows down, confirms `abort_check` cuts a pending yield short, and
    **a dedicated regression guard**
    (`test_yields_never_exceed_one_per_cycle_transition`) asserting
    `yields <= steps` and `steps < token_count` — this test exists
    specifically to catch a reintroduction of the double-counting bug
    the corrected design avoids.
  - Ran the new tests plus a ~30-file regression batch covering
    everything that touches `generate_mtpk` — only the same 7
    already-documented pre-existing numerical-precision failures
    (`test_graphbank_compiled_verify.py` ×6, `test_ccopy_bank_route.py`
    ×1) appeared; nothing new broke.
  - **Real-model validation, live server, default generation mode** (the
    actual point of this work): started `mtplx serve` with
    `Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed`, sent a 150-token
    request with `generation_mode` left at its default (**not** forced
    to `"ar"` this time — confirming the fix, not just the mechanism) at
    `interactive`: 3.11s, `decode.effective_duty_cycle: 0.4` exactly,
    `natural_tps_ema` ≈139.3, `delivered_tps_ema` ≈55.7 (139.3 × 0.4 ≈
    55.7). Same request at `max`, switched live via the admin API: 1.03s.
    **This is the fix working end-to-end on the model's actual default
    configuration** — the scenario that was completely inert before this
    hook.
  - Not yet done: `MTPLX_AR_PIPELINE` lane, batched decode
    (`batched_decode.py`), and the remaining three prefill functions are
    still unhooked (see "Next up").
- [x] **Full decode-lane and prefill-function coverage — done.**
  - **`MTPLX_AR_PIPELINE` lane** (`generate_ar`'s pipelined decode
    while-loop, `generation.py`): much simpler than the MTP hook — this
    loop commits exactly one token per iteration with a single clear
    sync point (`tok_lazy.item()`), confirmed by reading the loop before
    hooking (not assumed from the MTP lesson — verified this one
    genuinely doesn't share MTP's multi-call-per-cycle hazard). Direct
    per-token hook, same shape as the classic AR lane's. No new parameter
    threading needed — this lane lives inside `generate_ar`, which
    already has `resource_governor`/`abort_check` in scope from Phase 1.
  - **All three remaining prefill functions hooked**:
    `_prefill_restored_prompt_suffix` (warm SessionBank restore — both
    its fused single-shot path and its chunked path),
    `_prefill_committed_mtp_history_streaming` (the real default MTP
    prefill path, see below), and `_prefill_with_hidden_sequence`
    (needed a new `abort_check` parameter added — it didn't accept one
    before, so the governor yield there is now interruptible where it
    previously couldn't have been checked at all). Threaded
    `resource_governor` through the intermediate wrapper functions
    (`_restore_near_prefix_prompt_state`, both its call sites inside
    `restore_or_prefill_prompt_state`) needed to reach them from
    `generate_ar`/`generate_mtpk`.
  - **Second real gap found and closed, same session**: while wiring
    `_prefill_committed_mtp_history_streaming`, tracing the branch logic
    in `restore_or_prefill_prompt_state` showed that `generate_mtpk`'s
    own call passes `mtp_history_policy="committed"`, and — combined with
    `MTPLX_SUSTAINED_PREFILL` being on under the shipped default
    "sustained" profile — this means **real default MTP requests route
    prefill through `_prefill_committed_mtp_history_streaming`, not the
    plain `_prefill()` hooked back in Phase 2.** This exactly explains
    why the MTP-hook milestone's live-server test showed `"prefill":
    {"steps": 0}` even with decode pacing working correctly. Confirmed
    fixed with a fresh live-server test after this batch:
    `GET /admin/resource-governor` now shows `"prefill": {"steps": 1,
    "yields": 1, ...}` for a genuinely default-configured request (no
    `generation_mode` override at all).
  - Followed the same "verify before hooking" discipline the MTP lesson
    established: read each function's actual chunk-loop/call-site
    structure before assuming a hook point was safe, rather than
    pattern-matching from Phase 2's `_prefill()` shape. Also checked
    whether `batched_decode.py`'s `generate_greedy_batched()` was worth
    hooking the same way — confirmed via grep it has no callers outside
    its own file and tests, i.e. it's dead on the live path (same
    category as `MTPContinuousScheduler` from Phase 0 recon); deferred
    rather than hooking dead code. Found the real live batched path is
    narrower (A3B/whole-MoE-specific, `a3b_mtp_batch.py`) and deferred
    that too given its similar cycle-based complexity to the MTP hook.
  - Tests: 3 new files —
    `tests/test_resource_governor_ar_pipeline_integration.py` (7 tests,
    real MLX compute via a model that genuinely engages the pipeline
    lane, unlike the existing `test_async_decode.py` test which
    deliberately refuses engagement to test gating alone — includes an
    explicit `test_pipeline_lane_actually_engages_in_this_fixture` sanity
    check so a future upstream gating change can't silently make the
    other tests exercise the wrong code path),
    `tests/test_resource_governor_prefill_alt_paths_integration.py` (9
    tests reusing `tests/test_generation_sustained.py`'s own proven
    fixtures/mocks for these exact functions rather than building new
    ones from scratch). All confirm governor-off/max-profile are no-ops,
    throttled output matches unthrottled, wall-clock actually slows, and
    step/yield counts match expected chunk counts.
  - Ran the new tests plus `test_generation_sustained.py`,
    `test_a3b_whole_moe.py`, `test_laguna_model.py`, the full governor
    test suite, and `test_server_openai.py` — only the same pre-existing
    `test_laguna_model.py` RAM-preflight failure appeared.
- [x] **Phase 5 — basic admission enforcement, done.** Kept deliberately
  conservative per the brief's own guidance (section 13: "If dynamically
  changing scheduler concurrency safely at runtime is architecturally
  risky, separate that from phase-one pacing"):
  - New `_reject_if_resource_governor_admission_closed(state)` helper in
    `server/openai.py`, same shape/placement as the existing
    `_reject_prompt_over_context` — called at the very top of both
    `/v1/chat/completions` and `/v1/completions`, before any prompt
    encoding or generation setup. Uses `ResourceGovernor.admission_allowed()`
    (implemented since Phase 1, unused until now) — `protect`/`pause`
    profiles refuse new requests with a clean `503` and a clear reason
    (`{"code": "resource_governor_not_admitting", "profile": "..."}`,
    matching brief section 9's "return a clear error/reason"); every
    other profile, or a disabled governor, passes through untouched.
  - Deliberately did **not** touch live concurrency mutation
    (`max_active_requests`/`decode_batch_max`) or memory-pressure-based
    admission (Phase 6) — those need the deeper `AdmissionPolicy`/
    `MemoryPlan` integration IMPLEMENTATION_NOTES.md already flagged as
    dormant/unwired, and are exactly the kind of "architecturally risky"
    work the brief says to defer rather than force. This phase only
    covers the simple, safe case: refuse new requests outright when the
    profile says so.
  - Tests: 9 new tests in `tests/test_server_openai.py` — HTTP-level
    503 checks for both endpoints (via `TestClient`, confirming
    `_run_generation` is never reached — mocked to `pytest.fail()` if
    called), a check that a rejected request doesn't itself mutate
    governor state, and direct function-level checks that every
    non-blocking profile (and a disabled governor) pass through without
    raising. (A full HTTP-level "request succeeds end-to-end" test
    would have needed much deeper `_fake_state()` mocking unrelated to
    this check — session resolution, etc. — so that case is covered at
    the function level instead, which is what actually needed proving.)
  - Ran the full `test_server_openai.py` (377 tests) — clean.
  - **Validated live**: started `mtplx serve` with the real downloaded
    model, switched to `protect` via the admin API, confirmed
    `POST /v1/chat/completions` returns a clean `503` with the exact
    reason message before any generation starts; switched back to `max`
    and confirmed the same request completes normally (`200`, real
    generated tokens).
- [x] **Phase 7 — `mtplx-qos` companion tool, done.** `scripts/mtplx-qos`
  (executable, `#!/usr/bin/env python3`, stdlib-only — no `mtplx` package
  import, deliberately: mechanism lives in MTPLX, policy lives here per
  brief section 3/15).
  - Manual: `mtplx-qos {max,balanced,interactive,protect,pause}` (POSTs
    to `/admin/resource-governor/profile`), `mtplx-qos status` (GETs
    `/admin/resource-governor`). `--url`/`$MTPLX_QOS_URL` for a non-default
    server address; `$MTPLX_QOS_API_KEY`/`$MTPLX_API_KEY` sent as both
    `x-api-key` and `Authorization: Bearer` if the server requires auth.
  - Auto: `mtplx-qos auto` (decide once and apply), `--dry-run` (decide
    and print only), `--watch SECONDS` (loop). Initial policy exactly per
    brief section 15: Moonlight running → `interactive`, else →
    `balanced` — a starting hypothesis, not a tuned claim. Process
    detection via `pgrep -if <pattern>`, wrapped so a missing `pgrep` or
    any detector exception degrades to "not detected" rather than
    crashing auto mode.
  - Detector registry (`INTERACTIVE_TRIGGER_DETECTORS`) is a plain dict
    of `name -> callable`, checked in order, first match wins — adding a
    new interactive-trigger app (or later, non-process signals like idle
    state or screen lock) is a one-line addition, no changes to the
    decision logic itself, per brief section 15's explicit "keep the
    process detector modular" instruction.
  - Tests: 19 in `tests/test_mtplx_qos_tool.py`, loaded by file path
    (SourceFileLoader) since the script has no `.py` extension and isn't
    part of the `mtplx` package — decision logic (trigger fires/doesn't,
    detector-crash resilience, check-order-stops-at-first-match), CLI
    parsing, and the HTTP client (mocked `urllib.request.urlopen` —
    correct method/URL/body, both auth headers sent when an API key is
    set and neither when it isn't, clean `SystemExit` with the server's
    own error detail on HTTP errors, a helpful "is `mtplx serve`
    running?" hint on connection failure).
  - **Validated live against the real server** — and turned up a small
    surprise: `mtplx-qos auto` correctly reported `"moonlight is
    running"` and switched to `interactive` on the very first live test,
    because Moonlight (`Moonlight AV1.app`) genuinely was running on this
    dev machine at the time. Not a bug — a real end-to-end confirmation
    of the exact detector the whole project exists to support, found
    incidentally rather than staged. Separately confirmed the `balanced`
    branch and detector-crash handling in isolation (mocked detectors,
    no real Moonlight dependency).
- [x] **Full documentation suite — done**, per explicit user request to
  "ensure there's documentation for implementing this change" plus "a
  plain-speaking version... for people who maybe aren't as technically
  minded, but want to utilise this system":
  - `docs/resource-governor/README.md` — technical quick reference:
    what's actually enforced today vs. reported-but-not-yet (an explicit,
    important honesty note — profile `max_active_requests`/
    `decode_batch_max`/`prefill_chunk_tokens` are visible in the stats
    API but not yet wired to real scheduler behavior, only duty-cycle
    pacing and protect/pause admission refusal are live), CLI flags,
    config keys, the admin API with a real captured JSON example,
    `mtplx-qos` usage, correctness guarantee, known limitations.
  - `docs/resource-governor/ARCHITECTURE.md` — why Apple Silicon
    contention happens, why duty cycling over a fixed tok/s cap, why each
    of the three decode-lane shapes needed a different hook design (with
    the MTP double-counting bug as the concrete illustration), the
    MLX-laziness hazard, live profile-switching semantics, the
    mechanism/policy split.
  - `docs/resource-governor/PLAIN_LANGUAGE_GUIDE.md` — the explicitly
    requested non-technical version. No jargon (or jargon explained in
    one plain sentence where unavoidable), written for someone who owns
    this Mac and games on it but doesn't write code: what this is (one
    paragraph, plain analogy), the five profiles explained by what
    you'd actually notice, step-by-step `mtplx-qos` usage including
    `auto --watch`, an honest caveat about what's and isn't actually
    enforced yet (in plain terms, not just the technical doc's version),
    and simple troubleshooting.
  - `docs/resource-governor/BENCHMARKS.md` — every real measurement
    taken this session (AR-mode decode pacing, MTP-mode decode pacing,
    the full-coverage default-request confirmation with real captured
    JSON, admission enforcement, `mtplx-qos auto`'s live Moonlight
    detection), clearly labeled as M4 Max dev-machine numbers, not
    target-hardware tuning data — plus an explicit, unstarted checklist
    of brief section 19's Test A-F and section 20's Moonlight acceptance
    test, none of which are meaningfully runnable on this dev machine.
  - `docs/resource-governor/UPSTREAM_STATUS.md` — base commit, all 9
    commits on this branch summarized in order, why the diff is
    additive-only by construction, three documented deviations from the
    brief's illustrative sketches (async API, `PUT` vs `POST`, which
    prefill function is "the" representative one) with reasoning for
    each, and open questions to resolve before an actual upstream PR
    (none filed yet — this fork was built speculatively, not from an
    upstream-solicited RFC).
  - Verified every internal cross-link between these files resolves to
    an existing file, and spot-checked factual claims against the actual
    CLI (`--config` does **not** exist as a flag on `serve`, only
    `$MTPLX_CONFIG` — caught and fixed before committing) rather than
    trusting memory of what flags should exist.

## Blocked / needs investigation

- [ ] Real-hardware validation (Phase 8 Moonlight acceptance test, and
      generally any 96GB-scale memory-pressure testing) cannot happen on
      this dev machine (M4 Max, 36GB) — needs the actual target M5 Ultra
      Mac Studio.
- [x] This dev machine's disk was chronically near-full for most of this
      session (down to 117MB free at one point), unrelated to this
      project — user fixed it 2026-09-04 (now 143GB free). If it recurs,
      don't try to fix it unilaterally; ask (see LLM_NOTES.md for what
      happened when this wasn't handled carefully the first time).
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
- **New, discovered while regression-testing the MTP hook — test-order
  pollution, pre-existing, out of scope**: running
  `tests/test_no_mlx_imports.py tests/test_public_cli.py tests/test_runtime_kpis.py`
  (CONTRIBUTING.md's own recommended smoke subset) followed by
  `tests/test_loop_guard.py` in the *same* pytest process makes
  `test_generate_mtpk_guard_breaks_the_cycle` fail with `RuntimeError:
  capture commit failed after MTPLX_SKIP_VERIFY_SNAPSHOT=1`
  (`generation.py:12097`, or `:12067` before the MTP hook's line
  additions — same bug, confirmed via `git stash` that it reproduces
  identically on the pre-MTP-hook code, so it's not something this
  project introduced). Passes in isolation and passes as part of the
  full `pytest tests/` run (alphabetical collection order runs
  `test_loop_guard.py` before the three polluting files, so it never
  surfaced in the Phase 0 baseline). Something in one of those three
  files leaves global/env state that `test_generate_mtpk_guard_breaks_the_cycle`
  is sensitive to; root cause not investigated (out of scope for this
  project) — worth a `git bisect`-style narrowing if the user wants to
  fix it, or just avoid chaining ad-hoc file lists after that specific
  smoke subset in one pytest invocation.
- **New, found by a real external user (2026-09-04), in scope, fixed**:
  `mtplx serve --resource-profile interactive` (and the equivalent
  `--prefill-duty-cycle`/`--decode-duty-cycle`/`--min-decode-tps` flags)
  parsed fine and the server "started just fine," but the spawned server
  subprocess silently booted on the default `max` profile anyway — the
  flags never actually reached the running server. Root cause:
  `cmd_serve_public` (`mtplx/commands/public.py`) doesn't run the server
  in-process; it spawns it via `sys.executable -P -m mtplx.server.openai
  ...` with a hand-built subprocess argv (`cmd = [...]`), and these 4
  resource-governor flags were never added to that forwarding list (there
  is a pre-existing code comment on this exact pattern warning that
  anything not explicitly forwarded there never reaches the server — the
  Phase 4 hook work should have followed it as a template and didn't).
  Same bug class also existed in `_with_batching_args` (the `mtplx
  start`→`serve` namespace handoff used by `start pi/opencode/swival/
  hermes/...`) and `_batching_command_suffix` (the advisory copy-paste
  command string). Fixed in all 3 locations; regression-tested in
  `tests/test_public_cli.py` (4 new tests, confirmed to fail against the
  pre-fix code via `git stash`) and live-verified end-to-end (a fresh
  `mtplx serve --resource-profile interactive` immediately reporting
  `"profile":"interactive"` with `"steps":0` via
  `GET /admin/resource-governor`). My first diagnosis of the user's
  report — a stale background server absorbing requests while a
  port-conflict error silently killed new ones — was wrong; the user
  correctly pushed back ("I never encounter an error where port 8000 is
  already bound... it starts just fine, but the reports [go] nuts until
  manually changed"), which is what prompted re-reading the actual
  subprocess-spawn code instead of re-asserting the first theory.
- **New, found by the same external user (2026-09-04), in scope, fixed**:
  `scripts/mtplx-qos-ui.html` — profile buttons updated correctly on
  click, but the prefill/decode stats panel never updated on its own
  ("the details in the little panel never update even though the
  buttons do"). Root cause: `apiFetch()`'s `GET /admin/resource-governor`
  polling request had no cache-control of any kind (no `cache` fetch
  option, no cache-busting), and the endpoint itself sets no
  `Cache-Control` header — so a browser was free to serve a stale cached
  response for the identical repeated URL, while each profile-switch
  `POST` always hit the network for other reasons (different method,
  mutating request) and so always looked live. Fixed by adding
  `cache: "no-store"` and a `_=<timestamp>` cache-busting query param to
  every admin request. Live-verified: opened the page, sent a real
  `/v1/chat/completions` request via `curl` directly against the server
  (bypassing the UI entirely), and confirmed the on-screen prefill/decode
  step counts updated to match within one poll cycle with no manual
  interaction (previously would have required a page reload to show
  anything but the initial snapshot).

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
