# The extension can't reach the server, or says the token doesn't match

**Severity:** Sev1 — nothing can be summarized until it's fixed (`incident-severity.md`).
**Last verified against the code:** 2026-11-12, branch `chore/docs-tier2` at `a7f104c`.

## What you see

The extension can't reach the server:

> Can't reach the tldw server. Is it running? In a terminal:  tldw serve
> Can't reach the tldw server. Is it running?  tldw serve
> Can't reach the server. Is `tldw serve` running?
> This is taking too long. Make sure `tldw serve` is running, then try again.
> Audio is taking too long. Make sure `tldw serve` is running.
> That question is taking too long. Is `tldw serve` running?
> Finding key moments is taking too long. Is `tldw serve` running?

- The first is the panel, when a summary's request fails to connect (`extension/background.js:220`). The second is the same failure for Listen, a question, a voice preview or key moments (`background.js:332`, `:433`, `:485`, `:560`). The third is the Options page's voice preview (`extension/options.js:115`).
- The last four are the panel's own safety timers, when no answer comes back at all (`extension/content.js:667`, `:1113`, `:1391`, `:1533`).

The token doesn't match:

> Token mismatch. Re-check the token in the extension's Options.
> Token mismatch — check the token from `tldw serve`.

- The first is the panel, for a summary, key moments, a question or Listen (`background.js:572`, `:441`, `:492`). The second is the Options page's voice preview (`options.js:105-106`). Both replace the server's own `missing or invalid token` text, which you never see (`server.py:991`, `:1021`, `:1107`; `docs/contracts.md`, "What the extension does with a status").

No token has been saved at all:

> No server token set. Open the extension's Options and paste the token from `tldw serve`.
> Paste the token first.

- The first is the panel (`background.js:102`, `:138`, `:252`, `:372`, `:460`, `:504`), the second the Options page's preview button (`options.js:90`).

The full list of these messages, with every source line, is in `docs/contracts.md`, "User-facing message contracts".

## Why it happens

**Can't reach** means the browser's request to the server failed before any answer came back (`background.js:218-221`). Either:
1. `tldw serve` isn't running;
2. it couldn't start, most often because the port is already taken (below);
3. the extension's **Server URL** in Options points somewhere else. It defaults to `http://127.0.0.1:8765` (`options.js:3`, `background.js:10`).

**Token mismatch** is a `401` from the server: the token the extension sends isn't the one the server is using (`server.py:991`, `:1021`, `:1107`). The server picks its token in this order: `--token`, then `TLDW_TOKEN`, then the token file `~/.config/youtube-tldw/token` (`cli.py:603`; `server.py:692-708`). The file is created on first start and reused after that, so normally the token never changes (`docs/data-model.md`, store 6). It changes only if the file is deleted, or if you started the server with `--token` or `TLDW_TOKEN` set to something else.

**No server token set** means the extension's Options has no token saved yet (`background.js:99-103`).

## Check

1. **Is the server running?** Look for the terminal where you started `tldw serve`. If there isn't one, start it (Fix, step 1).
2. **Does it answer?** In any terminal:
   ```bash
   curl -s http://127.0.0.1:8765/health
   ```
   A JSON object with `"ok": true` means the server is up and listening on the default port (`server.py:973-976`). `/health` needs no token, so this never touches it. No answer means it isn't running or is on another port.
3. **Does Options point at it?** Open the extension's Options (from `chrome://extensions`, the extension's **Details**, then **Extension options**). **Server URL** should be `http://127.0.0.1:8765` unless you deliberately changed it.
4. **If you started the server with `--token` or `TLDW_TOKEN`,** that value is the one in use, not the file's (`server.py:698-699`).

## Fix

1. **Start the server** in a terminal and leave it running:
   ```bash
   tldw serve
   ```
   It prints its token as part of its startup. Don't share that terminal's output, or a screenshot of it (`server.py:1959`; `docs/deployment.md` § 5).

2. **If `tldw serve` won't start because the port is in use,** there's no tldw message: it ends with a Python traceback whose last line is an `OSError` "Address already in use" (on macOS, `[Errno 48]`), and exits with status 1 (`server.py:1941`; measured, see below). Another program, usually another `tldw serve`, already holds port 8765.
   - Find the other one and stop it: switch to its terminal and press Ctrl-C (`server.py:1964-1971`). If you can't find it, on macOS `lsof -nP -iTCP:8765 -sTCP:LISTEN` names the process holding the port.
   - Or run this one on another port, `tldw serve --port <port>`, and set **Server URL** in Options to `http://127.0.0.1:<port>`. Whether the extension can reach a port other than 8765 is `UNKNOWN — needs human confirmation`: its `host_permissions` cover `http://127.0.0.1:8765/*` only (`extension/manifest.json:7`; `docs/deployment.md` § 11; tracked in T-015). Stopping the other server is the reliable fix.

