# 0001 — Foundational stack: local-only, the user's own Claude plan, a stdlib server, an MV3 extension in plain JS

- **Status:** accepted
- **Date:** 2026-11-12. This ADR records decisions already made and shipped between 2026-06-17 and 2026-06-18 (commits `5115675`, `1fcaa57`, `0b4cf22`, `1dedf94`). It decides nothing new.
- **Deciders:** the owner (@drewcurley), as recorded in those commits.
- **Task:** T-001.11 (`tasks/T-001.11.md`)
- **Supersedes:** none
- **Redlines touched:** the Architect's "TLS everywhere" redline is a consequence of decision 1; it is handled separately in ADR 0003.

## Context

tldw turns a YouTube video into a short written summary, key moments, spoken audio and answers to follow-up questions. It began as a Python CLI ("text/video TL;DR from a YouTube URL via Claude Max", commit `5115675`, 2026-06-17). A browser extension and a small HTTP server were added the next day so a summary is one click away (commit `1fcaa57`, `docs/reviews/PLAN-extension.md`, "Goal").

The forces at the time, as recorded:
- **The model runs on someone's Claude account.** The `claude` CLI "uses whatever the CLI is logged into — Claude Pro, Max, Team, or an Anthropic API key" (`claude_client.py:3-4`). Sharing one person's plan with others was flagged as a terms-of-service problem: hosted mode would need "Anthropic **API billing, not Max** — a ToS requirement, not a gray area" (`docs/reviews/PLAN-extension.md`, "Review resolutions").
- **A hosted server needs much more.** That plan lists "TLS + real per-user auth + Anthropic API billing" as the price of hosting, deferred to "a separate, separately-reviewed PR" (same section).
- **YouTube rate-limits automated clients.** Bursts from one address get HTTP 429 (`metadata.py:39-41`, `txcache.py:3-6`). A server on the user's machine can take the transcript from the page the user is already watching (`server.py:785-797`).
- **Small surface.** The plan's server was "stdlib only" (`docs/reviews/PLAN-extension.md`, "Server"), reusing the CLI's core (`core.py`), and the extension was a thin MV3 client.

## Options considered

These are the options the record shows were weighed. Options nobody recorded weighing are listed as such, so this ADR doesn't invent a comparison.

1. **Hosted server for trusted users** (the original goal: "Local-first, but hostable later so trusted users can use a thin extension with no local install", `docs/reviews/PLAN-extension.md`, "Goal").
   - Pros: no install for users.
   - Cons: TLS, per-user auth and API billing; one person's plan paying for others' use.
   - **Rejected** in commit `1dedf94`: "Per the decision to not host: each user installs locally and runs their own `tldw serve` against their own Claude plan. Removes the 'hosting later' framing."
2. **Local-only: each user runs `tldw serve` on their own machine** with their own model account. **Chosen.**
3. **Model access through the `claude` CLI, rather than an API SDK with a key.** The CLI was the starting point (`5115675`); a pluggable backend was added the next day (`0b4cf22`, "pluggable model backend (--llm-cmd / TLDW_LLM_CMD)"). Whether calling the Anthropic API directly was weighed: `UNKNOWN — needs human confirmation`.
4. **Server framework:** Python stdlib `http.server` was chosen ("Stdlib only", `server.py:1-3`). Flask, FastAPI or another framework: not recorded as weighed. `UNKNOWN — needs human confirmation`.
5. **Extension:** MV3, plain JS, no build step. A framework or bundler: not recorded as weighed. `UNKNOWN — needs human confirmation`.

## Decision

1. **Local-only.** tldw has no hosted or shared server. `tldw serve` binds loopback only and refuses anything else (`server.py:1933-1939`); the READMEs say "local-only by design" (`README.md:433-435`, `extension/README.md:112-114`).
2. **The user's own Claude plan.** The default backend is the `claude` CLI, run as the user and under the user's login (`claude_client.py:1-10`, `:189-213`). Any command that reads a prompt on stdin and prints text can replace it through `TLDW_LLM_CMD` or the `llm_cmd` config key (`claude_client.py:5-7`, `:205-213`). That is operator configuration, never something a request can set.
3. **A Python stdlib server.** `ThreadingHTTPServer` on `127.0.0.1:8765` by default, HTTP/1.0, a bearer token, JSON in, JSON / NDJSON / mp3 out (`server.py:927-1971`, `cli.py:301-310`). The package's only required runtime dependency is Pillow (`pyproject.toml:10`).
4. **A Manifest V3 browser extension in plain JavaScript.** A service worker, a content script injected on click, and an options page, sharing one code base between Chrome and Firefox through a `browser ?? chrome` shim and a second manifest (`extension/background.js:6-8`, `manifest.json`, `manifest.firefox.json`, `build-firefox.sh`).
5. **No database.** State is JSON files under `~/.cache/youtube-tldw/` and `~/.config/youtube-tldw/` (`txcache.py:21-27`, `config.py:12`, `usage.py:26`).

## Consequences

### Good
- No hosting, no accounts, no multi-tenant data: each user's transcripts, questions and caches stay on their own machine, except what goes to their own model provider and to YouTube (`architecture.md` § 8).
- Billing is each user's own, under their own terms; tldw never holds an API key or a shared login.
- Plain HTTP on loopback is defensible, because the traffic never leaves the host (ADR 0003).
- Few moving parts: one Python process, no framework or database to patch, and an extension with no build step for Chrome.
- The model is swappable without code changes (`TLDW_LLM_CMD`), which is the exit from `claude` CLI lock-in.

### Bad
- Every user must install Python, `yt-dlp` and `ffmpeg`, and run a server in a terminal (`README.md:43-61`). There is no zero-install path.
- The richest features only work with the `claude` CLI: the model picker, token streaming, Q&A session resume, the usage ledger and the no-tools invocation (`claude_client.py:238-248`, `:304-495`; `stack.md`, "Model backend").
- The stdlib server means routes, auth, CORS, body limits and streaming are all hand-written, and their correctness rests on tests rather than a framework (`contracts.md` lists the five places a route is defined).
- A loopback server can be reached by any local process and any web page in the browser, so it needs its own token and a careful posture (`architecture.md` § 5–7).
- The extension depends on YouTube's page structure (selectors in `content.js:392-394`, `:480-499`). Those break when YouTube changes its markup, with no framework to absorb it.

### What would void or revisit it
- Any form of hosting or off-host serving. That reopens TLS, per-user auth and API billing, and voids ADR 0003.
- A wish to share one person's model account across users.
- YouTube changing so that a local, page-assisted flow no longer works.

## Acceptance

N/A — no redline is crossed by this ADR itself. It records the owner's existing decisions from June 2026. The redline its decision 1 touches is accepted separately in ADR 0003.

## How this was verified

- `git log --diff-filter=A` for `server.py`, `extension/manifest.json`, `claude_client.py` and `docs/reviews/PLAN-extension.md`; `git show 1dedf94` for the local-only decision and its message; `git log -S TLDW_LLM_CMD` for when the backend became pluggable.
- Read `server.py:1-10` and `:1933-1971`, `claude_client.py:1-10` and `:189-248`, `cli.py:301-310`, `pyproject.toml`, both manifests, `README.md:433-435` and `extension/README.md:102-114`.
- Options marked `UNKNOWN` are the ones for which no commit, comment or plan records a comparison.
