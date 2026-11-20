# Changelog

All notable changes to **Ninja-Viz** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-11-19

### Initial General Availability (GA) Release

`ninja-viz` 1.0.0 introduces a 100% code-driven, procedural audiovisual music video synthesizer built on top of Blender 5.x (`bpy`) and FFmpeg.

#### Added
- **Unified Multi-Template Architecture (`templates/`)**:
  - `main`: Classic 8-screen stage ring with cel-checker floor, radial neon grid, and central glowing orb.
  - `new-viz`: Stage ring featuring 4 cycling palettes (*Sunset*, *Ice*, *Acid*, *Neon*), screen-space halftone cel shadow dots, and 3-mode Geometry Nodes particles.
  - `mainframe`: Deep space `ToonCosmos` drifting 3D noise nebula with dual-layer 4D Voronoi twinkling stars, spinning Mainframe Core with multi-axis torus gimbals, neon wireframe cage, and 4 audio-reactive shooting streaks.
  - `vertical-drop`: Deep space cosmos with a periodic 20s overhead camera crane arc (`0°` eye-level to `90°` top-down) and synchronized `80°` upward screen tilt (`90° → 10°`).
- **Media Ingestion & DSP Pipeline (`common.py`)**:
  - Video probing with `ffprobe` for precise framerate and packet counts.
  - 48 kHz stereo uncompressed WAV audio extraction via `ffmpeg`.
  - 8-band logarithmic frequency decomposition (`20 Hz` to `20 kHz`: Sub-Bass, Bass, Low-Mid, Mid, High-Mid, Presence, High-Presence, Brilliance).
  - Graph Editor `sound_to_samples` automated baking into custom Action channelbags.
  - P95 normalization and FPS-invariant exponential moving average (EMA) envelope derivation (`BuildContext.per_frame`).
  - Transient onset trigger (`big_env`) for beat-synced visual drops and palette changes.
  - Dominant color extraction via `1x1` raw RGB streaming from FFmpeg.
- **Procedural 3D Geometry & Shaders**:
  - Zero-asset runtime generation of all meshes, materials, and world environments without pre-baked `.blend` files.
  - Screen-space cel shading, dynamic posterization levels, and chromatic UV RGB splitting.
  - Multi-mode Geometry Nodes particle simulation zone with *Blast*, *Vortex*, and *Halo* physics modes.
  - Automated disk simulation cache generation (`blendcache_ninja_viz/`).
- **Compositor FX Stack**:
  - Procedural post-processing pipeline featuring bloom glare, lens chromatic dispersion, beat glitch UV distortion, 4D animated film grain, and CRT scanlines.
- **CLI & Rendering Tooling**:
  - `build_scene.py`: Core CLI with options `--video`, `--template`, `--start`, `--duration`, `--section`, `--full`, `--version`, and `--no-quit`.
  - `render.sh`: Two-stage execution runner handling windowed UI F-Curve baking followed by headless EEVEE Next rendering (`1080p` H.264 + 192 kbps AAC).
  - `render_batch.sh`: Automated sequential batch generation across all 4 visual templates.
