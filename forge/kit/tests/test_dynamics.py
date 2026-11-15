"""Spring-chain dynamics: integrator numbers, then a real bpy armature.

Run:  /home/user/bpyenv/bin/python tests/test_dynamics.py
Prints PASS/FAIL per check and the measured numbers; exits 1 on any failure.
"""
import math
import os
import sys
import time

import numpy as np

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from forgekit import dynamics as D  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def upright_chain(preset="spine", **kw):
    pts = np.array([[0.0, 0.0, 0.1 * i] for i in range(9)])
    spec, _ = D.chain_from_points(pts, preset=preset, name="c", **kw)
    return D.new_state([spec], {"c": pts})


# ------------------------------------------------------------- pure numpy
def test_presets():
    want = {"spine", "tail", "hair", "cloak", "antenna", "belly", "ear"}
    check("presets: research table + ear", want <= set(D.PRESETS), sorted(D.PRESETS))
    ok = True
    for name, p in D.PRESETS.items():
        for key, (lo, hi) in p["ranges"].items():
            if not (lo <= p[key] <= hi):
                ok = False
                print(f"      {name}.{key}={p[key]} outside {lo}..{hi}")
    check("presets: every working value inside its research range", ok)


def test_settle():
    """Root acceleration step (4 m/s^2 for 0.25 s, then zero), in-place drive."""
    dt = 1 / 60
    st = upright_chain("spine", damping_ratio=1.0)
    tip = []
    for f in range(300):
        D.step(st, np.array([4.0, 0, 0]) if 10 <= f < 25 else None, dt)
        tip.append(st.P[0, -1, 0])
    tip = np.array(tip)
    i = int(np.argmax(np.abs(tip)))
    peak, sgn = abs(tip[i]), np.sign(tip[i])
    overshoot = max(0.0, -(sgn * tip[i:]).min()) / peak
    settle = next(k for k in range(25, 300) if np.all(np.abs(tip[k:]) < 0.02 * peak))
    check("settle: chain leans against the acceleration", sgn < 0 and peak > 0.02,
          f"peak tip {peak * 1000:.1f} mm")
    check("settle: no overshoot (critical damping)", overshoot < 0.01,
          f"overshoot {overshoot * 100:.3f}% of peak")
    check("settle: back within 2% of peak", settle < 300,
          f"{(settle - 25) / 60:.2f} s after the forcing stops")
    check("settle: returns to rest", abs(tip[-1]) < 1e-4, f"final {tip[-1]:.2e} m")
    # same chain, gravity on (spine preset gravity 0.15): must not buckle
    st = upright_chain("spine")
    for f in range(300):
        D.step(st, np.array([4.0, 0, 0]) if 10 <= f < 25 else None, dt)
    check("settle: upright chain under preset gravity does not buckle",
          abs(st.P[0, -1, 0]) < 1e-3, f"final tip x {st.P[0, -1, 0]:.2e}")


def stretch_run(ftl, S=4, I=2, seconds=10.0):
    """4 chains x 8 bones, rest horizontal, soft springs, gravity, root shaken."""
    specs, rest = [], {}
    for c in range(4):
        pts = np.array([[0.0, c * 0.5, 0.0]] + [[0.1 * (i + 1), c * 0.5, 0.0] for i in range(8)])
        sp, _ = D.chain_from_points(pts, name=f"h{c}", stiffness=8.0, drag=0.1,
                                    gravity=1.0, stretch_limit=0.0)
        specs.append(sp)
        rest[sp.name] = pts
    st = D.new_state(specs, rest, substeps=S, iterations=I)
    base = st.T.copy()
    worst = 0.0
    dt = 1 / 60
    for f in range(int(seconds * 60)):
        tg = base.copy()
        tg[:, :, 2] += 0.15 * math.sin(f * dt * 7.0)
        D.step(st, dt=dt, targets=tg, ftl=ftl)
        worst = max(worst, D.max_stretch(st))
    return worst


def test_stretch():
    pbd = stretch_run(False)
    full = stretch_run(True)
    pbd84 = stretch_run(False, 8, 4)
    print(f"      max stretch over 10 s: PBD only 4x2 {pbd * 100:.3f}%, "
          f"PBD only 8x4 {pbd84 * 100:.3f}%, PBD + follow-the-leader 4x2 {full * 100:.3f}%")
    check("stretch: 10 s at 4x2 stays under 1%", full < 0.01, f"{full * 100:.3f}%")
    check("stretch: 8x4 tighter than 4x2 (PBD only)", pbd84 < pbd,
          f"{pbd84 * 100:.3f}% < {pbd * 100:.3f}%")


