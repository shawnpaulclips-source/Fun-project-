"""Process-aware detail: make a part look MADE, not noised.

Real objects carry detail because of how they were manufactured. A turned
spindle has lathe feed marks running round it; a casting has a parting-line
seam and draft; a stamping is rounded on the punch side and sharp on the burr
side. Random noise has none of that structure and reads as CG. So detail here
is a rule set keyed by manufacturing process, and a part receives the marks its
process leaves and nothing else.

    from forgekit import detail as D
    D.tag_process(part, "turned")
    rep = D.apply_detail(part, "turned", seed=7, intensity=1.0)

Three tiers, always in this order (enforced - applying a tier after a later one
raises DetailOrderError, and a tier can be applied once):

    1. form     bevels, radii, draft                    (geometry, applied into the mesh)
    2. surface  process marks                           (geometry if resolvable, else shader bump)
    3. wear     cavity + edge wear driven by CURVATURE  (vertex attributes + geometry + material)

Every rule is a named function below with its amplitudes in METRES stated in its
docstring and in the returned report. `intensity` scales every amplitude
linearly; intensity=1 is the physically-motivated value.

Surface marks are often finer than the mesh. Each surface rule therefore
decides: if refining the mesh to >= 4 samples per period fits `budget_verts`,
the mark is real displaced geometry (visible in Workbench, survives any
export); otherwise it is written as a Bump node into the object's material,
driven by object-space coordinates (visible in Cycles, and bake.py bakes it into
the normal map). mode='geometry' | 'shader' | 'auto' forces or chooses. The
report says which path ran and why.

Units: Blender units are metres (forge contract). Axes are LOCAL object axes.
"""
import math, time, zlib
import bpy, bmesh
import numpy as np
from mathutils import Vector, noise

PROCESSES = ("cast", "forged", "turned", "stamped", "machined", "engraved")
TIERS = ("form", "surface", "wear")
_AX = {"X": 0, "Y": 1, "Z": 2}


class DetailOrderError(RuntimeError):
    """A tier was applied out of order or twice."""


# ===================================================================== tagging
def tag_process(obj, process):
    """Record the manufacturing process on the object (custom prop forge_process)."""
    if process not in PROCESSES:
        raise ValueError(f"unknown process {process!r}; valid: {PROCESSES}")
    prev = obj.get("forge_process")
    if prev and prev != process:
        raise ValueError(f"{obj.name} is already tagged {prev!r}; one part, one process. "
                         f"Split the part if it was made in stages.")
    obj["forge_process"] = process
    return process


def applied_tiers(obj):
    return list(obj.get("forge_detail_tiers", []))


def _claim_tier(obj, tier):
    done = applied_tiers(obj)
    if tier in done:
        raise DetailOrderError(f"tier {tier!r} already applied to {obj.name}; roll back to re-apply")
    later = [t for t in done if TIERS.index(t) > TIERS.index(tier)]
    if later:
        raise DetailOrderError(f"cannot apply {tier!r} after {later}: order is form -> surface -> wear")
    obj["forge_detail_tiers"] = done + [tier]


# ===================================================================== mesh helpers
def _axis_index(axis):
    a = axis.upper().lstrip("+-")
    if a not in _AX:
        raise ValueError(f"axis must be X, Y or Z (got {axis!r})")
    return _AX[a]


def _arrays(me):
    n = len(me.vertices)
    co = np.empty(n * 3, np.float64)
    me.vertices.foreach_get("co", co)
    nr = np.empty(n * 3, np.float64)
    me.vertex_normals.foreach_get("vector", nr)
    return co.reshape(-1, 3), nr.reshape(-1, 3)


def _write_co(me, co):
    me.vertices.foreach_set("co", co.reshape(-1).astype(np.float32))
    me.update()


