"""Rigging: skeletons, skinning, and blind validation.

THE CENTRAL DECISION: use Rigify, not a learned auto-rigger.

Rigify ships inside the bpy wheel and generates headless. Measured here:
    159-bone human metarig -> rigify_generate() ->
    706 bones, 533 constraints, 114 drivers, 23 bone collections, 6.38 s on 4 CPU cores.

That is a production control rig - IK/FK switching, foot rolls, finger drivers,
a face rig - for free. Every learned auto-rigger (UniRig, RigNet, RigAnything)
hands you a bare skeleton plus weights and leaves you to build the control rig
yourself, and most carry non-commercial licences.

A metarig is just an armature whose pose bones carry `rigify_type` strings.
Move its bones to match your mesh and you inherit the entire control rig.
"""
import bpy, bmesh, math, os
from mathutils import Vector, Matrix

R = math.radians

# Blender's symmetry ops, Rigify, and vertex_group_mirror ALL require .L/.R.
# Unreal wants _l/_r. Author internally in Blender convention and rename only
# at export - and when you rename, you must hit bone names, vertex groups,
# constraint subtargets AND driver bone_targets in lockstep or you ship a rig
# that looks correct and deforms nothing.
BLENDER_SUFFIX = (".L", ".R")
UNREAL_SUFFIX = ("_l", "_r")


def enable_rigify():
    """Must be called AFTER read_factory_settings() - that call resets addons."""
    import addon_utils
    mods = [m.__name__ for m in addon_utils.modules() if "rigify" in m.__name__.lower()]
    if not mods:
        raise RuntimeError("rigify not bundled in this bpy build")
    bpy.ops.preferences.addon_enable(module=mods[0])
    return mods[0]


def add_human_metarig():
    bpy.ops.object.armature_human_metarig_add()
    return bpy.context.object


def fit_metarig(meta, target_height=None, mesh=None, align_feet_to_zero=True):
    """Scale and place the metarig to match a mesh's bounding box.

    This is the cheap 80% of auto-rigging. The expensive 20% - per-limb landmark
    fitting - only matters if your character is far from human proportion.
    Returns the applied scale.
    """
    if mesh is not None:
        lo = Vector((1e9, 1e9, 1e9)); hi = Vector((-1e9, -1e9, -1e9))
        for c in mesh.bound_box:
            w = mesh.matrix_world @ Vector(c)
            lo = Vector(min(lo[i], w[i]) for i in range(3))
            hi = Vector(max(hi[i], w[i]) for i in range(3))
        target_height = hi.z - lo.z
        foot_z = lo.z
        center_xy = ((hi.x + lo.x) / 2, (hi.y + lo.y) / 2)
    else:
        foot_z = 0.0
        center_xy = (0.0, 0.0)

    if target_height:
        cur = meta.dimensions.z
        s = target_height / cur if cur else 1.0
        meta.scale = (s, s, s)
    else:
        s = 1.0
    if align_feet_to_zero:
        bpy.context.view_layer.update()
        lo_z = min((meta.matrix_world @ Vector(c)).z for c in meta.bound_box)
        meta.location.z += foot_z - lo_z
        meta.location.x += center_xy[0]
        meta.location.y += center_xy[1]
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action="DESELECT")
    meta.select_set(True)
    bpy.context.view_layer.objects.active = meta
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return s


def generate(meta, hide_metarig=True):
    """Run Rigify. Returns the generated rig object.

    The metarig SURVIVES generation and stays in the scene, so any later code
    that grabs 'the first armature' gets the metarig instead of the rig. That
    silently animates a control-less skeleton. Use find_rig() to re-acquire it.
    """
    bpy.context.view_layer.objects.active = meta
    meta.select_set(True)
    bpy.ops.pose.rigify_generate()
    rig = bpy.context.object
    if hide_metarig:
        meta.hide_viewport = True
        meta.hide_render = True
    return rig


def find_rig(scene=None):
    """Re-acquire the GENERATED rig from a scene holding both armatures.

    Identifies it by Rigify's mechanism-bone prefix, falling back to bone count.
    Never just take the first armature you find.
    """
    scene = scene or bpy.context.scene
    arms = [o for o in scene.objects if o.type == "ARMATURE"]
    if not arms:
        raise RuntimeError("no armature in scene")
    generated = [a for a in arms
                 if any(b.name.startswith("MCH-") for b in a.data.bones)]
    if generated:
        return max(generated, key=lambda a: len(a.data.bones))
    return max(arms, key=lambda a: len(a.data.bones))


