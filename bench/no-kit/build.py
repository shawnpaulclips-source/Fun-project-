"""Procedural models: Mario, Luigi, Koopa Troopa, and the whole set.

Characters are built facing +X (screen right) with the root at the feet and a
nominal total height of 1.0 so that SMALL/BIG is a single root-scale value.
Rigs are forward-kinematic chains of empties; meshes hang off the joints.
"""
import math

import bpy
from mathutils import Vector

import lib
from lib import attach, box, cyl, joint, mat, plane, ring, sphere

# ------------------------------------------------------------------ palette
SKIN = (0.98, 0.76, 0.56)
RED = (0.80, 0.08, 0.07)
GREEN = (0.10, 0.52, 0.12)
BLUE = (0.09, 0.18, 0.62)
BROWN = (0.33, 0.16, 0.07)
WHITE = (0.95, 0.95, 0.94)
DARK = (0.05, 0.05, 0.07)
GOLD = (0.95, 0.72, 0.10)
KSKIN = (0.97, 0.84, 0.30)
KSHELL = (0.13, 0.60, 0.16)
KRIM = (0.98, 0.90, 0.45)
BRICK = (0.72, 0.33, 0.12)
QBLOCK = (0.95, 0.68, 0.10)
QDIM = (0.42, 0.33, 0.26)
PIPE = (0.08, 0.62, 0.22)
GRASS = (0.26, 0.60, 0.18)
DIRT = (0.52, 0.30, 0.12)
SKY = (0.36, 0.62, 0.95)


def _m(name, col, **kw):
    return mat(name, col, **kw)


# ------------------------------------------------------------------ plumber

