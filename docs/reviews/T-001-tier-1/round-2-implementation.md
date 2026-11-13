# Review: chore/docs-tier1 — Round 2, Implementation (T-001 Tier 1 drafts)

**Status:** PASS WITH ITEMS. Backend and Frontend each blocked the draft on two wrong facts. Both passed after one fix pass, re-checked against the code. UX passed with four warnings, all fixed and confirmed. Each reviewer was a fresh agent, read only its own brief, re-checked the drafts against the source, and ran no state-changing git commands.

The two items still open from Round 1 were closed before this round began:
- testing.md's body text now cites each `FU-n` with its task inline (FU-1…7 → T-010, FU-8 → T-011, FU-9 → T-017).
- data-model.md's opening line names yt-dlp's own cache among the stores other tools write.

## Blockers (all fixed)
- [x] **Backend B1.** `api.md` § 11 said `whisper` has an underscore form that skips `_check`. It has none (`cli.py:319-320`, `:337`). Fixed: only `whisper_model` and `whisper_max_minutes`, typed with underscores, skip it.
- [x] **Backend B2.** The `claude` row in `contracts.md` said "Not installed → 502" for every call. That holds only for buffered calls (`claude_client.py:598-599`, `:634-635`). Streamed calls raise `FileNotFoundError` from `Popen` (`claude_client.py:333-343`), which nothing catches, so they end in `500` `internal error`. It contradicted `api.md`. Fixed, and the two docs now agree.
- [x] **Frontend B1.** The `CLIENT_TIMEOUT_MS` row said the worker's budget always runs out first. With `whisper: "always"`, the worker allows 2,400,000 ms (`background.js:144-145`), but the panel gives up at 160,000 ms (`content.js:670`). Fixed in the row and in follow-up 10, and added to T-008.
- [x] **Frontend B2.** The `ping` row said every open port is pinged. The preview port has no ping and no panel timer (`content.js:1421-1438`). Fixed, and a `/preview` (panel) row was added to the timeout table.

## Warnings
- [x] **Backend:**
  - the slot block's own `_validate` and pool choice (`server.py:1026-1033`) added to the route recipe and the dispatch row;
  - the order of checks for `/speak*`: slot before field validation, so a full pool answers `429` before `400`;
  - `_validate` checks only that `url` is a string, plus the in-stream `400` it leads to;
  - `tests/test_server.py:173` corrected to `:172`;
  - the `_STATUS` citation corrected to `server.py:711-717`.