def bind(mesh, rig, method="ARMATURE_AUTO", normalize=True, limit=4):
    """Skin a mesh to a rig.

    method:
      ARMATURE_AUTO  - bone heat. Default. ~0.6 s on 11k verts.
                       FAILS SILENTLY on non-manifold meshes.
      ARMATURE_ENVELOPE - envelope falloff. Crude; use only when heat fails.
      ARMATURE_NAME  - match existing vertex groups by name, no computation.

    Bone heat does NOT produce an exact partition of unity - measured sums run
    0.979 to 1.000 - so always normalize afterwards.
    """
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.parent_set(type=method)

    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    if limit:
        # glTF supports at most 4 influences per vertex. Exceeding it means the
        # exporter silently drops the smallest, changing your deformation.
        bpy.ops.object.vertex_group_limit_total(limit=limit)
    if normalize:
        bpy.ops.object.vertex_group_normalize_all(lock_active=False)
    return mesh


def weight_stats(mesh, rig=None):
    """Blind checks on skinning quality. Run this before you ever render."""
    me = mesh.data
    groups = {g.index: g.name for g in mesh.vertex_groups}
    deform_names = set()
    if rig:
        deform_names = {b.name for b in rig.data.bones if b.use_deform}

    unweighted = 0
    over_limit = 0
    sums = []
    per_bone = {n: 0 for n in groups.values()}
    for v in me.vertices:
        gs = [g for g in v.groups if g.weight > 1e-5]
        if not gs:
            unweighted += 1
            continue
        if len(gs) > 4:
            over_limit += 1
        s = sum(g.weight for g in gs)
        sums.append(s)
        for g in gs:
            n = groups.get(g.group)
            if n:
                per_bone[n] += 1
    empty_groups = [n for n, c in per_bone.items() if c == 0]
    return {
        "vertices": len(me.vertices),
        "vertex_groups": len(groups),
        "unweighted_vertices": unweighted,
        "vertices_over_4_influences": over_limit,
        "weight_sum_min": round(min(sums), 5) if sums else None,
        "weight_sum_max": round(max(sums), 5) if sums else None,
        "empty_vertex_groups": len(empty_groups),
        "empty_group_names": empty_groups[:12],
        "deform_bones": len(deform_names) if rig else None,
    }


def weights_to_vertex_color(mesh, mode="argmax", bone=None, attr="fk_weights"):
    """Bake skin weights into a colour attribute for visual inspection.

    mode='argmax'  one hue per owning bone - shows the whole weight partition
                   in a single image. The fastest way to spot a bone that has
                   claimed the wrong limb.
    mode='single'  blue->red ramp of one bone's influence. Shows falloff quality.

    Render it with render.workbench(shading='FLAT', color_type='VERTEX').
    Measured: 400x400 PNG in 0.87 s, no GPU.
    """
    import colorsys
    me = mesh.data
    if attr in me.color_attributes:
        me.color_attributes.remove(me.color_attributes[attr])
    ca = me.color_attributes.new(name=attr, type="FLOAT_COLOR", domain="POINT")

    groups = {g.index: g.name for g in mesh.vertex_groups}
    names = sorted(set(groups.values()))
    hue = {n: (i * 0.618033988749895) % 1.0 for i, n in enumerate(names)}
    target = mesh.vertex_groups.get(bone).index if (bone and mesh.vertex_groups.get(bone)) else None

    for i, v in enumerate(me.vertices):
        if mode == "single":
            w = 0.0
            for g in v.groups:
                if g.group == target:
                    w = g.weight
            ca.data[i].color = (w, 0.08 + 0.15 * (1 - w), 1.0 - w, 1.0)
        else:
            best, bw = None, 0.0
            for g in v.groups:
                if g.weight > bw:
                    bw, best = g.weight, groups.get(g.group)
            if best is None:
                ca.data[i].color = (0.05, 0.05, 0.05, 1.0)   # unweighted = near black
            else:
                r, g_, b = colorsys.hsv_to_rgb(hue[best], 0.78, 0.35 + 0.65 * bw)
                ca.data[i].color = (r, g_, b, 1.0)
    me.color_attributes.active_color = ca
    return attr


