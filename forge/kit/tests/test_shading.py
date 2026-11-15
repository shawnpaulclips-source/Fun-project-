"""Look-dev layer: lights, contact, value structure, palette, look_compare,
plus the materials extensions (metal_pbr, brushed Mapping, cavity_patina) and
the metal before/after render.

Run:  /home/user/bpyenv/bin/python tests/test_shading.py [--no-hero]
Prints PASS/FAIL per check and timings; exits 1 on any failure.
Writes renders/look_metal_before_after.png (unless --no-hero).
"""
import json
import math
import os
import sys
import tempfile
import time

import numpy as np

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from forgekit import env  # noqa: E402

env.prepare()
import bpy  # noqa: E402
import bmesh  # noqa: E402
from mathutils import Vector  # noqa: E402
from forgekit import materials as MAT, shading as SH, render as RN  # noqa: E402

TMP = tempfile.mkdtemp(prefix="forge_test_shading_")
FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def sphere(name="Ball", r=0.5, loc=(0, 0, 0.5), seg=64):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=seg // 2, radius=r)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    return ob


def box(name, size, loc):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    return ob


def plane(name="Ground", size=6.0, z=0.0, subdiv=0):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1 + subdiv, y_segments=1 + subdiv, size=size / 2)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location.z = z
    return ob


def top_camera(sc, ortho_scale, z=5.0, xy=(0, 0)):
    cd = bpy.data.cameras.new("Top")
    cd.type = "ORTHO"
    cd.ortho_scale = ortho_scale
    cam = bpy.data.objects.new("Top", cd)
    sc.collection.objects.link(cam)
    cam.location = (xy[0], xy[1], z)
    cam.rotation_euler = (0, 0, 0)
    sc.camera = cam
    return cam


def raw_render(sc, path, res=128, spp=8):
    RN.configure("CYCLES", samples=spp, resolution=(res, res), denoise=False)
    sc.cycles.use_adaptive_sampling = False
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.render.image_settings.color_depth = "16"
    return RN.shot(path, verbose=False)


def emit_socket(mat, socket):
    """Route a socket straight to the output as emission (to read a mask)."""
    nt = mat.node_tree
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(socket, em.inputs["Color"])
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    nt.links.new(em.outputs[0], out.inputs["Surface"])


# ------------------------------------------------------------- lights
def test_lights():
    res = {}
    for size in (0.2, 2.0, 20.0):
        reset()
        r = size / 2
        sphere(r=r, loc=(3.0, -1.0, r))
        info = SH.light_rig_from_scene_bounds()
        ctr = Vector(info["center"])
        aim = min((ctr - o.location).normalized().dot(
            o.matrix_world.to_3x3() @ Vector((0, 0, -1)))
            for o in bpy.context.scene.objects if o.type == "LIGHT")
        key = next(o for o in bpy.context.scene.objects if o.name.startswith("Key"))
        irr = key.data.energy / (key.location - ctr).length ** 2
        res[size] = (irr, info, aim)
    irrs = [v[0] for v in res.values()]
    spread = max(irrs) / min(irrs) - 1
    check("lights: key irradiance at the subject constant across 100x size", spread < 1e-6,
          f"{', '.join(f'{k} m: {v[0]:.1f} W/m^2' for k, v in res.items())}")
    check("lights: every light aimed at the bbox centre", min(v[2] for v in res.values()) > 0.9999)
    i = res[2.0][1]
    check("lights: lighting ratios reported", i["key_fill_ratio"] > 1.0,
          f"key:fill {i['key_fill_ratio']}, key:rim {i['key_rim_ratio']}")


