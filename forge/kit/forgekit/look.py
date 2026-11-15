"""look - colour and shading that an eye reads as detail.

Shader-side masks built from geometry (no UVs needed, Cycles only):

  convex mask   Pointiness above 0.5, sharpened, broken up by noise.
                Drives edge wear: brighter, smoother, more exposed material.
  concave mask  Pointiness below 0.5 combined with Ambient Occlusion.
                Drives dirt, grime, glaze pooling.
  edge mask     Bevel-node normal against the true normal. Finds every edge
                but cannot tell convex from concave, so it is only used to
                scale the convex mask (standard procedural-wear recipe).

Known Blender limitation: the AO node ignores render-time bevel, so concave
detection leans on Pointiness, which needs a reasonably dense mesh. Add a
bevel modifier or subdivision before relying on it.
"""
import bpy

TEX_SCALE_REF = 1.0  # noise scales are given per object size


# ------------------------------------------------------------------ node helpers
def node(nt, kind, x=0, y=0, **props):
    n = nt.nodes.new(kind)
    n.location = (x, y)
    for k, v in props.items():
        setattr(n, k, v)
    return n


def link(nt, out_sock, in_sock):
    nt.links.new(out_sock, in_sock)
    return in_sock


def math_node(nt, op, a, b=None, x=0, y=0, clamp=False):
    n = node(nt, "ShaderNodeMath", x, y, operation=op, use_clamp=clamp)
    _feed(nt, n.inputs[0], a)
    if b is not None:
        _feed(nt, n.inputs[1], b)
    return n.outputs[0]


def _feed(nt, sock, value):
    if hasattr(value, "is_output") or hasattr(value, "links"):
        nt.links.new(value, sock)
    else:
        sock.default_value = value


def map_range(nt, value, fmin, fmax, tmin=0.0, tmax=1.0, x=0, y=0):
    n = node(nt, "ShaderNodeMapRange", x, y, clamp=True)
    _feed(nt, n.inputs["Value"], value)
    n.inputs["From Min"].default_value = fmin
    n.inputs["From Max"].default_value = fmax
    n.inputs["To Min"].default_value = tmin
    n.inputs["To Max"].default_value = tmax
    return n.outputs["Result"]


def mix_colour(nt, fac, a, b, blend="MIX", x=0, y=0):
    """RGBA mix by socket identifier; avoids the duplicate 'A'/'B' names."""
    n = node(nt, "ShaderNodeMix", x, y, data_type="RGBA", blend_type=blend, clamp_result=True)
    ins = {s.identifier: s for s in n.inputs}
    _feed(nt, ins["Factor_Float"], fac)
    _feed(nt, ins["A_Color"], a if not isinstance(a, tuple) else (*a[:3], 1.0))
    _feed(nt, ins["B_Color"], b if not isinstance(b, tuple) else (*b[:3], 1.0))
    outs = {s.identifier: s for s in n.outputs}
    return outs["Result_Color"]


def mix_float(nt, fac, a, b, x=0, y=0):
    n = node(nt, "ShaderNodeMix", x, y, data_type="FLOAT")
    ins = {s.identifier: s for s in n.inputs}
    _feed(nt, ins["Factor_Float"], fac)
    _feed(nt, ins["A_Float"], a)
    _feed(nt, ins["B_Float"], b)
    return {s.identifier: s for s in n.outputs}["Result_Float"]


def object_size(obj):
    d = obj.dimensions
    return max(d.x, d.y, d.z, 1e-4)


def object_coords(nt, size, x=-1400, y=0):
    """Object-space coordinates divided by object size, so a noise scale of 8
    means about 8 features across the object whatever its real size."""
    tc = node(nt, "ShaderNodeTexCoord", x, y)
    mp = node(nt, "ShaderNodeMapping", x + 200, y)
    s = 1.0 / size
    mp.inputs["Scale"].default_value = (s, s, s)
    link(nt, tc.outputs["Object"], mp.inputs["Vector"])
    return mp.outputs["Vector"]


