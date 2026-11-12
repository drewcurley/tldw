# Claude Code — Project Entry Point

> **All project conventions, the 9-agent team, the 7 strategic lenses, the review cycle, and the session-start procedure live in [`AGENTS.md`](./AGENTS.md).**
>
> `AGENTS.md` is the canonical source of truth. This file exists so Claude Code finds the same procedure that Codex and other agents read from `AGENTS.md`. Do not duplicate content here — keep them in sync by editing `AGENTS.md` only.

## Read this first, every session

1. Open and read **[`AGENTS.md`](./AGENTS.md)** in full.
2. Follow the **Session Start Procedure** at the top of that file: first detect whether you're in a single repo or a multi-repo **workspace** (a `workspace.manifest.md` next to a `playbook/`), then read `/docs` (and the playbook's `integration-map.md` in the workspace model), or bootstrap docs if missing.
3. Then respond to the user's request, applying the agent team, review cycle, and hard gates defined there.

> **This repo may be a project, or a shared playbook.** If it contains a `workspace.manifest.md`, this is the **shared playbook** for a multi-repo project (see `AGENTS.md` § 6.5) — the code lives in sibling repos. Otherwise it's a single-repo project. The session-start procedure handles both.

## This repo: tldw, hackathon fork (T-003)

- **Which playbook applies.** A user-level `~/CLAUDE.md` may also load: it holds an older, 8-agent version of this procedure, its "Review Cycle & Development Workflow". For work in this repo, `AGENTS.md` here replaces **only that playbook**. It does **not** override the user's `~/.claude/CLAUDE.md` (for example usage pacing), auto-memory, or the harness's permission rules, all of which still apply.
- **Read "`main`" as `hackathon/demo`.** That is this fork's integration branch, and it is protected like `main`: by the hooks, by `.claude/settings.json`, and by a GitHub branch rule. Demo work goes on `feat/` / `fix/` / `chore/` / `refactor/` branches cut from `hackathon/demo`, and its PRs target `hackathon/demo`. **Never `main`:** this branch is thrown away after the demo and must never reach `main`.
- **Single repo.** There is no `workspace.manifest.md`, so Step 0 is "single repo".
- **Never run `scripts/setup-hooks.sh` here.** It sets `core.hooksPath` in the git config this worktree shares with the owner's everyday checkout. Hooks are installed for this worktree only: `git config extensions.worktreeConfig true && git config --worktree core.hooksPath .githooks`.

## Claude-Code-specific notes

- Subagents are defined in [`.claude/agents/`](./.claude/agents/). Invoke them via the `Task` / `Agent` tool, one per specialist.
- The Review Coordinator (`.claude/agents/review-coordinator.md`) is the entry point when running a review cycle — it dispatches the other agents.
- Project permissions and settings live in [`.claude/settings.json`](./.claude/settings.json).

## Anything not covered here

→ See `AGENTS.md`. If `AGENTS.md` doesn't cover it, ask the user — don't guess.
