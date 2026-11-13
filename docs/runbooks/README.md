# Runbooks

**Owner:** DevOps. **Task:** T-001.10. **Last verified against the code:** 2026-11-12, branch `chore/docs-tier2` at `a7f104c`.

What to do when tldw stops working on your machine. Each playbook starts with the exact message you see, then tells you how to fix it. tldw has one owner and no hosted service, so these are written for the person at the keyboard, not for an on-call rotation (`on-call.md`).

## Find your problem

Start from the message the panel, the Options page or the terminal shows you.

| If you see… | Severity | Playbook |
|---|---|---|
| "Can't reach the tldw server", "Is `tldw serve` running?", "Token mismatch", "No server token set" | Sev1 | [`unreachable-or-token-mismatch.md`](unreachable-or-token-mismatch.md) |
| "The model call failed", "Claude returned no output", "`claude` failed" | Sev1 | [`model-failing.md`](model-failing.md) |
| "YouTube is rate-limiting this machine (HTTP 429)…", or for "Cover listed words", "YouTube is rate-limiting tldw serve, so it couldn't get the audio…" (an in-stream `429`) | Sev2 | [`youtube-rate-limit.md`](youtube-rate-limit.md) |
| "Already working on … Try again in a moment", "Already reading … summaries aloud", "Server busy", "Already timing words for another video…", "Whisper is busy transcribing another video…" | Sev3 | [`server-busy.md`](server-busy.md) |
| Anything, and it started right after you updated tldw | Sev1 or Sev2 | [`rollback.md`](rollback.md) |
| Something in the table below | — | Less common problems, below |

The other files:
- [`incident-severity.md`](incident-severity.md): what Sev1, Sev2 and Sev3 mean here.
- [`on-call.md`](on-call.md): why there's no rotation, paging or escalation, and where problems outside tldw belong.
- [`_TEMPLATE.md`](_TEMPLATE.md): the shape every playbook follows.

Every message the extension can show, with its source line, is listed in `docs/contracts.md`, "User-facing message contracts". The server's own error texts are in `docs/contracts.md`, "Errors".

## Less common problems

