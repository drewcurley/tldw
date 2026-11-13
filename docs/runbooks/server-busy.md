# The server says it's busy

This is tldw's **own** concurrency limit. It has nothing to do with YouTube; for YouTube's limit, see [`youtube-rate-limit.md`](youtube-rate-limit.md).

**Severity:** Sev3 — it clears as soon as the work already running finishes (`incident-severity.md`).
**Last verified against the code:** 2026-11-12, branch `chore/docs-tier2` at `a7f104c`.

## What you see

The two messages you normally see name the limit that was hit, so they are built at run time and can't be quoted here:
- **The model pool:** "Already working on" the claude limit's number, then "summaries, key moments or questions. Try again in a moment." Built at `src/youtube_tldw/server.py:642-644`. Shown for a summary, key moments or a question.
- **The speech pool:** "Already reading" the speech limit's number, then "summaries aloud. Try again when one finishes." Built at `server.py:645-647`. Shown for Listen and for a voice preview.

Both are sent with status `429`, and the panel shows the server's text as-is (`extension/background.js:577`, `:442`, `:493`). The exact wording, with today's numbers, is in `docs/contracts.md`, "Server error text the extension can show".

Only if the server sends no text at all (an older server) does the panel fall back to its own copy:

> Server busy, try again in a moment.

That fallback is at `background.js:577`, `:442` and `:493`.

Before a refusal that comes **during** a request, the progress line says it is waiting for a free slot, with the pool's count (`server.py:1077`); that text is built at run time too.

## Why it happens

- The server runs at most a fixed number of model calls at once, and a separate, smaller number of read-aloud syntheses. The two pools never refuse each other's work. The sizes, and the rules below, are in `docs/contracts.md`, "Limits, pools and slots" (`server.py:560-563`).
- When a pool is full, the server says so straight away with a `429`; it never queues the request (`server.py:1055-1063`).
- A request that gave its slot up while it waited (for a transcription or a key-moment prefetch) tries to get one back. If it can't within the retake budget, it ends with the same busy message, in the stream (`server.py:1065-1088`).
- Background key-moment prefetch never takes the last slots, so it is never why your click is refused (`server.py:465-466`; `docs/contracts.md`, `PREFETCH_RESERVE`).
- What counts: every summary, key-moment selection and question running right now, from **every** tab and panel, plus any prefetch.
- In the `tldw serve` terminal, each refusal prints a `busy:` line naming the pool and how many of its slots are in use (`server.py:1062`, `:1083`). The limits are also printed once at startup (`server.py:1943`).

## Check

1. Look at the `tldw serve` terminal for the `busy:` line. It tells you which pool is full.
2. Think about what else is running: other YouTube tabs with the panel working, a question still being answered, Listen still playing.

## Fix

1. **Wait for one of the running requests to finish, then try again.** That's all the message asks.
2. **For Listen,** stop the reading that's already playing, or let it finish (the message says "Try again when one finishes").
3. **If it keeps refusing while you're sure nothing is running,** restart the server: Ctrl-C in its terminal, then `tldw serve` again. The slot counts live in the server's memory, and in-flight requests end with the process (`server.py:651`; `docs/deployment.md` § 4, "Stopping"). Then click TL;DW again.

## Don't

- **Don't change the limits** to make it go away. They size the work to what the machine and your model plan can take; changing them is a code change with its own review (`docs/contracts.md`, "Limits, pools and slots").
- **Don't click repeatedly.** Each click that gets a slot adds to the load.

## If that didn't work

- A restart didn't help and nothing else is running: that would be a bug. Record it as a task (`tasks/README.md`).
- The message mentions YouTube, or "HTTP 429" after "rate-limiting": see [`youtube-rate-limit.md`](youtube-rate-limit.md).

## How this was verified

- **Read on `a7f104c`:** `server.py:465-471`, `:560-563`, `:600-656`, `:1050-1088`, `:1943`; `background.js:440-447`, `:491-497`, `:571-581`.
- **The one quoted message** was checked against its string constant in `background.js`. The two busy messages are f-strings, so they are described and cited, not quoted.
- A busy refusal wasn't triggered for this doc: it needs more model calls at once than the limit, and this pass made no model calls. The refusal paths are covered by `tests/test_server.py` (`docs/contracts.md`, "Limits, pools and slots").

## Update this doc when

- `_make_pools`, `_Slots`, `_refuse_busy`, `_retake`, `CLAUDE_LIMIT`, `SPEECH_LIMIT`, `PREFETCH_RESERVE` or `RETAKE_BUDGET` changes (`src/youtube_tldw/server.py`);
- the `429` branch of `httpError`, `askError` or `speakError` changes (`extension/background.js`);
- `docs/contracts.md`'s "Limits, pools and slots" table changes.
