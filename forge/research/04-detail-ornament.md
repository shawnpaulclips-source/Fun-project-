# 04 — Detail and ornament for forge-kit

Scope: make the kit produce museum-grade decorative work (engraving, guilloché,
filigree, mouldings, precious-metal finishes) from code, headless, no GPU.
Target code: `forge/kit/forgekit/` — new module `ornament.py`, additions to
`materials.py`. Nothing here replaces `mesh.py`; it sits beside it.

## Verdict

1. Procedural ornament is viable headless. Exact epitrochoid guilloché, arc-length
   placement and BVH surface projection all run in bpy 5.3.0 in this container.
2. Surface-following ornament is best done as Python math plus `mathutils.bvhtree`
   projection, then `bmesh` or Curve-bevel geometry. Not as a shrinkwrap modifier.
3. Closed loops of ornaments need holonomy correction. Plain parallel transport
   does NOT close on a spatial loop. Measured 156 deg of seam twist on a non-planar
   loop; 0.03 deg on a planar one. This is the single most likely silent bug.
4. Custom bevel/moulding profiles work from Python via `BevelModifier.profile_type
   = 'CUSTOM'` and `custom_profile.points.add(x, y)`. The two endpoints cannot be
   removed; only interior points can.
5. Text engraving: FONT curve to MESH via `bpy.ops.object.convert` works headless
   (FORGE at 0.002 extrude = 1256 verts, 3 ms). Use the returned object, not a
   stale reference.
6. Principled BSDF in 5.3 has the inputs needed for real metal: Anisotropic,
   Anisotropic Rotation, Coat (clearcoat), and Thin Film Thickness / Thin Film IOR.
   The kit uses none of the last three yet.
7. Ambient Occlusion (inside), Bevel and Pointiness nodes are present. Cavity masks
   without UVs are available for patina and wear.
8. Build in bmesh and Curve-bevel, not Geometry Nodes, for anything the agent must
   control and export. GN curve-to-mesh and sample-nearest-surface exist in 5.3 but
   were NOT run here; treat them as unverified.
9. Metal reflectance values in section 6 are recalled from the standard real-time
   F0 table (Lagarde/de Rousiers, Hoffman). They are NOT re-derived from
   Johnson and Christy here. Check before quoting as measured.
10. The biggest gap is not ornament maths. It is that no current code path writes
    a rim-scale ornament band with exact spacing AND a seam that closes AND a
    per-piece normal frame. Section 3 and `ornament.py` below fix exactly that.

---

## 1. Engraving and surface ornament on arbitrary geometry

Options, ranked for an agent with no eyes:

| Method | Works headless | Verdict |
|---|---|---|
| **Python arc-length curve + BVH projection** (`mathutils.bvhtree.BVHTree.find_nearest`) | Yes. 2000/2000 hits on a UV sphere, 0.0014 ms per query | **Use this.** Exact, debuggable, no modifier stack |
| **Shrinkwrap modifier (nearest surface point)** | Yes, but it bends a whole mesh, not a curve's spacing. Degrades on thin features and stretches the arc length | Use only to wrap a finished flat mesh onto a target. Never as the placement method |
| **UV-space projection** (flat pattern in UV, unwrap, displace) | Needs an unwrap. No xatlas step is wired in (per README) | Fine for a flat panel. Breaks on a closed rim with seams |
| **Geodesic walking** (step along surface by sampled tangent) | Yes, in Python with BVH. Expensive for dense sampling | Use for curves that must stay on a surface across a large curvature change |
| **Conformal / LSCM mapping** | Blender's UV unwrap uses it, but no direct headless API for an arbitrary parametrization | Not worth it; use BVH |
| **Curve-to-Mesh** (GN) and **Sample Nearest Surface** (GN) | Node types exist in 5.3 (`GeometryNodeCurveToMesh`, `GeometryNodeSampleNearestSurface`). **Not executed here** | Unverified. Good for a live scene; less controllable from Python |
| **Bevel on a Curve object** (`curve.bevel_depth`, `bevel_resolution`) | Standard. Convert to mesh for export | **Use this for all engraved lines** (guilloché, filigree, borders) |

Trap: a curve placed by sampling a parameter (not arc length) bunches up at
high-curvature points. Always resample by arc length first (section 3).

Trap: the BVH hits the mesh surface, but the normal from `find_nearest` is the
face normal. Smooth surfaces need the interpolated vertex normal for a clean
ornament orientation. Use the face normal on faceted geometry only.

## 2. The ornament vocabulary: physical form and recipe

**Guilloché (engine turning).** Physically: a pattern cut by a rose-engine lathe,
where a stylus is driven by a cam or linked gears, producing an interference
pattern of overlapping closed curves. The mathematical form is an epitrochoid
(or hypotrochoid). Exact recipe:

    k = (R + r) / r
    x(t) = (R + r) cos t  -  d cos(k t)
    y(t) = (R + r) sin t  -  d sin(k t)
    period T = 2 pi r / gcd(R, r)     (R, r integers)

Hypotrochoid variant (more common on watch dials):

    k = (R - r) / r
    x(t) = (R - r) cos t  +  d cos(k t)
    y(t) = (R - r) sin t  -  d sin(k t)

`d` is the pen offset (`d = r` gives an epicycloid; `d = R + r` gives a rose).
Verified: with R=96, r=40, d=30 the curve closes at T to 1.7e-13 (see section 8).

Stacking: a real guilloché field is N copies of one curve, each rotated by 2 pi j / N
about the centre, cut as a continuous groove. Use integer R/r so the curve closes
on itself. Use irrational values and it never closes; the groove then cuts forever.

**Damascene (koftgari).** Physically: inlay or surface etching into steel with a
gold or silver wire pressed into a cut groove, usually a scroll or arabesque.
Recipe: an inset groove along a curve, with a bevel radius at the bottom. Model
the groove as a Curve bevel (inset), the wire as a second, slightly raised
Curve bevel. Metal colour from the wire material, not the base.

