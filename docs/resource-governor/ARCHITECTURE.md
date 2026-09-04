# Architecture

## The problem

Apple Silicon shares one pool of unified memory and one GPU across every
process on the machine — there's no OS-level mechanism to hand a process
"40% of the GPU" or "half the memory bandwidth" the way you might slice
CPU cores or set a memory cgroup on Linux. During LLM inference:

- **Prefill** (processing the prompt) can saturate GPU compute for
  seconds at a time on long contexts.
- **Decode** (generating tokens) is memory-bandwidth-heavy — every step
  reads the full model weights plus the growing KV cache.
- Both compete directly with anything else using the GPU: WindowServer
  compositing, a game, and — the motivating case for this fork —
  Moonlight decoding and rendering a game stream from another PC.

macOS/Metal exposes no public API to give a process a fixed share of GPU
cores or bandwidth. So this project doesn't attempt one. It makes MTPLX
cooperate with everything else on the machine from the inside, by
deliberately not running flat-out.

## Why duty cycling, not a fixed tokens/sec cap

A fixed "30 tok/s" limit means wildly different things for a 4B model
capable of 150 tok/s versus a 70B model capable of 10 tok/s — the first
is throttled to a fifth of its speed, the second isn't throttled at all
(and can't produce 30 tok/s to begin with). What we actually want to
express is a fraction of wall-clock time:

> Spend roughly this fraction of time doing real inference work, and
> deliberately yield the rest, regardless of how fast the model is.

For a target duty cycle `d` and a measured unit of work taking `w`
seconds:

```text
target_total_period = w / d
sleep_time = target_total_period - w
```

A 12ms decode step at `d = 0.40` yields ~18ms after it: 12ms work, 18ms
yield, repeat. That 18ms gap is real scheduling headroom — an opening for
macOS/Metal to run something else's Metal work, or for WindowServer to
composite a frame, before MTPLX's next step claims the GPU again.

This is deliberately not a promise of an exact GPU percentage. It's
cooperative pacing: MTPLX spends less wall-clock time actively scheduling
work, which in practice reduces contention, but nothing here guarantees
isolation the way a hardware partition would. See brief section 32 for
why the shipped profile numbers are explicitly framed as starting
hypotheses, not marketing claims.

## Where the yields go: prefill and decode are different problems

MTPLX already chunks long prefills and steps decode one unit at a time —
this project didn't invent that; it inserts a governed yield at the
*existing* natural boundaries between those units, rather than adding new
synchronization points of its own. Concretely: each hook measures the
wall-clock time of one already-existing scheduling unit (one prefill
chunk, one decode step or cycle), then sleeps in short interruptible
slices for a duration computed from the duty-cycle formula above.

**Decode** turned out to have three meaningfully different shapes across
MTPLX's own code, and the hook has a different design for each:

- **Classic AR decode**: one token per step, one clear sync point
  (`_eval()`/`mx.eval()`). Direct hook: pace right after that sync.
- **`MTPLX_AR_PIPELINE` lane**: also one token per iteration, one clear
  sync point (`tok_lazy.item()`). Same direct-hook shape as classic AR —
  confirmed independently before hooking, not assumed from precedent.
- **MTP/speculative decode (`generate_mtpk`)**: a draft→verify→commit
  cycle produces 1..depth tokens, and — this mattered — the function's
  internal token-commit helper (`emit_new_tokens()`) is called from
  several branches *within* one cycle, not once at the end of it. Pacing
  there would have double-counted work and let a sleep bleed into the
  next measurement. The actual hook paces at the top of each loop
  iteration against the *previous* iteration's measured time — an
  unambiguous single point per cycle regardless of how many internal
  calls happened inside it. See `IMPLEMENTATION_NOTES.md` section 5-6 for
  the full account of why this needed a different design, including the
  bug that got caught and fixed before it shipped.

**Prefill** is a chunk loop in every one of its five current
implementations in `mtplx/generation.py` (plain cold prefill, MTP
committed-history streaming, warm SessionBank restore — both its
single-shot "fused" path and its multi-chunk path, and one narrower
MTP-hidden-sequence path). Each hooked implementation paces after its
existing per-chunk sync call, including any additional forced-eval work
that chunk does (e.g. building the MTP draft head's history cache) — not
just the raw forward pass — since that's real GPU-adjacent work too.

One prefill path exists that real default-configured MTP requests
actually took *before* it was found and hooked: `mtp_history_policy`
`"committed"` plus the shipped `sustained` profile's
`MTPLX_SUSTAINED_PREFILL` routes prefill through a different function
than the one covering plain AR requests. This was discovered by testing
against a real running server with real default settings, not by reading
code alone — see `IMPLEMENTATION_NOTES.md` section 6 and
`PROJECT_STATUS.md`'s MTP-hook and full-coverage entries.

## The MLX-laziness hazard

MLX builds compute graphs lazily; a Python-level "sync point" only
creates real GPU breathing room if it corresponds to an actual
`mx.eval()`/materialization boundary, not just wall-clock time passing
while the host builds more lazy graph. MTPLX's own code already
documents and partially works around this hazard elsewhere (see
`prefill_rungs.py`'s docstring) — every governor hook pace *after* an
existing sync call already in the code, never adds a new one. Adding
gratuitous synchronization would risk destroying throughput for no
benefit; this project avoids that entirely by construction.

## Runtime profile switching

The active profile lives on `ServerState.resource_governor`, a single
in-process object read by every generation call and mutated (behind a
lock, inside `ResourceGovernor`) by the admin API. Switching is:

- **Live**: no restart, no model reload, no KV cache reset.
- **Prompt**: a change takes effect at the next safe scheduling
  boundary (the top of the next decode step/cycle, or the next prefill
  chunk) for any request already in flight — not instantly mid-step, but
  within one unit of work.
- **Cancellation-safe**: yields sleep in short slices, polling the same
  `abort_check` callback MTPLX's request-handling code already threads
  through for client disconnects and pressure-based aborts. A client
  disconnecting during a governor sleep doesn't wait for the sleep to
  finish.

## Mechanism inside MTPLX, policy outside

MTPLX's own code has no idea what Moonlight is, whether a game is
running, or whether a human is at the keyboard — and it shouldn't. The
governor only understands: an active profile, duty cycles, a minimum
throughput floor, and admission on/off. Deciding *when* to switch
profiles based on real-world conditions is `mtplx-qos`'s job, entirely
outside the `mtplx` package (see `README.md`). This split is what keeps
the core MTPLX diff small, reviewable, and — the intent from the start —
upstreamable: nothing in `mtplx/resource_governor.py` or its hooks
depends on Moonlight, gaming, or any other machine-specific concept.

## What this is not

No physical GPU partitioning, no NVIDIA-MIG-style hard slices, no exact
memory-bandwidth caps, no Metal driver changes, no private Apple APIs,
and no guarantee of isolation a hardware partition would give you. This
is cooperative scheduling: MTPLX voluntarily steps back, repeatedly and
briefly, so other work gets a real chance to run. On the target hardware
that's usually enough to keep a shared machine usable; it is not a
substitute for actual resource isolation if you need a hard guarantee.