def plumber(name, shirt, hat, tall=1.0, wide=1.0, nose_r=0.072,
            stache=(0.16, 0.14, 0.055), hair=DARK):
    """Build a Mario-family character. Returns the rig dict."""
    p = name
    m_skin = _m("skin", SKIN, rough=0.6)
    m_shirt = _m(p + "_shirt", shirt, rough=0.65)
    m_ov = _m("overalls", BLUE, rough=0.7)
    m_glove = _m("glove", WHITE, rough=0.7)
    m_boot = _m("boot", BROWN, rough=0.6)
    m_hat = _m(p + "_hat", hat, rough=0.65)
    m_white = _m("eyewhite", WHITE, rough=0.35)
    m_dark = _m("dark", DARK, rough=0.4)
    m_hair = _m(p + "_hair", hair, rough=0.5)
    m_but = _m("button", GOLD, rough=0.25, metal=0.7)

    W = wide
    root = joint(p, (0, 0, 0))
    root.empty_display_size = 0.2
    hips = joint(p + ".hips", (0, 0, 0.34 * tall), root)
    chest = joint(p + ".chest", (0, 0, 0.0), hips)
    neck = joint(p + ".neck", (0, 0, 0.26 * tall), chest)
    head = joint(p + ".head", (0, 0, 0.0), neck)

    # ---- torso
    t = box(p + ".torso", (0.175 * W, 0.125, 0.155 * tall),
            (0, 0, 0.13 * tall), m_shirt, bevel=0.035)
    attach_local(t, chest)
    ov = box(p + ".overalls", (0.180 * W, 0.132, 0.090 * tall),
             (0, 0, 0.045 * tall), m_ov, bevel=0.03)
    attach_local(ov, chest)
    for s in (-1, 1):
        st = box(p + ".strap%d" % s, (0.026 * W, 0.006, 0.085 * tall),
                 (-0.02, -0.132 * s, 0.175 * tall), m_ov)
        attach_local(st, chest)
        bu = cyl(p + ".btn%d" % s, 0.022, 0.02,
                 (-0.02, -0.140 * s, 0.112 * tall), m_but, segs=10, axis="Y")
        attach_local(bu, chest)

    # ---- legs + boots
    legs = {}
    for side, s in (("l", -1), ("r", 1)):
        lj = joint("%s.leg_%s" % (p, side), (0, 0.082 * s, 0.0), hips)
        thigh = box("%s.thigh_%s" % (p, side),
                    (0.072 * W, 0.070, 0.150 * tall),
                    (0, 0, -0.145 * tall), m_ov, bevel=0.02)
        attach_local(thigh, lj)
        bt = box("%s.boot_%s" % (p, side), (0.115 * W, 0.078, 0.062),
                 (0.028, 0, -0.33 * tall), m_boot, bevel=0.025)
        attach_local(bt, lj)
        legs[side] = lj

    # ---- arms + gloves
    arms = {}
    for side, s in (("l", -1), ("r", 1)):
        aj = joint("%s.arm_%s" % (p, side), (0, 0.168 * W * s, 0.205 * tall),
                   chest)
        up = box("%s.uparm_%s" % (p, side), (0.058 * W, 0.058, 0.100 * tall),
                 (0, 0, -0.095 * tall), m_shirt, bevel=0.02)
        attach_local(up, aj)
        hj = joint("%s.hand_%s" % (p, side), (0, 0, -0.205 * tall), aj)
        gl = sphere("%s.glove_%s" % (p, side), 0.082 * W, (0, 0, 0), m_glove,
                    segs=14, rings=8, scale=(1.0, 0.85, 1.0))
        attach_local(gl, hj)
        arms[side] = (aj, hj)

    # ---- head
    hd = sphere(p + ".skull", 0.163, (0, 0, 0.165 * tall), m_skin, segs=20,
                rings=12, scale=(1.0, 0.96, 1.02))
    attach_local(hd, head)
    nz = sphere(p + ".nose", nose_r * 1.18, (0.152, 0, 0.158 * tall), m_skin,
                segs=14, rings=9, scale=(1.1, 1.0, 1.0))
    attach_local(nz, head)
    ms = box(p + ".stache", (stache[0] * 0.5, stache[1] * 0.5, stache[2] * 0.5),
             (0.118, 0, 0.100 * tall), m_hair, bevel=0.02)
    attach_local(ms, head)
    for s in (-1, 1):
        ey = sphere(p + ".eye%d" % s, 0.040, (0.112, 0.055 * s, 0.215 * tall),
                    m_white, segs=12, rings=8, scale=(0.7, 1.0, 1.25))
        attach_local(ey, head)
        pu = sphere(p + ".pupil%d" % s, 0.019,
                    (0.139, 0.058 * s, 0.215 * tall), m_dark, segs=10, rings=6)
        attach_local(pu, head)
        eb = box(p + ".brow%d" % s, (0.030, 0.026, 0.011),
                 (0.118, 0.055 * s, 0.262 * tall), m_hair)
        attach_local(eb, head)
        er = sphere(p + ".ear%d" % s, 0.045, (-0.01, 0.154 * s, 0.158 * tall),
                    m_skin, segs=10, rings=7, scale=(1.0, 0.6, 1.1))
        attach_local(er, head)
    sb = box(p + ".sideburn", (0.038, 0.095, 0.055),
             (-0.105, 0, 0.135 * tall), m_hair, bevel=0.02)
    attach_local(sb, head)

    # ---- cap
    cap = sphere(p + ".cap", 0.172, (-0.004, 0, 0.270 * tall), m_hat, segs=20,
                 rings=10, scale=(1.0, 0.98, 0.62))
    attach_local(cap, head)
    brim = box(p + ".brim", (0.085, 0.105, 0.016),
               (0.185, 0, 0.268 * tall), m_hat, bevel=0.012)
    attach_local(brim, head)
    disc = cyl(p + ".emblem", 0.058, 0.016, (0.138, 0, 0.300 * tall), m_white,
               segs=16, axis="X")
    attach_local(disc, head)
    # initial letter, built from three slabs (M = two legs + vee / L = corner)
    letter = _m(p + "_letter", shirt, rough=0.5)
    if name.startswith("mario"):
        strokes = [((-0.030, 0.0), (0.010, 0.050), 0.0),
                   ((0.030, 0.0), (0.010, 0.050), 0.0),
                   ((-0.013, 0.014), (0.009, 0.030), 0.9),
                   ((0.013, 0.014), (0.009, 0.030), -0.9)]
    else:
        strokes = [((-0.012, 0.004), (0.010, 0.046), 0.0),
                   ((0.012, -0.034), (0.034, 0.010), 0.0)]
    for i, ((oy, oz), (sy, sz), tilt) in enumerate(strokes):
        sl = box(p + ".ltr%d" % i, (0.009, sy, sz),
                 (0.152, oy, 0.300 * tall + oz), letter, rot=(tilt, 0, 0))
        attach_local(sl, head)

    return {"name": name, "root": root, "hips": hips, "chest": chest,
            "neck": neck, "head": head, "legs": legs, "arms": arms,
            "hand_l": arms["l"][1], "hand_r": arms["r"][1]}


