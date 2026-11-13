# YouTube is rate-limiting this machine

This is the YouTube rate-limit playbook. It is **not** the server's own "busy" refusal, which is [`server-busy.md`](server-busy.md).

**Severity:** Sev2 — some videos fail while others, and anything summarized recently, still work (`incident-severity.md`). It's Sev1 for the moment if every video you try needs the server to fetch from YouTube itself.
**Last verified against the code:** 2026-11-12, branch `chore/docs-tier2` at `a7f104c`.

## What you see

> YouTube is rate-limiting this machine (HTTP 429) after too many requests in a short time. It clears on its own — wait a few minutes and try again. Videos summarized recently are cached and unaffected.

- In the panel, for a summary, key moments or a question; and in the terminal when you run the CLI (`tldw <url>`).
- The server sends it as a **`500`**, not a `429`: it's a plain `TldrError` (`src/youtube_tldw/metadata.py:66-76`; `docs/contracts.md`, "Every status the server sends", the `500` row). The panel shows the server's text as-is for a `500` (`extension/background.js:654`, `:520`). So go by the message, not the status number.

**When "Cover listed words" is timing words** (T-020), the panel shows the server's own text instead, sent as an in-stream `429` with `reason` `rate_limited` (`src/youtube_tldw/align.py`), followed by " Until then, spots are covered by caption line.":

> YouTube is rate-limiting tldw serve, so it couldn't get the audio. Turn this off and on again in a few minutes; the wait is at most ten.

## Why it happens

- YouTube throttles unauthenticated extraction from one machine after a burst of requests (`README.md:210-217`).
- tldw recognises it when a `yt-dlp` failure, or a direct caption download, mentions `429` or "too many requests" (`metadata.py:60-63`, `:176`), and replaces the raw `yt-dlp` text with the message above (`metadata.py:66-76`). It does this for the metadata fetch and the subtitle fetch (`metadata.py:87`, `:176`, `:222`).
- The `yt-dlp` calls already retry with exponential backoff before giving up (`metadata.py:56-57`, `README.md:231-232`), so by the time you see this, retrying straight away won't help.
- **A summary started from the extension usually doesn't ask YouTube for anything.** The panel reads the title, captions and duration from the watch page, in your own browser session (`README.md:212-217`; `extension/README.md:94-100`). The server only fetches from YouTube itself when the page couldn't supply captions, and the CLI always fetches for anything it hasn't cached (`README.md:219-221`).
- Anything whose transcript is already cached on disk needs no fetch. How long a transcript stays cached is set in `docs/data-model.md`, store 1.

- **A new trigger (T-020):** turning on "Cover listed words" for a video whose word timings aren't cached yet makes the server download that video's audio once. No cookies are passed for it. After a YouTube 429 there, the server does **no** alignment fetch for 10 minutes (`ALIGN_COOLDOWN_S`), and every attempt in that window gets the text above at once. The cooldown is held **in memory** and covers word timing only: summaries, key moments and questions are unaffected by it. A restart clears it.

## Check

1. Is it this message, word for word? If the panel says it's busy instead, see [`server-busy.md`](server-busy.md).
2. Try a video you summarized recently. If it opens, it's the rate limit, not tldw.

## Fix