# ------------------------------------------------------------- contact
def test_contact():
    reset()
    c = box("Crate", (1, 1, 1), (0, 0, 0.5))
    r = SH.contact_shadow_check(c, 0.0)
    check("contact: resting cube", r["status"] == "contact" and r["contact_verts"] == 4, json.dumps(r))
    c.location.z = 0.52
    r = SH.contact_shadow_check(c, 0.0)
    check("contact: floating cube detected", r["status"] == "floating" and abs(r["gap_m"] - 0.02) < 1e-6
          and abs(r["fix_dz"] + 0.02) < 1e-6, f"gap {r['gap_m']}, fix_dz {r['fix_dz']}")
    c.location.z = 0.45
    r = SH.contact_shadow_check(c, 0.0)
    check("contact: sunk cube detected", r["status"] == "intersecting" and abs(r["penetration_m"] - 0.05) < 1e-6)
    c.location.z = 0.5
    m = c.modifiers.new("b", "BEVEL")
    m.width = 0.05
    m.segments = 3
    r = SH.contact_shadow_check(c, 0.0)
    check("contact: evaluated mesh (bevel modifier) used", r["status"] == "contact"
          and abs(r["footprint_m"][0] - 0.9) < 1e-4, f"footprint {r['footprint_m']} (bevel shrinks it to 0.9)")


# ------------------------------------------------------------- value structure
def write_png(path, arr):
    from PIL import Image
    Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).save(path)
    return path


def test_value_synthetic():
    flat = write_png(os.path.join(TMP, "flat.png"), np.full((64, 64, 3), 0.5))
    yy = np.linspace(0, 1, 64)[:, None].repeat(64, 1)
    grad = write_png(os.path.join(TMP, "grad.png"), np.dstack([yy] * 3))
    vf, vg = SH.value_structure(flat), SH.value_structure(grad)
    check("value: uniform grey reads flat", vf["verdict"] == "flat" and vf["mid"] == 1.0,
          f"mid {vf['mid']}, contrast {vf['contrast']}")
    check("value: full gradient reads as form", vg["verdict"] == "form",
          f"shadow/mid/high {vg['shadow']}/{vg['mid']}/{vg['highlight']}, contrast {vg['contrast']}")
    rgba = np.zeros((32, 32, 4))
    rgba[8:24, 8:24] = (1, 1, 1, 1)
    from PIL import Image
    p = os.path.join(TMP, "alpha.png")
    Image.fromarray((rgba * 255).astype(np.uint8), "RGBA").save(p)
    va = SH.value_structure(p, background="alpha")
    check("value: alpha background mask", va["pixels"] == 256 and va["highlight"] == 1.0)


def lit_and_flat_renders():
    """The same grey sphere: uniform white world only (flat) vs the light rig."""
    out = {}
    for mode in ("flat", "lit"):
        sc = reset()
        s = sphere(r=0.5, loc=(0, 0, 0.5))
        MAT.assign(s, MAT.dielectric("Grey", (0.5, 0.5, 0.5), roughness=0.5))
        if mode == "flat":
            RN.setup_world((1.0, 1.0, 1.0), 1.0)
        else:
            RN.setup_world((0.02, 0.02, 0.025), 1.0)
            SH.light_rig_from_scene_bounds()
        RN.camera(lens=80, target=(0, 0, 0.5), location=(0, -RN.frame_distance(1.0), 0.5))
        RN.configure("CYCLES", samples=16, resolution=(96, 96), denoise=False)
        out[mode], dt = RN.shot(os.path.join(TMP, f"sphere_{mode}"), verbose=False)
        out[mode + "_s"] = dt
    return out


def test_value_renders(r):
    vf = SH.value_structure(r["flat"], background="corners")
    vl = SH.value_structure(r["lit"], background="corners")
    check("value: world-only sphere reads flat", vf["verdict"] == "flat",
          f"contrast {vf['contrast']}, mid {vf['mid']} ({r['flat_s']:.2f} s render)")
    check("value: three-point sphere reads as form", vl["verdict"] in ("form", "harsh")
          and vl["contrast"] > vf["contrast"] + 0.2,
          f"contrast {vl['contrast']}, s/m/h {vl['shadow']}/{vl['mid']}/{vl['highlight']} "
          f"({r['lit_s']:.2f} s render)")
    lc = SH.look_compare(r["lit"], r["flat"], background="corners")
    json.dumps(lc)
    check("look_compare: flat candidate called flatter vs lit reference",
          any("flatter" in v for v in lc["verdict"]) and lc["delta"]["contrast"] < 0,
          f"{lc['verdict']} scores {lc['scores']}")
    same = SH.look_compare(r["lit"], r["lit"])
    check("look_compare: identical renders match 1.0, plain dict",
          same["scores"]["match"] == 1.0 and isinstance(same, dict), f"{same['scores']}")


