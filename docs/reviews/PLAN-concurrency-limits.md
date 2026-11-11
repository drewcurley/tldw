# PLAN — limits that match what the work costs

**Status: PASS WITH ITEMS** — built; plan and build each reviewed by Backend, SDET and Ops; every blocker fixed and every item done or recorded.

Brief: "is there a good reason why i'm capped at doing 2 videos at a time?" There
isn't. `MAX_CONCURRENCY = 2` dates from the first server commit (2026-06-18) and
every kind of request shares those two slots. Agreed limits: about 6 for `claude`
calls (prefetch included), 2 for text-to-speech, none for the cache check and for
previews that are already made.

Review scope: bug fix → Backend + SDET + Ops.

## What happens today

One `threading.Semaphore(2)` on the server, taken non-blocking (busy → 429) by:

| route | what it actually does | slot held for |
|---|---|---|
| `/summarize`, `/summarize/stream` | `claude` call (or a cache hit) | the whole stream |
| `/segments/stream` | `claude` call, a cache hit, or *waiting* on a prefetch | the whole stream, including the wait |
| `/ask/stream` | `claude` call | the answer |
| `/speak`, `/speak/stream` | Piper + ffmpeg, CPU-bound | the whole playback (up to 600s) |
| `/preview` | memo hit, or a short Piper run (first use may download a voice) | the request |
| `/summarize/cached` | one disk read | the request |
| *(prefetch thread)* | `claude` call | **nothing — unbounded** |

So one video (summary streaming + listening, or summary + a question) fills the
server, a free disk read gets "busy", and the one piece of background work is the
one thing with no limit.

Measured on this machine (12 cores, 36 GB): a `claude` process is ~200 MB RSS and
spends its time waiting on the network; Piper is the CPU cost.

## The change

1. **Two pools instead of one** (`server.py`):
   - `CLAUDE_LIMIT = 6` — summarize, segments, ask, and prefetch.
   - `SPEECH_LIMIT = 2` — speak, speak/stream, and preview *synthesis*.
   - Transcription keeps its own single permit, unchanged.
   - `MAX_CONCURRENCY` is removed (no aliases left behind).
2. **A small `_Slots` class** rather than a bare `Semaphore`, because prefetch needs
   to ask "is there room *and* would I leave some for a person?", which a semaphore
   can't answer without reading its private counter. `try_take(reserve=0)`,
   `give_back()`, `take()` (blocking, for re-taking after a wait), and `in_use` for
   tests and logs. `give_back()` without a matching take raises — an over-release
   would silently raise the limit forever.
3. **Prefetch is counted, and never starves a request.** It takes a `claude` slot
   only if at least `PREFETCH_RESERVE = 2` would still be free; otherwise it is
   skipped and logged. Skipping costs nothing: the key-moments request makes the
   same call live when it's asked for. The slot is given back in the prefetch's
   `finally`, so a failed prefetch can't leak one.
4. **Waiting is not work** — the rule the transcription path already follows,
   extended to the other wait: a key-moments request waiting on a running prefetch
   gives its slot back while it waits and re-takes it (blocking, as the
   transcription path does) before it can fall through to a live call. Without
   this, every video that asks for key moments while its prefetch runs uses two
   slots for one call.
5. **No slot for things that cost nothing.** `_guarded` takes the pool to use, or
   `None`. `/summarize/cached` uses none. `/preview` uses none for a memo hit and
   takes a speech slot only around the synthesis.
6. **Say which limit was hit.** The 429 body names it ("6 summaries/questions
   already running" / "already reading 2 summaries aloud"), and the extension shows
   that detail instead of the current "busy with another summary", which is wrong
   once speech has its own pool. Same status code, so nothing else changes
   client-side.

Out of scope (Analyst): queueing instead of refusing; making the limits
configurable; any change to transcription's permit.

## Acceptance criteria

1. With the `claude` pool full: summarize/segments/ask get 429 naming that pool;
   speak, a memo-hit preview, and `/summarize/cached` are all still served.
2. With the speech pool full: speak and an uncached preview get 429 naming that
   pool; summarize and a memo-hit preview are served.
3. Prefetch holds one `claude` slot while it runs and gives it back on success and
   on failure.
4. Prefetch is skipped — no thread, no in-flight event registered — when taking a
   slot would leave fewer than `PREFETCH_RESERVE` free; a later key-moments
   request then runs live and succeeds.
5. A key-moments request waiting on a prefetch does not hold a slot during the
   wait: with every other slot taken, another request is served while it waits.
6. The transcription wait still gives back and re-takes its slot (existing tests,
   moved to the new pool).
7. `_Slots`: limit enforced, reserve honoured, over-release raises, blocking
   `take()` wakes on `give_back()`.
8. Every pool's `in_use` is back to 0 at the end of every test (conftest teardown
   asserts it), so a leaked slot anywhere fails the suite.
