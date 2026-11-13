# Testing

How tldw is tested: the levels, the tools, the gates, the test data, and how a change proves it did not break a documented contract (the regression-safety check, `AGENTS.md` § 7).

**Descriptive**, as of 2026-11-12: every statement here describes what the repository does today, with its source. Where the code does not settle something, it says `UNKNOWN — needs human confirmation`.

## At a glance

| | |
|---|---|
| Test runner | pytest (`pyproject.toml:13`, `dev = ["pytest>=8"]`; config at `pyproject.toml:23-32`) |
| Suite size | 717 tests: 660 before this change, plus 57 in `tests/test_docs_contracts.py` (JUnit count, 2026-11-12) |
| JavaScript | No JS test runner. The extension's real functions run in node, driven from pytest (`tests/test_extension_render.py`, `tests/js/*.js`) |
| End-to-end tool | None. N/A: there is no browser automation tool in the repository |
| Contract-test tool | None separate. `tests/test_server.py` runs the real HTTP server; `tests/test_docs_contracts.py` checks `docs/contracts.md` against the code |
| CI | GitHub Actions, `.github/workflows/checks.yml`, job `tldw` ("tldw tests (pytest + extension)", `checks.yml:115-129`), which runs `sh scripts/ci-tldw-tests.sh run` |
| Gate | Full pass, zero skips, at least `MIN_TESTS=717` tests (`scripts/ci-tldw-tests.sh:27`). No coverage-percentage gate |

## Test levels

### Unit (pytest)
One file per module under `tests/`. Each one calls the module's functions directly, with collaborators replaced by `monkeypatch`. Collected counts (JUnit, 2026-11-12):

| File | Tests | Module under test |
|---|---|---|
| `tests/test_ask.py` | 11 | `ask.py` |
| `tests/test_audio.py` | 53 | `audio.py` |
| `tests/test_claude_client.py` | 52 | `claude_client.py` |
| `tests/test_cli.py` | 29 | `cli.py` |
| `tests/test_config.py` | 4 | `config.py` |
| `tests/test_core.py` | 6 | `core.py` |
| `tests/test_hygiene.py` | 20 | `hygiene.py`, `tldw cache` |
| `tests/test_metadata.py` | 20 | `metadata.py` |
| `tests/test_naming.py` | 10 | `naming.py` |
| `tests/test_overlays.py` | 7 | `overlays.py` |
| `tests/test_proc.py` | 21 | `proc.py` |
| `tests/test_spans.py` | 22 | `spans.py` |
| `tests/test_summarize.py` | 28 | `summarize.py` |
| `tests/test_textmode.py` | 3 | `textmode.py` |
| `tests/test_timing.py` | 26 | `timing.py` |
| `tests/test_transcript.py` | 34 | `transcript.py` |
| `tests/test_urls.py` | 18 | `urls.py` |
| `tests/test_usage.py` | 18 | `usage.py`, `tldw usage` |
| `tests/test_videomode.py` | 21 | `videomode.py` |
| `tests/test_whisper_transcribe.py` | 35 | `whisper_transcribe.py` |
| `tests/test_isolation.py` | 6 | the isolation guards themselves (below) |

No unit test runs a real `claude`, `yt-dlp`, `ffmpeg`, Piper or faster-whisper. They are stubbed, and `tests/conftest.py` makes a forgotten stub fail (see Isolation).

### Integration and contract: `tests/test_server.py` (210 tests)
- **What it runs.** The `srv` fixture (`tests/test_server.py:34-43`) starts the real `server._Server`, a `ThreadingHTTPServer` subclass (`src/youtube_tldw/server.py:1924`). It binds to `127.0.0.1` on port 0 and runs `serve_forever` on a daemon thread. Tests call it over real HTTP with `urllib.request.urlopen` (`_req`, `tests/test_server.py:53-63`).
- **What that proves.** Routing, auth (`401`), body limits (`413`), the slot pools (`429`), CORS preflight, NDJSON framing and the error shapes are tested through the same code path the extension uses. Only the model, yt-dlp and Piper behind the handlers are faked.
- **Its level.** This is the suite's contract level for the HTTP API. There is no separate contract-test tool such as Pact or schemathesis, and no OpenAPI file.

