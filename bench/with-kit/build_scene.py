"""Phase 1 - model, material, rig, light and frame the whole set.

Writes work/scene.blend. Everything downstream loads that file.
Run:  /home/user/bpyenv/bin/python build_scene.py
"""
import os, sys, math, time, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib as L
from lib import (bpy, bmesh, Vector, Euler, R, box, ball, cyl, arc_bar, lathe,
                 Rig, apply_mods, M, MAT, RN, INS, A, CP, palette, flat)

WORK = os.path.join(HERE, "work")
os.makedirs(WORK, exist_ok=True)
T0 = time.time()


def lap(tag):
    print(f"[{time.time()-T0:7.2f}s] {tag}", flush=True)


# ===================================================================== helpers
def limb(name, p, q, r, mat, r2=None, seg=10):
    """Cylinder spanning two points. Arms and legs are built along their bone."""
    p, q = Vector(p), Vector(q)
    d = q - p
    ln = d.length
    z = Vector((0, 0, 1))
    if d.normalized().dot(z) < 0.9999:
        axis = z.cross(d.normalized())
        ang = math.acos(max(-1, min(1, z.dot(d.normalized()))))
        rot = (axis.normalized() * ang).to_track_quat  # placeholder, replaced below
    ob = cyl(name, tuple((p + q) / 2), r, ln, mat=mat, seg=seg, r2=r2, bevel=0.0)
    # orient: rotate local +Z onto d
    q4 = z.rotation_difference(d.normalized())
    ob.rotation_euler = q4.to_euler('XYZ')
    apply_rot(ob)
    return ob


def apply_rot(ob):
    """Bake the object's rotation into its mesh so joins and vertex groups are
    unaffected by it. object.join() does respect transforms, but keeping every
    part transform-free makes the rest pose auditable."""
    m = ob.rotation_euler.to_matrix().to_4x4()
    ob.data.transform(m)
    ob.rotation_euler = (0, 0, 0)
    return ob


