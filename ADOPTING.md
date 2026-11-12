# Adopting this starter — fork → enforced, in order

This is the one checklist that takes a fork from "files copied" to "the conventions
are actually enforced." **Do these in order.** The framework's whole value is
*enforcement* — until branch protection + an active CI check are in place, every
gate (hooks, review cycle, redlines) is advisory and a `--no-verify` push to `main`
bypasses all of it. That includes Claude Code's push prompts in `.claude/settings.json`:
they catch a push that names a protected branch, but not a bare `git push` while on `main`.

> **Single repo vs. multi-repo:** if your project spans several repos, this fork is
> the shared **playbook** — do steps 1–6 in the playbook, then repeat steps 3–5
> (hooks + CI + branch protection) in **each** code repo. See `AGENTS.md` § 6.5.

---

## 1. Fork / copy the repo
Use it as a template or copy the tree into a fresh repo. Keep the directory layout.

`LICENSE` is the starter's MIT license. Keep it, because MIT requires the notice to stay
with copies of these files. Your own code can carry whatever terms you choose.

**Then decide about `.starter/`: will you pull future updates from this starter?**
- **No** (template copy, no upstream sync): `git rm -r .starter` in your first PR. It is the
  starter's own task and decision history, and nothing depends on it.
- **Yes, or not sure:** keep it. Agents ignore it (`AGENTS.md` session start), and deleting
  it would cause a conflict on every sync. You can delete it later. Pick "keep" if you might
  ever add the starter as an upstream remote.

**Then decide about the overview deck.** It describes this starter, not your project, and
nothing depends on it. CI builds and compares it only on PRs that touch it or its CI
wiring (`scripts/ci-deck-needed.sh` lists those files); other PRs get a quick SKIP. To drop it, remove all three files in one PR:
`git rm Agentic-Starter-Overview.pptx scripts/build_deck.py scripts/requirements-deck.txt`.
Keep `scripts/test/test-deck.sh` and its CI job: CI then installs nothing and the suite
prints a `SKIP:` line. Removing only some of the three fails the suite. If you delete the
suite as well, remove the `deck` job from both CI files. On Actions, first remove
`overview deck (build + compare)` from the required checks (§ 5), or every PR blocks; on
Azure, § 5 requires only the overall check, which avoids this.

Your `tasks/` and `docs/` start clean: the docs-bootstrap seeds (`T-001*`, `T-002`) plus
templates. Code and docs that cite **"starter T-0NN"** mean the upstream repo's
`.starter/tasks/T-0NN.md`, not your own T-0NN. Both ledgers use `T-NNN` IDs, so in a synced
repo `git log --grep T-NNN` can match starter commits too.

**Existing forks only: syncing across this change (starter T-015).** Upstream moved its own
history out of `tasks/` and `docs/` into `.starter/`. If the sync conflicts:

- **`tasks/INDEX.md`:** delete the rows for tasks whose files now live in `.starter/tasks/`
  and keep your own.
- **A modify/delete conflict on a starter task, ADR or review file:** keep the file at its
  new path under `.starter/`, even if you had deleted it before. Those files are inert, and
  the ledger test checks that the folder is complete. If you don't want the starter's history
  at all, remove the whole folder instead: `git rm -r .starter`.

The sync also adds two CI steps:

- `scripts/test/test-task-ledgers.sh` fails if a seed task (`tasks/T-001*.md`,
  `tasks/T-002.md`) is missing: restore it from upstream and set `state: cancelled` instead
  of deleting it. While you keep `.starter/`, it also checks that the shipped docs don't
  point into `.starter/` at a file that isn't there, or at a starter file's old path.
- `scripts/test/test-ci-wiring.sh` fails if any `scripts/test/test-*.sh` isn't a step in
  both `azure-pipelines.yml` and the GitHub workflow, written `sh scripts/test/<name>.sh`.
  If you added suites of your own, wire them into both files that way.

**Existing forks only: syncing the deck refresh (starter T-016).** CI gains a `deck` job
that needs Python 3 with `venv`, and access to PyPI or to your own index through
`PIP_INDEX_URL`. If you changed the deck, the suite fails until you drop it as above, or
move your edits into `scripts/build_deck.py`, update the expected values in `test-deck.sh`,
and regenerate from the repo root:
`python3 -m venv ../deck-venv && ../deck-venv/bin/python -m pip install --require-hashes --only-binary :all: -r scripts/requirements-deck.txt && ../deck-venv/bin/python scripts/build_deck.py`.
A PyPI outage turns the required check red: re-run it, and don't use the § 5 break-glass.

