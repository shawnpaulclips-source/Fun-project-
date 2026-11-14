# forge — 03 Animation

Design doc. Owner: animation. Siblings: `01-geometry.md` (Blender/bpy, meshes, UV, PBR), `02-rigging.md` (skeletons, skinning, controls).

Target: a free, self-hosted MCP server that Claude drives to produce production animation on rigged assets, headless, on CPU, in a Linux container.

Everything marked **[verified]** was run in this container against Blender 5.3.0 (`bpy` 5.3.0, CPython 3.13.18) on 2026-11-14. Numbers are measured, not estimated.

---

## 1. Verdict (10 lines)

1. **Build it on the `bpy` Python module, not the Blender CLI.** `pip install bpy==5.3.0` matches this container's Python 3.13 exactly, installs in ~1.1 GB, imports in 0.6 s, and — critically — *keeps a window and screens*, so the Graph Editor operator set works headless. `blender --background` does not. **[verified]**
2. **The Graph Editor is forge's engine.** `decimate`, `ease`, `breakdown`, `blend_to_neighbor`, `match_slope`, `push_pull`, `shear`, `butterworth_smooth`, `euler_filter`, `equalize_handles` all run under `context.temp_override(window, screen, area, region, space_data)` with an area retyped to `GRAPH_EDITOR`. These are literally the pro animator's verbs. Do not reimplement them. **[verified]**
3. **Blender 5.x deleted `action.fcurves`.** Everything goes through slot → layer → strip → channelbag. Any code or tutorial older than 4.4 is wrong. Pin the version and write one accessor shim.
4. **The single most important artifact is the timeline IR** (§9.1), not any generator. Claude authors a JSON document in frames, named poses, easing presets, per-chain offsets and passes; forge compiles it to F-curves. That is the whole product.
5. **Structure the server around the four pro stages** — block (stepped) → breakdown → spline → polish/offset. They map 1:1 onto an agent's iterate-and-critique loop, and they stop Claude from doing the thing it does by default: two keys and a lerp.
6. **Claude gets eyes from Workbench, cheaply.** Workbench renders 0.112 s/frame at 480×270 on llvmpipe software Vulkan, no GPU, no Xvfb. A 24-frame playblast is 2.7 s. EEVEE is 23 s/frame — useless. Contact sheets via ffmpeg (0.35 s). **[verified]**
7. **Claude's real eyes are numbers.** Sampling 40 bones × 240 frames of world-space transforms costs 0.04 s; 38 400 `fcurve.evaluate()` calls cost 0.014 s. Full critique metrics are free. The critique report (§9) is the highest-leverage piece after the IR.
8. **Procedural wins for locomotion and secondary; Claude wins for acting; ML is a narrow third.** Footstep-driven walk/run and spring/noise passes are deterministic, tunable, and licence-clean. Keyframe-conditioned in-betweening (CondMDI lineage, MIT code) is the only ML worth wiring, and it is v2.
9. **The licence trap is the biggest project risk.** Nearly all free motion data and nearly all ML weights trace to AMASS/SMPL, which are non-commercial. CMU mocap is the one dataset usable in a sold product. LAFAN1 is CC BY-NC-**ND** — retargeting it is a derivative, so it is unusable, not merely restricted.
10. **No GPU, no physics-RL.** DeepMimic/AMP/PHC/PULSE need Isaac Gym. Drop them. Bullet-based ragdoll/cloth bakes in Blender are the CPU-affordable substitute.

---

## 2. Environment reality (measured first, because it constrains everything)

Container as found: Linux 6.18, 4 cores, 15 GB RAM, 30 GB free on `/`, Python 3.13.18, `ffmpeg 6.1.1`, no Blender, no GPU.

### 2.1 Install

```bash
python3 -m venv /opt/forge/venv
/opt/forge/venv/bin/pip install --upgrade pip
/opt/forge/venv/bin/pip install bpy==5.3.0          # ~236 MB wheel, 1.1 GB installed  [verified]
/opt/forge/venv/bin/pip install \
    "mcp>=1.2" numpy==2.5.4 scipy pillow pydantic
# ffmpeg: present (6.1.1). If not:  apt-get install -y ffmpeg
```

`bpy==5.3.0` declares `requires_python == 3.13.*`. That is a hard match with this container and a hard *mismatch* with 3.12 or 3.14 — the wheel does not exist for them. **Pin both.** **[verified]**

| item | measured |
|---|---|
| `pip install bpy==5.3.0` | 11 s from warm cache, 1.1 GB on disk **[verified]** |
| `import bpy` + factory reset | 0.63 s **[verified]** |
| Blender version reported | `5.3.0`, `bpy.app.background == True` **[verified]** |
| GPU backend in container | `VULKAN`, renderer `llvmpipe (LLVM 20.1.2, 256 bits)` **[verified]** |
| build options | `codec_ffmpeg=True`, `cycles=True`, `alembic=True`, `usd=True`, `openvdb=True`, `bullet=True` **[verified]** |
| bundled addons | `io_scene_gltf2` (v5.3.43), `io_scene_fbx`, `rigify` **[verified]** |

### 2.2 Blender release landscape (as of 2026-11-14)

`download.blender.org/release/` lists `Blender5.0` … `Blender5.3`, with `5.3.0` the newest Linux x64 tarball and `5.2.x` carrying five patch releases (`.0`–`.4`) — the patch-count signature of an LTS, consistent with the 3-minor-version LTS cadence (3.3, 3.6, 4.2, 4.5, → 5.2). `5.0` shipped 2025-11-18; `4.5 LTS` is supported to July 2027. **[verified: directory listing]** — *blender.org itself is behind Cloudflare and refused automated fetches, so the LTS badge is inferred, not read. Confirm before pinning for a long-lived deployment.*

Recommendation: pin `bpy==5.3.0` now for the Graph Editor op set and current exporter; move to `5.2.x` only if forge needs the longer support window, after re-testing §3 against it.

### 2.3 What degrades without a GPU

| path | cost | verdict |
|---|---|---|
| Workbench render, 320×180 | **0.054 s/frame** | contact sheets, thumbnails |
| Workbench render, 480×270 | **0.112 s/frame** (24 frames = 2.70 s) | **default playblast** |
| Workbench, first frame of a cold session | 0.8–1.4 s | llvmpipe shader compile — warm it once at startup |
| Workbench render, 960×540 | 0.373 s/frame (24 frames = 8.95 s) | review render only |
| EEVEE, 480×270 | **23.4 s/frame** | never |
| Cycles CPU, 480×270 | 4.42 s/frame | never, for animation |
| `bpy.ops.render.opengl` | **fails**: `Cannot use OpenGL render in background mode (no opengl context)` | unavailable — use Workbench full render, it looks the same |

All **[verified]**. Workbench shading controls present: `scene.display.shading.light ∈ {STUDIO, MATCAP, FLAT}`, `color_type ∈ {MATERIAL, OBJECT, RANDOM, VERTEX, TEXTURE, SINGLE}`, plus `show_object_outline`. `light='FLAT'` + `color_type='SINGLE'` + black single colour = a true silhouette pass for the silhouette metric. **[verified]**

Video out: `scene.render.image_settings.media_type` is new in 5.x and gates the codec list. `FFMPEG` is **not** in `file_format` until you set `media_type='VIDEO'`; then containers `{MPEG4, MKV, WEBM, …}` and codecs `{AV1, H264, H265, …}` appear. **[verified]** Either path works; ffmpeg CLI is easier to control for GIF palettes.

```bash
# contact sheet, 6x4 grid        0.35 s  [verified]
ffmpeg -y -i seq/f_%04d.png -vf "scale=240:-1,tile=6x4" -frames:v 1 sheet.png
# looping gif, palette-optimised 0.30 s  [verified]
ffmpeg -y -framerate 24 -i seq/f_%04d.png \
  -vf "scale=360:-1,split[s0][s1];[s0]palettegen=max_colors=64[p];[s1][p]paletteuse" -loop 0 pb.gif
# mp4                             0.56 s  [verified]
ffmpeg -y -framerate 24 -i seq/f_%04d.png -c:v libx264 -pix_fmt yuv420p -crf 26 pb.mp4
```

### 2.4 Disk budget

| | MB |
|---|---|
| `bpy` 5.3.0 | 1100 |
| mcp + numpy + scipy + pillow | ~180 |
| CMU mocap BVH (full, cgspeed conversion) | ~1500 |
| per-asset working dir (blend + playblast cache) | 20–150 |
| **v0/v1 total** | **~3 GB** |
| optional: one in-betweening checkpoint + torch CPU | +2600 |

---

## 3. The authoring substrate

### 3.1 Slotted actions — the 4.4/5.0 break

Blender 4.4 introduced slots; **5.0 removed the legacy accessors**. `action.fcurves`, `action.groups`, `action.id_root` are gone. Confirmed live: `hasattr(action, 'fcurves') == False` on 5.3.0. **[verified]**

The hierarchy is now:

```
Action
└── slots[]            ActionSlot    — one per animated datablock; identifier = 2-char ID prefix + name, e.g. "OBRig"
└── layers[]           ActionLayer   — currently exactly one, named "Layer"
    └── strips[]       type 'KEYFRAME'
        └── channelbags[]  ActionChannelbag — one per slot
            └── fcurves[]
            └── groups[]
```

**[verified]** on a fresh keyframe insert: `action.slots == [('OBRig','OBJECT','Rig')]`, `action.layers == [Layer]`, `layer.strips == [KEYFRAME]`.

Accessor shim — write this once, use it everywhere:

```python
from bpy_extras import anim_utils

def channelbag(id_block, create=True):
    """The channelbag holding id_block's own F-curves, or None."""
    ad = id_block.animation_data
    if not ad or not ad.action:
        return None
    if create:
        return anim_utils.action_ensure_channelbag_for_slot(ad.action, ad.action_slot)
    return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)

def fcurve(id_block, data_path, index=0, group=""):
    cb = channelbag(id_block)
    return cb.fcurves.ensure(data_path, index=index, group_name=group)
```

API surface, all **[verified]** present on 5.3.0:

- `anim_utils.action_get_channelbag_for_slot(action, slot) -> ActionChannelbag | None`
- `anim_utils.action_ensure_channelbag_for_slot(action, slot) -> ActionChannelbag` (creates layer + strip)
- `anim_utils.action_get_first_suitable_slot(action, target_id_type)`
- `anim_utils.animdata_get_channelbag_for_assigned_slot(anim_data)`
- `action.fcurve_ensure_for_datablock(datablock, data_path, index=, group_name=)` — creates layer, strip, slot and assigns it; requires the action already assigned
- `channelbag.fcurves.find(data_path, index=)` / `.new(data_path, index=, group_name=)` / `.ensure(data_path, index=, group_name=)`
- `action.slots.new(id_type, name)`; `slot.target_id_type` replaces the old `action.id_root`
- `action.pose_markers.new(name)` with `.frame` — works, and is the right place to stamp `contact_L`, `passing_R`, `apex` **[verified]**
- `action.use_frame_range`, `action.frame_range`, `action.use_cyclic` **[verified]**

Also removed in 5.0 and worth knowing because old scripts carry them: `INSERTKEY_XYZ_TO_RGB` option on `keyframe_insert()`, `action.layer_prev/next` ops, `space_data.action` in the Dope Sheet context (use `context.active_action`), `armature.bones[].select/select_head/select_tail`.

### 3.2 Keyframes, handles, interpolation

`channelbag.fcurves[n].keyframe_points`, a `FCurveKeyframePoints` collection:

```python
insert(frame, value, *, options={'REPLACE'|'NEEDED'|'FAST'}, keyframe_type='KEYFRAME') -> Keyframe
add(count); remove(keyframe, *, fast=False); clear(); sort(); deduplicate(); handles_recalc()
```

**Insert with `options={'FAST'}` in bulk, then one `sort()` + `handles_recalc()` + `fcurve.update()`.** `FAST` skips per-insert curve recalculation; without it a 240-key insert is quadratic.

`Keyframe` fields that matter:

| field | notes |
|---|---|
| `co` | `(frame, value)`. Raw — does *not* move handles |
| `co_ui` | same, but drags handles with it like the Graph Editor transform |
| `interpolation` | `CONSTANT` (= stepped, blocking), `LINEAR`, `BEZIER`, plus `SINE/QUAD/CUBIC/QUART/QUINT/EXPO/CIRC/BACK/BOUNCE/ELASTIC` |
| `easing` | `AUTO`, `EASE_IN`, `EASE_OUT`, `EASE_IN_OUT` — applies to the non-Bezier modes |
| `handle_left/right` | `(frame, value)` absolute, not relative |
| `handle_left_type/right_type` | `FREE`, `ALIGNED`, `VECTOR`, `AUTO`, `AUTO_CLAMPED` |
| `back`, `amplitude`, `period` | overshoot/elastic parameters |
| `type` | `KEYFRAME`, `BREAKDOWN`, `MOVING_HOLD`, `EXTREME`, `JITTER` — **cosmetic only, but forge should set it honestly**, because it is how a human reading the file sees the structure Claude intended |

**Blender's default handle extent is exactly 1/3 of the span.** Measured on a 2-key curve at frames 1 and 13: `k0.handle_right == (5.0, 0.0)`, `k1.handle_left == (9.0, 1.0)` — 4.0 of a 12-frame span = 0.3333. **[verified]** That 1/3 is the anchor for all easing math in §5.5.

`fcurve.evaluate(frame)` samples the curve including modifiers, without touching the depsgraph. 38 400 calls in 0.014 s ≈ 2.7 M/s. **[verified]** This is the cheap path for every curve-space metric.

### 3.3 F-curve modifiers — procedural layers, from Python

All seven types instantiate headless: `fc.modifiers.new(type=T)` for `T ∈ {NOISE, CYCLES, STEPPED, GENERATOR, FNGENERATOR, ENVELOPE, LIMITS}`. **[verified]**

**Noise** — life, idle, micro-sway:

| field | meaning | forge range |
|---|---|---|
| `strength` | amplitude in the channel's own units (radians for rotations!) | 0.004–0.03 rad (0.25–1.7°) for micro-sway |
| `scale` | period in frames | 6–14 for breath-rate jitter, 2–4 for nerves |
| `phase` | seed — **give every channel a different one** or the whole rig vibrates in lockstep | `hash(bone)%997` |
| `depth` | octaves; `lacunarity`/`roughness` only matter when `depth > 0` | 0 or 1 |
| `blend_type` | `REPLACE/ADD/SUBTRACT/MULTIPLY` — **always `ADD`** | `ADD` |
| `use_legacy_noise` | legacy can exceed ±1 | leave `False` |

All fields **[verified]** settable and `fc.evaluate()` reflects them.

**Cycles** — loops without duplicating keys: `mode_before/mode_after ∈ {NONE, REPEAT, REPEAT_OFFSET, MIRROR}`, `cycles_before/after` (0 = infinite). `REPEAT_OFFSET` is the one for root-motion travel; `REPEAT` for everything in place; `MIRROR` for a symmetric cycle authored half-length.

**Stepped** — blocking preview without destroying the splined curve: `frame_step` (default 2.0), `frame_offset` for `1-3` vs `5-7` hold patterns, plus `use_frame_start/end` + `frame_start/end` to restrict. This is how forge shows a stepped blocking pass of a splined curve non-destructively.

Every modifier also inherits `mute`, `influence` + `use_influence`, `blend_in`/`blend_out`, `use_restricted_range`, `frame_start`/`frame_end`. `influence` is what forge dials to A/B a procedural layer.

### 3.4 Graph Editor operators, headless — the key finding

In the `bpy` module every Graph Editor op initially fails:

```
RuntimeError: Operator bpy.ops.graph.decimate.poll() failed, context is incorrect
```

But `bpy.context.window_manager.windows` has **1 window** and `bpy.data.screens` has **11 screens** (the factory startup file, `Layout`/`Animation`/…). The `Animation` screen already contains a `DOPESHEET_EDITOR` area, and any area's `.type` can be reassigned. With a full override the ops run: **[verified]**

```python
import contextlib, bpy

@contextlib.contextmanager
def graph_context(area_type='GRAPH_EDITOR'):
    win = bpy.context.window_manager.windows[0]
    area = win.screen.areas[0]
    prev = area.type
    area.type = area_type
    region = next(r for r in area.regions if r.type == 'WINDOW')
    try:
        with bpy.context.temp_override(window=win, screen=win.screen,
                                       area=area, region=region,
                                       space_data=area.spaces.active):
            yield
    finally:
        area.type = prev
```

Measured inside it: `graph.decimate(mode='ERROR', remove_error_margin=0.05)` took a 240-key curve to **47 keys in 0.036 s** with measured max absolute error **0.0434** — inside the requested bound. A hand-rolled Ramer–Douglas–Peucker at ε = 0.05 on the same data kept **74 keys** for the same tolerance class, because RDP measures 2D point-to-chord distance while Blender removes the key of least influence and re-evaluates the actual Bezier. **[verified]**

**Conclusion: do not write RDP. Call `graph.decimate`.** Keep an RDP fallback only for the case where no window exists (CLI mode).

The toolbox this unlocks, with exact signatures — this is most of forge's curve layer:

```python
graph.decimate(mode='RATIO'|'ERROR', factor=0.333333, remove_error_margin=0.0)
graph.ease(factor=0.0, sharpness=2.0)                  # align keys onto an ease-in/out curve
graph.breakdown(factor=0.0)                            # move key to an inbetween position, -1..1
graph.blend_to_neighbor(factor=0.0)
graph.blend_to_default(factor=0.0)
graph.match_slope(factor=0.0)                          # take slope from neighbours
graph.push_pull(factor=1.0)                            # exaggerate / reduce  ->  the exaggeration principle
graph.shear(factor=0.0, direction='FROM_LEFT'|'FROM_RIGHT')
graph.scale_average(factor=1.0)
graph.time_offset(frame_offset=0.0)                    # the offsetting pass, per selection
graph.smooth()
graph.gaussian_smooth(factor=1.0, sigma=0.33, filter_width=6)
graph.butterworth_smooth(cutoff_frequency=3.0, filter_order=4, samples_per_frame=1,
                         blend=1.0, blend_in_out=1)    # denoise a bake, keep the shape
graph.clean(threshold=0.001, channels=False)
graph.bake_keys()                                      # key every frame between selected
graph.euler_filter()                                   # fix euler jumps/flips after a bake
graph.equalize_handles(side='LEFT'|'RIGHT'|'BOTH', handle_length=5.0, flatten=False)
graph.interpolation_type(type=...)   graph.easing_type(type=...)   graph.handle_type(type=...)
```

Selection is the argument. forge's curve ops therefore always have the shape: *resolve targets → set `select_control_point` on exactly the keys in range → enter `graph_context()` → call op → clear selection.*

### 3.5 Rotation channels, gimbal lock, and the frame-40 flip

`pose_bone.rotation_mode ∈ {QUATERNION, XYZ, XZY, YXZ, YZX, ZXY, ZYX, AXIS_ANGLE}`. The channel paths differ (`rotation_quaternion[0..3]` vs `rotation_euler[0..2]`), so **the IR must carry rotation mode per bone and forge must never guess.**

forge's policy, and the reason for it:

| bone class | mode | why |
|---|---|---|
| spine, neck, head, shoulders, hips | `QUATERNION` | multi-axis, large arcs, gimbal-prone |
| limb FK joints | `QUATERNION` | same |
| hinge joints (elbow, knee) | single-axis Euler (`XYZ`, key one channel) | one readable curve the agent can reason about |
| IK targets, root | Euler `XYZ` | translation-dominant; curves must be human-legible |
| twist/roll bones | Euler, axis last in the order | avoid lock on the twist axis |

**The flip bug, demonstrated numerically. [verified]**

`Quaternion((1,0,0), radians(170))` = `(0.087, 0.996, 0, 0)`. `Quaternion((1,0,0), radians(190))` = `(0.087, −0.996, 0, 0)` — Blender hands back the negated-hemisphere representation. Dot product: **−0.985**. Key those two as-is at frames 1 and 41 and the component-wise F-curve interpolation passes through **0.0°** at frame 21 instead of 180°. The bone travels 340° the wrong way. That *is* "the arm flips on frame 40".

