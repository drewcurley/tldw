# 0004 — Accept piper-tts's GPL-3.0-or-later license for the optional `[tts]` extra

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** the owner (@drewcurley), as area DRI and leadership for this solo repo (`docs/ownership.md:7`). See "Acceptance".
- **Task:** T-015, its answered licensing item (`tasks/T-015.md:28`, `:43`). T-015 itself stays `proposed`.
- **Supersedes:** none
- **Redlines touched:** the Architect's "**No supply-chain risk accepted silently.** New dependency = license check + maintainer-health check + Software Bill of Materials update." (`.claude/agents/architect.md:38`). This ADR records the license part of that check for piper-tts, and the owner's acceptance of the result, so the license is no longer accepted silently. It does **not** record a maintainer-health check or an SBOM update, and it accepts no gap in either; both stay open (see "What this does not settle").

## Context

**What piper-tts is in tldw.** piper-tts is the only dependency of the optional `tts` extra: `tts = ["piper-tts>=1.2"]` (`pyproject.toml:12-14`). The required dependencies are Pillow alone (`pyproject.toml:10`). The lock file pins piper-tts **1.4.2**, from PyPI (`uv.lock:573-575`). That version's package metadata declares `License: GPL-3.0-or-later` (its `*.dist-info/METADATA`, as recorded in `docs/stack.md:102`).

**How it is used: in-process.** tldw imports it into its own Python process, lazily, only when speech is needed:
- `from piper import PiperVoice` to load a voice (`src/youtube_tldw/audio.py:33`);
- `require_piper()` imports `PiperVoice` and piper's native `espeakbridge` phonemizer to check that speech can work (`src/youtube_tldw/audio.py:187-215`).

Its callers are the CLI's text-mode `--render-audio` (`src/youtube_tldw/cli.py:285`) and the server's Listen and voice-preview paths (`_validate_speak`, `src/youtube_tldw/server.py:1463-1478`; `_run_preview`, `:1857-1869`). Without piper-tts, those paths fail early with a message saying how to install it (`src/youtube_tldw/audio.py:198-202`); when it is installed but unusable, the message says "Everything except text-to-speech still works." (`:212-215`). The README installs it as a separate, optional step: `pipx inject youtube-tldw piper-tts`, or `pip install -e ".[tts]"` for a dev checkout (`README.md:58`, `:62`, `:172-173`).

**What tldw's own license is: none.** tldw has no license file. The starter's MIT license was deliberately not adopted, because adding it would relicense tldw (ADR 0002, `docs/decisions/0002-hackathon-fork-departures.md:35`, `:80`; `docs/stack.md:97`).

**The question this answers.** `docs/stack.md:118` recorded the combination as a fact with an open question: "piper-tts is GPL-3.0-or-later and is imported in-process by an optional extra, while tldw has no license of its own". T-015 carried it as a question for a human (`tasks/T-015.md:28`). The owner answered it on 2026-11-12 (`tasks/T-015.md:43`).

**Not in scope: ffmpeg.** `ffmpeg` and `ffprobe` are also GPL-3.0-or-later in the Homebrew build, depending on how they were built (`docs/stack.md:111`). tldw runs them as separate programs on `PATH`, never linked (`src/youtube_tldw/cli.py:280`; `src/youtube_tldw/videomode.py:33`, `:64`; `docs/stack.md:119`). That is a different kind of use, and this ADR doesn't cover it. `stack.md` keeps recording it as it does today.

## Options considered

1. **Accept piper-tts's license, as the optional in-process `[tts]` extra.** Chosen by the owner.
2. **Drop the `tts` extra, or replace piper-tts with a differently licensed TTS engine.** The TTS choice was never recorded as weighed against alternatives (`tasks/T-015.md:33`, the "TTS choice"). Listed for completeness only: `UNKNOWN — needs human confirmation` whether it was weighed.
3. **Give tldw a license of its own first.** Not recorded as weighed. ADR 0002 left tldw unlicensed (`docs/decisions/0002-hackathon-fork-departures.md:80`), and this ADR doesn't change that.

## Decision

The owner accepts piper-tts's GPL-3.0-or-later license for tldw's current use of it. That use is exactly:
- piper-tts **1.4.2**, the version in `uv.lock:573-575`;
- installed only through the optional `tts` extra (`pyproject.toml:14`) or the README's `pipx inject` step (`README.md:58`);
- imported in-process, lazily, by `src/youtube_tldw/audio.py` for text-to-speech.

It is checkable against the code: no file under `src/` other than `src/youtube_tldw/audio.py` imports piper (`:33`, `:205-206`), and no required dependency pulls it in (`pyproject.toml:10`).

**This is not a blanket "GPL is OK".** It covers piper-tts only, in the use above. Another GPL dependency, a GPL package becoming required, or piper-tts being used another way would each need its own decision (see "What would void or revisit it").

### What this does not settle