def attach_local(ob, parent):
    """Parent and treat the current location as parent-local."""
    ob.parent = parent
    return ob


# ------------------------------------------------------------------ koopa

def koopa(name="koopa"):
    p = name
    m_sk = _m("kskin", KSKIN, rough=0.55)
    m_sh = _m("kshell", KSHELL, rough=0.42, spec=0.5)
    m_rim = _m("krim", KRIM, rough=0.55)
    m_beak = _m("kbeak", (0.95, 0.62, 0.12), rough=0.5)
    m_white = _m("eyewhite", WHITE, rough=0.35)
    m_dark = _m("dark", DARK, rough=0.4)
    m_foot = _m("kfoot", (0.95, 0.60, 0.10), rough=0.55)

    root = joint(p, (0, 0, 0))
    root.empty_display_size = 0.2
    hips = joint(p + ".hips", (0, 0, 0.30), root)
    body = joint(p + ".body", (0, 0, 0), hips)
    neck = joint(p + ".neck", (0.03, 0, 0.26), body)
    head = joint(p + ".head", (0, 0, 0), neck)

    torso = sphere(p + ".torso", 0.175, (0, 0, 0.03), m_sk, segs=18, rings=11,
                   scale=(0.95, 0.95, 1.05))
    attach_local(torso, body)
    shell = sphere(p + ".shell", 0.275, (-0.075, 0, 0.050), m_sh, segs=20,
                   rings=12, scale=(0.96, 0.98, 0.92))
    attach_local(shell, body)
    rimr = ring(p + ".rim", 0.252, 0.042, (-0.075, 0, 0.045), m_rim,
                major_segs=22, minor_segs=8, axis="Y", scale=(1.0, 1.0, 0.95))
    attach_local(rimr, body)
    belly = sphere(p + ".belly", 0.148, (0.072, 0, 0.01), m_rim, segs=16,
                   rings=10, scale=(0.8, 0.9, 1.0))
    attach_local(belly, body)

    legs = {}
    for side, s in (("l", -1), ("r", 1)):
        lj = joint("%s.leg_%s" % (p, side), (0, 0.085 * s, 0.0), hips)
        th = cyl("%s.shin_%s" % (p, side), 0.045, 0.16, (0, 0, -0.10), m_sk,
                 segs=10)
        attach_local(th, lj)
        ft = box("%s.foot_%s" % (p, side), (0.082, 0.055, 0.045),
                 (0.034, 0, -0.255), m_foot, bevel=0.022)
        attach_local(ft, lj)
        legs[side] = lj

    arms = {}
    for side, s in (("l", -1), ("r", 1)):
        aj = joint("%s.arm_%s" % (p, side), (0.03, 0.155 * s, 0.10), body)
        ar = cyl("%s.arm_%s_m" % (p, side), 0.040, 0.15, (0, 0, -0.085), m_sk,
                 segs=10)
        attach_local(ar, aj)
        hn = sphere("%s.fist_%s" % (p, side), 0.052, (0, 0, -0.165), m_sk,
                    segs=10, rings=7)
        attach_local(hn, aj)
        arms[side] = (aj, aj)

    sk = sphere(p + ".skull", 0.148, (0, 0, 0.06), m_sk, segs=18, rings=11,
                scale=(1.0, 0.96, 1.0))
    attach_local(sk, head)
    bk = cyl(p + ".beak", 0.058, 0.115, (0.165, 0, 0.040), m_beak, segs=12,
             axis="X", r2=0.012)
    attach_local(bk, head)
    eyes_ok, eyes_x = [], []
    for s in (-1, 1):
        ey = sphere(p + ".eye%d" % s, 0.052, (0.095, 0.058 * s, 0.108),
                    m_white, segs=12, rings=8, scale=(0.75, 1.0, 1.1))
        attach_local(ey, head)
        eyes_ok.append(ey)
        pu = sphere(p + ".pupil%d" % s, 0.024, (0.130, 0.062 * s, 0.112),
                    m_dark, segs=10, rings=6)
        attach_local(pu, head)
        eyes_ok.append(pu)
        xg = joint(p + ".xeye%d" % s, (0.131, 0.062 * s, 0.112), head)
        for k, rt in enumerate((0.78, -0.78)):
            bar = box(p + ".xbar%d_%d" % (s, k), (0.006, 0.062, 0.013),
                      (0, 0, 0), m_dark, rot=(rt, 0, 0))
            attach_local(bar, xg)
        xg.scale = (0, 0, 0)
        eyes_x.append(xg)

    return {"name": name, "root": root, "hips": hips, "body": body,
            "neck": neck, "head": head, "legs": legs, "arms": arms,
            "shell": shell, "rim": rimr, "eyes_x": eyes_x}


