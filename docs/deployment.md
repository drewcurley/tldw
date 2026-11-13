# Deployment

> **Owner:** DevOps. **Task:** T-001.9. **Last verified against the code:** 2026-11-12, branch `chore/docs-tier1` at `7327c0b`.
>
> tldw has **no deployment target**. There is no hosted server, no staging, no production fleet. "Deploying" tldw means one person installing a version on their own machine, running `tldw serve` on loopback, and loading the browser extension from the same checkout. Everything below describes that, plus the CI and branch rules that decide what reaches the integration branch.

## 1. Environments

| Environment | What it is | How it differs | Source |
|---|---|---|---|
| **The user's machine** (the only runtime) | `tldw serve` bound to loopback, the browser extension, and the user's own model command (the `claude` CLI by default). All state is files under `~/.config/youtube-tldw/` and `~/.cache/youtube-tldw/`, plus `chrome.storage.local`. | It is the only place tldw runs. Nothing is shared between users: each runs their own server against their own model plan. | `server.py:1933-1939`; `extension/README.md` "Distribution"; `README.md:432-435` |
| **A development checkout** | The same machine, with an editable install (`pip install -e '.[dev]'`) in a virtualenv, running `pytest`. | The code runs from the checkout, so a `git checkout` changes what runs. Hooks are installed per worktree (§ 3). | `README.md:437-442`; `pyproject.toml:13-15` |
| **CI runners** | GitHub Actions, `ubuntu-24.04`, one fresh VM per job. | No secrets, no caches, no artifacts, `contents: read` only, no saved git credentials. It never runs a server against a model. | `.github/workflows/checks.yml:11-16`, `:27`, `:32-35`, `:111-128` |
| **This hackathon fork** | Repo `drewcurley/tldw`; integration branch `hackathon/demo`, protected by ruleset 27155529 (§ 8). `hackathon/demo` is thrown away after the demo and never merges into `main`. | Work lands on `hackathon/demo` through PRs. `main` keeps the released history and the tags. | `CLAUDE.md:18`; `tasks/T-003.md:18-19` |
| **Staging / production** | N/A — there is no hosted service to stage or promote to. The server refuses any non-loopback bind. | — | `server.py:1935-1939` |
| **Regional splits** | N/A — there is no hosted service, so there are no regions. | — | — |

## 2. Install (an end user)

Prerequisites, per `README.md:32-45`: Python 3.11+ (`pyproject.toml:9`), `yt-dlp` and `ffmpeg`/`ffprobe` on `PATH`, and a model backend (the `claude` CLI logged in, or a configured `llm_cmd`).

```bash
git clone https://github.com/drewcurley/tldw.git
cd tldw
pipx install .                          # recommended (README.md:57)
pipx inject youtube-tldw piper-tts      # optional: text-to-speech
```

- **`pipx install .` is not editable.** pipx installs a copy of the tree as it was at install time, so a later `git pull` or `git checkout` changes nothing until the package is reinstalled (`pipx install --force .`). This is standard pipx behaviour; this doc's rollback test (§ 10) used a plain venv, as its protocol requires, and did not exercise pipx.
- The README's alternative is a virtualenv with `pip install -e ".[tts]"` (`README.md:60-62`).
- Optional extras are `dev` (pytest), `tts` (piper-tts) and `whisper` (faster-whisper) (`pyproject.toml:13-15`).

Then:

1. **Start the server:** `tldw serve` (§ 4). It prints the token. See § 5 before you share a screen.
2. **Load the extension unpacked (Chromium):** `chrome://extensions` → Developer mode → **Load unpacked** → the `extension/` folder (`extension/README.md:21-24`). This applies to Chrome, Edge, Brave, Arc, Opera and Vivaldi (`README.md:428-430`).
3. **Firefox:** run `extension/build-firefox.sh`, which deletes and recreates `extension/dist-firefox/`, copies `background.js`, `content.js`, `options.js`, `options.html`, `icons/*.png`, and `manifest.firefox.json` as `manifest.json` (`extension/build-firefox.sh:7-12`). Load `dist-firefox/manifest.json` from `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on**, then in `about:addons` → the extension → **Permissions**, allow access to 127.0.0.1 if prompted (`extension/README.md:25-33`). A temporary add-on unloads when Firefox restarts. `dist-firefox/` is git-ignored (`.gitignore`, line `extension/dist-firefox/`). Any file not on the script's copy list is silently left out of the Firefox build.
4. **Configure:** open the extension's options and paste the token. The server URL defaults to `http://127.0.0.1:8765` (`extension/options.js:3`, `extension/background.js:10`).

