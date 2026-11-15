"""Low-level bpy helpers. Blender 5.3 / bpy module build.

Everything is built with bmesh.ops + from-scratch object creation. No bpy.ops
mesh operators: in a headless `import bpy` build the operator context is a
minefield and bmesh is context-free.
"""
import math
import os

import bpy  # must be first: bpy installs the path that exposes addon_utils
import addon_utils
import bmesh
from mathutils import Euler, Matrix, Vector

TAU = math.pi * 2.0

# ---------------------------------------------------------------- scene boot

def boot(fps=24, res=(854, 480)):
    """Wipe the default scene and return it configured."""
    addon_utils.enable("cycles", default_set=True, persistent=True)

    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.curves,
                bpy.data.lights, bpy.data.cameras, bpy.data.actions):
        for d in list(blk):
            blk.remove(d, do_unlink=True)

    sc = bpy.context.scene
    sc.render.fps = fps
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    vs = sc.view_settings
    for attr, val in (("view_transform", "NONE"), ("look", "None"),
                      ("exposure", 0.0), ("gamma", 1.0)):
        try:
            setattr(vs, attr, val)
        except Exception:
            pass
    return sc


def collection():
    return bpy.context.scene.collection


def link(ob):
    collection().objects.link(ob)
    return ob


# ---------------------------------------------------------------- materials

_MATS = {}


def mat(name, color, rough=0.55, metal=0.0, emit=0.0, spec=0.3):
    """Cached Principled material. `color` is an RGB 0..1 triple."""
    if name in _MATS:
        return _MATS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        for n in nt.nodes:
            if n.type == "BSDF_PRINCIPLED":
                bsdf = n
                break
    rgba = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    # 5.x renamed / moved a few sockets; set defensively.
    for key, val in (("Specular IOR Level", spec), ("Specular", spec),
                     ("Sheen Weight", 0.0), ("Coat Weight", 0.0)):
        if key in bsdf.inputs:
            try:
                bsdf.inputs[key].default_value = val
            except Exception:
                pass
    if emit > 0.0:
        for key in ("Emission Color", "Emission"):
            if key in bsdf.inputs:
                try:
                    bsdf.inputs[key].default_value = rgba
                except Exception:
                    pass
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = emit
    m.diffuse_color = rgba
    _MATS[name] = m
    return m


# ---------------------------------------------------------------- geometry

def _finish(name, bm, material, shade_smooth=False):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if shade_smooth:
        for p in me.polygons:
            p.use_smooth = True
    if material is not None:
        me.materials.append(material)
    ob = bpy.data.objects.new(name, me)
    return link(ob)


def box(name, size=(1, 1, 1), loc=(0, 0, 0), material=None, bevel=0.0,
        rot=(0, 0, 0)):
    bm = bmesh.new()
    # create_cube(size=1) spans -0.5..0.5, so `size` here is HALF-extents.
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size) * 2.0, verts=bm.verts)
    if bevel > 0.0:
        bmesh.ops.bevel(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
                        offset=bevel, segments=2, affect="EDGES",
                        offset_type="OFFSET", profile=0.5, clamp_overlap=True)
    ob = _finish(name, bm, material)
    ob.location = loc
    ob.rotation_euler = rot
    return ob


def sphere(name, r=0.5, loc=(0, 0, 0), material=None, segs=16, rings=9,
           scale=(1, 1, 1), smooth=True):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=r)
    if scale != (1, 1, 1):
        bmesh.ops.scale(bm, vec=Vector(scale), verts=bm.verts)
    ob = _finish(name, bm, material, shade_smooth=smooth)
    ob.location = loc
    return ob


def cyl(name, r=0.5, depth=1.0, loc=(0, 0, 0), material=None, segs=16,
        axis="Z", r2=None, smooth=True, rot=(0, 0, 0)):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs,
                          radius1=r, radius2=(r if r2 is None else r2),
                          depth=depth)
    if axis == "X":
        bmesh.ops.rotate(bm, verts=bm.verts,
                         matrix=Matrix.Rotation(math.pi / 2, 3, "Y"))
    elif axis == "Y":
        bmesh.ops.rotate(bm, verts=bm.verts,
                         matrix=Matrix.Rotation(math.pi / 2, 3, "X"))
    ob = _finish(name, bm, material, shade_smooth=smooth)
    ob.location = loc
    ob.rotation_euler = rot
    return ob


def ring(name, major=0.5, minor=0.1, loc=(0, 0, 0), material=None,
         major_segs=20, minor_segs=8, axis="Z", scale=(1, 1, 1)):
    """Torus built by hand - bmesh has no create_torus in 5.3."""
    bm = bmesh.new()
    grid = []
    for i in range(major_segs):
        a = TAU * i / major_segs
        cx, cy = math.cos(a) * major, math.sin(a) * major
        rowverts = []
        for j in range(minor_segs):
            b = TAU * j / minor_segs
            rr = major + math.cos(b) * minor
            rowverts.append(bm.verts.new((math.cos(a) * rr, math.sin(a) * rr,
                                          math.sin(b) * minor)))
        grid.append(rowverts)
    for i in range(major_segs):
        ni = (i + 1) % major_segs
        for j in range(minor_segs):
            nj = (j + 1) % minor_segs
            bm.faces.new((grid[i][j], grid[ni][j], grid[ni][nj], grid[i][nj]))
    bm.normal_update()
    if axis == "X":
        bmesh.ops.rotate(bm, verts=bm.verts,
                         matrix=Matrix.Rotation(math.pi / 2, 3, "Y"))
    elif axis == "Y":
        bmesh.ops.rotate(bm, verts=bm.verts,
                         matrix=Matrix.Rotation(math.pi / 2, 3, "X"))
    if scale != (1, 1, 1):
        bmesh.ops.scale(bm, vec=Vector(scale), verts=bm.verts)
    ob = _finish(name, bm, material, shade_smooth=True)
    ob.location = loc
    return ob


