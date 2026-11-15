"""A/B: kit hand vs raw-bpy hand, same anatomy, full blind battery.

    python ab_hand.py <arm> <outdir>
    arm: kit | kit_lbs | kit_cs | kit_heat | base | base_dqs

kit       procedural cage + analytic weights + dual-quaternion skinning + subsurf 2
kit_lbs   same, linear blend skinning                (attribution)
kit_cs    kit + corrective smooth                    (attribution: CS hurts here)
kit_heat  procedural cage, bone-heat weights, DQS    (attribution: topology alone)
kit_fit   kit with the CMA-ES-fitted blend parameters
base      capsules + voxel remesh + default roll + bone heat + LBS   (control)
base_dqs  control with dual-quaternion skinning      (is it just the skinning mode?)
"""
import sys, os, json, time, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "kit"))
from forgekit import env
env.prepare()
import bpy
import numpy as np
from mathutils import Vector
import hand as H, baseline_hand as B, formq as Q

ARM = sys.argv[-2]
OUT = sys.argv[-1]
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
t0 = time.time()
timing = {}


def genus(ob):
    A = Q.arrays(ob, False)
    V, E, F = len(A["V"]), len(A["E"]), len(A["poly_sizes"])
    return (2 - (V - E + F)) // 2


# ------------------------------------------------------------------ build
ref_hb = H.HandBuilder()
ref_ob = ref_hb.build("RefHand")
ref_rig = H.build_rig(ref_hb, "RefRig")
AXES = H.flexion_axes(ref_rig)          # anatomically correct flexion axes, world
bpy.data.objects.remove(ref_ob)
bpy.data.objects.remove(ref_rig)

if ARM.startswith("kit"):
    kw = dict(corrective=False, preserve_volume=True)
    weights = "analytic"
    if ARM == "kit_lbs":
        kw = dict(corrective=False, preserve_volume=False)
    elif ARM == "kit_cs":
        kw = dict(corrective=True, preserve_volume=True)
    elif ARM == "kit_heat":
        weights = "heat"
    params = {}
    if ARM == "kit_fit":      # blend parameters found by fit_hand.py skin (CMA-ES, 70 evals)
        params = {"blend": (1.8 * 0.55, 1.8 * 0.42, 1.8 * 0.36), "blend_asym": -0.3131, "blend_shift": 0.0}
    t = time.time()
    hb, ob, rig = H.build_hand(params, weights=weights, **kw)
    timing["build_s"] = round(time.time() - t, 3)
    drive_axes = None
else:
    t = time.time()
    chosen = None
    for vx in (0.0030, 0.0026, 0.0023, 0.0020, 0.0018, 0.0016, 0.0014):
        hb, ob = B.build_baseline(voxel=vx)
        g = genus(ob)
        if g == 0:
            chosen = vx
            break
        bpy.data.objects.remove(ob)
    rig = B.build_baseline_rig(hb)
    B.bind_auto(ob, rig)
    if ARM == "base_dqs":
        ob.modifiers["Armature"].use_deform_preserve_volume = True
    timing["build_s"] = round(time.time() - t, 3)
    timing["voxel_m"] = chosen
    drive_axes = AXES

bpy.context.view_layer.update()

# roll audit: does +X on each digit bone flex toward the palm?
roll_ok = 0
roll_total = 0
for b in rig.data.bones:
    if b.name in AXES and (b.name.split(".")[0] in H.DIGITS + ("thumb",) or b.name.startswith("mc_")):
        roll_total += 1
        x = (rig.matrix_world.to_3x3() @ b.matrix_local.to_3x3()) @ Vector((1, 0, 0))
        roll_ok += int(x.dot(AXES[b.name]) > 0.9)

# ------------------------------------------------------------------ joints
JOINTS = {}
for d in H.DIGITS:
    w = hb.D[d]["w"]
    for nm, bone in (("MCP", f"{d}.01"), ("PIP", f"{d}.02"), ("DIP", f"{d}.03")):
        JOINTS[f"{d}_{nm}"] = (bone, 0.62 * w if nm == "MCP" else 0.55 * w)
JOINTS["thumb_MCP"] = ("thumb.02", 0.6 * hb.T["w"])
JOINTS["thumb_IP"] = ("thumb.03", 0.55 * hb.T["w"])
JOINTS["wrist"] = ("hand", 0.028)
FA_MID = Vector((-0.5 * hb.p["forearm_len"] * hb.s, 0, 0))


def joint_centres():
    out = {}
    for j, (bone, r) in JOINTS.items():
        pb = rig.pose.bones[bone]
        out[j] = rig.matrix_world @ pb.head
    out["forearm_mid"] = FA_MID
    return out


def rest_cache():
    jc = joint_centres()
    spec = {j: (tuple(jc[j]), JOINTS[j][1]) for j in JOINTS}
    spec["forearm_mid"] = (tuple(FA_MID), 0.045)
    return Q.RestCache(ob, spec)


H.reset(rig)
t = time.time()
rest = rest_cache()
topo_cage = Q.topology(ob, evaluated=False)
topo = Q.topology(ob, evaluated=True)
smooth = Q.smoothness(ob)
timing["rest_metrics_s"] = round(time.time() - t, 3)


def pose_and_measure(apply):
    H.reset(rig)
    apply()
    bpy.context.view_layer.update()
    return Q.deformation(ob, rest, joint_centres())


def flex(bone, deg, spread=0.0):
    H.set_flex(rig, bone, deg, spread, axes=drive_axes)


