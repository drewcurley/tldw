# TL;DW browser extension

An MV3 extension (Chromium **and** Firefox) that summarizes the YouTube video you're
watching by calling your **local `tldw` server** — summary in an on-page modal, plus
Listen (streaming TTS, with an mp3 download), Play-key-moments, and follow-up
questions about the video.

> **After pulling changes, reload the extension** (`chrome://extensions` → reload)
> *and* every open YouTube tab, **then restart `tldw serve`**. Chrome keeps the old
> content script otherwise, so you'll be clicking a stale build against a new server
> — which looks like a bug in whatever you just changed. In that order because a
> server newer than the extension stops trusting what the old one reads off the page
> (it fetches from YouTube itself instead, and the panel says the extension is out
> of date).

## Setup

1. **Start the server** (in a terminal):
   ```bash
   tldw serve
   ```
   It prints a token (saved + reused across restarts). Leave it running.

2. **Load the extension** (unpacked):
   - **Chrome / Edge / Brave / Arc / Opera / Vivaldi:** open `chrome://extensions`
     (or `edge://extensions`) → enable **Developer mode** → **Load unpacked** →
     select this `extension/` folder.
   - **Firefox:** build the Firefox-flavored copy (event-page manifest), then load it:
     ```bash
     ./build-firefox.sh        # creates extension/dist-firefox/
     ```
     `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on** → select
     `dist-firefox/manifest.json`. Then in `about:addons` → the extension →
     **Permissions**, allow access to **127.0.0.1** if prompted (Firefox treats host
     access as opt-in). Temporary add-ons unload when Firefox restarts — re-load it,
     or sign it for a permanent install.

3. **Configure** → open the extension's **options** (Chrome: Details → Extension
   options; Firefox: about:addons → Preferences), paste the **token** from step 1.
   (Server URL defaults to `http://127.0.0.1:8765`.)

## Use

- Open any YouTube `/watch` page → click the **TL;DW** toolbar button.
- A modal shows the title, key points, and summary. They stream in as they're
  written: title in ~4s, first key point in ~8s, the rest filling in behind it.
- **Options → Key moments length** sets how hard **Play key moments** trims: *Auto*
  (default) lets the model judge each video — a dense talk stays long, a padded one
  gets cut hard — while *Tight* (~25%), *Balanced* (~40%) and *Generous* (~60%) are
  both a target and a hard cap on total clip time. It doesn't affect the written
  summary.
- **Options → Model** picks *Thorough (Opus)*, the default, or *Fast (Sonnet)*, which
  finishes in roughly half the time with shorter summaries. It applies to summaries,
  key moments and questions.
- **Copy** grabs the markdown; **Esc** or click-outside closes.
- **🔊 Listen to summary** synthesizes the summary to speech (local Piper TTS) and
  plays it in the modal. Playback starts on the first second of audio and the rest
  streams in behind it, so a long summary doesn't sit silent while it renders. Pick a
  voice (US/UK, male/female) right there — **▶ Preview** auditions one — or set a
  default in options. **⏹ Stop** abandons a render and frees the voice picker. Once a
  clip is complete, **⬇** saves it as `{video title} - {channel} - tldw version.mp3`.
  First use of a voice downloads its model (~once).