## 3. Development install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

- `README.md:437-442` gives `pip install -e ".[dev]"` and `pytest`; the venv step is `README.md:61`.
- The extension tests shell out to `node` and skip when it is missing (`README.md:444-446`). CI fails on any skip (§ 7).
- **Hooks:** in this fork they are installed per worktree, `git config extensions.worktreeConfig true && git config --worktree core.hooksPath .githooks`. Never run `scripts/setup-hooks.sh` here (`CLAUDE.md:20`).
- `sh scripts/ci-pr-checks.sh origin/hackathon/demo HEAD` runs the same procedure gate a PR build runs (`scripts/ci-pr-checks.sh:1-20`), except that locally it uses your own copy of the runner, while a PR build takes the runner from the target branch.

## 4. Running the server

`tldw serve` is dispatched before argparse (`cli.py:596-608`). Its flags (`cli.py:301-311`):

| Flag | Default | Behaviour | Source |
|---|---|---|---|
| `--host` | `127.0.0.1` | Only `127.0.0.1`, `localhost` or `::1` are accepted. Anything else prints `error: Refusing to bind a non-loopback host…` and exits 1 (measured). | `server.py:1935-1939`; `cli.py:607-609` |
| `--port` | `8765` | Any port. Measured: `--port 8799` serves normally. A port already in use ends with an unhandled `OSError: [Errno 48] Address already in use` traceback and exit 1 (measured on macOS), not a friendly message. | `cli.py:305`; `server.py:1941` |
| `--token` | none | The bearer token. Wins over `TLDW_TOKEN` and the token file. | `cli.py:306`, `:603`; `server.py:698-699` |
| `--allow-origin` | none | Pins one exact `Origin`. Without it, any `chrome-extension://` origin is allowed. | `cli.py:307-308`; `server.py:917-924` |
| `--llm-cmd` | none | Sets `TLDW_LLM_CMD` for this process. | `cli.py:309-310`, `:599-600` |

- **Startup** prints the `listening on` line, the limits line, the credential-name lines for the `claude` CLI, any startup-cleanup notes, then the token (`server.py:1942-1963`). Startup cleanup runs after the bind and only removes stale transcription audio under the temp dir and voice files the allowlist can never select (`server.py:89-112`).
- **Stopping:** Ctrl-C (`server.py:1964-1971`). Request threads are daemon threads (`server.py:1925`), so in-flight requests die with the process. SIGTERM also stopped it in the rollback test.
- **A second, isolated instance** (verified in § 10): a separate `HOME` gives it its own token file, config and caches, because every store path is `Path.home()`-based (`server.py:689`, `config.py:12`, `usage.py:26`, `txcache.py:21-25`). A separate `TMPDIR` gives it its own transcription temp dirs (`hygiene.py:107`). A separate `--port` avoids the bind clash. Whether the `claude` CLI finds its login under a different `HOME` is `UNKNOWN — needs human confirmation`; the test made no model calls. Whether the extension can reach a non-8765 port is in § 11.

## 5. Secret management

There is no secret manager. The seeded T-001.9 criterion carries a template placeholder for one. tldw has exactly one secret of its own, the bearer token.

| Secret | Where it lives | Invariant | Blast radius | Source |
|---|---|---|---|---|
| tldw bearer token, persisted | `~/.config/youtube-tldw/token`, created with `secrets.token_urlsafe(32)` on first start, then `chmod 0600` (best effort; on Windows the chmod is effectively a no-op and profile ACLs protect it). Measured mode after creation: `-rw-------`. | Stable across restarts, so the extension is configured once. Owner-only on POSIX. | Every authenticated route. Anyone who reads it can drive the server, and the user's model plan with it. | `server.py:689-708`; `config.py:44-56` |
| Token file creation window | The file is written first and chmodded after, so for a moment it has the umask's mode. | — (weakness, recorded) | Another local user racing the first start. | `server.py:706-707` |
| tldw token, from the environment | `TLDW_TOKEN`. Used when `--token` is absent. Not written to the file. | Explicit token wins over the file. | Same as above. | `cli.py:603`; `server.py:698-699` |
| tldw token, from the command line | `--token`. It is in the process's argv, so `ps` shows it to every local user. | — (weakness, recorded) | Any local user. | `cli.py:306` |
| Token printed at startup | `tldw serve` prints `token: <value>` on **every** start, whichever source it came from. | — (weakness, recorded) | Screen shares, terminal scrollback, pasted logs and issues. Never paste raw `tldw serve` output. | `server.py:1959` |
| Token in the extension | `chrome.storage.local` key `token`, in plain text. Only `background.js` and `options.js` read it on purpose; `content.js`'s code never reads `token`, but the storage area isn't restricted to trusted contexts (no `setAccessLevel`), and `content.js`'s `onChanged` listener is handed the new token whenever Options saves one. | — (weakness, recorded: `architecture.md` § 7, items 6 and 13) | Anything that can read the browser profile, and the content-script world on the YouTube tab where the panel is open. | `extension/options.js:137`; `extension/background.js:44`; `extension/content.js:50-55` |
| The `claude` CLI's own login | The CLI's keychain login, `CLAUDE_CODE_OAUTH_TOKEN`, or `ANTHROPIC_API_KEY` in the environment `tldw serve` runs in. tldw never reads a keychain. At startup it prints only *which variables are set*, never their values. | tldw does not store or forward these. | The user's Claude plan or API billing. | `server.py:660-679`; `README.md:73-74` |
| CI | No secrets at all. | No job needs or receives a credential. | — | `checks.yml:11-16`, `:111-113`, `:129-130`; `scripts/ci-tldw-tests.sh:20-22` |

