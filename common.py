"""Common utilities, video/audio helpers, and Blender setup for ninja-viz.
"""

from dataclasses import dataclass
import os
import subprocess
import sys

try:
    import bpy
    from bpy_extras import anim_utils
except ImportError:
    bpy = None
    anim_utils = None


@dataclass
class BuildContext:
    video_path: str
    template: str
    fps: int
    video_frames: int
    start: float
    duration: float
    section: float
    frame_count: int
    frame_offset: int
    tag: str
    out_blend: str
    wav_path: str
    base_fps: float = 30.0

    def per_frame(self, k: float) -> float:
        """Rescale a per-frame EMA/decay coefficient tuned at base_fps to current fps."""
        return 1.0 - (1.0 - k) ** (self.base_fps / self.fps)


def probe_video(path: str) -> tuple[int, int]:
    """Read fps and frame count from the source video via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=r_frame_rate,nb_read_packets,duration",
         "-of", "default=nw=1", path], check=True, capture_output=True, text=True).stdout
    info = dict(l.split("=", 1) for l in out.split() if "=" in l)
    num, den = info["r_frame_rate"].split("/")
    fps = int(round(float(num) / float(den)))
    video_frames = int(info["nb_read_packets"])
    print(f"[ninja-viz] source {os.path.basename(path)}: {fps} fps, {video_frames} frames, "
          f"{video_frames / fps:.2f} s")
    return fps, video_frames


def extract_wav(video_path: str, start: float, duration: float, out_wav: str) -> str:
    """Extract stereo 48kHz WAV audio excerpt from the source video."""
    if os.path.exists(out_wav):
        os.remove(out_wav)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(duration),
                    "-i", video_path, "-vn", "-ac", "2", "-ar", "48000", out_wav], check=True)
    return out_wav


def video_colors(video_path: str, start: float, duration: float, fps: int, n: int,
                 base_fps: float = 30.0) -> list[tuple[float, float, float]]:
    """Extract per-frame average RGB from source video using ffmpeg 1x1 scale."""
    cmd = [
        "ffmpeg", "-v", "error", "-ss", str(start), "-t", str(duration),
        "-i", video_path, "-vf", f"fps={fps},scale=1:1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
    ]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    cols = []
    for i in range(n):
        idx = i * 3
        if idx + 2 < len(raw):
            r, g, b = raw[idx] / 255.0, raw[idx + 1] / 255.0, raw[idx + 2] / 255.0
        elif cols:
            r, g, b = cols[-1]
        else:
            r, g, b = (0.5, 0.5, 0.5)
        mx = max(r, g, b, 1e-4)
        cols.append((r / mx, g / mx, b / mx))

    smooth, s = [], None
    k_smooth = 1.0 - (1.0 - 0.15) ** (base_fps / fps)
    for c in cols:
        if s is None:
            s = c
        else:
            s = tuple(s[k] + (c[k] - s[k]) * k_smooth for k in range(3))
        smooth.append(s)
    return smooth


# --------------------------------------------------------------------------- Blender helpers

def reset_scene():
    """Wipe all existing objects, materials, meshes, cameras, lights, actions, etc."""
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.cameras,
                 bpy.data.lights, bpy.data.actions, bpy.data.images, bpy.data.worlds):
        for item in list(coll):
            coll.remove(item)
    return bpy.context.scene


def fcurves_of(id_data):
    """Retrieve fcurves from the assigned slot's channel bag in Blender 5.x."""
    ad = id_data.animation_data
    if not ad or not ad.action:
        return []
    bag = anim_utils.animdata_get_channelbag_for_assigned_slot(ad)
    return list(bag.fcurves) if bag else []


def find_fcurve(id_data, path, index=0):
    for fc in fcurves_of(id_data):
        if fc.data_path == path and fc.array_index == index:
            return fc
    return None


def sampled_values(fc):
    vals = [0.0] * (len(fc.sampled_points) * 2)
    fc.sampled_points.foreach_get("co", vals)
    return vals[1::2]


def percentile(values, q):
    s = sorted(values)
    if not s:
        return 0.0
    return s[min(len(s) - 1, int(q * (len(s) - 1)))]


def sk(sockets, identifier):
    """Socket by identifier (e.g. ShaderNodeMix 'A_Vector'); names are ambiguous."""
    return next(x for x in sockets if x.identifier == identifier)


def new_mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.node_tree.nodes.clear()
    return m, m.node_tree.nodes, m.node_tree.links


def emission_output(nodes, links, color_socket, strength=1.0):
    em = nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = strength
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(color_socket, em.inputs["Color"])
    links.new(em.outputs[0], out.inputs["Surface"])
    return em