Negate the second quaternion so the dot is **+0.985**, and frame 21 evaluates to **180.0°** — the short arc, correct. Raw quaternion magnitude at the midframe is 0.9962, i.e. component lerp de-normalises slightly; renormalise or accept ≤0.4% error over a 20° span.

So, mandatory in forge, run on every write and after every import/bake:

```python
def quat_sign_continuity(cb, bone, frames):
    """Make adjacent quaternion keys lie in the same hemisphere."""
    fcs = [cb.fcurves.find(f'pose.bones["{bone}"].rotation_quaternion', index=i) for i in range(4)]
    keys = [[fc.keyframe_points[k].co[1] for fc in fcs] for k in range(len(fcs[0].keyframe_points))]
    for k in range(1, len(keys)):
        if sum(a*b for a, b in zip(keys[k-1], keys[k])) < 0.0:
            for i, fc in enumerate(fcs):
                fc.keyframe_points[k].co[1] = -keys[k][i]
                keys[k][i] = -keys[k][i]
    for fc in fcs:
        fc.update()
```

Metric (§9): `quat_hemisphere_violations` = count of adjacent key pairs with dot < 0. Threshold: **must be 0**. Any non-zero is a hard fail, not a warning — it is always a bug and always catastrophic visually.

For Euler channels the equivalent is `graph.euler_filter()` after a bake, plus a check for any `|Δ| > 180°` between adjacent keys on a single rotation channel.

**Gimbal avoidance rule forge enforces:** on an Euler bone, if any sampled frame puts the *middle* axis of the rotation order within 10° of ±90°, flag `gimbal_risk` and recommend a mode change. Cheap to compute from the sampled matrices.

### 3.6 Time, frame rate, subframes

```python
scene.render.fps = 24; scene.render.fps_base = 1.0        # 23.976 => fps=24, fps_base=1.001
scene.frame_start, scene.frame_end, scene.frame_step
scene.frame_set(frame, subframe=0.0)                       # subframe is a float 0..1
```

forge's canon: **24 fps internally, always.** Frames are integers in the IR. Export retimes. Reasons: every craft number in §5 is a 24 fps number; the 2s/3s conventions only make sense at 24; and mixing rates is where loop seams come from. `fps_base` handles 23.976/29.97 at export only.

Time remapping — `scene.render.frame_map_old/frame_map_new` — is a render-time trick, not a data edit. forge does not use it; it retimes by scaling key frames, so the result is inspectable.

### 3.7 Baking

Two paths, both **[verified]** working headless:

**Python (preferred):**

```python
from bpy_extras.anim_utils import bake_action, BakeOptions
opts = BakeOptions(
    only_selected=False, do_pose=True, do_object=False,
    do_visual_keying=True,          # sample the final, constrained transform
    do_constraint_clear=False, do_parents_clear=False, do_clean=False,
    do_location=True, do_rotation=True, do_scale=False,
    do_bbone=False, do_custom_props=False)
act = bake_action(obj, action=None, frames=range(1, 241), bake_options=opts)
```

`BakeOptions` is a 12-field dataclass-like constructor; the exact signature is **[verified]** as `(only_selected, do_pose, do_object, do_visual_keying, do_constraint_clear, do_parents_clear, do_clean, do_location, do_rotation, do_scale, do_bbone, do_custom_props)`. Also available: `bake_action_objects`, `bake_action_iter`, `bake_action_objects_iter` (coroutines, yield per frame — use these to stream progress).

Measured: 240 frames × 40 bones → **0.12 s**, producing 280 F-curves × 240 keys each. **[verified]**

**Operator path:** `bpy.ops.nla.bake(frame_start=1, frame_end=250, step=1, only_selected=True, visual_keying=False, clear_constraints=False, clear_parents=False, use_current_action=False, clean_curves=False, bake_types={'POSE'}, channel_types={'LOCATION','ROTATION','SCALE','BBONE','PROPS'})`. Same job, needs a context override, exposes `clean_curves`.

**`do_visual_keying=True` is non-optional** when the rig has IK, constraints or drivers: without it you bake the pre-constraint local transform, which is wrong by exactly the amount the constraints were doing.

**Then decimate, always.** A 240-frame bake is 240 keys/channel × 280 channels = 67 200 keys, which is unreadable, uneditable and slow. Pipeline:

```
bake(visual) -> quat_sign_continuity -> euler_filter (euler channels)
             -> butterworth_smooth(cutoff 3-6 Hz) if source is sim/mocap
             -> decimate(mode='ERROR', remove_error_margin=tol)
             -> handles_recalc()
```

Error tolerances forge uses, in channel units:

| channel | `remove_error_margin` | rationale |
|---|---|---|
| rotation (quaternion component) | 0.002 | ≈0.23° worst-case joint error |
| rotation (euler, radians) | 0.004 | ≈0.23° |
| location (metres, 1.75 m character) | 0.0015 | 1.5 mm — below the foot-slide threshold |
| scale | 0.003 | 0.3% |
| morph/shape-key weight | 0.01 | 1% |

Expected reduction at those tolerances on hand-animated-style curves: **65–85% of keys removed** (the measured case was 240 → 47, an 80% cut, at a looser 0.05). Report the ratio in the critique so Claude can see whether its source motion was noise.

### 3.8 Drivers and NLA

Drivers (`id.driver_add(path, index)` → `fcurve.driver`) belong to the **rig**, not the animation. forge reads them, never writes them, and bakes them with `do_visual_keying=True` before export because **glTF and FBX do not carry drivers**.

NLA, all **[verified]**:

```python
track = obj.animation_data.nla_tracks.new(); track.name = 'base'
strip = track.strips.new('walk', start_frame, action)
strip.blend_type      # REPLACE | COMBINE | ADD | SUBTRACT | MULTIPLY
strip.extrapolation   # NOTHING | HOLD | HOLD_FORWARD
strip.influence, strip.use_animated_influence, strip.use_auto_blend
strip.repeat, strip.scale, strip.use_reverse, strip.use_sync_length
strip.action_frame_start / action_frame_end     # which slice of the action
track.mute, track.is_solo, track.lock
```

**`COMBINE` is the one that matters.** It is the quaternion-aware additive mode: it combines rotations multiplicatively relative to the rest pose rather than adding components, which is what you need for an additive layer (a lean, a breath, a limp) on top of a base cycle. `ADD` on a quaternion channel is wrong and looks wrong.

Also note `animation_data.action_blend_type / action_extrapolation / action_influence` — the active action participates in the stack with its own blend mode, so forge can layer without pushing down first.

forge's layer model maps straight on: IR `layers[]` → NLA tracks, bottom-up, `blend` → `blend_type`, `influence` → `influence`.

---

## 4. The seam with rigging — contract spec

This is what forge/animation requires from forge/rigging. Short, and non-negotiable, because every generator and every metric reads it.

The rigger emits `rig_manifest.json` next to the .blend:

```json
{
  "forge_rig": "1.0",
  "armature_object": "RIG-char",
  "unit_scale": 1.0,
  "up_axis": "Z",
  "forward_axis": "-Y",
  "character_height_m": 1.75,
  "rotation_modes": {"hips": "QUATERNION", "knee_L": "XYZ", "root": "XYZ"},
  "root_motion_bone": "root",
  "cog_bone": "hips",
  "chains": {
    "spine":  ["hips", "spine_01", "spine_02", "chest", "neck", "head"],
    "arm_L":  ["clavicle_L", "upperarm_L", "lowerarm_L", "hand_L"],
    "leg_L":  ["thigh_L", "shin_L", "foot_L", "toe_L"],
    "tail":   ["tail_01", "tail_02", "tail_03", "tail_04"]
  },
  "ik": {
    "leg_L": {"target": "foot_L_ik", "pole": "knee_L_pole", "switch": "pose.bones[\"leg_L_props\"][\"ik_fk\"]", "chain_count": 2},
    "arm_L": {"target": "hand_L_ik", "pole": "elbow_L_pole", "switch": "pose.bones[\"arm_L_props\"][\"ik_fk\"]", "chain_count": 2}
  },
  "contacts": {
    "foot_L": {"heel": "heel_L", "ball": "ball_L", "toe": "toe_L", "plant_bone": "foot_L_ik"},
    "foot_R": {"heel": "heel_R", "ball": "ball_R", "toe": "toe_R", "plant_bone": "foot_R_ik"}
  },
  "naming_convention": "blender",
  "mirror": {"prefix_pairs": [[".L", ".R"]], "mirror_axis": "X"},
  "keyable": {
    "foot_L_ik": ["location", "rotation_euler"],
    "hips": ["location", "rotation_quaternion"],
    "head": ["rotation_quaternion"]
  },
  "deform_bones": ["DEF-spine_01", "..."],
  "control_bones": ["hips", "foot_L_ik", "..."],
  "rest_matrices": {"hips": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]},
  "bone_lengths_m": {"thigh_L": 0.44, "shin_L": 0.43},
  "limits": {"knee_L": {"x": [-2.44, 0.0]}},
  "shape_keys": {"mesh": "body", "visemes": ["AA","EE","OO"], "blinks": ["blink_L","blink_R"]}
}
```

Hard requirements on the rig:

1. **A root motion bone** named in `root_motion_bone`: no parent, identity rest transform at the world origin, Z-up. All horizontal travel lives here and nowhere else. Without it, root-motion extraction and in-place conversion are guesswork.
2. **A COG bone** (`cog_bone`) whose world head position tracks the pelvis/centre of mass closely enough to use for support-polygon and gravity checks.
3. **Named chains** covering every bone forge is allowed to offset. The offsetting pass (§5.7) is defined over chains; no chains, no overlap.
4. **Contact markers** — heel/ball/toe bones per foot, and the bone that is actually keyed when the foot is planted. Foot-slide measurement is defined on these. A rig without them cannot be checked for sliding, which means it cannot be checked at all for locomotion.
5. **Declared `rotation_modes` per bone**, and the rig must not change them afterwards. A silent switch invalidates every curve forge wrote.
6. **Mirror map** — explicit suffix pairs and mirror axis. Pose mirroring is used constantly (contact_L ↔ contact_R) and must be exact, not inferred from names.
7. **IK/FK switch property path** per limb, and the IK targets must be keyable controls (not helper bones driven by something else).
8. **`keyable` whitelist.** forge refuses to key anything not listed. This is the single cheapest guard against an agent keying deform bones or constraint-driven helpers and producing a double transform.
9. **Rest pose = A-pose or T-pose, declared**, with rest matrices included. Retargeting (§6.3) is defined relative to it.
10. **Bone lengths** for limb-length normalisation in retargeting, and `character_height_m` because every spatial threshold in §9 is normalised by it.

Reciprocal promise from animation: forge only writes to `control_bones ∩ keyable`, only in modes matching `rotation_modes`, and leaves the rig's constraints, drivers and custom properties untouched except where `ik[].switch` is explicitly animated.

**Agreed amendments (see `02-rigging.md` §17.2–17.3).** Rigging has accepted this schema and emits it from a `rig_manifest` tool. Two changes landed, and this document reflects them:

1. **Names are whatever the manifest says.** The bone names in the example above are illustrative, not a promise. Rigging's Blender-native default is `.L`/`.R` suffixes rather than `_L`/`_R`, so `mirror.prefix_pairs` is `[[".L", ".R"]]`. **forge must never pattern-match a bone name** — it reads `chains`, `contacts`, `ik`, `mirror` and `keyable` from the manifest and resolves everything through them. Any glob the agent passes in `targets` is resolved against the manifest's actual names, and a pattern matching nothing is reported, not silently ignored.
2. **`naming_convention`** (`"blender" | "ue" | "mixamo" | "vrm" | "smplx"`) tells forge which export rename table applies (§10.4) without guessing.

And one standing hazard: `switch` values are string data paths. A rig rename invalidates them, and a stale `switch` path fails silently — the IK/FK blend just never changes. forge validates every manifest path resolves at `rig_load` and refuses to start on a stale one.

---

## 5. Craft — the 12 principles as algorithms and numbers

This is the part that decides whether forge produces animation or interpolation. Each principle below is restated as something forge can *do* or *measure*, with numbers where the craft literature gives them.

Sources used for numbers: Richard Williams, *The Animator's Survival Kit* (2001, Faber, ISBN 0571202284) via the Monmouth University animation course's transcription of his timing and head-turn charts; Alejandro Garcia, *Physics of Timing & Spacing* (algarcia.org, 2011, CC BY-NC-SA) and *Principles of Animation Physics* (SIGGRAPH course notes); Bloop Animation and Animation Mentor blink/eye tutorials; UCSD CSE169 (Rotenberg) locomotion lecture for gait phase conventions. Where two sources disagree I say so.

### 5.1 The pro workflow, as forge's state machine

A real animator does not author a finished curve. They pass over the shot four or five times, each pass answering one question. forge makes those passes first-class, because an agent iterating needs a notion of "done for this stage".

| stage | what exists | interpolation | key types | what the critique checks |
|---|---|---|---|---|
| **block** | key poses only, on 2s/3s/4s | `CONSTANT` (stepped) | `EXTREME` | pose readability, silhouette, timing, COG over support |
| **breakdown** | the in-between that defines the *path* between extremes | `CONSTANT` still | `BREAKDOWN` | arcs, overlap intent, weight |
| **spline** | curves turned on | `BEZIER`, handles set per easing preset | unchanged | pops, flips, arc smoothness, loop seam |
| **polish** | settles, overshoots, moving holds, secondary | `BEZIER` + modifiers | `MOVING_HOLD`, `JITTER` | dead holds, volume, interpenetration, key density |
| **offset** | per-chain time offsets applied | unchanged | unchanged | overlap present, no channel desynced past tolerance |

Stage transitions are explicit tools (`stage_advance`). Advancing to `spline` without passing `block`'s critique is allowed but reported. Going *back* is a checkpoint restore, not an undo of splining — forge snapshots at every transition.

**The stepped trick:** forge never throws away the splined curve to show a blocking pass. It adds a `STEPPED` F-modifier with `frame_step` set to the blocking beat and `influence` 1.0, playblasts, then mutes it. Non-destructive A/B.

### 5.2 Timing in frames — the actual numbers

All at 24 fps. **Walk and run cycle lengths**, from the Williams chart (full cycle = two steps):

| cycle frames | frames/step | steps/sec | character |
|---|---|---|---|
| 8 | 4 | 3.0 | very fast run |
| 12 | 6 | 2.0 | run / very fast walk |
| 16 | 8 | 1.5 | slow run, or a "cartoon" walk |
| **24** | **12** | **1.0 cycle/s, 2 steps/s** | **brisk, business-like — the "natural walk"** |
| 32 | 16 | 0.75 | strolling, leisurely |
| 40 | 20 | 0.6 | elderly or tired |
| 48 | 24 | 0.5 | slow, deliberate step |
| 64 | 32 | 0.375 | very slow — a dirge |

*Caveat flagged honestly:* the Monmouth transcription's "steps per second" column is internally inconsistent (it prints 3 steps/sec for a 24-frame cycle, which cannot be right at 12 frames per step). I have recomputed the column from frames/step; the frames/step and cycle-length figures are the ones to trust. Williams' own text also notes most people walk on a 12-frame *step*, but recommends animating on 16s because 12 divides into thirds awkwardly — which is good advice for forge too: **16-frame steps subdivide cleanly to 8 and 4.**

**Walk cycle pose frames** for a 24-frame cycle (contact method, 4 poses per step):

| pose | frames | description |
|---|---|---|
| contact | 1, 13, (25 = 1) | both feet down, legs at maximum spread, body at mid height |
| down | 4, 16 | lowest body point, support knee bent absorbing weight |
| passing | 7, 19 | free leg passes the support leg, body rising, hips highest-but-one |
| up / push-off | 10, 22 | highest body point, support foot pushing off the ball |

Contact at 1/13/25 and down at 4/16, push-off at 10/22 are the Monmouth/Williams figures; the passing frames are the midpoints (7, 19), which the source describes positionally without numbering. For a 16-frame step the same structure scales to contact 1/17/33, down 5/21, passing 9/25, up 13/29.

**Williams' head-turn chart** — the single most useful timing table in the book, because it converts *frame count* directly into *emotional reading*:

| frames | seconds | inbetweens | reads as |
|---|---|---|---|
| 3 | 1/8 | 0 | head nearly knocked off by a strong force |
| 5 | 0.2 | 1 | hit by something substantial (a frying pan) |
| 7 | 0.3 | 2 | a nervous twitch |
| 9 | 0.375 | 3 | dodging a flying object |
| 11 | ~0.5 | 4 | giving a crisp order |
| 15 | 0.625 | 6 | seeing something inviting |
| 21 | ~0.9 | 9 | thinking about something |
| 23 | ~1.0 | 10 | stretching a sore muscle |

(The source prints 0.875 s for 15 frames; at 24 fps 15 frames is 0.625 s. Frame counts are the authority.)

forge exposes this as a named vocabulary: `head_turn("crisp_order")` → an 11-frame move with the matching easing. An agent asking for "a quick head turn" gets 5–7 frames; asking for "he considers it" gets 21.

**Blinks.** Sources disagree, so forge carries three presets and says which is which:

| preset | close | hold | open | total | source |
|---|---|---|---|---|---|
| `fast` | 1 | 1 | 3 | 5 | Bloop ("fast blink") |
| `normal` | 2 | 1 | 3 | **6** | Bloop ("regular blink") |
| `deliberate` | 2 | 2 | 5 | 9 | Bloop ("long blink") |
| `am_style` | 3–5 | 1 | 2–4 | 6–10 | Animation Mentor |

Consistent across all of them, and the part forge enforces: **closing is faster than opening**, and the upper lid does most of the travel with the lower lid following. A third source puts a standard blink at 3–5 frames at 24 fps and a deliberate one at 6–8; the disagreement is about where "standard" sits, not about the shape. Default `normal` (6).

Eye darts: **3–4 frames** between points of interest, no inbetweens worth speaking of, and the lids should move with the eyes on a large dart.

**Holds.** A hold shorter than **4 frames** does not read as a hold, it reads as a slow key. A dead hold longer than **8 frames** reads as a freeze (broken rig, dropped frame). Therefore: forge's `moving_hold` pass applies to any declared hold ≥ 6 frames, drifting the pose by **0.5–2.0° per major joint** across the hold, with the drift continuing the incoming direction of travel (so it reads as settling, not as noise). `MOVING_HOLD` key type stamped on both ends.

**Settle and overshoot.** A limb arriving at a pose overshoots by **8–15%** of the move's amplitude and comes back over **3–6 frames**, one soft bounce. Heavier objects overshoot less and settle longer (5–8 frames); light/whippy things overshoot more (15–25%) and settle in 2–4. Two bounces only for cartoon register; three is never.

**Anticipation.** Duration scales with the action: a snap action (≤6 frames) gets **2–4 frames** of anticipation; a medium action (10–16 frames) gets **5–8**; a big effort (jump, throw) gets **8–16**, and may hold at the anticipation extreme for 2–4 frames before releasing. Amplitude: **15–30% of the main action's amplitude, in the opposite direction.**

### 5.3 Physics numbers forge can actually check

From Garcia. These make "does this feel like it has weight" a computation rather than an opinion.

**The Odd Rule.** For anything in free fall, measured from the apex, the *spacing between consecutive frames* grows as **1 : 3 : 5 : 7 : 9 …**, and total distance from the apex as **1 : 4 : 9 : 16 : 25 …**. Equivalently, the per-frame spacing increases by a constant increment every frame.

The constant, at 24 fps under Earth gravity:

| shooting on | first spacing below apex |
|---|---|
| ones | **1/3 inch** (8.5 mm) |
| twos | **1 1/3 inch** (33.9 mm) |
| threes | **3 inches** (76 mm) |

