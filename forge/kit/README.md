# forge-kit

**A toolkit for building detailed 3D models, rigging them, and animating them — from code, headless, with no GPU.**

Written for an AI agent to use. Everything in here was executed and measured on
Blender 5.3.0 / Python 3.13.18 / Linux / 4 CPU cores / no GPU. Numbers quoted are
real timings from that machine, not estimates.

---

## 0. The idea, in one paragraph

You are a model that writes code and cannot see. Every text-to-3D product bets on
solving the second problem with a generative mesh model. That bet does not pay:
the open checkpoints emit isosurface blobs with no edge loops, which cannot be
rigged at a joint without folding. So bet the other way. **Detail comes from exact
procedural repetition — trig, swept profiles, lathed silhouettes, hundreds of
placed graduation marks — which is precisely what a code generator is good at.
Sight comes from a render loop fast enough to run on every single call.** A
Workbench inspection render is 0.62 s and a `.blend` checkpoint is 9 ms, so you
can look at your work constantly and undo for free. That is the whole method.

---

## 1. Install

```bash
# 1. the GL libraries. Blender 5.x ABORTS AT STARTUP without a GL/Vulkan context
#    even headless, and the bpy wheel does not declare this dependency.
apt-get install -y libegl1 libgl1 libglx-mesa0 libgl1-mesa-dri \
  mesa-vulkan-drivers libvulkan1 libopengl0 libxi6 libxxf86vm1 \
  libxfixes3 libxrender1 libsm6 libxkbcommon0 xvfb ffmpeg

# 2. Blender as a library. ~1.1 GB installed, 11 s.
python3 -m venv venv && ./venv/bin/pip install bpy==5.3.0

# 3. run anything
./venv/bin/python examples/01_armillary.py out/
```

`bpy==5.3.0` declares `requires_python == 3.13.*`. **It will not install on 3.12
or 3.14.** Pin your interpreter.

Every script must set the software-rasteriser env vars *before* `import bpy`:

```python
from forgekit import env
env.prepare()          # sets LIBGL_ALWAYS_SOFTWARE, GALLIUM_DRIVER, VK_ICD_FILENAMES
import bpy             # only now
```

---

## 2. The traps

Every one of these was hit while building this kit. They cost real time. Most of
them break code that any model trained before 2026 will confidently write,
because the API changed and the tutorials did not.

| Trap | What happens | Fix |
|---|---|---|
| `mesh.use_auto_smooth` | `AttributeError` — **removed in 4.1+** | `mesh.shade_smooth()`: weighted-normal modifier + `bevel(harden_normals=True)` |
| `action.fcurves` | **Deleted in 4.4+/5.x.** `hasattr(action,'fcurves') == False` | `anim.fcurves(obj)` — walks slot → layer → strip → channelbag |
| `BLENDER_EEVEE_NEXT` | `TypeError`, enum removed in 5.3 | It's `BLENDER_EEVEE` again — and you should not be using it, see below |
| `armature.layers`, `pose.bone_groups` | Gone in 4.x | Bone collections (`armature.collections_all`) |
| `bpy.ops.graph.*` | `poll() failed, context is incorrect` | `anim.select_for_graph(obj)` **then** `anim.graph_context()`. Both are required |
| **Rigify limbs default to IK** | You key `thigh_fk.L` for 24 frames, get perfect curves, and the character **does not move** | `rig.set_ik_fk(rig, 1.0)` before any FK animation |
| Metarig survives generation | `next(o for o in objects if o.type=='ARMATURE')` returns the **metarig**, not the rig | `rig.find_rig()` — identifies by `MCH-` prefix |
| Measuring control bones | Curves look healthy, mesh never deformed | `anim.deform_name()` — always measure `DEF-` bones |
| `bpy.ops.object.bake` fails | `RuntimeError: Object "Sun" is not a mesh` — adding a light left it active | Set the active object explicitly before every `bpy.ops` call |
| FBX `add_leaf_bones` | Defaults **True**, breaks every Unreal import | `rig.export()` sets it False |
| glTF 5-influence vertices | Exporter silently drops the smallest weight, changing deformation | `vertex_group_limit_total(4)` — `rig.bind()` does it |
| `pip install tetgen` | Drags pyvista + VTK, **565 MB** | `pip install tetgen --no-deps` → 908 KB |
| `pymeshlab` without `libOpenGL.so.0` | Silently exposes 235 filters instead of 281; quadric decimation just isn't there | `apt install libopengl0` |
| PyPI `sdf` | It's a scientific data format package, not the SDF modelling library | fogleman's `sdf` is git-only |
| Draco / MeshOptimizer | `.so` bridges absent from the wheel — no GLB compression in-process | post-process with `gltf-transform` on Node |
| `read_factory_settings()` | **Disables addons**, so Rigify vanishes | Call `rig.enable_rigify()` *after* it, never before |

