"""CPU-only optimiser-driven shape fitting with CMA-ES. Three real problems:

  silhouette  recover 6 hidden hand parameters from two target silhouettes
              (top + side), loss = 1 - mean IoU, numpy point-splat raster
  canon       fit cage parameters so the SUBDIVIDED skin hits tape-measure
              anthropometric targets (subdivision shrink makes this a
              non-obvious inverse problem)
  skin        fit analytic-weight blend parameters against the 90-degree
              knuckle battery (volume loss + self-intersection)

    python fit_hand.py <problem> <out.json>
"""
import sys, os, json, time, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "..", "kit"))
from forgekit import env
env.prepare()
import bpy, numpy as np
import hand as H, formq as Q
from cmaes import CMAES

PROBLEM = sys.argv[-2]
OUT = sys.argv[-1]


def clear():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)
    for a in list(bpy.data.armatures):
        bpy.data.armatures.remove(a)


def build_skin(params, levels=2):
    hb = H.HandBuilder(params)
    ob = hb.build("FitHand")
    ss = ob.modifiers.new("ss", "SUBSURF"); ss.levels = levels
    return hb, ob


# ------------------------------------------------------------- silhouettes
RES = 128
WIN = {"top": ((-0.02, 0.23), (-0.125, 0.125)), "side": ((-0.02, 0.23), (-0.125, 0.125))}


def silhouette(V, view):
    (x0, x1), (y0, y1) = WIN[view]
    u = V[:, 0]
    v = V[:, 1] if view == "top" else V[:, 2]
    i = ((u - x0) / (x1 - x0) * RES).astype(int)
    j = ((v - y0) / (y1 - y0) * RES).astype(int)
    m = (i >= 0) & (i < RES) & (j >= 0) & (j < RES)
    img = np.zeros((RES, RES), bool)
    img[j[m], i[m]] = True
    # 3x3 dilation then erosion closes splat gaps without growing the outline
    p = np.pad(img, 1)
    dil = np.zeros_like(img)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            dil |= p[dy:dy + RES, dx:dx + RES]
    p = np.pad(dil, 1, constant_values=True)
    ero = np.ones_like(img)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            ero &= p[dy:dy + RES, dx:dx + RES]
    return ero


def iou(a, b):
    return (a & b).sum() / max((a | b).sum(), 1)


def skin_verts(params):
    clear()
    hb, ob = build_skin(params, levels=2)
    V = Q.arrays(ob, True)["V"]
    return hb, ob, V


SIL_SPACE = {  # name: (lo, hi)
    "scale": (0.85, 1.15), "width_scale": (0.80, 1.25), "thumb_abduct": (25.0, 65.0),
    "finger_len": (0.85, 1.15), "spread_mult": (0.0, 3.0), "breadth": (0.90, 1.15),
}
SIL_TRUTH = {"scale": 1.05, "width_scale": 1.12, "thumb_abduct": 55.0,
             "finger_len": 0.93, "spread_mult": 2.0, "breadth": 1.06}


def decode(x, space):
    return {k: lo + float(xi) * (hi - lo) for xi, (k, (lo, hi)) in zip(x, space.items())}


def encode(p, space):
    return np.array([(p[k] - lo) / (hi - lo) for k, (lo, hi) in space.items()])


def run_silhouette():
    _, _, Vt = skin_verts(SIL_TRUTH)
    tgt = {v: silhouette(Vt, v) for v in ("top", "side")}
    t_eval = []

    def loss(x):
        t = time.time()
        _, _, V = skin_verts(decode(x, SIL_SPACE))
        l = 1 - 0.5 * (iou(silhouette(V, "top"), tgt["top"]) + iou(silhouette(V, "side"), tgt["side"]))
        t_eval.append(time.time() - t)
        return l

    x0 = encode({"scale": 1.0, "width_scale": 1.0, "thumb_abduct": 42.0, "finger_len": 1.0,
                 "spread_mult": 1.0, "breadth": 1.0}, SIL_SPACE)
    l0 = loss(x0)
    opt = CMAES(x0, sigma0=0.2, seed=1)
    T0 = time.time()
    first = {}
    while not opt.stop(max_evals=360, ftol=2e-4):
        X = opt.ask()
        opt.tell(X, [loss(x) for x in X])
        for thr in (0.05, 0.03, 0.02):
            if opt.best_f < thr and thr not in first:
                first[thr] = (opt.evals, round(time.time() - T0, 1))
    best = decode(opt.best_x, SIL_SPACE)
    rel = {k: round(abs(best[k] - SIL_TRUTH[k]) / (SIL_SPACE[k][1] - SIL_SPACE[k][0]), 4) for k in best}
    # workbench silhouette cost for comparison
    sys.path.insert(0, os.path.join(HERE, "..", "..", "kit"))
    from forgekit import render as RN
    clear()
    build_skin(SIL_TRUTH)
    RN.workbench(shading="FLAT", color_type="SINGLE", outline=False, cavity=False)
    RN.camera(lens=50, target=(0.1, 0, 0), location=(0.1, 0, 0.6))
    import tempfile
    tt = time.time()
    RN.shot(os.path.join(tempfile.gettempdir(), "sil_cost"), resolution=(128, 128), verbose=False)
    wb = time.time() - tt
    return {"problem": "silhouette", "start_loss": round(l0, 4), "best_loss": round(opt.best_f, 4),
            "best_mean_iou": round(1 - opt.best_f, 4), "evals": opt.evals, "gens": opt.gen,
            "wall_s": round(time.time() - T0, 1), "eval_ms_median": round(1000 * float(np.median(t_eval)), 1),
            "first_below": {str(k): v for k, v in first.items()},
            "truth": SIL_TRUTH, "best": {k: round(v, 4) for k, v in best.items()},
            "param_err_frac_of_range": rel, "workbench_128px_render_s": round(wb, 3),
            "history": opt.history}


