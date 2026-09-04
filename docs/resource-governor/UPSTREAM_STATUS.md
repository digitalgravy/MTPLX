# Upstream Status

## Tracking

- Upstream: `https://github.com/youssofal/MTPLX.git` (remote `upstream`)
- Fork: `https://github.com/digitalgravy/MTPLX.git` (remote `origin`)
- This work: branch `feature/resource-governor`
- Base commit: `e652d55e2652137a4abcf1312357abbf3eb9d692` (upstream `main`,
  2026-09-01, "ci: fail on any AI attribution in history (authors,
  committers, co-author trailers)")
- As of 2026-09-04: `upstream/main` has not moved past the base commit —
  `feature/resource-governor` has not needed a rebase yet.

## Local patches (this branch, in commit order)

1. `chore: document resource governor architecture` — reconnaissance only,
   `docs/resource-governor/IMPLEMENTATION_NOTES.md`, `PROJECT_STATUS.md`,
   `LLM_NOTES.md`. No production code.
2. `feat: add resource governor core` — new module
   `mtplx/resource_governor.py` (MLX-free) + decode pacing hook in
   `generate_ar`'s classic AR loop.
3. `feat: pace sustained prefill through resource governor` — prefill
   pacing hook in `_prefill()`.
4. `feat: add runtime resource profile API` — `ServerState.resource_governor`,
   `GET /admin/resource-governor`, `POST /admin/resource-governor/profile`.
5. `feat: add CLI and config support for resource profiles` —
   `--resource-profile`/`--prefill-duty-cycle`/`--decode-duty-cycle`/
   `--min-decode-tps` flags and matching `config.toml` keys.
6. `feat: pace MTP speculative decode through resource governor` —
   decode pacing hook in `generate_mtpk`.
7. `feat: complete decode/prefill governor coverage` — `MTPLX_AR_PIPELINE`
   lane hook, remaining three prefill functions hooked.
8. `feat: integrate resource-aware admission safeguards` — `protect`/
   `pause` return `503` on `/v1/chat/completions` and `/v1/completions`.
9. `feat: add mtplx-qos helper` — `scripts/mtplx-qos`, outside the
   `mtplx` package.

## Diff shape

Every commit above is additive: new optional parameters defaulting to
`None`/unset, new functions, new routes, new CLI flags, new config keys.
No existing function signature lost a parameter, no existing behavior
changed when the governor is unset or left at the `max` profile. This
was a deliberate constraint throughout (see `LLM_NOTES.md`'s repeated
"zero behavior change when unused" notes on each hook) specifically to
keep this rebasable and, eventually, reviewable as an upstream PR.

`git diff main...feature/resource-governor --stat` is the authoritative
current diff size; regenerate it before actually opening a PR rather than
trusting a number written here, since it'll be stale.

## Conflicts / deviations from the original brief

- The brief's illustrative API sketch (`MTPLX_RESOURCE_GOVERNOR_CODEX_BRIEF.md`
  section 4) shows `after_decode_step`/`after_prefill_chunk` as `async def`.
  The actual implementation is synchronous — the decode/prefill loops it
  hooks run on MTPLX's single owner thread with no event loop at the call
  site. Documented in `mtplx/resource_governor.py`'s module docstring and
  `IMPLEMENTATION_NOTES.md`.
- The brief's admin API sketch (section 12) shows `PUT
  /admin/resource-governor/profile`. Shipped as `POST`, matching every
  other mutation endpoint already in `server/openai.py`
  (`/v1/mtplx/settings`, fan mode, cache/session clear) — the brief itself
  says to follow existing convention over its own illustrative example.
- The brief expected `_prefill_with_hidden_sequence` to be "the
  representative" chunked-prefill function (section 1's original
  citation). Tracing the real call graph found it has exactly one caller
  behind a narrow condition; `_prefill()` is what the default cold-start
  AR path actually uses. Both are hooked now, but `_prefill()` was
  prioritized first for this reason. See `IMPLEMENTATION_NOTES.md`'s
  Phase 2 correction note.

## Open questions for upstream (not filed yet)

None filed. Before opening an upstream PR:

- Confirm `youssofal/MTPLX` wants this feature at all, and in what shape
  — this fork was built speculatively against a private project brief,
  not an upstream-solicited RFC.
- Resolve whether `MTPContinuousScheduler`/`AdmissionPolicy`
  (`mtplx/batching/`) are genuinely unused scaffolding upstream intends
  to wire up later, in which case this project's admission-refusal
  addition might eventually want to integrate with that instead of
  standing alone — see `IMPLEMENTATION_NOTES.md` section 1's finding that
  neither is reachable from the live server today.
- Check whether upstream's own CI (the base commit's own "fail on any AI
  attribution in history" check) has implications for how this branch's
  commit history should look before a PR — this session followed
  whatever attribution instructions were live in its own tooling, not a
  policy negotiated with upstream maintainers.

## Rebase log

- 2026-09-04: no rebases performed; base commit still current.
