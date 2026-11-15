"""Procedural hand + forearm with correct topology, rig and analytic weights.

The generator builds a quad CAGE from anatomical parameters, then a
Catmull-Clark subdivision makes the skin. Everything that makes a hand bend
well is decided in the cage:

* palm and forearm are one tube with a 26-vertex ring (12 dorsal columns,
  12 palmar columns, one mid-vertex per side wall)
* the knuckle end-cap is an 11x2 quad grid; each finger leaves through a
  2x2 port (8-vertex boundary), with one quad column of WEB between fingers
* the palmar edge of each port sits 30 % down the proximal phalanx, the
  dorsal edge at the knuckle: the palm skin really does cover the base of the
  finger, which is why a fist does not shear the palm crease
* three loops across every finger joint (joint +/- 0.5 r), two between
* the thumb leaves from a 2x2 port straddling the radial palm corner, on its
  own oblique axis, pronated ~70 deg so its nail faces outward
* fingertip caps are 4 quads around one valence-4 vertex: no triangles
* fingernails are an inset region on the distal dorsal quads, raised, with
  creased borders: a real nail bed loop

Coordinates: wrist crease centre at origin, fingers along +X, back of hand +Z,
index finger toward -Y (a LEFT hand in T-pose seen from above). Metres.

Bone roll: every digit bone has local Z pointing VOLAR (into the palm), so
**+X rotation is flexion** for every finger and the thumb. Spread is local Z.
"""
import math
import numpy as np
import bpy, bmesh
from mathutils import Vector, Matrix, Quaternion

MM = 0.001
DIGITS = ("index", "middle", "ring", "little")

# Adult hand, skeletal segment lengths in mm. Values are typical adult means
# from the hand-anthropometry literature (radiographic studies, e.g. Buryanov &
# Kotiuk 2010, Alexander 2010); recalled, not re-verified this session. Ratios
# P:M:D ~ 1 : 0.6 : 0.4 are the robust part.
CANON = {
    "mc":    {"thumb": 46, "index": 68, "middle": 65, "ring": 58, "little": 54},
    "prox":  {"thumb": 32, "index": 40, "middle": 45, "ring": 42, "little": 33},
    "mid":   {"index": 23, "middle": 28, "ring": 27, "little": 19},
    "dist":  {"thumb": 24, "index": 16, "middle": 18, "ring": 17, "little": 15},
    # skin width at the proximal phalanx base, mm
    "width": {"thumb": 23, "index": 19.5, "middle": 19.5, "ring": 18.0, "little": 16.0},
}

DEFAULT = {
    "scale": 1.0,                 # uniform
    "width_scale": 1.0302,        # finger girth multiplier  (CMA-ES canon fit)
    "finger_len": 1.0366,         # phalanx length multiplier (CMA-ES canon fit)
    "breadth": 1.0421,            # knuckle spacing multiplier (CMA-ES canon fit)
    "palm_thick": 27.5,           # mm, mid palm (CMA-ES canon fit)
    "palm_hollow": 3.5,           # mm, centre-of-palm concavity
    "knuckle_bump": 2.0,          # mm
    "arch": 4.0,                  # mm, transverse metacarpal arch (dorsal)
    "fan_deg": (-5.0, 0.0, 6.0, 12.0),      # metacarpal fan, index..little
    "spread_deg": (-4.0, 0.0, 3.0, 6.0),    # rest spread of fingers beyond MC line
    "mcp_x": (93.0, 95.0, 88.0, 78.0),      # knuckle arc (x, mm)
    "mcp_y": (-31.0, -10.5, 10.0, 29.5),
    "mcp_z": (0.0, 1.5, 0.0, -4.0),         # palm cupping at rest
    "thumb_cmc": (24.0, -21.0, -7.0),
    "thumb_abduct": 42.0,         # deg in palm plane away from index
    "thumb_palmar": 12.0,         # deg out of palm plane, volar
    "thumb_pronate": 70.0,        # deg about own axis
    "thenar": 1.6,  "thenar_bulge": 7.0,                # thumb-MC girth multiplier (thenar mass)
    "forearm_len": 250.0,
    "wrist_w": 58.0, "wrist_d": 38.0,
    "elbow_w": 82.0, "elbow_d": 68.0,
    "nails": True,
    "blend": (0.55, 0.42, 0.36),  # weight blend half-width at MCP/PIP/DIP, x finger depth
    "blend_asym": 0.5,            # >0 widens the dorsal blend, narrows the volar one
    "blend_shift": 0.5,           # dorsal blend centre moves distally by this x half-depth
    "cc_comp": 1.0985,            # cage inflation vs subdivision shrink (CMA-ES canon fit)
}