The commit and CI secret scan looks for AWS keys, private-key headers, GitHub tokens, Slack tokens and Anthropic/OpenAI-style keys (`scripts/ci-checks.sh:128-138`). It does not recognise a tldw token, which has no prefix.

## 6. Promotion path

```
feature branch (feat/ fix/ chore/ refactor/, cut from hackathon/demo)
  → PR targeting hackathon/demo
  → four required checks green, branch up to date (§ 8)
  → rebase-merge into hackathon/demo
  → push build on hackathon/demo
```

- `hackathon/demo` never merges into `main` (`CLAUDE.md:18`; `tasks/T-003.md:19`).
- **Releases live on `main`.** A release is a `chore: vX.Y.Z` commit that bumps the version (§ 9), then an annotated tag on that commit. Release commits: `6ae0839` (v1.1.0), `73bc1e4` (v1.2.0), `2aef5a9` (v1.3.0). All four tags (`v1.0.1`, `v1.1.0`, `v1.2.0`, `v1.3.0`) are annotated (`git cat-file -t` → `tag`) and are on origin. There are no GitHub Releases (the API returns 0).
- Users "promote" themselves: they pull, or check out a tag, and reinstall (§ 10).

## 7. CI pipeline

GitHub Actions workflow `procedure-checks` (`.github/workflows/checks.yml`). It runs on every `pull_request`, and on pushes to `main`, `master` and `hackathon/demo` (`checks.yml:21-25`). Never `pull_request_target` (`checks.yml:15-16`). Default token permissions are none (`checks.yml:27`). Every job has `contents: read`, `persist-credentials: false`, and actions pinned by commit SHA.

**The four required checks.** These names are exact, because the ruleset matches on them:

| Check name (exact) | Job | What it runs | Invariant | Blast radius | Source |
|---|---|---|---|---|---|
| `shell scripts (lint + test)` | `scripts` | A pinned, checksummed shellcheck over the hooks and `scripts/`, then the 15 `scripts/test/*.sh` fixture suites listed in the job. `test-deck.sh` runs in the `deck` job, which makes 16 in all. | A renamed job is a required check that never reports, and that blocks every PR. | Every PR into `hackathon/demo`. | `checks.yml:30-109` |
| `tldw tests (pytest + extension)` | `tldw` | `sh scripts/ci-tldw-tests.sh run …`: a hash-locked, binary-only venv install, tldw installed `--no-deps --no-build-isolation`, pytest, then a JUnit check. It fails on any failure, error or skip, or on fewer than `MIN_TESTS=723` tests (`scripts/ci-tldw-tests.sh:27`). | Same as above. The floor only goes up. | Same. | `checks.yml:115-127`; `scripts/ci-tldw-tests.sh:9-27`, `:77-82` |
| `overview deck (build + compare)` | `deck` | Asks the **target branch's** `scripts/ci-deck-needed.sh` whether to build. In this repo `scripts/build_deck.py` and `scripts/requirements-deck.txt` are absent, so the job installs nothing and `test-deck.sh` prints `SKIP:` and passes. | It must keep reporting success even with the deck gone, or every PR blocks (`ADOPTING.md:33-37`). | Same. | `checks.yml:131-185`; `scripts/test/test-deck.sh:56-60` |
| `procedure checks` | `procedure` | **On PRs: the trusted gate.** It takes `scripts/ci-pr-checks.sh` from the PR's *target* commit (`git cat-file blob "$target_sha:scripts/ci-pr-checks.sh"`). That runs the target's `ci-checks.sh` and `.placeholder-allow` against the PR, so a PR can't loosen the gate it is judged by. If the target has no runner, the PR's own copy runs, with a warning (bootstrap only). A non-regular file fails closed. **On pushes:** `ci-checks.sh` over the pushed range. The gates are: no template placeholders, the secret scan, a Task ID in every non-merge commit, and `unanimous-consensus: T-NNN` on commits touching `/docs/*`. The PR body must contain `## Task`, `## Summary`, `## Agent sign-offs`, `## Human review`, `## Risk` and `## Redlines`. | A gate change applies from the *next* PR on. | Same. | `checks.yml:187-272`; `scripts/ci-checks.sh:83-167`; `scripts/ci-pr-checks.sh:1-13` |

