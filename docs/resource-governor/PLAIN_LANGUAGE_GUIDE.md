# Using this without reading code: a plain-language guide

This page is for anyone using this Mac who isn't a programmer and just
wants to know: *how do I stop the AI from making my games stutter?* No
code, no jargon you haven't already met.

If you're comfortable with command lines, config files, and APIs, you
probably want [`README.md`](README.md) instead — it's shorter and more
precise. This page says the same things, just slower and friendlier.

## Don't have this on your Mac yet?

This feature is brand new and isn't part of the official MTPLX download
yet — so if you just installed MTPLX from its website or the App Store
version, you won't have it. It currently only lives in a specific,
separate copy of the project on GitHub.

Getting it installed involves running a couple of commands in Terminal,
which is a step better handled by whoever's comfortable doing that for
you (a tech-inclined friend, or the person who sent you here). Point
them at [`README.md`'s "Install" section](README.md#install) — it has
exact copy-paste commands. Once it's installed, everything below this
line is genuinely yours to drive without any more help.

## What is this, in one paragraph

This Mac can do two jobs at once: run an AI language model that answers
questions over your network, and be a normal desktop computer — including
streaming a game from a Windows PC over Moonlight. Both jobs want the
same graphics chip and the same pool of memory. Without help, the AI can
hog enough of the machine that your game stream starts stuttering. This
feature teaches the AI server to *deliberately slow itself down and take
breaks* while it's working, so there's real breathing room left over for
everything else on the machine. You get to choose how aggressive those
breaks are, and you can change your mind at any moment without restarting
anything.

## Why would I want this

If you only ever use this Mac as a dedicated AI server with nothing else
running on it, you probably don't need this at all — leave it on "full
speed" and ignore the rest of this page.

If you also game on this machine, watch video, do creative work, or just
want the desktop to feel normal while the AI is busy answering someone's
question, this is for you.

## The five settings ("profiles")

Think of these as five buttons, each trading AI speed for how much
breathing room everything else on the machine gets:

- **`max`** — Full speed. The AI uses the machine as hard as it can. Use
  this when nothing else matters right now except getting AI answers
  fast (e.g. it's the middle of the night and you're not gaming).
- **`balanced`** — A middle ground. The AI still runs at a good clip but
  takes noticeably more breaks. Good default for "I might glance at the
  desktop but I'm not actively gaming."
- **`interactive`** — Aggressive breathing room. The AI takes frequent,
  deliberate breaks so games and other interactive things stay smooth.
  The AI will feel slower. **This is the one you want on while gaming.**
- **`protect`** — Stop taking new questions entirely, but let anything
  already in progress finish. Use this if things are getting tight and
  you want the AI to back off completely for a while.
- **`pause`** — Same idea as `protect`; the server stays loaded (so it
  can resume instantly) but won't start anything new until you switch
  back.

None of these unload the AI model or restart the server — switching is
instant and doesn't interrupt anything already running (beyond the
in-flight response finishing at its new, changed pace).

**Important honesty note**: these settings pace *how* the AI works —
they make it take real breaks — but they don't (yet) put a hard limit on
how many people can ask it questions at once, or force smaller chunks of
work. If you need an absolute guarantee, the safest settings right now
are `protect` or `pause`, which stop new work outright.

## How to turn it on

**If you'd rather click than type**, there's a small on-screen control
panel — no Terminal needed. Ask whoever set this up for you where
`mtplx-qos-ui.html` lives (it's a single file, inside the `scripts`
folder of the project), then just open it like any other file (double-
click it, or drag it onto a browser window). You'll see five buttons,
one per setting above, plus live numbers showing what's happening right
now. Click a button, it takes effect immediately. If it says "Not
connected," open the small "Connection" section near the top and check
the address matches wherever the AI server is running (ask your
tech-inclined friend if you're not sure).

The rest of this section describes the Terminal alternative, in case
that's what you already have open — skip it if the button panel above
covers everything you need.

The easiest terminal way is a small helper tool that comes with this
project, called `mtplx-qos`. Open Terminal and try:

```bash
mtplx-qos interactive
```

That's it — the AI server (if it's running) immediately starts taking
more breaks. To check what's active right now:

```bash
mtplx-qos status
```

To go back to full speed:

```bash
mtplx-qos max
```

### Let it decide for you

If you'd rather not remember to switch it manually every time you start
gaming, there's an automatic mode:

```bash
mtplx-qos auto --watch 10
```

Leave that running in a Terminal window (or set it up to run in the
background — ask whoever set up this Mac for you if you're not sure how)
and it will check every 10 seconds: if Moonlight is running, it switches
to `interactive` for you; otherwise it settles on `balanced`. You don't
have to think about it again.

### If you'd rather set it once and forget it

You can also make a profile the *default* every time the AI server
starts, so you never have to remember to switch it at all — ask whoever
manages this Mac's setup to add `--resource-profile interactive` (or
whichever profile you prefer) to how the server gets started, or to add
a couple of lines to its settings file. This is a one-time setup step,
not something you do every day.

## What you'll actually notice

- **On `interactive`**: the AI visibly takes longer to answer. That's
  the entire point — it's trading its own speed for your game's
  smoothness. If it feels *too* slow for your taste, try `balanced`
  instead.
- **On `max`**: the AI is as fast as this Mac can make it, and anything
  else you're doing on the machine may stutter or feel sluggish while
  it's actively working.
- **Switching profiles**: instant. No waiting, no restart, no "please
  hold while the model reloads."
- **A response that's already streaming to you**: keeps going, it just
  gradually speeds up or slows down to match whatever profile is active
  now, from that point forward.

## Simple troubleshooting

**"I ran `mtplx-qos interactive` and nothing happened."**
Make sure the AI server is actually running first (`mtplx-qos status`
will fail with a connection error if it isn't). If the server is on a
different machine on your network, add `--url http://<that-machine>:8000`
to the command.

**"It says I need a password / key."**
Some setups require an access key for security. Ask whoever set this Mac
up for the key, then add it once: `export MTPLX_QOS_API_KEY=<the key>`
before running `mtplx-qos` commands (or ask them to set it up
permanently for you).

**"Games still stutter even on `interactive`."**
A few possibilities, roughly in order of how likely they are:
1. The AI wasn't actually busy with a request at the time you noticed
   stutter — check `mtplx-qos status` and see if it shows recent
   activity. If it's been idle, the AI isn't your problem right now.
2. The settings haven't been tuned for *this specific* Mac yet — the
   numbers behind `interactive` are a reasonable starting guess, not a
   promise, and may need adjusting for your hardware. That's a job for
   whoever manages the technical side (see `BENCHMARKS.md`).
3. Something entirely unrelated to the AI is causing the stutter (network
   issues, another app, etc.) — worth ruling out by pausing the AI
   server entirely (`mtplx-qos pause`) and seeing if the problem
   persists.

**"I want the AI to stop taking any new questions right now."**
`mtplx-qos protect` (or `mtplx-qos pause`) — either stops new requests
immediately with a clear error message to whoever asks, while leaving
the server ready to resume the instant you switch back.

## If you want more detail

Everything above is a simplified version of [`README.md`](README.md),
which has the exact commands, settings, and numbers for people
comfortable with a bit more technical detail. [`ARCHITECTURE.md`](ARCHITECTURE.md)
explains *why* it's built this way, for anyone curious.