# ===================================================================== plumber
def build_plumber(name, shirt, hat, h_target, le=0.0, w=1.0):
    """le = leg extra (Luigi is taller in the legs), w = body width factor."""
    P = palette()
    Z_ANK, Z_KNEE, Z_HIP = 0.095, 0.185 + le, 0.300 + le
    Z_WAIST, Z_CHEST, Z_SHLD = 0.345 + le, 0.430 + le, 0.545 + le
    Z_HEAD, Z_CAP = 0.655 + le, 0.712 + le
    hy, sy = 0.072 * w, 0.148 * w

    rg = Rig(name)
    rg.bone("root", (0, 0, 0), (0, 0, 0.12))
    rg.bone("hips", (0, 0, Z_HIP), (0, 0, Z_WAIST), "root")
    rg.bone("chest", (0, 0, Z_WAIST), (0, 0, Z_SHLD), "hips")
    rg.bone("head", (0, 0, Z_SHLD + 0.015), (0, 0, Z_HEAD + 0.10), "chest")
    rg.bone("cap", (0, 0, Z_HEAD + 0.055), (0, 0, Z_CAP + 0.085), "head")
    for s, sg in (("L", 1), ("R", -1)):
        rg.bone(f"thigh.{s}", (0, sg * hy, Z_HIP), (0, sg * hy, Z_KNEE), "hips")
        rg.bone(f"shin.{s}", (0, sg * hy, Z_KNEE), (0, sg * hy, Z_ANK), f"thigh.{s}")
        rg.bone(f"foot.{s}", (0, sg * hy, Z_ANK), (0.10, sg * hy, Z_ANK - 0.03), f"shin.{s}")
        rg.bone(f"uarm.{s}", (0, sg * sy, Z_SHLD), (0, sg * (sy + 0.055), Z_SHLD - 0.105), "chest")
        rg.bone(f"larm.{s}", (0, sg * (sy + 0.055), Z_SHLD - 0.105),
                (0, sg * (sy + 0.075), Z_SHLD - 0.205), f"uarm.{s}")
        rg.bone(f"hand.{s}", (0, sg * (sy + 0.075), Z_SHLD - 0.205),
                (0, sg * (sy + 0.085), Z_SHLD - 0.270), f"larm.{s}")

    pr = rg.part
    # --- legs / boots
    for s, sg in (("L", 1), ("R", -1)):
        pr(box(f"{name}_boot.{s}", (0.030, sg * hy, Z_ANK - 0.042),
               (0.215, 0.120 * w, 0.100), mat=P["brown"], bevel=0.016), f"foot.{s}")
        pr(limb(f"{name}_shin.{s}", (0, sg * hy, Z_KNEE), (0, sg * hy, Z_ANK - 0.01),
                0.054 * w, P["blue"]), f"shin.{s}")
        pr(limb(f"{name}_thigh.{s}", (0, sg * hy, Z_HIP + 0.01), (0, sg * hy, Z_KNEE),
                0.066 * w, P["blue"]), f"thigh.{s}")
    # --- torso
    pr(box(f"{name}_overalls", (0, 0, (Z_HIP + Z_WAIST) / 2 + 0.015),
           (0.175, 0.225 * w, 0.185), mat=P["blue"], bevel=0.030), "hips")
    pr(box(f"{name}_shirt", (0, 0, Z_CHEST + 0.045),
           (0.190, 0.245 * w, 0.150), mat=shirt, bevel=0.034), "chest")
    for sg in (1, -1):
        pr(box(f"{name}_strap{sg}", (0.090, sg * 0.062 * w, Z_CHEST + 0.055),
               (0.028, 0.055 * w, 0.165), mat=P["blue"], bevel=0.006), "chest")
        pr(ball(f"{name}_btn{sg}", (0.098, sg * 0.062 * w, Z_WAIST + 0.030), 0.026,
                mat=P["yellow"], seg=10, rings=6), "hips")
    # --- arms
    for s, sg in (("L", 1), ("R", -1)):
        pr(limb(f"{name}_uarm.{s}", (0, sg * sy, Z_SHLD + 0.015),
                (0, sg * (sy + 0.055), Z_SHLD - 0.105), 0.048 * w, shirt), f"uarm.{s}")
        pr(limb(f"{name}_larm.{s}", (0, sg * (sy + 0.050), Z_SHLD - 0.095),
                (0, sg * (sy + 0.075), Z_SHLD - 0.205), 0.042 * w, shirt), f"larm.{s}")
        pr(ball(f"{name}_hand.{s}", (0, sg * (sy + 0.080), Z_SHLD - 0.240), 0.058,
                scale=(1.0, 0.85, 1.0), mat=P["white"], seg=12, rings=8), f"hand.{s}")
    # --- head
    pr(ball(f"{name}_head", (0.005, 0, Z_HEAD), 0.108, scale=(1.08, 1.0, 1.02),
            mat=P["skin"], seg=18, rings=12), "head")
    pr(ball(f"{name}_nose", (0.105, 0, Z_HEAD - 0.012), 0.054,
            scale=(1.15, 1.0, 1.0), mat=P["skin"], seg=12, rings=8), "head")
    for sg in (1, -1):
        pr(box(f"{name}_mous{sg}", (0.092, sg * 0.032, Z_HEAD - 0.062),
               (0.090, 0.060, 0.034), mat=P["hair"], bevel=0.010), "head")
        pr(ball(f"{name}_eyew{sg}", (0.090, sg * 0.036, Z_HEAD + 0.038), 0.028,
                scale=(0.55, 1.0, 1.30), mat=P["white"], seg=10, rings=8), "head")
        pr(ball(f"{name}_eyep{sg}", (0.108, sg * 0.036, Z_HEAD + 0.036), 0.016,
                scale=(0.6, 1.0, 1.1), mat=P["black"], seg=8, rings=6), "head")
        pr(ball(f"{name}_ear{sg}", (-0.020, sg * 0.104, Z_HEAD - 0.010), 0.032,
                scale=(1.0, 0.6, 1.1), mat=P["skin"], seg=10, rings=8), "head")
        pr(ball(f"{name}_sideb{sg}", (-0.050, sg * 0.080, Z_HEAD - 0.045), 0.040,
                scale=(1.0, 0.7, 0.9), mat=P["hair"], seg=10, rings=8), "head")
    # --- cap: lathed crown + brim + emblem
    pr(lathe(f"{name}_crown", (0.005, 0, Z_CAP - 0.030),
             [(0.000, 0.088), (0.046, 0.082), (0.080, 0.058),
              (0.100, 0.026), (0.106, 0.000), (0.106, -0.022)],
             mat=hat, steps=22), "cap")
    pr(box(f"{name}_brim", (0.108, 0, Z_CAP - 0.050), (0.125, 0.165, 0.030),
           mat=hat, bevel=0.012), "cap")
    pr(ball(f"{name}_emblem", (0.078, 0, Z_CAP + 0.000), 0.040,
            scale=(0.35, 1.0, 1.0), mat=P["white"], seg=12, rings=8), "cap")

    rig, body = rg.build()
    # exact height -> uniform scale on the armature object
    dg = bpy.context.evaluated_depsgraph_get()
    me = body.evaluated_get(dg).data
    zmax = max(v.co.z for v in me.vertices)
    k = h_target / zmax
    rig.scale = (k, k, k)
    bpy.context.view_layer.update()
    return rig, body, k