- **Not protected by the trusted gate:** the pipeline YAML itself. A PR's build runs the PR's own `checks.yml`, and `ci-pr-checks.sh` says code-owner review is what protects it (`scripts/ci-pr-checks.sh:12-13`). In this fork, code-owner review is **off** (ruleset `require_code_owner_review: false`; `.github/CODEOWNERS` header). So a PR that edits `checks.yml` is not mechanically stopped. This is recorded as a fact and left as it is.
- **Azure DevOps is inert.** `azure-pipelines.yml` mirrors the same jobs and triggers `main`, `master` and `hackathon/demo` (`azure-pipelines.yml:20-29`). No Azure pipeline is connected, and none of its checks are required (`tasks/T-003.md:67`). It is kept in step with `checks.yml`, which `scripts/test/test-ci-wiring.sh` checks.
- **No CD stage.** Nothing is built, published or deployed by CI: no package upload, no extension store upload, no tag creation.

## 8. Branch protection

**`hackathon/demo`: ruleset 27155529, "hackathon/demo protection (T-003)"**, read with `gh api repos/drewcurley/tldw/rulesets/27155529` on 2026-11-12. It is the repo's only ruleset.

| Setting | Value | Invariant | Blast radius |
|---|---|---|---|
| Target / enforcement | `refs/heads/hackathon/demo`, `active` | — | Every change to the integration branch. |
| Bypass | `bypass_actors: []`, `current_user_can_bypass: never` | Nobody, the owner included, merges around it. | — |
| Pull request required | `required_approving_review_count: 0` (GitHub forbids self-approval on a solo repo); `require_code_owner_review: false`; `dismiss_stale_reviews_on_push: false`; `require_last_push_approval: false`; `required_review_thread_resolution: false`; `require_extra_approval_for_unattributed_changes: true` (its effect with 0 approvals is `UNKNOWN — needs human confirmation`) | Changes arrive only through a PR. | — |
| Merge method | `allowed_merge_methods: ["rebase"]` | Rebase-only, so commit SHAs change on merge. Identify reviewed content by tree hash, not commit SHA. | Review artifacts that cite SHAs. |
| Required status checks | the four names in § 7, each with `integration_id: 15368`; `strict_required_status_checks_policy: true` (the branch must be up to date); `do_not_enforce_on_create: false` | Keep the job names byte-for-byte. Bring a branch up to date by rebasing it. | Every PR. |
| Force-push | blocked (`non_fast_forward`) | — | — |
| Deletion | blocked (`deletion`) | — | — |

Repo-wide, merge commits, squash and rebase are all enabled (`allow_merge_commit`, `allow_squash_merge` and `allow_rebase_merge` all `true`). The ruleset is what restricts `hackathon/demo` to rebase.

**`main` has no ruleset and no branch protection.** `gh api repos/drewcurley/tldw/rules/branches/main` returns `[]`, and `…/branches/main/protection` returns 404 "Branch not protected". What guards it is client-side only:
- the `pre-commit` hook blocks a direct commit on `main`, `master`, `production`, `release`, `trunk` or `hackathon/demo` (`.githooks/pre-commit:19-23`);
- the `pre-push` hook blocks a non-fast-forward push to the same list (`.githooks/pre-push:10-27`);
- `.claude/settings.json` asks before any `git push` naming those branches (`.claude/settings.json:21-29`).

All three are bypassable (`--no-verify`, or hooks not installed in a given checkout). Removing the ruleset at teardown: `tasks/T-003.md:214`.

## 9. Versions and tags

| Where | Value at HEAD | Source |
|---|---|---|
| `pyproject.toml` `version` | `1.3.0` | `pyproject.toml:7` |
| `youtube_tldw.__version__` (served by `/health`) | `1.3.0` | `src/youtube_tldw/__init__.py:3`; `server.py:974-976` |
| `extension/manifest.json` `version` | `1.3.0` | `extension/manifest.json:4` |
| `extension/manifest.firefox.json` `version` | `1.3.0` | `extension/manifest.firefox.json:4` |
| `uv.lock` (the `youtube-tldw` package entry) | `1.3.0` | `uv.lock:788-789` |