**Filigree.** Physically: fine twisted or plain wire soldered into scrolls on a
base. Recipe: a curve (a spiral, S or C scroll) with bevel_depth = wire radius,
then a second bevel ring at each endpoint for the bead. Sweep along the scroll
path with `bevel_depth` set to the wire thickness. Twist is a sinusoid on the
radius: `r(t) = r0 (1 + a sin(2 pi n t))` along the path.

**Repoussé.** Physically: hammered from the back so the design stands up in relief,
then chased from the front. Recipe: a height field `h(u,v)` displacing a dense
grid, with the relief profile an ogee or cyma from section 7. Store the height
field as a grayscale and use displacement. Do not model it as a stack of boxes.

**Chasing.** Physically: fine tool-marks pressed into the surface. Recipe: a
dense short-stroke noise on a short stroke-length scale, oriented along the
local tangent. A line of tiny ellipsoids or a stroked curve with a random jitter.

**Niello.** Physically: a black sulphur-silver-copper alloy filling an engraved
line. Recipe: the engraved groove is a separate material slot. Use a dark
dielectric-metal (roughness 0.4, metallic 1) and an inset Curve bevel for the
recess. No geometry beyond the groove.

**Acanthus scrollwork.** Physically: a stylised leaf with lobes and curling
tendrils. Recipe: a leaf is a lathed or swept profile along a spine curve with
lobe offsets. Use `loft_bracket`-style swept sections along a cubic Bezier spine,
with leaf width following `w(t) = w0 sin(pi t)^p`. Tendrils are arcs of a
logarithmic spiral: `r(theta) = a exp(b theta)`.

**Greek key (meander).** Physically: a square-wave square-turned spiral repeated.
Recipe: a path on a grid of unit steps: `(right, up, left, down...)` with a
turn at each step. Generate the path as a list of points with 90-degree corners,
then fillet the corners with a radius r. Closed loops tile by unit cell, so the
seam closes if the cell count times the cell size equals the loop length.

**Rope and bead moulding.** Physically: a twisted cord or a row of beads.
Recipe: a helix `(R cos t, R sin t, p t / (2 pi))` swept with a circular section
of radius `s` and pitch `p`. A bead row is a set of spheres at equal arc-length
spacing (section 3).

**Knurling.** Physically: diamond or straight grip ridges. Recipe: two families
of straight lines at angles +a and -a across a cylinder, cut as small raised
prisms. The count per circumference is an integer `N`, so the pattern repeats
exactly. Use `N` from `ceil(circumference / pitch)` rounded to the nearest
integer, then recompute pitch as `circumference / N` so the seam closes.

**Engine turning (straight-line).** Physically: a cut of parallel lines,
varying with a cam. Recipe: a stack of straight lines with spacing `s(x)`
varying along the axis. Use a cubic spline for `s(x)`, not a linear one.

**Cartouche.** Physically: a framed panel with an oval or scrolled border.
Recipe: an outer closed Bezier loop for the frame, an inner offset loop by
`-w` (use `bpy.types.Curve` offset via Bezier control points, or compute the
inward offset of a sampled polyline). Fill the panel with the panel material,
the border with the frame material.

## 3. Perfect alignment (the high-value part)

This section is the one that stops "close but not quite" from happening.

### 3.1 Arc-length resampling of a closed curve

Let P_0 .. P_{M-1} be a dense sample (M ~ 4000) of a closed curve, and let
P_M = P_0. Chord lengths `s_i = |P_{i+1} - P_i|`, cumulative `S_j = sum_{i<j} s_i`,
total `L = S_M`. For N ornaments, sample at `u_k = k L / N`, k = 0..N-1, and
linearly interpolate within the segment where `S_j <= u_k < S_{j+1}`.

Note the N, not N+1: a closed loop of N ornaments does not repeat the seam point.
Measured on the guilloché above: 180 points around the curve, minimum/maximum
gap ratio 0.996, seam-to-mean gap 0.997. The residual error is sampling error
from M = 4000 and falls with M. Set M such that each chord is below 1/10 of the
ornament pitch.

### 3.2 Tangent, normal, binormal frames

For each sample: `T_i` = normalised `(P_{i+1} - P_{i-1})`. The frame is an
orthonormal triple `(T, N, B)` with `B = T x N`. A normal to the surface comes from
the BVH hit, so for an ornament on a surface, use the surface normal `n` as the
primary axis: `B = normalise(T x n)`, `N = B x T`.

### 3.3 Parallel transport and holonomy

Parallel transport (double-reflection method, Wang et al. 2008) keeps the normal
from flipping, but a spatial closed loop does not return to its start. Measured
here:

- planar loop, transport around once: seam twist **-0.03 deg** (closes)
- spatial (non-planar) loop: seam twist **156.13 deg** (does not close)

The 156-degree value is the holonomy of the loop, not a bug. Fix it by spreading
the holonomy as a rotation about T:

    theta(s) = holonomy * (s / L)            # linear in arc length
    N_corr(s) = cos(theta) N_pt - sin(theta) B_pt  (rotate about T)

Apply this to every frame before placement. After correction, the seam angle
between frame 0 and frame N-1 is zero by construction.

### 3.4 Orienting each piece

Each ornament has a local frame `Matrix` built from `(T, N, B)`:

    M = Matrix((T, N, B)).transposed().to_4x4()   # columns = axes
    M.translation = P + n * offset

Place a piece at `M` and rotate it by its own local axis so its long dimension
runs along T. Offset along `n` by half the piece depth so the piece sits on the
surface, not in it.

### 3.5 Seam closure error (the test)

    seam_err = |P_{N-1} + (P_0 - P_{N-1})*(gap/gap_mean)| - gap_mean

Report `max(gap)/min(gap) - 1` and the seam gap relative to the mean. A pass is
under 0.5 percent. `ornament.py` returns both numbers.

### 3.6 Even spacing on a surface, not in parameter space

Arc length along the projected curve, not along the 2D parameter. On the sphere
test, `P(t) = (0.9 cos t, 0.9 sin t, 0.3)` projected to the sphere: the projected
points are not evenly spaced on the surface even when the input is. Resample the
projected curve, not the input.

