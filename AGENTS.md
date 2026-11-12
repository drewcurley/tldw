# Project Conventions for AI Agents

> **This file is the single source of truth for how AI coding agents (Claude Code, Codex, Cursor, etc.) work in this repository.**
> It is read at the start of every session by every agent.
> `CLAUDE.md` mirrors this file so Claude Code and Codex follow the same procedure.
>
> **Setting up a fork?** The conventions here are only *enforced* once branch protection + an active CI check are in place. Follow **[`ADOPTING.md`](./ADOPTING.md)** — the ordered fork→enforced checklist — before relying on any gate.
>
> **This fork (tldw, T-003):** a single repo, adopted from the agentic starter at `bb9406f` for a hackathon demo. Its integration branch is **`hackathon/demo`**: wherever this file says `main` (protected branch, PR target, CI push trigger), read `hackathon/demo`, which is protected the same way. The branch is thrown away after the demo and never merges into `main`. Where this fork departs from the starter (no deck, no `.starter/`, no workspace manifest, an extra CI job for tldw's own tests) is recorded in `tasks/T-003.md`. See also the "This repo" notes in `CLAUDE.md`.

---

## Quick Summary — Read this before your first pull request

**Why `/docs/` exists — the one sentence that explains everything else:** the docs folder is the project's *living memory and source of truth*, built and maintained so that an agent who has read **only `/docs/`** can make a commit with high confidence that it will **not break an existing route, data contract, integration, or behavior**. Every rule below serves that goal. If a doc isn't complete enough or grounded enough in real code to support a safe autonomous commit, it isn't done — see the **Grounding & Completeness Protocol** in § 6.

If you have 60 seconds, here's the whole thing:

1. **Every session starts by reading `/docs/` and `tasks/INDEX.md`.** No exceptions. If `/docs/` is empty, the *only* job for that session is to build it (§ 6). The contract-critical inventory lives in `/docs/contracts.md` — the exhaustive, code-grounded list of every route, data contract, event, integration, environment variable, and invariant. Read it before changing anything.
2. **9 specialist agents** review every non-trivial change in **3 rounds** (Analysis → Implementation → Verification). See § 1, § 2.
3. **Each agent has redlines** in `.claude/agents/*.md` — non-negotiable lines. Crossing one BLOCKs the merge unless an Architectural Decision Record is filed with leadership approval.
4. **Major decisions** also run through **7 strategic lenses** (§ 4). Surface conflicts between lenses; don't paper over them.
5. **Tasks live one-per-file** in `/tasks/T-NNN.md`. Update the task file in the same pull request that delivers the work.
6. **`/docs/*` requires unanimous 9-agent consensus** to edit. `/tasks/*` flows through the normal review cycle.
7. **Human review still required** in addition to agent sign-off — see `CODEOWNERS` and § 7.
8. **Lightweight path exists** for trivial pull requests (typo / lockfile bump). See § 5.
9. **Hard gates** are in § 8. No exceptions, no "we'll fix it next sprint."

> **Don't try to internalize every redline at once.** All nine agents together carry roughly 80 redlines, but a typical pull request only touches three or four of them. When reviewing, open the relevant agent file and check against its redlines for the change at hand — that is the intended workflow, not memorization.

If anything below contradicts this summary, the section below wins — flag the contradiction so it gets fixed.

---

## 0. Session Start Procedure — DO THIS FIRST

Every agent — regardless of which command-line tool/integrated development environment invoked it — must run this procedure before answering the user's first request of a session.

> **Single-repo or multi-repo?** This starter supports two shapes. **(a) Single repo:** the fork *is* the project; `/docs`, `/tasks`, and the code all live in one repo — run Steps 1–4 against that repo and ignore the workspace step. **(b) Multi-repo project (workspace model):** the fork is a *shared playbook* that governs a project split across several repos checked out side by side; the playbook holds project-level `docs/` + `integration-map.md` + `workspace.manifest.md`, and each code repo carries its own `docs/`/`tasks`/hooks. Do Step 0 first to tell which you're in. See § 6.5 for the multi-repo model in full.

### Step 0 — Detect the workspace (multi-repo projects)
1. Look for a **`workspace.manifest.md`** — in the current repo root, or in a sibling/parent `playbook/` directory. Its presence means you are in the **workspace model**.
2. If found:
   - Read the **playbook's** `docs/` in full — especially **`integration-map.md`** (the cross-repo seams) and the project-wide `architecture.md`.
   - Read `workspace.manifest.md` to learn **every repo** in the project and where each one's `contracts.md` lives.
   - Identify the **home repo** — the repo the current working directory belongs to (or the one the user's request targets). You'll run Steps 1–2 against the home repo.
   - Note which sibling repos are actually checked out locally. If a repo named in the manifest is **not** present, say so — you may be reasoning about a cross-repo contract without being able to read the other side; flag that rather than guessing.
   - **Run the doc-aggregation drift check:** `sh playbook/scripts/aggregate-docs.sh --check`. If it reports the rollup is **stale** (a repo's `/docs` moved ahead of the playbook mirror), run `sh playbook/scripts/aggregate-docs.sh` to refresh it **locally** so this session reasons against current truth — then tell the user the committed rollup is stale and should be refreshed in a docs PR. Non-blocking; just never reason against a stale golden source. See § 6.6.
   - Acknowledge in one line, e.g. *"Workspace: 3 repos (web, api, infra); home repo = api; loaded playbook docs + integration-map; rollup current."*
3. If **not** found, you're in a single repo — say so in one line (e.g. *"Single repo; loading docs…"*) so the detected mode is visible and a misclassification is caught early, then proceed to Step 1 against this repo.

### Step 1 — Read the docs folder
1. Check whether `/docs` exists at the root of the **home repo** (in single-repo mode, that's just the repo).
2. If it exists, **read every file in it**. This is the repo's living memory: architecture, domain model, **contracts (every route, data contract, event, integration, env var, invariant)**, conventions, decisions, glossary, runbooks.
3. Briefly acknowledge to the user what you loaded (one line is enough — e.g. *"Loaded 8 docs: architecture.md, contracts.md, domain.md, glossary.md, …"*).
4. **Treat `contracts.md` as binding — and `integration-map.md` too in the workspace model.** Before you add, change, or remove any route, schema field, event, public function signature, integration call, environment variable, or other documented contract, find it in the home repo's `contracts.md` first. If your change would alter or remove something listed there, it is a **contract change** — call it out explicitly to the user, follow the regression-safety steps in § 7, and update `contracts.md` in the same pull request (which means the `/docs/*` consensus gate applies). **In the workspace model, also consult `integration-map.md`:** if the contract you're changing has a *consumer in a sibling repo*, that sibling is in your blast radius — read its `contracts.md` and coordinate the change (§ 6.5, § 7). If you discover a contract in the code that is **not** documented, that is a gap — surface it; don't silently rely on it.

### Step 2 — Read the tasks ledger
1. Open [`tasks/INDEX.md`](./tasks/INDEX.md) for the at-a-glance view.
2. For any **in-flight** task that looks relevant, open its per-task file at `tasks/T-NNN.md` for full context (description, acceptance criteria, activity log).
3. If the user's request relates to an existing task, reference its ID (`T-NNN`) in your reply rather than starting a parallel thread of work.
4. New task? Copy `tasks/_TEMPLATE.md`, pick the next free ID, set `state: proposed`. **One file per task** — never bulk-rewrite the ledger.
5. **`tasks/` is this project's ledger. Ignore `.starter/`** if it exists: it is the upstream starter's own task and decision history, not this project's state. Never read it for project work, and exclude it when searching for a task ID (`grep -r T-NNN --exclude-dir=.starter`). A comment saying "starter T-0NN" refers to that upstream history. The one exception is when you are changing the starter itself upstream; then `.starter/README.md` says where its ledger and review artifacts go.

### Step 3 — If `/docs` is empty or missing
This is the **first and only task** for the session until it's resolved. Tell the user:
> *"This project has no `/docs` folder yet. Per project convention, before any feature work I need to build it — not as a summary, but as a complete, code-grounded source of truth (every route, data contract, integration, env var, and invariant) so that future commits can be made safely without breaking what already exists. I'll run the full 9-agent review cycle to populate it with unanimous consensus, then we can start on what you actually asked for."*

Then run the **Docs Bootstrap Workflow** (§ 6), holding every doc to the **Grounding & Completeness Protocol** (§ 6.0): grounded in real code, no invented facts, exhaustive where it's a contract. The bootstrap subtasks are already seeded in `/tasks/` as `T-001` and `T-001.1` through `T-001.17` (split into Tier 1 and Tier 2 — see § 6; `T-001.17` is the exhaustive `contracts.md` inventory).

### Step 4 — Unanimous-consensus rule for `/docs`
No agent may add, edit, or remove anything in `/docs` unilaterally. Every change requires sign-off from **all 9 agents** (§ 1). The Review Coordinator (§ 1.9) drives this. Reason: docs are load-bearing for every future session — one wrong fact propagates forever.

> **If consensus stalls:** unanimous consent is powerful but can create a single veto point. When the cycle deadlocks on a documentation change, escalate via the tiebreaker rule in § 3 — the Directly Responsible Individual for the affected area decides, with the reasoning recorded as an Architectural Decision Record. Persistent vetoes against documented reasoning are themselves reviewable.

> **Note:** `/tasks/*` does **not** require separate unanimous consensus — it's updated as part of the normal review cycle for the underlying work. Only `/docs/*` carries the consensus gate.

---

## 1. The Agent Team (9 Specialists)

Every significant piece of work is reviewed by these 9 specialists before it ships. Each owns a domain and **wins** conflicts within it (§ 3).

| # | Agent | Role | Wins on |
|---|-------|------|---------|
| 1 | **Analyst** | Requirements, acceptance criteria, scope control | Scope — if it's not in the acceptance criteria, it's not in this pull request |
| 2 | **Architect** | Cloud architecture, security, scalability | Security — always |
| 3 | **Data Engineer** | Schema, queries, data accuracy, migrations | Schema & queries |
| 4 | **Backend Engineer** | Application Programming Interface routes, server logic, integrations | Application logic & feasibility |
| 5 | **Frontend Engineer** | user interface components, responsive design, client code | Technical feasibility on user interface |
| 6 | **UX Designer** | Usability, accessibility, interaction design | User-facing decisions |
| 7 | **Test Engineer** | Test automation at every level | Test coverage — always. *"We'll add tests later"* is never acceptable |
| 8 | **DevOps / Ops** | Continuous Integration / Continuous Delivery, deployment, monitoring, reliability | Deployment safety |
| 9 | **Review Coordinator** | Orchestrates rounds, gathers sign-offs, owns the review artifact | Process — owns the gate; can BLOCK on procedural failures |

Detailed role definitions live in `.claude/agents/*.md` (and are mirrored as plain reference in `/docs/agents/` once the docs folder is built).

---

## 2. Review Cycle (3 Rounds)

Run sequentially. Each round's blockers must be resolved before the next round begins. The **Review Coordinator** runs the cycle.

| Round | Phase | Agents (run in parallel within the round) |
|-------|-------|-------------------------------------------|
| 1 | **Analysis** | Analyst + Architect + Data Engineer |
| 2 | **Implementation** | Backend + Frontend + UX |
| 3 | **Verification** | Test Engineer + DevOps |

The Review Coordinator participates in every round but does not have a "win on" domain — its authority is procedural.

---

## 3. Conflict Resolution

When agents disagree, these defaults apply unless explicitly overridden by the user:

- **UX vs Frontend** — UX wins on user-facing. Frontend wins on feasibility. If UX wants something expensive, UX must propose a simpler alternative.
- **Backend vs Data** — Data wins on schema/queries. Backend wins on application logic.
- **Architect vs Everyone** — Architect wins on security.
- **Analyst vs Everyone** — Analyst wins on scope.
- **Test Engineer vs Everyone** — Test Engineer wins on test coverage.
- **Review Coordinator vs Everyone** — Coordinator wins on process. Can BLOCK if rounds were skipped, sign-offs missing, or artifact incomplete.

### When humans disagree (multi-engineer teams)

The defaults above resolve **agent vs agent** conflicts. When two **humans** disagree on the same call, the resolution path is:

1. **Named Directly Responsible Individual decides.** Every code area has a Directly Responsible Individual in `/docs/ownership.md` (and `.github/CODEOWNERS`). The Directly Responsible Individual for the affected area is the tiebreaker.
2. **If the disagreement spans areas**, the next level up in `/docs/ownership.md` (e.g. tech lead, eng manager) decides.
3. **Decisions that meaningfully shape the product** are recorded as an Architectural Decision Record in `/docs/decisions/` regardless of who made them — so future engineers can trace the why.
4. **An agent's redline (§ 1) is not a tiebreaker target** — humans cannot override an agent redline informally. Crossing a redline requires an explicit Architectural Decision Record accepted by the named Directly Responsible Individual **and** leadership.

Unresolvable conflicts are surfaced to the human Directly Responsible Individual with each side's position stated plainly.

---

## 4. The 7 Strategic Lenses

Every **major** decision — architecture, feature scope, pricing, tooling, or anything else that shapes the direction of tldw — must be evaluated through all 7 lenses **before** implementation. Each lens is a stand-in for a stakeholder whose perspective is easy to miss when an engineer or AI agent is heads-down on the work.

1. **CEO Lens** — Does this fit the strategy? What's the story to the board / leadership / public if it succeeds? If it fails? Any internal political risk in choosing this path?
2. **Purchasing Lens** — Cost vs. value? Security and compliance posture? Vendor lock-in and third-party risk? Would the procurement / legal team approve the dependencies?
3. **Product/PM Lens** — Is this buildable in a reasonable timeframe? What are the dependencies and unknowns? Scope-creep risk? Is this the right thing to build *right now*, or should something else be ahead of it?
4. **Adopter Lens** *(end-user / customer / operator who has to live with it)* — Will the people this is built for actually adopt it? Does it make their work easier, or does it add friction? Will it become part of their routine, or sit unused?
5. **Builder Lens** *(engineer / maintainer)* — Will the people maintaining this code be proud of it a year from now? Is the design respectful of their time? Would you want to be on-call for it?
6. **Investor Lens** — If someone were funding this work, would they see a path to value? Market or demand signal? Defensible advantage? Execution risk? Unit economics where applicable?
7. **Marketing/Sales Lens** — Can this be explained in 60 seconds to a non-expert? Does it demo well? What are the predictable objections, and is there an answer for each? Is there a clean before/after story?

**Surface conflicts between lenses explicitly.** If the Investor Lens loves it but the Builder Lens hates it, that's a red flag that must be resolved before proceeding — not papered over.

---

## 5. When to Run What

| Change type | Review scope |
|-------------|--------------|
| New features, user interface screens, architecture decisions | **Full cycle** — all 9 agents + 7 lenses |
| **Any change to a documented contract** (route, schema field, event, public signature, integration, env var, flag in `/docs/contracts.md`) | **Full cycle** + mandatory regression-safety check (§ 7) + `contracts.md` updated in the same pull request |
| Schema or security-sensitive work | Architect + Data + Test Engineer minimum, Coordinator drives |
| Bug fixes | Backend + Test Engineer + Ops |
| user interface polish | Frontend + UX |
| Config/infra changes | Architect + Ops |
| Query optimization | Data + Backend |
| Docs (`/docs/*`) | **All 9 — unanimous consensus required** |
| **Lightweight** — typo fixes, dependency lockfile bumps, comment-only edits, formatter-only churn, generated-file refreshes | **One agent (Review Coordinator)** + one human approver. No full cycle. Must declare in the pull request template as "Lightweight review" with justification. Coordinator BLOCKs if scope is mis-classified. |

> **What does NOT qualify as lightweight:** any behavior change, any user-visible change, any new dependency (even a patch bump if a transitive carries a known security vulnerability), any schema change, any change that touches `/docs/*` or `.github/CODEOWNERS`, any change to `.claude/agents/*.md` (including redlines), any security-relevant change. When in doubt, run the full cycle.

---

## 6. Docs Bootstrap Workflow

Triggered when `/docs` is missing or empty at session start. The workflow runs in **two tiers** so that feature work isn't blocked behind every doc the team will ever need.

> **The standard the bootstrap must meet.** The bootstrap is not "write some docs about the project." It is: *produce a living memory complete and accurate enough that a future agent who has read only `/docs/` can make a commit with high confidence it will not break an existing route, data contract, integration, or behavior.* A doc that reads well but omits a route, mis-states a schema, or invents a fact that isn't in the code is **worse than no doc** — it produces confident, wrong commits. Every Tier 1 and Tier 2 doc is held to the **Grounding & Completeness Protocol** below before any agent may sign off.

### 6.0 Grounding & Completeness Protocol (applies to EVERY doc)

No agent may sign off on a doc — at bootstrap or any later edit — unless it satisfies all of the following. The Review Coordinator BLOCKs any doc that doesn't.

1. **Grounded, not guessed.** Every factual claim is traceable to something real in the repository — a file, a route definition, a schema/migration, a config key, a CI workflow, an infra manifest. Where practical, cite the path (e.g. *"see `src/routes/billing.ts`"*). If the codebase is empty (greenfield), say so explicitly and mark the doc as **forward-looking** (describes the intended state) rather than **descriptive** (describes what exists) — never blur the two.
2. **No invented facts.** If a fact can't be confirmed from the code or from the human user, it does **not** go in the doc. Write `UNKNOWN — needs human confirmation` and open a follow-up task. A plausible guess that looks like a confirmed fact is the single most dangerous failure mode for living memory; an honest `UNKNOWN` is safe.
3. **Exhaustive where it's a contract.** For anything an autonomous commit could break — routes/endpoints, request/response shapes, event schemas, public function/module signatures, database tables and columns, environment variables, feature flags, third-party integrations, cron jobs, queues — the doc (or `contracts.md`) must enumerate **every** instance, not a representative sample. "etc." and "for example" are not acceptable in a contract inventory. If the list is long, it's still complete; length is not an excuse for sampling.
4. **States invariants and breakage modes.** Each contract entry notes what must stay true (the invariant) and what would break if it changed (who calls it, what depends on it). This is what lets a future agent reason about blast radius before editing.
5. **Verification recorded.** The drafting agent states *how* the doc was verified against the code — e.g. "enumerated routes by grepping the router definitions and cross-checking the OpenAPI spec; reconciled against integration tests." The reviewing agents check the doc against the code themselves, not just against the prose. A doc whose claims weren't independently checked against the source does not pass.
6. **Freshness contract.** Each doc names what code paths, when changed, require it to be updated (a "update-this-doc-when" footer). This is what keeps living memory from rotting — § 7 ties commits that touch those paths back to the doc.

A doc that cannot yet meet (3) because the relevant subsystem doesn't exist yet is fine — mark it forward-looking and list the contracts as "planned." A doc that *claims* completeness it doesn't have is a BLOCK.

### Tier 1 — must complete before feature work begins

The minimum context every engineer needs before writing the first line of feature code **and the full contract inventory needed to commit safely.** Contract-critical docs (`contracts.md`, `api.md`, `data-model.md`) are in Tier 1 on purpose: an agent doing feature work the moment Tier 1 lands must already know every route and schema it could break.

1. **Analyst** drafts the docs index.
2. **Architect** drafts: `architecture.md` (system overview, security model, trust boundaries, integration map), `stack.md` (languages / frameworks / services and the reason each was chosen), `decisions/` skeleton, and Architectural Decision Record 0001 capturing the foundational stack choice.
3. **Backend + Data Engineer + Architect** jointly draft **`contracts.md`** — the exhaustive, code-grounded inventory of every route/endpoint, request/response contract, event/message schema, public interface, integration, environment variable, feature flag, and cross-cutting invariant. This is the single most important doc for safe autonomous commits. It must satisfy § 6.0(3) — enumerate **everything**, no sampling.
4. **Backend** drafts `api.md` — the narrative companion to `contracts.md`: protocol, conventions, auth model, versioning/deprecation, and *how to add or change a route safely* (the rules, not just the list).
5. **Data Engineer** drafts `data-model.md` — schema, every table/column, indexes, PII classification, migration approach, and *how to change the schema safely* (reversibility, deploy sequencing, backfill rules).
6. **Test Engineer** drafts `testing.md` (strategy, levels, coverage gates, test-data approach) and documents the **regression-safety check** (§ 7) — how an agent proves a change didn't break a documented contract.
7. **DevOps** drafts `deployment.md` (environments, Continuous Integration / Continuous Delivery, promotion path, rollback).
8. **Architect + Analyst** draft `ownership.md` (Directly Responsible Individual map; mirrors `.github/CODEOWNERS`).
9. **Analyst** drafts `onboarding.md` (first-day path for new engineers).
10. **Review Coordinator** sets up `reviews/` skeleton, then runs the 3-round review on the drafted Tier 1 docs, gathers all 9 sign-offs, **verifies each doc against the Grounding & Completeness Protocol (§ 6.0)**, and only then writes files to `/docs/`.

After Tier 1 lands, feature work may begin — and an agent reading `/docs/` now has the full route + schema + integration inventory needed to commit without breaking things.

### Tier 2 — must complete by end of the first feature sprint

The reference material that catches up while feature work runs in parallel. Same unanimous-consensus rule and same Grounding & Completeness Protocol apply; the difference is only timing.

11. **Data Engineer + Analyst** draft `domain.md`.
12. **UX Designer** drafts `ux.md`.
13. **DevOps** drafts `runbooks/` skeleton + at least one concrete playbook.
14. **Analyst** drafts or migrates `glossary.md` from `domain.md`.
15. **Review Coordinator** runs the 3-round review on the Tier 2 docs, gathers all 9 sign-offs.

Tier 2 slipping past the first sprint is a process failure and should be retrospected.

**Recommended `/docs/` skeleton** (rename / omit anything that doesn't apply to your stack):

```
/docs
├── README.md              # how to use this folder; consensus rule restated
│
├── architecture.md        # Tier 1 — system overview, services, data flow, trust boundaries
├── contracts.md           # Tier 1 — ⭐ EXHAUSTIVE inventory: every route, data contract,
│                          #          event, integration, env var, flag, invariant. Code-grounded.
├── api.md                 # Tier 1 — API surface narrative + how to change a route safely
├── data-model.md          # Tier 1 — schema, every table/column, migrations + how to change safely
├── stack.md               # Tier 1 — languages, frameworks, services in use, and why
├── testing.md             # Tier 1 — strategy, levels, coverage gates, regression-safety check
├── deployment.md          # Tier 1 — environments, CI/CD, promotion path, rollback
├── ownership.md           # Tier 1 — Directly Responsible Individual map
├── onboarding.md          # Tier 1 — first-day path for new engineers
├── decisions/             # Tier 1 (skeleton) — Architectural Decision Records
├── reviews/               # Tier 1 (skeleton) — review artifacts per branch
│
├── domain.md              # Tier 2 — business domain, entities, vocabulary
├── ux.md                  # Tier 2 — personas, journeys, design system pointers
├── glossary.md            # Tier 2 — terms used across the codebase
└── runbooks/              # Tier 2 — on-call procedures
```

> **`contracts.md` vs. `api.md`/`data-model.md` — why both.** `contracts.md` is the *flat, exhaustive index* an agent scans to answer "does my change touch anything that already exists?" `api.md` and `data-model.md` are the *narrative* — the conventions and the safe-change procedure for routes and schema respectively. The inventory tells you what exists; the narratives tell you how to extend it without breaking it. On a large codebase `contracts.md` may itself point to generated/machine-readable sources (OpenAPI, schema dumps) — that's fine, as long as the pointer is exact and the generation is reproducible. A hand-wavy summary is not.

---

## 6.5 Multi-Repo Projects — The Workspace Model

Many real projects are **one product split across several repos** (e.g. `web`, `api`, `infra`) that can't or shouldn't be merged into a monorepo. This starter supports that without losing cross-repo safety. The mechanism is deliberately low-friction: no submodules, no repo consolidation — just a shared playbook, a manifest, a central integration map, and a setup script.

### The shape

The forked starter stops being "one project's repo" and becomes a **shared playbook** the team forks, commits, and shares. It sits beside the code repos in a **workspace** folder:

```
<workspace-root>/                 # a plain folder, not a repo
├── playbook/                     # ← the forked starter (shared, team-committed)
│   ├── AGENTS.md, CLAUDE.md      #    the conventions (this file)
│   ├── .claude/agents/           #    the 9 specialists
│   ├── .githooks/                #    hook templates, installed into each repo
│   ├── workspace.manifest.md     #    ⭐ the repo roster + where each contracts.md lives
│   ├── docs/
│   │   ├── architecture.md       #    project-WIDE architecture (spans all repos)
│   │   └── integration-map.md    #    ⭐ the cross-repo seams (APIs, events, env, stores)
│   ├── tasks/                    #    project-level / cross-repo work
│   └── scripts/setup-workspace.sh#    one scripted step: lay out repos + install hooks
├── web/    (its own git repo)    # ← adopts the playbook: own docs/ (incl. contracts.md), tasks/, hooks
├── api/    (its own git repo)
└── infra/  (its own git repo)
```

### Two levels of living memory

- **Playbook `docs/` (project level):** the cross-repo architecture and the **integration map**. Shared, committed once, pulled by everyone.
- **Each repo's own `docs/` (repo level):** that repo's `architecture.md`, `contracts.md`, `data-model.md`, `api.md`, etc. — its self-contained source of truth.

`integration-map.md` is the **only** authoritative record of how the repos connect (see its template). Each repo's `contracts.md` describes that repo's own surface and links *up* to the integration map for its cross-repo edges. When a contract changes, the map is what tells an agent **which sibling repos consume it** — that's the cross-repo blast-radius signal a set of disconnected repos can't produce.

> **The playbook's own `docs/` is bootstrapped like any repo's** (§ 6) — `architecture.md` here is the *project-wide* one. Until it's bootstrapped, an agent should treat a missing playbook doc as "not built yet," not an error, and proceed with the integration map + manifest it does have.

### Adopting the model (the manual steps, scripted)

1. Fork this starter as the team's **playbook** repo. Replace placeholders (§ 10), fill in `workspace.manifest.md` with the project's repos.
2. Check out the code repos as **siblings** of `playbook/` under the workspace root.
3. Run `sh playbook/scripts/setup-workspace.sh` — it installs the playbook's hooks into **every** repo (`core.hooksPath`) and reports any repo from the manifest that isn't checked out. Re-run whenever a repo is added.
4. Ensure each code repo also runs the **CI mirror** so `--no-verify` can't bypass the gates server-side. Hooks are per-repo; CI must be too. That's GitHub Actions (`.github/workflows/checks.yml`, on by default) or Azure DevOps (`azure-pipelines.yml`, one pipeline per repo); see `ADOPTING.md` § 4. **Co-install the matching `.github/pull_request_template.md`** — the pipeline's PR-body check asserts the template's section headings, so a repo running the pipeline without the template will fail every PR. A repo that skips CI entirely has *no* server-side backstop, so `--no-verify` fully bypasses its gates — don't.
5. Each code repo runs its **own docs bootstrap** (§ 6) before its first feature — its `contracts.md` becomes a node the integration map connects.
6. The Architect + Backend + Data Engineer populate `integration-map.md` with the verified seams (held to § 6.0 — exhaustive, grounded, no invented edges).

### What this buys you

An agent launched inside **any one** repo reads the playbook's integration map first, knows the full repo roster from the manifest, and reads the home repo's `contracts.md` — so it can change `api`'s route and immediately see *"`web` consumes this"* and act accordingly. That cross-repo awareness is the entire point: **well-informed changes that don't break a sibling repo.**

### Honest limits (per-repo enforcement)

Git enforcement is per-repository — there is no single hook that governs three repos. Consequences, stated plainly:
- Hooks must be **installed in each repo** (the setup script does this; re-run it after cloning a new machine).
- A change spanning two repos is **two commits in two pull requests**, coordinated — not one atomic commit. The integration map + the regression-safety check (§ 7) are what keep them in step; sequence producer-before-consumer for backward-compatible changes.
- Cross-repo visibility is a **local side-by-side checkout**. A fresh clone of one repo alone won't see its siblings until they're checked out into the workspace. Keep the manifest accurate so the gap is at least visible.

---

## 6.6 Doc Aggregation — the playbook as golden source of truth

In the workspace model, `playbook/docs/` must be the **complete, current** picture of the whole project — so that an agent (or human) can read *one* place and see everything. That is achieved by **aggregating up**, not by mirroring docs sideways between repos.

### The model: aggregate up, single writer per fact

- **Each repo's `/docs` is the single writer for that repo's own facts** — its `contracts.md`, `architecture.md`, runbooks. Nothing else writes there. (Single writer = no clobber, the failure mode bilateral sync invites.)
- **`scripts/aggregate-docs.sh` mirrors** every repo's `/docs` into `playbook/docs/repos/<name>/` — read-only, SHA-stamped with the source repo's `HEAD:docs`. This is **generated**; never hand-edit it (edit the source repo's `/docs`).
- **Cross-repo / project-wide docs stay hand-authored in the playbook only** — `integration-map.md`, the project-wide `architecture.md`, shared ADRs. These are *not* aggregated; they're authored once, where they belong.

```
playbook/docs/
├── integration-map.md      authored — cross-repo seams (consensus-gated)
├── architecture.md         authored — project-wide (consensus-gated)
├── decisions/              authored — shared ADRs
└── repos/                  GENERATED mirror — do not hand-edit
    ├── web/   (= web/docs   @ <sha>)
    ├── api/   (= api/docs   @ <sha>)
    └── infra/ (= infra/docs @ <sha>)
```

Result: `playbook/docs/` = project-wide authored docs **+** a current mirror of every repo = the golden source of truth, with no clobber.

### How it stays current ("bilateral reconciliation, done safely")

1. **As changes occur:** the PR that changes a repo's `/docs` also refreshes and commits the rollup (`sh playbook/scripts/aggregate-docs.sh`, commit `docs/repos/`).
2. **Session-start backstop (§ 0 Step 0):** every workspace session runs `aggregate-docs.sh --check`. If a repo's `/docs` advanced past the mirror, the agent refreshes locally (so it reasons against truth) and flags that the committed rollup is stale. This is what catches a forgotten step — the drift can't silently persist.
3. **Staleness is detectable, not guessed:** the SHA stamp means "is the mirror current?" is a cheap, exact comparison — the same drift-guard principle `contracts.md` uses against code.

### Consensus gate carve-out

`docs/repos/**` is **exempt** from the `/docs/*` unanimous-consensus assertion (hooks + CI). Its content was already gated by consensus **in its source repo**; re-gating the mirror would force a consensus assertion on every routine refresh. Authored playbook docs (everything in `docs/` *outside* `docs/repos/`) keep the full gate.

---

## 7. Branch & Commit Workflow

All work happens on feature branches. **No direct pushes to `main`.**

**Branch naming:**
- `feat/<short-name>` — new features
- `fix/<short-name>` — bug fixes
- `chore/<short-name>` — infra, docs, process
- `refactor/<short-name>` — restructuring without behavior change

**Flow:**
1. **Open** (or update) the task file at [`tasks/T-NNN.md`](./tasks/). New tasks start as `proposed` — copy `_TEMPLATE.md` and pick the next free ID.
2. Create branch from `main`. Update the task's **State → `in-review`** and fill in **Branch** in the frontmatter; append a line to the activity log.
3. **Plan** the work — what changes, which files, what approach.
4. Run the agent review on the **plan** — get sign-off *before* writing code. Update **State → `in-progress`** once approved.
5. **Build** — implement the approved plan.
6. Run the agent review on the **build** — full cycle (or lightweight per § 5).
7. **Test** — automated tests, not manual checks. Every code path exercised.
8. **Regression-safety check (mandatory before commit).** Prove the change did not silently break a documented contract — in this repo or a sibling:
   - Diff your change against `/docs/contracts.md`. For every route, schema field, event, public signature, integration, env var, or flag your change touches, confirm it was *intended* and that the change is reflected back in `contracts.md` (and `api.md` / `data-model.md` as applicable) **in this same pull request**.
   - If you changed or removed something in `contracts.md`, that is a **contract change**: it requires the `/docs/*` unanimous-consensus gate, an explicit note to reviewers of the blast radius (who/what depends on it), and either backward compatibility or a documented migration/deprecation path.
   - **Cross-repo (workspace model):** consult `integration-map.md`. If the contract you changed has a **consumer in a sibling repo**, that sibling is in your blast radius. Either keep the change backward-compatible, or open a coordinated follow-up task/pull request in the consumer repo and update the integration map in the same change — sequence **producer-before-consumer** so the consumer is never pointed at a contract that doesn't exist yet. Never land a breaking producer change with the consumer left dangling. Two more rules make this real, not aspirational:
     - **Hard stop on an unreadable consumer.** If a declared consumer repo is *not checked out locally*, you cannot verify its read site — do **not** land a breaking producer change. Either get the repo checked out or keep the change backward-compatible.
     - **The consumer follow-up is a required, linked artifact.** Record the consumer-repo task ID on the producer change; the producer is not "complete" until that task exists and is referenced. This is what stops a dual-write/dual-read from becoming permanent because the `contract` (cleanup) step was forgotten.
   - Run the test levels that exercise the touched contracts (contract tests, integration tests, end-to-end per `testing.md`). A green unit suite alone does not satisfy this step.
   - State in the review artifact: *"Regression-safety: contracts touched = […]; reflected in docs = yes/no; cross-repo consumers = none | [repo:surface]; compatibility = backward-compatible | migration at <path>."* "None" is a valid answer and must be stated explicitly, not omitted.
9. **Commit** — only after all required agents sign off, tests pass, and the regression-safety check is recorded. **Include the task-file update in this same commit** (not a follow-up PR): set **State → `completed`**, fill in **Branch**, **Review artifact** path, and **PR number** once known (amend the last commit, or push it as the PR's final commit), and append to the activity log. **Do not hand-record the merge SHA** — you can't know your own commit's SHA inside it, and chasing it after merge is exactly what spawns noisy follow-up PRs. The `commit` field is optional and derived later (`git log --grep T-NNN`); the INDEX regen can backfill it. See the note below on why marking `completed` pre-merge is safe.
10. **Push** immediately after sign-off. Don't ask, just push.
11. Open a pull request using `.github/pull_request_template.md`. Link the review artifact. Add the PR number to the task file's `pr:` field and push that as the final commit.
12. **Human review:** at least one approver per `.github/CODEOWNERS` for the affected paths, in addition to all required agent sign-offs. Agent sign-off does not replace human review — they're complementary.
13. Continuous Integration must pass before merge. The procedure checks run server-side via `scripts/ci-checks.sh` (the CI mirror of `.githooks/`) — bypassing local hooks with `--no-verify` does not bypass Continuous Integration. See **CI provider** below for which system runs it.
    **Wait for it yourself.** Don't end your turn asking a human to tell you when checks finish. Read the result for your latest commit when it should be ready, using a timer or your tool's CI monitor if it has one, then act on it. Fix the cause of a failure under the normal gates, never by loosening a gate, allowlist or test. Once green, continue the remaining steps; green doesn't replace step 12's approval. If your tool can't wait, checks run far past their usual time, or a second fix fails, stop, say so and name what's left. A check that never appears is a finding to report, and pending or missing is never a pass.
14. **Review Coordinator regenerates `tasks/INDEX.md`** at the end of the cycle so the at-a-glance view stays accurate.

> **Why marking `completed` before merge is safe — and why there are no follow-up "mark it done" PRs.** The ledger update rides in the *same* PR as the work, so a `completed` state only reaches `main` when that PR merges. A task shown `completed` on `main` is therefore — by construction — merged. Consequences, all self-correcting, no bot required:
> - **PR abandoned/closed** → its `completed` line never reached `main`; nothing to unwind.
> - **PR reopened** → still just a branch; `main` is untouched until it merges.
> - **Merged then reverted** → the revert reverts the ledger line too (it was one atomic commit).
>
> `main` is the source of truth and is never wrong. A *feature branch* may briefly show `completed` while its PR is open — read that as "claimed done, pending merge." This is why the terminal step records `branch` + `pr` (both known before merge) and **not** the merge SHA (knowable only after, and the thing that used to force a noisy second PR).

### CI provider — GitHub Actions by default, or Azure DevOps

Two CI systems are supported and run the same checks; pick by where your pipelines live (`ADOPTING.md` § 4, which also covers running both).

- **GitHub Actions** — [`.github/workflows/checks.yml`](./.github/workflows/checks.yml). The default: it runs on every PR and push to `main` as soon as the repo is on GitHub, with nothing to set up. It runs with least privilege (no token permissions by default, read-only contents, no saved git credentials, actions pinned by commit SHA) and uses `pull_request`, never `pull_request_target`.
- **Azure DevOps** — [`/azure-pipelines.yml`](./azure-pipelines.yml), for teams whose pipelines live there (code stays in GitHub; the Azure Pipelines GitHub App connects Azure to the repo). Create one pipeline per repo; its `pr:` trigger builds every PR. A team that runs only Azure parks the Actions workflow at `.github/workflows-disabled/checks.yml` so PRs aren't checked twice.
- **Both CI systems call the same [`scripts/ci-checks.sh`](./scripts/ci-checks.sh)** — the single source of truth for the procedure gates (placeholders, secrets, task-ID, `/docs/*` consensus). This is what keeps them from drifting. On a PR build they run the **target branch's** copy (and its `.placeholder-allow`) through [`scripts/ci-pr-checks.sh`](./scripts/ci-pr-checks.sh), so a PR can't loosen the gate it is judged by; a gate change applies from the next PR on (starter T-014, `ADOPTING.md` § 5).
- **Both pin their tools** — the Ubuntu image and shellcheck (version and checksum) — to the same values; `scripts/test/test-ci-wiring.sh` checks they match.

> ⚠️ **Nothing is enforced until the checks are required.** CI that runs but isn't required on `main` blocks nothing, and `--no-verify` bypasses the hooks. Require the checks of each system you run, each with its expected source (`ADOPTING.md` § 5). An Azure-only repo has no server-side gate until its pipeline exists; a repo whose org disables Actions gets no Actions checks and no warning. Treat wiring CI as part of repo setup, not an afterthought.

### Hooks (one-time setup, every fresh clone)

**Single repo:** run `scripts/setup-hooks.sh` once after cloning. **Multi-repo workspace:** run `scripts/setup-workspace.sh` from the playbook — it installs the hooks into every repo in the manifest (§ 6.5). Either way `core.hooksPath` is set to `.githooks/` so the hooks run on every commit and push. The hooks enforce:

- No direct commits to `main` / `master` / `production` / `release` / `trunk`.
- No `[insert … here]` placeholders in staged content.
- Basic secret scan (AWS / GitHub / Slack / OpenAI / Anthropic tokens, private-key blocks).
- Task ID (`T-NNN`) required in every commit message.
- `unanimous-consensus: T-NNN` required in any commit touching `/docs/*`.
- No force-push to protected branches.

Stack-specific lint/format/type-check hooks are configured separately via `.pre-commit-config.yaml` (template, mostly commented).

### Keeping a fork in sync with the starter (upstream sync)

The team's playbook is a **fork** — tweak it freely, but keep pulling improvements to the shared procedure so forks don't rot into incompatible dialects. One-time, on the fork:

```sh
git remote add upstream <url-of-the-canonical-agentic_starter>
```

Periodically (e.g. once a sprint), pull upstream changes to the **policy files** and resolve conflicts in favor of your local tweaks where they're intentional:

```sh
git fetch upstream
git merge upstream/main          # or: git cherry-pick the policy commits you want
```

The policy surface worth syncing: `AGENTS.md`, `.claude/agents/*.md`, `.githooks/*`, `scripts/*` (incl. `ci-checks.sh`, `aggregate-docs.sh`), `.github/pull_request_template.md`, `azure-pipelines.yml`, `.github/workflows/checks.yml` (or its parked path), `.placeholder-allow`, `docs/README.md`, and the task `_TEMPLATE.md`. Your *content* — filled-in placeholders, real `contracts.md`, real tasks — is yours and won't conflict if you kept it out of the template files.

Two cautions, because this merge touches the files that gate everything:
- **Treat the upstream merge as a config/infra change** — run it through the review cycle (Architect + Ops per § 5). A careless conflict resolution in `.githooks/*`, `scripts/ci-checks.sh`, or `azure-pipelines.yml` can silently weaken or disable a gate, and nothing else will catch it. Diff the resolved hooks/CI against upstream to confirm no gate was dropped. "Resolve in favor of local" is the wrong default *for the enforcement files specifically*.
- **`workspace.manifest.md` and `docs/integration-map.md` are templates you fill in place**, so they *will* conflict on upstream sync (unlike `contracts.md`, which lives in each repo, not the template). Expect it; reconcile structure-from-upstream with your-content-preserved. A bad resolution here is caught by the `/docs/*` consensus gate.

Record any intentional divergence from upstream as an Architectural Decision Record in `decisions/` so the next person knows it's deliberate, not drift.

---

## 8. Hard Gates — No Exceptions

> **Mechanically enforced vs. review-enforced.** Most gates below are enforced by the git hooks + CI mirror (`.githooks/`, `scripts/ci-checks.sh` run by `azure-pipelines.yml`) — a machine blocks them. A few — marked *(review-enforced)* — cannot be checked by a per-repo hook (e.g. anything cross-repo, since one repo's hook can't see a sibling). Those depend on the review cycle and human discipline. They are no less mandatory, but be honest that nothing mechanical catches them: the cost of separate repos instead of a monorepo.

- No code without a reviewed plan.
- No commit without agent sign-off.
- No merge without at least one human approver per `.github/CODEOWNERS`. **(Solo-repo exception:** GitHub forbids self-approval, so a one-person repo runs 0 required approvals — the PR + CI gates still apply. The human-approver gate activates the moment a second maintainer joins; see `ADOPTING.md` step 5.)
- No commit without passing automated tests.
- No manual-only testing — every test runs in Continuous Integration.
- No intentional tech debt — build it right or don't build it.
- No edits to `/docs/*` without unanimous 9-agent consensus.
- **No agent redline crossed without a documented Architectural Decision Record** accepted by the area Directly Responsible Individual and leadership. Redlines live in `.claude/agents/*.md` and are non-negotiable by default.
- No lightweight-review claim on a change that doesn't qualify (see § 5). The Review Coordinator BLOCKs mis-classifications.
- **No commit that touches a documented contract without updating `/docs/contracts.md` (and `api.md` / `data-model.md` as applicable) in the same pull request.** Living memory that lags the code is how confident-but-wrong commits happen. A change to a route, schema field, event, public signature, integration, env var, or flag and its doc are one atomic change, not two.
- **No doc signed off that violates the Grounding & Completeness Protocol (§ 6.0)** — invented facts, sampled-not-exhaustive contract lists, or unverified claims are a BLOCK, not a warning.
- **(Workspace model — *review-enforced*) No breaking change to a contract with a cross-repo consumer left unhandled.** If `integration-map.md` shows a sibling repo consumes the contract, the change is backward-compatible or has a coordinated, sequenced consumer-side follow-up — and the integration map is updated in the same change. Producer-before-consumer ordering is mandatory. Note the unavoidable consequence: a cross-repo change is two pull requests that **cannot both pass CI atomically** — there is a transient window where the producer has merged and the consumer follow-up has not. Keep the producer change backward-compatible across that window; never let the window contain a broken consumer.

---

## 9. Review Output (Artifact)

Every review cycle produces a documented artifact. Use this template:

```markdown
# Review: <branch-name> — <round or "final">

**Status:** PASS | PASS WITH ITEMS | BLOCK

## Blockers (must fix before merge)
- [ ] …

## Warnings (fix before next review)
- [ ] …

## Suggestions (consider for polish)
- …

## Agent Sign-offs
- [ ] Analyst — <reason if unchecked>
- [ ] Architect —
- [ ] Data Engineer —
- [ ] Backend —
- [ ] Frontend —
- [ ] UX —
- [ ] Test Engineer —
- [ ] DevOps —
- [ ] Review Coordinator —

## Lens Sign-offs (major decisions only)
- [ ] CEO  - [ ] Purchasing  - [ ] PM  - [ ] Adopter  - [ ] Builder  - [ ] Investor  - [ ] Marketing
```

Artifacts live in `/docs/reviews/<branch>/<round>.md` once the docs folder exists.

---

## 10. Stack-Agnostic Placeholders

This starter is intentionally stack-neutral. Replace the bracketed placeholders below in your fork. Search the repo for `[insert ` to find every one.

| Placeholder | Replace with |
|-------------|--------------|
| `[insert cloud provider here]` | AWS / GCP / Azure / Vercel / Cloudflare / etc. |
| `[insert frontend framework here]` | Next.js / Remix / SvelteKit / Vue / plain React / etc. |
| `[insert backend framework here]` | Node + Express / FastAPI / Go + chi / Rails / .NET / etc. |
| `[insert database here]` | Postgres / MySQL / SQLite / DynamoDB / Mongo / etc. |
| `[insert authentication provider here]` | Clerk / Auth0 / Cognito / NextAuth / custom / etc. |
| `[insert Continuous Integration provider here]` | **GitHub Actions** (default) or **Azure DevOps Pipelines** — both shipped, see § 7 "CI provider" / GitLab Pipelines / CircleCI / etc. |
| `[insert hosting platform here]` | Vercel / Fly.io / Render / AWS ECS / k8s / etc. |
| `[insert monitoring here]` | Datadog / Sentry / Grafana / New Relic / etc. |
| `[insert test runner here]` | Vitest / Jest / Pytest / Go test / RSpec / etc. |
| `[insert end-to-end tool here]` | Playwright / Cypress / Selenium / etc. |
| `[insert package manager here]` | pnpm / npm / yarn / pip / poetry / cargo / etc. |
| `[insert language here]` | TypeScript / Python / Go / Rust / etc. |
| `[insert team name here]` | Your team or org name |
| `[insert product name here]` | The product/project this repo builds |
| `[insert primary user persona here]` | Who this product is for |
| `[insert secret manager here]` | AWS Secrets Manager / GCP Secret Manager / Vault / Doppler / 1Password / etc. |
| `[insert repo-N dir]` / `[insert role …]` / `[insert git URL]` *(workspace model)* | The repos that make up the project — fill in `workspace.manifest.md` and `docs/integration-map.md`. |

Agents should **flag** any unreplaced `[insert …]` they encounter during review.

---

## 11. Tool-Specific Notes

- **Claude Code** — Subagents live in `.claude/agents/*.md` and are invokable via the `Task` / `Agent` tool. `CLAUDE.md` exists and points back to this file.
- **Codex** — Reads this `AGENTS.md` natively. The `.claude/agents/*.md` files double as plain-markdown role briefs; copy / paste them into a system prompt when emulating a specialist.
- **Cursor / other agents** — Same convention: point the tool at `AGENTS.md` first.

The goal is **identical procedure regardless of which command-line tool the engineer uses**.

---

## 12. Scaling Down — Adapting for Small Teams and Early-Stage Projects

This starter is calibrated for enterprise / worldwide / high-stakes use. If your team is smaller, your stakes lower, or your stage earlier, scale the procedure to match — don't abandon it. Three knobs you can turn without losing the spirit:

### Knob 1 — One human, multiple agent hats
Nothing requires that each of the 9 agent roles maps to a distinct human. In a team of 3 engineers, one person may wear three hats — but the **review still runs as if those were separate voices**. When you sign off as the Architect, you sign off as the Architect, with the Architect's redlines in front of you. Then you context-switch and sign off as Test Engineer with the Test Engineer's redlines. Slower than one big think, but it forces the role-switch your team's missing specialists would have forced.

For solo founders: same thing, one person wearing all 9 hats sequentially. The friction is the feature.

### Knob 2 — Expand the lightweight review path
For a smaller team, the threshold for "lightweight review" (§ 5) can be raised — for example, "any change under 50 lines that touches no `/docs/*`, no `.claude/agents/*.md`, no schema, no authorization code, and no third-party dependency." The Review Coordinator BLOCKs mis-classifications regardless of team size, so the gate stays honest.

Document the threshold your team chooses in an Architectural Decision Record so it doesn't drift.

### Knob 3 — Defer Tier 2 docs, but never skip them
Tier 1 of the docs bootstrap (§ 6) is the line that should not move regardless of team size — every engineer needs `architecture.md`, `contracts.md`, `api.md`, `data-model.md`, `stack.md`, `testing.md`, `deployment.md`, `ownership.md`, `onboarding.md`. The contract-critical trio (`contracts.md`, `api.md`, `data-model.md`) is the *least* skippable part of Tier 1: it is exactly what lets a small team's lone engineer — or its agents — commit without breaking what already exists. Tier 2 (`domain.md`, `ux.md`, `glossary.md`, `runbooks/`) can be deferred further than the first sprint for small teams — *but it must be tracked*, and missing it past one quarter is a flag.

### What you should *not* turn off

- **Redlines.** They are calibrated for the highest-stakes case because the cost of being wrong there is asymmetric. A small team writing software for a regulated industry has the same redlines as a 500-person team in the same industry.
- **The git hooks and Continuous Integration mirror.** Mechanical enforcement is cheap; engineers under pressure are not.
- **The unanimous-consensus rule for `/docs/*`.** What it gates is the project's shared understanding. Eroding that to ship faster trades short-term velocity for long-term incoherence.

### The honest tradeoff

This procedure adds friction. That is the point. The friction is intended to catch the failures that are expensive in this kind of work. If your work *isn't* this kind of work — if you're prototyping for a demo, exploring a research question, or building something that doesn't need to survive contact with the real world — fork the starter, simplify it for your context, and document what you removed and why in an Architectural Decision Record. Don't pretend the full procedure is running when it isn't.