### Engine choice is not a preference

Measured, same frame, no GPU:

| Engine | 512×512 | Use for |
|---|---|---|
| **Workbench** | **0.62 s** | Every inspection pass. Structure, silhouette, weight heatmaps, playblasts |
| **Cycles CPU** | 1.95–5.11 s @ 32 spp | Hero renders, materials, bakes |
| EEVEE | 23.4–43.2 s | **Nothing.** 12–30× slower than path tracing on llvmpipe |

The instinct that EEVEE is the fast preview engine is exactly backwards without a
GPU. `env.capabilities()` reports `eevee_allowed` — respect it.

---

## 3. Modelling: where detail actually comes from

```python
from forgekit import mesh as M, materials as MAT
```

The armillary sphere in `examples/01_armillary.py` is 17 parts and ~180k triangles
and contains **452 individually placed graduation marks**. Not a texture — real
bevelled geometry that catches a rim light. No image-generation model produces
that correctly, and it took four lines:

```python
ring = M.graduated_ring("Ring_Meridian", radius=1.00, thickness=0.045,
                        height=0.150, divisions=180, every=5)
```

Four primitives cover most hard-surface work:

- **`M.lathe(name, profile)`** — revolve a `(radius, z)` profile. Pedestals,
  finials, bottles, columns, pipe fittings, shell casings. The silhouette *is*
  the design; spend your effort on the profile list.
- **`M.loft_bracket(bm, p0,p1,p2,p3, ...)`** — sweep a tapering rectangular
  section along a cubic Bézier. Support arms, handles, horns, ribs, chassis
  members. **A stack of boxes along the same path reads as visible stair-stepping
  in any render.** This was a real first-draft failure; see §7.
- **`M.graduated_ring` / `M.radial_ticks`** — exact radial repetition. The single
  highest-value detail trick available to a code generator.
- **`M.add_box`** into a shared `bmesh` — rivets, panels, collars, teeth.

Then the two finishing passes that are not optional:

```python
M.bevel(ob, width=0.0035, segments=2, angle_deg=34)   # 2-4 mm at metre scale
M.shade_smooth(ob)
```

**A perfectly sharp edge receives zero specular highlight and reads as CG.** The
bevel is what makes brass look like brass. If your render looks "3D-rendered"
rather than photographed, you forgot the bevel before you forgot anything else.

Materials: `MAT.metal("Brass", "brass", roughness=0.22)` ships measured linear
base colours (real metal reflectance is coloured and bright, not mid-grey) and
wires procedural noise into **both** normal and roughness. Constant roughness
reads as plastic. `MAT.wear_edges(mat)` adds cavity-driven edge wear with no UVs
and no baking.

### Never guess camera distance

```python
bounds = INS.scene_bounds(parts)
dist = RN.frame_distance(bounds["height"], lens=80)
```

The #1 first-render failure is a cropped subject, and you burn a full render
finding out. Compute it.

---

## 4. Rigging

```python
from forgekit import rig as RIG
RIG.enable_rigify()                      # AFTER read_factory_settings()
meta = RIG.add_human_metarig()
RIG.fit_metarig(meta, mesh=body)         # scale + place from the mesh bbox
rig  = RIG.generate(meta)                # 6.4-7.2 s
RIG.bind(body, rig, method="ARMATURE_AUTO")
```

**Use Rigify. Do not reach for a learned auto-rigger.** Rigify ships inside the
bpy wheel and generates headless. Measured:

> 159-bone human metarig → **706 bones, 533 constraints, 114 drivers, 220 custom
> shapes, 23 bone collections, in 6.4 s on 4 CPU cores.**

That is a production control rig — IK/FK switching, foot rolls, finger drivers, a
face rig — for free. A metarig is just an armature whose pose bones carry
`rigify_type` strings, so fitting its bones to your mesh inherits the entire
control rig. Every learned auto-rigger (UniRig, RigNet, RigAnything,
MagicArticulate) hands you a bare skeleton plus weights and leaves the control rig
unbuilt — and several are non-commercial (**RigAnything is Adobe noncommercial**;
RigNet is GPLv3 and pinned to torch 1.12 since 2023).

### The mesh contract

Bone heat **fails silently** on bad input. Before binding, the mesh must be:

1. **Manifold.** `inspect.mesh_stats(ob)["non_manifold_edges"] == 0`. If you built
   it from overlapping primitives, voxel-remesh it — `examples/02` does exactly
   this, 0.018 m voxels, and asserts the result.