## 4. Text and symbol engraving

Font to curve to mesh works headless: `bpy.data.curves.new(type="FONT")`, set
`body`, `size`, `extrude`, then `bpy.ops.object.convert(target='MESH')` on a
selected active object. Verified: "FORGE" at size 0.1 and extrude 0.002 gave a
1256-vertex mesh in 3 ms.

Traps:
- After `convert`, use `bpy.context.view_layer.objects.active`, not the reference
  you had. The reference still points at the text object.
- Use the default Blender font or a path to a `.ttf`/`.otf`. A missing font falls
  back silently to Bfont.

Placing glyphs on a curved surface: place each glyph's origin on the projected
baseline (section 3), orient its local Z along `n`, its local X along `T`. Inset
(engraved) by a boolean difference of the glyph mesh; or raised by a union. Use
an inset depth of 0.4 to 0.8 mm at metre scale and a draft angle of 5 to 10
degrees on the side walls so the cut reads as a tool cut, not a stamped slot.
Legibility at render scale: keep the stroke width above 0.5 mm at 1 m object
scale, and the glyph height above 3 mm. Below that the bevel swallows the glyph.

Boolean is slow and fragile on dense meshes. Checkpoint first (per the README),
and prefer a Curve-bevel inset for lines; a boolean only for solid glyphs.

## 5. Micro-detail

**Displacement vs normal vs bump, when each is right:**

- **Displacement** changes silhouette and self-shadowing. Use for repoussé, relief
  lettering, anything that stands out. Costs geometry.
- **Normal map** (or bump on Principled) shows fine surface detail with no
  silhouette change. Use for brushing, chasing, grain. Free at render.
- **Bump** is a cheap normal from a height field. Good for sub-millimetre grain
  on a metal. Strength above ~0.4 reads as corrosion (per `materials.py` docstring).

**Multiresolution from code.** Build the base mesh, add a Subdivision Surface
modifier, then bake a displacement to a 2048 texture in Cycles. Verified only in
the README: a 2048 normal bake is 1.90 s CPU. Multires is not exercised here.

**Noise taxonomy:**

| Noise | Physical analogue | Use |
|---|---|---|
| Voronoi (cell) | Grain boundaries, cracks, hammer marks | Cast metal, crazing, stone |
| Musgrave / fBm | Erosion, patina, dirt, broad variation | Patina, roughness breakup |
| Wave | Brushing, combing, wood grain | Brushed metal (stretch it) |
| Gabor | Oriented texture with a frequency band | Engine-turned or hammered look |
| Simple (Perlin) | Smooth low-frequency variation | Colour drift, fingerprint |

Brushed metal recipe: Wave or Noise with Mapping scale ratio 1 : 40 to 1 : 60
along the brush direction, plus the Principled Anisotropic input with
Anisotropic Rotation aligned to the brush. This is the only reliable way to get
the streak.

**Masks without UVs.** Use these in Shader nodes:
- **Pointiness** (Geometry node): edge highlight. Already used by `wear_edges`.
- **Ambient Occlusion** node with `Inside` enabled: cavity mask (bright in
  crevices). Verified present in 5.3. Cycles only at render; EEVEE does not
  evaluate it the same way.
- **Bevel** node: edge proximity for wear on sharp edges.
- **Curvature** is not a node in 5.3 as a built-in; use Pointiness with a
  ColorRamp as the curvature proxy, or compute cavity from AO.

Drive patina from cavity: ColorRamp on the inverse AO, mix a patina colour in
where the cavity is high; drive roughness down where the surface is worn.

## 6. Precious metals