POSE_BATTERY = [
    # (bone substring, axis, degrees) - extreme poses that expose bad weights.
    ("upper_arm", "Z", 95),
    ("forearm", "Z", -120),
    ("thigh", "X", -85),
    ("shin", "X", 115),
    ("spine", "X", 35),
    ("head", "Z", 70),
]


def _pose_targets(rig, substr):
    """Pick the bones to rotate for a pose-battery entry.

    On a Rigify rig the DEF- bones are constrained to follow the control chain,
    so rotating them directly does NOTHING - the test would report a perfect
    joint that was never bent. Prefer FK controls, then any unprefixed control,
    and only fall back to deform bones on a plain (non-Rigify) armature.
    """
    skip = ("mch-", "org-", "def-", "wgt-", "vis_")
    cands = [pb for pb in rig.pose.bones if substr in pb.name.lower()]
    fk = [pb for pb in cands if "_fk" in pb.name.lower()]
    if fk:
        return fk
    ctrl = [pb for pb in cands if not pb.name.lower().startswith(skip)
            and "_ik" not in pb.name.lower() and "tweak" not in pb.name.lower()]
    plain = [pb for pb in ctrl if not pb.constraints]
    if plain:
        return plain
    return [pb for pb in cands if pb.bone.use_deform and not pb.constraints]


def pose_test(rig, mesh, poses=None, joint_radius=0.6):
    """Drive the rig through extreme poses and measure mesh damage PER JOINT.

    Whole-mesh volume is the wrong number: measured on a hand, it stayed >= 0.994
    while a single knuckle folded completely inside out. A pinch is local, so this
    reports the local volume of the surface patch around each bent joint (a cone
    from the joint centre, formq.deformation) as well as the whole-mesh figures.

    joint_volume_min < ~0.90  the joint is pinching
    joint_volume_min < 0      the joint has folded through itself
    self_intersections        rises monotonically with a bad bend

    Rigify limbs are switched to FK for the test and restored afterwards.
    """
    from . import formq as Q
    poses = poses or POSE_BATTERY
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="OBJECT")

    saved_switch = {b: rig.pose.bones[b][k] for b, k in ik_fk_switches(rig).items()}
    set_ik_fk(rig, 1.0)

    # joint centres = head of each bone we will bend, in world space at rest.
    # Patch radius must reach the skin: a spine bone sits ~15 cm inside a torso
    # but is only ~10 cm long, so a length-only radius captures no faces at all.
    import numpy as np
    V0 = Q.arrays(mesh, True)["V"]
    plan = []
    joints = {}
    for substr, axis, deg in poses:
        targets = _pose_targets(rig, substr)
        plan.append((substr, axis, deg, targets))
        for pb in targets:
            head = np.asarray(rig.matrix_world @ pb.head)
            skin = float(np.sqrt(((V0 - head) ** 2).sum(axis=1)).min()) if len(V0) else 0.0
            joints[pb.name] = (tuple(head), max(pb.bone.length * joint_radius, skin * 1.4, 1e-4))
    bpy.context.view_layer.update()
    rest = Q.RestCache(mesh, joints=joints)

    bpy.ops.object.mode_set(mode="POSE")
    results = []
    for substr, axis, deg, targets in plan:
        tag = f"{substr}:{axis}{deg}"
        if not targets:
            results.append({"pose": tag, "skipped": "no poseable bone"})
            continue
        saved = [(pb, pb.rotation_mode, tuple(pb.rotation_euler), tuple(pb.rotation_quaternion))
                 for pb in targets]
        for pb in targets:
            pb.rotation_mode = "XYZ"
            setattr(pb.rotation_euler, axis.lower(), R(deg))
        bpy.context.view_layer.update()
        centres = {pb.name: tuple(rig.matrix_world @ pb.head) for pb in targets}
        d = Q.deformation(mesh, rest, joint_centres=centres)
        jv = {k: v for k, v in (d.get("joint_volume") or {}).items() if k in centres}
        vals = [v for v in jv.values() if v is not None]
        results.append({
            "pose": tag,
            "bones": [pb.name for pb in targets],
            "joint_volume": jv,
            "joint_volume_min": round(min(vals), 4) if vals else None,
            "volume_ratio": d["volume_ratio"],
            "self_intersections": d["self_intersections"],
            "edge_strain_p99": d["edge_strain_p99"],
            "verdict": ("FOLDED" if vals and min(vals) < 0 else
                        "PINCH" if vals and min(vals) < 0.90 else "ok"),
        })
        for pb, mode, eul, quat in saved:
            pb.rotation_mode = mode
            pb.rotation_euler = eul
            pb.rotation_quaternion = quat
        bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode="OBJECT")
    for b, v in saved_switch.items():
        rig.pose.bones[b][ik_fk_switches(rig)[b]] = v
    return results


