"""Build an audio-reactive toon "video ring" scene in Blender 5.2.

8 outward-facing screens in a ring, each playing the full source video,
scale-pulsing to its own frequency band (Bake Sound to F-Curves), with a
camera that orbits faster when the music is louder, toon look, particle
system, bloom post-fx, and variation pass (palettes, particle modes,
halftone shadows, film grain, scanlines, camera breathing).

Must run WITH a window (the sound bake operator needs a Graph Editor):

    Blender --factory-startup --python build_scene.py -- --start 0 --duration 60 --section 15
    Blender --factory-startup --python build_scene.py -- --full --section 30

Then render headless:

    Blender -b ninja_viz.blend -a
"""

import argparse
import colorsys
import math
import os
import subprocess
import sys
import traceback

import bpy
from bpy_extras import anim_utils
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.join(HERE, "Sunrise.mov")  # overridable with --video
FPS = 24              # set from ffprobe in probe_video()
VIDEO_FRAMES = 6115   # set from ffprobe in probe_video()
BASE_FPS = 30.0       # fps the per-frame constants below were tuned at

N_SCREENS = 8
RING_RADIUS = 4.5
SCREEN_W, SCREEN_H = 1.6, 0.9
SCREEN_Z = 1.2
PULSE_GAIN = 0.6
POSTERIZE_LEVELS = 6
BANDS = [(20, 60), (60, 150), (150, 400), (400, 1000),
         (1000, 2500), (2500, 5000), (5000, 10000), (10000, 20000)]
CAM_RADIUS, CAM_HEIGHT = 6.5, 1.0
CAM_TARGET = (0.0, 0.0, 1.25)  # rig-space: center-locked aim on the Mainframe Core
LAP_SLOW_S, LAP_FAST_S = 55.0, 11.0


def probe_video(path):
    """Read fps and frame count from the source file (sets FPS / VIDEO_FRAMES)."""
    global FPS, VIDEO_FRAMES
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=r_frame_rate,nb_read_packets,duration",
         "-of", "default=nw=1", path], check=True, capture_output=True, text=True).stdout
    info = dict(l.split("=", 1) for l in out.split() if "=" in l)
    num, den = info["r_frame_rate"].split("/")
    FPS = int(round(float(num) / float(den)))
    VIDEO_FRAMES = int(info["nb_read_packets"])
    print(f"[ninja-viz] source {os.path.basename(path)}: {FPS} fps, {VIDEO_FRAMES} frames, "
          f"{VIDEO_FRAMES / FPS:.2f} s")


def per_frame(k):
    """Rescale a per-frame EMA/decay coefficient tuned at BASE_FPS to the current FPS."""
    return 1.0 - (1.0 - k) ** (BASE_FPS / FPS)


def parse_args():
    global VIDEO
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=float, default=0.0, help="excerpt start (s)")
    p.add_argument("--duration", type=float, default=30.0, help="excerpt length (s)")
    p.add_argument("--section", type=float, default=30.0, help="section duration in seconds for palette and motion mode cycle")
    p.add_argument("--full", action="store_true", help="use the whole video")
    p.add_argument("--out", default=os.path.join(HERE, "ninja_viz.blend"))
    p.add_argument("--no-quit", action="store_true")
    p.add_argument("--video", default=VIDEO, help="source video (audio is taken from it too)")
    a = p.parse_args(argv)
    VIDEO = os.path.abspath(a.video)
    probe_video(VIDEO)
    if a.full:
        a.start, a.duration = 0.0, VIDEO_FRAMES / FPS
    return a


# --------------------------------------------------------------------------- utils

def fcurves_of(id_data):
    ad = id_data.animation_data
    if not ad or not ad.action:
        return []
    bag = anim_utils.animdata_get_channelbag_for_assigned_slot(ad)
    return list(bag.fcurves) if bag else []


def find_fcurve(id_data, path):
    for fc in fcurves_of(id_data):
        if fc.data_path == path:
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


# ----------------------------------------------------------------------- materials

def make_video_material(frame_count, frame_offset):
    m, nodes, links = new_mat("Video_Toon")
    img = bpy.data.images.load(VIDEO)
    img.source = "MOVIE"

    tc = nodes.new("ShaderNodeTexCoord")
    be = view_layer_attr(nodes, "big_env")

    # RGB split: offset = 0.012 * big_env
    off = nodes.new("ShaderNodeMath")
    off.operation = "MULTIPLY"
    links.new(be.outputs["Fac"], off.inputs[0])
    off.inputs[1].default_value = 0.012

    comb_off = nodes.new("ShaderNodeCombineXYZ")
    links.new(off.outputs[0], comb_off.inputs[0])

    uv_r = nodes.new("ShaderNodeVectorMath")
    uv_r.operation = "SUBTRACT"
    links.new(tc.outputs["UV"], uv_r.inputs[0])
    links.new(comb_off.outputs[0], uv_r.inputs[1])

    uv_b = nodes.new("ShaderNodeVectorMath")
    uv_b.operation = "ADD"
    links.new(tc.outputs["UV"], uv_b.inputs[0])
    links.new(comb_off.outputs[0], uv_b.inputs[1])

    def make_tex(uv_sock):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        iu = tex.image_user
        iu.frame_duration = frame_count
        iu.frame_start = 1
        iu.frame_offset = frame_offset
        iu.use_auto_refresh = True
        iu.use_cyclic = False
        links.new(uv_sock, tex.inputs["Vector"])
        return tex

    tex_r = make_tex(uv_r.outputs[0])
    tex_g = make_tex(tc.outputs["UV"])
    tex_b = make_tex(uv_b.outputs[0])

    sep_r = nodes.new("ShaderNodeSeparateColor")
    sep_g = nodes.new("ShaderNodeSeparateColor")
    sep_b = nodes.new("ShaderNodeSeparateColor")
    links.new(tex_r.outputs["Color"], sep_r.inputs["Color"])
    links.new(tex_g.outputs["Color"], sep_g.inputs["Color"])
    links.new(tex_b.outputs["Color"], sep_b.inputs["Color"])

    comb = nodes.new("ShaderNodeCombineColor")
    links.new(sep_r.outputs["Red"], comb.inputs["Red"])
    links.new(sep_g.outputs["Green"], comb.inputs["Green"])
    links.new(sep_b.outputs["Blue"], comb.inputs["Blue"])

    # Hue rotation: Hue = 0.5 + 0.15 * big_env
    hs = nodes.new("ShaderNodeHueSaturation")
    links.new(comb.outputs["Color"], hs.inputs["Color"])
    hue_val = nodes.new("ShaderNodeMath")
    hue_val.operation = "MULTIPLY_ADD"
    links.new(be.outputs["Fac"], hue_val.inputs[0])
    hue_val.inputs[1].default_value = 0.15
    hue_val.inputs[2].default_value = 0.5
    links.new(hue_val.outputs[0], hs.inputs["Hue"])

    # Dynamic posterize: SNAP inc = 1 / (6 - 3 * min(big_env, 1))
    min_e = nodes.new("ShaderNodeMath")
    min_e.operation = "MINIMUM"
    links.new(be.outputs["Fac"], min_e.inputs[0])
    min_e.inputs[1].default_value = 1.0

    mul3 = nodes.new("ShaderNodeMath")
    mul3.operation = "MULTIPLY"
    links.new(min_e.outputs[0], mul3.inputs[0])
    mul3.inputs[1].default_value = 3.0

    lev = nodes.new("ShaderNodeMath")
    lev.operation = "SUBTRACT"
    lev.inputs[0].default_value = 6.0
    links.new(mul3.outputs[0], lev.inputs[1])

    step = nodes.new("ShaderNodeMath")
    step.operation = "DIVIDE"
    step.inputs[0].default_value = 1.0
    links.new(lev.outputs[0], step.inputs[1])

    half = nodes.new("ShaderNodeMath")
    half.operation = "MULTIPLY"
    half.inputs[1].default_value = 0.5
    links.new(step.outputs[0], half.inputs[0])

    step_v = nodes.new("ShaderNodeCombineXYZ")
    links.new(step.outputs[0], step_v.inputs[0])
    links.new(step.outputs[0], step_v.inputs[1])
    links.new(step.outputs[0], step_v.inputs[2])

    half_v = nodes.new("ShaderNodeCombineXYZ")
    links.new(half.outputs[0], half_v.inputs[0])
    links.new(half.outputs[0], half_v.inputs[1])
    links.new(half.outputs[0], half_v.inputs[2])

    half_add = nodes.new("ShaderNodeVectorMath")
    half_add.operation = "ADD"
    links.new(hs.outputs["Color"], half_add.inputs[0])
    links.new(half_v.outputs[0], half_add.inputs[1])

    snap = nodes.new("ShaderNodeVectorMath")
    snap.operation = "SNAP"
    links.new(half_add.outputs[0], snap.inputs[0])
    links.new(step_v.outputs[0], snap.inputs[1])

    emission_output(nodes, links, snap.outputs["Vector"], 1.5)
    return m


