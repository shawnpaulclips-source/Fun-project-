"""Toon-character + platformer-set library, built on forge-kit.

forge-kit covers hard-surface procedural geometry, PBR, Rigify, f-curve craft and
headless rendering. It does NOT cover: flat toon materials, rigid (one-bone-per-part)
skinning, object-hierarchy character construction, world-axis bone posing, or
ballistic arc keyframing. That is what this module adds.
"""
import sys, os, math

sys.path.insert(0, "/home/user/Fun-project-/forge/kit")
from forgekit import env
env.prepare()

import bpy, bmesh
from mathutils import Vector, Matrix, Euler

from forgekit import mesh as M, materials as MAT, render as RN
from forgekit import inspect as INS, anim as A, checkpoint as CP

R = math.radians
TAU = math.tau


# ---------------------------------------------------------------- scene reset
def reset():
    """Factory reset WITHOUT killing addons (README trap: read_factory_settings
    disables addons). We do not need Rigify, so the cheap path is fine."""
    bpy.ops.wm.read_homefile(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps = 24
    sc.unit_settings.system = 'METRIC'
    return sc


# ---------------------------------------------------------------- materials
def flat(name, color, rough=0.45, sheen=0.0, emit=0.0):
    """Toon-ish dielectric: constant base colour, no procedural noise.

    forgekit.materials.dielectric() wires noise into normal+roughness, which is
    correct for hard surface and wrong for a cel-shaded cap.
    """
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Metallic"].default_value = 0.0
    b.inputs["Roughness"].default_value = rough
    if "Sheen Weight" in b.inputs:
        b.inputs["Sheen Weight"].default_value = sheen
    if emit > 0.0:
        b.inputs["Emission Color"].default_value = (*color, 1.0)
        b.inputs["Emission Strength"].default_value = emit
    m.diffuse_color = (*color, 1.0)       # Workbench MATERIAL colour
    return m


PAL = {}


def palette():
    if PAL:
        return PAL
    P = {
        "red":      (0.72, 0.045, 0.040),
        "green":    (0.055, 0.42, 0.075),
        "blue":     (0.045, 0.085, 0.52),
        "white":    (0.93, 0.93, 0.90),
        "skin":     (0.95, 0.68, 0.46),
        "brown":    (0.16, 0.075, 0.030),
        "hair":     (0.055, 0.030, 0.020),
        "black":    (0.016, 0.016, 0.020),
        "yellow":   (0.90, 0.68, 0.10),
        "shellg":   (0.075, 0.46, 0.105),
        "shelll":   (0.35, 0.72, 0.22),
        "beak":     (0.92, 0.80, 0.30),
        "brick":    (0.58, 0.20, 0.085),
        "brickdk":  (0.30, 0.10, 0.045),
        "ground":   (0.72, 0.42, 0.12),
        "groundd":  (0.38, 0.20, 0.055),
        "qblock":   (0.95, 0.58, 0.055),
        "qdim":     (0.30, 0.24, 0.20),
        "pipe":     (0.075, 0.55, 0.14),
        "pipedk":   (0.040, 0.33, 0.085),
        "sky":      (0.32, 0.60, 0.95),
        "cloud":    (0.97, 0.98, 1.00),
        "coin":     (1.00, 0.80, 0.12),
        "mushred":  (0.80, 0.07, 0.07),
        "mushstem": (0.96, 0.90, 0.72),
    }
    rough = {"coin": 0.25, "qblock": 0.35, "white": 0.5, "black": 0.6}
    for k, v in P.items():
        PAL[k] = flat("m_" + k, v, rough=rough.get(k, 0.45))
    PAL["sky"].node_tree.nodes["Principled BSDF"].inputs["Emission Color"].default_value = (*P["sky"], 1)
    PAL["sky"].node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 1.1
    PAL["cloud"].node_tree.nodes["Principled BSDF"].inputs["Emission Color"].default_value = (*P["cloud"], 1)
    PAL["cloud"].node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 0.55
    return PAL


# ---------------------------------------------------------------- primitives
def _mk(name):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob, me


def _finish(ob, me, bm, mat, smooth, bevel, bseg=1):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = smooth
    if mat is not None:
        me.materials.append(mat)
    if bevel:
        m = ob.modifiers.new('bv', 'BEVEL')
        m.width = bevel
        m.segments = bseg
        m.limit_method = 'ANGLE'
        m.angle_limit = R(32)
        m.harden_normals = False
        apply_mods(ob)
    return ob


def apply_mods(ob):
    """Bake every modifier into the mesh. MUST happen before object.join():
    join drops the modifier stack of every non-active object."""
    dg = bpy.context.evaluated_depsgraph_get()
    new = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    old = ob.data
    ob.data = new
    ob.modifiers.clear()
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return ob


def box(name, center, size, rot=(0, 0, 0), mat=None, bevel=0.008, smooth=False):
    ob, me = _mk(name)
    bm = bmesh.new()
    M.add_box(bm, (0, 0, 0), size, Euler(rot, 'XYZ'))
    ob.location = center
    return _finish(ob, me, bm, mat, smooth, bevel)


def ball(name, center, radius, scale=(1, 1, 1), mat=None, seg=16, rings=10, smooth=True):
    ob, me = _mk(name)
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=radius)
    except TypeError:                                   # pre-3.0 kwarg name
        bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, diameter=radius)
    bmesh.ops.scale(bm, vec=Vector(scale), verts=bm.verts)
    ob.location = center
    return _finish(ob, me, bm, mat, smooth, 0)


