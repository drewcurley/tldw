# `/docs` — tldw's Living Memory

This folder is tldw's **living memory and source of truth**. `AGENTS.md` § 0 Step 1 (`AGENTS.md:52-56`) requires every AI agent (Claude Code, Codex, Cursor, or any other) to read the files here at the start of every session, before doing any work. Which files that read covers is set out in [Session-start read](#how-to-use-this-folder-during-a-session) below.

tldw is a single repo (`CLAUDE.md:19`): a Python package (`src/youtube_tldw/`) that runs a loopback-only HTTP server, `tldw serve` (`src/youtube_tldw/server.py:1933-1938`), and a Manifest V3 browser extension in plain JavaScript (`extension/`) that calls it. It has no database, no cloud and no hosting. Each user runs their own server on their own machine, with their own model plan (`README.md:432-435`, `extension/README.md:110-115`). This checkout is the hackathon fork; its integration branch is `hackathon/demo` (`AGENTS.md:9`, `CLAUDE.md:18`).

**New here?** Start with [`onboarding.md`](onboarding.md): it gets tldw running in 30 minutes, then gives the read order.

## The bar this folder must meet

The test for whether `/docs/` is good enough is a single sentence:

> **An agent reading only `/docs/` can commit safely without breaking an existing route, data contract, integration, or behavior.**

That is a much higher bar than "documentation that describes the project." The docs must be **complete enough** to list everything an autonomous change could break, and **accurate enough** that nothing in them is a plausible-sounding guess. Both are enforced by the **Grounding & Completeness Protocol** in `AGENTS.md` § 6.0 (`AGENTS.md:178-189`). Read it before drafting or reviewing any doc.

For tldw, "everything a change could break" means: the HTTP routes `tldw serve` exposes, the NDJSON stream it sends, the messages the extension's content script and background worker exchange, the extension's `chrome.storage.local` keys, the on-disk JSON stores, the CLI's subcommands and flags, the environment variables and config keys, the external programs it runs (the model command, `yt-dlp`, `ffmpeg`), the YouTube page elements the extension reads, and the source text the tests depend on. All of these are listed in full in `contracts.md`.

## Why this folder matters

Without it, every session starts cold. The agents re-derive architecture from grep, re-discover conventions by trial and error, and, worst of all, invent **wrong** facts that look plausible. A confident agent acting on a wrong fact ships a confident, wrong commit. This folder prevents that, but only if it is grounded in real code and kept current with it.

## The Grounding & Completeness Protocol (summary)

The full version is `AGENTS.md` § 6.0. No agent may sign off on a doc unless it is:

- **Grounded, not guessed:** every claim can be traced to a real file, route, config key or workflow. tldw's docs cite `path:line` or a symbol.
- **Free of invented facts:** anything unconfirmed is written `UNKNOWN — needs human confirmation`, with a follow-up task, never as if it were settled.
- **Exhaustive where it's a contract:** routes, message and event types, storage keys, public signatures, env vars, config keys and integrations are listed in full, not sampled. A contract inventory never trails off with "and so on".
- **Explicit about invariants and blast radius:** each contract entry says what must stay true and what depends on it.
- **Verified:** the drafting agent records how it checked the doc against the code, and reviewers re-check against the source, not just the prose.
- **Self-dating:** each doc names the code paths that, when changed, require it to be updated.

Every doc here is **descriptive**: it describes code that exists today. None is forward-looking. Where a doc describes something not yet built, it says so and calls it "pending".

## Hard rule — unanimous consensus

**No file in this folder may be added, edited, or removed without sign-off from all 9 agents** defined in `AGENTS.md` § 1 (`AGENTS.md:80-96`). The Review Coordinator (`.claude/agents/review-coordinator.md`) drives this process. One wrong fact here propagates into every future session, so the bar is deliberately high.

If a single agent BLOCKs a `/docs/*` change, the change does not land. If consensus stalls, `AGENTS.md` § 0 Step 4 (`AGENTS.md:71-74`, the stall note at `:74`) sends it to the tiebreaker in § 3, where the Directly Responsible Individual for the affected area decides (`AGENTS.md:125-134`). In this repo that is the single owner; see `ownership.md`.

The rule is enforced mechanically as well as by review:
- the `commit-msg` hook rejects a commit that touches `docs/` without `unanimous-consensus: T-NNN` in its message (`.githooks/commit-msg:24-37`);
- CI runs the same check on every commit in the range (`scripts/ci-checks.sh:167`), so `--no-verify` does not get around it.