Garcia's figures, and they check out exactly: g/(2·fps²) = 9.81/(2·576) = 8.52 mm = 0.335 in. Which gives forge a clean metric: **for a ballistic arc, the second difference of COG height must be constant and equal to g/fps² = 17.0 mm/frame² at 24 fps** (0.67 in/frame²). Scale by (24/fps)² for other rates. Deviation from that is "wrong gravity" in a measurable percentage. Note Garcia's own caution: the Odd Rule is for judging spacing by feel, and the first spacing is the same **regardless of the object's mass** — a bowling ball and a ping-pong ball have identical spacing in a vacuum-ish shot, which is exactly the mistake animators make when they "weight" a fall by spacing it differently.

**Speed ↔ spacing table** at 24 fps (Garcia), useful for validating stride length against declared speed:

| mph | inches/frame | distance per second |
|---|---|---|
| 2 | 1.5 | 35 in |
| 4 | 3 | 70 in |
| 10 | 7 | 14 ft 8 in |
| 30 | 22 | 44 ft |
| 60 | 44 | 88 ft |
| 90 | 66 | 44 yd |

Normal walking ≈ **3 mph**; running ≈ **6–10 mph**. Garcia's worked example: a character power-walking at 4 mph, blocked with a key every 6 frames, moves ~18 inches between keys (6 × 3 in).

Which closes the loop on locomotion: **stride length × cadence must equal root travel.** For a 24-frame cycle (2 steps/s) at 3 mph = 52.8 in/s, each step covers ~26 in (0.67 m) for a 1.75 m character — about 0.38 × height. forge checks this as `stride_consistency`, and it is the metric that catches the single most common procedural-walk bug: a cycle that looks fine in place and skates when the root moves.

**Jump magnification** (Garcia, *Principles of Animation Physics*): jump height divided by the height the COG rises from take-off to apex sets how fast the push must be. The paper's worked example gives a four-foot jump roughly **12 frames** take-off to apex, implying ~3 frames of push for a one-foot crouch, and scales so that a four-foot jump takes twice as long to apex as a two-foot one (time ∝ √height). *The source text extracted raggedly from the PDF; the √height scaling is sound physics and the 12-frame figure is consistent with it (√(4 ft) → 0.5 s ≈ 12 frames), but treat the exact frame count as approximate.*

**Squash and stretch, as volume preservation.** Not a free scale. If the squash axis scales by `s`, the two perpendicular axes each scale by `1/√s`:

```python
def squash(s):                 # s < 1 squashes, s > 1 stretches
    k = 1.0 / math.sqrt(s)
    return (k, s, k)           # assuming local Y is the squash axis
```

forge's `volume_error` metric = `|sx·sy·sz − 1|`. Threshold: **≤ 0.05** for anything claiming realism, ≤ 0.25 for cartoon register. Garcia's honest caveat, which forge should repeat to Claude: most objects do not physically squash; you squash them to remove the visual jar at a sudden direction change. So forge applies squash **at impact frames and the 1–2 frames either side**, not as a continuous effect.

### 5.4 Pose mechanics

What forge checks and, where it can, fixes.

**Line of action.** Fit a single quadratic Bézier through the world-space head positions of the spine chain (hips → head). Report (a) the chord-to-arc ratio, (b) the number of curvature sign changes along the chain. A strong pose has **exactly one** curvature sign change at most — a C or a reversed C. Two or more means the spine is a noodle with no line. Report also the angle of the line's chord against vertical, because that number *is* the pose's attitude: 0–5° reads neutral/stiff, 8–20° reads engaged, >25° reads extreme or falling.

**Hip/shoulder counter-rotation.** On a walk, the pelvis and chest rotate in opposite directions about the vertical axis. Typical magnitudes for a natural walk: **pelvis ±4–8°**, **chest/shoulders ±6–12°**, in antiphase, both peaking at the contact poses. Metric: `counter_rotation_phase` = the phase difference between the pelvis-Z and chest-Z curves, which should be **180° ± 25°**. Zero phase difference (both rotating together) is the dead giveaway of a procedurally generated walk, and it is a one-line fix.

**Weight shift and the support polygon.** The ground projection of the COG must lie inside the convex hull of the planted contact points, whenever the character is not airborne. forge builds the polygon per frame from the `contacts` bones that are planted that frame, expands it by a **30 mm** tolerance (feet are not points), and counts violating frames. A single frame of violation during a fast weight transfer is normal; three or more consecutive is falling over. During single support the COG should sit over the **ball of the planted foot**, not its centre.

**Contact / down / passing / up.** Already numbered in §5.2. forge's `walk_cycle` generator emits exactly these four per step and nothing else at the blocking stage — because that is what a human blocks, and because four poses is what an agent can reason about in one tool call.

**Moving holds.** Covered in §5.2. The implementation detail that matters: the hold's second key must not be a copy of the first. forge offsets it by the drift amount *and* leaves the handles un-flattened on the incoming side so the deceleration into the hold continues through it.

### 5.5 Easing math — handle positions, verified

Blender easing is handle geometry. The two numbers that define a segment's feel:

- `out_frac` — how far the **left** key's right handle extends, as a fraction of the segment span
- `in_frac` — how far the **right** key's left handle extends, as a fraction of the span

Both handles flat (y = the key's own value) means zero slope at the key. **Long flat handle = slow; short flat handle = fast.** Default Blender is 1/3 and 1/3. **[verified]**

Measured sample values on a 13-frame segment going 0 → 1, `fcurve.evaluate()` at each frame. **All rows [verified] in this container:**

| preset | out_frac | in_frac | sampled values, frames 1→13 | feel |
|---|---|---|---|---|
| `smooth` (default) | 0.333 | 0.333 | 0, .02, .074, .156, .259, .376, **.5**, .624, .741, .844, .926, .98, 1 | symmetric S; 50% at the midframe |
| `ease_out` | 0.00 | 0.667 | 0, **.202**, .364, .5, .615, .712, .793, .859, .911, .951, .979, .995, 1 | leaves fast, arrives slow; 50% by frame 4 |
| `ease_in` | 0.667 | 0.00 | 0, .005, .021, .049, .089, .141, .207, .288, .385, **.5**, .636, .798, 1 | creeps out, arrives fast; 50% at frame 10 |
| `snap` | 0.10 | 0.70 | 0, **.121**, .298, .457, .591, .701, .790, .860, .914, .953, .98, .995, 1 | **snappy with a settle** — 30% by frame 3, 90% by frame 9 |
| `float` | 0.70 | 0.70 | 0, .005, .024, .062, .132, .263, **.5**, .737, .868, .938, .976, .995, 1 | hangs at both ends, races through the middle |
| `overshoot_ready` | 0.333 | 0.10 | 0, .018, .064, .128, .207, .296, .393, .496, .604, .715, .825, .929, 1 | nearly linear arrival — so the *next* segment can overshoot |

So "snappy with a settle" is literally `out_frac=0.10, in_frac=0.70`, and forge can say that to the agent as a named preset while writing exact handle coordinates:

```python
def ease(fc, i, out_frac, in_frac, flat_out=True, flat_in=True):
    """Shape the segment between keyframe_points[i] and [i+1]."""
    k0, k1 = fc.keyframe_points[i], fc.keyframe_points[i+1]
    span = k1.co[0] - k0.co[0]
    k0.handle_right_type = k1.handle_left_type = 'FREE'
    y0 = k0.co[1] if flat_out else k0.handle_right[1]
    y1 = k1.co[1] if flat_in else k1.handle_left[1]
    k0.handle_right = (k0.co[0] + span * max(out_frac, 1e-4), y0)
    k1.handle_left  = (k1.co[0] - span * max(in_frac,  1e-4), y1)
    fc.update()
```

Non-flat handles are how you get a pass-through (a key the curve does not decelerate into — an in-between on an arc). Set `flat_in=False` and leave the handle's y on the chord slope; `graph.match_slope` does the same thing from a selection.

The named vocabulary forge exposes, which is what the IR's `ease` field accepts: `stepped`, `linear`, `smooth`, `ease_in`, `ease_out`, `snap`, `float`, `overshoot_ready`, `hold`, plus `[out_frac, in_frac]` as a raw escape hatch, plus `{"out": "...", "in": "..."}` to mix halves.

### 5.6 Arcs — detecting a bad one numerically

End effectors travel on arcs. Procedural and lerped animation puts them on straight lines or, worse, on lines with a kink at every key. This is the most reliably detectable craft failure, so it is worth doing properly.

Sample the world-space position **p(f)** of an end-effector bone over the move's frame range (0.2 ms/frame for a whole rig — free). Then:

```
v(f) = p(f+1) − p(f)                     velocity
a(f) = v(f+1) − v(f)                     acceleration
j(f) = a(f+1) − a(f)                     jerk
κ(f) = |v × a| / |v|³                    discrete curvature
```

Four metrics, each with a distinct failure mode behind it:

| metric | computed as | what a bad value means |
|---|---|---|
| `arc_straightness` | max perpendicular deviation of p(f) from the chord between the move's endpoints, ÷ chord length | < 0.02 = a straight line, i.e. no arc at all. Pro arcs sit at **0.06–0.25** |
| `arc_curvature_flips` | count of sign changes of the signed curvature in the plane of best fit | ≤ 1 for a single gesture. ≥ 2 means a wobble, usually a mis-set handle |
| `arc_jerk_peak` | max ‖j(f)‖ ÷ (bone chain length), per frame³ | spikes at keys = handles not matched; a spike > 5× the median is a visible snag |
| `arc_kink` | max angle between v(f−1) and v(f) outside of intended direction changes | > 25° at a non-extreme frame is a kink |

**The fix, in order of how much it respects the animator's intent:**

1. `graph.match_slope(factor=1.0)` on the offending interior keys — takes the slope from neighbours, which removes the kink without moving the key.
2. `graph.equalize_handles(side='BOTH', handle_length=L)` — makes the handles symmetric so the curve passes through cleanly.
3. If the arc is straight rather than kinked, the problem is missing breakdowns, not easing: insert a breakdown at the midpoint and displace it perpendicular to the chord by `0.08 × chord` in the direction the body mechanics imply (up for a reaching arm, down for a dropping hand). `graph.breakdown(factor=...)` then positions it in time.
4. `graph.gaussian_smooth(factor=1.0, sigma=0.33, filter_width=6)` only on baked/mocap data. Never on hand-authored keys — it flattens extremes, which is the opposite of polish.

forge also renders the arc, because a picture of a motion trail is worth a page of numbers: sample p(f), build a poly-line mesh, assign an emissive-ish Workbench single colour, render over the frame. §7.3.

### 5.7 Overlap, follow-through, offsetting — the pass that makes it look animated

Nothing in a body starts at the same time. Hips lead, the spine follows, the head arrives last, and anything dangling arrives later still. This is one pass over existing curves and it is the highest ratio of visual improvement to code in the whole system.

**Per-chain offsets, in frames, for a humanoid at 24 fps** — these are forge's defaults, derived from standard overlap practice:

| chain / part | offset from the leading part | notes |
|---|---|---|
| hips / COG | **0** (the leader) | everything is relative to this |
| spine_01 | +1 | |
| spine_02 / chest | +2 | |
| clavicles | +2 | |
| upper arm | +3 | |
| lower arm | +4 | |
| hand | +5 | |
| neck | +3 | |
| head | **+4** | the classic "head last" |
| hair / cloth / tail, per segment | +1.5 each, cumulative | a 4-segment tail trails 6 frames at the tip |
| props held in hand | +1 past the hand | |

For a fast action (≤8 frames) halve these and round; for a slow one (≥30 frames) multiply by 1.5. The leader is not always the hips — on a throw it is the hips, on a reaction it is the point of impact, on a head turn it is the eyes (which lead the head by 1–2 frames, the one case where something arrives *before* the hips).

**Implementation**, two modes:

- `shift` — move every key on the chain's channels later by `n` frames. Exact, cheap, and correct when the chain's motion is a copy of the leader's. Leaves a hole at the start of the range, which the cycle/extrapolation handles for loops and a held first key handles for one-shots.
- `propagate` — cumulative per-bone delay down the chain (`per_bone` frames each), which is what you want for a tail, a whip, a rope, a ponytail. This is a travelling wave: bone *i* gets `i × per_bone` frames of delay.

```python
def offset_chain(obj, bones, frames, per_bone=0.0, channels=None):
    cb = channelbag(obj)
    for i, bone in enumerate(bones):
        shift = frames + i * per_bone
        if abs(shift) < 1e-6:
            continue
        for fc in cb.fcurves:
            if f'bones["{bone}"]' not in fc.data_path:
                continue
            if channels and fc.data_path.rsplit('.', 1)[-1] not in channels:
                continue
            for k in fc.keyframe_points:
                k.co[0] += shift
                k.handle_left[0] += shift
                k.handle_right[0] += shift
            fc.keyframe_points.sort()
            fc.update()
```

Note the handles must shift with the control point — `co_ui` would drag handles but recomputes them, which destroys hand-set easing. Shift all three explicitly.

**Metric:** `overlap_present` = for each declared chain, the lag (in frames) of its peak angular velocity behind the leader's, measured by cross-correlation of the velocity magnitude curves. If every chain's lag is 0, the pass never ran and the animation will read as mechanical. If a chain's lag exceeds **8 frames** on a 24-frame cycle it has desynced far enough to look broken rather than loose.

### 5.8 The remaining principles, operationalised

- **Secondary action** — a separate IR layer with `blend: "combine"` and its own, coarser keys. forge's rule: a secondary action must not peak on the same frame as the primary, or it reads as one action. Offset its extremes by ≥2 frames.
- **Timing vs spacing** — timing is *which frames have keys*, spacing is *the values between them*. forge keeps them separately editable: `retime` moves keys without touching values or handle shapes (scaling handle x-extents proportionally); `reease` changes handles without moving keys. Most agent requests ("make it snappier") are spacing requests and should never move a key.
- **Exaggeration** — `graph.push_pull(factor)`. `factor > 1` pushes keys away from the curve's average, `< 1` pulls them in. forge's `exaggerate(targets, amount)` wraps it with amount 1.15–1.4 for "punch it up". Measure before/after amplitude so the agent sees what it did.
- **Solid drawing** — the silhouette metric (§9) plus the line-of-action metric (§5.4). A pose whose silhouette area is within 3% of the previous extreme's has not changed shape, only position, and will read as a cheat.
- **Appeal** — not measurable. forge does not pretend. What it *can* do is surface the contact sheet and the three strongest poses (by silhouette area change) so Claude judges them against each other rather than against nothing.
- **Straight-ahead vs pose-to-pose** — forge is pose-to-pose by construction (the IR is a list of keys). Straight-ahead exists as the procedural generators and the spring pass, which produce frame-by-frame results.

---

## 6. Procedural generation

Deterministic, licence-clean, tunable, no model weights. This is where forge should spend its effort, because for locomotion and secondary motion procedural output beats both ML and naive keyframing.

### 6.1 Biped locomotion — footstep-driven

Generate from a **footstep plan**, not from a pose cycle. The plan is the ground truth, which makes foot-locking exact rather than corrective.

Parameters and their sane ranges for a 1.75 m character:

| parameter | symbol | range | default (natural walk) |
|---|---|---|---|
| cycle frames | T | 8–64 | 24 |
| stride length (per step) | L | 0.25–0.95 × height | 0.38 × h = 0.67 m |
| double-support fraction | d | 0.0 (run) – 0.25 (walk) | 0.12 |
| duty factor (stance ÷ cycle, per leg) | D | 0.35 (run) – 0.65 (walk) | 0.62 |
| hip (pelvis) yaw | — | ±3–8° | ±5° |
| pelvis roll (list, drop to swing side) | — | 2–6° | 4° |
| pelvis vertical bob | — | 0.02–0.05 × h, 2 cycles per stride | 0.035 m |
| shoulder counter-yaw | — | ±6–12° | ±8° |
| arm swing (shoulder pitch) | — | 10–35° | 22° |
| foot lift (max toe clearance) | — | 0.03–0.12 m | 0.06 m |
| knee bend at `down` | — | 12–25° | 18° |

Algorithm:

1. **Plan footfalls.** For each step *k*, a contact position `P_k` on the ground and a contact window `[f_on, f_off]` in frames. `f_on_k = k·T/2`, `f_off_k = f_on_k + D·T`. Windows for the two legs overlap by `d·T` frames (double support) or are separated by a flight phase (run, when `D < 0.5`).
2. **Plant the feet.** During `[f_on, f_off]` the IK foot target is keyed *identically* at the window's ends and nowhere between — so it is exactly static, and there is no sliding to fix. Toe roll at `f_off − 2` for the push-off.
3. **Swing the feet.** Between `f_off_k` and `f_on_{k+1}`, a parabolic-ish arc from `P_k` to `P_{k+1}` peaking at `foot_lift` at 55% of the swing (slightly late, because the foot rises fast and lowers slow). Keys at swing start, peak, and 80% (the reach), with `ease_out` leaving and `ease_in` arriving.
4. **Drive the pelvis.** Vertical bob at 2× cycle frequency, lowest at the `down` frames (4, 16 in a 24-frame cycle) and highest at `up` (10, 22). Yaw and roll at 1× cycle frequency, phase-locked to contacts. Horizontal travel on the root motion bone, linear, `L` per half-cycle.
5. **Solve the legs** with the rig's IK (`chain_count` from the manifest). `view_layer.update()` then read the result — **[verified]** Blender's IK solves headless and hits the target exactly (`solved tail == target` to 3 dp).
6. **Upper body.** Counter-rotate the chest against the pelvis in antiphase. Arms swing in antiphase to the same-side leg. Then run the §5.7 offsetting pass so the spine and head trail.
7. **Loop.** Cycles modifier `REPEAT` on everything except the root, which gets `REPEAT_OFFSET`.
8. **Validate.** Run `foot_slide`, `loop_seam`, `stride_consistency`, `counter_rotation_phase`. The generator is only trustworthy if it reports its own metrics.

**Run differences, not just faster:** duty factor drops below 0.5 (flight phase appears), the torso pitches forward 8–15°, arm swing roughly doubles and the elbows bend to 70–100°, foot lift rises to 0.15–0.25 m, and the vertical bob inverts its emphasis — lowest at mid-stance, highest at mid-flight.

**Sneak:** T = 32–48, duty factor 0.70+, foot lift low (0.02 m) but knee lift high, COG held low and constant (bob < 0.01 m), weight transfer deliberately slow with a 3–5 frame hold at each contact.

**Terrain adaptation:** raycast down from each planned `P_k`, set the contact height and the foot's pitch/roll from the surface normal (clamped to ±25°), and re-solve. Pelvis height becomes `min(leg_reach)` over the planted feet minus a small crouch allowance. This is cheap and it is what makes a generated walk usable on anything but a plane.

### 6.2 Quadruped gaits

Phase convention, from the biomechanics literature: **phase = the fraction of the stride between a hind footfall and the ipsilateral (same-side) fore footfall.** 50% is a trot; 25% is an evenly-spaced four-beat walk; 0%/100% is a pace.

Per-leg phase offsets as fractions of the stride, with legs ordered **LH, LF, RH, RF** (left hind, left fore, right hind, right fore):

| gait | LH | LF | RH | RF | duty factor | notes |
|---|---|---|---|---|---|---|
| walk (lateral sequence, 4-beat) | 0.00 | 0.25 | 0.50 | 0.75 | 0.60–0.80 | the standard quadruped walk; always 2–3 feet down |
| amble / running walk | 0.00 | 0.25 | 0.50 | 0.75 | 0.45–0.55 | same order, shorter stance |
| pace | 0.00 | 0.00 | 0.50 | 0.50 | 0.45–0.55 | ipsilateral pairs together (camels, some horses) |
| trot | 0.00 | 0.50 | 0.50 | 0.00 | 0.35–0.50 | diagonal pairs together; 2-beat |
| canter (right lead, 3-beat) | 0.00 | 0.60 | 0.10 | 0.70 | 0.30–0.40 | one hind, then diagonal pair, then one fore |
| transverse gallop | 0.00 | 0.40 | 0.10 | 0.50 | 0.25–0.35 | hind pair then fore pair, same lateral order |
| rotary gallop | 0.00 | 0.50 | 0.10 | 0.60 | 0.25–0.35 | fore pair order reversed — cheetahs, dogs at speed |
| bound | 0.00 | 0.50 | 0.00 | 0.50 | 0.25–0.35 | hinds together, fores together |

**Honest sourcing note.** The 25%/50%/0% phase definitions for walk/trot/pace come straight from the gait-analysis literature and are solid. The canter and gallop numbers are the UCSD CSE169 locomotion lecture's figures (`.0 .5` for walk/trot patterns, `.0 .1 / .4 .5` transverse, `.0 .1 / .6 .7` equestrian/feline, and a canter nearer `.6, .0, .0, .1`) translated into the LH/LF/RH/RF ordering above, and **the lecture PDF itself would not fetch from this container** (TLS chain failure on `cseweb.ucsd.edu`, then 503) so I am working from the search engine's extract of it, cross-checked against the duty-factor constraints. The walk/trot/pace rows I would ship. The canter and gallop rows I would ship as **starting points flagged in the tool's docstring**, to be tuned against reference footage, because the lead-leg convention flips between sources and a canter with the wrong lead reads as a limp.

Hard constraint forge enforces regardless of the numbers: **a walking gait requires duty factor > 0.5** so that at least one foot of each pair is always down; below that it is a running gait by definition and needs a flight phase. Violating it produces the floating-dog look.

Beyond legs: spine flexion ±5–12° at 1× stride for a trot and 2× for a gallop (the back is a spring at speed), head bob 1× stride phase-locked to the fore contacts, tail as a propagated chain (§6.3).

### 6.3 Flight, swim, tails, tentacles — travelling waves

One primitive covers all of them: a sinusoidal wave propagated down a chain with a per-bone phase delay and an amplitude envelope.

```
θ_i(f) = A · env(i/N) · sin(2π·(f/T) − i·φ) + bias_i
```

| use | T (frames) | A | φ per bone | env |
|---|---|---|---|---|
| tail idle sway | 60–96 | 2–5° | 0.25–0.45 rad | rising to the tip (0 → 1) |
| tail, running | 16–24 | 8–15° | 0.4 rad | rising |
| tentacle/whip | 12–30 | 15–35° | 0.5–0.9 rad | rising steeply, tip dominant |
| fish body (carangiform swim) | 8–16 | 5–20° | 0.6 rad | low at head, max at tail |
| eel (anguilliform) | 12–20 | 20–30° | 1.0 rad | roughly flat |
| bird wing flap | 8–16 | 35–60° (shoulder) | 0.3 rad along wing | rising to the primaries |
| cloth hem | 30–50 | 3–8° | 0.5 rad | rising |

Forward flight = flap cycle on the wing chain + a 1× body bob 90° out of phase with the downstroke + a travelling wave on the feather/primary chain. Gliding = hold the wing pose and run only a low-amplitude noise layer. Swim = the body wave plus a yaw oscillation at the head of ±3–6° in antiphase, because the head counter-rotates against the body wave.

Implementation choice: **write these as keys, not as F-modifiers**, at 1/4 of the period (so 4 keys per cycle) with `smooth` easing, then let Cycles repeat them. Keys are inspectable and editable by the agent; a `FNGENERATOR` modifier is neither.

### 6.4 Spring / dynamics post-pass — jiggle, inertia, lag

This is the "Wiggle" class of effect, and it is worth implementing in code rather than depending on an addon (the Wiggle 2 addon is GPL and Blender-version-coupled; forge needs something stable and headless).

Per-bone, per-channel damped spring driven by the *existing* animation:

```python
def spring_pass(obj, bones, stiffness=18.0, damping=0.35, mass=1.0,
                frames=None, channel='rotation_quaternion', amount=1.0):
    """Second-order follow on top of baked motion. One pass, no iteration."""
    dt = 1.0 / 24.0
    for bone in bones:
        target = sample_channel(obj, bone, channel, frames)   # the driving signal
        x = target[0]; v = 0.0 * x
        out = []
        for t in target:
            a = (stiffness * (t - x) - damping * stiffness ** 0.5 * 2.0 * v) / mass
            v = v + a * dt
            x = x + v * dt
            out.append(x + (t - x) * (1.0 - amount))
        write_channel(obj, bone, channel, frames, out)
```

Parameters and what they feel like, at 24 fps:

| feel | stiffness k | damping ratio ζ | overshoot | settle frames |
|---|---|---|---|---|
| tight (armour, a backpack) | 60–120 | 0.7–0.9 | 2–6% | 2–4 |
| normal (flesh, a loose belt) | 20–50 | 0.4–0.6 | 8–15% | 4–8 |
| loose (long hair, a cloak) | 8–20 | 0.2–0.35 | 20–35% | 8–18 |
| floppy (jelly, a comedy prop) | 3–8 | 0.1–0.2 | 40–60% | 18–40 |

Natural frequency `ω = √(k/m)` in rad/s; the overshoot period in frames is `2π/ω × 24`. Stability: explicit Euler needs `k·dt²/m < 2`, i.e. **k < 1150 at 24 fps** — well above any useful value, so explicit integration is fine, but clamp it anyway because an agent will pass 10000.

Chain mode: run the spring down a chain, each bone driven by its parent's *solved* output, with stiffness decreasing to the tip (×0.8 per segment). That gives whip propagation for free and is the same primitive as §6.3 with a different driver.

Run this **after** splining and **after** offsetting, on a duplicate action, so it is revertible.

### 6.5 Noise layers for life

The difference between a held pose and a living one. Three stacked F-modifier noise layers, all `blend_type='ADD'`, each with a different `phase` per channel:

| layer | targets | `strength` | `scale` (frames) | note |
|---|---|---|---|---|
| breath | chest Y-scale or chest pitch | 0.004–0.010 rad (0.25–0.6°), or 0.5–1.5% scale | 72–108 (= 13–20 breaths/min) | use a keyed sine, not noise, if the breath should read |
| weight shift | hips location X/Y, hips yaw | 0.004–0.012 m / 0.3–1.0° | 120–240 | the slow one; this is what sells an idle |
| micro-sway | head, neck, hands, fingers | 0.003–0.008 rad (0.17–0.45°) | 8–20 | the fast one; keep it tiny or it reads as a tremor |

Amplitudes above those ranges stop reading as life and start reading as a shiver — and that is the single most common mistake with noise modifiers. forge's `idle` generator clamps to them and says so in its return value.

`phase = (hash(bone_name + channel) % 997)` so nothing is in lockstep. `depth=0` (pure 1-octave) for micro-sway, `depth=1` for weight shift.

### 6.6 Physics sim → keys

Available in-container: **Bullet rigid body** (`build_options.bullet == True` **[verified]**), Blender cloth, soft body, and hair/particle dynamics. All CPU, all headless.

| sim | viability on 4 CPU cores | notes |
|---|---|---|
| rigid body ragdoll (15–25 bodies, 240 frames) | **good** — seconds | the practical one. Set up capsules from bones, constrain, bake |
| cloth (cape, 2–5 k verts, 240 frames) | **acceptable** — tens of seconds to a few minutes | drop quality steps to 3–5, collision quality 2 |
| cloth, garment-grade (20 k+ verts, self-collision) | **poor** — tens of minutes | do not offer it in a tool that an agent calls in a loop |
| hair dynamics | **marginal** | prefer the spring pass (§6.4) on a hair *chain*; it is 100× cheaper and more controllable |
| soft body | **avoid** | slow and unstable; the spring pass covers the use cases |

Workflow: sim → `bake_action(do_visual_keying=True)` → `butterworth_smooth(cutoff_frequency=4–6)` to kill solver chatter → `decimate(ERROR)`. The hybrid pattern that is actually useful: **key the primary, sim the secondary, blend the sim in with a `COMBINE` NLA strip at 0.6–0.9 influence** so the animator's intent survives the solver.

For ragdoll specifically: the useful trick is a blended takeover — keyed up to the impact frame, then rigid-body from there, with a 3–5 frame cross-fade on strip influence. Pure ragdoll from frame 1 looks like what it is.

### 6.7 IK, headless

**[verified]** in this container: `pose.ik_solver ∈ {LEGACY, ITASC}`; the `IK` and `SPLINE_IK` pose constraints both construct; an `IK` constraint with `chain_count=3` solved to its target exactly (`[1.2, -0.0, 1.0]` against a target at `(1.2, 0, 1.0)`) after `view_layer.update()`.

When to use what:

| solver | use | why |
|---|---|---|
| Blender `IK` (LEGACY) | limbs, 2–3 bone chains, everything normal | fast, stable, respects bone limits and pole targets |
| Blender `IK` (ITASC) | chains needing stiffness/weight control, soft limits, or multiple competing targets | more controllable, slower, more tuning surface |
| `SPLINE_IK` | tails, spines, tentacles following a curve | the right tool for a chain whose *shape* is the animation |
| Python FABRIK | chains where forge needs the solve *without* touching the scene (planning, previewing, retarget fitting) | position-only, 10–20 iterations, converges fast, trivial to implement, no depsgraph round-trip |
| Python CCD | same, when joint limits must be enforced per-joint during the solve | easier to clamp than FABRIK |
| analytic 2-bone | leg/arm IK when forge wants exact, deterministic, differentiable results | closed form; use for the locomotion generator's inner loop |

Rule: **solve in the scene when the result is going to be keyed; solve in Python when the result is going to be measured.** A `frame_set` + `view_layer.update()` round-trip is ~0.2 ms, which is cheap per frame but not cheap inside a 20-iteration solver over 240 frames.

---

## 7. Motion capture and data-driven motion

### 7.1 Formats

| format | carries | import | verdict for forge |
|---|---|---|---|
| **BVH** | joint hierarchy + Euler rotations + root translation, one skeleton | `bpy.ops.import_anim.bvh` | **the workhorse.** Plain text, universally available, no licence weirdness in the format itself |
| **FBX anim** | full rig, skinning, multiple takes, Euler or quat | `bpy.ops.import_scene.fbx` | fine, heavier, version-fragile |
| **glTF animation** | sampled TRS per node + morph weights | `bpy.ops.import_scene.gltf` | good round-trip with engines, poor as a mocap interchange (no skeleton semantics) |
| **Alembic** | baked transforms or point caches | `bpy.ops.wm.alembic_import` (`build_options.alembic == True` **[verified]**) | for deformation caches, not rigs |
| **SMPL / SMPL-X params** | 3 global trans + 22/55 joint axis-angles + 10–16 shape betas, usually 20 or 30 fps | needs a converter | **this is what every ML model outputs.** §8.5 |
| **AMASS .npz** | SMPL-H params, `poses` (N×156), `trans`, `betas`, `mocap_framerate` | numpy | the big aggregated dataset, non-commercial |

forge's internal canonical form for imported motion is **a Blender action on a declared source skeleton**, plus a sidecar JSON with fps, frame range, and the source's joint naming. Not BVH, not SMPL — those are entry points.

### 7.2 What is actually free, and what "free" means

This matters more than any technical detail in this section, so it goes in a table with the licence text.

| source | size | licence | usable in a sold asset? |
|---|---|---|---|
| **CMU Graphics Lab Mocap** (~2600 clips, 144 subjects) | ~1.5 GB as BVH | site states: *"free for all uses"* … *"This data is free for use in research projects. You may include this data in commercially-sold products, but you may not resell this data directly, even in converted form."* **[verified: fetched mocap.cs.cmu.edu]** | **YES** — the one unambiguous yes. Bake it into a character and sell the character; do not sell a repack of the BVHs |
| **AMASS** (~45 h, 11k+ motions, unified SMPL-H) | ~20 GB | *"non-commercial scientific research, education, or artistic projects. Any other use, in particular any use for commercial purposes, is prohibited"* — and explicitly bars training models for commercial use. Commercial licensing via ps-license@tue.mpg.de | **NO** |
| **SMPL / SMPL-X body models** | small | academic research only; commercial licensing via Meshcapade | **NO** (and this taints anything that needs the body model at runtime) |
| **LAFAN1** (Ubisoft La Forge) | 137 MB | **CC BY-NC-ND 4.0** **[verified: fetched repo README]** | **NO — and worse than NC.** *NoDerivatives* means retargeting it is a prohibited derivative. Benchmarks only |
| **Mixamo** (Adobe) | library | royalty-free for personal/commercial/non-profit use per Adobe's FAQ; needs an Adobe ID (not Enterprise/Federated); bundling the raw animation files into a resold pack is outside the licence; ML-dataset use unanswered by Adobe | **mostly yes, with care** — but secondhand sourcing (Adobe's live help page was erroring; guides cite an archived copy), and multiple vendors note Adobe has repeatedly floated sunsetting it. **Do not architect a dependency on it** |
| **100STYLE** | ~4 M frames, 100 locomotion styles | academic release, non-commercial terms | research only |
| **Human3.6M** | large | non-commercial academic | research only |
| **Motion-X / Motion-X++** | 15 M+ SMPL-X frames | own licence that explicitly requires honouring the licence of **each constituent source dataset**, several of which are NC | treat as NC |
| **Truebones** | ~1500 creature/biped BVH | varies by pack; some free sets | check per pack; creature mocap is otherwise near-impossible to find free |
| **Quaternius** | small animated sets | CC0 | **YES** — low volume but genuinely free |

