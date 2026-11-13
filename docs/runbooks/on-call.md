# On-call

**Owner:** DevOps. **Task:** T-001.10. **Last verified against the code:** 2026-11-12, branch `chore/docs-tier2` at `a7f104c`.

**There is no on-call.** tldw has one owner, who is also its only known user, and no hosted service. Each user runs their own `tldw serve` on their own machine with their own model plan, and the server refuses to bind anything but loopback (`docs/ownership.md:5-10`, `:77-87`; `README.md:432-435`; `src/youtube_tldw/server.py:1935-1939`). When something breaks, the person at the keyboard is the person who fixes it, using the playbooks in this folder.

## Rotation

N/A — one owner, `@drewcurley`, owns every area with no backup (`docs/ownership.md:5-10`, `.github/CODEOWNERS:7-13`). There is nobody to rotate with.

## Paging tool

N/A — nothing pages anyone. There is no hosted service to watch and no alerting: the only telemetry is the server's own stdout and stderr and the usage log `~/.config/youtube-tldw/usage.jsonl`, read with `tldw usage` (`docs/deployment.md` § 11, "Logs" and "Observability, alerting, on-call, SLOs"). You find out something is wrong when the panel shows a message; [`README.md`](README.md) maps each message to its playbook.

## Escalation tree

N/A — there is no one inside the project to escalate to. What is outside tldw's control, and where it belongs:

| Problem | Whose it is | What you can do |
|---|---|---|
| The `claude` CLI is logged out, rate-limited or failing | The `claude` CLI and your Claude plan, not tldw | [`model-failing.md`](model-failing.md); the CLI's own login and support |
| YouTube is throttling this machine | YouTube; it clears on its own (`src/youtube_tldw/metadata.py:71-75`) | [`youtube-rate-limit.md`](youtube-rate-limit.md) |
| A bug in tldw itself | The owner | Record it as a task (`tasks/README.md`) |
| A security problem | `UNKNOWN — needs human confirmation`: the repo has no `SECURITY.md` and names no contact (`docs/ownership.md:85-86`; tracked in T-015) | — |

## When a second maintainer joins

The changes that would make rotation, paging and escalation apply are listed in `docs/ownership.md`, "When a second maintainer joins". None is made now. This file is rewritten then.

## How this was verified

- Read `docs/ownership.md:5-10` and `:77-100`, `.github/CODEOWNERS:7-13`, `docs/deployment.md` § 11, and `src/youtube_tldw/server.py:1933-1939` on `a7f104c`.
- Checked for a security contact: there is no `SECURITY.md` in the repo (`ls`), matching `docs/ownership.md:85`.

## Update this doc when

- `docs/ownership.md` or `.github/CODEOWNERS` names a second person, a backup, or a security contact;
- tldw gains a hosted server, alerting, or any telemetry beyond stdout, stderr and `usage.jsonl`;
- a playbook named in the escalation table is renamed or removed.
