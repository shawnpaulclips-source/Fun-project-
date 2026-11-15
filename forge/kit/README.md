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

---

## 10. v0.3 modules

Each module below was built and tested in-container. The module notes are
reproduced verbatim from `notes/`; every test file in `tests/` runs standalone
and prints PASS/FAIL per check.

| Module | Test | Notes |
|---|---|---|
| `hand.py`, `formq.py`, `fit.py` | `forge/proto/form/` scripts | research/08 |
| `dynamics.py`, `ornament.py`, `materials.py` (extended), `shading.py` | `tests/test_dynamics.py`, `test_ornament.py`, `test_shading.py` | `notes/agent_a.md` |
| `bake.py`, `detail.py`, `compare.py`, `server.py` | `tests/test_bake.py`, `test_detail.py`, `test_compare.py`, `test_server.py` | `notes/agent_c.md` |
| `anim.py` (foot planting, clip export) | `tests/test_anim.py` | `notes/agent_b_findings.md` |

Known gap: process detail in `detail.py` uses real-world amplitudes and changes
only ~1.2% of pixels at full-object distance (11.7% in close-up). It is
accurate, not yet noticeable.


---

## Dynamics, ornament, metal look-dev, shading checks

Four modules: `dynamics.py` (spring chains on bones), `ornament.py` (guilloche,
even placement, frames that don't twist, mouldings), `materials.py`
(additions: `METAL_F0`, `metal_pbr`, `cavity_patina`) and `shading.py`
(numbers about light, contact, value and colour, readable without looking at
an image). Every number below was measured in this container (bpy 5.3.0,
py3.13, 4 shared cores, no GPU) by `tests/test_dynamics.py`,
`tests/test_ornament.py` and `tests/test_shading.py`. Each test runs on its own
and exits non-zero on failure.

---

### dynamics.py: reactive secondary motion

The integrator is pure numpy with no bpy import, so it runs and tests anywhere.
The rig functions import bpy lazily.

| Function | Does |
|---|---|
| `ChainSpec(name, bones, stiffness, damping_ratio, drag, gravity, stretch_limit, compliance, substeps, iterations, collider_names, blend)` | One chain. `stiffness` 1/s², `damping_ratio` 1.0 = critical, `drag` = fraction of velocity lost per second, `blend` = additive weight |
| `ChainSpec.from_preset(name, bones, "tail", **overrides)` | Spec from `PRESETS` |
| `PRESETS` | spine, tail, hair, cloak, antenna, belly: research/06 §11 ranges, stored verbatim under `"ranges"`, plus one working value inside each range. **`ear` is mine, not from the research**: interpolated between hair and antenna, underdamped (0.6) on purpose, untuned |
| `ColliderSpec(name, kind, bone, radius, offset, tail, normal, friction)` | `"sphere"`, `"capsule"`, `"plane"`. Uses world space, or bone space when `bone` is set (`bake` moves it each frame) |
| `new_state(specs, rest_pose)` / `chain_from_points(pts, preset)` | Builds a batch with no rig. Chains of different lengths are padded and masked |
| `step(state, root_accel=None, dt=1/60, targets=None, ...)` | Advances one frame. Use `targets` (C,N+1,3) for world-space driving: the root follows the animation and inertia comes out of the motion. Use `root_accel` for in-place driving: the chain feels −a perpendicular to its axis. **Never both** |
| `impulse(state, chain, particle, dv, sigma)` / `impulse_at(state, point, dv, radius)` | Touch. Changes velocity with a falloff |
| `set_colliders(state, colliders)` | World-space colliders for the numpy path |
| `chains_from_rig(rig, bone_list, preset=...)` | Reads world rest positions from an armature. Checks that every bone exists and that the chain is a real parent→child chain |
| `write_pose(rig, state)` / `live_frame(rig, state)` | Live path (for a `frame_change_post` handler): animator targets → step → layered write |
| `bake(rig, chains, f0, f1, colliders=None, preset=None)` | Pass 1 records the animator pose, pass 2 simulates, pass 3 keys. Writes into a **copy** of the action (`<name>_dyn`), so the source keys survive and you can re-bake. Returns ms/frame and max stretch |
| `additive_layer(q_anim, q_sim, w)` | `q_anim * slerp(I, q_anim⁻¹ q_sim, w)`, quaternions in wxyz order |
| `benchmark()`, `max_stretch()`, `export_vrm_springs()` | Measurement, plus a VRMC_springBone-shaped dict (**not validated against the schema**) |

Model per substep: semi-implicit Euler, an angular spring toward the rest
direction carried by the *simulated* parent (a dot-product-only Rodrigues
rotation), tangential damping `c = 2ζ√k`, drag `(1-drag)^h`, a velocity clamp
(0.5 L/h), colliders with friction, red-black PBD/XPBD distance projection,
then a follow-the-leader stretch clamp on the last substep.

Measured:
- 8 chains × 8 bones, 4 substeps × 2 iterations: **0.40–0.52 ms/frame** with
  two other agents loading the CPU (research: 0.42–0.47). 1×8 0.30–0.40 ms,
  16×8 0.60–0.63 ms, 64×16 1.6–2.1 ms.
- bpy armature, 8×8 = 64 bones, 150-frame bake: sim **0.55–0.67 ms/frame**.
  Total including `frame_set`, recording and keying is **13–17 ms/frame**
  (2.0–2.5 s wall, 9600 keys). Blender's own cost is the bill, not the sim.
- Settle: an upright 8-bone spine with a 4 m/s² root step for 0.25 s leans
  120 mm at the tip, overshoots **0.001 %** of peak, is back within 2 % of peak
  **1.85 s** after the forcing stops, and finishes at 4e-9 m.
- Stretch over 10 s (4 chains × 8, horizontal soft chains swinging, root
  shaken): PBD alone at 4×2 is **2.07 %**, at 8×4 **0.18 %**. With the
  follow-the-leader clamp at 4×2 it is **0.100 %**. That floor is 0.1 % when
  `stretch_limit` is 0.
- Layering: at w=0 the bake reproduces the keyframes exactly (0.0° error).
  At w=1 the chain lags the root by up to 3.8° and settles back onto the keyed
  pose (0.000° at the end).

Example:
```python
from forgekit import dynamics as D
specs = [D.ChainSpec.from_preset("tail", tail_bones, "tail"),
         D.ChainSpec.from_preset("ear_L", ["ear.L", "ear_tip.L"], "ear", blend=0.7)]
head = D.ColliderSpec("head", "sphere", bone="head", radius=0.11, offset=(0, 0.08, 0))
rep = D.bake(rig, specs, 1, 120, colliders=[head])
# rep["ms_per_frame_sim"], rep["max_stretch"], rep["action"] == "<src>_dyn"
```

Traps:
- **Tail-only angular springs buckle upright chains under gravity.** Each
  spring moves one particle, but that particle drags everything beyond it
  through the length constraint, so the root joint is 8× too soft. The fix is
  `carry=True` (the default): scale each bone's spring by the number of
  particles it carries. I also tried equal-and-opposite reaction torques. They
  made the chain *slower* and overshoot 5–26 %, so I rejected them.
- A follow-the-leader sweep on every substep cost 0.17 ms of the 0.53 ms
  frame. It now runs on the last substep only (`ftl="substep"` brings the old
  behaviour back).
- `np.cross` and `ndarray.sum` wrappers dominate at this array size. Use
  dot-only rotation formulas and `np.add.reduce`.
- `bake` used to overwrite the animator's keys on the chain bones. It now
  keys into a copied action and re-points `action_slot` (5.x slotted actions).
- In-place mode (`root_accel`) and world-space mode (`targets` moving) double
  count inertia if you use both together.

---

### ornament.py: guilloche, placement, mouldings

Placement is plain numpy. Only `Surface`/projection and geometry builders touch bpy.

| Function | Does |
|---|---|
| `guilloche_points(R, r, d, n_turns=None, samples, variant)` | Epitrochoid or hypotrochoid. `n_turns=None` gives exactly one closed period (R and r must be integers) |
| `guilloche_period(R, r)`, `guilloche_closure(...)` | `T = 2πr/gcd(R, r)`, and `\|P(T)−P(0)\|` |
| `resample_closed_2d/3d(points, n)` → `(pts, L)` | Even spacing by arc length, with no repeated seam point |
| `gap_stats(points)` | min gap, max gap, min/max ratio, seam gap error |
| `frames_closed(points, up, correct=True, normals=None)` → `(frames, info)` | Double-reflection transport. Holonomy is spread linearly in arc length about T. `info`: `holonomy_deg` (before correction), `seam_error_deg` (excess twist across the seam versus the uniform rate elsewhere), `max_step_error_deg` |
| `twist_profile(frames)`, `frames_open(points)` | Seam diagnostic, and frames for open paths |
| `Surface(obj)`, `project_to_surface(points, obj, offset, direction=None)` | BVH over the evaluated, world-space mesh with **vertex-interpolated normals**. Nearest point or ray cast |
| `project_curve_closed(points, obj, n)` | Project → re-space by arc length on the surface → re-project |
| `placement_matrices(frames, normals, offset)` | Local X = tangent, Z = surface normal |
| `seam_report(points, hol, seam)`, `ring_placement(count, radius, target, offset, center)` | Even ring of pieces, optionally on a surface. Returns `(matrices, SeamReport, P, N)` |
| `knurl_count(circumference, pitch)` | Integer count, so the knurl closes |
| `place_instances(name, src_obj, matrices)` | Joins copies of a mesh at the matrices |
| `MOULDINGS` | ogee (= cyma_recta), cyma_reversa, ovolo (= torus), scotia (= cavetto). Nine points each, research/04 §7 |
| `moulding_mesh(profile, path, name, width, height, closed, up, cap)` | Sweeps a profile along a 3D path. Watertight for closed and open paths. Faces outward from the centroid |
| `moulding_lathe(name, profile, radius, height)` | Solid of revolution, closed at the axis |
| `bevel_profile(obj, profile, width, segments)` | `profile_type='CUSTOM'` bevel. Writes interior points only |
| `guilloche_band(name, R, r, d, copies, radius, target=..., center, groove_radius, sink)` | Engraved band. With `target` it **follows the surface**. Sets `ob["surface_max_dev_m"]` and `ob["seam_gap_error"]` |

Measured:
- Closure: epitrochoid 96/40/30 **1.6e-13**, hypotrochoid 1.7e-14. A
  non-period span (1.3 turns) misses by 167 units.
- Resampling: 180 points on the guilloche give min/max gap **0.9958** (raw
  parameter sampling gives 0.143) and seam error 0.30 %. A trefoil at 240
  points gives 0.9997.
- Seam twist: a toroidal helix has holonomy **173.07°**. After correction the
  seam error is **1.5e-13°**. A trefoil goes from 173.78° to 7.2e-14°. A planar
  circle shows 0.0000° holonomy, and a symmetric saddle also shows 0°.
  Research measured 156° on a different helix. The spec was < 0.5°.
- BVH: 2000/2000 hits on a moved, non-uniformly scaled sphere, residual
  1.7e-7 m, 79 ms total. Normal error against the analytic ellipsoid:
  **0.02°** interpolated versus **3.64°** with face normals.
- ring_placement on the ellipsoid (72 pieces): gap ratio 1.0000, seam twist
  3e-15°, piece +Z equals the surface normal.
- guilloche_band, 6 copies on the ellipsoid: the centreline sits within
  **8.9e-8 m** of the surface, seam gap error 0.13 %, 49k verts, 0.84 s.
- bevel_profile: on a 2 m cube with a 0.5 bevel, the mid profile vertex sits
  0.207 from the corner for ovolo (a convex quarter circle is 0.207) and
  0.500 for scotia (a concave one is 0.500).

Example:
```python
from forgekit import ornament as O
dome = M.lathe("Dome", profile)
band = O.guilloche_band("Engine", 96, 40, 30, copies=12, radius=0.04,
                        target=dome, center=(0, 0, 0.2), groove_radius=0.0003)
mats, rep, P, N = O.ring_placement(O.knurl_count(2*math.pi*0.05, 0.004), 0.05,
                                   target=dome, offset=0.0005, center=(0, 0, 0.1))
beads = O.place_instances("Beads", bead_obj, mats)
assert rep.seam_error_deg < 0.5 and rep.gap_ratio > 0.99
O.bevel_profile(base, "ogee", width=0.004)
```

Traps:
- **The custom-profile widget's frame is not the research tables' frame.**
  Blender's path runs from (1,0) to (0,1), and a quarter circle centred on
  (0,0) is convex. The research tables run from (0,0) to (1,1).
  `bevel_profile` maps each point to `(1−y, x)`. The test above confirms the
  mapping. The endpoints cannot be removed (RuntimeError), so only interior
  points are written.
- `find_nearest` returns the flat face normal, which steps ornament
  orientation on faceted geometry. `Surface` interpolates vertex normals
  barycentrically.
- Seam measurement: comparing frame 0 with frame N−1 directly is wrong,
  because they are one segment apart. Carry N−1 across the seam and compare
  the twist with the per-segment rate. That is `twist_profile`.
- `bmesh.ops.spin` on a profile that touches the axis leaves duplicated axis
  vertices and a zero-length edge (non-manifold). Fix: `remove_doubles` at a
  scale-relative tolerance, then `dissolve_degenerate`.
- After an even resample, chord points sit slightly inside a convex surface.
  Always re-project.

---

### materials.py additions

- `METAL_F0`: linear F0 for gold, silver, copper, aluminium, platinum,
  titanium, chromium, iron, brass, bronze, rose_gold. **All of these are
  estimates.** They are recalled from the real-time table (Lagarde/de Rousiers,
  Hoffman), not derived from Johnson & Christy. brass, bronze and rose_gold
  are not in that table at all. `METAL_F0_STATUS` labels each entry.
  `PALETTE` is unchanged; copper and silver differ between the two.
- `new_material(name)`: in 5.3 a new material already has a Principled node
  tree, and `Material.use_nodes` is deprecated (it warns and is due for
  removal in 6.0). This helper only touches it on older builds.
- `metal_pbr(name, metal, roughness, anisotropy, anisotropic_rotation, coat,
  coat_roughness, thin_film_thickness, thin_film_ior, breakup_scale,
  noise_stretch, brush_axis, tangent_axis, bump_strength, roughness_spread,
  return_report)`. Every Principled input is checked with `in bsdf.inputs`.
  The report goes to `mat["forge_inputs_applied"/"_missing"]`. In 5.3 all of
  Anisotropic, Anisotropic Rotation, Coat Weight, Coat Roughness, Thin Film
  Thickness and Thin Film IOR are present and applied. Anisotropy wires a
  radial Tangent node, so the streak direction is explicit.
  **Brushed grain:** Texture Coordinate(Object) → Mapping (scale 1 along
  `brush_axis`, `noise_stretch` across) → Noise. Measured with a top-down
  render: grain correlation length x/y is **1.00** at stretch 1 and **6.8** at
  stretch 8.
- `cavity_patina(obj_or_mat, colour, amount, distance, mode="BOTH",
  patina_roughness, only_local=False)`. The AO node (`inside=False`; sockets
  `Distance`, output `AO`; checked in 5.3) and/or Geometry.Pointiness feed a
  mask that mixes into Base Color and Roughness. Whatever already fed those
  inputs is preserved (wear_edges, metal_pbr breakup). Measured as a rendered
  mask, cube on a plane: **crevice 0.91, open floor 0.000, cube top 0.000**.
  **Trap:** research suggested `only_local=True`, but that hides the crevice
  between two objects. I measured a mask of 0.00 there, so the default is
  False.

```python
m = MAT.metal_pbr("Brushed", "aluminium", roughness=0.28, anisotropy=0.8,
                  noise_stretch=50, coat=1.0, coat_roughness=0.04)
MAT.assign(part, m)
MAT.cavity_patina(part, colour=(0.25, 0.55, 0.45), amount=0.7, distance=0.01)
```

Before/after: `renders/look_metal_before_after.png`, the same brass sphere and
the same lights. Cycles, 256 px, 32 spp, 4.0–6.3 s per frame. `metal()` is on
the left. On the right is `metal_pbr(anisotropy 0.8, coat 1.0, stretch 40)`:
the key highlight smears into a horizontal band and the grain reads as
brushed. That difference is real. The coat lobe is **not** separately readable
at 256 px. `look_compare` calls the pair "within tolerance" (contrast Δ 0.007),
yet 37.6 % of pixels change by more than 2 %. The change is in highlight shape,
which value and palette statistics don't measure.

---

### shading.py: look-dev in numbers

| Function | Returns |
|---|---|
| `light_rig_from_scene_bounds(objects=None, key, fill, rim, reference_size=2.0)` | Calls `render.three_point`, then fixes its energy scaling and re-aims every light at the bounding-box centre. Returns energies, distances, `key_fill_ratio` and `key_rim_ratio` (irradiance ratios) |
| `contact_shadow_check(obj, ground_z, tolerance)` | `status` contact / floating / intersecting, `gap_m`, `fix_dz`, `contact_verts`, `footprint_m`, `com_inside`. Uses the evaluated mesh |
| `value_structure(png, shadow=.25, highlight=.75, background=None/"alpha"/"corners")` | shadow/mid/highlight fractions, histogram, percentiles, contrast, clipped/crushed, key, **verdict** flat/form/harsh, plus notes naming the fix |
| `image_palette(png)` | k-means colours, mean saturation, warmth (R−B) |
| `colour_palette_check(obj_or_mat)` | Per material: colour (traced through Mix nodes), saturation, luminance, metallic, nearest METAL_F0, and flags: `DIELECTRIC_GREY_METAL`, `DARK_METAL`, `PARTIAL_METALLIC`, `ALBEDO_TOO_DARK/BRIGHT`, `OVERSATURATED`, `COLOUR_UNKNOWN` |
| `look_compare(ref_png, cand_png)` | A plain JSON-safe dict for compare.py: `reference`, `candidate`, `delta` (mean, contrast, s/m/h, saturation, warmth, histogram EMD, palette distance), `scores` 0..1, and `verdict` strings |

Measured:
- Key irradiance at the subject is **79.4 W/m² for 0.2 m, 2 m and 20 m**
  subjects (identical). `three_point(scale=s)` alone is off by a factor of s.
- A grey sphere lit by a white world alone reads `flat` (contrast 0.03, 98 %
  mids). Under the rig it reads `form` (contrast 0.36). `look_compare`
  reports "candidate is flatter: contrast 0.03 vs 0.36".
- Kit `PALETTE` entries the grey-metal check flags: brass_dark, steel_blued,
  gunmetal. These are deliberate oxide finishes, so read the flag with
  judgement.

```python
info = SH.light_rig_from_scene_bounds([hero])
assert SH.contact_shadow_check(hero, 0.0)["status"] == "contact"
p, _ = RN.shot("out/hero")
v = SH.value_structure(p, background="corners")
if v["verdict"] == "flat": ...           # v["notes"] says what to change
print(SH.colour_palette_check(hero)["materials"])
diff = SH.look_compare("ref.png", p)      # plain dict
```

Traps:
- `render.three_point` scales energy linearly with `scale`, but irradiance
  falls with distance². The wrapper multiplies energy by s².
- `matrix_world` is stale after setting `rotation_euler` until
  `view_layer.update()` runs. The aim check failed on that until I fixed it.
- After `read_factory_settings` every held `Scene` reference is dead
  (`ReferenceError: StructRNA of type Scene has been removed`). Fetch
  `bpy.context.scene` again.
- Measuring procedural grain from a render aliases at 128 px. Keep the noise
  scale low and Detail at 1 for the measurement only.

### Not verified
- METAL_F0 values against measured optical data.
- The VRM export against the VRMC_springBone schema.
- Dynamics on a skinned mesh or a Rigify rig: the tests use a 64-bone
  synthetic armature. The live handler has not been timed inside a running
  viewport.
- The `ear` preset against a real character.
- Pointiness-mode patina is built but has no measurement, because it depends
  on mesh density.
- Glossy/coat appearance is judged only by eye on one 256 px render.


---

## Baking, process detail, image comparison, and the MCP server

Four modules: `bake`, `detail`, `compare` and `server`. All numbers below were measured in this
container: Blender 5.3.0, Python 3.13, 4 shared CPU cores, no GPU, with two other agents running
at the same time.

Packages added to `/home/user/bpyenv`: `mcp==2.3.0` (required by `server.py`), `xatlas==0.0.11` (optional) and
`pillow==13.0.0` (optional). `compare.py` has its own PNG decoder and `bake.py` falls back to Smart UV Project,
so neither optional package is required.

---

### `forgekit.bake`: getting procedural materials through glTF export

Procedural shader trees are not part of glTF, so the exporter writes a flat colour. The steps are: unwrap,
bake each channel to an image, rewire the material to those images, then export and check the result.

```python
from forgekit import bake as BK
BK.uv_unwrap(ob, method="xatlas")        # or "smart". The result says which one ran: {"method", "fallback", ...}
baked = BK.bake_material(ob, mat, resolution=1024, samples=8, out_dir="out/tex")
BK.rewire_to_images(mat, baked)          # base colour (sRGB), packed ORM, normal map
res = BK.export_textured_glb(ob, None, "out/asset.glb")   # re-imports the file and checks it
assert res["ok"], res["problems"]
```

- Channels: `base_color`, `roughness`, `metallic`, `normal` (tangent space, +Y), `ao`. Base colour and metallic
  are baked by feeding their input socket into an Emission shader and baking EMIT. A DIFFUSE/COLOR bake of a
  metal comes out **black**, because Principled multiplies the diffuse colour by (1 - metallic).
- `rewire_to_images` packs occlusion, roughness and metallic into one ORM image (R = occlusion, G = roughness,
  B = metallic). It wires occlusion through a `glTF Material Output` group so the exporter writes
  `occlusionTexture`. The original procedural tree is kept as `<mat>_procedural` (fake user), so you can bake again.
- `export_textured_glb` checks the result in two ways. It reads the GLB's JSON chunk without Blender: every
  material must have `baseColorTexture` and `metallicRoughnessTexture`, and image sizes come from the embedded PNG
  headers. It also re-imports the file into a fresh factory-settings `bpy` process, so you know it survives a
  clean scene.
- xatlas works on triangles. If a chart seam cuts a quad diagonal, the mesh is triangulated and charted again
  (`triangulate_on_conflict=True`), so UVs are exact. glTF stores triangles anyway.
- `bake_material(..., samples=8)` timings on a 768-face lathed brass part with a bevel modifier:

| resolution | base_color | roughness | metallic | normal | ao | all 5 |
|---|---|---|---|---|---|---|
| 1024² | 0.72 s | 1.95 s | 0.58 s | 1.92 s | 2.30 s | **7.98 s** |
| 2048² | 2.74 s | 7.60 s | 2.76 s | 7.70 s | 9.01 s | **31.9 s** |

  The research quoted 1.90 s for a 2048² normal bake. On this part, with shared cores, it measured
  **7.5–8.3 s**: 4× slower. Plan on about 8 s per 2048² channel.

### `forgekit.detail`: detail that comes from how the part was made

```python
from forgekit import detail as D
rep = D.apply_detail(part, "turned", seed=7, intensity=1.0)     # form -> surface -> wear
D.rules_for("cast")   # {'form': ['form_cast_radii_and_draft'], 'surface': ['surface_cast_parting_line', 'surface_cast_sand_skin'], 'wear': ['wear_curvature']}
```

Supported processes are `cast`, `forged`, `turned`, `stamped`, `machined` and `engraved`. A part gets only the
rules for its own process, and tagging one part with a second process raises an error. Each rule is a named
function, and its docstring and report give the amplitudes in metres:

| process | form | surface marks |
|---|---|---|
| turned | 0.8 mm break-edge chamfer | feed rings, 0.5 mm feed, 0.8 mm nose radius, scallop Rt = f²/8r = **39 µm** |
| cast | 2.5 mm fillets + 1.0° draft (1.7 mm per 100 mm) | parting-line seam **60 µm** × 0.35 mm wide; sand skin **25 µm**, 1.2 mm features |
| forged | 1.5 mm soft radii | hammer facets, 6 mm cells, **60 µm** dishes |
| stamped | punch side 1.2 mm radius, burr side 0.18 mm | draw streaks **4 µm**, stretched 1:40 |
| machined | 0.3 mm chamfer | milling lay, 0.4 mm pitch, **5 µm** |
| engraved | 0.3 mm chamfer (machined blank) | recessed inset **0.3 mm** deep, border 12 % of the panel |
| (all) | | wear: curvature-driven edge wear (full at R < 1.5 mm) and cavity, 40 µm edge pull |

- The tier order is enforced. A tier can be applied once, and applying `surface` after `wear` raises
  `DetailOrderError`. Form bevels are applied into the mesh, so surface marks are displaced on the bevelled shape.
- Surface marks become geometry when refining the mesh to at least 4 samples per period fits within
  `budget_verts` (default 400k). Otherwise they become a Bump node in the material with Distance set to the
  amplitude in metres, which Cycles and `bake.py` pick up but Workbench does not. Use `mode='geometry'|'shader'`
  to force one path. The report's `path` field says which one ran.
- Wear does not use UVs. It comes from the discrete mean curvature of the mesh (`D.vertex_curvature`), stored as
  the `forge_wear_edge`, `forge_cavity` and `forge_wear` (colour) attributes and wired into the material through
  Attribute nodes.
- Output is deterministic: the same seed gives byte-identical geometry.
- **Before/after check (Workbench, 12 cm turned spindle):** in the full view the feed rings show only as faint
  banding (1.2 % of pixels change). They are clear in a close-up (about 0.05 mm per pixel, 11.7 % of pixels
  change). Rings with a 0.5 mm pitch need at least about 3 px per period to read. In a whole-object view they are
  surface texture, not a feature.

### `forgekit.compare`: comparing your render with a reference image

```python
from forgekit import compare as CMP
d = CMP.compare_renders("ref.png", "mine.png")
# d["silhouette_iou"], d["silhouette"]["extra_frac"/"missing_frac"/"centroid_shift_px"],
# d["value"]["hist_emd"/"band_delta"(shadow, mid, highlight; mine minus ref)], d["edges"]["f1"]
w = CMP.localise_error("ref.png", "mine.png", grid=(4, 4))
w["worst"][0]   # {'row','col','kind': silhouette|value|edges, 'hint': 'candidate is missing mass here', ...}
r = CMP.render_pair(ob, "ref.png", camera_loc=(0, -4, 1), lens=50)   # matched-camera render, then compare
```

- The silhouette comes from alpha when the image has it. Otherwise it is the distance from the border-median
  background colour, which fails for a dark object on a dark backdrop. Render with a transparent film;
  `render_pair` and `forge_compare` do this.
- Each error is counted once per grid cell. A shifted outline counts as a silhouette error, not also as a
  value error.
- The module is pure numpy and does not import `bpy` at load time.
- Test result: IoU of a disc against an offset disc matches the analytic lens area to within 0.0004.

### `forgekit.server`: the MCP server

Start it:

```bash
cd /home/user/Fun-project-/forge/kit
/home/user/bpyenv/bin/python -m forgekit.server        # MCP over stdio. Logs go to stderr; stdout carries only JSON-RPC.
# optional: FORGE_SESSION_DIR=/path (default $TMPDIR/forge_sessions)   FORGE_IDLE_S=1800
```

Client configuration (Claude Desktop / Claude Code `mcpServers` entry):

```json
{"forge": {"command": "/home/user/bpyenv/bin/python", "args": ["-m", "forgekit.server"],
           "cwd": "/home/user/Fun-project-/forge/kit"}}
```

Example call with the Python SDK (`mcp` 2.3.0):

```python
from mcp import Client, StdioServerParameters
params = StdioServerParameters(command="/home/user/bpyenv/bin/python", args=["-m", "forgekit.server"],
                               cwd="/home/user/Fun-project-/forge/kit")
async with Client(params) as c:
    sid = (await c.call_tool("forge_session_open", {})).structured_content["session_id"]
    await c.call_tool("forge_exec", {"session_id": sid, "code": "bpy.ops.mesh.primitive_cube_add(); result = 'ok'"})
    r = await c.call_tool("forge_render", {"session_id": sid, "view": "persp", "resolution": 768})
    png_b64 = r.content[1].data          # content[0] is the text summary, content[1] the image block
```

Tools: `forge_session_open`, `forge_session_close`, `forge_exec`, `forge_scene`, `forge_inspect`,
`forge_render`, `forge_checkpoint`, `forge_rollback`, `forge_compare`, `forge_export`. Each returns a short text
summary plus `structuredContent`. Renders also return a PNG image block.

- **Stateless at the MCP level.** `session_id` is a handle the server creates and you pass back as a normal
  argument (SEP-2567). Each handle has one `bpy` worker process. The server process never imports `bpy`.
- **forge_exec**: `bpy`, `bmesh`, `mathutils` and the forge-kit modules are preloaded. Assign `result = ...` to
  return a value. A checkpoint is taken first. If the code raises, you get
  `{kind, message, op, likely_cause, suggested_fix, state_at_failure{active_object, mode, selected, engine},
  traceback, rolled_back}` and the scene is restored to the checkpoint. About 18 failure kinds are classified,
  including adding a light that becomes active (`bpy_operator_context_active`), the wrong mode, removed APIs
  (`use_auto_smooth`, `action.fcurves`, `BLENDER_EEVEE_NEXT`), name typos with close matches and the Principled
  socket renames, stale references, and bad enum values (the valid values are listed).
- **Crash and timeout supervision.** If `bpy` segfaults (exit code -11) or a call exceeds `timeout_s`, the worker
  is restarted from the newest checkpoint, the call returns `worker_crashed` or `timeout`, and the session keeps
  going. An idle worker is shut down after `FORGE_IDLE_S` seconds and restarted from its checkpoint on the next
  call.
- **Image limits.** Workbench images are at most 1000 px on the long side (1000×1000 ≈ 1333 tokens, using
  w×h/750). Cycles images are at most 640 px. No image block goes over 2500 tokens. The default is 768 px
  (≈ 786 tokens). EEVEE is refused.
- Measured latencies (client side, `tests/test_server.py`): connect 0.47–0.59 s, `tools/list` 4 ms,
  `forge_session_open` 0.52–0.55 s (worker start), `forge_exec` 8–15 ms, `forge_scene` and `forge_inspect`
  3 ms, `forge_render` Workbench 768 px 0.51–0.53 s, `forge_checkpoint` 7 ms, `forge_rollback` 10 ms,
  `forge_compare` 0.62–0.85 s, `forge_export` 28 ms, Cycles 384 px at 8 spp 0.94–1.39 s, segfault plus
  recovery 0.48 s, restarting a reaped worker 0.49 s.

Tests (each prints PASS/FAIL per check and exits nonzero on failure):

```bash
/home/user/bpyenv/bin/python tests/test_bake.py     [out_dir]   # ~60 s (includes the 2048² bakes)
/home/user/bpyenv/bin/python tests/test_detail.py   [out_dir]   # ~7 s, writes the before/after renders
/home/user/bpyenv/bin/python tests/test_compare.py  [out_dir]   # ~2 s
/home/user/bpyenv/bin/python tests/test_server.py               # ~14 s, the MCP acceptance test
```


---

# Findings from the face/anim build agent (stopped early, nothing committed by it)

Measured in-container before the stop. Treat as measurements, not finished modules.

## 1. The foot_slide "after" number is mostly a metric artefact
examples/03_walk_cycle.py passes expected_root_speed = path_length / 24 (about 0.0635 m/frame).
That is the foot's whole path including swing, not stance speed. Planted frames move only
0.005-0.010 m/frame, yet abs(d - expected) reports about 0.0635 anyway.
=> The 101 mm -> 64 mm improvement reported earlier is NOT a real improvement. Fix the
   expected speed (estimate it from contact frames) before quoting any foot_slide figure.

## 2. The walk is in place, with no root motion
In world space the planted foot moves backward at stride speed during stance (about
0.06 m/frame, frames 2-9). A speed threshold measured in world space will not find
contacts. plant_feet needs either treadmill mode (measure foot velocity relative to the
body/root) or real root motion added before locking.

## 3. Rig structure for plant_feet
- FK chain: shin_fk.L -> parent thigh_fk.L -> parent MCH-thigh_parent.L; foot_fk.L parent MCH-foot_fk.L
- FK controls have no constraints, no rotation locks, XYZ Euler.
- DEF-foot.L follows foot_tweak.L.
- Plan: IK constraint (chain 2, use_tail) on shin_fk targeting a keyed empty, world Copy
  Rotation on foot_fk, then bake FK controls parents-first with convert_space.

## 4. Rigify face metarig (Blender 5.3), source landmarks
- 92 descendant bones under `face` (faces.super_face)
- eye.L head (0.052, -0.121, 1.894)
- nose tip (0, -0.197, 1.845)
- jaw tail / chin (0, -0.152, 1.739)
- ear loop ear.L .. ear.L.004, starting near (0.092, -0.031, 1.862)
- brow.B.L chain; mouth corner at lip.T.L.001 tail (0.035, -0.149, 1.807)
- Rigify's own `jaw` head sits at (0, -0.094, 1.744): midline, near chin height, below the
  ear-canal line. Consistent with research 05 (jaw pivot belongs at the ear-canal line).
  Moving it and regenerating is still untested.

## 5. glTF animation export, Blender 5.3
- gltf_action_filter matches actions by Python id(): fragile. Use
  export_animation_mode='ACTIONS' instead.
- The exporter skips muted NLA strips; each single-strip track becomes one named animation.
- A strip needs action_slot set or the exporter ignores it.

## Baseline reproduced by the agent
- examples/02 rebuilt: 706 bones, 160 deform, 16.7 s wall.
- examples/03 walk: score 52/100; foot_slide 0.101 m before polish, 0.064 m after (see 1);
  jerk 0.340 -> 0.299 on DEF-foot.L/R.