**The structural consequence**, and it is the project's biggest non-technical risk: nearly every published text-to-motion and in-betweening checkpoint is trained on HumanML3D, which is built on AMASS. The *code* is MIT (verified: MoMask, MDM, CondMDI, HumanML3D all carry MIT **[verified: fetched LICENSE files]**), but the **weights are AMASS derivatives**, so a model that is "open source" is still non-commercial in practice. forge must therefore:

1. Track provenance per clip in a sidecar (`source`, `licence`, `commercial_ok`).
2. Mark any action that touched a non-commercial source, and **propagate the mark through retargeting, blending and export**.
3. Refuse `export(commercial=True)` on a tainted action, and say which clip caused it.

That provenance plumbing is three days of work and it is the difference between a usable tool and a liability.

### 7.3 Retargeting — the math, from first principles

The problem: source skeleton S with its own rest pose, bone names, lengths and roll angles; target skeleton T likewise. Copy the *motion*, not the numbers.

**Step 0 — correspondence.** A bone map `S→T`, from the rig manifest plus a name-matching heuristic for common conventions (Mixamo `mixamorig:`, CMU/BVH `Hips/LeftUpLeg/…`, Rigify `DEF-`/`ORG-`, UE4 `pelvis/thigh_l/…`). Unmapped target bones keep their rest pose; unmapped source bones are dropped. Report both lists — an agent needs to know what was ignored.

**Step 1 — rest-pose delta.** This is the whole trick. For each mapped pair, in world space at rest:

```
R_S_rest = world rest rotation of the source bone
R_T_rest = world rest rotation of the target bone
Δ = R_T_rest · R_S_rest⁻¹              (a constant per-bone correction quaternion)
```

**Step 2 — per frame, in world space:**

```
R_S(f) = world rotation of source bone at frame f
R_T_world(f) = Δ · R_S(f) · ?          — not quite; see below
```

The correct form, derived rather than guessed: what must be preserved is the bone's rotation *relative to its own rest*, expressed in the target's frame.

```
R_S_local_to_rest(f) = R_S_rest⁻¹ · R_S(f)                 # source's deviation from its rest, in source rest space
R_T_world(f)         = R_T_rest · R_S_local_to_rest(f)      # same deviation, applied in target rest space
                     = R_T_rest · R_S_rest⁻¹ · R_S(f)
                     = Δ · R_S(f)
```

So `Δ · R_S(f)` *is* right for world rotation — provided both are world rotations and both rest poses are the same *shape* (both A-pose, or both T-pose). If the rest poses differ in shape (source T-pose, target A-pose), `Δ` absorbs that difference and silently bakes a 45° shoulder offset into every frame. **Hence: normalise rest poses first**, or compute `Δ` from a matched reference pose rather than from rest. forge does the latter — it requires a `retarget_reference` pose on both skeletons (usually a T-pose) and computes `Δ` there. That one decision removes most retargeting pain.

**Step 3 — to local/parent space**, which is what Blender keys:

```
R_T_local(f) = (R_T_parent_world(f))⁻¹ · R_T_world(f) · R_T_rest_local_inverse
```

In practice, use Blender's own machinery instead of hand-rolling the basis change:

```python
pb.matrix = target_world_matrix                 # world-space pose matrix; Blender converts
# or, explicitly:
pb.matrix_basis = (pb.bone.matrix_local.inverted() @ parent_correction @ M_world)
```

`pose_bone.matrix` assignment handles the rest-matrix and parent chain for you and is the sane path. Set it per frame, key, then move on. Bone **roll** differences disappear automatically because roll is part of `bone.matrix_local`, which is exactly what `matrix_basis` divides out.

**Step 4 — translation and scale.**

- **Root height normalisation:** `scale = target_hip_height_rest / source_hip_height_rest`. Apply it to the root bone's translation. Without this, a 1.6 m source on a 2.4 m target walks on its knees.
- **Limb length scaling:** only matters for IK-driven targets. For an FK retarget, rotations are length-independent and you simply inherit the target's proportions — which is correct and is why FK retargeting is robust.
- **IK targets:** scale the end-effector's position by the *limb* ratio, not the height ratio, or the arms over/undershoot.
- **Unit mismatch:** BVH is commonly in centimetres and Blender in metres. Check the hip height after import; if it is ~100× wrong, that is the cause. The BVH importer exposes a scale factor — set it explicitly, never trust the default.

**Step 5 — fix the feet.** Rotation-only retargeting always introduces foot sliding, because the target's leg lengths differ. §7.4.

**Step 6 — clean up.** `quat_sign_continuity` (retargeting produces sign flips constantly, because it multiplies quaternions per frame), then `decimate(ERROR)`, then validate.

**Existing tools, honestly assessed:**

| tool | status | verdict |
|---|---|---|
| **Auto-Rig Pro** Remap | paid (~$40), actively maintained, visual bone mapping | the best turnkey option, but it is a paid GUI addon — wrong shape for an MCP server |
| **Rokoko Studio Live** (Blender addon) | free, on Blender's extension platform, includes a retarget panel | usable, but Rokoko's focus is hardware and the addon lags Blender releases; GUI-oriented |
| **Expy-Kit** | free, open source, bone-naming conversion + retarget helpers | the most forge-shaped of the existing options; worth vendoring the naming maps even if not the whole addon |
| **Blender built-in** | no first-class retargeter as of 5.x | constraint-based manual setup only |
| **Open Mocap** (GitHub) | open source but README states Blender ≤4.0 only | dead for our purposes |

**Recommendation: implement it.** The math above is ~250 lines. Every addon option is GUI-coupled, version-fragile, or paid, and forge needs the retargeter to be callable, deterministic and inspectable. Vendor Expy-Kit's naming tables (they are the tedious part), write the solver.

### 7.4 Foot sliding and ground contact fixup

Two-pass, applied after any retarget, bake or ML import.

**Pass 1 — detect contacts.** For each foot, per frame, compute toe/heel world height `h` and horizontal speed `s`. A frame is a contact when `h < h_thresh` and `s < s_thresh`. Thresholds, for a 1.75 m character at 24 fps: `h_thresh = 0.03 m`, `s_thresh = 0.012 m/frame` (= 0.29 m/s). Merge runs shorter than 2 frames into their neighbours; discard contact runs < 3 frames.

