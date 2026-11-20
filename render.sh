#!/usr/bin/env bash
# Build + render an audio-reactive music video with ninja-viz.
#
# Usage:
#   ./render.sh --video <video_path> --template <template_name> [options]
#
# Available Templates:
#   main            Classic Ring with floor, stands, and simple central orb
#   new-viz         Stage Ring with 4 cycling palettes, halftone shadows, and 3 particle modes
#   mainframe       Deep space nebula cosmos with spinning Mainframe Core and shooting streaks
#   vertical-drop   Deep space cosmos with 20s overhead camera crane and synchronized 80° screen tilt
#
# Examples:
#   ./render.sh --video Sunrise.mov --template vertical-drop
#   ./render.sh --video "Mainframe.mov" --template mainframe --start 60 --duration 45
#   ./render.sh --video "Wakeup Endor.mov" --template new-viz --full
#   ./render.sh --video "i am and it is.mp4" --template main --duration 30
#
set -euo pipefail
cd "$(dirname "$0")"

BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
if [ ! -x "$BLENDER" ]; then
    echo "Error: Blender executable not found at $BLENDER" >&2
    exit 1
fi

# Windowed build: Bake Sound to F-Curves needs a Graph Editor (window closes itself when done).
"$BLENDER" --factory-startup --python build_scene.py -- "$@"

# Headless render:
"$BLENDER" -b ninja_viz.blend -a