# ======================================================================= koopa
def build_koopa(name):
    P = palette()
    rg = Rig(name)
    rg.bone("root", (0, 0, 0), (0, 0, 0.10))
    rg.bone("body", (0, 0, 0.10), (0, 0, 0.26), "root")
    rg.bone("shell", (0, 0, 0.20), (0, 0, 0.38), "body")
    rg.bone("head", (0, 0, 0.26), (0, 0, 0.44), "body")
    rg.bone("eye", (0, 0, 0.36), (0, 0, 0.42), "head")
    rg.bone("xeye", (0, 0, 0.36), (0, 0, 0.42), "head")
    for s, sg in (("L", 1), ("R", -1)):
        rg.bone(f"foot.{s}", (0, sg * 0.082, 0.055), (0.10, sg * 0.082, 0.040), "root")
        rg.bone(f"arm.{s}", (0, sg * 0.105, 0.225), (0.065, sg * 0.145, 0.185), "body")
    pr = rg.part

    for s, sg in (("L", 1), ("R", -1)):
        pr(box(f"{name}_foot.{s}", (0.030, sg * 0.082, 0.040),
               (0.165, 0.105, 0.080), mat=P["beak"], bevel=0.016), f"foot.{s}")
        pr(limb(f"{name}_arm.{s}", (0, sg * 0.100, 0.230), (0.070, sg * 0.150, 0.180),
                0.040, P["yellow"]), f"arm.{s}")
        pr(ball(f"{name}_hand.{s}", (0.078, sg * 0.158, 0.172), 0.040,
                mat=P["beak"], seg=10, rings=8), f"arm.{s}")
    pr(ball(f"{name}_belly", (0.005, 0, 0.195), 0.125, scale=(0.92, 0.88, 0.80),
            mat=P["yellow"], seg=18, rings=12), "body")
    # shell: lathed dome + kit ring for the rim + spots
    sh = lathe(f"{name}_shelldome", (-0.030, 0, 0.175),
               [(0.000, 0.185), (0.075, 0.172), (0.130, 0.130),
                (0.168, 0.070), (0.178, 0.000), (0.168, -0.035)],
               mat=P["shellg"], steps=24)
    pr(sh, "shell")
    rim = M.plain_ring(f"{name}_shellrim", radius=0.178, thickness=0.030,
                       height=0.055, steps=28, smooth=True)
    rim.location = (-0.030, 0, 0.165)
    rim.data.materials.append(P["shelll"])
    pr(rim, "shell")
    for i in range(6):
        a = math.tau * i / 6
        pr(ball(f"{name}_spot{i}", (-0.030 + 0.085 * math.cos(a) * 0.5,
                                    0.118 * math.sin(a), 0.175 + 0.095 * math.cos(a)),
                0.034, scale=(0.5, 1.0, 1.0), mat=P["shelll"], seg=8, rings=6), "shell")
    # head
    pr(ball(f"{name}_head", (0.055, 0, 0.345), 0.105, scale=(1.0, 0.95, 0.92),
            mat=P["yellow"], seg=18, rings=12), "head")
    pr(cyl(f"{name}_beak", (0.165, 0, 0.330), 0.052, 0.080, rot=(0, R(90), 0),
           r2=0.012, mat=P["beak"], seg=12, bevel=0.0), "head")
    for sg in (1, -1):
        pr(ball(f"{name}_eyew{sg}", (0.105, sg * 0.048, 0.392), 0.034,
                scale=(0.8, 1.0, 1.15), mat=P["white"], seg=10, rings=8), "eye")
        pr(ball(f"{name}_eyep{sg}", (0.128, sg * 0.050, 0.390), 0.018,
                mat=P["black"], seg=8, rings=6), "eye")
        for rr in (35, -35):
            pr(box(f"{name}_x{sg}_{rr}", (0.122, sg * 0.050, 0.392),
                   (0.012, 0.012, 0.085), rot=(0, R(rr), 0),
                   mat=P["black"], bevel=0.0), "xeye")
    rig, body, = rg.build()
    dg = bpy.context.evaluated_depsgraph_get()
    me = body.evaluated_get(dg).data
    zmax = max(v.co.z for v in me.vertices)
    k = 0.70 / zmax
    rig.scale = (k, k, k)
    bpy.context.view_layer.update()
    # eyes normal, X hidden
    rig.pose.bones["xeye"].scale = (0.001, 0.001, 0.001)
    bpy.context.view_layer.update()
    return rig, body, k


