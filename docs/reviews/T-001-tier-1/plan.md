# Review: chore/docs-tier1 — plan (T-001 Tier 1 bootstrap)

**Status:** PASS WITH ITEMS (rev 4). All 9 agents signed off across three rounds, run in order.

The plan itself is in `tasks/T-001.md` under "Plan (2026-11-12, rev 4…)".

## How the plan changed
- **Rev 1.** Round 1 returned:
  - Analyst: PASS WITH ITEMS, then BLOCK until four items were in;
  - Architect: BLOCK;
  - Data Engineer: BLOCK;
  - Review Coordinator: BLOCK.
- **Rev 2:**
  - Three missing data stores: `chrome.storage.local`, the Claude CLI session dir, and the output/temp dirs.
  - Public-repo rules: no real paths, video IDs, rows or tokens.
  - ADR 0002 and 0003 get acceptance-criterion lines. The `decisions/` and `reviews/` skeletons get templates.
  - Every missing contract surface goes into the completeness script: storage keys, manifests, `urlopen`, model downloads, the model I/O contract, page selectors and thread starts.
  - N/A with a reason, as a rule.
  - The plan review runs all three rounds.
  - Sign-off is on the final content.
  - The placeholder traps are handled.

  All four Round 1 agents said yes.
- **Rev 3:**
  - **UX B1:** user-visible error text is a contract.
  - **UX W1:** the keyboard and accessibility baseline is an invariant.
  - **Frontend B1:** a working selector rule and storage-key rule for the script.
  - **Frontend B2:** `options.js` as a direct HTTP client; defaults in three places; the absent manifest keys; the Firefox differences; the page contract.
  - **Backend:** the five route-definition sites, the three error layers, request and response keys, the CLI dispatch and the thread split. Also the code's pointer to `docs/reviews/PLAN-extension.md`, which must not move.

  Backend, Frontend and UX all said yes.
- **Rev 4:**
  - **Test Engineer B1:** the completeness and leak checks are committed as `tests/test_docs_contracts.py`, with the CI floor raised. A one-time manual proof would let `contracts.md` drift.
  - **The testing.md content:** the contract level, end-to-end as N/A, and a test-to-contract table.
  - **DevOps B1–B3:** secret management, the promotion path, and the ruleset facts in deployment.md; install facts true to the README, with owner-specific setup excluded; a rollback test isolated from the owner's install.
  - **Sign-off identity:** the `git write-tree` content hash, because rebase-merge changes commit SHAs.

  Test Engineer, DevOps and the Analyst (scope: the test file is in scope, with a T-001.17 criterion line) said yes. Architect and Data re-confirmed the changes to their domains. The Review Coordinator said yes.

## Items carried into the draft
- **ADR 0003 crosses the Architect's "TLS everywhere" redline.** **The owner accepted it on 2026-11-12:** "the loopback can work on plain http. this whole service runs only on local machine, nowhere else, so risk is minimal." It is recorded as `accepted`.
- **The storage-key extractor** reads shorthand properties (`options.js:137`).
- **The rollback test** records the installed commit, since HEAD also reports 1.3.0. It settles the custom-`serverUrl` question.
- **Reviewers never run state-changing git commands** in the worktree. One plan reviewer switched branches there; it was restored, and nothing was lost.

## Agent Sign-offs (plan)
- [x] Analyst — rev 2 yes; rev 4 scope yes (test file in scope, with a criterion line)
- [x] Architect — rev 2 yes; rev 4 delta yes
- [x] Data Engineer — rev 2 yes; rev 4 delta yes
- [x] Backend — rev 2 yes; rev 3 yes
- [x] Frontend — rev 3 yes
- [x] UX — rev 3 yes
- [x] Test Engineer — rev 4 yes
- [x] DevOps — rev 4 yes
- [x] Review Coordinator — rev 4 yes

## Lens Sign-offs
- n/a. Nothing new is decided: ADR 0001 records an existing choice, 0002 records departures the owner already made, and 0003 records an existing design. Plan rule 10 sends any draft that sets direction to the lenses, or cuts it.
