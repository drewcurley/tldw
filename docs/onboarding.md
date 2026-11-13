# Onboarding — tldw

The path from a fresh clone to a running tldw, and then to a first pull request under this repo's playbook. It is written for a hackathon audience: you can run the product first and read the process second.

tldw summarizes the YouTube video you are watching. The browser extension in `extension/` calls a small server, `tldw serve`, that runs on your own machine, binds loopback only (`src/youtube_tldw/server.py:1933-1938`), and uses your own model plan (`README.md:205-208`, `extension/README.md:110-115`). There is no hosted service to sign up for.

## First 30 minutes

### Before you start

- **Python 3.11 or newer** (`pyproject.toml:9`) and **pipx**, which the recommended install uses (`README.md:56-57`).
- **`yt-dlp`** and **`ffmpeg`/`ffprobe`** on your `PATH`. On macOS: `brew install yt-dlp ffmpeg` (`README.md:43-45`).
- **A model backend.** The default is the `claude` CLI, installed and logged in once; any command that reads a prompt on stdin and prints the answer also works (`README.md:65-84`). To use another command, run `tldw config set llm_cmd "<command>"`, or start the server with `TLDW_LLM_CMD="<command>" tldw serve`. The README's form without `set` prints a usage line and changes nothing (`src/youtube_tldw/cli.py:349`, `:365-367`; tracked as [T-012](../tasks/T-012.md)). Summaries and questions are real model calls on *your* plan (`README.md:243-248`).
- **A browser:** Chrome, Edge, Brave, Arc, Opera or Vivaldi for the unpacked extension, or Firefox through a build script (`README.md:428-430`).

### 1. Get the code

```bash
git clone https://github.com/drewcurley/tldw.git
cd tldw
git switch hackathon/demo
```

`README.md:53-54` gives the clone. `hackathon/demo` is this fork's integration branch, and demo work starts from it (`CLAUDE.md:18`).

### 2. Install

```bash
pipx install .
pipx inject youtube-tldw piper-tts    # optional: the "Listen" voices
```

This is the recommended install (`README.md:56-58`). It gives you a global `tldw` command. pipx installs a *copy* of the checkout, so code you change later doesn't reach that command until you reinstall it. For development, use the virtualenv path under [Dev setup](#dev-setup) instead.

### 3. Start the server

```bash
tldw serve
```

It listens on `http://127.0.0.1:8765` (`src/youtube_tldw/cli.py:304-305`) and prints a bearer token (`src/youtube_tldw/server.py:1959`). The token is saved to `~/.config/youtube-tldw/token` and reused on every restart, so you configure the extension once (`src/youtube_tldw/server.py:689-707`, `:1960-1962`). Leave the server running; it logs each request below the startup lines.

> ⚠️ **`tldw serve` prints the token — don't screen-share it or paste it into issues.**
>
> - The token line appears **every time** the server starts (`src/youtube_tldw/server.py:1959`). At a hackathon, start the server before you share your screen, or clear the terminal first.
> - Never paste the server's startup output anywhere: not into an issue, a PR, a chat, or a doc. In examples, write `<your-token>`.
> - The token stays valid across restarts. If it leaks, replace it: stop the server, delete `~/.config/youtube-tldw/token`, start the server again (it generates and saves a new one, `src/youtube_tldw/server.py:700-707`), and paste the new token into the extension's Options. `--token` or `TLDW_TOKEN` override the file (`src/youtube_tldw/cli.py:306`, `:603`), so a token set that way is replaced where you set it.

### 4. Load the extension

- **Chrome, Edge, Brave, Arc, Opera, Vivaldi:** open `chrome://extensions` (or `edge://extensions`), turn on **Developer mode**, choose **Load unpacked**, and select the `extension/` folder (`extension/README.md:21-24`).
- **Firefox:** run `extension/build-firefox.sh`, then load `extension/dist-firefox/manifest.json` from `about:debugging` → **This Firefox** → **Load Temporary Add-on**, and allow access to `127.0.0.1` if Firefox asks (`extension/README.md:25-33`).

### 5. Paste the token into Options

Open the extension's options (Chrome: **Details** → **Extension options**; Firefox: `about:addons` → **Preferences**), paste the token into **Token**, and click **Save** (`extension/README.md:35-37`, `extension/options.html:28-32`, `:78`).

