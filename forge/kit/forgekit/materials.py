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
