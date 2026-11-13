# Review: chore/docs-tier1 — Round 1, Analysis (T-001 Tier 1 drafts)

**Status:** PASS WITH ITEMS. The Analyst, Architect and Data Engineer each blocked the first draft, then signed off after one fix pass. Each verified the fixes against the files and the code.

## Blockers (all fixed)
- [x] **Analyst B1.** The follow-up tasks didn't exist. Fixed: `tasks/T-004.md`…`T-018.md`, grouped by theme. Every follow-up list cites its task, and the index lists them all.
- [x] **Analyst B2.** Seeded criteria were narrowed without saying so. Fixed: 40 criteria across the Tier 1 subtask files are marked "met as N/A" or "met differently", each with its reason. None was ticked.
- [x] **Analyst B3.** The PII classification was never confirmed. Fixed: the Architect confirmed it in `architecture.md` § 8, with two refinements: `chrome.storage.local` is Secret, and the worker's in-memory caches get their own rows.
- [x] **Architect B1.** "content.js never holds the token" was false. Storage isn't restricted to trusted contexts (no `setAccessLevel`), and content.js's `onChanged` listener receives the new token (`content.js:50-55`). Reworded in architecture.md § 2.2, § 4, I-17, ADR 0003 item 4 and deployment.md § 5. Added as § 7 item 13 (→ T-004).
- [x] **Architect B2.** § 7 claimed follow-up tasks that didn't exist. Fixed: every item ends "→ T-0NN".
- [x] **Data B1.** data-model.md said the request log has no query string, contradicting contracts.md. Fixed: the query string *is* logged, and the stale comment at `server.py:962-963` goes to T-012.
- [x] **Data B2.** The summary key was described as covering every input. Fixed: `PROMPT_FINGERPRINT` covers four strings (`summarize.py:144`). `_ratio_clause`, `_metadata_header` and `SINGLE_PASS_CHARS` aren't covered, so editing them serves stale summaries for up to 14 days. The invariant now reads "must be in its key" and names the gap (→ T-014).

## Warnings
- [x] Architect W1–W5:
  - I-13's redirect caveat;
  - the DoS cell states the facts (no socket timeout, unbounded threads, `server.py:1924-1925`);
  - the contracts invariant is scoped to unexpected exceptions;
  - any installed extension's origin passes CORS;
  - `checks.yml:194` is in stack.md.
- [x] Data W1–W9:
  - the third-party stores (yt-dlp's cache, the cookie DB);
  - the never-change-a-type rule;
  - the type fragility in stats and config;
  - the marker, in-memory, buffered `/summarize` and unreadable-file facts;
  - data-model.md owns the key composition and the TTL/cap values.
- [x] Analyst W1–W4:
  - the index's `ls -R` check;
  - the custom `serverUrl` question, which needs a browser (→ T-015);
  - the pipx rollback marked untested (→ T-015);
  - the citation fix.
- [ ] **Open (Analyst):** testing.md body text still says "FU-n", resolved only through its Follow-ups list. Writing the T-NNN inline would be clearer.
- [ ] **Open (Data nit):** data-model.md's opening line doesn't name yt-dlp among the tools that write data.

## Spot-checks (by reviewer, against the code)
- **Analyst:** 15 checks, 14 ✔ and 1 ✘ (the README:40 citation, since fixed).
  - `server.py:1935-1938`, `:1959`, `:689-707`
  - `cli.py:304-306`, `:603`
  - `options.js:3`; `manifest.json:7`; `pyproject.toml:9`, `:13-15`
  - `scripts/ci-tldw-tests.sh:27`; `scripts/setup-hooks.sh:12`
  - `.github/CODEOWNERS`; `checks.yml:266`
  - `tasks/T-003.md:210`; `build-firefox.sh:10-12`; `README.md:53-58`
- **Architect:** 21 checks, 18 ✔ and 3 ✘ (B1, B2, W5, all since fixed).
  - `server.py:1935-1939`, `:704`, `:955-960`, `:1102-1110`, `:1017-1019`, `:1129-1149`, `:917-924`, `:705-707`
  - `claude_client.py:110`, `:135`, `:238-248`
  - `urls.py:12-19`, `:38`; `metadata.py:129-139`
  - `options.js:30`; `content.js:136`; `manifest.json:6-7`
  - ADR 0003's acceptance, verbatim
- **Data Engineer:** 16 checks, 14 ✔ and 2 ✘ (B1, B2, since fixed).
  - `txcache.py:57`, `:84`, `:95-99`
  - `server.py:767-770`, `:390-398`, `:1556-1558`, `:704-707`
  - `config.py:19`; `usage.py:186-189`; `cli.py:488`; `hygiene.py:173-188`
  - `claude_client.py:147-149`; `tests/conftest.py:26-37`; `background.js:10-11`
  - The re-check confirmed `summarize.py:17`, `:144`, `:316`, `:318` and `:338`.

## Checks
- **The suite:** `tests/test_docs_contracts.py` passes 57/57, and the full suite passes 717/717.
- **Scans over `docs/` and the new task files:**
  - no home-directory paths (the four patterns in `tests/test_docs_contracts.py:857-858`, written here in bracket form: `/[U]sers/`, `/[h]ome/`, `C:\[U]sers`, `-[U]sers-`);
  - no template-placeholder text.

## Round 1 sign-offs
- [x] Analyst — yes (PASS WITH ITEMS)
- [x] Architect — yes (PASS)
- [x] Data Engineer — yes (PASS)