### Extension: node harnesses driven from pytest (`tests/test_extension_render.py`, 6 tests)
The harnesses run the extension's **real** functions, cut out of the source files, not copies.
- **`tests/js/render_checks.js`** (`test_answer_rendering_escaping_and_citations`, `tests/test_extension_render.py:22`)
  - Cuts functions out of `content.js` by literal start/end markers (`grab`, `tests/js/render_checks.js:7-15`) and `eval`s them.
  - Checks HTML escaping in `renderSummary` and `renderAnswer`, keyboard handling (`fromModal`), `closePolicy`, `sanitizeField`, audio helpers and `trimmedNote` escaping.
  - Runs a few structural checks against `background.js` and `options.js`, with comments stripped. One of them checks that `WHISPER_POLICIES` in `options.js` matches `background.js` (`tests/js/render_checks.js:422-432`).
- **`tests/js/page_transcript_checks.js`** (`test_reading_the_transcript_from_the_page`, `:35`)
  - Runs the real page-reading code against a small fake DOM that matches by tag, class, id and attribute.
  - The YouTube selectors (`transcript-segment-view-model`, `ytd-engagement-panel-section-list-renderer`, `ytd-video-description-transcript-section-renderer`, `#visibility-button`) are therefore exercised, not stubbed (`tests/js/page_transcript_checks.js:105-196`).
- **`tests/js/probe_checks.js`** (`test_the_cache_check_before_reading_the_page`, `:57`)
  - Runs `startSummarize`, `probeOutcome` and `handleProbe` inside `vm.createContext` sandboxes (`tests/js/probe_checks.js:61-85`), with a fake port, page and `fetch`.
  - Covers the old-server 404 treated as a miss, and the `httpError`, `askError` and `speakError` mappings (`:191-197`).
- **`test_extension_scripts_parse`** (`:47`, parametrized over 3 files) runs `node --check` on `content.js`, `background.js` and `options.js`.

**Coupling to watch.** The harnesses find code by literal source markers, comments included. Renaming or reflowing one of those lines breaks a `grab()`-based check, which reports "start marker not found" (`tests/js/render_checks.js:7-15`). Not every check fails that loudly: the bare `src.indexOf` slices (`tests/js/render_checks.js:137`, `:155`, `:165`) return -1 on a missing marker and can pass, and renaming the `PANEL_*_MS` constants silently drops the speed-up replacements in `tests/js/page_transcript_checks.js:96-98` (FU-11, [T-010](../tasks/T-010.md)). `tests/test_claude_client.py:284` (`test_extension_model_lists_match_the_server_allowlist`) likewise matches the literal `const MODELS = [` in `background.js` and `options.js`. `docs/contracts.md` lists these markers as contracts.

### Procedure: hook and CI-script fixture suites (`scripts/test/*.sh`, 16 suites)
These are shell fixture suites for the git hooks and CI scripts (`.githooks/`, `scripts/*.sh`). CI's `scripts` job runs each one by name (`.github/workflows/checks.yml:30-109`), and `scripts/test/test-ci-wiring.sh` fails if a suite isn't wired in. They test the procedure gates, not tldw. `scripts/test/test-ci-tldw-tests.sh` (32 cases) tests the floor and the zero-skip check of `ci-tldw-tests.sh` itself.

### Docs-contract check: `tests/test_docs_contracts.py` (57 tests)
- **What it checks.**
  - It extracts 23 categories of contract from the code: routes, methods, NDJSON types, `partial` kinds, request and response keys, port messages and names, status codes, CLI subcommands, flags and exit codes, env vars, config keys, storage keys, home paths, temp prefixes, executables, `urlopen` sites, model downloads, manifest permissions, page selectors and thread starts.
  - Each item must appear in `docs/contracts.md` as an exact backticked token.
  - **Reverse checks:** any route, env var or storage key listed in the doc's tables but gone from the code fails.