The workspace model's `docs/repos/**` carve-out (`AGENTS.md:334-336`) is N/A here: this is a single repo with no `docs/repos/`.

## § 0 Step 3 in this repo

`AGENTS.md` § 0 Step 3 (`AGENTS.md:65-69`) says that when `/docs` is empty or missing, building it is the first and only task. For tldw:

- **Until T-001's Tier 1 PR merges into `hackathon/demo`,** `docs/` on that branch holds only the 13 legacy files in `docs/reviews/` (listed [below](#legacy-review-files-pre-playbook-history)). The folder is not empty, but Tier 1 is **missing in substance**, so Step 3 applies: the bootstrap (T-001) is the only work, and no feature work starts.
- **Once it merges,** every Tier 1 doc is present and Step 3 no longer applies. Feature work may begin (`AGENTS.md:206`).
- **Tier 2 being open does not block feature work.** Tier 2 has the same consensus rule and protocol, and differs only in timing (`AGENTS.md:208-210`). The owner deferred it under `AGENTS.md` § 12 Knob 3 (`AGENTS.md:538-539`) on 2026-11-12, with a target of the end of the hackathon's first feature PR (`tasks/T-001.md:59`, `tasks/T-001.md:315`). It stays tracked: T-001 stays `in-progress` until Tier 2 completes.

## What goes here

