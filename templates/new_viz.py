"""Template: new-viz ('Variation / Stage Ring')

Features:
- 8 outward-facing screens in a ring with stands and bezels
- Checker floor with radial neon grid lines
- Halftone dots in cel shadows + bass-pulsing outlines
- Dynamic audio-reactive video shader (RGB split, hue rotation, dynamic posterization on big_env)
- 4 section palettes (Sunset, Ice, Acid, Neon) snapped to big beats + video average color blending
- Particles: 3 modes (0: Blast, 1: Vortex, 2: Halo) with multi-shape instances (ico, cube, diamond) & streak trails
- Camera breathing dolly (8s) + bass height bob
- Compositor: bloom glare + lens aberration + glitch + vignette + 4D film grain + CRT scanlines
"""

import colorsys
import math
import os

try:
    import bpy
    from mathutils import Vector
except ImportError:
    bpy = None
    Vector = None

from common import (
    new_mat, emission_output, sk,
    fcurves_of, find_fcurve, percentile, drive, view_layer_attr,
    write_keys, bake_property, video_colors
)

N_SCREENS = 8
RING_RADIUS = 4.5
SCREEN_W, SCREEN_H = 1.6, 0.9
SCREEN_Z = 1.2
PULSE_GAIN = 0.6
POSTERIZE_LEVELS = 6
BANDS = [(20, 60), (60, 150), (150, 400), (400, 1000),
         (1000, 2500), (2500, 5000), (5000, 10000), (10000, 20000)]
CAM_RADIUS, CAM_HEIGHT = 6.5, 1.0
CAM_TARGET = (2.0, 1.6, 1.25)  # rig-space: ~20° ahead along the ring, slight up-tilt
LAP_SLOW_S, LAP_FAST_S = 55.0, 11.0

ORB_Z = SCREEN_Z
ORB_R = 0.35
P_LIFE = 2.2          # seconds
P_DRAG = 0.97         # per frame at 30 fps
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


# ----------------------------------------------------------------------- materials

def make_video_material(video_path, frame_count, frame_offset):
    m, nodes, links = new_mat("Video_Toon")
    img = bpy.data.images.load(video_path)
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

    # Halftone dots in shadow band
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

