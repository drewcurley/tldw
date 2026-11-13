# Review: <branch-name> — <plan | round-1-analysis | round-2-implementation | round-3-verification | final>

<!--
How to use this file: docs/reviews/README.md.
Copy it to docs/reviews/<folder>/<file>.md and replace every <angle-bracket> slot.
Delete these comments. Keep every heading: a section that has nothing in it says "none" or "n/a — <reason>".
It is the AGENTS.md § 9 template, plus three lines this repo requires:
the spot-checks on every sign-off, the regression-safety line (AGENTS.md § 7 step 8)
and, in final.md, the content hash.
Public repo: no real home-directory paths (write ~/…), no real video IDs or titles,
no real cache/usage/summary rows, no token or token-shaped string (write <your-token>),
no raw `tldw serve` output, no session IDs.
-->

**Task:** <T-NNN> (`tasks/<T-NNN>.md`)
**Review scope:** <the AGENTS.md § 5 row, written out: "Docs (/docs/*) — all 9, unanimous" for a docs change>
**Status:** <PASS | PASS WITH ITEMS | BLOCK>

## Blockers (must fix before merge)
<!-- One line per blocker: who raised it, what it is, and how it was fixed. A round's blockers are fixed before the next round starts (AGENTS.md § 2). -->
- [ ] <agent> — <blocker> — <fix, or "open">

## Warnings (fix before next review)
- [ ] <agent> — <warning>

## Suggestions (consider for polish)
- <agent> — <suggestion>

## Regression safety
<!-- AGENTS.md § 7 step 8. "None" is a valid answer and is written out, never left out. -->
Regression-safety: contracts touched = <none | the `docs/contracts.md` entries>; reflected in docs = <yes | no | n/a — reason>; cross-repo consumers = <none | repo:surface>; compatibility = <backward-compatible | migration at path>.

## Agent Sign-offs
<!--
Every line: [x] once the agent passed, [ ] otherwise.
Verdict is PASS, PASS WITH ITEMS, BLOCK (with the reason) or "not required (§ 5, <row>)".
Spot-checks: the path:line places this agent read in the source to check the change, not the prose.
In final.md, each sign-off is on the content identified by the hash below.
-->
- [ ] Analyst — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`
- [ ] Architect — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`
- [ ] Data Engineer — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`
- [ ] Backend — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`
- [ ] Frontend — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`
- [ ] UX — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`
- [ ] Test Engineer — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`
- [ ] DevOps — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`
- [ ] Review Coordinator — <verdict>: <reason>. Spot-checks: `<path:line>`, `<path:line>`

## Content hash
<!-- final.md only. In a round file or plan.md, write "n/a — not the final sign-off". -->
- **Tree:** `<git write-tree hash>`
- **Paths left out of the hash:** `docs/reviews/<folder>/final.md`<, any other path and why>
- **Command run:** `<the exact commands, as in docs/reviews/README.md>`
- **Changed after the hash:** <none | each later commit, the paths it touched and why>

## Lens Sign-offs (major decisions only)
<!-- AGENTS.md § 4. If this is not a major decision, replace the line with: "n/a — <why nothing new is decided>". If lenses conflict, say so here. -->
- [ ] CEO  - [ ] Purchasing  - [ ] PM  - [ ] Adopter  - [ ] Builder  - [ ] Investor  - [ ] Marketing
