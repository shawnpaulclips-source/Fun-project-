"""Ornament: guilloche, arc-length placement, twist-free frames, mouldings.

Design rules (research/04 sections 2, 3, 7):
- Placement is pure numpy math; bpy is imported only to build geometry or to
  query a surface. Everything above "surface" runs without Blender.
- Every closed loop closes exactly: N pieces, N gaps, the seam reported.
- Frames use parallel transport with holonomy correction, so a ring of pieces
  never twists at the seam.

Conventions: points are numpy arrays (M, 2) or (M, 3); closed loops do NOT
repeat the first point at the end.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from math import gcd

import numpy as np

# ---------------------------------------------------------------- data
# research/04 section 7, nine points each, normalised (x, y) in [0, 1].
MOULDINGS = {
    # cubic Bezier (0,0),(0.5,0),(0.5,1),(1,1)
    "ogee": [(0.0000, 0.0000), (0.1660, 0.0430), (0.2969, 0.1562), (0.4043, 0.3164),
             (0.5000, 0.5000), (0.5957, 0.6836), (0.7031, 0.8438), (0.8340, 0.9570),
             (1.0000, 1.0000)],
    # transpose of the ogee
    "cyma_reversa": [(0.0000, 0.0000), (0.0430, 0.1660), (0.1562, 0.2969), (0.3164, 0.4043),
                     (0.5000, 0.5000), (0.6836, 0.5957), (0.8438, 0.7031), (0.9570, 0.8340),
                     (1.0000, 1.0000)],
    # x = sin t, y = 1 - cos t (convex quarter round)
    "ovolo": [(0.0000, 0.0000), (0.1951, 0.0192), (0.3827, 0.0761), (0.5556, 0.1685),
              (0.7071, 0.2929), (0.8315, 0.4444), (0.9239, 0.6173), (0.9808, 0.8049),
              (1.0000, 1.0000)],
    # x = 1 - cos t, y = sin t (concave quarter round)
    "scotia": [(0.0000, 0.0000), (0.0192, 0.1951), (0.0761, 0.3827), (0.1685, 0.5556),
               (0.2929, 0.7071), (0.4444, 0.8315), (0.6173, 0.9239), (0.8049, 0.9808),
               (1.0000, 1.0000)],
}
MOULDINGS["cyma_recta"] = MOULDINGS["ogee"]
MOULDINGS["cavetto"] = MOULDINGS["scotia"]
MOULDINGS["torus"] = MOULDINGS["ovolo"]


@dataclass(frozen=True)
class Frame:
    """Orthonormal frame on a curve: point, tangent T, normal N, binormal B = T x N."""
    point: np.ndarray
    tangent: np.ndarray
    normal: np.ndarray
    binormal: np.ndarray


@dataclass(frozen=True)
class SeamReport:
    """Quality of a closed placement.

    gap_ratio:       min gap / max gap; 1.0 is perfectly even.
    seam_gap_error:  |gap(last -> first) - mean| / mean.
    holonomy_deg:    twist plain parallel transport accumulates around the loop
                     (0 for planar loops). This is what correction removes.
    seam_error_deg:  excess frame twist across the seam relative to the
                     uniform twist rate elsewhere, after correction.
    """
    pieces: int
    gap_min: float
    gap_max: float
    gap_ratio: float
    seam_gap_error: float
    holonomy_deg: float
    seam_error_deg: float

    def as_dict(self):
        return {k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


# ---------------------------------------------------------------- guilloche
def guilloche_period(R, r):
    """Parameter period T = 2 pi r / gcd(R, r); integer radii only."""
    if not (isinstance(R, (int, np.integer)) and isinstance(r, (int, np.integer))
            and R > 0 and r > 0):
        raise ValueError("R and r must be positive integers for a closed period")
    return 2.0 * math.pi * r / gcd(int(R), int(r))


def _troch(R, r, d, th, variant):
    if variant == "epitrochoid":
        k = (R + r) / r
        return np.stack([(R + r) * np.cos(th) - d * np.cos(k * th),
                         (R + r) * np.sin(th) - d * np.sin(k * th)], -1)
    if variant == "hypotrochoid":
        k = (R - r) / r
        return np.stack([(R - r) * np.cos(th) + d * np.cos(k * th),
                         (R - r) * np.sin(th) - d * np.sin(k * th)], -1)
    raise ValueError("variant must be 'epitrochoid' or 'hypotrochoid'")


def guilloche_points(R, r, d, n_turns=None, samples=2048, variant="epitrochoid"):
    """Sample a guilloche (epitrochoid / hypotrochoid) curve, shape (samples, 2).

    R, r:     fixed / rolling circle radii. Integers give a closed curve.
    d:        pen offset from the rolling centre (d = r: epicycloid).
    n_turns:  None = exactly one closed period (integer R, r required).
              A number = sample n_turns * 2 pi of parameter (any R, r; closure
              not guaranteed; check with guilloche_closure).
    The seam point is not repeated.
    """
    if variant == "hypotrochoid" and r >= R:
        raise ValueError("hypotrochoid needs r < R")
    T = guilloche_period(R, r) if n_turns is None else 2.0 * math.pi * float(n_turns)
    th = T * np.arange(samples) / samples
    return _troch(R, r, d, th, variant)


def guilloche_closure(R, r, d, variant="epitrochoid", n_turns=None):
    """|P(T) - P(0)| for the period guilloche_points would use. ~1e-13 = closed."""
    T = guilloche_period(R, r) if n_turns is None else 2.0 * math.pi * float(n_turns)
    p = _troch(R, r, d, np.array([0.0, T]), variant)
    return float(np.linalg.norm(p[1] - p[0]))


# ---------------------------------------------------------------- resampling
def _resample_closed(points, n):
    Q = np.asarray(points, float)
    Qc = np.vstack([Q, Q[:1]])
    seg = np.linalg.norm(np.diff(Qc, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    L = cum[-1]
    s = L * np.arange(n) / n
    j = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(seg) - 1)
    t = np.where(seg[j] > 0, (s - cum[j]) / np.maximum(seg[j], 1e-300), 0.0)
    out = Qc[j] * (1 - t)[:, None] + Qc[j + 1] * t[:, None]
    return out, float(L)


def resample_closed_2d(points, n):
    """Resample a closed 2D polyline to n points even in arc length -> (pts, L)."""
    return _resample_closed(np.asarray(points, float)[:, :2], n)


def resample_closed_3d(points, n):
    """Resample a closed 3D polyline to n points even in arc length -> (pts, L)."""
    return _resample_closed(np.asarray(points, float)[:, :3], n)


def gap_stats(points, closed=True):
    """(min gap, max gap, min/max ratio, seam gap error) of a point sequence."""
    P = np.asarray(points, float)
    nxt = np.roll(P, -1, axis=0) if closed else P[1:]
    gaps = np.linalg.norm(nxt - (P if closed else P[:-1]), axis=1)
    mean = gaps.mean()
    return float(gaps.min()), float(gaps.max()), float(gaps.min() / gaps.max()), \
        float(abs(gaps[-1] - mean) / mean)


# ---------------------------------------------------------------- frames
def _unit(v):
    return v / max(np.linalg.norm(v), 1e-300)


def _reflect(v, axis, c):
    return v - (2.0 / c) * (axis @ v) * axis


def _transport(N, p0, p1, T0, T1):
    """Double reflection (Wang et al. 2008): carry normal N from p0 to p1."""
    v1 = p1 - p0
    c1 = v1 @ v1
    if c1 < 1e-30:
        return N
    rL = _reflect(N, v1, c1)
    tL = _reflect(T0, v1, c1)
    v2 = T1 - tL
    c2 = v2 @ v2
    rF = _reflect(rL, v2, c2) if c2 > 1e-24 else rL
    rF = rF - T1 * (rF @ T1)
    return _unit(rF)


def _signed_angle(a, b, axis):
    return math.atan2(axis @ np.cross(a, b), a @ b)


def _tangents(P, closed=True):
    if closed:
        T = np.roll(P, -1, axis=0) - np.roll(P, 1, axis=0)
    else:
        T = np.gradient(P, axis=0)
    return T / np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-300)


def frames_closed(points, up=None, correct=True, normals=None):
    """Parallel-transport frames around a closed 3D loop, holonomy corrected.

    points:  (M, 3) closed loop, seam not repeated (resample first for even
             spacing).
    up:      vector seeding the first normal (default world Z, or X if the
             tangent is vertical).
    correct: spread the seam twist linearly in arc length about the tangent.
    normals: optional (M, 3) surface normals; if given the first normal is
             seeded from normals[0] instead of `up`.

    Returns (frames, info). info has holonomy_deg (twist before correction),
    seam_error_deg (excess twist across the seam vs the uniform rate,
    ~0 after correction, == holonomy without), twist_rate_deg_per_m.
    """
    P = np.asarray(points, float)
    n = len(P)
    if n < 3:
        raise ValueError("need at least 3 points")
    T = _tangents(P, True)
    ref = np.asarray(normals[0] if normals is not None else
                     (up if up is not None else (0.0, 0.0, 1.0)), float)
    N0 = ref - T[0] * (ref @ T[0])
    if np.linalg.norm(N0) < 1e-6:
        alt = np.array([1.0, 0.0, 0.0])
        N0 = alt - T[0] * (alt @ T[0])
    N = np.empty_like(P)
    N[0] = _unit(N0)
    for i in range(n - 1):
        N[i + 1] = _transport(N[i], P[i], P[i + 1], T[i], T[i + 1])
    back = _transport(N[-1], P[-1], P[0], T[-1], T[0])
    hol = _signed_angle(N[0], back, T[0])          # radians, (-pi, pi]

    seg = np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)[:-1]])
    L = float(seg.sum())
    if correct:
        th = -hol * s / L
        c, sn = np.cos(th)[:, None], np.sin(th)[:, None]
        B = np.cross(T, N)
        N = N * c + B * sn
        N -= T * np.sum(N * T, axis=1, keepdims=True)
        N /= np.linalg.norm(N, axis=1, keepdims=True)
    B = np.cross(T, N)
    frames = [Frame(P[i].copy(), T[i].copy(), N[i].copy(), B[i].copy()) for i in range(n)]
    seam = twist_profile(frames)
    info = {"holonomy_deg": math.degrees(hol), "seam_error_deg": seam["seam_error_deg"],
            "max_step_error_deg": seam["max_step_error_deg"],
            "twist_rate_deg_per_m": seam["rate_deg_per_m"], "length": L}
    return frames, info


def twist_profile(frames):
    """Twist of each frame step relative to parallel transport.

    For every step i -> i+1 (including the seam step last -> first), the angle
    between frame i's normal carried to i+1 and frame i+1's normal. A clean set
    has the same twist per unit length everywhere; seam_error_deg is how far
    the seam step departs from that rate.
    """
    n = len(frames)
    tw, ds = np.empty(n), np.empty(n)
    for i in range(n):
        a, b = frames[i], frames[(i + 1) % n]
        Nt = _transport(a.normal, a.point, b.point, a.tangent, b.tangent)
        tw[i] = _signed_angle(Nt, b.normal, b.tangent)
        ds[i] = np.linalg.norm(b.point - a.point)
    rate = tw[:-1].sum() / max(ds[:-1].sum(), 1e-300)
    err = np.degrees(np.abs(tw - rate * ds))
    return {"seam_error_deg": float(err[-1]), "max_step_error_deg": float(err[:-1].max()),
            "rate_deg_per_m": float(math.degrees(rate))}


def frames_open(points, up=None):
    """Parallel-transport frames along an open polyline (no seam to fix)."""
    P = np.asarray(points, float)
    T = _tangents(P, False)
    ref = np.asarray(up if up is not None else (0.0, 0.0, 1.0), float)
    N0 = ref - T[0] * (ref @ T[0])
    if np.linalg.norm(N0) < 1e-6:
        N0 = np.array([1.0, 0, 0]) - T[0] * T[0][0]
    N = [_unit(N0)]
    for i in range(len(P) - 1):
        N.append(_transport(N[-1], P[i], P[i + 1], T[i], T[i + 1]))
    return [Frame(P[i].copy(), T[i].copy(), N[i], np.cross(T[i], N[i])) for i in range(len(P))]


# ---------------------------------------------------------------- surface
class Surface:
    """BVH over an evaluated mesh, world space, with smooth (vertex-interpolated)
    normals. Build once, query many: Surface(obj).nearest(points)."""

    def __init__(self, obj, smooth=True):
        import bpy
        from mathutils.bvhtree import BVHTree
        if obj is None or obj.type != "MESH":
            raise ValueError("target must be a mesh object")
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        nv = len(me.vertices)
        co = np.empty(nv * 3)
        me.vertices.foreach_get("co", co)
        vn = np.empty(nv * 3)
        me.vertices.foreach_get("normal", vn)
        tri = np.empty(len(me.loop_triangles) * 3, dtype=np.int32)
        me.loop_triangles.foreach_get("vertices", tri)
        ev.to_mesh_clear()
        M = np.array(obj.matrix_world)
        self.M, self.Minv = M, np.linalg.inv(M)
        self.Nmat = np.linalg.inv(M[:3, :3]).T
        self.co = co.reshape(-1, 3)
        self.vn = vn.reshape(-1, 3)
        self.tri = tri.reshape(-1, 3)
        self.smooth = smooth
        self.tree = BVHTree.FromPolygons(self.co.tolist(), self.tri.tolist())

    def _to_local(self, p):
        return self.Minv[:3, :3] @ p + self.Minv[:3, 3]

    def _finish(self, loc, nrm, idx):
        loc = np.array(loc)
        n = np.array(nrm)
        if self.smooth and idx is not None:
            a, b, c = self.co[self.tri[idx]]
            v0, v1, v2 = b - a, c - a, loc - a
            d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
            d20, d21 = v2 @ v0, v2 @ v1
            den = d00 * d11 - d01 * d01
            if abs(den) > 1e-30:
                v = (d11 * d20 - d01 * d21) / den
                w = (d00 * d21 - d01 * d20) / den
                u = 1.0 - v - w
                ns = self.vn[self.tri[idx]]
                sm = u * ns[0] + v * ns[1] + w * ns[2]
                if np.linalg.norm(sm) > 1e-9 and sm @ n > 0:
                    n = sm
        wl = self.M[:3, :3] @ loc + self.M[:3, 3]
        wn = _unit(self.Nmat @ n)
        return wl, wn

    def nearest(self, points):
        """-> (points (M,3), normals (M,3), hit mask (M,)) in world space."""
        from mathutils import Vector
        P = np.atleast_2d(np.asarray(points, float))
        out = np.array(P, copy=True)
        nrm = np.zeros_like(P)
        hit = np.zeros(len(P), bool)
        for i, p in enumerate(P):
            loc, n, idx, _ = self.tree.find_nearest(Vector(self._to_local(p)))
            if loc is None:
                continue
            out[i], nrm[i] = self._finish(loc, n, idx)
            hit[i] = True
        return out, nrm, hit

    def raycast(self, points, direction):
        """Project along a world direction (both ways, nearest hit wins)."""
        from mathutils import Vector
        P = np.atleast_2d(np.asarray(points, float))
        d_loc = _unit(self.Minv[:3, :3] @ np.asarray(direction, float))
        out = np.array(P, copy=True)
        nrm = np.zeros_like(P)
        hit = np.zeros(len(P), bool)
        for i, p in enumerate(P):
            pl = self._to_local(p)
            best = None
            for sgn in (1.0, -1.0):
                loc, n, idx, dist = self.tree.ray_cast(Vector(pl), Vector(d_loc * sgn))
                if loc is not None and (best is None or dist < best[3]):
                    best = (loc, n, idx, dist)
            if best is None:
                continue
            out[i], nrm[i] = self._finish(best[0], best[1], best[2])
            hit[i] = True
        return out, nrm, hit


def project_to_surface(points, target_obj, offset=0.0, direction=None, smooth=True,
                       surface=None):
    """Project points onto a mesh object's evaluated surface (modifiers respected).

    points:    (M, 3) world-space points.
    offset:    push along the surface normal after projection.
    direction: None = nearest surface point; a vector = ray cast along it.
    smooth:    interpolate vertex normals (research trap: find_nearest returns
               the flat face normal, which faceted-steps ornament orientation).
    surface:   a prebuilt Surface to reuse.
    Returns (points (M,3), normals (M,3), hit (M,) bool). Misses keep their
    input position and a zero normal.
    """
    srf = surface or Surface(target_obj, smooth=smooth)
    if direction is None:
        p, n, hit = srf.nearest(points)
    else:
        p, n, hit = srf.raycast(points, direction)
    return p + n * offset, n, hit


def surface_deviation(points, target_obj=None, surface=None):
    """Max and mean distance from points to the surface (metres)."""
    srf = surface or Surface(target_obj)
    p, _n, _h = srf.nearest(points)
    d = np.linalg.norm(np.asarray(points, float) - p, axis=1)
    return float(d.max()), float(d.mean())


def project_curve_closed(points, target_obj=None, n=None, offset=0.0, direction=None,
                         passes=2, surface=None):
    """Project a closed curve onto a surface and keep it even in arc length
    *on the surface* (research/04 3.6): project, resample the projected curve,
    re-project (resampled chords cut inside a convex surface), repeat.
    Returns (points, normals)."""
    srf = surface or Surface(target_obj)
    P = np.asarray(points, float)
    n = n or len(P)
    for _ in range(max(1, passes)):
        P, N, _h = project_to_surface(P, None, 0.0, direction, surface=srf)
        P, _L = resample_closed_3d(P, n)
    P, N, _h = project_to_surface(P, None, 0.0, direction, surface=srf)
    return P + N * offset, N


# ---------------------------------------------------------------- placement
def placement_matrices(frames, normals=None, offset=0.0):
    """4x4 placement per piece (mathutils.Matrix). Local +X along the tangent,
    +Z along the surface normal (or the frame normal), +Y = Z x X.
    offset pushes each piece along its +Z (half the piece depth seats it)."""
    from mathutils import Matrix
    out = []
    for i, f in enumerate(frames):
        nn = f.normal if normals is None else np.asarray(normals[i], float)
        if np.linalg.norm(nn) < 1e-9:
            nn = f.normal
        nn = _unit(nn)
        T = f.tangent - nn * (f.tangent @ nn)
        if np.linalg.norm(T) < 1e-9:
            T = f.binormal
        T = _unit(T)
        B = np.cross(nn, T)
        p = f.point + nn * offset
        out.append(Matrix(((T[0], B[0], nn[0], p[0]),
                           (T[1], B[1], nn[1], p[1]),
                           (T[2], B[2], nn[2], p[2]),
                           (0.0, 0.0, 0.0, 1.0))))
    return out


def seam_report(points, holonomy_deg=0.0, seam_error_deg=0.0):
    """SeamReport for placed piece centres (closed, in order)."""
    gmin, gmax, ratio, seam = gap_stats(points, True)
    return SeamReport(len(points), gmin, gmax, ratio, seam, float(holonomy_deg),
                      float(seam_error_deg))


def ring_placement(count, radius, target=None, offset=0.0, center=(0.0, 0.0, 0.0),
                   direction=None, dense=16):
    """count pieces evenly around a ring in the XY plane at `center`, optionally
    projected onto `target` and re-spaced by arc length on the surface.

    direction: projection mode for a target (None = nearest, or a vector).
    Returns (matrices, SeamReport, points, normals).
    """
    if count < 3:
        raise ValueError("count must be >= 3")
    c = np.asarray(center, float)
    t = np.linspace(0, 2 * math.pi, count * dense, endpoint=False)
    ring = np.stack([c[0] + radius * np.cos(t), c[1] + radius * np.sin(t),
                     np.full_like(t, c[2])], -1)
    if target is not None:
        P, N = project_curve_closed(ring, target, count, 0.0, direction)
    else:
        P, _ = resample_closed_3d(ring, count)
        N = np.tile([0.0, 0.0, 1.0], (count, 1))
    frames, info = frames_closed(P, normals=N)
    mats = placement_matrices(frames, N, offset)
    return mats, seam_report(P, info["holonomy_deg"], info["seam_error_deg"]), P, N


def knurl_count(circumference, pitch):
    """Integer count so a knurl closes exactly: max(3, round(C / pitch)).
    The actual pitch is then circumference / count."""
    if pitch <= 0:
        raise ValueError("pitch must be > 0")
    return max(3, int(round(circumference / pitch)))


def place_instances(name, source_obj, matrices, link=True):
    """Join copies of source_obj's mesh at each matrix into one new object."""
    import bpy
    import bmesh
    bm = bmesh.new()
    src = source_obj.data
    for M in matrices:
        tmp = bmesh.new()
        tmp.from_mesh(src)
        bmesh.ops.transform(tmp, matrix=M, verts=tmp.verts)
        me_tmp = bpy.data.meshes.new("_tmp")
        tmp.to_mesh(me_tmp)
        tmp.free()
        bm.from_mesh(me_tmp)
        bpy.data.meshes.remove(me_tmp)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    if link:
        bpy.context.scene.collection.objects.link(ob)
    return ob


