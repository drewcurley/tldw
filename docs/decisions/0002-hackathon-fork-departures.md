# 0002 — This hackathon fork's departures from the agentic starter

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** the owner (@drewcurley), who made these decisions on 2026-11-12 under T-003, a solo repo with one person wearing every agent's hat (`AGENTS.md` § 12 Knob 1). The Architect, DevOps, Test Engineer and Review Coordinator reviewed the plan and the build (`tasks/T-003.md`, "Activity log").
- **Task:** T-003 (`tasks/T-003.md`); recorded here under T-001.11
- **Supersedes:** none
- **Redlines touched:** none crossed. One rule from a starter suite is waived and recorded below (Departure 6).

## Context

tldw adopted the agentic starter at commit `bb9406f` (2026-11-09) on a branch used for a hackathon demo (`tasks/T-003.md`, "Description"). `AGENTS.md` § 7 asks that "any intentional divergence from upstream" be recorded as an ADR "so the next person knows it's deliberate, not drift". § 12 allows scaling the procedure down for a small team, provided what was changed is written down. The departures were planned, reviewed and recorded in `tasks/T-003.md`. This ADR collects them in one place. `tasks/T-003.md` remains the detailed record.

## Options considered

1. **Adopt the starter unchanged.** Rejected. Several starter files describe the starter rather than tldw (its README, deck and license), or assume a multi-repo workspace this repo isn't (`tasks/T-003.md`, "What is left out, and why").
2. **Adopt it with the departures below, each recorded.** Chosen.

## Decision: the departures

1. **The integration branch is `hackathon/demo`, not `main`.**
   - It is thrown away after the demo and never merges into `main` (`tasks/T-003.md`, "Description"; `AGENTS.md`, "This fork" note; `CLAUDE.md:15-18`).
   - It is protected like `main`:
     - the hooks (`.githooks/pre-commit:22`, `.githooks/pre-push:10`);
     - an `ask` rule in `.claude/settings.json:27`;
     - CI push triggers (`.github/workflows/checks.yml:24-25`, `azure-pipelines.yml:22-23`);
     - GitHub ruleset 27155529 (`tasks/T-003.md`, "Activity log").
   - The ruleset requires a PR with 0 approvals and code-owner review off, rebase merges only, and the four checks `procedure checks`, `shell scripts (lint + test)`, `overview deck (build + compare)` and `tldw tests (pytest + extension)`, strict up to date. It blocks force-push and deletion and has no bypass actors.
   - **`main` has no ruleset.**
2. **Single repo.** No `workspace.manifest.md` and no `docs/integration-map.md`; T-002 (the workspace model) is `cancelled` (`tasks/T-002.md:6`). The workspace scripts stay because the starter's tests cover them, and with no manifest they do nothing. One case in `scripts/test/test-setup-workspace.sh` was changed to write its placeholder manifest inline, with its assertions untouched.
3. **Left out, each for a stated reason** (`tasks/T-003.md`, "What is left out, and why"):
   - `.starter/`: no upstream sync on a throwaway branch.
   - The overview deck (`Agentic-Starter-Overview.pptx`, `scripts/build_deck.py`, `scripts/requirements-deck.txt`): it describes the starter, not tldw. `scripts/test/test-deck.sh` and its CI job stay and print `SKIP:`.
   - The starter's `README.md`: tldw keeps its own.
   - `LICENSE`: the starter's MIT license is the owner's own copyright, and adding it would relicense tldw, which nobody asked for. tldw has no license file.
   - `.github/FUNDING.yml`: unrelated to the playbook.
   - `.claude/settings.local.json`: the starter author's personal allowlist, never part of the template.
4. **`azure-pipelines.yml` is kept but inert.**
   - Five starter suites read it unconditionally.
   - No Azure DevOps pipeline is connected, and no Azure check is required.
   - It gets every change `checks.yml` gets, so the two stay in step.
5. **CI also runs tldw's own tests.**
   - A `tldw tests (pytest + extension)` job, implemented once in `scripts/ci-tldw-tests.sh` and called by both CI files.
   - Least privilege: `contents: read`, a pinned `actions/checkout` with `persist-credentials: false`, no secrets, caches or artifacts.
   - A hash-locked, binary-only install from `scripts/requirements-ci.txt`, then `pip install --no-deps --no-build-isolation -e .`.
   - The job fails unless the JUnit report shows 0 skipped, 0 failed, 0 errors and at least `MIN_TESTS` tests (`scripts/ci-tldw-tests.sh:27`; 660 under T-003, raised to 717 by T-001).
   - Pinning node with `actions/setup-node` was declined: the starter allows only `actions/checkout`, and the runner's node is used.
