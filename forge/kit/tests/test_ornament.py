"""Ornament: guilloche closure, arc-length spacing, seam twist, surface fit.

Run:  /home/user/bpyenv/bin/python tests/test_ornament.py
Prints PASS/FAIL per check and the measured numbers; exits 1 on any failure.
"""
import math
import os
import sys
import time

import numpy as np

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from forgekit import env  # noqa: E402

env.prepare()
import bpy  # noqa: E402
import bmesh  # noqa: E402
from forgekit import ornament as O  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def test_guilloche():
    e = O.guilloche_closure(96, 40, 30)
    check("guilloche: epitrochoid 96/40/30 closes", e < 1e-9, f"|P(T)-P(0)| = {e:.1e}")
    e2 = O.guilloche_closure(96, 40, 30, "hypotrochoid")
    check("guilloche: hypotrochoid closes", e2 < 1e-9, f"{e2:.1e}")
    check("guilloche: period 2 pi r / gcd", abs(O.guilloche_period(96, 40) - 2 * math.pi * 5) < 1e-12,
          f"T = {O.guilloche_period(96, 40):.6f}")
    open_e = O.guilloche_closure(96, 40, 30, n_turns=1.3)
    check("guilloche: a non-period parameter span does not close", open_e > 1e-3, f"{open_e:.3f}")
    pts = O.guilloche_points(96, 40, 30, samples=4000)
    check("guilloche: samples, seam not repeated", pts.shape == (4000, 2)
          and np.linalg.norm(pts[-1] - pts[0]) > 1e-6)
    try:
        O.guilloche_points(40, 96, 10, variant="hypotrochoid")
        check("guilloche: hypotrochoid r >= R rejected", False)
    except ValueError:
        check("guilloche: hypotrochoid r >= R rejected", True)


def test_resample():
    pts = O.guilloche_points(96, 40, 30, samples=4000)
    raw = O.gap_stats(pts)
    rs, L = O.resample_closed_2d(pts, 180)
    gmin, gmax, ratio, seam = O.gap_stats(rs)
    check("resample 2d: 180 points, min/max gap ratio > 0.99", ratio > 0.99,
          f"ratio {ratio:.4f} (raw parameter sampling {raw[2]:.3f}), seam err {seam * 100:.3f}%")
    t = np.linspace(0, 2 * math.pi, 3000, endpoint=False)
    knot = np.stack([(2 + np.cos(3 * t)) * np.cos(2 * t), (2 + np.cos(3 * t)) * np.sin(2 * t),
                     np.sin(3 * t)], -1)
    rs3, L3 = O.resample_closed_3d(knot, 240)
    r3 = O.gap_stats(rs3)
    check("resample 3d: trefoil, gap ratio > 0.99 and seam < 0.5%", r3[2] > 0.99 and r3[3] < 0.005,
          f"ratio {r3[2]:.4f}, seam {r3[3] * 100:.3f}%")


def test_frames():
    # planar loop: holonomy ~0
    t = np.linspace(0, 2 * math.pi, 200, endpoint=False)
    circ = np.stack([np.cos(t), np.sin(t), np.zeros_like(t)], -1)
    _, ip = O.frames_closed(circ)
    check("frames: planar loop has ~zero holonomy", abs(ip["holonomy_deg"]) < 0.1,
          f"{ip['holonomy_deg']:.4f} deg")
    # spatial loops: a closed helix on a torus and a trefoil
    loops = {
        "toroidal helix (5 turns)": np.stack([(1 + 0.35 * np.cos(5 * t)) * np.cos(t),
                                              (1 + 0.35 * np.cos(5 * t)) * np.sin(t),
                                              0.35 * np.sin(5 * t)], -1),
        "trefoil": np.stack([(2 + np.cos(3 * t)) * np.cos(2 * t),
                             (2 + np.cos(3 * t)) * np.sin(2 * t), np.sin(3 * t)], -1),
        "saddle": np.stack([np.cos(t), np.sin(t), 0.4 * np.cos(2 * t)], -1),
    }
    for name, loop in loops.items():
        P, _ = O.resample_closed_3d(loop, 400)
        _, raw = O.frames_closed(P, correct=False)
        fr, cor = O.frames_closed(P)
        print(f"      {name}: holonomy {raw['holonomy_deg']:+.2f} deg, seam twist uncorrected "
              f"{raw['seam_error_deg']:.2f} deg -> corrected {cor['seam_error_deg']:.2e} deg, "
              f"worst non-seam step {cor['max_step_error_deg']:.2e} deg")
        check(f"frames: {name} seam error < 0.5 deg after correction",
              cor["seam_error_deg"] < 0.5 and (name == "saddle" or abs(raw["holonomy_deg"]) > 1.0),
              f"{cor['seam_error_deg']:.2e} deg (was {raw['seam_error_deg']:.2f})")
        ortho = max(max(abs(f.tangent @ f.normal), abs(np.linalg.norm(f.normal) - 1)) for f in fr)
        check(f"frames: {name} frames orthonormal", ortho < 1e-9, f"{ortho:.1e}")