def test_colliders_impulse():
    pts = np.array([[0.0, 0.0, 1.0]] + [[0.0, 0.0, 1.0 - 0.1 * (i + 1)] for i in range(8)])
    spec, _ = D.chain_from_points(pts, preset="cloak", name="cl")
    st = D.new_state([spec], {"cl": pts})
    sph = D.ColliderSpec("ball", "sphere", radius=0.15, offset=(0.05, 0.0, 0.55))
    floor = D.ColliderSpec("floor", "plane", normal=(0, 0, 1), offset=(0, 0, 0.35))
    cap = D.ColliderSpec("arm", "capsule", radius=0.05, offset=(-0.3, 0.0, 0.8),
                         tail=(0.3, 0.0, 0.8))
    D.set_colliders(st, [sph, floor, cap])
    D.impulse(st, 0, 8, np.array([2.0, 0.0, 0.0]))
    pen_s = pen_p = 0.0
    for _ in range(240):
        D.step(st, dt=1 / 60)
        d = np.linalg.norm(st.P[0, 1:] - np.array(sph.offset), axis=1)
        pen_s = max(pen_s, float((0.15 - d).max()))
        pen_p = max(pen_p, float((0.35 - st.P[0, 1:, 2]).max()))
    check("colliders: sphere penetration < 2 mm", pen_s < 0.002, f"{pen_s * 1000:.2f} mm")
    check("colliders: plane penetration < 2 mm", pen_p < 0.002, f"{pen_p * 1000:.2f} mm")
    a, b = np.array(cap.offset), np.array(cap.tail)
    t = np.clip(((st.P[0, 1:] - a) @ (b - a)) / ((b - a) @ (b - a)), 0, 1)
    dcap = np.linalg.norm(st.P[0, 1:] - (a + t[:, None] * (b - a)), axis=1)
    check("colliders: capsule respected", dcap.min() > 0.05 - 0.002, f"min dist {dcap.min():.4f}")
    st2 = upright_chain("hair")
    n = D.impulse_at(st2, st2.P[0, 8], (0.0, 1.0, 0.0), 0.15)
    check("impulse_at: touch reaches neighbours", n >= 2, f"{n} particles")


def test_layer_math():
    rng = np.random.default_rng(3)
    qa = rng.normal(size=(50, 4))
    qa /= np.linalg.norm(qa, axis=1, keepdims=True)
    qs = rng.normal(size=(50, 4))
    qs /= np.linalg.norm(qs, axis=1, keepdims=True)
    q0 = D.additive_layer(qa, qs, 0.0)
    q1 = D.additive_layer(qa, qs, 1.0)
    same = lambda x, y: np.minimum(np.abs(x - y).max(1), np.abs(x + y).max(1)).max()
    check("layer: w=0 returns the keyframed rotation", same(q0, qa) < 1e-9, f"{same(q0, qa):.1e}")
    check("layer: w=1 returns the simulated rotation", same(q1, qs) < 1e-9, f"{same(q1, qs):.1e}")


def test_benchmark():
    runs = [D.benchmark(8, 8, 4, 2, frames=400) for _ in range(3)]
    ms = min(runs)
    print(f"      8 chains x 8 bones, 4 substeps x 2 iterations: "
          f"{', '.join(f'{r:.3f}' for r in runs)} ms/frame (best {ms:.3f})")
    print(f"      1x8 {D.benchmark(1, 8, 4, 2, frames=300):.3f} ms, "
          f"16x8 {D.benchmark(16, 8, 4, 2, frames=300):.3f} ms, "
          f"64x16 {D.benchmark(64, 16, 4, 2, frames=200):.3f} ms")
    check("perf: 8x8 at 4x2 under 1 ms/frame (research 0.42-0.47)", ms < 1.0, f"{ms:.3f} ms")


