"""Author a walk cycle the way an animator does: block, spline, offset, critique.

This is the loop that matters. An agent cannot see motion, so it blocks key
poses from known craft numbers, splines them, measures the result, and reads a
scored report naming the fix as an actual function call.

Williams' 24-frame walk (2 steps at 24 fps) key frames:
    contact 1, 13, 25   down 4, 16   passing 7, 19   up 10, 22

Run:  python examples/03_walk_cycle.py out/      (needs out/character.blend)
"""
import sys, os, math, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from forgekit import env
env.prepare()
import bpy
from forgekit import anim as A, render as RN, inspect as INS, rig as RIG, checkpoint as CK

OUT = sys.argv[-1] if len(sys.argv) > 1 else "out"
os.makedirs(OUT, exist_ok=True)

blend = os.path.join(OUT, "character.blend")
if not os.path.exists(blend):
    raise SystemExit("run 02_rig_character.py first")
bpy.ops.wm.open_mainfile(filepath=blend)
RIG.enable_rigify()

rig = RIG.find_rig()   # NOT "the first armature" - the metarig is still there
body = next(o for o in bpy.context.scene.objects if o.type == "MESH")
sc = bpy.context.scene
sc.render.fps = 24
sc.frame_start, sc.frame_end = 1, 24

def pick(*candidates):
    """Resolve a control bone by trying known Rigify names. NEVER hard-code a
    single name - resolve, and fail loudly if the rig does not have it."""
    for c in candidates:
        if c in rig.pose.bones:
            return c
    return None

B = {
    "torso":   pick("torso", "spine_fk", "spine"),
    "chest":   pick("chest", "spine_fk.003", "spine_fk.002", "spine.002"),
    "head":    pick("head", "head_fk", "spine.006"),
    "thigh_L": pick("thigh_fk.L", "thigh.L"),
    "shin_L":  pick("shin_fk.L", "shin.L"),
    "foot_L":  pick("foot_fk.L", "foot.L"),
    "thigh_R": pick("thigh_fk.R", "thigh.R"),
    "shin_R":  pick("shin_fk.R", "shin.R"),
    "foot_R":  pick("foot_fk.R", "foot.R"),
    "uarm_L":  pick("upper_arm_fk.L", "upper_arm.L"),
    "farm_L":  pick("forearm_fk.L", "forearm.L"),
    "uarm_R":  pick("upper_arm_fk.R", "upper_arm.R"),
    "farm_R":  pick("forearm_fk.R", "forearm.R"),
}
missing = [k for k, v in B.items() if v is None]
print(f"rig: {rig.name}, {len(rig.data.bones)} bones, resolved "
      f"{len(B)-len(missing)}/{len(B)} controls")
if missing:
    raise SystemExit(f"unresolved controls {missing}; available controls: "
                     + str([b.name for b in rig.pose.bones
                            if not b.name.startswith(('MCH-','ORG-','DEF-','WGT-','VIS_'))][:40]))

bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="POSE")

# A fresh Rigify rig is in IK. Keying FK controls while the limb is in IK keys
# nothing visible - the character stands still and the curves fill with data.
switched = RIG.set_ik_fk(rig, 1.0)
print(f"IK/FK switches set to FK: {list(switched)}")

# ---------------------------------------------------------------- key poses
# Degrees. Contact = legs split, opposite arm forward. Passing = legs together,
# support leg straight, free knee bent, body at its highest.
def leg(thigh, shin, foot, t, s, f):
    return {B[thigh]: {"rot": (t, 0, 0)}, B[shin]: {"rot": (s, 0, 0)},
            B[foot]: {"rot": (f, 0, 0)}}

def arm(upper, fore, u, f):
    return {B[upper]: {"rot": (u, 0, 0)}, B[fore]: {"rot": (f, 0, 0)}}

def merge(*ds):
    out = {}
    for d in ds:
        out.update({k: v for k, v in d.items() if k is not None})
    return out

CONTACT_L = merge(
    leg("thigh_L", "shin_L", "foot_L", 26, -8, 6),
    leg("thigh_R", "shin_R", "foot_R", -24, 18, -10),
    arm("uarm_L", "farm_L", -22, -14),
    arm("uarm_R", "farm_R", 24, -20),
    {B["torso"]: {"rot": (2, 0, 0)}, B["chest"]: {"rot": (-1, 0, 3)}},
)
DOWN_L = merge(
    leg("thigh_L", "shin_L", "foot_L", 14, -16, 2),
    leg("thigh_R", "shin_R", "foot_R", -14, 34, -16),
    arm("uarm_L", "farm_L", -14, -10),
    arm("uarm_R", "farm_R", 15, -16),
    {B["torso"]: {"rot": (4, 0, 0)}},
)
PASSING_L = merge(
    leg("thigh_L", "shin_L", "foot_L", -2, -4, 0),
    leg("thigh_R", "shin_R", "foot_R", 6, 52, -22),
    arm("uarm_L", "farm_L", -4, -8),
    arm("uarm_R", "farm_R", 4, -12),
    {B["torso"]: {"rot": (1, 0, 0)}},
)
UP_L = merge(
    leg("thigh_L", "shin_L", "foot_L", -14, -2, -8),
    leg("thigh_R", "shin_R", "foot_R", 20, 30, -12),
    arm("uarm_L", "farm_L", 6, -8),
    arm("uarm_R", "farm_R", -6, -14),
    {B["torso"]: {"rot": (0, 0, 0)}},
)

