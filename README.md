# Ninja-Viz 🥷 🎥

Audio-reactive toon music video generator for Blender 5.x.

Takes any music video file, analyzes and extracts its audio, bakes sound frequency bands to animation curves, and synthesizes stylized 3D audio-reactive music videos across 4 visual templates.

---

## Templates

| Template | Aesthetic | Centerpiece | World / Sky | Floor & Stands | Screen Behavior | Camera Choreography |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`main`** | **Classic Ring** | Simple Glowing Orb | `ToonSky` vertical gradient | Cel checker floor + radial neon grid + stands | Outward-facing vertical ring | Off-center orbiting camera |
| **`new-viz`** | **Stage Ring** | Simple Glowing Orb | `ToonSky` + cycling palette hue shift | Cel checker floor + radial neon grid + stands | Outward-facing vertical ring with dynamic RGB split + posterize | Breathing dolly (`8s`) + bass height bob |
| **`mainframe`** | **Space Mainframe** | Mainframe Core (plasma + 3 spinning gimbal rings + wireframe cage) | `ToonCosmos` drifting 3D noise nebula + 4D twinkling stars | None (floating in deep space) | Outward-facing vertical ring with dynamic RGB split + posterize | Center-locked reverse orbit with breathing dolly + bass bob |
| **`vertical-drop`** | **Overhead Drop** | Mainframe Core (plasma + 3 spinning gimbal rings + wireframe cage) | `ToonCosmos` drifting 3D noise nebula + 4D twinkling stars | None (floating in deep space) | Synchronized upward `80°` tilt facing overhead camera | 20s crane arc: eye-level (`0°`, `6.1m`) to top-down overhead (`90°`, `15.6m`) |

---

## Quick Start

### 1. Build and Render

To build the scene and render the final video:

```bash
./render.sh --video Sunrise.mov --template vertical-drop
```

### 2. Custom Excerpts & Options

Render a 30-second test excerpt starting at minute 1:00:
```bash
./render.sh --video "Mainframe.mov" --template mainframe --start 60 --duration 30
```

Render an entire music video:
```bash
./render.sh --video "Wakeup Endor.mov" --template new-viz --full
```

### 3. Build-Only (Preview in Blender GUI)

To only build the `.blend` file (without launching the headless render) and inspect the scene in Blender:

```bash
/Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python build_scene.py -- \
    --video Sunrise.mov --template vertical-drop --duration 10 --no-quit
```

---

## CLI Options

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--video PATH` | **(Required)** Path to source video file | *None* |
| `--template NAME` | **(Required)** One of: `main`, `new-viz`, `mainframe`, `vertical-drop` | *None* |
| `--start SECONDS` | Start time in seconds | `0.0` |
| `--duration SECONDS` | Duration in seconds | `30.0` |
| `--section SECONDS` | Section interval for palette and particle mode cycling | `30.0` |
| `--full` | Render entire source video | `false` |
| `--out PATH` | Path to generated `.blend` file | `ninja_viz.blend` |
| `--no-quit` | Keep Blender GUI open after build finishes | `false` |

---

## Output Organization

- **Generated Scene**: `ninja_viz.blend`
- **Audio Stems**: `audio_excerpt.wav`
- **Render Output**: `renders/{video_name}_{tag}_####.mp4` (where `tag` is `test` or `full`)
- **Particle Cache**: `blendcache_ninja_viz/`