2. **≥3 edge loops across every bend joint.** Below that, no weight distribution
   avoids volume collapse. This is a *geometry* failure that gets blamed on
   rigging every time.
3. **Limbs not touching at rest**, or weights bleed across the contact.
4. **No self-intersection**, or tetrahedralisation refuses and the BBW path dies.

Bone heat does not produce an exact partition of unity — measured sums run
0.979–1.000 — so `RIG.bind()` always normalises after.

### Looking at a rig without eyes

```python
RIG.weights_to_vertex_color(body, mode="argmax")
RN.workbench(shading="FLAT", color_type="VERTEX")
RN.shot("weights_front")
```

**0.87 s for a 400×400 PNG, no GPU.** `argmax` paints one hue per owning bone, so
the entire weight partition is one image. A bone that has claimed the wrong limb
is instantly obvious; near-black means unweighted. This is the highest-value tool
in the kit — see `renders/weights_front.png`.

Then stress it: `RIG.pose_test(rig, body)` drives extreme poses and reports
self-intersection counts and volume ratio per pose. Self-intersections rise
monotonically with a bad bend (measured 0 → 20 → 193 → 297 → 474 pairs at
0/45/90/135/170°), and a volume ratio below ~0.93 means the joint is pinching.

`RIG.manifest(rig, body)` emits the contract the animation layer reads. **Animation
must never pattern-match a bone name** — it resolves everything through the
manifest, so a rig in any naming convention works.

Export with `RIG.export()`, which sets the four flags that silently ruin assets:
`export_def_bones=True` (706 bones → **160**), `export_influence_nb=4`,
`export_force_sampling=True`, and FBX `add_leaf_bones=False`.

---

## 5. Animation

Work in stages, the way an animator does. This is what stops the default failure
mode of two keys and a lerp.

```python
from forgekit import anim as A
RIG.set_ik_fk(rig, 1.0)                  # 1.0 = FK. READ THE TRAP TABLE.

for frame, pose in SEQUENCE:             # BLOCK
    A.apply_pose(rig, pose, frame=frame)
A.stage_block(rig)                       # stepped keys, EXTREME type

A.stage_spline(rig, ease="smooth")       # SPLINE
A.cycle(rig)

A.offset_chain(rig, ["torso","chest","head"], frames_per_bone=2.0)   # OFFSET
A.close_loop(rig, 1, 24)                 # POLISH — after offsetting, never before
A.soften_handles(rig, 0.55)
A.decimate(rig, error=0.02)
```

Craft numbers are built in. `A.WALK_24` is Williams' 24-frame walk — contact
1/13/25, down 4/16, passing 7/19, up 10/22. `A.GAITS` holds quadruped footfall
phase offsets. `A.EASE` holds measured Bézier handle fractions: `"snap"` is
`(0.10, 0.70)`, which puts 30 % of the move in by frame 3 of 13 and 90 % by frame
9 — that is what "snappy with a settle" means numerically.

Poses are degrees in, radians stored (`A.set_bone`). An agent reasoning in degrees
against a radian channel is a reliable 57× bug. `A.mirror_pose(pose, mirror_map)`
gives you the second stride free.

**Do not hand-roll curve algorithms.** The Graph Editor operators run headless
through `A.graph_op(rig, "ease", factor=0.4)` — `decimate`, `ease`,
`butterworth_smooth`, `breakdown`, `blend_to_neighbor`, `match_slope`,
`euler_filter`, all verified. Blender's `decimate` took 240 keys → 47 within its
error bound where hand-written RDP kept 74 for the same tolerance, because Blender
removes the least-influential key and re-evaluates the real Bézier while RDP only
measures point-to-chord distance.

### Critique: seeing motion without watching it

```python
m = A.metrics(rig, tracked_deform_bones, 1, 24, contact_bones=contacts)
print(A.critique(m))
```

| Metric | Threshold | Means |
|---|---|---|
| `jerk_max` | > 0.08 m/f³ | Pops. Soften handles or decimate |
| `foot_slide_m` | > 0.010 m | Foot not planted. Pass `expected_root_speed` for in-place cycles |
| `loop_seam_error_m` | > 0.001 m | Cycle doesn't close. Compares frame `start` to frame **`end+1`**, never to `end` |
| `arc_straightness` | < 0.06 | End effector travelling a straight line, reads robotic |

The report names the fix as an actual function call. That is the point — you read
~300 tokens and know what to run next.

**And look at it anyway**: `A.playblast()` is ~0.11 s/frame at 480×270, then
`RN.contact_sheet()` tiles 24 frames into one image. MCP has no video content
type, so a contact sheet is the only way to see motion in a single result. Hand
the MP4 back as a link.

---

## 6. The loop, which is the actual product