- **Its own guards.**
  - Every extractor has a count floor (`FLOORS`), so a broken extractor can't find zero and pass.
  - `EXCLUDED` maps every deliberate omission to a reason; an empty reason or a stale entry fails.
  - Every `querySelector` receiver must be classified as page or non-page.
  - A negative test removes `/ask/stream` from a copy of the doc, adds a route that doesn't exist, and asserts that both are reported.
- **Where its conventions live.** The module docstring documents the token conventions and how a category's section is found (by heading).
- **The leak check.** The file also holds the deterministic leak check for `docs/` and new task files: home-directory paths, the secret patterns read from `scripts/ci-checks.sh`, and the template-placeholder marker.

### End-to-end: N/A
There is no browser automation tool in the repository, so no test loads the extension in a real browser against a real YouTube page. Manual browser checks, made by driving `content.js` on a real watch page, support a change but **never gate one**: they are not repeatable in CI. Whatever a manual check finds is turned into an automated test at one of the levels above.

### Security and dependency scans
- **Secret scan.**
  - `.githooks/pre-commit` scans staged content (`.githooks/pre-commit:89-104`).
  - `scripts/ci-checks.sh` scans the diff in CI with the same patterns (`secret_patterns`, `scripts/ci-checks.sh:129`).
- **Hash-locked installs.** The CI test job installs only from `scripts/requirements-ci.txt`, with `--require-hashes --only-binary :all:` (`scripts/ci-tldw-tests.sh:79-80`). It then installs tldw with `--no-deps --no-build-isolation` (`:82`), so nothing unpinned is fetched. CI has no secrets (`checks.yml:119-124`, `permissions: contents: read`, `persist-credentials: false`).
- **Nothing else.** There is no SAST tool, no dependency-vulnerability audit (no pip-audit, Dependabot or CodeQL), and no license scan. `shellcheck` runs on the scripts (`checks.yml:46`), but it is a linter, not a security scan.
- **Negative security tests, at the unit and contract level:**
  - token required: `test_summarize_requires_token`, `test_preview_requires_token`, `test_speak_requires_token`, `test_ask_requires_token`, `test_stopping_needs_a_token`, `test_the_probe_needs_a_token`;
  - a web origin is refused: `test_preflight_rejects_web_origin`;
  - an unknown model is refused: `tests/test_server.py:733`;
  - the token file is created 0600: `tests/test_server.py:781`;
  - HTML escaping of model output: `render_checks.js`.

## Isolation (`tests/conftest.py`)
Every test runs with these autouse fixtures. Each guard exists because the thing it prevents once happened (`tests/conftest.py:1-12`).
- **`_isolated_home`** (`tests/conftest.py:46`):
  - redirects every `Path.home()`-derived module constant listed in `HOME_PATHS` (`:26-37`) into the test's `tmp_path`;
  - sets `HOME` and `USERPROFILE`, and points `tempfile.tempdir` at a scratch dir (`:58`);
  - deletes every `TLDW_*`, `DISABLE_PROMPT_CACHING*`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `HF_HOME` and `HF_HUB_CACHE` from the environment (`:61-73`);
  - puts `claude` and `yt-dlp` stubs first on `PATH`; they exit 97 with "tests must not run the real …" (`:77-84`).
- **`_fresh_server_state`** (`:88`):
  - empties the server's in-memory registries and gives each test fresh slot pools (`:114`);
  - after the test, waits up to 10 s for background work (prefetch, transcription) to finish, then **fails** the test if any is still running (`:124-129`), or if a slot was leaked or given back twice (`:130-135`).
- **`tests/test_isolation.py`** guards the guards:
  - `test_conftest_covers_every_home_path` (`:18`) fails when `src/` gains a `X = Path.home() / …` that `HOME_PATHS` doesn't list;
  - the other tests check that the paths really point inside the test dir, that the real `claude` and `yt-dlp` can't run, that no `TLDW_*` leaks in, and that the real config isn't read.