- **Invariant:** the four version fields, plus the `uv.lock` entry, move together in the release commit. Releases `2aef5a9`, `73bc1e4` and `6ae0839` each change exactly these five files.
- **Blast radius:** `/health`'s `version` is the only version any running component reports.
- **No test checks that they match.** A grep of `tests/` finds no version assertion.
- **The version only changes at a release.** HEAD is 28 commits past `v1.3.0` and still reports `1.3.0`. `/health`'s `version` therefore can't tell you which commit is installed. Record `git rev-parse --short HEAD` of the installed checkout instead.
- **Nothing checks that the extension matches the server.** The extension reads only `backend` from `/health` (`extension/options.js:52-65`) and never `version`. A rollback must move both halves, and the extension must be reloaded after any pull or checkout. Chrome keeps the old content script until you reload the extension *and* the YouTube tab (`extension/README.md:8-11`). For Firefox, re-run `build-firefox.sh` and reload the temporary add-on, because `dist-firefox/` is a copy (`extension/build-firefox.sh:8-12`).

## 10. Rollback

### Procedure (a user's install)

1. Stop `tldw serve` (Ctrl-C).
2. In the checkout: `git checkout <tag>`, for example `v1.3.0`.
3. Reinstall the server from that checkout: `pipx install --force .` (pipx), or `pip install -e .` in the venv (tested below). **The pipx path, the recommended install, is untested.** The tested rollback used a plain venv so that it stayed isolated from the owner's own pipx install (see the isolation protocol below), because `pipx install --force` installs into the user's shared pipx environment, not a scratch directory. A pipx rollback in a throwaway environment is still open → [T-015](../tasks/T-015.md).
4. Rebuild the Firefox copy if you use Firefox: `extension/build-firefox.sh`.
5. Reload the extension (`chrome://extensions` → reload, or reload the temporary add-on) **and** the YouTube tab.
6. `tldw serve`, then `curl -s http://127.0.0.1:8765/health`. Confirm the installed commit with `git rev-parse --short HEAD`, not with `version`.
7. To roll forward, do the same with the newer ref.

No store needs migrating or restoring: config, the token, caches and `usage.jsonl` are all read tolerantly by both versions tested (below). There is no backup or restore step, because there is no data that a rollback damages.

### Tested rollback record (2026-11-12)

**Isolation protocol.** The owner's install and data were not touched:
- a `git clone` of the repo into a scratch directory, `<scratch>` below;
- a plain venv, not pipx;
- `HOME=<scratch>/home` and `TMPDIR=<scratch>/tmp` for every `tldw` command;
- `TLDW_TOKEN`, `TLDW_LLM_CMD`, `TLDW_OUTPUT_DIR`, `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` unset;
- port 8799;
- **no model calls**: only `/health`, an unauthenticated `POST /transcribe/stop`, and `tldw config` / `tldw cache` / `tldw usage`;
- every server killed afterwards (no listener left on 8799);
- the real `~/.config/youtube-tldw` and `~/.cache/youtube-tldw` showed no modified entries afterwards.

Python 3.14.10, macOS.

```bash
git clone <repo> <scratch>/clone                       # at 7327c0b (chore/docs-tier1)
python3 -m venv <scratch>/venv
<scratch>/venv/bin/pip install -e <scratch>/clone
export HOME=<scratch>/home TMPDIR=<scratch>/tmp

# 1. HEAD
tldw serve --port 8799 > <scratch>/serve-head.log 2>&1 &
curl -s http://127.0.0.1:8799/health
# seed every store without a network or model call:
tldw config set whisper off
tldw config set whisper-max-minutes 30
tldw config set output-dir <scratch>/out
python -I -c '…txcache.put("AAAAAAAAAAA", <invented meta>, <2 invented cues>);
             txcache.put_summary("seedkey", {…}); txcache.put_segments("AAAAAAAAAAA-seed", …, []);
             usage.note_video("AAAAAAAAAAA", 60000, 120)'
tldw config get; tldw cache; tldw usage
# restart HEAD on the seeded stores, then stop it
git -C <scratch>/clone rev-parse --short HEAD          # 7327c0b

# 2. Roll back
git -C <scratch>/clone checkout v1.3.0
<scratch>/venv/bin/pip install -e <scratch>/clone
git -C <scratch>/clone rev-parse --short HEAD          # 2aef5a9
tldw serve --port 8799 > <scratch>/serve-v130.log 2>&1 &
curl -s http://127.0.0.1:8799/health
tldw config get; tldw cache; tldw usage

# 3. Roll forward
git -C <scratch>/clone checkout chore/docs-tier1
<scratch>/venv/bin/pip install -e <scratch>/clone
git -C <scratch>/clone rev-parse --short HEAD          # 7327c0b
tldw serve --port 8799 > <scratch>/serve-forward.log 2>&1 &
curl -s http://127.0.0.1:8799/health
```

