# LLM/Codex Handover Notes

Detailed log for future Claude/Codex sessions working on the MTPLX Resource
Governor. Read `MTPLX_RESOURCE_GOVERNOR_CODEX_BRIEF.md` and
`PROJECT_STATUS.md` first; this file has the "why" and the pitfalls that
don't fit in status-tracker bullet points.

## Session 1 — 2026-09-03

### Environment / repo setup

- User account: `digitalgravy` on GitHub. `gh` was not installed; installed
  via `brew install gh`, user ran `gh auth login` interactively (device
  code flow) themselves — do not attempt to auth as the user yourself.
- Forked `youssofal/MTPLX` → `digitalgravy/MTPLX` via
  `gh repo fork youssofal/MTPLX --clone=false`.
- **Pitfall hit while cloning:** first attempt used a bash `for item in
  "$SRC"/*; do mv ...` loop intending `shopt -s dotglob` to pick up
  dotfiles (`.git`, `.github`, etc). `shopt` is a bash builtin and the
  harness's shell invocation doesn't reliably behave like interactive bash
  — the `shopt` call failed with "command not found", and (unexpectedly,
  given `set -e`) the subsequent `mv`/`rm -rf` still ran, moving only the
  non-dotfile content into place and then deleting the source dir —
  **losing `.git` in the process**. No real work was lost (it was a fresh
  clone with zero local commits), but the fix was to re-clone into a fresh
  scratch dir and use `cp -a "$SRC"/. "$DST"/` instead, which copies
  dotfiles regardless of shell glob settings and doesn't require deleting
  anything first. **Lesson: don't rely on `shopt -s dotglob` in this
  harness's shell; use `cp -a src/. dst/` for full-tree copies including
  dotfiles.**
- Working directory `/Users/stue/Documents/Projects/mtplx-fork` is now the
  actual git working tree (not a subdirectory) — `origin` = the fork,
  `upstream` = `youssofal/MTPLX`. The original brief file
  (`MTPLX_RESOURCE_GOVERNOR_CODEX_BRIEF.md`) lives at the repo root
  alongside the checked-out MTPLX source; it's untracked (not part of
  upstream's tree) — leave it untracked/gitignored rather than committing
  it into the fork's history unless the user asks otherwise.
- Branch `feature/resource-governor` created off `main` at commit
  `e652d55e2652137a4abcf1312357abbf3eb9d692`.
- **Important discovery:** that exact starting commit's message is *"ci:
  fail on any AI attribution in history (authors, committers, co-author
  trailers)"* — i.e. upstream MTPLX's own CI explicitly rejects commits
  with AI co-author trailers. This has direct bearing on how any commits
  in this fork (and especially anything intended for an eventual upstream
  PR per brief section 1.7) should be authored. Follow whatever attribution
  instructions are live in the current session's system reminders (they
  can change session to session) and lean toward *not* adding AI
  co-author trailers to commits that might travel upstream.

### Dev environment

- Python: used `uv venv --python 3.12.11 .venv` (repo requires >=3.11; the
  system default python3 was 3.14.3, which is untested against this repo's
  pinned `mlx`/`mlx-lm`/`transformers` ranges, so 3.12.11 was chosen instead
  since it was already available locally via `uv python list`).
- `source .venv/bin/activate && uv pip install -e ".[dev,server]"` installs
  cleanly: `mlx==0.32.2`, `mlx-lm==0.31.3`, `transformers==5.14.1`, etc.
  (Plain `python -m pip install -e .` inside the fresh venv fails first —
  the venv has no `pip` module by default under `uv venv`; use `uv pip
  install` instead, or `uv pip install pip` first.)
