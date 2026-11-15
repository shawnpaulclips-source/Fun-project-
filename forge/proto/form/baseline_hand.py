"""The control arm: a hand + forearm the way a competent agent builds it in raw
bpy without the kit. Same anatomical joint positions as hand.py (so the A/B
measures structure, not proportions), built the standard way:

    cylinders per phalanx + spheres at joints + scaled-cube palm + cone forearm
    -> join -> voxel remesh (to fuse) -> shade smooth
    -> armature with default bone roll -> automatic (bone heat) weights

This is exactly the recipe in forge-kit's own examples/02_rig_character.py,
applied to a hand. Nothing here is sabotaged: the voxel size is the largest
that keeps every finger separate (genus 0), searched, not guessed.
"""
import math
import bpy
from mathutils import Vector
import hand as H


def _cyl(p0, p1, r0, r1, verts=16):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r0, radius2=r1, depth=d.length,
                                    location=(p0 + p1) / 2)
    ob = bpy.context.object
    ob.rotation_mode = "QUATERNION"
    ob.rotation_quaternion = d.to_track_quat("Z", "Y")
    return ob


def _sph(c, r):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=c, segments=16, ring_count=10)
    return bpy.context.object


def build_baseline(voxel=0.0022, name="BaseHand", hb=None):
    hb = hb or H.HandBuilder()
    if not hasattr(hb, "D"):
        hb.layout()
        hb.digit = {d: {"origin": hb.D[d]["head"] + hb.D[d]["u"] * (-0.06 * hb.D[d]["dep"])} for d in H.DIGITS}
    parts = []
    s = hb.s
    # palm: scaled cube spanning wrist -> knuckles
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.048, 0.0, 0.0))
    palm = bpy.context.object
    palm.scale = (0.092, 0.074, 0.028)
    bpy.ops.object.transform_apply(scale=True)
    bv = palm.modifiers.new("bevel", "BEVEL")       # a charitable baseline: rounded palm
    bv.width = 0.009; bv.segments = 4
    bpy.context.view_layer.objects.active = palm
    bpy.ops.object.modifier_apply(modifier="bevel")
    parts.append(palm)
    L = hb.p["forearm_len"] * s
    parts.append(_cyl((-L, 0, 0), (0.004, 0, 0), 0.5 * hb.p["elbow_w"] * s * 0.9, 0.5 * hb.p["wrist_w"] * s * 0.8, 24))
    for d in H.DIGITS:
        g = hb.D[d]
        o = hb.digit[d]["origin"]
        pts = [o + g["a"] * t for t in (0.0, g["P"], g["P"] + g["M"], g["L"])]
        r = [0.5 * g["w"] * (1 - 0.2 * t / g["L"]) for t in (0.0, g["P"], g["P"] + g["M"], g["L"])]
        for k in range(3):
            parts.append(_cyl(pts[k], pts[k + 1], r[k], r[k + 1]))
        for k in range(4):
            parts.append(_sph(pts[k], r[k]))
    T = hb.T
    pts = [T["cmc"] + T["a"] * t for t in (0.0, T["MC"], T["MC"] + T["P"], T["L"])]
    rr = [0.5 * T["w"] * 1.5, 0.5 * T["w"], 0.5 * T["w"] * 0.95, 0.5 * T["w"] * 0.85]
    for k in range(3):
        parts.append(_cyl(pts[k], pts[k + 1], rr[k], rr[k + 1]))
    for k in range(1, 4):
        parts.append(_sph(pts[k], rr[k]))
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    ob = bpy.context.object
    ob.name = name
    rm = ob.modifiers.new("remesh", "REMESH")
    rm.mode = "VOXEL"
    rm.voxel_size = voxel
    rm.use_smooth_shade = True
    bpy.ops.object.modifier_apply(modifier="remesh")
    for poly in ob.data.polygons:
        poly.use_smooth = True
    return hb, ob


def build_baseline_rig(hb, name="BaseRig"):
    """Same joint positions as hand.build_rig, but bone roll left at Blender's
    default - what you get from edit_bones.new() + head/tail and nothing else.
    No twist bones: one forearm bone, one hand bone."""
    s = hb.s
    arm = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.edit_bones
    L = hb.p["forearm_len"] * s
    fa = eb.new("forearm"); fa.head = (-L, 0, 0); fa.tail = (0, 0, 0)
    hand = eb.new("hand"); hand.head = (0, 0, 0); hand.tail = (0.028 * hb.p["scale"], 0, 0)
    hand.parent = fa; hand.use_connect = True
    for d in H.DIGITS:
        g = hb.D[d]
        o = hb.digit[d]["origin"]
        mc = eb.new(f"mc_{d}"); mc.head = g["base"] + g["u"] * (-0.06 * g["dep"]); mc.tail = o; mc.parent = hand
        prev = mc
        pts = [o + g["a"] * t for t in (0.0, g["P"], g["P"] + g["M"], g["L"])]
        for k in range(3):
            b = eb.new(f"{d}.0{k+1}"); b.head, b.tail = pts[k], pts[k + 1]
            b.parent = prev; b.use_connect = True
            prev = b
    T = hb.T
    pts = [T["cmc"] + T["a"] * t for t in (0.0, T["MC"], T["MC"] + T["P"], T["L"])]
    prev = hand
    for k in range(3):
        b = eb.new(f"thumb.0{k+1}"); b.head, b.tail = pts[k], pts[k + 1]
        b.parent = prev; b.use_connect = k > 0
        prev = b
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in rig.pose.bones:
        pb.rotation_mode = "XYZ"
    return rig


def bind_auto(ob, rig):
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True); rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.vertex_group_limit_total(limit=4)
    bpy.ops.object.vertex_group_normalize_all(lock_active=False)
    return ob
