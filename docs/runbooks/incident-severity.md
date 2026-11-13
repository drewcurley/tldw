# Incident severity

**Owner:** DevOps. **Task:** T-001.10. **Last verified against the code:** 2026-11-12, branch `chore/docs-tier2` at `a7f104c`.

tldw is a single-user tool that runs on the owner's own machine: one `tldw serve` on loopback, one browser extension, one model plan (`docs/deployment.md` § 1; `src/youtube_tldw/server.py:1933-1939`). There are no other users to protect and no service to keep up. So severity is measured by **what the owner loses**, not by how many people are affected.

## The levels

| Level | What you lose | Typical causes | Playbook |
|---|---|---|---|
| **Sev1** | **You can't summarize anything.** Every click on TL;DW fails. | The server isn't running or can't start; the extension's token doesn't match; every model call fails. | [`unreachable-or-token-mismatch.md`](unreachable-or-token-mismatch.md), [`model-failing.md`](model-failing.md); [`rollback.md`](rollback.md) if it started after an update |
| **Sev2** | **One feature is broken, or some videos fail,** but summaries of other videos still work. | YouTube rate-limiting the server's own fetches; Listen with no Piper; a captionless video with no Whisper (Whisper missing breaks every captionless video); key moments or questions failing alone. | [`youtube-rate-limit.md`](youtube-rate-limit.md); the "Less common" table in [`README.md`](README.md) |
| **Sev3** | **An annoyance with a workaround,** or one video. Trying again, waiting, or using the CLI gets you there. | The server's own "busy" refusal; a timeout that succeeds on retry; one video with no captions; wording that names the wrong step. | [`server-busy.md`](server-busy.md); the "Less common" table in [`README.md`](README.md) |

How to place a problem that isn't listed: if summarizing **any** video fails, it's Sev1. If summarizing works but something else doesn't, it's Sev2. If it goes away on its own or with a retry, it's Sev3.

Two things move a problem up a level:
- **It started right after you updated tldw** (a `git pull` or `git checkout`, then a reinstall). Treat it as one level higher, and consider [`rollback.md`](rollback.md) first.
- **It happens with the CLI too** (`tldw <url>`). The CLI and the server both fetch from YouTube through `metadata.py` (`cli.py:122`; `core.py:67`) and call the model through `claude_client.py` (`summarize.py:11`), so a failure in both points at the model command or YouTube, not the extension, and the CLI workaround is gone too.

## What each level asks of you

| Level | What to do |
|---|---|
| Sev1 | Open the matching playbook now. If it began with an update, roll back (`rollback.md`). |
| Sev2 | Open the matching playbook. Meanwhile the CLI (`tldw <url>`) or a different video usually still works. |
| Sev3 | Retry, or wait a minute. If it keeps happening and looks like a bug in tldw, record it as a task (`tasks/README.md`). |

## Response-time targets

N/A — there are no response-time SLAs. tldw has one owner, no on-call, and no hosted service whose uptime anyone is promised (`docs/ownership.md:5-10`, `:77-87`; `docs/deployment.md` § 11, "Observability, alerting, on-call, SLOs"). A problem is fixed when the owner gets to it. See [`on-call.md`](on-call.md).

## Out of scope for these levels

- **A leaked token** (for example raw `tldw serve` output pasted somewhere shared; it prints the token on every start, `server.py:1959`). The token's exposure is tracked in T-004, and where to report a security problem is `UNKNOWN — needs human confirmation` (`docs/ownership.md:85-86`). To replace a leaked token, see `onboarding.md`, "First 30 minutes", step 3 ("Start the server").
- **Data loss.** N/A — nothing tldw stores is the only copy of anything: every store is a cache, a log or config (`docs/data-model.md`, "Stores at a glance"), and a rollback needs no restore (`docs/deployment.md` § 10).

## How this was verified

- The three levels are scaled to the owner context recorded in the Tier 2 plan (the owner, 2026-11-12: the only known user, on a machine only they use) and to `docs/ownership.md:5-10` and `:77-87`.
- Each "typical cause" was traced to the message the code produces, in the playbook it links to; those playbooks carry their own `path:line` citations.
- `server.py:1933-1939` (the loopback-only bind) and `:1959` (the token print) were read on `a7f104c`.

## Update this doc when

- a playbook is added, renamed or removed in `docs/runbooks/`;
- tldw gains a second user, a hosted server, or an on-call (then response-time targets stop being N/A);
- `docs/ownership.md`'s summary or on-call section changes;
- the server stops binding loopback only (`serve()`, `src/youtube_tldw/server.py`).
