"""Detailed hard-surface asset, built entirely from numbers.

17 parts, ~180k triangles after modifiers, 36 s hero render on 4 CPU cores.
Nothing here is sculpted or generated - every graduation mark is placed by trig.

Run:  python examples/01_armillary.py out/
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from forgekit import env
env.prepare()
import bpy
from forgekit import mesh as M, materials as MAT, render as RN, inspect as INS, checkpoint as CK
from mathutils import Euler

OUT = sys.argv[-1] if len(sys.argv) > 1 else "out"
os.makedirs(OUT, exist_ok=True)
R = math.radians

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.unit_settings.system = "METRIC"

brass = MAT.metal("Brass", "brass", roughness=0.22, noise_scale=240, bump_strength=0.22)
brass_d = MAT.metal("BrassDark", "brass_dark", roughness=0.38, noise_scale=180, bump_strength=0.30)
steel = MAT.metal("Steel", "iron", roughness=0.18, noise_scale=320, bump_strength=0.14)
globe_m = MAT.metal("Globe", (0.300, 0.330, 0.360), roughness=0.30, noise_scale=60, bump_strength=0.55)

parts = []

meridian = M.graduated_ring("Ring_Meridian", 1.00, 0.045, 0.150, divisions=180, every=5)
meridian.rotation_euler = Euler((R(90), 0, 0), "XYZ")
MAT.assign(meridian, brass); parts.append(meridian)

equator = M.graduated_ring("Ring_Equator", 0.90, 0.038, 0.120, divisions=144, every=6)
MAT.assign(equator, brass); parts.append(equator)

ecliptic = M.graduated_ring("Ring_Ecliptic", 0.90, 0.038, 0.135, divisions=120, every=10)
ecliptic.rotation_euler = Euler((R(23.44), 0, R(12)), "XYZ")   # real obliquity
MAT.assign(ecliptic, brass_d); parts.append(ecliptic)

colure = M.plain_ring("Ring_Colure", 0.985, 0.030, 0.095)
colure.rotation_euler = Euler((R(90), 0, R(90)), "XYZ")
MAT.assign(colure, brass_d); parts.append(colure)

for name, rr, zz in (("Tropic_N", 0.826, 0.358), ("Tropic_S", 0.826, -0.358),
                     ("Polar_N", 0.398, 0.820), ("Polar_S", 0.398, -0.820)):
    ring = M.plain_ring(name, rr, 0.022, 0.055, steps=128)
    ring.location = (0, 0, zz)
    MAT.assign(ring, steel); parts.append(ring)

horizon = M.graduated_ring("Ring_Horizon", 1.17, 0.055, 0.195, divisions=128, every=4,
                           major_size=(0.014, 0.011, 0.072))
MAT.assign(horizon, brass); parts.append(horizon)

axis = M.lathe("Polar_Axis", [
    (0.000, -1.14), (0.030, -1.14), (0.030, -1.02), (0.055, -0.99), (0.030, -0.96),
    (0.022, -0.90), (0.022, 0.90), (0.030, 0.96), (0.055, 0.99), (0.030, 1.02),
    (0.030, 1.14), (0.000, 1.14)], steps=48)
MAT.assign(axis, steel); parts.append(axis)

bpy.ops.mesh.primitive_uv_sphere_add(radius=0.072, segments=32, ring_count=16,
                                     location=(0, 0, 1.205))
finial = bpy.context.object; finial.name = "Finial"
MAT.assign(finial, brass); parts.append(finial)

bpy.ops.mesh.primitive_uv_sphere_add(radius=0.255, segments=64, ring_count=32)
globe = bpy.context.object; globe.name = "Globe"
M.subdivide(globe, 2, 3)
MAT.assign(globe, globe_m); parts.append(globe)

pedestal = M.lathe("Pedestal", [
    (0.000, -1.72), (0.430, -1.72), (0.430, -1.655), (0.330, -1.60), (0.300, -1.545),
    (0.255, -1.50), (0.230, -1.40), (0.205, -1.26), (0.150, -1.20), (0.118, -1.16),
    (0.170, -1.115), (0.185, -1.075), (0.150, -1.035), (0.095, -1.00), (0.095, -0.93),
    (0.140, -0.90), (0.175, -0.855), (0.150, -0.815), (0.100, -0.785), (0.085, -0.72),
    (0.000, -0.72)], steps=80)
MAT.assign(pedestal, brass_d); parts.append(pedestal)

import bmesh
for i in range(3):
    ob, me = M.new_mesh_object(f"Arm_{i}")
    bm = bmesh.new()
    M.loft_bracket(bm, (0.095, -0.770), (0.760, -0.820), (1.175, -0.560), (1.150, 0.000),
                   segments=26, width_start=0.105, width_end=0.074,
                   depth_start=0.058, depth_end=0.042)
    M.add_box(bm, (1.118, 0.0, 0.0), (0.070, 0.118, 0.215))
    M.finish(ob, me, bm)
    ob.rotation_euler = Euler((0, 0, math.tau * i / 3 + R(30)), "XYZ")
    MAT.assign(ob, brass_d); parts.append(ob)

base = M.lathe("Base", [
    (0.000, -1.88), (0.620, -1.88), (0.640, -1.862), (0.648, -1.835), (0.628, -1.812),
    (0.585, -1.800), (0.560, -1.772), (0.566, -1.745), (0.540, -1.722), (0.470, -1.712),
    (0.430, -1.700), (0.430, -1.655), (0.000, -1.655)], steps=96)
MAT.assign(base, brass_d); parts.append(base)

for ob in parts:
    M.bevel(ob, width=0.0035, segments=2, angle_deg=34)

CK.checkpoint(os.path.join(OUT, "ck"), "built")

# ---- look at it before committing to an expensive render
INS.report(parts)
bounds = INS.scene_bounds(parts)
print("bounds:", bounds)

RN.setup_world()
RN.three_point()
dist = RN.frame_distance(bounds["height"], lens=80)      # never guess this
cam, tgt = RN.camera(lens=80, target=(0, 0, bounds["center"][2]),
                     location=(dist * 0.62, -dist * 0.76, 1.6))
print(f"camera distance {dist:.2f} m for a {bounds['height']:.2f} m object")

RN.configure(engine="CYCLES", samples=96, resolution=(900, 1100))
RN.shot(os.path.join(OUT, "armillary_hero"))

bpy.ops.export_scene.gltf(filepath=os.path.join(OUT, "armillary.glb"),
                          export_format="GLB", export_apply=True)
print("exported", os.path.getsize(os.path.join(OUT, "armillary.glb")), "bytes")
