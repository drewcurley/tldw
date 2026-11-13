# Ownership — tldw

Who owns which part of tldw, who plays each agent role, who breaks ties, and who is on call. `AGENTS.md` § 3 (`AGENTS.md:125-134`) relies on this file to name the Directly Responsible Individual (DRI) for each area.

## Summary

- **tldw is a solo repo.** One person, `@drewcurley`, owns every area and is the DRI for all of them (`.github/CODEOWNERS:7-13`).
- **There is no backup.** No second person is named for any area.
- **There is no on-call.** tldw is not a hosted service, so there is no production for anyone to be on call for (see [On-call](#on-call)).
- **Required approvals are 0, and code-owner review is off,** because GitHub forbids approving your own PR (`.github/CODEOWNERS:7-10`, `AGENTS.md:436`, `ADOPTING.md:209-215`). The `hackathon/demo` ruleset is set this way (`tasks/T-003.md:210`). `CODEOWNERS` therefore routes review requests but blocks nothing.

## Area map

This mirrors `.github/CODEOWNERS`. GitHub uses the **last** matching pattern for each path (`.github/CODEOWNERS:4`). Every row's DRI is `@drewcurley`, and every row's backup is none.

| Area | Paths (tracked files) | `CODEOWNERS` line that decides it | DRI | Backup |
|---|---|---|---|---|
| Python package | `src/youtube_tldw/` (all 23 files) | `*` (line 13): there is no `src/` line | `@drewcurley` | none |
| Browser extension | `extension/` (the scripts, `options.html`, both manifests, `build-firefox.sh`, icons, `README.md`) | `*` (line 13): there is no `extension/` line | `@drewcurley` | none |
| Tests | `tests/` (the pytest modules, `tests/js/`, `tests/fixtures/`, `conftest.py`) | `*` (line 13): there is no `tests/` line | `@drewcurley` | none |
| CI gate scripts | `scripts/ci-base.sh`, `scripts/ci-checks.sh`, `scripts/ci-deck-needed.sh`, `scripts/ci-pr-checks.sh`, `scripts/ci-tldw-tests.sh` | `/scripts/ci-*.sh` (line 27) | `@drewcurley` | none |
| CI install lock | `scripts/requirements-ci.txt` | `/scripts/requirements-ci.*` (line 28) | `@drewcurley` | none |
| Other scripts | `scripts/aggregate-docs.sh`, `scripts/setup-hooks.sh`, `scripts/setup-workspace.sh` | `*` (line 13) | `@drewcurley` | none |
| Script fixture suites | `scripts/test/*.sh` (16 suites) | `*` (line 13): `/scripts/ci-*.sh` doesn't reach into `scripts/test/` | `@drewcurley` | none |
| Git hooks | `.githooks/` | `/.githooks/` (line 29) | `@drewcurley` | none |
| GitHub config | `.github/workflows/checks.yml`, `.github/pull_request_template.md` | `/.github/` (line 19) | `@drewcurley` | none |
| Code owners | `.github/CODEOWNERS` | `/.github/CODEOWNERS` (line 32) | `@drewcurley` | none |
| Azure pipeline (inert) | `azure-pipelines.yml` | `/azure-pipelines.yml` (line 26) | `@drewcurley` | none |
| Placeholder allowlist | `.placeholder-allow` | `/.placeholder-allow` (line 30) | `@drewcurley` | none |
| Agent briefs and Claude settings | `.claude/agents/*.md`, `.claude/settings.json` | `/.claude/` (line 18) | `@drewcurley` | none |
| Conventions | `AGENTS.md`; `CLAUDE.md`; `ADOPTING.md` | `/AGENTS.md` (line 16); `/CLAUDE.md` (line 17); `*` (line 13) for `ADOPTING.md`, which has no line | `@drewcurley` | none |
| Tasks ledger | `tasks/` | `/tasks/` (line 35) | `@drewcurley` | none |
| Docs with their own line | `docs/architecture.md`, `docs/stack.md`, `docs/data-model.md`, `docs/api.md`, `docs/testing.md`, `docs/deployment.md`, `docs/decisions/`, `docs/ownership.md`, `docs/onboarding.md`, `docs/ux.md` and `docs/runbooks/` | their own lines (lines 39-49) | `@drewcurley` | none |
| Other docs | `docs/README.md`, `docs/contracts.md`, `docs/reviews/`, `docs/domain.md` and `docs/glossary.md` | `/docs/` (line 38): there is no `/docs/contracts.md` line | `@drewcurley` | none |
| Packaging and repo files | `pyproject.toml`, `uv.lock`, `README.md`, `.gitignore`, `.pre-commit-config.yaml` | `*` (line 13) | `@drewcurley` | none |

**What `CODEOWNERS` leaves to the defaults.** It has no line for `src/`, `extension/`, `tests/` or `/docs/contracts.md`:
- `src/`, `extension/` and `tests/` fall to `*` (line 13).
- `docs/contracts.md` falls to `/docs/` (line 38), the broadest pattern that matches it, rather than to `*`.

Either way the owner is `@drewcurley`, so today nothing about review changes. The "Code areas" block is still the starter's commented-out example (`.github/CODEOWNERS:51-56`), and no real code-area lines were added.

**Facts about the file itself**, recorded but not changed here:
- Line 12's comment says "at least one human approver required on every PR". That is the starter's wording and doesn't match this repo, where 0 approvals are required (lines 7-10; `tasks/T-003.md:210`).
- Line 31 names `/.gitattributes`, which is not a tracked file in this repo (`git ls-files`).
- Line 5 says to keep `CODEOWNERS` in sync with this file. A change to one is a change to the other.

## Agent roles

The 9 roles in `AGENTS.md` § 1 (`AGENTS.md:80-96`) run as Claude Code subagents, one per brief in `.claude/agents/` (`CLAUDE.md:24-25`). One human, the owner, stands behind every role: the owner is accountable for each sign-off and is the only person who can accept an Architectural Decision Record. This is `AGENTS.md` § 12 Knob 1 for a solo founder: one person wears all 9 hats, one after another, each with that role's redlines in front of them (`AGENTS.md:528-531`).

| Role | Brief | Human |
|---|---|---|
| Analyst | `.claude/agents/analyst.md` | `@drewcurley` |
| Architect | `.claude/agents/architect.md` | `@drewcurley` |
| Data Engineer | `.claude/agents/data-engineer.md` | `@drewcurley` |
| Backend Engineer | `.claude/agents/backend-engineer.md` | `@drewcurley` |
| Frontend Engineer | `.claude/agents/frontend-engineer.md` | `@drewcurley` |
| UX Designer | `.claude/agents/ux-designer.md` | `@drewcurley` |
| Test Engineer | `.claude/agents/sdet.md` | `@drewcurley` |
| DevOps / Ops | `.claude/agents/devops.md` | `@drewcurley` |
| Review Coordinator | `.claude/agents/review-coordinator.md` | `@drewcurley` |

## Tiebreak rules

`AGENTS.md` § 3 (`AGENTS.md:114-134`) applies as written. With one human, it collapses like this:

| Rule in `AGENTS.md` § 3 | In this repo |
|---|---|
| **Agent vs. agent** defaults: UX wins on user-facing decisions, Frontend on feasibility, Data on schema and queries, Backend on application logic, Architect on security, Analyst on scope, Test Engineer on coverage, Review Coordinator on process (`AGENTS.md:116-123`) | Unchanged. These apply "unless explicitly overridden by the user" (`AGENTS.md:116`), and the user is the owner. |
| 1. The **DRI for the affected area** decides when humans disagree (`AGENTS.md:129`) | The owner, for every area. With one human there is no human-vs-human disagreement, so in practice this is the owner deciding when the agents can't resolve something between them. |
| 2. If the disagreement **spans areas**, the next level up decides (`AGENTS.md:130`) | There is no next level. The owner decides. |
| 3. Decisions that meaningfully shape the product are **recorded as an Architectural Decision Record**, whoever made them (`AGENTS.md:131`) | Unchanged: they go in `docs/decisions/`. |
| 4. **A redline is not a tiebreak target.** Crossing one needs an Architectural Decision Record accepted by the area DRI *and* leadership (`AGENTS.md:132`, `AGENTS.md:441`) | There is no separate leadership, so the owner's explicit, dated acceptance is the only human acceptance available. ADR 0003 is the example: the owner accepted it on 2026-11-12 (`docs/reviews/T-001-tier-1/plan.md:41`). |
| A `/docs/*` **consensus stall** goes to the DRI, with the reasoning recorded as an Architectural Decision Record (`AGENTS.md:74`) | The owner decides, and the decision is recorded as an Architectural Decision Record. |

## On-call

**There is no on-call rotation, and no on-call person.** tldw has no hosted service:
- each user runs their own `tldw serve` on their own machine, with their own model plan (`README.md:432-435`, `extension/README.md:110-115`);
- the server refuses to bind any host but loopback (`src/youtube_tldw/server.py:1935-1938`).

There is no production deployment for anyone to be paged about. A problem with one user's install is that user's to fix, with the procedures in `docs/runbooks/` (start at `docs/runbooks/README.md`; why there is no rotation, paging or escalation is `docs/runbooks/on-call.md`).

These are `UNKNOWN — needs human confirmation`:
- **How to report a security problem.** The repo has no `SECURITY.md`, and no file names a contact.
- **Who else, if anyone, has write access to the GitHub repo.** It was not checked, and this file only knows what `CODEOWNERS` says.

## When a second maintainer joins

These are the changes the repo's own files already call for. None is made now:

1. **Raise required approvals to at least 1, and turn on "Require review from Code Owners",** in the `hackathon/demo` ruleset (`ADOPTING.md:209-215`; `AGENTS.md:436`; `.github/CODEOWNERS:9-10`; `tasks/T-003.md:171`). That restores the human review gate. It also closes the risk accepted in T-003: with code-owner review off, nothing on the server stops a PR from editing the workflow YAML, and today the owner reading gate-file diffs is the only protection (`tasks/T-003.md:165`, `ADOPTING.md:218-224`).
2. **Give every gate-file line in `CODEOWNERS` a second owner:** lines 26-32. Because the last match wins, no gate line may name only one of them (`.github/CODEOWNERS:21-25`).
3. **Name a backup for each area,** in this file and in `CODEOWNERS` together (`.github/CODEOWNERS:5`). Then name who decides when a disagreement spans areas (rule 2 above).
4. **Only turn on code-owner review while `CODEOWNERS` has no template placeholders,** or every PR becomes unmergeable (`ADOPTING.md:244-246`). It has none today: `.placeholder-allow` doesn't list it, so the gate scans it (`tasks/T-003.md:107`).
5. **Revisit `onboarding.md`'s N/A sections** (channels, and 30/60/90), which are N/A only because the repo is solo.

Each of these goes through the review cycle. Changing `.github/CODEOWNERS` never qualifies as lightweight (`AGENTS.md:168`), and this file needs unanimous consensus.

## How this was verified

- **`CODEOWNERS`:** read `.github/CODEOWNERS` line by line, and compared every pattern against `git ls-files` to find which line decides each tracked path. `git ls-files src extension scripts .claude` and `git ls-files | grep -v /` gave the area lists. `/.gitattributes` does not appear in `git ls-files`.
- **The solo settings:** `.github/CODEOWNERS:7-10`, `AGENTS.md:436`, `ADOPTING.md:186-246`, and the ruleset record in `tasks/T-003.md:209-215`. The GitHub ruleset itself was not queried for this doc.
- **The roles:** `ls .claude/agents` lists the 9 briefs, and `.claude/agents/sdet.md` names itself the Test Engineer. `CLAUDE.md:24-25` says how they are invoked.
- **The tiebreaks:** `AGENTS.md:71-74` and `AGENTS.md:114-134`. ADR 0003's acceptance comes from `docs/reviews/T-001-tier-1/plan.md:41`.
- **On-call:** `README.md:432-435`, `extension/README.md:110-115`, and `src/youtube_tldw/server.py:1935-1938`. `ls SECURITY.md .github/SECURITY.md` finds neither file.

## Update this doc when

- `.github/CODEOWNERS` changes (line 5 asks for the two files to be kept in sync);
- a maintainer joins or leaves, or anyone else is given write access;
- the `hackathon/demo` ruleset's approval or code-owner settings change;
- a top-level directory is added, removed or renamed, or `scripts/` gains or loses a file;
- `.claude/agents/` gains, loses or renames a role, or `AGENTS.md` § 1, § 3 or § 12 changes;
- a security-contact file is added, or tldw gains any hosted component that something could be on call for.