**Pass 2 — lock and re-solve.** For each contact run, pick the anchor position (the median of the run's positions, which is more robust than the first frame), snap the IK target to it for every frame of the run, then re-solve the leg IK. Where the leg cannot reach, lower the pelvis by the deficit, distributed over a ±4 frame window with a smooth falloff so the correction does not pop. This is the standard "IK lock + pelvis compensation" and it is the right order: never fix sliding by moving the foot *in the air*, always by moving the hips.

**Residual measurement** is the `foot_slide` metric (§9). Report per-contact total drift, not per-frame — animators and agents both reason about "the foot slid 2 cm during that step".

### 7.5 Motion editing

- **Time warping.** A monotonic map `f_out → f_in`, authored as its own small curve. Scale key frames through it and scale handle x-extents by the local derivative, or the easing changes meaning. forge exposes `retime(targets, warp=[[1,1],[12,8],[24,24]])` with linear or Bezier segments between control points.
- **Blending / transitions.** Two actions, a transition window of `n` frames. Quaternion slerp per bone with a smootherstep weight, `w = 6t⁵−15t⁴+10t³` (C² continuous, so no velocity pop at the seams — a plain smoothstep leaves an acceleration discontinuity you can see). Transition length: **6–10 frames** for same-speed same-gait, **12–20** for a gait change, and align the source and destination to matching contact phases first or the feet will cross. Phase alignment is what makes transitions work; interpolation quality is secondary.
- **Motion graphs.** A graph over clips with edges where poses and velocities match within tolerance. Useful for a runtime; **wrong for forge**, because forge's output is a baked clip, not a controller. Skip.
- **Motion matching.** Same verdict, more emphatically. Motion matching is a *runtime* technique: it searches a database per frame against a query of trajectory and pose features. It belongs in the engine, not in an asset generator. What *is* worth borrowing is its feature vector (future root trajectory at +20/+40/+60 frames, foot positions and velocities) as a **similarity metric** for forge's clip library search. That is a genuinely useful small piece.
- **Layered additive motion.** NLA `COMBINE` strips (§3.8). The canonical use: a base locomotion cycle plus an additive "injured" or "carrying" layer authored as deltas from the base's rest. forge's IR supports this directly as `layers[]` with `blend: "combine"`.

### 7.6 Video-to-motion in 2026

**GVHMR** (world-grounded human motion recovery via gravity-view coordinates) is the practical front end. It is now in print at TPAMI 2026 (vol. 48 no. 10), outputs ground-aligned SMPL/SMPL-X sequences from a single camera, and 2026 work still uses it as *the* off-the-shelf stage — e.g. 4DAnyone runs GVHMR to get a ground-aligned SMPL-X mesh sequence from casual monocular video. Its peers are **WHAM** and **TRAM**; 2026 papers (Mocap-2-to-3 at CVPR 2026, FlowHMR) benchmark against all three rather than clearly displacing them. FlowHMR targets physical plausibility specifically and reports improvement over GVHMR as the strongest baseline, though the extracted figure was garbled and I would not quote it.

**Is it worth wiring into forge? Not in v0 or v1.** Reasons, in order:

1. It needs a GPU to be anything but painful, and this container has none.
2. Output is SMPL-X parameters, so it inherits the whole SMPL licence problem (§7.2) *and* the SMPL→rig retarget problem (§8.5).
3. Quality is "good enough to retarget and then clean up", not "good enough to ship" — global trajectory drift and foot contact are still the weak points, which is precisely what the 2026 papers are chasing.
4. An agent cannot supply reference video in an MCP tool call anyway, in the normal case.

Correct position: a **v2 optional import path** — `mocap_import_video(path)` that shells out to a GVHMR install if one exists, lands the result as a tainted-provenance action on an SMPL skeleton, and hands it to the normal retarget + footlock + decimate pipeline. Document it as needing a GPU and a licence check. Do not make it a dependency.

---

## 8. ML motion generation — where it belongs

### 8.1 Text-to-motion, late 2026

| model | year | code licence | output | needs GPU | notes |
|---|---|---|---|---|---|
| **MDM** (Human Motion Diffusion Model) | 2022 | **MIT** **[verified]** | HumanML3D-263 → SMPL joints | yes for comfort (~6–8 GB), CPU possible and slow | the baseline everything forks |
| **MotionDiffuse** | 2022 | open | same family | yes | superseded |
| **T2M-GPT** | 2023 | open | VQ tokens → motion | yes | FID 0.141 on HumanML3D |
| **MLD** (latent diffusion) | 2023 | open | latent → motion | yes | much faster sampling than MDM |
| **MoMask** | 2023 | **MIT** **[verified]** | HumanML3D-263 | ~8 GB | **FID 0.045** on HumanML3D, the long-standing reference point; authors note limited diversity |
| **MotionGPT** | 2023 | open | motion-as-language | yes | good for editing-by-instruction |
| **OmniControl** | 2023 | open | spatially-controllable joints | yes | relevant: lets you pin a joint trajectory |
| **MotionLCM** | 2024 | open | real-time latent consistency | yes | fast; quality below MoMask |
| **CAMDM** | 2024 | open | character-controllable | yes | runtime-oriented |
| **MotionStreamer** | 2025–26 | open | streaming autoregressive | yes | on the HumanML3D-272 leaderboard at 11.79 FID (a *different* benchmark variant — not comparable to 0.045) |
| **LUMA** | 2025 (arXiv 2509.25304) | unverified | SMPL params | yes | reports **FID 0.035** on HumanML3D, beating MoMask on FID, R-Precision and MM-Dist |
| **FloodDiffusion** | 2025 (arXiv 2512.03520) | unverified | streaming | yes | FID 0.057, competitive with MoMask while streaming |
| **ReMoMask-2** | 2026 (arXiv 2609.08365) | unverified | retrieval-augmented masked | yes | claims lowest FID on KIT-ML and SnapMoGen (not HumanML3D) |
| **MARDM** | 2026 | unverified | — | yes | 7.044 on the HumanML3D-272 leaderboard, best of the three listed there |

**Benchmark caution, because the numbers above look comparable and are not.** MoMask's 0.045 and LUMA's 0.035 are on original HumanML3D. The 2026-03 SOTA2 leaderboard for **HumanML3D-272** puts MoMask at 12.232, MotionStreamer at 11.79, MARDM at 7.044 — a different dataset variant and evaluation protocol. Compare within a paper, never across.

**Verdict for forge: text-to-motion is not the right tool.** It produces a 20 fps SMPL sequence from a sentence. forge's user is Claude, which can author poses and timing *directly* and far more controllably than it can write a prompt and hope. Text-to-motion's value is as a **rough reference for an unfamiliar action** ("what does a capoeira kick even look like"), which is a research aid, not a production path. And the weights are AMASS-tainted.

### 8.2 Motion in-betweening — the one that matters

This is the ML task that fits the agent loop exactly: Claude blocks key poses, the model fills the gaps, Claude critiques and re-blocks. Keyframe-conditioned, not text-conditioned.

| model | year | code licence | conditioning | notes |
|---|---|---|---|---|
| **Robust Motion In-betweening** (Harvey et al.) | SIGGRAPH 2020 | — | fixed-length transitions | the origin; introduced LAFAN1 |
| **Δ-Interpolator** | 2022 | open | sparse keyframes | strong non-diffusion baseline |
| **CondMDI** / *Flexible Motion In-betweening with Diffusion Models* (Cohan et al., SIGGRAPH 2024, NVIDIA) | 2024 | **MIT** **[verified]** (repo `setarehc/diffusion-motion-inbetweening`) | **temporally-sparse keyframes AND partial pose specifications, plus optional text** | **the pick.** Partial-pose conditioning is exactly "I have authored the hips and feet, fill the arms" |
| **SILK** (Akhoundi, Ling, Deshmukh, Bütepage — CVPR 2025 HuMoGen workshop) | 2025 | code release unconfirmed | sparse keyframes | a **single Transformer encoder** matches complex models; the paper's finding is that data quantity, pose representation and velocity features matter more than architecture. **Read this before building anything** |
| **Scalable In-betweening + physics adaptation** | 2025 (arXiv 2504.09413) | — | keyframes, two-stage | second stage is a physics pass that kills foot sliding; beats Stage-1 and priors on K-FID, especially for long transitions |
| **Multi-level diffusion** | CVPR 2026 | — | precise keyframes + semantic text | compares against CondMDI; benchmark numbers not retrieved |
| **Latent diffusion over continuous implicit representations** | 2026 (arXiv 2605.12778) | — | extremely sparse keyframes, no text | claims SOTA for very sparse keyframe in-betweening |
| **Adaptive interpolation-synthesis (BiLSTM)** | 2026 (arXiv 2605.02742) | — | 5- and 10-frame gaps | claims SOTA on short LAFAN1 gaps; transformers still better for long sparse horizons |

**Metric soup warning:** these papers use L2Q, L2P, NPSS, K-FID and K-Error over different context lengths and gap sizes. There is no single current LAFAN1 leaderboard. "State of the art" is per-protocol.

**Verdict:** this is the only ML forge should actually integrate, and **SILK's finding is the strategic one** — if a single transformer encoder trained on good data matches the diffusion models, then forge's in-betweener can be small, CPU-feasible, and trained on **CMU mocap** (commercially usable) rather than on AMASS. That is a genuinely attractive v2: a compact in-betweener with clean provenance, which no published checkpoint offers.

Interim v1 position: **classical in-betweening wins and is free.** Given blocked extremes and declared easing, a Bezier spline with per-chain offsets and a spring pass covers most of what an in-betweener buys you, deterministically, in milliseconds, with no weights to ship. The ML model earns its place only for *long* gaps (>20 frames) of complex full-body motion, which is the minority case in asset production.

### 8.3 Physics-based RL controllers

| model | year | licence | runtime |
|---|---|---|---|
| **DeepMimic** | 2018 | open | Bullet; the lineage's origin |
| **AMP / ASE** | 2021/22 | open | Isaac Gym, NVIDIA GPU |
| **PHC** (Perpetual Humanoid Control, ICCV 2023) | 2023 | **BSD-3-Clause-Clear** **[verified]** | Isaac Gym; progressive multiplicative control policy; recovers from falls |
| **PULSE** (ICLR 2024 spotlight) | 2024 | (LICENSE not found at the usual paths) | Isaac Gym; latent motor-skill space; large code overlap with PHC; its bundled **PHC+** reports 100% success on AMASS |
| **SMPLSim** | — | open | supports PHC/PHC-MJX/PULSE/SimXR; **PHC-MJX is a MuJoCo variant**, the only CPU-plausible door |

**Verdict: out of scope.** The Isaac Gym dependency is a hard NVIDIA-GPU requirement, and even the MuJoCo variant is an RL inference loop per frame with an SMPL humanoid — orders of magnitude past what belongs behind an MCP tool call on 4 CPU cores. The capability these offer that nothing else does is *physically valid* motion with recovery; forge's affordable substitute is Bullet ragdoll with a keyed-to-sim handoff (§6.6) plus the gravity and support-polygon metrics (§9) to catch physical nonsense in keyed animation. That gets most of the benefit for none of the infrastructure.

### 8.4 Audio-driven

Music-to-dance (AIST++/FACT lineage, EDGE) and speech-to-gesture both exist, both need GPUs, both output SMPL, both are AMASS/AIST-tainted. **Cheap to add only in the sense that the API surface is small; expensive in every other sense.** Not v0, not v1, and only v2 if Wren actually wants dance. The one genuinely cheap audio feature worth having is lip-sync from a phoneme track driving shape keys — that is signal processing plus a viseme map, not ML, and `graph.sound_to_samples` already bakes an audio envelope to an F-curve for procedural audio-reactive secondary motion.

### 8.5 The SMPL → forge-rig problem

Every model above outputs SMPL or SMPL-X: a 22-joint (SMPL) or 55-joint (SMPL-X) tree with its own rest pose, axis-angle rotations, and a global translation, usually at 20 fps (HumanML3D) or 30 fps (AMASS). forge's rigs are not SMPL. So every ML path ends in a retarget, which means every ML path inherits:

1. **A rest-pose mismatch.** SMPL's rest is a specific A-pose with distinctive shoulder and hip angles. Handled by the reference-pose `Δ` of §7.3, not by rest-pose `Δ`.
2. **Joint-count mismatch.** SMPL has one spine3; a production rig has 3–5 spine bones. Distribute: give each target spine bone a fraction of the source's rotation, weighted by bone length, with the fractions summing to 1. Reverse for the opposite case (collapse multiple source joints into one target by composing their rotations).
3. **No twist.** SMPL has no roll/twist bones, so forearm and upper-arm twist must be synthesised: take the total twist implied by the hand's orientation and distribute it 40/60 between upper and lower arm (the anatomical split), which is also the standard fix for the "candy-wrapper" skinning artifact.
4. **Frame-rate mismatch.** Resample 20 or 30 fps to 24. Do it by slerp on rotations and Catmull-Rom on translation, *then* decimate — never by nearest-frame, which introduces a visible 4 Hz stutter at 30→24.
5. **No fingers** (SMPL) or crude fingers (SMPL-X). Pose them from a library, do not retarget them.
6. **Licence taint** propagated through all of it.

Which is the real argument for forge's priority order: **procedural and Claude-authored motion have no retarget step at all.** They are authored on the target rig in the target's units at the target's frame rate. Every ML and mocap path pays a five-step tax that the native paths simply do not.

### 8.6 Verdict: where each approach wins

| job | winner | why |
|---|---|---|
| walk, run, sneak, gait variants, quadruped gaits | **procedural** | parameterised, licence-clean, instantly re-tunable, no retarget |
| idle, breathing, micro-motion, jiggle, cloth lag, tails | **procedural** (noise + spring + wave) | deterministic, cheap, exactly controllable amplitude |
| acting, reactions, specific beats, stylised performance, anything with intent | **Claude authoring the IR** | this is the whole point of the system; an agent that knows the craft numbers and can see a critique report outperforms a prompt-to-motion model on controllability by a wide margin |
| filling long complex gaps between authored extremes | **ML in-betweening** (v2) | the one place a model beats splines |
| a reference for an unfamiliar physical action | **mocap search** (CMU) or text-to-motion as a sketch | lookup, not production |
| physically-exact falls, impacts, ragdoll | **Bullet sim + keyed handoff** | RL controllers are unaffordable here |
| realistic human nuance at volume | **mocap retarget** (CMU only, for commercial work) | nothing else gets you 2600 real performances |

---

## 9. The agent loop — how Claude animates without eyes

Three mechanisms, in order of importance: an IR it authors, numbers it reads, pictures it looks at.

### 9.1 The timeline IR — forge's central artifact

Design constraints: authorable by a language model in one message; diffable; frame-exact; expressive enough for "contact at 1, passing at 7, hips lead by 2, snap out, add a settle"; and *compilable without inference* — every field has one meaning.

```jsonc
{
  "forge_anim": "1.0",
  "name": "walk_brisk_a",
  "rig": "rig_manifest.json",
  "fps": 24,
  "range": [1, 24],
  "stage": "block",                      // block | breakdown | spline | polish | offset
  "intent": "brisk business walk, slight forward lean, confident",

  "loop": {
    "cyclic": true,
    "seam": "wrap",                      // wrap: frame range+1 must equal frame range start
    "root_motion": "offset"              // offset | hold | none  -> Cycles mode on the root
  },

  "pose_library": ["poses/humanoid_locomotion.json", "poses/char_specific.json"],

  "defaults": {
    "ease": "smooth",
    "rotation_mode": "from_rig",
    "key_type": "KEYFRAME"
  },

  "keys": [
    { "frame": 1,  "pose": "walk_contact_L", "label": "contact",  "key_type": "EXTREME" },
    { "frame": 4,  "pose": "walk_down_L",    "label": "down",     "key_type": "BREAKDOWN",
      "ease": { "out": "snap", "in": "ease_in" } },
    { "frame": 7,  "pose": "walk_passing_L", "label": "passing",  "key_type": "BREAKDOWN",
      "channels": {
        "hips.rotation_quaternion":  { "axis_angle": ["Z", 5.0] },
        "chest.rotation_quaternion": { "axis_angle": ["Z", -8.0] },
        "head.rotation_quaternion":  { "value": [1, 0, 0, 0], "ease": "float" }
      } },
    { "frame": 10, "pose": "walk_up_L",      "label": "up",       "key_type": "BREAKDOWN" },
    { "frame": 13, "pose": "walk_contact_L", "label": "contact",  "key_type": "EXTREME",
      "mirror": true },
    { "frame": 16, "pose": "walk_down_L",    "label": "down",     "key_type": "BREAKDOWN", "mirror": true },
    { "frame": 19, "pose": "walk_passing_L", "label": "passing",  "key_type": "BREAKDOWN", "mirror": true },
    { "frame": 22, "pose": "walk_up_L",      "label": "up",       "key_type": "BREAKDOWN", "mirror": true },
    { "frame": 25, "pose": "walk_contact_L", "label": "contact",  "key_type": "EXTREME", "seam_of": 1 }
  ],

  "offsets": [
    { "chains": ["spine"],        "frames": 1, "per_bone": 0.5, "mode": "propagate" },
    { "chains": ["arm_L","arm_R"],"frames": 3, "per_bone": 1.0, "mode": "propagate" },
    { "bones":  ["neck","head"],  "frames": 4, "mode": "shift" },
    { "chains": ["tail"],         "frames": 2, "per_bone": 1.5, "mode": "propagate" }
  ],

  "layers": [
    { "name": "base",    "blend": "replace", "influence": 1.0 },
    { "name": "breathe", "blend": "combine", "influence": 0.8,
      "generated": { "op": "idle", "parts": ["breath"], "period_frames": 96 } },
    { "name": "limp",    "blend": "combine", "influence": 0.0,
      "keys": [ { "frame": 4, "channels": { "hips.location": { "value": [0, 0, -0.035] } } } ] }
  ],

  "modifiers": [
    { "type": "noise",  "targets": ["spine.*", "head"], "channels": ["rotation"],
      "strength_deg": 0.35, "scale": 11, "phase": "per_channel", "blend": "ADD" },
    { "type": "cycles", "targets": ["*"],      "mode": "REPEAT" },
    { "type": "cycles", "targets": ["root"],   "mode": "REPEAT_OFFSET" }
  ],

  "passes": [
    { "op": "spline",      "ease_default": "smooth" },
    { "op": "settle",      "targets": ["hand_L_ik","hand_R_ik"], "at": "each_extreme",
      "overshoot": 0.12, "frames": 4, "bounces": 1 },
    { "op": "moving_hold", "ranges": [[10, 16]], "drift_deg": 1.2 },
    { "op": "spring",      "targets": ["tail","hair"], "stiffness": 16, "damping": 0.3, "amount": 0.8 },
    { "op": "arc_fix",     "targets": ["hand_L_ik","hand_R_ik"], "method": "match_slope" },
    { "op": "decimate",    "mode": "ERROR", "rotation_error": 0.002, "location_error": 0.0015 }
  ],

  "checks": {
    "foot_slide_mm_per_contact": 10.0,
    "loop_seam_deg": 0.5,
    "loop_seam_vel_deg_per_frame": 1.0,
    "quat_hemisphere_violations": 0,
    "cog_outside_support_frames": 2,
    "stride_consistency_pct": 5.0,
    "key_density_per_24f": 8
  },

  "provenance": { "source": "procedural", "licence": "none", "commercial_ok": true }
}
```

Field semantics, precisely:

- **`keys[]`** is ordered by frame; a key is a pose reference, optional per-channel overrides, and optional easing. `pose` names an entry in a pose library; omit it and only `channels` apply (a partial key — exactly how a human keys one limb).
- **`mirror: true`** applies the rig manifest's mirror map to the named pose. `seam_of: N` means "this key is a copy of the key at frame N" and forge *verifies* rather than trusts it — the loop-seam check.
- **`ease`** is either a preset name, a `{out, in}` pair of presets, or a raw `[out_frac, in_frac]`. It applies to the segment *leaving* this key (`out`) and *arriving* at this key (`in`). This split is important: an animator sets the out-handle of one key and the in-handle of the next independently, and the IR must allow that.
- **`channels`** keys are `"<bone>.<property>"`, with `value` (raw, mode-appropriate), `axis_angle` (`["Z", degrees]` — far easier for an agent than quaternion components, and forge converts), or `euler_deg` (`[x, y, z]`). Any of them with an optional per-channel `ease`.
- **`offsets[]`** runs after keys are written and before `passes`. `shift` = uniform, `propagate` = cumulative `per_bone`.
- **`layers[]`** compile to NLA tracks bottom-up. `blend: "combine"` for additive rotation layers, always. `generated` invokes a procedural generator into that layer. An `influence: 0.0` layer is authored-but-off, which is how an agent stages an option without committing.
- **`modifiers[]`** are F-modifiers, applied last so they stack on the final curves. `strength_deg` is converted to radians by forge, because an agent thinking in degrees and a channel measured in radians is a reliable source of 57×-too-large noise.
- **`passes[]`** run in order. Order matters and the IR makes it explicit rather than implicit.
- **`checks`** are the thresholds this clip is held to; the critique report scores against them. Overridable per clip, because a cartoon squash-and-stretch cycle and a realistic walk have different tolerances.
- **`stage`** gates what the compiler does: `block` forces `CONSTANT` interpolation and ignores `passes` after `spline`; `spline` turns on Bezier and runs the easing; `polish` runs everything.

The compiler is a pure function: `IR + rig_manifest → action`. Deterministic, re-runnable, and the IR is the file under version control. **Claude edits the IR, not the F-curves.** Direct curve tools exist for surgical fixes, but the IR is the source of truth and `compile` is idempotent.

### 9.2 Pose library format

```jsonc
{
  "forge_poses": "1.0",
  "rig_class": "humanoid_biped",
  "poses": {
    "walk_contact_L": {
      "tags": ["locomotion", "contact", "left"],
      "mirror_of": null,
      "bones": {
        "hips":        { "location": [0, 0, -0.02], "quat": [0.999, 0, 0, 0.044] },
        "thigh_L":     { "euler_deg": [22, 0, 0] },
        "shin_L":      { "euler_deg": [-4, 0, 0] },
        "foot_L_ik":   { "location": [0.0, 0.33, 0.0], "euler_deg": [-12, 0, 0] },
        "foot_R_ik":   { "location": [0.0, -0.34, 0.0], "euler_deg": [14, 0, 0] },
        "chest":       { "quat": [0.998, 0, 0, -0.063] },
        "upperarm_L":  { "euler_deg": [-18, 0, 0] }
      },
      "contacts": { "foot_L": "heel", "foot_R": "toe" },
      "notes": "max leg spread, body at mid height, heel strike left"
    }
  }
}
```

- Bones not listed are untouched — so a pose is a *partial* pose by default, which is what makes a pose library composable (a hand pose and a leg pose applied together).
- `euler_deg` and `quat` both accepted; forge converts to the rig's declared mode and sign-corrects.
- `mirror_of` lets `walk_contact_R` be generated rather than stored. `pose_mirror` uses the manifest's mirror map and negates the correct quaternion components (for an X-mirror: negate y and z of the quaternion, negate Y/Z Euler, negate X location) — and it must mirror *bone names* too.
- `contacts` records which part of the foot is down in this pose, which the generator and the foot-slide metric both read.
- `tags` make the library searchable, which matters once it has 200 poses.

Poses are captured from the live rig (`pose_save`), so the library grows from work rather than being authored cold.

### 9.3 Visual feedback — what MCP can actually return

Confirmed against the **current MCP specification revision, `2026-07-28`** (the revision list names it current; `2025-11-25` and earlier are the handshake-based predecessors). **[verified: fetched modelcontextprotocol.io]**

Tool results carry `content[]` of: **`text`**, **`image`** (`{type, data: base64, mimeType, annotations?}`), **`audio`**, **`resource_link`** (`{type, uri, name, description, mimeType}`), **`resource`** (embedded), plus optional **`structuredContent`** validated against the tool's **`outputSchema`**. There is **no video content type.** `resultType` is `"complete"`; errors are `isError: true` with actionable text.

So:

| want | mechanism |
|---|---|
| a single frame, a pose, a contact sheet, an arc overlay, a graph of a curve | **`image`** block, PNG base64 |
| a playblast "video" | **not possible as video.** A GIF is `image/gif` and a client *may* animate it, but do not rely on it — return a **contact sheet** as the primary and a `resource_link` to the MP4/GIF on disk for the human |
| metrics, critique report | **`structuredContent`** + a compact `text` mirror (the spec says to also return the serialized JSON as text for backwards compatibility) |
| the written .blend / .glb / IR file | **`resource_link`** — never embed a binary asset |

**Image token cost** is driven by pixel dimensions, not file size: roughly `(width × height) / 750` tokens, and images are downscaled so the long edge is ≤1568 px. Which sets forge's defaults hard:

| product | dimensions | ≈ tokens | when |
|---|---|---|---|
| pose thumbnail | 384 × 384 | ~200 | every pose op, cheap enough to be automatic |
| contact sheet, 6×4 of a 24f cycle | 1440 × 1080 | ~2 100 | the default visual for any clip |
| contact sheet, 4×3 of 12 keys | 960 × 720 | ~920 | blocking review |
| single review frame | 960 × 540 | ~690 | a specific pose question |
| arc overlay | 960 × 540 | ~690 | when the arc metric flags |
| curve graph (matplotlib) | 800 × 500 | ~530 | when a channel is suspect |
| side-by-side A/B | 1280 × 480 | ~820 | after an iteration |

**Rule: never return more than ~2 500 image tokens in one result, and never return an image the metrics did not justify.** A contact sheet plus a 300-token critique is the right default; six playblasts is how you burn a context window and learn nothing.

Products and how they are made:

1. **Playblast** → Workbench PNG sequence (0.112 s/frame at 480×270) → ffmpeg to MP4/GIF → `resource_link`. 24 frames = 2.7 s + 0.6 s. **[verified]**
2. **Contact sheet** → same sequence, `ffmpeg -vf "scale=240:-1,tile=6x4"`, 0.35 s. **[verified]** Frame numbers burned in via `drawtext` so Claude can refer to them. This is the single most valuable image forge produces: one picture, the whole timing.
3. **Pose thumbnail** → single Workbench frame, three-quarter camera, `show_object_outline=True`.
4. **Silhouette pass** → `shading.light='FLAT'`, `color_type='SINGLE'`, single colour black, white world. **[verified available]** Feeds the silhouette metric *and* makes the strongest possible pose-readability image.
5. **Motion trail** → sample the end-effector world positions, build a poly-line mesh, render it over the frame in a contrasting single colour. Dots at key frames, so spacing is visible — which is exactly how an animator reads an arc.
6. **Curve graph** → matplotlib from `fcurve.evaluate()` samples: value, velocity, and key positions on one axis set. Cheaper and clearer than trying to render the Graph Editor.
7. **A/B** → two contact sheets stacked with labels. The critique's before/after.

### 9.4 Numeric critique — the metrics

The real eyes. All of these are computable in well under a second (sampling 40 bones × 240 frames = 0.04 s **[verified]**), so forge computes the whole set every time and reports only what matters.

Thresholds below are for a **1.75 m biped at 24 fps**; spatial ones scale by `character_height_m / 1.75`, temporal ones by `24 / fps`.

| # | metric | definition | good | warn | fail |
|---|---|---|---|---|---|
| 1 | `quat_hemisphere_violations` | adjacent quaternion keys with dot < 0, per channel | **0** | — | ≥1 (**always a bug**) |
| 2 | `euler_jump_deg` | max \|Δ\| between adjacent keys on one Euler channel | <150° | 150–180° | >180° (wrap) |
| 3 | `foot_slide_mm` | total horizontal drift of the plant bone across one contact run | <10 | 10–30 | >30 |
| 4 | `foot_float_mm` | min height of the plant bone during a contact run, above ground | <5 | 5–20 | >20 |
| 5 | `loop_seam_pose_deg` | max per-bone angle between frame `end+1` and frame `start` | <0.5 | 0.5–2 | >2 |
| 6 | `loop_seam_vel_deg_f` | difference in per-bone angular velocity across the seam | <1.0 | 1–3 | >3 |
| 7 | `pop_ratio` | max per-bone \|Δangle\| ÷ median \|Δangle\| over the clip | <3× | 3–6× | >6× |
| 8 | `jerk_peak_norm` | max ‖third difference‖ of an end-effector ÷ chain length, per frame³ | <1.5 | 1.5–5 | >5 |
| 9 | `arc_straightness` | max perpendicular deviation from the chord ÷ chord length, per gesture | 0.06–0.25 | 0.02–0.06 | <0.02 (**no arc**) |
| 10 | `arc_curvature_flips` | signed-curvature sign changes per gesture | ≤1 | 2 | ≥3 |
| 11 | `cog_support_violation_frames` | frames with the COG ground-projection outside the 30 mm-dilated support polygon, while grounded | 0 | 1–2 | ≥3 consecutive |
| 12 | `gravity_error_pct` | \|measured COG vertical second difference − g/fps²\| ÷ (g/fps²) during flight; g/fps² = **17.0 mm/frame²** at 24 fps | <15% | 15–40% | >40% |
| 13 | `stride_consistency_pct` | \|root travel per cycle − (stride × steps)\| ÷ root travel | <5% | 5–15% | >15% |
| 14 | `counter_rotation_phase_deg` | phase of chest-yaw vs pelvis-yaw (should be antiphase) | 155–205° | 130–230° | outside (0° = **mechanical**) |
| 15 | `overlap_lag_frames` | per chain, velocity-peak lag behind the leader | 1–6 | 0 or 7–8 | 0 for *all* chains = no overlap pass |
| 16 | `silhouette_area_delta` | per-frame change in silhouette pixel area, normalised | — | — | <3% between consecutive extremes = **poses not reading** |
| 17 | `hold_drift_deg` | max per-bone motion inside a declared hold | 0.5–2.0 | 0.2–0.5 | <0.1 (**dead hold**) or >4 (not a hold) |
| 18 | `volume_error` | \|sx·sy·sz − 1\| at max squash/stretch | <0.05 | 0.05–0.15 | >0.15 (realistic) / >0.4 (cartoon) |
| 19 | `interpenetration_count` | frames with capsule-capsule overlap between non-adjacent bones | 0 | 1–3 | >3 |
| 20 | `key_density` | keys per channel per 24 frames after decimation | block ≤4, spline ≤8 | 8–16 | >16 (**baked, not animated**) |
| 21 | `channel_coverage` | fraction of `keyable` channels with any key | — | — | report only; tells Claude what it forgot |
| 22 | `gimbal_risk_frames` | frames where an Euler bone's middle axis is within 10° of ±90° | 0 | 1–5 | >5 |
| 23 | `limit_violation_frames` | frames exceeding the rig's declared joint limits | 0 | 1–2 | ≥3 |
| 24 | `ik_unreachable_frames` | frames where an IK target is beyond the chain's reach | 0 | 1–2 | ≥3 |

Derivations worth restating, since they are what make the numbers defensible rather than invented:

- **#12** comes straight from Garcia's Odd Rule: the first spacing below the apex is g/(2·fps²), so the per-frame increment is **g/fps² = 9.81/576 = 17.0 mm/frame²** at 24 fps — which matches his published 1/3 inch on ones exactly.
- **#3**'s 10 mm is 0.57% of character height, chosen to sit comfortably above the 1.5 mm location decimation tolerance (§3.7) so decimation can never *cause* a foot-slide failure.
- **#9**'s 0.06 lower bound is the point below which an arc stops reading as an arc; it is the one threshold here that is a judgement call rather than a derivation, and it should be tuned against real clips in v1.
- **#14** is the pelvis ±5° / chest ±8° antiphase relationship from §5.4.

### 9.5 The critique report

What Claude reads. Target: **under 350 tokens**, scored, prioritised, actionable, with the frame numbers needed to act.

```
CRITIQUE  walk_brisk_a  stage=spline  24f @24fps  rig=char_rig
SCORE 71/100   (block 88 | spline 71 | polish —)

FAIL  2
  foot_slide        R foot, contact f13-f20: 41mm drift (max 30)
                    -> fix: footlock(foot_R, 13, 20) then ik_resolve
  arc_straightness  hand_L_ik f7-f19: 0.011 (min 0.02) -- straight line, no arc
                    -> fix: breakdown(hand_L_ik, 13, perp=+0.08) or arc_fix(hand_L_ik)

WARN  3
  loop_seam_vel     hips.rotZ: 2.2 deg/f across seam (max 1.0)
                    -> fix: match_slope(hips.rotation, [1,25])
  overlap_lag       head 0f, neck 0f  -- offset pass has not run
                    -> fix: offset(chains=[spine], frames=1, per_bone=0.5); offset(bones=[neck,head], frames=4)
  counter_rotation  chest/pelvis phase 12 deg (want 155-205) -- reads mechanical
                    -> fix: set chest.rotZ opposite sign to pelvis at f1,f7,f13,f19

PASS 19   quat_hemisphere 0 | gravity n/a (grounded) | cog_support 0 | key_density 5.2
          pop_ratio 2.1x | volume 0.01 | interpenetration 0 | limits 0 | ik_reach 0 ...

NEXT  footlock -> arc_fix -> offset -> recompile -> critique
sheet: resource://forge/walk_brisk_a/sheet_v3.png  (1440x1080)
```

Design rules that make this work:
- **Fails first, with the fix named as an actual tool call.** An agent given a diagnosis and a prescription acts; one given a number argues with it.
- **Frame ranges always.** "Foot slides" is useless; "R foot, f13–f20" is actionable.
- **Passing metrics collapse to one line.** They exist to prove the check ran, not to be read.
- **A single score per stage**, so iteration progress is visible across turns. Weight the score by stage: at `block`, pose/timing/support metrics dominate and arc/jerk are not scored at all; at `spline`, continuity and arcs dominate; at `polish`, holds, volume, density and overlap.
- **`NEXT`** is an ordered plan, because the fixes interact — footlock before arc fix (locking moves the hand), offset after arcs (offsetting invalidates arc measurements), decimate last, always.

### 9.6 Iteration protocol

The concrete loop. forge should *enforce* this ordering, not merely suggest it, because an agent left to itself will go straight to polish on unsplined blocking.

```
0  rig_load            -> manifest summary, chain list, keyable channels
1  anim_new(IR)        -> stage=block, stepped keys, extremes only
2  compile             -> action written
3  critique + sheet    -> score(block). Loop 1-3 until block passes:
                          pose readability, timing, COG/support, silhouette deltas
4  stage_advance(breakdown) -> add the path-defining keys between extremes
5  compile, critique   -> arcs are now meaningful. Loop 4-5.
6  stage_advance(spline)    -> Bezier on, easing presets applied
7  compile, critique   -> pops, flips, seams, arcs. Loop 6-7.
8  offset pass         -> per-chain time offsets
9  compile, critique   -> overlap_lag now nonzero; re-check seams
10 polish              -> settles, overshoots, moving holds, spring, noise layers
11 compile, critique   -> holds, density, volume, interpenetration
12 decimate + validate -> final key counts, then export
```

Guard rails worth building in:

- **Checkpoint on every `stage_advance`** and every `compile` that follows a failing critique. `checkpoint_restore` is one call. An agent that can cheaply undo experiments more and panics less.
- **Refuse to `export` on a failing critique** unless `force=True` is passed, and name the failures in the refusal.
- **Cap the loop.** If three consecutive critiques at the same stage do not improve the score, return a different kind of result: the A/B sheet plus the two best candidates, and say plainly that the metric is not responding to the edits. That is the honest answer, and it stops an agent grinding.
- **Never auto-fix silently.** `critique` reports; `fix_*` tools act. An agent that did not choose the fix cannot learn which fixes work.

---

## 10. Export and engine integration

### 10.1 glTF 2.0

The bundled exporter is `io_scene_gltf2` **v5.3.43** **[verified]**. Animation-relevant options, with their actual defaults **[verified by introspecting the operator's RNA]**:

| option | default | what it does |
|---|---|---|
| `export_animations` | `True` | active actions + NLA tracks → glTF animations |
| `export_animation_mode` | `'ACTIONS'` | `ACTIONS` / `ACTIVE_ACTIONS` / `NLA_TRACKS` / `SCENE` |
| `export_force_sampling` | `True` | **sample every frame instead of exporting Bezier** |
| `export_frame_range` | `False` | clip to the scene playback range — **set this True** |
| `export_frame_step` | `1` | evaluation interval |
| `export_optimize_animation_size` | `True` | drop duplicate keyframes |
| `export_optimize_animation_keep_anim_armature` | `True` | keep a minimal animation even if all keys are identical |
| `export_nla_strips` | `True` | multiple actions on NLA become one glTF animation |
| `export_merge_animation` | `'ACTION'` | how tracks merge (4.4+ merges by action, not by name) |
| `export_anim_slide_to_zero` | `False` | start every animation at t=0 — **useful for loops** |
| `export_morph_animation` | `True` | shape-key animation → morph target weights |
| `export_sampling_interpolation_fallback` | `'LINEAR'` | for unkeyed properties |
| `export_bake_animation` | `False` | force-export animation on every object (constraints/drivers) |
| `export_yup` | `True` | glTF's +Y up convention |
| `export_negative_frame` | `'SLIDE'` | slide or crop negative frames |
| `export_convert_animation_pointer` | `False` | `KHR_animation_pointer` for non-TRS properties |

Measured round-trip **[verified]**: a 3-bone armature with quaternion keys at frames 1/12/24 exported with `export_force_sampling=True` produced one animation, **9 channels / 9 samplers**, targeting `translation`, `rotation`, `scale`, with sampler interpolations `{LINEAR, STEP}`. Generator string `Khronos glTF Blender I/O v5.3.43`.

**What survives:** baked TRS per joint node, morph-target weight tracks, multiple named animations, and that is essentially it.

**What does not survive, and must be baked first:** constraints (IK, copy-rotation, limits), drivers, NLA blend modes and influence curves, F-modifiers (noise, cycles, stepped), bendy-bone channels, custom properties, pose markers, action slots. Note `export_force_sampling=True` means **Bezier handles are gone** — the curve becomes per-frame LINEAR samples. That is a fidelity-for-compatibility trade and it is the right one for engines, but it is why §3.7's decimation matters: without it you ship 240 samples per channel.

Conventions: glTF is **+Y up, −Z forward, right-handed, metres**. Blender is **+Z up, −Y forward, metres**. `export_yup=True` handles the axis swap. Verify `unit_scale == 1.0` before export or everything is off by a factor.

### 10.2 FBX

`io_scene_fbx`, **[verified]** exporting a 3-bone animated armature in **0.03 s** to 43 KB.

```python
bpy.ops.export_scene.fbx(filepath=..., bake_anim=True,
    bake_anim_use_all_bones=True, bake_anim_use_nla_strips=False,
    bake_anim_use_all_actions=True, bake_anim_step=1.0,
    bake_anim_simplify_factor=1.0, add_leaf_bones=False,
    axis_forward='-Z', axis_up='Y')
```

- `add_leaf_bones=False` — **always.** Leaf bones are the single most common cause of "my Unity rig has twice as many bones as my Blender rig".
- `bake_anim_simplify_factor` is FBX's own key reduction (0 = none, 1 = default, higher = more aggressive). Prefer forge's own decimation (measurable, inspectable) and leave this at 1.0, or set 0.0 if you have already decimated.
- `bake_anim_use_all_actions=True` exports every action as a take; `False` + NLA strips exports the stack as one.
- `axis_forward='-Z', axis_up='Y'` is the Unity/Maya convention.

FBX carries multiple takes and is still what most studios ingest; glTF is cleaner and better specified. forge should offer both and default to glTF.

### 10.3 Clip splitting, root motion, looping

**Clip splitting.** One action per clip is the clean model (`export_animation_mode='ACTIONS'`). The alternative — one long action with markers, split at export — is where off-by-one errors live. forge's `clip_split(action, [(name, start, end), ...])` creates separate actions with their own slots, copies the key subrange, trims handles at the boundaries, and re-keys the boundary frames explicitly so no clip depends on a neighbour's extrapolation.

**Root motion extraction.** For an in-place cycle that an engine will drive:

```
1. Measure total horizontal travel of the root bone over the clip: Δp = p(end) − p(start).
2. Write Δp (and Δyaw) into the clip metadata as velocity: Δp / duration_seconds.
3. Subtract the per-frame horizontal travel from the root channel, leaving vertical bob and rotation intact.
4. Re-key, then verify foot_slide — it will have *changed*, because the feet were planted in world space
   and are now planted in a moving frame. This is the step everyone forgets.
```

In-place → root-motion is the inverse: integrate the declared velocity onto the root channel, then re-run footlock. Either direction, **re-run the foot-slide metric afterwards**, because the conversion is exactly the operation that breaks contacts.

**Looping metadata.** glTF has no standard loop flag — looping is the importer's business. So forge emits a sidecar `clips.json`:

```json
{"clips": [{"name": "walk", "start": 1, "end": 24, "loop": true, "fps": 24,
            "root_velocity_mps": [0, 1.34, 0], "root_angular_dps": 0.0,
            "contacts": {"foot_L": [[1,8],[13,20]], "foot_R": [[13,20],[1,8]]},
            "provenance": {"source": "procedural", "commercial_ok": true}}]}
```

and, on the Blender side, sets `action.use_cyclic` and `action.use_frame_range` + `action.frame_range` so a human opening the file sees the intent. The contact windows in the sidecar are genuinely valuable downstream — footstep audio, IK adjustment and procedural leaning all want them.

**The loop seam rule:** frame `end+1` must equal frame `start`. So a 24-frame cycle is authored with keys at 1 **and** 25, where 25 is verified identical to 1, and exported as frames 1–24. If you export 1–25 you get a one-frame hitch every loop. forge's `loop_seam_pose_deg` metric exists to catch exactly this and the IR's `seam_of` field declares the intent.

### 10.4 Engine conventions

| | up | forward | units | scale | notes |
|---|---|---|---|---|---|
| **Blender** | +Z | −Y | m | 1.0 | source of truth |
| **glTF** | +Y | −Z (right-handed) | m | 1.0 | `export_yup=True` |
| **Unity** | +Y | +Z (**left-handed**) | m | 1.0 | FBX `axis_up='Y', axis_forward='-Z'`; the handedness flip is why mirrored animation sometimes inverts. Humanoid avatars re-retarget on import — set Avatar to Generic to keep forge's exact curves |
| **Unreal** | +Z | +X (**left-handed**) | **cm** | **100×** | Blender 5.x FBX/glTF handles the 100× via unit scale, but verify; UE expects `pelvis` at the root with a `root` bone at origin for root motion, and `Enable Root Motion` + `Force Root Lock` on the anim sequence |
| **Godot 4** | +Y | −Z | m | 1.0 | glTF is the native path; `.glb` imports cleanly with animations. Loop mode is set per-animation in the import dock or via an import script |

Unreal specifics worth encoding in forge's exporter presets: UE's root motion wants the travel on a bone literally named `root` at the origin, and UE's retargeter is happier with a bone naming convention it recognises (`pelvis`, `thigh_l`, `calf_l`, `foot_l`, `clavicle_l`, `upperarm_l`, `lowerarm_l`, `hand_l`, `spine_01..05`, `neck_01`, `head`). Offer a `naming="ue5"` option on export that renames on the way out via a mapping table — renaming at export is safe, renaming the rig is not.

### 10.5 Compression and key reduction trade-offs

| approach | ratio | cost |
|---|---|---|
| forge decimate at the §3.7 tolerances | 3–6× fewer keys | ≤0.23° joint error — invisible |
| aggressive decimate (`remove_error_margin` ×5) | 10–20× | visible flattening of extremes; **this is where animation dies** |
| glTF `export_optimize_animation_size` | removes duplicate keys only | free, lossless, leave on |
| quantised sampler data (16-bit via `KHR_mesh_quantization`-style tooling) | ~2× file size | post-process with `gltfpack`/`gltf-transform`, outside Blender |
| drop scale channels when unanimated | ~30% of channels on a typical rig | free — forge should do this by default |
| drop translation on non-root, non-IK bones | large | correct for rotation-only rigs; **wrong** if the rig has translating controls. Read `keyable` from the manifest, don't guess |

The honest guidance for the agent: **decimate to the perceptual threshold and stop.** File size is almost never the binding constraint for an animation clip; curve editability and extreme preservation are. The one real win is dropping channels that never move, which is lossless.

### 10.6 Validators that catch broken animation

Run before every export, hard-fail on the first four:

1. `quat_hemisphere_violations == 0` across every quaternion channel.
2. No NaN or inf in any keyframe `co`, `handle_left`, or `handle_right` (a bad IK solve or a divide-by-zero in a spring pass produces these, and they propagate silently into the exported file where they become an unopenable asset).
3. Every keyed channel's `data_path` resolves on the rig — stale paths from renamed bones export as orphan channels.
4. Keys are sorted and deduplicated per channel (`keyframe_points.sort()` + `deduplicate()`).
5. Loop seam within tolerance if `loop.cyclic`.
6. Frame range matches the declared clip range; no keys outside it unless `seam_of`.
7. No keys on bones outside `keyable`.
8. Rotation mode of every keyed bone matches the manifest.
9. Root motion: either all horizontal travel is on the root bone, or `root_motion: "none"` is declared.
10. Provenance: if any contributing clip is `commercial_ok: false`, refuse a commercial export and name the clip.
11. Post-export readback — re-import the .glb and compare joint world positions at 5 sampled frames against the source, tolerance 1 mm. This catches axis-convention and unit errors, which are the failures that look fine in Blender and arrive broken in the engine. Cheap: one import, 5 `frame_set` calls.

---

## 11. MCP tool surface

Specified as Python signatures with return contracts. Design rules throughout:

- **Returns are token-cheap.** Summaries and counts, never curve dumps. Anything large becomes a `resource_link`.
- **Every mutating tool returns a `checkpoint` id** so the agent can always get back.
- **Frames are integers, angles are degrees, distances are metres.** The server converts to radians internally. An agent thinking in radians is an agent making 57× errors.
- **`targets` accepts bone names, chain names from the manifest, or glob patterns** (`"spine.*"`, `"*_ik"`), resolved against `keyable` and reported if a pattern matched nothing.
- **`outputSchema`** declared on every tool, returning `structuredContent` plus a compact text mirror.

```python
# ─────────────────────────────── session & rig ───────────────────────────────

def rig_load(blend_path: str, manifest_path: str | None = None) -> dict:
    """Open a .blend and read its rig manifest (§4). Required before anything else.
    Returns {session, rig, bones: int, chains: {name: count}, keyable: int,
             rotation_modes_summary, character_height_m, fps, has_root_motion: bool,
             warnings: [str]}.  Warnings name every contract violation (§4) found."""

def rig_info(session: str, detail: str = "summary") -> dict:
    """detail ∈ {summary, chains, keyable, ik, contacts, limits, full}.
    Returns the requested slice of the manifest. 'full' is a resource_link, not inline."""

def scene_set(session: str, fps: int = 24, frame_start: int = 1,
              frame_end: int | None = None) -> dict:
    """Set frame rate and range. Returns the resolved {fps, fps_base, range}."""

# ─────────────────────────────────── poses ───────────────────────────────────

def pose_set(session: str, bones: dict[str, dict], frame: int | None = None,
             key: bool = False, additive: bool = False) -> dict:
    """Apply a partial pose. Each bones[name] is {quat|euler_deg|axis_angle|location|scale}.
    additive=True composes onto the current pose instead of replacing.
    key=True inserts keyframes at `frame`.
    Returns {bones_set: int, rejected: [{bone, reason}], frame, checkpoint}."""

def pose_get(session: str, bones: list[str] | None = None,
             frame: int | None = None, space: str = "local") -> dict:
    """Read the current or frame-evaluated pose. space ∈ {local, world}.
    Returns {frame, bones: {name: {euler_deg|quat, location}}} — rounded to 4 dp
    to keep it readable. Omit `bones` for the whole keyable set."""

def pose_save(session: str, name: str, library: str, bones: list[str] | None = None,
              tags: list[str] = (), contacts: dict | None = None,
              notes: str = "") -> dict:
    """Capture the current pose into a pose library file (§9.2).
    Returns {name, library, bones: int, thumbnail: resource_link}."""

def pose_load(session: str, name: str, library: str | None = None,
              frame: int | None = None, key: bool = False,
              mirror: bool = False, blend: float = 1.0) -> dict:
    """Apply a library pose, optionally mirrored, optionally partially blended
    (blend<1 slerps from the current pose). Returns like pose_set."""

def pose_mirror(session: str, frame: int | None = None,
                bones: list[str] | None = None, key: bool = False) -> dict:
    """Mirror the current pose across the manifest's mirror axis, swapping L/R bone
    names. Returns {bones_mirrored: int, unpaired: [str], checkpoint}."""

def pose_library_search(session: str, query: str = "", tags: list[str] = (),
                        limit: int = 20) -> dict:
    """Returns {poses: [{name, tags, bones, notes}], total} — names and tags only."""

# ─────────────────────────────── IR & compilation ────────────────────────────

def anim_new(session: str, ir: dict, name: str | None = None) -> dict:
    """Validate and store a timeline IR document (§9.1). Does not compile.
    Returns {name, stage, keys: int, layers: int, passes: int,
             errors: [{path, message}], warnings: [...]}.
    Validation is schema + semantics: unknown bones, keys outside range,
    easing names, mirror targets, seam_of consistency."""

def anim_get(session: str, name: str, section: str = "all") -> dict:
    """Read back the stored IR. section ∈ {all, keys, offsets, layers, passes, checks}.
    Large sections return a resource_link."""

def anim_patch(session: str, name: str, ops: list[dict]) -> dict:
    """JSON-Patch-style edits to the IR: [{op: add|replace|remove, path, value}].
    The normal way an agent iterates — never resend the whole document.
    Returns {applied: int, errors: [...], ir_version: int}."""

def anim_compile(session: str, name: str, stage: str | None = None) -> dict:
    """Compile IR -> Blender action. Idempotent; replaces the previous compile.
    stage overrides the IR's stage for a one-off preview.
    Returns {action, stage, channels: int, keys: int, duration_s: float,
             checkpoint, warnings: [...]}."""

def stage_advance(session: str, name: str, to: str) -> dict:
    """Move the IR to the next stage (block|breakdown|spline|polish|offset),
    checkpointing first and applying the stage's interpolation/easing rules.
    Refuses a skip of more than one stage unless force. Returns {stage, checkpoint}."""

# ──────────────────────────────── keyframe ops ───────────────────────────────

def key_insert(session: str, targets: list[str], frame: int,
               channels: list[str] | None = None,
               key_type: str = "KEYFRAME", interpolation: str | None = None) -> dict:
    """Key the current pose on `targets` at `frame`. Returns {keys: int, checkpoint}."""

def key_delete(session: str, targets: list[str], frames: list[int] | list[list[int]],
               channels: list[str] | None = None) -> dict:
    """Delete keys at frames or [start,end] ranges. Returns {keys_removed: int, checkpoint}."""

def key_move(session: str, targets: list[str], frame_map: dict[int, int],
             channels: list[str] | None = None) -> dict:
    """Retime specific keys, carrying handles. Returns {keys_moved: int, checkpoint}."""

def key_list(session: str, targets: list[str], channels: list[str] | None = None) -> dict:
    """Returns {channels: [{path, index, keys: [frame,...], types: [...]}]} — frames and
    key types only, no values. The cheap way for an agent to see structure."""

def key_copy(session: str, targets: list[str], src_frame: int, dst_frames: list[int],
             mirror: bool = False, offset: list[float] | None = None) -> dict:
    """Duplicate a pose's keys to other frames, optionally mirrored.
    This is how a cycle's second half gets authored. Returns {keys: int, checkpoint}."""

# ───────────────────────────────── curve ops ─────────────────────────────────

def curve_ease(session: str, targets: list[str], frames: list[int] | None = None,
               preset: str | None = None,
               out_frac: float | None = None, in_frac: float | None = None,
               flat_out: bool = True, flat_in: bool = True,
               channels: list[str] | None = None) -> dict:
    """Set segment easing by preset (smooth|snap|float|ease_in|ease_out|
    overshoot_ready|linear|stepped|hold) or by raw handle fractions (§5.5).
    Returns {segments: int, preset, out_frac, in_frac, checkpoint}."""

def curve_offset(session: str, chains: list[str] = (), bones: list[str] = (),
                 frames: float = 0.0, per_bone: float = 0.0,
                 mode: str = "shift", channels: list[str] | None = None) -> dict:
    """The overlap pass (§5.7). mode ∈ {shift, propagate}.
    Returns {bones_offset: int, total_shift_frames: {bone: float}, checkpoint}."""

def curve_retime(session: str, targets: list[str],
                 warp: list[list[float]] | None = None,
                 scale: float | None = None, pivot: int = 1) -> dict:
    """Time-warp via a monotonic [[f_in,f_out],...] map, or uniform `scale` about `pivot`.
    Handle x-extents scale by the local derivative. Returns {keys_moved, new_range, checkpoint}."""

def curve_noise(session: str, targets: list[str], strength_deg: float = 0.35,
                scale: float = 11.0, depth: int = 0, blend: str = "ADD",
                channels: list[str] = ("rotation",), phase: str = "per_channel",
                frame_range: list[int] | None = None) -> dict:
    """Add a NOISE F-modifier per channel with decorrelated phases (§6.5).
    strength_deg is converted to radians for rotation channels.
    Clamps to the §6.5 sane ranges and reports if it clamped.
    Returns {modifiers: int, clamped: bool, effective_strength_deg, checkpoint}."""

def curve_cycle(session: str, targets: list[str], mode: str = "REPEAT",
                before: str | None = None, count: int = 0) -> dict:
    """Add/update a CYCLES F-modifier. mode ∈ {REPEAT, REPEAT_OFFSET, MIRROR, NONE}.
    Returns {modifiers: int, checkpoint}."""

def curve_stepped(session: str, targets: list[str], frame_step: float = 2.0,
                  frame_offset: float = 0.0, enabled: bool = True) -> dict:
    """Non-destructive blocking preview via a STEPPED F-modifier (§5.1).
    enabled=False mutes it. Returns {modifiers: int, muted: bool}."""

def curve_smooth(session: str, targets: list[str], method: str = "butterworth",
                 cutoff_hz: float = 4.0, order: int = 4,
                 sigma: float = 0.33, width: int = 6, blend: float = 1.0,
                 frames: list[int] | None = None) -> dict:
    """method ∈ {butterworth, gaussian, moving_mean}. For baked/mocap/sim data only.
    Returns {channels: int, method, params, checkpoint}."""

def curve_decimate(session: str, targets: list[str] | None = None,
                   mode: str = "ERROR", rotation_error: float = 0.002,
                   location_error: float = 0.0015, scale_error: float = 0.003,
                   ratio: float = 0.333) -> dict:
    """Per-channel-type decimation via graph.decimate (§3.7).
    Returns {keys_before: int, keys_after: int, ratio: float,
             max_error_by_type: {...}, checkpoint}."""

def curve_exaggerate(session: str, targets: list[str], amount: float = 1.2,
                     frames: list[int] | None = None) -> dict:
    """graph.push_pull. amount>1 pushes extremes out, <1 pulls in.
    Returns {channels: int, amplitude_before, amplitude_after, checkpoint}."""

def curve_match_slope(session: str, targets: list[str], frames: list[int],
                      factor: float = 1.0) -> dict:
    """Take handle slope from neighbouring keys — the arc-kink fix (§5.6).
    Returns {keys: int, checkpoint}."""

def curve_bake(session: str, targets: list[str], frame_range: list[int] | None = None,
               visual: bool = True, step: int = 1,
               then_decimate: bool = True) -> dict:
    """bake_action with visual keying, then sign-continuity, euler filter, and
    optional decimation (§3.7). Returns {channels, keys_before, keys_after,
    duration_s, checkpoint}."""

def curve_graph(session: str, targets: list[str], channels: list[str] | None = None,
                frame_range: list[int] | None = None,
                show: list[str] = ("value", "velocity")) -> dict:
    """Render a matplotlib plot of the curves with key markers.
    Returns an image block (800x500, ~530 tokens) plus {channels: [...]}."""

# ──────────────────────────── procedural generators ──────────────────────────

def gen_walk(session: str, cycle_frames: int = 24, stride_m: float | None = None,
             duty_factor: float = 0.62, double_support: float = 0.12,
             pelvis_yaw_deg: float = 5.0, pelvis_roll_deg: float = 4.0,
             bob_m: float | None = None, shoulder_yaw_deg: float = 8.0,
             arm_swing_deg: float = 22.0, foot_lift_m: float = 0.06,
             style: str = "natural", in_place: bool = True,
             terrain: str | None = None) -> dict:
    """Footstep-driven biped cycle (§6.1). style ∈ {natural, brisk, stroll, sneak,
    tired, run}; style presets the whole parameter block, explicit args override.
    stride_m and bob_m default from character height.
    terrain: name of a collision object to raycast against.
    Returns {action, ir, cycle_frames, stride_m, speed_mps, contacts: {...},
             metrics: {foot_slide_mm, loop_seam_deg, stride_consistency_pct},
             checkpoint}."""

def gen_quadruped(session: str, gait: str = "walk", cycle_frames: int = 24,
                  stride_m: float | None = None, duty_factor: float | None = None,
                  lead: str = "right", spine_flex_deg: float = 8.0,
                  in_place: bool = True) -> dict:
    """gait ∈ {walk, amble, pace, trot, canter, transverse_gallop, rotary_gallop, bound}
    with the §6.2 phase table. duty_factor defaults per gait and is validated against
    the >0.5 walking constraint. Docstring states that canter/gallop phases are
    tuned starting points, not measured ground truth.
    Returns like gen_walk plus {phases: {leg: float}, gait}."""

def gen_idle(session: str, duration_frames: int = 96,
             parts: list[str] = ("breath", "weight_shift", "micro_sway"),
             intensity: float = 1.0) -> dict:
    """Layered idle (§6.5). intensity scales within the sane ranges and cannot exceed
    them. Returns {action, layers: [...], amplitudes_deg: {...}, checkpoint}."""

def gen_wave(session: str, chain: str, period_frames: int = 60,
             amplitude_deg: float = 4.0, phase_per_bone: float = 0.35,
             envelope: str = "rising", axis: str = "Z") -> dict:
    """Travelling wave down a chain (§6.3) — tails, tentacles, fins, hems.
    envelope ∈ {rising, flat, falling, bell}. Returns {bones: int, keys: int, checkpoint}."""

def gen_spring(session: str, targets: list[str], stiffness: float = 20.0,
               damping: float = 0.5, mass: float = 1.0, amount: float = 1.0,
               chain_falloff: float = 0.8,
               frame_range: list[int] | None = None) -> dict:
    """Damped-spring follow pass on existing motion (§6.4). Operates on a copy.
    Returns {bones: int, overshoot_pct, settle_frames, action, checkpoint}."""

def gen_settle(session: str, targets: list[str], at: str = "each_extreme",
               frames: list[int] | None = None, overshoot: float = 0.12,
               settle_frames: int = 4, bounces: int = 1) -> dict:
    """Add overshoot + settle after arrivals (§5.2). Returns {events: int, checkpoint}."""

def gen_moving_hold(session: str, ranges: list[list[int]], drift_deg: float = 1.2,
                    targets: list[str] | None = None) -> dict:
    """Convert dead holds into moving holds (§5.2). Stamps MOVING_HOLD key type.
    Returns {holds: int, bones_drifted: int, checkpoint}."""

def gen_anticipation(session: str, targets: list[str], action_frame: int,
                     frames: int | None = None, amount: float = 0.22) -> dict:
    """Insert a pre-pose opposite the main action (§5.2). frames defaults from the
    action's duration. Returns {anticipation_frame, amplitude_deg, checkpoint}."""

def gen_squash(session: str, target: str, frame: int, factor: float = 0.85,
               axis: str = "Y", spread_frames: int = 1) -> dict:
    """Volume-preserving squash/stretch (§5.3). Returns {scale: [x,y,z],
    volume_error: float, frames_keyed: [int], checkpoint}."""

# ───────────────────────── mocap, retargeting, ML ────────────────────────────

def mocap_import(session: str, path: str, fmt: str = "auto",
                 source_fps: float | None = None, scale: float | None = None,
                 provenance: dict | None = None) -> dict:
    """Import BVH/FBX/glTF/SMPL-npz onto a source skeleton. Does not retarget.
    Returns {source_rig, bones: int, frames: int, source_fps, hip_height_m,
             detected_naming: str, provenance, warnings: [...]}."""

def mocap_search(session: str, query: str = "", library: str = "cmu",
                 duration_s: list[float] | None = None, limit: int = 20,
                 commercial_only: bool = False) -> dict:
    """Search a local clip library by tags/description and optionally by motion-matching
    feature similarity to the current motion. Returns {clips: [{id, name, duration_s,
    fps, licence, commercial_ok, tags}], total}."""

def retarget(session: str, source_rig: str, bone_map: dict[str, str] | None = None,
             reference_pose: str = "tpose", height_normalise: bool = True,
             distribute_spine: bool = True, synthesise_twist: bool = True,
             footlock: bool = True, resample_to_fps: int = 24,
             then_decimate: bool = True) -> dict:
    """Retarget a source action onto the session rig (§7.3). bone_map=None runs the
    naming heuristic and reports what it matched.
    Returns {action, mapped: int, unmapped_source: [str], unmapped_target: [str],
             height_scale: float, metrics: {foot_slide_mm, quat_violations_fixed},
             provenance, checkpoint}."""

def footlock(session: str, feet: list[str] | None = None,
             frame_range: list[int] | None = None,
             height_thresh_m: float = 0.03, speed_thresh_m: float = 0.012,
             pelvis_compensate: bool = True) -> dict:
    """Detect contacts and lock them, re-solving IK with pelvis compensation (§7.4).
    Returns {contacts: {foot: [[start,end],...]}, slide_before_mm, slide_after_mm,
             pelvis_adjust_max_m, checkpoint}."""

def motion_blend(session: str, action_a: str, action_b: str,
                 transition_frames: int = 8, align_phase: bool = True,
                 at_frame: int | None = None) -> dict:
    """Blend two clips with a C2 smootherstep weight, phase-aligned on contacts (§7.5).
    Returns {action, transition: [start,end], seam_metrics: {...}, checkpoint}."""

def ml_inbetween(session: str, keyframes: list[int], model: str = "condmdi",
                 bones: list[str] | None = None, temperature: float = 1.0) -> dict:
    """[v2] Fill gaps between authored key poses with a keyframe-conditioned model (§8.2).
    Requires a local checkpoint; returns a clear error naming the install step if absent.
    Output lands as a new action with provenance marked from the checkpoint's training data.
    Returns {action, frames_generated: int, model, provenance, metrics: {...}, checkpoint}."""

# ──────────────────────── layers, NLA, checkpoints ──────────────────────────

def layer_add(session: str, name: str, blend: str = "combine",
              influence: float = 1.0, action: str | None = None) -> dict:
    """Push an action onto an NLA track (§3.8). blend ∈ {replace, combine, add,
    subtract, multiply}. Returns {track, strip, blend, influence, checkpoint}."""

def layer_set(session: str, name: str, influence: float | None = None,
              mute: bool | None = None, blend: str | None = None,
              frame_start: int | None = None) -> dict:
    """Adjust a layer. Returns the layer's resolved state."""

def layer_list(session: str) -> dict:
    """Returns {layers: [{name, blend, influence, mute, range, action}], active_action}."""

def layer_flatten(session: str, name: str = "flattened") -> dict:
    """Bake the NLA stack to a single action (visual keying), then decimate.
    Returns {action, channels, keys, checkpoint}."""

def checkpoint(session: str, label: str = "") -> dict:
    """Snapshot the session (actions + IR). Returns {checkpoint, label, size_kb}."""

def checkpoint_restore(session: str, checkpoint: str) -> dict:
    """Restore. Returns {checkpoint, restored_from, actions: int}."""

def checkpoint_list(session: str, limit: int = 20) -> dict:
    """Returns {checkpoints: [{id, label, stage, score, created}]} — newest first.
    `score` is the critique score at that point, so an agent can go back to its best."""

# ────────────────────────── critique & visual feedback ──────────────────────

def critique(session: str, action: str | None = None, stage: str | None = None,
             metrics: list[str] | None = None, include_sheet: bool = True,
             verbosity: str = "normal") -> dict:
    """Run the §9.4 metric suite and return the §9.5 report.
    verbosity ∈ {terse, normal, full}; terse is fails only (~80 tokens).
    include_sheet attaches a contact sheet image block.
    Returns structuredContent {score, by_stage, fails: [...], warns: [...],
    passes: {...}, next: [str]} plus the text report and optional image."""

def metrics(session: str, which: list[str], action: str | None = None,
            frame_range: list[int] | None = None, per_frame: bool = False) -> dict:
    """Raw numbers for specific metrics, no scoring, no prose. per_frame=True returns
    arrays (use sparingly — that is where tokens go).
    Returns {metric: value | {bone: value} | [per-frame...]}."""

def playblast(session: str, frame_range: list[int] | None = None,
              resolution: list[int] = (480, 270), camera: str | None = None,
              shading: str = "studio", fmt: str = "mp4",
              fps: int | None = None) -> dict:
    """Workbench render -> ffmpeg. shading ∈ {studio, matcap, flat, silhouette}.
    Returns {resource_link, frames: int, duration_s, render_s} — no inline video."""

def contact_sheet(session: str, frame_range: list[int] | None = None,
                  every_nth: int = 1, grid: list[int] | None = None,
                  cell_width: int = 240, shading: str = "studio",
                  label_frames: bool = True, max_tokens: int = 2500) -> dict:
    """The default visual (§9.3). Auto-chooses the grid and cell size to stay under
    max_tokens. Returns an image block plus {frames: [int], grid, dimensions}."""

def render_pose(session: str, frame: int | None = None,
                camera: str = "three_quarter", resolution: list[int] = (384, 384),
                shading: str = "studio", overlay: str | None = None) -> dict:
    """Single-frame render. overlay ∈ {None, silhouette, line_of_action, support_polygon}.
    Returns an image block plus {frame, camera}."""

def render_trail(session: str, targets: list[str],
                 frame_range: list[int] | None = None, at_frame: int | None = None,
                 resolution: list[int] = (960, 540), dots_on_keys: bool = True) -> dict:
    """Motion trail / arc overlay rendered over a frame (§9.3).
    Returns an image block plus {arc_metrics: {straightness, flips, jerk_peak}}."""

def render_compare(session: str, checkpoints: list[str],
                   frame_range: list[int] | None = None,
                   resolution: list[int] = (1280, 480)) -> dict:
    """Side-by-side contact sheets of two checkpoints, labelled.
    Returns an image block plus {scores: {checkpoint: score}}."""

# ──────────────────────────── export & validation ───────────────────────────

def clip_split(session: str, clips: list[dict]) -> dict:
    """clips: [{name, start, end, loop: bool}]. Creates one action per clip with
    explicit boundary keys (§10.3). Returns {clips: [{name, action, frames}], checkpoint}."""

def root_motion(session: str, mode: str = "extract") -> dict:
    """mode ∈ {extract, apply, none}. extract: strip horizontal travel to metadata;
    apply: integrate declared velocity onto the root. Re-runs foot_slide afterwards.
    Returns {mode, velocity_mps: [x,y,z], angular_dps, foot_slide_after_mm, checkpoint}."""

def validate_export(session: str, actions: list[str] | None = None,
                    commercial: bool = False) -> dict:
    """The §10.6 validator suite, pre-flight. Returns {ok: bool,
    failures: [{check, detail}], warnings: [...], provenance_blocked_by: [str]}."""

def export(session: str, path: str, fmt: str = "glb",
           actions: list[str] | None = None, naming: str | None = None,
           target_fps: int | None = None, commercial: bool = False,
           force: bool = False, readback_check: bool = True) -> dict:
    """fmt ∈ {glb, gltf, fbx, blend}. naming ∈ {None, ue5, unity, mixamo} renames
    bones on the way out. Runs validate_export first and refuses on failure unless
    force=True. readback_check re-imports and compares joint positions (§10.6 #11).
    Returns {resource_link, fmt, animations: int, channels: int, keys: int,
             file_kb, sidecar: resource_link, readback_max_error_mm, warnings: [...]}."""
```

That is **56 tools**, which is too many for one server's tool list to be comfortable in a context window. Group them behind five namespaced facades with a `detail` or `op` discriminator if the host's tool budget is tight — `forge_pose`, `forge_curve`, `forge_gen`, `forge_review`, `forge_io` — but keep the signatures above as the internal API, because a flat, explicitly-typed function is what makes the server testable.

---

## 12. Install, from zero

```bash
# ── 1. system ───────────────────────────────────────────────────────────────
apt-get update && apt-get install -y --no-install-recommends ffmpeg
# (ffmpeg 6.1.1 already present in this container  [verified])

# ── 2. python env — 3.13 is mandatory, bpy has no wheel for anything else ──
python3 --version                      # must report 3.13.x   [verified: 3.13.18]
python3 -m venv /opt/forge/venv
. /opt/forge/venv/bin/activate
pip install --upgrade pip

# ── 3. blender as a library ─────────────────────────────────────────────────
pip install bpy==5.3.0                 # 236 MB wheel, 1.1 GB installed, ~11 s  [verified]

# ── 4. server deps ──────────────────────────────────────────────────────────
pip install "mcp>=1.2" numpy==2.5.4 scipy pillow pydantic matplotlib

# ── 5. verify the three things that actually matter ─────────────────────────
python -I - <<'EOF'
import bpy, time
from bpy_extras import anim_utils
print("blender", bpy.app.version_string, "background", bpy.app.background)
bpy.ops.wm.read_factory_settings(use_empty=True)

# (a) slotted action API
ob = bpy.data.objects.new("t", None); bpy.context.scene.collection.objects.link(ob)
bpy.context.view_layer.objects.active = ob; ob.select_set(True)   # graph ops poll on selection
for f, v in [(1,0.0),(4,1.0),(7,0.3),(10,0.9)]:
    ob.location = (v,0,0); ob.keyframe_insert('location', frame=f)
ad = ob.animation_data
cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
assert cb and len(cb.fcurves) == 3, "slotted action API broken"
assert not hasattr(ad.action, 'fcurves'), "unexpected legacy API present"

# (b) graph editor ops headless  -- the load-bearing finding
win = bpy.context.window_manager.windows[0]
area = win.screen.areas[0]; area.type = 'GRAPH_EDITOR'
region = next(r for r in area.regions if r.type == 'WINDOW')
fc = cb.fcurves[0]
for k in fc.keyframe_points: k.select_control_point = True
with bpy.context.temp_override(window=win, screen=win.screen, area=area,
                               region=region, space_data=area.spaces.active):
    assert bpy.ops.graph.decimate(mode='RATIO', factor=0.5) == {'FINISHED'}

# (c) workbench render speed
bpy.ops.mesh.primitive_monkey_add()
bpy.ops.object.camera_add(location=(0,-5,1.5), rotation=(1.3,0,0))
sc = bpy.context.scene; sc.camera = bpy.context.object
sc.render.engine = 'BLENDER_WORKBENCH'
sc.render.resolution_x, sc.render.resolution_y = 480, 270
sc.render.filepath = '/tmp/forge_check_'
t = time.time(); bpy.ops.render.render(write_still=True)
print("workbench 480x270: %.3f s/frame" % (time.time()-t))
import gpu; print("gpu:", gpu.platform.backend_type_get(), gpu.platform.renderer_get())
print("OK")
EOF

# ── 6. optional: commercially-usable mocap library ──────────────────────────
#   CMU Graphics Lab Motion Capture Database — "free for all uses"; may be included
#   in commercially-sold products but not resold directly, even converted.
#   BVH conversions are mirrored by cgspeed; ~1.5 GB.
mkdir -p /opt/forge/data/cmu   # download into its own empty dir, treat as untrusted

# ── 7. optional [v2]: ML in-betweening, GPU strongly preferred ──────────────
# pip install torch --index-url https://download.pytorch.org/whl/cpu   # ~2.6 GB
# git clone https://github.com/setarehc/diffusion-motion-inbetweening  # MIT code
#   NOTE: the published checkpoints are trained on HumanML3D -> AMASS -> NON-COMMERCIAL.
#   Train on CMU for commercially-clean weights (see §8.2, SILK's finding).
```

Expected output of step 5 in this container **[verified — the script above was extracted from this document and run; all three checks pass]**:

```
blender 5.3.0 background True
workbench 480x270: 1.423 s/frame
gpu: VULKAN llvmpipe (LLVM 20.1.2, 256 bits)
OK
```

The 1.4 s is a **cold** first frame — llvmpipe compiles shaders on the first render. Steady state is 0.112 s/frame (§2.3), so forge should render one throwaway frame at session start and not report it in any timing.

Disk after steps 1–5: **~1.3 GB**. With CMU: ~2.8 GB. With torch + a checkpoint: ~5.5 GB.

---

## 13. Phased build plan

### v0 — usable (the smallest thing that beats hand-written bpy scripts)

Scope: **the IR, the compiler, the critique, and one picture.** No generators, no mocap, no ML.

| | deliverable |
|---|---|
| 1 | `rig_load` + manifest parsing + the §4 contract validator. Fail loudly on a bad rig |
| 2 | The accessor shim (§3.1) and `quat_sign_continuity` (§3.5). Everything depends on these |
| 3 | IR v1.0 schema + `anim_new`/`anim_patch`/`anim_get` + `anim_compile` for stages `block` and `spline` |
| 4 | Pose library: `pose_set/get/save/load/mirror`, partial poses, mirroring from the manifest |
| 5 | `curve_ease` with the §5.5 preset table; `key_insert/delete/move/list/copy` |
| 6 | `curve_offset` (shift + propagate) — the overlap pass, because it is the biggest visual win for the least code |
| 7 | `curve_decimate` via the `graph_context` override |
| 8 | Metrics 1–7, 11, 17, 20 (continuity, foot slide, loop seam, pops, support polygon, holds, density) + the critique report |
| 9 | `contact_sheet` and `playblast` |
| 10 | `checkpoint` / `checkpoint_restore` / `checkpoint_list` |
| 11 | `export` glb + fbx + `validate_export` + the readback check |
| 12 | Provenance fields plumbed from day one, even with only procedural sources — retrofitting them later is miserable |

v0 is done when Claude can author a 24-frame walk as IR from the §5.2 pose table, compile it, read a critique, fix two flagged problems, and export a .glb that loads in Godot with the feet not sliding.

### v1 — the generators and the craft

| | deliverable |
|---|---|
| 1 | `gen_walk` with the full §6.1 parameter set, styles, and terrain raycast |
| 2 | `gen_quadruped` with the §6.2 phase table |
| 3 | `gen_idle`, `gen_wave`, `gen_spring`, `gen_settle`, `gen_moving_hold`, `gen_anticipation`, `gen_squash` |
| 4 | Stages `breakdown`, `polish`, `offset` + `stage_advance` enforcement |
| 5 | Remaining metrics: arcs (8–10), gravity (12), stride (13), counter-rotation (14), overlap lag (15), silhouette (16), volume (18), interpenetration (19), gimbal (22), limits (23), IK reach (24) |
| 6 | `render_trail`, `render_pose` with overlays, `render_compare`, `curve_graph` |
| 7 | `mocap_import` (BVH first) + the retargeter (§7.3) + `footlock` |
| 8 | `mocap_search` over a local CMU library with motion-matching features |
| 9 | NLA layers: `layer_add/set/list/flatten` with `COMBINE` |
| 10 | `curve_retime`, `curve_noise`, `curve_cycle`, `curve_stepped`, `curve_smooth`, `curve_exaggerate`, `curve_match_slope`, `curve_bake` |
| 11 | `clip_split`, `root_motion`, UE5/Unity naming maps |
| 12 | `motion_blend` with phase alignment |

v1 is done when a one-sentence request ("a tired 1.9 m character's walk cycle, 32 frames, slight limp on the left") produces a clip that passes its own critique at `polish` without human intervention.

### v2 — ML, physics, and the long tail

| | deliverable |
|---|---|
| 1 | `ml_inbetween` behind an optional checkpoint, with provenance tainting |
| 2 | **Train a compact in-betweener on CMU only** (per SILK: a single transformer encoder, good data, velocity features) for commercially-clean weights. This is the genuinely novel piece and nothing published offers it |
| 3 | Bullet ragdoll with keyed→sim handoff and influence cross-fade; cloth bake for capes at low quality steps |
| 4 | Facial: viseme library, phoneme-track lip sync, blink generator with the §5.2 presets |
| 5 | Optional `mocap_import_video` via a GVHMR install, GPU-gated, licence-flagged |
| 6 | SMPL/SMPL-X importer with spine distribution and twist synthesis (§8.5) |
| 7 | Alembic deformation-cache export for offline rendering |
| 8 | Audio-reactive secondary via `graph.sound_to_samples` |
| 9 | A clip library with motion-matching-feature search over everything forge has produced |

---

## 14. Open questions, honestly

1. **Is Blender 5.2 actually the LTS?** The release-directory patch counts and the historical 3-minor cadence both say yes, but blender.org is behind Cloudflare and refused every automated fetch from this container, so I could not read the LTS badge. If forge needs a long support window, confirm by hand before pinning. 5.3.0 is definitely current stable.
2. **The canter and gallop phase numbers.** The `cseweb.ucsd.edu` lecture PDF failed TLS chain validation from this container and then 503'd through the fetch tool, so those rows in §6.2 come from a search engine's extract of it, re-derived into LH/LF/RH/RF ordering by me. Walk/trot/pace are solid. Canter and gallop need one afternoon against reference footage, and the lead-leg convention in particular needs pinning down.
3. **Williams' numbers are secondhand.** The walk-cycle and head-turn tables are the Monmouth University course's transcription of *The Animator's Survival Kit*, and that page's own arithmetic is inconsistent in one column (I recomputed it). Worth checking the 2001 Faber edition directly. The head-turn chart in particular is load-bearing for forge's timing vocabulary.
4. **`arc_straightness`'s 0.06 lower bound is a judgement, not a measurement.** Every other threshold in §9.4 derives from physics, from the decimation tolerance, or from craft literature. This one I picked. It needs calibrating against a corpus of real clips, and it is the metric most likely to produce false positives in v1.
5. **Does the `graph_context` override survive future Blender releases?** It depends on the `bpy` module loading the factory startup file with a window and 11 screens — which is an implementation detail, not a documented API. It works on 5.3.0 **[verified]**. If a future release drops the startup window in module mode, forge loses decimate/ease/breakdown/butterworth and has to reimplement them. Mitigation: pin the version, keep the RDP fallback, and put this in the test suite as a canary.
6. **Can the in-betweener actually be trained on CMU alone?** SILK's finding says data quantity and representation matter more than architecture, and CMU is ~2600 clips against LAFAN1's ~4.5 hours — plausibly comparable. But SILK trained on LAFAN1, nobody has published CMU-only numbers, and CMU's capture quality is 2003-era with marker noise and no consistent skeleton. This is the most interesting open question in the doc and the one I would test first in v2.
7. **Mixamo's licence for derivative work.** Adobe's FAQ reads as royalty-free for commercial use, but the live help page was erroring and every guide cites an archived copy; the ML-dataset question has an unanswered Adobe forum thread; and multiple vendors report Adobe repeatedly floating a sunset. I would not build a dependency on it, and I did not verify it firsthand.
8. **Whether Claude can hold the IR in working memory at production scale.** A 24-frame walk's IR is ~2 KB and fine. A 300-frame acting shot with 40 keys, 6 layers and per-channel overrides is 30 KB+, which is a lot to patch reliably. `anim_patch` mitigates it, but the real answer may be that forge needs *sectioned* IR — one document per layer, composed at compile time. Worth designing for in v0 even if not implemented.
9. **How much the silhouette metric is worth.** It requires a render per frame (0.054 s at 320×180, so 24 frames = 1.3 s — affordable) and it is the only metric that measures pose *readability* rather than continuity. But the normalised-area-delta threshold of 3% is a guess, and a pose can change silhouette area without becoming more readable. It may need to be area *plus* a shape descriptor (Hu moments on the silhouette) to be trustworthy.
10. **Interpenetration cost.** Capsule-capsule over a 60-bone rig is 1770 pairs per frame, which is fine; mesh-level self-intersection is not. forge should ship the capsule version and say plainly that it will miss cloth-through-arm.
11. **The 56-tool surface.** That is a lot of tool definitions in a context window, and I do not know where this host's practical ceiling is. The namespaced-facade fallback in §11 is the mitigation, but facades hurt discoverability and make the schemas vaguer, which is exactly what an agent does not need. Someone should measure before committing.