# ------------------------------------------------------------------ world

def q_mark(parent, zoff, material):
    """'?' glyph built from slabs, on the +Y and -Y faces of a block."""
    seg = [((0.000, 0.205), (0.078, 0.034)),      # top bar
           ((-0.078, 0.150), (0.034, 0.055)),     # upper-left shoulder
           ((0.078, 0.140), (0.034, 0.065)),      # upper-right side
           ((0.030, 0.060), (0.082, 0.034)),      # hook coming back left
           ((0.000, -0.020), (0.034, 0.082)),     # stem
           ((0.000, -0.145), (0.040, 0.040))]     # dot
    out = []
    for sgn in (-1, 1):
        for i, ((ox, oz), (sx, sz)) in enumerate(seg):
            b = box("qm_%d_%d" % (sgn, i), (sx, 0.020, sz),
                    (ox, 0.498 * sgn, zoff + oz), material)
            attach_local(b, parent)
            out.append(b)
    return out


def q_block(loc):
    m_q = _m("qblock", QBLOCK, rough=0.45, spec=0.5)
    m_qd = _m("qdim", QDIM, rough=0.7)
    m_dk = _m("qdark", (0.45, 0.26, 0.03), rough=0.6)
    root = joint("qb", loc)
    root.empty_display_size = 0.3
    cube = box("qb.cube", (0.5, 0.5, 0.5), (0, 0, 0), m_q, bevel=0.045)
    attach_local(cube, root)
    for sx in (-1, 1):
        for sz in (-1, 1):
            r = box("qb.rivet_%d%d" % (sx, sz), (0.055, 0.021, 0.055),
                    (0.40 * sx, -0.50, 0.40 * sz), m_dk)
            attach_local(r, root)
            r2 = box("qb.rivetb_%d%d" % (sx, sz), (0.055, 0.021, 0.055),
                     (0.40 * sx, 0.50, 0.40 * sz), m_dk)
            attach_local(r2, root)
    glyph = q_mark(root, 0.0, _m("qglyph", (0.99, 0.97, 0.90), rough=0.35))
    return {"root": root, "cube": cube, "glyph": glyph,
            "mat_on": m_q, "mat_off": m_qd}


