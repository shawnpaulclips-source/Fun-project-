"""Look-dev you can run without eyes: light placement, contact, value, colour.

Every function returns numbers in a plain dict so an agent can decide what to
fix from ~100 tokens instead of a 2000-token image.

    light_rig_from_scene_bounds()  key/fill/rim sized and aimed from the bbox
    contact_shadow_check(obj, z)   is it sitting on the ground, floating, sunk?
    value_structure(png)           shadow / mid / highlight split: flat or formed?
    colour_palette_check(obj)      base colour sanity, grey-metal detector
    look_compare(ref, cand)        value + palette deltas between two renders

Image functions need only numpy + Pillow (bpy fallback for formats Pillow
cannot read); the scene functions need bpy.
"""
from __future__ import annotations

import math
import os

import numpy as np

# Rec.709 / sRGB luminance weights
_LUMA = np.array([0.2126, 0.7152, 0.0722])

# Thresholds on display-encoded luma (what a painter calls value). 0.25 / 0.75
# split an image into thirds of the tonal range on the encoded scale.
SHADOW, HIGHLIGHT = 0.25, 0.75


# ================================================================ lights
def light_rig_from_scene_bounds(objects=None, key=2600.0, fill=700.0, rim=1500.0,
                                reference_size=2.0, clear_existing=False):
    """Key / fill / rim from render.three_point, rescaled and re-aimed at the
    bounding box of `objects` (default: every mesh in the scene).

    render.three_point is tuned for a ~1-3 m object at the origin and scales
    energy *linearly* with `scale`; area-light irradiance falls with distance
    squared, so a 10x larger rig with 10x energy is 10x too dark. This wraps it:
    positions and light sizes scale by s = size / reference_size, energies by
    s^2, the rig is moved to the bbox centre and every light is re-aimed at it.

    Returns {"lights", "center", "size", "scale", "energy_w", "distance_m",
    "key_fill_ratio", "key_rim_ratio"} where the ratios are irradiance at the
    centre (energy / distance^2), the photographer's lighting ratio.
    """
    import bpy
    from mathutils import Vector
    from forgekit import inspect as INS, render as RN
    if clear_existing:
        for o in [o for o in bpy.context.scene.objects if o.type == "LIGHT"]:
            bpy.data.objects.remove(o)
    b = INS.scene_bounds(objects)
    if b is None:
        raise ValueError("no mesh objects to light")
    size = max(max(b["size"]), 1e-4)
    s = size / reference_size
    lights = RN.three_point(key=key, fill=fill, rim=rim, scale=s)
    ctr = Vector(b["center"])
    out = {"lights": [], "energy_w": {}, "distance_m": {}}
    irr = {}
    for o in lights:
        o.data.energy *= s                 # three_point gave E*s, want E*s^2
        o.data.size *= s
        o.location = Vector(o.location) + ctr
        d = ctr - o.location
        o.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        dist = d.length
        out["lights"].append(o.name)
        out["energy_w"][o.name] = round(o.data.energy, 2)
        out["distance_m"][o.name] = round(dist, 4)
        irr[o.name.split(".")[0]] = o.data.energy / max(dist * dist, 1e-12)
    bpy.context.view_layer.update()
    out.update(center=tuple(ctr), size=round(size, 4), scale=round(s, 4),
               key_fill_ratio=round(irr["Key"] / irr["Fill"], 3),
               key_rim_ratio=round(irr["Key"] / irr["Rim"], 3))
    return out


# ================================================================ contact
def _world_verts(obj):
    import bpy
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M = np.array(obj.matrix_world)
    return co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]


