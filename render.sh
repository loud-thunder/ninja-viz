#!/usr/bin/env bash
# Build + render the audio-reactive toon video ring.
#   ./render.sh                 -> 30 s test excerpt from 0:00
#   ./render.sh --start 60 --duration 45
#   ./render.sh --full          -> entire video
set -euo pipefail
cd "$(dirname "$0")"
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
# Windowed build: Bake Sound to F-Curves needs a Graph Editor (window closes itself).
"$BLENDER" --factory-startup --python build_scene.py -- "$@"
"$BLENDER" -b ninja_viz.blend -a