# ================================================================= environment
def ground_row(x0, x1, z_top=0.0):
    P = palette()
    ob, me = L._mk("GroundRow")
    bm = bmesh.new()
    n = int(x1 - x0)
    for i in range(n):
        cx = x0 + i + 0.5
        M.add_box(bm, (cx, 0, z_top - 0.5), (0.98, 1.0, 0.98))
        M.add_box(bm, (cx, -0.50, z_top - 0.12), (0.86, 0.06, 0.12))
        for sgn in (-1, 1):
            M.add_box(bm, (cx + sgn * 0.33, -0.50, z_top - 0.62), (0.26, 0.06, 0.26))
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = False
    me.materials.append(P["ground"])
    # a second, deeper fill slab so the trench below never shows through
    fill = box("GroundFill", (0, 0.3, -1.6), (x1 - x0, 1.6, 2.0),
               mat=P["groundd"], bevel=0.0)
    return ob, fill


def brick(name, x, z):
    P = palette()
    ob, me = L._mk(name)
    bm = bmesh.new()
    M.add_box(bm, (0, 0, 0), (0.98, 0.98, 0.98))
    for row, dz in enumerate((0.30, 0.0, -0.30)):
        off = 0.245 if row % 2 else 0.0
        for k in (-1, 0, 1):
            M.add_box(bm, (k * 0.49 + off, -0.50, dz), (0.030, 0.06, 0.26))
        M.add_box(bm, (0, -0.50, dz + 0.15), (0.98, 0.06, 0.030))
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = False
    me.materials.append(P["brick"])
    ob.location = (x, 0, z)
    return ob


def qblock(name, x, z, dim=False):
    P = palette()
    body = box(name, (x, 0, z), (0.98, 0.98, 0.98),
               mat=P["qdim"] if dim else P["qblock"], bevel=0.035)
    kids = [body]
    for sx in (-1, 1):
        for sz in (-1, 1):
            kids.append(box(f"{name}_r{sx}{sz}", (x + sx * 0.38, -0.50, z + sz * 0.38),
                            (0.13, 0.08, 0.13), mat=P["qdim"] if dim else P["brickdk"],
                            bevel=0.012))
    qm = P["qdim"] if dim else P["white"]
    kids.append(arc_bar(f"{name}_qarc", (x, -0.54, z + 0.17), 0.175, 0.090, 0.055,
                        R(-40), R(215), mat=qm, steps=16))
    kids.append(box(f"{name}_qstem", (x + 0.01, -0.54, z - 0.11),
                    (0.090, 0.055, 0.155), mat=qm, bevel=0.012))
    kids.append(box(f"{name}_qdot", (x + 0.01, -0.54, z - 0.31),
                    (0.100, 0.055, 0.095), mat=qm, bevel=0.016))
    return join_into(name, kids)


def join_into(name, objs):
    for o in objs:
        apply_mods(o)
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    objs[0].name = name
    return objs[0]


