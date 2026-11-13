# Something broke right after an update: roll back

The procedure itself lives in `docs/deployment.md` § 10, "Rollback". This playbook says when to use it, and what to watch for. It doesn't repeat the steps.

**Severity:** a Sev1 or Sev2 that began right after you updated tldw (`incident-severity.md`).
**Last verified against the code:** 2026-11-12, branch `chore/docs-tier2` at `a7f104c`.

## What you see

No single message. You see whichever message the other playbooks cover, and it started right after you pulled or checked out a newer version and reinstalled. Find the message in [`README.md`](README.md)'s index first: most failures after an update are one of those, with a fix that doesn't need a rollback.

One message is specific to updating, when the extension is reloaded while a summary is running on an open YouTube tab and the tab isn't reloaded too:

> Lost connection to the extension worker. Click TL;DW to try again.

- That's the panel losing its connection to the extension's worker (`extension/content.js:664-669`). Reload the YouTube tab and click TL;DW again.

## Why it happens

- A newer version changed something the old setup relied on. tldw has no update channel and no auto-update; you choose a ref and install it yourself (`docs/deployment.md` § 6, § 12).
- The server and the extension come from the same checkout but are installed separately: the server by `pipx` or `pip`, the extension by reloading it from `extension/` (`docs/deployment.md` § 2). One can be updated without the other.

## Check

1. Which version is running? In the checkout, `git rev-parse --short HEAD`. Go by the commit, not by the `version` that `/health` reports (`docs/deployment.md` § 10, step 6).
2. Did you reinstall the server after the checkout? With `pipx install .`, a `git pull` alone changes nothing until you reinstall (`docs/deployment.md` § 2).
3. Did you reload the extension **and** the YouTube tab? (`docs/deployment.md` § 10, step 5.)

## Fix

Follow `docs/deployment.md` § 10, "Procedure (a user's install)": stop the server, check out the last version that worked, reinstall from it, rebuild the Firefox copy if you use Firefox, reload the extension at `chrome://extensions` and then the YouTube tab, and start `tldw serve` again.

What to know before you start:
- **No data needs restoring.** Config, the token, the caches and the usage log are read by both versions that were tested, so a rollback damages nothing (`docs/deployment.md` § 10).
- **The token doesn't change** across a rollback: it's the same file (`docs/data-model.md`, store 6). You don't need to paste it again.
- **Only the plain-venv path was tested.** The recommended `pipx install --force .` path is untested (`docs/deployment.md` § 10; T-015).
- **Rolling the extension back is `UNKNOWN — needs human confirmation`:** an older extension against settings a newer one saved, or against a newer server, hasn't been tested (`docs/deployment.md`, Follow-ups item 6; T-015).
- To go forward again later, do the same with the newer ref.

## Don't

- **Don't delete `~/.config/youtube-tldw/` or `~/.cache/youtube-tldw/`** to "start clean." Nothing there needs it, and deleting the token file breaks the extension until you paste the new token in (`unreachable-or-token-mismatch.md`).
- **Don't share the new server's startup output** while checking it works: it prints the token (`server.py:1959`). Check with `curl -s http://127.0.0.1:8765/health` instead.

## If that didn't work

- The old version fails the same way: it wasn't the update. Go back to the playbook for the message you see, via [`README.md`](README.md).

## How this was verified

- **Read on `a7f104c`:** `docs/deployment.md` §§ 2, 6, 10 and 12, and its Follow-ups; `docs/data-model.md`, store 6; `content.js:643-648`; `server.py:1959`.
- **The quoted message** was checked against its string constant (`content.js:667`).
- The rollback itself was run in isolation for `docs/deployment.md` § 10, on 2026-11-12, and is recorded there; this playbook didn't re-run it.

## Update this doc when

- `docs/deployment.md` § 10 changes, or T-015 settles the pipx or the extension rollback;
- a release changes a store format, so a rollback needs a migration or a restore (`docs/data-model.md`, "No version markers, and how to change a format safely");
- the port disconnect handling in `extension/content.js` changes.
