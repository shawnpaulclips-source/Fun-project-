# forge — environment baseline (verified, not researched)

Measured 2026-11-14 in the cloud container. Every number here came from a real run, not a doc.

## Host
- Linux 6.18, x86_64, 4 cores, 15 GB RAM, ~30 GB free disk
- Python 3.13.18
- ffmpeg 6.1.1 present at /usr/bin/ffmpeg (playblasts, GIF, contact sheets)
- No GPU. No display. No Blender binary.

## Blender as a library
`pip install bpy` → **bpy 5.3.0 installs and imports on Python 3.13.18**. No tarball, no `blender --background` subprocess needed.
- venv footprint: **1.1 GB**
- pulls numpy 2.5.4, cython, requests, zstandard, cattrs
- verified: primitive add, SUBSURF modifier, mesh introspection, glTF/GLB export

## Hard dependency the wheel does not declare
Blender 5.x aborts at startup without a GL/Vulkan context, even headless:
```
Couldn't open libEGL.so.1
gpu.vulkan | WARNING Unable to initialize a Vulkan 1.1 instance.
```
Fix (required, apt):
```
apt-get install -y libegl1 libgl1 libglx-mesa0 libgl1-mesa-dri \
  mesa-vulkan-drivers libvulkan1 libxi6 libxxf86vm1 libxfixes3 \
  libxrender1 libsm6 libxkbcommon0 xvfb
```
Env for software rasterisation:
```
LIBGL_ALWAYS_SOFTWARE=1
GALLIUM_DRIVER=llvmpipe
VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json
```
Vulkan 1.1 still warns (lavapipe reports a lower instance version) — harmless, Blender falls back to GL.

## Render engine benchmark
Suzanne, SUBSURF level 3 (500 polys base), one area light, 512x512.
Engine enum in 5.3 is `BLENDER_EEVEE`, **not** `BLENDER_EEVEE_NEXT` — that enum was removed.

| Engine | Samples | Time | Verdict |
|---|---|---|---|
| BLENDER_WORKBENCH | — | **0.62 s** | Default for every inspection pass: ortho quads, wireframe, weight heatmaps, pose contact sheets |
| CYCLES (CPU) | 32 | **5.11 s** | Hero renders, material checks, AO/curvature bakes. Scales with cores. |
| BLENDER_EEVEE | 32 | **43.19 s** | Dead on lavapipe. Slower than CPU path tracing. Do not wire it in. |

Decision: **Workbench for the agent feedback loop, Cycles CPU for anything that needs light transport. EEVEE is off the table without a real GPU.**

## Export
`export_scene.gltf` → 69 KB GLB from the test scene. Works in-process.

## Consequences for the design
1. `forge` is a pip-installable Python package with one apt preamble. No Blender install step.
2. The render-back vision loop is proven: geometry → Workbench render in under a second → image content block → Claude critiques its own output. Sub-second turnaround is fast enough to put a render in *every* mutation tool's response if we want it.
3. Playblasts are viable on CPU: 24 frames of Workbench ≈ 15 s. A 120-frame cycle ≈ 75 s. Contact sheets are cheap; full-res Cycles animation is not.
4. 1.1 GB venv + mesa means the whole runtime lands around 1.5 GB. Fine here, matters if this ever ships as a container image.
