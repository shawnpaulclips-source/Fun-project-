"""Procedural mesh construction primitives.

Everything here builds geometry from numbers. That is the whole point: an LLM is
good at trig and bad at sculpting, so detail comes from exact repetition, swept
profiles and lathed silhouettes rather than from a generative model's blob.
"""
import bpy, bmesh, math
from mathutils import Vector, Matrix, Euler

TAU = math.tau
R = math.radians


# --------------------------------------------------------------- object helpers
def new_mesh_object(name, collection=None):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    (collection or bpy.context.scene.collection).objects.link(ob)
    return ob, me


def finish(ob, me, bm, smooth=True):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        shade_smooth(ob)
    return ob


# --------------------------------------------------------------- primitives
def add_box(bm, center, size, rot=None):
    """Axis-aligned (or Euler-rotated) box appended into an existing bmesh."""
    m = Matrix.Translation(Vector(center))
    if rot is not None:
        m = m @ rot.to_matrix().to_4x4()
    m = m @ Matrix.Diagonal((size[0], size[1], size[2], 1.0))
    bmesh.ops.create_cube(bm, size=1.0, matrix=m)


def spin_band(bm, radius, thickness, height, steps=192):
    """A flat band ring: rectangular cross-section revolved about Z."""
    r, t, h = radius, thickness, height
    pts = [(r - t / 2, 0, -h / 2), (r + t / 2, 0, -h / 2),
           (r + t / 2, 0, h / 2), (r - t / 2, 0, h / 2)]
    vs = [bm.verts.new(p) for p in pts]
    es = [bm.edges.new((vs[i], vs[(i + 1) % 4])) for i in range(4)]
    bmesh.ops.spin(bm, geom=vs + es, cent=(0, 0, 0), axis=(0, 0, 1),
                   dvec=(0, 0, 0), angle=TAU, steps=steps, use_merge=True)


def lathe(name, profile, steps=96, close=False, smooth=True):
    """Revolve a 2D (radius, z) profile about Z. The workhorse for anything turned:
    pedestals, finials, bottles, columns, pipe fittings."""
    ob, me = new_mesh_object(name)
    bm = bmesh.new()
    vs = [bm.verts.new((p[0], 0.0, p[1])) for p in profile]
    es = [bm.edges.new((vs[i], vs[i + 1])) for i in range(len(vs) - 1)]
    if close:
        es.append(bm.edges.new((vs[-1], vs[0])))
    bmesh.ops.spin(bm, geom=vs + es, cent=(0, 0, 0), axis=(0, 0, 1),
                   dvec=(0, 0, 0), angle=TAU, steps=steps, use_merge=True)
    return finish(ob, me, bm, smooth)


def bezier_xz(p0, p1, p2, p3, t):
    u = 1 - t
    return (u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
            u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1])


def loft_bracket(bm, p0, p1, p2, p3, segments=26,
                 width_start=0.10, width_end=0.07,
                 depth_start=0.055, depth_end=0.04):
    """Sweep a tapering rectangular section along a cubic Bezier in the XZ plane.

    Use this for anything that curves and tapers: support arms, handles, horns,
    ribs, chassis members. A stack of boxes along the same path reads as visible
    stair-stepping in any render; a swept section does not.
    """
    rings = []
    n = max(3, segments)
    for i in range(n):
        t = i / (n - 1)
        x, z = bezier_xz(p0, p1, p2, p3, t)
        e = 1e-3
        x2, z2 = bezier_xz(p0, p1, p2, p3, min(1.0, t + e))
        x1, z1 = bezier_xz(p0, p1, p2, p3, max(0.0, t - e))
        tan = Vector((x2 - x1, 0.0, z2 - z1)).normalized()
        nrm = Vector((-tan.z, 0.0, tan.x))
        up = Vector((0.0, 1.0, 0.0))
        w = width_start + (width_end - width_start) * t
        d = depth_start + (depth_end - depth_start) * t
        c = Vector((x, 0.0, z))
        quad = [c + up * (w/2) + nrm * (d/2), c - up * (w/2) + nrm * (d/2),
                c - up * (w/2) - nrm * (d/2), c + up * (w/2) - nrm * (d/2)]
        rings.append([bm.verts.new(q) for q in quad])
    for i in range(len(rings) - 1):
        a, b = rings[i], rings[i + 1]
        for j in range(4):
            k = (j + 1) % 4
            bm.faces.new((a[j], a[k], b[k], b[j]))
    bm.faces.new(rings[0][::-1])
    bm.faces.new(rings[-1])


