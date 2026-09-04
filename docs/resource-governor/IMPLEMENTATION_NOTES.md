# Resource Governor — Implementation Notes (Phase 0 reconnaissance)

Recorded against fork `digitalgravy/MTPLX`, branch `feature/resource-governor`,
starting upstream commit `e652d55e2652137a4abcf1312357abbf3eb9d692`
(upstream `main`, 2026-09-01). All line numbers below were read directly from
the checked-out source on this branch; re-verify before relying on them if
the branch has since moved.

This is reconnaissance only — no governor code exists yet. Per the brief
(section 2), do not assume any name below is stable; it is what currently
exists, not a promise of what to build against.

---

## 1. Section 2 checklist — current source locations

**CLI argument parsing**
`mtplx/cli.py`. Parser built by `build_parser()` (`cli.py:2085`), a
`_FlagRecordingArgumentParser` subclass (`cli.py:2065`) that records which
flags were *explicitly* passed into `args._cli_flags` — this is the
mechanism config-precedence relies on (see Q14 below). The scheduler/batching
flag group lives in `_add_batching_args()` (`cli.py:703-740`) and already
defines `--scheduler-mode`, `--batching-preset`, `--max-active-requests`,
`--decode-batch-max`, `--batch-wait-ms`, and `--prefill-chunk-tokens`
(`cli.py:732-735`). This is the natural place to add
`--resource-profile` / `--prefill-duty-cycle` / `--decode-duty-cycle` /
`--min-decode-tps`.

**Config/profile resolution**
`mtplx/config.py`. `apply_user_config()` (`config.py:207-228`) loads the
persisted user config and calls per-field appliers (`_apply_model_default`,
`_apply_cache_default`, `_apply_profile_default` at `config.py:258`,
`_apply_runtime_defaults`). Every applier checks `args._cli_flags` first and
only fills in the config value when the user did **not** pass the flag
explicitly — i.e. CLI > user config > built-in default. This is the existing
precedence chain a resource-profile config entry should slot into.

Important naming collision: `--profile` **already exists** and means
something else entirely — a *performance/quality* tuning profile
(`stable | performance-cold | sustained | turbo | exact | max-diagnostic`,
`mtplx/profiles.py:16-23`, `DEFAULT_PROFILE_NAME = "sustained"`). It is also
in `DASHBOARD_RESTART_REQUIRED_KEYS` (`server/openai.py:15191`) — changing it
requires a server restart. The brief's `--resource-profile` naming is
correct and necessary to avoid colliding with this.

**OpenAI-compatible server startup**
`mtplx/server/openai.py` (34,791 lines — the single largest file in the
package). App/route construction happens in one large function that calls
`app.add_middleware(...)` (`openai.py:26894-26912`, see Auth below) and then
defines routes with `@app.get/@app.post` decorators from roughly
`openai.py:26914` onward.

**Scheduler creation**
`_scheduler_config_from_args(args)` (`openai.py:16022-16042`) builds a
`BatchSchedulerConfig` (`mtplx/batching/scheduler.py:31-87`) fresh from
`state.args` each time it's called — it is **not** cached once at startup.
`_scheduler_policy_label()` (`openai.py:16045-16061`) and
`_mtplx_scheduler_state()` (`openai.py:16086-16230+`) derive the
human-readable policy/lane and are what backs the scheduler stats surface.

Note: `mtplx/batching/scheduler.py` also defines a `MTPContinuousScheduler`
class — a fully-built cooperative request state machine (submit/step/cancel,
prefill→decode_ready→postcommit phases, admission integration). Grep shows
it is used **only in `tests/test_batching_foundation.py`**, not from
`cli.py`, `server/openai.py`, or `commands/public.py`. Its own docstring says
it is "intended to run *inside* the existing single owner thread once the
generation primitives are fully stepable" — i.e. this looks like scaffolding
for a future architecture, not the live request-dispatch path today. The
concepts it defines (`SchedulerMode`, `SchedulerPreset`, `BatchSchedulerConfig`)
**are** imported and used live by `cli.py` and `server/openai.py`, just not
via this class. Treat `MTPContinuousScheduler` as a preview of a possible
future execution model, not as the thing to hook.

