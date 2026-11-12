#!/usr/bin/env sh
# setup-workspace.sh — wire up a multi-repo project to this shared playbook.
#
# WHAT IT DOES (idempotent — safe to re-run):
#   1. Confirms this playbook sits in a workspace root alongside the project repos.
#   2. For each repo listed in ../<repo> that exists as a sibling, installs the
#      playbook's git hooks into it (copies .githooks/ and sets core.hooksPath),
#      so the procedure in AGENTS.md is enforced in EVERY repo of the project.
#   3. Reports which repos from workspace.manifest.md are present / missing.
#
# WHY copy hooks instead of share them: git's core.hooksPath is per-repo and
# cannot point outside a repo's own tree in a portable way. Copying keeps each
# repo self-contained and lets CI mirror the same hooks server-side.
#
# Run from the playbook repo root:  sh scripts/setup-workspace.sh
# Re-run after adding a repo to workspace.manifest.md.

set -e

red()  { printf "\033[0;31m%s\033[0m\n" "$*" >&2; }
grn()  { printf "\033[0;32m%s\033[0m\n" "$*"; }
yel()  { printf "\033[0;33m%s\033[0m\n" "$*"; }

# --- locate the playbook root (this script lives in <playbook>/scripts/) ---
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
workspace_root=$(CDPATH='' cd -- "$playbook_dir/.." && pwd)
manifest="$playbook_dir/workspace.manifest.md"

if [ ! -d "$playbook_dir/.githooks" ]; then
  red "No .githooks/ found in playbook ($playbook_dir). Are you running this from a fork of the starter?"
  exit 1
fi

grn "Playbook:        $playbook_dir"
grn "Workspace root:  $workspace_root"

# --- install hooks into a repo, then VERIFY governance is actually wired -----
install_hooks() {
  target="$1"
  if [ ! -d "$target/.git" ] && ! (cd "$target" 2>/dev/null && git rev-parse --git-dir >/dev/null 2>&1); then
    return 1
  fi
  # Copy hooks from the playbook — unless target IS the playbook (source==dest).
  if [ "$target" != "$playbook_dir" ]; then
    mkdir -p "$target/.githooks"
    cp "$playbook_dir/.githooks/pre-commit" "$target/.githooks/pre-commit"
    cp "$playbook_dir/.githooks/commit-msg" "$target/.githooks/commit-msg"
    cp "$playbook_dir/.githooks/pre-push"   "$target/.githooks/pre-push"
  fi
  chmod +x "$target/.githooks/pre-commit" "$target/.githooks/commit-msg" "$target/.githooks/pre-push" 2>/dev/null || true
  (cd "$target" && git config core.hooksPath .githooks)
  # Verify: core.hooksPath is set AND the hook files exist. A silent miss here
  # is an ungoverned repo masquerading as governed — exactly what we must avoid.
  hp=$(cd "$target" && git config core.hooksPath 2>/dev/null || echo "")
  if [ "$hp" != ".githooks" ] || [ ! -f "$target/.githooks/pre-commit" ]; then
    return 2
  fi
  return 0
}

# Reject anything that isn't a bare, single-segment directory name. This blocks
# path traversal (../evil), absolute paths (/etc), home expansion (~), and glob
# metacharacters reaching the shell — the manifest is committed, but a bad row
# must never cause writes outside the workspace root.
valid_dir_name() {
  case "$1" in
    *[!A-Za-z0-9._-]* ) return 1 ;;   # any char outside the allowlist
    "" | "." | ".." )   return 1 ;;
    * )                 return 0 ;;
  esac
}

echo
grn "Installing hooks into the playbook repo…"
install_hooks "$playbook_dir"; rc=$?
if [ "$rc" -eq 0 ]; then
  grn "  ok: playbook (core.hooksPath verified)"
elif [ "$rc" -eq 2 ]; then
  yel "  WARN: playbook hooks copied but core.hooksPath not verified"
