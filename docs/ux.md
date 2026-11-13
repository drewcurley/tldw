# UX — tldw

Who uses tldw, the journeys they take through the browser extension, and the user-facing floor: accessibility, design system, internationalization and microcopy. This doc is **descriptive**. It records what `extension/content.js`, `extension/background.js`, `extension/options.js` and `extension/options.html` do today, and sets no new direction.

It cites the Tier 1 docs rather than restating them:

- every message the user can read, and the "names a next step" invariant, are in `contracts.md` → [User-facing message contracts](contracts.md#user-facing-message-contracts) (`contracts.md:736-862`);
- every key, dialog attribute, live region and accessible name is in `contracts.md` → [Keyboard and accessibility baseline](contracts.md#keyboard-and-accessibility-baseline) (`contracts.md:1012-1094`);
- every `chrome.storage.local` setting and its defaults are in `contracts.md` → chrome.storage.local keys (`contracts.md:864-888`);
- TTLs and caps belong to `data-model.md`.

**On this fork the extension's manifest `name` is `TL;DW — Hackathon`** (`extension/manifest.json:3`, `extension/manifest.firefox.json:3`; T-019). That name shows only in the browser's own extension lists. In Chrome the two install side by side; in Firefox the hackathon build replaces the normal one, because both use the same `gecko.id` (`manifest.firefox.json`; T-019). The toolbar tooltip stays `TL;DW this video` (`manifest.json:10`), and the panel's heading stays `TL;DW` (`content.js:251`). The Options page's title is `TL;DW options` and its heading `TL;DW settings` (`options.html:5`, `:27`).

## Personas

There has been no user research. Both personas are grounded in what the repo says about its users.

### 1. The owner (the one known user)

The owner wrote tldw and uses it daily on their own machine. In the owner's words, "i am the only known user, this runs only on my local machine, which only i use" (`tasks/T-009.md:38`). They run `tldw serve` themselves, have the `claude` CLI logged in on their own plan, and know what the token is for. On this fork they keep the normal extension and the hackathon one installed in one Chrome, with only one enabled at a time (`tasks/T-019.md:18`). They often close the panel to go back to the video, so work that keeps running after the panel closes matters to them (`README.md:177-184`, `options.html:68-75`).

**Primary goals:**
- get the gist of a video without watching it, and read it within seconds of clicking (`README.md:402-405`);
- listen to the summary instead of reading it;
- watch only the key moments in the real player;
- ask follow-up questions and jump to the cited moment;
- close the panel without losing work in progress.

### 2. A developer installing it from the README (or a hackathon attendee)

Someone who clones the repo and follows the README's install, or the hackathon audience that `onboarding.md` is written for: "you can run the product first and read the process second" (`docs/onboarding.md:3`). They install with pipx (`README.md:50-63`), start `tldw serve`, load the unpacked extension and paste the token into Options (`docs/onboarding.md:35-64`, `extension/README.md:13`). They meet the setup's sharp edges first: the token printed at startup (`docs/onboarding.md:43-47`), the host permission tied to port 8765 (`docs/onboarding.md:58`), and the reload needed after pulling changes (`extension/README.md:8-11`).

**Primary goals:**
- get from a fresh clone to a first summary inside the first 30 minutes (`docs/onboarding.md:7`);
- know when setup is wrong, and what to do about it, from the messages the extension shows;
- understand the product well enough to change it in a first PR (`docs/onboarding.md:151`).

## Primary journeys

Seven journeys, all in the browser extension.

**The CLI is not a journey here:** `N/A — tldw <url>` is a terminal command with its own text, video and audio modes. Its usage is in `README.md:102-176`, and its flags are in `contracts.md` → CLI (`contracts.md:394-461`).

Messages below are named, not quoted. Their exact text is in `contracts.md`'s message tables. Every screen's way out is the same unless a journey says otherwise: **Escape**, the **✕** button, or a click on the backdrop closes the panel (`content.js:263-266`, `:300`), under the "close action" setting (journey 7).

### Journey 1 — Summarize a video

1. **Trigger.** On a YouTube `/watch` page, the user clicks the toolbar action (tooltip `TL;DW this video`, `manifest.json:9-12`). **There is no keyboard shortcut**: the manifest declares no `commands` (`contracts.md:937`). The click injects `content.js` and sends it `TLDW_INVOKE` (`background.js:58-68`).
2. **Wrong page.** On any other page the extension can inject into, the panel opens with the "not a video page" message (`background.js:62-65`). On a page it can't inject into (a `chrome://` page or the Web Store), nothing appears: `executeScript` runs before the video check, with no error handling (`background.js:58-68`).
3. **No token.** With no token saved, the first request answers with the "no server token set" message, before the page is read (`background.js:99-103`).
4. **Cache check.** The panel asks whether a summary already exists, from the worker's cache and then the server's (`content.js:650-656`, `background.js:92-127`). A hit renders at once as a cached result (journey 2). Any failure counts as a miss (`content.js:556-563`).
5. **Reading the page.** On a miss, the panel opens YouTube's own transcript panel, reads it and closes it again (`content.js:515-554`). Then it asks for the summary (`content.js:580-587`).
6. **Loading.** A spinner, a status line starting at `Starting…`, a bar and a percentage (`content.js:673-682`). The server's progress messages replace the status line. During the model step the bar creeps about 1 % a second and never parks at 99 % (`content.js:21`, `:725-734`).
7. **Writing.** The title, channel and length arrive first, then each key point and paragraph as it is written. The header says `writing…`, and the action buttons are shown disabled, titled "Available once the summary is finished" (`content.js:852-853`, `:877-893`).
8. **Done.** The finished summary replaces the partial one in the same layout, so nothing below it moves (`content.js:849-851`, `:926-954`; journey 2).
9. **Closing mid-run.** Under the default setting the work keeps going. A notice in the corner says the summary is ready, and clicking it opens the summary. Clicking the toolbar action again re-attaches to the run in flight, restoring what it had written or its progress (`content.js:322-341`, `:636`, `:753-777`, `:1912-1933`; `README.md:177-184`).

**Errors:** a status from the server is turned into text by `httpError` (`contracts.md:748-758`). The worker's and panel's own timeouts and lost connections are in `contracts.md:792-833`. All of them show in the panel's main error view (`content.js:842-847`), or as the corner notice if the panel was closed.

### Journey 2 — Read the result (cached, and regenerate)

1. **Header.** `TL;DW`, or `TL;DW (cached)` when the result was not freshly generated for this click (`content.js:930`; what "cached" covers is in `contracts.md:847`).
2. **Meta line.** Channel · original length → `~<length> read` · an `original` link. Once key moments are known, a trimmed-video note is added. A summary written from a Whisper transcription is labelled `transcribed from audio by Whisper (<model>)` (`content.js:934-936`, `:1658-1681`, `:1891-1898`).
3. **Body.** The title, the action row (journeys 3 and 5), Key points, Summary (headings, bullets, bold and italic, all escaped first, `content.js:98-125`), and the model's rationale in small italics when there is one (`content.js:937-941`).
4. **Copy** puts the summary on the clipboard as Markdown and says `Copied` for 1.5 s (`content.js:965-971`, `:1900-1906`).
5. **↻ (regenerate)** asks for a new summary and skips the cache, but keeps the cached transcript (`content.js:253`, `:955-964`; guarded by `tests/js/render_checks.js:330-346`). It does nothing while other work is running (`content.js:959`).
6. **Re-opening.** Clicking the toolbar action again on the same video in the same page session shows the last result at once, as cached (`content.js:1910-1911`). The worker's summary map is keyed by video id only, so after a model or length change ↻ is the way to get a new summary (`docs/data-model.md:389`).
7. **The sponsor ask.** After 25 summarized videos, a box offers a GitHub Sponsors link and `No thanks — don't ask again`. It is shown at most three times, and a dismissal is permanent (`content.js:1186-1220`; guarded by `render_checks.js:245-262`).

### Journey 3 — Listen to the summary

1. **Pick a voice.** The panel's voice picker preselects the Options default (`content.js:1003-1011`). A voice picked here is **not saved** (`contracts.md:880`). The panel's list is hard-coded, while Options asks the server (`content.js:77-92`; T-018).
2. **Preview (optional).** `▶ Preview` plays a short sample. The button shows `…` while it loads (`content.js:1414-1439`).
3. **Listen.** `🔊 Listen to summary` becomes `⏹ Stop`, and the voice picker and Preview are locked. A progress ring and status line track synthesis (`content.js:1035-1078`, `:1448-1452`).
4. **Playback starts early.** Where the browser supports streaming mp3 (`MediaSource`), playback starts on the first block of audio. Firefox has no `audio/mpeg` there, so it plays the finished clip instead (`content.js:29-32`, `:1117-1184`).
5. **Done.** The player (`aria-label="Spoken summary"`) takes focus. A `⬇` button saves the finished mp3 as `<title> - <channel> - tldw version.mp3`, and the Listen button reads `🔊 Regenerate` (`content.js:1813-1884`).
6. **Stop.** `⏹ Stop` drops the request and the partial clip, puts back any earlier clip, and says to pick another voice and try again (`content.js:1080-1104`).
7. **Closing the panel** does not stop synthesis. The clip lands in the worker's cache, so pressing Listen again after re-opening is instant (`background.js:257-259`).

**Errors:** `speakError` (`contracts.md:771-781`), and the panel's idle watchdog (`content.js:1106-1115`). All of them show in the status line under the action row.

### Journey 4 — Ask a question

1. **Open.** `💬 Ask about this video` expands the ask panel (`aria-expanded`), focuses the text box, and the button becomes `💬 Hide questions` (`content.js:943-954`, `:1254-1264`).
2. **Ask.** Enter sends and Shift+Enter adds a new line (`content.js:302-309`). The question appears as a bubble, and the answer streams into the next one (`content.js:1303-1359`).
3. **Answering.** `Send` becomes `⏹ Stop`, and the text box is disabled until the answer is done (`content.js:1395-1403`).
4. **Citations.** `[12:34]`-style citations become links. Clicking one closes the panel and seeks the player there (`content.js:1278-1301`). As the code reads, they can't be activated from the keyboard (T-009). Anything the model adds from outside the transcript is labelled `Not in the video:` (`src/youtube_tldw/ask.py:32`, `:70`).
5. **The conversation lasts the page session.** Re-opening the panel replays it, and re-attaches an answer that kept streaming while the panel was shut (`content.js:33-35`, `:1231-1241`).
6. **Stop.** It keeps any partial answer, drops an empty bubble, and says `Stopped.` (`content.js:1366-1377`).

**Where the words go:** the browser sends only the question and the conversation so far (`background.js:365-366`). The server sends the transcript, title, channel and question to the model backend (`docs/architecture.md:208`, § 8), and the `claude` CLI keeps the Q&A session, transcript included, under `~/.claude/projects/` (`docs/data-model.md:227-237`). The model is the prompt-injection boundary (`docs/architecture.md:145`, `:157`): a transcript can steer the answer's text, which is escaped before it is shown (`content.js:1281-1293`; T-007).

**Errors:** `askError` (`contracts.md:760-769`), and the panel's ask safety timer (`content.js:1386-1393`). They show in the line under the text box.

### Journey 5 — Play key moments

1. **Start.** `⏭ Play key moments` locks the other audio controls, and the status reads `Finding key moments…` with a spinning ring (`content.js:1469-1488`).
2. **First clip.** As soon as the first clip arrives, the panel closes and the player jumps to it. More clips keep arriving behind it (`content.js:1496-1503`, `:1569-1589`).
3. **The pill.** A floating pill shows `⏭ Clip <n>/<total>`, the clip's times and a running tally of kept time, with `‹` previous, `›` next and `✕` stop skipping (`content.js:1723-1786`). If playback catches up with the clips found so far, it pauses on `⏳ Loading next clip...` (`content.js:1622-1626`, `:1765-1766`).
4. **Done.** `✓ Key moments done` for 4 s, then the pill goes (`content.js:1644-1656`).
5. **Again.** A second press in the same page session reuses the clips without asking the server (`content.js:1472-1475`).
6. **Length** comes from Options → **Key moments length** (`options.html:47-56`).

**Escape:** the pill's `✕` stops skipping (`content.js:1750`).

**Errors:** `httpError` (`contracts.md:748-758`), `No key moments found.`, `Couldn't find the YouTube player to control.`, and the key-moments timeouts (`contracts.md:811-833`). A server timeout here is worded "Summarizing timed out" (T-008).

### Journey 6 — Transcribe a video with no captions

What happens depends on Options → **Videos with no subtitles** (`options.html:57-67`). The server's own setting outranks it (`README.md:375-380`).

1. **Ask (the default).** The panel explains that the video has no subtitles, quotes a rough time for *this* video, warns that the CPU will be busy and that the first run downloads a model, and offers `Transcribe the audio` (`content.js:791-833`). An unknown length is said as such, not quoted as a time (`content.js:779-789`).
2. **Whisper not installed.** Instead of a button, the panel shows the install command, and says to restart `tldw serve` and click TL;DW again (`content.js:798-809`).
3. **Accepted.** The request is re-sent with a 40-minute budget (`background.js:142-146`). Once the server says it is transcribing, a `⏹ Stop transcribing` button appears; it reads `Stopping…` once pressed (`content.js:602-616`, `:684-702`).
4. **Done.** The summary renders as in journey 2, labelled as transcribed from audio, in the panel and in the copied Markdown (`content.js:1891-1906`).
5. **Panel closed.** An offer that arrives while the panel is shut becomes a corner notice saying TL;DW needs an answer; clicking it shows the offer (`content.js:618-631`, `:766-772`). Under "Stop and discard", closing the panel stops a running transcription too (`content.js:347-350`).
6. **Transcribe without asking** skips step 1. **Don't transcribe** gives the no-transcript error (`README.md:366-368`).

**Errors:** the transcription timeouts (`contracts.md:792-833`); the no-captions `422` (`contracts.md:754`), which names no next step (T-008).

### Journey 7 — Configure

**Open Options:** Chrome → Details → Extension options; Firefox → `about:addons` → Preferences (`docs/onboarding.md:54-56`). The page opens with the saved values filled in (`options.js:34-47`).

| Field | What the user sets | Source |
|---|---|---|
| Server URL | Where requests go. Empty means the default. Only the default is covered by the host permission (`contracts.md:913`). | `options.html:29-30` |
| Token | The token `tldw serve` prints. The field is `type="password"`, which only masks it on screen: it is stored in plain text in `chrome.storage.local` (`contracts.md:872`; T-004). | `options.html:31-32` |
| Default voice and `▶ Preview` | The voice Listen starts with. The list comes from the server's `/voices` when it answers, otherwise a built-in list (`options.js:68-75`; T-006). Preview works with the token in the field, without saving first. | `options.html:33-38`, `options.js:78-120` |
| Model | Opus (default) or Sonnet. Disabled, with a note, when the server uses a custom model command; that note names a command form that doesn't work (T-012). | `options.html:39-46`, `options.js:48-66` |
| Key moments length | Auto (default), or about 25 %, 40 % or 60 %. | `options.html:47-56` |
| Videos with no subtitles | Offer (default), transcribe without asking, or don't transcribe. | `options.html:57-67` |
| If you close the panel while it's still working | Keep working in the background (default), or stop and discard. | `options.html:68-75` |

**Save** writes all seven settings and says `Saved` for 1.5 s (`options.js:124-142`). A change applies to the next request; requests already running are unaffected (`contracts.md:874`).

**Errors:** only Preview reports any: no token, a token mismatch, a failure, or a server it can't reach (`contracts.md:783-790`, `:811-833`). Save doesn't check that the server answers or that the token is right.

### States per surface

The UX brief requires an empty, a loading and a success state on every screen (`.claude/agents/ux-designer.md`, Redlines).

| Surface | Empty | Loading | Success | Error |
|---|---|---|---|---|
| Panel, summarize | N/A: the panel only opens on a click, and starts loading at once | `content.js:673-682`, then the partial view `:877-893` | `content.js:926-1001` | `content.js:842-847` |
| Transcription offer | — | the loading view, plus `⏹ Stop transcribing` (`content.js:684-702`) | the offer (`content.js:811-832`), then the result | the install-hint view (`content.js:798-809`) |
| Listen | the idle button | ring and status (`content.js:1448-1452`, `:1793-1806`) | the player (`content.js:1855-1884`) | `content.js:1886-1889` |
| Ask | the collapsed toggle, then an empty log (`content.js:204-205`) | the pending bubble with a cursor (`content.js:214`, `:1315-1317`) | the answer bubble | the ask status line (`content.js:1405-1410`) |
| Key moments | — | `Finding key moments…` and the ring | the pill, then `✓ Key moments done` | the status line under the action row |
| Options | the placeholders in Server URL and Token (`options.html:30`, `:32`) | Preview's `…` (`options.js:94-95`) | `Saved` (`options.js:140`) | `#previewStatus` (`options.js:89-116`) |

## Design system

`N/A — there is no design system or component library.` The panel is plain CSS written inline into an **open** shadow root (`content.js:136-247`; the open mode is a recorded fact, T-007). `:host { all: initial; }` keeps YouTube's styles out (`content.js:139`).

- **Dark mode:** the panel follows `prefers-color-scheme` (`content.js:146-151`, `:217`). The corner notice and the key-moments pill are always dark, in inline styles (`content.js:759-763`, `:1727-1731`). The Options page has no dark mode (`options.html:6-24`).
- **Type and colour:** `system-ui`. The red `#c00` is the accent for primary buttons, progress and citations, on the panel and the Options page alike (`content.js:167`, `:175`, `:215`; `options.html:13`).
- **Icons** are Unicode and emoji glyphs (✕ ↻ ⏭ 🔊 ▶ ⏹ ⬇ 💬 ‹ ›). Their accessible names are listed in `contracts.md:1058-1082`.

## Accessibility

**The floor: WCAG 2.2, level AA** (`.claude/agents/ux-designer.md`; `tasks/T-001.7.md`). This is the **stated target**. No one has verified that tldw meets it.

### How it is enforced today

- **There is no automated WCAG audit.** No contrast check, no axe or similar scanner, and no screen-reader test. There is no browser automation at all (`docs/testing.md:14`, `:87-88`).
- **A few keyboard and interaction invariants are guarded** by `tests/js/render_checks.js`, run in node by `tests/test_extension_render.py:21-28`:
  - the key shield's "is this from our modal?" test, and its fallback (`render_checks.js:84-99`);
  - the shield sits on `window` in the capture phase, is removed on close, and the chat box has no `keydown` handler of its own, so Enter keeps working (`render_checks.js:101-114`);
  - the `⏹ Stop transcribing` button's `aria-label` (`render_checks.js:401-402`);
  - closing keeps work running unless the user chose otherwise (`render_checks.js:117-130`);
  - the in-progress controls are disabled, and both views share one layout (`render_checks.js:264-290`);
  - the sponsor ask can't turn into a nag (`render_checks.js:245-262`).
- **CI can't silently skip them.** `tests/test_extension_render.py` skips when node is missing, but the CI script requires node, and fails the run on any skip (`scripts/ci-tldw-tests.sh:9-12`, `:76`).

### The baseline and its known gaps

The keyboard and screen-reader baseline (keys, dialog semantics and focus, live regions, accessible names) is in `contracts.md:1012-1082`. Its known gaps are in `contracts.md:1084-1094` and tracked in [T-009](../tasks/T-009.md), which is deferred. **They were found by reading the code, not in a browser.**

Found while drafting this doc, also by reading the code, and appended to T-009:

- **The Whisper install command is near-invisible in light mode.** `.installhint` sets text `#e9e9ea` on an 18 % grey wash, which over the light panel's white works out at about `#e8e8e8`: roughly 1:1 (`content.js:182-185`, `:143`). In dark mode it is about 10.8:1. The comment above it says it is "the one thing on that screen to act on" (`content.js:180-181`).
- **The corner notice can't be used from the keyboard.** It is a `div` with an `onclick`, with no `tabindex` or button role (`content.js:753-777`). An error notice goes after 12 s and a ready notice after 20 s (`content.js:776`). Clicking the toolbar action again reaches a finished summary (`content.js:1911`).
- **The Options page's `<html>` has no `lang`** (`options.html:2`).
- **Nothing reacts to `prefers-reduced-motion`** (no match in `extension/`), and the spinners, ring and creeping bar animate (`content.js:168-171`, `:239-246`, `:725-734`).

### Contrast

**Contrast: `UNKNOWN — needs human confirmation`.** It has never been measured in a browser.

Arithmetic on the declared hex values alone (WCAG relative luminance, ignoring opacity and anti-aliasing) puts these normal-size text pairs under 4.5:1. This is not a measurement, and it is appended to T-009:

- `#888` on `#fff` (light `.hint`, `.pct`, `.audiostatus`, `.askstatus`, `.writing`, `.txsrc`): 3.5:1 (`content.js:143`, `:177`, `:186-187`, `:195`, `:223`, `:226`);
- `#777` on `#fff` (light `.rationale`): 4.48:1 (`content.js:159`);
- `#c0392b` on `#1e1f24` (the error colour, which has no dark-mode override): 3.0:1 (`content.js:147`, `:178`, `:196`, `:234`);
- `#0a0` on `#fff` (Options `Saved`): 3.1:1 (`options.html:15`).

Colour is not the only carrier of the error state: every error is also a sentence.

### Target size and mobile

- **WCAG 2.5.8 target size: `UNKNOWN — needs human confirmation`**, never measured. The pill's buttons are the smallest declared: `all:unset`, `padding: 0 4px`, 18 px glyphs (`content.js:1737-1738`).
- **The 44×44 px mobile redline: `N/A — the extension has no mobile layout`.** It is loaded unpacked into desktop browsers (`README.md:428-430`, `docs/onboarding.md:49-52`). The panel's only responsive rule is `width: min(680px, 92vw)` (`content.js:143`).

## Internationalization

- **Locales at launch: English only.** Every string is hard-coded in the extension's JS and HTML. There is no `_locales/` directory, no `default_locale` in either manifest, and no call to `i18n.getMessage` (`extension/`). Times and durations are formatted by hand in English abbreviations (`content.js:1691-1707`).
- **Transcript language.**
  - The extension never sends `lang` (`contracts.md:109`). The server defaults it to `en` (`src/youtube_tldw/server.py:1163-1165`, `:1758-1760`), and that is what it asks `yt-dlp` for.
  - The CLI takes `--lang`, default `en` (`src/youtube_tldw/cli.py:63`).
  - A transcript read from the page is in whatever language YouTube's transcript panel shows. The page payload carries no language (`content.js:515-544`; `contracts.md:643-655`). The row reader drops YouTube's English-only screen-reader copy of each timestamp by class name rather than by its text (`content.js:424-428`).
  - No cache key includes the language (`contracts.md:1488`; T-014).
- **Output language:** the summary and Q&A prompts give no output-language instruction (no "language" in `src/youtube_tldw/summarize.py` or `ask.py`), so it is the model's choice.
- **Locale routing: `N/A — there is one locale`**, so there is nothing to route between.
- **Fallback policy: `N/A — there is one locale`**, so there is nothing to fall back to.
- **The "no string outside the i18n system once multilingual" redline: `N/A — the product is not multilingual`.**

## Microcopy voice

- **Every error names a next step.** That is the invariant in `contracts.md:849-862`: retry, re-check the token, run `tldw serve`, use the CLI, or install something. The exceptions are recorded there, and tracked in [T-008](../tasks/T-008.md) (deferred).
- **Plain second person, with the command to run.** `tldw serve`, the install command, the CLI. Backticks become code only in the panel's main error view; everywhere else they show literally (`contracts.md:835-840`; T-008).
- **Honest about time.** An estimate is given only when the length is known (`content.js:779-789`), and the progress bar doesn't front-load (`content.js:727-728`).
- **Short confirmations:** `Copied`, `Saved`, `Stopped.` (`content.js:969`, `options.js:140`, `content.js:1376`).
- **Asks without nagging:** the sponsor box needs real use behind it, dismisses for good, and retires itself (`content.js:1186-1191`).
- **"Runs entirely on your own machine."** The Options footer says it (`options.html:79-80`), and so does `README.md:205`. tldw does run locally, but with the default backend the transcript and questions go to Anthropic (`docs/architecture.md:204-219`). Read as a privacy claim, the phrase overstates it. That's appended to T-012; this doc makes no "fully private" claim.

## How this was verified

- **Journeys.** Read `extension/content.js`, `background.js`, `options.js` and `options.html` in full, and both manifests, on branch `chore/docs-tier2` on 2026-11-12. Each stage was traced from the handler that starts it to the view it renders. Every `path:line` above was read at that line.
- **Messages and accessibility.** Cross-checked against `docs/contracts.md:736-862` and `:1012-1094`. This doc names those messages and facts and links to them, and contradicts none of them.
- **Automated guards.** Listed by reading the `check(` names and section markers in `tests/js/render_checks.js`, how `tests/test_extension_render.py` runs them, and `scripts/ci-tldw-tests.sh`.
- **No WCAG tool and no browser automation.** Taken from `docs/testing.md`. A grep of `extension/` for `prefers-reduced-motion`, `lang=`, `_locales` and `default_locale` found none.
- **Contrast figures.** Computed with the WCAG 2.x relative-luminance formula from the hex values declared in the CSS. Nothing was rendered.
- **i18n.** Read `server.py:1152-1166` and `:1758-1760` and `cli.py:63`, and grepped `summarize.py` and `ask.py` for an output-language instruction.
- **The "must not contradict" rules** of the Tier 2 plan: § 8 on what leaves the machine (journey 4 and Microcopy voice); the plain-text token (journey 7); Q&A sessions under `~/.claude/projects/` (journey 4); and the model as the prompt-injection boundary (journey 4).

## Update this doc when

- `extension/content.js` changes a view, a control, its label or `aria-*`, a key handler, the close policy, the corner notice, the key-moments pill, or the panel's CSS;
- `extension/background.js` changes what a toolbar click does, or a timeout or error mapping a journey names;
- `extension/options.html` or `options.js` adds, removes or renames a setting, or changes Save or Preview;
- either manifest changes `name`, `action`, `commands`, `default_locale` or the options page;
- `tests/js/render_checks.js` adds or drops a keyboard or accessibility check, or `scripts/ci-tldw-tests.sh` stops requiring node;
- a WCAG, contrast or browser-automation tool is added;
- the extension starts sending `lang`, a second locale is added, or the prompts gain an output-language instruction;
- `contracts.md`'s user-facing message or accessibility sections change, or T-008 or T-009 is worked;
- a new kind of user, or a second known user, appears.
