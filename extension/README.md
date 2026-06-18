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

## How it stays local & safe

- The extension only talks to `127.0.0.1:8765`; the server uses your local `claude`
  (Max) + `yt-dlp`. Nothing else leaves your machine.
- The server requires the bearer **token** and only accepts `chrome-extension://`
  origins (a web page can't trigger it). It binds loopback only.
- Long videos (map-reduce-sized transcripts) return 413 — use the `tldw` CLI for those.

## Sharing it later

To let trusted people use a hosted server instead of running it locally, you must
move the server off `localhost` — which means TLS, a real per-user auth model, and
**Anthropic API billing instead of your personal Max subscription** (serving others
through Max is against its terms). That's a separate, deliberate step.