def brick(loc, name):
    m_b = _m("brick", BRICK, rough=0.72)
    m_l = _m("bricklines", (0.42, 0.17, 0.05), rough=0.75)
    g = joint(name, loc)
    c = box(name + ".c", (0.5, 0.5, 0.5), (0, 0, 0), m_b, bevel=0.04)
    attach_local(c, g)
    for z in (-0.17, 0.17):
        for sgn in (-1, 1):
            s = box(name + ".h%d%d" % (int(z * 100), sgn),
                    (0.5, 0.012, 0.022), (0, 0.50 * sgn, z), m_l)
            attach_local(s, g)
    for x, z in ((-0.25, 0.33), (0.25, 0.33), (0.0, 0.0), (-0.25, -0.33),
                 (0.25, -0.33)):
        for sgn in (-1, 1):
            s = box(name + ".v%d%d%d" % (int(x * 100), int(z * 100), sgn),
                    (0.022, 0.012, 0.165), (x, 0.50 * sgn, z), m_l)
            attach_local(s, g)
    return g


def ground(x0=-22, x1=26, top=0.0):
    m_g = _m("grass", GRASS, rough=0.8)
    m_d = _m("dirt", DIRT, rough=0.85)
    m_dl = _m("dirtline", (0.40, 0.21, 0.07), rough=0.85)
    objs = []
    for x in range(x0, x1):
        b = box("gnd_%d" % x, (0.495, 0.60, 0.11), (x + 0.5, 0, top - 0.11),
                m_g, bevel=0.03)
        objs.append(b)
        d = box("dirt_%d" % x, (0.495, 0.58, 0.44), (x + 0.5, 0, top - 0.66),
                m_d, bevel=0.03)
        objs.append(d)
        for z in (-0.40, -0.90):
            objs.append(box("dl_%d_%d" % (x, int(z * 10)), (0.46, 0.012, 0.016),
                            (x + 0.5, -0.59, top + z), m_dl))
    base = box("gnd_base", (26, 0.58, 2.0), (2, 0, top - 3.0), m_d)
    objs.append(base)
    return objs


def pipe(loc):
    m_p = _m("pipe", PIPE, rough=0.42, spec=0.6)
    m_pd = _m("piped", (0.04, 0.42, 0.14), rough=0.5)
    g = joint("pipe", loc)
    body = cyl("pipe.body", 0.52, 1.5, (0, 0, 0.75), m_p, segs=24)
    attach_local(body, g)
    lipo = cyl("pipe.lip", 0.64, 0.34, (0, 0, 1.63), m_p, segs=24)
    attach_local(lipo, g)
    hole = cyl("pipe.hole", 0.50, 0.12, (0, 0, 1.78), m_pd, segs=24)
    attach_local(hole, g)
    shade = box("pipe.shade", (0.10, 0.10, 0.74), (-0.42, -0.30, 0.75), m_pd)
    attach_local(shade, g)
    return g


def mushroom(loc=(0, 0, 0)):
    m_c = _m("mcap", (0.88, 0.14, 0.12), rough=0.45)
    m_s = _m("mspot", (0.97, 0.95, 0.90), rough=0.5)
    m_st = _m("mstem", (0.98, 0.90, 0.72), rough=0.6)
    m_dk = _m("dark", DARK, rough=0.4)
    g = joint("mush", loc)
    g.empty_display_size = 0.15
    cap = sphere("mush.cap", 0.205, (0, 0, 0.085), m_c, segs=20, rings=12,
                 scale=(1.0, 0.95, 0.82))
    attach_local(cap, g)
    for (ox, oy, oz, r) in ((0.0, 0.0, 0.17, 0.075), (0.125, 0.0, 0.10, 0.058),
                            (-0.125, 0.0, 0.10, 0.058),
                            (0.0, 0.135, 0.085, 0.050),
                            (0.0, -0.135, 0.085, 0.050)):
        s = sphere("mush.sp_%.2f_%.2f" % (ox, oz), r, (ox, oy, oz), m_s,
                   segs=10, rings=7, scale=(1.0, 1.0, 0.45))
        attach_local(s, g)
    st = cyl("mush.stem", 0.135, 0.17, (0, 0, -0.06), m_st, segs=18)
    attach_local(st, g)
    for s in (-1, 1):
        e = box("mush.eye%d" % s, (0.018, 0.028, 0.040),
                (0.122 * 1.0, 0.052 * s, -0.045), m_dk)
        e.location = (0.108, 0.048 * s, -0.045)
        attach_local(e, g)
    return g


