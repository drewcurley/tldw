# TL;DW browser extension

A thin Chromium (Chrome/Arc/Edge/Brave) extension that summarizes the YouTube video
you're watching by calling your **local `tldw` server**. Text only — the summary
appears in a modal on the page.

## Setup

1. **Start the server** (in a terminal):
   ```bash
   tldw serve
   ```
   It prints a token. Leave it running.

2. **Load the extension** (unpacked):
   - Open `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
     select this `extension/` folder.

3. **Configure** → click the extension's **Details → Extension options** (or the gear),
   paste the **token** from step 1. (Server URL defaults to `http://127.0.0.1:8765`.)

## Use

- Open any YouTube `/watch` page → click the **TL;DW** toolbar button.
- A modal shows the title, key points, and summary (~20s; transcript fetch + Claude).
- **Copy** grabs the markdown; **Esc** or click-outside closes.
- **🔊 Listen to summary** synthesizes the summary to speech (local Piper TTS) and
  plays it in the modal. Pick a voice (US/UK, male/female) right there or set a
  default in options. First use of a voice downloads its model (~once).

## How it stays local & safe

- The extension only talks to `127.0.0.1:8765`; the server uses your local `claude`
  (Max) + `yt-dlp`. Nothing else leaves your machine.
- The server requires the bearer **token** and only accepts `chrome-extension://`
  origins (a web page can't trigger it). It binds loopback only.
- Long videos (map-reduce-sized transcripts) return 413 — use the `tldw` CLI for those.

## Distribution

This is **local-only by design.** There is no hosted/shared server. If you want
someone else to use it, they install the tool on their own machine and run their own
`tldw serve` against their own Claude plan — each person uses their own subscription.
The server deliberately refuses to bind anything but loopback.