- **Thread exceptions fail the test.** `pyproject.toml:30-32` turns `PytestUnhandledThreadExceptionWarning` into an error, so an assertion that fires inside a handler thread is not silently lost.

## Coverage gates
There are **no coverage-percentage gates** at any level: no coverage tool is configured. The only automated gates are these, all in `scripts/ci-tldw-tests.sh`:
1. pytest must exit 0;
2. the JUnit report must show **0 failures, 0 errors and 0 skips** (`scripts/ci-tldw-tests.sh:58-62`);
3. at least `MIN_TESTS` (717) tests must run (`:27`, `:59-60`), so a deleted or uncollected test file fails CI.

`MIN_TESTS` is raised by hand when tests are added, and `scripts/test/test-ci-tldw-tests.sh:185` fails if it ever drops below 717. Beyond that, "every contract has a guarding test" is enforced by review (Test Engineer redlines in `.claude/agents/sdet.md`) and by the mapping table below, not by a tool.

## Skips: `skipif(node)` versus CI's zero-skip rule
`tests/test_extension_render.py` marks all of its tests `skipif(shutil.which("node") is None)` (`:21`, `:34`, `:45`, `:56`). That covers 6 tests, counting the 3 parametrized parse checks.
- **Locally**, a machine without node skips them, and pytest still exits 0.
- **In CI**, `ci-tldw-tests.sh` fails if `node` is missing (`:76`), before anything installs, and fails on **any** skip in the report (`:58-62`).

So the extension tests can never pass CI by being skipped. Locally, run with `-rs` (`ci-tldw-tests.sh:85` does) to see whether anything was skipped. No `skip`, `xfail` or `todo` markers exist besides these four `skipif`s (grep of `tests/*.py`, 2026-11-12).

## Flake policy
Flakes are bugs: fix the test or delete it. **Never re-run until green.** CI has no retry step; the job runs pytest once (`scripts/ci-tldw-tests.sh:85`).

The suite removes the usual sources of flakiness by construction:
- servers bind port 0;
- background work must finish inside the test (`_fresh_server_state`);
- timing races are driven through seams instead of sleeps. For example, `claude_client._Timer` exists so tests can fire the watchdog on command (`src/youtube_tldw/claude_client.py:294-296`).

**Known flake:** `tests/test_claude_client.py::test_a_silent_claude_is_killed_at_its_deadline` depends on wall-clock time (a 0.5 s deadline, `tests/test_claude_client.py:443`, that a real fake-CLI process must beat) and failed once in five full runs on 2026-11-12. Tracked in [T-010](../tasks/T-010.md) (FU-12); fix it, don't re-run it.

A test that depends on wall-clock time, the network or random IDs without such a seam is a redline (`.claude/agents/sdet.md`).

## Test data
- **Everything is invented or synthetic.**
  - Model output, metadata and transcripts are built inline in each test, for example `_summary()` at `tests/test_server.py:46-50`.
  - `tests/fixtures/` holds two caption files, `auto_captions.vtt` and `manual.srt`, with invented generic sentences.
- **No factories, seeded environments or databases.** Each test writes the files it needs under its own `tmp_path` home (Isolation).
- **No production data.**
  - Nothing is read from the real `~/.cache/youtube-tldw` or `~/.config/youtube-tldw`, and conftest makes that impossible (`tests/test_isolation.py:30`, `:54`).
  - Real transcripts, summaries, usage rows, tokens and session IDs from a developer's machine must never be copied into `tests/fixtures/` or a test.
  - A fixture taken from a real failure is rewritten first: invented words, invented 11-character video IDs such as `AAAAAAAAAAA` (as in `tests/js/probe_checks.js:176`), no real titles or channels, no token-shaped strings. Its timing and structure, which are what the test is about, are kept.
- **An existing exception.** `tests/test_server.py` uses one well-known public video ID throughout, starting at `tests/test_server.py:48`. It does not come from anyone's caches; it predates this rule and is left as it is (FU-8, [T-011](../tasks/T-011.md)).