def _mean_edge(me):
    n = len(me.edges)
    if n == 0:
        return 0.0
    ev = np.empty(n * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
    co, _ = _arrays(me)
    ev = ev.reshape(-1, 2)
    return float(np.linalg.norm(co[ev[:, 0]] - co[ev[:, 1]], axis=1).mean())


def _apply_bevel(obj, width, segments, angle_deg=30.0, limit="ANGLE", weight_attr=None,
                 profile=0.5):
    """Add a Bevel modifier and APPLY it into the mesh, evaluating only it.

    Other modifiers (weighted normals, etc.) are disabled during evaluation and
    re-enabled after, so they keep acting on the new geometry."""
    others = [(m, m.show_viewport, m.show_render) for m in obj.modifiers]
    for m, _, _ in others:
        m.show_viewport = m.show_render = False
    mod = obj.modifiers.new("forge_form_bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.profile = profile
    mod.limit_method = limit
    if limit == "ANGLE":
        mod.angle_limit = math.radians(angle_deg)
    if weight_attr:
        mod.edge_weight = weight_attr
    mod.harden_normals = False
    mod.use_clamp_overlap = True
    obj.modifiers.move(len(obj.modifiers) - 1, 0)
    try:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        new = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
        old = obj.data
        obj.data = new
        new.name = old.name
        for mat in old.materials:
            if mat and mat.name not in [m.name for m in new.materials if m]:
                new.materials.append(mat)
        if old.users == 0:
            bpy.data.meshes.remove(old)
    finally:
        obj.modifiers.remove(obj.modifiers["forge_form_bevel"])
        for m, v, r in others:
            m.show_viewport, m.show_render = v, r
    return {"verts": len(obj.data.vertices)}


def _refine_axial(obj, axis_i, spacing, budget):
    """Subdivide edges that run along the axis so no edge spans more than
    `spacing` metres of axial distance. Quads from a lathe stay quads.
    Returns (ok, verts). ok=False if the budget would be exceeded (mesh untouched)."""
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    buckets = {}
    extra = 0
    for e in bm.edges:
        dz = abs(e.verts[0].co[axis_i] - e.verts[1].co[axis_i])
        k = int(math.ceil(dz / spacing)) - 1
        if k > 0:
            buckets.setdefault(k, []).append(e)
            extra += k
    if len(bm.verts) + extra * 2 > budget:
        bm.free()
        return False, len(me.vertices) + extra * 2
    for k, edges in sorted(buckets.items()):
        edges = [e for e in edges if e.is_valid]
        if edges:
            bmesh.ops.subdivide_edges(bm, edges=edges, cuts=k, use_grid_fill=True)
    bm.to_mesh(me)
    n = len(bm.verts)
    bm.free()
    me.update()
    return True, n


def _refine_edges(obj, max_len, budget):
    """Halve every edge longer than max_len until none remain or the vertex
    budget is hit. Returns (ok, verts, achieved_max_edge)."""
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    ok = True
    for _ in range(12):
        long_e = [e for e in bm.edges if e.calc_length() > max_len]
        if not long_e:
            break
        if len(bm.verts) + len(long_e) * 3 > budget:
            ok = False
            break
        bmesh.ops.subdivide_edges(bm, edges=long_e, cuts=1, use_grid_fill=True)
    mx = max((e.calc_length() for e in bm.edges), default=0.0)
    bm.to_mesh(me)
    n = len(bm.verts)
    bm.free()
    me.update()
    return ok and mx <= max_len * 1.001, n, mx


def _bisect_band(obj, axis_i, center, half_width, cuts):
    """Bisect the mesh with `cuts` planes perpendicular to the axis, evenly
    spread over center +- half_width. Cheap way to resolve a narrow feature."""
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    nrm = Vector((0, 0, 0))
    nrm[axis_i] = 1.0
    for k in range(cuts):
        z = center - half_width + 2 * half_width * k / max(1, cuts - 1)
        co = Vector((0, 0, 0))
        co[axis_i] = z
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co, plane_no=nrm)
    # a plane passing within a hair of an existing vertex leaves a sliver face;
    # collapse anything under 1 um so later refinement does not multiply it
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
    bm.to_mesh(me)
    bm.free()
    me.update()
    return len(me.vertices)


def _principled_of(obj):
    for slot in obj.material_slots:
        m = slot.material
        if m and m.use_nodes:
            b = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if b:
                return m, b
    return None, None


def _ensure_material(obj):
    m, b = _principled_of(obj)
    if m is None:
        m = bpy.data.materials.new(f"{obj.name}_detail")
        m.use_nodes = True
        obj.data.materials.append(m)
        b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    return m, b


def _shader_bump(obj, label, amplitude_m, build_height):
    """Insert a Bump node fed by build_height(nt, coord_socket)->float socket,
    chained in front of whatever already drives the Principled Normal input.
    Distance = amplitude in metres, Strength 1, so the shading normal tilts as a
    real displacement of that height would."""
    m, b = _ensure_material(obj)
    nt = m.node_tree
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.label = f"forge_{label}_coord"
    h = build_height(nt, tc.outputs["Object"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.label = f"forge_{label}"
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = amplitude_m
    nt.links.new(h, bump.inputs["Height"])
    nin = b.inputs["Normal"]
    if nin.is_linked:
        nt.links.new(nin.links[0].from_socket, bump.inputs["Normal"])
    nt.links.new(bump.outputs["Normal"], nin)
    return m.name


def _math(nt, op, a, b=None):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            nt.links.new(v, n.inputs[i])
    return n.outputs[0]


def _axis_component(nt, coord, axis_i):
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(coord, sep.inputs[0])
    return sep.outputs[axis_i]


def _cos_profile(nt, s, period, phase):
    """0.5 * (1 + cos(2*pi*s/period + phase)) as a node chain."""
    x = _math(nt, "MULTIPLY", s, 2 * math.pi / period)
    x = _math(nt, "ADD", x, phase)
    c = _math(nt, "COSINE", x)
    return _math(nt, "MULTIPLY", _math(nt, "ADD", c, 1.0), 0.5)


def _choose_path(mode, can_geometry):
    if mode == "geometry":
        return "geometry"
    if mode == "shader":
        return "shader"
    return "geometry" if can_geometry else "shader"


def _seed_phase(seed, salt):
    # crc32, not hash(): str hashing is salted per process and would break determinism
    rng = np.random.default_rng((zlib.crc32(salt.encode()) ^ (int(seed) * 2654435761)) % (2 ** 32))
    return float(rng.uniform(0, 2 * math.pi)), rng


# ===================================================================== FORM rules
def form_turned_break_edge(obj, ctx):
    """Turned: a lathe operator breaks every shoulder with a small chamfer.

    Bevel width 0.0008 m (0.8 mm) x intensity, 2 segments, edges sharper than
    30 deg. Turned shoulders are crisp - keep this small."""
    w = 0.0008 * ctx["intensity"]
    r = _apply_bevel(obj, w, 2, 30.0)
    return {"rule": "form_turned_break_edge", "bevel_width_m": w, **r}


def form_cast_radii_and_draft(obj, ctx):
    """Cast: generous fillets (a pattern cannot hold a sharp internal corner)
    and draft so the pattern can leave the mould.

    Fillet: bevel width 0.0025 m x intensity, 3 segments, edges > 25 deg.
    Draft: 1.0 deg (x intensity) taper of the walls away from the parting plane
    along the pull axis. Lateral offset at distance h from the parting plane is
    h * tan(1 deg) = 0.01745 * h metres (1.7 mm per 100 mm of wall)."""
    ai = _axis_index(ctx.get("axis", "Z"))
    me = obj.data
    co, _ = _arrays(me)
    zp = ctx.get("parting_offset")
    if zp is None:
        zp = float((co[:, ai].min() + co[:, ai].max()) / 2)
    ang = math.radians(1.0 * ctx["intensity"])
    others = [i for i in range(3) if i != ai]
    centre = co[:, others].mean(axis=0)
    rad = co[:, others] - centre
    r = np.linalg.norm(rad, axis=1)
    h = np.abs(co[:, ai] - zp)
    shrink = np.minimum(h * math.tan(ang), r * 0.5)
    with np.errstate(invalid="ignore", divide="ignore"):
        unit = np.where(r[:, None] > 1e-9, rad / r[:, None], 0.0)
    co[:, others] = co[:, others] - unit * shrink[:, None]
    _write_co(me, co)
    w = 0.0025 * ctx["intensity"]
    res = _apply_bevel(obj, w, 3, 25.0)
    ctx["parting_plane"] = zp
    return {"rule": "form_cast_radii_and_draft", "fillet_width_m": w,
            "draft_deg": round(math.degrees(ang), 4), "max_draft_offset_m": round(float(shrink.max()), 6),
            "parting_plane_m": round(zp, 6), **res}


def form_forged_radii(obj, ctx):
    """Forged: metal flowed under the hammer/die, so every edge is a soft
    radius, never a cut. Bevel width 0.0015 m x intensity, 3 segments, profile
    0.6 (slightly fuller than circular), edges > 25 deg."""
    w = 0.0015 * ctx["intensity"]
    r = _apply_bevel(obj, w, 3, 25.0, profile=0.6)
    return {"rule": "form_forged_radii", "bevel_width_m": w, **r}


def form_stamped_asymmetric_edges(obj, ctx):
    """Stamped/blanked: the punch-entry side rolls over (rounded), the exit
    side tears and carries a burr (sharp). Edge radii differ by side.

    Punch side (the +axis half of the part): radius 0.0012 m x intensity.
    Burr side (-axis half): 0.15 x that = 0.00018 m. Only edges sharper than
    30 deg are rounded. Implemented with bevel weights on the edge attribute
    'bevel_weight_edge' and a WEIGHT-limited bevel, 2 segments."""
    ai = _axis_index(ctx.get("axis", "Z"))
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    zs = [v.co[ai] for v in bm.verts]
    mid = (min(zs) + max(zs)) / 2
    lay = bm.edges.layers.float.get("bevel_weight_edge") or bm.edges.layers.float.new("bevel_weight_edge")
    n_punch = n_burr = 0
    for e in bm.edges:
        if len(e.link_faces) != 2 or e.calc_face_angle(0.0) < math.radians(30):
            e[lay] = 0.0
            continue
        zc = (e.verts[0].co[ai] + e.verts[1].co[ai]) / 2
        if zc >= mid:
            e[lay] = 1.0
            n_punch += 1
        else:
            e[lay] = 0.15
            n_burr += 1
    bm.to_mesh(me)
    bm.free()
    w = 0.0012 * ctx["intensity"]
    r = _apply_bevel(obj, w, 2, limit="WEIGHT", weight_attr="bevel_weight_edge")
    return {"rule": "form_stamped_asymmetric_edges", "punch_side_radius_m": w,
            "burr_side_radius_m": round(w * 0.15, 7), "punch_edges": n_punch, "burr_edges": n_burr, **r}


def form_machined_break_edge(obj, ctx):
    """Machined: a deburring pass leaves a tiny 45-deg break-edge chamfer.
    Bevel width 0.0003 m x intensity, 1 segment, edges > 30 deg."""
    w = 0.0003 * ctx["intensity"]
    r = _apply_bevel(obj, w, 1, 30.0)
    return {"rule": "form_machined_break_edge", "chamfer_width_m": w, **r}


def form_engraved_blank(obj, ctx):
    """Engraved: engraving is done on a finished, machined blank, so the form
    tier is the machined break edge (0.0003 m x intensity, 1 segment)."""
    r = form_machined_break_edge(obj, ctx)
    r["rule"] = "form_engraved_blank"
    return r


# ===================================================================== SURFACE rules
def surface_turned_tool_lines(obj, ctx):
    """Turned: feed marks. A round-nosed tool advancing `feed` per revolution
    leaves a scallop running round the part, so the marks are concentric rings
    along the revolve axis (a helix of pitch=feed; at these pitches a helix and
    rings are indistinguishable).

    Feed f = 0.0005 m (0.5 mm/rev, a roughing feed: the coarsest that reads at
    render scale; finishing feeds of 0.05-0.2 mm are below any render's
    resolution). Tool nose radius r = 0.0008 m. Scallop height (peak to valley)
    Rt = f^2 / (8 r) = 3.9e-5 m (39 um) x intensity. Displacement is inward,
    along the vertex normal, weighted by how radial the surface is
    (1 - |n . axis|), so end faces are left alone. The profile is the true
    circular-arc scallop, not a sine."""
    ai = _axis_index(ctx.get("axis", "Z"))
    f = ctx.get("feed", 0.0005)
    rn = ctx.get("nose_radius", 0.0008)
    rt = f * f / (8 * rn) * ctx["intensity"]
    phase, _ = _seed_phase(ctx["seed"], "turned")
    off = phase / (2 * math.pi) * f
    me = obj.data
    co, _ = _arrays(me)
    extent = float(co[:, ai].max() - co[:, ai].min())
    need = len(me.vertices) + int(extent / (f / 4)) * 2 * max(8, int(math.sqrt(len(me.vertices))))
    path = _choose_path(ctx["mode"], need <= ctx["budget"])
    out = {"rule": "surface_turned_tool_lines", "feed_m": f, "nose_radius_m": rn,
           "scallop_height_m": round(rt, 9), "path": path}
    if path == "geometry":
        ok, n = _refine_axial(obj, ai, f / 4, ctx["budget"])
        out["refined_verts"] = n
        if not ok:
            if ctx["mode"] == "geometry":
                out["resolved"] = False
                out["note"] = "vertex budget exceeded; geometry left unrefined, marks would alias"
                return out
            path = out["path"] = "shader"
    if path == "geometry":
        co, nr = _arrays(me)
        u = np.mod(co[:, ai] - off, f) - f / 2
        scallop = (rn - np.sqrt(np.maximum(rn * rn - u * u, 0.0)))
        scallop = scallop / max(scallop.max(), 1e-12) * rt
        depth = rt - scallop
        w = 1.0 - np.abs(nr[:, ai])
        co -= nr * (depth * w)[:, None]
        _write_co(me, co)
        out["resolved"] = True
        out["samples_per_period"] = 4
    else:
        def height(nt, coord):
            return _cos_profile(nt, _axis_component(nt, coord, ai), f, phase)
        out["material"] = _shader_bump(obj, "turned_tool_lines", rt, height)
        out["resolved"] = True
        out["note"] = "shader bump: visible in Cycles and in a baked normal map, not in Workbench"
    return out


def surface_cast_parting_line(obj, ctx):
    """Cast: the parting line. Where the two mould halves met, a thin fin of
    flash formed; fettling grinds it off but leaves a faint raised seam.

    Seam height 6e-5 m (60 um) x intensity, Gaussian half-width 3.5e-4 m
    (0.35 mm), on the plane through the part's middle perpendicular to the pull
    axis (or ctx['parting_plane'] set by the form tier). Always geometry: 9
    bisect planes across the seam resolve it at any mesh density."""
    ai = _axis_index(ctx.get("axis", "Z"))
    a = 6e-5 * ctx["intensity"]
    hw = 3.5e-4
    me = obj.data
    co, _ = _arrays(me)
    zp = ctx.get("parting_plane")
    if zp is None:
        zp = float((co[:, ai].min() + co[:, ai].max()) / 2)
    n = _bisect_band(obj, ai, zp, 2.0 * hw, 9)
    co, nr = _arrays(me)
    dz = co[:, ai] - zp
    w = 1.0 - np.abs(nr[:, ai])
    co += nr * (a * np.exp(-(dz / hw) ** 2) * w)[:, None]
    _write_co(me, co)
    return {"rule": "surface_cast_parting_line", "seam_height_m": a, "seam_half_width_m": hw,
            "parting_plane_m": round(zp, 6), "path": "geometry", "resolved": True, "verts": n}


def surface_cast_sand_skin(obj, ctx):
    """Cast: the as-cast skin. Sand grains print a fine, isotropic pitting.

    Pit depth 2.5e-5 m (25 um) x intensity, feature size 0.0012 m (1.2 mm, a
    coarse green-sand finish). Seeded Perlin noise, inward only. Geometry when
    the mesh can reach 0.3 mm edges inside the budget, else shader bump."""
    a = 2.5e-5 * ctx["intensity"]
    cell = 0.0012
    _, rng = _seed_phase(ctx["seed"], "sand")
    off = Vector(rng.uniform(-100, 100, 3))
    me = obj.data
    area = sum(p.area for p in me.polygons)
    need = int(area / (cell / 4) ** 2)
    path = _choose_path(ctx["mode"], need <= ctx["budget"])
    out = {"rule": "surface_cast_sand_skin", "pit_depth_m": a, "feature_m": cell, "path": path}
    if path == "geometry":
        ok, n, mx = _refine_edges(obj, cell / 4, ctx["budget"])
        co, nr = _arrays(me)
        v = np.array([noise.noise(Vector(p) / cell + off) for p in co])
        co -= nr * (a * np.clip(v, 0, None) / max(np.abs(v).max(), 1e-9))[:, None]
        _write_co(me, co)
        out.update(resolved=ok, refined_verts=n, max_edge_m=round(mx, 6))
    else:
        def height(nt, coord):
            tx = nt.nodes.new("ShaderNodeTexNoise")
            tx.inputs["Scale"].default_value = 1.0 / cell
            tx.inputs["Detail"].default_value = 2.0
            mp = nt.nodes.new("ShaderNodeMapping")
            mp.inputs["Location"].default_value = tuple(off * cell)
            nt.links.new(coord, mp.inputs["Vector"])
            nt.links.new(mp.outputs["Vector"], tx.inputs["Vector"])
            return tx.outputs["Fac"]
        out["material"] = _shader_bump(obj, "cast_sand_skin", a, height)
        out["resolved"] = True
    return out


def surface_forged_hammer_facets(obj, ctx):
    """Forged: hammer or swage facets. Each blow leaves a shallow dish; the
    dishes tile the surface like Voronoi cells.

    Facet size 0.006 m (6 mm), dish depth 6e-5 m (60 um) x intensity. Seeded
    Voronoi F1; depth = a * (1 - (d1/(0.5*size))^2) clipped at 0, inward.
    Geometry if edges can reach 1.5 mm inside the budget, else shader bump."""
    a = 6e-5 * ctx["intensity"]
    size = 0.006
    _, rng = _seed_phase(ctx["seed"], "forge")
    off = Vector(rng.uniform(-100, 100, 3))
    me = obj.data
    area = sum(p.area for p in me.polygons)
    need = int(area / (size / 4) ** 2 * 2)
    path = _choose_path(ctx["mode"], need <= ctx["budget"])
    out = {"rule": "surface_forged_hammer_facets", "facet_size_m": size, "dish_depth_m": a, "path": path}
    if path == "geometry":
        ok, n, mx = _refine_edges(obj, size / 4, ctx["budget"])
        co, nr = _arrays(me)
        d1 = np.array([noise.voronoi(Vector(p) / size + off)[0][0] for p in co])
        depth = a * np.clip(1.0 - (d1 / 0.5) ** 2, 0.0, 1.0)
        co -= nr * depth[:, None]
        _write_co(me, co)
        out.update(resolved=ok, refined_verts=n, max_edge_m=round(mx, 6))
    else:
        def height(nt, coord):
            vo = nt.nodes.new("ShaderNodeTexVoronoi")
            vo.inputs["Scale"].default_value = 1.0 / size
            mp = nt.nodes.new("ShaderNodeMapping")
            mp.inputs["Location"].default_value = tuple(off * size)
            nt.links.new(coord, mp.inputs["Vector"])
            nt.links.new(mp.outputs["Vector"], vo.inputs["Vector"])
            d = _math(nt, "MULTIPLY", vo.outputs["Distance"], 2.0)
            return _math(nt, "SUBTRACT", 1.0, _math(nt, "MULTIPLY", d, d))
        out["material"] = _shader_bump(obj, "forged_hammer_facets", a, height)
        out["resolved"] = True
    return out


def surface_stamped_draw_lines(obj, ctx):
    """Stamped: die-contact streaks running in the draw direction (the sheet
    slid over the die radius).

    Depth 4e-6 m (4 um) x intensity. Noise stretched 1:40 along the draw axis
    (the brushed-metal ratio from research 04), feature 0.0004 m across.
    This is below mesh resolution on any sane part, so it is shader bump unless
    mode='geometry' is forced (then geometry with whatever density fits)."""
    ai = _axis_index(ctx.get("axis", "Z"))
    a = 4e-6 * ctx["intensity"]
    across = 0.0004
    path = "geometry" if ctx["mode"] == "geometry" else "shader"
    out = {"rule": "surface_stamped_draw_lines", "depth_m": a, "feature_across_m": across,
           "stretch": 40, "path": path}
    _, rng = _seed_phase(ctx["seed"], "stamp")
    off = Vector(rng.uniform(-100, 100, 3))
    scale = [1.0 / across] * 3
    scale[ai] = 1.0 / (across * 40)
    if path == "geometry":
        ok, n, mx = _refine_edges(obj, across / 2, ctx["budget"])
        me = obj.data
        co, nr = _arrays(me)
        v = np.array([noise.noise(Vector((p[0] * scale[0], p[1] * scale[1], p[2] * scale[2])) + off) for p in co])
        co -= nr * (a * np.abs(v))[:, None]
        _write_co(me, co)
        out.update(resolved=ok, refined_verts=n, max_edge_m=round(mx, 6))
    else:
        def height(nt, coord):
            mp = nt.nodes.new("ShaderNodeMapping")
            mp.inputs["Scale"].default_value = tuple(scale)
            mp.inputs["Location"].default_value = tuple(off)
            nt.links.new(coord, mp.inputs["Vector"])
            tx = nt.nodes.new("ShaderNodeTexNoise")
            tx.inputs["Scale"].default_value = 1.0
            nt.links.new(mp.outputs["Vector"], tx.inputs["Vector"])
            return tx.outputs["Fac"]
        out["material"] = _shader_bump(obj, "stamped_draw_lines", a, height)
        out["resolved"] = True
    return out


def surface_machined_grain(obj, ctx):
    """Machined: the lay of a milling or grinding pass - fine parallel grooves
    in one direction across every face.

    Pitch 0.0004 m (0.4 mm stepover), depth 5e-6 m (5 um) x intensity, along
    ctx['direction'] (default X). Cosine profile with a seeded phase. Geometry
    only if 0.1 mm sampling fits the budget (it rarely does), else shader bump."""
    di = _axis_index(ctx.get("direction", "X"))
    pitch = 0.0004
    a = 5e-6 * ctx["intensity"]
    phase, _ = _seed_phase(ctx["seed"], "mill")
    me = obj.data
    co, _ = _arrays(me)
    extent = float(co[:, di].max() - co[:, di].min())
    need = len(me.vertices) * max(1, int(extent / (pitch / 4) / max(1, len(me.vertices) ** (1 / 3))))
    path = _choose_path(ctx["mode"], need <= ctx["budget"])
    out = {"rule": "surface_machined_grain", "pitch_m": pitch, "depth_m": a,
           "direction": "XYZ"[di], "path": path}
    if path == "geometry":
        ok, n = _refine_axial(obj, di, pitch / 4, ctx["budget"])
        co, nr = _arrays(me)
        prof = 0.5 * (1 + np.cos(2 * math.pi * co[:, di] / pitch + phase))
        w = 1.0 - np.abs(nr[:, di])
        co -= nr * (a * prof * w)[:, None]
        _write_co(me, co)
        out.update(resolved=ok, refined_verts=n)
    else:
        def height(nt, coord):
            return _cos_profile(nt, _axis_component(nt, coord, di), pitch, phase)
        out["material"] = _shader_bump(obj, "machined_grain", a, height)
        out["resolved"] = True
    return out


def surface_engraved_inset(obj, ctx):
    """Engraved: a recessed inset panel cut into the face pointing along
    ctx['engrave_axis'] (default '+Z').

    Depth 0.0003 m (0.3 mm) x intensity. Border = 12 % of the panel's smaller
    side (ctx['border_frac']). Faces whose normal is within ~18 deg of the axis
    form the panel; they are inset (bmesh inset_region, even offset) and the
    inner region pushed in along -axis. Always geometry."""
    spec = ctx.get("engrave_axis", "+Z")
    ai = _axis_index(spec)
    sign = -1.0 if spec.startswith("-") else 1.0
    depth = 0.0003 * ctx["intensity"]
    frac = ctx.get("border_frac", 0.12)
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.normal_update()
    faces = [f for f in bm.faces if f.normal[ai] * sign > 0.95]
    if not faces:
        bm.free()
        return {"rule": "surface_engraved_inset", "path": "geometry", "resolved": False,
                "note": f"no face points along {spec}"}
    pts = [v.co for f in faces for v in f.verts]
    others = [i for i in range(3) if i != ai]
    ext = min(max(p[i] for p in pts) - min(p[i] for p in pts) for i in others)
    border = frac * ext
    res = bmesh.ops.inset_region(bm, faces=faces, thickness=border, depth=0.0, use_even_offset=True,
                                 use_boundary=True)
    inner_verts = {v for f in faces for v in f.verts}
    for v in inner_verts:
        v.co[ai] -= sign * depth
    bm.to_mesh(me)
    bm.free()
    me.update()
    return {"rule": "surface_engraved_inset", "depth_m": depth, "border_m": round(border, 6),
            "panel_faces": len(faces), "rim_faces": len(res["faces"]), "path": "geometry", "resolved": True}


# ===================================================================== WEAR rule
def vertex_curvature(me):
    """Per-vertex discrete mean curvature proxy in 1/m: mean over edges of
    2 * n_i . (p_i - p_j) / |p_i - p_j|^2. Positive = convex (edges, rims),
    negative = concave (cavities, inside corners). ~1/R for a radius R."""
    co, nr = _arrays(me)
    ev = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    i, j = ev[:, 0], ev[:, 1]
    d = co[i] - co[j]
    l2 = np.maximum((d * d).sum(1), 1e-18)
    ki = 2 * (nr[i] * d).sum(1) / l2
    kj = 2 * (nr[j] * -d).sum(1) / l2
    n = len(co)
    k = np.bincount(i, ki, n) + np.bincount(j, kj, n)
    deg = np.bincount(i, None, n) + np.bincount(j, None, n)
    return k / np.maximum(deg, 1), ev


def _smooth_scalar(v, ev, iters):
    n = len(v)
    i, j = ev[:, 0], ev[:, 1]
    deg = np.maximum(np.bincount(i, None, n) + np.bincount(j, None, n), 1)
    for _ in range(iters):
        s = np.bincount(i, v[j], n) + np.bincount(j, v[i], n)
        v = 0.5 * v + 0.5 * s / deg
    return v


def _smoothstep(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def wear_curvature(obj, ctx):
    """Wear driven by CURVATURE, never by UVs.

    Edge wear: convex vertices with curvature above 1/0.004 m (radius < 4 mm)
    ramp to fully worn at 1/0.0015 m (radius 1.5 mm). Cavity: concave below
    -1/0.004 m ramps to full at -1/0.0015 m. Both are smoothed over 2 edge rings
    and broken up by seeded low-frequency noise (feature 0.03 m, 40-100 %).

    Writes POINT attributes 'forge_wear_edge' and 'forge_cavity' (0..1) and a
    colour attribute 'forge_wear' (R=edge, G=cavity) for Workbench inspection.
    Geometry: worn convex vertices pulled in by up to 4e-5 m (40 um) x
    intensity - handling rounds an edge. Material: edge wear -> base colour x1.35
    and roughness x0.6; cavity -> base colour x0.55 and roughness +0.15, via
    Attribute nodes, so bake.py captures it in the textures."""
    me = obj.data
    k, ev = vertex_curvature(me)
    edge = _smoothstep(k, 1 / 0.004, 1 / 0.0015)
    cav = _smoothstep(-k, 1 / 0.004, 1 / 0.0015)
    edge = _smooth_scalar(edge, ev, 2)
    cav = _smooth_scalar(cav, ev, 2)
    _, rng = _seed_phase(ctx["seed"], "wear")
    off = Vector(rng.uniform(-100, 100, 3))
    co, nr = _arrays(me)
    patch = np.array([noise.noise(Vector(p) / 0.03 + off) for p in co])
    patch = 0.4 + 0.6 * _smoothstep(patch, -0.3, 0.4)
    edge = np.clip(edge * patch * min(1.0, ctx["intensity"]), 0, 1)
    cav = np.clip(cav * min(1.0, ctx["intensity"]), 0, 1)
    pull = 4e-5 * ctx["intensity"]
    co -= nr * (edge * pull)[:, None]
    _write_co(me, co)
    for name, vals in (("forge_wear_edge", edge), ("forge_cavity", cav)):
        if name in me.attributes:
            me.attributes.remove(me.attributes[name])
        at = me.attributes.new(name, "FLOAT", "POINT")
        at.data.foreach_set("value", vals.astype(np.float32))
    if "forge_wear" in me.color_attributes:
        me.color_attributes.remove(me.color_attributes["forge_wear"])
    ca = me.color_attributes.new("forge_wear", "FLOAT_COLOR", "POINT")
    rgba = np.zeros((len(co), 4), np.float32)
    rgba[:, 0], rgba[:, 1], rgba[:, 3] = edge, cav, 1.0
    ca.data.foreach_set("color", rgba.reshape(-1))
    mat = _wire_wear_material(obj)
    return {"rule": "wear_curvature", "edge_radius_full_m": 0.0015, "edge_radius_start_m": 0.004,
            "edge_pull_m": pull, "worn_vertex_frac": round(float((edge > 0.5).mean()), 4),
            "cavity_vertex_frac": round(float((cav > 0.5).mean()), 4),
            "curvature_p99_per_m": round(float(np.percentile(k, 99)), 2), "material": mat,
            "attributes": ["forge_wear_edge", "forge_cavity", "forge_wear"]}


def _wire_wear_material(obj):
    m, b = _principled_of(obj)
    if m is None:
        return None
    nt = m.node_tree
    ae = nt.nodes.new("ShaderNodeAttribute")
    ae.attribute_name = "forge_wear_edge"
    ae.label = "forge_wear_edge"
    ac = nt.nodes.new("ShaderNodeAttribute")
    ac.attribute_name = "forge_cavity"
    ac.label = "forge_cavity"

    def src(sock):
        return sock.links[0].from_socket if sock.is_linked else None

    bc_in = b.inputs["Base Color"]
    base_src = src(bc_in)
    base_val = tuple(bc_in.default_value)
    worn = tuple(min(1.0, c * 1.35) for c in base_val[:3]) + (1.0,)
    dirty = tuple(c * 0.55 for c in base_val[:3]) + (1.0,)
    mix1 = nt.nodes.new("ShaderNodeMix")
    mix1.data_type = "RGBA"
    mix1.blend_type = "MULTIPLY"
    if base_src is not None:
        nt.links.new(base_src, mix1.inputs[6])
        mix1.inputs[7].default_value = (1.35, 1.35, 1.35, 1.0)
    else:
        mix1.blend_type = "MIX"
        mix1.inputs[6].default_value = base_val
        mix1.inputs[7].default_value = worn
    nt.links.new(ae.outputs["Fac"], mix1.inputs[0])
    mix2 = nt.nodes.new("ShaderNodeMix")
    mix2.data_type = "RGBA"
    mix2.blend_type = "MULTIPLY"
    nt.links.new(mix1.outputs[2], mix2.inputs[6])
    mix2.inputs[7].default_value = (0.55, 0.55, 0.55, 1.0)
    nt.links.new(ac.outputs["Fac"], mix2.inputs[0])
    nt.links.new(mix2.outputs[2], bc_in)

    r_in = b.inputs["Roughness"]
    r_src = src(r_in)
    r_val = r_in.default_value
    m1 = nt.nodes.new("ShaderNodeMapRange")  # worn: roughness * (1 - 0.4 * edge)
    nt.links.new(ae.outputs["Fac"], m1.inputs["Value"])
    m1.inputs["To Min"].default_value = 1.0
    m1.inputs["To Max"].default_value = 0.6
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    if r_src is not None:
        nt.links.new(r_src, mul.inputs[0])
    else:
        mul.inputs[0].default_value = r_val
    nt.links.new(m1.outputs["Result"], mul.inputs[1])
    add = nt.nodes.new("ShaderNodeMath")
    add.operation = "MULTIPLY_ADD"
    nt.links.new(ac.outputs["Fac"], add.inputs[0])
    add.inputs[1].default_value = 0.15
    nt.links.new(mul.outputs[0], add.inputs[2])
    add.use_clamp = True
    nt.links.new(add.outputs[0], r_in)
    return m.name


# ===================================================================== registry + driver
RULES = {
    "turned":   {"form": [form_turned_break_edge],        "surface": [surface_turned_tool_lines]},
    "cast":     {"form": [form_cast_radii_and_draft],     "surface": [surface_cast_parting_line, surface_cast_sand_skin]},
    "forged":   {"form": [form_forged_radii],             "surface": [surface_forged_hammer_facets]},
    "stamped":  {"form": [form_stamped_asymmetric_edges], "surface": [surface_stamped_draw_lines]},
    "machined": {"form": [form_machined_break_edge],      "surface": [surface_machined_grain]},
    "engraved": {"form": [form_engraved_blank],           "surface": [surface_engraved_inset]},
}
WEAR_RULES = [wear_curvature]


def rules_for(process):
    """Names of the rules a process gets, tier by tier."""
    r = RULES[process]
    return {"form": [f.__name__ for f in r["form"]], "surface": [f.__name__ for f in r["surface"]],
            "wear": [f.__name__ for f in WEAR_RULES]}


def _ctx(obj, seed, intensity, mode, budget, params):
    c = {"seed": int(seed), "intensity": float(intensity), "mode": mode, "budget": int(budget)}
    stored = obj.get("forge_detail_ctx")
    if stored:
        c.update({k: v for k, v in dict(stored).items() if k not in c})
    c.update(params)
    return c


def _run_tier(obj, process, tier, ctx):
    if obj.type != "MESH":
        raise TypeError(f"detail needs a MESH, got {obj.type}")
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    tag_process(obj, process)
    _claim_tier(obj, tier)
    fns = WEAR_RULES if tier == "wear" else RULES[process][tier]
    out = []
    for fn in fns:
        t = time.time()
        r = fn(obj, ctx)
        r["seconds"] = round(time.time() - t, 3)
        out.append(r)
    keep = {k: v for k, v in ctx.items() if isinstance(v, (int, float, str))}
    obj["forge_detail_ctx"] = keep
    return out


def apply_form(obj, process, seed=0, intensity=1.0, **params):
    """Tier 1 only. See apply_detail."""
    return _run_tier(obj, process, "form", _ctx(obj, seed, intensity, "auto", 400_000, params))


def apply_surface(obj, process, seed=0, intensity=1.0, mode="auto", budget_verts=400_000, **params):
    """Tier 2 only. Raises DetailOrderError if wear was already applied."""
    return _run_tier(obj, process, "surface", _ctx(obj, seed, intensity, mode, budget_verts, params))


def apply_wear(obj, process, seed=0, intensity=1.0, **params):
    """Tier 3 only."""
    return _run_tier(obj, process, "wear", _ctx(obj, seed, intensity, "auto", 400_000, params))


def apply_detail(obj, process, seed=0, intensity=1.0, tiers=TIERS, mode="auto",
                 budget_verts=400_000, **params):
    """Apply the detail `process` leaves, tier by tier, in the fixed order.

    process     one of PROCESSES; the object is tagged with it.
    seed        drives every stochastic choice (phases, noise offsets): same seed,
                same part.
    intensity   linear scale on every amplitude (1.0 = the physical value).
    tiers       subset of ('form','surface','wear'); always run in that order,
                and refused if a later tier is already on the object.
    mode        surface path: 'auto' | 'geometry' | 'shader'.
    budget_verts  ceiling for surface-tier mesh refinement.
    params      rule parameters: axis ('Z' default: revolve/pull/punch axis),
                direction (machined lay, 'X'), engrave_axis ('+Z'), border_frac,
                feed, nose_radius, parting_offset.
    Returns {"process", "rules", "tiers": {tier: [rule reports]}, "verts_before",
             "verts_after", "seconds"}.
    """
    if process not in PROCESSES:
        raise ValueError(f"unknown process {process!r}; valid: {PROCESSES}")
    order = [t for t in TIERS if t in tiers]
    bad = set(tiers) - set(TIERS)
    if bad:
        raise ValueError(f"unknown tiers {sorted(bad)}")
    t0 = time.time()
    v0 = len(obj.data.vertices)
    ctx = _ctx(obj, seed, intensity, mode, budget_verts, params)
    rep = {"process": process, "rules": rules_for(process), "tiers": {}}
    for tier in order:
        rep["tiers"][tier] = _run_tier(obj, process, tier, ctx)
    rep["verts_before"] = v0
    rep["verts_after"] = len(obj.data.vertices)
    rep["seconds"] = round(time.time() - t0, 3)
    return rep