def build_world(scene):
    world = bpy.data.worlds.new("ToonSky")
    scene.world = world
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()
    coord = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(coord.outputs["Window"], sep.inputs[0])
    ramp = nodes.new("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    cr.interpolation = "CONSTANT"
    stops = [(0.0, (0.004, 0.004, 0.02)), (0.55, (0.012, 0.008, 0.05)),
             (0.7, (0.035, 0.01, 0.08)), (0.82, (0.06, 0.012, 0.11)), (0.92, (0.1, 0.02, 0.14))]
    cr.elements[0].position, cr.elements[0].color = stops[0][0], (*stops[0][1], 1)
    cr.elements[1].position, cr.elements[1].color = stops[1][0], (*stops[1][1], 1)
    for pos, col in stops[2:]:
        cr.elements.new(pos).color = (*col, 1)
    links.new(sep.outputs["Y"], ramp.inputs["Fac"])

    hs = nodes.new("ShaderNodeHueSaturation")
    links.new(ramp.outputs["Color"], hs.inputs["Color"])
    hue_attr = nodes.new("ShaderNodeAttribute")
    hue_attr.attribute_type = "VIEW_LAYER"
    hue_attr.attribute_name = "hue"
    links.new(hue_attr.outputs["Fac"], hs.inputs["Hue"])

    bg = nodes.new("ShaderNodeBackground")
    out = nodes.new("ShaderNodeOutputWorld")
    links.new(hs.outputs["Color"], bg.inputs["Color"])
    links.new(bg.outputs[0], out.inputs["Surface"])


def build_screens(video_mat, bezel_mat, outline_mat, scene):
    screens = []
    for i in range(N_SCREENS):
        theta = i * 2 * math.pi / N_SCREENS
        pivot = bpy.data.objects.new(f"Screen_{i}", None)
        bpy.context.collection.objects.link(pivot)
        pivot.empty_display_type = "ARROWS"
        pivot.location = (RING_RADIUS * math.cos(theta), RING_RADIUS * math.sin(theta), SCREEN_Z)
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

        stand_h = SCREEN_Z - (SCREEN_H + 0.12) / 2 + 0.05
        inward = Vector((-math.cos(theta), -math.sin(theta), 0)) * 0.08
        bpy.ops.mesh.primitive_cylinder_add(radius=0.04, depth=stand_h)
        stand = bpy.context.active_object
        stand.name = f"Screen_{i}_Stand"
        stand.location = Vector((pivot.location.x, pivot.location.y, stand_h / 2)) + inward
        stand.data.materials.append(bezel_mat)
        add_outline(stand, outline_mat, scene, 0.02)

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


def build_camera(scene, fps):
    rig = bpy.data.objects.new("CamRig", None)
    scene.collection.objects.link(rig)
    target = bpy.data.objects.new("CamTarget", None)
    scene.collection.objects.link(target)
    target.parent = rig
    target.location = CAM_TARGET
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 35
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.parent = rig
    cam.location = (CAM_RADIUS, 0, CAM_HEIGHT)
    tr = cam.constraints.new("TRACK_TO")
    tr.target = target
    tr.track_axis, tr.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    scene.camera = cam
    rig["loud"] = 0.0
    rig["norm"] = 1.0
    rig.keyframe_insert('["loud"]', frame=1)

    # Breathing dolly (6.5 <-> 5.7m, 8s period)
    drive(cam, "location", f"6.1 + 0.4 * cos(frame / {fps} * 2 * pi / 8.0)", 0)
    # Height bob on bass_s
    drive(cam, "location", "1.0 + 0.2 * bs", 2, bs=(scene, '["bass_s"]'))

    return rig


def build_light(scene):
    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 3.0
    sun.angle = 0.0
    obj = bpy.data.objects.new("Sun", sun)
    scene.collection.objects.link(obj)
    obj.rotation_euler = (math.radians(50), 0, math.radians(30))


def add_scale_driver(screen):
    for axis in range(3):
        drive(screen, "scale", f"1.0 + {PULSE_GAIN} * min(a * n * a * n, 1.5)", axis,
              a=(screen, '["audio"]'), n=(screen, '["norm"]'))


def key_orbit(rig, loud_vals, frame_count, fps, ctx):
    norm = rig["norm"]
    w_slow = 2 * math.pi / (LAP_SLOW_S * fps)
    w_fast = 2 * math.pi / (LAP_FAST_S * fps)
    smooth, s = [], 0.0
    k_ema = ctx.per_frame(0.08)
    for v in loud_vals[:frame_count]:
        s += (min(v * norm, 1.0) - s) * k_ema
        smooth.append(s)
    angle, co = 0.0, []
    for f, l in enumerate(smooth):
        co += [f + 1, angle]
        angle += w_slow + (w_fast - w_slow) * l
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

def interp_stops(t, stops):
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
    h1, s1, v1 = colorsys.rgb_to_hsv(*c1)
    h2, s2, v2 = colorsys.rgb_to_hsv(*c2)
    dh = ((h2 - h1 + 0.5) % 1.0) - 0.5
    h = (h1 + w * dh) % 1.0
    s = max(0.0, min(1.0, (1.0 - w) * s1 + w * s2))
    v = max(0.0, min(1.0, (1.0 - w) * v1 + w * v2))
    return colorsys.hsv_to_rgb(h, s, v)


def build_schedule(n, big, section_s, fps):
    sec_frames = int(round(section_s * fps))
    fade_len = max(1, int(round(0.5 * fps)))
    window = int(round(2.0 * fps))

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


def build_audio_bus(scene, band_vals, band_norms, loud_vals, loud_norm, n, section_s, vid_colors, fps, ctx):
    nb = [[min(v * nm, 1.5) for v in vals[:n]] for vals, nm in zip(band_vals, band_norms)]
    loud = [min(v * loud_norm, 1.5) for v in loud_vals[:n]]
    bass = [(nb[0][f] + nb[1][f]) / 2 for f in range(n)]

    beat, last = [0.0] * n, -99
    win_size = max(2, round(0.33 * fps))
    min_dist = max(2, round(0.2 * fps))
    for f in range(n):
        w = bass[max(0, f - win_size):f]
        m = sum(w) / len(w) if w else 0.0
        if bass[f] > 0.35 and bass[f] > 1.2 * m and f - last >= min_dist:
            beat[f], last = bass[f], f
    onsets = [f for f in range(n) if beat[f] > 0]
    env, e = [], 0.0
    decay = 0.75 ** (ctx.base_fps / fps)
    for f in range(n):
        e = max(beat[f], e * decay)
        env.append(e)
    big = [0.0] * n
    if onsets:
        thr = percentile([beat[f] for f in onsets], 0.85)
        for f in onsets:
            if beat[f] >= thr:
                for k in range(max(2, round(0.1 * fps))):
                    if f + k < n:
                        big[f + k] = 1.0

    bass_s, bs = [], 0.0
    k_bs = ctx.per_frame(0.15)
    for v in bass:
        bs += (v - bs) * k_bs
        bass_s.append(bs)

    big_env, be = [], 0.0
    decay_be = 0.85 ** (ctx.base_fps / fps)
    for b in big:
        be = max(b, be * decay_be)
        big_env.append(be)

    cent, s = [], None
    for f in range(n):
        tot = sum(nb[i][f] for i in range(8))
        c = sum(i * nb[i][f] for i in range(8)) / (7 * max(tot, 1e-6))
        s = c if s is None else s + (c - s) * ctx.per_frame(0.15)
        cent.append(s)
    lo, hi = percentile(cent, 0.05), percentile(cent, 0.95)
    span = max(hi - lo, 1e-6)

    bounds, mode_list, pal_a_list, pal_b_list, weight_list = build_schedule(n, big, section_s, fps)

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

    print(f"[ninja-viz] audio bus: {len(onsets)} onsets ({len(onsets) / (n / fps):.2f}/s), "
          f"big frames={int(sum(big))}, centroid p5..p95={lo:.3f}..{hi:.3f}")
    print(f"[ninja-viz] sections: {len(bounds) - 1} sections, boundaries: {bounds}")
    return onsets


# ----------------------------------------------------------------------- orb + light

def build_orb(scene, outline_mat):
    m, nodes, links = new_mat("Orb_Glow")
    spec = view_layer_attr(nodes, "spec")
    loud = view_layer_attr(nodes, "loud")
    strength = nodes.new("ShaderNodeMath")
    strength.operation = "MULTIPLY_ADD"
    links.new(loud.outputs["Fac"], strength.inputs[0])
    strength.inputs[1].default_value = 10.0
    strength.inputs[2].default_value = 3.0
    em = emission_output(nodes, links, spec.outputs["Color"])
    links.new(strength.outputs[0], em.inputs["Strength"])

    bpy.ops.mesh.primitive_ico_sphere_add(radius=ORB_R, subdivisions=3, location=(0, 0, ORB_Z))
    orb = bpy.context.active_object
    orb.name = "Orb"
    bpy.ops.object.shade_smooth()
    orb.data.materials.append(m)
    add_outline(orb, outline_mat, scene, 0.02)
    orb.visible_shadow = False
    for axis in range(3):
        drive(orb, "scale", "1.0 + 0.4 * b", axis, b=(scene, '["bass"]'))
    return orb


def build_orb_light(scene):
    light = bpy.data.lights.new("OrbLight", "POINT")
    light.shadow_soft_size = 0.3
    obj = bpy.data.objects.new("OrbLight", light)
    scene.collection.objects.link(obj)
    obj.location = (0, 0, ORB_Z)
    for i in range(3):
        drive(light, "color", "c", i, c=(scene, f'["spec"][{i}]'))
    drive(light, "energy", "150 + 1500 * l", l=(scene, '["loud"]'))
    return obj


# ----------------------------------------------------------------------- particles (GN)

def _set(sock, val):
    if isinstance(val, bpy.types.NodeSocket):
        sock.id_data.links.new(val, sock)
    else:
        sock.default_value = val


def build_particle_tree(mat, fps, ctx):
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
    v_drag0 = vmath("SCALE", vel_cur, scale=P_DRAG ** (ctx.base_fps / fps))
    vel_0 = vmath("SUBTRACT", v_drag0, combine(0.0, 0.0, math("MULTIPLY", dt, P_GRAVITY)))

    # Mode 1 (Vortex): swirl + lift
    v_drag1 = vmath("SCALE", vel_cur, scale=0.985 ** (ctx.base_fps / fps))
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

    dirv = vmath("NORMALIZE", rand("FLOAT_VECTOR", (-1, -1, -1), (1, 1, 1), frame))
    pos_sphere = vmath("ADD", vmath("SCALE", dirv, scale=ORB_R), (0, 0, ORB_Z))

    ang = rand("FLOAT", 0.0, 6.2831853, math("ADD", frame, 13.0))
    pos_halo = combine(math("MULTIPLY", 3.0, math("COSINE", ang)),
                       math("MULTIPLY", 3.0, math("SINE", ang)),
                       math("ADD", ORB_Z, rand("FLOAT", -0.25, 0.25, math("ADD", frame, 29.0))))
    pos_emit = mix_vec(is_emit_h, pos_sphere, pos_halo)
    _set(pts.inputs["Position"], pos_emit)
    _set(pts.inputs["Radius"], 0.05)

    index = N.new("GeometryNodeInputIndex").outputs[0]
    burst_mult = math("MULTIPLY_ADD", compare("GREATER_EQUAL", index, inp["Rate"]), 0.6, 1.0)
    speed = math("MULTIPLY", math("MULTIPLY", inp["Speed"],
                                  rand("FLOAT", 0.6, 1.0, math("ADD", frame, 7.0))), burst_mult)

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

    ico = N.new("GeometryNodeMeshIcoSphere")
    ico.inputs["Radius"].default_value = 0.06
    ico.inputs["Subdivisions"].default_value = 1

    cube = N.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (0.1, 0.1, 0.1)

    dia = N.new("GeometryNodeMeshUVSphere")
    dia.inputs["Segments"].default_value = 4
    dia.inputs["Rings"].default_value = 2
    dia.inputs["Radius"].default_value = 0.06

    g2i = N.new("GeometryNodeGeometryToInstance")
    ng.links.new(dia.outputs[0], g2i.inputs[0])
    ng.links.new(cube.outputs[0], g2i.inputs[0])
    ng.links.new(ico.outputs[0], g2i.inputs[0])

    iop = N.new("GeometryNodeInstanceOnPoints")
    _set(iop.inputs["Points"], so.outputs["Geometry"])
    _set(iop.inputs["Instance"], g2i.outputs["Instances"])
    iop.inputs["Pick Instance"].default_value = True
    _set(iop.inputs["Instance Index"], attr("shape", "INT"))

    al = N.new("FunctionNodeAlignEulerToVector")
    al.axis = "Z"
    _set(al.inputs["Vector"], attr("vel", "FLOAT_VECTOR"))
    _set(iop.inputs["Rotation"], al.outputs["Rotation"])

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


def build_particles(scene, fps, ctx):
    m, nodes, links = new_mat("Particle_Glow")
    a = nodes.new("ShaderNodeAttribute")
    a.attribute_type = "GEOMETRY"
    a.attribute_name = "pcol"
    emission_output(nodes, links, a.outputs["Vector"], 2.4)

    ng, ids = build_particle_tree(m, fps, ctx)
    mesh = bpy.data.meshes.new("ParticlesMesh")
    obj = bpy.data.objects.new("Particles", mesh)
    scene.collection.objects.link(obj)
    obj.visible_shadow = False
    mod = obj.modifiers.new("OrbParticles", "NODES")
    mod.node_group = ng
    p = lambda k: f'modifiers["OrbParticles"].properties.inputs.{ids[k]}.value'
    sv = lambda k: (scene, f'["{k}"]')

    df = "(1.0 - 0.4 * (m > 0.5) - 0.35 * (m > 1.5))"
    rate_scale = ctx.base_fps / fps
    drive(obj, p("Rate"), f"{rate_scale} * (8.0 + 26.0 * l) * {df}", l=sv("loud"), m=sv("mode"))
    drive(obj, p("Burst"), f"200.0 * b * {df}", b=sv("beat"), m=sv("mode"))
    drive(obj, p("Speed"), "5.0 + 4.0 * l", l=sv("loud"))
    drive(obj, p("Mode"), "m", m=sv("mode"))
    drive(obj, p("Special"), "b", b=sv("big"))
    for i, k in enumerate("RGB"):
        drive(obj, p(k), "c * (0.6 + 0.8 * l)", c=(scene, f'["pcol"][{i}]'), l=sv("loud"))
    return obj


# ----------------------------------------------------------------------- shader upgrades

def _insert_before_emission(mat, make_color):
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    em = next(n for n in nodes if n.type == "EMISSION")
    src = em.inputs["Color"].links[0].from_socket
    links.new(make_color(nodes, links, src), em.inputs["Color"])


def _glow_color(nodes, links, gain_attr, base, gain):
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


# ----------------------------------------------------------------------- build entrypoint

def build(scene, ctx, ov):
    """Build the new-viz (Variation / Stage Ring) scene."""
    video_mat = make_video_material(ctx.video_path, ctx.frame_count, ctx.frame_offset)
    bezel_mat = make_cel_material("Bezel_Cel", (0.05, 0.6, 0.8), (0.01, 0.08, 0.15), (0.6, 0.95, 1.0))
    floor_mat = make_cel_material("Floor_Cel", (0.05, 0.03, 0.15), (0.01, 0.005, 0.03),
                                  (0.15, 0.08, 0.35), checker=True)
    outline_mat = make_outline_material()

    build_world(scene)
    build_light(scene)
    build_floor(floor_mat)
    screens = build_screens(video_mat, bezel_mat, outline_mat, scene)
    rig = build_camera(scene, ctx.fps)

    scene.frame_set(1)
    band_vals, band_norms = [], []
    for scr, (lo, hi) in zip(screens, BANDS):
        vals = bake_property(scr, '["audio"]', ctx.wav_path, lo, hi, ov)
        p = percentile(vals, 0.98) or 1.0
        scr["norm"] = 1.0 / p
        band_vals.append(vals)
        band_norms.append(1.0 / p)
        add_scale_driver(scr)
        print(f"[ninja-viz] {scr.name} band {lo}-{hi} Hz: {len(vals)} samples, p98={p:.4g}")
    loud = bake_property(rig, '["loud"]', ctx.wav_path, 20, 20000, ov)
    rig["norm"] = 1.0 / (percentile(loud, 0.98) or 1.0)
    key_orbit(rig, loud, ctx.frame_count, ctx.fps, ctx)
    rot = next(c for c in fcurves_of(rig) if c.data_path == "rotation_euler" and c.array_index == 2)
    print(f"[ninja-viz] orbit total = {math.degrees(rot.evaluate(ctx.frame_count)):.0f} deg")

    vid_colors = video_colors(ctx.video_path, ctx.start, ctx.duration, ctx.fps, ctx.frame_count, ctx.base_fps)
    build_audio_bus(scene, band_vals, band_norms, loud, rig["norm"], ctx.frame_count, ctx.section, vid_colors, ctx.fps, ctx)
    build_orb(scene, outline_mat)
    build_orb_light(scene)
    particles = build_particles(scene, ctx.fps, ctx)
    upgrade_bezel_rim(bezel_mat)
    upgrade_floor_grid(floor_mat)
    build_compositor(scene)

    return particles