def make_sphere(name="Dome", r=1.0):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=96, v_segments=48, radius=r)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def test_surface():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sph = make_sphere()
    sph.location = (0.3, -0.2, 0.1)
    sph.scale = (1.0, 1.0, 0.6)          # ellipsoid: a non-trivial curved surface
    bpy.context.view_layer.update()
    rng = np.random.default_rng(1)
    pts = rng.normal(size=(2000, 3)) * 0.8 + np.array([0.3, -0.2, 0.1])
    t0 = time.perf_counter()
    srf = O.Surface(sph)
    p, n, hit = O.project_to_surface(pts, sph, surface=srf)
    dt = (time.perf_counter() - t0) * 1000
    dev, _ = O.surface_deviation(p, surface=srf)
    check("project: 2000/2000 hits on a transformed ellipsoid", hit.all() and dev < 1e-6,
          f"{hit.sum()} hits, max residual {dev:.1e} m, {dt:.1f} ms total")
    # smooth normals agree with the analytic ellipsoid normal better than face normals
    loc = p - np.array([0.3, -0.2, 0.1])
    an = loc / np.array([1.0, 1.0, 0.36])
    an /= np.linalg.norm(an, axis=1, keepdims=True)
    _, nf, _ = O.project_to_surface(pts, sph, smooth=False)
    e_s = np.degrees(np.arccos(np.clip(np.sum(n * an, 1), -1, 1))).max()
    e_f = np.degrees(np.arccos(np.clip(np.sum(nf * an, 1), -1, 1))).max()
    check("project: interpolated normals beat face normals", e_s < e_f,
          f"max error smooth {e_s:.2f} deg vs face {e_f:.2f} deg")

    mats, rep, P, N = O.ring_placement(72, 0.7, target=sph, offset=0.002,
                                       center=(0.3, -0.2, 1.0))
    check("ring_placement on ellipsoid: even spacing", rep.gap_ratio > 0.99 and rep.seam_gap_error < 0.005,
          f"gap ratio {rep.gap_ratio:.4f}, seam gap err {rep.seam_gap_error * 100:.3f}%")
    check("ring_placement: seam twist < 0.5 deg", rep.seam_error_deg < 0.5,
          f"{rep.seam_error_deg:.2e} deg (holonomy {rep.holonomy_deg:+.2f} deg)")
    zax = np.array([[m[0][2], m[1][2], m[2][2]] for m in mats])
    check("ring_placement: piece +Z is the surface normal", np.min(np.sum(zax * N, 1)) > 0.9999)
    flat_m, flat_r, _, _ = O.ring_placement(36, 1.0)
    check("ring_placement flat: exact", flat_r.gap_ratio > 0.99999 and flat_r.seam_error_deg < 1e-6,
          f"{flat_r.as_dict()}")
    n = O.knurl_count(2 * math.pi * 0.012, 0.0008)
    check("knurl_count: integer count, pitch recomputed", n == 94,
          f"{n} teeth, pitch {2 * math.pi * 0.012 / n * 1000:.4f} mm")
    return sph