## Mutation practice
There is **no mutation-testing tool**.
- **What stands in for one.** Every bug fix carries a regression test that **fails before the fix and passes after** (`.claude/agents/sdet.md`, redlines). The author shows it failing against the unfixed code before committing the fix.
- **How the suite has been tested this way.** Several tests were rewritten after real regressions passed the old ones:
  - `tests/js/probe_checks.js:6-8` replaced source regexes that four real regressions passed;
  - `pyproject.toml:26-29` records four `pytest.fail` guards that were "silently decorative".
- **The docs-contract test checks itself.** `test_the_checks_catch_a_missing_and_a_stale_route` breaks a copy of the doc on purpose and asserts that the failure names the item.

## Regression-safety check (`AGENTS.md` § 7)
**The rule:** a change that touches a contract listed in `docs/contracts.md` must:
1. update `docs/contracts.md` in the same PR;
2. pass a test **at the level that guards that contract**: contract (`test_server.py`), extension harness, or the docs-contract check. A green unit suite alone does not prove a contract still holds;
3. if the contract is marked **unguarded** below, add its guarding test in that same PR.

**How to find a contract's guard.** Find the `docs/contracts.md` section in the table below, then run the tests listed for it. To find the tests for one item, grep the item in `tests/`: `grep -rn -- '/ask/stream' tests/`.