def cyl(name, center, radius, depth, rot=(0, 0, 0), mat=None, seg=16,
        r2=None, smooth=True, bevel=0.004):
    ob, me = _mk(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                          radius1=radius, radius2=(radius if r2 is None else r2),
                          depth=depth, matrix=Euler(rot, 'XYZ').to_matrix().to_4x4())
    ob.location = center
    return _finish(ob, me, bm, mat, smooth, bevel)


def arc_bar(name, center, radius, thick, depth, a0, a1, mat=None, steps=14,
            smooth=True):
    """A swept square section along a circular arc in the XZ plane (faces -Y).

    Used for the question mark's hook. A stack of boxes along the same path
    reads as stair-stepping (forge-kit README section 7).
    """
    ob, me = _mk(name)
    bm = bmesh.new()
    rings = []
    for i in range(steps + 1):
        a = a0 + (a1 - a0) * i / steps
        cx, cz = math.cos(a), math.sin(a)
        for_r = Vector((cx, 0.0, cz))
        quad = [Vector((0, -depth / 2, 0)) + for_r * (radius - thick / 2),
                Vector((0, -depth / 2, 0)) + for_r * (radius + thick / 2),
                Vector((0, depth / 2, 0)) + for_r * (radius + thick / 2),
                Vector((0, depth / 2, 0)) + for_r * (radius - thick / 2)]
        rings.append([bm.verts.new(q) for q in quad])
    for i in range(len(rings) - 1):
        a, b = rings[i], rings[i + 1]
        for j in range(4):
            k = (j + 1) % 4
            bm.faces.new((a[j], a[k], b[k], b[j]))
    bm.faces.new(rings[0][::-1])
    bm.faces.new(rings[-1])
    ob.location = center
    return _finish(ob, me, bm, mat, smooth, 0)


def lathe(name, center, profile, mat=None, steps=24, smooth=True, close=False):
    """forge-kit's lathe, plus placement and material. Profile is [(r, z), ...]."""
    ob = M.lathe(name, profile, steps=steps, close=close, smooth=False)
    for p in ob.data.polygons:
        p.use_smooth = smooth
    if mat is not None:
        ob.data.materials.append(mat)
    ob.location = center
    return ob


def setmat(ob, mat):
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    return ob


