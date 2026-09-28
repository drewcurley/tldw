# PLAN — the transcript comes from the page's own panel

**Status: PASS WITH ITEMS**

The 429s that started this: every summarize was rate-limited at YouTube, on the
first request of the day, while the same machine could open videos in a browser all
afternoon. This is the third attempt at moving the transcript off the server; the
first two are in the branch history and both failed for reasons worth keeping.

## What the 429 actually was

Measured, not inferred. tldw makes exactly two yt-dlp calls per video, and only one
of them is refused:

| request | result |
|---|---|
| `yt-dlp -J` (metadata) | **200**, 11MB of JSON |
| caption download (`timedtext`) | **429** |
| that same signed caption URL, plain `curl` | **429** |
| ...with a browser User-Agent | **429** |
| ...with `Referer`/`Origin`/`Accept-Language` too | **429** |
| the page's own `baseUrl`, fetched *from the watch page*, `json3`/`vtt`/raw | **200, empty body** |

So it was never the metadata call, and never really a rate limit: YouTube has
stopped serving `timedtext` to anything that isn't the player. It answers a
non-browser client 429 and a page fetch 200-with-nothing, the signature of a
proof-of-origin token we can't mint. That is why the previous attempt could only
ever report `captions-empty (json3-empty,vtt-empty,raw-empty)` — it was not a bug in
the fetch, and no format, header or retry was going to fix it.

Cookies were the other candidate and are not available either: every Chromium
profile directory on this machine reads as empty and Safari's returns `Operation not
permitted`. Those are macOS TCC refusals, which apply to the calling application, so
this is *undetermined* rather than ruled out — see Items.

## What still works

YouTube's own transcript panel, because the page loads it for itself. So: open the
panel, read the rows it rendered, put it back.

- **No request to YouTube from the server at all** on this path, and none added in
  the browser either — the previous version fetched the watch page a second time
  (~1MB) for `ytInitialPlayerResponse`; title, channel and duration all come from
  the page's own metadata tags instead.
- It runs in the **content script**, not the worker: the page's own origin and
  session, no host permission, no MAIN-world injection. That last point is what sank
  attempt #2 — `executeScript` into MAIN needs a grant `activeTab` doesn't give.
- The cues are *better* than the caption track: punctuated and capitalized
  ("If you want to know what the printers used to print…") rather than ASR
  fragments, and ~270 rows instead of ~1,900 for a 30-minute video, which is less
  index-picking work for segment selection.

## Design

- **Reading a row is structural, not by class name.** YouTube's generated classes
  churn; "the timestamp is the child that reads like a clock, the words are what's
  left" has held across both the old `ytd-transcript-segment-renderer` and the
  current `transcript-segment-view-model`. The row also carries a screen-reader twin
  of the timestamp — "5 seconds" — which is English-only, so it is dropped by class
  rather than by matching its text.
- **The trigger is found structurally too**
  (`ytd-video-description-transcript-section-renderer button`). Matching the label
  "Show transcript" would have worked only in English.
- **Ends are reconstructed server-side** (`transcript.parse_panel_rows`). The panel
  gives a start and the words; on screen a line simply runs until the next one
  starts. The last line runs to the video's duration, because its start is where the
  closing sentence *begins*, not where it stops.
- **Everything from the page is untrusted** and arrives over HTTP. Rows that aren't
  a start plus some text are dropped rather than repaired; starts are bounds-checked
  and `bool` is rejected explicitly (`isinstance(True, int)` is true in Python);
  rows and per-row text are capped.
- **The panel is put back.** We opened it to read it; leaving it open would
  rearrange the page around someone who only asked for a summary. A panel the viewer
  already had open is left alone.
- **Back-compat**: an extension older than this change still sends `captions`, and
  the server still parses it. Cues win when both arrive.

## Measurements (real pages, real `content.js`)

| | result |
|---|---|
| video whose panel fills | **`ok-panel (271 cues)`, 481ms**, 31,070 chars, title/channel/duration correct, panel restored to `HIDDEN` |
| video whose panel doesn't | **`panel-empty`, 9.0s**, metadata still returned, panel restored |

## Verification

- `pytest`: 448 passing, 30 new — cue pairing, the last cue's end, sorting, the
  untrusted-input table (12 shapes), caps, the server preferring cues over captions,
  and the old caption path still working.
- `tests/js/page_transcript_checks.js` rewritten. It builds a small but *real* DOM —
  nodes match by tag, class, id, attribute and descendant — so the selectors in
  `content.js` are exercised rather than stubbed. It covers both renderers, the
  already-open case, the never-fills case, a row with no words, and a thrown error.
- **The real `content.js` code was run against real YouTube watch pages** (both
  outcomes above), which is how this repo verifies client code — there is no JS
  harness for the extension.

**A bug the tests caught:** `transcriptPanel()` originally found the panel *by its
rows*, so in the one case this whole change exists to handle — a panel that opens and
never fills — there were no rows to find it by, and we left YouTube's transcript
panel sitting open on the page. It now falls back to the panel's `target-id` and
expanded state.

## Review dimensions

A bug fix (Backend + SDET + Ops by the table), plus UX because it now touches the
page's own UI. Checked in one pass, not by eight subagents — recorded as such.

- **Backend** ✅ One parser, in `transcript.py` where cue-building belongs; the
  server stays about HTTP. Fallback unchanged and still reached on every failure.
- **SDET** ✅ See Verification. Both outcomes verified against real pages.
- **Ops** ✅ No new dependency or config. Old extensions keep working. The status log
  no longer truncates at 40 characters — that truncation ate the `raw-empty` tail of
  the diagnostic that identified this problem in the first place.
- **UX** ✅ The panel is restored, including when it fails; an already-open panel is
  left alone. Costs ~0.5s when it works.
- **Architect** ✅ Untrusted input validated and bounded before it reaches a prompt
  or a filename. No new network surface — strictly fewer requests than before.

## Items (non-blocking)

- **Not every video's panel fills.** Of six sampled in a logged-out browser, one
  filled. YouTube's *own* `get_transcript` call returns 400 on the others and the
  panel spins forever. Every browser available here is logged out, and logged-out is
  exactly the fingerprint being refused, so this rate is probably pessimistic — but
  it is unverified against a signed-in session, and it is the number that decides how
  much of the 429 problem this actually removes.
- **A video whose panel won't fill now costs ~9s** before the server's fallback
  starts, because we can't answer until we've given up. `PANEL_WAIT_MS` is 8s against
  a measured ~300ms for a panel that is going to fill; it could come down a long way
  if the failure turns out to be common.
- **Cookies are untested, not excluded.** `--cookies-from-browser` couldn't be
  evaluated from here because of TCC. If the panel path proves unreliable, an
  authenticated yt-dlp is the next lever (`tldw config ytdlp_cookies safari`,
  with Full Disk Access for whatever runs `tldw serve`).
- **Timestamps are second-resolution**, where the caption track gave milliseconds.
  Fine for clipping; worth knowing if cuts ever need to be tighter.
- **This depends on YouTube's DOM.** It is read structurally and falls back cleanly,
  but a panel redesign will eventually need this revisited — the parsing is covered
  by tests, the *selectors* can only be caught against a live page.
