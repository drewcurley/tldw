# 0003 — Plain HTTP on loopback, against the "TLS everywhere" redline

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** the owner (@drewcurley), as area DRI and leadership for this solo repo. See "Acceptance".
- **Task:** T-001.11 (`tasks/T-001.11.md`)
- **Supersedes:** none
- **Redlines touched:** the Architect's "**No production traffic over an unencrypted channel.** Transport Layer Security version 1.2 or newer everywhere; legacy fallbacks are documented, time-boxed, and approved." (`.claude/agents/architect.md`, "Redlines"). This ADR records accepting a crossing of it.

## Context

The browser extension talks to `tldw serve` over plain HTTP: `http://127.0.0.1:8765` is the default server URL in both extension `DEFAULTS` objects and the only host permission (`extension/background.js:10`, `extension/options.js:3`, `extension/manifest.json:7`, `extension/manifest.firefox.json:7`). The server is the stdlib `ThreadingHTTPServer`, with no TLS (`server.py:1924-1941`). The bearer token and every body cross that connection unencrypted: transcripts, summaries, questions and audio.

This is the design that has shipped since the extension was added (commit `1fcaa57`), as a direct consequence of ADR 0001's local-only decision. Its pre-playbook plan already tied TLS to hosting: "Hosted mode (TLS + real per-user auth + Anthropic API billing, not Max) is a separate, separately-reviewed PR" (`docs/reviews/PLAN-extension.md`, "Review resolutions"). The code's own refusal says the same: "Refusing to bind a non-loopback host. Hosting for others needs TLS and API billing" (`server.py:1936-1938`). Under the playbook adopted in T-003, the Architect's redline makes this an explicit crossing, so it needs a recorded, accepted ADR.

## Options considered

1. **TLS on loopback** (a self-signed or locally-issued certificate for `127.0.0.1`).
   - Pros: meets the redline literally; protects the token from any process that can capture loopback traffic.
   - Cons: certificate generation, trust-store installation or a browser exception on every user's machine. A new failure mode on every install. Python's stdlib server would need `ssl` wrapping and key management.
   - Not chosen.
2. **Plain HTTP, bound to loopback only, with compensating controls.** Chosen.
3. **A non-HTTP transport** (for example browser native messaging). Not recorded as weighed: `UNKNOWN — needs human confirmation`.

## Decision

Plain HTTP is accepted for traffic between the extension and `tldw serve`, **on the condition that the traffic never leaves the host.**

### What stands in for TLS

1. **Loopback-only binding.** `serve` refuses any host other than `127.0.0.1`, `localhost` or `::1` before it binds (`server.py:1935-1939`), and defaults to `127.0.0.1` (`cli.py:304`, `server.py:1933`). The traffic never reaches a network interface.
2. **A bearer token on every POST.** `secrets.token_urlsafe(32)`, compared with `hmac.compare_digest`, checked before any body is read (`server.py:704`, `:955-960`, `:990`, `:1020`, `:1106`). Only `GET /health` and `GET /voices` are open, and they return no secrets or user data (`server.py:972-980`, `claude_client.py:238-248`).
3. **JSON-only bodies with size caps.** Chunked transfer refused, `application/json` required, `Content-Length` required, and per-route caps of 16 KiB, 64 KiB or 8 MiB (`server.py:542-548`, `:773-782`, `:1129-1149`).
4. **Least-privilege extension.** `host_permissions` is exactly `http://127.0.0.1:8765/*`, there is no `externally_connectable` key, and only extension-origin scripts read the token and send it (`manifest.json:6-7`, `background.js:43-52`). `content.js`'s code never reads `token`, but the storage area isn't restricted to trusted contexts (no `setAccessLevel`), and `content.js`'s `onChanged` listener (`content.js:50-55`) is handed the new token whenever Options saves one (`architecture.md` § 7, item 13).
5. **Everything else in `architecture.md` § 6**, the security invariants, which this acceptance assumes stay true.

### What voids this acceptance

**This acceptance is void if tldw is ever served off-host.** That includes:
- binding any non-loopback address;
- removing or weakening the refusal at `server.py:1935-1939`;
- forwarding, proxying or tunnelling the port so another host can reach it;
- any form of remote hosting.

Any such change needs TLS, real per-user authentication and its own reviewed ADR first. ADR 0001's local-only decision would also have to be superseded.

## Consequences

### Good
- No certificates to create, install, trust or rotate on any user's machine; install stays `pipx install .` plus `tldw serve` (`README.md:55-61`).
- The traffic stays on the host, where an attacker who could read it can usually already read the token file, the caches and the user's own processes.

### Bad
- The token and all bodies are in cleartext on the loopback interface. Any process on the host that can capture loopback traffic can read them.
- The acceptance is not time-boxed, as the redline asks of legacy fallbacks. It is bounded by the void condition instead.
- The extension will send the token in cleartext to whatever `serverUrl` is set to, loopback or not. Nothing checks the URL (`architecture.md` § 7, item 5). A user who points it off-host has crossed the void condition without the code noticing.
- The known weaknesses in `architecture.md` § 7 remain. Those most related to this ADR:
  - the token printed at startup;
  - the token-file chmod race;
  - Origin being CORS rather than authentication;
  - DNS rebinding reading the open GETs, since there is no `Host` check.

### What would void or revisit it
- Any off-host serving (the clause above).
- A finding that a common, unprivileged local process can read loopback traffic on a supported platform: `UNKNOWN — needs human confirmation` per platform.
- Removing any of the five stand-in controls.

## Acceptance

- **Accepted by:** the owner (@drewcurley), area DRI and leadership for this solo repo, on 2026-11-12.
- **The owner's reasoning, recorded as given:** "the loopback can work on plain http. this whole service runs only on local machine, nowhere else, so risk is minimal."
- **This acceptance is void if tldw is ever served off-host.** That is the "What voids this acceptance" clause above.

## How this was verified

- Read `server.py:1924-1971` (bind refusal, token, server), `:927-1149` (auth, CORS, body limits), `cli.py:301-310`, both manifests, and the `DEFAULTS` objects in `background.js` and `options.js`.
- Checked `docs/reviews/PLAN-extension.md` for the original TLS-with-hosting coupling.
- The acceptance lines are as relayed to the drafting Architect by the Review Coordinator on 2026-11-12. The same acceptance and quotation are recorded in `docs/reviews/T-001-tier-1/plan.md`, "Items carried into the draft".