CANON_SPACE = {"scale": (0.9, 1.1), "cc_comp": (1.0, 1.25), "width_scale": (0.9, 1.2),
               "finger_len": (0.95, 1.1), "breadth": (0.95, 1.1), "palm_thick": (24.0, 34.0)}


def run_canon():
    tgt = H.canon_targets()
    t_eval = []

    def meas(p):
        clear()
        hb, ob = build_skin(p, levels=2)
        return H.measure_hand(hb, ob)

    def loss(x):
        t = time.time()
        e, _ = H.proportion_error(meas(decode(x, CANON_SPACE)), tgt)
        t_eval.append(time.time() - t)
        return e

    p0 = {k: H.DEFAULT[k] for k in CANON_SPACE}
    x0 = encode(p0, CANON_SPACE)
    l0 = loss(x0)
    opt = CMAES(x0, sigma0=0.15, seed=2)
    T0 = time.time()
    while not opt.stop(max_evals=300, ftol=1e-5):
        X = opt.ask()
        opt.tell(X, [loss(x) for x in X])
    best = decode(opt.best_x, CANON_SPACE)
    m = meas(best)
    e, errs = H.proportion_error(m, tgt)
    return {"problem": "canon", "start_err": round(l0, 4), "best_err": round(e, 4), "evals": opt.evals,
            "wall_s": round(time.time() - T0, 1), "eval_ms_median": round(1000 * float(np.median(t_eval)), 1),
            "start": p0, "best": {k: round(v, 4) for k, v in best.items()},
            "measured": {k: round(v, 2) for k, v in m.items()}, "target": {k: round(v, 2) for k, v in tgt.items()},
            "per_measure_err": {k: round(v, 4) for k, v in errs.items()}, "history": opt.history}


SKIN_SPACE = {"blend_scale": (0.3, 1.8), "blend_asym": (-0.5, 1.0), "blend_shift": (0.0, 1.2)}


def run_skin():
    t_eval = []

    def evaluate(q):
        clear()
        params = {"blend": tuple(q["blend_scale"] * b for b in (0.55, 0.42, 0.36)),
                  "blend_asym": q["blend_asym"], "blend_shift": q["blend_shift"]}
        hb, ob, rig = H.build_hand(params)
        J = {}
        for d in ("index", "middle", "little"):
            w = hb.D[d]["w"]
            for nm, b in (("MCP", f"{d}.01"), ("PIP", f"{d}.02"), ("DIP", f"{d}.03")):
                J[f"{d}_{nm}"] = (b, (0.62 if nm == "MCP" else 0.55) * w)
        J["thumb_MCP"] = ("thumb.02", 0.6 * hb.T["w"])
        cen = lambda: {j: rig.matrix_world @ rig.pose.bones[b].head for j, (b, r) in J.items()}
        c0 = cen()
        rest = Q.RestCache(ob, {j: (tuple(c0[j]), J[j][1]) for j in J})
        jv, si, st = [], [], []
        for j, (b, r) in J.items():
            H.reset(rig); H.set_flex(rig, b, 90); bpy.context.view_layer.update()
            m = Q.deformation(ob, rest, cen())
            jv.append(m["joint_volume"][j]); si.append(m["self_intersections"]); st.append(m["edge_strain_p99"])
        return min(jv), float(np.mean(jv)), float(np.mean(si)), max(st)

    def loss(x):
        t = time.time()
        jmin, jmean, si, st = evaluate(decode(x, SKIN_SPACE))
        t_eval.append(time.time() - t)
        return (1 - jmin) + 0.5 * (1 - jmean) + si / 200.0 + max(0.0, st - 0.30)

    x0 = encode({"blend_scale": 1.0, "blend_asym": 0.0, "blend_shift": 0.0}, SKIN_SPACE)
    l0 = loss(x0)
    start = evaluate(decode(x0, SKIN_SPACE))
    opt = CMAES(x0, sigma0=0.25, seed=3)
    T0 = time.time()
    while not opt.stop(max_evals=70, ftol=1e-3):
        X = opt.ask()
        opt.tell(X, [loss(x) for x in X])
    best = decode(opt.best_x, SKIN_SPACE)
    jmin, jmean, si, st = evaluate(best)
    return {"problem": "skin", "start_loss": round(l0, 4), "best_loss": round(opt.best_f, 4),
            "start": {"jv_min": round(start[0], 4), "jv_mean": round(start[1], 4), "si_mean": start[2], "strain": round(start[3], 4)},
            "best_metrics": {"jv_min": round(jmin, 4), "jv_mean": round(jmean, 4), "si_mean": si, "strain": round(st, 4)},
            "evals": opt.evals, "wall_s": round(time.time() - T0, 1),
            "eval_ms_median": round(1000 * float(np.median(t_eval)), 1),
            "best": {k: round(v, 4) for k, v in best.items()}, "history": opt.history}


bpy.ops.wm.read_factory_settings(use_empty=True)
res = {"silhouette": run_silhouette, "canon": run_canon, "skin": run_skin}[PROBLEM]()
json.dump(res, open(OUT, "w"), indent=1, default=float)
print(json.dumps({k: v for k, v in res.items() if k != "history"}, default=float))