**Existing forks only: syncing the push rules (starter T-017).** `scripts/test/test-claude-settings.sh`
fails if `.claude/settings.json` loses a push rule or `.githooks/pre-push` protects a branch
with no rule. Keep the rules, or add one for your branch: `"Bash(git push *<branch>*)"`.

**Existing forks only: syncing GitHub Actions on by default (starter T-022).** The GitHub
workflow moves from `.github/workflows-disabled/` to `.github/workflows/`, so it starts
running on your PRs as soon as you sync, if Actions is enabled for your repo. If you run
only Azure DevOps, keep it parked: `git mv .github/workflows/checks.yml
.github/workflows-disabled/checks.yml` in the sync PR (or disable the workflow in the
Actions tab). If your org allows only some actions, allow `actions/checkout`, or every PR
gets a red check from it. On private repos, see § 4a for the minutes it uses.

**Existing forks only: syncing the hook file modes (starter T-018).** Upstream now commits the
hooks as executable. If you ran `setup-hooks.sh`, the sync can stop with "commit your changes
or stash them" on `.githooks/*`. If those files show no changes of your own in `git diff`
beyond the mode, run `git restore .githooks`, sync again, then `sh scripts/setup-hooks.sh`.

## 2. Replace every placeholder
```sh
grep -rl --exclude-dir=.starter '\[insert ' .   # find them all
```
Fill in stack, product name, and the **handles in `.github/CODEOWNERS`** (real GitHub
teams/users — these route human review). The pre-commit hook blocks any unreplaced
`[insert … here]`, so CI/commits will fail until this is done. That's intentional.

## 3. Install the git hooks
- **Single repo:** `sh scripts/setup-hooks.sh`
- **Multi-repo playbook:** `sh scripts/setup-workspace.sh` (installs into every repo in `workspace.manifest.md`)

Hooks are **per-repo and per-clone** — every teammate runs this once per fresh clone.
Hooks are the *local* gate; they do **not** survive `--no-verify`. The server-side
backstop is steps 4–5.

## 4. Turn on server-side CI: pick one
**Org blocks GitHub Actions, or your pipelines already live in Azure DevOps? Use 4b. Otherwise use 4a.**
Both run the same checks (`scripts/ci-checks.sh`, shellcheck and the fixture suites, the
deck, the PR-body template). Running both is allowed but checks every PR twice; do it only
on purpose.

Either way, **land `scripts/` on `main` before requiring any check.** A PR build runs the
gate from the *target* branch (`scripts/ci-pr-checks.sh`, starter T-014), so it fails
closed while `main` has no `scripts/ci-checks.sh`. Merge the starter's files first.

> ⚠️ **Running isn't enforcing.** A check that runs but isn't required blocks nothing,
> and `--no-verify` bypasses the hooks. The server-side gate exists only once § 5
> requires the checks.

### 4a. GitHub Actions (default: runs once the file is on `main`)
`.github/workflows/checks.yml` runs on every PR and every push to `main`.
1. **Make sure Actions can run.** A repo created from the template, or copied, runs it
   right away. A true GitHub fork has Actions off until you enable it in its Actions tab.
   If Actions is disabled for your org or repo, the workflow never runs and nothing
   fails: you get no checks and no warning. Use 4b, and don't require the Actions checks.
