"""Smoothing / remeshing benchmark on a surface with known ground truth.

Truth  : the kit hand + forearm skin (subdivided quad cage), rest pose.
Damaged: voxel remesh at 2.5 mm (the stair-stepped blob every organic
         pipeline produces) plus 0.25 mm Gaussian normal noise.
Each fixer runs on the damaged mesh; we measure what it fixed (smoothness)
and what it destroyed (distance to truth, volume, thin-feature loss).

    python smooth_bench.py out.json
"""
import sys, os, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "..", "kit"))
SCRATCH_LIB = os.environ.get("FORM_PYLIB")       # optional: pynanoinstantmeshes location
if SCRATCH_LIB:
    sys.path.append(SCRATCH_LIB)
from forgekit import env
env.prepare()
import bpy, bmesh, numpy as np
from mathutils.bvhtree import BVHTree
import hand as H, formq as Q


def mesh_from(V, F, name):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in V], [], [tuple(int(i) for i in f) for f in F])
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    for p in me.polygons:
        p.use_smooth = True
    return ob


def neighbours(n, E):
    nb = [[] for _ in range(n)]
    for a, b in E:
        nb[a].append(b); nb[b].append(a)
    idx = np.concatenate([np.array(x, np.int64) for x in nb])
    ptr = np.concatenate([[0], np.cumsum([len(x) for x in nb])])
    return idx, ptr


def umbrella(V, idx, ptr):
    s = np.add.reduceat(V[idx], ptr[:-1], axis=0)
    cnt = (ptr[1:] - ptr[:-1])[:, None]
    return s / np.maximum(cnt, 1) - V


def laplacian(V, idx, ptr, lam=0.5, it=10):
    V = V.copy()
    for _ in range(it):
        V = V + lam * umbrella(V, idx, ptr)
    return V


def taubin(V, idx, ptr, lam=0.5, mu=-0.53, it=10):
    V = V.copy()
    for _ in range(it):
        V = V + lam * umbrella(V, idx, ptr)
        V = V + mu * umbrella(V, idx, ptr)
    return V


def hc(V0, idx, ptr, alpha=0.0, beta=0.5, it=10):
    """Vollmer, Mencl & Mueller 1999 HC-Laplacian: Laplacian step, then push
    each vertex back by a blend of its own and its neighbours' drift."""
    q = V0.copy()
    for _ in range(it):
        p = q + umbrella(q, idx, ptr)
        b = p - (alpha * V0 + (1 - alpha) * q)
        bn = np.add.reduceat(b[idx], ptr[:-1], axis=0) / np.maximum((ptr[1:] - ptr[:-1])[:, None], 1)
        q = p - (beta * b + (1 - beta) * bn)
    return q


def dist_stats(ob, truth_tree, truth_V):
    A = Q.arrays(ob, True)
    V, T = A["V"], A["T"]
    d1 = np.array([truth_tree.find_nearest(tuple(v))[3] for v in V])
    tree = BVHTree.FromPolygons([tuple(v) for v in V], [tuple(t) for t in T], all_triangles=True)
    d2 = np.array([tree.find_nearest(tuple(v))[3] for v in truth_V])
    return {"to_truth_mean_mm": round(1000 * float(d1.mean()), 3), "to_truth_p99_mm": round(1000 * float(np.percentile(d1, 99)), 3),
            "truth_to_mesh_p99_mm": round(1000 * float(np.percentile(d2, 99)), 3),
            "hausdorff_mm": round(1000 * float(max(d1.max(), d2.max())), 3),
            "volume_ratio": round(Q.volume(V, T) / TRUTH_VOL, 4)}


def score(ob, label, t):
    sm = Q.smoothness(ob)
    tp = Q.topology(ob, evaluated=True)
    r = {"method": label, "seconds": round(t, 3), "faces": tp["faces"], "quad_frac": tp["quad_frac"],
         "pole_frac": tp["pole_frac"], **{k: sm[k] for k in ("normal_dev_p99", "curv_jump_p95", "zebra_jump_p95")},
         **dist_stats(ob, TRUTH_TREE, TRUTH_V)}
    print(r, flush=True)
    return r


bpy.ops.wm.read_factory_settings(use_empty=True)
hb, hand, rig = H.build_hand()
hand.modifiers["Armature"].show_viewport = False
# truth as a triangle soup is fine for distances
A = Q.arrays(hand, True)
TV, TT = A["V"], A["T"]
TRUTH_V = TV
TRUTH_TREE = BVHTree.FromPolygons([tuple(v) for v in TV], [tuple(t) for t in TT], all_triangles=True)
TRUTH_VOL = Q.volume(TV, TT)
truth_ob = mesh_from(TV, TT, "Truth")
rows = [score(truth_ob, "truth (kit skin)", 0.0)]

