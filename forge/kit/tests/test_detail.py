"""detail.py: every process rule runs, tier order is enforced, results are
deterministic, wear follows curvature, and a before/after Workbench render of a
turned part (full view + close-up) is produced and measured.

Run:  /home/user/bpyenv/bin/python tests/test_detail.py [out_dir]
"""
import os, sys, math, tempfile, json, hashlib

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from forgekit import env
env.prepare()
import bpy, bmesh
import numpy as np
from mathutils import Matrix
from forgekit import mesh as M, materials as MAT, render as RN, inspect as INS, detail as D, compare as CMP

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="forge_test_detail_")
os.makedirs(OUT, exist_ok=True)
FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


SPINDLE = [(0.0, 0.0), (0.030, 0.0), (0.030, 0.012), (0.022, 0.016), (0.018, 0.045),
           (0.026, 0.052), (0.026, 0.060), (0.014, 0.066), (0.012, 0.105), (0.020, 0.112),
           (0.020, 0.120), (0.0, 0.120)]


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def spindle(name="Spindle"):
    ob = M.lathe(name, SPINDLE, steps=96)
    MAT.assign(ob, MAT.metal(f"{name}_steel", "iron", roughness=0.3))
    return ob


def block(name, size, segments=1):
    ob, me = M.new_mesh_object(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((*size, 1.0)))
    if segments > 1:
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=segments - 1, use_grid_fill=True)
    bm.to_mesh(me)
    bm.free()
    MAT.assign(ob, MAT.metal(f"{name}_mat", "steel_blued" if "stamp" in name else "aluminium", roughness=0.35))
    return ob


def coords(ob):
    a = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def digest(ob):
    return hashlib.sha1(np.round(coords(ob), 9).tobytes()).hexdigest()[:12]


# ------------------------------------------------------------- every process
fresh()
parts = {
    "turned": (spindle("Turned"), {}),
    "cast": (block("Cast", (0.08, 0.06, 0.05), 4), {}),
    "forged": (block("Forged", (0.15, 0.03, 0.03), 2), {"axis": "X"}),
    "stamped": (block("Stamped", (0.08, 0.05, 0.002)), {}),
    "machined": (block("Machined", (0.06, 0.04, 0.03)), {"direction": "X"}),
    "engraved": (block("Engraved", (0.06, 0.04, 0.008)), {"engrave_axis": "+Z"}),
}
for proc, (ob, params) in parts.items():
    rep = D.apply_detail(ob, proc, seed=3, intensity=1.0, **params)
    st = INS.mesh_stats(ob, evaluated=False)
    names = [r["rule"] for t in rep["tiers"].values() for r in t]
    expect = sum(D.rules_for(proc).values(), [])
    check(f"{proc}: exactly its own rules ran, in tier order", names == expect and list(rep["tiers"]) == list(D.TIERS),
          f"{names}")
    surf = rep["tiers"]["surface"]
    amps = {k: v for r in surf for k, v in r.items() if k.endswith("_m")}
    check(f"{proc}: surface amplitudes stated in metres", bool(amps), json.dumps(amps))
    check(f"{proc}: mesh stays sane", st["degenerate_faces"] < max(5, 0.001 * st["faces"]) and st["zero_normals"] == 0,
          f"verts {rep['verts_before']}->{rep['verts_after']} faces={st['faces']} degen={st['degenerate_faces']} "
          f"paths={[r.get('path') for r in surf]} {rep['seconds']}s")
    check(f"{proc}: wear attributes written", all(a in ob.data.attributes for a in ("forge_wear_edge", "forge_cavity")))

# turned: measured scallop depth on the side wall matches Rt = f^2/(8r)
fresh()
a = spindle("ScallopCheck")
D.apply_form(a, "turned")
ref_co = coords(a).copy()
rep = D.apply_surface(a, "turned", seed=1)
r0 = rep[0]
co = coords(a)
rad = np.hypot(co[:, 0], co[:, 1])
side = (co[:, 2] > 0.075) & (co[:, 2] < 0.100)  # the plain 12 mm neck of the spindle
want_r = 0.012 + (0.014 - 0.012) * (0.105 - co[side, 2]) / (0.105 - 0.066)
dev = want_r - rad[side]
check("turned: geometry path ran", r0["path"] == "geometry" and r0["resolved"], f"verts={r0.get('refined_verts')}")
check("turned: peak-to-valley on neck ~= Rt (39 um)", abs((dev.max() - dev.min()) - r0["scallop_height_m"]) < 0.4 * r0["scallop_height_m"],
      f"measured {1e6*(dev.max()-dev.min()):.1f} um, Rt {1e6*r0['scallop_height_m']:.1f} um")

# ------------------------------------------------------------- order enforcement
fresh()
o = spindle("Order")
D.apply_form(o, "turned")
D.apply_wear(o, "turned")
try:
    D.apply_surface(o, "turned")
    check("surface after wear is refused", False)