def make_cel_material(name, base_rgb, shadow_rgb, highlight_rgb, checker=False):
    m, nodes, links = new_mat(name)
    diff = nodes.new("ShaderNodeBsdfDiffuse")
    s2r = nodes.new("ShaderNodeShaderToRGB")
    links.new(diff.outputs[0], s2r.inputs[0])
    ramp = nodes.new("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    cr.interpolation = "CONSTANT"
    cr.elements[0].position, cr.elements[0].color = 0.0, (*shadow_rgb, 1)
    cr.elements[1].position, cr.elements[1].color = 0.25, (*base_rgb, 1)
    e = cr.elements.new(0.75)
    e.color = (*highlight_rgb, 1)
    bw = nodes.new("ShaderNodeRGBToBW")
    links.new(s2r.outputs["Color"], bw.inputs[0])
    if checker:
        chk = nodes.new("ShaderNodeTexChecker")
        chk.inputs["Scale"].default_value = 12.0
        chk.inputs["Color1"].default_value = (1, 1, 1, 1)
        chk.inputs["Color2"].default_value = (0.6, 0.6, 0.6, 1)
        mul = nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        links.new(bw.outputs[0], mul.inputs[0])
        links.new(chk.outputs["Fac"], mul.inputs[1])
        links.new(mul.outputs[0], ramp.inputs["Fac"])
    else:
        links.new(bw.outputs[0], ramp.inputs["Fac"])

    # Halftone dots in the shadow band
    tc = nodes.new("ShaderNodeTexCoord")
    vor = nodes.new("ShaderNodeTexVoronoi")
    vor.voronoi_dimensions = "2D"
    vor.feature = "F1"
    vor.distance = "EUCLIDEAN"
    vor.inputs["Randomness"].default_value = 0.0
    vor.inputs["Scale"].default_value = 60.0
    links.new(tc.outputs["Window"], vor.inputs["Vector"])

    in_shadow = nodes.new("ShaderNodeMath")
    in_shadow.operation = "LESS_THAN"
    in_shadow.inputs[1].default_value = 0.25
    links.new(bw.outputs[0], in_shadow.inputs[0])

    dot = nodes.new("ShaderNodeMath")
    dot.operation = "LESS_THAN"
    dot.inputs[1].default_value = 0.35
    links.new(vor.outputs["Distance"], dot.inputs[0])

    ht_fac = nodes.new("ShaderNodeMath")
    ht_fac.operation = "MULTIPLY"
    links.new(in_shadow.outputs[0], ht_fac.inputs[0])
    links.new(dot.outputs[0], ht_fac.inputs[1])

    dark_ramp = nodes.new("ShaderNodeVectorMath")
    dark_ramp.operation = "SCALE"
    dark_ramp.inputs["Scale"].default_value = 0.35
    links.new(ramp.outputs["Color"], dark_ramp.inputs[0])

    mix_ht = nodes.new("ShaderNodeMix")
    mix_ht.data_type = "VECTOR"
    links.new(ht_fac.outputs[0], sk(mix_ht.inputs, "Factor_Float"))
    links.new(ramp.outputs["Color"], sk(mix_ht.inputs, "A_Vector"))
    links.new(dark_ramp.outputs[0], sk(mix_ht.inputs, "B_Vector"))

    # Light tint: the ramp only sees luminance, so recover the light's chroma
    lum = nodes.new("ShaderNodeMath")
    lum.operation = "ADD"
    lum.inputs[1].default_value = 0.001
    links.new(bw.outputs[0], lum.inputs[0])
    chroma = nodes.new("ShaderNodeVectorMath")
    chroma.operation = "DIVIDE"
    links.new(s2r.outputs["Color"], chroma.inputs[0])
    links.new(lum.outputs[0], chroma.inputs[1])
    cap = nodes.new("ShaderNodeVectorMath")
    cap.operation = "MINIMUM"
    cap.inputs[1].default_value = (1.6, 1.6, 1.6)
    links.new(chroma.outputs[0], cap.inputs[0])
    soft = nodes.new("ShaderNodeMix")
    soft.data_type = "VECTOR"
    sk(soft.inputs, "Factor_Float").default_value = 0.6
    sk(soft.inputs, "A_Vector").default_value = (1, 1, 1)
    links.new(cap.outputs[0], sk(soft.inputs, "B_Vector"))
    tint = nodes.new("ShaderNodeVectorMath")
    tint.operation = "MULTIPLY"
    tint.name = "CelOut"
    links.new(sk(mix_ht.outputs, "Result_Vector"), tint.inputs[0])
    links.new(sk(soft.outputs, "Result_Vector"), tint.inputs[1])

    # Palette hue shift
    hs = nodes.new("ShaderNodeHueSaturation")
    links.new(tint.outputs[0], hs.inputs["Color"])
    hue_attr = view_layer_attr(nodes, "hue")
    links.new(hue_attr.outputs["Fac"], hs.inputs["Hue"])

    emission_output(nodes, links, hs.outputs["Color"], 1.0)
    return m


def make_outline_material():
    m, nodes, links = new_mat("Outline_Black")
    em = nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0, 0, 0, 1)
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(em.outputs[0], out.inputs["Surface"])
    m.use_backface_culling = True
    return m


def add_outline(obj, outline_mat, scene, thickness=0.03):
    obj.data.materials.append(outline_mat)
    mod = obj.modifiers.new("Outline", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_rim = False
    mod.material_offset = len(obj.data.materials) - 1
    # outline thickness pulses with bass_s
    drive(obj, f'modifiers["{mod.name}"].thickness', f"{thickness} * (1.0 + 1.2 * bs)",
          bs=(scene, '["bass_s"]'))


# ----------------------------------------------------------------------- scene

def reset_scene():
    # Don't reload factory settings here (it would rebuild the UI mid-script).
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.cameras,
                 bpy.data.lights, bpy.data.actions, bpy.data.images, bpy.data.worlds):
        for item in list(coll):
            coll.remove(item)
    return bpy.context.scene