def _ss(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _rot(v, axis, deg):
    return Quaternion(Vector(axis).normalized(), math.radians(deg)) @ Vector(v)


class HandBuilder:
    def __init__(self, params=None):
        p = dict(DEFAULT)
        p.update(params or {})
        self.p = p
        self.bm = bmesh.new()
        self.tags = []          # per vertex: (region, digit, t_m)
        self.joints = {}        # name -> (head Vector, radius m)
        self.digit = {}         # digit -> dict(frame, chain)

    # ------------------------------------------------------------ helpers
    def v(self, co, tag):
        vert = self.bm.verts.new(co)
        self.tags.append(tag)
        return vert

    def quad(self, a, b, c, d):
        return self.bm.faces.new((a, b, c, d))

    def bridge(self, ra, rb):
        n = len(ra)
        for k in range(n):
            self.quad(ra[k], ra[(k + 1) % n], rb[(k + 1) % n], rb[k])

    # ------------------------------------------------------------ layout
    def layout(self):
        p, s = self.p, self.p["scale"] * MM
        ws = p["width_scale"]
        self.s = s
        D = {}
        for i, d in enumerate(DIGITS):
            head = Vector((p["mcp_x"][i], p["mcp_y"][i] * p["breadth"], p["mcp_z"][i])) * s
            fan = math.radians(p["fan_deg"][i])
            mcdir = Vector((math.cos(fan), math.sin(fan), 0))
            base = head - mcdir * CANON["mc"][d] * s
            sp = math.radians(p["fan_deg"][i] + p["spread_deg"][i] * p.get("spread_mult", 1.0))
            a = Vector((math.cos(sp), math.sin(sp), 0)).normalized()
            l = Vector((-math.sin(sp), math.cos(sp), 0))
            u = Vector((0, 0, 1))
            fl = p["finger_len"]
            P, M_, Dd = (CANON["prox"][d] * s * fl, CANON["mid"][d] * s * fl, CANON["dist"][d] * s * fl)
            w = CANON["width"][d] * s * ws
            D[d] = dict(head=head, base=base, a=a, l=l, u=u, P=P, M=M_, D=Dd, w=w,
                        dep=0.86 * w, B=[0.0, P, P + M_], L=P + M_ + Dd)
        self.D = D
        # thumb frame
        cmc = Vector(p["thumb_cmc"]) * s
        a = _rot(_rot((1, 0, 0), (0, 0, 1), -p["thumb_abduct"]), (0, 1, 0), 0)
        a = _rot(a, Vector((0, 0, 1)).cross(a), -p["thumb_palmar"])
        a = Vector(a).normalized()
        if a.z > 0:
            a = Vector((a.x, a.y, -a.z))
        z0 = (Vector((0, 0, 1)) - a * a.dot(Vector((0, 0, 1)))).normalized()
        cand = [_rot(z0, a, ang) for ang in (p["thumb_pronate"], -p["thumb_pronate"])]
        u = max(cand, key=lambda c: c.dot(Vector((0, -1, 0))))
        u = Vector(u).normalized()
        l = u.cross(a).normalized()
        if l.dot(Vector((0, 1, 0))) < 0:      # keep a right-handed (l, u) sense matching fingers
            l = -l
        fl = p["finger_len"]
        MC, P, Dd = CANON["mc"]["thumb"] * s, CANON["prox"]["thumb"] * s * fl, CANON["dist"]["thumb"] * s * fl
        w = CANON["width"]["thumb"] * s * ws
        self.T = dict(cmc=cmc, a=a, u=u, l=l, MC=MC, P=P, D=Dd, w=w, dep=0.85 * w,
                      B=[MC, MC + P], L=MC + P + Dd)

    # ------------------------------------------------------------ palm + forearm tube
    def ring_layout(self):
        """Column y (at the knuckle cap) and per-column metadata for the 26-ring."""
        cols = []
        for i, d in enumerate(DIGITS):
            g = self.D[d]
            hw = 0.41 * g["w"]
            for k, off in enumerate((-hw, 0.0, hw)):
                c = g["head"] + g["l"] * off
                cols.append(dict(digit=i, k=k, y=c.y, x=c.x, z=g["head"].z))
        return cols

    def build_tube(self):
        p, s = self.p, self.s
        cols = self.ring_layout()
        ncol = len(cols)                    # 12
        y_cap = np.array([c["y"] for c in cols])
        x_cap = np.array([c["x"] for c in cols])
        z_cap = np.array([c["z"] for c in cols])
        ymid = 0.5 * (y_cap[0] + y_cap[-1])
        half = 0.5 * (y_cap[-1] - y_cap[0])
        # wrist ring y positions: compress the column layout into the wrist width
        y_wr = ymid * 0.3 + (y_cap - ymid) * (p["wrist_w"] * s * 0.5 / (half + 0.004 * self.p["scale"]))
        y_wr = y_wr - y_wr.mean() * 0.7
        # palm stations as fractions of each column's x_cap
        fr = [0.0, 0.20, 0.42, 0.62, 0.80, 0.92]
        rings = []
        th = p["palm_thick"] * s
        for si, f in enumerate(fr):
            fs = _ss(np.array(f / 0.92))
            y = y_wr + (y_cap - y_wr) * fs
            x = x_cap * f
            t = (y - ymid) / (half + 1e-9)       # -1..1 across
            # dorsal surface: wrist ellipse -> palm with arch
            zd_w = 0.5 * p["wrist_d"] * s * np.sqrt(np.clip(1 - (t * 0.92) ** 2, 0.05, 1))
            zd_p = 0.5 * th * 0.55 + p["arch"] * s * (1 - t ** 2) + z_cap * 0.6
            zd = zd_w + (zd_p - zd_w) * fs
            # palmar surface: thenar (t<0 near wrist), hypothenar (t>0), hollow centre
            zp_w = -0.5 * p["wrist_d"] * s * np.sqrt(np.clip(1 - (t * 0.92) ** 2, 0.05, 1))
            hollow = p["palm_hollow"] * s * np.exp(-((t - 0.1) / 0.45) ** 2) * math.sin(math.pi * min(f / 0.92, 1.0))
            hyp = 3.0 * s * np.exp(-((t - 0.85) / 0.3) ** 2) * math.sin(math.pi * min(f / 0.8, 1.0))
            thn = p["thenar_bulge"] * s * np.exp(-((t + 0.95) / 0.35) ** 2) * math.sin(math.pi * min(f / 0.75, 1.0))
            zp_p = -0.5 * th * 0.62 - hyp - thn + hollow + z_cap * 0.6
            zp = zp_w + (zp_p - zp_w) * fs
            ring = []
            for c in range(ncol):
                ring.append(self.v((x[c], y[c], zd[c]), ("palm", -1, 0.0)))
            # ulnar (little-side) wall mid
            ring.append(self.v((x[-1], y[-1] + 0.20 * (zd[-1] - zp[-1]), 0.5 * (zd[-1] + zp[-1])), ("palm", -1, 0.0)))
            for c in reversed(range(ncol)):
                ring.append(self.v((x[c], y[c], zp[c]), ("palm", -1, 0.0)))
            ring.append(self.v((x[0], y[0] - 0.20 * (zd[0] - zp[0]), 0.5 * (zd[0] + zp[0])), ("palm", -1, 0.0)))
            rings.append(ring)
        # forearm rings behind the wrist, mapped by angle onto growing ellipses
        L = p["forearm_len"] * s
        wr = np.array([tuple(v.co) for v in rings[0]])
        cy, cz = wr[:, 1].mean(), wr[:, 2].mean()
        ang = np.arctan2((wr[:, 2] - cz) / (p["wrist_d"] * s), (wr[:, 1] - cy) / (p["wrist_w"] * s))
        fa = []
        for f in (0.06, 0.18, 0.35, 0.55, 0.75, 0.92, 1.0):
            x = -L * f
            k = _ss(np.array(min(f / 0.7, 1.0)))
            W = (p["wrist_w"] + (p["elbow_w"] - p["wrist_w"]) * k) * s * 0.5
            Dp = (p["wrist_d"] + (p["elbow_d"] - p["wrist_d"]) * k) * s * 0.5
            ring = [self.v((x, cy * (1 - f) + W * math.cos(a_), cz * (1 - f) + Dp * math.sin(a_)),
                           ("forearm", -1, x)) for a_ in ang]
            fa.append(ring)
        allr = list(reversed(fa)) + rings
        for i in range(len(allr) - 1):
            self.bridge(allr[i], allr[i + 1])
        self.palm_rings = rings
        self.elbow_ring = fa[-1]
        self.cols = cols
        return rings

    def grid_cap(self, ring, x_offsets, mid_x, tag_fn, mid_z=None):
        """Cap a 26-ring with an 11x2 quad grid. Returns the 3x12 vertex grid
        [dorsal row, middle row, palmar row] with columns 0..11."""
        n = 12
        dors = ring[0:n]
        pal = list(reversed(ring[n + 1:2 * n + 1]))
        side_u, side_r = ring[n], ring[2 * n + 1]
        mid = [side_r]
        for c in range(1, n - 1):
            co = (dors[c].co + pal[c].co) * 0.5
            co.x = mid_x[c]
            if mid_z is not None:
                co.z = mid_z[c]
            mid.append(self.v(co, tag_fn(c)))
        mid.append(side_u)
        grid = [dors, mid, pal]
        for r in range(2):
            for c in range(n - 1):
                self.quad(grid[r][c], grid[r][c + 1], grid[r + 1][c + 1], grid[r + 1][c])
        return grid

    # ------------------------------------------------------------ digits
    def section(self, center, l, u, w, d, ang):
        return [center + l * (0.5 * w * math.cos(a)) + u * (0.5 * d * math.sin(a)) for a in ang]

    def tube_from_port(self, port, frame, stations, tip, digit, t_of):
        """Extrude a digit tube from an 8-vertex port ring.

        stations: list of (t, w, d, blend, dz_profile) where blend in 0..1 mixes
        the port ring (0) with the canonical section (1)."""
        origin, a, l, u = frame
        cen = sum((v.co for v in port), Vector()) / len(port)
        rel = [v.co - cen for v in port]
        th = np.unwrap([math.atan2(r.dot(u), r.dot(l)) for r in rel])
        if th[-1] > th[0]:
            port = [port[0]] + list(reversed(port[1:]))
            rel = [v.co - cen for v in port]
            th = np.unwrap([math.atan2(r.dot(u), r.dot(l)) for r in rel])
        # regularised angles: offset + k*(-45deg)
        off = np.mean(th - (-np.pi / 4) * np.arange(8))
        reg = off + (-np.pi / 4) * np.arange(8)
        prev = port
        rings = [port]
        cc = self.p["cc_comp"]
        for (t, w, d, blend, prof) in stations:
            c = origin + a * t
            angs = th + (reg - th) * min(1.0, blend * 1.4)
            canon = self.section(c, l, u, w * cc, d * cc, angs)
            ring = []
            for k in range(8):
                co = port[k].co.lerp(canon[k], blend)
                co = co + u * prof[k] * self.s
                ring.append(self.v(co, ("digit", digit, t_of(co))))
            self.bridge(prev, ring)
            rings.append(ring)
            prev = ring
        tipv = self.v(origin + a * tip[0] + u * tip[1], ("digit", digit, tip[0]))
        r = prev
        for k in (0, 2, 4, 6):
            self.quad(r[k], r[(k + 1) % 8], r[(k + 2) % 8], tipv)
        return rings

    def finger_stations(self, g):
        P, M_, Dd, w0, d0 = g["P"], g["M"], g["D"], g["w"], g["dep"]
        L = g["L"]
        def wd(t, bulge=0.0, pad=0.0):
            k = 1 - 0.20 * t / L
            return w0 * k * (1 + bulge), d0 * k * (1 + bulge * 0.6)
        r1 = 0.5 * wd(P)[1]
        r2 = 0.5 * wd(P + M_)[1]
        kb = self.p["knuckle_bump"]
        flat = [0] * 8
        def prof(dorsal=0.0, pad=0.0, crease=0.0):
            # indices: 0..2 dorsal, 3 lateral+, 4..6 palmar, 7 lateral-
            return [dorsal * 0.5, dorsal, dorsal * 0.5, 0, -pad * 0.5 + crease * 0.5,
                    -pad + crease, -pad * 0.5 + crease * 0.5, 0]
        S = []
        S.append((0.28 * P, *wd(0.28 * P), 0.55, prof(0.0, 0.0)))
        S.append((0.58 * P, *wd(0.58 * P, -0.03), 0.92, prof(0.0, 0.6)))
        S.append((P - 0.55 * r1, *wd(P - 0.55 * r1, 0.02), 1.0, flat))
        S.append((P, *wd(P, 0.05), 1.0, prof(kb * 0.45, 0.0, 0.9)))
        S.append((P + 0.55 * r1, *wd(P + 0.55 * r1, 0.02), 1.0, flat))
        S.append((P + 0.5 * M_, *wd(P + 0.5 * M_, -0.03), 1.0, prof(0.0, 0.5)))
        S.append((P + M_ - 0.55 * r2, *wd(P + M_ - 0.55 * r2, 0.02), 1.0, flat))
        S.append((P + M_, *wd(P + M_, 0.04), 1.0, prof(kb * 0.3, 0.0, 0.7)))
        S.append((P + M_ + 0.55 * r2, *wd(P + M_ + 0.55 * r2, 0.0), 1.0, flat))
        S.append((P + M_ + 0.50 * Dd, *wd(P + M_ + 0.5 * Dd, 0.0), 1.0, prof(0.0, 0.8)))
        S.append((P + M_ + 0.84 * Dd, *wd(P + M_ + 0.84 * Dd, -0.06), 1.0, prof(-0.2, 0.6)))
        tip = (L + 0.12 * d0, -0.12 * d0)
        return S, tip, (P + M_ + 0.55 * r2, P + M_ + 0.84 * Dd)

    def build_fingers(self, grid):
        p, s = self.p, self.s
        self.nail_spans = []
        for i, d in enumerate(DIGITS):
            g = self.D[d]
            c0 = 3 * i
            dors, mid, pal = grid
            port = [dors[c0], dors[c0 + 1], dors[c0 + 2], mid[c0 + 2],
                    pal[c0 + 2], pal[c0 + 1], pal[c0], mid[c0]]
            # remove the centre vertex of the 2x2 patch (it carries 4 cap quads)
            centre = mid[c0 + 1]
            self.bm.verts.remove(centre)
            S, tip, nail = self.finger_stations(g)
            origin = g["head"] + g["u"] * (-0.06 * g["dep"])
            t_of = (lambda co, o=origin, a=g["a"]: (co - o).dot(a))
            self.tube_from_port(port, (origin, g["a"], g["l"], g["u"]), S, tip, i, t_of)
            self.digit[d] = dict(origin=origin)
            self.nail_spans.append((i, origin, g["a"], g["u"], nail))

    def build_thumb(self, rings):
        T, s = self.T, self.s
        # port: palm col1, palm col0, radial side-mid, at palm stations 1..3
        n = 12
        def pal(r, c):
            return r[n + 1:2 * n + 1][::-1][c]
        r1, r2, r3 = rings[2], rings[3], rings[4]
        side = lambda r: r[2 * n + 1]
        port = [pal(r1, 1), pal(r1, 0), side(r1), side(r2), side(r3), pal(r3, 0), pal(r3, 1), pal(r2, 1)]
        centre = pal(r2, 0)
        # delete the 4 faces around the centre vertex, then the vertex
        self.bm.verts.remove(centre)
        cen = sum((v.co for v in port), Vector()) / 8
        a, l, u = T["a"], T["l"], T["u"]
        origin = T["cmc"]
        tp = (cen - origin).dot(a)
        MC, P, Dd, w0, d0 = T["MC"], T["P"], T["D"], T["w"], T["dep"]
        L = T["L"]
        th = self.p["thenar"]
        r1_ = 0.5 * d0
        def wd(t, k=1.0):
            q = 1 - 0.18 * (t - MC) / (L - MC) if t > MC else 1.0
            return w0 * q * k, d0 * q * k
        flat = [0] * 8
        def prof(dorsal=0.0, pad=0.0, crease=0.0):
            return [dorsal * 0.5, dorsal, dorsal * 0.5, 0, -pad * 0.5 + crease * 0.5,
                    -pad + crease, -pad * 0.5 + crease * 0.5, 0]
        st = []
        t1 = tp + 0.45 * (MC - tp)
        st.append((t1, *wd(t1, th), 0.55, prof(0, 3.0)))
        t2 = tp + 0.80 * (MC - tp)
        st.append((t2, *wd(t2, 0.5 * (1 + th)), 0.9, prof(0, 0.8)))
        st.append((MC - 0.4 * r1_, *wd(MC - 0.4 * r1_, 1.03), 1.0, flat))
        st.append((MC, *wd(MC, 1.06), 1.0, prof(self.p["knuckle_bump"] * 0.4, 0, 0.8)))
        st.append((MC + 0.55 * r1_, *wd(MC + 0.55 * r1_, 1.02), 1.0, flat))
        st.append((MC + 0.5 * P, *wd(MC + 0.5 * P, 0.98), 1.0, prof(0, 0.5)))
        st.append((MC + P - 0.5 * r1_, *wd(MC + P - 0.5 * r1_, 1.01), 1.0, flat))
        st.append((MC + P, *wd(MC + P, 1.04), 1.0, prof(0.3, 0, 0.7)))
        st.append((MC + P + 0.5 * r1_, *wd(MC + P + 0.5 * r1_, 1.0), 1.0, flat))
        st.append((MC + P + 0.5 * Dd, *wd(MC + P + 0.5 * Dd, 1.0), 1.0, prof(0, 0.9)))
        st.append((MC + P + 0.84 * Dd, *wd(MC + P + 0.84 * Dd, 0.93), 1.0, prof(-0.2, 0.6)))
        tip = (L + 0.12 * d0, -0.12 * d0)
        t_of = (lambda co: (co - origin).dot(a))
        self.tube_from_port(port, (origin, a, l, u), st, tip, 4, t_of)
        self.T["tp"] = tp
        self.nail_spans.append((4, origin, a, u, (MC + P + 0.5 * r1_, MC + P + 0.84 * Dd)))

    def nails(self):
        """Inset the distal dorsal quads into a nail bed and raise the plate."""
        bm = self.bm
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        crease_edges = []
        for (di, origin, a, u, (t0, t1)) in self.nail_spans:
            faces = []
            for f in bm.faces:
                c = f.calc_center_median()
                t = (c - origin).dot(a)
                if t0 - 1e-5 <= t <= t1 + 1e-5 and f.normal.dot(u) > 0.55:
                    # keep only faces of this digit
                    if all(self.vtag(v)[1] == di for v in f.verts):
                        faces.append(f)
            if not faces:
                continue
            res = bmesh.ops.inset_region(bm, faces=faces, thickness=1.1 * self.s,
                                         depth=0.35 * self.s, use_even_offset=True)
            for f in faces:
                for e in f.edges:
                    if any(ff not in faces for ff in e.link_faces):
                        crease_edges.append(e)
        return crease_edges

    def vtag(self, v):
        return self._nearest_tag(v)

    # ------------------------------------------------------------ build
    def build(self, name="Hand"):
        self.layout()
        rings = self.build_tube()
        # knuckle cap: the palmar row moves forward onto the proximal phalanx
        n = 12
        last = rings[-1]
        for c in range(n):
            col = self.cols[c]
            g = self.D[DIGITS[col["digit"]]]
            pal_v = last[n + 1:2 * n + 1][::-1][c]
            dor_v = last[c]
            base = g["head"] + g["l"] * (pal_v.co - g["head"]).dot(g["l"])
            dor_v.co = base + g["u"] * (0.5 * g["dep"] * self.p["cc_comp"]) - g["a"] * 0.002 * self.p["scale"]
            if col["k"] == 1:
                dor_v.co += g["u"] * self.p["knuckle_bump"] * self.s
            pal_v.co = base - g["u"] * (0.5 * g["dep"] * self.p["cc_comp"]) + g["a"] * 0.38 * g["P"]
        # web columns: pull the web quads forward between fingers
        mid_x = []
        for c in range(n):
            g = self.D[DIGITS[self.cols[c]["digit"]]]
            mid_x.append(last[c].co.x + 0.24 * g["P"])
        # side-wall mids of the cap ring follow the moved rows, bulged outward
        for side, c, sgn in ((last[2 * n + 1], 0, -1), (last[n], n - 1, 1)):
            g = self.D[DIGITS[self.cols[c]["digit"]]]
            dv, pv = last[c], last[n + 1:2 * n + 1][::-1][c]
            side.co = (dv.co + pv.co) * 0.5 + g["l"] * (sgn * 0.18 * g["dep"])
        grid = self.grid_cap(last, None, mid_x, lambda c: ("palm", -1, 0.0))
        self.build_fingers(grid)
        self.build_thumb(rings)
        er = self.elbow_ring
        x = er[0].co.x
        self.grid_cap(er, None, [x] * 12, lambda c: ("forearm", -1, x))
        creases = self.nails() if self.p["nails"] else []
        bmesh.ops.recalc_face_normals(self.bm, faces=self.bm.faces)
        me = bpy.data.meshes.new(name)
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
        crease_keys = {tuple(sorted((e.verts[0].index, e.verts[1].index))) for e in creases if e.is_valid}
        self.bm.verts.index_update()
        tags = [self._nearest_tag(v) for v in self.bm.verts]
        self.bm.to_mesh(me)
        self.bm.free()
        # creases (Blender 5.x: float attribute 'crease_edge' on the EDGE domain)
        if crease_keys:
            attr = me.attributes.new("crease_edge", "FLOAT", "EDGE")
            for e in me.edges:
                if tuple(sorted(e.vertices)) in crease_keys:
                    attr.data[e.index].value = 0.55
        for poly in me.polygons:
            poly.use_smooth = True
        self.tags_final = tags
        return ob

    def _nearest_tag(self, v):
        """Tag a vertex by geometry: which digit tube / palm / forearm it lies in."""
        co = v.co
        if co.x < -0.004 * self.p["scale"]:
            return ("forearm", -1, co.x)
        best, bd = None, 1e9
        for i, d in enumerate(DIGITS):
            g = self.D[d]
            o = self.digit.get(d, {}).get("origin", g["head"])
            t = (co - o).dot(g["a"])
            if t < -0.002:
                continue
            q = o + g["a"] * min(t, g["L"])
            dist = (co - q).length
            if dist < 0.75 * g["w"] and dist < bd:
                best, bd = ("digit", i, t), dist
        T = self.T
        t = (co - T["cmc"]).dot(T["a"])
        if "tp" in T and t > T["tp"] - 0.002:
            q = T["cmc"] + T["a"] * min(t, T["L"])
            dist = (co - q).length
            if dist < 0.9 * T["w"] * self.p["thenar"] and dist < bd:
                best, bd = ("digit", 4, t), dist
        return best or ("palm", -1, 0.0)


# ---------------------------------------------------------------- rig
TWIST_SEGS = ((-0.75, -0.5), (-0.5, -0.25), (-0.25, 0.0))
TWIST_INFL = (1 / 3, 2 / 3, 1.0)
# forearm weight nodes (fraction of forearm length, negative = toward elbow)
TWIST_NODES = (("forearm", -0.85), ("forearm_tw1", -0.60), ("forearm_tw2", -0.35), ("forearm_tw3", -0.10))
BONE_CHAIN = {d: [f"mc_{d}", f"{d}.01", f"{d}.02", f"{d}.03"] for d in DIGITS}
BONE_CHAIN["thumb"] = ["thumb.01", "thumb.02", "thumb.03"]


def build_rig(hb, name="HandRig"):
    """Armature with forearm twist split, hand, metacarpals, phalanges.
    Roll: local Z = volar for every digit bone, so +X rotation flexes."""
    s = hb.s
    arm = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.edit_bones
    L = hb.p["forearm_len"] * s
    fa = eb.new("forearm"); fa.head = (-L, 0, 0); fa.tail = (0, 0, 0)
    fa.align_roll(Vector((0, 0, -1)))
    for k, (x0, x1) in enumerate(TWIST_SEGS):
        tb = eb.new(f"forearm_tw{k+1}")
        tb.head = (x0 * L, 0, 0); tb.tail = (x1 * L, 0, 0)
        tb.parent = fa
        tb.align_roll(Vector((0, 0, -1)))
    hand = eb.new("hand"); hand.head = (0, 0, 0); hand.tail = (0.028 * hb.p["scale"], 0, 0)
    hand.parent = fa; hand.use_connect = True
    hand.align_roll(Vector((0, 0, -1)))
    for i, d in enumerate(DIGITS):
        g = hb.D[d]
        o = hb.digit[d]["origin"]
        volar = -g["u"]
        mc = eb.new(f"mc_{d}")
        mc.head = g["base"] + g["u"] * (-0.06 * g["dep"]); mc.tail = o; mc.parent = hand
        mc.align_roll(volar)
        prev = mc
        pts = [o + g["a"] * t for t in (0.0, g["P"], g["P"] + g["M"], g["L"])]
        for k in range(3):
            b = eb.new(f"{d}.0{k+1}")
            b.head, b.tail = pts[k], pts[k + 1]
            b.parent = prev; b.use_connect = True
            b.align_roll(volar)
            prev = b
    T = hb.T
    pts = [T["cmc"] + T["a"] * t for t in (0.0, T["MC"], T["MC"] + T["P"], T["L"])]
    prev = hand
    for k in range(3):
        b = eb.new(f"thumb.0{k+1}")
        b.head, b.tail = pts[k], pts[k + 1]
        b.parent = prev
        b.use_connect = k > 0
        b.align_roll(-T["u"])
        prev = b
    bpy.ops.object.mode_set(mode="OBJECT")
    # distribute the hand's twist down the forearm: 1/3, 2/3, 3/3
    for k, infl in enumerate(TWIST_INFL):
        pb = rig.pose.bones[f"forearm_tw{k+1}"]
        c = pb.constraints.new("COPY_ROTATION")
        c.target = rig; c.subtarget = "hand"
        c.use_x = False; c.use_z = False; c.use_y = True
        c.target_space = "LOCAL"; c.owner_space = "LOCAL"
        c.influence = infl
    for pb in rig.pose.bones:
        pb.rotation_mode = "XYZ"
    return rig


def analytic_weights(hb, ob, rig):
    """Closed-form skin weights from the generator's own knowledge of where
    every vertex sits along its digit. Partition of unity by construction."""
    s = hb.s
    me = ob.data
    V = np.array([tuple(v.co) for v in me.vertices])
    names = [b.name for b in rig.data.bones]
    W = {n: np.zeros(len(V)) for n in names}
    tags = hb.tags_final

    def ramp(t, b, h):
        return _ss((t - (b - h)) / (2 * h))

    def chain_weights(t, bounds, hs):
        # bones k=0..len(bounds): w_k = S(t-B_{k-1}) * (1 - S(t-B_k))
        n = len(bounds) + 1
        out = []
        for k in range(n):
            lo = ramp(t, bounds[k - 1], hs[k - 1]) if k > 0 else 1.0
            hi = (1 - ramp(t, bounds[k], hs[k])) if k < n - 1 else 1.0
            out.append(lo * hi)
        return out

    # palm weights helper
    mc_lines = []
    for d in DIGITS:
        g = hb.D[d]
        mc_lines.append((g["base"], hb.digit[d]["origin"]))
    T = hb.T

    def palm_w(co):
        w = {}
        x = co[0]
        # forearm/hand at the wrist
        k_hand = _ss((x + 0.012 * hb.p["scale"]) / (0.026 * hb.p["scale"]))
        if k_hand < 1:
            # forearm region handled by caller
            pass
        # lateral metacarpal weights
        g = []
        for (a, b) in mc_lines:
            ab = b - a
            tt = max(0.0, min(1.0, (Vector(co) - a).dot(ab) / ab.length_squared))
            q = a + ab * tt
            dy = (Vector(co) - q)
            dy = math.hypot(dy.y, dy.x * 0.3)
            g.append(math.exp(-(dy / (0.010 * hb.p["scale"])) ** 2))
        gs = sum(g) + 1e-12
        f_mc = _ss((x - 0.012 * hb.p["scale"]) / (0.035 * hb.p["scale"]))
        for i, d in enumerate(DIGITS):
            w[f"mc_{d}"] = (g[i] / gs) * f_mc
        w["hand"] = 1 - f_mc
        # thenar: thumb.01 claims tissue near the thumb metacarpal
        a, b = T["cmc"], T["cmc"] + T["a"] * T["MC"]
        ab = b - a
        tt = max(0.0, min(1.0, (Vector(co) - a).dot(ab) / ab.length_squared))
        dth = (Vector(co) - (a + ab * tt)).length
        wt = 0.85 * math.exp(-(dth / (0.016 * hb.p["scale"])) ** 2)
        for kk in w:
            w[kk] *= (1 - wt)
        w["thumb.01"] = wt
        return w

    def blend_wrist(co, w):
        x = co[0]
        k = _ss((x + 0.010 * hb.p["scale"]) / (0.024 * hb.p["scale"]))
        out = {kk: vv * k for kk, vv in w.items()}
        out["forearm_tw3"] = out.get("forearm_tw3", 0) + (1 - k)
        return out

    Lfa = hb.p["forearm_len"] * s
    for i, co in enumerate(V):
        reg, di, t = tags[i]
        if reg != "digit" and co[0] < -0.010 * hb.p["scale"]:
            f = co[0] / Lfa
            xs = [n[1] for n in TWIST_NODES]
            w = {}
            if f <= xs[0]:
                w["forearm"] = 1.0
            elif f >= xs[-1]:
                w["forearm_tw3"] = 1.0
            else:
                for j in range(len(xs) - 1):
                    if xs[j] <= f <= xs[j + 1]:
                        u_ = float(_ss((f - xs[j]) / (xs[j + 1] - xs[j])))
                        w[TWIST_NODES[j][0]] = 1 - u_
                        w[TWIST_NODES[j + 1][0]] = u_
        elif reg == "digit" and di < 4:
            d = DIGITS[di]
            g = hb.D[d]
            o = hb.digit[d]["origin"]
            t = (Vector(co) - o).dot(g["a"])
            q = o + g["a"] * t
            side = max(-1.0, min(1.0, (Vector(co) - q).dot(g["u"]) / (0.5 * g["dep"])))
            bl = hb.p["blend"]
            k = max(0.15, 1 + hb.p["blend_asym"] * side)
            hs = [bl[0] * g["dep"] * k, bl[1] * g["dep"] * k, bl[2] * g["dep"] * k]
            sh = hb.p["blend_shift"] * side * 0.5 * g["dep"]
            cw = chain_weights(t - sh, [0.0, g["P"], g["P"] + g["M"]], hs)
            w = {}
            pw = palm_w(co)
            for kk, vv in pw.items():
                w[kk] = vv * cw[0]
            for k, bn in enumerate(BONE_CHAIN[d][1:]):
                w[bn] = w.get(bn, 0) + cw[k + 1]
        elif reg == "digit" and di == 4:
            t = (Vector(co) - T["cmc"]).dot(T["a"])
            tp = T["tp"]
            q = T["cmc"] + T["a"] * t
            side = max(-1.0, min(1.0, (Vector(co) - q).dot(T["u"]) / (0.5 * T["dep"])))
            bl = hb.p["blend"]
            k = max(0.15, 1 + hb.p["blend_asym"] * side)
            sh = hb.p["blend_shift"] * side * 0.5 * T["dep"]
            # base blend: palm weights -> thumb chain over the port region
            kb = _ss((t - tp) / (0.6 * (T["MC"] - tp) + 1e-9))
            cw = chain_weights(t - sh, [T["MC"], T["MC"] + T["P"]], [bl[1] * 1.15 * T["dep"] * k, bl[2] * 1.1 * T["dep"] * k])
            w = {kk: vv * (1 - kb) for kk, vv in palm_w(co).items()}
            for k, bn in enumerate(BONE_CHAIN["thumb"]):
                w[bn] = w.get(bn, 0) + cw[k] * kb
        else:
            w = blend_wrist(co, palm_w(co))
        tot = sum(w.values()) + 1e-12
        for kk, vv in w.items():
            if vv > 1e-4:
                W[kk][i] += vv / tot
    ob.vertex_groups.clear()
    for n in names:
        idx = np.nonzero(W[n] > 1e-4)[0]
        if len(idx) == 0:
            continue
        vg = ob.vertex_groups.new(name=n)
        for j in idx:
            vg.add([int(j)], float(W[n][j]), "REPLACE")
    _limit_normalize(ob, 4)
    return ob


def _limit_normalize(ob, limit=4):
    me = ob.data
    for v in me.vertices:
        gs = sorted(((g.group, g.weight) for g in v.groups), key=lambda x: -x[1])
        for gi, _ in gs[limit:]:
            ob.vertex_groups[gi].remove([v.index])
    for v in me.vertices:
        s = sum(g.weight for g in v.groups)
        if s > 0:
            for g in v.groups:
                g.weight /= s


def add_modifiers(ob, rig, preserve_volume=True, corrective=False, subsurf=2):
    for m in list(ob.modifiers):
        ob.modifiers.remove(m)
    ob.parent = rig
    am = ob.modifiers.new("Armature", "ARMATURE")
    am.object = rig
    am.use_deform_preserve_volume = preserve_volume
    if corrective:
        cs = ob.modifiers.new("CorrectiveSmooth", "CORRECTIVE_SMOOTH")
        cs.rest_source = "ORCO"
        cs.smooth_type = "LENGTH_WEIGHTED"
        cs.iterations = 6
        cs.factor = 0.5
        cs.use_only_smooth = False
    if subsurf:
        ss = ob.modifiers.new("Subsurf", "SUBSURF")
        ss.levels = subsurf
        ss.render_levels = subsurf
    return ob


def build_hand(params=None, name="Hand", weights="analytic", **mod_kw):
    hb = HandBuilder(params)
    ob = hb.build(name)
    rig = build_rig(hb, name + "Rig")
    if weights == "analytic":
        analytic_weights(hb, ob, rig)
    else:
        ob.parent = rig
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True); rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        for m in list(ob.modifiers):
            ob.modifiers.remove(m)
        _limit_normalize(ob, 4)
    add_modifiers(ob, rig, **mod_kw)
    return hb, ob, rig


