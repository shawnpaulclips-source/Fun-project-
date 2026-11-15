"""Animation metric fix, foot planting (treadmill + root motion) and glTF clip export.

Run:  /home/user/bpyenv/bin/python tests/test_anim.py [work_dir]
Builds the rigged character (examples/02) and the walk (examples/03) into
work_dir if they are not there, then tests on the polished walk.
Prints PASS/FAIL per check; exits 1 on any failure.
"""
import os, sys, time, json, subprocess, tempfile

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from forgekit import env
env.prepare()
import bpy
from mathutils import Vector
from forgekit import anim as A, rig as RIG

WORK = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="forge_test_anim_")
os.makedirs(WORK, exist_ok=True)
FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def run(script):
    t = time.time()
    r = subprocess.run([sys.executable, os.path.join(KIT, "examples", script), WORK],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit(f"{script} failed")
    return round(time.time() - t, 1)


# ------------------------------------------------------------- pure-math units
pts = [Vector((0, 0.06 * i, 0.07)) for i in range(8)] + [Vector((0, 0.4 - 0.05 * i, 0.3)) for i in range(16)]
belt, lows = A.stance_velocity(pts)
check("stance_velocity recovers a constant treadmill speed", abs(belt.y - 0.06) < 1e-6, f"belt={tuple(belt)}")
runs, b2 = A.contact_frames([p.z for p in pts], [p.xy for p in pts], None)
check("contact_frames finds the low, belt-speed run", runs and runs[0][0] == 0 and len(runs[0]) == 8,
      f"runs={runs} belt={tuple(round(v, 4) for v in b2)}")
check("_runs wraps across the cycle seam", A._runs([1, 0, 0, 1, 1], True) == [[3, 4, 0]], str(A._runs([1, 0, 0, 1, 1], True)))

# ------------------------------------------------------------- build the walk
if not os.path.exists(os.path.join(WORK, "character.blend")):
    print(f"building character: {run('02_rig_character.py')} s")
ck = os.path.join(WORK, "ck", "001_splined.blend")
if not os.path.exists(ck):
    print(f"building walk: {run('03_walk_cycle.py')} s")


def load_polished():
    bpy.ops.wm.open_mainfile(filepath=ck)
    RIG.enable_rigify()
    rig = RIG.find_rig()
    A.close_loop(rig, 1, 24)
    A.soften_handles(rig, 0.55)
    A.decimate(rig, error=0.02)
    body = next(o for o in bpy.context.scene.objects if o.type == "MESH" and o.find_armature() == rig) \
        if any(o.type == "MESH" and o.find_armature() == rig for o in bpy.context.scene.objects) \
        else next(o for o in bpy.context.scene.objects if o.type == "MESH")
    return rig, body


TRACK = ["DEF-foot.L", "DEF-foot.R", "head"]
CONTACT = ["DEF-foot.L", "DEF-foot.R"]
FEET = ["foot_fk.L", "foot_fk.R"]

rig, body = load_polished()
check("leg_chain resolves FK chain by geometry",
      RIG.leg_chain(rig, "foot_fk.L") == ("thigh_fk.L", "shin_fk.L", "foot_fk.L"),
      str(RIG.leg_chain(rig, "foot_fk.L")))
check("root_bone", RIG.root_bone(rig) == "root")

# ------------------------------------------------------------- metric fix
solo = A.metrics(rig, ["DEF-foot.L"], 1, 24)["DEF-foot.L"]["jerk_max"]
dup = A.metrics(rig, ["DEF-foot.L"], 1, 24, contact_bones=["DEF-foot.L"])["DEF-foot.L"]["jerk_max"]
check("metrics: a bone in both lists is sampled once (jerk unchanged)", abs(solo - dup) < 1e-9,
      f"solo={solo} dup={dup}")
raw = A.metrics(rig, TRACK, 1, 24, contact_bones=CONTACT, expected_root_speed=0.0)
legacy = A.metrics(rig, TRACK, 1, 24, contact_bones=CONTACT,
                   expected_root_speed=raw["DEF-foot.L"]["path_length_m"] / 24)
auto = A.metrics(rig, TRACK, 1, 24, contact_bones=CONTACT, expected_root_speed="auto")
print(f"      foot_slide on polished in-place walk: raw={raw['DEF-foot.L']['foot_slide_m']} "
      f"legacy(path/24)={legacy['DEF-foot.L']['foot_slide_m']} auto={auto['DEF-foot.L']['foot_slide_m']} "
      f"stance_speed={auto['DEF-foot.L']['stance_speed_m_per_frame']}")
check("auto stance speed is the treadmill speed, not path/24",
      0.03 < auto["DEF-foot.L"]["stance_speed_m_per_frame"] < 0.1
      and abs(auto["DEF-foot.L"]["stance_speed_m_per_frame"] - raw["DEF-foot.L"]["path_length_m"] / 24) > 0.001)
pre_jerk = auto["DEF-foot.L"]["jerk_max"]

# ------------------------------------------------------------- treadmill
t = time.time()
rep = A.plant_feet(rig, FEET, 1, 24, mode="treadmill", swing="smooth")
dt_t = time.time() - t
m = A.metrics(rig, TRACK, 1, 24, contact_bones=CONTACT)
crit = A.critique(m)
print("      treadmill:", json.dumps({k: rep[k] for k in ("belt_m_per_frame", "contacts",
      "reach_clamped_frames", "reach_clamped_contact_frames", "knee_side_fixed", "residual")}))
check("plant_feet treadmill: contacts found on both feet",
      all(rep["contact_frames"][f] >= 4 for f in FEET), str(rep["contacts"]))
check("plant_feet treadmill: deform ankle lands on target during contact (<1 mm)",
      all(rep["residual"][f]["contact_max_err_m"] < 0.001 for f in FEET),
      str({f: rep["residual"][f]["contact_max_err_m"] for f in FEET}))
check("treadmill foot_slide (auto) <= 0.010 m",
      all(m[b]["foot_slide_m"] <= 0.010 for b in CONTACT),
      str({b: m[b]["foot_slide_m"] for b in CONTACT}))
check("treadmill foot jerk <= 0.08", all(m[b]["jerk_max"] <= 0.08 for b in CONTACT),
      f"before={pre_jerk} after={ {b: m[b]['jerk_max'] for b in CONTACT} }")
check("treadmill loop closes", all(m[b]["loop_seam_error_m"] <= 0.001 for b in CONTACT))
check("treadmill critique score 100", crit["score"] == 100, f"score={crit['score']} {crit['fails'] + crit['warns']} ({dt_t:.2f}s)")

# ------------------------------------------------------------- root motion
rig, body = load_polished()
t = time.time()
rep = A.plant_feet(rig, FEET, 1, 24, mode="root", swing="smooth")
dt_r = time.time() - t
m = A.metrics(rig, TRACK, 1, 24, contact_bones=CONTACT, root_bone="root")
w = A.metrics(rig, CONTACT, 1, 24, contact_bones=CONTACT, expected_root_speed=0.0)
crit = A.critique(m)
check("root mode: root travels one stride per cycle", rep["root_travel_m"] > 0.5,
      f"travel={rep['root_travel_m']} m belt={rep['belt_m_per_frame']}")
check("root mode: RAW world-space foot_slide <= 0.010 m (feet truly planted)",
      all(w[b]["foot_slide_m"] <= 0.010 for b in CONTACT), str({b: w[b]["foot_slide_m"] for b in CONTACT}))
check("root mode: foot jerk <= 0.08", all(m[b]["jerk_max"] <= 0.08 for b in CONTACT),
      str({b: m[b]["jerk_max"] for b in CONTACT}))
check("root mode: seam closes relative to root", all(m[b]["loop_seam_error_m"] <= 0.001 for b in TRACK))
check("root mode critique score 100", crit["score"] == 100, f"score={crit['score']} {crit['fails'] + crit['warns']} ({dt_r:.2f}s)")

# ------------------------------------------------------------- export
glb = os.path.join(WORK, "walk_root.glb")
t = time.time()
ex = A.export_clip_gltf(rig, body, glb, 1, 24, clip_name="walk_root")
print("      export:", json.dumps(ex))
an = ex["animations"]
check("export: exactly one named clip", len(an) == 1 and an[0]["name"] == "walk_root", str([a["name"] for a in an]))
check("export: deform-only skeleton", 100 <= ex["joints"] <= 200, f"joints={ex['joints']}")
check("export: clip spans frames 1-24 only (no duplicated seam frame)", abs(an[0]["duration_s"] - 23 / 24) < 1e-3 and an[0]["start_s"] == 0.0,
      f"start={an[0]['start_s']} duration={an[0]['duration_s']}")
hips = an[0]["hips_travel"]
check("export: root motion survives on the deform hips",
      bool(hips) and all(abs(v - rep["root_travel_m"] * 23 / 24) < 0.02 for v in hips.values()),
      f"{hips} vs expected {rep['root_travel_m'] * 23 / 24:.4f} ({time.time() - t:.2f}s)")

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