def build_world(scene):
    world = bpy.data.worlds.new("ToonCosmos")
    scene.world = world
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()

    coord = nodes.new("ShaderNodeTexCoord")

    # Drifting mapping for nebula
    mapping = nodes.new("ShaderNodeMapping")
    links.new(coord.outputs["Normal"], mapping.inputs["Vector"])
    drive(world.node_tree, f'nodes["{mapping.name}"].inputs[1].default_value',
          "frame / 24.0 * 0.02", 0)

    # 3D Noise for nebula clouds
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 1.5
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.55
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])

    # Posterized color ramp with hard toon bands
    ramp = nodes.new("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    cr.interpolation = "CONSTANT"
    cr.elements[0].position, cr.elements[0].color = 0.0, (0.003, 0.004, 0.015, 1)   # deep space void
    cr.elements[1].position, cr.elements[1].color = 0.44, (0.035, 0.010, 0.080, 1)  # dark violet nebula
    e1 = cr.elements.new(0.58); e1.color = (0.080, 0.025, 0.160, 1)                 # mid magenta band
    e2 = cr.elements.new(0.72); e2.color = (0.130, 0.060, 0.280, 1)                 # bright cyan-violet band
    links.new(noise.outputs["Factor"], ramp.inputs["Fac"])

    # Screen-space halftone dots in the nebula
    vor_ht = nodes.new("ShaderNodeTexVoronoi")
    vor_ht.voronoi_dimensions = "2D"
    vor_ht.inputs["Scale"].default_value = 75.0
    links.new(coord.outputs["Window"], vor_ht.inputs["Vector"])
    dot_ht = nodes.new("ShaderNodeMath")
    dot_ht.operation = "GREATER_THAN"
    dot_ht.inputs[1].default_value = 0.40
    links.new(vor_ht.outputs["Distance"], dot_ht.inputs[0])

    # Multiply halftone into nebula
    nebula_ht = nodes.new("ShaderNodeMix")
    nebula_ht.data_type = "RGBA"
    nebula_ht.blend_type = "MULTIPLY"
    nebula_ht.inputs["Factor"].default_value = 0.35
    links.new(ramp.outputs["Color"], nebula_ht.inputs["A"])
    links.new(dot_ht.outputs[0], nebula_ht.inputs["B"])

    # Star Layer 1: Prominent stars with dynamic 4D Voronoi twinkle
    vor1 = nodes.new("ShaderNodeTexVoronoi")
    vor1.name = "StarsVoronoi1"
    vor1.voronoi_dimensions = "4D"
    vor1.inputs["Scale"].default_value = 50.0
    links.new(coord.outputs["Normal"], vor1.inputs["Vector"])
    drive(world.node_tree, f'nodes["{vor1.name}"].inputs[1].default_value',
          "frame / 24.0 * (0.3 + 1.8 * l)", l=(scene, '["loud"]'))

    star_dot1 = nodes.new("ShaderNodeMath")
    star_dot1.operation = "LESS_THAN"
    star_dot1.inputs[1].default_value = 0.045
    links.new(vor1.outputs["Distance"], star_dot1.inputs[0])

    # Star Layer 2: Fine background starfield
    vor2 = nodes.new("ShaderNodeTexVoronoi")
    vor2.name = "StarsVoronoi2"
    vor2.voronoi_dimensions = "4D"
    vor2.inputs["Scale"].default_value = 110.0
    links.new(coord.outputs["Normal"], vor2.inputs["Vector"])
    drive(world.node_tree, f'nodes["{vor2.name}"].inputs[1].default_value',
          "frame / 24.0 * (0.2 + 1.2 * l)", l=(scene, '["loud"]'))

    star_dot2 = nodes.new("ShaderNodeMath")
    star_dot2.operation = "LESS_THAN"
    star_dot2.inputs[1].default_value = 0.035
    links.new(vor2.outputs["Distance"], star_dot2.inputs[0])

    # Star brightness summation
    stars_comb = nodes.new("ShaderNodeMath")
    stars_comb.operation = "ADD"
    links.new(star_dot1.outputs[0], stars_comb.inputs[0])
    links.new(star_dot2.outputs[0], stars_comb.inputs[1])

    # Tint stars with 50% spec color / 50% white
    spec_attr = nodes.new("ShaderNodeAttribute")
    spec_attr.attribute_type = "VIEW_LAYER"
    spec_attr.attribute_name = "spec"

    star_tint = nodes.new("ShaderNodeMix")
    star_tint.data_type = "RGBA"
    star_tint.inputs["Factor"].default_value = 0.5
    star_tint.inputs["A"].default_value = (1.0, 1.0, 1.0, 1.0)
    links.new(spec_attr.outputs["Color"], star_tint.inputs["B"])

    # Star emission strength (blooms on highlights)
    star_intensity = nodes.new("ShaderNodeMath")
    star_intensity.operation = "MULTIPLY"
    star_intensity.inputs[1].default_value = 3.6
    links.new(stars_comb.outputs[0], star_intensity.inputs[0])

    star_colored = nodes.new("ShaderNodeMix")
    star_colored.data_type = "RGBA"
    star_colored.blend_type = "MULTIPLY"
    star_colored.inputs["Factor"].default_value = 1.0
    links.new(star_tint.outputs["Result"], star_colored.inputs["A"])
    links.new(star_intensity.outputs[0], star_colored.inputs["B"])

    # Add stars on top of nebula
    world_add = nodes.new("ShaderNodeMix")
    world_add.data_type = "RGBA"
    world_add.blend_type = "ADD"
    world_add.inputs["Factor"].default_value = 1.0
    links.new(nebula_ht.outputs["Result"], world_add.inputs["A"])
    links.new(star_colored.outputs["Result"], world_add.inputs["B"])

    # Section palette hue rotation
    hs = nodes.new("ShaderNodeHueSaturation")
    links.new(world_add.outputs["Result"], hs.inputs["Color"])
    hue_attr = nodes.new("ShaderNodeAttribute")
    hue_attr.attribute_type = "VIEW_LAYER"
    hue_attr.attribute_name = "hue"
    links.new(hue_attr.outputs["Fac"], hs.inputs["Hue"])

    bg = nodes.new("ShaderNodeBackground")
    out = nodes.new("ShaderNodeOutputWorld")
    links.new(hs.outputs["Color"], bg.inputs["Color"])
    links.new(bg.outputs[0], out.inputs["Surface"])
    return world


def build_screens(video_mat, bezel_mat, outline_mat, scene):
    screens = []
    for i in range(N_SCREENS):
        theta = i * 2 * math.pi / N_SCREENS
        pivot = bpy.data.objects.new(f"Screen_{i}", None)
        bpy.context.collection.objects.link(pivot)
        pivot.empty_display_type = "ARROWS"
        pivot.location = (RING_RADIUS * math.cos(theta), RING_RADIUS * math.sin(theta), SCREEN_Z)
        # plane faces local -Y after rot X 90; face outward (+radial)
        pivot.rotation_euler = (math.radians(90), 0, theta + math.radians(90))

        bpy.ops.mesh.primitive_plane_add(size=1)
        plane = bpy.context.active_object
        plane.name = f"Screen_{i}_Video"
        plane.scale = (SCREEN_W, SCREEN_H, 1)
        plane.location = (0, 0, 0.026)
        plane.data.materials.append(video_mat)
        plane.parent = pivot

        bpy.ops.mesh.primitive_cube_add(size=1)
        bezel = bpy.context.active_object
        bezel.name = f"Screen_{i}_Bezel"
        bezel.scale = (SCREEN_W + 0.12, SCREEN_H + 0.12, 0.05)
        bezel.data.materials.append(bezel_mat)
        bev = bezel.modifiers.new("Bevel", "BEVEL")
        bev.width, bev.segments = 0.02, 2
        add_outline(bezel, outline_mat, scene, 0.03)
        bezel.parent = pivot

        pivot["audio"] = 0.0
        pivot["norm"] = 1.0
        pivot.keyframe_insert('["audio"]', frame=1)
        screens.append(pivot)
    return screens


def build_floor(floor_mat):
    bpy.ops.mesh.primitive_plane_add(size=80)
    floor = bpy.context.active_object
    floor.name = "Floor"
    floor.data.materials.append(floor_mat)
    return floor


def build_camera(scene):
    rig = bpy.data.objects.new("CamRig", None)
    scene.collection.objects.link(rig)

    pivot = bpy.data.objects.new("CamPivot", None)
    scene.collection.objects.link(pivot)
    pivot.parent = rig
    pivot.location = (0.0, 0.0, ORB_Z)

    # 20s periodic smooth pitch arc: 0° (eye-level) <-> 90° (overhead looking straight down)
    drive(pivot, "rotation_euler", f"- (pi / 4.0) * (1.0 - cos(frame / {FPS} * 2 * pi / 20.0))", 1)

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 35
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.parent = pivot
    cam.location = (CAM_RADIUS, 0, 0)
    # Camera facing pivot: -Z forward, +Y up -> aligned to pitch arm
    cam.rotation_euler = (math.radians(90), 0, math.radians(90))
    scene.camera = cam
    rig["loud"] = 0.0
    rig["norm"] = 1.0
    rig.keyframe_insert('["loud"]', frame=1)

    # Radial breathing dolly (6.5 <-> 5.7m, 8s period) + bass punch
    drive(cam, "location", f"6.1 + 0.4 * cos(frame / {FPS} * 2 * pi / 8.0) - 0.2 * bs", 0,
          bs=(scene, '["bass_s"]'))

    return rig


def build_light(scene):
    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 4.0
    sun.angle = 0.0
    obj = bpy.data.objects.new("Sun", sun)
    scene.collection.objects.link(obj)
    obj.rotation_euler = (math.radians(50), 0, math.radians(30))


# ----------------------------------------------------------------------- audio bake

def extract_wav(start, duration):
    wav = os.path.join(HERE, "audio_excerpt.wav")
    if os.path.exists(wav):
        os.remove(wav)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(duration),
                    "-i", VIDEO, "-vn", "-ac", "2", "-ar", "48000", wav], check=True)
    return wav


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
    fc.select = True
    fc.hide = False
    with bpy.context.temp_override(**ov):
        bpy.ops.graph.sound_to_samples(
            filepath=wav, low=float(lo), high=float(hi),
            attack=0.005, release=0.1, threshold=0.0,
            use_accumulate=False, use_additive=False, use_square=False)
    fc = find_fcurve(obj, path)
    vals = sampled_values(fc)
    if len(vals) < 2:
        raise RuntimeError(f"bake produced no samples on {obj.name}{path}")
    return vals


def add_scale_driver(screen):
    for axis in range(3):
        d = screen.driver_add("scale", axis).driver
        d.type = "SCRIPTED"
        for name, prop in (("a", '["audio"]'), ("n", '["norm"]')):
            v = d.variables.new()
            v.name = name
            v.targets[0].id = screen
            v.targets[0].data_path = prop
        # squared response: quiet passages stay calm, hits punch
        d.expression = f"1.0 + {PULSE_GAIN} * min(a * n * a * n, 1.5)"


def key_orbit(rig, loud_vals, frame_count):
    norm = rig["norm"]
    w_slow = 2 * math.pi / (LAP_SLOW_S * FPS)
    w_fast = 2 * math.pi / (LAP_FAST_S * FPS)
    # light smoothing (EMA) so speed changes feel like momentum
    smooth, s = [], 0.0
    for v in loud_vals[:frame_count]:
        s += (min(v * norm, 1.0) - s) * per_frame(0.08)
        smooth.append(s)
    angle, co = 0.0, []
    for f, l in enumerate(smooth):
        co += [f + 1, angle]
        angle -= w_slow + (w_fast - w_slow) * l
    rig.rotation_euler = (0, 0, 0)
    rig.keyframe_insert("rotation_euler", index=2, frame=1)
    fc = next(c for c in fcurves_of(rig) if c.data_path == "rotation_euler" and c.array_index == 2)
    fc.keyframe_points.clear()
    fc.keyframe_points.add(len(smooth))
    fc.keyframe_points.foreach_set("co", co)
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
    fc.update()


# ----------------------------------------------------------------------- audio bus & palettes

ORB_Z = SCREEN_Z
ORB_R = 0.35
P_LIFE = 2.2          # seconds
P_DRAG = 0.97         # per frame at BASE_FPS (rescaled in the sim)
P_GRAVITY = 1.5       # m/s^2