def pipe(name, x, h=1.65):
    P = palette()
    rim = lathe(f"{name}_rim", (x, 0, h - 0.17),
                [(0.00, 0.17), (0.56, 0.17), (0.66, 0.11), (0.66, -0.06),
                 (0.56, -0.12), (0.46, -0.12), (0.46, 0.09), (0.00, 0.09)],
                mat=P["pipe"], steps=26)
    hole = lathe(f"{name}_hole", (x, 0, h - 0.19),
                 [(0.00, 0.00), (0.46, 0.00), (0.46, -0.10), (0.00, -0.10)],
                 mat=P["pipedk"], steps=26)
    body = lathe(f"{name}_body", (x, 0, 0),
                 [(0.00, 0.0), (0.52, 0.0), (0.52, h - 0.30), (0.00, h - 0.30)],
                 mat=P["pipe"], steps=26)
    return join_into(name, [body, rim, hole])


def cloud(name, x, z, s=1.0):
    P = palette()
    parts = []
    for i, (dx, dz, r) in enumerate(((-0.9, -0.05, 0.42), (0.0, 0.12, 0.62),
                                     (0.85, -0.02, 0.46), (-0.35, 0.18, 0.44),
                                     (0.42, 0.16, 0.42))):
        parts.append(ball(f"{name}_{i}", (x + dx * s, 7.0, z + dz * s), r * s,
                          scale=(1.0, 0.55, 0.8), mat=P["cloud"], seg=12, rings=8))
    return join_into(name, parts)


def mushroom(name):
    P = palette()
    stem = lathe(f"{name}_stem", (0, 0, 0),
                 [(0.00, 0.0), (0.105, 0.0), (0.105, 0.135), (0.00, 0.145)],
                 mat=P["mushstem"], steps=18)
    cap = lathe(f"{name}_cap", (0, 0, 0.135),
                [(0.00, 0.150), (0.075, 0.143), (0.130, 0.112),
                 (0.168, 0.058), (0.175, 0.000), (0.150, -0.022)],
                mat=P["mushred"], steps=22)
    spots = []
    for i, (a, rr) in enumerate(((0, 0.085), (120, 0.095), (240, 0.090))):
        spots.append(ball(f"{name}_sp{i}",
                          (rr * math.cos(R(a)), rr * math.sin(R(a)) * 0.6, 0.265),
                          0.048, scale=(1, 1, 0.45), mat=P["mushstem"],
                          seg=10, rings=6))
    eyes = []
    for sg in (1, -1):
        eyes.append(ball(f"{name}_ey{sg}", (-0.085, sg * 0.040, 0.075), 0.022,
                         scale=(0.6, 1, 1.2), mat=P["black"], seg=8, rings=6))
    return join_into(name, [stem, cap] + spots + eyes)


def loose_shell(name):
    P = palette()
    d = lathe(f"{name}_d", (0, 0, 0),
              [(0.000, 0.185), (0.075, 0.172), (0.130, 0.130),
               (0.168, 0.070), (0.178, 0.000), (0.168, -0.035)],
              mat=P["shellg"], steps=24)
    rim = M.plain_ring(f"{name}_rim", radius=0.178, thickness=0.030,
                       height=0.055, steps=28)
    rim.location = (0, 0, -0.010)
    rim.data.materials.append(P["shelll"])
    under = lathe(f"{name}_u", (0, 0, -0.035),
                  [(0.000, 0.000), (0.168, 0.000), (0.168, -0.055), (0.000, -0.080)],
                  mat=P["beak"], steps=24)
    spots = []
    for i in range(6):
        a = math.tau * i / 6
        spots.append(ball(f"{name}_s{i}", (0.085 * math.cos(a), 0.118 * math.sin(a),
                                           0.095 * math.cos(a) * 0.6 + 0.06),
                          0.034, scale=(0.6, 1.0, 1.0), mat=P["shelll"],
                          seg=8, rings=6))
    return join_into(name, [d, rim, under] + spots)


