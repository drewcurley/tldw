# The model call failed

**Severity:** Sev1 — summaries, key moments and questions all need the model, so none of them work. Listen still does: reading aloud uses Piper, not the model (`incident-severity.md`; `src/youtube_tldw/audio.py:198-216`, `server.py:1478-1480`).
**Last verified against the code:** 2026-11-12, branch `chore/docs-tier2` at `a7f104c`.

## What you see

For a summary or key moments, the panel always shows the same fixed text, whatever the real reason was:

> The model call failed. If you're on the Claude CLI, make sure `claude` is logged in; otherwise check your configured model command.

- It's what `httpError` shows for any `502`; the server's own explanation is dropped (`extension/background.js:578`; `docs/contracts.md`, "What the extension does with a status"). **The real reason is only in the `tldw serve` terminal** (Check, step 1).

For a question, the panel shows the server's text instead (`background.js:446`). When the `claude` CLI printed nothing at all, that is:

> Claude returned no output. Are you logged in? Try `claude` once interactively to confirm your session.

- Built at `src/youtube_tldw/claude_client.py:41-44`.
- The other model errors carry the CLI's own words, so they're built at run time and can't be quoted: "`claude` failed:" followed by the CLI's error report (`claude_client.py:483-484`), "`claude` failed (exit" with the exit code and the last lines it printed (`claude_client.py:494-495`), and "Claude reported an error:" with the detail (`claude_client.py:49-51`). Every one is listed in `docs/contracts.md`, "Pass-through `TldrError` text reachable from a route".

## Why it happens

The server runs the `claude` CLI (or your own model command) for every summary, key-moment selection and question. Any failure of that command is a `ClaudeError`, sent as a `502` (`server.py:711-717`). The common causes:

1. **The CLI is logged out.** It answers with nothing, or with an error report saying so (`claude_client.py:41-44`; `:410-415`).
2. **Several calls at once logged a stored login out.** With no `CLAUDE_CODE_OAUTH_TOKEN` in the environment, concurrent `claude` calls on a stored login can log you out (`server.py:675-677`; `README.md:73`; anthropics/claude-code#76905). tldw runs several at once by design: a summary, its key-moment prefetch, and questions (`docs/contracts.md`, "Limits, pools and slots").
3. **A usage limit, or the API being overloaded.** The CLI reports these as an error result, part-way through if need be, and tldw passes its words on (`claude_client.py:410-415`, `:483-484`).
4. **`ANTHROPIC_API_KEY` is set** in the environment `tldw serve` runs in. Then calls may be billed to the API rather than your Claude plan (`server.py:671-673`; `README.md:74`). Whether a key with no credit fails these calls is `UNKNOWN — needs human confirmation`.
5. **A custom model command** (`TLDW_LLM_CMD`, `--llm-cmd` or the `llm_cmd` config key) is failing, or isn't on your `PATH` (`claude_client.py:59-62`, `:95-97`; `docs/contracts.md`, "Pass-through `TldrError` text").

A model call that takes too long is a `504` timeout, not this; see the "Less common" table in [`README.md`](README.md).

## Check

1. **Read the `tldw serve` terminal.** Each failed request prints a line starting `failed (502)` (a summary), `segments failed (502)` (key moments) or `ask failed (502)` (a question), followed by the real reason (`server.py:1184`, `:1303`, `:1631`, `:1839`). That reason tells you which cause it is.
2. **Read the startup lines of the same terminal.** When the backend is the `claude` CLI, the server says which login variables are in its environment, by **name only, never their values**: whether `CLAUDE_CODE_OAUTH_TOKEN` is set, and whether `ANTHROPIC_API_KEY` is (`server.py:660-679`, `:1946-1949`). Don't copy these lines out with the rest of the output: the token line follows them (`server.py:1959`).
3. **Run `claude` by hand,** interactively, in a terminal, once. This is the CLI's own check, the one its error suggests (`claude_client.py:41-44`). If it asks you to log in, that's cause 1.

## Fix

1. **Logged out:** run `claude` in a terminal and follow its login prompt, then try again in the panel. tldw starts the CLI afresh for every call (`claude_client.py:196`), so it doesn't need restarting for this.

2. **Logged out again and again (cause 2):** give the server a token that concurrent calls can share.
   - Run `claude setup-token` and follow its prompts (`server.py:675-677`; `README.md:73`).
   - In the **same shell that will run `tldw serve`**, and only there, put it in `CLAUDE_CODE_OAUTH_TOKEN`. One way that keeps it off the screen and out of your shell history:
     ```bash
     read -rs CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN
     ```
     then paste the token and press Enter.
   - Stop `tldw serve` (Ctrl-C) and start it again from that shell. Its startup line now says the variable is set.
   - **Never write the value into a file** (a shell profile, an `.env`, a script) **or into a log,** and never paste it anywhere else. tldw itself only checks whether the variable is set (`server.py:666-668`).

3. **Usage limit or API overloaded:** nothing in tldw to change. Wait, then try again. Your plan's limits are the CLI's, not tldw's.

4. **`ANTHROPIC_API_KEY` set but you meant to use your plan:** stop `tldw serve`, `unset ANTHROPIC_API_KEY` in that shell, and start the server again. Check the startup lines no longer mention it.

5. **Custom model command:** run the same command by hand to see its error. To change it, `tldw config set llm_cmd "<your command>"`, or set `TLDW_LLM_CMD` where `tldw serve` runs (`cli.py:349-358`; `claude_client.py:210`). The form the Options page and the README give, `tldw config llm_cmd …`, doesn't work (`docs/contracts.md`, "Fixed messages from the panel and Options"; T-012).

## Don't

- **Don't paste `tldw serve` output** to show someone the error. It prints the token on every start (`server.py:1959`). Copy just the one `failed (502)` line.
- **Don't put `CLAUDE_CODE_OAUTH_TOKEN`'s value in a file, a log, a screenshot or a chat.**
- **Don't delete tldw's token file** to "reset the login." tldw's token and the `claude` CLI's login are unrelated; deleting it only breaks the extension (`unreachable-or-token-mismatch.md`).

## If that didn't work

- `claude` works by hand, the startup lines look right, and every call still fails: the `failed (502)` line's reason is what to search for, or to record as a task (`tasks/README.md`).
- It started right after you updated tldw: [`rollback.md`](rollback.md).
- Whether the `claude` CLI finds its login when `tldw serve` runs under a different `HOME` is `UNKNOWN — needs human confirmation` (`docs/deployment.md` § 4; T-015).

## How this was verified

- **Read on `a7f104c`:** `claude_client.py:36-62`, `:90-98`, `:390-495`; `server.py:660-679`, `:711-717`, `:1180-1188`, `:1300-1310`, `:1628-1638`, `:1836-1846`, `:1942-1963`; `background.js:440-447`, `:571-581`; `README.md:73-74`; `cli.py:314-367`.
- **The two quoted messages** were checked against their string constants (`background.js:578`; `claude_client.py:41-44`, one string by implicit concatenation). Messages carrying the CLI's words are f-strings, so they are cited, not quoted.
- **No model call was made** for this doc, and no login was changed. The `read -rs` line is standard `zsh` and `bash`, not part of tldw.

## Update this doc when

- `_extract_result_text`, `_raw_extract`, the streaming error handling (`result_error`) or the `proc` failure text changes (`src/youtube_tldw/claude_client.py`, `src/youtube_tldw/proc.py`);
- `_login_lines`, `_STATUS`, or the `failed (…)` log lines change (`src/youtube_tldw/server.py`);
- the `502` branch of `httpError`, or `askError`'s fallback, changes (`extension/background.js`);
- the README's "Choose your model" table changes;
- T-012 fixes the `tldw config llm_cmd` form in the README and the Options page.
