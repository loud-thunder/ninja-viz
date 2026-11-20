"""Template: main ('Classic Ring')

Features:
- 8 outward-facing screens in a ring with stands and bezels
- Checker cel floor with radial neon grid lines
- Simple central glowing orb + point light
- Classic vertical gradient ToonSky world
- Off-center orbiting camera
- Single fixed HSV spectrum palette
- Static 6-level posterized video shader
- Particles: single-mode blast shrinking icospheres
- Compositor: bloom glare + lens chromatic aberration + big-beat pixel glitch + vignette
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
    write_keys, bake_property
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
PALETTE = [(0.0, (1.0, 0.05, 0.35)),   # bass   -> magenta/red
           (0.5, (1.0, 0.55, 0.05)),   # mids   -> orange/yellow
           (1.0, (0.05, 0.85, 1.0))]   # treble -> cyan


# ----------------------------------------------------------------------- materials

def make_video_material(video_path, frame_count, frame_offset):
    m, nodes, links = new_mat("Video_Toon")
    img = bpy.data.images.load(video_path, check_existing=True)
    img.source = "MOVIE"
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.extension = "CLIP"
    iu = tex.image_user
    iu.frame_duration = frame_count
    iu.frame_offset = frame_offset
    iu.frame_start = 1
    iu.use_auto_refresh = True
    iu.use_cyclic = False

    # posterize: round each channel to 1/levels steps
    half = nodes.new("ShaderNodeVectorMath")
    half.operation = "ADD"
    half.inputs[1].default_value = (0.5 / POSTERIZE_LEVELS,) * 3
    links.new(tex.outputs["Color"], half.inputs[0])
    snap = nodes.new("ShaderNodeVectorMath")
    snap.operation = "SNAP"
    snap.inputs[1].default_value = (1 / POSTERIZE_LEVELS,) * 3
    links.new(half.outputs["Vector"], snap.inputs[0])
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
    links.new(ramp.outputs["Color"], tint.inputs[0])
    links.new(sk(soft.outputs, "Result_Vector"), tint.inputs[1])
    emission_output(nodes, links, tint.outputs[0], 1.0)
    return m


def make_outline_material():
    m, nodes, links = new_mat("Outline_Black")
    em = nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0, 0, 0, 1)
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(em.outputs[0], out.inputs["Surface"])
    m.use_backface_culling = True
    return m


def add_outline(obj, outline_mat, thickness=0.03):
    obj.data.materials.append(outline_mat)
    mod = obj.modifiers.new("Outline", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_rim = False
    mod.material_offset = len(obj.data.materials) - 1


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
    bg = nodes.new("ShaderNodeBackground")
    out = nodes.new("ShaderNodeOutputWorld")
    links.new(ramp.outputs["Color"], bg.inputs["Color"])
    links.new(bg.outputs[0], out.inputs["Surface"])


def build_screens(video_mat, bezel_mat, outline_mat):
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
        add_outline(bezel, outline_mat, 0.03)
        bezel.parent = pivot

        # stand: from floor to bezel bottom edge
        stand_h = SCREEN_Z - (SCREEN_H + 0.12) / 2 + 0.05
        inward = Vector((-math.cos(theta), -math.sin(theta), 0)) * 0.08
        bpy.ops.mesh.primitive_cylinder_add(radius=0.04, depth=stand_h)
        stand = bpy.context.active_object
        stand.name = f"Screen_{i}_Stand"
        stand.location = Vector((pivot.location.x, pivot.location.y, stand_h / 2)) + inward
        stand.data.materials.append(bezel_mat)
        add_outline(stand, outline_mat, 0.02)

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


# ----------------------------------------------------------------------- audio bus

def palette(t):
    t = max(0.0, min(1.0, t))
    hsv = [(p, colorsys.rgb_to_hsv(*c)) for p, c in PALETTE]
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
    return PALETTE[-1][1]


def build_audio_bus(scene, band_vals, band_norms, loud_vals, loud_norm, n, fps, ctx):
    nb = [[min(v * nm, 1.5) for v in vals[:n]] for vals, nm in zip(band_vals, band_norms)]
    loud = [min(v * loud_norm, 1.5) for v in loud_vals[:n]]
    bass = [(nb[0][f] + nb[1][f]) / 2 for f in range(n)]

    beat, last = [0.0] * n, -99
    win_size = max(2, round(0.33 * fps))
    min_dist = max(2, round(0.2 * fps))
    for f in range(n):
        w = bass[max(0, f - win_size):f]
        m = sum(w) / len(w) if w else 0.0
        if bass[f] > 0.5 and bass[f] > 1.3 * m and f - last >= min_dist:
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

    cent, s = [], None
    k_cent = ctx.per_frame(0.15)
    for f in range(n):
        tot = sum(nb[i][f] for i in range(8))
        c = sum(i * nb[i][f] for i in range(8)) / (7 * max(tot, 1e-6))
        s = c if s is None else s + (c - s) * k_cent
        cent.append(s)
    lo, hi = percentile(cent, 0.05), percentile(cent, 0.95)
    span = max(hi - lo, 1e-6)
    cols = [palette((c - lo) / span) for c in cent]

    for name in ("loud", "bass", "beat", "beat_env", "big"):
        scene[name] = 0.0
    scene["spec"] = [1.0, 0.2, 0.5]
    write_keys(scene, "loud", loud)
    write_keys(scene, "bass", bass)
    write_keys(scene, "beat", beat, constant=True)
    write_keys(scene, "beat_env", env)
    write_keys(scene, "big", big, constant=True)
    for i in range(3):
        write_keys(scene, "spec", [c[i] for c in cols], index=i)
    print(f"[ninja-viz] audio bus: {len(onsets)} onsets ({len(onsets) / (n / fps):.2f}/s), "
          f"big frames={int(sum(big))}, centroid p5..p95={lo:.3f}..{hi:.3f}")
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
    add_outline(orb, outline_mat, 0.02)
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
    for name, default in (("Rate", 4.0), ("Burst", 0.0), ("Speed", 5.0),
                          ("R", 1.0), ("G", 0.2), ("B", 0.5)):
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

    def rand(dtype, lo, hi, seed):
        n = N.new("FunctionNodeRandomValue")
        n.data_type = dtype
        _set(n.inputs["Min"], lo)
        _set(n.inputs["Max"], hi)
        _set(n.inputs["Seed"], seed)
        return n.outputs["Value"]

    gi = N.new("NodeGroupInput")
    go = N.new("NodeGroupOutput")
    inp = {k: gi.outputs[v] for k, v in ids.items()}
    si = N.new("GeometryNodeSimulationInput")
    so = N.new("GeometryNodeSimulationOutput")
    si.pair_with_output(so)
    dt = si.outputs["Delta Time"]
    frame = N.new("GeometryNodeInputSceneTime").outputs["Frame"]

    # --- advance existing particles
    g = store(si.outputs["Geometry"], "age", "FLOAT", math("ADD", attr("age", "FLOAT"), dt))
    drag_scaled = P_DRAG ** (ctx.base_fps / fps)
    vel_new = vmath("SUBTRACT", vmath("SCALE", attr("vel", "FLOAT_VECTOR"), scale=drag_scaled),
                    combine(0.0, 0.0, math("MULTIPLY", dt, P_GRAVITY)))
    g = store(g, "vel", "FLOAT_VECTOR", vel_new)
    sp = N.new("GeometryNodeSetPosition")
    _set(sp.inputs["Geometry"], g)
    _set(sp.inputs["Offset"], vmath("SCALE", attr("vel", "FLOAT_VECTOR"), scale=dt))
    pos = N.new("GeometryNodeInputPosition").outputs[0]
    sep = N.new("ShaderNodeSeparateXYZ")
    _set(sep.inputs[0], pos)
    dead = N.new("FunctionNodeBooleanMath")
    dead.operation = "OR"
    _set(dead.inputs[0], compare("GREATER_THAN", attr("age", "FLOAT"), P_LIFE))
    _set(dead.inputs[1], compare("LESS_THAN", sep.outputs["Z"], 0.0))
    dl = N.new("GeometryNodeDeleteGeometry")
    dl.domain = "POINT"
    _set(dl.inputs["Geometry"], sp.outputs["Geometry"])
    _set(dl.inputs["Selection"], dead.outputs[0])

    # --- emit new particles
    pts = N.new("GeometryNodePoints")
    _set(pts.inputs["Count"], math("ADD", inp["Rate"], inp["Burst"]))
    dirv = vmath("NORMALIZE", rand("FLOAT_VECTOR", (-1, -1, -1), (1, 1, 1), frame))
    _set(pts.inputs["Position"], vmath("ADD", vmath("SCALE", dirv, scale=ORB_R), (0, 0, ORB_Z)))
    _set(pts.inputs["Radius"], 0.05)
    index = N.new("GeometryNodeInputIndex").outputs[0]
    burst_mult = math("MULTIPLY_ADD", compare("GREATER_EQUAL", index, inp["Rate"]), 0.6, 1.0)
    speed = math("MULTIPLY", math("MULTIPLY", inp["Speed"],
                                  rand("FLOAT", 0.6, 1.0, math("ADD", frame, 7.0))), burst_mult)
    newg = store(pts.outputs["Points"], "vel", "FLOAT_VECTOR", vmath("SCALE", dirv, scale=speed))
    newg = store(newg, "pcol", "FLOAT_VECTOR", combine(inp["R"], inp["G"], inp["B"]))
    newg = store(newg, "age", "FLOAT", 0.0)

    join = N.new("GeometryNodeJoinGeometry")
    ng.links.new(newg, join.inputs[0])
    ng.links.new(dl.outputs["Geometry"], join.inputs[0])
    _set(so.inputs["Geometry"], join.outputs[0])

    # --- render: shrinking flat spheres
    ico = N.new("GeometryNodeMeshIcoSphere")
    ico.inputs["Radius"].default_value = 0.06
    ico.inputs["Subdivisions"].default_value = 1
    iop = N.new("GeometryNodeInstanceOnPoints")
    _set(iop.inputs["Points"], so.outputs["Geometry"])
    _set(iop.inputs["Instance"], ico.outputs["Mesh"])
    shrink = math("MAXIMUM", math("SUBTRACT", 1.0, math("DIVIDE", attr("age", "FLOAT"), P_LIFE)), 0.0)
    _set(iop.inputs["Scale"], shrink)
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
    emission_output(nodes, links, a.outputs["Vector"], 3.5)

    ng, ids = build_particle_tree(m, fps, ctx)
    mesh = bpy.data.meshes.new("ParticlesMesh")
    obj = bpy.data.objects.new("Particles", mesh)
    scene.collection.objects.link(obj)
    obj.visible_shadow = False
    mod = obj.modifiers.new("OrbParticles", "NODES")
    mod.node_group = ng
    p = lambda k: f'modifiers["OrbParticles"].properties.inputs.{ids[k]}.value'
    sv = lambda k: (scene, f'["{k}"]')
    rate_scale = ctx.base_fps / fps
    drive(obj, p("Rate"), f"{rate_scale} * (3.0 + 26.0 * l)", l=sv("loud"))
    drive(obj, p("Burst"), f"{rate_scale} * (160.0 * b)", b=sv("beat"))
    drive(obj, p("Speed"), "5.0 + 4.0 * l", l=sv("loud"))
    for i, k in enumerate("RGB"):
        drive(obj, p(k), "c * (0.6 + 0.8 * l)", c=(scene, f'["spec"][{i}]'), l=sv("loud"))
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

    go = N.new("NodeGroupOutput")
    L.new(sk(vig.outputs, "Result_Color"), go.inputs["Image"])
    scene.compositing_node_group = ng
    scene.render.use_compositing = True
    return ng


# ----------------------------------------------------------------------- build entrypoint

def build(scene, ctx, ov):
    """Build the Classic Ring scene."""
    video_mat = make_video_material(ctx.video_path, ctx.frame_count, ctx.frame_offset)
    bezel_mat = make_cel_material("Bezel_Cel", (0.05, 0.6, 0.8), (0.01, 0.08, 0.15), (0.6, 0.95, 1.0))
    floor_mat = make_cel_material("Floor_Cel", (0.05, 0.03, 0.15), (0.01, 0.005, 0.03),
                                  (0.15, 0.08, 0.35), checker=True)
    outline_mat = make_outline_material()

    build_world(scene)
    build_light(scene)
    build_floor(floor_mat)
    screens = build_screens(video_mat, bezel_mat, outline_mat)
    rig = build_camera(scene)

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

    build_audio_bus(scene, band_vals, band_norms, loud, rig["norm"], ctx.frame_count, ctx.fps, ctx)
    build_orb(scene, outline_mat)
    build_orb_light(scene)
    particles = build_particles(scene, ctx.fps, ctx)
    upgrade_bezel_rim(bezel_mat)
    upgrade_floor_grid(floor_mat)
    build_compositor(scene)

    return particles
