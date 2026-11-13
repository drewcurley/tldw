# <Playbook title: the symptom, in the owner's words>

<!--
How to use this file: docs/runbooks/README.md, "Adding a playbook".
Copy it to docs/runbooks/<short-symptom-name>.md, replace every <angle-bracket> slot, and delete these comments.
Keep every heading. A section that doesn't apply says "N/A — <reason>".
A fact the code doesn't settle is "UNKNOWN — needs human confirmation", plus a line in the closest deferred task.
Public repo: no real home-directory paths (write ~/…), no real video ids or titles, no token values (write <your-token>),
and no raw `tldw serve` or `tldw config get` output.

"What you see" format (tests/test_docs_contracts.py checks it): put each exact message the owner sees on its own line,
starting with a greater-than sign and a space, copied character for character from a string constant in
extension/*.js or src/youtube_tldw/*.py. A message built at run time (an f-string, or JavaScript that joins strings)
is not quoted: name it in prose and cite where it is built.
-->

**Severity:** <Sev1 | Sev2 | Sev3, from incident-severity.md>, <one line: what the owner loses>.
**Last verified against the code:** <YYYY-MM-DD>, <branch> at <short commit>.

## What you see

<Each exact message on its own quoted line, as described in the comment above. Then one sentence per message saying where it shows up: the panel, the Options page, or the `tldw serve` terminal, with its `path:line`.>

<Messages with variable parts: named here in prose, each with the `path:line` that builds it.>

## Why it happens

<The cause, with the `path:line` that produces the message. If there is more than one cause, list each one.>

## Check

1. <A step the owner can run to tell the causes apart. Plain second person. No step that prints the token.>

## Fix

1. <What to do for each cause, in order. Say which ones change the owner's live config, and how to undo them.>

## Don't

- <Anything that would make it worse or weaken security: for example, binding off loopback, loosening CORS, pasting the token or raw `tldw serve` output anywhere shared.>

## If that didn't work

<The next playbook to try, or `rollback.md` if this started after an update.>

## How this was verified

<What was read (`path:line`), and what was run, in what isolation. Record exit codes and single lines only, never full `tldw serve` output.>

## Update this doc when

- <The code paths that, when changed, make this playbook wrong: the message's source line, the mapper that shows it, the setting it names.>
