"""compare.py: synthetic discs with analytic IoU, localisation, value structure,
the fallback PNG decoder, and a real render_pair round trip through bpy.

Run:  /home/user/bpyenv/bin/python tests/test_compare.py [out_dir]
"""
import os, sys, math, tempfile, time
import numpy as np

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from forgekit import compare as CMP

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="forge_test_cmp_")
os.makedirs(OUT, exist_ok=True)
FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def disc(w, h, cx, cy, r, fg=(0.8, 0.7, 0.4), bg=(0.1, 0.1, 0.12), alpha=False):
    yy, xx = np.mgrid[0:h, 0:w]
    m = (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= r * r
    img = np.empty((h, w, 4 if alpha else 3), np.float32)
    img[..., :3] = bg
    img[m, :3] = fg
    if alpha:
        img[..., 3] = m.astype(np.float32)
    return img


def lens_iou(r, d):
    if d >= 2 * r:
        return 0.0
    inter = 2 * r * r * math.acos(d / (2 * r)) - (d / 2) * math.sqrt(4 * r * r - d * d)
    return inter / (2 * math.pi * r * r - inter)


W, H, R = 512, 512, 120.0
ref = CMP.write_png(os.path.join(OUT, "disc_ref.png"), disc(W, H, 256, 256, R))
for d in (0.0, 30.0, 80.0, 110.0):
    cand = CMP.write_png(os.path.join(OUT, f"disc_off{int(d)}.png"), disc(W, H, 256 + d, 256, R))
    t = time.time()
    res = CMP.compare_renders(ref, cand)
    dt = time.time() - t
    want = lens_iou(R, d)
    check(f"IoU disc offset {d:.0f}px (background silhouette)", abs(res["silhouette_iou"] - want) < 0.01,
          f"got {res['silhouette_iou']:.4f} want {want:.4f} ({dt*1000:.0f} ms)")
    if d:
        check(f"centroid shift reported for {d:.0f}px", abs(res["silhouette"]["centroid_shift_px"][0] - d) < 1.0,
              f"{res['silhouette']['centroid_shift_px']}")

# alpha-based silhouette
refa = CMP.write_png(os.path.join(OUT, "disc_ref_a.png"), disc(W, H, 256, 256, R, alpha=True))
canda = CMP.write_png(os.path.join(OUT, "disc_off50_a.png"), disc(W, H, 306, 256, R, alpha=True))
res = CMP.compare_renders(refa, canda)
check("IoU via alpha channel", abs(res["silhouette_iou"] - lens_iou(R, 50)) < 0.01 and
      res["silhouette"]["method"]["reference"] == "alpha", f"{res['silhouette_iou']} want {lens_iou(R, 50):.4f}")

# resampled candidate (half resolution) still measures the same geometry
half = CMP.write_png(os.path.join(OUT, "disc_off30_half.png"), disc(256, 256, 143, 128, R / 2))
res = CMP.compare_renders(ref, half)
check("IoU with resampled candidate", res["resampled"] and abs(res["silhouette_iou"] - lens_iou(R, 30)) < 0.02,
      f"{res['silhouette_iou']} want {lens_iou(R, 30):.4f}")

# identical
res = CMP.compare_renders(ref, ref)
check("identical images: IoU 1, emd 0, edge f1 1", res["silhouette_iou"] == 1.0 and res["value"]["hist_emd"] == 0
      and res["edges"]["f1"] == 1.0, f"{res['silhouette_iou']} {res['value']['hist_emd']} {res['edges']['f1']}")

# value structure: same silhouette, darker candidate -> more shadow, negative mean delta
grey = (0.5, 0.5, 0.5)
ref_g = CMP.write_png(os.path.join(OUT, "disc_ref_grey.png"), disc(W, H, 256, 256, R, bg=grey))
dark = CMP.write_png(os.path.join(OUT, "disc_dark.png"), disc(W, H, 256, 256, R, fg=(0.12, 0.10, 0.06), bg=grey))
res = CMP.compare_renders(ref_g, dark)
v = res["value"]
check("value: darker candidate -> shadow band up, mean down", v["band_delta"][0] > 0.9 and v["mean_delta"] < -0.4,
      f"band_delta={v['band_delta']} mean_delta={v['mean_delta']} emd={v['hist_emd']}")
check("value: silhouette unaffected by value change", res["silhouette_iou"] > 0.99, f"{res['silhouette_iou']}")

# localisation: candidate disc shifted right -> worst cells on the right edge are extra mass, left are missing
loc = CMP.localise_error(ref, CMP.write_png(os.path.join(OUT, "disc_off80.png"), disc(W, H, 336, 256, R)), grid=(4, 4))
worst = loc["worst"][0]
check("localise: worst cell is a silhouette error", worst["kind"] == "silhouette", f"{worst}")
right = [c for c in loc["cells"] if c["col"] == 3 and c["row"] in (1, 2)]
left = [c for c in loc["cells"] if c["col"] == 1 and c["row"] in (1, 2)]
check("localise: right cells report extra mass", all(c["silhouette_extra"] > c["silhouette_missing"] for c in right),
      f"{[(c['row'], c['col'], c['silhouette_extra']) for c in right]}")
check("localise: left cells report missing mass", all(c["silhouette_missing"] > c["silhouette_extra"] for c in left),
      f"{[(c['row'], c['col'], c['silhouette_missing']) for c in left]}")
corner = [c for c in loc["cells"] if c["row"] == 0 and c["col"] == 0][0]
check("localise: empty corner agrees", corner["hint"] == "agrees", f"{corner}")

# fallback decoder matches Pillow, on a file with every filter type (Pillow writes adaptive filters)
try:
    from PIL import Image
    rng = np.random.default_rng(3)
    noisy = (rng.random((64, 80, 3)) * 0.3 + disc(80, 64, 40, 32, 20)).clip(0, 1)
    p = os.path.join(OUT, "pil_written.png")
    Image.fromarray((noisy * 255).astype(np.uint8)).save(p, optimize=True)
    pa = Image.open(p).convert("RGBA")
    Image.fromarray(np.asarray(pa)).save(os.path.join(OUT, "pil_rgba.png"))
    a = CMP.load_image(p)
    b = CMP._decode_png(p)
    c = CMP._decode_png(os.path.join(OUT, "pil_rgba.png"))
    check("fallback PNG decoder == Pillow (RGB, adaptive filters)", np.abs(a - b).max() < 1e-6, f"maxdiff={np.abs(a-b).max()}")
    check("fallback PNG decoder RGBA", c.shape[2] == 4 and np.abs(c[..., :3] - a).max() < 1e-6)
except ImportError:
    b = CMP._decode_png(ref)
    check("fallback PNG decoder reads our own writer", b.shape == (H, W, 3))

# render_pair: a real bpy render compared against a reference rendered from the same camera
try:
    from forgekit import env
    env.prepare()
    import bpy
    from forgekit import render as RN
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=48, ring_count=24)
    ob = bpy.context.active_object
    cam_loc = (0.0, -4.0, 0.0)
    r1 = CMP.render_pair(ob, ref, cam_loc, target=(0, 0, 0), lens=50, out_path=os.path.join(OUT, "rp_self.png"))
    selfres = CMP.compare_renders(r1["candidate_path"], r1["candidate_path"])
    check("render_pair renders at reference size with alpha silhouette",
          r1["size"] == (W, H) and r1["silhouette"]["method"]["candidate"] == "alpha",
          f"{r1['render_seconds']}s iou_vs_disc={r1['silhouette_iou']}")
    # move the sphere 0.25 m right; compare against the centred render: IoU must drop by the analytic amount
    sphere_px = math.sqrt(r1["silhouette"]["cand_coverage"] * W * H / math.pi)
    ob.location.x = 0.25
    r2 = CMP.render_pair(ob, r1["candidate_path"], cam_loc, target=(0, 0, 0), lens=50,
                         out_path=os.path.join(OUT, "rp_moved.png"))
    shift = r2["silhouette"]["centroid_shift_px"][0]
    want = lens_iou(sphere_px, abs(shift))
    check("render_pair: moved sphere IoU matches analytic disc overlap", abs(r2["silhouette_iou"] - want) < 0.03,
          f"iou={r2['silhouette_iou']} want~{want:.3f} shift={shift}px r={sphere_px:.1f}px")
    check("render_pair: localise names a silhouette cell", r2["localise"][0]["kind"] == "silhouette", f"{r2['localise'][0]}")
    check("render_pair restores scene camera", bpy.context.scene.camera is None and "ForgeCompareCam" not in bpy.data.objects)
except ImportError as e:
    print(f"SKIP  render_pair (bpy not importable: {e})")

print(f"{len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