**Results.** Each start printed `tldw serve listening on http://127.0.0.1:8799` and logged no traceback or error line. `/health` returned the same JSON at all three commits:

```json
{"ok": true, "name": "tldw", "version": "1.3.0", "backend": {"kind": "claude", "label": "Claude", "models": ["opus", "sonnet"], "streaming": true}}
```

| Check | HEAD `7327c0b` | `v1.3.0` `2aef5a9` on HEAD-written stores | Forward `7327c0b` |
|---|---|---|---|
| Starts, `listening on` line, `/health` 200 | yes | yes | yes |
| Reuses the token file (no new token) | created, mode `-rw-------` | reused | reused |
| `tldw config get` | 3 keys | the same 3 keys listed (v1.3.0 has no whisper code: `server.py` has 0 `whisper` matches at that tag, so 2 of the keys are inert) | 3 keys |
| `tldw cache` | exit 0; 1 transcript, 1 summary, 1 key-moments entry | **exit 1: `error: URL must start with https://`**. `cache` isn't a subcommand at v1.3.0 (`cli.py` at the tag dispatches only `config`, `usage` and `serve`), so it is parsed as a URL. | exit 0, the same entries |
| `tldw usage` | reads the 1 seeded row | reads it | reads it |
| HEAD-written transcript read by `txcache.get` in-process | hit | **hit**: v1.3.0 reads the `subtitles`/`auto_captions` that HEAD no longer writes with defaults | hit |
| `POST /transcribe/stop`, no token | 401 | **404**: route absent at v1.3.0 | 401 |

**What a rollback from HEAD to v1.3.0 changes.** These come from the code at the two refs. Only the rows marked "measured" were exercised.
- Server routes absent at v1.3.0: `/summarize/cached` and `/transcribe/stop`. Measured 404 for `/transcribe/stop`. The HEAD extension calls both (`extension/background.js:108`, `:231`). It treats any non-OK probe as a cache miss (`background.js:113-126`) and ignores a failed stop (`background.js:230-240`). A HEAD extension against a v1.3.0 server therefore degrades silently: every open re-summarizes, and Stop-transcription does nothing. This is one more reason a rollback moves both halves.
- **Summary cache keys differ.** HEAD adds the transcript `source` to the key (`server.py:748-770`). v1.3.0's key has no source (`server.py:278-290` at the tag). Summaries written by one version are misses for the other, so each re-opened video costs one model call after a rollback, and again after rolling forward.
- v1.3.0 has no on-disk key-moments store (`txcache.py` at the tag defines only `CACHE_DIR` and `SUMMARY_DIR`). HEAD's `segments/` files are ignored and are still there on roll-forward (measured).
- Startup cleanup (`hygiene.py`) doesn't exist at v1.3.0. HEAD's `voices/.spoken-tracking-since` marker is left alone, and HEAD reuses it on roll-forward.

**Extension rollback:** `UNKNOWN — needs human confirmation`. Reloading an older unpacked extension needs a throwaway browser profile, and that wasn't tested. Still open: whether `chrome.storage.local` keys written by a newer options page survive a downgrade and are read correctly, and whether a v1.3.0 extension works against a HEAD server.

## 11. Ops facts