# ---------------------------------------------------------------- mouldings
def moulding_profile(name_or_points, width=1.0, height=1.0, flip=False):
    """Scale a normalised profile to (out, up) metres. flip mirrors it (concave
    <-> convex read from the other side)."""
    pts = MOULDINGS[name_or_points] if isinstance(name_or_points, str) else name_or_points
    P = np.asarray(pts, float)
    if flip:
        P = np.stack([1.0 - P[::-1, 0], 1.0 - P[::-1, 1]], -1)
    return P * np.array([width, height])


def moulding_mesh(profile_points, path, name="Moulding", width=0.02, height=0.02,
                  closed=None, up=(0.0, 0.0, 1.0), outward=None, cap=True, smooth=True,
                  link=True):
    """Sweep a moulding profile along a path into a solid.

    profile_points: MOULDINGS key or normalised (x, y) points; x runs outward
                    from the path (scaled by width), y runs up (scaled by height).
    path:           (M, 3) points the back-bottom edge of the moulding follows.
    closed:         None = auto (first and last points closer than a segment).
    up:             the moulding's up axis; None = parallel transport (holonomy
                    corrected on closed paths) for paths with no global up.
    outward:        +1/-1 to force which side the profile faces; default is
                    away from the centroid on closed paths, the right-hand side
                    (T x up) on open ones.
    cap:            close the cross-section along the back (x=0) and top so the
                    sweep is a watertight solid, plus end caps on open paths.
    Returns the new mesh object (bmesh built, linked to the scene).
    """
    import bpy
    import bmesh
    prof = moulding_profile(profile_points, width, height) \
        if isinstance(profile_points, str) else np.asarray(profile_points, float) * [width, height]
    if cap:
        tail = []
        if abs(prof[-1, 0]) > 1e-12:
            tail.append([0.0, prof[-1, 1]])
        prof = np.vstack([prof, tail]) if tail else prof
        if abs(prof[0, 0]) > 1e-12:
            prof = np.vstack([[0.0, prof[0, 1]], prof])
    P = np.asarray(path, float)
    if closed is None:
        seglen = np.linalg.norm(np.diff(P, axis=0), axis=1).mean()
        closed = np.linalg.norm(P[-1] - P[0]) < 0.5 * seglen
        if np.linalg.norm(P[-1] - P[0]) < 1e-9:
            P = P[:-1]
    if up is None:
        frames = frames_closed(P)[0] if closed else frames_open(P)
        U = np.array([f.normal for f in frames])
        T = np.array([f.tangent for f in frames])
    else:
        T = _tangents(P, closed)
        u = np.asarray(up, float)
        U = u - T * (T @ u)[:, None]
        U /= np.maximum(np.linalg.norm(U, axis=1, keepdims=True), 1e-12)
    O = np.cross(T, U)
    if outward is None:
        if closed:
            ctr = P.mean(axis=0)
            sgn = 1.0 if np.mean(np.sum(O * (P - ctr), axis=1)) >= 0 else -1.0
        else:
            sgn = 1.0
    else:
        sgn = float(np.sign(outward) or 1.0)
    O *= sgn
    bm = bmesh.new()
    rings = []
    for i in range(len(P)):
        rings.append([bm.verts.new(tuple(P[i] + O[i] * x + U[i] * y)) for x, y in prof])
    m = len(prof)
    nseg = len(P) if closed else len(P) - 1
    closed_prof = cap
    for i in range(nseg):
        a, b = rings[i], rings[(i + 1) % len(P)]
        for j in range(m if closed_prof else m - 1):
            k = (j + 1) % m
            try:
                bm.faces.new((a[j], a[k], b[k], b[j]))
            except ValueError:
                pass
    if cap and not closed:
        for r in (rings[0], rings[-1]):
            try:
                bm.faces.new(r)
            except ValueError:
                pass
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-9)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    if link:
        bpy.context.scene.collection.objects.link(ob)
    return ob