Leave **Server URL** at its default, `http://127.0.0.1:8765` (`extension/options.js:3`). The Chrome manifest only grants the extension access to `http://127.0.0.1:8765/*` (`extension/manifest.json:7`), so the default port is the one that works without further changes.

### 6. Open a YouTube video

Open any YouTube `/watch` page and click the **TL;DW** toolbar button, whose tooltip is "TL;DW this video" (`extension/manifest.json:9-10`, `extension/README.md:41`). The summary streams into a panel on the page, and from there you can listen, play the key moments, or ask questions (`extension/README.md:42-70`).

**If something looks stale,** reload the extension at `chrome://extensions` *and* the YouTube tab. Chrome keeps the old content script otherwise (`extension/README.md:8-11`).

## Read order

Read `docs/README.md` first: it lists every doc and what it is for. Then:

1. `architecture.md`: the components, how data flows, and the trust boundaries.
2. `stack.md`: what tldw is built with, and why.
3. `domain.md`: what the product reasons about (videos, transcripts, summaries, key moments, questions), how those relate, and which word the docs use for each. Keep `glossary.md` open beside it: every term the docs use, defined once.
4. Then by area:
   - **any change at all:** `contracts.md`, the full inventory of what a change can break;
   - **routes and the server:** `api.md`;
   - **anything on disk or in `chrome.storage.local`:** `data-model.md`;
   - **tests, and proving a change broke nothing:** `testing.md`;
   - **install, CI, versions and rollback:** `deployment.md`;
   - **past decisions:** `decisions/`;
   - **who owns what:** `ownership.md`;
   - **review artifacts:** `reviews/README.md`;
   - **the panel, the Options page and who uses them:** `ux.md`;
   - **something stopped working on your machine:** `runbooks/README.md`, which starts from the message you see.

The 13 flat files in `docs/reviews/` are pre-playbook history, not part of this read.

## Dev setup

Use a virtualenv with an editable install, so the `tldw` command runs your checkout's code:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

`README.md:61` gives the virtualenv, and `README.md:439-441` the dev install. The `dev` extra adds pytest (`pyproject.toml:13`). Two optional extras exist: `tts` for Piper voices and `whisper` for transcribing captionless videos (`pyproject.toml:14-15`).

If you also installed with pipx, check which `tldw` your shell finds before testing a change, because only the virtualenv's runs your edits.

**Node** is needed to run the extension's tests (see [Running tests](#running-tests)).

**Changing the extension:**
- After any change, reload the extension and the YouTube tab (`extension/README.md:8-11`).
- For Firefox, re-run `extension/build-firefox.sh`. It copies a fixed list of files (`extension/build-firefox.sh:10-12`), so a new extension file must be added to that list or the Firefox build silently leaves it out.

## Hooks: installed per worktree

The git hooks in `.githooks/` enforce the procedure locally:
- `pre-commit`: no direct commit to a protected branch (`hackathon/demo` included), no template placeholders, and a basic secret scan;
- `commit-msg`: a task ID in every commit, and `unanimous-consensus: T-NNN` on any commit touching `docs/`;
- `pre-push`: no force-push to a protected branch.

The full table is in `.githooks/README.md:17-28`.

**In this repo, install them for your worktree only:**

```bash
git config extensions.worktreeConfig true && git config --worktree core.hooksPath .githooks
```

**Never run `scripts/setup-hooks.sh` here** (`CLAUDE.md:20`). That script sets `core.hooksPath` in the repo-wide git config (`scripts/setup-hooks.sh:12`), which every worktree of the clone shares, including the owner's everyday checkout, which must keep running no hooks (`tasks/T-003.md:128-131`). The starter's own instructions (`AGENTS.md:392-394`, `.githooks/README.md:5-9`) say to run it; for this repo, `CLAUDE.md` overrides that.

Check the install with `git config --worktree --get core.hooksPath`, which should print `.githooks` (`tasks/T-003.md:142`). The hook files are committed executable, so no `chmod` is needed.

`--no-verify` skips the hooks but not CI, which runs the same checks (`.githooks/README.md:30-32`).

## Running tests