# ---------------------------------------------------------------- rigid rig
class Rig:
    """One bone per body part, rigid skinning via single-weight vertex groups.

    Deliberately not Rigify. Rigify generates 706 bones and a face rig for a
    biped with smooth weights; an N64-era character has hard part boundaries,
    needs bone SCALE for squash-and-stretch, and must never have a weight bleed
    between a cap and a head. Rigid binding has no mesh contract to satisfy
    (no manifold requirement, no 3-edge-loop rule) and cannot fail silently.
    """

    def __init__(self, name):
        self.name = name
        self.specs = []        # (bone, head, tail, parent)
        self.parts = []        # (object, bone)
        self.rig = None
        self.body = None

    def bone(self, name, head, tail, parent=None):
        self.specs.append((name, Vector(head), Vector(tail), parent))
        return name

    def part(self, ob, bone):
        self.parts.append((ob, bone))
        return ob

    def build(self):
        arm = bpy.data.armatures.new(self.name + "_arm")
        rig = bpy.data.objects.new(self.name + "_rig", arm)
        bpy.context.scene.collection.objects.link(rig)
        bpy.ops.object.select_all(action='DESELECT')
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        made = {}
        for bn, head, tail, parent in self.specs:
            eb = arm.edit_bones.new(bn)
            eb.head, eb.tail = head, tail
            eb.use_deform = True
            eb.use_connect = False
            if parent:
                eb.parent = made[parent]
            made[bn] = eb
        bpy.ops.object.mode_set(mode='OBJECT')
        self.rig = rig

        for ob, bn in self.parts:
            apply_mods(ob)
            vg = ob.vertex_groups.new(name=bn)
            vg.add(list(range(len(ob.data.vertices))), 1.0, 'REPLACE')

        objs = [o for o, _ in self.parts]
        bpy.ops.object.select_all(action='DESELECT')
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.object.join()
        body = objs[0]
        body.name = self.name
        body.data.name = self.name + "_mesh"
        body.parent = rig
        mod = body.modifiers.new('skin', 'ARMATURE')
        mod.object = rig
        mod.use_vertex_groups = True
        for pb in rig.pose.bones:
            pb.rotation_mode = 'XYZ'
        self.body = body
        return rig, body


def bone_axis(rig, bone_name, world_axis='Y'):
    """Which local euler index drives rotation about a given WORLD axis, and the
    sign. Bone-local euler channels are in bone space, so a leg bone pointing
    down does NOT swing on local X just because the camera sees world X.
    Guessing this is a silent 'limb rotates on the wrong plane' bug."""
    b = rig.data.bones[bone_name]
    m = b.matrix_local.to_3x3()
    want = {'X': Vector((1, 0, 0)), 'Y': Vector((0, 1, 0)), 'Z': Vector((0, 0, 1))}[world_axis]
    best, bi, bs = -1.0, 0, 1.0
    for i in range(3):
        d = m.col[i].normalized().dot(want)
        if abs(d) > best:
            best, bi, bs = abs(d), i, (1.0 if d >= 0 else -1.0)
    return bi, bs


class Poser:
    """Pose bones by WORLD swing/twist/bend instead of bone-local euler index."""

    def __init__(self, rig):
        self.rig = rig
        self._cache = {}

    def ax(self, bone, world_axis):
        k = (bone, world_axis)
        if k not in self._cache:
            self._cache[k] = bone_axis(self.rig, bone, world_axis)
        return self._cache[k]

    def set(self, bone, swing=0.0, lean=0.0, twist=0.0, loc=None, scale=None):
        """swing = rotation about world Y (forward/back, the platformer plane)
        lean  = rotation about world X (sideways tilt)
        twist = rotation about world Z (turn to face camera)"""
        pb = self.rig.pose.bones.get(bone)
        if pb is None:
            raise KeyError(f"no bone {bone} on {self.rig.name}")
        rot = [0.0, 0.0, 0.0]
        for val, wa in ((swing, 'Y'), (lean, 'X'), (twist, 'Z')):
            if val:
                i, s = self.ax(bone, wa)
                rot[i] += R(val) * s
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = Euler(rot, 'XYZ')
        if loc is not None:
            pb.location = loc
        if scale is not None:
            pb.scale = scale if hasattr(scale, '__len__') else (scale, scale, scale)
        return pb

    def pose(self, frame, spec):
        """spec: {bone: dict(swing=, lean=, twist=, loc=, scale=)}"""
        for bone, kw in spec.items():
            self.set(bone, **kw)
        for bone in spec:
            pb = self.rig.pose.bones[bone]
            pb.keyframe_insert('rotation_euler', frame=frame)
            pb.keyframe_insert('location', frame=frame)
            pb.keyframe_insert('scale', frame=frame)


