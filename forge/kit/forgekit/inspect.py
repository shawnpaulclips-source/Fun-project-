"""Numeric introspection: how an agent 'sees' without rendering.

Renders cost seconds and tokens. Numbers cost microseconds and ~50 tokens.
Check the numbers first; render only to confirm or to judge aesthetics.
"""
import bpy, bmesh, math
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def mesh_stats(ob, evaluated=True):
    """Topology health for one mesh. Everything here is a blind-check metric."""
    if evaluated:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
    else:
        me = ob.data
        ev = None

    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    loose_verts = sum(1 for v in bm.verts if not v.link_edges)
    degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-9)
    ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
    tris = sum(1 for f in bm.faces if len(f.verts) == 3)
    quads = sum(1 for f in bm.faces if len(f.verts) == 4)
    flipped = sum(1 for f in bm.faces if f.normal.length < 1e-9)

    V, E, F = len(bm.verts), len(bm.edges), len(bm.faces)
    euler = V - E + F
    watertight = (non_manifold == 0 and boundary == 0)
    volume = bm.calc_volume(signed=True) if watertight else 0.0
    area = sum(f.calc_area() for f in bm.faces)

    bb = [Vector(c) for c in ob.bound_box]
    dims = tuple(round(d, 5) for d in ob.dimensions)

    bm.free()
    if ev is not None:
        ev.to_mesh_clear()

    return {
        "name": ob.name,
        "verts": V, "edges": E, "faces": F,
        "tris": tris, "quads": quads, "ngons": ngons,
        "euler": euler,
        "genus": (2 - euler) // 2 if watertight else None,
        "watertight": watertight,
        "non_manifold_edges": non_manifold,
        "boundary_edges": boundary,
        "loose_verts": loose_verts,
        "degenerate_faces": degenerate,
        "zero_normals": flipped,
        "volume_m3": round(volume, 8),
        "area_m2": round(area, 6),
        "dimensions_m": dims,
    }


def scene_summary(max_objects=60):
    """Token-cheap scene graph. NEVER dump geometry back to an agent."""
    sc = bpy.context.scene
    objs = []
    for o in list(sc.objects)[:max_objects]:
        entry = {
            "name": o.name, "type": o.type,
            "loc": tuple(round(v, 4) for v in o.location),
            "dims": tuple(round(v, 4) for v in o.dimensions),
        }
        if o.type == "MESH":
            entry["faces"] = len(o.data.polygons)
            entry["modifiers"] = [m.type for m in o.modifiers]
            entry["materials"] = [m.name for m in o.data.materials if m]
        if o.type == "ARMATURE":
            entry["bones"] = len(o.data.bones)
        objs.append(entry)
    return {
        "engine": sc.render.engine,
        "frame_range": (sc.frame_start, sc.frame_end),
        "object_count": len(sc.objects),
        "objects": objs,
        "truncated": len(sc.objects) > max_objects,
    }


def scene_bounds(objects=None):
    """World-space bounding box of everything. Feed the height to
    render.frame_distance() instead of guessing camera distance."""
    objs = objects or [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objs:
        return None
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector((min(lo[i], w[i]) for i in range(3)))
            hi = Vector((max(hi[i], w[i]) for i in range(3)))
    return {
        "min": tuple(round(v, 4) for v in lo),
        "max": tuple(round(v, 4) for v in hi),
        "size": tuple(round(hi[i] - lo[i], 4) for i in range(3)),
        "center": tuple(round((hi[i] + lo[i]) / 2, 4) for i in range(3)),
        "height": round(hi.z - lo.z, 4),
    }


def self_intersections(ob):
    """Count self-intersecting face pairs. Monotone with joint bend angle, so
    it is the blind metric for 'is this pose breaking the mesh'."""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.transform(ob.matrix_world)
    tree = BVHTree.FromBMesh(bm, epsilon=0.0)
    pairs = tree.overlap(tree)
    n = len(pairs)
    bm.free()
    ev.to_mesh_clear()
    return n


def symmetry_error(ob, axis=0, samples=4000):
    """Mean distance from each vertex to its mirrored counterpart.

    Near zero for a symmetric asset. A large value on something that should be
    symmetric means a modelling mistake no render angle will reveal.
    """
    import mathutils
    me = ob.data
    verts = [ob.matrix_world @ v.co for v in me.vertices]
    if not verts:
        return None
    step = max(1, len(verts) // samples)
    sample = verts[::step]
    size = max((max(v[i] for v in verts) - min(v[i] for v in verts)) for i in range(3))
    kd = mathutils.kdtree.KDTree(len(verts))
    for i, v in enumerate(verts):
        kd.insert(v, i)
    kd.balance()
    total = 0.0
    for v in sample:
        m = v.copy()
        m[axis] = -m[axis]
        _, _, d = kd.find(m)
        total += d
    return round(total / len(sample) / max(size, 1e-9), 6)


def report(objects=None, verbose=True):
    """Run the full suite and print a compact table. Returns list of dicts."""
    objs = objects or [o for o in bpy.context.scene.objects if o.type == "MESH"]
    rows = [mesh_stats(o) for o in objs]
    if verbose:
        bad = 0
        print(f"{'object':22} {'faces':>7} {'nonman':>7} {'bound':>6} {'degen':>6} {'ngon':>5} {'wt':>3}")
        for r in rows:
            flag = r["non_manifold_edges"] or r["degenerate_faces"] or r["loose_verts"]
            bad += 1 if flag else 0
            print(f"{r['name'][:22]:22} {r['faces']:7d} {r['non_manifold_edges']:7d} "
                  f"{r['boundary_edges']:6d} {r['degenerate_faces']:6d} {r['ngons']:5d} "
                  f"{'Y' if r['watertight'] else 'n':>3}")
        print(f"-- {len(rows)} meshes, {sum(r['faces'] for r in rows)} faces, {bad} flagged")
    return rows