| What | Command | Notes |
|---|---|---|
| tldw's suite | `pytest` | From the repo root, in the dev virtualenv (`README.md:439-441`). `pyproject.toml:23-25` sets `testpaths` and `-q`. Every test runs against a fake home directory and with the real `claude` and `yt-dlp` replaced by stand-ins that refuse to run, so the suite never touches your real config or caches and never spends model quota (`tests/conftest.py:1-12`, `:46-84`). |
| The extension's tests | (part of `pytest`) | They run the extension's real JavaScript in `node`. Without `node` they **skip** (`tests/test_extension_render.py:21`, `:34`, `:45`, `:56`), and CI fails on any skip (`scripts/ci-tldw-tests.sh:9-12`). Install node and make sure nothing is skipped. |
| CI's full-pass rule, locally | `pytest --junitxml=junit.xml` then `sh scripts/ci-tldw-tests.sh check junit.xml` | Fails on any failure, error or skip, or fewer tests than the floor `MIN_TESTS` (`scripts/ci-tldw-tests.sh:16-17`, `:27`, `:58-62`). Don't use the script's `run` mode locally: its lock file holds Linux wheels for CI's runner only (`scripts/requirements-ci.txt:5`). |
| The playbook's shell suites | `for s in scripts/test/test-*.sh; do sh "$s"; done` | The 16 fixture suites for the hooks and CI scripts (`tasks/T-003.md:137`). CI lints the same scripts with a pinned shellcheck (`.githooks/README.md:36-47`). |
| The procedure gates | `sh scripts/ci-checks.sh origin/hackathon/demo HEAD` | Placeholders, secrets, task IDs and the docs consensus line, over your branch's commits (`scripts/ci-checks.sh:10-16`). |
| The PR gate, as CI runs it | `sh scripts/ci-pr-checks.sh origin/hackathon/demo HEAD` | Runs the *target* branch's copy of the gates against your branch, the way a PR build does (`scripts/ci-pr-checks.sh:2-8`). Run it before you push. |

What each test level covers, and which tests guard which contract, is in `testing.md`.

## Reading CI output

CI is GitHub Actions, in `.github/workflows/checks.yml`; `azure-pipelines.yml` is kept but inert (`tasks/T-003.md:60-67`). It runs on every PR, and on pushes to `hackathon/demo`, `main` and `master` (`.github/workflows/checks.yml:21-25`). There are four jobs, and all four are required checks on `hackathon/demo` (`tasks/T-003.md:209-213`):

| Check name | What it runs | What a failure looks like |
|---|---|---|
| `shell scripts (lint + test)` | shellcheck, then every `scripts/test/*.sh` suite, one step each (`.github/workflows/checks.yml:30-109`) | The failing step is named after its suite. Re-run that one suite locally with `sh scripts/test/<suite>.sh`. |
| `tldw tests (pytest + extension)` | `scripts/ci-tldw-tests.sh run`: a hash-locked install, then pytest, then the report check (`.github/workflows/checks.yml:115-127`) | The last line is a summary, `tests=… failures=… errors=… skipped=…` (`scripts/ci-tldw-tests.sh:57`), followed on failure by `not a full pass:` and the reasons (`scripts/ci-tldw-tests.sh:62`). A skip counts as a failure here. |
| `overview deck (build + compare)` | The starter's deck suite (`.github/workflows/checks.yml:131-185`) | This fork has no deck, so the suite prints `SKIP:` and passes (`tasks/T-003.md:51`). |
| `procedure checks` | The target branch's gates against the PR (`.github/workflows/checks.yml:187-259`), then the PR-body check (`:261-272`) | Each problem is printed on its own line: an unreplaced placeholder, a possible secret, a commit missing its task ID, or a `docs/` commit missing `unanimous-consensus: T-NNN` (`scripts/ci-checks.sh:119`, `:135`, `:149`, `:167`). A PR body missing a required heading prints `pull request body is missing section:` and the heading (`.github/workflows/checks.yml:266-268`). |

Fix the cause. Never loosen a gate, an allowlist or a test, (`AGENTS.md:371`), and never re-run a red job until it happens to go green: a re-run that passes is not a pass (`tasks/T-003.md:144`).

## Your first PR under this playbook

Read the `AGENTS.md` Quick Summary (`AGENTS.md:13-31`) first. The full flow is `AGENTS.md` § 7 (`AGENTS.md:340-379`); in this repo it runs like this:

1. **Task file.** Copy `tasks/_TEMPLATE.md` to `tasks/T-NNN.md` with the next free ID, and set `state: proposed` (`AGENTS.md:62`, `:351`; `tasks/README.md`). If an existing task covers the work, use that one instead (`AGENTS.md:61`). Which `proposed` task is the best first one is the owner's call; `tasks/INDEX.md` lists them.
2. **Branch** from `hackathon/demo`, named `feat/`, `fix/`, `chore/` or `refactor/` plus a short name (`AGENTS.md:344-348`, `CLAUDE.md:18`). Set the task to `in-review`, fill in `branch`, and add a line to its activity log.
3. **Plan review.** Write the plan in the task file: what changes, which files, and the approach. Run it through the review the change type needs (`AGENTS.md` § 5, `AGENTS.md:154-168`): the 3 rounds of § 2 for anything non-trivial. Get sign-off before writing code. Then set `state: in-progress`. The hard gate is "no code without a reviewed plan" (`AGENTS.md:434`).
4. **Build** the approved plan.
5. **Build review.** The same rounds again, on the build. The artifact goes in `docs/reviews/<folder>/` (see `reviews/README.md`). Because it is under `docs/`, the commit that adds it needs `unanimous-consensus: T-NNN` (`.githooks/commit-msg:24-37`).
6. **Tests.** Automated tests for every code path, run in CI. Then the **regression-safety check**: diff your change against `contracts.md`, and if it touches anything listed there, update `contracts.md` (and `api.md` or `data-model.md`) in the same PR (`AGENTS.md:358-365`; `testing.md` makes it concrete).
7. **Commit.** Put the task ID in every commit message. Include the task-file update in the same commit: `state: completed`, `branch`, `review_artifact`, and an activity-log line (`AGENTS.md:366`).
8. **Push**, then **open the PR into `hackathon/demo`**, never into `main` (`CLAUDE.md:18`). Use `.github/pull_request_template.md`; CI fails a body missing any of its six required headings, `## Task`, `## Summary`, `## Agent sign-offs`, `## Human review`, `## Risk` and `## Redlines` (`.github/workflows/checks.yml:266`). Put the PR number in the task's `pr:` field as the final commit (`AGENTS.md:368`).
9. **CI must pass.** All four checks are required, and the branch must be up to date with `hackathon/demo` (`tasks/T-003.md:211-212`). Get up to date by rebasing your branch, never with GitHub's merge-style update (`tasks/T-001.md:269`). Merges are rebase-only, and 0 approvals are required, because this is a solo repo (`tasks/T-003.md:210`; see `ownership.md`).

Whether hackathon participants without push access can contribute through fork PRs into `hackathon/demo` is `UNKNOWN — needs human confirmation`. The workflow's `pull_request` trigger does run on PRs from forks, with no secrets (`.github/workflows/checks.yml:15-16`, `:21-22`).

## Who to ask, channels, and 30/60/90

- **Who to ask:** the owner, `@drewcurley`, for everything. See `ownership.md`.
- **Communication channels: N/A.** tldw is a solo repo with one maintainer (`.github/CODEOWNERS:7-10`). It has no team chat, mailing list or meeting cadence, and the repo defines none. Whether GitHub issues are the place for hackathon questions is `UNKNOWN — needs human confirmation`. If you do open an issue, never paste the token or `tldw serve`'s output into it.
- **30/60/90-day expectations: N/A.** There is no team to ramp a new engineer into. The repo has one maintainer, and this hackathon branch is thrown away after the demo and never merges into `main` (`AGENTS.md:9`, `CLAUDE.md:18`). A second maintainer joining would be the moment to write one (see `ownership.md`).

## Where to look next