| Fact | Invariant | Blast radius | Source |
|---|---|---|---|
| Default port `8765`, loopback only | The bind refuses any host other than `127.0.0.1`, `localhost` or `::1`. | The extension's defaults and host permissions assume it. | `cli.py:304-305`; `server.py:1933-1939` |
| `host_permissions` is exactly `http://127.0.0.1:8765/*` in **both** manifests | It is coupled to the default port and to the literal `127.0.0.1`. A `localhost` URL or any other port isn't covered. | Every background fetch, and the options page's `/health`, `/voices` and `/preview` fetches. | `extension/manifest.json:7`; `extension/manifest.firefox.json:7`; `extension/background.js:108-511`; `extension/options.js:53`, `:70`, `:97` |
| A custom `serverUrl` / port | **Server side, measured:** `--port 8799` serves normally. The server answers a CORS preflight from a `chrome-extension://` origin on any port (`OPTIONS` → 204 with `Access-Control-Allow-Origin` echoed, `Allow-Headers: authorization,content-type`, `Allow-Methods: POST,GET,OPTIONS`). The server never checks its own port. **Extension side:** `UNKNOWN — needs human confirmation`. Whether Chromium's extension fetches to a port outside `host_permissions` succeed on CORS alone, or fail, wasn't tested in a browser. It needs a browser, so it can't be settled in this docs pass → [T-015](../tasks/T-015.md). | Anyone running a second server or a non-default port. | `server.py:932-940`, `:966-970` |
| Firefox's origin | **Measured:** with no `--allow-origin`, an `Origin: moz-extension://…` request gets no `Access-Control-Allow-Origin` header, and neither does a web origin. Only the `chrome-extension://` prefix passes (`server.py:924`). Whether Firefox's extension fetches send that origin and are blocked, or are exempted by host permissions, is `UNKNOWN — needs human confirmation`. `--allow-origin` can pin one exact origin. | Firefox users. | `server.py:917-924`; `cli.py:307-308` |
| Logs | No log file and no rotation. Status lines go to **stdout** through `print`. Per-request lines (the request line as sent, so the method, the path including any query string, and the HTTP version, plus the status; no body or headers, so never the token. The extension sends no query strings) go to **stderr** through the stdlib's default `log_message`. Model calls are appended to `~/.config/youtube-tldw/usage.jsonl` (owner-only, append-only; `tldw cache clean` doesn't touch it). | Capture both streams when you report a problem, and strip the `token:` line first. | `server.py:962-963`, `:1942-1963`; `usage.py:26`, `:184-189` |
| Observability, alerting, on-call, SLOs | N/A: there is no hosted service and no on-call. The only telemetry is the local stdout/stderr and `usage.jsonl`, read with `tldw usage`. | — | `.claude/agents/devops.md` "What you own" |
| Concurrency limits | Printed at startup: 6 claude calls (prefetch never takes the last 2), 2 reading aloud, 1 transcription. | A busy server answers 429 with which limit it hit. | `server.py:654-656`, `:1943` |

## 12. Categories that don't apply

- **Feature-flag policy:** N/A. tldw has no feature-flag system, so there is nothing to require or retire. The switches that behave like flags are operator config and env vars: `TLDW_LLM_CMD` / `llm_cmd`, `whisper`, `whisper_max_minutes`, `whisper_model`, `ytdlp_cookies` and `TLDW_OUTPUT_DIR` / `output_dir`. The extension's options are `chrome.storage.local` keys. All of them are inventoried in `contracts.md`. New behaviour on the HTTP API goes behind an opt-in body flag or a new route (`api.md`).
- **Progressive rollout (canary / regional ramp / cohort):** N/A. There is no fleet, no update channel and no auto-update. Each user picks a ref and installs it themselves, so a "cohort" is one machine. The nearest thing to a ramp is the integration branch being used before a release tag is cut on `main`.
- **Infrastructure as code, cost estimates, break-glass production access, backup restore:** N/A. There is no infrastructure, no production, and no hosted data.
- **Extension store publishing:** N/A. The extension is loaded unpacked, or as a temporary Firefox add-on; it isn't published to any store.

## Follow-ups

Each of these is tracked in a `proposed` follow-up task, named at the end of the item:
1. **The token is printed on every start** (`server.py:1959`). It is a screen-share and log-paste leak. → [T-004](../tasks/T-004.md)
2. **The token file chmod race:** it is written before it's chmodded (`server.py:706-707`). → [T-004](../tasks/T-004.md)
3. **`--token` is visible in `ps`** to other local users (`cli.py:306`). → [T-004](../tasks/T-004.md)
4. **The extension against a non-8765 port:** `UNKNOWN`. It needs a browser test with a custom `serverUrl`, so it can't be settled in this docs pass. → [T-015](../tasks/T-015.md)
5. **Firefox's origin:** `UNKNOWN`. A `moz-extension://` origin gets no CORS header by default (measured). Does the Firefox build work without `--allow-origin`? → [T-015](../tasks/T-015.md)
6. **Extension rollback:** `UNKNOWN`. It needs a throwaway browser profile: a downgrade with HEAD-written `chrome.storage.local`, and the old extension against a new server. → [T-015](../tasks/T-015.md)
7. **The `claude` CLI login under a separate `HOME`:** `UNKNOWN`. It affects the "second isolated instance" pattern. → [T-015](../tasks/T-015.md)
8. **The ruleset's `require_extra_approval_for_unattributed_changes: true` with 0 approvals:** its effect is `UNKNOWN`. → [T-015](../tasks/T-015.md)
9. **No test that the five version fields match** (`pyproject.toml`, `__init__.py`, both manifests, `uv.lock`). → [T-010](../tasks/T-010.md)
10. **A busy port gives a raw `OSError` traceback**, not a message (`server.py:1941`). → [T-013](../tasks/T-013.md)
11. **`main` has no server-side protection**, only client-side hooks and Claude settings. → [T-017](../tasks/T-017.md)
12. **The pipeline YAML isn't protected by the trusted gate**, and code-owner review is off in this fork, so a PR can change `checks.yml` unchecked (`scripts/ci-pr-checks.sh:12-13`). → [T-017](../tasks/T-017.md)
13. **The README and the options page give `tldw config llm_cmd "…"`** (`README.md:76-77`, `:81`; `extension/options.js:61-63`). Measured, that form prints the usage line and exits 1, without setting anything. The working form is `tldw config set llm_cmd "…"` (`cli.py:349-366`). → [T-012](../tasks/T-012.md)
14. **`build-firefox.sh` silently omits** any extension file that isn't on its copy list (`extension/build-firefox.sh:10-12`). → [T-010](../tasks/T-010.md)