def moulding_lathe(name, profile, radius, height, steps=96, smooth=True):
    """Revolve a normalised moulding profile about Z (x -> radius, y -> height),
    closed to the axis so the result is a solid."""
    import bpy
    import bmesh
    prof = MOULDINGS[profile] if isinstance(profile, str) else profile
    pts = [(0.0, 0.0)] + [(x * radius, y * height) for x, y in prof] + [(0.0, height)]
    clean = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, clean[-1]) > 1e-12:
            clean.append(p)
    bm = bmesh.new()
    vs = [bm.verts.new((x, 0.0, z)) for x, z in clean]
    es = [bm.edges.new((vs[i], vs[i + 1])) for i in range(len(vs) - 1)]
    bmesh.ops.spin(bm, geom=vs + es, cent=(0, 0, 0), axis=(0, 0, 1), dvec=(0, 0, 0),
                   angle=math.tau, steps=steps, use_merge=True)
    # axis vertices are duplicated per step; weld them, then drop the
    # zero-area faces and zero-length edges the weld leaves behind
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=max(radius, height) * 1e-6)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=max(radius, height) * 1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def bevel_profile(obj, points, width=0.004, segments=8, angle_deg=34.0, harden=True):
    """Bevel modifier with a custom profile (profile_type='CUSTOM').

    points: MOULDINGS key or normalised (x, y) points from (0, 0) to (1, 1),
            the research/04 convention. Blender's profile widget runs from
            (1, 0) to (0, 1) with a quarter circle centred on (0, 0) = convex,
            so each point maps to (1 - y, x): ovolo stays convex, scotia
            concave. Only interior points are written: the two endpoints are
            fixed by Blender and points.remove() on them raises RuntimeError.
    Returns the modifier.
    """
    pts = MOULDINGS[points] if isinstance(points, str) else points
    m = obj.modifiers.new("bevel_profile", "BEVEL")
    m.width = width
    m.segments = segments
    m.limit_method = "ANGLE"
    m.angle_limit = math.radians(angle_deg)
    m.harden_normals = harden
    m.profile_type = "CUSTOM"
    cp = m.custom_profile
    for p in list(cp.points)[1:-1]:
        cp.points.remove(p)
    interior = [(1.0 - y, x) for x, y in pts[1:-1]]
    for x, y in interior:
        cp.points.add(x, y)
    cp.update()
    return m


