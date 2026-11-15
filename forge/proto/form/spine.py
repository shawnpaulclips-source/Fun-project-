"""Spine deformation experiment: how many bones, FK vs B-bones, uniform vs
anatomical distribution of bend and twist.

A torso proxy (quad rings on the real S-curve, 0.56 m sacrum->C7, chest wider
and deeper than the waist) is skinned with closed-form hat weights along the
spine's arc length and posed through four extreme poses with the SAME total
rotation in every variant. Measured: volume, self-intersection, strain,
deformed-surface curvature continuity (kinks from too few joints) and the
minimum local volume along the spine (pinching on the inside of the bend).

    python spine.py out.json
"""
import sys, os, json, math, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "..", "kit"))
from forgekit import env
env.prepare()
import bpy, bmesh, numpy as np
from mathutils import Vector, Matrix, Quaternion
import formq as Q

H_SP = 0.56          # sacrum -> C7, metres
NR, NV = 36, 32      # rings, verts per ring


def s_curve(u):
    """Sagittal offset (Y, +back) of the spine at height fraction u: lumbar
    lordosis (concave back, apex ~L3, u~0.2) and thoracic kyphosis (convex,
    apex ~T7, u~0.62). Amplitudes ~2.5 cm, typical adult standing."""
    return -0.025 * math.sin(math.pi * min(u / 0.42, 1.0)) * (u < 0.42) + \
           0.028 * math.sin(math.pi * max(0.0, (u - 0.38) / 0.62)) * (u >= 0.38)


def torso_profile(u):
    """Half-width and half-depth (m) of the torso at height u: pelvis, waist
    (narrowest ~u=0.3), ribcage (widest ~u=0.7), shoulders."""
    w = 0.165 - 0.030 * math.exp(-((u - 0.30) / 0.12) ** 2) + 0.020 * math.exp(-((u - 0.72) / 0.16) ** 2)
    d = 0.105 - 0.012 * math.exp(-((u - 0.30) / 0.12) ** 2) + 0.018 * math.exp(-((u - 0.66) / 0.18) ** 2)
    return w, d


def build_torso():
    bm = bmesh.new()
    rings = []
    for i in range(NR):
        u = i / (NR - 1)
        z = u * H_SP
        w, d = torso_profile(u)
        yc = s_curve(u)
        ring = []
        for k in range(NV):
            a = 2 * math.pi * k / NV
            # spine sits at the back: the torso centre is in front of it
            ring.append(bm.verts.new((w * math.cos(a), yc - 0.6 * d + d * math.sin(a), z)))
        rings.append(ring)
    for i in range(NR - 1):
        for k in range(NV):
            bm.faces.new((rings[i][k], rings[i][(k + 1) % NV], rings[i + 1][(k + 1) % NV], rings[i + 1][k]))
    for r in (rings[0], rings[-1]):
        c = bm.verts.new(sum((v.co for v in r), Vector()) / NV)
        for k in range(NV):
            bm.faces.new((r[k], r[(k + 1) % NV], c))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("Torso")
    bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new("Torso", me)
    bpy.context.scene.collection.objects.link(ob)
    for p in me.polygons:
        p.use_smooth = True
    return ob