else
  yel "  skipped: playbook is not a git repo"
fi

# --- parse repo dirs from the manifest's repo table -------------------------
# First column of any table row whose dir cell is a backticked name, minus the
# placeholder rows. Read line-by-line (no unquoted word-splitting / globbing).
if [ ! -f "$manifest" ]; then
  echo
  yel "No workspace.manifest.md found — nothing else to wire up."
  yel "Copy the template, list your repos, and re-run."
  exit 0
fi

# Strip HTML comment blocks (<!-- … -->) first so a commented-out worked-example
# row in the template is never parsed as a real repo. Then take the first
# backticked cell of each table row, dropping placeholder rows.
# shellcheck disable=SC2016  # the grep/sed patterns are literal regex, not meant to expand
repos=$(awk '
          /<!--/ { inc=1 }
          !inc   { print }
          /-->/  { inc=0 }
        ' "$manifest" \
        | grep -E '^\| *`[^`]+` *\|' \
        | sed -E 's/^\| *`([^`]+)`.*/\1/' \
        | grep -vE '^\[insert' || true)

if [ -z "$repos" ]; then
  echo
  yel "No repos listed in workspace.manifest.md yet (still placeholders?)."
  yel "Fill in the repo table and re-run."
  exit 0
fi

echo
grn "Wiring up project repos listed in the manifest:"
missing=0
rejected=0
wired=0
# Loop in the MAIN shell (no pipe → counters persist). Disable pathname
# expansion (set -f) and split only on newlines so a manifest cell containing
# a glob or spaces can't be expanded or word-split.
set -f
old_ifs=$IFS
IFS='
'
for dir in $repos; do
  [ -z "$dir" ] && continue
  if ! valid_dir_name "$dir"; then
    red "  REJECTED: '$dir' is not a bare directory name (no / .. ~ or glob chars). Fix the manifest."
    rejected=$((rejected + 1))
    continue
  fi
  target="$workspace_root/$dir"
  if [ ! -d "$target" ]; then
    yel "  missing:  $dir   (expected at $target — clone it here, then re-run)"
    missing=$((missing + 1))
    continue
  fi
  install_hooks "$target"; rc=$?
  if [ "$rc" -eq 0 ]; then
    grn "  ok:       $dir   (hooks installed, core.hooksPath verified)"
    wired=$((wired + 1))
  elif [ "$rc" -eq 2 ]; then
    yel "  WARN:     $dir   (hooks copied but core.hooksPath not verified — inspect manually)"
  else
    yel "  skipped:  $dir   (present but not a git repo)"
  fi
done
IFS=$old_ifs
set +f

echo
if [ "$rejected" -gt 0 ]; then
  red "$rejected manifest row(s) REJECTED for invalid directory names — fix workspace.manifest.md and re-run."
fi
if [ "$missing" -gt 0 ]; then
  yel "$missing repo(s) from the manifest are not checked out under $workspace_root."
  yel "Clone them as siblings of playbook/ and re-run this script."
fi
if [ "$missing" -eq 0 ] && [ "$rejected" -eq 0 ]; then
  grn "All $wired repo(s) wired (0 missing, 0 rejected)."
fi

cat <<EOF

Done. Each present repo now enforces the AGENTS.md procedure locally.
Reminder: hooks are per-repo. CI must also run the mirror (scripts/ci-checks.sh via
the GitHub Actions workflow or azure-pipelines.yml) in EACH repo so --no-verify
can't bypass the gates server-side. It is NOT enforced until main requires the
checks in each repo (ADOPTING.md § 4-5).

Next:
  - Fill in docs/integration-map.md with the cross-repo seams.
  - Each repo runs its own docs bootstrap (AGENTS.md § 6) before feature work.
  - Aggregate every repo's /docs into the playbook (golden source of truth):
      sh scripts/aggregate-docs.sh      then commit docs/repos/   (AGENTS.md § 6.6)
EOF
