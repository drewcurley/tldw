# Review artifacts (`docs/reviews/`)

Every review cycle ends in a written artifact (`AGENTS.md` § 9, lines 449–482). This folder is where those artifacts live. It holds the record of who reviewed what, what they found, and what content they signed off. The Review Coordinator writes the artifacts (`.claude/agents/review-coordinator.md:20`, `:60`).

The template is [`_TEMPLATE.md`](./_TEMPLATE.md). It is the § 9 template plus three lines this repo requires: spot-checks on every sign-off, the regression-safety line, and a content hash in `final.md`.

## Where an artifact goes

**One folder per review: `docs/reviews/<folder>/`.**

- **The folder is named after the task, when the task gives the folder a name.** T-001 names `docs/reviews/T-001-tier-1/final.md` (`tasks/T-001.md:22`), so this PR's artifacts go in `docs/reviews/T-001-tier-1/`.
- **Otherwise it is named after the branch, with every `/` replaced by `-`.** A branch `feat/example-name` gets `docs/reviews/feat-example-name/`.
- **Why the replacement.** `AGENTS.md:482`, `.claude/agents/review-coordinator.md:20`, `:42`, `:60` and `.github/pull_request_template.md:60` say `docs/reviews/<branch>/`. Every branch prefix in `AGENTS.md` § 7 (`AGENTS.md:345`–`348`: `feat/`, `fix/`, `chore/`, `refactor/`) puts a `/` in the name, and a `/` would nest one folder inside another. The rule was settled in the T-001 plan, rev 4 (`tasks/T-001.md:189`). Read `<branch>` in those files as the folder named by this rule; they are not edited here (editing `AGENTS.md` is out of T-001's scope).

**The files in a folder:**

| File | When it is written | Who signs it |
|---|---|---|
| `plan.md` | When the plan is reviewed (`AGENTS.md` § 7 step 4). It records every round of the plan review in one file, as `T-001-tier-1/plan.md` does. | The agents of each round the plan went through |
| `round-1-analysis.md` | After Round 1 of the build or draft review | Analyst, Architect, Data Engineer (`AGENTS.md:106`) |
| `round-2-implementation.md` | After Round 2 | Backend, Frontend, UX (`AGENTS.md:107`) |
| `round-3-verification.md` | After Round 3 | Test Engineer, DevOps (`AGENTS.md:108`) |
| `final.md` | Once every round's blockers are fixed and the final content is agreed | Every agent the § 5 scope requires; for a `/docs/*` change, all 9 (`AGENTS.md:165`) |

- The Review Coordinator takes part in every round (`AGENTS.md:110`), so it signs every file.
- The rounds run in order, and a round's blockers are fixed before the next round starts (`AGENTS.md:102`).
- **When the § 5 scope puts no agent in a round**, that round has no file. `final.md` says which rounds ran and which did not, and marks each agent outside the scope "not required (§ 5, <row>)", as `tasks/T-003.md:349`–`354` does.

## Writing an artifact

1. **Copy `_TEMPLATE.md`** to the file named above, then fill every slot. A section with nothing in it says "none" or "n/a — <reason>"; no heading is dropped.
2. **Status** is one of PASS, PASS WITH ITEMS or BLOCK (`AGENTS.md` § 9).
3. **Every sign-off lists its spot-checks:** the `path:line` places the agent read in the source to check the change (`tasks/T-001.md:208`). For a docs change, reviewers check the docs against the code, not against the prose (`AGENTS.md:186`, § 6.0(5)).
4. **The regression-safety line** is the one `AGENTS.md:365` (§ 7 step 8) gives, with every field filled in. "None" is a valid answer and is written out.
5. **Lens sign-offs** (`AGENTS.md` § 4) are for major decisions. Otherwise the section says "n/a — <why nothing new is decided>", as `T-001-tier-1/plan.md` does.
6. **Public repo.** An artifact never contains (`tasks/T-001.md:91`): a real home-directory path (write `~/…`); a real video ID or title (use invented 11-character IDs such as `AAAAAAAAAAA`); a real cache, usage or summary row; a token or anything token-shaped (write `<your-token>`); raw `tldw serve` output, which prints the token; a session ID.
7. **No template placeholder marker.** `docs/` is not on `.placeholder-allow`, so the placeholder gate in `.githooks/pre-commit:57`–`77` and `scripts/ci-checks.sh:83`–`119` refuses a commit that adds one. The template's slots are written in angle brackets, which that gate does not match.
8. **The commit needs the consensus trailer.** Any commit that touches a file under `docs/` outside `docs/repos/` needs `unanimous-consensus: T-NNN` in its message (`.githooks/commit-msg:33`–`37`, `scripts/ci-checks.sh:153`–`167`). That includes a commit that only adds an artifact.

## Sign-off is on the final content

- **An edit made after an agent's PASS** is re-confirmed by every agent whose domain it touches. Then every required agent confirms the final content (`tasks/T-001.md:208`).
- **The final content is identified by a tree hash, not a commit SHA.** Rebase-merging rewrites commit SHAs, but the tree hash of the same content does not change. `final.md` records the `git write-tree` hash of the staged tree at sign-off.
- **`final.md` itself is left out of the hash.** Writing the hash into `final.md` changes the tree, so a hash that included it could never match. The hash line names every path left out, and why.
- **Commits after the hash** (for example the one that fills in a task's `pr:` field) are listed in `final.md` with the paths they touch, so a reader can see what was not signed.

**Taking the hash at sign-off**, from the repo root with the final content staged. It works on a copy of the index, so the real index is not changed:

```sh
tmp=$(mktemp)
cp "$(git rev-parse --git-path index)" "$tmp"
GIT_INDEX_FILE="$tmp" git update-index --force-remove docs/reviews/<folder>/final.md
GIT_INDEX_FILE="$tmp" git write-tree
rm -f "$tmp"
```

**Checking it later** against a commit (on the branch or after merge):

```sh
tmp=$(mktemp)
GIT_INDEX_FILE="$tmp" git read-tree <commit>
GIT_INDEX_FILE="$tmp" git update-index --force-remove docs/reviews/<folder>/final.md
GIT_INDEX_FILE="$tmp" git write-tree
rm -f "$tmp"
```

Add one `--force-remove` for each other path the hash line names. The two printed hashes match only when everything else in the tree is what was signed.

## Artifacts are never edited after merge

- **An artifact is a record.** Once the PR that adds it has merged, its file is not changed again: not to fix a typo, not to update a verdict.
- **To correct or add to one, add a new artifact** in the same folder. It names the artifact it corrects and says what changed. The original stays as it merged.
- **Before merge**, an artifact is still a draft on its branch and may change. Every change still goes through the sign-off rules above.
- **Enforcement is by review only.** No hook or CI check refuses an edit to a merged artifact; the only mechanical gate on these files is the consensus trailer (step 8 above).

## Artifacts kept in a task file

A review whose scope is less than all 9 agents may keep its artifact in the task file instead of here, as T-003 did:

- T-003 was a config/infra change, reviewed by Architect and Ops plus the Test Engineer (`AGENTS.md:163`, `tasks/T-003.md:349`–`357`).
- Writing under `docs/` needs the `unanimous-consensus:` trailer, which the hook's message describes as certifying that all 9 agents signed off (`.githooks/commit-msg:37`). A config/infra review does not have all 9.
- So T-003's frontmatter says `review_artifact: tasks/T-003.md#review-artifact` (`tasks/T-003.md:10`), and the artifact is the file's `## Review artifact` section (`tasks/T-003.md:291`). The reason is written in `tasks/T-003.md:239`.
- A task doing the same sets `review_artifact: tasks/T-NNN.md#review-artifact`, puts the filled template under a `## Review artifact` heading, and follows the same never-edited-after-merge rule for that section.
- **Other reduced scopes** (bug fixes, UI polish, query optimization: `AGENTS.md:161`–`164`) and **lightweight reviews** (`AGENTS.md:166`): whether their artifacts go here, in the task file, or nowhere is `UNKNOWN — needs human confirmation`. The earlier bug-fix reviews below were written here, under the old playbook.

## Legacy files: pre-playbook history

The 13 flat files directly in `docs/reviews/` were written under the earlier one-page playbook, before T-003 adopted `AGENTS.md` (added in commit `16787cf`, 2026-11-12). That playbook had eight agents, with an "SDET" and no Review Coordinator (`docs/reviews/PLAN-concurrency-limits.md:289`–`292`), and its own artifact shape (`docs/reviews/REVIEW.md:1`–`3`), not the § 9 template.

**What they are:**
- **Non-binding history.** They are not contracts. When one disagrees with `docs/contracts.md`, `contracts.md` wins.
- **Not part of the session-start read.** `AGENTS.md:54` (§ 0 Step 1) says to read every file in `/docs`; these 13 are the exception, as the docs index (`docs/README.md`, T-001.1) records, per the T-001 plan (`tasks/T-001.md:107`, `:189`). Read one only for the history of a feature.
- **Not a format to copy.** New artifacts use `_TEMPLATE.md`.
- **Kept in place, unedited.** None is moved, renamed or changed.

**`PLAN-extension.md` must not move.** The code cites it by path, in the module docstring (`src/youtube_tldw/server.py:3`) and in the message that refuses a non-loopback bind (`src/youtube_tldw/server.py:1935`–`1939`).

| File | First heading (the feature it planned or reviewed) | Added (commit, date) |
|---|---|---|
| `PLAN.md` | youtube-tldw — Implementation Plan | `5115675`, 2026-06-17 |
| `REVIEW.md` | Review Artifact — youtube-tldw (feat/youtube-tldw) | `5115675`, 2026-06-17 |
| `PLAN-coherence.md` | Plan — coherent, sentence-complete cuts | `b903e8e`, 2026-06-17 |
| `PLAN-polish.md` | Plan — Video Polish (intro / badge / fade-to-black end card) | `860587b`, 2026-06-17 |
| `PLAN-extension.md` | Plan — Browser extension + local server (text-only TL;DW) | `1fcaa57`, 2026-06-18 |
| `PLAN-audio-extension.md` | Plan — "Generate audio" in the browser extension (TTS + player) | `c2bbb58`, 2026-06-18 |
| `PLAN-streaming-tts.md` | PLAN — streaming TTS playback + voice preview | `82f4726`, 2026-09-08 |
| `PLAN-ask.md` | PLAN — follow-up Q&A about a video | `19d5a0c`, 2026-09-11 |
| `PLAN-faster-summaries.md` | PLAN — faster summaries in the extension | `7874a7d`, 2026-09-21 |
| `PLAN-page-transcript.md` | PLAN — the transcript comes from the page's own panel | `4c51ddd`, 2026-09-28 |
| `PLAN-whisper-fallback.md` | PLAN — transcribe captionless videos with local Whisper | `006ea80`, 2026-10-15 |
| `PLAN-spend-speed-disk.md` | PLAN — spend less, wait less, keep less | `02c5eb1`, 2026-11-01 |
| `PLAN-concurrency-limits.md` | PLAN — limits that match what the work costs | `57dfe78`, 2026-11-10 |

## How this was verified

- **The 13 legacy files:** listed with `ls docs/reviews/*.md` (13 files). Each first heading was read with `head -1`, and each add commit and date found with `git log --diff-filter=A`. The eight-agent "SDET" set was read in `docs/reviews/PLAN-concurrency-limits.md:286`–`294` and `docs/reviews/REVIEW.md`.
- **Who cites them:** `git grep` for every legacy filename across the tracked tree, the T-001 task files left out. The only hits are `src/youtube_tldw/server.py:3` and `:1938`.
- **Rules and template:** read in `AGENTS.md` § 2 (`:102`–`110`), § 5 (`:161`–`166`), § 6.0 (`:186`), § 7 (`:365`), § 9 (`:449`–`482`); `.claude/agents/review-coordinator.md`; `.github/pull_request_template.md:60`; the T-001 plan, rev 4 (`tasks/T-001.md:22`, `:91`, `:189`, `:208`); and T-003's in-task artifact (`tasks/T-003.md:10`, `:239`, `:291`–`360`).
- **Gates:** the placeholder gate read in `.githooks/pre-commit:57`–`77`, `scripts/ci-checks.sh:83`–`119` and `.placeholder-allow` (no `docs/` entry); the consensus gate in `.githooks/commit-msg:33`–`37` and `scripts/ci-checks.sh:153`–`167`.
- **The hash commands** were run in a throwaway repository in the scratchpad: the hash taken from a copy of the staged index matched the hash rebuilt from the commit, and did not change when only `final.md` changed.

## Update this doc when

- `AGENTS.md` § 2, § 5, § 7 step 8 or § 9 changes (the rounds, the scopes, the regression-safety line or the template).
- `.claude/agents/review-coordinator.md` or `.github/pull_request_template.md` changes where artifacts go.
- `.githooks/commit-msg`, `.githooks/pre-commit`, `scripts/ci-checks.sh` or `.placeholder-allow` changes the consensus or placeholder gate for `docs/`.
- A file is added to, removed from or moved within `docs/reviews/` outside a review folder.
- `src/youtube_tldw/server.py` changes its pointer to `docs/reviews/PLAN-extension.md`.
- Another task keeps its artifact in its task file, or the owner settles the `UNKNOWN` above.
