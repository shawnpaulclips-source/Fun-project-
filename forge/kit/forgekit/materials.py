"""PBR material construction.

Principled BSDF only. Every material gets procedural breakup on roughness and
normal, because a constant-roughness metal reads as plastic in every render.
"""
import bpy

# Measured linear base colours. Metals are NOT mid-grey - real metal reflectance
# is coloured and bright. These are from spectral measurements, not guesses.
PALETTE = {
    "brass":        (0.681, 0.451, 0.157),
    "brass_dark":   (0.400, 0.252, 0.086),
    "copper":       (0.926, 0.721, 0.504),
    "gold":         (1.000, 0.766, 0.336),
    "silver":       (0.962, 0.949, 0.922),
    "aluminium":    (0.913, 0.922, 0.924),
    "iron":         (0.560, 0.570, 0.580),
    "steel_blued":  (0.180, 0.195, 0.225),
    "gunmetal":     (0.135, 0.140, 0.150),
    "chrome":       (0.550, 0.556, 0.554),
    "lead":         (0.632, 0.626, 0.641),
}


def _bsdf(mat):
    return mat.node_tree.nodes["Principled BSDF"]


def metal(name, color, roughness=0.25, anisotropy=0.0,
          noise_scale=240.0, bump_strength=0.22, roughness_spread=0.14):
    """Metal with procedural micro-surface.

    noise_scale is in object-space units: ~240 reads as fine brushed grain at
    metre scale, ~60 as coarse cast texture. bump_strength above ~0.4 starts to
    look like corrosion rather than finish.
    """
    if isinstance(color, str):
        color = PALETTE[color]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = _bsdf(m)
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = roughness
    if "Anisotropic" in b.inputs:
        b.inputs["Anisotropic"].default_value = anisotropy

    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = noise_scale
    noise.inputs["Detail"].default_value = 8.0
    noise.inputs["Roughness"].default_value = 0.6

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = bump_strength
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])

    rng = nt.nodes.new("ShaderNodeMapRange")
    rng.inputs["To Min"].default_value = max(0.0, roughness - roughness_spread)
    rng.inputs["To Max"].default_value = min(1.0, roughness + roughness_spread * 1.15)
    nt.links.new(noise.outputs["Fac"], rng.inputs["Value"])
    nt.links.new(rng.outputs["Result"], b.inputs["Roughness"])
    return m