- **⏭ Play key moments** finds the key segments (transcript + Claude — no download,
  no recut) and **auto-skips the YouTube player through just those moments**, in full
  quality in your own player. A floating pill shows progress, including the total
  playtime of the clips against the original (e.g. "Clip 2/5 · 0:30–0:34 · 12m of
  49m (25%)"), with a ✕ to stop. Once the clips are known, the summary header shows
  the trimmed length too.
- **💬 Ask about this video** opens a chat under the summary. Questions are answered
  from the transcript the server already has cached, so the first answer starts in a
  few seconds. Answers cite moments as `[12:34]` — click one to jump the player
  there. Anything the model adds from outside the video is labelled "Not in the
  video:". **⏹ Stop** abandons an answer mid-stream.

### Closing the panel mid-run

Closing the panel doesn't cancel what's running. Summaries, key moments, speech and
answers all keep going in the background — useful when you close the modal just to
pause or scrub the video. A summary that finishes while the panel is closed shows a
small notice you can click to read it, and re-opening mid-run re-attaches to the work
already in flight rather than starting it over. Options has an **"If you close the
panel while it's still working"** setting if you'd rather it stopped and discarded.

### Automatic summaries for channels you choose

Off by default. **Options → Automatic summaries → "Summarize videos from chosen
channels automatically"** turns it on; Chrome then asks to let TL;DW read
youtube.com pages (the extension can't see YouTube until you allow it, and unticking
the box gives that access back). After that, every summary's panel offers
**⚡ Always summarize {channel}**. When you open a video from a channel on your list,
TL;DW summarizes it in the background with the panel closed and shows a notice when
it's ready — click it, or the toolbar button, to read it. If you miss the notice, the
toolbar button shows a **✓** for that tab (its tooltip says the summary is ready)
until you open the summary, move to another video, or reload or close the tab.
(Leaving YouTube in that tab clears it too, as best it can: if the page unloads before
the message gets through, it stays until the tab closes.)

- It only runs for the tab you're looking at, after about 3 seconds on the video,
  never on top of a summary already running, and at most once per video per page.
  Already-summarized videos come from the cache for free.
- The worker caps automatic runs at **10 an hour and 40 a day** (your clicks are
  never counted). Past that you get one notice and can still click TL;DW. The log
  it counts from is `autoRunLog` in the extension's storage (service worker
  DevTools → Application → Storage).
- Each one uses your Claude plan like a click does — a summary, plus the key-moment
  selection the server prepares after every summary.
- Reading the transcript briefly opens and closes YouTube's transcript panel, as a
  click does.
- It applies to YouTube tabs opened or reloaded after you turn it on. Remove
  channels on the options page. Why a run did or didn't happen is logged in the
  page's console as `[TL;DW auto] …`.
- **Firefox:** after pulling, re-run `./build-firefox.sh` (`dist-firefox/` isn't
  tracked). If your Firefox can't grant the permission, the section says it isn't
  supported.
- **Rolling back:** untick the box *first*, while the new version is still loaded —
  that gives back the access and unregisters the background script — then revert
  and reload the extension and your YouTube tabs. The stored list is left alone; an
  older version ignores it. If you skipped the untick, check `chrome://extensions` →
  TL;DW → Details → Site access, and remove youtube.com if it's listed.

## Any model, not just Claude

The server shells out to whatever model command it's configured with, so the
extension works the same against a Claude subscription, an Anthropic API key,
OpenAI or Gemini via the `llm` CLI, or a local model through ollama. The summary
still streams in as it's written wherever the backend streams its output.

Two things are Claude-CLI specific and the options page adapts automatically:
the **Model** picker (Opus/Sonnet), which it disables for other backends, and
`tldw usage`, which needs the token and cost figures only that CLI reports. See
[Choose your model](../README.md#choose-your-model) for configuration.

## Where the transcript comes from

The extension reads the title, channel, duration and caption track from the watch
page you're already on, and fetches the captions in your own browser session. The
server never contacts YouTube for a summary started this way — which is why the
extension keeps working when `yt-dlp` alone gets rate-limited. If the page can't
supply them (no captions exposed, or another browser), the server falls back to
fetching them itself.

## How it stays local & safe

- The extension only talks to `127.0.0.1:8765`; the server uses your local `claude`
  (any plan) + `yt-dlp`. Nothing else leaves your machine.
- The server requires the bearer **token** and only accepts `chrome-extension://`
  origins (a web page can't trigger it). It binds loopback only.
- Long videos (map-reduce-sized transcripts) return 413 — use the `tldw` CLI for those.

## Distribution

This is **local-only by design.** There is no hosted/shared server. If you want
someone else to use it, they install the tool on their own machine and run their own
`tldw serve` against their own Claude plan — each person uses their own subscription.
The server deliberately refuses to bind anything but loopback.