Every file `/docs/` contains or will contain is listed below and mapped to the `T-001` subtask that delivers it, so coverage can be audited against `tasks/T-001.md`. The skeleton is the one in `AGENTS.md` § 6 (`AGENTS.md:220-243`), with nothing renamed and nothing omitted. What it leaves out because this is a single repo is listed under [Not in this repo](#not-in-this-repo).

### Tier 1 — required before any feature work begins

Tier 1 includes the **contract-critical docs** (`contracts.md`, `api.md`, `data-model.md`) on purpose: the moment feature work begins, an agent must already have the full inventory of what it could break.

| File | Owner (lead author) | Task | State | Purpose |
|------|---------------------|------|-------|---------|
| `README.md` (this file) | Analyst | T-001.1 | present | The index: the bar, the consensus rule, every doc mapped to its task, and how to use the folder in a session. |
| `architecture.md` | Architect | T-001.2 | present | Components and data flow; trust boundaries on the user's own machine (loopback HTTP, extension ↔ server, extension ↔ page, subprocesses, outbound network, and the model as the prompt-injection boundary); the security invariants; what data leaves the machine; known weaknesses, recorded as facts with follow-up tasks. |
| `contracts.md` ⭐ | Backend + Data Engineer + Architect (Frontend co-drafts the extension parts) | T-001.17 | present | **The exhaustive, code-grounded inventory** of every HTTP route, request and response key, NDJSON event type, extension port message, `chrome.storage.local` key, manifest permission, CLI subcommand and flag, env var, config key, subprocess and network integration, YouTube page selector, background thread, and cross-cutting invariant, each with its invariant and blast radius. The single most important doc for safe autonomous commits. `tests/test_docs_contracts.py` (added by T-001.17) fails the build if the code gains an item this doc doesn't list. |
| `api.md` | Backend | T-001.5 | present | The HTTP API narrative: protocol, the auth model, error shapes, NDJSON streaming, versioning (there is none), and **how to add or change a route safely**, naming every place a route is defined. |
| `data-model.md` | Data Engineer | T-001.4 | present | Every store, for file-backed JSON with no database (so tables, indexes and foreign keys are N/A): path, writer, shape, key scheme, TTL, cap and cleanup; PII classification; and **how to change a store's format safely**, given that no store carries a version marker. |
| `stack.md` | Architect | T-001.6 | present | Languages, frameworks, tools and services in use, why each was chosen, and their license posture. |
| `testing.md` | Test Engineer (`.claude/agents/sdet.md`) | T-001.8 | present | Test levels, coverage gates, the test-data approach, and the **regression-safety check**: which tests guard each `contracts.md` section, and which contracts are unguarded. |
| `deployment.md` | DevOps | T-001.9 | present | Environments (the user's machine, and this fork); install; secret management; the CI/CD pipeline on GitHub Actions (`.github/workflows/checks.yml`), with `azure-pipelines.yml` kept but inert; the promotion path; versions; and a tested rollback. |
| `ownership.md` | Architect + Analyst | T-001.14 | present | The Directly Responsible Individual map. Solo: one owner for everything, no backup, no on-call. Mirrors `.github/CODEOWNERS`, and says what changes when a second maintainer joins. |
| `onboarding.md` | Analyst | T-001.15 | present | The first-30-minutes path for a hackathon audience, then the read order, development setup, tests, hooks, CI output, and the first PR under this playbook. |
| `decisions/` | Architect | T-001.11 | present | `README.md` (numbering, status lifecycle; an Architectural Decision Record is never edited, only superseded), `_TEMPLATE.md`, `0001-foundational-stack.md` (local-only, on the user's own Claude plan), `0002-hackathon-fork-departures.md` (where this fork departs from the starter, from T-003), and `0003-local-security-model.md` (plain HTTP on loopback against the "TLS everywhere" redline). Each record states its own status. |
| `reviews/` | Review Coordinator | T-001.12 | present | `README.md` (where review artifacts go and how folders are named) and `_TEMPLATE.md`. Artifacts live in `reviews/<folder>/`, one file per round plus `final.md`. This bootstrap's are in `reviews/T-001-tier-1/`, including the plan review `plan.md`. Also holds the [legacy files](#legacy-review-files-pre-playbook-history). |

The Tier 1 review itself, with all 9 sign-offs and the § 6.0 grounding check, is **T-001.13**. Its artifact is `reviews/T-001-tier-1/final.md` (`tasks/T-001.md:10`).

### Tier 2 — required by end of the first feature sprint

| File | Owner (lead author) | Task | State | Purpose |
|------|---------------------|------|-------|---------|
| `domain.md` | Data Engineer + Analyst | T-001.3 | pending, tracked as T-001.3 | The domain: its entities and their key relationships. |
| `ux.md` | UX Designer | T-001.7 | pending, tracked as T-001.7 | Personas, primary journeys, design-system pointers, accessibility baseline. |
| `glossary.md` | Analyst | [T-016](../tasks/T-016.md) | pending, tracked as T-016 | Every term used in the codebase, defined once. `AGENTS.md` § 6 item 14 (`AGENTS.md:214`) calls for it, but no `T-001` subtask was seeded for it, so follow-up task T-016 tracks it (`tasks/T-001.md:60`). |
| `runbooks/` | DevOps | T-001.10 | pending, tracked as T-001.10 | One file per operating procedure, for a user's own install. There is no on-call (see `ownership.md`). |

The Tier 2 review, with all 9 sign-offs, is **T-001.16**. Its artifact will be `reviews/T-001-tier-2/final.md` (`tasks/T-001.md:51`).

> **Tier 1 vs. Tier 2 is a discipline tool, not a license to skip.** Both tiers carry the unanimous 9-agent consensus rule. Tier 2 past its target is a process failure that should be retrospected, and missing it past one quarter is a flag (`AGENTS.md:539`).

### Follow-up tasks from the Tier 1 bootstrap

The Tier 1 docs record weaknesses and `UNKNOWN`s as facts, without changing code (`tasks/T-001.md`, plan rules 2 and 9). Each one is tracked in one of these `proposed` tasks, and each doc's follow-up list names the task for every item:

- [T-004](../tasks/T-004.md): Reduce token exposure (startup print, ps, file modes, child env, extension storage)
- [T-005](../tasks/T-005.md): Harden the server and integration trust boundaries (Origin, Host, timeouts, URL checks, redirects)
- [T-006](../tasks/T-006.md): Escape server data in the options page's voice list
- [T-007](../tasks/T-007.md): Mark untrusted text in the summary prompt; review the open shadow root
- [T-008](../tasks/T-008.md): Make every user-facing error name a next step, and say what actually failed
- [T-009](../tasks/T-009.md): Close the panel's keyboard and accessibility gaps
- [T-010](../tasks/T-010.md): Add guarding tests for the unguarded contracts
- [T-011](../tasks/T-011.md): Replace the public video ID in tests with an invented one
- [T-012](../tasks/T-012.md): Fix documentation and comments outside /docs that the bootstrap found wrong
- [T-013](../tasks/T-013.md): Fix server and CLI edge-case bugs the bootstrap found
- [T-014](../tasks/T-014.md): Make data stores and extension state robust
- [T-015](../tasks/T-015.md): Get human answers for the docs' UNKNOWNs
- [T-016](../tasks/T-016.md): Write docs/glossary.md
- [T-017](../tasks/T-017.md): Record governance gaps of the solo fork
- [T-018](../tasks/T-018.md): Use the extension's own source of truth for model, whisper and voice lists

### Not in this repo

Each of these is N/A, with its reason:

- **`integration-map.md`** and **`docs/repos/`**: N/A. These belong to the multi-repo workspace model (`AGENTS.md` § 6.5 and § 6.6). tldw is a single repo with no `workspace.manifest.md` (`CLAUDE.md:19`, `tasks/T-003.md:52`).
- **`docs/agents/`**: not in this bootstrap. `AGENTS.md:96` says the role definitions in `.claude/agents/*.md` are mirrored here "once the docs folder is built", but the T-001 plan (rev 4) does not include that mirror, and the role definitions stay in `.claude/agents/`. Whether a mirror is wanted is `UNKNOWN — needs human confirmation` → [T-015](../tasks/T-015.md).

### Legacy review files (pre-playbook history)

These 13 flat files in `docs/reviews/` predate this playbook (T-003 adopted it). They record how tldw's features were planned and reviewed under the older process:

| File | What it covers (its own heading) |
|------|----------------------------------|
| `docs/reviews/PLAN.md` | The original implementation plan |
| `docs/reviews/REVIEW.md` | The original review artifact |
| `docs/reviews/PLAN-polish.md` | Video polish (intro, badge, end card) |
| `docs/reviews/PLAN-coherence.md` | Sentence-complete cuts |
| `docs/reviews/PLAN-extension.md` | The browser extension and local server |
| `docs/reviews/PLAN-audio-extension.md` | Audio in the extension |
| `docs/reviews/PLAN-streaming-tts.md` | Streaming text-to-speech and voice preview |
| `docs/reviews/PLAN-ask.md` | Follow-up questions about a video |
| `docs/reviews/PLAN-whisper-fallback.md` | Transcribing captionless videos with Whisper |
| `docs/reviews/PLAN-faster-summaries.md` | Faster summaries in the extension |
| `docs/reviews/PLAN-page-transcript.md` | Reading the transcript from the page's own panel |
| `docs/reviews/PLAN-spend-speed-disk.md` | Spending less, waiting less, keeping less |
| `docs/reviews/PLAN-concurrency-limits.md` | Limits that match what the work costs |

Rules for these files:
- **They are non-binding history, not contracts.** Where one disagrees with the code or with `contracts.md`, **`contracts.md` wins**. If `contracts.md` itself disagrees with the code, that is a doc bug to fix under the consensus rule.
- **They are not part of the session-start read.** Open one only to learn why a past design was chosen.
- **`docs/reviews/PLAN-extension.md` must not be moved or renamed.** The code points to it: the `server.py` module docstring (`src/youtube_tldw/server.py:3`) and the message `serve()` raises when it refuses a non-loopback host (`src/youtube_tldw/server.py:1935-1938`). `README.md:451-452` also points readers at `docs/reviews/` for the design history.
- They are still under `docs/`, so editing one still needs the `unanimous-consensus: T-NNN` line (`.githooks/commit-msg:24-37`). New review artifacts never go beside them as flat files; they go in `reviews/<folder>/` (see `reviews/README.md`).

## How to use this folder during a session

**You are an agent.** At session start:

1. Say that this is a single repo (`AGENTS.md` § 0 Step 0; `CLAUDE.md:19`).
2. List every file under `/docs/`.
3. **Read every file, except the 13 legacy files in `docs/reviews/` listed above.** tldw is small enough that nothing else is skipped. Never skip `architecture.md`, `contracts.md`, `data-model.md` or `stack.md`, nor `domain.md` and `glossary.md` once they exist.
4. Say what you loaded in one line, for example: *"Single repo. Loaded N docs: README.md, architecture.md, contracts.md, …"*.
5. Read `tasks/INDEX.md` and any relevant in-flight task (`AGENTS.md` § 0 Step 2).
6. **Before changing anything, check `contracts.md`.** If your change touches a documented route, message type, storage key, store format, public signature, integration, env var or config key, treat it as a contract change: run the regression-safety check (`AGENTS.md` § 7 step 8, made concrete in `testing.md`) and update `contracts.md` in the same pull request.
7. Then do what the user asked, applying what you learned.

**You are a human reviewing a pull request.** Check that any `/docs/*` change has its review artifact attached, with 9-of-9 sign-offs. If not, comment "blocked: needs unanimous consensus per AGENTS.md". In this repo the reviewer is the owner, `@drewcurley` (see `ownership.md`).

## Anti-patterns

- ❌ Editing a doc to match a code change without running the review cycle.
- ❌ Adding a "draft" doc that no one has signed off on. Drafts live on a branch, not in `/docs/`.
- ❌ Leaving an unreplaced template placeholder (the bracketed marker listed in `AGENTS.md` § 10, which the hooks and CI scan for). Either fill it in, remove the line, or open an Architectural Decision Record explaining why it is still undecided.
- ❌ Writing prescriptive style or lint rules here. Those belong in tooling configs, not in living memory.
- ❌ **Inventing a fact because it sounds right.** If it isn't confirmed by the code or the human, it's `UNKNOWN`, not a sentence that reads as settled. A plausible guess in living memory is a landmine.
- ❌ **Sampling a contract inventory.** "The main routes are …" is a BLOCK. Contracts are listed in full.
- ❌ **Letting `contracts.md` drift behind the code.** A change to a documented contract and its doc update are one atomic pull request, never "I'll update the docs later."
- ❌ **Treating a legacy `docs/reviews/*.md` file as a contract,** or moving `docs/reviews/PLAN-extension.md`.
- ❌ **Putting real data in a doc.** This repo is public: write `~` for the home directory, invented 11-character video IDs, `<your-token>` for the token, and never paste `tldw serve`'s output, because it prints the token (`src/youtube_tldw/server.py:1959`).

## How this was verified

- **The template:** read the starter's index at `bb9406f:docs/README.md` (the commit T-003 adopted) and kept its sections. The three stack placeholders (cloud provider, database, CI provider) were filled from the code: no cloud (`server.py:1935-1938` refuses non-loopback hosts); file-backed JSON with no database (`README.md:234-241`, `README.md:245`); GitHub Actions (`.github/workflows/checks.yml`) with `azure-pipelines.yml` inert (`tasks/T-003.md:60-67`). The anti-pattern bullet about placeholders was reworded so it does not contain the marker the gate scans for (the pattern at `scripts/ci-checks.sh:112`).
- **The doc list and task mapping:** checked against `AGENTS.md:191-243` (the Tier 1 and Tier 2 lists and the skeleton), `tasks/T-001.md` (the plan's "Docs and drafters" table and the subtask list, `tasks/T-001.md:105-189`, `:282-303`), and each seeded `tasks/T-001.*.md`. Every T-001 subtask appears exactly once.
- **The legacy files:** `git ls-files docs` and `git ls-tree --name-only hackathon/demo docs/reviews/` both list exactly the 13 files above. The "what it covers" column paraphrases each file's first heading. `grep -rn 'PLAN-extension' src extension tests scripts` finds only `server.py:3` and `server.py:1938`.
- **Enforcement claims:** read `.githooks/commit-msg:24-37`, `scripts/ci-checks.sh:112`, `scripts/ci-checks.sh:167`, and `.placeholder-allow`, which does not list `docs/README.md`, so the gate scans this file.
- **The index against the tree:** `ls -R docs` lists exactly the files and folders named in the Tier 1 table, the 13 legacy files, and the T-001 review folder (`reviews/T-001-tier-1/plan.md`); nothing in the tree is missing from this index, and every Tier 1 row marked `present` exists under the name given, including the five files in `decisions/`. Not yet present, as stated: the Tier 2 files, and `reviews/T-001-tier-1/final.md`, which T-001.13 writes. T-001.13's final review re-runs this check on the assembled tree.
- **The follow-up task list:** each of `tasks/T-004.md` to `tasks/T-018.md` exists, and each title below is copied from that file's frontmatter `title`.

## Update this doc when

- a file or folder is added to, removed from, or renamed in `docs/`;
- a `T-001` subtask changes ID or state, or a follow-up task (T-004 to T-018) is added, renamed, split or closed;
- Tier 2 lands (the State column, and the § 0 Step 3 section);
- `AGENTS.md` § 0, § 6, § 6.0 or § 12 changes;
- `.githooks/commit-msg` or `scripts/ci-checks.sh` changes how the consensus or placeholder gate works;
- `src/youtube_tldw/server.py`'s docstring or `serve()`'s refusal message stops pointing at `docs/reviews/PLAN-extension.md`, or a new code reference to a legacy file appears;
- this fork gains a second repo (the workspace model) or a second maintainer.
