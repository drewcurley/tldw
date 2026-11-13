# Review: chore/docs-tier2 — Round 3, Verification (Tier 2 drafts)

**Status:** PASS WITH ITEMS. The Test Engineer blocked on two items, found by mutation. It signed off after the fix, confirmed by re-running the mutants. DevOps passed with items.

**How it was reviewed:** one fresh agent played the Test Engineer and DevOps (`AGENTS.md` § 12 Knob 1). Mutants were run on scratch copies only.

## Blockers (fixed)
- [x] **Test B1.** `RUNBOOK_QUOTE_FLOOR` was 1, so renaming a runbook's `## What you see` heading silently dropped that file's quotes; the mutant survived. Fixed: the floor is now 16, today's count. The mutant now fails `test_runbook_quotes_are_messages_the_code_shows`, re-run on a scratch copy by the author and by the reviewer. Quote lines are also matched with `>` and an optional space, which kills the no-space mutant.
- [x] **Test B2.** `testing.md` didn't describe the new guards. Fixed: there's a "Tier 2 guards" bullet, and the Manifests row lists `test_manifest_names_match_contracts_md` as the `name` guard.

## Mutants (all now fail)
- A reworded quote; an em dash changed to a hyphen; the code's "Server busy" changed.
- Only one manifest's name changed, either one; the contracts row changed; both manifests changed together.
- An indented quote; a renamed heading; a quote with no space.
- One test dropped (722 runs against a floor of 723); `MIN_TESTS` lowered to 722.

## Warnings
**Open, recorded:**
- Docstrings count as string constants.
- The backticked messages in the runbooks README's "Less common" table, the CLI's `config set/unset ytdlp-cookies` mapping, and port-in-use exiting 1 aren't under automated test. DevOps measured each of them in isolation.
- `testing.md:228`'s "How this was verified" records the Tier 1 run.

**DevOps (applied or planned):** INDEX regenerated before the hash; the trailer on every docs commit; `ci-pr-checks.sh` after the commit; rebuild Firefox and reload the extension.

## Spot-checks
- **Test Engineer:**
  - `scripts/ci-tldw-tests.sh:27` is 723, and `test-ci-tldw-tests.sh:185` and its fixture sums agree.
  - `testing.md:12`, `:21`, `:71`, `:122`, `:125`, `:201`.
- **DevOps:**
  - `server.py:1941` comes before `:1959`. Re-measured with a temp HOME and a dummy socket: exit 1, `OSError: [Errno 48]`, no token printed.
  - `cli.py:349-367`, re-measured; `metadata.py:53-54`; `server.py:666-668`.
  - `unreachable-or-token-mismatch.md:82` against `onboarding.md:47`.
  - The quotes against `background.js:572`, `:577`, `:578` and `options.js:106`.
  - `deployment.md:109` and § 10; `ux.md:12`; `T-019.md:27`.
- **CI:** all 16 `scripts/test` suites pass, shellcheck is clean, the secret scan finds 0 hits, and there are no placeholders.

## Sign-offs
- [x] Test Engineer — yes (after B1 and B2, mutants re-run)
- [x] DevOps — yes