def dielectric(name, color, roughness=0.45, ior=1.45, noise_scale=120.0,
               bump_strength=0.15):
    """Non-metal: plastic, painted surfaces, stone, wood base."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = _bsdf(m)
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Metallic"].default_value = 0.0
    b.inputs["Roughness"].default_value = roughness
    if "IOR" in b.inputs:
        b.inputs["IOR"].default_value = ior
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = noise_scale
    noise.inputs["Detail"].default_value = 6.0
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = bump_strength
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


def glass(name, color=(1.0, 1.0, 1.0), roughness=0.02, ior=1.52):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = _bsdf(m)
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Roughness"].default_value = roughness
    b.inputs["Transmission Weight"].default_value = 1.0
    if "IOR" in b.inputs:
        b.inputs["IOR"].default_value = ior
    return m


def emissive(name, color=(1.0, 0.8, 0.5), strength=12.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = _bsdf(m)
    b.inputs["Emission Color"].default_value = (*color, 1.0)
    b.inputs["Emission Strength"].default_value = strength
    return m


def assign(ob, mat, slot=0):
    if len(ob.data.materials) <= slot:
        ob.data.materials.append(mat)
    else:
        ob.data.materials[slot] = mat
    return mat


def wear_edges(mat, worn_color=None, amount=0.6):
    """Mix a brighter, smoother metal into cavity-exposed edges via Pointiness.

    Pointiness needs no UVs and no baking, which makes it the cheapest possible
    edge-wear pass for a procedural asset.
    """
    nt = mat.node_tree
    b = _bsdf(mat)
    base = b.inputs["Base Color"].default_value
    worn = worn_color or tuple(min(1.0, c * 1.45) for c in base[:3])

    geo = nt.nodes.new("ShaderNodeNewGeometry")
    rng = nt.nodes.new("ShaderNodeMapRange")
    rng.inputs["From Min"].default_value = 0.50
    rng.inputs["From Max"].default_value = 0.58
    rng.inputs["To Min"].default_value = 0.0
    rng.inputs["To Max"].default_value = amount
    nt.links.new(geo.outputs["Pointiness"], rng.inputs["Value"])

    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs[6].default_value = base
    mix.inputs[7].default_value = (*worn, 1.0)
    nt.links.new(rng.outputs["Result"], mix.inputs[0])
    nt.links.new(mix.outputs[2], b.inputs["Base Color"])
    return mat


# =============================================================== metal_pbr layer
# Linear-sRGB normal-incidence reflectance F0, the value a metal's Base Color
# takes. ESTIMATES, NOT MEASUREMENTS: recalled from the standard real-time table
# (Lagarde/de Rousiers "Moving Frostbite to PBR", Hoffman) per research/04 s.6,
# never re-derived from Johnson & Christy optical data. brass, bronze and
# rose_gold are not in that table at all and are pure estimates. PALETTE above
# is untouched; where the two disagree (copper, silver) METAL_F0 is the
# recalled-table value and PALETTE is the kit's original value.
METAL_F0 = {
    "gold":       (1.000, 0.766, 0.336),
    "silver":     (0.972, 0.960, 0.915),
    "copper":     (0.955, 0.638, 0.538),
    "aluminium":  (0.913, 0.922, 0.924),
    "platinum":   (0.672, 0.637, 0.585),
    "titanium":   (0.542, 0.497, 0.449),
    "chromium":   (0.550, 0.556, 0.554),
    "iron":       (0.560, 0.570, 0.580),
    "brass":      (0.681, 0.451, 0.157),
    "bronze":     (0.550, 0.360, 0.170),
    "rose_gold":  (0.920, 0.600, 0.500),
}
METAL_F0_STATUS = {k: "recalled table value, unverified" for k in METAL_F0}
METAL_F0_STATUS.update({"brass": "kit value, not in the table, unverified",
                        "bronze": "estimate", "rose_gold": "estimate (gold/copper blend)",
                        "iron": "kit PALETTE value, unverified"})


def new_material(name):
    """bpy.data.materials.new with a node tree. In 5.3 a new material already
    has one (Principled BSDF + Output) and Material.use_nodes is deprecated
    (warns, slated for removal in 6.0), so only touch it on older builds."""
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    return m


def _set_input(node, name, value, report):
    if name in node.inputs:
        node.inputs[name].default_value = value
        report["applied"].append(name)
        return True
    report["missing"].append(name)
    return False


_AXIS = {"X": 0, "Y": 1, "Z": 2}


def metal_pbr(name, metal="brass", roughness=0.25, anisotropy=0.0,
              anisotropic_rotation=0.0, coat=0.0, coat_roughness=0.05,
              thin_film_thickness=0.0, thin_film_ior=1.5, breakup_scale=240.0,
              noise_stretch=1.0, brush_axis="X", tangent_axis="Z",
              bump_strength=0.18, roughness_spread=0.12, return_report=False):
    """Physically structured metal on the Principled BSDF 5.3 inputs.

    metal:        METAL_F0 key, PALETTE key, or a linear RGB tuple.
    roughness:    base roughness; breakup noise spreads it by +-roughness_spread.
    anisotropy:   'Anisotropic' 0..1 (brushed 0.6-0.85). A Tangent node (radial
                  about tangent_axis) is wired so the streak direction is
                  deterministic rather than Cycles' implicit default.
    anisotropic_rotation: 'Anisotropic Rotation' 0..1 (fraction of a turn).
    coat, coat_roughness: 'Coat Weight' / 'Coat Roughness' (lacquer).
    thin_film_thickness:  'Thin Film Thickness' in nm (0 = off; 300-800 tarnish /
                  heat tint); thin_film_ior: 'Thin Film IOR' (1.45-1.8).
    breakup_scale: noise frequency across the grain, object units.
    noise_stretch: grain elongation along brush_axis. Texture Coordinate ->
                  Mapping (scale 1 along the brush, noise_stretch across) ->
                  Noise, so 40-60 gives brushed streaks and 1 is isotropic.
    Every input is checked with `in bsdf.inputs`; missing ones are skipped and
    listed. The report is stored on the material as mat["forge_inputs_applied"]
    / ["forge_inputs_missing"]; return_report=True returns (mat, report).
    """
    if isinstance(metal, str):
        color = METAL_F0.get(metal) or PALETTE[metal]
    else:
        color = tuple(metal)[:3]
    m = new_material(name)
    nt = m.node_tree
    b = _bsdf(m)
    rep = {"applied": [], "missing": []}
    _set_input(b, "Base Color", (*color, 1.0), rep)
    _set_input(b, "Metallic", 1.0, rep)
    _set_input(b, "Roughness", roughness, rep)
    if anisotropy > 0:
        if _set_input(b, "Anisotropic", anisotropy, rep):
            _set_input(b, "Anisotropic Rotation", anisotropic_rotation, rep)
            if "Tangent" in b.inputs:
                tan = nt.nodes.new("ShaderNodeTangent")
                tan.direction_type = "RADIAL"
                tan.axis = tangent_axis
                nt.links.new(tan.outputs["Tangent"], b.inputs["Tangent"])
                rep["applied"].append("Tangent(radial %s)" % tangent_axis)
    if coat > 0:
        if _set_input(b, "Coat Weight", coat, rep):
            _set_input(b, "Coat Roughness", coat_roughness, rep)
    if thin_film_thickness > 0:
        if _set_input(b, "Thin Film Thickness", thin_film_thickness, rep):
            _set_input(b, "Thin Film IOR", thin_film_ior, rep)

    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    sc = [max(noise_stretch, 1e-6)] * 3
    sc[_AXIS[brush_axis.upper()]] = 1.0
    mp.inputs["Scale"].default_value = sc
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = breakup_scale / max(noise_stretch, 1e-6)
    noise.inputs["Detail"].default_value = 8.0
    noise.inputs["Roughness"].default_value = 0.6
    nt.links.new(mp.outputs["Vector"], noise.inputs["Vector"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = bump_strength
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    # Coat Normal is left unlinked on purpose: lacquer levels the grain, so the
    # coat lobe stays sharp over a bumpy base. That contrast is what reads as coat.
    rng = nt.nodes.new("ShaderNodeMapRange")
    rng.inputs["To Min"].default_value = max(0.0, roughness - roughness_spread)
    rng.inputs["To Max"].default_value = min(1.0, roughness + roughness_spread)
    nt.links.new(noise.outputs["Fac"], rng.inputs["Value"])
    nt.links.new(rng.outputs["Result"], b.inputs["Roughness"])

    m["forge_inputs_applied"] = ",".join(rep["applied"])
    m["forge_inputs_missing"] = ",".join(rep["missing"])
    m["forge_metal"] = metal if isinstance(metal, str) else "custom"
    return (m, rep) if return_report else m


def _upstream(nt, sock):
    """(socket feeding `sock` or None, its default value)."""
    for l in nt.links:
        if l.to_socket == sock:
            return l.from_socket, sock.default_value
    return None, sock.default_value


def _feed(nt, dst, src_socket, value):
    if src_socket is not None:
        nt.links.new(src_socket, dst)
    else:
        dst.default_value = value


def cavity_patina(target, colour=(0.25, 0.55, 0.45), amount=0.8, distance=0.02,
                  mode="BOTH", patina_roughness=0.6, pointiness_range=(0.5, 0.44),
                  only_local=False):
    """Darken / tint concave areas so patina sits in crevices.

    target:   a mesh object (every material slot is patched) or one material.
    colour:   patina colour, linear RGB. Verdigris estimate (0.25, 0.55, 0.45);
              for plain grime use something dark like (0.05, 0.04, 0.03).
    amount:   maximum mix strength 0..1.
    distance: AO search distance in metres, about the crevice width.
    mode:     "AO" (Ambient Occlusion node, inside=False; low AO = occluded =
              crevice), "POINTINESS" (Geometry.Pointiness below
              0.5 = concave; depends on mesh density) or "BOTH" (max of the two).
    pointiness_range: (from, to) pointiness mapped to mask 0 -> 1.
    only_local: AO sees only this object. Default False: research/04 suggested
              True, but then a crevice between two objects (a part sitting on a
              base) is invisible to the mask - measured mask 0.00 there.
    Wires the mask into Base Color and Roughness, preserving whatever fed them
    (wear_edges, metal_pbr breakup). Cycles evaluates both masks; Workbench
    ignores them. Returns the list of patched materials; each gets
    mat["forge_cavity_mask"] naming the mask node so it can be inspected.
    """
    mats = [target] if isinstance(target, bpy.types.Material) else \
        [s.material for s in target.material_slots if s.material]
    if not mats:
        raise ValueError("no material to patch")
    out = []
    for mat in mats:
        nt = mat.node_tree
        b = _bsdf(mat)
        masks = []
        if mode in ("AO", "BOTH"):
            ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
            ao.inside = False
            ao.only_local = only_local
            ao.samples = 16
            ao.inputs["Distance"].default_value = distance
            inv = nt.nodes.new("ShaderNodeMapRange")
            inv.inputs["From Min"].default_value = 1.0
            inv.inputs["From Max"].default_value = 0.55
            inv.inputs["To Min"].default_value = 0.0
            inv.inputs["To Max"].default_value = 1.0
            inv.clamp = True
            nt.links.new(ao.outputs["AO"], inv.inputs["Value"])
            masks.append(inv.outputs["Result"])
        if mode in ("POINTINESS", "BOTH"):
            geo = nt.nodes.new("ShaderNodeNewGeometry")
            pr = nt.nodes.new("ShaderNodeMapRange")
            pr.inputs["From Min"].default_value = pointiness_range[0]
            pr.inputs["From Max"].default_value = pointiness_range[1]
            pr.clamp = True
            nt.links.new(geo.outputs["Pointiness"], pr.inputs["Value"])
            masks.append(pr.outputs["Result"])
        if not masks:
            raise ValueError("mode must be AO, POINTINESS or BOTH")
        mask = masks[0]
        if len(masks) == 2:
            mx = nt.nodes.new("ShaderNodeMath")
            mx.operation = "MAXIMUM"
            nt.links.new(masks[0], mx.inputs[0])
            nt.links.new(masks[1], mx.inputs[1])
            mask = mx.outputs[0]
        amt = nt.nodes.new("ShaderNodeMath")
        amt.operation = "MULTIPLY"
        amt.inputs[1].default_value = amount
        amt.name = amt.label = "forge_cavity_mask"
        nt.links.new(mask, amt.inputs[0])
        fac = amt.outputs[0]

        src, val = _upstream(nt, b.inputs["Base Color"])
        mixc = nt.nodes.new("ShaderNodeMix")
        mixc.data_type = "RGBA"
        _feed(nt, mixc.inputs[6], src, val)
        mixc.inputs[7].default_value = (*colour, 1.0)
        nt.links.new(fac, mixc.inputs[0])
        nt.links.new(mixc.outputs[2], b.inputs["Base Color"])

        src, val = _upstream(nt, b.inputs["Roughness"])
        mixr = nt.nodes.new("ShaderNodeMix")
        mixr.data_type = "FLOAT"
        _feed(nt, mixr.inputs[2], src, val)
        mixr.inputs[3].default_value = patina_roughness
        nt.links.new(fac, mixr.inputs[0])
        nt.links.new(mixr.outputs[0], b.inputs["Roughness"])
        mat["forge_cavity_mask"] = amt.name
        out.append(mat)
    return out