2. **Settings → Actions → General:** allow `actions/checkout` (the only action the
   workflow uses; it's pinned to a commit SHA), set the default workflow permissions to
   read, require approval before workflows run for outside contributors, and require
   actions to be pinned to a full-length commit SHA. Fork PRs already get a read-only
   token and no secrets; approval stops strangers spending your minutes. Turn on
   Dependabot alerts (Settings → Code security) so you hear about a new `actions/checkout`
   release, then bump the pinned SHA by hand: Dependabot's own PRs carry no Task ID and
   would fail the procedure gate.
3. **Minutes:** free on public repos. On private repos each push to a PR uses about
   3 billed minutes of your plan's monthly Actions allowance (three jobs, each rounded up
   to a whole minute), and about 2 more when the PR touches the deck or its CI wiring,
   since only then does the deck job build (starter T-019 and starter T-022). See
   [GitHub's Actions billing](https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions)
   for your plan's allowance. Running 4b as well uses Azure's quota on top.

**Done when:** a throwaway PR shows `procedure checks`, `shell scripts (lint + test)` and
`overview deck (build + compare)`, all green. If no run appears, recheck step 1, then
close and reopen the PR.

### 4b. Azure DevOps (your pipelines live there, or your org blocks Actions)
1. **Create one pipeline per repo.** In your Azure DevOps project: **Pipelines → New
   pipeline → GitHub → your repo → Existing Azure Pipelines YAML file →
   `/azure-pipelines.yml`**. Connect through the **Azure Pipelines GitHub App** when
   asked: it posts the checks § 5 requires (an OAuth or PAT connection reports a commit
   status under a different name). Keep to one pipeline: a second one built from the same
   file posts every check twice and doubles the wait, so delete or disable any extra
   before § 5.
   - A new Azure DevOps organization has **no hosted agents** until it gets the free grant:
     request it at https://aka.ms/azpipelines-parallelism-request (approval can take a few
     days), or link a billing subscription under Organization settings → Billing. The
     free tier runs **one job at a time**, so a PR's three jobs queue one after another.
   - *"No agent found in pool Azure Pipelines which satisfies the specified demands:
     Agent.Version …"* most likely means the hosted-agent grant isn't active yet. In starter T-021 it
     appeared right after the grant and was gone on a later run; if it persists, check
     Organization settings → Parallel jobs.
2. **Park the GitHub Actions workflow** unless you want both systems:
   `git mv .github/workflows/checks.yml .github/workflows-disabled/checks.yml`. Parked,
   GitHub doesn't run it; the tests accept either path.
3. **Check that PRs build.** The YAML `pr:` trigger runs the pipeline on every PR to a
   GitHub repo; no Azure branch policy is needed (Build Validation is for Azure Repos
   only). If no Azure checks appear on a PR, open the pipeline's **Triggers** and make
   sure **"Override the YAML pull request trigger"** is unchecked.
4. (Optional) to enable the PR-body template check, add a **secret** pipeline variable
   `GITHUB_TOKEN` holding a fine-grained token for this repo only, with read-only **Pull
   requests** access and an expiry. Without it the step is skipped with a warning.
5. **Fork PRs** (pipeline → Triggers → Pull request validation): leave **"Make
   secrets available to builds of forks" off** and require a team member's comment
   before a fork build runs. The checkout uses `persistCredentials: true`, so a
   build's job token is visible to the pipeline YAML — which a PR can edit.
6. **The `deck` job runs five third-party Python packages from PyPI** (hash-locked in
   `scripts/requirements-deck.txt`). It keeps no git credentials and maps no secrets, but
   Azure's job token still exists on the agent. In Project Settings → Pipelines → Settings,
   limit job authorization scope to the current project, and keep the build identity
   read-only on this repo. To use a mirror, set `PIP_INDEX_URL` as a non-secret pipeline
   variable pointing at an index that needs no login; the hashes still apply. On a
   self-hosted pool the job loses the per-job VM isolation it relies on, so keep it on
   hosted agents or drop the deck (§ 1).

**Done when:** a throwaway PR shows the Azure checks (`<pipeline name>` plus one per job),
all green.

## 5. Protect `main` (the keystone) — do this AFTER step 4
Branch protection is what makes every other gate real. Configure on GitHub:
**Settings → Branches → Add branch ruleset (or protection rule) for `main`:**
- ✅ Require a pull request before merging
- ✅ Require approvals: **see "Solo vs. team" below** — and **Require review from Code Owners** *(team only)*
- ✅ Require status checks to pass — the checks of each CI system you run (§ 4), each
  with its **expected source** set, so a check of the same name from anywhere else (say,
  a workflow a PR adds) can't satisfy it:

  | You run | Require | Expected source |
  |---|---|---|
  | GitHub Actions (4a) | `procedure checks`, `shell scripts (lint + test)`, `overview deck (build + compare)` | GitHub Actions |
  | Azure DevOps (4b) | the overall check, named after the pipeline | Azure Pipelines |

  Require the checks of every system you run, and park any system you don't require.
  Renaming a job (Actions) or the pipeline (Azure) renames its check, and a required check
  that never reports blocks every PR, so settle names first. For Azure, don't require the
  per-job checks (`<pipeline> (<job>)`), for the same reason. A check appears in the list
  only *after* it has run once: open a throwaway PR to make it report, then require it.
