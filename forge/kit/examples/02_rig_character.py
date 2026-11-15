"""Build a humanoid, rig it with Rigify, skin it, and validate blind.

Pipeline: primitives -> join -> voxel remesh (makes it manifold, which bone heat
REQUIRES) -> fit metarig -> rigify_generate -> bone heat bind -> weight heatmap
-> extreme-pose battery -> GLB export with deform-only bones.

Run:  python examples/02_rig_character.py out/
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from forgekit import env
env.prepare()
import bpy
from forgekit import mesh as M, materials as MAT, render as RN, inspect as INS
from forgekit import rig as RIG, checkpoint as CK
from mathutils import Vector, Euler

OUT = sys.argv[-1] if len(sys.argv) > 1 else "out"
os.makedirs(OUT, exist_ok=True)
R = math.radians

bpy.ops.wm.read_factory_settings(use_empty=True)
RIG.enable_rigify()          # AFTER factory reset - that call disables addons

# ---------------------------------------------------------------- body
def capsule(name, p0, p1, r0, r1, segments=16):
    """Tapered limb segment between two world points."""
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    bpy.ops.mesh.primitive_cone_add(vertices=segments, radius1=r0, radius2=r1,
                                    depth=d.length, location=(p0 + p1) / 2)
    ob = bpy.context.object
    ob.name = name
    ob.rotation_mode = "QUATERNION"
    ob.rotation_quaternion = d.to_track_quat("Z", "Y")
    return ob

PARTS = []
# proportions in metres for a 1.80 m figure, A-pose
PARTS.append(capsule("torso",  (0, 0, 1.02), (0, 0, 1.48), 0.155, 0.185))
PARTS.append(capsule("hips",   (0, 0, 0.92), (0, 0, 1.04), 0.175, 0.160))
PARTS.append(capsule("neck",   (0, 0, 1.47), (0, 0, 1.58), 0.062, 0.055))
for s in (1, -1):
    PARTS.append(capsule(f"upperarm{s}", (0.17*s, 0, 1.44), (0.45*s, 0, 1.18), 0.072, 0.056))
    PARTS.append(capsule(f"forearm{s}",  (0.45*s, 0, 1.18), (0.70*s, 0, 0.95), 0.056, 0.042))
    PARTS.append(capsule(f"hand{s}",     (0.70*s, 0, 0.95), (0.80*s, 0, 0.87), 0.044, 0.030))
    PARTS.append(capsule(f"thigh{s}",    (0.095*s, 0, 0.92), (0.11*s, 0, 0.50), 0.105, 0.078))
    PARTS.append(capsule(f"shin{s}",     (0.11*s, 0, 0.50), (0.115*s, 0, 0.09), 0.078, 0.052))
    PARTS.append(capsule(f"foot{s}",     (0.115*s, 0.0, 0.055), (0.115*s, -0.16, 0.035), 0.055, 0.042))
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.105, location=(0, 0.005, 1.665), segments=24, ring_count=16)
head = bpy.context.object; head.name = "head"
head.scale = (0.92, 1.05, 1.12)
PARTS.append(head)

bpy.ops.object.select_all(action="DESELECT")
for p in PARTS:
    p.select_set(True)
bpy.context.view_layer.objects.active = PARTS[0]
bpy.ops.object.join()
body = bpy.context.object
body.name = "Body"

# Voxel remesh fuses the overlapping primitives into ONE manifold surface.
# This is not cosmetic: bone heat fails SILENTLY on non-manifold input.
rm = body.modifiers.new("remesh", "REMESH")
rm.mode = "VOXEL"
rm.voxel_size = 0.018
rm.use_smooth_shade = True
bpy.ops.object.modifier_apply(modifier="remesh")
M.shade_smooth(body)
MAT.assign(body, MAT.dielectric("Skin", (0.52, 0.38, 0.31), roughness=0.55))

stats = INS.mesh_stats(body, evaluated=False)
print(f"body: {stats['faces']} faces, non-manifold {stats['non_manifold_edges']}, "
      f"watertight {stats['watertight']}")
assert stats["non_manifold_edges"] == 0, "remesh failed to produce manifold geometry"

CK.checkpoint(os.path.join(OUT, "ck"), "body")

# ---------------------------------------------------------------- rig
meta = RIG.add_human_metarig()
scale = RIG.fit_metarig(meta, mesh=body)
print(f"metarig fitted, scale {scale:.3f}, {len(meta.data.bones)} bones")

import time
t = time.time()
rig = RIG.generate(meta)   # metarig is hidden, not deleted
print(f"rigify: {len(rig.data.bones)} bones, "
      f"{sum(len(pb.constraints) for pb in rig.pose.bones)} constraints, "
      f"{len(rig.animation_data.drivers) if rig.animation_data else 0} drivers, "
      f"{time.time()-t:.2f}s")

RIG.bind(body, rig, method="ARMATURE_AUTO")
ws = RIG.weight_stats(body, rig)
print("weights:", {k: ws[k] for k in ("unweighted_vertices", "vertices_over_4_influences",
                                      "weight_sum_min", "weight_sum_max", "empty_vertex_groups")})

CK.checkpoint(os.path.join(OUT, "ck"), "rigged")

# ---------------------------------------------------------------- look at it
RIG.weights_to_vertex_color(body, mode="argmax")
RN.setup_world(color=(0.06, 0.07, 0.09))
RN.workbench(shading="FLAT", color_type="VERTEX", cavity=False, outline=True)
bounds = INS.scene_bounds([body])
dist = RN.frame_distance(bounds["height"], lens=65)
cam, tgt = RN.camera(lens=65, target=(0, 0, bounds["center"][2]),
                     location=(0, -dist, bounds["center"][2]))
rig.hide_render = True
RN.shot(os.path.join(OUT, "weights_front"), resolution=(560, 900))
RN.shot(os.path.join(OUT, "weights_side"), location=(dist, 0, bounds["center"][2]),
        resolution=(560, 900))

# ---------------------------------------------------------------- stress it
print("pose battery:")
for row in RIG.pose_test(rig, body):
    print("  ", row)

# ---------------------------------------------------------------- ship it
man = RIG.manifest(rig, body)
import json
with open(os.path.join(OUT, "rig_manifest.json"), "w") as f:
    json.dump(man, f, indent=2)
print(f"manifest: {man['bone_count']} bones, {len(man['deform_bones'])} deform, "
      f"root={man['root_motion_bone']}, cog={man['cog_bone']}")

RIG.export(body, rig, os.path.join(OUT, "character.glb"), fmt="GLB")
print("glb", os.path.getsize(os.path.join(OUT, "character.glb")), "bytes")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "character.blend"))
