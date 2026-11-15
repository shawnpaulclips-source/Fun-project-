"""formq - blind form-quality metrics for organic meshes.

Everything here is computed from geometry, not from a render, so an agent can
score a surface without looking at it. Numpy + mathutils only.

    from formq import arrays, topology, smoothness, deformation, rubric

Conventions
-----------
* All metrics read the *evaluated* mesh (modifiers applied: armature, subsurf),
  in world space, because that is what renders and exports.
* Smoothness numbers are density-dependent. Compare meshes at matched face
  counts (within ~25 %), or the denser one wins for free.
"""
import math
import numpy as np
import bpy
from mathutils.bvhtree import BVHTree


# --------------------------------------------------------------------- extract
def arrays(ob, evaluated=True):
    """Return dict with V (n,3) world verts, T (m,3) triangles, polys (list of
    vertex index tuples), E (k,2) edges. Uses foreach_get, ~10 ms per 50k verts."""
    if evaluated:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
    else:
        me = ob.data
    n = len(me.vertices)
    V = np.empty(n * 3, np.float64)
    me.vertices.foreach_get("co", V)
    V = V.reshape(-1, 3)
    M = np.array(ob.matrix_world)
    V = V @ M[:3, :3].T + M[:3, 3]
    me.calc_loop_triangles()
    T = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("vertices", T)
    T = T.reshape(-1, 3)
    E = np.empty(len(me.edges) * 2, np.int64)
    me.edges.foreach_get("vertices", E)
    E = E.reshape(-1, 2)
    ls = np.empty(len(me.polygons), np.int64)
    me.polygons.foreach_get("loop_total", ls)
    lv = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get("vertex_index", lv)
    out = {"V": V, "T": T, "E": E, "poly_sizes": ls, "loop_verts": lv}
    if evaluated:
        ev.to_mesh_clear()
    return out


def _polys(A):
    ls, lv = A["poly_sizes"], A["loop_verts"]
    off = np.concatenate([[0], np.cumsum(ls)])
    return [lv[off[i]:off[i + 1]] for i in range(len(ls))]


# --------------------------------------------------------------------- geometry
def face_normals(V, T):
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a, 1e-30)[:, None], a * 0.5


def vertex_normals(V, T):
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, T[:, k], n)
    return vn / np.maximum(np.linalg.norm(vn, axis=1), 1e-30)[:, None]


def volume(V, T):
    return float(np.einsum("ij,ij->i", V[T[:, 0]],
                           np.cross(V[T[:, 1]], V[T[:, 2]])).sum() / 6.0)


def tri_adjacency(T):
    """Pairs of triangles sharing an edge -> (f1, f2, va, vb)."""
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    f = np.tile(np.arange(len(T)), 3)
    k = np.sort(e, axis=1)
    key = k[:, 0] * (k.max() + 1) + k[:, 1]
    o = np.argsort(key, kind="stable")
    key, f, k = key[o], f[o], k[o]
    same = key[1:] == key[:-1]
    i = np.nonzero(same)[0]
    return f[i], f[i + 1], k[i, 0], k[i, 1]


def cotan_laplacian(V, T):
    """Cotangent Laplacian as COO lists + Voronoi-ish (barycentric) vertex areas."""
    n = len(V)
    I, J, W = [], [], []
    for a, b, c in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
        u = V[T[:, b]] - V[T[:, a]]
        w = V[T[:, c]] - V[T[:, a]]
        cot = np.einsum("ij,ij->i", u, w) / np.maximum(np.linalg.norm(np.cross(u, w), axis=1), 1e-30)
        cot = np.clip(cot, -1e3, 1e3) * 0.5
        I += [T[:, b], T[:, c]]
        J += [T[:, c], T[:, b]]
        W += [cot, cot]
    I = np.concatenate(I); J = np.concatenate(J); W = np.concatenate(W)
    _, fa = face_normals(V, T)
    area = np.zeros(n)
    for k in range(3):
        np.add.at(area, T[:, k], fa / 3.0)
    return I, J, W, np.maximum(area, 1e-30)


def mean_curvature(V, T):
    """Signed mean curvature per vertex (1/m) from the cotan Laplacian."""
    I, J, W, area = cotan_laplacian(V, T)
    Lx = np.zeros_like(V)
    np.add.at(Lx, I, W[:, None] * (V[J] - V[I]))
    Hn = Lx / (2.0 * area[:, None])
    vn = vertex_normals(V, T)
    return -np.einsum("ij,ij->i", Hn, vn), area