- ✅ Require branches to be up to date before merging
- ✅ Include administrators (no bypass) — the redlines apply to everyone
- ✅ Block force pushes

> **Solo vs. team — set the approval count to match headcount.** GitHub will **not**
> let you approve your own PR, so on a **one-person repo, set required approvals to `0`**
> (and leave code-owner review **off**) — otherwise every PR deadlocks. You still get
> every *mechanical* gate: no direct push to `main`, PR-based history, required CI
> check, no force-push. **The moment a second maintainer joins, raise approvals to ≥1
> and turn on "Require review from Code Owners"** — that restores the human peer-review
> gate. This is the one protection setting that scales with team size; everything else
> stays the same.

> **What CI can't protect on its own.** A PR build runs the PR's own pipeline YAML,
> and no file in the repo can stop a PR from editing it. The gate logic and the
> placeholder allowlist come from `main` (`scripts/ci-pr-checks.sh`), so weakening
> them takes a visible edit to the YAML or `scripts/ci-*.sh` — and `CODEOWNERS`
> routes those gate files to the devops team + security lead. That only binds once
> **"Require review from Code Owners"** is on; a solo repo relies on its one
> maintainer reading gate-file diffs. Consequences to know:
> - A change to `.placeholder-allow` or `scripts/ci-checks.sh` takes effect on the
>   **next** PR, not the one that makes it. Land an allowlist entry before the file
>   that needs it.
> - Once you've filled in `.github/CODEOWNERS`, remove its line from
>   `.placeholder-allow`, so a stray placeholder there gets caught.
>
> - The gates don't see Git LFS content (only the pointer is in git) or submodule
>   content (only the commit SHA); scan those where they live.
>
> **Break-glass (the gate on `main` itself is broken).** Because PRs are judged by
> `main`'s gate, a PR that fixes a broken gate can be blocked by it. An admin
> temporarily removes the required checks in GitHub (Settings → Rules → Rulesets, or
> Settings → Branches for classic protection), runs the *fixed*
> `scripts/ci-checks.sh <base> <head>` by hand against the fix PR and pastes the output
> into the fix's task file, merges the fix PR with the usual reviews, and restores the
> checks immediately. The PR's CI run still shows red; with the check not required,
> that doesn't block the merge. Record who, when and why in that
> task file. Never bypass for anything else.

> ⚠️ **Order matters / don't brick the repo:** do NOT require a status check that
> has never reported, and do NOT require Code-Owner review while `CODEOWNERS` still
> has placeholders — either makes *every* PR unmergeable. Finish steps 2 and 4 first.

## 6. First agent session — bootstrap the docs
Start your agent (`claude` / `codex` / …). On the first session `/docs/` is empty, so
the agent runs the **Docs Bootstrap Workflow** (`AGENTS.md` § 6) — Tier 1 (incl. the
exhaustive `contracts.md`) must land before feature work. Just let it run.

## 7. Multi-repo only — aggregate docs into the playbook
Once each repo has its `/docs`, make the playbook the **golden source of truth**:
```sh
sh scripts/aggregate-docs.sh        # mirrors every repo's /docs → playbook/docs/repos/
git add docs/repos && git commit ... # commit the rollup
```
Each repo's `/docs` stays the single writer for its own facts; the playbook holds the
project-wide docs (`integration-map.md`, …) **plus** this generated mirror. Session-start
re-checks for drift automatically and refreshes locally — see `AGENTS.md` § 6.6. Never
hand-edit `docs/repos/**`; edit the source repo's `/docs`.

---

## Definition of "shipped / enforced"
A repo is properly enforced when **all** are true:
- [ ] No `[insert … here]` placeholders remain (CI green).
- [ ] Hooks installed locally by each contributor (`core.hooksPath` = `.githooks`).
- [ ] The checks of the CI system you chose (§ 4) appear on every PR and are required on `main` with their expected source (§ 5); any system you don't require is parked.
- [ ] `main` is protected: PR required, the CI check required, admins included, force-push blocked, and approvals set to match headcount (**0 solo**; **≥1 + code-owner review** once a second maintainer joins).
- [ ] `CODEOWNERS` routes to real handles.
- [ ] Docs Tier 1 bootstrapped (`/docs/contracts.md` etc. exist).
- [ ] **(Multi-repo)** Playbook rollup current — `sh scripts/aggregate-docs.sh --check` passes.

Until every box is checked, treat the conventions as **advisory** and say so to the team —
don't let a half-configured repo masquerade as an enforced one.