# ---------------------------------------------------------------- pose library
# Degrees. Flexion = +X on every digit bone. Spread = local Z.
# Fingers converge as they flex (all point at the scaphoid): the 'conv' spread
# terms below encode that cascade.
def _f(mcp, pip, dip, spread=0.0):
    return {"01": (mcp, 0, spread), "02": (pip, 0, 0), "03": (dip, 0, 0)}


POSES = {
    "open":      {"index": _f(0, 0, 0, -6), "middle": _f(0, 0, 0, 0), "ring": _f(0, 0, 0, 5),
                  "little": _f(0, 0, 0, 10), "thumb": {"01": (-5, 0, 10), "02": (0, 0, 0), "03": (0, 0, 0)}},
    "relaxed":   {"index": _f(18, 28, 14, 0), "middle": _f(24, 36, 18, 0), "ring": _f(30, 42, 20, -1),
                  "little": _f(36, 48, 22, -3), "thumb": {"01": (12, 0, -8), "02": (10, 0, 0), "03": (12, 0, 0)},
                  "mc": {"ring": 3, "little": 6}},
    "fist":      {"index": _f(85, 100, 65, 3), "middle": _f(88, 102, 66, 0), "ring": _f(90, 102, 64, -4),
                  "little": _f(92, 100, 60, -9), "thumb": {"01": (32, 0, -22), "02": (45, 0, 0), "03": (35, 0, 0)},
                  "mc": {"ring": 6, "little": 14}},
    "point":     {"index": _f(0, 0, 0, 0), "middle": _f(88, 102, 66, 0), "ring": _f(90, 102, 64, -4),
                  "little": _f(92, 100, 60, -9), "thumb": {"01": (28, 0, -18), "02": (40, 0, 0), "03": (30, 0, 0)},
                  "mc": {"ring": 6, "little": 14}},
    "grip":      {"index": _f(55, 70, 40, 2), "middle": _f(58, 72, 42, 0), "ring": _f(60, 72, 40, -3),
                  "little": _f(62, 70, 38, -6), "thumb": {"01": (25, 0, -20), "02": (25, 0, 0), "03": (20, 0, 0)},
                  "mc": {"ring": 4, "little": 9}},
    "high_five": {"index": _f(-8, 0, -4, -10), "middle": _f(-8, 0, -4, 0), "ring": _f(-8, 0, -4, 8),
                  "little": _f(-8, 0, -4, 16), "thumb": {"01": (-10, 0, 20), "02": (-5, 0, 0), "03": (-10, 0, 0)}},
}