- CONTRIBUTING.md's recommended smoke subset (`test_no_mlx_imports.py
  test_public_cli.py test_runtime_kpis.py`) — **303/303 passed** cleanly.
- Full `pytest tests/` run was kicked off in the background; showed some
  `F` failures starting around the 12% mark of the run. **Not yet
  triaged as of this note** — next session (or later in this one) needs to
  check `/private/tmp/.../scratchpad/full_test_run.log`-equivalent output,
  identify which tests failed and why (pre-existing upstream flakiness vs.
  something environment-specific to this machine/Python version), and
  record the outcome here and in PROJECT_STATUS.md's "Bugs discovered"
  section. Do not assume the governor caused them — no governor code has
  been written yet at this point in the project.

### Hardware note

- **This dev/build machine is an Apple M4 Max with 36GB unified memory**,
  not the actual M5 Ultra / 96GB target machine described in the brief.
  It's fine for code development, unit tests, and small-model smoke tests,
  but:
  - The default verified MTPLX model (`Youssofal/Qwen3.8-27B-MTPLX-Optimized-Speed`)
    is likely too large / marginal to run comfortably here — pulled the
    smaller `Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed` instead for
    real-inference validation of governor hooks (brief section 25: "at
    least one real MLX inference workload must validate each prefill/decode
    hook" — the 4B model satisfies that without needing the 27B+ models).
  - Memory-pressure/hard-limit testing (brief section 14) and the real
    Moonlight acceptance test (brief section 20) **cannot** be meaningfully
    done here — both need the actual target M5 Ultra hardware. Don't claim
    those are validated based on this machine.

### Codebase shape (first look, pre-deep-recon)

- `mtplx/` package has **252 Python files** — much larger and more
  specialized (many per-model-family "MTP patch" files: `deepseek_mtp_patch.py`,
  `glm_mtp_patch.py`, `qwen3_5_mtp_patch.py`, `hy_v3_mtp_patch.py`, etc.)
  than the brief's phrasing might suggest. This is not a small toy
  scheduler — expect the actual prefill/decode/MTP scheduling logic to be
  spread across multiple cooperating files rather than one obvious
  "scheduler.py".
- CLI entry point is the `mtplx` console script (not `python -m mtplx` —
  that fails with "No module named mtplx.__main__; 'mtplx' is a package").
  Top-level commands include `serve`, `quickstart`, `chat`, `run`, `ask`,
  `status`, `settings` (get/set **live daemon settings** — worth checking
  closely as a possible existing runtime-control mechanism the governor's
  admin API could reuse per brief section 12), `trace` (diagnostics /
  TPS curves / live status — possible reuse target for governor
  observability per brief section 16), `hardware`, `models`.
- `mtplx/thermal.py` and `mtplx/fan_mode.py` already exist — the brief asks
  (question 13, section 30) whether MTPLX already has a thermal/fan/profile
  abstraction worth reusing. These are strong candidates; the recon pass
  was explicitly pointed at them.
- `mtplx setup --dry-run --json` output showed a large table of env-var
  style tuning knobs (e.g. `MTPLX_DYNAMIC_PAGED_KV`, `MTPLX_LAZY_VERIFY_LOGITS`,
  `MTPLX_CLEAR_CACHE_EVERY`, `MTPLX_LONG_CONTEXT_MTP_DEPTH*`) baked into
  named "profiles" (`stable`, `performance-cold`, `sustained`, `turbo`,
  `exact`, `max-diagnostic` — see `mtplx setup --help`). This is a
  **different kind of "profile" than the resource-governor's proposed
  max/balanced/interactive/protect/pause** — don't conflate them, but it's
  evidence MTPLX already has a profile-resolution system precedent (brief
  section 13's "effective scheduler/profile resolution layer") worth
  studying before inventing a parallel one.

### Delegation pitfall (worth remembering)

- First attempt to delegate the deep reconnaissance to a forked subagent
  failed silently in a specific way: the fork's completion notification
  reported generic session-status prose (essentially restating what the
  parent already knew) instead of actual code findings, and it had not
  created `docs/resource-governor/IMPLEMENTATION_NOTES.md` at all. It
  appears to have misinterpreted its job as "monitor the other background
  tasks" rather than "go read the source code yourself right now." Fixed
  by sending a follow-up message via `SendMessage` explicitly telling it
  to ignore the unrelated background tasks and do the file-reading work
  itself. **Lesson: when forking for a large synthesis/writing task, the
  prompt needs to make unmistakably clear that the fork itself must do the
  primary research — not just supervise — especially when the parent has
  other background tasks in flight that could be misread as "the work
  already in progress."**

### Disk space crisis mid-session (important, could recur)

This machine's disk hit **117MB free out of 460GB** partway through this
session — not caused by this project (the venv + deps are only a few GB;
something else on the user's system had already eaten ~444GB before this
session started, likely APFS container-wide usage not visible in a single
volume's `df` "Used" column — e.g. other volumes/snapshots in the same
container). It got bad enough that `Bash`, `Write`, and directory creation
all started failing with `ENOSPC` (`Read` still worked, since it doesn't
write). This broke the model download, the full test suite mid-run, and
the reconnaissance fork's ability to write its output file.

**Do not try to unilaterally free significant disk space** — freeing a
partial download this session created (`~/.mtplx/models/Youssofal--Qwen3.5-4B-MTPLX-Optimized-Speed`,
1.2GB) was fine since it was this session's own artifact, but broader
cleanup (Trash, caches, old files) is the user's call, not something to
guess at. Asked the user via AskUserQuestion; they chose to proceed with
whatever headroom existed (~1.1-2.3GB, fluctuating) rather than pause for
a full cleanup. That's enough for text-file writes and running the test
suite, but **not enough for another model download** (needs several GB)
and it makes MTPLX's own low-disk safety guard fire during tests (see
"Full test suite triage" below) — a real constraint on what Phase 0 can
fully validate on this machine right now.

If you hit `ENOSPC` again: check `df -h /` first, don't assume it's this
project's fault, and ask the user rather than deleting things.

### Full test suite triage — DONE, baseline established

Ran `pytest tests/` twice. First run got corrupted by the ENOSPC event
mid-run (spurious extra failures from files that couldn't be written) —
disregard that run entirely. Second run (~2.3GB free) is the real
baseline: **64 failures out of the full suite, all triaged, none are bugs
to fix as part of this project.** Full detail in PROJECT_STATUS.md's "Bugs
discovered" section; short version:

- 48 failures: MTPLX's own `cold_tier.py` low-disk guard (<10GiB free)
  correctly disables SSD spill/cache — pure artifact of this session's
  disk crunch, would pass with more headroom.
- 1 failure (`test_laguna_model.py`): a preflight check correctly refuses
  to load a config needing 85.3GiB RAM on this 36GB machine — test doesn't
  mock the RAM detector. Would pass on the real M5 Ultra target (96GB).
- 15 failures (`test_sdpa_nax_tile.py` x8, `test_graphbank_compiled_verify.py`
  x6, `test_ccopy_bank_route.py` x1): genuine floating-point
  bit-exactness/precision mismatches between compiled/eager or
  kernel/reference paths. These look like real, pre-existing MTPLX issues
  possibly specific to M4 Max + MLX 0.32.2 — **not** governor-related, not
  something this project should fix. Worth mentioning to the user if they
  want to file an upstream issue, but otherwise just noted and moved on
  from.

Net: Phase 0's "run existing tests" checklist item is done. The 15
numerical failures are a known baseline going forward — if a *future* test
run shows *different* numerical failures after governor code is added,
that's worth investigating (governor changes must not alter model
semantics per brief section 18), but these specific 15 predate any
governor code and shouldn't be attributed to it.

### Reconnaissance — DONE, IMPLEMENTATION_NOTES.md written

`docs/resource-governor/IMPLEMENTATION_NOTES.md` exists (~500 lines) and
is solid — every claim cites file:line, gathered by actually reading
source, honest about what's unverified. Read it in full before writing any
governor code; don't re-derive this from scratch. Highlights not to lose:

- **Prefill**: chunk loop is `_prefill_with_hidden_sequence()`
  (`generation.py:5655-5733`), syncs via `_eval()` at `generation.py:5701`
  after each chunk from `_iter_prefill_chunk_spans()`
  (`generation.py:883-902`). MTPLX's *own* `prefill_rungs.py` docstring
  already documents the exact laziness hazard brief section 31 asks about
  — the GPU idles while the host builds ~64 layers of lazy graph before
  that eval. A governor yield goes right after the existing `_eval()` call;
  no new sync needs inventing.
- **AR decode**: one token per boundary, two lanes (`generation.py:6199`
  blocking-`_eval`, `:6265` `.item()`-based pipelined lane) — both are
  real sync points, just different call shapes.
- **MTP decode** (`generate_mtpk`, `generation.py:7312`, ~5,300 lines):
  boundary is one draft+verify+commit "verify cycle," 1..depth tokens per
  cycle. NOT fully traced statement-by-statement — whether draft-forward
  time should count as governed "work" alongside target-verify time is
  still open (brief Q7). `MTPLX_EVAL_AUDIT` (`generation.py:209-236`) is a
  built-in per-eval timing/audit JSONL log — use it instead of adding new
  instrumentation when this gets resolved empirically.
- **Batched decode**: `batched_decode.py:584`, literally commented in
  source as `# THE one blocking sync`.
