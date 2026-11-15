"""Sweep the analytic-weight blend parameters against the 90-degree knuckle
battery. Shows the kit tuning its own skinning from a blind metric.

    python sweep_weights.py out.json
"""
import sys, os, json, time, math, itertools
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "..", "kit"))
from forgekit import env
env.prepare()
import bpy, numpy as np
import hand as H, formq as Q

def evaluate(params, mode):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    kw = {"lbs": dict(corrective=False, preserve_volume=False),
          "dqs": dict(corrective=False, preserve_volume=True),
          "lbs_cs": dict(corrective=True, preserve_volume=False),
          "dqs_cs": dict(corrective=True, preserve_volume=True)}[mode]
    hb, ob, rig = H.build_hand(params, **kw)
    J = {}
    for d in ("index", "middle", "little"):
        w = hb.D[d]["w"]
        for nm, b in (("MCP", f"{d}.01"), ("PIP", f"{d}.02"), ("DIP", f"{d}.03")):
            J[f"{d}_{nm}"] = (b, (0.62 if nm == "MCP" else 0.55) * w)
    J["thumb_MCP"] = ("thumb.02", 0.6 * hb.T["w"])
    def centres():
        return {j: rig.matrix_world @ rig.pose.bones[b].head for j, (b, r) in J.items()}
    c0 = centres()
    rest = Q.RestCache(ob, {j: (tuple(c0[j]), J[j][1]) for j in J})
    jv, si, st = [], [], []
    for j, (b, r) in J.items():
        H.reset(rig)
        H.set_flex(rig, b, 90)
        bpy.context.view_layer.update()
        m = Q.deformation(ob, rest, centres())
        jv.append(m["joint_volume"][j]); si.append(m["self_intersections"]); st.append(m["edge_strain_p99"])
    return {"jv_min": round(min(jv), 4), "jv_mean": round(float(np.mean(jv)), 4),
            "si_mean": round(float(np.mean(si)), 1), "strain_max": round(max(st), 4)}

rows = []
t0 = time.time()
for scale, asym, shift, mode in itertools.product((0.35, 0.6, 1.0), (0.0, 0.5), (0.0, 0.5), ("lbs", "dqs")):
    p = {"blend": tuple(scale * b for b in (0.55, 0.42, 0.36)), "blend_asym": asym, "blend_shift": shift}
    r = evaluate(p, mode)
    r.update(scale=scale, asym=asym, shift=shift, mode=mode)
    rows.append(r)
    print(r, flush=True)
for mode in ("lbs_cs", "dqs_cs"):
    r = evaluate({}, mode); r.update(scale=1.0, asym=0, shift=0, mode=mode); rows.append(r); print(r)
print("total", round(time.time() - t0, 1), "s")
json.dump(rows, open(sys.argv[-1], "w"), indent=1)