9. The extension's 429 message shows the server's detail (vm harness test).

Each guard gets a mutation check: remove it, and at least one test must fail.

## Amendments after plan review (round 1)

Backend and Ops passed with items; SDET blocked on four specification gaps. All
are folded in below and supersede the sections above where they differ.

**A1. Where the pools live (Backend W3 vs Ops W4 — resolved).** Module-level
`_claude_slots` and `_speech_slots`, one instance each, so the request path and the
prefetch can't count separately (Ops), and tests that call `_start_seg_prefetch`
without a server keep working (SDET B3). Backend's concern — state carried from one
test into the next — is met by conftest putting fresh pools in place for every
test. A holder gives back to the pool object it took from, never by re-reading the
module attribute, so a late handler from a previous test can't touch the new pool.

**A2. One mechanism for every exit path (Backend W1, Ops W1, SDET B2/B4).** A
request's slot is a small `_Hold` (taken / released / re-taken / closed), and the
outer `finally` closes the hold, which gives back *only if held*. That makes the
transcription wait and the key-moments wait the same code:
- before waiting: `hold.release()` — before the first progress line;
- the wait ends with a cache hit (prefetch succeeded), a timeout, or a hang-up:
  return; nothing to re-take;
- it falls through to a live call: re-take with `take(timeout=…)` in a loop that
  keeps the heartbeat going, bounded by the route's budget; on running out, a 429
  error event naming the pool, never a silent stall;
- the transcription wait no longer re-takes after the client has gone (today it
  blocks in `acquire()` only to give the slot straight back).

The same applies to a waiter on another request's live selection (`live_evt`):
same code path.

**A3. Prefetch ordering (Backend W2, SDET B3, W2).** The `try_take` happens
inside the existing `_cache_lock` block, after the dedupe check passes and before
the in-flight event is registered. Lock order is `_cache_lock` → slots lock; nothing
takes them the other way round. If `Thread.start()` raises, the slot is given back
and the event popped. In the prefetch's `finally`, the slot is given back *first*,
then the event popped, then set — so whoever is woken finds the slot free.

**A4. The reserve doesn't count the caller's own slot (Backend W5).** Two of the
three call sites run inside a summarize request that has finished its `claude` call
and is about to give its slot back; their prefetch is in effect taking over that
slot. Those pass `caller_holds_slot=True`, which lowers the reserve by one. The
`/summarize/cached` call site holds no slot (A5 below) and passes False. Without
this, opening five videos at once would skip prefetches that run today.

**A5. Over-release is counted, not just raised (SDET B2).** `give_back()` with
nothing held raises *and* increments `over_released`. A raise inside a handler's
`finally` is printed by `socketserver` and otherwise lost; the counter isn't.
conftest asserts it is 0.

**A6. conftest (SDET B3).** Fresh pools per test. Teardown polls, inside the
existing deadline loop, until both pools' `in_use` is 0 (handlers can outlive the
client: a `Content-Length` response returns before the handler's `finally` runs),
then asserts `in_use == 0` and `over_released == 0`. `_preview_cache` joins the
registries cleared per test. `_wait_prefetch_idle` in the tests also waits for the
pool.