# damaged: voxel remesh + noise
dmg = mesh_from(TV, TT, "Damaged")
rm = dmg.modifiers.new("rm", "REMESH"); rm.mode = "VOXEL"; rm.voxel_size = 0.0025
bpy.context.view_layer.objects.active = dmg
bpy.ops.object.modifier_apply(modifier="rm")
rng = np.random.default_rng(0)
D = Q.arrays(dmg, False)
DV, DT, DE = D["V"], D["T"], D["E"]
DF = Q._polys(D)
vn = Q.vertex_normals(DV, DT)
DVn = DV + vn * rng.normal(0, 0.00025, len(DV))[:, None]
damaged = mesh_from(DVn, DF, "Damaged+noise")
rows.append(score(damaged, "damaged: voxel 2.5mm + 0.25mm noise", 0.0))
idx, ptr = neighbours(len(DVn), DE)

for label, fn in [("laplacian x10 (lam .5)", lambda: laplacian(DVn, idx, ptr, 0.5, 10)),
                  ("laplacian x40", lambda: laplacian(DVn, idx, ptr, 0.5, 40)),
                  ("taubin x10 (.5/-.53)", lambda: taubin(DVn, idx, ptr, 0.5, -0.53, 10)),
                  ("taubin x40", lambda: taubin(DVn, idx, ptr, 0.5, -0.53, 40)),
                  ("HC x10 (a0 b.5)", lambda: hc(DVn, idx, ptr, 0.0, 0.5, 10)),
                  ("HC x40", lambda: hc(DVn, idx, ptr, 0.0, 0.5, 40))]:
    t = time.time(); V = fn(); t = time.time() - t
    rows.append(score(mesh_from(V, DF, label), label, t))

# Blender modifiers on the damaged mesh
def mod_run(label, setup):
    ob = mesh_from(DVn, DF, label)
    t = time.time()
    setup(ob)
    bpy.context.view_layer.objects.active = ob
    for m in list(ob.modifiers):
        bpy.ops.object.modifier_apply(modifier=m.name)
    t = time.time() - t
    rows.append(score(ob, label, t))
    return ob


def lap_mod(ob):
    m = ob.modifiers.new("ls", "LAPLACIANSMOOTH"); m.iterations = 10; m.lambda_factor = 0.8
    m.use_volume_preserve = True; m.use_normalized = True


def cs_mod(ob):
    m = ob.modifiers.new("cs", "CORRECTIVE_SMOOTH"); m.iterations = 20; m.factor = 0.5
    m.use_only_smooth = True; m.smooth_type = "LENGTH_WEIGHTED"


mod_run("blender LaplacianSmooth x10 lam.8 vol-preserve", lap_mod)
mod_run("blender CorrectiveSmooth only-smooth x20", cs_mod)

# QuadriFlow: retopology. It CANCELS silently (returns {'CANCELLED'}, no
# exception) on input with non-manifold vertices - which voxel remesh produces
# wherever two fingers come within a voxel of each other. Report that.
def nm_verts(ob):
    bm = bmesh.new(); bm.from_mesh(ob.data)
    n = sum(1 for v in bm.verts if not v.is_manifold)
    bm.free()
    return n


for faces in (4000, 12000):
    for src_label, src_V in (("damaged", DVn), ("taubin x10", taubin(DVn, idx, ptr, 0.5, -0.53, 10))):
        ob = mesh_from(src_V, DF, f"qf{faces}")
        bpy.ops.object.select_all(action="DESELECT"); ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        nmv = nm_verts(ob)
        t = time.time()
        r = bpy.ops.object.quadriflow_remesh("EXEC_DEFAULT", target_faces=faces, use_preserve_sharp=False,
                                             use_mesh_symmetry=False, smooth_normals=True, seed=0)
        t = time.time() - t
        if "FINISHED" not in r:
            rows.append({"method": f"quadriflow {faces} on {src_label}", "result": list(r), "non_manifold_verts": nmv})
            print("quadriflow", faces, src_label, r, "non-manifold verts:", nmv)
            continue
        ob = bpy.context.view_layer.objects.active
        rows.append(score(ob, f"quadriflow {faces} on {src_label}", t))
        ss = ob.modifiers.new("ss", "SUBSURF"); ss.levels = 1
        rows.append(score(ob, f"quadriflow {faces} on {src_label} + subsurf1", t))

# Instant Meshes (nanobind port) if importable
try:
    import pynanoinstantmeshes as PNIM
    for faces in (4000, 12000):
        t = time.time()
        Vq, Fq = PNIM.remesh(DVn.astype(np.float32), np.asarray(DT, np.uint32), faces, deterministic=True)
        t = time.time() - t
        Fq = np.asarray(Fq)
        Fq = [f[:3] if f[2] == f[3] else f for f in Fq.tolist()] if Fq.shape[1] == 4 else Fq
        ob = mesh_from(np.asarray(Vq, float), Fq, f"im{faces}")
        rows.append(score(ob, f"instant meshes {faces}", t))
        ss = ob.modifiers.new("ss", "SUBSURF"); ss.levels = 1
        rows.append(score(ob, f"instant meshes {faces} + subsurf1", t))
except Exception as e:
    rows.append({"method": "instant meshes", "error": repr(e)})
    print("instant meshes unavailable/failed:", repr(e))

json.dump(rows, open(sys.argv[-1], "w"), indent=1, default=float)