def graph_override():
    win = bpy.context.window_manager.windows[0]
    area = max(win.screen.areas, key=lambda a: a.width * a.height)
    area.type = "GRAPH_EDITOR"
    area.ui_type = "FCURVES"
    region = next(r for r in area.regions if r.type == "WINDOW")
    space = area.spaces.active
    space.dopesheet.show_only_selected = True
    space.dopesheet.show_hidden = True
    return dict(window=win, screen=win.screen, area=area, region=region)


def bake_property(obj, path, wav, lo, hi, ov):
    """Real Bake Sound to F-Curves on obj[path], via the Graph Editor."""
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for o in bpy.data.objects:
        for fc in fcurves_of(o):
            fc.select = False
    fc = find_fcurve(obj, path)
    if not fc:
        # Check without index match if path is unindexed
        for c in fcurves_of(obj):
            if c.data_path == path:
                fc = c
                break
    fc.select = True
    fc.hide = False
    with bpy.context.temp_override(**ov):
        bpy.ops.graph.sound_to_samples(
            filepath=wav, low=float(lo), high=float(hi),
            attack=0.005, release=0.1, threshold=0.0,
            use_accumulate=False, use_additive=False, use_square=False)
    fc = find_fcurve(obj, path) or fc
    vals = sampled_values(fc)
    if len(vals) < 2:
        raise RuntimeError(f"bake produced no samples on {obj.name}{path}")
    return vals


def write_keys(id_data, prop, values, index=-1, constant=False):
    path = f'["{prop}"]'
    id_data.keyframe_insert(path, index=index, frame=1)
    fc = next(c for c in fcurves_of(id_data)
              if c.data_path == path and c.array_index == max(index, 0))
    kps = fc.keyframe_points
    kps.clear()
    kps.add(len(values))
    co = []
    for i, v in enumerate(values):
        co += [i + 1, v]
    kps.foreach_set("co", co)
    kps.foreach_set("interpolation", [0 if constant else 1] * len(values))  # 0=CONSTANT 1=LINEAR
    fc.update()


def drive(id_data, path, expr, index=-1, **variables):
    """Add a scripted (simple-expression) driver. variables: name=(id, data_path)."""
    fc = id_data.driver_add(path, index) if index >= 0 else id_data.driver_add(path)
    d = fc.driver
    d.type = "SCRIPTED"
    for name, (tid, dp) in variables.items():
        v = d.variables.new()
        v.name = name
        v.type = "SINGLE_PROP"
        v.targets[0].id_type = tid.id_type
        v.targets[0].id = tid
        v.targets[0].data_path = dp
    d.expression = expr
    return fc


def view_layer_attr(nodes, name):
    a = nodes.new("ShaderNodeAttribute")
    a.attribute_type = "VIEW_LAYER"
    a.attribute_name = name
    return a


def bake_particles(obj, ov, blend_path):
    """Bake the simulation nodes cache to disk."""
    mod = obj.modifiers["OrbParticles"]
    mod.bake_target = "DISK"
    name = os.path.splitext(os.path.basename(blend_path))[0]
    mod.bake_directory = f"//blendcache_{name}/"
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    with bpy.context.temp_override(**ov, active_object=obj, object=obj, selected_objects=[obj]):
        bpy.ops.object.simulation_nodes_cache_bake(selected=True)


def setup_render(scene, video_path: str, start: float, frame_count: int, fps: int,
                 tag: str, wav_path: str = None, template: str = None):
    """Configure render settings, output paths, and audio sync strip."""
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.fps = fps
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.resolution_percentage = 100
    scene.frame_start = 1
    scene.frame_end = frame_count

    try:
        scene.view_settings.view_transform = "Standard"
    except TypeError:
        pass
    try:
        scene.eevee.taa_render_samples = 16
    except AttributeError:
        pass

    ims = scene.render.image_settings
    ims.media_type = "VIDEO"
    ims.file_format = "FFMPEG"
    ff = scene.render.ffmpeg
    ff.format = "MPEG4"
    ff.codec = "H264"
    ff.constant_rate_factor = "HIGH"
    ff.ffmpeg_preset = "GOOD"
    ff.audio_codec = "AAC"
    ff.audio_bitrate = 192
    ff.audio_channels = "STEREO"

    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "renders"), exist_ok=True)
    clip = "".join(c if c.isalnum() else "_" for c in os.path.splitext(os.path.basename(video_path))[0]).strip("_").lower()
    tmpl_part = f"_{template}" if template else ""
    scene.render.filepath = os.path.join(here, "renders", f"{clip}{tmpl_part}_{tag}_")

    se = scene.sequence_editor_create()
    sound_src = wav_path if wav_path and os.path.exists(wav_path) else video_path
    snd = se.strips.new_sound("Audio", sound_src, channel=1, frame_start=1)
    if sound_src == wav_path:
        snd.frame_offset_start = 0
        snd.frame_start = 1
    else:
        off = int(round(start * fps))
        snd.frame_offset_start = off
        snd.frame_start = 1 - off
    snd.frame_final_end = frame_count + 1
