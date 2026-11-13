# Review: chore/docs-tier2 — plan (T-001 Tier 2, T-016, T-019, ADR 0004)

**Status:** PASS WITH ITEMS (rev 4). All 9 agents signed off, in three rounds run in order, plus a rename delta and the Review Coordinator's gate.

The plan itself is in `tasks/T-001.md`, under "Plan (2026-11-12, Tier 2, rev 4 …)".

## Scope
- The Tier 2 docs: `domain.md` (T-001.3), `ux.md` (T-001.7), `runbooks/` (T-001.10), and `glossary.md` (T-016).
- The review, T-001.16.
- **ADR 0004:** the owner accepts piper-tts's GPL-3.0 license. It traces to T-015's answered item.
- **The rename (T-019):** the owner asked for the hackathon extension's manifest `name` to become `TL;DW — Hackathon`. It's a contract change, so `contracts.md` updates in the same PR, with a guard test.

## How the plan was reviewed (`AGENTS.md` § 12 Knob 1: one agent, several hats)
In each round, one reviewer agent played that round's roles. Each role read its own brief and redlines and gave its own verdict. This is the scaling-down knob, recorded here so the procedure isn't mistaken for nine separate agents.
- **Round 1 (one agent playing Analyst, Architect and Data Engineer):**
  - Rev 1: Analyst BLOCK. The Tier 1 docs that call Tier 2 "pending" weren't in the plan.
  - Architect A1: there's no license line in `architecture.md`; ADR 0004 should be scoped to piper-tts only.
  - Data Engineer D1: six missing entities; give logical cardinality.
  - Rev 2: all three yes.
- **Round 2 (one agent playing Backend, Frontend and UX):**
  - Rev 2: UX BLOCK. "No automated a11y enforcement" was false, because `render_checks.js` guards some keyboard invariants.
  - Backend: YouTube rate-limiting reaches the user as a 500 carrying its message, not a 429; the model-failure playbook gets more detail; a "less common" table.
  - Frontend: the full configure journey; toolbar entry; dark mode.
  - Rev 3: all three yes.
- **Round 3 (one agent playing Test Engineer and DevOps):**
  - Rev 3: PASS WITH ITEMS. Test Engineer B1: a guard test for the runbooks' quoted messages. DevOps: runbook safety specifics.
  - Rev 4: both yes, with the rename's guard test, the scope-line fix and a Firefox note.
- **The rename delta (rev 4):**
  - Analyst: yes, once it's traced to T-019.
  - Frontend: yes; the name alone is enough in Chrome.
  - UX: yes; `default_title` stays.
- **Re-confirmations on rev 4 with the rename** (Review Coordinator B1):
  - Architect: PASS. No trust boundary moves; two plain-text token copies are added to T-004.
  - Data Engineer: PASS. No store changes.
  - Backend: PASS WITH ITEMS. The f-string busy messages are cited, not quoted.
- **Review Coordinator:**
  - BLOCK until the 7 domain roles had all confirmed rev 4 with the rename;
  - warnings W1–W5, now recorded here and in the ledger;
  - then yes.

## Lenses
- **ADR 0004:** n/a. It records the owner's existing decision ("glp-3.0 is fine with me", 2026-11-12) and makes no new one.
- **The rename:** n/a. It's a cosmetic label on a fork that is thrown away, at the owner's request.
- **The docs:** n/a. The Tier 2 docs describe what exists.

## Ledger (W4)
- T-019 is created at the start, `in-progress`.
- T-001.3, .7, .10, .16 and T-016 are `in-progress` on `chore/docs-tier2`, with the review artifact set.
- T-015 has an activity line pointing to ADR 0004, and stays `proposed`.
- T-004 gains the two-token-copies item.
- T-001 moves to `completed` at the end.

## Agent Sign-offs (plan)
- [x] Analyst — rev 2 yes; rename yes (T-019)
- [x] Architect — rev 2 yes; rev 4 with the rename PASS
- [x] Data Engineer — rev 2 yes; rev 4 with the rename PASS
- [x] Backend — rev 3 yes; rev 4 with the rename PASS WITH ITEMS
- [x] Frontend — rev 3 yes; rename yes
- [x] UX — rev 3 yes; rename yes
- [x] Test Engineer — rev 4 yes (with the rename)
- [x] DevOps — rev 4 yes (with the rename)
- [x] Review Coordinator — rev 4 yes