| For | Read |
|---|---|
| The whole procedure in 60 seconds | `AGENTS.md` Quick Summary (`AGENTS.md:13-31`) |
| What to do at the start of every session | `AGENTS.md` § 0 (`AGENTS.md:35-76`) |
| The 9 agents and the 3 review rounds | `AGENTS.md` § 1 and § 2 (`AGENTS.md:80-110`); the role briefs in `.claude/agents/*.md` |
| Who wins a disagreement | `AGENTS.md` § 3 (`AGENTS.md:114-134`) |
| Which review a change needs | `AGENTS.md` § 5 (`AGENTS.md:154-168`) |
| The rules every doc is held to | `AGENTS.md` § 6.0 (`AGENTS.md:178-189`) |
| Branches, commits, CI and hooks | `AGENTS.md` § 7 (`AGENTS.md:340-403`) |
| The hard gates | `AGENTS.md` § 8 (`AGENTS.md:430-445`) |
| The review artifact format | `AGENTS.md` § 9 (`AGENTS.md:449-482`) |
| How the procedure scales down for one person | `AGENTS.md` § 12 (`AGENTS.md:524-549`) |
| The PR body | `.github/pull_request_template.md` |
| Who reviews which path | `.github/CODEOWNERS`, and `ownership.md` |
| What each hook blocks | `.githooks/README.md` |
| The task ledger's rules | `tasks/README.md` |
| Branch protection, and solo versus team | `ADOPTING.md` § 5 (`ADOPTING.md:186-246`) |

## How this was verified

- **The first-30-minutes path** was taken from `README.md` (requirements, install, clone, model backends) and `extension/README.md` (setup and use). Each step was checked against the code it describes: the serve parser's defaults (`src/youtube_tldw/cli.py:300-311`), the dispatch that passes `TLDW_TOKEN` (`src/youtube_tldw/cli.py:596-603`), `serve()` (`src/youtube_tldw/server.py:1933-1971`), `load_or_create_token` (`src/youtube_tldw/server.py:689-707`), the options form (`extension/options.html:28-32`, `:78`), the defaults (`extension/options.js:3`), and the manifest (`extension/manifest.json`). The steps were not run end to end in a fresh environment for this doc.
- **The token warning** comes from `server.py:1959`, which prints the token on every start, and `:700-707`, which shows a deleted token file being regenerated.
- **Hooks:** `CLAUDE.md:20`, `scripts/setup-hooks.sh:12`, `tasks/T-003.md:128-131` and `:142`, and `git ls-files -s .githooks`, which shows the three hooks at mode `100755`. `git config --worktree --get core.hooksPath` in this worktree prints `.githooks`.
- **Tests and CI:** read `.github/workflows/checks.yml` in full, `scripts/ci-tldw-tests.sh`, the headers of `scripts/ci-checks.sh` and `scripts/ci-pr-checks.sh`, the error lines in `scripts/ci-checks.sh` (`grep -n 'err "'`), `scripts/requirements-ci.txt:1-12`, `tests/conftest.py:1-90`, and the `skipif` lines in `tests/test_extension_render.py`. `ls scripts/test` lists 16 suites. The required checks and merge settings come from the ruleset record in `tasks/T-003.md:209-215`; the GitHub settings themselves were not read for this doc.
- **The first-PR flow:** `AGENTS.md` § 7, adapted by `CLAUDE.md:15-20`, plus `tasks/README.md` and `tasks/T-001.md:269`.
- **Owner-specific setup** (keychain aliases, a second demo server, the owner's own install) was left out on purpose.

## Update this doc when

- `README.md`'s Requirements, Install or Development sections, or `extension/README.md`'s Setup, change;
- `src/youtube_tldw/cli.py`'s `_serve_parser` or `main` dispatch, `serve()` or `load_or_create_token` in `src/youtube_tldw/server.py`, or `TOKEN_FILE`, change;
- `extension/manifest.json`'s `host_permissions` or `action`, `extension/options.html`, `extension/options.js`'s `DEFAULTS`, or `extension/build-firefox.sh` change;
- `pyproject.toml`'s Python version, extras or pytest settings change;
- `.githooks/*`, `CLAUDE.md`'s "This repo" notes, or `scripts/setup-hooks.sh` change;
- `.github/workflows/checks.yml`'s jobs, job names or PR-body headings, `scripts/ci-*.sh`, or `MIN_TESTS` change, or a suite is added to `scripts/test/`;
- the `hackathon/demo` ruleset changes;
- `AGENTS.md` § 0, § 5, § 7 or § 8 changes;
- a doc in the read order (including `domain.md`, `ux.md`, `glossary.md` or `runbooks/README.md`) is added, renamed or removed, or changes what it covers;
- a second maintainer joins, or the fork-PR or issues question is settled.