def coin(loc=(0, 0, 0)):
    m_c = _m("coin", GOLD, rough=0.22, metal=0.85)
    m_i = _m("coininner", (0.99, 0.86, 0.35), rough=0.3, metal=0.6)
    g = joint("coin", loc)
    c = cyl("coin.body", 0.24, 0.055, (0, 0, 0), m_c, segs=22, axis="Y")
    attach_local(c, g)
    i = cyl("coin.in", 0.145, 0.07, (0, 0, 0), m_i, segs=18, axis="Y")
    attach_local(i, g)
    return g


def loose_shell(loc=(0, 0, 0)):
    m_sh = _m("kshell", KSHELL, rough=0.42, spec=0.5)
    m_rim = _m("krim", KRIM, rough=0.55)
    g = joint("lshell", loc)
    g.empty_display_size = 0.15
    s = sphere("lshell.s", 0.255, (0, 0, 0), m_sh, segs=20, rings=12,
               scale=(0.96, 0.96, 0.80))
    attach_local(s, g)
    r = ring("lshell.r", 0.235, 0.042, (0, 0, -0.02), m_rim, major_segs=22,
             minor_segs=8, axis="Z")
    attach_local(r, g)
    g.scale = (0, 0, 0)
    return g


def clouds():
    m_c = _m("cloud", (0.99, 0.99, 0.99), rough=0.9, emit=0.10)
    out = []
    for i, (cx, cy, cz, sc) in enumerate(((-7.5, 9.0, 5.2, 1.0),
                                          (6.0, 11.0, 6.1, 1.35),
                                          (16.0, 10.0, 4.8, 0.9))):
        g = joint("cloud%d" % i, (cx, cy, cz))
        for (ox, oz, r) in ((-0.62, -0.05, 0.42), (0.0, 0.12, 0.60),
                            (0.66, -0.02, 0.46), (0.30, -0.22, 0.34),
                            (-0.32, -0.20, 0.32)):
            s = sphere("cloud%d_%.2f" % (i, ox), r * sc, (ox * sc, 0, oz * sc),
                       m_c, segs=12, rings=8, scale=(1.0, 0.8, 0.85))
            attach_local(s, g)
        out.append(g)
    return out


def backdrop():
    m_sky = _m("sky", SKY, rough=1.0, emit=0.55)
    m_h = _m("hill", (0.14, 0.38, 0.14), rough=0.9)
    m_h2 = _m("hill2", (0.10, 0.29, 0.11), rough=0.9)
    bd = plane("backdrop", 70, 34, (2, 34.0, 10.0), m_sky,
               rot=(math.pi / 2, 0, 0))
    hills = []
    for i, (cx, r, m) in enumerate(((-11.0, 4.2, m_h2), (4.0, 5.4, m_h),
                                    (17.0, 3.6, m_h2), (27.0, 4.6, m_h))):
        s = sphere("hill%d" % i, r, (cx, 26.0, -5.4), m, segs=18, rings=8,
                   scale=(2.1, 0.30, 1.05))
        hills.append(s)
    for i, (cx, r) in enumerate(((-6.0, 1.1), (12.0, 1.4), (21.0, 1.0))):
        b = cyl("bush%d" % i, r, 0.6, (cx, 15.0, -1.35),
                _m("bush", (0.20, 0.50, 0.17), rough=0.85), segs=14, axis="Y")
        hills.append(b)
    return bd, hills