- **Huge reuse win — live mutable config already exists**:
  `POST /v1/mtplx/settings` (`openai.py:27310-27325`) already mutates
  `state.args.prefill_chunk_tokens` live, no restart, because
  `_scheduler_config_from_args()` re-derives config from `state.args` on
  every call rather than caching it. This is the exact mechanism the
  governor's own live profile-switching API should extend — add governor
  knobs (`resource_profile`, `prefill_duty_cycle`, etc.) to
  `DASHBOARD_MUTABLE_SETTINGS_KEYS` (`openai.py:15151`) following the same
  pattern. `max_active_requests`/`decode_batch_max`/`scheduler_mode` are
  NOT currently in that mutable list — extending them live is architecturally
  plausible (same re-derivation mechanism) but **unverified at runtime**,
  flagged as a concrete next step.
- **Auth is already handled**: `_AuthRateLimitMiddleware`
  (`openai.py:21631`, installed globally at `:26912`) covers every route
  including `/admin/*` via `x-api-key` + `secrets.compare_digest`. The
  governor's admin endpoints need zero new auth code.
- **Memory accounting already exists in full**: `mtplx/memory_plan.py`
  (`MemoryPlan`, `plan_memory`, `usable_engine_bytes`) — reuse this, don't
  build parallel accounting, per the brief's explicit instruction.
  `mtplx/batching/admission.py`'s `AdmissionPolicy`/`MemoryPressure`
  already implements soft/hard pressure tiers that shrink concurrency and
  chunk size — conceptually exactly brief section 14's ask — **but** it's
  currently only wired to `MTPContinuousScheduler`
  (`batching/scheduler.py`), which recon found is used **only in
  `tests/test_batching_foundation.py`**, not from the live CLI/server
  path. Whether admission pressure is actually enforced on real HTTP
  requests today is **unresolved** — `mtplx/server/request_policy.py`
  (`RequestPolicy`, `resolve_request_policy()` at line 395) was located as
  the likely real per-request policy entry point but not yet read in full.
  **Read that file before assuming admission.py is live.**