def ik_fk_switches(rig):
    """Find every IK/FK blend property on a Rigify rig.

    Returns {bone_name: property_name}. Rigify stores the switch as a custom
    property on the limb's parent control, e.g. thigh_parent.L["IK_FK"].
    """
    found = {}
    for pb in rig.pose.bones:
        for key in pb.keys():
            if key.upper().replace("-", "_") in ("IK_FK", "IKFK", "FK_IK"):
                found[pb.name] = key
    return found


def set_ik_fk(rig, value=1.0, limbs=None):
    """Switch limbs between IK and FK. **1.0 = FK, 0.0 = IK.**

    THIS IS THE TRAP THAT EATS A WHOLE AFTERNOON: a freshly generated Rigify rig
    has its limbs in IK. Keying the FK controls (thigh_fk.L, upper_arm_fk.L...)
    then does NOTHING - the deform bones follow the IK chain and your character
    stands perfectly still while your F-curves fill up with keys.

    Call this with 1.0 before any FK animation, or key the *_ik controls instead.
    """
    switches = ik_fk_switches(rig)
    touched = {}
    for bone, key in switches.items():
        if limbs and not any(l in bone for l in limbs):
            continue
        rig.pose.bones[bone][key] = value
        touched[bone] = value
    bpy.context.view_layer.update()
    return touched


def deform_only(rig):
    """Names of bones that actually export. Rigify's 706 bones become ~160."""
    return [b.name for b in rig.data.bones if b.use_deform]


def rename_convention(rig, mesh=None, to="unreal"):
    """Swap .L/.R <-> _l/_r across bones, vertex groups and constraint subtargets.

    Miss any one of those three and the rig looks right and deforms nothing.
    """
    src, dst = (BLENDER_SUFFIX, UNREAL_SUFFIX) if to == "unreal" else (UNREAL_SUFFIX, BLENDER_SUFFIX)
    def conv(n):
        for a, b in zip(src, dst):
            if n.endswith(a):
                return n[: -len(a)] + b
        return n
    mapping = {b.name: conv(b.name) for b in rig.data.bones}
    for pb in rig.pose.bones:
        for c in pb.constraints:
            if getattr(c, "subtarget", None) in mapping:
                c.subtarget = mapping[c.subtarget]
    for b in rig.data.bones:
        b.name = mapping[b.name]
    if mesh:
        for g in mesh.vertex_groups:
            if g.name in mapping:
                g.name = mapping[g.name]
    return mapping