def world_sky():
    w = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    grad = nt.nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "LINEAR"
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.30
    ramp.color_ramp.elements[0].color = (0.22, 0.42, 0.85, 1)
    ramp.color_ramp.elements[1].position = 0.75
    ramp.color_ramp.elements[1].color = (0.62, 0.82, 0.99, 1)
    mapn = nt.nodes.new("ShaderNodeMapping")
    texc = nt.nodes.new("ShaderNodeTexCoord")
    mapn.inputs["Rotation"].default_value = (0, math.radians(90), 0)
    nt.links.new(texc.outputs["Generated"], mapn.inputs["Vector"])
    nt.links.new(mapn.outputs["Vector"], grad.inputs["Vector"])
    nt.links.new(grad.outputs["Color"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.42
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return w


def lights():
    out = []
    d = bpy.data.lights.new("sun", type="SUN")
    d.energy = 2.3
    d.angle = math.radians(14)
    d.color = (1.0, 0.97, 0.90)
    ob = lib.link(bpy.data.objects.new("sun", d))
    ob.rotation_euler = (math.radians(58), math.radians(8), math.radians(-38))
    out.append(ob)

    f = bpy.data.lights.new("fill", type="AREA")
    f.energy = 150.0
    f.size = 14.0
    f.color = (0.72, 0.84, 1.0)
    fo = lib.link(bpy.data.objects.new("fill", f))
    fo.location = (0.0, -11.0, 7.0)
    fo.rotation_euler = (math.radians(58), 0, 0)
    out.append(fo)

    r = bpy.data.lights.new("rim", type="AREA")
    r.energy = 170.0
    r.size = 9.0
    r.color = (1.0, 0.88, 0.70)
    ro = lib.link(bpy.data.objects.new("rim", r))
    ro.location = (10.0, 9.0, 6.0)
    ro.rotation_euler = (math.radians(118), 0, math.radians(30))
    out.append(ro)
    return out


def camera():
    cd = bpy.data.cameras.new("cam")
    cd.lens = 48.0
    cd.sensor_width = 36.0
    ob = lib.link(bpy.data.objects.new("cam", cd))
    bpy.context.scene.camera = ob
    ob.rotation_euler = (math.pi / 2, 0, 0)
    return ob


# ------------------------------------------------------------------ full set

def build_set():
    s = {}
    s["ground"] = ground()
    s["bricks"] = [brick((x, 0, 2.0), "brick_%d" % i)
                   for i, x in enumerate((-3.0, -2.0, -1.0, 1.0, 2.0, 3.0))]
    s["qb"] = q_block((0.0, 0, 2.0))
    s["pipe"] = pipe((9.5, 0, 0.0))
    s["pipe2"] = pipe((-13.0, 0, 0.0))
    s["mush"] = mushroom((0.0, 0, 2.0))
    s["mush"].scale = (0, 0, 0)
    s["coin"] = coin((0.0, 0, 2.0))
    s["coin"].scale = (0, 0, 0)
    s["lshell"] = loose_shell((0, 0, 0.26))
    s["clouds"] = clouds()
    s["backdrop"], s["hills"] = backdrop()
    world_sky()
    s["lights"] = lights()
    s["cam"] = camera()
    s["mario"] = plumber("mario", RED, RED, tall=1.0, wide=1.0, nose_r=0.074,
                         stache=(0.17, 0.145, 0.058))
    s["luigi"] = plumber("luigi", GREEN, GREEN, tall=1.13, wide=0.87,
                         nose_r=0.082, stache=(0.19, 0.145, 0.052),
                         hair=(0.14, 0.07, 0.03))
    s["koopa"] = koopa()
    return s