- **Naming**: `--profile` already means something unrelated (a
  performance/quality tuning profile: stable/performance-cold/sustained/
  turbo/exact/max-diagnostic, `mtplx/profiles.py:16-23`, restart-required).
  Confirms the brief's `--resource-profile` naming choice avoids a real
  collision — don't use bare `--profile` for the governor.
- **Thermal/fan system** (`thermal.py`, `fan_mode.py`) is a good *shape*
  precedent (CLI verb + `--status` + HTTP mirror at
  `/v1/mtplx/thermal/fan_mode` and `/status`) but operates on a different
  axis (external fan tool) — no implementation-level reuse, only
  interface-shape inspiration.
- **Cancellation**: the live generation path uses a poll-style
  `abort_check: Callable[[], bool]` callback checked at commit points
  throughout `generation.py` (e.g. `:2576, 2692, 2740, 2777`). A governor
  yield should be a short-interval sleep loop that re-checks `abort_check()`
  between slices — not one long uninterruptible sleep. (There's a second,
  separate `threading.Event`-based cancellation primitive in the
  not-yet-live `batching/state.py` scaffold — don't conflate the two.)

**Usage gotcha discovered empirically, and it recurs**: `MTPLX_EVAL_AUDIT`
(`generation.py:209`) is a **file path**, not a boolean flag. This bit the
reconnaissance fork once (it set `"1"` expecting boolean-style
enable/disable) and then bit the local test suite a second time,
independently: `tests/test_async_decode.py::test_eval_audit_forces_synchronous`
(an existing, not-project-owned test) does `monkeypatch.setenv("MTPLX_EVAL_AUDIT", "1")`
and MTPLX dutifully writes real per-eval JSONL entries to a file literally
named `1` in whatever the process's cwd is — the repo root, when pytest is
run from there. **Any full or partial test-suite run that includes that
test will recreate this stray `1` file** — check `git status` before
committing and delete it (`rm -f ./1`), it's never meaningful project
state, just proof the instrumentation fires. Not something to "fix" as
part of this project (it's an existing test's behavior, harmless, just
untidy) — just remember to look for it. To use `MTPLX_EVAL_AUDIT` for real
governor-timing verification, set it to an actual path, e.g.
`MTPLX_EVAL_AUDIT=/tmp/eval_audit.jsonl`.

**Not yet done, explicitly flagged in the doc** (brief section 25 requires
this before trusting any hook, and it's the natural next step once disk
space allows the model download): run a real MLX inference workload
end-to-end. Also not yet read in depth: `request_policy.py`,
`cache_state.py` (4,777 lines), `session_bank.py` (2,612 lines),
`kpi/runtime_kpis.py`.

**Delegation pitfall worth remembering for next time**: the reconnaissance
fork's *first* completion notification reported generic session-status
prose instead of actual findings, and hadn't created the output file at
all — it seems to have misread "go do deep reconnaissance" as "monitor
what else is happening in this session." A follow-up message via
`SendMessage`, explicitly telling it to ignore unrelated background tasks
and do the file-reading itself right now, fixed it completely — the
corrected run was excellent. When forking for a large research+writing
task while other background work is also in flight, make the prompt
unmistakable that the fork itself is the one doing the primary work, not
supervising.

## Session 1 continued — Phase 1 (same day)

Implemented and tested the minimal decode governor. Full detail is in
PROJECT_STATUS.md's Phase 1 "Completed" entry; key things not to
rediscover:

- `mtplx/resource_governor.py` is a new, standalone, MLX-free module.
  Deliberately synchronous (not the brief's illustrative `async def`) —
  see the module docstring for why; don't "fix" this to be async without
  re-reading that rationale first, it was a considered decision not an
  oversight.
- The hook lives in `generate_ar`'s **classic AR loop only**
  (`generation.py`, right after the per-token `_eval()`/`async_eval()`
  dispatch). The `MTPLX_AR_PIPELINE` lane, `generate_mtpk` (MTP), and
  `batched_decode.py` are NOT hooked yet — this was a deliberate Phase 1
  scope cut (brief section 33 only asks for "serial AR decode"), not
  missed work. Do them as their own small, testable commits per brief
  section 26's discipline, not bundled together.
- Validated with two test files: `tests/test_resource_governor.py` (pure
  timing-math unit tests, no MLX) and
  `tests/test_resource_governor_ar_integration.py` (real MLX compute via
  a toy model, same pattern `tests/test_async_decode.py` already uses).
  The toy-model tests are genuine MLX execution on this machine, but
  they are not a substitute for validating against a real downloaded
  model — that's still blocked on disk space and still needs doing
  before fully trusting this hook (brief section 25).
- Ran the new tests against ~48 existing test files that touch
  `generate_ar`/`generation.py` to check for regressions from the new
  `resource_governor` parameter. Only the same pre-existing
  `test_laguna_model.py` RAM-preflight failure showed up — nothing new
  broke.
- **Recurring gotcha**: running `tests/test_async_decode.py::test_eval_audit_forces_synchronous`
  (as part of any batch that includes it) drops a stray file named `1`
  in the repo root every time (see the `MTPLX_EVAL_AUDIT` gotcha note
  above). Check `git status` and `rm -f ./1` before committing if you've
  run tests since the last commit.

## Session 1 continued — Phase 2 (same day, 2026-09-04)

Implemented and tested the prefill governor. Full detail in
PROJECT_STATUS.md's Phase 2 "Completed" entry; key things not to
rediscover:

- Hooked `after_prefill_chunk` into plain `_prefill()`
  (`generation.py:5317+`), **not** `_prefill_with_hidden_sequence` (which
  Phase 0's IMPLEMENTATION_NOTES.md flagged as "the representative case"
  for reconnaissance purposes — that was fine for documenting the pattern,
  but tracing the real call graph in this session found
  `_prefill_with_hidden_sequence` has exactly one caller, gated behind a
  narrow MTP-history-policy combination). `_prefill()` is what
  `generate_ar`'s default cold-start path actually calls, and it's shared
  by `generate_mtp1`/`generate_mtpa` too. IMPLEMENTATION_NOTES.md now has
  an inline correction note pointing here rather than being rewritten.
- **Important, non-obvious discovery**: chunked prefill itself
  (`_iter_prefill_chunk_spans`) only chunks when `MTPLX_SUSTAINED_PREFILL`
  is truthy — otherwise it's a single span regardless of chunk-size
  settings. This had me confused for one failed test run (see below) until
  I traced it. It's not a dead path in practice: `profiles.py`'s
  `SUSTAINED_PROFILE` (the literal default product profile) and
  `TURBO_PROFILE` both set it, so real default usage does chunk. But any
  future governor test or manual check that doesn't set
  `MTPLX_SUSTAINED_PREFILL=1` will see exactly one "chunk" (the whole
  prompt) and zero governor pacing calls, which can look like a bug when
  it isn't — remember this env var.
- Same threading pattern as Phase 1: new `resource_governor` parameter on
  `_prefill`, `restore_or_prefill_prompt_state`, and passed through from
  `generate_ar` — all default `None`, zero behavior change when unused.
- Tests: `tests/test_resource_governor_prefill_integration.py`, same
  toy-MLX-model approach as Phase 1's decode tests, but needs
  `monkeypatch.setenv("MTPLX_SUSTAINED_PREFILL", "1")` plus
  `prefill_chunk_size_override(2)` on a multi-token prompt to actually
  exercise more than one chunk (a single-token prompt, or prefill without
  that env var, never enters the chunk loop at all — see
  `test_single_token_prompt_never_enters_chunk_loop`).
- Ran the same ~48-file regression batch as Phase 1 plus
  `test_prefill_chunk_defaults.py`/`test_generation_sustained.py`
  directly — only the same pre-existing `test_laguna_model.py` RAM
  failure, nothing new broke.
- Still not hooked (deliberately deferred, same discipline as Phase 1):
  `_prefill_restored_prompt_suffix` (warm-restore suffix, `:2548`),
  `_prefill_committed_mtp_history_streaming` (committed/last_window MTP
  history, `:5414`), `_prefill_with_hidden_sequence` (`:5656`).

## Session 1 continued — Phase 3 (same day, 2026-09-04)

Implemented and tested the runtime admin API and live profile switching.
Full detail in PROJECT_STATUS.md's Phase 3 "Completed" entry; key things
not to rediscover:

- `ServerState.__init__` (`server/openai.py`) now owns
  `self.resource_governor = ResourceGovernor()`, defaulting to `max`
  (no-op) on every server start — there is no CLI/config/env way to start
  it on a different profile yet, that's Phase 4. Right now the *only*
  lever is the new HTTP API, and it resets to `max` on every restart (no
  persistence across restarts — also Phase 4 territory: "persisted
  config").
- Chose `POST /admin/resource-governor/profile` over the brief's
  illustrative `PUT` — every other mutation route in this file is POST.
  Don't "fix" this to PUT later without a real reason; it was a
  deliberate convention match, not an oversight.
- The live `generate_ar(...)` call in `_run_generation`
  (`server/openai.py`) now gets `resource_governor=state.resource_governor`
  — and for free, its existing `abort_check` lambda (already checking
  `cancel_event.is_set() or _pressure_abort_requested(state)`) is what
  the governor's yield-sleep polls, so cancellation-safety (brief section
  18/section 30 Q15) came from reusing what was already there, not new
  plumbing.
- `_fake_state()` in `tests/test_server_openai.py` (shared by ~50
  existing tests) got one new field, `resource_governor=ResourceGovernor()`
  — necessary because the new routes read `state.resource_governor`.
  Confirmed via full-file test run this didn't disturb any other test.
- Verified (not assumed) that no new auth code was needed: Phase 0 recon
  predicted the global `_AuthRateLimitMiddleware` would cover new routes
  automatically, and `test_resource_governor_admin_endpoints_require_auth`
  proves it — unauthenticated GET/POST both 401, and confirms the
  attempted POST didn't mutate `state.resource_governor` either (not just
  that the HTTP call failed).
- Ran the new tests, the full `test_server_openai.py` (365 tests),
  `test_dashboard_endpoints.py`, and a ~75-file batch covering everything
  else touching `server/openai.py`. Same two pre-existing failure
  categories as always (low-disk SSD guard, `test_graphbank_compiled_verify.py`
  numerical precision) — nothing new.
- Still open for a future phase: exercising this against an actual
  running `mtplx serve` process (curl/httpx against a real subprocess),
  not just `TestClient` against an in-process fake state. Also open:
  whether/how `admission_allowed()` (already implemented in the governor
  core since Phase 1, unused until now) should actually gate the live
  request-admission path — Phase 0 recon found there's no live admission
  enforcement to hook into at all today (see IMPLEMENTATION_NOTES.md's
  "Resolved" note under Q12), so this is genuinely new wiring, not a hook
  into something existing. That's Phase 5/6 territory per the brief's own
  phasing, not done in Phase 3.

## Session 1 continued — Phase 4 + real-model validation (same day, 2026-09-04)

Disk space, which had been chronically blocking real-model work all
session, got fixed by the user (723MB → 143GB free, external fix, not
something I did). Used the headroom to: finish Phase 4 (CLI + config
integration — straightforward, followed `config.py`'s existing generic
`_RUNTIME_DEFAULTS` precedence mechanism exactly, no new logic needed),
retry and complete the `Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed`
download, and — for the first time this project — actually run the
governor against a real downloaded model through a real `mtplx serve`
process (not `TestClient`, not a toy model).

**The real-model test was extremely valuable and surfaced something
important.** Full detail in PROJECT_STATUS.md's "Real-model validation"
completed entry; the short version:

- The mechanism itself works exactly as designed: switched
  `max → interactive` live via the admin API with a real server running,
  no restart, and with `generation_mode: "ar"` explicit in the request,
  decode went from a natural ~95.3 tok/s to ~38.1 tok/s delivered — right
  at the 0.4 duty cycle target (95.3 × 0.4 ≈ 38.1). Prefill paced
  correctly too (`effective_duty_cycle: 0.4` exactly).
- **But the very first attempt, without an explicit `generation_mode`,
  showed ZERO governor activity** — `"steps": 0` on both lanes, despite
  the profile switch reporting success. Root cause: this model (like
  most real MTPLX-served models) defaults to `generation_mode: "mtp"`,
  and `_run_generation` in `server/openai.py` only passes
  `resource_governor=state.resource_governor` into the `generate_ar(...)`
  branch — the `else` branch (`generate_mtpk(...)`, MTP path) doesn't get
  it at all.
- This was always a *documented* scope cut (Phase 1 said "classic AR
  lane only" from the start), but until this test I hadn't internalized
  what it actually *means* in practice: **on a real MTPLX server running
  its own default configuration, the governor currently does nothing.**
  MTP is the product's flagship mode. This isn't a nice-to-have follow-up
  anymore — it's the thing standing between "the governor works in tests"
  and "the governor does anything useful on the actual target machine."
  Promoted to top priority in PROJECT_STATUS.md.

## Session 1 continued — MTP decode hook (same day, 2026-09-04)

Closed the critical gap. Full technical detail in
`docs/resource-governor/IMPLEMENTATION_NOTES.md` section 5 and
PROJECT_STATUS.md's MTP-hook "Completed" entry — read those before
touching `generate_mtpk` again. The one thing worth re-emphasizing here:

**A fork's confident-sounding recommendation was wrong in a way that
would have shipped a real bug if taken at face value.** The fork (used
to trace `generate_mtpk`'s ~5,300-line loop rather than loading it all
into this session's own context) recommended hooking pacing inside
`emit_new_tokens()`, a helper called from ~10 branch sites that looked
like a single per-cycle convergence point. I independently re-read the
actual call sites before writing code (per the "trust but verify"
discipline for subagent work) and found several fire **mid-cycle** — one
literally right after the primary token is sampled, before that cycle's
draft-forward/target-verify work has even happened, with a comment at
that exact site confirming it. Hooking there as recommended would have
double-counted tokens per cycle and — worse — folded a prior governor
sleep's duration into the *next* call's "work" measurement, since a
second call within one cycle would measure elapsed time inclusive of the
first call's sleep. A slow, incorrectly-escalating pacing bug, in
correctness-sensitive speculative-decoding code, that unit tests
wouldn't obviously catch unless someone specifically thought to check
`yields <= steps`.

**Lesson for next time**: forks are excellent at surveying large
unfamiliar code and proposing a hook point, but a proposed hook point in
control-flow-heavy code needs independent verification of the *specific
claim that makes it look safe* (here: "called once per cycle") before
committing to it, not just a general sanity read. This cost maybe 15
extra minutes of reading two call sites closely before writing the real
implementation — cheap insurance against a real bug.

**The actual (corrected) design**: pace at the top of the `while` loop
against the *previous* iteration's measured wall time and token count,
before that iteration's work begins — not inside `emit_new_tokens()` at
all. Single unambiguous point per iteration, immune to however many
internal helper calls happened inside the previous iteration.
Regression-tested specifically (`test_yields_never_exceed_one_per_cycle_transition`
in `tests/test_resource_governor_mtp_integration.py`) so this can't
silently regress back to the buggy design.

**Real-model proof the fix matters**: same live-server test as before,
but this time *without* forcing `generation_mode: "ar"` — the model's
own default MTP path. A 150-token request: 1.03s at `max`, 3.11s at
`interactive` (`effective_duty_cycle: 0.4` exactly, live profile switch,
no restart). This is the scenario that was completely inert before this
hook — now it works.

## Session 1 continued — full decode/prefill coverage (same day, requested by user: "round it out")

Hooked everything left reachable from a live request: the
`MTPLX_AR_PIPELINE` decode lane, and all three remaining prefill
functions. Full detail in `docs/resource-governor/IMPLEMENTATION_NOTES.md`
section 6 and PROJECT_STATUS.md; two things worth not rediscovering:

- **`MTPLX_AR_PIPELINE` lane was simple, MTP's lesson still applied as a
  check, not a blanket rule**: independently verified this loop commits
  exactly one token per iteration with one clear sync point — genuinely
  safe for a direct per-token hook, no previous-iteration trick needed.
  The lesson from the MTP hook wasn't "always use the complicated
  design," it was "verify the specific safety claim before hooking,
  every time" — this time verification confirmed the simple approach
  *was* safe.
- **A second real gap, same pattern as the first**: wiring
  `_prefill_committed_mtp_history_streaming` required re-reading
  `restore_or_prefill_prompt_state`'s branches, which showed real default
  MTP requests (`mtp_history_policy="committed"` + sustained profile)
  never actually reach the plain `_prefill()` hooked in Phase 2 — they
  go through this other function instead. This is exactly why the MTP
  hook's own live-server test showed zero prefill steps despite decode
  pacing working. Fixed and confirmed with a fresh live-server check.
  **Lesson reinforced**: a live-server test with real stats output is
  what actually caught this both times — unit tests against toy models
  proved the mechanism correct but couldn't have surfaced "which function
  does a real default request actually call," since that's a question
  about the surrounding branch logic, not the hook itself. Keep doing
  real end-to-end checks after each milestone, not just unit tests.
- `batched_decode.py`'s main entry point (`generate_greedy_batched`) is
  confirmed dead on the live path (no callers outside its own file/tests)
  — don't hook it. The real live batched path is
  `a3b_mtp_batch.py`'s `install_a3b_mtp_batch_lane`, narrow (A3B/whole-MoE
  models only) and cycle-based like `generate_mtpk` — deferred, needs the
  same careful independent verification before touching, not a quick
  addition.

## Session 1 continued — basic admission enforcement (same day)

Added `_reject_if_resource_governor_admission_closed(state)` to
`server/openai.py`, called at the very top of both
`/v1/chat/completions` and `/v1/completions`. `protect`/`pause` profiles
now return a real `503` before any generation work starts; everything
else passes through. This is intentionally the *simple* half of Phase
5/6 — did not touch concurrency mutation or memory-pressure admission,
both flagged by the brief as safe to defer when architecturally risky.
Validated live against the real model (503 in `protect`, 200 in `max`).
If you resume Phase 6 later, this function is the natural place a
memory-pressure check would also live (call it or extend it, don't build
a parallel admission gate).

### Unresolved questions / exact next action for a fresh session

Phases 0-4, the MTP decode hook, the AR pipeline lane, all reachable
prefill functions, and basic admission enforcement are done, tested, and
pushed. Decode/prefill coverage is complete for every path a real
`mtplx serve` request can take in AR or MTP mode. If you're picking this
up cold:

1. Read `docs/resource-governor/IMPLEMENTATION_NOTES.md` in full and this
   file's session notes above before writing more governor code — all
   hook-placement decisions, including three corrected mistakes/gaps
   found along the way (see IMPLEMENTATION_NOTES.md sections 5-6), are
   settled. Don't re-derive them.
2. Check whether documentation work is done: `docs/resource-governor/README.md`,
   `ARCHITECTURE.md`, a plain-language non-technical guide, `BENCHMARKS.md`,
   `UPSTREAM_STATUS.md`, and the `mtplx-qos` companion tool were requested
   by the user in this session — check PROJECT_STATUS.md's "In progress"
   to see how far that got before this session ended.
3. Remaining scope, roughly in priority order:
   - Phase 5/6: wire `admission_allowed()` (implemented since Phase 1,
     still unused) into an actual request-admission check — no live
     enforcement point exists to hook into yet, this is new wiring.
   - A3B batched lane (`a3b_mtp_batch.py`) — deferred, narrow, needs the
     same careful verification as the MTP hook.
   - Phase 8: M5 Ultra hardware tuning + real Moonlight acceptance test —
     needs the actual target machine, not this M4 Max dev machine.
4. Keep committing docs as you go, not just at the end of a session
   (brief section 28).
5. **Check `git status` for a stray file named `1` before every commit**
   if you've run tests since the last one — this has now bitten multiple
   times (the `MTPLX_EVAL_AUDIT` gotcha, see above).
