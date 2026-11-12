# Fix: documented `tldw config` commands that don't work

**Branch:** `fix/config-set-docs`.
**Review scope (bug fix, per the project playbook):** Backend + SDET + Ops.

## The bug
The docs tell users to run `tldw config <key> <value>`. `_run_config` accepts only:
- `get`;
- `set <key> <value…>`;
- `unset <key>`.

Anything else prints the usage line and exits 1 (`src/youtube_tldw/cli.py:365-367`). So the documented way to choose another model backend, and to lend yt-dlp your browser cookies, fails. Found by the hackathon fork's docs bootstrap, which measured it. That is task T-012 there.

The wrong forms:
- `README.md:76` and `:77`, in the "You have / Configure" table: `tldw config llm_cmd "…"`.
- `README.md:81`, in the code block: `tldw config llm_cmd "llm -m gpt-4o"`.
- `README.md:225`: `tldw config ytdlp_cookies chrome`.
- `extension/options.js:63`, the note shown when the server runs a custom model command: "tldw config llm_cmd ... or TLDW_LLM_CMD".

## The fix
1. Insert `set` in each of the five places. Keep the key spelling as it is (`llm_cmd`, `ytdlp_cookies`). `_run_config` maps `-` to `_`, so both spellings work, and `unset llm_cmd` on `README.md:83` already uses the underscore.
2. **Add a test** in `tests/test_cli.py` that every `tldw config …` command the docs show is one `_run_config` accepts. Then this can't drift again.
   - **Extraction ends differently per source (rev 2, SDET):** a backtick span ends at its closing backtick; a fenced code-block line runs to end of line and is split with `shlex.split(line, comments=True)`; the `.js` string ends at `"` or at ` or `. The draft's single "stop at `"`" rule would have cut `set llm_cmd "llm -m gpt-4o"` short, and failed even on the fixed docs.
   - Each command runs through `cli._run_config` with `config.load`/`set_value`/`unset` stubbed. The test asserts that it doesn't return 1, the usage exit, **and** that `set_value` was called for every `set` line.
   - A `...` stands in for a value (options.js), so it is replaced with a dummy.
   - **A count floor** pinned at the post-fix total. On failure the test prints what it extracted.
   - **Mutation check:** revert a backtick line (`README.md:76`) and a code-block line (`:225`) to the old form, and the test must fail naming each.
3. **The usage line and docstring** (`cli.py:315`, `:365-366`) name `set <key> <value>`, not only `output-dir`, so the error points at the right form (Backend).
4. Rebuild the git-ignored `extension/dist-firefox/` so the Firefox build matches.

## Not in scope
- Making `tldw config <key> <value>` itself work, as a CLI shorthand. That would be a behaviour change. The docs are what's wrong, so the fix goes in the docs.

## Tests
- The full suite (`.venv/bin/python -m pytest -p no:cacheprovider`) passes.
- The new test passes, and fails on the mutation.
- `node --check extension/options.js` passes.

## Plan review
- Backend: PASS WITH ITEMS. The usage string is now item 3.
- SDET: BLOCK. The terminators broke the quoted values. Fixed in rev 2, above.
- Ops: PASS WITH ITEMS. Rebuild dist-firefox and reload the extension; no server restart is needed.

## Build review
**Status:** PASS WITH ITEMS.

- **Backend:** PASS. The docs fix is correct; the help text and docstring now show the working `set <key> <value>` form.
- **SDET:** PASS WITH ITEMS.
  - The extractor and assertions match rev 2.
  - The 14 extracted commands match the 14 raw `git grep` mentions, and the floor is pinned at 14.
  - Mutants of `README.md:76` (backtick) and `:225` (code block) each fail, naming their line.
  - **Warning, kept for later:** the code-block rule only catches lines that start with `tldw config` (after any `VAR=` prefix), so a `$ ` prompt or a `&&` chain would be missed. The count floor partly covers this.
- **Ops:** PASS WITH ITEMS. No server restart is needed: the Python change is only the usage text. Reload the extension in Chrome and Firefox (`dist-firefox` is rebuilt). The README:81-83 comment alignment nit is fixed.

**Tests:**
- The full suite: 675 passed (660 + 15 new).
- `node --check extension/options.js`: OK.

**Sign-offs:** Backend ✔ · SDET ✔ · Ops ✔
