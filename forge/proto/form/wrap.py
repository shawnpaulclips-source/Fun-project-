"""Topology transfer: put the kit's loop-correct hand onto an arbitrary blob.

Target: a capsule + voxel-remesh hand (no edge flow, the kind of surface an
ML generator or a metaball body gives you) built with HIDDEN proportions.

  naive   default kit hand, Shrinkwrap(NEAREST_SURFACEPOINT) on the cage
  prior   CMA-ES fits the procedural parameters to the blob first (chamfer
          loss), so the topology lands on the right fingers
  prior+fit  then iterate "move each cage vertex by the residual of its LIMIT
          position" + tangential relax, so the SUBDIVIDED skin, not the cage,
          sits on the target. Vertex indices never change, so the rig and the
          analytic weights transfer for free - then we run the knuckle battery.

    python wrap.py out.json
"""
import sys, os, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "..", "kit"))
from forgekit import env
env.prepare()
import bpy, numpy as np
from mathutils.bvhtree import BVHTree
import hand as H, baseline_hand as B, formq as Q
from cmaes import CMAES

HIDDEN = {"scale": 1.05, "width_scale": 1.12, "thumb_abduct": 55.0, "finger_len": 0.93,
          "spread_mult": 2.0, "breadth": 1.06}
SPACE = {"scale": (0.85, 1.15), "width_scale": (0.80, 1.25), "thumb_abduct": (25.0, 65.0),
         "finger_len": (0.85, 1.15), "spread_mult": (0.0, 3.0), "breadth": (0.90, 1.15)}


def tree_of(V, T):
    return BVHTree.FromPolygons([tuple(v) for v in V], [tuple(t) for t in T], all_triangles=True)


def nearest(tree, P):
    out = np.empty_like(P); d = np.empty(len(P)); nrm = np.empty_like(P)
    for i, p in enumerate(P):
        loc, n, _, dist = tree.find_nearest(tuple(p))
        out[i] = loc; d[i] = dist; nrm[i] = n
    return out, d, nrm


bpy.ops.wm.read_factory_settings(use_empty=True)
hbT = H.HandBuilder(HIDDEN)
_, blob = B.build_baseline(voxel=0.0022, name="Blob", hb=hbT)
BA = Q.arrays(blob, False)
BV, BT = BA["V"], BA["T"]
btree = tree_of(BV, BT)
rng = np.random.default_rng(0)
BS = BV[rng.choice(len(BV), 3000, replace=False)]
# restrict comparisons to the hand (x > -0.03): the forearm is not the point
BS = BS[BS[:, 0] > -0.03]


def chamfer(V, T):
    m = V[:, 0] > -0.03
    _, d1, _ = nearest(btree, V[m][::3])
    t = tree_of(V, T)
    _, d2, _ = nearest(t, BS)
    return float(d1.mean() + d2.mean()), d1, d2


def skin_of(params):
    for ob in list(bpy.data.objects):
        if ob.name.startswith("Fit"):
            bpy.data.objects.remove(ob)
    hb = H.HandBuilder(params)
    ob = hb.build("FitHand")
    ss = ob.modifiers.new("ss", "SUBSURF"); ss.levels = 1
    A = Q.arrays(ob, True)
    return hb, ob, A["V"], A["T"]


def report(label, ob, extra=None):
    A = Q.arrays(ob, True)
    V, T = A["V"], A["T"]
    c, d1, d2 = chamfer(V, T)
    fn, _ = Q.face_normals(V, T)
    cen = V[T].mean(axis=1)
    m = cen[:, 0] > -0.03
    _, _, bn = nearest(btree, cen[m][::2])
    flipped = int((np.einsum("ij,ij->i", fn[m][::2], bn) < 0).sum())
    tp = Q.topology(ob, evaluated=False)
    r = {"method": label, "skin_to_blob_mean_mm": round(1000 * d1.mean(), 3), "skin_to_blob_p99_mm": round(1000 * np.percentile(d1, 99), 3),
         "blob_to_skin_mean_mm": round(1000 * d2.mean(), 3), "blob_to_skin_p99_mm": round(1000 * np.percentile(d2, 99), 3),
         "flipped_vs_target_frac": round(flipped / max(len(cen[m][::2]), 1), 4),
         "cage_quad_angle_dev_p95": tp["quad_angle_dev_p95"], "cage_poles": tp["poles"]}
    if extra:
        r.update(extra)
    print(r, flush=True)
    return r


rows = []
T0 = time.time()
# ---------------------------------------------------------------- naive
hb0, ob0, V0, T0_ = skin_of({})
rows.append(report("default kit hand, no wrap", ob0))
sw = ob0.modifiers.new("sw", "SHRINKWRAP"); sw.target = blob; sw.wrap_method = "NEAREST_SURFACEPOINT"
ob0.modifiers.move(len(ob0.modifiers) - 1, 0)          # shrinkwrap the cage, then subdivide
rows.append(report("naive: shrinkwrap cage (nearest surface point)", ob0))
ob0.modifiers.remove(sw)

# ---------------------------------------------------------------- prior fit
t = time.time()
def encode(p):
    return np.array([(p.get(k, H.DEFAULT.get(k, 1.0)) - lo) / (hi - lo) for k, (lo, hi) in SPACE.items()])