**A7. Observability (Ops W2, suggestions).**
- Every 429 logs `busy: claude 6/6` (pool name, in use / limit).
- A skipped prefetch logs `prefetch skipped for <vid>: claude 4/6 busy`.
- Startup prints the limits next to the listening line.
- Startup prints which login the `claude` CLI will use — presence only, never a
  value: `CLAUDE_CODE_OAUTH_TOKEN` set, or `ANTHROPIC_API_KEY` set (bills the
  API), or neither, with a one-line warning that concurrent calls on the keychain
  login can log you out (anthropics/claude-code#76905) and that `claude
  setup-token` avoids it. More concurrency makes that race more likely, so this
  belongs in this change.

**A8. All three extension messages (Ops, SDET criterion 9).** `httpError`,
`speakError` and `askError` all hard-code their 429 text. All three show the
server's detail when there is one, and a generic message when there isn't.

**A9. Tests made non-vacuous at the new limit (SDET B1).** Every test that
proves "served despite load" fills the pool to `limit − 1` (or `limit`, for the 429
tests) from the test thread first, so a missing release is caught at 6 as it was
at 2. The silent-claude test fills `limit − 1` and sends 2 requests, instead of 7.

**A10. Guards and the tests that must catch their removal (SDET W1).**

| guard | test (mutation must fail it) |
|---|---|
| each route → its pool (6 routes) | route × pool-full matrix: expected status per cell |
| `/summarize/cached` takes nothing | matrix, both pools full → 200 |
| memo-hit preview takes nothing | matrix, speech pool full → 200 |
| preview synthesis takes a speech slot | speech pool full, uncached → 429; synthesis that raises gives it back |
| reserve check, both sides of the boundary | `LIMIT−RESERVE−1` held → runs; `LIMIT−RESERVE` held → skipped, no event, stub not called |
| `caller_holds_slot` lowers the reserve by one | the same boundary, with and without the flag |
| dedupe before take | two prefetch starts for one key → `in_use == 1` |
| prefetch gives back on success and on failure | blocking stub: `in_use == 1` while running, 0 after; raising stub → 0 |
| key-moments waiter releases while waiting | prefetch running + test holds `LIMIT−2` → another request is served mid-wait |
| waiter re-takes before a live fall-through | prefetch fails → the live call runs while holding a slot (`in_use` observed inside the stub) |
| transcription wait releases | test holds `LIMIT−1` → a captioned request is served mid-transcription |
| `_Hold.close` only gives back if held | unit: release then close → no over-release; raise inside a released block |
| over-release raises and counts | `_Slots` unit tests |
| extension shows the 429 detail (×3) | vm harness: `fn(429, detail) == detail`, `fn(429, "")` non-empty |

**Round 1b (SDET re-review: PASS WITH ITEMS), folded in:**
- **A timed-out wait keeps today's behaviour.** It falls through to a live call,
  as it does now (the return value of `_wait_with_heartbeat` is ignored); A2's
  "return on timeout" is withdrawn. Only a cache hit after the wait returns
  without re-taking. Test: tiny `SEGMENTS_TIMEOUT`, blocked prefetch → the live
  call runs, holding a slot.
- **Re-take budget:** `_Slots.take(timeout)` returns False when nothing frees
  (unit test); HTTP test — pool full when the prefetch fails, small budget → a 429
  error event naming the pool, `in_use` back to its start, `over_released == 0`.
- **No re-take after a hang-up:** the transcription hang-up test also holds the
  pool full and asserts the handler exits (pool drains, no over-release) instead
  of blocking in `take`.
- **Login line:** presence only, worded as "no token in the environment" rather
  than a claim about which login the CLI will use; when both variables are set,
  both are named and no precedence is claimed. conftest deletes
  `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY`; tests set each branch
  explicitly (neither / token / API key / both) and assert the value never
  appears in the output.
- **Criterion 5 ordering:** start the prefetch first, then fill `LIMIT−2` —
  filling first makes the reserve skip the prefetch and the test proves nothing.
- The 429 log line and the skipped-prefetch log line are asserted with `capsys`.

**Out of scope, recorded (Backend S2, Ops):** a per-voice lock in
`ensure_voice` (two first uses of one voice can clobber each other's download —
predates this change); a socket timeout on the handler (a client that stays
connected but stops reading holds its slot); a periodic "pool stuck full" warning.
The JS checks skip when `node` is absent; node is present on the only machine that
runs them, accepted knowingly.

## Risks (Ops)

- **Leaked slot** → the server gradually refuses everything. Mitigated by
  `give_back()` in `finally` everywhere, the over-release check, and criterion 8.
- **Deadlock** between a waiter re-taking a slot and a prefetch that needs one:
  not possible — prefetch never blocks (try-only), and a waiter holds no slot
  while waiting.
- **More concurrent `claude` processes** → more memory (6 × ~200 MB) and more
  simultaneous use of the plan's quota. Six was the agreed number; the reserve
  keeps background work from taking the last two.
- Rollback: one revert. No persisted state changes.

## Build review

**Backend: PASS WITH ITEMS.** No blockers. Every take, release, re-take and give-back
balances; no lock-order inversion (`_cache_lock` → pool, and the pool never blocks
under `_cache_lock`); keep-alive connections are safe because `self._hold` is set on
every request that can reach a wait. Items, all done:
- A client hanging up while `_retake` waits raised out of the handler (log noise,
  no leak). `_retake` now returns False on `OSError`.
- If giving the slot back in `_finish` ever raised, the in-flight marker stayed
  and that video's key moments waited 300s for nothing. Now `try/finally`, tested
  by making `give_back` raise.
- Recorded, not changed: the "Waiting for a free slot" line has no percent; a
  re-take after a long wait can still run a call for a client that already gave
  up (true before this change too).

**Ops: PASS WITH ITEMS.** Log lines, startup lines (names only, never values; only
for the Claude CLI backend) and both-version compatibility checked. Items, all done:
- What `serve()` prints wasn't tested — now it is, for both backends.
- A malformed custom model command could stop startup now that it's checked
  there; contained, and tested.
- Startup lines flushed. README: the limits, and the `claude setup-token` tip.
- `extension/dist-firefox/` (git-ignored) rebuilt locally; after merging, rebuild
  and reload it if you use Firefox.

**SDET: BLOCK → fixed.** All 25 original mutants were caught by the test meant to
catch them. The blocker: nothing tested that the two summary call sites pass
`caller_holds_slot=True` — removing either left every test green. Fixed with one
HTTP test per call site, the pool filled so the prefetch fits only with the flag.
Also fixed: `take()` waking on a give-back was only "proven" by its own timeout;
the thread-start cleanup, the transcription's failed re-take and `serve()`'s output
had no test; the hang-up test's fixed 2.5s sleep is now a wait for the job's
progress reads to stop; the route matrix asserts exact statuses. Writing the
"Waiting for a free slot" test found a real gap: with a short budget, `_retake`
used it all on its first wait and gave up without ever saying it was waiting. It
now tries once without waiting, announces, then waits in steps that can't overrun
the budget.

**SDET re-check: PASS WITH ITEMS**, items done. All 35 mutants caught by the test
meant to catch each, and all 11 of the reviewer's own earlier survivors that still
apply are now caught. Two gaps were left in code added in this round, now tested: a
client hanging up while waiting for its slot back is let go quietly (no traceback,
nothing given back twice, the handler finishes), and the re-take can't overrun its
budget (a 0.3s budget answers in under 1.5s). The thread-start test now patches only
the server module's view of `threading`.

Departure from Round 1b, noted: the hang-up test counts re-takes (stronger than
filling the pool and watching it drain) rather than filling the pool.

## Verification

- 660 tests pass, three runs in a row (~25s each); the tests that wait across
  threads passed 10–15 repeated runs.
- 37 mutants, one per guard (route → pool for each route, the reserve on both sides
  of its boundary, the hand-over flag at all three call sites, every give-back,
  every release and re-take, the startup lines, the extension's three messages) —
  all caught.
- The real `~/.cache/youtube-tldw` and `~/.config/youtube-tldw` unchanged across the
  runs (151 entries: names, sizes, mtimes).

## Sign-offs

Review scope for a bug fix: Backend + SDET + Ops.

- [x] Backend Engineer — PASS WITH ITEMS, items done
- [x] SDET — BLOCK, fixed; re-check PASS WITH ITEMS, items done
- [x] DevOps/Ops — PASS WITH ITEMS, items done
- [ ] Analyst — not required for a bug fix; scope held to the brief (queueing and
      configurable limits stayed out)
- [ ] Architect — not required; no change to the security posture (loopback, token,
      CORS untouched; the startup line prints variable names, never values)
- [ ] Data Engineer — not required; no schema or stored state changes
- [ ] Frontend Engineer — not required; the extension change is three message
      functions, covered by the vm harness
- [ ] UX Designer — not required; the visible change is the wording of a busy message

Lenses: not a major decision; not run.