def manifest(rig, mesh=None):
    """The contract the animation layer reads.

    Animation must NEVER pattern-match a bone name. It resolves everything
    through this document, so a rig built on any naming convention works.
    """
    bones = {b.name: {
        "parent": b.parent.name if b.parent else None,
        "deform": b.use_deform,
        "length_m": round(b.length, 5),
        "head": tuple(round(v, 5) for v in b.head_local),
        "tail": tuple(round(v, 5) for v in b.tail_local),
    } for b in rig.data.bones}

    def find(*subs, controls_only=True):
        # Rigify prefixes mechanism/original/deform bones. A control bone has
        # no prefix, and that is what an animator keys.
        skip = ("mch-", "org-", "def-", "wgt-", "vis_")
        for b in rig.data.bones:
            n = b.name.lower()
            if controls_only and n.startswith(skip):
                continue
            if all(s in n for s in subs):
                return b.name
        return None

    mirror = {}
    for n in bones:
        if n.endswith(".L") and n[:-2] + ".R" in bones:
            mirror[n] = n[:-2] + ".R"

    return {
        "rig": rig.name,
        "mesh": mesh.name if mesh else None,
        "naming_convention": "blender" if any(n.endswith(".L") for n in bones) else "other",
        "bone_count": len(bones),
        "deform_bones": deform_only(rig),
        "root_motion_bone": find("root") or rig.data.bones[0].name,
        "cog_bone": find("torso") or find("hips") or find("spine"),
        "chains": {
            "arm_L": [n for n in ("upper_arm_fk.L", "forearm_fk.L", "hand_fk.L") if n in bones],
            "arm_R": [n for n in ("upper_arm_fk.R", "forearm_fk.R", "hand_fk.R") if n in bones],
            "leg_L": [n for n in ("thigh_fk.L", "shin_fk.L", "foot_fk.L") if n in bones],
            "leg_R": [n for n in ("thigh_fk.R", "shin_fk.R", "foot_fk.R") if n in bones],
        },
        "ik_controls": [n for n in bones if "_ik" in n or n.endswith("_ik")],
        "mirror_map": mirror,
        "rotation_modes": {pb.name: pb.rotation_mode for pb in rig.pose.bones},
        "keyable": [pb.name for pb in rig.pose.bones
                    if not pb.bone.hide and pb.bone.use_deform is False] or list(bones),
        "bone_lengths_m": {n: v["length_m"] for n, v in bones.items()},
    }


def export(mesh, rig, filepath, fmt="GLB", deform_only_bones=True):
    """Export with the four flags that silently ruin assets if left default."""
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    if fmt.upper() in ("GLB", "GLTF"):
        bpy.ops.export_scene.gltf(
            filepath=filepath,
            export_format="GLB" if fmt.upper() == "GLB" else "GLTF_SEPARATE",
            use_selection=True,
            export_apply=True,
            export_skins=True,
            export_def_bones=deform_only_bones,   # 706 bones -> ~160
            export_influence_nb=4,                # glTF hard limit
            export_force_sampling=True,           # mandatory on constrained rigs
        )
    else:
        bpy.ops.export_scene.fbx(
            filepath=filepath,
            use_selection=True,
            add_leaf_bones=False,                 # default True BREAKS UE import
            bake_anim_use_nla_strips=False,
            bake_anim_use_all_actions=False,
        )
    return filepath


# --------------------------------------------------------------- leg chains
_MECH_PREFIX = ("MCH-", "ORG-", "DEF-", "WGT-", "VIS_")


def leg_chain(rig, foot_bone, tol=None):
    """Resolve (thigh, shin, foot) control names for a foot control.

    Works by geometry, not by name: the shin is the nearest non-mechanism
    ancestor whose TAIL sits on the foot's head (the ankle), and the thigh is
    the nearest non-mechanism ancestor of the shin whose tail sits on the
    shin's head (the knee). On Rigify that walks
        foot_fk.L -> MCH-foot_fk.L -> shin_fk.L -> thigh_fk.L
    skipping the MCH- bone between foot and shin. On a plain armature it is
    simply foot -> parent -> grandparent. Raises if the chain cannot be found.
    """
    bones = rig.data.bones
    foot = bones.get(foot_bone)
    if foot is None:
        raise KeyError(f"no bone {foot_bone!r} on {rig.name}")
    tol = tol or max(1e-4, foot.length * 0.05)

    def ancestor_ending_at(b, point):
        p = b.parent
        while p is not None:
            if not p.name.startswith(_MECH_PREFIX) and (p.tail_local - point).length <= tol:
                return p
            p = p.parent
        return None

    shin = ancestor_ending_at(foot, foot.head_local)
    if shin is None:
        raise RuntimeError(f"{foot_bone}: no ancestor ends at the ankle; pass chains= explicitly")
    thigh = ancestor_ending_at(shin, shin.head_local)
    if thigh is None:
        raise RuntimeError(f"{shin.name}: no ancestor ends at the knee; pass chains= explicitly")
    return thigh.name, shin.name, foot.name


def root_bone(rig):
    """The bone that carries root motion: 'root' if present, else the
    parentless bone with the most descendants."""
    if "root" in rig.data.bones:
        return "root"
    tops = [b for b in rig.data.bones if b.parent is None]
    if not tops:
        raise RuntimeError("armature has no bones")
    return max(tops, key=lambda b: len(b.children_recursive)).name