def flexion_axes(rig):
    """World-space rest flexion axis per digit bone (the axis +X maps to)."""
    out = {}
    for b in rig.data.bones:
        out[b.name] = (rig.matrix_world.to_3x3() @ b.matrix_local.to_3x3()) @ Vector((1, 0, 0))
    return out


def set_flex(rig, bone, deg, spread=0.0, axes=None):
    """Rotate `bone` by `deg` of flexion. With `axes` given (world flexion axes
    from a reference rig), the rotation is converted into this bone's local
    frame - that is how a rig with wrong roll is driven fairly in a test."""
    pb = rig.pose.bones[bone]
    if axes is None:
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = (math.radians(deg), 0, math.radians(spread))
        return
    pb.rotation_mode = "QUATERNION"
    Rl = (rig.matrix_world.to_3x3() @ pb.bone.matrix_local.to_3x3())
    ax = Rl.inverted() @ axes[bone]
    q = Quaternion(ax.normalized(), math.radians(deg))
    pb.rotation_quaternion = q


def apply_pose(rig, pose, axes=None):
    reset(rig)
    for d, segs in pose.items():
        if d == "mc":
            for dd, deg in segs.items():
                set_flex(rig, f"mc_{dd}", deg, axes=axes)
            continue
        for seg, (fx, _, sp) in segs.items():
            set_flex(rig, f"{d}.{seg}", fx, sp, axes=axes)
    bpy.context.view_layer.update()


