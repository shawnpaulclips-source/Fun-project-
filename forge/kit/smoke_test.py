"""Verify the environment and every forgekit module. Run after install.

Each assertion here corresponds to something that actually broke during
development. If one fails, the trap table in README section 2 explains it.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forgekit import env
env.prepare()
import bpy

FAIL = []
def check(name, fn):
    t = time.time()
    try:
        r = fn()
        print(f"  PASS  {name:38} {time.time()-t:6.2f}s  {r if r is not None else ''}")
    except Exception as e:
        FAIL.append(name)
        print(f"  FAIL  {name:38} {type(e).__name__}: {str(e)[:70]}")

from forgekit import mesh as M, materials as MAT, render as RN
from forgekit import inspect as INS, rig as RIG, anim as A, checkpoint as CK

print("forge-kit smoke test")
print(f"  bpy {bpy.app.version_string}, python {sys.version.split()[0]}")

caps = env.capabilities()
print(f"  gpu={caps['has_gpu']} ffmpeg={caps['ffmpeg']} cores={caps['cpu_count']}")
if caps["eevee_allowed"]:
    print("  note: GPU present, EEVEE is viable here")

bpy.ops.wm.read_factory_settings(use_empty=True)

check("mesh.lathe", lambda: f"{len(M.lathe('t', [(0,0),(0.5,0),(0.5,1),(0,1)], 32).data.polygons)} faces")
check("mesh.graduated_ring",
      lambda: f"{len(M.graduated_ring('r', 1.0, 0.04, 0.12, divisions=60).data.polygons)} faces")
check("materials.metal", lambda: MAT.metal("m", "brass").name)
check("inspect.mesh_stats", lambda: f"nonmanifold={INS.mesh_stats(bpy.data.objects['r'])['non_manifold_edges']}")
check("inspect.scene_bounds", lambda: f"h={INS.scene_bounds()['height']:.2f}m")
check("checkpoint", lambda: f"{CK.checkpoint('/tmp/forgekit_smoke', 'x')['seconds']*1000:.0f}ms")

check("render.workbench 256px", lambda: (
    RN.setup_world(), RN.three_point(), RN.camera(location=(4, -5, 2)),
    RN.workbench(), RN.configure(engine="BLENDER_WORKBENCH", resolution=(256, 256)),
    f"{RN.shot('/tmp/forgekit_smoke/wb', verbose=False)[1]:.2f}s")[-1])
check("render.cycles 256px", lambda: (
    RN.configure(engine="CYCLES", samples=16, resolution=(256, 256)),
    f"{RN.shot('/tmp/forgekit_smoke/cy', verbose=False)[1]:.2f}s")[-1])

bpy.ops.wm.read_factory_settings(use_empty=True)
check("rig.enable_rigify", lambda: RIG.enable_rigify())          # AFTER factory reset
check("rig.add_human_metarig", lambda: f"{len(RIG.add_human_metarig().data.bones)} bones")

def gen():
    meta = bpy.data.objects["metarig"]
    r = RIG.generate(meta)
    return (f"{len(r.data.bones)} bones, "
            f"{sum(len(pb.constraints) for pb in r.pose.bones)} constraints")
check("rig.generate (Rigify)", gen)
check("rig.find_rig", lambda: RIG.find_rig().name)
check("rig.ik_fk_switches", lambda: f"{len(RIG.ik_fk_switches(RIG.find_rig()))} found")
check("rig.manifest", lambda: f"{len(RIG.manifest(RIG.find_rig())['deform_bones'])} deform bones")

def anim_api():
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.object
    for f in (1, 12, 24):
        ob.location.z = f * 0.1
        ob.keyframe_insert("location", index=2, frame=f)
    n = len(A.fcurves(ob))
    assert n > 0, "fcurves() returned nothing - 5.x channelbag access is broken"
    return f"{n} curves (5.x channelbag)"
check("anim.fcurves (5.x slotted)", anim_api)
check("anim.graph_op decimate", lambda: str(A.decimate(bpy.data.objects["Cube"], error=0.05)))
check("anim.metrics", lambda: "ok" if A.EASE["snap"] == (0.10, 0.70) else "ease table altered")

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
    sys.exit(1)
print("all checks passed")
