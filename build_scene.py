"""Build an audio-reactive toon music video scene in Blender 5.2.

Supports 4 visual templates:
    main            Classic Ring with floor, stands, and simple central orb
    new-viz         Stage Ring with 4 cycling palettes, halftone shadows, and 3 particle modes
    mainframe       Deep space nebula cosmos with spinning Mainframe Core and shooting streaks
    vertical-drop   Deep space cosmos with 20s overhead camera crane and synchronized 80° screen tilt

Usage:
    Blender --factory-startup --python build_scene.py -- --video <path> --template <name> [options]

Options:
    --video PATH            (Required) Path to source video file
    --template NAME         (Required) Choice: main, new-viz, mainframe, vertical-drop
    --start SECONDS         Excerpt start in seconds (default: 0.0)
    --duration SECONDS      Excerpt duration in seconds (default: 30.0)
    --section SECONDS       Section duration for palette & particle mode cycling (default: 30.0)
    --full                  Render the entire video
    --out PATH              Output blend file path (default: ninja_viz.blend)
    --no-quit               Keep Blender UI open after building
"""

import argparse
import os
import sys
import traceback

try:
    import bpy
except ImportError:
    bpy = None

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from common import (
    BuildContext, probe_video, extract_wav, reset_scene,
    graph_override, bake_particles, setup_render
)
from templates import get_template, TEMPLATES


def get_parser():
    p = argparse.ArgumentParser(description="Build audio-reactive music video scene.")
    p.add_argument("--video", required=True, help="Path to source video file")
    p.add_argument("--template", required=True, choices=list(TEMPLATES.keys()),
                   help=f"Visual template: {', '.join(TEMPLATES.keys())}")
    p.add_argument("--start", type=float, default=0.0, help="Excerpt start (s)")
    p.add_argument("--duration", type=float, default=30.0, help="Excerpt length (s)")
    p.add_argument("--section", type=float, default=30.0,
                   help="Section duration in seconds for palette and motion mode cycle")
    p.add_argument("--full", action="store_true", help="Use the whole video")
    p.add_argument("--out", default=os.path.join(HERE, "ninja_viz.blend"),
                   help="Output .blend filepath")
    p.add_argument("--no-quit", action="store_true", help="Keep Blender window open")
    return p


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = get_parser()
    a = p.parse_args(argv)
    video_path = os.path.abspath(a.video)
    if not os.path.exists(video_path):
        p.error(f"Video file not found: {video_path}")

    fps, video_frames = probe_video(video_path)
    if a.full:
        a.start, a.duration = 0.0, video_frames / fps

    frame_count = int(round(a.duration * fps))
    frame_offset = int(round(a.start * fps))
    tag = "full" if a.full else "test"
    wav_path = os.path.join(HERE, "audio_excerpt.wav")

    ctx = BuildContext(
        video_path=video_path,
        template=a.template,
        fps=fps,
        video_frames=video_frames,
        start=a.start,
        duration=a.duration,
        section=a.section,
        frame_count=frame_count,
        frame_offset=frame_offset,
        tag=tag,
        out_blend=os.path.abspath(a.out),
        wav_path=wav_path
    )
    return a, ctx


def main():
    args, ctx = parse_args()
    print(f"[ninja-viz] building template '{ctx.template}' for video '{os.path.basename(ctx.video_path)}'")
    print(f"[ninja-viz] start={ctx.start}s duration={ctx.duration}s frames={ctx.frame_count} "
          f"fps={ctx.fps} section={ctx.section}s tag={ctx.tag}")

    scene = reset_scene()
    scene.render.fps = ctx.fps
    scene.frame_start, scene.frame_end = 1, ctx.frame_count

    extract_wav(ctx.video_path, ctx.start, ctx.duration, ctx.wav_path)
    ov = graph_override()

    tmpl = get_template(ctx.template)
    particles = tmpl.build(scene, ctx, ov)

    setup_render(scene, ctx.video_path, ctx.start, ctx.frame_count, ctx.fps, ctx.tag, ctx.wav_path)

    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=ctx.out_blend)
    bake_particles(particles, ov, ctx.out_blend)
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=ctx.out_blend)
    print(f"[ninja-viz] successfully built and saved {ctx.out_blend}")


def run():
    code = 0
    try:
        main()
    except SystemExit as se:
        code = se.code if isinstance(se.code, int) else 1
    except Exception:
        traceback.print_exc()
        code = 1

    try:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
        no_quit = "--no-quit" in argv
    except Exception:
        no_quit = False

    if not no_quit:
        print(f"[ninja-viz] {'FAILED' if code else 'DONE'}")
        sys.stdout.flush()
        os._exit(code)
    return None


# Check for help early
_early_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
if "-h" in _early_argv or "--help" in _early_argv:
    get_parser().print_help()
    sys.exit(0)

# Defer execution until the Blender window / UI exists (needed for Graph Editor bake)
if bpy and hasattr(bpy, "app") and hasattr(bpy.app, "timers"):
    bpy.app.timers.register(run, first_interval=1.0)

