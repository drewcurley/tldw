# TL;DW browser extension

An MV3 extension (Chromium **and** Firefox) that summarizes the YouTube video you're
watching by calling your **local `tldw` server** — summary in an on-page modal, plus
Listen (TTS) and Play-key-moments.

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
- A modal shows the title, key points, and summary (~20s; transcript fetch + Claude).
- **Copy** grabs the markdown; **Esc** or click-outside closes.
- **🔊 Listen to summary** synthesizes the summary to speech (local Piper TTS) and
  plays it in the modal. Pick a voice (US/UK, male/female) right there or set a
  default in options. First use of a voice downloads its model (~once).
- **⏭ Play key moments** finds the key segments (transcript + Claude — no download,
  no recut) and **auto-skips the YouTube player through just those moments**, in full
  quality in your own player. A floating pill shows progress (e.g. "Key moment 2/5")
  with a ✕ to stop.

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
