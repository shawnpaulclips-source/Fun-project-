"""'What it looks like versus what I made': measurable image differences.

An agent hands in a reference image and its own render and gets back numbers it
can act on - never pixels:

    from forgekit import compare as CMP
    d = CMP.compare_renders("ref.png", "mine.png")
    d["silhouette_iou"]          # 1.0 = same outline
    d["value"]["band_delta"]     # +shadow means mine is darker than the reference
    w = CMP.localise_error("ref.png", "mine.png", grid=(4, 4))
    w["worst"]                   # the cell and the kind of disagreement there

Pure numpy. No bpy import at module level, so the MCP server process (which
must never hold bpy) can use it. Images load with Pillow when present, else a
built-in PNG decoder (8-bit and 16-bit, grey/RGB/RGBA/palette, non-interlaced).

Conventions:
  * Images are compared at the reference's resolution; the candidate is
    resampled (bilinear) if the sizes differ. `resampled` says so.
  * Luminance is Rec.709 on the stored (display-encoded) values, 0..1, which is
    what a person judging value structure looks at.
  * Value bands: shadow < 0.25 <= mid < 0.75 <= highlight, measured inside each
    image's own silhouette (the object's value structure, not the backdrop).
"""
import os, zlib, struct
import numpy as np

SHADOW, HIGHLIGHT = 0.25, 0.75
HIST_BINS = 32