# ---------------------------------------------------------------- guilloche band
def guilloche_band(name, R, r, d, copies=1, radius=0.02, target=None, center=(0, 0, 0),
                   samples=1024, groove_radius=0.0003, sink=0.5, direction=None,
                   variant="epitrochoid", as_mesh=True, resolution=2):
    """Guilloche engraving as a bevelled curve, optionally following a surface.

    R, r, d, variant: see guilloche_points (integer R, r so every copy closes).
    copies:   rotated copies, each by 2 pi / copies (a guilloche field).
    radius:   outer radius of the pattern in metres (curve scaled to fit).
    target:   mesh to engrave onto. The planar pattern sits at `center` in XY,
              is projected (nearest point, or along `direction`), re-spaced by
              arc length on the surface and re-projected.
    groove_radius: bevel radius of the groove tube.
    sink:     fraction of the tube pushed below the surface (0.5 = half sunk).
    as_mesh:  convert to a mesh via the evaluated depsgraph (no bpy.ops).
    Returns the object; ob["surface_max_dev_m"], ob["seam_gap_error"] hold the
    measured fit (max distance of the groove centreline from the surface, and
    worst seam-gap error over copies) when a target is given.
    """
    import bpy
    base = guilloche_points(R, r, d, None, samples, variant)
    ext = R + r + abs(d) if variant == "epitrochoid" else R - r + abs(d)
    base = base * (radius / ext)
    c = np.asarray(center, float)
    cu = bpy.data.curves.new(name, type="CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = groove_radius
    cu.bevel_resolution = resolution
    cu.use_fill_caps = False
    srf = Surface(target) if target is not None else None
    worst_dev, worst_seam = 0.0, 0.0
    for j in range(copies):
        a = math.tau * j / copies
        ca, sa = math.cos(a), math.sin(a)
        xy = base @ np.array([[ca, sa], [-sa, ca]])
        pts = np.column_stack([xy[:, 0] + c[0], xy[:, 1] + c[1], np.full(len(xy), c[2])])
        if srf is not None:
            P, N = project_curve_closed(pts, None, len(pts), -sink * groove_radius * 2 + groove_radius,
                                        direction, surface=srf)
            dev, _ = surface_deviation(P - N * (groove_radius - sink * groove_radius * 2),
                                       surface=srf)
            worst_dev = max(worst_dev, dev)
            worst_seam = max(worst_seam, gap_stats(P)[3])
            pts = P
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        flat = np.column_stack([pts, np.ones(len(pts))]).ravel()
        sp.points.foreach_set("co", flat)
        sp.use_cyclic_u = True
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    if as_mesh:
        dg = bpy.context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
        me.name = name
        bpy.data.objects.remove(ob)
        bpy.data.curves.remove(cu)
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
        for p in me.polygons:
            p.use_smooth = True
    if srf is not None:
        ob["surface_max_dev_m"] = worst_dev
        ob["seam_gap_error"] = worst_seam
    return ob