def contact_shadow_check(obj, ground_z=0.0, tolerance=0.001, band=None):
    """Does obj sit on the plane z = ground_z?

    gap = lowest world-space vertex (evaluated, modifiers included) - ground_z.
    status: "contact" (|gap| <= tolerance), "floating" (gap > tolerance: a
    visible light leak under the object, the classic composited-in look) or
    "intersecting" (gap < -tolerance: sunk into the floor).
    contact_verts counts vertices within `band` (default 2 x tolerance) of the
    lowest point; footprint_m is their XY extent; com_inside reports whether
    the vertex centroid lies over that footprint (a crude "would it tip" hint).
    fix_dz is the translation that puts it exactly on the plane.
    """
    P = _world_verts(obj)
    if len(P) == 0:
        raise ValueError("object has no vertices")
    zmin = float(P[:, 2].min())
    gap = zmin - ground_z
    band = band if band is not None else 2 * tolerance
    low = P[P[:, 2] <= zmin + band]
    lo, hi = low[:, :2].min(0), low[:, :2].max(0)
    com = P[:, :2].mean(0)
    status = "contact" if abs(gap) <= tolerance else ("floating" if gap > 0 else "intersecting")
    return {"object": obj.name, "status": status, "gap_m": round(gap, 6),
            "penetration_m": round(max(0.0, -gap), 6), "contact_verts": int(len(low)),
            "footprint_m": tuple(round(float(v), 5) for v in (hi - lo)),
            "com_inside": bool(np.all(com >= lo - 1e-9) and np.all(com <= hi + 1e-9)),
            "fix_dz": round(-gap, 6), "tolerance_m": tolerance}