Each of these stays `UNKNOWN — needs human confirmation`. This ADR makes **no legal claim** about any of them:
- **What distributing tldw would mean,** given an unlicensed tldw and an in-process GPL-3.0-or-later dependency. Nothing here says whether GPL-3.0 obligations apply to tldw, or how.
- **Other piper-tts versions.** `pyproject.toml:14` allows `>=1.2`, and the README's `pipx inject` installs whatever PyPI serves (`README.md:58`), not the locked version. The licenses of versions other than 1.4.2 were not checked.
- **piper-tts's own dependencies and data:** its native espeak phonemizer (`src/youtube_tldw/audio.py:206`), onnxruntime (recorded as MIT, `docs/stack.md:105`), and the 13 downloaded voice models (`docs/stack.md:113`, per-voice licenses not checked).
- **Maintainer health** of piper-tts: not checked.
- **SBOM:** there is none (`docs/stack.md:120`).
- **CVE status:** whether piper-tts or anything it pulls in has a known critical vulnerability (CVSS ≥ 9.0) is unknown, and nothing checks for one (`docs/stack.md:121`).
- **The other licenses in T-015's item:** the `claude` CLI, the voice models, the Whisper weights and the other transitive dependencies (`tasks/T-015.md:28`; `docs/stack.md:109-115`). The owner's answer was about piper-tts's GPL-3.0; these are unchanged.

## Consequences

### Good
- The GPL-3.0 question in `stack.md` has a recorded, dated owner answer instead of an open UNKNOWN, and the Architect's license check for piper-tts is no longer silent.
- Listen and voice preview keep their local neural TTS with no change to code or install steps (`README.md:172-173`, `:407`).
- The scope is narrow and checkable: one package, one extra, one importing module.

### Bad
- tldw still has no license, and still imports a GPL-3.0-or-later package in-process when `[tts]` is installed. What that means for anyone who distributes tldw is still unknown; this ADR only records that the owner is fine with it.
- The acceptance is pinned to 1.4.2, but nothing enforces that version on a user's install (`pyproject.toml:14`, `README.md:58`).
- The rest of the supply-chain redline (maintainer health, SBOM) and the CVE question remain open. They are recorded as deferred items in `tasks/T-017.md`: there is no dependency-vulnerability audit (`:23`), and piper-tts has no maintainer-health check and no SBOM entry (`:25`, added in this PR).
- The decision rests on the owner's context: one known user, on one local machine (`tasks/T-015.md:44`). It was not weighed for a wider audience.

### What would void or revisit it
- tldw is distributed to others, packaged, or published as something other than a source checkout each user installs themselves (ADR 0001's local-only decision).
- tldw adopts a license of its own.
- piper-tts becomes a required dependency, is vendored into the repo, or is imported outside `src/youtube_tldw/audio.py`.
- piper-tts changes its license, or a version other than 1.4.2 is locked.
- Any other GPL-licensed package is added in-process. It needs its own license check and decision.
- A maintainer-health check, an SBOM or a vulnerability scan turns up something about piper-tts.

## Acceptance

- **Accepted by:** the owner (@drewcurley), area DRI and leadership for this solo repo, on 2026-11-12.
- **The owner's words, verbatim (typo included):** "glp-3.0 is fine with me"
- **Reasoning, as given:** no reasoning beyond that sentence was given. The owner's context for the deferred work around it, recorded the same day: "i am the only known user, this runs only on my local machine, which only i use. i'm not super concerned about blast radius." (`tasks/T-015.md:44`).
- **Scope of the acceptance:** piper-tts's license only, as in "Decision". It is not an acceptance of the open maintainer-health, SBOM or CVE items.

## How this was verified

- Read `pyproject.toml:10-15` (dependencies and extras) and `uv.lock:573-580` (the locked piper-tts version, source and dependencies).
- Read the `License` and `Version` fields of piper-tts 1.4.2's `*.dist-info/METADATA` in a project venv (read-only); they match `docs/stack.md:102`.
- Grepped `src/` for `piper` imports: only `src/youtube_tldw/audio.py:33`, `:205-206`. Read `audio.py:28-40` and `:187-215`, and traced `require_piper()`'s callers (`cli.py:285`, `server.py:1478`, `:1869`).
- Grepped `src/` for `ffmpeg` and `ffprobe` calls to confirm they run as subprocesses (`cli.py:280`, `videomode.py`).
- Read `docs/stack.md:95-121`, ADR 0002's license lines, `tasks/T-015.md`, and `.claude/agents/architect.md`'s redlines.
- The owner's quote and date are as relayed to the drafting Architect on 2026-11-12, and as recorded in `tasks/T-015.md:43` and `docs/reviews/T-001-tier-2/plan.md:42`.

## Update this doc when

N/A — an accepted ADR is never edited except its status line (`docs/decisions/README.md`, "Never edit, only supersede"). When anything under "What would void or revisit it" happens, write a new ADR that supersedes this one.
