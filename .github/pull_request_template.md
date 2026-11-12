<!--
pull request template for repos using the agentic_starter conventions.
Read AGENTS.md before opening this pull request. All sections below are required for non-trivial pull requests.
For trivial pull requests (typo / lockfile bump / etc., per AGENTS.md § 5), keep "Summary" + "Why this is trivial" and delete the rest.
-->

## Task

- **Task ID:** [T-NNN](../../tree/main/tasks/T-NNN.md)
- **Type:** feat | fix | chore | refactor | docs
- **Lightweight review?** yes / no (see `AGENTS.md` § 5; if yes, justify below)

## Summary

<!-- 2–4 sentences. What changes. Why now. -->

## Acceptance criteria (from task file)

- [ ] …
- [ ] …

## Agent sign-offs

> All required agents must sign off **before merge**. Round-by-round verdicts live in the linked review artifact.

- [ ] **Analyst** — scope intact, acceptance criteria matches task file
- [ ] **Architect** — security posture not weakened, threat model considered
- [ ] **Data Engineer** — schema/queries reviewed (or not applicable)
- [ ] **Backend** — application logic reviewed (or not applicable)
- [ ] **Frontend** — user interface implementation reviewed (or not applicable)
- [ ] **UX Designer** — user-facing decisions reviewed (or not applicable)
- [ ] **Test Engineer** — every acceptance criteria has an automated test; coverage gates pass
- [ ] **DevOps** — deploy/rollback path documented, observability in place
- [ ] **Review Coordinator** — process followed, artifact filed

## 7 lenses (major decisions only)

- [ ] CEO &nbsp;&nbsp; - [ ] Purchasing &nbsp;&nbsp; - [ ] PM &nbsp;&nbsp; - [ ] Adopter &nbsp;&nbsp; - [ ] Builder &nbsp;&nbsp; - [ ] Investor &nbsp;&nbsp; - [ ] Marketing

## Human review

- **Required reviewer(s):** @<dri> + at least one human approver per `CODEOWNERS`
- **Tested locally?** yes / no — describe how
- **Continuous Integration green?** yes / no
- **Migrations included?** yes / no — if yes, link to rollback plan in `/docs/runbooks/`

## Regression safety / contracts (required — see `AGENTS.md` § 7)

> Proves this change didn't silently break a documented contract. "None" is a valid answer and must be stated, not omitted.

- **Contracts touched** (routes / schema fields / events / public signatures / integrations / env vars / flags from `/docs/contracts.md`): none / list them
- [ ] If any contract was touched, it is reflected in `/docs/contracts.md` (and `api.md` / `data-model.md`) **in this pull request** — and that doc change carries the `/docs/*` unanimous-consensus assertion
- **Cross-repo consumers** (workspace model — from `integration-map.md`): none / list `repo:surface` — and link the coordinated follow-up pull request(s)
- [ ] If a sibling repo consumes the touched contract, the change is backward-compatible **or** a producer-before-consumer follow-up is opened and `integration-map.md` is updated
- **Compatibility:** backward-compatible / migration at `<path>` / deprecation window per `api.md`
- [ ] The test levels that exercise the touched contracts (contract / integration / end-to-end per `testing.md`) were run and pass — a green unit suite alone does not satisfy this

## Review artifact

Link to `/docs/reviews/<branch>/final.md` once Round 3 is complete.

## Risk

- **Blast radius:** none / module / service / production-wide
- **Rollback strategy:** <one line, must be tested>
- **Feature flag in front:** yes / no — flag name if yes

## Redlines

Confirm none of the redlines in `.claude/agents/*.md` are crossed by this pull request.
If any redline is intentionally accepted (with leadership / Directly Responsible Individual approval), link the Architectural Decision Record.

- [ ] No redlines crossed, or accepted with Architectural Decision Record: <link>

## Out of scope (for this pull request)

- …