PALETTES = [
    # 0: Sunset (warm red / orange / pink)
    {"name": "Sunset",
     "stops": [(0.0, (1.0, 0.05, 0.35)), (0.5, (1.0, 0.55, 0.05)), (1.0, (1.0, 0.2, 0.8))],
     "hue": 0.0},
    # 1: Ice (cyan / blue / violet)
    {"name": "Ice",
     "stops": [(0.0, (0.05, 0.5, 1.0)), (0.5, (0.2, 0.85, 1.0)), (1.0, (0.7, 0.3, 1.0))],
     "hue": 0.55},
    # 2: Acid (lime / yellow / teal)
    {"name": "Acid",
     "stops": [(0.0, (0.4, 0.95, 0.1)), (0.5, (0.95, 0.9, 0.1)), (1.0, (0.05, 0.9, 0.8))],
     "hue": 0.25},
    # 3: Neon (white-pink / magenta / cyan)
    {"name": "Neon",
     "stops": [(0.0, (1.0, 0.1, 0.6)), (0.5, (0.95, 0.4, 1.0)), (1.0, (0.1, 0.95, 1.0))],
     "hue": 0.8},
]


def video_colors(start, duration, n):
    """Extract per-frame average RGB from the source video using ffmpeg 1x1 scale."""
    cmd = [
        "ffmpeg", "-v", "error", "-ss", str(start), "-t", str(duration),
        "-i", VIDEO, "-vf", f"fps={FPS},scale=1:1",
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
    k_smooth = per_frame(0.15)
    for c in cols:
        if s is None:
            s = c
        else:
            s = tuple(s[k] + (c[k] - s[k]) * k_smooth for k in range(3))
        smooth.append(s)
    return smooth


def interp_stops(t, stops):
    """Interpolate color stops in HSV (hue unwrapped forward)."""
    t = max(0.0, min(1.0, t))
    hsv = [(p, colorsys.rgb_to_hsv(*c)) for p, c in stops]
    hues, prev = [], None
    for _, (h, _, _) in hsv:
        if prev is not None:
            while h < prev:
                h += 1.0
        hues.append(h)
        prev = h
    for i in range(len(hsv) - 1):
        p0, p1 = hsv[i][0], hsv[i + 1][0]
        if t <= p1:
            k = (t - p0) / (p1 - p0)
            h = hues[i] + (hues[i + 1] - hues[i]) * k
            sat = hsv[i][1][1] + (hsv[i + 1][1][1] - hsv[i][1][1]) * k
            val = hsv[i][1][2] + (hsv[i + 1][1][2] - hsv[i][1][2]) * k
            return colorsys.hsv_to_rgb(h % 1.0, sat, val)
    return stops[-1][1]


def blend_hsv(c1, c2, w):
    """Blend two RGB colors in HSV along the shortest hue arc."""
    h1, s1, v1 = colorsys.rgb_to_hsv(*c1)
    h2, s2, v2 = colorsys.rgb_to_hsv(*c2)
    dh = ((h2 - h1 + 0.5) % 1.0) - 0.5
    h = (h1 + w * dh) % 1.0
    s = max(0.0, min(1.0, (1.0 - w) * s1 + w * s2))
    v = max(0.0, min(1.0, (1.0 - w) * v1 + w * v2))
    return colorsys.hsv_to_rgb(h, s, v)


def build_schedule(n, big, section_s):
    """Compute section boundaries (snapped to nearest big beat within +-2s)
    and per-frame mode, palette indices, and cross-fade weight."""
    sec_frames = int(round(section_s * FPS))
    fade_len = max(1, int(round(0.5 * FPS)))
    window = int(round(2.0 * FPS))

    raw_bounds = []
    t = sec_frames
    while t < n:
        raw_bounds.append(t)
        t += sec_frames

    big_frames = [f for f, b in enumerate(big) if b > 0.5]
    bounds = [0]
    for rb in raw_bounds:
        candidates = [bf for bf in big_frames if abs(bf - rb) <= window]
        if candidates:
            snapped = min(candidates, key=lambda x: abs(x - rb))
        else:
            snapped = rb
        if snapped > bounds[-1] + fade_len:
            bounds.append(snapped)
    bounds.append(n)

    mode_list = [0.0] * n
    pal_a_list = [0] * n
    pal_b_list = [0] * n
    weight_list = [0.0] * n

    for k in range(len(bounds) - 1):
        b_start = bounds[k]
        b_end = bounds[k + 1]
        m = float(k % 3)
        pal_curr = k % 4
        pal_prev = (k - 1) % 4 if k > 0 else pal_curr

        for f in range(b_start, min(b_end, n)):
            mode_list[f] = m
            if k > 0 and f < b_start + fade_len:
                w = (f - b_start) / float(fade_len)
                pal_a_list[f] = pal_prev
                pal_b_list[f] = pal_curr
                weight_list[f] = w
            else:
                pal_a_list[f] = pal_curr
                pal_b_list[f] = pal_curr
                weight_list[f] = 1.0

    return bounds, mode_list, pal_a_list, pal_b_list, weight_list


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


def build_audio_bus(scene, band_vals, band_norms, loud_vals, loud_norm, n, section_s, vid_colors):
    nb = [[min(v * nm, 1.5) for v in vals[:n]] for vals, nm in zip(band_vals, band_norms)]
    loud = [min(v * loud_norm, 1.5) for v in loud_vals[:n]]
    bass = [(nb[0][f] + nb[1][f]) / 2 for f in range(n)]

    # onsets on the bass envelope
    beat, last = [0.0] * n, -99
    win_size = max(2, round(0.33 * FPS))
    min_dist = max(2, round(0.2 * FPS))
    for f in range(n):
        w = bass[max(0, f - win_size):f]
        m = sum(w) / len(w) if w else 0.0
        if bass[f] > 0.35 and bass[f] > 1.2 * m and f - last >= min_dist:
            beat[f], last = bass[f], f
    onsets = [f for f in range(n) if beat[f] > 0]
    env, e = [], 0.0
    decay = 0.75 ** (BASE_FPS / FPS)
    for f in range(n):
        e = max(beat[f], e * decay)
        env.append(e)
    big = [0.0] * n
    big_onsets = []
    if onsets:
        thr = percentile([beat[f] for f in onsets], 0.85)
        for f in onsets:
            if beat[f] >= thr:
                big_onsets.append(f)
                for k in range(max(2, round(0.1 * FPS))):
                    if f + k < n:
                        big[f + k] = 1.0

    # bass_s (EMA smoothed bass, ~0.25s)
    bass_s, bs = [], 0.0
    k_bs = per_frame(0.15)
    for v in bass:
        bs += (v - bs) * k_bs
        bass_s.append(bs)

    # big_env (exponential decay of big, ~0.3s)
    big_env, be = [], 0.0
    decay_be = 0.85 ** (BASE_FPS / FPS)
    for b in big:
        be = max(b, be * decay_be)
        big_env.append(be)

    # spectral centroid
    cent, s = [], None
    for f in range(n):
        tot = sum(nb[i][f] for i in range(8))
        c = sum(i * nb[i][f] for i in range(8)) / (7 * max(tot, 1e-6))
        s = c if s is None else s + (c - s) * per_frame(0.15)
        cent.append(s)
    lo, hi = percentile(cent, 0.05), percentile(cent, 0.95)
    span = max(hi - lo, 1e-6)

    # Section schedule
    bounds, mode_list, pal_a_list, pal_b_list, weight_list = build_schedule(n, big, section_s)

    spec_cols, hue_list, pcols = [], [], []
    for f in range(n):
        t_spec = (cent[f] - lo) / span
        pa, pb, w = pal_a_list[f], pal_b_list[f], weight_list[f]
        col_a = interp_stops(t_spec, PALETTES[pa]["stops"])
        col_b = interp_stops(t_spec, PALETTES[pb]["stops"])
        spec_c = blend_hsv(col_a, col_b, w)
        spec_cols.append(spec_c)

        ha, hb = PALETTES[pa]["hue"], PALETTES[pb]["hue"]
        dh = ((hb - ha + 0.5) % 1.0) - 0.5
        h_offset = (ha + w * dh) % 1.0
        hue_list.append((0.5 + h_offset) % 1.0)

        # 60% spectrum color / 40% video average color
        pcol = blend_hsv(spec_c, vid_colors[f], 0.4)
        pcols.append(pcol)

    for name in ("loud", "bass", "bass_s", "beat", "beat_env", "big", "big_env", "mode", "hue"):
        scene[name] = 0.0
    scene["spec"] = [1.0, 0.2, 0.5]
    scene["pcol"] = [1.0, 0.2, 0.5]

    write_keys(scene, "loud", loud)
    write_keys(scene, "bass", bass)
    write_keys(scene, "bass_s", bass_s)
    write_keys(scene, "beat", beat, constant=True)
    write_keys(scene, "beat_env", env)
    write_keys(scene, "big", big, constant=True)
    write_keys(scene, "big_env", big_env)
    write_keys(scene, "mode", mode_list, constant=True)
    write_keys(scene, "hue", hue_list)
    for i in range(3):
        write_keys(scene, "spec", [c[i] for c in spec_cols], index=i)
        write_keys(scene, "pcol", [c[i] for c in pcols], index=i)

    print(f"[ninja-viz] audio bus: {len(onsets)} onsets ({len(onsets) / (n / FPS):.2f}/s), "
          f"big frames={int(sum(big))}, centroid p5..p95={lo:.3f}..{hi:.3f}")
    print(f"[ninja-viz] sections: {len(bounds) - 1} sections, boundaries: {bounds}")
    return onsets, big_onsets


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


# ----------------------------------------------------------------------- mainframe core + light

def build_mainframe_core(scene, outline_mat):
    # 1. Inner Plasma Core
    m_plasma, nodes, links = new_mat("PlasmaCore_Mat")
    spec = view_layer_attr(nodes, "spec")
    loud = view_layer_attr(nodes, "loud")
    big = view_layer_attr(nodes, "big_env")

    math_loud = nodes.new("ShaderNodeMath")
    math_loud.operation = "MULTIPLY"
    links.new(loud.outputs["Fac"], math_loud.inputs[0])
    math_loud.inputs[1].default_value = 18.0

    math_big = nodes.new("ShaderNodeMath")
    math_big.operation = "MULTIPLY"
    links.new(big.outputs["Fac"], math_big.inputs[0])
    math_big.inputs[1].default_value = 16.0

    sum_l_b = nodes.new("ShaderNodeMath")
    sum_l_b.operation = "ADD"
    links.new(math_loud.outputs[0], sum_l_b.inputs[0])
    links.new(math_big.outputs[0], sum_l_b.inputs[1])

    strength = nodes.new("ShaderNodeMath")
    strength.operation = "ADD"
    links.new(sum_l_b.outputs[0], strength.inputs[0])
    strength.inputs[1].default_value = 4.0

    em = emission_output(nodes, links, spec.outputs["Color"])
    links.new(strength.outputs[0], em.inputs["Strength"])

    bpy.ops.mesh.primitive_ico_sphere_add(radius=0.22, subdivisions=3, location=(0, 0, ORB_Z))
    core = bpy.context.active_object
    core.name = "PlasmaCore"
    bpy.ops.object.shade_smooth()
    core.data.materials.append(m_plasma)
    core.visible_shadow = False
    for axis in range(3):
        drive(core, "scale", "0.9 + 0.35 * b + 0.25 * be", axis,
              b=(scene, '["bass"]'), be=(scene, '["big_env"]'))

    # 2. Concentric Multi-Axis Gimbal Rings
    ring_mat = make_cel_material("Gimbal_Ring_Cel", (0.04, 0.06, 0.10), (0.01, 0.015, 0.025), (0.5, 0.85, 1.0))
    ring_configs = [
        # (radius, minor_r, tilt_deg (x, y, z), spin_driver_expr)
        (0.38, 0.016, (35, 0, 0), "1.4 * (frame / 24.0) + 1.0 * (frame / 24.0) * l + 0.45 * b"),
        (0.50, 0.018, (0, 45, 0), "-1.8 * (frame / 24.0) - 1.2 * (frame / 24.0) * l + 0.55 * b"),
        (0.62, 0.020, (25, 25, 0), "1.0 * (frame / 24.0) + 0.7 * (frame / 24.0) * l - 0.40 * b"),
    ]
    rings = []
    for i, (r_maj, r_min, tilt, spin_expr) in enumerate(ring_configs):
        # Empty pivot for tilt orientation
        pivot = bpy.data.objects.new(f"GimbalPivot_{i}", None)
        scene.collection.objects.link(pivot)
        pivot.location = (0, 0, ORB_Z)
        pivot.rotation_euler = (math.radians(tilt[0]), math.radians(tilt[1]), math.radians(tilt[2]))

        bpy.ops.mesh.primitive_torus_add(
            major_radius=r_maj, minor_radius=r_min,
            major_segments=48, minor_segments=12,
            location=(0, 0, 0)
        )
        ring = bpy.context.active_object
        ring.name = f"GimbalRing_{i}"
        bpy.ops.object.shade_smooth()
        ring.parent = pivot
        ring.data.materials.append(ring_mat)
        add_outline(ring, outline_mat, scene, 0.012)
        drive(ring, "rotation_euler", spin_expr, 2, l=(scene, '["loud"]'), b=(scene, '["beat"]'))
        rings.append(ring)

    # 3. Outer Neon Wireframe Cage
    bpy.ops.mesh.primitive_ico_sphere_add(radius=0.74, subdivisions=1, location=(0, 0, ORB_Z))
    cage = bpy.context.active_object
    cage.name = "WireframeCage"
    cage.visible_shadow = False

    mod_wf = cage.modifiers.new("Wireframe", "WIREFRAME")
    mod_wf.thickness = 0.012
    mod_wf.use_replace = True
    mod_wf.use_even_offset = True

    m_cage, c_nodes, c_links = new_mat("Cage_Neon")
    c_spec = view_layer_attr(c_nodes, "spec")
    c_loud = view_layer_attr(c_nodes, "loud")
    c_big = view_layer_attr(c_nodes, "big_env")

    c_m1 = c_nodes.new("ShaderNodeMath")
    c_m1.operation = "MULTIPLY"
    c_links.new(c_loud.outputs["Fac"], c_m1.inputs[0])
    c_m1.inputs[1].default_value = 5.0

    c_m2 = c_nodes.new("ShaderNodeMath")
    c_m2.operation = "MULTIPLY"
    c_links.new(c_big.outputs["Fac"], c_m2.inputs[0])
    c_m2.inputs[1].default_value = 8.0

    c_sum = c_nodes.new("ShaderNodeMath")
    c_sum.operation = "ADD"
    c_links.new(c_m1.outputs[0], c_sum.inputs[0])
    c_links.new(c_m2.outputs[0], c_sum.inputs[1])

    c_str = c_nodes.new("ShaderNodeMath")
    c_str.operation = "ADD"
    c_links.new(c_sum.outputs[0], c_str.inputs[0])
    c_str.inputs[1].default_value = 2.5

    c_em = emission_output(c_nodes, c_links, c_spec.outputs["Color"])
    c_links.new(c_str.outputs[0], c_em.inputs["Strength"])
    cage.data.materials.append(m_cage)

    drive(cage, "rotation_euler", "-0.5 * (frame / 24.0)", 2)
    for axis in range(3):
        drive(cage, "scale", "1.0 + 0.08 * be + 0.04 * b", axis,
              be=(scene, '["big_env"]'), b=(scene, '["beat"]'))

    return core, rings, cage


def build_orb(scene, outline_mat):
    return build_mainframe_core(scene, outline_mat)


def build_orb_light(scene):
    light = bpy.data.lights.new("OrbLight", "POINT")
    light.shadow_soft_size = 0.2
    obj = bpy.data.objects.new("OrbLight", light)
    scene.collection.objects.link(obj)
    obj.location = (0, 0, ORB_Z)
    for i in range(3):
        drive(light, "color", "c", i, c=(scene, f'["spec"][{i}]'))
    drive(light, "energy", "250 + 2200 * l + 1200 * be",
          l=(scene, '["loud"]'), be=(scene, '["big_env"]'))
    return obj


def build_streaks(scene, big_frames, frame_count):
    if not big_frames:
        return []
    streaks = []
    m, nodes, links = new_mat("Streak_Mat")
    spec = view_layer_attr(nodes, "spec")
    emission_output(nodes, links, spec.outputs["Color"], 8.0)

    n_pool = 4
    for i in range(n_pool):
        bpy.ops.mesh.primitive_cone_add(radius1=0.04, radius2=0.0, depth=4.0, location=(0, 0, -100))
        cone = bpy.context.active_object
        cone.name = f"ShootingStreak_{i}"
        cone.data.materials.append(m)
        cone.visible_shadow = False
        cone.scale = (0, 0, 0)
        cone.keyframe_insert("scale", frame=1)
        cone.keyframe_insert("location", frame=1)
        streaks.append(cone)

    streak_trajectories = [
        (Vector((-20, 20, 18)), Vector((15, -15, 6)), (math.radians(30), math.radians(45), math.radians(-45))),
        (Vector((22, 18, 20)), Vector((-14, -16, 8)), (math.radians(-30), math.radians(45), math.radians(135))),
        (Vector((-18, -20, 16)), Vector((16, 14, 4)), (math.radians(40), math.radians(-30), math.radians(-120))),
        (Vector((20, -22, 18)), Vector((-15, 15, 5)), (math.radians(-35), math.radians(-40), math.radians(60))),
    ]

    dur = 10
    for idx, f_hit in enumerate(big_frames):
        if f_hit >= frame_count:
            continue
        cone = streaks[idx % n_pool]
        start_v, end_v, rot = streak_trajectories[idx % len(streak_trajectories)]
        f_start = f_hit
        f_end = min(f_hit + dur, frame_count)

        if f_start > 1:
            cone.scale = (0, 0, 0)
            cone.keyframe_insert("scale", frame=f_start - 1)

        cone.location = start_v
        cone.rotation_euler = rot
        cone.scale = (1, 1, 1)
        cone.keyframe_insert("location", frame=f_start)
        cone.keyframe_insert("rotation_euler", frame=f_start)
        cone.keyframe_insert("scale", frame=f_start)

        cone.location = end_v
        cone.scale = (1, 1, 1)
        cone.keyframe_insert("location", frame=f_end)
        cone.keyframe_insert("scale", frame=f_end)

        if f_end < frame_count:
            cone.scale = (0, 0, 0)
            cone.keyframe_insert("scale", frame=f_end + 1)

    return streaks


# ----------------------------------------------------------------------- particles (GN)

def _set(sock, val):
    """Link a socket or set its default value."""
    if isinstance(val, bpy.types.NodeSocket):
        sock.id_data.links.new(val, sock)
    else:
        sock.default_value = val


def build_particle_tree(mat):
    ng = bpy.data.node_groups.new("OrbParticles", "GeometryNodeTree")
    itf = ng.interface
    itf.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    ids = {}
    for name, default in (("Rate", 8.0), ("Burst", 0.0), ("Speed", 5.0),
                          ("R", 1.0), ("G", 0.2), ("B", 0.5),
                          ("Mode", 0.0), ("Special", 0.0)):
        s = itf.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
        s.default_value = default
        ids[name] = s.identifier
    N = ng.nodes

    def math(op, a, b=None, c=None):
        n = N.new("ShaderNodeMath")
        n.operation = op
        for sock, v in zip(n.inputs, (a, b, c)):
            if v is not None:
                _set(sock, v)
        return n.outputs[0]

    def vmath(op, a, b=None, scale=None):
        n = N.new("ShaderNodeVectorMath")
        n.operation = op
        _set(n.inputs[0], a)
        if b is not None:
            _set(n.inputs[1], b)
        if scale is not None:
            _set(n.inputs["Scale"], scale)
        return n.outputs[0]

    def attr(name, dtype):
        n = N.new("GeometryNodeInputNamedAttribute")
        n.data_type = dtype
        n.inputs["Name"].default_value = name
        return n.outputs["Attribute"]

    def store(geo, name, dtype, value):
        n = N.new("GeometryNodeStoreNamedAttribute")
        n.data_type = dtype
        n.domain = "POINT"
        _set(n.inputs["Geometry"], geo)
        n.inputs["Name"].default_value = name
        _set(n.inputs["Value"], value)
        return n.outputs["Geometry"]

    def compare(op, a, b):
        n = N.new("FunctionNodeCompare")
        n.data_type = "FLOAT"
        n.operation = op
        _set(n.inputs["A"], a)
        _set(n.inputs["B"], b)
        return n.outputs["Result"]

    def combine(x, y, z):
        n = N.new("ShaderNodeCombineXYZ")
        for s, v in zip(n.inputs, (x, y, z)):
            _set(s, v)
        return n.outputs[0]

    def separate(vec):
        n = N.new("ShaderNodeSeparateXYZ")
        _set(n.inputs[0], vec)
        return n.outputs["X"], n.outputs["Y"], n.outputs["Z"]

    def rand(dtype, lo, hi, seed):
        n = N.new("FunctionNodeRandomValue")
        n.data_type = dtype
        _set(n.inputs["Min"], lo)
        _set(n.inputs["Max"], hi)
        _set(n.inputs["Seed"], seed)
        return n.outputs["Value"]

    def mix_vec(fac, a, b):
        n = N.new("ShaderNodeMix")
        n.data_type = "VECTOR"
        _set(sk(n.inputs, "Factor_Float"), fac)
        _set(sk(n.inputs, "A_Vector"), a)
        _set(sk(n.inputs, "B_Vector"), b)
        return sk(n.outputs, "Result_Vector")

    gi = N.new("NodeGroupInput")
    go = N.new("NodeGroupOutput")
    inp = {k: gi.outputs[v] for k, v in ids.items()}
    si = N.new("GeometryNodeSimulationInput")
    so = N.new("GeometryNodeSimulationOutput")
    si.pair_with_output(so)
    dt = si.outputs["Delta Time"]
    frame = N.new("GeometryNodeInputSceneTime").outputs["Frame"]

    # --- advance existing particles
    pos = N.new("GeometryNodeInputPosition").outputs[0]
    px, py, pz = separate(pos)
    r_xy = combine(px, py, 0.0)
    d_xy = math("MAXIMUM", vmath("LENGTH", r_xy), 0.05)
    inv_d = math("DIVIDE", 1.0, d_xy)
    r_hat = vmath("SCALE", r_xy, scale=inv_d)
    t_hat = combine(math("MULTIPLY", math("MULTIPLY", -1.0, py), inv_d),
                    math("MULTIPLY", px, inv_d), 0.0)

    m = attr("mode", "FLOAT")
    is_v = compare("GREATER_THAN", m, 0.5)
    is_h = compare("GREATER_THAN", m, 1.5)

    # Mode 0 (Blast): drag + gravity
    vel_cur = attr("vel", "FLOAT_VECTOR")
    v_drag0 = vmath("SCALE", vel_cur, scale=P_DRAG ** (BASE_FPS / FPS))
    vel_0 = vmath("SUBTRACT", v_drag0, combine(0.0, 0.0, math("MULTIPLY", dt, P_GRAVITY)))

    # Mode 1 (Vortex): swirl + lift
    v_drag1 = vmath("SCALE", vel_cur, scale=0.985 ** (BASE_FPS / FPS))
    a1 = combine(math("MULTIPLY", 6.0, separate(t_hat)[0]),
                 math("MULTIPLY", 6.0, separate(t_hat)[1]), 1.2)
    vel_1 = vmath("ADD", v_drag1, vmath("SCALE", a1, scale=dt))

    # Mode 2 (Halo): centripetal force
    vx, vy, vz = separate(vel_cur)
    v_xy2 = math("ADD", math("MULTIPLY", vx, vx), math("MULTIPLY", vy, vy))
    cent_a = math("DIVIDE", v_xy2, d_xy)
    a2_xy = vmath("SCALE", r_hat, scale=math("MULTIPLY", -1.0, cent_a))
    z_err = math("SUBTRACT", pz, ORB_Z)
    a2_z = math("MULTIPLY", -2.0, z_err)
    a2 = vmath("ADD", a2_xy, combine(0.0, 0.0, a2_z))
    vel_2 = vmath("ADD", vel_cur, vmath("SCALE", a2, scale=dt))

    # Mix velocity
    vel_next = mix_vec(is_h, mix_vec(is_v, vel_0, vel_1), vel_2)

    # Advance age and vel
    g = store(si.outputs["Geometry"], "age", "FLOAT", math("ADD", attr("age", "FLOAT"), dt))
    g = store(g, "vel", "FLOAT_VECTOR", vel_next)
    sp = N.new("GeometryNodeSetPosition")
    _set(sp.inputs["Geometry"], g)
    _set(sp.inputs["Offset"], vmath("SCALE", vel_next, scale=dt))

    max_life = math("ADD", 2.2, math("ADD", math("MULTIPLY", 1.0, is_v), math("MULTIPLY", 0.8, is_h)))
    dead = N.new("FunctionNodeBooleanMath")
    dead.operation = "OR"
    _set(dead.inputs[0], compare("GREATER_THAN", attr("age", "FLOAT"), max_life))
    _set(dead.inputs[1], compare("LESS_THAN", pz, 0.0))

    dl = N.new("GeometryNodeDeleteGeometry")
    dl.domain = "POINT"
    _set(dl.inputs["Geometry"], sp.outputs["Geometry"])
    _set(dl.inputs["Selection"], dead.outputs[0])

    # --- emit new particles
    pts = N.new("GeometryNodePoints")
    _set(pts.inputs["Count"], math("ADD", inp["Rate"], inp["Burst"]))

    is_emit_h = compare("GREATER_THAN", inp["Mode"], 1.5)
    is_emit_v = compare("GREATER_THAN", inp["Mode"], 0.5)

    # Sphere pos
    dirv = vmath("NORMALIZE", rand("FLOAT_VECTOR", (-1, -1, -1), (1, 1, 1), frame))
    pos_sphere = vmath("ADD", vmath("SCALE", dirv, scale=ORB_R), (0, 0, ORB_Z))

    # Halo pos
    ang = rand("FLOAT", 0.0, 6.2831853, math("ADD", frame, 13.0))
    pos_halo = combine(math("MULTIPLY", 3.0, math("COSINE", ang)),
                       math("MULTIPLY", 3.0, math("SINE", ang)),
                       math("ADD", ORB_Z, rand("FLOAT", -0.25, 0.25, math("ADD", frame, 29.0))))
    pos_emit = mix_vec(is_emit_h, pos_sphere, pos_halo)
    _set(pts.inputs["Position"], pos_emit)
    _set(pts.inputs["Radius"], 0.05)

    # Speed
    index = N.new("GeometryNodeInputIndex").outputs[0]
    burst_mult = math("MULTIPLY_ADD", compare("GREATER_EQUAL", index, inp["Rate"]), 0.6, 1.0)
    speed = math("MULTIPLY", math("MULTIPLY", inp["Speed"],
                                  rand("FLOAT", 0.6, 1.0, math("ADD", frame, 7.0))), burst_mult)

    # Velocity
    vel_e0 = vmath("SCALE", dirv, scale=speed)
    dx, dy, dz = separate(dirv)
    d_dir = math("MAXIMUM", math("SQRT", math("ADD", math("MULTIPLY", dx, dx), math("MULTIPLY", dy, dy))), 0.01)
    t_dir_x = math("DIVIDE", math("MULTIPLY", -1.0, dy), d_dir)
    t_dir_y = math("DIVIDE", dx, d_dir)
    vel_e1 = combine(math("MULTIPLY", math("ADD", math("MULTIPLY", dx, 0.4), math("MULTIPLY", t_dir_x, 0.8)), speed),
                     math("MULTIPLY", math("ADD", math("MULTIPLY", dy, 0.4), math("MULTIPLY", t_dir_y, 0.8)), speed),
                     math("ADD", math("MULTIPLY", dz, speed), 1.2))
    vel_e2 = combine(math("MULTIPLY", -3.0, math("SINE", ang)),
                     math("MULTIPLY", 3.0, math("COSINE", ang)), 0.0)
    vel_emit = mix_vec(is_emit_h, mix_vec(is_emit_v, vel_e0, vel_e1), vel_e2)

    # Shape
    rnd_shape = rand("FLOAT", 0.0, 1.0, math("ADD", frame, 42.0))
    is_dia = compare("GREATER_THAN", rnd_shape, 0.5)
    shape_spec = math("ADD", 1.0, is_dia)
    shape = math("MULTIPLY", inp["Special"], shape_spec)

    newg = store(pts.outputs["Points"], "vel", "FLOAT_VECTOR", vel_emit)
    newg = store(newg, "pcol", "FLOAT_VECTOR", combine(inp["R"], inp["G"], inp["B"]))
    newg = store(newg, "mode", "FLOAT", inp["Mode"])
    newg = store(newg, "shape", "INT", shape)
    newg = store(newg, "age", "FLOAT", 0.0)

    join = N.new("GeometryNodeJoinGeometry")
    ng.links.new(newg, join.inputs[0])
    ng.links.new(dl.outputs["Geometry"], join.inputs[0])
    _set(so.inputs["Geometry"], join.outputs[0])

    # --- render: instances with trails
    ico = N.new("GeometryNodeMeshIcoSphere")
    ico.inputs["Radius"].default_value = 0.06
    ico.inputs["Subdivisions"].default_value = 1

    cube = N.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (0.1, 0.1, 0.1)

    dia = N.new("GeometryNodeMeshUVSphere")
    dia.inputs["Segments"].default_value = 4
    dia.inputs["Rings"].default_value = 2
    dia.inputs["Radius"].default_value = 0.06

    # Reverse link order to yield indices 0: ico, 1: cube, 2: dia
    g2i = N.new("GeometryNodeGeometryToInstance")
    ng.links.new(dia.outputs[0], g2i.inputs[0])
    ng.links.new(cube.outputs[0], g2i.inputs[0])
    ng.links.new(ico.outputs[0], g2i.inputs[0])

    iop = N.new("GeometryNodeInstanceOnPoints")
    _set(iop.inputs["Points"], so.outputs["Geometry"])
    _set(iop.inputs["Instance"], g2i.outputs["Instances"])
    iop.inputs["Pick Instance"].default_value = True
    _set(iop.inputs["Instance Index"], attr("shape", "INT"))

    # Align rotation to vel
    al = N.new("FunctionNodeAlignEulerToVector")
    al.axis = "Z"
    _set(al.inputs["Vector"], attr("vel", "FLOAT_VECTOR"))
    _set(iop.inputs["Rotation"], al.outputs["Rotation"])

    # Scale with streak trail
    shrink = math("MAXIMUM", math("SUBTRACT", 1.0, math("DIVIDE", attr("age", "FLOAT"), max_life)), 0.0)
    v_len = vmath("LENGTH", attr("vel", "FLOAT_VECTOR"))
    trail_z = math("MULTIPLY_ADD", v_len, 0.4, 1.0)
    scale = combine(shrink, shrink, math("MULTIPLY", shrink, trail_z))
    _set(iop.inputs["Scale"], scale)

    rz = N.new("GeometryNodeRealizeInstances")
    _set(rz.inputs["Geometry"], iop.outputs["Instances"])
    sm = N.new("GeometryNodeSetMaterial")
    _set(sm.inputs["Geometry"], rz.outputs["Geometry"])
    sm.inputs["Material"].default_value = mat
    _set(go.inputs["Geometry"], sm.outputs["Geometry"])
    return ng, ids


def build_particles(scene):
    m, nodes, links = new_mat("Particle_Glow")
    a = nodes.new("ShaderNodeAttribute")
    a.attribute_type = "GEOMETRY"
    a.attribute_name = "pcol"
    emission_output(nodes, links, a.outputs["Vector"], 2.4)

    ng, ids = build_particle_tree(m)
    mesh = bpy.data.meshes.new("ParticlesMesh")
    obj = bpy.data.objects.new("Particles", mesh)
    scene.collection.objects.link(obj)
    obj.visible_shadow = False
    mod = obj.modifiers.new("OrbParticles", "NODES")
    mod.node_group = ng
    p = lambda k: f'modifiers["OrbParticles"].properties.inputs.{ids[k]}.value'
    sv = lambda k: (scene, f'["{k}"]')

    df = "(1.0 - 0.4 * (m > 0.5) - 0.35 * (m > 1.5))"
    drive(obj, p("Rate"), f"{BASE_FPS / FPS} * (8.0 + 26.0 * l) * {df}", l=sv("loud"), m=sv("mode"))
    drive(obj, p("Burst"), f"200.0 * b * {df}", b=sv("beat"), m=sv("mode"))
    drive(obj, p("Speed"), "5.0 + 4.0 * l", l=sv("loud"))
    drive(obj, p("Mode"), "m", m=sv("mode"))
    drive(obj, p("Special"), "b", b=sv("big"))
    for i, k in enumerate("RGB"):
        drive(obj, p(k), "c * (0.6 + 0.8 * l)", c=(scene, f'["pcol"][{i}]'), l=sv("loud"))
    return obj


def bake_particles(obj, ov, blend_path):
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


# ----------------------------------------------------------------------- shader upgrades

def _insert_before_emission(mat, make_color):
    """Re-route the cel color through make_color(nodes, links, cel_socket) -> socket."""
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    em = next(n for n in nodes if n.type == "EMISSION")
    src = em.inputs["Color"].links[0].from_socket
    links.new(make_color(nodes, links, src), em.inputs["Color"])


def _glow_color(nodes, links, gain_attr, base, gain):
    """spec * (base + gain * attr) as a vector socket."""
    spec = view_layer_attr(nodes, "spec")
    k = nodes.new("ShaderNodeMath")
    k.operation = "MULTIPLY_ADD"
    links.new(view_layer_attr(nodes, gain_attr).outputs["Fac"], k.inputs[0])
    k.inputs[1].default_value = gain
    k.inputs[2].default_value = base
    sc = nodes.new("ShaderNodeVectorMath")
    sc.operation = "SCALE"
    links.new(spec.outputs["Vector"], sc.inputs[0])
    links.new(k.outputs[0], sc.inputs["Scale"])
    return sc.outputs[0]


def upgrade_bezel_rim(mat):
    def make(nodes, links, cel):
        lw = nodes.new("ShaderNodeLayerWeight")
        lw.inputs["Blend"].default_value = 0.5
        ramp = nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.interpolation = "CONSTANT"
        ramp.color_ramp.elements[1].position = 0.75
        links.new(lw.outputs["Facing"], ramp.inputs["Fac"])
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "VECTOR"
        links.new(ramp.outputs["Color"], sk(mix.inputs, "Factor_Float"))
        links.new(cel, sk(mix.inputs, "A_Vector"))
        links.new(_glow_color(nodes, links, "loud", 1.0, 4.0), sk(mix.inputs, "B_Vector"))
        return sk(mix.outputs, "Result_Vector")
    _insert_before_emission(mat, make)


def upgrade_floor_grid(mat):
    def make(nodes, links, cel):
        tc = nodes.new("ShaderNodeTexCoord")
        fr = nodes.new("ShaderNodeVectorMath")
        fr.operation = "FRACTION"
        links.new(tc.outputs["Object"], fr.inputs[0])
        off = nodes.new("ShaderNodeVectorMath")
        off.operation = "SUBTRACT"
        off.inputs[1].default_value = (0.5, 0.5, 0.5)
        links.new(fr.outputs[0], off.inputs[0])
        ab = nodes.new("ShaderNodeVectorMath")
        ab.operation = "ABSOLUTE"
        links.new(off.outputs[0], ab.inputs[0])
        sep = nodes.new("ShaderNodeSeparateXYZ")
        links.new(ab.outputs[0], sep.inputs[0])
        mx = nodes.new("ShaderNodeMath")
        mx.operation = "MAXIMUM"
        links.new(sep.outputs["X"], mx.inputs[0])
        links.new(sep.outputs["Y"], mx.inputs[1])
        line = nodes.new("ShaderNodeMath")
        line.operation = "GREATER_THAN"
        line.inputs[1].default_value = 0.48
        links.new(mx.outputs[0], line.inputs[0])
        # fade lines out with view distance to avoid far-field aliasing / moire
        cam = nodes.new("ShaderNodeCameraData")
        fade = nodes.new("ShaderNodeMapRange")
        fade.inputs["From Min"].default_value = 6.0
        fade.inputs["From Max"].default_value = 22.0
        fade.inputs["To Min"].default_value = 1.0
        fade.inputs["To Max"].default_value = 0.0
        links.new(cam.outputs["View Distance"], fade.inputs["Value"])
        fac = nodes.new("ShaderNodeMath")
        fac.operation = "MULTIPLY"
        links.new(line.outputs[0], fac.inputs[0])
        links.new(fade.outputs["Result"], fac.inputs[1])
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "VECTOR"
        links.new(fac.outputs[0], sk(mix.inputs, "Factor_Float"))
        links.new(cel, sk(mix.inputs, "A_Vector"))
        links.new(_glow_color(nodes, links, "bass", 0.3, 4.0), sk(mix.inputs, "B_Vector"))
        return sk(mix.outputs, "Result_Vector")
    _insert_before_emission(mat, make)


# ----------------------------------------------------------------------- compositor

def build_compositor(scene):
    ng = bpy.data.node_groups.new("PostFX", "CompositorNodeTree")
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    N, L = ng.nodes, ng.links

    def in_path(node, name):
        idx = next(i for i, s in enumerate(node.inputs) if s.name == name)
        return f'nodes["{node.name}"].inputs[{idx}].default_value'

    rl = N.new("CompositorNodeRLayers")
    glare = N.new("CompositorNodeGlare")
    glare.name = "Bloom"
    glare.inputs["Type"].default_value = "Bloom"
    glare.inputs["Quality"].default_value = "High"
    glare.inputs["Highlights Threshold"].default_value = 1.0
    glare.inputs["Size"].default_value = 0.6
    L.new(rl.outputs["Image"], glare.inputs["Image"])
    drive(ng, in_path(glare, "Strength"), "0.4 + 1.0 * b", b=(scene, '["bass"]'))

    lens = N.new("CompositorNodeLensdist")
    lens.name = "Aberration"
    lens.inputs["Distortion"].default_value = 0.0
    L.new(glare.outputs["Image"], lens.inputs["Image"])
    drive(ng, in_path(lens, "Dispersion"), "0.01 + 0.08 * min(e, 1.0)", e=(scene, '["beat_env"]'))

    pix = N.new("CompositorNodePixelate")
    pix.inputs["Size"].default_value = 14
    L.new(lens.outputs["Image"], pix.inputs["Color"])
    glitch = N.new("ShaderNodeMix")
    glitch.name = "Glitch"
    glitch.data_type = "RGBA"
    L.new(lens.outputs["Image"], sk(glitch.inputs, "A_Color"))
    L.new(pix.outputs[0], sk(glitch.inputs, "B_Color"))
    drive(ng, in_path(glitch, "Factor"), "g", g=(scene, '["big"]'))

    ell = N.new("CompositorNodeEllipseMask")
    ell.inputs["Size"].default_value = (0.95, 0.85)
    ell.inputs["Value"].default_value = 1.0
    blur = N.new("CompositorNodeBlur")
    blur.inputs["Size"].default_value = (150, 150)
    L.new(ell.outputs["Mask"], blur.inputs["Image"])
    vig = N.new("ShaderNodeMix")
    vig.data_type = "RGBA"
    vig.blend_type = "MULTIPLY"
    sk(vig.inputs, "Factor_Float").default_value = 0.55
    L.new(sk(glitch.outputs, "Result_Color"), sk(vig.inputs, "A_Color"))
    L.new(blur.outputs["Image"], sk(vig.inputs, "B_Color"))

    # Film grain (white noise in 4D animated by frame)
    coord = N.new("CompositorNodeImageCoordinates")
    wn = N.new("ShaderNodeTexWhiteNoise")
    wn.noise_dimensions = "4D"
    st = N.new("CompositorNodeSceneTime")
    L.new(st.outputs["Frame"], wn.inputs["W"])
    L.new(coord.outputs["Pixel"], wn.inputs["Vector"])

    grain_mix = N.new("ShaderNodeMix")
    grain_mix.data_type = "RGBA"
    grain_mix.blend_type = "ADD"
    sk(grain_mix.inputs, "Factor_Float").default_value = 0.04
    L.new(sk(vig.outputs, "Result_Color"), sk(grain_mix.inputs, "A_Color"))
    L.new(wn.outputs["Color"], sk(grain_mix.inputs, "B_Color"))

    # Scanlines (4px period sine wave)
    sep_c = N.new("ShaderNodeSeparateXYZ")
    L.new(coord.outputs["Pixel"], sep_c.inputs[0])
    m_freq = N.new("ShaderNodeMath")
    m_freq.operation = "MULTIPLY"
    m_freq.inputs[1].default_value = math.pi / 2.0
    L.new(sep_c.outputs["Y"], m_freq.inputs[0])
    sin_node = N.new("ShaderNodeMath")
    sin_node.operation = "SINE"
    L.new(m_freq.outputs[0], sin_node.inputs[0])
    remap = N.new("ShaderNodeMath")
    remap.operation = "MULTIPLY_ADD"
    remap.inputs[1].default_value = 0.03
    remap.inputs[2].default_value = 0.97
    L.new(sin_node.outputs[0], remap.inputs[0])

    scan_mix = N.new("ShaderNodeMix")
    scan_mix.data_type = "RGBA"
    scan_mix.blend_type = "MULTIPLY"
    sk(scan_mix.inputs, "Factor_Float").default_value = 1.0
    L.new(sk(grain_mix.outputs, "Result_Color"), sk(scan_mix.inputs, "A_Color"))

    comb_s = N.new("CompositorNodeCombineColor")
    L.new(remap.outputs[0], comb_s.inputs["Red"])
    L.new(remap.outputs[0], comb_s.inputs["Green"])
    L.new(remap.outputs[0], comb_s.inputs["Blue"])
    L.new(comb_s.outputs["Image"], sk(scan_mix.inputs, "B_Color"))

    go = N.new("NodeGroupOutput")
    L.new(sk(scan_mix.outputs, "Result_Color"), go.inputs["Image"])
    scene.compositing_node_group = ng
    scene.render.use_compositing = True
    return ng


# ----------------------------------------------------------------------- output

def setup_render(scene, start, frame_count, tag, wav_path=None):
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.fps = FPS
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.resolution_percentage = 100
    scene.frame_start, scene.frame_end = 1, frame_count
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
    ff.audio_codec = "AAC"
    ff.audio_bitrate = 192
    ff.audio_channels = "STEREO"
    os.makedirs(os.path.join(HERE, "renders"), exist_ok=True)
    clip = "".join(c if c.isalnum() else "_" for c in os.path.splitext(os.path.basename(VIDEO))[0]).strip("_").lower()
    scene.render.filepath = os.path.join(HERE, "renders", f"{clip}_{tag}_")

    se = scene.sequence_editor_create()
    sound_src = wav_path if wav_path and os.path.exists(wav_path) else VIDEO
    snd = se.strips.new_sound("Audio", sound_src, channel=1, frame_start=1)
    if sound_src == wav_path:
        snd.frame_offset_start = 0
        snd.frame_start = 1
    else:
        off = int(round(start * FPS))
        snd.frame_offset_start = off
        snd.frame_start = 1 - off
    snd.frame_final_end = frame_count + 1


# ----------------------------------------------------------------------- main

def main():
    args = parse_args()
    frame_count = int(round(args.duration * FPS))
    frame_offset = int(round(args.start * FPS))
    tag = "full" if args.full else "test"
    print(f"[ninja-viz] start={args.start}s frames={frame_count} section={args.section}s tag={tag}")

    scene = reset_scene()
    scene.render.fps = FPS
    scene.frame_start, scene.frame_end = 1, frame_count

    video_mat = make_video_material(frame_count, frame_offset)
    bezel_mat = make_cel_material("Bezel_Cel", (0.05, 0.6, 0.8), (0.01, 0.08, 0.15), (0.6, 0.95, 1.0))
    outline_mat = make_outline_material()

    build_world(scene)
    build_light(scene)
    screens = build_screens(video_mat, bezel_mat, outline_mat, scene)
    rig = build_camera(scene)

    wav = extract_wav(args.start, args.duration)
    scene.frame_set(1)  # the bake starts writing samples at the current frame
    ov = graph_override()
    band_vals, band_norms = [], []
    for scr, (lo, hi) in zip(screens, BANDS):
        vals = bake_property(scr, '["audio"]', wav, lo, hi, ov)
        p = percentile(vals, 0.98) or 1.0
        scr["norm"] = 1.0 / p
        band_vals.append(vals)
        band_norms.append(1.0 / p)
        add_scale_driver(scr)
        print(f"[ninja-viz] {scr.name} band {lo}-{hi} Hz: {len(vals)} samples, p98={p:.4g}")
    loud = bake_property(rig, '["loud"]', wav, 20, 20000, ov)
    rig["norm"] = 1.0 / (percentile(loud, 0.98) or 1.0)
    key_orbit(rig, loud, frame_count)
    rot = next(c for c in fcurves_of(rig) if c.data_path == "rotation_euler" and c.array_index == 2)
    print(f"[ninja-viz] orbit total = {math.degrees(rot.evaluate(frame_count)):.0f} deg")

    vid_colors = video_colors(args.start, args.duration, frame_count)
    onsets, big_onsets = build_audio_bus(scene, band_vals, band_norms, loud, rig["norm"], frame_count, args.section, vid_colors)
    build_orb(scene, outline_mat)
    build_orb_light(scene)
    build_streaks(scene, big_onsets, frame_count)
    particles = build_particles(scene)
    upgrade_bezel_rim(bezel_mat)
    build_compositor(scene)

    setup_render(scene, args.start, frame_count, tag, wav)
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=args.out)  # bake dir is relative to the .blend
    bake_particles(particles, ov, args.out)
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=args.out)
    print(f"[ninja-viz] saved {args.out}")


def run():
    code = 0
    try:
        main()
    except Exception:
        traceback.print_exc()
        code = 1
    args = parse_args()
    if not args.no_quit:
        print(f"[ninja-viz] {'FAILED' if code else 'DONE'}")
        sys.stdout.flush()
        os._exit(code)
    return None


# Defer until the window/UI exists.
bpy.app.timers.register(run, first_interval=1.0)