def noise(nt, coord, scale, detail=6.0, roughness=0.55, x=0, y=0):
    n = node(nt, "ShaderNodeTexNoise", x, y)
    link(nt, coord, n.inputs["Vector"])
    n.inputs["Scale"].default_value = scale
    n.inputs["Detail"].default_value = detail
    n.inputs["Roughness"].default_value = roughness
    return n.outputs["Fac"]


# ------------------------------------------------------------------ masks
def convex_mask(nt, coord, breakup_scale=6.0, low=0.52, high=0.60, bevel_radius=0.0,
                breakup=0.55, x=-600, y=400):
    """0..1 on convex edges and corners. Pointiness band low..high, scaled by
    a noise so wear is heavy in places and absent in others."""
    geo = node(nt, "ShaderNodeNewGeometry", x - 600, y)
    m = map_range(nt, geo.outputs["Pointiness"], low, high, 0.0, 1.0, x - 400, y)
    if bevel_radius > 0:
        bev = node(nt, "ShaderNodeBevel", x - 600, y - 200, samples=8)
        bev.inputs["Radius"].default_value = bevel_radius
        dot = node(nt, "ShaderNodeVectorMath", x - 400, y - 200, operation="DOT_PRODUCT")
        link(nt, bev.outputs["Normal"], dot.inputs[0])
        link(nt, geo.outputs["Normal"], dot.inputs[1])
        edge = map_range(nt, dot.outputs["Value"], 0.995, 0.90, 0.0, 1.0, x - 200, y - 200)
        m = math_node(nt, "MAXIMUM", m, math_node(nt, "MULTIPLY", edge, 0.6, x=x - 100, y=y - 200),
                      x=x - 200, y=y)
    n = noise(nt, coord, breakup_scale, 8.0, 0.6, x - 400, y + 200)
    gate = map_range(nt, n, breakup - 0.08, breakup + 0.08, 0.0, 1.0, x - 200, y + 200)
    return math_node(nt, "MULTIPLY", m, gate, x=x, y=y, clamp=True)


def concave_mask(nt, coord, ao_distance, low=0.42, high=0.49, ao_weight=0.6,
                 breakup_scale=10.0, x=-600, y=-200):
    """0..1 in crevices and recesses: Pointiness below `high`, combined with
    1 - AO over `ao_distance` metres. Soft noise keeps it from looking stamped."""
    geo = node(nt, "ShaderNodeNewGeometry", x - 600, y)
    pt = map_range(nt, geo.outputs["Pointiness"], high, low, 0.0, 1.0, x - 400, y)
    ao = node(nt, "ShaderNodeAmbientOcclusion", x - 600, y - 220, samples=12, inside=False,
              only_local=False)
    ao.inputs["Distance"].default_value = ao_distance
    occl = map_range(nt, ao.outputs["AO"], 1.0, 0.35, 0.0, 1.0, x - 400, y - 220)
    both = math_node(nt, "MAXIMUM", pt, math_node(nt, "MULTIPLY", occl, ao_weight, x=x - 300, y=y - 220),
                     x=x - 200, y=y)
    n = noise(nt, coord, breakup_scale, 4.0, 0.5, x - 400, y + 160)
    soft = map_range(nt, n, 0.25, 0.75, 0.55, 1.0, x - 200, y + 160)
    return math_node(nt, "MULTIPLY", both, soft, x=x, y=y, clamp=True)


def height_mask(nt, low=0.0, high=0.35, x=-600, y=-600):
    """0..1 from the object's bottom (1) to `high` of its height (0), using
    generated coordinates. Grime and splash collect low on real objects."""
    tc = node(nt, "ShaderNodeTexCoord", x - 400, y)
    sep = node(nt, "ShaderNodeSeparateXYZ", x - 200, y)
    link(nt, tc.outputs["Generated"], sep.inputs["Vector"])
    return map_range(nt, sep.outputs["Z"], high, low, 0.0, 1.0, x, y)