# --------------------------------------------------------------------- metrics
def topology(ob, evaluated=False):
    """Topology health of the CAGE (evaluated=False) or the final surface.

    poles = interior vertices with valence != 4. On a quad mesh every pole is a
    place where the limit surface loses G2 under Catmull-Clark; you want few,
    deliberately placed ones (fingertip caps, knuckle corners), not thousands.
    """
    A = arrays(ob, evaluated)
    V, E = A["V"], A["E"]
    ps = A["poly_sizes"]
    nv = len(V)
    val = np.bincount(E.ravel(), minlength=nv)
    # boundary vertices: on an edge used by one face
    polys = _polys(A)
    ecount = {}
    for p in polys:
        for i in range(len(p)):
            a, b = int(p[i]), int(p[(i + 1) % len(p)])
            k = (a, b) if a < b else (b, a)
            ecount[k] = ecount.get(k, 0) + 1
    bverts = set()
    nonman = 0
    for (a, b), c in ecount.items():
        if c == 1:
            bverts.update((a, b))
        elif c > 2:
            nonman += 1
    interior = np.array([i for i in range(nv) if i not in bverts], np.int64)
    iv = val[interior] if len(interior) else np.array([4])
    hist = {int(k): int(v) for k, v in zip(*np.unique(iv, return_counts=True))}
    poles = int((iv != 4).sum())
    # quad quality: corner angle deviation from 90 degrees
    devs = []
    for p in polys:
        if len(p) != 4:
            continue
        P = V[p]
        for i in range(4):
            u = P[(i + 1) % 4] - P[i]
            w = P[(i - 1) % 4] - P[i]
            c = np.dot(u, w) / max(np.linalg.norm(u) * np.linalg.norm(w), 1e-30)
            devs.append(abs(math.degrees(math.acos(max(-1, min(1, c)))) - 90))
    el = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
    return {
        "verts": nv, "faces": int(len(ps)),
        "quad_frac": round(float((ps == 4).mean()), 4),
        "tri_frac": round(float((ps == 3).mean()), 4),
        "ngons": int((ps > 4).sum()),
        "valence_hist": hist,
        "poles": poles,
        "pole_frac": round(poles / max(len(iv), 1), 4),
        "quad_angle_dev_mean": round(float(np.mean(devs)), 2) if devs else None,
        "quad_angle_dev_p95": round(float(np.percentile(devs, 95)), 2) if devs else None,
        "edge_len_cv": round(float(el.std() / el.mean()), 4),
        "non_manifold_edges": nonman,
        "boundary_verts": len(bverts),
    }


def smoothness(ob, evaluated=True, view=(0.3, -1.0, 0.4), zebra_axis=(0, 0, 1), stripes=12):
    """How smooth the rendered surface reads, without rendering it.

    normal_dev_*   angle (deg) between each face normal and its edge-neighbours.
                   Kinks and voxel stair-steps live in the p99 tail.
    curv_jump      median |H_i - H_j| across edges / median |H|. Curvature
                   (G2) continuity. Remeshed blobs score high; a subdivided
                   quad cage scores low.
    zebra_jump     reflection-line test done numerically: stripe phase
                   phi = acos(r . axis) with r the reflected view vector; take
                   the per-face gradient of phi and measure how much it jumps
                   across edges relative to its median size. Jumps are exactly
                   what a modeller sees as broken zebra stripes.
    """
    A = arrays(ob, evaluated)
    V, T = A["V"], A["T"]
    fn, fa = face_normals(V, T)
    f1, f2, va, vb = tri_adjacency(T)
    # skip the diagonal of each quad (coplanar by construction) - keep real edges
    d = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", fn[f1], fn[f2]), -1, 1)))
    H, area = mean_curvature(V, T)
    medH = np.median(np.abs(H)) + 1e-12
    cj = np.abs(H[va] - H[vb]) / medH
    # zebra
    vn = vertex_normals(V, T)
    vdir = np.asarray(view, float); vdir /= np.linalg.norm(vdir)
    r = vdir - 2 * np.einsum("ij,j->i", vn, vdir)[:, None] * vn
    phi = np.arccos(np.clip(r @ (np.asarray(zebra_axis, float) / np.linalg.norm(zebra_axis)), -1, 1))
    # per-face gradient of a linear function on a triangle
    e1 = V[T[:, 1]] - V[T[:, 0]]; e2 = V[T[:, 2]] - V[T[:, 0]]
    d1 = phi[T[:, 1]] - phi[T[:, 0]]; d2 = phi[T[:, 2]] - phi[T[:, 0]]
    g11 = np.einsum("ij,ij->i", e1, e1); g12 = np.einsum("ij,ij->i", e1, e2); g22 = np.einsum("ij,ij->i", e2, e2)
    det = np.maximum(g11 * g22 - g12 * g12, 1e-30)
    a = (d1 * g22 - d2 * g12) / det; b = (d2 * g11 - d1 * g12) / det
    G = a[:, None] * e1 + b[:, None] * e2
    gm = np.median(np.linalg.norm(G, axis=1)) + 1e-12
    zj = np.linalg.norm(G[f1] - G[f2], axis=1) / gm
    return {
        "normal_dev_p50": round(float(np.percentile(d, 50)), 3),
        "normal_dev_p95": round(float(np.percentile(d, 95)), 3),
        "normal_dev_p99": round(float(np.percentile(d, 99)), 3),
        "curv_jump_p50": round(float(np.median(cj)), 4),
        "curv_jump_p95": round(float(np.percentile(cj, 95)), 4),
        "zebra_jump_p50": round(float(np.median(zj)), 4),
        "zebra_jump_p95": round(float(np.percentile(zj, 95)), 4),
        "tris": int(len(T)),
    }