def reset(rig):
    for pb in rig.pose.bones:
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
    bpy.context.view_layer.update()


# ---------------------------------------------------------------- measurement
def measure_hand(hb, ob, evaluated=True):
    """Anthropometric measurements taken from the SKIN (evaluated mesh), the
    way a tape measure would: hand length, knuckle breadth, per-finger length
    from the MCP joint to the tip, finger breadth at the PIP joint."""
    import formq as Q
    A = Q.arrays(ob, evaluated)
    V = A["V"]
    out = {"hand_length_mm": float(V[:, 0].max() * 1000)}
    T = hb.T
    relT = V - np.array(T["cmc"])
    tT = np.clip(relT @ np.array(T["a"]), 0, T["L"])
    dT = np.linalg.norm(relT - np.outer(tT, np.array(T["a"])), axis=1)
    thumbish = (dT < 0.75 * T["w"] * hb.p["thenar"]) & (tT > T.get("tp", 0.0))
    slab = (V[:, 0] > 0.070 * hb.p["scale"]) & (V[:, 0] < 0.090 * hb.p["scale"]) & ~thumbish
    out["breadth_mm"] = float((V[slab, 1].max() - V[slab, 1].min()) * 1000)
    axes, dist = [], []
    for d in DIGITS:
        g = hb.D[d]
        o = np.array(hb.digit[d]["origin"])
        a = np.array(g["a"])
        rel = V - o
        t = np.clip(rel @ a, 0, g["L"])
        dist.append(np.linalg.norm(rel - np.outer(t, a), axis=1))
        axes.append((d, o, a, np.array(g["l"]), g))
    owner = np.argmin(np.array(dist), axis=0)
    for i, (d, o, a, l, g) in enumerate(axes):
        rel = V - o
        t = rel @ a
        own = (owner == i) & (dist[i] < 0.8 * g["w"]) & (t > -0.005)
        out[f"{d}_len_mm"] = float(t[own].max() * 1000)
        sl = own & (np.abs(t - g["P"]) < 0.0010)
        lat = rel[sl] @ l
        out[f"{d}_width_mm"] = float((lat.max() - lat.min()) * 1000) if sl.any() else None
    return out


def canon_targets():
    """Fixed tape-measure targets for the default adult hand (mm). Hand length
    and breadth are typical adult-male anthropometric means (ANSUR-style,
    recalled); finger targets follow CANON. Independent of any parameters."""
    t = {"hand_length_mm": 190.0, "breadth_mm": 86.0}
    for d in DIGITS:
        L = CANON["prox"][d] + CANON["mid"][d] + CANON["dist"][d]
        t[f"{d}_len_mm"] = L + 0.10 * 0.86 * CANON["width"][d] + 1.0
        t[f"{d}_width_mm"] = CANON["width"][d] * 0.96
    return t


def proportion_error(meas, target=None):
    target = target or canon_targets()
    errs = {k: abs(meas[k] - v) / v for k, v in target.items() if meas.get(k)}
    return float(np.mean(list(errs.values()))), errs
