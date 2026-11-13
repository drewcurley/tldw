# UX — tldw

Who uses tldw, the journeys they take through the browser extension, and the user-facing floor: accessibility, design system, internationalization and microcopy. This doc is **descriptive**. It records what `extension/content.js`, `extension/background.js`, `extension/options.js` and `extension/options.html` do today, and sets no new direction.

It cites the Tier 1 docs rather than restating them:

- every message the user can read, and the "names a next step" invariant, are in `contracts.md` → [User-facing message contracts](contracts.md#user-facing-message-contracts) (`contracts.md:736-862`);
- every key, dialog attribute, live region and accessible name is in `contracts.md` → [Keyboard and accessibility baseline](contracts.md#keyboard-and-accessibility-baseline) (`contracts.md:1012-1094`);
- every `chrome.storage.local` setting and its defaults are in `contracts.md` → chrome.storage.local keys (`contracts.md:864-888`);
- TTLs and caps belong to `data-model.md`.

**On this fork the extension's manifest `name` is `TL;DW — Hackathon`** (`extension/manifest.json:3`, `extension/manifest.firefox.json:3`; T-019). That name shows only in the browser's own extension lists. In Chrome the two install side by side; in Firefox the hackathon build replaces the normal one, because both use the same `gecko.id` (`manifest.firefox.json`; T-019). The toolbar tooltip stays `TL;DW this video` (`manifest.json:10`), and the panel's heading stays `TL;DW` (`content.js:272`). The Options page's title is `TL;DW options` and its heading `TL;DW settings` (`options.html:5`, `:27`).

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

Messages below are named, not quoted. Their exact text is in `contracts.md`'s message tables. Every screen's way out is the same unless a journey says otherwise: **Escape**, the **✕** button, or a click on the backdrop closes the panel (`content.js:284-287`, `:321`), under the "close action" setting (journey 7).

### Journey 1 — Summarize a video

1. **Trigger.** On a YouTube `/watch` page, the user clicks the toolbar action (tooltip `TL;DW this video`, `manifest.json:9-12`). **There is no keyboard shortcut**: the manifest declares no `commands` (`contracts.md:937`). The click injects `content.js` and sends it `TLDW_INVOKE` (`background.js:58-68`).
2. **Wrong page.** On any other page the extension can inject into, the panel opens with the "not a video page" message (`background.js:62-65`). On a page it can't inject into (a `chrome://` page or the Web Store), nothing appears: `executeScript` runs before the video check, with no error handling (`background.js:58-68`).
3. **No token.** With no token saved, the first request answers with the "no server token set" message, before the page is read (`background.js:173-178`).
4. **Cache check.** The panel asks whether a summary already exists, from the worker's cache and then the server's (`content.js:671-677`, `background.js:166-201`). A hit renders at once as a cached result (journey 2). Any failure counts as a miss (`content.js:577-584`).
5. **Reading the page.** On a miss, the panel opens YouTube's own transcript panel, reads it and closes it again (`content.js:536-575`). Then it asks for the summary (`content.js:601-608`).
6. **Loading.** A spinner, a status line starting at `Starting…`, a bar and a percentage (`content.js:694-703`). The server's progress messages replace the status line. During the model step the bar creeps about 1 % a second and never parks at 99 % (`content.js:21`, `:746-755`).
7. **Writing.** The title, channel and length arrive first, then each key point and paragraph as it is written. The header says `writing…`, and the action buttons are shown disabled, titled "Available once the summary is finished" (`content.js:873-874`, `:903-919`).
8. **Done.** The finished summary replaces the partial one in the same layout, so nothing below it moves (`content.js:870-872`, `:952-980`; journey 2).
9. **Closing mid-run.** Under the default setting the work keeps going. A notice in the corner says the summary is ready, and clicking it opens the summary. Clicking the toolbar action again re-attaches to the run in flight, restoring what it had written or its progress (`content.js:343-362`, `:657`, `:774-798`, `:3208-3229`; `README.md:177-184`).

**Errors:** a status from the server is turned into text by `httpError` (`contracts.md:748-758`). The worker's and panel's own timeouts and lost connections are in `contracts.md:792-833`. All of them show in the panel's main error view (`content.js:863-868`), or as the corner notice if the panel was closed.

### Journey 2 — Read the result (cached, and regenerate)

1. **Header.** `TL;DW`, or `TL;DW (cached)` when the result was not freshly generated for this click (`content.js:956`; what "cached" covers is in `contracts.md:847`).
2. **Meta line.** Channel · original length → `~<length> read` · an `original` link. Once key moments are known, a trimmed-video note is added. A summary written from a Whisper transcription is labelled `transcribed from audio by Whisper (<model>)` (`content.js:960-962`, `:2954-2977`, `:3187-3194`).
3. **Body.** The title, the action row (journeys 3 and 5), Key points, Summary (headings, bullets, bold and italic, all escaped first, `content.js:112-139`), and the model's rationale in small italics when there is one (`content.js:963-967`).
4. **Copy** puts the summary on the clipboard as Markdown and says `Copied` for 1.5 s (`content.js:991-997`, `:3196-3202`).
5. **↻ (regenerate)** asks for a new summary and skips the cache, but keeps the cached transcript (`content.js:274`, `:981-990`; guarded by `tests/js/render_checks.js:330-346`). It does nothing while other work is running (`content.js:985`).
6. **Re-opening.** Clicking the toolbar action again on the same video in the same page session shows the last result at once, as cached (`content.js:3206-3207`). The worker's summary map is keyed by video id only, so after a model or length change ↻ is the way to get a new summary (`docs/data-model.md:389`).
7. **The sponsor ask.** After 25 summarized videos, a box offers a GitHub Sponsors link and `No thanks — don't ask again`. It is shown at most three times, and a dismissal is permanent (`content.js:1213-1247`; guarded by `render_checks.js:245-262`).

### Journey 3 — Listen to the summary

1. **Pick a voice.** The panel's voice picker preselects the Options default (`content.js:1030-1038`). A voice picked here is **not saved** (`contracts.md:880`). The panel's list is hard-coded, while Options asks the server (`content.js:91-106`; T-018).
2. **Preview (optional).** `▶ Preview` plays a short sample. The button shows `…` while it loads (`content.js:1441-1466`).
3. **Listen.** `🔊 Listen to summary` becomes `⏹ Stop`, and the voice picker and Preview are locked. A progress ring and status line track synthesis (`content.js:1062-1105`, `:1475-1479`).
4. **Playback starts early.** Where the browser supports streaming mp3 (`MediaSource`), playback starts on the first block of audio. Firefox has no `audio/mpeg` there, so it plays the finished clip instead (`content.js:29-32`, `:1144-1211`).
5. **Done.** The player (`aria-label="Spoken summary"`) takes focus. A `⬇` button saves the finished mp3 as `<title> - <channel> - tldw version.mp3`, and the Listen button reads `🔊 Regenerate` (`content.js:3109-3126`, `:1048`).
6. **Stop.** `⏹ Stop` drops the request and the partial clip, puts back any earlier clip, and says to pick another voice and try again (`content.js:1107-1131`).
7. **Closing the panel** does not stop synthesis. The clip lands in the worker's cache, so pressing Listen again after re-opening is instant (`background.js:331-333`).

**Errors:** `speakError` (`contracts.md:771-781`), and the panel's idle watchdog (`content.js:1133-1142`). All of them show in the status line under the action row.

### Journey 4 — Ask a question

1. **Open.** `💬 Ask about this video` expands the ask panel (`aria-expanded`), focuses the text box, and the button becomes `💬 Hide questions` (`content.js:969-980`, `:1281-1291`).
2. **Ask.** Enter sends and Shift+Enter adds a new line (`content.js:323-330`). The question appears as a bubble, and the answer streams into the next one (`content.js:1330-1386`).
3. **Answering.** `Send` becomes `⏹ Stop`, and the text box is disabled until the answer is done (`content.js:1422-1430`).
4. **Citations.** `[12:34]`-style citations become links. Clicking one closes the panel and seeks the player there (`content.js:1305-1328`). As the code reads, they can't be activated from the keyboard (T-009). Anything the model adds from outside the transcript is labelled `Not in the video:` (`src/youtube_tldw/ask.py:32`, `:70`).
5. **The conversation lasts the page session.** Re-opening the panel replays it, and re-attaches an answer that kept streaming while the panel was shut (`content.js:33-35`, `:1258-1268`).
6. **Stop.** It keeps any partial answer, drops an empty bubble, and says `Stopped.` (`content.js:1393-1404`).

**Where the words go:** the browser sends only the question and the conversation so far (`background.js:439-440`). The server sends the transcript, title, channel and question to the model backend (`docs/architecture.md:208`, § 8), and the `claude` CLI keeps the Q&A session, transcript included, under `~/.claude/projects/` (`docs/data-model.md:227-237`). The model is the prompt-injection boundary (`docs/architecture.md:145`, `:157`): a transcript can steer the answer's text, which is escaped before it is shown (`content.js:1308-1320`; T-007).

**Errors:** `askError` (`contracts.md:760-769`), and the panel's ask safety timer (`content.js:1413-1420`). They show in the line under the text box.

### Journey 5 — Play key moments

1. **Start.** `⏭ Play key moments` locks the other audio controls, and the status reads `Finding key moments…` with a spinning ring (`content.js:2765-2784`).
2. **First clip.** As soon as the first clip arrives, the panel closes and the player jumps to it. More clips keep arriving behind it (`content.js:2792-2799`, `:2865-2885`).
3. **The pill.** A floating pill shows `⏭ Clip <n>/<total>`, the clip's times and a running tally of kept time, with `‹` previous, `›` next and `✕` stop skipping (`content.js:3019-3082`). If playback catches up with the clips found so far, it pauses on `⏳ Loading next clip...` (`content.js:2918-2922`, `:3061-3062`).
4. **Done.** `✓ Key moments done` for 4 s, then the pill goes (`content.js:2940-2952`).
5. **Again.** A second press in the same page session reuses the clips without asking the server (`content.js:2768-2771`).
6. **Length** comes from Options → **Key moments length** (`options.html:47-56`).

**Escape:** the pill's `✕` stops skipping (`content.js:3046`).

**Errors:** `httpError` (`contracts.md:748-758`), `No key moments found.`, `Couldn't find the YouTube player to control.`, and the key-moments timeouts (`contracts.md:811-833`). A server timeout here is worded "Summarizing timed out" (T-008).

### Journey 6 — Transcribe a video with no captions

What happens depends on Options → **Videos with no subtitles** (`options.html:57-67`). The server's own setting outranks it (`README.md:375-380`).

1. **Ask (the default).** The panel explains that the video has no subtitles, quotes a rough time for *this* video, warns that the CPU will be busy and that the first run downloads a model, and offers `Transcribe the audio` (`content.js:812-854`). An unknown length is said as such, not quoted as a time (`content.js:800-810`).
2. **Whisper not installed.** Instead of a button, the panel shows the install command, and says to restart `tldw serve` and click TL;DW again (`content.js:819-830`).
3. **Accepted.** The request is re-sent with a 40-minute budget (`background.js:216-220`). Once the server says it is transcribing, a `⏹ Stop transcribing` button appears; it reads `Stopping…` once pressed (`content.js:623-637`, `:705-723`).
4. **Done.** The summary renders as in journey 2, labelled as transcribed from audio, in the panel and in the copied Markdown (`content.js:3187-3202`).
5. **Panel closed.** An offer that arrives while the panel is shut becomes a corner notice saying TL;DW needs an answer; clicking it shows the offer (`content.js:639-652`, `:787-793`). Under "Stop and discard", closing the panel stops a running transcription too (`content.js:368-371`).
6. **Transcribe without asking** skips step 1. **Don't transcribe** gives the no-transcript error (`README.md:366-368`).

**Errors:** the transcription timeouts (`contracts.md:792-833`); the no-captions `422` (`contracts.md:754`), which names no next step (T-008).

### Journey 8 — Cover listed words (T-020)

**What it's for:** watching a video with listed words covered by a bleep (the default), silence, or a moo, timed to the word where Whisper can.

1. **Turn it on.** In the result view, press `Cover listed words`, beside Listen and Play key moments. The button shows `✓` and `aria-pressed="true"`. While the captions are read (up to about 13 s, the existing panel-reading bounds of 8 s for the first row and 5 s to settle), it announces `Reading the captions…` and is `aria-disabled`, keeping focus; pressing it again stops.
2. **Covered at once, by caption line.** Every caption line that holds a listed word, or YouTube's `[ __ ]` marker, is covered right away: `Bleeping 2 spots from the captions (about 9 seconds of audio). Timing them to the word…`. Nothing waits on Whisper.
3. **Timed to the word.** The server times the words in short windows around each hit. The visible line counts them (`Timed to the word: 1 of 2.`) without announcing each one. A range the playhead is inside is never shrunk mid-word: the narrower range applies once the playhead leaves it.
4. **Done.** The announcement says how many were word-timed, and that the rest stay by caption line.
5. **Watch with the panel closed.** It keeps working. A pill on the player (`🔔 Bleeping listed words`, `· now` while a word is covered) has a `Stop` button; with the panel closed, announcements come from the pill.
6. **Stop.** On the pill (focus moves to the player, `Bleeping is off.` is announced, and the pill goes a second later) or with the panel toggle. Moving to another video also stops it. Any mute the feature set is undone.

**The user's own mute and volume win.** Clicking YouTube's mute button or pressing `m` hands control back: the feature never unmutes what the user muted, and stops its sound. A sound plays at most 0.15 (bleep) or 0.25 (moo) of the video's own volume, and none at volume 0.

**Options:** "Words to cover (for 'Cover listed words')" (empty turns it off; "Restore default list" refills the box and saving keeps it), and "Sound for listed words" (Bleep, Mute, Moo). The "Videos with no subtitles" hint says it also controls word timing: `Don't transcribe` keeps every word covered by caption line. Edits apply right away, even while it's on: a new sound starts at the next covered spot (the spot playing now stays silent), and a new list is re-scanned at once. Emptying the list, or a list that matches nothing, turns it off. Options' Save says so (`Saved. Videos where Cover listed words is on use the new settings right away.`). Keep the word list off a shared screen.

**Honest limits** (said in this order when demoing):
- It finds only words that are **in the captions**, in the transcript's language track. It is not a guarantee and not a parental control.
- Until a hit is word-timed, it covers its whole caption line, up to about 8 s.
- Word timing needs Whisper, ffmpeg and a downloaded model on the server, and takes roughly 10–45 s on a video it hasn't seen (unmeasured estimates); a seen video is instant. Word-timestamp precision is about 100–300 ms by faster-whisper's usual figures, **not measured here** (`UNKNOWN`, the real-browser check).
- The server downloads the video's audio once for this.
- The sound plays for at most the first 1.5 s of a covered span; after that the video stays silent until the span ends. A moo may run about 250 ms past the unmute.
- YouTube's volume icon shows muted during a covered span, and clicking it hands control back. Whether YouTube's own mute state and saved volume follow the feature's mute is `UNKNOWN`, to be settled by the real-browser check.
- Ads pause it (`An ad is playing, so covering is paused. It picks up again when the video comes back.`); live streams aren't supported; videos over the Whisper limit stay by caption line.
- Each tab is on its own, and it stops when the video changes.
- At 1.25× or faster, a word shorter than about 250 ms of real time could be missed if the browser's timer is late; the `timeupdate` backstop bounds the error.
- Keyboard path: click TL;DW again to reopen the panel, then the toggle.

**Errors:** every failure keeps the words covered by caption line and names a next step; the full list is in `contracts.md` → "Word timing (`/align/stream`, T-020)".

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

The fields "Words to cover" and "Sound for listed words" are in Journey 8.

**Save** writes all seven settings, plus the sound, and writes or removes the word list (T-020), and says `Saved` for 1.5 s (`options.js:124-142`). A change applies to the next request; requests already running are unaffected (`contracts.md:874`).

**Errors:** only Preview reports any: no token, a token mismatch, a failure, or a server it can't reach (`contracts.md:783-790`, `:811-833`). Save doesn't check that the server answers or that the token is right.

### States per surface

The UX brief requires an empty, a loading and a success state on every screen (`.claude/agents/ux-designer.md`, Redlines).

| Surface | Empty | Loading | Success | Error |
|---|---|---|---|---|
| Panel, summarize | N/A: the panel only opens on a click, and starts loading at once | `content.js:694-703`, then the partial view `:903-919` | `content.js:952-1028` | `content.js:863-868` |
| Transcription offer | — | the loading view, plus `⏹ Stop transcribing` (`content.js:705-723`) | the offer (`content.js:832-853`), then the result | the install-hint view (`content.js:819-830`) |
| Listen | the idle button | ring and status (`content.js:1475-1479`, `:3089-3107`) | the player (`content.js:3151-3180`) | `content.js:3182-3185` |
| Ask | the collapsed toggle, then an empty log (`content.js:218-219`) | the pending bubble with a cursor (`content.js:228`, `:1342-1344`) | the answer bubble | the ask status line (`content.js:1432-1437`) |
| Key moments | — | `Finding key moments…` and the ring | the pill, then `✓ Key moments done` | the status line under the action row |
| Cover listed words | `No words are listed…` (empty list) or `None of this video's caption lines contain your listed words…` | `Reading the captions…`, then `Timed to the word: K of N.` | `Bleeping N spots, all timed to the word…`, and the pill | the failure statuses, each keeping caption-line covering (`contracts.md`) |
| Options | the placeholders in Server URL and Token (`options.html:30`, `:32`) | Preview's `…` (`options.js:94-95`) | `Saved` (`options.js:140`) | `#previewStatus` (`options.js:89-116`) |

## Design system

`N/A — there is no design system or component library.` The panel is plain CSS written inline into an **open** shadow root (`content.js:150-268`; the open mode is a recorded fact, T-007). `:host { all: initial; }` keeps YouTube's styles out (`content.js:153`).

- **Dark mode:** the panel follows `prefers-color-scheme` (`content.js:160-165`, `:231`). The corner notice and the key-moments pill are always dark, in inline styles (`content.js:780-784`, `:3023-3027`). The Options page has no dark mode (`options.html:6-24`).
- **Type and colour:** `system-ui`. The red `#c00` is the accent for primary buttons, progress and citations, on the panel and the Options page alike (`content.js:181`, `:189`, `:229`; `options.html:13`).
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

- **The Whisper install command is near-invisible in light mode.** `.installhint` sets text `#e9e9ea` on an 18 % grey wash, which over the light panel's white works out at about `#e8e8e8`: roughly 1:1 (`content.js:196-199`, `:157`). In dark mode it is about 10.8:1. The comment above it says it is "the one thing on that screen to act on" (`content.js:194-195`).
- **The corner notice can't be used from the keyboard.** It is a `div` with an `onclick`, with no `tabindex` or button role (`content.js:774-798`). An error notice goes after 12 s and a ready notice after 20 s (`content.js:797`). Clicking the toolbar action again reaches a finished summary (`content.js:3207`).
- **The Options page's `<html>` has no `lang`** (`options.html:2`).
- **Nothing reacts to `prefers-reduced-motion`** (no match in `extension/`), and the spinners, ring and creeping bar animate (`content.js:182-185`, `:253-260`, `:746-755`).

### Contrast

**Contrast: `UNKNOWN — needs human confirmation`.** It has never been measured in a browser.

Arithmetic on the declared hex values alone (WCAG relative luminance, ignoring opacity and anti-aliasing) puts these normal-size text pairs under 4.5:1. This is not a measurement, and it is appended to T-009:

- `.coverstatus` ("Cover listed words", T-020) is `#767676` on `#fff` in light mode, about 4.5:1, and `#888` in dark mode, about 4.6:1 on `#1e1f24`; it was set so a new flow doesn't ship below AA.
- `#888` on `#fff` (light `.hint`, `.pct`, `.audiostatus`, `.askstatus`, `.writing`, `.txsrc`): 3.5:1 (`content.js:157`, `:191`, `:200-201`, `:209`, `:237`, `:240`);
- `#777` on `#fff` (light `.rationale`): 4.48:1 (`content.js:173`);
- `#c0392b` on `#1e1f24` (the error colour, which has no dark-mode override): 3.0:1 (`content.js:161`, `:192`, `:210`, `:248`);
- `#0a0` on `#fff` (Options `Saved`): 3.1:1 (`options.html:15`).

Colour is not the only carrier of the error state: every error is also a sentence.

### Target size and mobile

- **WCAG 2.5.8 target size: `UNKNOWN — needs human confirmation`**, never measured. The pill's buttons are the smallest declared: `all:unset`, `padding: 0 4px`, 18 px glyphs (`content.js:3033-3034`).
- **The 44×44 px mobile redline: `N/A — the extension has no mobile layout`.** It is loaded unpacked into desktop browsers (`README.md:428-430`, `docs/onboarding.md:49-52`). The panel's only responsive rule is `width: min(680px, 92vw)` (`content.js:157`).

## Internationalization

- **Locales at launch: English only.** Every string is hard-coded in the extension's JS and HTML. There is no `_locales/` directory, no `default_locale` in either manifest, and no call to `i18n.getMessage` (`extension/`). Times and durations are formatted by hand in English abbreviations (`content.js:2987-3003`).
- **Transcript language.**
  - The extension never sends `lang` (`contracts.md:109`). The server defaults it to `en` (`src/youtube_tldw/server.py:1163-1165`, `:1758-1760`), and that is what it asks `yt-dlp` for.
  - The CLI takes `--lang`, default `en` (`src/youtube_tldw/cli.py:63`).
  - A transcript read from the page is in whatever language YouTube's transcript panel shows. The page payload carries no language (`content.js:536-565`; `contracts.md:643-655`). The row reader drops YouTube's English-only screen-reader copy of each timestamp by class name rather than by its text (`content.js:445-449`).
  - No cache key includes the language (`contracts.md:1488`; T-014).
- **Output language:** the summary and Q&A prompts give no output-language instruction (no "language" in `src/youtube_tldw/summarize.py` or `ask.py`), so it is the model's choice.
- **Locale routing: `N/A — there is one locale`**, so there is nothing to route between.
- **Fallback policy: `N/A — there is one locale`**, so there is nothing to fall back to.
- **The "no string outside the i18n system once multilingual" redline: `N/A — the product is not multilingual`.**

## Microcopy voice

- **Every error names a next step.** That is the invariant in `contracts.md:849-862`: retry, re-check the token, run `tldw serve`, use the CLI, or install something. The exceptions are recorded there, and tracked in [T-008](../tasks/T-008.md) (deferred).
- **Plain second person, with the command to run.** `tldw serve`, the install command, the CLI. Backticks become code only in the panel's main error view; everywhere else they show literally (`contracts.md:835-840`; T-008).
- **Honest about time.** An estimate is given only when the length is known (`content.js:800-810`), and the progress bar doesn't front-load (`content.js:748-749`).
- **Short confirmations:** `Copied`, `Saved`, `Stopped.` (`content.js:995`, `options.js:140`, `content.js:1403`).
- **Asks without nagging:** the sponsor box needs real use behind it, dismisses for good, and retires itself (`content.js:1213-1218`).
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
- "Cover listed words" changes a status, the toggle, the pill, its Options fields, or a limit Journey 8 names; or the real-browser check records its results (T-020).