- [x] **Frontend:**
  - `api.md`'s caller row reordered to match its citations;
  - `externally_connectable` added to the absent manifest keys;
  - two accessibility gaps added (Shift+Tab from the focused panel; the skip pill's missing focus ring), with T-009 items.
- [x] **UX:**
  - W1: the Options custom-backend row now says the command it names doesn't work, and the note joins the next-step exceptions (→ T-012);
  - W2: onboarding gives the working `tldw config set llm_cmd` form and `TLDW_LLM_CMD`;
  - W3: the "no captions, Whisper not installed" view added to the fixed-messages table (`content.js:798-808`);
  - W4: `docs/README.md` points new readers to onboarding.

## Suggestions
- **Applied (Backend):** the ordering line in `api.md` (only `/speak/stream` writes from two threads), and the prefetch's `keep_free=1` case.
- **Applied (UX S2):** the busy message on `/preview` → T-008.
- **Not applied, optional:**
  - Backend: mention port-in-use in `api.md` § 11 (`contracts.md` already has it).
  - Frontend: the pill ids' Kind column; an `audio.DEFAULT_VOICE` row; the `settledRows` clause.
  - UX S1: rewording the `.regen` gap. UX S3: a troubleshooting pointer in onboarding.

## Found during this round
- `tests/test_claude_client.py::test_a_silent_claude_is_killed_at_its_deadline` failed once in five full-suite runs on 2026-11-12. It passed alone and on every re-run. It gives a fake CLI 0.5 s to start (`tests/test_claude_client.py:438-447`). Recorded as an item in T-010. It is a pre-existing product test that this PR does not touch; Round 3 rules on it.
- `docs/reviews/T-001-tier-1/round-1-analysis.md` wrote the four home-path patterns literally, which tripped the committed leak check. They are now written in bracket form.

## Spot-checks (by reviewer, against the code)
- **Backend (first pass, 2 ✘ plus 3 citation ✘, all since fixed):**
  - `server.py`: `37-49`, `64-70`, `115-160`, `172-295`, `360-394`, `434-522`, `540-656`, `682-717`, `773-852`, `917-1166` (transport, CORS, auth, dispatchers, slot block, pools, body read, validation), and every handler from `1172` to `1971`;
  - `cli.py:276-283`, `:301-311`, `:314-367`, `:576-618`; `config.py:12`, `:44-56`;
  - `claude_client.py:1-11`, `:34`, `:100-111`, `:155-213`, `:236-254`, `:304-343`, `:370-392`, `:559-600`, `:615-646`;
  - `proc.py:42-63`; `ask.py:15-19`, `:52-64`, `:93-140`; `core.py:184-200`; `summarize.py:315-335`; `audio.py:59`;
  - `tests/test_server.py`: 18 lines, plus `:172`; `tests/test_claude_client.py:284-294`.
- **Backend (re-check, all ✔):** `cli.py:318-337`, `:349-352`; `claude_client.py:333-343`, `:577-579`, `:598-599`, `:634-635`; `summarize.py:324-326`, `:395`; `server.py:465-466`, `:562`, `:711-717`, `:1026-1035`, `:1153`, `:1355`, `:1463-1480`, `:1513-1515`, `:1653`, `:1670-1686`, `:1762`, `:1775-1786`; `tests/test_server.py:172`.
- **Frontend (first pass, 2 ✘ plus 1 citation ✘, all since fixed):**
  - `background.js`: 40 ranges from `8` to `581`, including `16-23`, `71-82`, `144-145`, `464`, `571-581`;
  - `content.js`: 80 ranges from `6` to `1935`, including `267-319`, `602-670`, `842-847`, `1108-1115`, `1421-1438`, `1738`;
  - `options.js:1-141`; `options.html:32-83`; both manifests; `build-firefox.sh:7-12`; `.gitignore:9`;
  - `server.py:66`, `:917-938`; `audio.py:44-58`, `:73-74`; `claude_client.py:164`;
  - `tests/js/render_checks.js:132-148`, `:284-287`, `:395-406`, `:544-547`; `probe_checks.js:194-203`; `page_transcript_checks.js:95-101`.
- **Frontend (re-check, all ✔):** `background.js:71`, `:108-511` (the seven caller lines), `:144-145`, `:464`; `content.js:267`, `:317`, `:602-615`, `:660`, `:670`, `:1048`, `:1051`, `:1108-1115`, `:1320`, `:1421-1438`, `:1490`, `:1738`; both manifests.
- **UX (first pass, 2 ✘, since fixed):**
  - `background.js` mappers `:440-447`, `:491-497`, `:571-581`, and 22 fixed-message lines;
  - `options.js:3`, `:60-63`, `:90`, `:105-107`, `:115`, `:140-141`;
  - every `"error"` emission in `server.py` (53 lines), including busy messages `:642-647` and limits `:560-561`;
  - `content.js`: panel messages, keyboard and focus (`:249-319`, `:366-372`), live regions and labels (`:692-1881`);
  - `README.md:43-84`, `:205-248`, `:428-441`; `pyproject.toml:6-25`; `cli.py:300-311`, `:332-367`, `:596-603`; `options.html:28-32`, `:78`; `manifest.json:7-10`; `extension/README.md:8-37`.
- **UX (re-check, all ✔):** `docs/contracts.md:818`, `:832`, `:861`; `docs/onboarding.md:13`; `docs/README.md:7`; `tasks/T-008.md:29`, each against the code it cites.

## Checks
- **The full suite:** 717 passed, apart from the one flaky run above. `tests/test_docs_contracts.py` passed 57/57.
- **All 16 `scripts/test/test-*.sh` suites:** passed.
- **The leak and placeholder checks over `docs/` and the new task files:** clean.

## Round 2 sign-offs
- [x] Backend — yes (PASS, after one fix pass)
- [x] Frontend — yes (PASS WITH ITEMS, after one fix pass; three optional suggestions open)
- [x] UX — yes (PASS)