def build_spine(n, bbone_segments=1):
    arm = bpy.data.armatures.new(f"Spine{n}")
    rig = bpy.data.objects.new(f"Spine{n}", arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    prev = None
    for i in range(n):
        u0, u1 = i / n, (i + 1) / n
        b = arm.edit_bones.new(f"sp{i:02d}")
        b.head = (0, s_curve(u0), u0 * H_SP)
        b.tail = (0, s_curve(u1), u1 * H_SP)
        b.align_roll(Vector((0, -1, 0)))     # Z forward: +X rotation = forward flexion
        b.bbone_segments = bbone_segments
        if prev:
            b.parent = prev; b.use_connect = True
        prev = b
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in rig.pose.bones:
        pb.rotation_mode = "XYZ"
    return rig


def hat_weights(ob, rig, n, overlap=1.0):
    """Each vertex blends between the two nearest bone centres along height
    (piecewise-linear hats, width = overlap x segment). Partition of unity."""
    ob.vertex_groups.clear()
    groups = [ob.vertex_groups.new(name=f"sp{i:02d}") for i in range(n)]
    centres = (np.arange(n) + 0.5) / n
    for v in ob.data.vertices:
        u = min(max(v.co.z / H_SP, 0.0), 1.0)
        if n == 1:
            groups[0].add([v.index], 1.0, "REPLACE"); continue
        w = np.maximum(0.0, 1 - np.abs(u - centres) * n / overlap)
        if u <= centres[0]:
            w = np.zeros(n); w[0] = 1
        if u >= centres[-1]:
            w = np.zeros(n); w[-1] = 1
        w /= w.sum()
        for i in np.nonzero(w > 1e-4)[0]:
            groups[i].add([v.index], float(w[i]), "REPLACE")


# Per-level share of motion, sacrum->C7, 5 lumbar + 12 thoracic levels.
# Shapes follow the classic regional pattern (White & Panjabi): flexion is
# concentrated low in the lumbar spine, axial twist in the thoracic spine,
# lateral bend roughly even. Values recalled, not re-verified this session.
LEVELS = 17
FLEX = np.array([12, 13, 13, 16, 17] + [4] * 6 + [5] * 6, float)
TWIST = np.array([2, 2, 2, 2, 1] + [9, 8, 8, 8, 8, 8, 7, 7, 6, 6, 5, 4], float)
LAT = np.array([6, 7, 8, 8, 6] + [6] * 6 + [8] * 6, float)


def distribute(n, total, profile):
    """Split `total` degrees over n bones following a per-level profile."""
    if profile is None:
        return np.full(n, total / n)
    edges = np.linspace(0, LEVELS, n + 1)
    share = np.array([profile[int(math.floor(a)):int(math.ceil(b))].sum() for a, b in zip(edges[:-1], edges[1:])])
    return total * share / share.sum()


POSES = {"flex70": ("x", 70), "lateral40": ("z", 40), "twist60": ("y", 60), "flex45_twist45": (("x", 45), ("y", 45))}


def pose(rig, n, spec, anatomical):
    for pb in rig.pose.bones:
        pb.rotation_euler = (0, 0, 0)
    specs = spec if isinstance(spec[0], tuple) else (spec,)
    prof = {"x": FLEX, "y": TWIST, "z": LAT}
    for axis, total in specs:
        share = distribute(n, total, prof[axis] if anatomical else None)
        for i, pb in enumerate(rig.pose.bones):
            e = pb.rotation_euler
            setattr(e, axis, getattr(e, axis) + math.radians(share[i]))
    bpy.context.view_layer.update()


def run_variant(n, bseg=1, anatomical=False, dqs=False, subsurf=1):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ob = build_torso()
    rig = build_spine(n, bseg)
    hat_weights(ob, rig, n, overlap=1.0)
    ob.parent = rig
    am = ob.modifiers.new("Armature", "ARMATURE"); am.object = rig
    am.use_deform_preserve_volume = dqs
    if subsurf:
        ss = ob.modifiers.new("ss", "SUBSURF"); ss.levels = subsurf
    # slab regions along the spine, centred on the torso axis
    pts = {}
    for k in range(1, 6):
        u = k / 6
        w, d = torso_profile(u)
        pts[f"u{k}"] = (Vector((0, s_curve(u) - 0.6 * d, u * H_SP)), 1.1 * w)
    rest = Q.RestCache(ob, {k: (tuple(c), r) for k, (c, r) in pts.items()})
    # back midline (x = 0, posterior half), ordered by height, ends trimmed
    R = rest.V
    zc = np.clip(R[:, 2] / H_SP, 0, 1)
    ctr_y = np.array([s_curve(u) - 0.6 * torso_profile(u)[1] for u in zc])
    mid = np.nonzero((np.abs(R[:, 0]) < 1e-5) & (R[:, 1] > ctr_y) & (zc > 0.03) & (zc < 0.97))[0]
    mid = mid[np.argsort(R[mid, 2])]

    def kappa_of(P, plane):
        """Signed curvature (1/m) of the midline projected on a plane:
        'yz' sagittal (flexion), 'xz' coronal (lateral bend)."""
        seg = P[1:] - P[:-1]
        a = seg[:, 1] if plane == "yz" else seg[:, 0]
        th = np.unwrap(np.arctan2(a, seg[:, 2]))
        ln = np.linalg.norm(seg, axis=1)
        return (th[1:] - th[:-1]) / (0.5 * (ln[1:] + ln[:-1]))
    k_rest = {pl: kappa_of(R[mid], pl) for pl in ("yz", "xz")}
    rest_sm = Q.smoothness(ob)

    def posed_centre(c):
        u = min(max(c.z / H_SP, 0), 0.9999)
        pb = rig.pose.bones[min(int(u * n), n - 1)]
        M = rig.matrix_world @ pb.matrix @ pb.bone.matrix_local.inverted()
        return M @ c

    out = {}
    for name, spec in POSES.items():
        pose(rig, n, spec, anatomical)
        m = Q.deformation(ob, rest, {k: posed_centre(c) for k, (c, r) in pts.items()})
        sm = Q.smoothness(ob)
        P = Q.arrays(ob, True)["V"][mid]
        pl = "xz" if name.startswith("lateral") else "yz"
        dk = np.abs(kappa_of(P, pl) - k_rest[pl])   # added bending along the back, 1/m
        out[name] = {"back_dkappa_peak_ratio": round(float(dk.max() / max(dk.mean(), 1e-9)), 3),
                     "back_dkappa_max": round(float(dk.max()), 3),"volume_ratio": m["volume_ratio"], "self_intersections": m["self_intersections"],
                     "edge_strain_p99": m["edge_strain_p99"], "local_volume_min": m["joint_volume_min"],
                     "curv_jump_p95": sm["curv_jump_p95"], "normal_dev_p99": sm["normal_dev_p99"]}
    return {"bones": n, "bbone_segments": bseg, "anatomical": anatomical, "dqs": dqs,
            "rest_curv_jump_p95": rest_sm["curv_jump_p95"], "poses": out}


if __name__ == "__main__":
    t0 = time.time()
    rows = []
    for n, bseg, anat, dqs in [(1, 1, False, False), (2, 1, False, False), (3, 1, False, False),
                               (5, 1, False, False), (8, 1, False, False), (17, 1, False, False),
                               (3, 8, False, False), (5, 6, False, False),
                               (5, 1, True, False), (5, 6, True, False), (5, 6, True, True),
                               (8, 1, False, True)]:
        t = time.time()
        r = run_variant(n, bseg, anat, dqs)
        r["seconds"] = round(time.time() - t, 2)
        rows.append(r)
        p = r["poses"]
        print(f"n={n:2d} bseg={bseg} anat={int(anat)} dqs={int(dqs)} | " + " | ".join(
            f"{k}: vol {v['volume_ratio']:.3f} lv {v['local_volume_min']:.3f} si {v['self_intersections']} "
            f"dk {v['back_dkappa_peak_ratio']:.2f}/{v['back_dkappa_max']:.1f}" for k, v in p.items()), flush=True)
    print("total", round(time.time() - t0, 1))
    json.dump(rows, open(sys.argv[-1], "w"), indent=1, default=float)