# ------------------------------------------------------------------ battery
t = time.time()
per_joint = {}
for j, (bone, r) in JOINTS.items():
    if j == "wrist":
        continue
    m = pose_and_measure(lambda b=bone: flex(b, 90))
    per_joint[j] = {"joint_volume": m["joint_volume"][j], "self_intersections": m["self_intersections"],
                    "volume_ratio": m["volume_ratio"], "edge_strain_p99": m["edge_strain_p99"],
                    "collapsed_faces": m["collapsed_faces"]}


def full_curl():
    for d in H.DIGITS:
        for k in (1, 2, 3):
            flex(f"{d}.0{k}", 90)
    flex("thumb.02", 60); flex("thumb.03", 60)


curl_all = pose_and_measure(full_curl)

poses = {}
for pname, pose in H.POSES.items():
    poses[pname] = pose_and_measure(lambda p=pose: H.apply_pose(rig, p, axes=drive_axes))
    # crease formation: how much sharper did the surface get than at rest?
    sm_p = Q.smoothness(ob)
    poses[pname]["normal_dev_p99"] = sm_p["normal_dev_p99"]
    poses[pname]["curv_jump_p95"] = sm_p["curv_jump_p95"]


def twist():
    pb = rig.pose.bones["hand"]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = (0, math.radians(90), 0)   # hand's own long axis: pronation


def wrist_flex():
    flex("hand", 70) if drive_axes is None else H.set_flex(rig, "hand", 70, axes=drive_axes)


tw = pose_and_measure(twist)
wf = pose_and_measure(wrist_flex)
timing["battery_s"] = round(time.time() - t, 3)
H.reset(rig)

# ------------------------------------------------------------------ proportions
props = H.measure_hand(hb, ob)
TARGET = H.canon_targets()
prop_err, perr = H.proportion_error(props, TARGET)

# ------------------------------------------------------------------ rubric
jv = [v["joint_volume"] for v in per_joint.values() if v["joint_volume"] is not None]
flat = {
    "quad_frac": topo["quad_frac"],
    "pole_frac": topo["pole_frac"],
    "quad_angle_dev_p95": topo["quad_angle_dev_p95"],
    "normal_dev_p99": smooth["normal_dev_p99"],
    "curv_jump_p95": smooth["curv_jump_p95"],
    "zebra_jump_p95": smooth["zebra_jump_p95"],
    "knuckle_isect_mean": float(np.mean([v["self_intersections"] for v in per_joint.values()])),
    "full_curl_isect": curl_all["self_intersections"],
    "curl_joint_vol_min": min(jv),
    "curl_vol_ratio_min": min(v["volume_ratio"] for v in per_joint.values()),
    "curl_strain_p99_max": max(v["edge_strain_p99"] for v in per_joint.values()),
    "twist_vol_min": min(tw["joint_volume"]["wrist"], tw["joint_volume"]["forearm_mid"]),
    "proportion_err": round(prop_err, 4),
    "pose_battery_isect": float(np.mean([p["self_intersections"] for p in poses.values()])),
    "pose_crease_gain": float(np.mean([p["normal_dev_p99"] for p in poses.values()]) - smooth["normal_dev_p99"]),
}
score, sub = Q.rubric(flat)
result = {
    "arm": ARM, "score": score, "subscores": sub, "flat": flat,
    "topology_cage": topo_cage, "topology": topo, "smoothness": smooth,
    "roll_correct": f"{roll_ok}/{roll_total}",
    "per_joint_90": per_joint, "curl_all": curl_all, "poses": poses,
    "twist_90": tw, "wrist_flex_70": wf,
    "proportions": {k: round(v, 2) if v else v for k, v in props.items()},
    "proportion_target": {k: round(v, 2) for k, v in TARGET.items()},
    "timing": timing, "total_s": round(time.time() - t0, 2),
}
with open(os.path.join(OUT, f"ab_{ARM}.json"), "w") as f:
    json.dump(result, f, indent=1, default=float)
print(json.dumps({k: result[k] for k in ("arm", "score", "flat", "roll_correct", "timing", "total_s")}, default=float))

# ------------------------------------------------------------------ pictures
if "--render" in sys.argv or ARM in ("kit", "kit_fit", "base"):
    from forgekit import render as RN
    rig.hide_render = True
    RN.setup_world(color=(0.08, 0.08, 0.09))
    RN.workbench(shading="STUDIO", color_type="SINGLE", outline=False, cavity=True)
    bpy.context.scene.display.shading.single_color = (0.75, 0.6, 0.52)
    shots = [("rest", None, (0.32, -0.30, 0.30)), ("fist", "fist", (0.30, -0.32, 0.12)),
             ("fist_palm", "fist", (0.16, -0.06, -0.40)), ("point", "point", (0.30, -0.32, 0.20)),
             ("twist", "twist", (0.05, -0.42, 0.18)), ("curl_close", "curl", (0.17, -0.16, -0.05))]
    for nm, pz, loc in shots:
        H.reset(rig)
        if pz == "twist":
            twist()
        elif pz == "curl":
            full_curl()
        elif pz:
            H.apply_pose(rig, H.POSES[pz], axes=drive_axes)
        bpy.context.view_layer.update()
        tgt = (0.02, 0, 0) if pz == "twist" else (0.09, 0, -0.01)
        RN.camera(lens=50, target=tgt, location=loc)
        RN.shot(os.path.join(OUT, f"{ARM}_{nm}"), resolution=(360, 360), verbose=False)