except D.DetailOrderError as e:
    check("surface after wear is refused", True, str(e))
try:
    D.apply_form(o, "turned")
    check("re-applying a tier is refused", False)
except D.DetailOrderError as e:
    check("re-applying a tier is refused", True, str(e))
try:
    D.apply_detail(o, "cast")
    check("a second process on one part is refused", False)
except ValueError as e:
    check("a second process on one part is refused", True, str(e)[:80])

# ------------------------------------------------------------- determinism
fresh()
h = []
for seed in (5, 5, 6):
    p = block(f"Det{seed}_{len(h)}", (0.05, 0.05, 0.05), 3)
    D.apply_detail(p, "forged", seed=seed)
    h.append(digest(p))
check("same seed -> identical geometry", h[0] == h[1], f"{h}")
check("different seed -> different geometry", h[0] != h[2])

# ------------------------------------------------------------- wear follows curvature
fresh()
w = block("WearCube", (0.04, 0.04, 0.04), 6)
D.apply_form(w, "machined")
D.apply_wear(w, "machined", seed=2)
co = coords(w)
we = np.empty(len(co))
w.data.attributes["forge_wear_edge"].data.foreach_get("value", we)
corner = (np.abs(co) > 0.0195).sum(1) >= 2
srt = np.sort(np.abs(co), axis=1)
face_centre = (srt[:, 2] > 0.0195) & (srt[:, 1] < 0.008)   # on a face, >= 12 mm from every edge
check("edge wear high on convex edges, ~0 on flat faces",
      we[corner].mean() > 0.3 and we[face_centre].mean() < 0.02,
      f"edges {we[corner].mean():.3f} vs faces {we[face_centre].mean():.3f}")

# ------------------------------------------------------------- shader path
fresh()
s = block("ShaderPath", (0.06, 0.04, 0.03))
rep = D.apply_detail(s, "machined", tiers=("form", "surface"), mode="shader")
mname = rep["tiers"]["surface"][0].get("material")
bumps = [n for n in bpy.data.materials[mname].node_tree.nodes if n.type == "BUMP" and n.label.startswith("forge_")]
check("shader path writes a Bump with Distance = amplitude (m)", bumps and abs(bumps[0].inputs["Distance"].default_value - 5e-6) < 1e-9,
      f"{[(b.label, b.inputs['Distance'].default_value) for b in bumps]}")

# ------------------------------------------------------------- before / after render
def stage(ob, close=False):
    RN.workbench(shading="STUDIO", color_type="SINGLE", outline=False, cavity=False)
    sh = bpy.context.scene.display.shading
    sh.single_color = (0.75, 0.75, 0.78)
    sh.show_specular_highlight = True
    sc = bpy.context.scene
    if close:
        cam, _ = RN.camera(lens=100, target=(0.0, 0.0, 0.085), location=(0.0, -0.11, 0.095))
    else:
        d = RN.frame_distance(0.13, lens=80)
        cam, _ = RN.camera(lens=80, target=(0.0, 0.0, 0.06), location=(0.0, -d, 0.075))
    cam.data.clip_start = 0.002   # default 0.1 m clips a close-up of a 12 cm part
    sc.render.resolution_percentage = 100
    return cam


renders = {}
for label, do in (("before", False), ("after", True)):
    for close in (False, True):
        fresh()
        ob = spindle("Spindle")
        if do:
            rep = D.apply_detail(ob, "turned", seed=7, intensity=1.0, tiers=("form", "surface"))
        else:
            D.apply_form(ob, "turned")      # same form tier both sides: the only difference is the surface marks
        stage(ob, close)
        tag = f"turned_{label}_{'close' if close else 'full'}"
        p, secs = RN.shot(os.path.join(OUT, tag), resolution=(800, 800), verbose=False)
        renders[tag] = (p, secs)
for view in ("full", "close"):
    b, a = renders[f"turned_before_{view}"][0], renders[f"turned_after_{view}"][0]
    res = CMP.compare_renders(b, a)
    ib, ia = CMP.load_image(b)[..., :3], CMP.load_image(a)[..., :3]
    mad = float(np.abs(ib - ia).mean())
    changed = float((np.abs(ib - ia).max(-1) > 4 / 255).mean())
    check(f"before/after render ({view})", os.path.exists(a),
          f"mean|diff|={mad:.4f} pixels changed>4/255: {100*changed:.1f}% edge_f1={res['edges']['f1']} "
          f"iou={res['silhouette_iou']} render {renders[f'turned_after_{view}'][1]:.2f}s")
    # side-by-side composite for eyeballing
    CMP.write_png(os.path.join(OUT, f"turned_before_after_{view}.png"), np.concatenate([ib, ia], axis=1))
print(f"\nrenders in {OUT}: turned_before_after_full.png, turned_before_after_close.png")
print(f"{len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