# ---------------------------------------------------------------- obj keys
def okey(ob, frame, loc=None, rot=None, scale=None, hide=None):
    if loc is not None:
        ob.location = loc
        ob.keyframe_insert('location', frame=frame)
    if rot is not None:
        ob.rotation_euler = Euler([R(a) for a in rot], 'XYZ')
        ob.keyframe_insert('rotation_euler', frame=frame)
    if scale is not None:
        ob.scale = scale if hasattr(scale, '__len__') else (scale, scale, scale)
        ob.keyframe_insert('scale', frame=frame)
    if hide is not None:
        ob.hide_render = hide
        ob.hide_viewport = hide
        ob.keyframe_insert('hide_render', frame=frame)
        ob.keyframe_insert('hide_viewport', frame=frame)


def ballistic(f0, f1, p0, p1, apex, n=7):
    """Sample a parabolic arc. Returns [(frame, (x,y,z)), ...].

    A jump keyed on two keys is a straight line in Z regardless of easing, and
    the brief fails it outright. Sampling the real parabola is 6 lines.
    """
    out = []
    for i in range(n):
        t = i / (n - 1)
        x = p0[0] + (p1[0] - p0[0]) * t
        y = p0[1] + (p1[1] - p0[1]) * t
        # parabola through z0 at t=0, z1 at t=1, peaking `apex` above the chord
        zc = p0[2] + (p1[2] - p0[2]) * t
        z = zc + apex * 4 * t * (1 - t)
        out.append((f0 + (f1 - f0) * t, (x, y, z)))
    return out


def ease_ch(ob, kind="snap", only_paths=None):
    """Apply forge-kit measured handle fractions to an object's own f-curves.
    forgekit.anim.stage_spline() does every curve; this one can target a subset."""
    out_f, in_f = A.EASE[kind]
    for f in A.fcurves(ob):
        if only_paths and f.data_path not in only_paths:
            continue
        pts = f.keyframe_points
        for i, k in enumerate(pts):
            k.interpolation = 'BEZIER'
            k.handle_left_type = 'FREE'
            k.handle_right_type = 'FREE'
            px = pts[i - 1].co.x if i > 0 else k.co.x - 8
            nx = pts[i + 1].co.x if i < len(pts) - 1 else k.co.x + 8
            k.handle_left = (k.co.x - (k.co.x - px) * max(in_f, 1e-3), k.co.y)
            k.handle_right = (k.co.x + (nx - k.co.x) * max(out_f, 1e-3), k.co.y)
        f.update()


def step_channels(ob, paths):
    """Force CONSTANT interpolation on named channels (visibility pops, X-eye
    reveals, size flicker). Bezier on a boolean reads as a fade."""
    for f in A.fcurves(ob):
        if f.data_path in paths:
            for k in f.keyframe_points:
                k.interpolation = 'CONSTANT'
            f.update()


def tri_count(objs=None):
    objs = objs or [o for o in bpy.context.scene.objects if o.type == 'MESH']
    dg = bpy.context.evaluated_depsgraph_get()
    total = 0
    for o in objs:
        me = o.evaluated_get(dg).data
        total += sum(len(p.vertices) - 2 for p in me.polygons)
    return total
