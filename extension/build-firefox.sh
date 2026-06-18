#!/usr/bin/env bash
# Assemble a Firefox-loadable copy of the extension into dist-firefox/.
# The JS is shared with the Chrome build (cross-browser `api` shim); only the
# manifest differs (Firefox uses an event-page background.scripts + a gecko id).
set -euo pipefail
cd "$(dirname "$0")"
OUT="dist-firefox"
rm -rf "$OUT"
mkdir -p "$OUT/icons"
cp background.js content.js options.js options.html "$OUT/"
cp icons/*.png "$OUT/icons/"
cp manifest.firefox.json "$OUT/manifest.json"
echo "Built $OUT/"
echo "Load it in Firefox: about:debugging → This Firefox → Load Temporary Add-on"
echo "  → select $OUT/manifest.json"