| `docs/contracts.md` section | Guarding tests | Level | Unguarded parts (follow-up) |
|---|---|---|---|
| HTTP routes, methods, auth | `tests/test_server.py`: every route is called over real HTTP (`/summarize/stream`, `/summarize/cached`, `/segments/stream`, `/speak`, `/speak/stream`, `/preview`, `/ask/stream`, `/transcribe/stop`, `/health`, `/voices`, buffered `/summarize`); `*_requires_token`; `test_preflight_allows_extension_origin` (`:186`), `test_preflight_rejects_web_origin` (`:792`); `test_docs_contracts.py[routes, http_methods]` | contract | No test asserts the `404 {"error":"not found"}` answer for an unknown path, or the stdlib 501 for an unhandled method (FU-1, [T-010](../tasks/T-010.md)) |
| Request and response bodies, error shapes | `test_server.py`: `test_oversized_body` (`:151`), `test_summarize_accepts_a_transcript_sized_body_but_not_an_endless_one` (`:159`), `test_a_bad_body_to_the_stop_route_is_a_400` (`:1907`), `test_a_bad_probe_is_a_400` (`:2228`), unknown-model 400 (`:733`), `test_summarize_stream_error_is_in_band` (`:222`); `test_docs_contracts.py[request_keys, response_keys]` | contract | Chunked bodies → `400` and a missing `Content-Length` → `411` (FU-10, [T-010](../tasks/T-010.md)) |
| Request and response headers; server-side cross-cutting invariants | `test_server.py`: the token tests (`*_requires_token`), CORS (`:111`, `:186-192`, `:792-797`), body caps (`:151`, `:159`), token-file mode (`:771-790`, POSIX only) | contract | The loopback refusal, `X-Content-Type-Options`/`Cache-Control`, and the caption host allowlist (FU-10, [T-010](../tasks/T-010.md)) |
| Extension → server HTTP calls | `tests/js/probe_checks.js` (the `/summarize/cached` probe and its 404→miss); `test_docs_contracts.py[routes]` (every called path exists) | extension harness | The request bodies and headers `background.js` and `options.js` send for the other routes (FU-2, FU-5, [T-010](../tasks/T-010.md)) |
| Limits and slot pools | `test_server.py`: `test_busy_returns_429` (`:176`), `test_each_route_counts_against_its_own_limit` (`:2524`), `test_the_two_limits_say_different_things` (`:2542`), the prefetch-reserve tests (`:2663-2686`); conftest's leak and double-release check | contract | — |
| NDJSON stream events and `partial` kinds | `test_server.py`: `test_summarize_stream_emits_progress_then_result` (`:205`), `test_summarize_stream_sends_the_summary_as_it_is_written` (`:689`), `test_segments_stream` (`:234`), `test_speak_stream_*` (`:299-350`), `test_ask_streams_progress_then_deltas` (`:515`); every emitted type occurs in `test_server.py`; `test_docs_contracts.py[ndjson_types, partial_kinds]`, `test_background_reads_only_ndjson_types_the_server_emits` | contract | — |
| Extension message protocol (port, `tabs.sendMessage`) | `tests/js/probe_checks.js` (`summarize`, `probeCache`, `probe`, `result`, `error`); `tests/js/render_checks.js` (`stopTranscribe`, `needsTranscription`, the refresh flag); `test_docs_contracts.py[port_message_types, port_names]` (inventory only) | extension harness | The speak, ask, segments and preview message flows (`speakProgress`, `audioChunk`, `audioEnd`, `askDelta`, `askDone`, `segmentAdded`, `segmentsDone`, `getSegments`, `previewError`), and `TLDW_INVOKE`/`TLDW_ERROR`, have no behavioural test (FU-2, [T-010](../tasks/T-010.md)) |
| Status → message mapping (`httpError`, `askError`, `speakError`, `showError`) | `tests/js/probe_checks.js:191-197`; `test_docs_contracts.py[status_codes]` | extension harness | — |
| CLI subcommands, flags, exit codes | `tests/test_cli.py`, `tests/test_config.py` (`config set/get`), `tests/test_usage.py` (`usage --file/--by-video`), `tests/test_hygiene.py:218-245` (`cache clean`, `--dry-run`); `test_docs_contracts.py[cli_*]` | unit | No test of the `serve` parser through `cli.main` (`--host`, `--port`, `--token`, `--allow-origin`, `--llm-cmd`, the `TLDW_TOKEN` fallback), of `config unset`, or of exit code `130` (FU-3, [T-010](../tasks/T-010.md)) |
| Environment variables | `TLDW_LLM_CMD` (`test_claude_client.py`, `test_server.py:1104`); `TLDW_OUTPUT_DIR` (`test_config.py:23-36`); `TLDW_YTDLP_COOKIES` (`test_metadata.py:130-150`); `DISABLE_PROMPT_CACHING` (`test_claude_client.py:376-421`); `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` (`test_server.py:2899-2921`); `HF_HUB_CACHE` (`test_hygiene.py:229`); `test_docs_contracts.py[env_vars]` (both directions) | unit | `TLDW_TOKEN`, `HF_HOME` and `HF_HUB_DOWNLOAD_TIMEOUT` have no test (FU-4, [T-010](../tasks/T-010.md)) |
| Config keys | `test_config.py`; `test_cli.py:326-342` (`test_a_setting_the_server_would_ignore_is_refused`, `test_valid_settings_are_written`); `test_isolation.py:54`; `test_docs_contracts.py[config_keys]` | unit | — |
| On-disk stores and home paths | `test_isolation.py:18` (every `Path.home()` constant is redirected); per-store tests in `test_server.py`, `test_usage.py`, `test_hygiene.py`; token file mode (`test_server.py:771-790`); `test_docs_contracts.py[home_paths, temp_prefixes]` | unit and contract | — |
| `chrome.storage.local` keys and defaults | `test_docs_contracts.py[storage_keys]` (inventory, both directions); `render_checks.js` (`closePolicy`, sponsor counters) | inventory plus a partial harness | `options.js` load/save (`load`, the save handler at `extension/options.js:124-141`) and the differing `DEFAULTS` in `options.js` and `background.js` have no behavioural test (FU-5, [T-010](../tasks/T-010.md)) |
| Lists copied from the server | `MODELS`: `test_claude_client.py:284`; `WHISPER_POLICIES`: options vs worker only (`render_checks.js:422-432`) | unit / harness | `WHISPER_POLICIES` isn't compared with `server.py:66`; `VOICE_FALLBACK`/`VOICE_OPTIONS` aren't compared with `audio.VOICES` (FU-6, [T-010](../tasks/T-010.md)) |
| Manifests and the Firefox build | `test_docs_contracts.py[manifest_permissions]` (inventory only) | inventory | **Unguarded:** both manifests (versions kept equal, `host_permissions` tied to port 8765), and `extension/build-firefox.sh`'s copy list (FU-7, [T-010](../tasks/T-010.md)) |
| YouTube page contract (selectors, transcript panel) | `tests/js/page_transcript_checks.js` (segment rows, panel, transcript button, `#visibility-button`, `meta[itemprop=…]`); `test_docs_contracts.py[page_selectors]` | extension harness | `video.html5-main-video` (seek and play) has no harness test (FU-2, [T-010](../tasks/T-010.md)) |
| Subprocess integrations and the model I/O contract | `test_proc.py`, `test_claude_client.py` (stream-json parsing, watchdog, env), `test_metadata.py`, `test_videomode.py`, `test_audio.py`, `test_whisper_transcribe.py`; `test_isolation.py:36-46` (real binaries refused); `test_docs_contracts.py[executables, urlopen_sites, model_download_sites]` | unit | Against stubs only. A real `claude`, `yt-dlp` or `ffmpeg` release that changes its output is caught only by use, never in CI (by design: no network, no quota) |
| Background jobs and threads | conftest's "background work outlived the test" check; `test_server.py` transcription and prefetch tests (`:1541-1664`, `:2663-2976`); `test_docs_contracts.py[thread_starts]` | contract | — |
| Text the tests depend on (source markers) | `grab()` fails on a missing marker (`tests/js/render_checks.js:7-15`) | harness | The bare `indexOf` slices (`render_checks.js:137`, `:155`, `:165`) and the `PANEL_*_MS` replacements (`page_transcript_checks.js:96-98`) don't fail on a missing marker (FU-11, [T-010](../tasks/T-010.md)) |
| Keyboard and accessibility baseline | `render_checks.js:84-114` (`fromModal`, the hotkey shield and its removal, no `keydown` handler on the chat input), `render_checks.js:402` (the stop button's `aria-label`) | extension harness | Escape, Enter/Shift+Enter, the Tab trap, the dialog semantics and the `aria-live` regions have no test (FU-2, [T-010](../tasks/T-010.md)) |
| Public module entry points | the unit suites import and call them (`test_cli.py`, `test_server.py`, `test_claude_client.py` and the rest) | unit | No test pins the signatures as such; a rename fails only where a test calls it |
| Cross-cutting data invariants | per-store tests in `test_server.py`, `test_usage.py`, `test_hygiene.py`; `test_isolation.py:18` | unit and contract | As listed per store in `docs/data-model.md` |
| Interface text (`"trimmed video"`, `" (cached)"`) | `render_checks.js:325-327` (trimmed note escaped) | harness | The `" (cached)"` header suffix (`extension/content.js:930`) has no test (FU-2, [T-010](../tasks/T-010.md)) |

## What to run before a commit
From the repository root, in the dev venv:
1. **The whole tldw suite:** `.venv/bin/python -m pytest -p no:cacheprovider -rs`.
   - Expect 0 failures and 0 skips, and at least 717 tests.
   - It includes `tests/test_docs_contracts.py`, so any change to a contract surface also needs `docs/contracts.md` updated.
   - Never run a subset alone before committing a contract-touching change.
2. **The extension tests are not optional.** With node installed, `-rs` must show no skips; without node, the extension tests did not run.
3. **When you added tests,** raise `MIN_TESTS` in `scripts/ci-tldw-tests.sh` to the new total, and keep `scripts/test/test-ci-tldw-tests.sh` passing: `sh scripts/test/test-ci-tldw-tests.sh`.
4. **When you touched hooks or `scripts/`,** run the matching `scripts/test/*.sh` suite, or all 16.
5. **Before pushing,** run `sh scripts/ci-pr-checks.sh origin/<target> HEAD`, which mirrors CI's procedure checks.

## Follow-ups
- FU-1: no test for the unknown-route `404` (`server.py:980`, `:1017-1019`) or the `501` on an unhandled method (sent by the stdlib `BaseHTTPRequestHandler`; it has no line in `server.py`). → [T-010](../tasks/T-010.md)
- FU-2: no behavioural test of these extension message flows:
  - the speak, ask, segments and preview flows;
  - `TLDW_INVOKE` and `TLDW_ERROR`;
  - `video.html5-main-video`;
  - the `" (cached)"` suffix. → [T-010](../tasks/T-010.md)
- FU-3: no test of the `serve` CLI path through `cli.main`, of `config unset`, or of exit code 130. → [T-010](../tasks/T-010.md)
- FU-4: no test of `TLDW_TOKEN`, `HF_HOME` or `HF_HUB_DOWNLOAD_TIMEOUT`. → [T-010](../tasks/T-010.md)
- FU-5: no behavioural test of `options.js` load/save, or of the two `DEFAULTS` objects that differ. → [T-010](../tasks/T-010.md)
- FU-6: `WHISPER_POLICIES` is not compared with the server's; `VOICE_FALLBACK`/`VOICE_OPTIONS` are not compared with `audio.VOICES`. → [T-010](../tasks/T-010.md)
- FU-7: the manifests (version equality, `host_permissions` coupled to 8765) and the copy list in `extension/build-firefox.sh` are unguarded. → [T-010](../tasks/T-010.md)
- FU-8: `tests/test_server.py` uses a well-known public video ID; replace it with an invented one. → [T-011](../tasks/T-011.md)
- FU-9: no coverage tool, dependency-vulnerability audit or SAST. This records the current state; it is not a decision to add one. → [T-017](../tasks/T-017.md)
- FU-10: no test of the loopback refusal, chunked → `400`, missing `Content-Length` → `411`, the constant response headers (`X-Content-Type-Options`, `Cache-Control`) or the caption host allowlist. → [T-010](../tasks/T-010.md)
- FU-11: the bare `src.indexOf` slices in `tests/js/render_checks.js` and the `PANEL_*_MS` replacements in `tests/js/page_transcript_checks.js` don't fail on a missing marker. → [T-010](../tasks/T-010.md)
- FU-12: `test_a_silent_claude_is_killed_at_its_deadline` is a wall-clock flake. → [T-010](../tasks/T-010.md)

## How this was verified
- **Counts.** Ran `.venv/bin/python -m pytest -p no:cacheprovider -rs --junitxml=…` on 2026-11-12. That gave 717 collected: 691 passed, and 26 failed only because `docs/contracts.md` didn't exist yet. Per-file counts come from the JUnit `classname`s.
- **Fixtures and CI.** Read `tests/conftest.py`, `tests/test_isolation.py`, `tests/test_extension_render.py`, the three `tests/js/*.js` harnesses, the `srv` fixture in `tests/test_server.py`, `scripts/ci-tldw-tests.sh`, `.github/workflows/checks.yml`, `.githooks/pre-commit`, `scripts/ci-checks.sh` and `pyproject.toml`.
- **Skips.** Grepped `tests/*.py` for `skipif|pytest.skip|xfail`.
- **The mapping table.** Built by grepping `tests/` for every route, NDJSON type, port-message type, env var, storage key and selector that `tests/test_docs_contracts.py` extracts. "Unguarded" means the grep found no test that exercises the item's behaviour; an occurrence only in `test_docs_contracts.py` counts as inventory, not as a guard.
- **Coverage tools.** Checked for coverage, mutation and vulnerability-scan tooling in `pyproject.toml`, `scripts/requirements-ci.txt` and `.github/`; there is none.

## Update this doc when
- a test file is added or removed under `tests/` or `tests/js/`, or a file's count changes much;
- `tests/conftest.py` or `tests/test_isolation.py` changes;
- `scripts/ci-tldw-tests.sh` changes: `MIN_TESTS`, the skip rule, or the install flags;
- `.github/workflows/checks.yml` changes its `tldw` or `scripts` job;
- the hooks' or `scripts/ci-checks.sh`'s secret patterns change;
- `pyproject.toml`'s pytest config changes;
- a contract section is added to `docs/contracts.md`, or a test is added that guards one marked unguarded here;
- a coverage, mutation, end-to-end or scanning tool is added.
