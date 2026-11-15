"""Bake a procedural brass material, export GLB, re-import clean, check textures.

Run:  /home/user/bpyenv/bin/python tests/test_bake.py [out_dir]
Prints PASS/FAIL per check; exits 1 on any failure.
"""
import os, sys, time, json, tempfile

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from forgekit import env
env.prepare()
import bpy
from forgekit import mesh as M, materials as MAT, bake as BK

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="forge_test_bake_")
os.makedirs(OUT, exist_ok=True)
FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def build_part():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prof = [(0.0, 0.0), (0.090, 0.0), (0.095, 0.008), (0.060, 0.030), (0.035, 0.060),
            (0.030, 0.140), (0.045, 0.160), (0.050, 0.175), (0.0, 0.180)]
    ob = M.lathe("BrassPart", prof, steps=96)
    M.bevel(ob, width=0.002, segments=2, angle_deg=30)
    mat = MAT.metal("Brass", "brass", roughness=0.24, noise_scale=180.0)
    MAT.wear_edges(mat)
    MAT.assign(ob, mat)
    return ob, mat


# ------------------------------------------------------------- xatlas / fallback
ob, mat = build_part()
info = BK.uv_unwrap(ob, method="xatlas")
check("uv_unwrap xatlas reports what ran", info["method"] in ("xatlas", "smart"),
      json.dumps({k: info[k] for k in ("requested", "method", "fallback", "seconds", "loop_conflicts")}))
if BK.xatlas_available():
    check("xatlas path ran when module present", info["method"] == "xatlas" and not info["fallback"],
          f"charts={info.get('charts')}")
info_s = BK.uv_unwrap(ob, method="smart")
check("uv_unwrap smart", info_s["method"] == "smart" and len(ob.data.uv_layers) >= 1,
      f"{info_s['seconds']}s faces={info_s['faces']}")

# ------------------------------------------------------------- bake 1024
baked = BK.bake_material(ob, mat, resolution=1024, samples=8, out_dir=os.path.join(OUT, "tex1024"))
per = {k: v["seconds"] for k, v in baked["channels"].items()}
check("bake 1024 all five channels", set(baked["channels"]) == set(BK.CHANNELS),
      f"total={baked['total_seconds']}s per={per}")
bc = baked["channels"]["base_color"]["mean"]
check("base_color not black (metal diffuse trap avoided)", max(bc) > 0.3, f"mean sRGB={bc}")
check("base_color is brass-hued (R>G>B)", bc[0] > bc[1] > bc[2], f"{bc}")
mt = baked["channels"]["metallic"]["mean"][0]
check("metallic ~1", mt > 0.9, f"{mt}")
rg = baked["channels"]["roughness"]["mean"][0]
check("roughness in plausible range", 0.05 < rg < 0.6, f"{rg}")
nm = baked["channels"]["normal"]["mean"]
check("normal map is tangent-space blue", nm[2] > 0.7 and abs(nm[0] - 0.5) < 0.15, f"{nm}")
ao = baked["channels"]["ao"]["mean"][0]
check("ao in (0,1]", 0.2 < ao <= 1.0, f"{ao}")

# ------------------------------------------------------------- 2048 timings
ob2, mat2 = build_part()
BK.uv_unwrap(ob2, method="smart")
t2 = BK.bake_material(ob2, mat2, channels=("normal",), resolution=2048, samples=8,
                      out_dir=os.path.join(OUT, "tex2048"))
check("bake 2048 normal @8spp", "normal" in t2["channels"],
      f"{t2['channels']['normal']['seconds']}s (research: 1.90 s)")
t2all = BK.bake_material(ob2, mat2, resolution=2048, samples=8, out_dir=os.path.join(OUT, "tex2048"))
check("bake 2048 all channels", len(t2all["channels"]) == 5,
      f"total={t2all['total_seconds']}s per={ {k: v['seconds'] for k, v in t2all['channels'].items()} }")

# ------------------------------------------------------------- rewire + export + reimport
ob, mat = build_part()
BK.uv_unwrap(ob, method="smart")
baked = BK.bake_material(ob, mat, resolution=1024, samples=8, out_dir=os.path.join(OUT, "tex_export"))
rw = BK.rewire_to_images(mat, baked)
check("rewire wired base/orm/normal/occlusion",
      all(k in rw["wired"] for k in ("base_color", "orm", "normal", "occlusion")), json.dumps(rw["wired"]))
glb = os.path.join(OUT, "brass_textured.glb")
ex = BK.export_textured_glb(ob, None, glb)
check("export ok, no problems", ex["ok"], "; ".join(ex["problems"]) or f"{ex['bytes']} bytes in {ex['seconds']}s")
gm = ex["glb"]["materials"][0]
for slot in ("baseColorTexture", "metallicRoughnessTexture", "normalTexture", "occlusionTexture"):
    s = gm[slot]
    check(f"GLB {slot}", bool(s), f"{s.get('w')}x{s.get('h')} {s.get('bytes')} B" if s else "missing")
re = ex["reimport"]
imgs = [i for m in re["materials"] for i in m["image_textures"]]
check("clean re-import has image textures", len(imgs) >= 3,
      json.dumps([(i["image"], i["size"], i["feeds"]) for i in imgs]))
feeds = " ".join(f for i in imgs for f in i["feeds"])
check("re-import: an image feeds Base Color", "Base Color" in feeds)
check("re-import: metallic/roughness texture present", ("Roughness" in feeds or "Metallic" in feeds
      or "SEPARATE_COLOR" in feeds), feeds)
print(f"\nGLB: {glb}  ({ex['bytes']} bytes)   textures in {os.path.join(OUT, 'tex_export')}")
print(f"{len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
