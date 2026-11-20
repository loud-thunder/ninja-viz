#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TEMPLATES=("main" "new-viz" "mainframe" "vertical-drop")

for tmpl in "${TEMPLATES[@]}"; do
    echo "======================================================================"
    echo ">>> [$(date '+%Y-%m-%d %H:%M:%S')] Starting render for template: ${tmpl}"
    echo "======================================================================"
    ./render.sh --video Sunrise.mov --template "${tmpl}" --duration 30
    echo ">>> [$(date '+%Y-%m-%d %H:%M:%S')] Finished render for template: ${tmpl}"
    echo ""
done

echo "All 4 variations rendered successfully!"
