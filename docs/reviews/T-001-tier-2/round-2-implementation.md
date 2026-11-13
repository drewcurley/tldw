# Review: chore/docs-tier2 — Round 2, Implementation (Tier 2 drafts)

**Status:** PASS WITH ITEMS. There were no blockers. All warnings were applied, and all three roles re-confirmed after the fixes.

**How it was reviewed:** one fresh agent played Backend, Frontend and UX (`AGENTS.md` § 12 Knob 1), each role with its own verdict.

## Warnings (all applied)
- **Backend:**
  - The runbooks' Piper `503` row notes that the server's text names the CLI flag `--render-audio`, and that the same install is still the fix.
  - The stale `_tx_jobs` comment (`server.py:64`, actually keyed by `(video_id, model)`, `:196`) is recorded in T-012, deferred.
- **Frontend:**
  - `ux.md` journey 1: the panel opens only on pages the extension can inject into, and nothing appears on `chrome://` or the Web Store (`background.js:58-68`).
  - The Firefox `gecko.id` note is in `ux.md`.
  - The two limits of the JS tokenizer (string `+=`, and a regex after `)` or `}`) are documented in the test's header comment. Neither occurs in today's extension.
- **UX:**
  - The severity CLI bullet now says "the CLI workaround is gone too".
  - `model-failing.md` step 1 now says to run `claude` in a terminal and follow its login prompt.
  - The Sev2 entry says Whisper missing breaks every captionless video.

## Spot-checks
- **Backend:** `metadata.py:66-76` and `server.py:711-717` (the rate limit is a 500); `server.py:1063`, `:642-647`; `background.js:578`, `:446`; `claude_client.py:41-44`, `:483-495`; `cli.py:349-367`; `server.py:675-677`, `:1941`, `:1959`, `:692-708`.
- **Frontend:** `content.js:1289-1290`, `:753-777`, `:21`, `:729-733`, `:40-41`, `:1827-1829`; `options.js:137`; the manifests at `:3` (the old name appears nowhere in the repo).
- **UX:** WCAG 2.2 AA is stated as a target, not verified. The contrast arithmetic was re-derived by the reviewer and is labelled as not measured: `#888` 3.5:1, `#777` 4.48:1, `#c0392b` on `#1e1f24` 3.0:1, `#0a0` 3.1:1. `content.js:182`; `options.html:2`, `:79-80`. The runbook voice opens with the exact message, uses second person, and gives severity by what the owner loses.
- All 16 runbook quotes were checked against their source files with a probe script.

## Sign-offs
- [x] Backend — yes
- [x] Frontend — yes
- [x] UX — yes