class RestCache:
    """Snapshot of the rest pose for deformation comparisons."""

    def __init__(self, ob, joints=None, radius=None):
        A = arrays(ob, True)
        self.V, self.T, self.E = A["V"], A["T"], A["E"]
        self.vol = volume(self.V, self.T)
        self.elen = np.linalg.norm(self.V[self.E[:, 0]] - self.V[self.E[:, 1]], axis=1)
        _, self.farea = face_normals(self.V, self.T)
        self.regions = {}
        # joints: {name: (centre_world (3,), radius)} -> faces near that joint
        for name, (c, r) in (joints or {}).items():
            cen = self.V[self.T].mean(axis=1)
            m = np.linalg.norm(cen - np.asarray(c), axis=1) < r
            self.regions[name] = (np.nonzero(m)[0], np.asarray(c, float))


def _cone_volume(V, T, faces, c):
    t = T[faces]
    a, b, d = V[t[:, 0]] - c, V[t[:, 1]] - c, V[t[:, 2]] - c
    return float(np.einsum("ij,ij->i", a, np.cross(b, d)).sum() / 6.0)


def deformation(ob, rest, joint_centres=None):
    """Damage done by the current pose relative to `rest` (a RestCache).

    volume_ratio        whole-mesh volume / rest. < 0.97 on a single joint bend
                        means something is collapsing.
    self_intersections  BVH triangle pairs that cross (adjacent pairs excluded).
    edge_strain_p99     99th percentile |len/len_rest - 1|. Rubber-sheeting.
    collapsed_faces     triangles whose area fell below 25 % of rest.
    joint_volume[j]     local volume of the surface patch around joint j, as a
                        cone from the (posed) joint centre: the per-knuckle
                        "did it pinch" number. Ratio to rest.
    """
    A = arrays(ob, True)
    V, T = A["V"], A["T"]
    vol = volume(V, T)
    tree = BVHTree.FromPolygons([tuple(v) for v in V], [tuple(t) for t in T], all_triangles=True)
    si = len(tree.overlap(tree))
    el = np.linalg.norm(V[rest.E[:, 0]] - V[rest.E[:, 1]], axis=1)
    strain = np.abs(el / np.maximum(rest.elen, 1e-12) - 1)
    _, fa = face_normals(V, T)
    out = {
        "volume_ratio": round(vol / rest.vol, 4),
        "self_intersections": si,
        "edge_strain_p99": round(float(np.percentile(strain, 99)), 4),
        "collapsed_faces": int((fa < 0.25 * rest.farea).sum()),
    }
    if rest.regions:
        jv = {}
        for name, (faces, c0) in rest.regions.items():
            c = np.asarray((joint_centres or {}).get(name, c0), float)
            v0 = _cone_volume(rest.V, rest.T, faces, c0)
            v1 = _cone_volume(V, T, faces, c)
            jv[name] = round(v1 / v0, 4) if abs(v0) > 1e-15 else None
        out["joint_volume"] = jv
        vals = [v for v in jv.values() if v is not None]
        out["joint_volume_min"] = round(min(vals), 4) if vals else None
    return out


# --------------------------------------------------------------------- rubric
def _band(x, good, bad):
    """Map x to 0..1, 1 at `good`, 0 at `bad`, linear between (either direction)."""
    if x is None:
        return 0.0
    t = (x - bad) / (good - bad)
    return float(max(0.0, min(1.0, t)))


RUBRIC = {
    # metric: (weight, good, bad)
    "quad_frac":            (6,  1.00, 0.50),
    "pole_frac":            (6,  0.03, 0.40),
    "quad_angle_dev_p95":   (3,  15.0, 45.0),
    "normal_dev_p99":       (8,  4.0, 25.0),
    "curv_jump_p95":        (8,  1.0, 8.0),
    "zebra_jump_p95":       (8,  1.0, 8.0),
    "knuckle_isect_mean":   (10, 0, 120),   # mean over single-joint 90 deg bends
    "full_curl_isect":      (4,  300, 3000), # all fingers 90/90/90: includes legit tip-palm contact
    "curl_joint_vol_min":   (14, 0.92, 0.55),
    "curl_vol_ratio_min":   (5,  0.995, 0.95),
    "curl_strain_p99_max":  (6,  0.35, 1.20),
    "twist_vol_min":        (8,  0.92, 0.55),
    "proportion_err":       (8,  0.03, 0.30),
    "pose_battery_isect":   (6,  0, 600),
    "pose_crease_gain":     (6,  2.0, 20.0),   # deg added to normal_dev_p99 by posing
}


def rubric(m):
    """Weighted 0-100 form-quality score from a flat metric dict. Returns
    (score, per-metric subscores). Missing metrics score 0 - that is deliberate:
    an asset that cannot be posed has no deformation quality."""
    tot = sum(w for w, _, _ in RUBRIC.values())
    sub = {}
    s = 0.0
    for k, (w, good, bad) in RUBRIC.items():
        v = _band(m.get(k), good, bad)
        sub[k] = round(v * w, 2)
        s += v * w
    return round(100 * s / tot, 1), sub