1. **Wait a few minutes, then try again.** It clears on its own (`metadata.py:71-75`; `README.md:241`). This is the fix almost every time.
2. **In the meantime,** summarize from the watch page in the browser rather than the CLI: the page path doesn't ask YouTube for captions (`README.md:212-217`).
3. **If it keeps coming back,** you can lend `yt-dlp` your browser session, so it looks like you rather than a bot (`README.md:219-229`):
   ```bash
   tldw config set ytdlp-cookies chrome
   ```
   - The browsers it accepts are `chrome`, `chromium`, `brave`, `edge`, `firefox`, `safari`, `opera` and `vivaldi` (`metadata.py:48-49`). Anything else is silently ignored (`metadata.py:52-55`).
   - **This changes your live config.** It's saved in `~/.config/youtube-tldw/config.json` and stays on until you remove it (`config.py:12`, `:27-31`). It takes effect on the next `yt-dlp` call, without a restart, because the config is read on every call (`config.py:15-24`).
   - **To undo it:**
     ```bash
     tldw config unset ytdlp-cookies
     ```
   - `TLDW_YTDLP_COOKIES` in the environment of `tldw serve` overrides the saved setting while it's set (`metadata.py:53-54`).
   - **What it exposes:** `yt-dlp` reads that browser's cookie database, which holds your signed-in browser sessions. That database is classed **secret** (`docs/data-model.md:278`). tldw itself never sees the values; only the metadata and subtitle calls pass it to `yt-dlp` (`docs/data-model.md:278`). Turn it off again once you no longer need it.
   - The README's form, `tldw config ytdlp_cookies chrome` (`README.md:225`), doesn't work: `config` accepts only `get`, `set` and `unset`, so it prints the usage line and exits 1 without setting anything (`cli.py:365-367`; measured). Use `config set`, as above.

## Don't

- **Don't retry in a loop,** from the CLI or by clicking repeatedly. More requests keep the throttle going.
- **Don't restart `tldw serve` just to clear the word-timing cooldown.** It exists so the server stops asking YouTube while it is throttled; a restart only sends the next request straight back into the throttle.
- **Don't treat it as a tldw bug** to reinstall or roll back: nothing in tldw is broken.
- **Don't paste `tldw config get` output anywhere shared:** it prints home-directory paths.

## If that didn't work

- It hasn't cleared after an hour or so: `UNKNOWN — needs human confirmation` how long YouTube's throttle lasts; nothing in the repo records it. Use the browser path, or the cookies setting above.
- The message is different: find it in [`README.md`](README.md)'s index, or in `docs/contracts.md`, "Pass-through `TldrError` text reachable from a route".

## How this was verified

- **Read on `a7f104c`:** `metadata.py:48-91`, `:160-182`, `:200-223`; `config.py:12-41`; `cli.py:314-367`; `background.js:440-447`, `:571-581`; `README.md:210-241`; `extension/README.md:90-100`; `docs/data-model.md:278`.
- **The quoted message** was checked against `metadata.py:71-75` (one string, joined by implicit concatenation).
- **Measured 2026-11-12** with `HOME` and `TMPDIR` pointed at empty temp dirs, never the owner's config: `tldw config set ytdlp-cookies chrome` exited 0; `tldw config unset ytdlp-cookies` then removed it, and a second `unset` reported it was not set, both exiting 0; the README's `tldw config ytdlp_cookies chrome` exited 1. No `yt-dlp` or model call was made. The temp dirs were deleted afterwards.
- The rate-limit path itself wasn't triggered: that needs YouTube to throttle the machine.

## Update this doc when

- `_translate`, `rate_limited`, `_RETRY`, `_cookie_args` or `COOKIE_BROWSERS` changes, or the message's wording does (`src/youtube_tldw/metadata.py`);
- the `_STATUS` map changes how a plain `TldrError` is sent (`src/youtube_tldw/server.py`), or `httpError` / `askError` stop passing a `500`'s text through (`extension/background.js`);
- `_run_config` changes how keys are spelled or accepted (`src/youtube_tldw/cli.py`);
- `ALIGN_RATE_LIMITED_TEXT`, `ALIGN_COOLDOWN_S` or `_run_align`'s rate-limit branch changes (T-020);
- the panel's rebuild retry changes (`MUTE_RETRY_MS` in `extension/content.js`: 3 retries, 5/10/20 s, and only for a `busy` that came before any `alignProgress`, so never after an audio download began; `rate_limited` is never retried) (T-020 rev 5.6);
- the README's "YouTube rate limiting" section changes, or the page-transcript path stops being the extension's default.
