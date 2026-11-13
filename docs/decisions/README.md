# Architectural Decision Records

This folder holds tldw's Architectural Decision Records (ADRs): one file per decision a future engineer would otherwise ask "why?" about. The Architect owns the folder (`.claude/agents/architect.md`, "What you own"). Like everything under `/docs/`, ADRs land only through the unanimous-consensus gate (`AGENTS.md` § 0 Step 4).

## Index

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](./0001-foundational-stack.md) | Foundational stack: local-only, the user's own Claude plan, a stdlib server, an MV3 extension in plain JS | accepted | 2026-11-12 (records decisions made 2026-06-17 to 2026-06-18) |
| [0002](./0002-hackathon-fork-departures.md) | This hackathon fork's departures from the agentic starter | accepted | 2026-11-12 |
| [0003](./0003-local-security-model.md) | Plain HTTP on loopback, against the "TLS everywhere" redline | accepted | 2026-11-12 |

Add a row here in the same PR as each new ADR.

## Numbering and file names

- Files are named `NNNN-slug.md`: a four-digit, zero-padded number, a hyphen, and a short lowercase slug, for example `0001-foundational-stack.md`.
- Numbers are sequential and never reused, not even for an ADR that was rejected before merge. Take the next number after the highest one in this folder.
- If two open PRs pick the same number, the one that merges second renumbers before it merges.
- `_TEMPLATE.md` and this `README.md` are not ADRs and carry no number.

## Status lifecycle

```
proposed ──► accepted ──► deprecated
                 │
                 └──────► superseded (by NNNN)
```

| Status | Meaning |
|---|---|
| `proposed` | Written and under review, not yet in force. It may still be revised in its own PR. |
| `accepted` | In force. Code and docs are expected to match it. |
| `deprecated` | No longer in force, and nothing replaces it. The ADR says why and from when. |
| `superseded` | Replaced by a later ADR, named in the status line as `superseded by NNNN`. |

**A crossed redline needs a named, dated acceptance.** An ADR that accepts crossing one of an agent's redlines (`.claude/agents/*.md`) may move to `accepted` only with the owner's explicit acceptance, recorded in the ADR with who accepted it, in what capacity, on what date, and their reasoning as given. That is the "formally accepted by the user with a documented ADR" clause in `.claude/agents/architect.md`. Until then it stays `proposed`.

## Never edit, only supersede

- Once an ADR has merged as `accepted`, its body is never edited: not to fix a fact, not to change the decision, not to "keep it current". A changed decision gets a **new** ADR that says what it replaces and why.
- The **only** change allowed to a merged ADR is its status line, and only in the PR that adds the ADR replacing or retiring it: `accepted` becomes `superseded by NNNN` or `deprecated`, with that PR's date.
- An ADR that turns out to describe the code wrongly is corrected the same way: a new ADR that supersedes it and records the correction.
- ADRs record decisions; they are not the inventory. The current state of routes, keys and stores lives in `contracts.md`, `api.md` and `data-model.md`, which *are* kept current. An ADR may cite them but doesn't restate them.

## What gets an ADR

- A choice a future engineer would otherwise ask "why?" about: a framework, a store, a protocol, a trust model.
- Any accepted crossing of a redline (above).
- Any intentional divergence from the agentic starter (`AGENTS.md` § 7, "Record any intentional divergence from upstream as an Architectural Decision Record").
- A scaling-down threshold chosen under `AGENTS.md` § 12 Knob 2.

## Writing one

Copy `_TEMPLATE.md` to `NNNN-slug.md` and fill every section. A section that doesn't apply says `N/A — <reason>`. A fact the code doesn't settle is written `UNKNOWN — needs human confirmation` and gets a follow-up task (`AGENTS.md` § 6.0). This is a public repo: no real home-directory paths (write `~/…`), no real video ids or titles, no tokens or anything token-shaped.

## How this was verified

The conventions restate T-001.11's acceptance criteria (`tasks/T-001.11.md`) and the Architect's redline-override clause (`.claude/agents/architect.md`, "Wins on" and "Redlines"). The three ADRs' statuses are their own status lines.

## Update this doc when

- An ADR is added, deprecated or superseded (the index).
- `AGENTS.md` changes its rules for ADRs or redline overrides, or `.claude/agents/architect.md` changes its redlines.