def decode(x):
    return {k: lo + float(v) * (hi - lo) for v, (k, (lo, hi)) in zip(x, SPACE.items())}
x0 = encode({"thumb_abduct": H.DEFAULT["thumb_abduct"], "spread_mult": 1.0})
opt = CMAES(x0, sigma0=0.2, seed=4)
while not opt.stop(max_evals=240, ftol=2e-6):
    X = opt.ask()
    opt.tell(X, [chamfer(*skin_of(decode(x))[2:])[0] for x in X])
fitted = decode(opt.best_x)
fit_s = time.time() - t
hb1, ob1, _, _ = skin_of(fitted)
rows.append(report("prior: CMA-ES param fit (chamfer)", ob1,
                   {"evals": opt.evals, "seconds": round(fit_s, 1),
                    "fitted": {k: round(v, 4) for k, v in fitted.items()}, "hidden": HIDDEN}))

# ---------------------------------------------------------------- prior + limit-surface fit
t = time.time()
me = ob1.data
n = len(me.vertices)
cage = np.array([v.co[:] for v in me.vertices])
E = np.array([e.vertices[:] for e in me.edges])
nb = [[] for _ in range(n)]
for a, b in E:
    nb[a].append(b); nb[b].append(a)
handmask = cage[:, 0] > -0.012
for it in range(6):
    ev = Q.arrays(ob1, True)["V"][:n]                 # OpenSubdiv keeps cage vertices first
    if it == 0:
        order_check = float(np.linalg.norm(ev - cage, axis=1).max())
    tgt, d, nrm = nearest(btree, ev)
    delta = (tgt - ev) * handmask[:, None]
    cage = cage + 0.85 * delta
    # tangential relax: move toward the neighbour average, but only in-plane
    avg = np.array([cage[nb[i]].mean(axis=0) for i in range(n)])
    lap = avg - cage
    vn = Q.vertex_normals(cage, Q.arrays(ob1, False)["T"])
    lap_t = lap - np.einsum("ij,ij->i", lap, vn)[:, None] * vn
    cage = cage + 0.35 * lap_t * handmask[:, None]
    me.vertices.foreach_set("co", cage.ravel())
    me.update()
fit2_s = time.time() - t
rows.append(report("prior + limit-surface residual fit x6 + relax", ob1,
                   {"seconds": round(fit2_s, 2), "cage_vs_limit_index_check_mm": round(order_check * 1000, 2)}))

# ---------------------------------------------------------------- does the rig still work?
hbR, obR, rigR = H.build_hand(fitted, name="Wrapped")
obR.data.vertices.foreach_set("co", cage.ravel()); obR.data.update()
J = {}
for d in H.DIGITS:
    w = hbR.D[d]["w"]
    for nm, b in (("MCP", f"{d}.01"), ("PIP", f"{d}.02"), ("DIP", f"{d}.03")):
        J[f"{d}_{nm}"] = (b, (0.62 if nm == "MCP" else 0.55) * w)
cen = lambda: {j: rigR.matrix_world @ rigR.pose.bones[b].head for j, (b, r) in J.items()}
c0 = cen()
rest = Q.RestCache(obR, {j: (tuple(c0[j]), J[j][1]) for j in J})
jv, si = [], []
for j, (b, r) in J.items():
    H.reset(rigR); H.set_flex(rigR, b, 90); bpy.context.view_layer.update()
    m = Q.deformation(obR, rest, cen())
    jv.append(m["joint_volume"][j]); si.append(m["self_intersections"])
rows.append({"method": "wrapped mesh + transferred rig: 12 finger joints at 90 deg",
             "joint_volume_min": round(min(jv), 4), "joint_volume_mean": round(float(np.mean(jv)), 4),
             "self_intersections_mean": round(float(np.mean(si)), 1)})
print(rows[-1])
rows.append({"total_s": round(time.time() - T0, 1)})
json.dump(rows, open(sys.argv[-1], "w"), indent=1, default=float)

if True:   # three Workbench stills, ~0.3 s total
    from forgekit import render as RN
    rigR.hide_render = True
    for o in bpy.data.objects:
        if o.type == "MESH" and o not in (obR, blob):
            o.hide_render = True
    RN.setup_world(color=(0.08, 0.08, 0.09))
    RN.workbench(shading="STUDIO", color_type="SINGLE", outline=False, cavity=True)
    bpy.context.scene.display.shading.single_color = (0.75, 0.6, 0.52)
    H.reset(rigR)
    out = os.path.dirname(sys.argv[-1])
    RN.camera(lens=50, target=(0.09, 0, 0), location=(0.30, -0.30, 0.30))
    obR.hide_render = True
    RN.shot(os.path.join(out, "wrap_target_blob"), resolution=(360, 360), verbose=False)
    obR.hide_render = False; blob.hide_render = True
    RN.shot(os.path.join(out, "wrap_result"), resolution=(360, 360), verbose=False)
    H.apply_pose(rigR, H.POSES["fist"])
    RN.shot(os.path.join(out, "wrap_result_fist"), resolution=(360, 360), verbose=False)