## How this was verified

- **Read:**
  - `src/youtube_tldw/server.py`: the token, `serve`, the CORS, auth and route handlers, startup hygiene, `_summary_key`;
  - `cli.py`: `main`, `_serve_parser`, `_run_config`, `_run_cache`;
  - `config.py`, `usage.py`, `txcache.py`, `hygiene.py`;
  - both manifests, `background.js`, `options.js`, `build-firefox.sh`, `extension/README.md`, `README.md`, `pyproject.toml`, `uv.lock`;
  - `.github/workflows/checks.yml`, `azure-pipelines.yml`, `scripts/ci-checks.sh`, `ci-pr-checks.sh`, `ci-tldw-tests.sh`, `scripts/test/test-deck.sh`;
  - `.githooks/pre-commit`, `.githooks/pre-push`, `.claude/settings.json`, `.github/CODEOWNERS`, `CLAUDE.md`, `ADOPTING.md`, `tasks/T-003.md`.
- **Read from GitHub on 2026-11-12:**
  - `gh api repos/drewcurley/tldw/rulesets/27155529` (every field in § 8 is copied from it);
  - `gh api repos/drewcurley/tldw/rulesets` (one ruleset);
  - `gh api repos/drewcurley/tldw/rules/branches/main` (`[]`);
  - `gh api repos/drewcurley/tldw/branches/main/protection` (404);
  - the repo's merge settings;
  - `gh api …/releases` (0).
- **Git, read-only:**
  - `git tag -l -n1`, and `git cat-file -t` on each tag;
  - `git show --stat` of the release commits;
  - `git show v1.3.0:<file>` to compare routes, cache modules and CLI dispatch with HEAD;
  - `git ls-remote --tags origin`.
- **The rollback in § 10** was run by hand in an isolated clone, as described there. Only the commands, the `listening on` line and the `/health` JSON are recorded. Serve output was captured to files and grepped, never copied.
- **The CORS, preflight, `moz-extension` origin, busy-port and non-loopback results** in §§ 4 and 11 were measured against that isolated server on port 8799.

## Update this doc when

- `serve()`, `load_or_create_token`, `TOKEN_FILE`, `_origin_allowed`, `_cors_headers`, `do_OPTIONS` or `do_GET` `/health` change (`src/youtube_tldw/server.py`);
- `_serve_parser`, or the `serve` / `config` / `cache` dispatch in `main`, changes (`src/youtube_tldw/cli.py`);
- `permissions`, `host_permissions`, `version`, `background` or `options_*` change in either manifest, or `DEFAULTS.serverUrl` changes (`extension/background.js`, `extension/options.js`);
- `extension/build-firefox.sh` changes;
- `.github/workflows/checks.yml` changes: a job name, the triggers, the trusted-gate block, or the deck gate. So do `azure-pipelines.yml`, `scripts/ci-checks.sh`, `scripts/ci-pr-checks.sh`, `scripts/ci-tldw-tests.sh` (`MIN_TESTS`), `.githooks/pre-commit` and `.githooks/pre-push`;
- the GitHub ruleset 27155529 is edited or removed, or `main` gains a ruleset or branch protection;
- a release is cut: the version fields move, a new tag appears, or a store format changes. Re-run § 10 against the new tag;
- the install instructions in `README.md` or `extension/README.md` change, or `pyproject.toml` extras change.
