#!/usr/bin/env sh
# aggregate-docs.sh — roll every project repo's /docs up into the playbook,
# so playbook/docs is the COMPLETE, current golden source of truth.
#
# Model (see AGENTS.md § 6.6):
#   - Each repo's /docs is the SINGLE WRITER for that repo's facts.
#   - This script MIRRORS each repo's /docs (read-only) into
#       playbook/docs/repos/<name>/  and stamps the source repo's HEAD SHA.
#   - Cross-repo / project-wide docs (integration-map.md, architecture.md, …)
#     stay hand-authored in playbook/docs and are NOT touched here.
#   - The mirror is GENERATED — never hand-edit it; edit the source repo's /docs.
#
# Usage:
#   sh scripts/aggregate-docs.sh           # refresh the rollup from every repo
#   sh scripts/aggregate-docs.sh --check   # report drift; exit 1 if stale, 0 if current
#
# --check is cheap (SHA compare) and is run at session start in the workspace
# model. Run from the playbook repo root.

set -u

mode="refresh"
[ "${1:-}" = "--check" ] && mode="check"

red()  { printf "\033[0;31m%s\033[0m\n" "$*" >&2; }
grn()  { printf "\033[0;32m%s\033[0m\n" "$*"; }
yel()  { printf "\033[0;33m%s\033[0m\n" "$*"; }

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
workspace_root=$(CDPATH='' cd -- "$playbook_dir/.." && pwd)
manifest="$playbook_dir/workspace.manifest.md"
rollup_dir="$playbook_dir/docs/repos"
stamp=".source-sha"   # file written into each mirror dir

if [ ! -f "$manifest" ]; then
  yel "No workspace.manifest.md — single-repo project, nothing to aggregate."
  exit 0
fi

valid_dir_name() {
  case "$1" in
    *[!A-Za-z0-9._-]* ) return 1 ;;
    "" | "." | ".." )   return 1 ;;
    * )                 return 0 ;;
  esac
}

# Repo dirs from the manifest table (comment-stripped, placeholders dropped).
# shellcheck disable=SC2016
repos=$(awk '/<!--/{inc=1} !inc{print} /-->/{inc=0}' "$manifest" \
        | grep -E '^\| *`[^`]+` *\|' \
        | sed -E 's/^\| *`([^`]+)`.*/\1/' \
        | grep -vE '^\[insert' || true)

if [ -z "$repos" ]; then
  yel "No repos listed in workspace.manifest.md yet (still placeholders?)."
  exit 0
fi

# HEAD sha of a repo's /docs subtree (changes only when docs change), or "" if absent.
docs_sha() {
  ( cd "$1" 2>/dev/null && git rev-parse HEAD:docs 2>/dev/null ) || true
}

drift=0      # repos whose source docs are ahead of the rollup
missing=0
refreshed=0

set -f
old_ifs=$IFS
IFS='
'
for dir in $repos; do
  [ -z "$dir" ] && continue
  if ! valid_dir_name "$dir"; then
    red "  REJECTED: '$dir' is not a bare directory name. Fix workspace.manifest.md."
    drift=$((drift + 1)); continue
  fi
  repo="$workspace_root/$dir"
  if [ ! -d "$repo/docs" ]; then
    yel "  skip: $dir has no /docs yet (run its docs bootstrap)."
    missing=$((missing + 1)); continue
  fi
  src_sha=$(docs_sha "$repo")
  mirror="$rollup_dir/$dir"
  have_sha=""
  [ -f "$mirror/$stamp" ] && have_sha=$(cat "$mirror/$stamp")

  if [ "$src_sha" = "$have_sha" ] && [ -n "$src_sha" ]; then
    grn "  current: $dir ($src_sha)"
    continue
  fi

  # stale (or never built)
  drift=$((drift + 1))
  if [ "$mode" = "check" ]; then
    yel "  STALE:   $dir — docs at ${src_sha:-?}, rollup at ${have_sha:-none}"
    continue
  fi

  # refresh: replace the mirror wholesale (so deletions propagate), copy /docs, stamp.
  rm -rf "$mirror"
  mkdir -p "$mirror"
  # copy contents of repo/docs into the mirror (portable; no cp -T)
  ( cd "$repo/docs" && tar cf - . ) | ( cd "$mirror" && tar xf - )
  printf '%s\n' "$src_sha" > "$mirror/$stamp"
  cat > "$mirror/README.md.GENERATED" <<EOF
GENERATED MIRROR — do not edit.
Source of truth: $dir/docs (single writer). Refresh with scripts/aggregate-docs.sh.
Source HEAD:docs = $src_sha
EOF
  grn "  refreshed: $dir → docs/repos/$dir ($src_sha)"
  refreshed=$((refreshed + 1))
done
IFS=$old_ifs
set +f

echo
if [ "$mode" = "check" ]; then
  if [ "$drift" -gt 0 ]; then
    yel "Rollup is STALE for $drift repo(s). Run: sh scripts/aggregate-docs.sh  (then commit docs/repos/)."
    exit 1
  fi
  grn "Rollup is current. playbook/docs is the golden source of truth."
  exit 0
fi

grn "Aggregated: $refreshed refreshed, $missing without docs."
[ "$missing" -gt 0 ] && yel "Repos without /docs should run their docs bootstrap (AGENTS.md § 6)."
echo "Commit docs/repos/ so the shared golden source reflects every repo."
exit 0