def plane(name, sx=1.0, sy=1.0, loc=(0, 0, 0), material=None, rot=(0, 0, 0)):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(-sx, -sy, 0), (sx, -sy, 0), (sx, sy, 0), (-sx, sy, 0)],
                   [], [(0, 1, 2, 3)])
    me.update()
    if material is not None:
        me.materials.append(material)
    ob = link(bpy.data.objects.new(name, me))
    ob.location = loc
    ob.rotation_euler = rot
    return ob


# ---------------------------------------------------------------- hierarchy

def joint(name, loc=(0, 0, 0), parent=None):
    """An empty used as a rig joint. Forward-kinematic chains of these are the
    rig: cheaper and far more predictable headless than bones + skinning."""
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = "SPHERE"
    e.empty_display_size = 0.06
    link(e)
    e.location = loc
    if parent is not None:
        e.parent = parent
    e.rotation_mode = "XYZ"
    return e


def attach(ob, parent):
    """Parent keeping the current local transform as the offset."""
    ob.parent = parent
    ob.matrix_parent_inverse = parent.matrix_world.inverted()
    return ob


def parent_keep_local(ob, parent):
    """Parent treating ob.location as already parent-relative."""
    ob.parent = parent
    return ob


# ---------------------------------------------------------------- keyframes

def key(ob, frame, loc=None, rot=None, scale=None, interp="BEZIER"):
    paths = []
    if loc is not None:
        ob.location = loc
        paths.append("location")
    if rot is not None:
        ob.rotation_euler = rot
        paths.append("rotation_euler")
    if scale is not None:
        ob.scale = scale
        paths.append("scale")
    for p in paths:
        ob.keyframe_insert(data_path=p, frame=frame)
    if interp != "BEZIER":
        set_interp(ob, interp, only_frame=frame)


def set_interp(ob, interp="LINEAR", only_frame=None, easing=None):
    ad = ob.animation_data
    if ad is None or ad.action is None:
        return
    for fc in _fcurves(ad.action):
        for kp in fc.keyframe_points:
            if only_frame is None or abs(kp.co[0] - only_frame) < 1e-4:
                kp.interpolation = interp
                if easing:
                    kp.easing = easing


def _fcurves(action):
    """5.x moved fcurves into action slots/layers; fall back cleanly."""
    fcs = getattr(action, "fcurves", None)
    if fcs is not None and len(fcs):
        return list(fcs)
    out = []
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for cb in getattr(strip, "channelbags", []):
                out.extend(cb.fcurves)
    return out


def clean_all_fcurves(interp="LINEAR"):
    for ob in bpy.data.objects:
        if ob.animation_data and ob.animation_data.action:
            for fc in _fcurves(ob.animation_data.action):
                for kp in fc.keyframe_points:
                    kp.interpolation = interp


# ---------------------------------------------------------------- render

def set_cycles(samples=24, bounces=3, denoise=True):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    c = sc.cycles
    c.device = "CPU"
    c.samples = samples
    c.preview_samples = 8
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.05
    c.max_bounces = bounces
    c.diffuse_bounces = bounces
    c.glossy_bounces = 1
    c.transmission_bounces = 1
    c.volume_bounces = 0
    c.transparent_max_bounces = 2
    c.use_fast_gi = True
    c.caustics_reflective = False
    c.caustics_refractive = False
    c.use_auto_tile = True
    c.tile_size = 256
    c.use_light_tree = False
    if denoise:
        try:
            c.use_denoising = True
            c.denoising_input_passes = "RGB_ALBEDO_NORMAL"
        except Exception:
            c.use_denoising = False
    else:
        c.use_denoising = False
    return c


def set_eevee(samples=8):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    ee = sc.eevee
    for attr, val in (("taa_render_samples", samples), ("taa_samples", samples),
                      ("use_raytracing", False), ("use_shadows", True)):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except Exception:
                pass
    return ee


def png_out(path):
    sc = bpy.context.scene
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.render.image_settings.compression = 15
    sc.render.filepath = path
    return sc


def render_still(path):
    png_out(path)
    bpy.ops.render.render(write_still=True)


def poly_count():
    tris = verts = 0
    dg = bpy.context.evaluated_depsgraph_get()
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        me = ob.evaluated_get(dg).to_mesh()
        me.calc_loop_triangles()
        tris += len(me.loop_triangles)
        verts += len(me.vertices)
        ob.evaluated_get(dg).to_mesh_clear()
    return verts, tris