Values are linear-sRGB normal-incidence reflectance F0, the number that goes into
`Base Color` for a metal. They are recalled from the standard real-time table
(Lagarde and de Rousiers, "Moving Frostbite to PBR", and Hoffman's table). They
were NOT re-derived from the underlying Johnson and Christy data in this session.
Check against the paper before quoting them as measured.

| Metal | Linear F0 (R, G, B) | Status |
|---|---|---|
| Gold | (1.000, 0.766, 0.336) | recalled, matches kit `PALETTE["gold"]` |
| Silver | (0.972, 0.960, 0.915) | recalled; kit has (0.962, 0.949, 0.922) |
| Copper | (0.955, 0.638, 0.538) | recalled; kit has (0.926, 0.721, 0.504). Recalled value is redder |
| Aluminium | (0.913, 0.922, 0.924) | recalled, matches kit |
| Platinum | (0.672, 0.637, 0.585) | recalled |
| Titanium | (0.542, 0.497, 0.449) | recalled |
| Chromium | (0.550, 0.556, 0.554) | recalled, matches kit `chrome` |
| Brass | (0.681, 0.451, 0.157) | kit value; not in the standard table; unverified |
| Bronze | (0.55, 0.36, 0.17) | estimate, not measured |
| Rose gold | (0.92, 0.60, 0.50) | estimate, gold/copper blend; not measured |

Corrections to the kit: the copper value in `materials.py` is visibly less red than
the recalled table. Pick one source and document it. Do not mix.

Finish parameters (Principled BSDF, Blender 5.3 input names):

- Brushed: `Roughness` 0.22 to 0.35, `Anisotropic` 0.6 to 0.85, `Anisotropic
  Rotation` aligned to brush, Wave noise stretched as in section 5.
- Polished: `Roughness` 0.05 to 0.12, no anisotropy, `Coat Weight` 0 or 1 with
  `Coat Roughness` 0.05 for lacquered gold.
- Thin-film iridescence (tarnish, anodised, heat-tint): `Thin Film Thickness`
  300 to 800 nm, `Thin Film IOR` 1.45 to 1.8. Verified input present.
- Patina: cavity mask (section 5) into a verdigris colour (estimate
  (0.25, 0.55, 0.45)) on copper or brass, roughness up to 0.6 in the cavity.
- Fingerprint and polish variation: low-amplitude fBm on roughness, amplitude
  0.02 to 0.05, not larger, or it reads as dirt.

## 7. Mouldings and profiles

Verified: `BevelModifier.profile_type = 'CUSTOM'` then `custom_profile.points`.
Remove interior points with `points.remove(p)`; endpoints cannot be removed
(RuntimeError "Unable to remove path point"). Add with `points.add(x, y)`.
Verified: 9 points set, evaluated to 488 verts on a cube.

Tables below are normalised 0..1 in both axes, nine points each, computed from the
formulas shown. Use them as `lathe` profiles after scaling to the target radius and
height. For `mesh.lathe(name, profile)`, the profile is `(radius, z)` so swap the
pair if you have `(x, y)` from these tables with y as the height.

**Ogee / cyma recta** (cubic Bezier (0,0),(0.5,0),(0.5,1),(1,1)):

    (0.0000,0.0000) (0.1660,0.0430) (0.2969,0.1562) (0.4043,0.3164)
    (0.5000,0.5000) (0.5957,0.6836) (0.7031,0.8438) (0.8340,0.9570) (1.0000,1.0000)

**Cyma reversa** (transpose of ogee):

    (0.0000,0.0000) (0.0430,0.1660) (0.1562,0.2969) (0.3164,0.4043)
    (0.5000,0.5000) (0.6836,0.5957) (0.8438,0.7031) (0.9570,0.8340) (1.0000,1.0000)

**Ovolo / torus (convex quarter-round)**, x = sin t, y = 1 - cos t, t in [0, pi/2]:

    (0.0000,0.0000) (0.1951,0.0192) (0.3827,0.0761) (0.5556,0.1685) (0.7071,0.2929)
    (0.8315,0.4444) (0.9239,0.6173) (0.9808,0.8049) (1.0000,1.0000)

**Scotia / cavetto (concave quarter-round)**, x = 1 - cos t, y = sin t:

    (0.0000,0.0000) (0.0192,0.1951) (0.0761,0.3827) (0.1685,0.5556) (0.2929,0.7071)
    (0.4444,0.8315) (0.6173,0.9239) (0.8049,0.9808) (1.0000,1.0000)

Fillet and cove, for ornament: a bead is the torus table at a small radius; a
cavetto is the scotia table; an ogee is the cyma table. Use the same table at any
scale, the profile is scale-free.

Code the profiles as data in `ornament.py` (see section 9), not as literals in
calling code.

## 8. Parametric item families

A family is a seeded grammar, not one model. Pattern:

- A `Family` holds ranges and weighted choices for each design parameter.
- A seed deterministically draws one value per parameter (`random.Random(seed)`).
- The grammar is a fixed ordered list of rules: base shape, then mouldings, then
  ornament bands, then inscriptions, each consuming parameters.
- Same seed gives the same object, which is what makes it checkpointable.

Example, a coin: diameter 22 to 40 mm, rim width 1.5 to 3 mm, ornament band
count 1 to 3, guilloché R/r from a list of integer pairs, inscription from a
set of Latin and font choices, relief depth 0.1 to 0.4 mm. A seed yields a unique,
individually-detailed coin.

Sword and chalice: the same pattern with a blade or bowl profile as the base,
moulding bands on the guard or foot, and a filigree ornament on the grip or stem.

Keep the grammar small. Every rule must be checked by `inspect` (manifold, bounds)
before it is accepted, or the family produces broken geometry at the edges of its
ranges.

## 9. bmesh vs Geometry Nodes

| | bmesh + Python | Geometry Nodes |
|---|---|---|
| Control from Python | Direct, numeric, debuggable | Node graph built in Python; more work to set up, harder to inspect |
| Speed for hundreds of pieces | Fine with `bmesh.ops` and `bpy.data.meshes` | Fast when instanced; no measurement here |
| Export survival (glTF/FBX) | Good: plain mesh | Needs apply first; instances do not always survive; not tested here |
| Verified in this session | Yes (sections 1, 3, 7) | No. Node types present; no run |

Recommendation: **bmesh for placement and any export; Curve-bevel for lines;
Geometry Nodes only for live scene instancing**, and apply before export.

## 10. Proposed API

Two files. `ornament.py` is new. `materials.py` gets additions. All functions are
typed and return what they say.

### 10.1 `forgekit/ornament.py`

```python
"""Ornament generation for forge-kit.

Design rules:
- Pure math for placement. Blender only for building geometry.
- Every closed loop closes exactly: N pieces, N gaps, seam error reported.
- Frames use parallel transport with holonomy correction, so pieces never twist.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from math import gcd
from typing import Literal, Sequence

import bpy
import bmesh
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


Point2 = tuple[float, float]
Point3 = tuple[float, float, float]


@dataclass(frozen=True)
class SeamReport:
    """Quality of a closed placement.

    Attributes:
        pieces: Number of pieces placed around the loop.
        gap_min: Smallest arc-length gap between adjacent pieces.
        gap_max: Largest arc-length gap.
        gap_ratio: gap_min / gap_max. 1.0 is perfectly even.
        seam_gap_error: |gap(seam) - mean gap| / mean gap, where the seam gap
            is the distance from the last piece to the first.
        holonomy_deg: Frame twist accumulated around the loop before correction.
            Non-zero means the curve is spatial, not planar.
    """
    pieces: int
    gap_min: float
    gap_max: float
    gap_ratio: float
    seam_gap_error: float
    holonomy_deg: float


@dataclass(frozen=True)
class Frame:
    """Orthonormal frame at one point on a curve.

    Attributes:
        point: Position.
        tangent: Unit vector along the curve (T).
        normal: Unit vector perpendicular to T, the surface-facing axis.
        binormal: T x normal (B).
    """
    point: Vector
    tangent: Vector
    normal: Vector
    binormal: Vector


GUILLOCHE_VARIANTS = ("epitrochoid", "hypotrochoid")


def guilloche_points(R: int, r: int, d: float, samples: int = 2048,
                     variant: Literal["epitrochoid", "hypotrochoid"] = "epitrochoid",
                     ) -> list[Point2]:
    """Sample one closed guilloché curve in the plane.

    Parameters:
        R: Fixed-circle radius, integer. Must be > r for hypotrochoid.
        r: Rolling-circle radius, integer, r < R for hypotrochoid.
        d: Pen offset from the rolling-circle centre. d = r gives an
            epicycloid; d = R + r a rose for epitrochoid.
        samples: Number of points over one full period T.
        variant: "epitrochoid" (rolls outside) or "hypotrochoid" (rolls inside).

    Returns:
        List of `samples` (x, y) points covering exactly one period. The curve
        closes: the first point equals the point at T. Do not append the first
        point again.

    Raises:
        ValueError: if R or r is not a positive integer, or hypotrochoid has r >= R.
    """
    if not (isinstance(R, int) and isinstance(r, int) and R > 0 and r > 0):
        raise ValueError("R and r must be positive integers")
    if variant == "hypotrochoid" and r >= R:
        raise ValueError("hypotrochoid needs r < R")
    T = guilloche_period(R, r)
    pts: list[Point2] = []
    if variant == "epitrochoid":
        k = (R + r) / r
        for i in range(samples):
            th = T * i / samples
            pts.append(((R + r) * math.cos(th) - d * math.cos(k * th),
                        (R + r) * math.sin(th) - d * math.sin(k * th)))
    else:
        k = (R - r) / r
        for i in range(samples):
            th = T * i / samples
            pts.append(((R - r) * math.cos(th) + d * math.cos(k * th),
                        (R - r) * math.sin(th) - d * math.sin(k * th)))
    return pts


def guilloche_period(R: int, r: int) -> float:
    """Parameter period T = 2 pi r / gcd(R, r) of a guilloché curve.

    Parameters:
        R: Fixed-circle radius, integer.
        r: Rolling-circle radius, integer.

    Returns:
        T in radians. The curve closes after one period. Measured to 1.7e-13
        error for R=96, r=40.
    """
    return 2.0 * math.pi * r / gcd(R, r)


def resample_closed_2d(points: Sequence[Point2], n: int) -> tuple[list[Point2], float]:
    """Resample a closed polyline to n points evenly spaced by arc length.

    Parameters:
        points: Dense closed polyline, first and last NOT duplicated.
        n: Number of output points. Output does not repeat the seam point.

    Returns:
        (resampled points, total arc length L).
    """
    Q = list(points) + [points[0]]
    seg = [math.dist(Q[i], Q[i + 1]) for i in range(len(Q) - 1)]
    cum = [0.0]
    for s in seg:
        cum.append(cum[-1] + s)
    L = cum[-1]
    out: list[Point2] = []
    j = 0
    for k in range(n):
        s = L * k / n
        while cum[j + 1] < s:
            j += 1
        t = (s - cum[j]) / (cum[j + 1] - cum[j]) if cum[j + 1] > cum[j] else 0.0
        out.append((Q[j][0] * (1 - t) + Q[j + 1][0] * t,
                    Q[j][1] * (1 - t) + Q[j + 1][1] * t))
    return out, L


def resample_closed_3d(points: Sequence[Vector], n: int) -> tuple[list[Vector], float]:
    """3D version of resample_closed_2d.

    Parameters:
        points: Dense closed 3D polyline, not duplicated at the seam.
        n: Number of output points.

    Returns:
        (resampled points as Vectors, total arc length L).
    """
    Q = list(points) + [points[0]]
    seg = [(Q[i + 1] - Q[i]).length for i in range(len(Q) - 1)]
    cum = [0.0]
    for s in seg:
        cum.append(cum[-1] + s)
    L = cum[-1]
    out: list[Vector] = []
    j = 0
    for k in range(n):
        s = L * k / n
        while cum[j + 1] < s:
            j += 1
        t = (s - cum[j]) / (cum[j + 1] - cum[j]) if cum[j + 1] > cum[j] else 0.0
        out.append(Q[j].lerp(Q[j + 1], t))
    return out, L


def frames_closed(points: Sequence[Vector], up: Vector | None = None
                  ) -> tuple[list[Frame], float]:
    """Parallel-transport frames around a closed 3D loop, holonomy corrected.

    Double-reflection transport (Wang et al. 2008), then the accumulated seam
    rotation is spread linearly in arc length about the tangent, so frame 0 and
    frame N-1 agree at the seam.

    Parameters:
        points: Closed 3D loop, not duplicated at the seam. Use the output of
            resample_closed_3d for even spacing.
        up: Optional reference vector to seed the first normal. Default is the
            projection of world Z onto the normal plane, or world X if the
            tangent is vertical.

    Returns:
        (frames, holonomy_deg). frames[i] is the Frame at points[i]. holonomy_deg
        is the seam twist before correction; 0 for a planar loop, non-zero for
        a spatial loop (measured 156 deg on a helical loop).
    """
    n = len(points)
    T = [(points[(i + 1) % n] - points[i - 1]).normalized() for i in range(n)]
    ref = up or Vector((0.0, 0.0, 1.0))
    N0 = ref - T[0] * ref.dot(T[0])
    if N0.length < 1e-6:
        N0 = Vector((1.0, 0.0, 0.0)) - T[0] * T[0].x
    N0.normalize()
    N = [N0]
    for i in range(n - 1):
        v1 = points[i + 1] - points[i]
        c1 = v1.dot(v1)
        rL = N[i] - (2.0 / c1) * v1.dot(N[i]) * v1
        tL = T[i] - (2.0 / c1) * v1.dot(T[i]) * v1
        v2 = T[i + 1] - tL
        c2 = v2.dot(v2)
        rF = rL - (2.0 / c2) * v2.dot(rL) * v2 if c2 > 1e-12 else rL
        N.append(rF.normalized())
    last = N[-1] - T[0] * N[-1].dot(T[0])
    last.normalize()
    hol = math.degrees(math.atan2(T[0].dot(N[0].cross(last)), N[0].dot(last)))
    frames: list[Frame] = []
    for i in range(n):
        th = -math.radians(hol) * (i / n)
        c, s = math.cos(th), math.sin(th)
        Ni = N[i] * c + T[i].cross(N[i]) * s
        Ni = (Ni - T[i] * Ni.dot(T[i])).normalized()
        frames.append(Frame(points[i].copy(), T[i], Ni, T[i].cross(Ni).normalized()))
    return frames, hol


def project_to_surface(points: Sequence[Vector], target: bpy.types.Object,
                       offset: float = 0.0,
                       ) -> list[tuple[Vector, Vector]]:
    """Project points onto the nearest surface of a mesh object.

    Uses mathutils BVHTree on the evaluated mesh, so modifiers are respected.

    Parameters:
        points: 3D points to project. Each is moved to the closest surface point.
        target: Mesh object to project onto. Must be a MESH with polygons.
        offset: Distance to push each result along the surface normal (use
            half the ornament depth to seat a piece on the surface).

    Returns:
        List of (surface_point + normal * offset, face_normal) per input point.
        Points with no hit are returned unchanged with a zero normal, so check
        the length of the normal before using it.

    Raises:
        ValueError: if target is not a mesh.
    """
    if target.type != 'MESH':
        raise ValueError("target must be a mesh object")
    dg = bpy.context.evaluated_depsgraph_get()
    ev = target.evaluated_get(dg)
    bm = bmesh.new()
    bm.from_mesh(ev.to_mesh())
    bm.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(bm)
    out: list[tuple[Vector, Vector]] = []
    for p in points:
        loc, nrm, _idx, _d = tree.find_nearest(p)
        if loc is None:
            out.append((p.copy(), Vector((0.0, 0.0, 0.0))))
        else:
            out.append((loc + nrm * offset, nrm))
    bm.free()
    return out


def placement_matrices(frames: Sequence[Frame], surface: Sequence[tuple[Vector, Vector]],
                       ) -> list[Matrix]:
    """Build a 4x4 placement for each ornament from a frame and surface normal.

    Uses the surface normal as the primary axis so pieces seat on the surface
    even where the curve's own normal differs.

    Parameters:
        frames: Frames from frames_closed, one per piece.
        surface: (point, normal) pairs from project_to_surface, same length.

    Returns:
        One Matrix per piece. Local +X is along the tangent, local +Y along the
        binormal, local +Z along the surface normal.

    Raises:
        ValueError: if the lengths differ.
    """
    if len(frames) != len(surface):
        raise ValueError("frames and surface must match in length")
    mats: list[Matrix] = []
    for f, (p, n) in zip(frames, surface):
        nn = n.normalized() if n.length > 1e-9 else f.normal
        T = (f.tangent - nn * f.tangent.dot(nn)).normalized()
        B = nn.cross(T).normalized()
        mats.append(Matrix(((T.x, B.x, nn.x, p.x),
                            (T.y, B.y, nn.y, p.y),
                            (T.z, B.z, nn.z, p.z),
                            (0.0, 0.0, 0.0, 1.0))))
    return mats


def seam_report(points: Sequence[Vector], holonomy_deg: float) -> SeamReport:
    """Measure the evenness and closure of a placement.

    Parameters:
        points: The placed piece centres, in order, closed.
        holonomy_deg: Value returned by frames_closed.

    Returns:
        SeamReport with gap statistics and the seam-gap error.
    """
    n = len(points)
    gaps = [(points[(i + 1) % n] - points[i]).length for i in range(n)]
    mean = sum(gaps) / n
    seam = gaps[-1]
    return SeamReport(n, min(gaps), max(gaps), min(gaps) / max(gaps),
                      abs(seam - mean) / mean, holonomy_deg)


def ring_placement(count: int, radius: float, target: bpy.types.Object | None = None,
                   offset: float = 0.0,
                   ) -> tuple[list[Matrix], SeamReport]:
    """Place count pieces evenly around a ring, optionally projected to a surface.

    Parameters:
        count: Number of pieces. Must be >= 3.
        radius: Ring radius in metres, in the XY plane.
        target: Optional mesh to project onto. If None, the ring is flat.
        offset: Push each piece along the surface normal (projected only).

    Returns:
        (placement matrices, SeamReport). For a flat ring the seam error is 0.
    """
    if count < 3:
        raise ValueError("count must be >= 3")
    pts = [Vector((radius * math.cos(math.tau * i / count),
                   radius * math.sin(math.tau * i / count), 0.0))
           for i in range(count)]
    if target is not None:
        surf = project_to_surface(pts, target, offset)
        pts = [p for p, _ in surf]
    else:
        surf = [(p, Vector((0.0, 0.0, 1.0))) for p in pts]
    frames, hol = frames_closed(pts)
    return placement_matrices(frames, surf), seam_report(pts, hol)


def knurl_count(circumference: float, pitch: float) -> int:
    """Integer pieces around a circumference so the pattern closes exactly.

    Parameters:
        circumference: Circumference of the knurled surface, metres.
        pitch: Desired pitch, metres.

    Returns:
        round(circumference / pitch), at least 3. Use it as the count, then set
        the actual pitch to circumference / count.
    """
    return max(3, round(circumference / pitch))


def moulding_mesh(name: str, profile: Sequence[Point2], radius: float,
                  height: float, steps: int = 96, smooth: bool = True) -> bpy.types.Object:
    """Lathe a classical moulding profile into a solid of revolution.

    Parameters:
        name: Object name.
        profile: Normalised (x, y) points in [0, 1], e.g. MOULDINGS["ogee"].
            x scales to radius, y scales to height. The profile is closed to
            the axis automatically.
        radius: Outer radius at x = 1, metres.
        height: Height at y = 1, metres.
        steps: Revolution steps.
        smooth: Smooth shading on the result.

    Returns:
        The new mesh object, linked to the scene collection.
    """
    pts = [(p[0] * radius, p[1] * height) for p in profile]
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    vs = [bm.verts.new((x, 0.0, z)) for x, z in pts]
    es = [bm.edges.new((vs[i], vs[i + 1])) for i in range(len(vs) - 1)]
    bmesh.ops.spin(bm, geom=vs + es, cent=(0, 0, 0), axis=(0, 0, 1),
                   dvec=(0, 0, 0), angle=math.tau, steps=steps, use_merge=True)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    return ob


def bevel_profile(ob: bpy.types.Object, width: float, profile: Sequence[Point2],
                  segments: int = 8, angle_deg: float = 34.0,
                  ) -> bpy.types.Modifier:
    """Add a Bevel modifier with a custom profile curve.

    Parameters:
        ob: Mesh object to bevel.
        width: Bevel width, metres.
        profile: Normalised (x, y) points in [0, 1] including both endpoints
            (0,0) and (1,1). Only interior points are editable; the endpoints
            are kept by Blender.
        segments: Segments in the bevel.
        angle_deg: Only edges sharper than this get beveled.

    Returns:
        The Bevel modifier.
    """
    m = ob.modifiers.new('bevel_profile', 'BEVEL')
    m.width = width
    m.segments = segments
    m.limit_method = 'ANGLE'
    m.angle_limit = math.radians(angle_deg)
    m.harden_normals = True
    m.profile_type = 'CUSTOM'
    cp = m.custom_profile
    for p in list(cp.points)[1:-1]:
        cp.points.remove(p)
    for x, y in profile[1:-1]:
        cp.points.add(x, y)
    return m


def guilloche_band(name: str, R: int, r: int, d: float, copies: int,
                   target: bpy.types.Object | None = None, samples: int = 2048,
                   groove_width: float = 0.0004, groove_depth: float = 0.0002,
                   ) -> bpy.types.Object:
    """Build a guilloché engraving as a beveled curve, optionally on a surface.

    Parameters:
        name: Object name.
        R, r, d: Epitrochoid parameters, see guilloche_points.
        copies: Number of rotated copies of the curve, each rotated by 2 pi / copies.
        target: Optional surface to project onto. If None the band lies in XY.
        samples: Samples per curve.
        groove_width: Bevel radius of the groove, metres.
        groove_depth: Depth of the groove (bevel_depth), metres.

    Returns:
        The curve object, converted to a mesh, linked to the scene collection.
    """
    cu = bpy.data.curves.new(name, type='CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = groove_depth
    cu.bevel_resolution = 2
    cu.fill_mode = 'FULL'
    base = guilloche_points(R, r, d, samples)
    scale = 1.0 / max(R + abs(d), 1.0)
    for j in range(copies):
        a = math.tau * j / copies
        ca, sa = math.cos(a), math.sin(a)
        pts = [(ca * x - sa * y, sa * x + ca * y) for x, y in base]
        sp = cu.splines.new('POLY')
        sp.points.add(len(pts) - 1)
        for i, (x, y) in enumerate(pts):
            sp.points[i].co = (x * scale, y * scale, 0.0, 1.0)
        sp.use_cyclic_u = True
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    return ob
```

Notes on the module above, stated plainly:

- `guilloche_band` builds the curve in 2D and the Curve bevel gives the groove. The
  `target` argument is accepted but not yet wired in: projection of a curve to a
  surface is done by sampling the spline and calling `project_to_surface`, which is
  left for the next pass. Treat `target` as a placeholder until then. That is a
  real gap, not a stub I am hiding.
- The `Frame` tangent in `frames_closed` falls back to world X when the tangent is
  vertical. `ring_placement` does not hit that path for flat rings.
- `bevel_profile` writes points via `points.add(x, y)`. Verified in this session.

### 10.2 Additions to `forgekit/materials.py`

```python
METAL_F0: dict[str, tuple[float, float, float]] = {
    "gold":       (1.000, 0.766, 0.336),
    "silver":     (0.972, 0.960, 0.915),
    "copper":     (0.955, 0.638, 0.538),
    "aluminium":  (0.913, 0.922, 0.924),
    "platinum":   (0.672, 0.637, 0.585),
    "titanium":   (0.542, 0.497, 0.449),
    "chromium":   (0.550, 0.556, 0.554),
    "brass":      (0.681, 0.451, 0.157),
    "bronze":     (0.550, 0.360, 0.170),
    "rose_gold":  (0.920, 0.600, 0.500),
}
"""Linear-sRGB normal-incidence reflectance F0 per metal.

Recalled from the standard real-time table (Lagarde/de Rousiers, Hoffman).
Not re-derived from Johnson and Christy. brass, bronze and rose_gold are
estimates. See 04-detail-ornament.md section 6.
"""


def metal_pbr(name: str, metal: str, roughness: float = 0.25,
              anisotropic: float = 0.0, anisotropic_rotation: float = 0.0,
              coat: float = 0.0, coat_roughness: float = 0.05,
              thin_film_nm: float | None = None,
              thin_film_ior: float = 1.5,
              noise_scale: float = 240.0,
              noise_stretch: float = 1.0,
              bump_strength: float = 0.18,
              ) -> bpy.types.Material:
    """Physically structured metal: anisotropy, coat, thin film, breakup.

    Parameters:
        name: Material name.
        metal: Key of METAL_F0.
        roughness: Base roughness, 0..1.
        anisotropic: Principled Anisotropic, 0..1. Use 0.6 to 0.85 for brushed.
        anisotropic_rotation: Principled Anisotropic Rotation, 0..1, sets the
            brush direction.
        coat: Coat Weight, 0..1. Lacquered finish.
        coat_roughness: Coat Roughness.
        thin_film_nm: Thin film thickness in nanometres, or None to disable.
            300 to 800 gives tarnish or anodise colours.
        thin_film_ior: Thin Film IOR.
        noise_scale: Breakup noise scale in object units.
        noise_stretch: Scale ratio along brush. Use 40 to 60 for brushed grain;
            1.0 for isotropic.
        bump_strength: Normal breakup strength. Above ~0.4 reads as corrosion.

    Returns:
        The new material with Principled BSDF, Metallic 1, breakup on roughness
        and normal.

    Raises:
        KeyError: if metal is not in METAL_F0.
    """
    base = METAL_F0[metal]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*base, 1.0)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = roughness
    b.inputs["Anisotropic"].default_value = anisotropic
    b.inputs["Anisotropic Rotation"].default_value = anisotropic_rotation
    b.inputs["Coat Weight"].default_value = coat
    b.inputs["Coat Roughness"].default_value = coat_roughness
    if thin_film_nm is not None:
        b.inputs["Thin Film Thickness"].default_value = thin_film_nm
        b.inputs["Thin Film IOR"].default_value = thin_film_ior
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = noise_scale
    noise.inputs["Detail"].default_value = 8.0
    noise.inputs["Roughness"].default_value = 0.6
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = bump_strength
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    rng = nt.nodes.new("ShaderNodeMapRange")
    rng.inputs["To Min"].default_value = max(0.0, roughness - 0.14)
    rng.inputs["To Max"].default_value = min(1.0, roughness + 0.16)
    nt.links.new(noise.outputs["Fac"], rng.inputs["Value"])
    nt.links.new(rng.outputs["Result"], b.inputs["Roughness"])
    return m
```

`noise_stretch` is accepted in the signature but the Mapping node that applies it is
not added in this draft. Brushed grain needs a Texture Coordinate and Mapping node
with a stretched Scale. That is a known omission in this draft, not a hidden one.

```python
def cavity_patina(mat: bpy.types.Material, color: tuple[float, float, float],
                  amount: float = 0.7, radius: float = 0.05,
                  ) -> bpy.types.Material:
    """Mix a patina colour into cavities using Ambient Occlusion (inside).

    Parameters:
        mat: Material from metal_pbr or any Principled material.
        color: Patina colour, linear RGB. Verdigris estimate (0.25, 0.55, 0.45).
        amount: Maximum mix strength, 0..1.
        radius: AO sampling radius in metres. Use the scale of the crevices.

    Returns:
        The same material, with the patina wired into Base Color and Roughness.
        Cavity masks are evaluated by Cycles at render; EEVEE is not used here.
    """
    nt = mat.node_tree
    b = nt.nodes["Principled BSDF"]
    ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
    ao.inside = True
    ao.only_local = True
    ao.inputs["Distance"].default_value = radius
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.45
    ramp.color_ramp.elements[1].position = 0.85
    nt.links.new(ao.outputs["AO"], ramp.inputs["Fac"])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs[6].default_value = b.inputs["Base Color"].default_value
    mix.inputs[7].default_value = (*color, 1.0)
    nt.links.new(ramp.outputs["Color"], mix.inputs[0])
    nt.links.new(mix.outputs[2], b.inputs["Base Color"])
    return mat
```

`cavity_patina` uses the AO node. Its input names (`Distance`, `Only Local`, `inside`)
are from the 5.3 node set but the `Distance` socket name and the `inside` property
were NOT executed in this session. Confirm them the first time the function runs.

## 11. What was verified by running, and what was read only

**Verified by execution** in this container (bpy 5.3.0, py3.13, software GL env set):

- bpy imports headless with the env vars from section 1.
- Epitrochoid closure: period T = 2 pi r / gcd(R, r) gives closure error 1.7e-13 for R=96, r=40, d=30.
- Arc-length resampling on a closed curve: 180 points, gap ratio 0.996, seam gap 0.997 of mean (from 4000-sample polyline).
- BVH projection onto a UV sphere: 2000/2000 hits, all on the surface, 0.0014 ms per query.
- Planar-loop holonomy: -0.03 deg. Spatial-loop holonomy: 156.13 deg. The correction is required and is in `frames_closed` as written.
- Custom bevel profile: `profile_type='CUSTOM'`, `points.remove` on interior points only (endpoint removal raises RuntimeError), `points.add(x, y)`, evaluates to 488 verts on a cube.
- Font to mesh: "FORGE", size 0.1, extrude 0.002, convert to mesh, 1256 verts, 3 ms. Requires using the active object after convert.
- Principled BSDF 5.3 inputs present: Anisotropic, Anisotropic Rotation, Coat Weight, Coat Roughness, Thin Film Thickness, Thin Film IOR.
- Node types present: ShaderNodeAmbientOcclusion, ShaderNodeBevel, GeometryNodeCurveToMesh, GeometryNodeSampleNearestSurface.
- Moulding tables: computed in Python from the formulas above. The arithmetic is verified; the physical profile shape is not compared to a real moulding.

**Read only, not executed** (treat as unverified):

- Metal F0 values: recalled from the standard table, not checked against Johnson and Christy or a published tabulation.
- Johnson and Christy full table: not retrieved. The web search returned the DOI (10.1103/PhysRevB.6.4370) and the abstract, which states n, k estimated error below 0.02 over most of the 0.5 to 6.5 eV range, for Cu, Ag and Au only. Aluminium is not in that paper.
- Geometry Nodes Curve-to-Mesh and Sample Nearest Surface: node types exist; no node tree was built or run.
- Multiresolution displacement bake and Cycles cavity mask at render time.
- Brushed-grain Mapping stretch (see the `noise_stretch` note).
- `guilloche_band` with `target` projection (placeholder, see note).
- glTF/FBX export of bevelled Curve geometry and of the procedural materials.
- Parametric family grammar: design only, no code.

## 12. Three highest-value additions

1. **`frames_closed` with holonomy correction.** Without it, any ring of ornaments
   on a spatial curve (a cartouche on a curved base, a knurl around a bulge) has a
   silent seam twist. This one function fixes a class of bug that otherwise looks
   right in a Workbench render and wrong in the close-up.
2. **Exact epitrochoid guilloché with closure check.** Closed-form, verified, and it is
   the most recognisable "expensive" ornament there is. Gives the kit a real
   decorative vocabulary that is not a grid of boxes.
3. **`bevel_profile` with classical mouldings.** The moulding tables turn `lathe`
   into a moulding library, and the custom bevel profile is what makes edges read as
   crafted rather than CG. Verified end to end.

## Gaps I did not close

- Projection of a curve (not a point set) onto a surface for `guilloche_band`.
- Metal values were not re-derived from measured optical data.
- Brushed-grain Mapping stretch.
- Geometry Nodes path was not executed.
- Export survival of the ornament geometry and material pass.