man = RIG.manifest(rig, body)
mirror = man["mirror_map"]

# One stride authored, the second mirrored. Frame 25 repeats frame 1 so the
# cycle closes; it is keyed then trimmed to 24 by the scene range.
SEQUENCE = [
    (1,  CONTACT_L), (4,  DOWN_L), (7,  PASSING_L), (10, UP_L),
    (13, A.mirror_pose(CONTACT_L, mirror)), (16, A.mirror_pose(DOWN_L, mirror)),
    (19, A.mirror_pose(PASSING_L, mirror)), (22, A.mirror_pose(UP_L, mirror)),
    (25, CONTACT_L),
]

# ---------------------------------------------------------------- BLOCK
for frame, pose in SEQUENCE:
    A.apply_pose(rig, pose, frame=frame)
A.stage_block(rig)
keyed = sum(len(f.keyframe_points) for f in A.fcurves(rig))
print(f"blocked: {len(SEQUENCE)} poses, {len(A.fcurves(rig))} curves, {keyed} keys")
bpy.ops.object.mode_set(mode="OBJECT")
CK.checkpoint(os.path.join(OUT, "ck"), "blocked")

# ---------------------------------------------------------------- SPLINE
A.stage_spline(rig, ease="smooth")
A.cycle(rig)
print("splined + cycled")

# ---------------------------------------------------------------- OFFSET
# Overlap: hips lead, chest follows, head last. Two frames per link.
chain = [b for b in (B["torso"], B["chest"], B["head"]) if b]
A.offset_chain(rig, chain, frames_per_bone=2.0)
print(f"offset chain {chain} by 2 frames per link")
CK.checkpoint(os.path.join(OUT, "ck"), "splined")

# ---------------------------------------------------------------- CRITIQUE
# Measure the DEFORM bones. The FK controls move whether or not the limb is
# actually in FK, so measuring them hides the most common animation failure.
tracked = [A.deform_name(rig, b)
           for b in (B["foot_L"], B["foot_R"], B["head"], B["torso"]) if b]
contacts = [A.deform_name(rig, b) for b in (B["foot_L"], B["foot_R"]) if b]
print("measuring deform bones:", tracked)
m = A.metrics(rig, tracked, 1, 24, contact_bones=contacts)
for bone, vals in m.items():
    print(f"  {bone:16} " + "  ".join(f"{k}={v}" for k, v in vals.items()))
rep = A.critique(m)
print(f"\nSCORE {rep['score']}/100   fails={len(rep['fails'])} warns={len(rep['warns'])}")
for line in rep["fails"] + rep["warns"]:
    print("   ", line)

# ---------------------------------------------------------------- POLISH
# This is the whole point: the report named the fixes, so apply them and
# measure again. An agent does not need eyes to do this.
print("\npolish pass: close_loop + soften_handles + decimate")
print("  ", A.close_loop(rig, 1, 24))
A.soften_handles(rig, factor=0.55)
print("  ", A.decimate(rig, error=0.02))

# A walk cycle authored in place has no root translation, so a planted foot
# genuinely travels backwards relative to the root. Measure against the stride
# the root WOULD cover rather than against zero.
stride_per_frame = m[tracked[0]]["path_length_m"] / 24
m2 = A.metrics(rig, tracked, 1, 24, contact_bones=contacts,
               expected_root_speed=stride_per_frame)
rep2 = A.critique(m2)
print(f"SCORE {rep['score']} -> {rep2['score']}/100   "
      f"fails {len(rep['fails'])} -> {len(rep2['fails'])}   "
      f"warns {len(rep['warns'])} -> {len(rep2['warns'])}")
for line in rep2["fails"] + rep2["warns"]:
    print("   ", line)
if not rep2["fails"] and not rep2["warns"]:
    print("    clean")

with open(os.path.join(OUT, "critique.json"), "w") as f:
    json.dump({"before": {"metrics": m, "report": rep},
               "after": {"metrics": m2, "report": rep2}}, f, indent=2)

# ---------------------------------------------------------------- LOOK
RN.setup_world(color=(0.05, 0.06, 0.08))
bounds = INS.scene_bounds([body])
dist = RN.frame_distance(bounds["height"], lens=65)
RN.camera(lens=65, target=(0, 0, bounds["center"][2]),
          location=(dist * 0.45, -dist * 0.88, bounds["center"][2] + 0.25))
rig.hide_render = True
frames = A.playblast(OUT, "walk", 1, 24, step=1, resolution=(300, 420))
sheet = RN.contact_sheet(frames, os.path.join(OUT, "walk_contactsheet.png"), cols=8, tile=180)
print("contact sheet:", sheet)
try:
    print("mp4:", RN.encode(frames, os.path.join(OUT, "walk.mp4"), fps=24))
except Exception as e:
    print("encode skipped:", e)

A.bake(rig, 1, 24)
RIG.export(body, rig, os.path.join(OUT, "character_walk.glb"), fmt="GLB")
print("glb", os.path.getsize(os.path.join(OUT, "character_walk.glb")), "bytes")
