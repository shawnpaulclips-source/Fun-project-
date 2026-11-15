"""forgekit - procedural 3D asset construction, rigging, animation and look-dev for agents.

Verified on Blender 5.3.0 / Python 3.13.18 / Linux / no GPU.

    from forgekit import env
    env.prepare()          # MUST run before `import bpy`
    import bpy
    from forgekit import mesh, materials, render, inspect, rig, anim, checkpoint

Read README.md before writing anything. It contains the traps.
"""
__version__ = "0.3.0"
__all__ = [
    "env", "mesh", "materials", "render", "inspect", "rig", "anim", "checkpoint",
    "hand", "formq", "fit", "dynamics", "ornament", "shading",
    "bake", "detail", "compare", "server",
]