def radial_ticks(bm, radius, count, every=5,
                 major_size=(0.012, 0.010, 0.055),
                 minor_scale=(0.65, 0.60, 0.55),
                 inset=0.0015):
    """Graduation marks around a ring. Real geometry, not a texture.

    This is the single highest-value detail trick available to a code generator:
    hundreds of exactly-placed bevelled marks that catch a rim light. No
    image-generation model produces this correctly.
    """
    for i in range(count):
        a = TAU * i / count
        major = (i % every == 0)
        sx, sy, sz = major_size
        if not major:
            sx, sy, sz = sx * minor_scale[0], sy * minor_scale[1], sz * minor_scale[2]
        ctr = (math.cos(a) * (radius + sx / 2 - inset),
               math.sin(a) * (radius + sx / 2 - inset), 0.0)
        add_box(bm, ctr, (sx, sy, sz), Euler((0, 0, a), 'XYZ'))


def graduated_ring(name, radius, thickness, height,
                   divisions=180, every=5, steps=224,
                   major_size=None, smooth=True):
    """A band ring carrying graduation marks. The armillary's signature part."""
    ob, me = new_mesh_object(name)
    bm = bmesh.new()
    spin_band(bm, radius, thickness, height, steps)
    radial_ticks(bm, radius + thickness / 2, divisions, every,
                 major_size=major_size or (0.012, 0.010, height * 0.37))
    return finish(ob, me, bm, smooth)


def plain_ring(name, radius, thickness, height, steps=160, smooth=True):
    ob, me = new_mesh_object(name)
    bm = bmesh.new()
    spin_band(bm, radius, thickness, height, steps)
    return finish(ob, me, bm, smooth)


# --------------------------------------------------------------- finishing
def bevel(ob, width=0.0035, segments=2, angle_deg=34, harden=True):
    """Bevel every edge sharper than angle_deg.

    Non-negotiable for hard surface. A perfectly sharp edge receives zero
    specular highlight and reads as CG. The bevel is what makes brass look like
    brass. Keep it small: 2-4mm at metre scale.
    """
    m = ob.modifiers.new('bevel', 'BEVEL')
    m.width = width
    m.segments = segments
    m.limit_method = 'ANGLE'
    m.angle_limit = R(angle_deg)
    m.harden_normals = harden
    return m


def shade_smooth(ob, keep_sharp=True):
    """Smooth shading + weighted normals.

    NOTE: `mesh.use_auto_smooth` was REMOVED in Blender 4.1+. Any code that sets
    it raises AttributeError on 5.x. Weighted Normals + bevel harden_normals is
    the replacement.
    """
    for p in ob.data.polygons:
        p.use_smooth = True
    if not any(m.type == 'WEIGHTED_NORMAL' for m in ob.modifiers):
        m = ob.modifiers.new('weighted_normal', 'WEIGHTED_NORMAL')
        m.keep_sharp = keep_sharp


def subdivide(ob, viewport=2, render=3):
    m = ob.modifiers.new('subsurf', 'SUBSURF')
    m.levels = viewport
    m.render_levels = render
    return m


def solidify(ob, thickness=0.01):
    m = ob.modifiers.new('solidify', 'SOLIDIFY')
    m.thickness = thickness
    return m


def array_radial(ob, count, axis='Z'):
    """Radial array via an empty. Cheaper than duplicating geometry in bmesh
    when the instance is heavy."""
    empty = bpy.data.objects.new(f'{ob.name}_pivot', None)
    bpy.context.scene.collection.objects.link(empty)
    m = ob.modifiers.new('array', 'ARRAY')
    m.use_relative_offset = False
    m.use_object_offset = True
    m.offset_object = empty
    m.count = count
    setattr(empty.rotation_euler, axis.lower(), TAU / count)
    return m, empty