# ======================================================================== main
def main():
    L.reset()
    P = palette()
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 480
    lap("reset")

    ground_row(-20, 20)
    bricks = [brick(f"Brick{i}", x, 2.5) for i, x in
              enumerate((-3, -2, -1, 1, 2, 3))]
    q_on = qblock("QBlock", 0.0, 2.5, dim=False)
    q_off = qblock("QBlockUsed", 0.0, 2.5, dim=True)
    q_off.scale = (0.001, 0.001, 0.001)
    pipe("Pipe", 8.2)
    for i, (x, z, s) in enumerate(((-6.5, 5.6, 1.0), (3.4, 6.2, 0.82),
                                   (9.5, 5.2, 0.9), (-1.0, 7.0, 0.6))):
        cloud(f"Cloud{i}", x, z, s)
    # sky card + hills
    sky = box("Sky", (0, 15.0, 5.0), (90, 0.2, 34), mat=P["sky"], bevel=0.0)
    for i, (x, r) in enumerate(((-9, 5.5), (2, 4.4), (12, 6.0))):
        h = ball(f"Hill{i}", (x, 11.0, -1.4), r, scale=(1.0, 0.25, 0.55),
                 mat=P["green"], seg=16, rings=10)
    lap("environment")

    mario_rig, mario_body, km = build_plumber("Mario", P["red"], P["red"], 0.90, le=0.0, w=1.0)
    lap("mario modelled+rigged")
    luigi_rig, luigi_body, kl = build_plumber("Luigi", P["green"], P["green"], 1.00,
                                              le=0.075, w=0.90)
    lap("luigi modelled+rigged")
    koopa_rig, koopa_body, kk = build_koopa("Koopa")
    lap("koopa modelled+rigged")

    mush = mushroom("Mushroom")
    mush.location = (0, 0, 2.5)
    mush.scale = (0.001, 0.001, 0.001)
    shell = loose_shell("LooseShell")
    shell.location = (3.2, 0, 0.16)
    shell.scale = (0.001, 0.001, 0.001)
    lap("props")

    # --- placement for the opening frame
    mario_rig.location = (-6.0, 0.0, 0.0)
    luigi_rig.location = (-7.6, 0.0, 0.0)
    koopa_rig.location = (11.5, 0.0, 0.0)

    # --- light: 1 sun + 1 broad fill. Cheap for Cycles, reads as noon platformer.
    RN.setup_world(color=(0.30, 0.52, 0.86), strength=0.55)
    sun_d = bpy.data.lights.new("Sun", 'SUN')
    sun_d.energy = 4.2
    sun_d.angle = R(6.0)
    sun_d.color = (1.0, 0.96, 0.88)
    sun = bpy.data.objects.new("Sun", sun_d)
    sc.collection.objects.link(sun)
    sun.rotation_euler = Euler((R(52), R(8), R(-28)), 'XYZ')
    fill_d = bpy.data.lights.new("Fill", 'AREA')
    fill_d.energy = 900
    fill_d.size = 14.0
    fill_d.color = (0.72, 0.84, 1.0)
    fill = bpy.data.objects.new("Fill", fill_d)
    sc.collection.objects.link(fill)
    fill.location = (-7.0, -11.0, 5.5)
    fill.rotation_euler = Euler((R(62), 0, R(-38)), 'XYZ')

    # --- camera: locked side-on. Never guess the distance.
    cam_d = bpy.data.cameras.new("Camera")
    cam_d.lens = 50.0
    cam = bpy.data.objects.new("Camera", cam_d)
    sc.collection.objects.link(cam)
    sc.camera = cam
    frame_h = 5.0
    dist = RN.frame_distance(frame_h, lens=50.0, sensor=36.0 * 480 / 854, margin=1.06)
    cam.location = (-5.6, -dist, 1.85)
    cam.rotation_euler = Euler((R(90), 0, 0), 'XYZ')
    print(f"[cam] side-on distance for a {frame_h} unit tall frame: {dist:.2f}")

    sc.render.resolution_x, sc.render.resolution_y = 854, 480
    lap("lights+camera")

    tris = L.tri_count()
    stats = {"triangles": tris, "cam_distance": round(dist, 3),
             "mario_scale": round(km, 4), "luigi_scale": round(kl, 4),
             "koopa_scale": round(kk, 4),
             "objects": len(sc.objects),
             "bones": {r.name: len(r.data.bones) for r in sc.objects if r.type == 'ARMATURE'}}
    print("[stats]", json.dumps(stats, indent=2))
    with open(os.path.join(WORK, "build_stats.json"), "w") as f:
        json.dump(stats, f, indent=2)

    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(WORK, "scene.blend"))
    lap("saved work/scene.blend")


if __name__ == "__main__":
    main()