6. **A starter rule waived, with matching controls.** `test-deck.sh`'s rule that only the deck job installs Python packages is inactive here, because that suite skips once the deck is gone. The tldw job is a deliberate exception, with the deck job's controls: hash-locked, binaries only, no secrets or artifacts (`tasks/T-003.md`, "Adaptations" item 5 and "Warnings").
7. **Hooks are installed for this worktree only.**
   - `git config extensions.worktreeConfig true`, then `git config --worktree core.hooksPath .githooks`.
   - `scripts/setup-hooks.sh` is never run here, because it would set `core.hooksPath` in the config shared with the owner's everyday checkout (`CLAUDE.md:20`).
8. **`CLAUDE.md` and `AGENTS.md` adaptations.**
   - `CLAUDE.md` keeps the starter text and adds a "This repo" note: `AGENTS.md` here replaces only the older "Review Cycle & Development Workflow" playbook a parent `CLAUDE.md` may load, and does not override user-level settings, memory or permission rules (`CLAUDE.md:15-20`).
   - `AGENTS.md` keeps the starter text, with only the § 4 product name filled in as `tldw` and a "This fork" note at the top (`AGENTS.md:9`).
9. **Placeholders are filled for tldw.**
   - The stack, LLM, tooling and people placeholders are filled; unknowns are left as `UNKNOWN`.
   - `.placeholder-allow` drops `.github/CODEOWNERS`, `README.md`, `docs/README.md`, `workspace.manifest.md` and `docs/integration-map.md`, so those files are scanned (`.placeholder-allow`).
   - `.pre-commit-config.yaml` is kept, with its two placeholders filled in stanzas that stay commented out, because `setup-hooks.sh` names the file.
10. **Solo-repo scaling** (`AGENTS.md` § 12).
    - CODEOWNERS names `@drewcurley` for every path, so approvals are 0. GitHub forbids approving your own PR.
    - **Accepted risk:** with code-owner review off, nothing on the server protects the workflow YAML from a PR that edits it. The protection is the owner reading gate-file diffs (`tasks/T-003.md`, "Branch rule").
    - Lenses were not run for T-003, because the owner made the call on a throwaway branch (`tasks/T-003.md`, "Review artifact", Scope).
11. **Bootstrap exception, once.** On PR #11 the `procedure checks` job failed closed, by design, because the target branch had no `scripts/ci-checks.sh` yet (`ADOPTING.md` § 4). A local gate run, a PR-body check and the post-merge push build covered it (`tasks/T-003.md`, "Known bootstrap failure").
12. **T-003's review artifact lives in the task file** (`tasks/T-003.md#review-artifact`), not in `docs/reviews/`, because that PR touched nothing under `/docs/`.

**Recorded elsewhere, for completeness.** T-001 defers its Tier 2 docs under `AGENTS.md` § 12 Knob 3, with a target date, by the owner's decision ("build Tier 1 now", 2026-11-12). That is logged in `tasks/T-001.md`, "Activity log", not in T-003.

## Consequences

### Good
- The demo runs the current methodology with its gates enforced, server-side on `hackathon/demo` and client-side in this worktree, without touching the owner's everyday checkout or `main`.
- Nothing from the starter that describes the starter itself, or relicenses tldw, leaked in.
- tldw's own 660 tests became a required check, hash-locked and fail-on-skip.

### Bad
- `main` has no server-side protection; only client-side hooks, where installed, guard it.
- The workflow YAML has no server-side protection from a PR that edits it (Departure 10).
- `MIN_TESTS` must be raised by hand as tests are added (`tasks/T-003.md`, "Warnings").
- An inert Azure file has to be kept in step with `checks.yml` by hand, for tests that need it to exist.
- tldw remains unlicensed.

### What would void or revisit it
- The fork merging into `main`, or outliving the hackathon. The branch is meant to be thrown away.
- A second maintainer joining, which brings back code-owner review and approvals (`tasks/T-003.md`, "Suggestions").
- The deck returning, which re-activates the rule Departure 6 waives.
- Syncing with the starter upstream, which would treat this ADR as the list of intentional divergences.

## Acceptance

N/A — no redline crossed. The owner made these decisions on 2026-11-12 under T-003, which was reviewed and merged as PR #11 (`tasks/T-003.md`, "Activity log").

## How this was verified

- Read `tasks/T-003.md` in full.
- Checked the departures that are visible in the repo:
  - the protected-branch lists in `.githooks/pre-commit:22` and `.githooks/pre-push:10`, `.claude/settings.json:27`, and the CI triggers;
  - `.placeholder-allow`;
  - `CLAUDE.md:15-20`;
  - `MIN_TESTS` at `scripts/ci-tldw-tests.sh:27` (660 at T-003's merge; 717 after T-001);
  - `tasks/T-002.md` `state: cancelled`;
  - that `.starter/`, `LICENSE`, `.github/FUNDING.yml`, `workspace.manifest.md`, `docs/integration-map.md` and the deck file are absent.
- The GitHub ruleset and the push-build results are taken from `tasks/T-003.md`'s log, not re-queried.