| Problem | What you see | What to do | Source |
|---|---|---|---|
| **A timeout (`504`)** | For a summary or key moments, the panel's fixed text, `Summarizing timed out on the server. Try again, or use the CLI for long videos.`, even for key moments, where "Summarizing" is the wrong word. If the panel's own budget runs out first, it says so itself (`background.js:290-291`, `:632`). For questions and Listen, their own timeout text (`background.js:519`, `:569`). | Try again. For a very long video, use the CLI: `tldw <url>`. | `extension/background.js:653`; the time budgets are in `docs/contracts.md`, "Limits, pools and slots" |
| **A transcription that's taking too long** | The server's "it may still finish" text is **replaced** by the summary timeout text above, because `httpError` replaces every `504` (`docs/contracts.md`, "Server error text the extension can show"). The panel's own transcription timers say `Transcribing timed out. The transcript may still finish on the server — try again in a few minutes.` (`background.js:290`) or `Transcribing is taking longer than expected. It may still finish on the server — try again in a few minutes.` (`content.js:632-633`). | Wait a few minutes, then click TL;DW again: a finished transcript is kept (`docs/data-model.md`, store 1). Only one transcription runs at a time, machine-wide (`docs/contracts.md`, `_tx_permit`). | `server.py:1429-1431`; `background.js:653` |
| **Listen says Piper isn't installed (`503`)** | The server's text, naming the install command, or, if it sends none, `Audio isn't available — Piper TTS isn't installed on the server.` (`background.js:568`). If Piper is installed but can't run on this machine, the server says so and names the platform (`audio.py:210-216`). | `pipx inject youtube-tldw piper-tts` (or `pip install piper-tts` in a venv), then restart `tldw serve` (`audio.py:198-202`). The server's text mentions the CLI flag `--render-audio`; the same install is still the fix. On a platform with no Piper wheel, there's no fix: everything except speech still works (`audio.py:210-216`). | `server.py:1478-1480`; `audio.py:198-216` |
| **A video with no captions, and Whisper isn't installed** | The panel says the video has no subtitles and that transcribing the audio needs Whisper, then gives the install command (`content.js:819-829`). With the Whisper setting off, the server's "nothing to summarize" text instead (`metadata.py:121-124`). | `pipx inject youtube-tldw faster-whisper` (or `pip install faster-whisper`), restart `tldw serve`, and click TL;DW again (`content.js:819-829`). Or try a different video. | `content.js:819-829`; `metadata.py:121-124`; `docs/contracts.md`, `422` |
| **A captionless video that's too long to transcribe (`413`)** | The server's text, with the video's length and the limit in minutes (built at run time, `server.py:1395-1399`). | Raise the limit, `tldw config set whisper-max-minutes <minutes>`. That changes your live config; undo it with `tldw config unset whisper-max-minutes` (`cli.py:319-366`). | `server.py:1385-1400`; `docs/data-model.md`, store 5 |
| **A transcript too long for the browser (`413`)** | "This transcript is too long for the browser flow; use the `tldw` CLI for very long videos." (the server's text, shown as-is, `background.js:647`). | Use the CLI: `tldw <url>`. The browser routes never split a long transcript (`docs/contracts.md`, `SINGLE_PASS_CHARS`). | `src/youtube_tldw/core.py:189-192` |
| **"Cover listed words": ffmpeg missing** (`ffmpeg_missing`) | `… by caption line: Word timing needs ffmpeg, and tldw serve can't find it. Install ffmpeg, restart tldw serve, then turn this off and on again.` | Install ffmpeg so it is on the `PATH` of the shell that runs `tldw serve`, restart it, then toggle off and on. Words stay covered by caption line meanwhile. The server log shows `align: ffmpeg_missing <video_id>`. | `align.py` `FFMPEG_MISSING_TEXT` |
| **"Cover listed words": Whisper's model isn't downloaded** (`model_missing`) | `… by caption line: Whisper's model isn't downloaded yet. Transcribe one video that has no subtitles to fetch it, then turn this off and on again.` | Word timing never downloads a model. Transcribe any captionless video once (that downloads it), then toggle. Log: `align: model_missing`. | `align.py` `MODEL_MISSING_TEXT`; `content.js` `MUTE_TEXT.modelMissing` |
| **"Cover listed words": Whisper not installed, or can't load** (`whisper_missing`) | `… by caption line. To time them to the word, install Whisper for tldw serve: pipx inject youtube-tldw faster-whisper, then restart tldw serve and turn this off and on again.` | Run the command, restart `tldw serve`, toggle. If it is installed but can't load, reinstall it with the same command and restart; the panel shows the same install text either way. The server log shows only `align: whisper_missing <video_id>`. | `content.js` `MUTE_TEXT.whisperMissing`; `align.py` `WHISPER_BROKEN_TEXT` |
| **"Cover listed words": Whisper turned off** (`whisper_off`) | `… by caption line, because Whisper is turned off. To time them to the word, set 'Videos with no subtitles' in TL;DW's options to either transcribe choice.`, or, when the server pins it off, `… by caption line: Whisper is turned off for tldw serve on this machine (tldw config unset whisper turns it back on).` | Change the Options setting, or run `tldw config unset whisper`. Log: `align: whisper_off`. | `content.js` `MUTE_TEXT.whisperOff`; `align.py` `WHISPER_PINNED_OFF_TEXT` |
| **"Cover listed words": video longer than the Whisper limit** (`too_long`, or `download_failed` when yt-dlp skips it) | `… by caption line: This video is longer than tldw serve's Whisper limit. To time words, run tldw config set whisper-max-minutes <minutes>, then turn this off and on again.`, or `… YouTube didn't give this video's length, or it's longer than tldw serve's Whisper limit, so its audio can't be fetched. If it's the limit, run tldw config set whisper-max-minutes <minutes>, then turn this off and on again.` | Raise the limit as it says, or accept caption-line covering for this video. Log: `align: too_long` or `align: download_failed`. | `align.py` `TOO_LONG_TEXT`, `AUDIO_SKIPPED_TEXT` |
| **"Cover listed words": the audio fetch failed or timed out** (`download_failed`, `timeout`, `other`) | `… by caption line: Couldn't fetch this video's audio. Turn this off and on again to retry.`, `… Fetching the video's audio timed out. …`, or `… Timing the words failed unexpectedly. …` | Toggle off and on once. If it repeats, check the server log: `align: <reason> <video_id>` names the reason, and the exception's type name is on its own line, `align: unexpected <video_id>: <Type>` or `align: window <n> failed <video_id>: <Type>`, and the rate-limit playbook. | `align.py` `DOWNLOAD_FAILED_TEXT`, `DOWNLOAD_TIMEOUT_TEXT`, `ALIGN_OTHER_TEXT` |
| **"Cover listed words": an older server** | `… by caption line: this tldw serve can't time words. Update tldw, then restart tldw serve and turn this off and on again.` | The server answered `404` for `/align/stream`: update it (or, after a rollback, accept caption-line covering). | `content.js` `MUTE_TEXT.olderServer` |
| **"Cover listed words": the token** | `… by caption line: TL;DW's token for tldw serve is missing or wrong. …` | See [`unreachable-or-token-mismatch.md`](unreachable-or-token-mismatch.md). | `content.js` `MUTE_TEXT.token` |
| **`tldw serve` won't start: the port is in use, or a second server** | A Python traceback ending in an `OSError`, "Address already in use" (on macOS, `[Errno 48]`), and exit status 1. There's no tldw message. | Stop the other server (Ctrl-C in its terminal). Or use `tldw serve --port <port>` and set **Server URL** in Options to match; whether the extension can reach a port other than 8765 is `UNKNOWN — needs human confirmation` (T-015). Details: [`unreachable-or-token-mismatch.md`](unreachable-or-token-mismatch.md), Fix step 2. | `server.py:1941`; measured (see that playbook); T-013 |
| **Firefox can't reach the server** | `UNKNOWN — needs human confirmation`. With no `--allow-origin`, a `moz-extension://` origin gets no CORS header (measured); whether Firefox's extension requests are blocked by that is untested. | `UNKNOWN — needs human confirmation`. `--allow-origin` pins one exact origin; don't loosen it beyond that. | `docs/deployment.md` § 11; `server.py:917-924`; T-015 |

## Rules for every playbook

- **Never share raw `tldw serve` output,** or a screenshot of its terminal. It prints the token on every start (`server.py:1959`). Copy the one line you need. When you report a problem, strip the `token:` line first (`docs/deployment.md` § 11, "Logs").
- **Never put a token in a doc, a chat, an issue or a file:** not tldw's (write `<your-token>`), and not the `claude` CLI's `CLAUDE_CODE_OAUTH_TOKEN`.
- **Never bind the server off loopback, loosen CORS, or skip the token** to make something work (`server.py:1935-1939`; ADR 0003).
- **Never share `tldw config get` output:** it prints home-directory paths.
- **"Reload the extension"** means `chrome://extensions`, the extension's reload button, and then reload the YouTube tab as well (`docs/deployment.md` § 10, step 5).

## Adding a playbook

1. Copy [`_TEMPLATE.md`](_TEMPLATE.md) to `docs/runbooks/<short-symptom-name>.md`, and fill every `<angle>` slot.
2. Under `## What you see`, put each exact message on its own line starting with `> `, copied from its string constant in `extension/*.js` or `src/youtube_tldw/*.py`. A message with a variable part (an f-string, or JavaScript built around a variable) is cited by `path:line`, not quoted. `tests/test_docs_contracts.py` fails on any quoted line that isn't a fixed message in the code.
3. Add it to the table at the top of this file, with its severity from `incident-severity.md`.
4. A change to `docs/` needs all 9 agents' sign-off (`AGENTS.md` § 0, Step 4).

## How this was verified

- Each row of "Less common problems" was traced on `a7f104c`: `background.js:216-217`, `:445`, `:494-495`, `:558`, `:571-581`; `content.js:611-612`, `:798-808`; `server.py:917-924`, `:1385-1400`, `:1429-1431`, `:1478-1480`, `:1941`; `audio.py:198-216`; `core.py:189-192`; `metadata.py:121-124`; `cli.py:314-367`.
- The index's message fragments were matched against the quoted messages in each playbook, which `tests/test_docs_contracts.py` checks against the code.
- The busy-port row was measured in isolation; how is recorded in `unreachable-or-token-mismatch.md`. Nothing else here was run, and no model call was made.

## Update this doc when

- a playbook in this folder is added, renamed or removed;
- a message named in the index changes (`extension/background.js`, `extension/content.js`, `extension/options.js`, `src/youtube_tldw/server.py`, `src/youtube_tldw/metadata.py`, `src/youtube_tldw/claude_client.py`);
- the status mapping in `httpError`, `askError` or `speakError` changes (`extension/background.js`);
- an install hint changes (`audio.py`, `whisper_transcribe.py`, `content.js:819-829`);
- a "Cover listed words" status or `align.py` text changes, or a word-timing `reason` is added (T-020);
- T-013 gives the busy port a message, or T-015 settles the non-8765 port or Firefox's origin.