**Sustained/chunked prefill**
`mtplx/generation.py`. Chunk *sizing* comes from `_prefill_chunk_size()`
(`generation.py:810-823`, reads `MTPLX_PREFILL_CHUNK_SIZE`, default 2048,
overridable per-request via the `prefill_chunk_size_override()` context
manager at `generation.py:826-841`). Chunk *spans* come from
`_iter_prefill_chunk_spans()` (`generation.py:883-902`). The actual chunk
loop that owns progression is in `_prefill_with_hidden_sequence()`
(`generation.py:5655-5733`):

```
for start, end in _iter_prefill_chunk_spans(len(body)):   # generation.py:5679
    ...
    chunk_logits, chunk_hidden = rt.forward_ar(...)        # builds lazy graph
    _eval(chunk_logits, chunk_hidden)                       # generation.py:5701 — sync point
    _runtime_count(rt, "prefill_chunks")
```

The same chunk-span helper is also consumed at `generation.py:2774`, `3598`,
`5355`, `5483` (other prefill code paths — restore-from-cache prefill, the
plain `_prefill()` at `generation.py:5316`, etc). All of them end each chunk
iteration with an explicit `_eval(...)` call — see section 3 below.

> **Phase 2 correction (implementation session, not reconnaissance):**
> `_prefill_with_hidden_sequence` above was read as "the representative
> case" for documenting the pattern, but tracing the actual call graph
> shows it has exactly one caller, reached only when MTP history policy is
> `committed`/`last_window` *and* sustained prefill is disabled — a narrow
> combination. Plain `_prefill()` (`generation.py:5317` as of Phase 2) is
> what `generate_ar`'s default `mtp_history_policy="cycle"` cold-start path
> actually calls, and it's shared by `generate_mtp1`/`generate_mtpa` too.
> The resource governor's `after_prefill_chunk` hook (Phase 2) was placed
> in `_prefill()`, not here, for broader real coverage. Also: chunking
> itself only activates when `MTPLX_SUSTAINED_PREFILL` is truthy (see
> `_iter_prefill_chunk_spans`'s early-return for the single-span case) —
> which the shipped default profile (`profiles.py`'s `SUSTAINED_PROFILE`,
> `DEFAULT_PROFILE_NAME = "sustained"`) and `TURBO_PROFILE` both set, so
> this is live under default settings, not a dead path. See
> `PROJECT_STATUS.md`'s Phase 2 entry for full detail.

**Autoregressive decode**
`generate_ar()`, `mtplx/generation.py:5951` (until `generate_mtp1` at 6634).
Contains multiple lanes selected by env flags:
- default blocking-eval loop (baseline path),
- `MTPLX_ASYNC_AR=1` double-buffered decode (`_ar_sync_eval` flag,
  `generation.py:6199-6202`),
- `MTPLX_AR_PIPELINE` software-pipelined lane (`generation.py:6204-6304+`),
  which syncs via `tok_lazy.item()` (`generation.py:6265`) rather than an
  explicit `mx.eval()` — `.item()` forces evaluation of the token's lazy
  graph, so it is a real sync point too, just a different call shape.

**MTP/speculative decode**
Three generators in `generation.py`: `generate_mtp1` (6634), `generate_mtpk`
(7312 — the depth>1 speculative path, ~5,300 lines), `generate_mtpa` (12605).
`mtplx/mtp_patch.py` is about MTP *weight loading/injection*
(`inject_mtp_support`, `mtp_patch.py:732`), not the decode loop itself — its
own `mx.eval()` call (`mtp_patch.py:810`) is a one-time weight-materialization
sync at model load, unrelated to per-step pacing.

Inside `generate_mtpk`, each draft+verify+commit cycle increments a
`verify_calls` counter and ends with an explicit `_eval()`/`mx.eval()` call
at its commit point (many sites, e.g. `generation.py:9824-9829`,
`generation.py:12214`). This was read at the granularity of "a verify cycle
exists and ends in an eval call" across a very large function; the exact
boundary of "one cycle" (whether it should include draft-forward time or
only target-verify time as governed "work") was **not** fully traced
line-by-line and needs a closer pass — see Q7 below.

**Batched decode**
`mtplx/batched_decode.py`. `generate_greedy_batched()` (line 935) is the
cohort entry point. Lower-level loops `_run_ar_loop()` (528) and
`_run_foldin_loop()` (624) each have a `_read()` closure with an explicit
blocking sync — literally commented in the source:
```python
mx.eval(sub["x"])  # THE one blocking sync         (batched_decode.py:584)
```
with `mx.async_eval(...)` dispatched earlier in the same loop
(`batched_decode.py:603, 614, 822, 837`) to overlap host graph-building with
GPU execution before that one blocking point. Per-cohort-step eval also
appears at `batched_decode.py:1129, 1371, 1390`.

**Request admission/concurrency**
`mtplx/batching/admission.py` (124 lines, read in full) —
`AdmissionPolicy.decide()` returns an `AdmissionDecision` (ADMIT/WAIT/REJECT)
factoring in a `MemoryPressure` enum (NORMAL/SOFT/HARD) computed from
`active_memory_bytes / total_memory_bytes` against per-preset thresholds
(`soft_memory_fraction=0.85`, `hard_memory_fraction=0.92`,
`batching/state.py:69-70`). `effective_limits()`
(`admission.py:76-84`) already **shrinks** `max_active_requests` /
`decode_batch_max` / `prefill_chunk_tokens` under soft/hard pressure — this
is precisely the "protect mode" mechanism the brief asks for in section 14,
already built. As noted above, this is currently wired into
`MTPContinuousScheduler`, which is not on the live serving path — so this
admission/pressure logic may not be *actually enforced* on real HTTP
requests today. This needs a runtime check (send concurrent requests, watch
whether admission decisions are actually consulted) before assuming it's
live. `mtplx/server/request_policy.py` (`RequestPolicy` class,
`resolve_request_policy()` at line 395) looks like the real per-request
policy-resolution entry point for the live server and should be read in
full in a follow-up pass — not yet done here.

**Health/stats endpoints**
All in `mtplx/server/openai.py`. Notable routes (line numbers from
`@app.get/@app.post` decorators):
- `GET /health` — `openai.py:27001`
- `GET/POST /v1/mtplx/settings` (+ bare `/mtplx/settings` alias) —
  `openai.py:27310-27325` — **the existing live runtime-control surface**
- `GET /v1/mtplx/snapshot` — `27327`
- `GET /v1/mtplx/flight` — `27338` (in-flight request status)
- `POST /v1/mtplx/cancel/{request_id}` — `27345`
- `POST/GET /v1/mtplx/thermal/fan_mode`, `/v1/mtplx/thermal/status` —
  `27369-27443` (closest existing precedent for a small governor-style
  runtime knob with both CLI and HTTP surfaces)
- `GET /admin/sessions`, `POST /admin/sessions/{id}/clear`,
  `POST /admin/cache/clear`, `GET/POST /admin/cache/ssd...` —
  `28168-28227`
- `GET /metrics` — `28158`
- `GET /dashboard` — `33651`

**Runtime command/control**
Same `/v1/mtplx/settings` POST handler,
`update_mtplx_settings()` (`openai.py:27315-27325`), backed by
`_mtplx_apply_settings_payload()` (`openai.py:15670-15812`) — see Q9/Q12
below, this is the concrete precedent to extend or mirror for the governor's
own runtime API.

**Memory/KV reservation and cache management**
`mtplx/memory_plan.py` (625 lines, read in full) — already a complete
machine/model memory-budgeting system:
- `detect_total_ram_bytes()` (239) — `sysctlbyname("hw.memsize")` via ctypes
  (deliberately avoids a `sysctl` subprocess call, which broke once under a
  sanitized PATH — see the docstring, a real prior incident).
- `usable_engine_bytes(total)` (273-280) — engine allocator envelope as
  `min(total, max(floor, total * fraction), cap)`. This is the existing
  analog of the brief's `reserve_system_memory` concept, though currently a
  single fixed fraction/floor/cap, not operator-configurable per the brief's
  ask.
- `MemoryPlan` dataclass (288-378) — `total_ram_bytes`, `usable_bytes`,
  `model_weights_bytes`, `kv_bytes_per_token(_effective)`,
  `context_window_fit` / `context_window_resolved`, `kv_reserve_bytes`,
  `bank_idle_max_bytes` / `bank_steady_bytes`, `headroom_bytes`.
- `plan_memory(...)` (385+) resolves one of these per (machine, model,
  config) triple.

`mtplx/cache_state.py` (4,777 lines) and `mtplx/session_bank.py` (2,612
lines) exist for KV-cache state and the persistent session bank
(SSD-backed, per `--ssd-session-cache` flags seen in `cli.py`) respectively —
not read in depth this pass; flagged for a follow-up read before
implementing memory-safety admission (brief section 14). The governor should
almost certainly consume `memory_plan.MemoryPlan`/`plan_memory` rather than
build parallel accounting, per the brief's explicit instruction.

---

## 2. Section 30 — the 15 reconnaissance questions

**1. What exact function owns chunk progression in sustained prefill?**
`_prefill_with_hidden_sequence()`, `generation.py:5655-5733`, specifically
the `for start, end in _iter_prefill_chunk_spans(len(body))` loop at
`generation.py:5679`. (Other prefill entry points — `_prefill()` at 5316,
the restore-prompt path around 5355/5483, the streaming-restore path around
2774 — reuse the same `_iter_prefill_chunk_spans()` helper and the same
per-chunk `_eval()` pattern; `_prefill_with_hidden_sequence` was read in
full as the representative case.)

**2. Does the CPU regain control after each chunk while Metal work is
complete, or is work lazily queued/evaluated later?**
Lazily queued, confirmed explicitly in-repo. `mtplx/prefill_rungs.py`
(106 lines, read in full) exists specifically because of this: its docstring
says "MLX builds a whole prefill-chunk forward lazily and dispatches only at
the end-of-chunk eval, so the GPU idles while the host walks 64 layers of
graph construction" (`prefill_rungs.py:4-6`). That module's fix is an
*opt-in* diagnostic (`MTPLX_PREFILL_ASYNC_RUNGS`, off by default) that
inserts `mx.async_eval()` mid-chunk to overlap host graph-build with GPU
execution — it does not change the end-of-chunk `mx.eval()` semantics the
governor would pace against.

**3. Where must an `mx.eval`/sync point occur for a yield to create real GPU
breathing room?**
Immediately after the existing per-chunk `_eval(chunk_logits, chunk_hidden)`
call (`generation.py:5701`) and before the next loop iteration starts
building the next chunk's graph (`generation.py:5679` next iteration). That
`_eval()` already exists — no new synchronization needs to be invented for
prefill pacing; the governor's yield is inserted *after* it. `_eval()`
itself (`generation.py:208-215`) is a one-line wrapper: `mx.eval(*values)`
plus a call to `_owner_progress_tick()` (imported from
`mtplx/progress_heartbeat.py`) — a liveness heartbeat for a stall watchdog,
worth reusing/extending for governor telemetry rather than adding a second
tick mechanism.

**4. Can that synchronization be added without destroying existing prefill
performance semantics?**
Yes for prefill — the sync point already exists unconditionally (`_eval()`
runs every chunk regardless of any governor). Adding a yield *after* it adds
wall-clock time but does not change what gets evaluated or in what order.
Not yet verified: whether `MTPLX_PREFILL_ASYNC_RUNGS` (mid-chunk
`async_eval`) is enabled in any of the shipped profiles — if it is, the
governor needs to be aware pacing happens at chunk granularity, not layer
granularity, regardless.

**5. Smallest safe decode scheduling boundary in AR mode?**
One decode step (one token). Confirmed sync points: `generation.py:6265`
(`tok_lazy.item()` in the pipelined lane) and the plain blocking lane's
`_eval()` calls guarded by `_ar_sync_eval` (`generation.py:6199-6202`).
Both lanes retire exactly one token's GPU work per boundary.

**6. Smallest safe scheduling boundary in MTP mode?**
One draft+verify+commit cycle (what the code calls a "verify cycle",
tracked by a `verify_calls` counter throughout `generate_mtpk`). Each cycle
produces 1..depth accepted tokens (not exactly 1), consistent with the
brief's warning not to assume "one decode step" == "one token." Confirmed at
the level of "a cycle exists and ends in an eval/commit," e.g.
`generation.py:9824-9829` and `:12214`; the precise start/end boundary of a
single cycle was not traced statement-by-statement given the function's
size (~5,300 lines) — recommend a closer, dedicated read of one representative
cycle in `generate_mtpk` before wiring a governor hook here.

**7. In MTP, should timing count draft work, target verification, or the
whole cycle as one active interval?**
Not conclusively determined this pass — flagged as needing either a closer
static read or runtime tracing. Leaning toward "whole cycle" (draft forward
+ target verify + commit), since draft forward is real GPU work that also
contends with Moonlight/WindowServer scheduling, and the brief's own
guidance (section 7) says duty-cycle pacing should be based on measured
*active work time*, not on which sub-phase produced the accepted token.

**8. How does current batching change the safe yield boundary?**
In `batched_decode.py`, the safe boundary is the same "one blocking sync"
point per cohort step (`batched_decode.py:584`, and per-cohort-step evals at
1129/1371/1390) — i.e. one boundary yields for the whole cohort at once, not
per-request-in-cohort. Consistent with the brief's expectation that batching
changes *what* one step means, not *whether* a safe boundary exists.

**9. Can concurrency limits be changed live, or only at scheduler-object
creation?**
Partially yes today, by direct precedent — but not for the specific knobs
the brief cares about most, yet. `prefill_chunk_tokens` **is already** in
`DASHBOARD_MUTABLE_SETTINGS_KEYS` (`openai.py:15151`) and can be changed via
`POST /v1/mtplx/settings` with no restart: the handler takes `state.lock`
and does `setattr(state.args, key, value)` (`openai.py:15766-15768`), and
`_scheduler_config_from_args(state.args)` re-derives `BatchSchedulerConfig`
from `state.args` on every call rather than once at startup
(`openai.py:16022-16042`) — so a mutated `state.args.prefill_chunk_tokens`
takes effect on the next chunk without any special plumbing.

By contrast, `max_active_requests`, `decode_batch_max`, and `scheduler_mode`
are **not** in `DASHBOARD_MUTABLE_SETTINGS_KEYS` (`openai.py:15137-15155`)
— they're absent from both the mutable list and
`DASHBOARD_RESTART_REQUIRED_KEYS` (`openai.py:15190+`), meaning today
there's simply no live-settings path for them, mutable or not. Given
`_scheduler_config_from_args` already re-reads `state.args` fresh every
call, it looks architecturally straightforward to add them to the mutable
list following the exact same pattern — but this is inference from the
`prefill_chunk_tokens` precedent, not something directly observed for these
three keys. **Must be verified with an actual runtime test** (mutate
`state.args.max_active_requests` while a concurrent load is running, confirm
admission actually changes) before the governor relies on it for profile
switching.

**10. What authentication mechanism should protect runtime governor
writes?**
Reuse the existing one — don't build a second mechanism.
`_AuthRateLimitMiddleware` (`openai.py:21631`) is installed globally via
`app.add_middleware(_AuthRateLimitMiddleware, state=state)`
(`openai.py:26912`) and therefore already covers every route, including any
new governor endpoints, automatically. It checks the `x-api-key` header
(`_request_api_key()`, `openai.py:3990-3997`) against
`state.args.api_key` using constant-time comparison
(`secrets.compare_digest`, `_request_is_authorized()`,
`openai.py:4008-4012`). `--host` other than localhost requires `--api-key`
or `--api-key-file` to be set at all (`openai.py:3977-3980`). No separate
"admin" credential tier exists — `/admin/*` routes use the same global
middleware as everything else. The governor's admin API needs no new auth
code, just new routes.

**11. What existing stats infrastructure can carry governor telemetry?**
`_mtplx_scheduler_state()` (`openai.py:16086-16230+`) already assembles
scheduler mode/policy/active-lane/telemetry into one dict consumed by the
dashboard and (presumably) `/v1/mtplx/snapshot`. This is the natural place
to fold in governor fields (duty-cycle EMAs, yield counts, etc.) rather than
building a separate stats blob, consistent with the brief's ask to extend
existing stats rather than duplicate them. `mtplx/kpi/runtime_kpis.py` (478
lines) is a second stats surface — not read this pass, worth checking for
overlap before choosing where governor telemetry lands.

**12. What existing memory budget/admission system can be extended instead
of replaced?**
`mtplx/memory_plan.py` (accounting: `MemoryPlan`, `plan_memory`,
`usable_engine_bytes`) for the *budget* side, and
`mtplx/batching/admission.py` (`AdmissionPolicy`, `MemoryPressure`,
`effective_limits()`) for the *pressure→limits* side — see section 1 above.
Together they already implement most of what brief section 14 asks for
conceptually (soft/hard pressure tiers that shrink concurrency and chunk
size).

**Resolved (follow-up pass, same session):** `admission.py`'s
`AdmissionPolicy`/`MemoryPressure` are confirmed **dormant on the live
path**, not just "possibly." `mtplx/server/openai.py:102` imports only
`BatchSchedulerConfig, SchedulerMode, SchedulerPreset` from
`mtplx.batching` — never `AdmissionPolicy` or `MemoryPressure`. `cli.py`
imports only `SchedulerMode` from `.batching.state`. A repo-wide grep for
`AdmissionPolicy`/`batching.admission` shows it referenced only inside
`mtplx/batching/` itself (`admission.py`, `scheduler.py`) — i.e. only by
`MTPContinuousScheduler`, which section 1 above already established is
exercised only by `tests/test_batching_foundation.py`. **No live HTTP
request today is admitted or rejected by `AdmissionPolicy`.** (Correction:
`mtplx/server/request_policy.py`, flagged in the original pass as the
likely live admission entry point, turned out to be unrelated — it's
per-request *generation* policy resolution — sampler/tool/reasoning-effort
config for `/v1/chat/completions` et al. — not admission/concurrency
control. No separate "real" admission entry point was found; there simply
isn't one live today.)

Implication for brief section 14 (memory protection): the governor cannot
just "hook into existing admission enforcement," because there isn't any
on the live path yet. The `MemoryPressure`/`AdmissionPolicy` *logic* is
still good to reuse (the pressure-tier shape is exactly what's wanted),
but wiring it into `server/openai.py`'s actual request-accept path is new
work, not a hook into something already enforced.

**13. Does MTPLX already have a thermal/fan/resource-profile abstraction
worth reusing?**
Yes for *fan/thermal* control, but it is a different axis and not directly
reusable for compute/bandwidth pacing. `mtplx/thermal.py` (2,572 lines) +
`mtplx/fan_mode.py` (32 lines, read in full) implement `mtplx max
--on/--max/--off/--status` (per `INSTALL.md`): `FAN_MODE_DEFAULT/SMART/MAX`
enum, a `MaxSession` context manager (`thermal.py:1246`), and
`SmartFanController` (`thermal.py:1356`), all built around driving an
external tool (ThermalForge/TG Pro) via subprocess/socket, with a sudoers
rule installer for passwordless fan control
(`install_passwordless_sudoers_rule`, `thermal.py:841`). This is a good
*pattern* precedent (CLI verb + status subcommand + HTTP mirror at
`/v1/mtplx/thermal/status` and `/v1/mtplx/thermal/fan_mode`,
`openai.py:27369-27443`) for how the governor's own CLI/HTTP surface should
feel, but the mechanism itself (external fan-control tool) has nothing to
do with GPU/bandwidth duty-cycle pacing — no reuse at the implementation
level, only at the "shape of the feature" level. Also worth noting:
`--profile` (performance profile, section 1 above) is a second, unrelated
existing "profile" concept — three different meanings of "profile" now
coexist in this codebase (performance profile, fan mode, and the new
resource-QoS profile), which is exactly why the brief's
`--resource-profile` naming avoids a third collision.

**14. How are config values overridden by launch-target presets, and where
should resource-profile precedence apply?**
`args._cli_flags` (populated by `_FlagRecordingArgumentParser`,
`cli.py:2065`) is the single source of truth for "was this explicitly
passed." Every config-precedence decision in `config.py` follows the same
shape: `if key in cli_flags: return` (skip the config/preset default),
else apply it — see `_apply_profile_default()` (`config.py:258-266`) as the
closest existing analog. The resource-governor's own precedence resolution
(CLI explicit > persisted config > built-in profile default) should hook in
at the same point, likely a new `_apply_resource_profile_default()` alongside
the existing appliers in `apply_user_config()` (`config.py:207-228`).

**15. What cancellation primitive should governor sleeps wait on so
shutdown/client cancellation stays responsive?**
Two different cancellation primitives currently exist, not yet unified:
- The actual generation loop path uses a poll-style
  `abort_check: Callable[[], bool] | None` callback threaded through
  `generate_ar`/`generate_mtp*` and checked via `_check_postcommit_abort()`
  (`generation.py:2451-2452`) at many commit points throughout the file
  (e.g. `generation.py:2576, 2692, 2740, 2777, ...`).
- The (apparently not-yet-live) cooperative scheduler scaffold in
  `mtplx/batching/state.py` uses a `threading.Event`
  (`RequestState.cancel_event`, `batching/state.py:131`), checked
  synchronously in `MTPContinuousScheduler` (`scheduler.py:171-184,
  239, 261, 284, 307, 318-319`).

For the actual (live) generation path, a governor's cooperative yield should
be implemented so it can be interrupted by the same `abort_check` callback
already passed into `generate_*` — e.g. a short-interval sleep loop that
re-checks `abort_check()` between slices, rather than one uninterruptible
`time.sleep()`/`asyncio.sleep()` for the full yield duration. This matches
the brief's "avoid CPU busy-waiting" + "cancellation-aware" requirements
without inventing a new cancellation channel.

---

## 3. Section 31 — MLX async execution investigation

Summary: MTPLX's own source already documents and works around the exact
laziness hazard the brief warns about, which makes this more tractable than
"investigate from scratch."

- **Sustained prefill**: lazy across the whole chunk. `prefill_rungs.py`'s
  docstring (lines 1-14, quoted above under Q2) states plainly that a
  prefill-chunk forward is built lazily and only dispatched at the
  end-of-chunk `mx.eval()`, and that this causes real, measured GPU idle
  time while the host builds the graph (their fix, `async_eval` rungs, is
  off by default). The existing `_eval(chunk_logits, chunk_hidden)` call at
  `generation.py:5701` **is** the real dispatch/sync boundary — confirmed,
  not inferred. A governor yield placed right after it creates genuine GPU
  idle time, not a no-op.

- **AR decode**: two lanes, two sync shapes. The default blocking lane
  syncs via an explicit `_eval()`/`mx.eval()` gated by `_ar_sync_eval`
  (`generation.py:6199-6202`). The opt-in pipelined lane
  (`MTPLX_AR_PIPELINE`) syncs via `.item()` on the sampled token
  (`generation.py:6265`) instead of a bare `mx.eval()` — `.item()` also
  forces evaluation of whatever lazy graph the value depends on, so it is
  functionally the same kind of sync point, just a different call. A
  governor hooking AR decode needs to handle both call shapes (or hook at a
  point after both lanes have already synced, e.g. right after the token is
  appended to `tokens`/emitted).

- **MTP target verification**: each verify cycle in `generate_mtpk` ends in
  an explicit `_eval()`/`mx.eval()` at its commit point (confirmed at
  several sites, e.g. `generation.py:9824-9829`, `:12214`), consistent with
  the same "build lazily, sync once per unit" pattern. Not fully confirmed:
  whether the *draft* forward within one cycle is separately synced before
  the target-verify forward begins, or whether both stay lazy until one
  shared eval — this affects whether "one active interval" (Q7) should
  literally be one Python-level timer around the whole cycle, or two
  smaller measured intervals. Needs either a closer read of one complete
  cycle or runtime tracing (e.g. wrapping suspect calls with
  `MTPLX_EVAL_AUDIT`, which `generation.py:209-236` already supports as a
  built-in per-eval timing/audit log — genuinely useful for this
  investigation without writing new instrumentation).

- **Batched decode**: same pattern, confirmed via the self-documenting
  comment `mx.eval(sub["x"])  # THE one blocking sync` at
  `batched_decode.py:584`, with `mx.async_eval(...)` dispatched earlier in
  the same loop (`batched_decode.py:603, 614, 822, 837`) specifically to
  overlap host-side graph construction with GPU execution ahead of that one
  blocking point — the same idea as `prefill_rungs.py`, applied to the
  batched decode loop instead of prefill.

**Where NOT to add new synchronization**: everywhere above, an explicit
sync point already exists at the natural chunk/step/cycle boundary. The
governor should insert yields *after* these existing calls, never add new
`mx.eval()` calls of its own — matching the brief's explicit warning
against "turning every tiny operation into a forced sync."

**Built-in tool worth reusing for verification**: `MTPLX_EVAL_AUDIT`
(`generation.py:209-236`) already logs `{elapsed_s, function, line, values}`
per `_eval()` call to a JSONL file when set. This is a ready-made way to
empirically confirm (once real inference is run — not done this pass, see
below) that governor yields land where expected and actually correspond to
gaps in GPU dispatch, rather than trusting the static reading above.

---

## 4. What this pass did *not* do (explicitly, per brief section 25)

Brief section 25 requires "at least one real MLX inference workload" to
validate each prefill/decode hook — **not done in the original
reconnaissance pass**, which was static code reading only (blocked at
the time by low disk space on the dev machine). **Since done**, in a
later implementation session, against a real downloaded model via a live
`mtplx serve` process for both the AR and MTP hooks — see section 5
below and `PROJECT_STATUS.md`'s real-model-validation entries. Still not
done:
- `mtplx/cache_state.py` (4,777 lines) and `mtplx/session_bank.py` (2,612
  lines) were located but not read — needed before touching memory-safety
  admission (brief section 14).
- `mtplx/kpi/runtime_kpis.py` was located but not read — needed to decide
  where governor telemetry should live relative to existing KPI reporting.
- No runtime test was performed confirming that mutating
  `state.args.max_active_requests`/`decode_batch_max` live (outside the
  currently-mutable-settings list) actually changes admission behavior on a
  running server. (The separate question — whether `AdmissionPolicy`/
  `MemoryPressure` in `batching/admission.py` is consulted on the live HTTP
  path — **is** now resolved, see section 1 above: it is not. No runtime
  test was needed to settle that; a targeted import-graph grep was
  conclusive.)

---

## 5. MTP hook note (implementation session, resolves Q6/Q7, not reconnaissance)

Q6 and Q7 above were left genuinely open by Phase 0 reconnaissance. They
were resolved while actually implementing the `generate_mtpk` decode hook,
by reading the current code directly (not by further static guessing):

**Q6 (exact cycle boundary) — resolved, and the first hypothesis was
wrong.** `generate_mtpk`'s internal `emit_new_tokens()` helper (defined
once, called from ~10 different branch sites across the function's
verify_strategy/draft_core branches) looked like a clean single per-cycle
convergence point worth hooking directly. **It is not.** Reading the
call sites showed several fire mid-cycle — e.g. one call happens
immediately after the primary token is sampled, before that cycle's
draft-forward and target-verify work has even happened yet, with the
comment at that site literally noting "everything later this cycle...
validates windows that FOLLOW the primary." A hook placed inside
`emit_new_tokens()` itself would have double-counted tokens across the
multiple calls within one cycle, and worse, would have folded a prior
governor sleep's duration into the *next* call's "work" measurement
(since the second call in a cycle would measure elapsed time including
the first call's sleep) — a real correctness bug, not a cosmetic one,
caught before it was committed by writing a regression test
(`test_yields_never_exceed_one_per_cycle_transition` in
`tests/test_resource_governor_mtp_integration.py`) and manually
inspecting governor stats against known cycle counts.

**The actual hook**: pace at the top of `generate_mtpk`'s
`while len(tokens) < max_tokens:` loop, against the *previous*
iteration's measured wall time and token-count delta, before that
iteration's own work begins — not inside `emit_new_tokens()` at all. This
sidesteps the multi-call-per-cycle problem entirely: "top of iteration N"
is unambiguous regardless of how many internal helper calls iteration
N-1 made. Tradeoff: the very last cycle's work is never paced against
(no "next iteration" exists to trigger it) — negligible, since generation
is ending anyway.

**Q7 (what counts as governed "work") — resolved.** The codebase's own
telemetry keeps `draft_time`/`verify_time` as separate accumulators, never
summed into one "cycle work" variable, so there's no existing convention
to just reuse here (unlike the AR/prefill hooks, which reused an existing
sync point directly). The MTP hook's `work_seconds` is a new, whole-cycle
wall-time measurement (draft-forward + target-verify + commit +
`emit_new_tokens()`'s own housekeeping, which can itself block on a real
`mx.synchronize` barrier per its docstring) — a deliberate choice matching
brief section 7's "pace on total measured active work time," not an
existing measurement being repurposed.

**Validated against a real downloaded model, not just a toy model**: via
a live `mtplx serve` process running `Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed`
at its **default** `generation_mode` (i.e. without forcing `"ar"`, which
is what the earlier AR-only hook required and which was the whole reason
this MTP hook was urgent — see PROJECT_STATUS.md's real-model-validation
entries for the full before/after). A 150-token request took 1.03s at
`max` and 3.11s at `interactive` (`decode.effective_duty_cycle: 0.4`
exactly, `natural_tps_ema` ≈139.3, `delivered_tps_ema` ≈55.7 — 139.3 ×
0.4 ≈ 55.7, matches the formula), switched live via the admin API with no
restart, on the model's own default MTP decode path.

These are the concrete next steps before further governor phases
(MTPLX_AR_PIPELINE lane, batched decode, remaining prefill functions,
memory-safety admission) begin.
