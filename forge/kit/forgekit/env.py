"""Environment setup and capability probing.

Import this FIRST, before bpy, if you are running outside a prepared container.
It sets the software-rasteriser env vars that Blender 5.x needs to start at all.
"""
import os, sys, shutil, subprocess

SOFTWARE_GL = {
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "GALLIUM_DRIVER": "llvmpipe",
    "VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/lvp_icd.x86_64.json",
}

APT_PACKAGES = [
    "libegl1", "libgl1", "libglx-mesa0", "libgl1-mesa-dri", "mesa-vulkan-drivers",
    "libvulkan1", "libopengl0", "libxi6", "libxxf86vm1", "libxfixes3",
    "libxrender1", "libsm6", "libxkbcommon0", "xvfb",
]


def prepare():
    """Set software-GL env vars. Must run before `import bpy`."""
    for k, v in SOFTWARE_GL.items():
        os.environ.setdefault(k, v)


def capabilities():
    """Return what this machine can actually do. Call before choosing an engine."""
    import bpy
    cy = bpy.context.preferences.addons.get("cycles")
    devices = []
    if cy:
        try:
            prefs = cy.preferences
            prefs.refresh_devices()
            devices = [(d.name, d.type) for d in prefs.devices]
        except Exception:
            pass
    gpu = [d for d in devices if d[1] in ("CUDA", "OPTIX", "HIP", "METAL", "ONEAPI")]
    return {
        "blender": bpy.app.version_string,
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "gpu_devices": gpu,
        "has_gpu": bool(gpu),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        # EEVEE is 12-30x SLOWER than Cycles CPU on llvmpipe. Never pick it without a GPU.
        "preview_engine": "BLENDER_WORKBENCH",
        "beauty_engine": "CYCLES",
        "eevee_allowed": bool(gpu),
    }


def check():
    """Human-readable capability dump."""
    c = capabilities()
    for k, v in c.items():
        print(f"{k:16} {v}")
    return c