# ------------------------------------------------------------- bpy armature
def build_rig(bpy, chains=8, bones=8, L=0.1):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm = bpy.data.armatures.new("DynRig")
    rig = bpy.data.objects.new("DynRig", arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    root = arm.edit_bones.new("root")
    root.head, root.tail = (0, 0, 0), (0, 0, 0.2)
    names = []
    for c in range(chains):
        prev = root
        x = (c - chains / 2) * 0.15
        chain = []
        for i in range(bones):
            b = arm.edit_bones.new(f"c{c}_{i}")
            b.head = (x, 0.0, 0.2 + L * i)
            b.tail = (x, 0.0, 0.2 + L * (i + 1))
            b.parent = prev
            b.use_connect = i > 0
            prev = b
            chain.append(b.name)
        names.append(chain)
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return rig, names


def animate(bpy, rig, keyed_bone, keyed_rot):
    """root: still 1-10, accelerates to x=0.6 by 30, holds to 150.
    keyed_bone carries an animator rotation key held over the whole range."""
    root = rig.pose.bones["root"]
    for f, x in ((1, 0.0), (10, 0.0), (30, 0.6), (150, 0.6)):
        root.location = (x, 0.0, 0.0)
        root.keyframe_insert("location", frame=f)
    pb = rig.pose.bones[keyed_bone]
    pb.rotation_quaternion = keyed_rot
    pb.keyframe_insert("rotation_quaternion", frame=1)
    pb.keyframe_insert("rotation_quaternion", frame=150)


def test_bpy():
    from forgekit import env
    env.prepare()
    import bpy
    from mathutils import Quaternion

    q_key = Quaternion((1, 0, 0), math.radians(25))
    keyed = "c0_3"

    # --- w = 0: the bake must reproduce the keyframed animation exactly
    rig, names = build_rig(bpy)
    animate(bpy, rig, keyed, q_key)
    specs = [D.ChainSpec.from_preset(f"ch{c}", n, "spine", blend=0.0) for c, n in enumerate(names)]
    D.bake(rig, specs, 1, 150)
    sc = bpy.context.scene
    err = 0.0
    for f in (5, 20, 40, 149):
        sc.frame_set(f)
        for c, n in enumerate(names):
            for b in n:
                want = q_key if b == keyed else Quaternion()
                got = rig.pose.bones[b].rotation_quaternion
                err = max(err, math.degrees(want.rotation_difference(got).angle))
    check("bpy w=0: keyframed pose survives the bake exactly", err < 1e-3, f"max {err:.2e} deg")

    # --- w = 1: dynamics on top, keyed pose is the base it settles back to
    rig, names = build_rig(bpy)
    animate(bpy, rig, keyed, q_key)
    specs = [D.ChainSpec.from_preset(f"ch{c}", n, "spine", gravity=0.0) for c, n in enumerate(names)]
    t0 = time.perf_counter()
    rep = D.bake(rig, specs, 1, 150)
    sc = bpy.context.scene
    wall = time.perf_counter() - t0
    print(f"      bpy bake 8x8 bones, 150 frames: sim {rep['ms_per_frame_sim']:.3f} ms/frame, "
          f"total incl. frame_set + keying {rep['ms_per_frame_total']:.2f} ms/frame "
          f"({wall:.2f} s wall, {rep['keys']} keys, max stretch {rep['max_stretch'] * 100:.3f}%)")
    check("bpy bake: keys written", rep["keys"] == 64 * 150, f"{rep['keys']}")
    sc.frame_set(32)
    lag = max(math.degrees(rig.pose.bones[f"c1_{i}"].rotation_quaternion.angle) for i in range(8))
    check("bpy w=1: chain lags behind the root move", lag > 0.5, f"max bone angle {lag:.2f} deg at f32")
    sc.frame_set(150)
    d = math.degrees(q_key.rotation_difference(rig.pose.bones[keyed].rotation_quaternion).angle)
    rest = max(math.degrees(rig.pose.bones[f"c1_{i}"].rotation_quaternion.angle) for i in range(8))
    check("bpy w=1: settles back onto the keyframed pose", d < 0.5 and rest < 0.5,
          f"keyed bone off by {d:.3f} deg, free chain {rest:.3f} deg at f150")

    src = bpy.data.actions.get(rep["source_action"] or "")
    check("bpy bake: source action kept, result in a copy",
          src is not None and rep["action"] != rep["source_action"],
          f"{rep['source_action']} -> {rep['action']}")
    if src is not None:
        dyn = rig.animation_data.action
        rig.animation_data.action = src
        sc.frame_set(32)
        d_src = math.degrees(q_key.rotation_difference(rig.pose.bones[keyed].rotation_quaternion).angle)
        free = max(math.degrees(rig.pose.bones[f"c1_{i}"].rotation_quaternion.angle) for i in range(8))
        check("bpy bake: source action still holds the animator keys", d_src < 1e-4 and free < 1e-4,
              f"keyed bone {d_src:.2e} deg, free chain {free:.2e} deg at f32")
        rig.animation_data.action = dyn

    # --- w = 0.5 obeys q_anim * slerp(I, q_anim^-1 q_sim, w) on the live path
    rig, names = build_rig(bpy, chains=1)
    animate(bpy, rig, "c0_3", q_key)
    sc = bpy.context.scene
    sc.frame_set(1)
    st = D.chains_from_rig(rig, names, preset="spine", blend=0.5)
    out = None
    for f in range(1, 33):
        sc.frame_set(f)
        out = D.live_frame(rig, st)
    tg, mats, basis = D.targets_from_anim(rig, st)
    full = D.compute_layered(rig, st, mats, basis, blend_by_bone={b: 1.0 for b in names[0]})
    q_anim = basis["c0_3"]
    expect = D.additive_layer(q_anim, full["c0_3"], 0.5)
    got = out["c0_3"]
    dev = min(np.abs(expect - got).max(), np.abs(expect + got).max())
    # c0_3's parent differs between the w=0.5 and w=1 evaluations, so compare
    # the root bone (no blended parent) for the exact identity
    e0 = D.additive_layer(basis["c0_0"], full["c0_0"], 0.5)
    dev0 = min(np.abs(e0 - out["c0_0"]).max(), np.abs(e0 + out["c0_0"]).max())
    check("bpy w=0.5: root bone matches the additive formula", dev0 < 1e-9, f"{dev0:.1e}")
    print(f"      (c0_3 vs formula with an un-blended parent: {dev:.2e}, expected non-zero)")


if __name__ == "__main__":
    test_presets()
    test_settle()
    test_stretch()
    test_colliders_impulse()
    test_layer_math()
    test_benchmark()
    test_bpy()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