3. **Fix a token mismatch, or a missing token,** by copying the server's token into Options:
   - Copy the token from `~/.config/youtube-tldw/token`. On macOS, `pbcopy < ~/.config/youtube-tldw/token` puts it on the clipboard without showing it on screen.
   - Open the extension's Options, paste it into **Token**, and click **Save**. "Saved" confirms it (`options.js:137-141`). The next request uses it; nothing needs reloading (`background.js:43-44` reads the settings on every request).
   - Paste it **only** into the extension's Options. Never into a chat, an issue, a screenshot, or a file.
   - If you started the server with `--token` or `TLDW_TOKEN`, either paste that value instead, or restart with plain `tldw serve` so the file's token is used. A token on the command line is visible to other local users in `ps` (`docs/deployment.md` § 5; T-004).
   - If you have both the normal and the hackathon extension installed, each keeps its own token in its own Options (T-019, T-004). Paste it into the one you're using.

4. **Try again:** click TL;DW on the YouTube tab.

## Don't

- **Don't delete the token file** to "reset" things. The next `tldw serve` makes a new token, and every extension you've configured gets a token mismatch until you paste the new one in (`server.py:700-708`; `docs/data-model.md:370`). The one exception is replacing a **leaked** token, which is `onboarding.md`, "First 30 minutes", step 3 ("Start the server").
- **Don't share raw `tldw serve` output** or a screenshot of its terminal: it prints the token on every start (`server.py:1959`).
- **Don't copy the token file's contents into anything shared,** and don't print it into a terminal you're screen-sharing.
- **Don't bind the server to anything but loopback** (`--host 0.0.0.0` and the like) to "make it reachable". The server refuses, and that refusal is deliberate (`server.py:1935-1939`; ADR 0003).
- **Don't loosen CORS or skip the token.** The server allows only `chrome-extension://` origins unless `--allow-origin` pins one exact origin (`docs/deployment.md` § 4, § 11).

## If that didn't work

- The server answers `/health` and the token matches, but summaries still fail: see [`model-failing.md`](model-failing.md) or [`server-busy.md`](server-busy.md), by the message you see.
- You use Firefox: whether the Firefox build works without `--allow-origin` is `UNKNOWN — needs human confirmation` (`docs/deployment.md` § 11; T-015). See the "Less common" table in [`README.md`](README.md).
- It started right after an update: [`rollback.md`](rollback.md).

## How this was verified

- **Read on `a7f104c`:** `background.js:43-44`, `:99-103`, `:218-221`, `:332`, `:433`, `:441`, `:485`, `:492`, `:560`, `:572`; `options.js:3`, `:85-116`, `:137-141`; `content.js:667`, `:1113`, `:1391`, `:1533`; `server.py:689-708`, `:973-992`, `:1021`, `:1107`, `:1933-1971`; `cli.py:301-311`, `:596-609`; `extension/manifest.json:7`.
- **Every quoted message** was checked against its string constant in `extension/*.js`.
- **Port in use, measured 2026-11-12 on macOS,** in a scratch directory with `HOME` and `TMPDIR` pointed at empty temp dirs: a spare port (not 8765 or 8766) was held by a dummy listening socket, first without and then with `SO_REUSEADDR` (as a second `tldw serve` would set it). Both times `tldw serve --port <that port>` exited 1, and the last exception line was `OSError: [Errno 48] Address already in use`. It printed no `listening on` line and no token line. Only the exit code and that one line were recorded. No model call was made, and the scratch directory was deleted afterwards. The same result is recorded in `docs/deployment.md` § 4.
- `lsof` and `pbcopy` are standard macOS tools, not part of tldw. Neither was run against the owner's real token.

## Update this doc when

- any message quoted above changes, or its mapper does: `httpError`, `askError`, `speakError`, the network-failure branches in `extension/background.js`, the preview handler in `extension/options.js`, or the safety timers in `extension/content.js`;
- `load_or_create_token`, `TOKEN_FILE`, `_authorized`, `serve()` or the `/health` handler changes (`src/youtube_tldw/server.py`);
- `_serve_parser` or the `serve` dispatch changes (`src/youtube_tldw/cli.py`);
- `DEFAULTS.serverUrl` or either manifest's `host_permissions` changes;
- T-015 settles whether the extension can reach a non-8765 port, or how Firefox's origin behaves.