# ------------------------------------------------------------- palette
def test_palette():
    reset()
    ob = sphere()
    bad = MAT.new_material("GreyMetal")
    b = bad.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.3, 0.3, 0.3, 1)
    b.inputs["Metallic"].default_value = 1.0
    good = MAT.metal_pbr("Gold", "gold", anisotropy=0.7)
    neon = MAT.dielectric("Neon", (1.0, 0.0, 0.02))
    half = MAT.dielectric("Half", (0.4, 0.3, 0.2))
    half.node_tree.nodes["Principled BSDF"].inputs["Metallic"].default_value = 0.5
    worn = MAT.wear_edges(MAT.metal("Worn", "copper"))
    for m in (bad, good, neon, half, worn):
        ob.data.materials.append(m)
    rep = SH.colour_palette_check(ob)
    by = {r["material"]: r for r in rep["materials"]}
    check("palette: grey metal flagged", "DIELECTRIC_GREY_METAL" in by["GreyMetal"]["flags"],
          f"Y {by['GreyMetal']['luminance']}")
    check("palette: METAL_F0 gold clean, matched", not by["Gold"]["flags"]
          and by["Gold"]["nearest_metal"] == "gold", f"{by['Gold']}")
    check("palette: neon dielectric flagged", "OVERSATURATED" in by["Neon"]["flags"])
    check("palette: partial metallic flagged", "PARTIAL_METALLIC" in by["Half"]["flags"])
    check("palette: colour traced through wear_edges Mix", by["Worn"].get("base_colour") is not None
          and not by["Worn"]["flags"], f"{by['Worn']['colour_source']}")
    dark = [k for k, v in MAT.PALETTE.items()
            if SH._rgb_stats(v)[1] < 0.45]
    print(f"      kit PALETTE entries a metal check would flag (Y < 0.45): {dark}")


# ------------------------------------------------------------- materials
def test_metal_pbr_report():
    reset()
    m, rep = MAT.metal_pbr("Full", "brass", anisotropy=0.8, anisotropic_rotation=0.25, coat=1.0,
                           coat_roughness=0.04, thin_film_thickness=450, thin_film_ior=1.6,
                           noise_stretch=40, return_report=True)
    want = {"Anisotropic", "Anisotropic Rotation", "Coat Weight", "Coat Roughness",
            "Thin Film Thickness", "Thin Film IOR"}
    check("metal_pbr: every 5.3 input applied, none missing", want <= set(rep["applied"])
          and not rep["missing"], f"applied {rep['applied']}")
    b = m.node_tree.nodes["Principled BSDF"]
    vals = (b.inputs["Anisotropic"].default_value, b.inputs["Coat Weight"].default_value,
            b.inputs["Thin Film Thickness"].default_value)
    check("metal_pbr: values landed", abs(vals[0] - 0.8) < 1e-6 and vals[1] == 1.0
          and abs(vals[2] - 450) < 1e-3, f"{vals}")
    mp = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeMapping")
    check("metal_pbr: Mapping scale stretches across the brush axis",
          tuple(mp.inputs["Scale"].default_value) == (1.0, 40.0, 40.0))
    check("materials: METAL_F0 keeps PALETTE intact",
          MAT.PALETTE["copper"] == (0.926, 0.721, 0.504) and MAT.METAL_F0["copper"] == (0.955, 0.638, 0.538))
    old = MAT.metal("Old", "brass", roughness=0.22)
    check("materials: metal() signature still works", old.node_tree.nodes["Principled BSDF"]
          .inputs["Metallic"].default_value == 1.0)