```
build → inspect (numbers, free) → render (0.6 s) → read → fix → checkpoint (9 ms)
```

Run `inspect.report()` before you ever render. Numbers cost microseconds and ~50
tokens; renders cost seconds and ~2000. Check the numbers first, render to confirm
or to judge aesthetics.

Checkpoint constantly — `checkpoint.checkpoint(dir, "label")` is 9 ms and 260 KB.
There is no reason to be cautious about trying something. **Do checkpoint before
every boolean and remesh: `bpy` can segfault on pathological geometry with no
Python exception, and a segfault takes the whole process.**

Keep images under ~2500 tokens per result (~`w×h/750` each). Three 512² PNGs a turn
will eat your context before the asset is finished. Composite instead.

---

## 7. Worked example: two iterations that mattered

**Modelling.** First armillary render cropped the finial and the whole base, and
the support brackets — built as a stack of boxes along an arc — read as visible
stair-stepping. Fix: replaced them with `loft_bracket` (a section swept along a
cubic Bézier) and used `frame_distance()` instead of guessing the camera at 3.5 m
when the object needed 8.3 m. Second render was right. **Two minutes wall clock**,
because looking costs 36 s and being wrong costs nothing.

**Animation.** First walk cycle produced 918 keys across 108 curves and a contact
sheet showing a character standing almost perfectly still. The curves were
flawless. The limbs were in IK and the FK controls drove nothing. One call to
`set_ik_fk(rig, 1.0)` fixed it — and it exposed a second bug, that the metrics
were tracking *control* bones, which moved either way. Both are now in the trap
table above, which is the only reason you will not lose that afternoon.

---

## 8. What's verified, and what isn't

Verified by execution here: bpy 5.3.0 on py3.13.18; Rigify headless at 706 bones /
6.4 s; bone heat binding with 0 unweighted vertices and weight sums of exactly
1.000; weight heatmap renders; Graph Editor operators; glTF and FBX export;
Workbench / Cycles / EEVEE timings; ffmpeg contact sheets and MP4.

**Not solved, and honestly:**

- **The FK walk in `examples/03` scores 52/100.** The two remaining failures —
  foot slide and foot jerk — are inherent to an FK walk with no IK foot planting.
  The polish pass genuinely improves both (jerk 0.340 → 0.299, slide 101 → 64 mm)
  without crossing the thresholds. **IK foot planting is the fix and it is not
  built.** The kit flags the problem correctly; it does not yet solve it.
- Materials are procedural shaders, so **they do not survive glTF export**. They
  must be baked to textures first (a 2048² normal bake is 1.90 s on CPU). Not built.
- No UV unwrapping step is wired in (`xatlas` is the intended tool, 0.32 s).
- `fit_metarig` fits by bounding box. Per-limb landmark fitting — symmetry plane,
  geodesic extremity detection, medial centreline — is the real version.
- Quadruped gait phases in `A.GAITS`: walk/trot/pace are from the gait literature
  and solid. **Canter and gallop are not included** because the source could not
  be verified.
- Licensing, if you go near motion capture: nearly every free motion dataset and
  ML checkpoint derives from AMASS/SMPL, which are **non-commercial**. The code is
  MIT; the weights are not. LAFAN1 is CC BY-NC-**ND**, so retargeting it is a
  prohibited derivative. **CMU is the one clean source** — free for all uses
  including commercial products.
- **forge-kit imports bpy in-process, so it is GPL-3.0 as distributed.**

`reference/` holds the full research behind these decisions: environment baseline,
geometry and MCP server architecture, rigging, animation, and a synthesis with the
risk register and a phased build plan.

---

## 9. Files

```
forgekit/env.py          software-GL setup, capability probe
forgekit/mesh.py         lathe, loft, bands, radial ticks, bevel, smooth
forgekit/materials.py    measured metal palette, procedural PBR, edge wear
forgekit/render.py       engines, 3-point light, frame_distance, quads,
                         turntable, contact sheet, ffmpeg encode
forgekit/inspect.py      manifold/euler/volume/symmetry, self-intersection,
                         token-cheap scene summary
forgekit/rig.py          Rigify, metarig fit, bind, weight stats, heatmap,
                         pose battery, IK/FK, manifest, export
forgekit/anim.py         5.x fcurve access, graph operators, stages, easing,
                         offset, cycle, metrics, critique, playblast
forgekit/checkpoint.py   9 ms snapshots and rollback

examples/01_armillary.py      detailed hard-surface asset, 2 iterations
examples/02_rig_character.py  body -> remesh -> Rigify -> bind -> validate
examples/03_walk_cycle.py     block -> spline -> offset -> critique -> polish

renders/                 the actual outputs, including the weight heatmap
reference/               ~7,200 lines of underlying research
```