# ----------------------------------------------------------------- image IO
def _decode_png(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG and Pillow is not installed")
    pos, idat, plte, trns = 8, [], None, None
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            plte = np.frombuffer(body, np.uint8).reshape(-1, 3)
        elif typ == b"tRNS":
            trns = np.frombuffer(body, np.uint8)
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"IEND":
            break
        pos += 12 + ln
    if interlace:
        raise ValueError(f"{path}: interlaced PNG needs Pillow")
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = max(1, chans * depth // 8)
    stride = (w * chans * depth + 7) // 8
    raw = np.frombuffer(zlib.decompress(b"".join(idat)), np.uint8).reshape(h, stride + 1)
    out = np.zeros((h, stride), np.uint8)
    prev = np.zeros(stride, np.uint8)
    for y in range(h):
        ft, line = raw[y, 0], raw[y, 1:].copy()
        if ft == 1:
            for i in range(bpp, stride, bpp):
                line[i:i + bpp] = line[i:i + bpp] + line[i - bpp:i]
        elif ft == 2:
            line = line + prev
        elif ft == 3:
            for i in range(stride):
                left = int(line[i - bpp]) if i >= bpp else 0
                line[i] = (int(line[i]) + ((left + int(prev[i])) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = int(line[i - bpp]) if i >= bpp else 0
                c = int(prev[i - bpp]) if i >= bpp else 0
                b = int(prev[i])
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (int(line[i]) + pr) & 0xFF
        out[y] = line
        prev = line
    if depth == 16:
        arr = out.view(">u2").reshape(h, w, chans).astype(np.float32) / 65535.0
    elif depth == 8:
        arr = out.reshape(h, w, chans).astype(np.float32) / 255.0
    else:
        bits = np.unpackbits(out, axis=1)[:, :w * depth].reshape(h, w, depth)
        vals = (bits * (1 << np.arange(depth - 1, -1, -1))).sum(-1)
        if ctype == 3:
            arr = vals[..., None].astype(np.float32)
        else:
            arr = (vals / float((1 << depth) - 1))[..., None].astype(np.float32)
            chans = 1
    if ctype == 3:
        idx = (arr[..., 0] * 255.0).round().astype(int) if depth == 8 else arr[..., 0].astype(int)
        rgb = plte[idx].astype(np.float32) / 255.0
        if trns is not None:
            alpha = np.ones(len(plte), np.float32)
            alpha[:len(trns)] = trns / 255.0
            return np.dstack([rgb, alpha[idx]])
        return rgb
    if chans == 1:
        return np.repeat(arr, 3, axis=2)
    if chans == 2:
        return np.dstack([np.repeat(arr[..., :1], 3, axis=2), arr[..., 1:2]])
    return arr


def load_image(path_or_array):
    """Return float32 HxWxC (C=3 or 4) in 0..1. Accepts a path or an array."""
    if isinstance(path_or_array, np.ndarray):
        a = path_or_array.astype(np.float32)
        if a.max() > 1.0:
            a = a / 255.0
        if a.ndim == 2:
            a = np.repeat(a[..., None], 3, axis=2)
        return a
    try:
        from PIL import Image
        im = Image.open(path_or_array)
        mode = "RGBA" if ("A" in im.getbands() or im.info.get("transparency") is not None) else "RGB"
        return np.asarray(im.convert(mode), dtype=np.float32) / 255.0
    except ImportError:
        return _decode_png(path_or_array)


def write_png(path, arr):
    """Write an 8-bit PNG from a float 0..1 or uint8 array (HxW, HxWx3, HxWx4)."""
    a = np.asarray(arr)
    if a.dtype != np.uint8:
        a = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    if a.ndim == 2:
        a = a[..., None]
    h, w, c = a.shape
    ctype = {1: 0, 3: 2, 4: 6}[c]
    raw = b"".join(b"\x00" + a[y].tobytes() for y in range(h))

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    return path


def _resize(a, h, w):
    """Bilinear resample HxWxC to h x w."""
    H, W = a.shape[:2]
    if (H, W) == (h, w):
        return a
    ys = (np.arange(h) + 0.5) * H / h - 0.5
    xs = (np.arange(w) + 0.5) * W / w - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, H - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, W - 1)
    y1, x1 = np.clip(y0 + 1, 0, H - 1), np.clip(x0 + 1, 0, W - 1)
    fy = np.clip(ys - y0, 0, 1)[:, None, None]
    fx = np.clip(xs - x0, 0, 1)[None, :, None]
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bot = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return top * (1 - fy) + bot * fy


def luminance(img):
    return img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722


# ----------------------------------------------------------------- silhouette
def _silhouette(img, bg_threshold=0.08):
    """(mask, method). Alpha if it carries information, else distance from the
    border-estimated background colour."""
    if img.shape[2] == 4 and img[..., 3].min() < 0.98:
        return img[..., 3] > 0.5, "alpha"
    rgb = img[..., :3]
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(-1))
    return dist > bg_threshold, "background"


def silhouette_from_render(path, bg_threshold=0.08, return_method=False):
    """Boolean HxW mask of the subject. Uses alpha when the render has a
    transparent film, otherwise thresholds distance from the background colour
    (median of the 1-px image border) at bg_threshold (0..1 RGB distance).

    The background route needs subject/background contrast above bg_threshold:
    a dark object on a dark backdrop vanishes. Render candidates with a
    transparent film (render_pair does) and the problem is gone on that side."""
    m, how = _silhouette(load_image(path), bg_threshold)
    return (m, how) if return_method else m


# ----------------------------------------------------------------- edges
def _sobel(lum):
    p = np.pad(lum, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    return np.hypot(gx, gy) / 8.0


def edge_map(img, mask=None, rel=0.15, floor=0.02):
    """Edges = luminance gradient above max(floor, rel * 99th-percentile gradient),
    plus the silhouette boundary if a mask is given."""
    g = _sobel(luminance(img))
    thr = max(floor, rel * float(np.percentile(g, 99)))
    e = g > thr
    if mask is not None:
        e |= mask ^ _erode(mask, 1)
    return e


def _dilate(m, r):
    out = m.copy()
    for _ in range(r):
        p = np.pad(out, 1)
        out = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return out


def _erode(m, r):
    return ~_dilate(~m, r)


# ----------------------------------------------------------------- compare
def _pair(reference_path, candidate_path):
    ref = load_image(reference_path)
    cand = load_image(candidate_path)
    resampled = ref.shape[:2] != cand.shape[:2]
    if resampled:
        cand = _resize(cand, *ref.shape[:2])
    return ref, cand, resampled


def _value_structure(lum, mask):
    v = lum[mask] if mask.any() else lum.reshape(-1)
    hist = np.histogram(v, bins=HIST_BINS, range=(0.0, 1.0))[0].astype(np.float64)
    hist /= max(hist.sum(), 1.0)
    bands = np.array([(v < SHADOW).mean(), ((v >= SHADOW) & (v < HIGHLIGHT)).mean(), (v >= HIGHLIGHT).mean()])
    return hist, bands, float(v.mean()), float(v.std())


def compare_renders(reference_path, candidate_path, bg_threshold=0.08, edge_tolerance_px=2):
    """Measurable differences between a reference image and a candidate render.

    Returns plain numbers:
      silhouette_iou        intersection/union of the two subject masks (1 = identical)
      silhouette            {ref_coverage, cand_coverage, extra_frac, missing_frac,
                             centroid_shift_px (dx, dy), method}
                            extra = candidate-only pixels / ref area; missing = ref-only / ref area
      value                 {hist_emd (0..1, earth-mover between luminance histograms),
                             hist_l1 (0..2), ref_bands, cand_bands, band_delta (cand-ref,
                             shadow/mid/highlight fractions), mean_delta, contrast_delta}
      edges                 {precision, recall, f1} of edge maps with +-edge_tolerance_px
      size                  reference (w, h); resampled: whether the candidate was resized
    """
    ref, cand, resampled = _pair(reference_path, candidate_path)
    mr, how_r = _silhouette(ref, bg_threshold)
    mc, how_c = _silhouette(cand, bg_threshold)
    inter = float((mr & mc).sum())
    union = float((mr | mc).sum())
    area_r = max(float(mr.sum()), 1.0)
    iou = inter / union if union else 1.0

    def centroid(m):
        ys, xs = np.nonzero(m)
        return (float(xs.mean()), float(ys.mean())) if len(xs) else (0.0, 0.0)

    cr, cc = centroid(mr), centroid(mc)
    lr, lc = luminance(ref), luminance(cand)
    hr, br, mean_r, std_r = _value_structure(lr, mr)
    hc, bc, mean_c, std_c = _value_structure(lc, mc)
    emd = float(np.abs(np.cumsum(hr) - np.cumsum(hc)).sum() / HIST_BINS)

    er, ec = edge_map(ref, mr), edge_map(cand, mc)
    dr, dc = _dilate(er, edge_tolerance_px), _dilate(ec, edge_tolerance_px)
    prec = float((ec & dr).sum() / max(ec.sum(), 1))
    rec = float((er & dc).sum() / max(er.sum(), 1))
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    r4 = lambda x: round(float(x), 4)
    return {
        "silhouette_iou": r4(iou),
        "silhouette": {
            "ref_coverage": r4(mr.mean()), "cand_coverage": r4(mc.mean()),
            "extra_frac": r4((mc & ~mr).sum() / area_r),
            "missing_frac": r4((mr & ~mc).sum() / area_r),
            "centroid_shift_px": (round(cc[0] - cr[0], 2), round(cc[1] - cr[1], 2)),
            "method": {"reference": how_r, "candidate": how_c},
        },
        "value": {
            "hist_emd": r4(emd), "hist_l1": r4(np.abs(hr - hc).sum()),
            "ref_bands": [r4(x) for x in br], "cand_bands": [r4(x) for x in bc],
            "band_delta": [r4(x) for x in (bc - br)],
            "mean_delta": r4(mean_c - mean_r), "contrast_delta": r4(std_c - std_r),
        },
        "edges": {"precision": r4(prec), "recall": r4(rec), "f1": r4(f1)},
        "size": (ref.shape[1], ref.shape[0]),
        "resampled": resampled,
    }


def localise_error(reference_path, candidate_path, grid=(4, 4), bg_threshold=0.08,
                   edge_tolerance_px=2, top=3):
    """Split both images into grid=(rows, cols) cells; report, per cell, the
    largest disagreement and what kind it is, so an agent learns WHERE it is wrong.

    Per cell: silhouette XOR fraction (split into extra/missing), mean absolute
    luminance difference where BOTH images have subject, and interior-edge
    disagreement (1 - F1, outline excluded, weighted 0.5 and scaled by edge
    density so a few stray edge pixels cannot dominate). Each kind of error is
    counted once: a shifted outline is a silhouette error, not also a value error.
    The three are on comparable 0..1 scales; `kind` names the biggest and `hint`
    says which direction to move. Cells are row-major from the TOP-LEFT of the
    image. Returns {"grid", "cells": [...], "worst": [top-N cells]}.
    """
    ref, cand, _ = _pair(reference_path, candidate_path)
    rows, cols = grid
    mr, _ = _silhouette(ref, bg_threshold)
    mc, _ = _silhouette(cand, bg_threshold)
    lr, lc = luminance(ref), luminance(cand)
    # Interior edges only, inside the region both images cover: the outline is
    # already scored by the silhouette term and must not be counted twice.
    both = _erode(mr & mc, edge_tolerance_px + 1)
    er, ec = edge_map(ref) & both, edge_map(cand) & both
    dr, dc = _dilate(er, edge_tolerance_px), _dilate(ec, edge_tolerance_px)
    H, W = mr.shape
    ys = np.linspace(0, H, rows + 1).round().astype(int)
    xs = np.linspace(0, W, cols + 1).round().astype(int)
    cells = []
    for i in range(rows):
        for j in range(cols):
            sl = (slice(ys[i], ys[i + 1]), slice(xs[j], xs[j + 1]))
            a_r, a_c = mr[sl], mc[sl]
            n = a_r.size
            extra = float((a_c & ~a_r).sum() / n)
            missing = float((a_r & ~a_c).sum() / n)
            u = a_r & a_c
            if u.any():
                dl = lc[sl][u] - lr[sl][u]
                lum_err, lum_bias = float(np.abs(dl).mean()), float(dl.mean())
            else:
                lum_err = lum_bias = 0.0
            e_r, e_c = er[sl], ec[sl]
            if e_r.any() or e_c.any():
                p = (e_c & dr[sl]).sum() / max(e_c.sum(), 1)
                r = (e_r & dc[sl]).sum() / max(e_r.sum(), 1)
                edge_err = float(1 - (2 * p * r / (p + r) if (p + r) else 0.0))
                edge_bias = int(e_c.sum()) - int(e_r.sum())
            else:
                edge_err, edge_bias = 0.0, 0
            # A cell with a handful of edge pixels must not outrank a real outline
            # error, so edge disagreement is scaled by edge density (full weight
            # once edges cover 5 % of the cell).
            density = min(1.0, float(e_r.sum() + e_c.sum()) / (2 * 0.05 * n))
            scores = {"silhouette": extra + missing, "value": lum_err, "edges": edge_err * 0.5 * density}
            kind = max(scores, key=scores.get)
            if kind == "silhouette":
                hint = "candidate has extra mass here" if extra >= missing else "candidate is missing mass here"
            elif kind == "value":
                hint = "candidate too bright here" if lum_bias > 0 else "candidate too dark here"
            else:
                hint = "candidate has extra edges/detail here" if edge_bias > 0 else "candidate lacks edges/detail here"
            cells.append({
                "row": i, "col": j, "bbox_xywh": (int(xs[j]), int(ys[i]), int(xs[j + 1] - xs[j]), int(ys[i + 1] - ys[i])),
                "kind": kind, "score": round(scores[kind], 4), "hint": hint if scores[kind] > 0.005 else "agrees",
                "silhouette_extra": round(extra, 4), "silhouette_missing": round(missing, 4),
                "value_abs": round(lum_err, 4), "value_bias": round(lum_bias, 4), "edge_err": round(edge_err, 4),
            })
    worst = sorted(cells, key=lambda c: -c["score"])[:top]
    return {"grid": (rows, cols), "size": (W, H), "cells": cells, "worst": worst}


# ----------------------------------------------------------------- render + compare
def render_pair(obj, reference_path, camera_loc, target=None, lens=50.0, engine="workbench",
                out_path=None, isolate=True, resolution=None, samples=16, ortho_scale=None):
    """Render `obj` from a matched camera and compare it with the reference.

    The candidate is rendered at the reference's resolution (or `resolution`)
    with a transparent film, so its silhouette comes from alpha. Camera, engine,
    resolution, film and object visibility are restored afterwards. target
    defaults to obj's world bounding-box centre. Pass ortho_scale to render
    orthographic (matching an orthographic reference).
    Returns compare_renders(...) plus "candidate_path", "render_seconds" and "localise".
    """
    import bpy, tempfile
    from mathutils import Vector
    from forgekit import render as RN

    ref = load_image(reference_path)
    h, w = ref.shape[:2]
    if resolution:
        w, h = resolution
    sc = bpy.context.scene
    saved = dict(camera=sc.camera, engine=sc.render.engine, rx=sc.render.resolution_x,
                 ry=sc.render.resolution_y, pct=sc.render.resolution_percentage,
                 film=sc.render.film_transparent, path=sc.render.filepath)
    hidden = []
    if target is None:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        target = tuple(sum(c[i] for c in corners) / 8 for i in range(3))
    cam_data = bpy.data.cameras.new("ForgeCompareCam")
    cam_data.lens = lens
    if ortho_scale:
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = ortho_scale
    cam = bpy.data.objects.new("ForgeCompareCam", cam_data)
    sc.collection.objects.link(cam)
    cam.location = camera_loc
    direction = Vector(target) - Vector(camera_loc)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    out_path = out_path or os.path.join(tempfile.mkdtemp(prefix="forge_cmp_"), "candidate.png")
    try:
        if isolate:
            for o in sc.objects:
                if o is not obj and o.type in ("MESH", "CURVE", "SURFACE", "META", "FONT") and not o.hide_render:
                    o.hide_render = True
                    hidden.append(o)
        sc.camera = cam
        if engine == "workbench":
            RN.workbench()
        else:
            RN.configure(engine="CYCLES", samples=samples, resolution=(w, h), transparent=True)
        sc.render.resolution_x, sc.render.resolution_y = w, h
        sc.render.resolution_percentage = 100
        sc.render.film_transparent = True
        sc.render.image_settings.color_mode = "RGBA"
        path, secs = RN.shot(out_path, verbose=False)
    finally:
        for o in hidden:
            o.hide_render = False
        sc.camera = saved["camera"]
        sc.render.engine = saved["engine"]
        sc.render.resolution_x, sc.render.resolution_y = saved["rx"], saved["ry"]
        sc.render.resolution_percentage = saved["pct"]
        sc.render.film_transparent = saved["film"]
        sc.render.filepath = saved["path"]
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    res = compare_renders(reference_path, path)
    res["candidate_path"] = path
    res["render_seconds"] = round(secs, 3)
    res["localise"] = localise_error(reference_path, path)["worst"]
    return res