def grain_anisotropy(stretch):
    """Render metal_pbr's breakup noise (through its Mapping) as emission on a
    top-down plane; return mean |d/dy| / mean |d/dx|. Streaks along X -> >> 1."""
    sc = reset()
    p = plane(size=1.0)
    m = MAT.metal_pbr("Grain", "brass", noise_stretch=stretch, breakup_scale=10.0)
    noise = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeTexNoise")
    noise.inputs["Detail"].default_value = 1.0   # high octaves alias at 128 px
    emit_socket(m, noise.outputs["Fac"])
    MAT.assign(p, m)
    top_camera(sc, 1.0)
    path, dt = raw_render(sc, os.path.join(TMP, f"grain_{stretch}"), res=128, spp=4)
    y = SH.load_rgba(path)[..., :3] @ SH._LUMA
    y = y - y.mean()

    def corr_len(a, axis):
        # first lag where the mean normalised autocorrelation drops below 0.5
        n = a.shape[axis]
        for k in range(1, n // 2):
            x0 = np.take(a, range(0, n - k), axis=axis)
            x1 = np.take(a, range(k, n), axis=axis)
            if (x0 * x1).mean() / max((a * a).mean(), 1e-12) < 0.5:
                return k
        return n // 2
    return corr_len(y, 1) / corr_len(y, 0), dt, path


def test_brushed_stretch():
    iso, t1, _ = grain_anisotropy(1.0)
    brushed, t2, _ = grain_anisotropy(8.0)
    check("brushed: noise_stretch=1 grain is isotropic", 0.7 < iso < 1.4,
          f"correlation length x/y {iso:.2f}")
    check("brushed: noise_stretch=8 grain streaks along X", brushed > 4.0,
          f"correlation length x/y {brushed:.1f} ({t1:.2f}+{t2:.2f} s renders)")


def test_cavity_patina():
    """Cube on a plane, AO mask rendered top-down: the crevice ring around the
    cube base must light up, open floor and the cube top must not."""
    sc = reset()
    g = plane(size=2.0, subdiv=40)
    c = box("Block", (0.5, 0.5, 0.5), (0, 0, 0.25))
    m = MAT.metal_pbr("Patina", "copper")
    MAT.assign(g, m)
    MAT.assign(c, m)
    patched = SH.colour_palette_check(g)  # sanity: still a clean metal before patina
    out = MAT.cavity_patina(g, amount=1.0, distance=0.15, mode="AO")
    mask = m.node_tree.nodes[m["forge_cavity_mask"]]
    emit_socket(m, mask.outputs[0])
    top_camera(sc, 2.0)
    path, dt = raw_render(sc, os.path.join(TMP, "cavity"), res=128, spp=16)
    y = SH.load_rgba(path)[..., :3] @ SH._LUMA
    px = lambda x, yv: y[int((1 - (yv + 1) / 2) * 127), int((x + 1) / 2 * 127)]
    crevice = np.mean([px(0.28, 0.0), px(-0.28, 0.0), px(0.0, 0.28), px(0.0, -0.28)])
    open_floor = np.mean([px(0.9, 0.9), px(-0.9, -0.9), px(0.9, -0.9)])
    top = px(0.0, 0.0)
    check("cavity_patina: mask bright in the crevice, dark on open surfaces",
          crevice > 0.3 and open_floor < 0.05 and top < 0.05,
          f"crevice {crevice:.2f}, open floor {open_floor:.3f}, cube top {top:.3f} ({dt:.2f} s)")
    m2 = MAT.metal_pbr("P2", "brass")
    MAT.cavity_patina(m2, mode="BOTH")
    types = {n.bl_idname for n in m2.node_tree.nodes}
    check("cavity_patina: BOTH builds AO + Pointiness nodes and keeps the roughness breakup",
          {"ShaderNodeAmbientOcclusion", "ShaderNodeNewGeometry"} <= types
          and any(l.to_node.bl_idname == "ShaderNodeMix" and l.from_node.bl_idname == "ShaderNodeMapRange"
                  for l in m2.node_tree.links), f"{len(out)} material(s) patched")


# ------------------------------------------------------------- hero
def hero_before_after(out_path):
    """Same sphere, same light, metal() vs metal_pbr(anisotropy + coat).
    Cycles 256 px, 32 spp each, composited side by side."""
    from PIL import Image, ImageDraw
    shots, times = [], []
    for label in ("before", "after"):
        sc = reset()
        s = sphere(r=0.5, loc=(0, 0, 0.5), seg=128)
        sub = s.modifiers.new("sub", "SUBSURF")
        sub.levels = sub.render_levels = 1
        g = plane(size=8.0)
        MAT.assign(g, MAT.dielectric("Floor", (0.08, 0.08, 0.085), roughness=0.6))
        if label == "before":
            mat = MAT.metal("Brass", "brass", roughness=0.22)
        else:
            mat = MAT.metal_pbr("Brass", "brass", roughness=0.22, anisotropy=0.8,
                                anisotropic_rotation=0.0, coat=1.0, coat_roughness=0.03,
                                noise_stretch=40.0, bump_strength=0.12)
        MAT.assign(s, mat)
        # world: dark floor-side to a bright sky so the metal has something to reflect
        w = RN.setup_world((0.05, 0.05, 0.06), 1.0)
        nt = w.node_tree
        tc = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.45
        ramp.color_ramp.elements[0].color = (0.02, 0.02, 0.025, 1)
        ramp.color_ramp.elements[1].position = 1.0
        ramp.color_ramp.elements[1].color = (0.55, 0.6, 0.7, 1)
        nt.links.new(tc.outputs["Generated"], sep.inputs[0])
        nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], nt.nodes["Background"].inputs["Color"])
        SH.light_rig_from_scene_bounds([s])
        d = RN.frame_distance(1.15, lens=80)
        RN.camera(lens=80, target=(0, 0, 0.5), location=(d * 0.5, -d * 0.86, 0.5 + d * 0.18))
        RN.configure("CYCLES", samples=32, resolution=(256, 256), denoise=True)
        p, dt = RN.shot(os.path.join(TMP, f"hero_{label}"), verbose=False)
        shots.append(p)
        times.append(dt)
    a, b = Image.open(shots[0]).convert("RGB"), Image.open(shots[1]).convert("RGB")
    canvas = Image.new("RGB", (a.width + b.width + 4, a.height), (12, 12, 14))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 4, 0))
    dr = ImageDraw.Draw(canvas)
    dr.text((8, 6), "metal()", fill=(230, 230, 230))
    dr.text((a.width + 12, 6), "metal_pbr(aniso 0.8, coat 1.0)", fill=(230, 230, 230))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)
    return shots, times