def test_band(sph):
    t0 = time.perf_counter()
    band = O.guilloche_band("Band", 96, 40, 30, copies=6, radius=0.45, target=sph,
                            center=(0.3, -0.2, 1.0), samples=1024, groove_radius=0.002)
    dt = time.perf_counter() - t0
    dev = band["surface_max_dev_m"]
    check("guilloche_band: follows the curved surface", band.type == "MESH" and dev < 1e-5,
          f"centreline off-surface max {dev:.1e} m, seam gap err {band['seam_gap_error'] * 100:.3f}%, "
          f"{len(band.data.vertices)} verts, {dt:.2f} s")
    zs = np.array([v.co.z for v in band.data.vertices])
    check("guilloche_band: band is curved (not planar)", zs.max() - zs.min() > 0.01,
          f"z span {zs.max() - zs.min():.3f} m")
    flat = O.guilloche_band("Flat", 96, 40, 30, copies=3, radius=0.02)
    zf = np.array([v.co.z for v in flat.data.vertices])
    check("guilloche_band: flat variant", flat.type == "MESH" and zf.max() - zf.min() < 0.001)


def mesh_counts(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return len(ob.data.vertices), nm


def test_mouldings():
    check("mouldings: four profiles, nine points each",
          all(len(O.MOULDINGS[k]) == 9 for k in ("ogee", "cyma_reversa", "ovolo", "scotia")))
    t = np.linspace(0, 2 * math.pi, 128, endpoint=False)
    path = np.stack([0.5 * np.cos(t), 0.3 * np.sin(t), np.zeros_like(t)], -1)
    ob = O.moulding_mesh("ogee", path, "Ring", width=0.03, height=0.04)
    nv, nm = mesh_counts(ob)
    check("moulding_mesh: closed ogee sweep is watertight", nm == 0, f"{nv} verts, {nm} non-manifold")
    xy = np.array([(v.co.x, v.co.y) for v in ob.data.vertices])
    r_out = np.max(np.hypot(xy[:, 0] / 0.53, xy[:, 1] / 0.33))
    check("moulding_mesh: profile faces outward", r_out > 0.99, f"{r_out:.3f}")
    op = O.moulding_mesh("scotia", path[:40], "Open", width=0.02, height=0.02)
    nv2, nm2 = mesh_counts(op)
    check("moulding_mesh: open path with end caps is watertight", nm2 == 0, f"{nm2} non-manifold")
    la = O.moulding_lathe("Foot", "ovolo", 0.2, 0.05)
    check("moulding_lathe: solid", mesh_counts(la)[1] == 0)


def bevel_mid_distance(profile):
    """Bevel one cube with a custom profile; return the distance from the
    original corner edge to the middle profile vertex on an edge's section."""
    me = bpy.data.meshes.new("cube")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(0, 0, 0), plane_no=(0, 0, 1))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("cube", me)
    bpy.context.scene.collection.objects.link(ob)
    m = O.bevel_profile(ob, profile, width=0.5, segments=8, angle_deg=30)
    m.harden_normals = False
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    em = ev.to_mesh()
    co = np.array([v.co[:] for v in em.vertices])
    n = len(co)
    ev.to_mesh_clear()
    # vertices in the z ~ 0 mid-slice of the +x+y vertical edge
    sl = co[(np.abs(co[:, 2]) < 0.05) & (co[:, 0] > 0.4) & (co[:, 1] > 0.4)]
    d = np.abs((sl[:, 0] - 1.0) + (sl[:, 1] - 1.0)) / math.sqrt(2.0)   # distance from corner diag
    bpy.data.objects.remove(ob)
    mid = int(np.argmin(np.abs(sl[:, 0] - sl[:, 1])))     # profile vertex on the diagonal
    return d[mid], n, len(sl), m


def test_bevel_profile():
    convex, n, ns, m = bevel_mid_distance("ovolo")
    concave, _, _, _ = bevel_mid_distance("scotia")
    print(f"      bevel mid-vertex distance from corner edge: ovolo {convex:.3f}, scotia {concave:.3f} "
          f"(chamfer 0.354, circle 0.207 convex / 0.500 concave); {n} verts")
    check("bevel_profile: ovolo bevel is convex", abs(convex - 0.207) < 0.03, f"{convex:.3f}")
    check("bevel_profile: scotia bevel is concave", abs(concave - 0.500) < 0.03, f"{concave:.3f}")


if __name__ == "__main__":
    test_guilloche()
    test_resample()
    test_frames()
    sph = test_surface()
    test_band(sph)
    test_mouldings()
    test_bevel_profile()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