# ================================================================ images
def _srgb_encode(x):
    x = np.clip(x, 0.0, None)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def load_rgba(path):
    """Image as float (H, W, 4) in display-encoded [0, 1]. Pillow first; bpy
    fallback (float/linear images such as EXR are sRGB-encoded first)."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    try:
        from PIL import Image
        im = Image.open(path)
        if im.mode in ("I;16", "I;16B", "I"):
            a = np.asarray(im, dtype=np.float64) / 65535.0
            return np.dstack([a, a, a, np.ones_like(a)])
        return np.asarray(im.convert("RGBA"), dtype=np.float64) / 255.0
    except Exception:
        import bpy
        img = bpy.data.images.load(path, check_existing=False)
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        is_float = img.is_float
        bpy.data.images.remove(img)
        a = px.reshape(h, w, 4)[::-1].astype(np.float64)
        if is_float:
            a[..., :3] = _srgb_encode(a[..., :3])
        return np.clip(a, 0, 1)


def _pixel_mask(img, background):
    h, w, _ = img.shape
    if background in (None, False, "none"):
        return np.ones((h, w), bool)
    if background == "alpha":
        return img[..., 3] > 0.5
    if background == "corners":
        k = max(2, min(h, w) // 32)
        patches = np.concatenate([img[:k, :k, :3].reshape(-1, 3), img[:k, -k:, :3].reshape(-1, 3),
                                  img[-k:, :k, :3].reshape(-1, 3), img[-k:, -k:, :3].reshape(-1, 3)])
        bg = np.median(patches, axis=0)
        return np.abs(img[..., :3] - bg).max(-1) > 0.03
    raise ValueError("background must be None, 'alpha' or 'corners'")


def value_structure(image_path, shadow=SHADOW, highlight=HIGHLIGHT, bins=16, background=None):
    """Luminance structure of a render.

    Luma = Rec.709 weights on display-encoded values (perceptual value).
    background: None (all pixels), "alpha" (alpha > 0.5) or "corners" (drop
    pixels matching the median corner colour, for a subject on a plain world).
    Returns fractions in shadow / mid / highlight, a `bins`-bin histogram,
    mean, std, p5/p50/p95, contrast (p95 - p5), clipped (>0.99) and crushed
    (<0.01) fractions, key ("low"/"mid"/"high"), and verdict:
      "flat"  - contrast < 0.30 or > 85 % mids: no form, lighting needs a ratio
      "harsh" - > 60 % of pixels in shadow+highlight with < 15 % mids
      "form"  - otherwise.
    """
    img = load_rgba(image_path)
    mask = _pixel_mask(img, background)
    y = (img[..., :3] @ _LUMA)[mask]
    if y.size == 0:
        raise ValueError("mask removed every pixel")
    hist, _ = np.histogram(y, bins=bins, range=(0.0, 1.0))
    hist = hist / y.size
    sh = float((y < shadow).mean())
    hi = float((y > highlight).mean())
    mid = 1.0 - sh - hi
    p5, p50, p95 = (float(v) for v in np.percentile(y, [5, 50, 95]))
    contrast = p95 - p5
    mean = float(y.mean())
    if contrast < 0.30 or mid > 0.85:
        verdict = "flat"
    elif sh + hi > 0.60 and mid < 0.15:
        verdict = "harsh"
    else:
        verdict = "form"
    key = "low" if mean < 0.33 else ("high" if mean > 0.66 else "mid")
    notes = []
    if verdict == "flat":
        notes.append("raise the key:fill ratio (e.g. fill energy down 2-4x) or add a rim")
    if float((y > 0.99).mean()) > 0.02:
        notes.append("highlights clipping: lower key energy or exposure")
    if float((y < 0.01).mean()) > 0.10:
        notes.append("shadows crushed: raise fill or world strength")
    return {"path": os.path.basename(image_path), "pixels": int(y.size),
            "shadow": round(sh, 4), "mid": round(mid, 4), "highlight": round(hi, 4),
            "mean": round(mean, 4), "std": round(float(y.std()), 4),
            "p5": round(p5, 4), "p50": round(p50, 4), "p95": round(p95, 4),
            "contrast": round(contrast, 4),
            "clipped": round(float((y > 0.99).mean()), 4),
            "crushed": round(float((y < 0.01).mean()), 4),
            "histogram": [round(float(v), 4) for v in hist],
            "key": key, "verdict": verdict, "notes": notes,
            "thresholds": [shadow, highlight]}


def _kmeans(X, k=5, iters=10):
    if len(X) < k:
        return X.copy(), np.full(len(X), 1.0 / max(len(X), 1))
    y = X @ _LUMA
    order = np.argsort(y)
    C = X[order[np.linspace(0, len(X) - 1, k).astype(int)]].copy()
    for _ in range(iters):
        d = ((X[:, None, :] - C[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(k):
            sel = X[lab == j]
            if len(sel):
                C[j] = sel.mean(0)
    d = ((X[:, None, :] - C[None]) ** 2).sum(-1)
    lab = d.argmin(1)
    w = np.bincount(lab, minlength=k) / len(X)
    keep = w > 0
    o = np.argsort(-w[keep])
    return C[keep][o], w[keep][o]


def image_palette(image_path, k=5, background=None, sample=20000):
    """Dominant colours (k-means on display RGB), mean saturation, warmth."""
    img = load_rgba(image_path)
    mask = _pixel_mask(img, background)
    X = img[..., :3][mask]
    if len(X) > sample:
        X = X[np.random.default_rng(0).choice(len(X), sample, replace=False)]
    C, w = _kmeans(X, k)
    mx, mn = X.max(1), X.min(1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    m = X.mean(0)
    return {"colours": [[round(float(c), 4) for c in col] for col in C],
            "weights": [round(float(v), 4) for v in w],
            "mean_rgb": [round(float(v), 4) for v in m],
            "mean_saturation": round(float(sat.mean()), 4),
            "warmth": round(float(m[0] - m[2]), 4)}


def _palette_distance(pa, pb):
    """Weighted nearest-colour distance from palette a to b, symmetrised."""
    def one(a, b):
        A, wa, B = np.array(a["colours"]), np.array(a["weights"]), np.array(b["colours"])
        d = np.sqrt(((A[:, None] - B[None]) ** 2).sum(-1)).min(1)
        return float((d * wa).sum() / max(wa.sum(), 1e-9))
    return 0.5 * (one(pa, pb) + one(pb, pa))


def look_compare(reference_path, candidate_path, background=None):
    """Value-structure and palette deltas, candidate minus reference.

    Returns a plain JSON-safe dict: {"reference", "candidate" (each with
    "value" and "palette"), "delta", "scores", "verdict" (list of strings)}.
    scores are 0..1 similarities: value_score from the histogram earth-mover
    distance (1 - 2*EMD), palette_score from the weighted nearest-colour
    distance (1 - d / 0.5). match is their mean.
    """
    va = value_structure(reference_path, background=background)
    vb = value_structure(candidate_path, background=background)
    pa = image_palette(reference_path, background=background)
    pb = image_palette(candidate_path, background=background)
    ha, hb = np.array(va["histogram"]), np.array(vb["histogram"])
    emd = float(np.abs(np.cumsum(ha) - np.cumsum(hb)).sum() / len(ha))
    pdist = _palette_distance(pa, pb)
    delta = {k: round(vb[k] - va[k], 4) for k in
             ("mean", "contrast", "shadow", "mid", "highlight", "p5", "p50", "p95", "clipped")}
    delta["saturation"] = round(pb["mean_saturation"] - pa["mean_saturation"], 4)
    delta["warmth"] = round(pb["warmth"] - pa["warmth"], 4)
    delta["mean_rgb"] = [round(b - a, 4) for a, b in zip(pa["mean_rgb"], pb["mean_rgb"])]
    delta["histogram_emd"] = round(emd, 4)
    delta["palette_distance"] = round(pdist, 4)
    vs = max(0.0, 1.0 - 2.0 * emd)
    ps = max(0.0, 1.0 - pdist / 0.5)
    verdict = []
    if abs(delta["contrast"]) > 0.08:
        verdict.append(f"candidate is {'more contrasty' if delta['contrast'] > 0 else 'flatter'}: "
                       f"contrast {vb['contrast']:.2f} vs {va['contrast']:.2f}")
    if abs(delta["mean"]) > 0.05:
        verdict.append(f"candidate is {'brighter' if delta['mean'] > 0 else 'darker'}: "
                       f"mean value {vb['mean']:.2f} vs {va['mean']:.2f}")
    if abs(delta["shadow"]) > 0.08:
        verdict.append(f"shadow share {vb['shadow']:.0%} vs {va['shadow']:.0%}")
    if abs(delta["highlight"]) > 0.05:
        verdict.append(f"highlight share {vb['highlight']:.0%} vs {va['highlight']:.0%}")
    if abs(delta["saturation"]) > 0.06:
        verdict.append(f"candidate is {'more' if delta['saturation'] > 0 else 'less'} saturated "
                       f"({pb['mean_saturation']:.2f} vs {pa['mean_saturation']:.2f})")
    if abs(delta["warmth"]) > 0.04:
        verdict.append(f"candidate is {'warmer' if delta['warmth'] > 0 else 'cooler'} "
                       f"(R-B {pb['warmth']:+.2f} vs {pa['warmth']:+.2f})")
    if not verdict:
        verdict.append("value structure and palette match within tolerance")
    return {"reference": {"value": va, "palette": pa},
            "candidate": {"value": vb, "palette": pb},
            "delta": delta,
            "scores": {"value_score": round(vs, 4), "palette_score": round(ps, 4),
                       "match": round((vs + ps) / 2, 4)},
            "verdict": verdict}


# ================================================================ palette
def _rgb_stats(rgb):
    r = np.asarray(rgb[:3], float)
    mx, mn = r.max(), r.min()
    sat = (mx - mn) / mx if mx > 1e-6 else 0.0
    lum = float(r @ _LUMA)
    return float(sat), lum


def _principled(mat):
    if not mat or not mat.node_tree:
        return None
    for n in mat.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    return None


def _trace_colour(mat, sock, depth=0):
    """Best-effort constant colour feeding a socket -> (rgb, how)."""
    links = [l for l in mat.node_tree.links if l.to_socket == sock]
    if not links:
        return tuple(sock.default_value)[:3], "constant"
    n = links[0].from_node
    if depth > 4:
        return None, f"driven by {n.bl_idname}"
    if n.type == "RGB":
        return tuple(n.outputs[0].default_value)[:3], "RGB node"
    if n.bl_idname == "ShaderNodeMix" and getattr(n, "data_type", "") == "RGBA":
        rgb, how = _trace_colour(mat, n.inputs[6], depth + 1)
        return rgb, f"Mix A ({how}); mixed by a mask"
    if n.type == "MIX_RGB":
        rgb, how = _trace_colour(mat, n.inputs[1], depth + 1)
        return rgb, f"MixRGB Color1 ({how}); mixed by a mask"
    return None, f"driven by {n.bl_idname}"


def _nearest_metal(rgb):
    from forgekit.materials import METAL_F0
    r = np.asarray(rgb, float)
    best = min(METAL_F0.items(), key=lambda kv: np.linalg.norm(np.array(kv[1]) - r))
    return best[0], round(float(np.linalg.norm(np.array(best[1]) - r)), 4)


def colour_palette_check(obj_or_material):
    """Base-colour sanity per material (object = all its slots).

    Per material: base colour (linear), HSV saturation, linear luminance Y,
    metallic, how the colour was found, nearest METAL_F0 entry for metals, and
    flags:
      DIELECTRIC_GREY_METAL  metallic, Y < 0.45, saturation < 0.12. Every bare
                             metal in METAL_F0 has Y >= 0.5; a grey this dark is
                             a dielectric value painted on a metal (the classic
                             mistake). Deliberate oxide/coated finishes (blued
                             steel, gunmetal) also trip it: judge, don't obey.
      DARK_METAL             metallic, Y < 0.45 but coloured.
      PARTIAL_METALLIC       0.1 < metallic < 0.9 (real surfaces are 0 or 1).
      ALBEDO_TOO_DARK        dielectric Y < 0.01 (below charcoal, ~0.02-0.04).
      ALBEDO_TOO_BRIGHT      dielectric Y > 0.90 (above fresh snow).
      OVERSATURATED          saturation > 0.90 with Y > 0.15 (neon, CG look).
      COLOUR_UNKNOWN         colour is texture/procedural driven; not judged.
    Returns {"materials": [...], "flagged": n, "ok": bool}.
    """
    import bpy
    if isinstance(obj_or_material, bpy.types.Material):
        mats = [obj_or_material]
    else:
        mats = [s.material for s in obj_or_material.material_slots if s.material]
    rows = []
    for m in mats:
        b = _principled(m)
        row = {"material": m.name, "flags": []}
        if b is None:
            row["flags"].append("NO_PRINCIPLED")
            rows.append(row)
            continue
        rgb, how = _trace_colour(m, b.inputs["Base Color"])
        met_links = [l for l in m.node_tree.links if l.to_socket == b.inputs["Metallic"]]
        metallic = None if met_links else float(b.inputs["Metallic"].default_value)
        row.update(metallic=metallic, colour_source=how)
        if rgb is None:
            row["flags"].append("COLOUR_UNKNOWN")
            rows.append(row)
            continue
        sat, lum = _rgb_stats(rgb)
        row.update(base_colour=[round(float(c), 4) for c in rgb], saturation=round(sat, 4),
                   luminance=round(lum, 4))
        is_metal = metallic is not None and metallic >= 0.5
        if metallic is not None and 0.1 < metallic < 0.9:
            row["flags"].append("PARTIAL_METALLIC")
        if is_metal:
            row["nearest_metal"], row["nearest_metal_dist"] = _nearest_metal(rgb)
            if lum < 0.45:
                row["flags"].append("DIELECTRIC_GREY_METAL" if sat < 0.12 else "DARK_METAL")
        elif metallic is not None:
            if lum < 0.01:
                row["flags"].append("ALBEDO_TOO_DARK")
            if lum > 0.90:
                row["flags"].append("ALBEDO_TOO_BRIGHT")
        if sat > 0.90 and lum > 0.15:
            row["flags"].append("OVERSATURATED")
        rows.append(row)
    n = sum(1 for r in rows if r["flags"])
    return {"materials": rows, "flagged": n, "ok": n == 0}