def test_hero():
    out = os.path.join(KIT, "renders", "look_metal_before_after.png")
    shots, times = hero_before_after(out)
    lc = SH.look_compare(shots[0], shots[1], background=None)
    diff = np.abs(SH.load_rgba(shots[0])[..., :3] - SH.load_rgba(shots[1])[..., :3])
    print(f"      hero renders: before {times[0]:.2f} s, after {times[1]:.2f} s (256 px, 32 spp)")
    print(f"      before/after look_compare: {lc['verdict']}, delta contrast "
          f"{lc['delta']['contrast']}, highlight {lc['delta']['highlight']}, "
          f"mean |pixel diff| {diff.mean():.4f}, pixels changed >2%: {(diff.max(-1) > 0.02).mean():.1%}")
    check("hero: before/after written", os.path.exists(out), out)
    check("hero: the two materials render measurably differently", (diff.max(-1) > 0.02).mean() > 0.05)


if __name__ == "__main__":
    t0 = time.time()
    test_lights()
    test_contact()
    test_value_synthetic()
    r = lit_and_flat_renders()
    test_value_renders(r)
    test_palette()
    test_metal_pbr_report()
    test_brushed_stretch()
    test_cavity_patina()
    if "--no-hero" not in sys.argv:
        test_hero()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}  ({time.time() - t0:.1f} s)")
    sys.exit(1 if FAILS else 0)