# ------------------------------------------------------------------ grain
def grain(nt, coord, kind, scale, x=-600, y=-1000):
    """Returns (height, tone) float sockets for a surface grain, or (None, None).

    height feeds the bump; tone (0..1) modulates colour value so the grain is
    seen in the albedo as well as the shading.
      brushed  noise stretched along X: machined / satin metal
      wood     rings along Z, distorted, with fibre noise across them
      stone    voronoi cells (crack network) over multi-scale noise
      cast     fine isotropic sand texture
      ceramic  near-smooth with a faint crackle network
      cloth    two crossed fine waves: woven fabric
      leather  voronoi pebbling
    """
    if kind in (None, "none"):
        return None, None
    if kind == "brushed":
        mp = node(nt, "ShaderNodeMapping", x - 400, y)
        mp.inputs["Scale"].default_value = (0.08, 1.0, 1.0)
        link(nt, coord, mp.inputs["Vector"])
        h = noise(nt, mp.outputs["Vector"], scale, 10.0, 0.7, x - 200, y)
        return h, h
    if kind == "wood":
        w = node(nt, "ShaderNodeTexWave", x - 200, y, wave_type="RINGS", rings_direction="Z")
        link(nt, coord, w.inputs["Vector"])
        w.inputs["Scale"].default_value = scale
        w.inputs["Distortion"].default_value = 4.0
        w.inputs["Detail"].default_value = 4.0
        fibre = noise(nt, coord, scale * 12.0, 8.0, 0.65, x - 200, y - 200)
        tone = math_node(nt, "MULTIPLY", w.outputs["Fac"], map_range(nt, fibre, 0, 1, 0.75, 1.0,
                                                                         x - 100, y - 200), x=x, y=y)
        return tone, tone
    if kind == "stone":
        v = node(nt, "ShaderNodeTexVoronoi", x - 200, y, feature="DISTANCE_TO_EDGE")
        link(nt, coord, v.inputs["Vector"])
        v.inputs["Scale"].default_value = scale
        crack = map_range(nt, v.outputs["Distance"], 0.0, 0.04, 0.0, 1.0, x, y)
        n = noise(nt, coord, scale * 3.0, 10.0, 0.6, x - 200, y - 200)
        h = math_node(nt, "MULTIPLY", crack, map_range(nt, n, 0, 1, 0.6, 1.0, x, y - 200), x=x + 150, y=y)
        return h, n
    if kind == "cast":
        n = noise(nt, coord, scale, 12.0, 0.75, x - 200, y)
        return n, map_range(nt, n, 0, 1, 0.85, 1.0, x, y)
    if kind == "ceramic":
        v = node(nt, "ShaderNodeTexVoronoi", x - 200, y, feature="DISTANCE_TO_EDGE")
        link(nt, coord, v.inputs["Vector"])
        v.inputs["Scale"].default_value = scale
        crack = map_range(nt, v.outputs["Distance"], 0.0, 0.006, 0.0, 1.0, x, y)
        return crack, map_range(nt, crack, 0, 1, 0.82, 1.0, x + 150, y)
    if kind == "cloth":
        a = node(nt, "ShaderNodeTexWave", x - 200, y, wave_type="BANDS", bands_direction="X")
        b = node(nt, "ShaderNodeTexWave", x - 200, y - 200, wave_type="BANDS", bands_direction="Y")
        for wv in (a, b):
            link(nt, coord, wv.inputs["Vector"])
            wv.inputs["Scale"].default_value = scale
        h = math_node(nt, "MULTIPLY", a.outputs["Fac"], b.outputs["Fac"], x=x, y=y)
        return h, map_range(nt, h, 0, 1, 0.8, 1.0, x + 150, y)
    if kind == "leather":
        v = node(nt, "ShaderNodeTexVoronoi", x - 200, y, feature="SMOOTH_F1")
        link(nt, coord, v.inputs["Vector"])
        v.inputs["Scale"].default_value = scale
        return v.outputs["Distance"], map_range(nt, v.outputs["Distance"], 0, 1, 0.85, 1.0, x, y)
    raise ValueError(f"unknown grain {kind!r}")
