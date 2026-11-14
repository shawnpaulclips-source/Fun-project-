# forge — Rigging Subsystem Design
**Researcher: rigging. Date: 2026-11-14. Status: design, verified against a live Blender 5.3 bpy module on this container.**

---

## VERDICT (read this, skip the rest if you're busy)

1. **`pip install bpy` gives you Blender 5.3.0 on Python 3.13 in 9 seconds, 927 MB, no GPU, no X server.** Verified on this container. No apt, no system Blender, no `--background` subprocess. `import bpy` and you have the whole thing in-process.
2. **Rigify ships inside that wheel and generates headless.** Verified: human metarig (159 bones) → `bpy.ops.pose.rigify_generate()` → 706-bone rig with 533 constraints, 114 drivers, 220 custom shapes, in **6.5 s on 4 CPU cores**. That is a professional rig, for free, today.
3. **The practical auto-rig path is landmark-driven metarig fitting + Rigify, not a learned model.** Learned auto-riggers (UniRig, MagicArticulate, Anymate) are real and good but want an 8 GB CUDA GPU; on CPU they greedy-decode at 1–several minutes/mesh and you still have to fix the skeleton by hand. Keep them as an optional backend, not the spine of the system.
4. **Skinning: Blender's `ARMATURE_AUTO` (bone heat) is the default and it's fine.** 11,088 verts / 2 bones in **0.62 s**, partition-of-unity 0.979–1.000, max 2 influences, zero orphan verts. Verified.
5. **Bounded Biharmonic Weights is available and fast, but only on a tet mesh.** `igl.bbw` exists in the `libigl` PyPI wheel. On the raw surface it throws `RuntimeError: boundary_conditions: failed to compute`. Tetrahedralize first, insert Steiner samples along each bone segment, then it solves 2,742 tets in **0.08 s** with exact partition-of-unity and zero negatives. Verified end to end.
6. **Tetrahedralizer: `pip install tetgen --no-deps` — 908 KB.** The default install drags pyvista+VTK (565 MB). `--no-deps` works; verified. fTetWild (`wildmeshing`, 23 MB) is the fallback but has a dict-options API that needs a wrapper.
7. **Claude can see a rig.** Workbench engine, `light='FLAT'`, `color_type='VERTEX'`, weights baked to a point-domain color attribute → a 400×400 weight heatmap PNG in **0.87 s on CPU**. This is the highest-value tool in the whole rigging surface and it costs nothing.
8. **Self-intersection is a usable blind metric.** `BVHTree.overlap(self)` on the posed mesh: 0 pairs at rest → 20 at 45° → 193 at 90° → 297 at 135° → 474 at 170°. Monotone, cheap (0.06 s / 3,840 verts), and it catches candy-wrapper and joint collapse without a human looking.
9. **Declarative rig IR beats incremental tool calls for the skeleton, loses for weight repair.** Ship both: `rig_compile(ir)` for the one-shot structural pass, then incremental `weight_*` ops driven by heatmap renders. Argued in §12.
10. **Biggest risk is not the rigging — it's geometry.** Bone heat fails on non-manifold meshes, meshes with interior geometry, and limbs that touch at rest. The contract with the geometry researcher (§13) is load-bearing; without it half the rigs will be garbage and the validator will only tell us *that*, not *why*.

---

## 1. ENVIRONMENT REALITY

Measured on this container: Linux 6.18, Python 3.13.18, 4 cores, 15 GB RAM, no GPU, no `nvidia-smi`, 24 GB disk free after everything below.

### 1.1 What's needed

```bash
python3 -m venv /opt/forge/venv
/opt/forge/venv/bin/pip install bpy==5.3.0          # Blender 5.3 as an importable module
/opt/forge/venv/bin/pip install libigl numpy scipy  # BBW, geodesics, winding numbers
/opt/forge/venv/bin/pip install tetgen --no-deps    # tetrahedralizer, 908 KB without VTK
/opt/forge/venv/bin/pip install trimesh             # mesh IO + convex hull + PCA helpers
/opt/forge/venv/bin/pip install skeletor            # curve skeletons for non-humanoids
```

### 1.2 Footprint, measured

| component | installed size | notes |
|---|---|---|
| `bpy` 5.3.0 | **927 MB** | includes Rigify, glTF/FBX IO, Cycles, EEVEE, Workbench |
| `libigl` | 16 MB | nanobind wheel, 302 exposed functions |
| `numpy` + libs | 70 MB | |
| `scipy` + libs | 139 MB | only needed for sparse solves in custom weight code |
| `tetgen --no-deps` | **0.9 MB** | |
| `trimesh` | ~5 MB | |
| **total** | **≈ 1.16 GB** | one-time; install wall time under 60 s |

Avoid: `pip install tetgen` without `--no-deps` → pulls pyvista + `vtkmodules` (565 MB) for nothing. Avoid `wildmeshing` unless tetgen fails; it is 23 MB but its `tetwild(options: dict, meshes: dict)` pybind signature needs a wrapper written and tested.

### 1.3 `bpy` module vs `blender --background`

Use the module. Reasons:
- No subprocess, no serializing scene state across a process boundary, no `--python-expr` quoting hell.
- Exceptions come back as Python exceptions with tracebacks.
- The MCP server holds the scene in memory across tool calls, which is the entire point — Claude does `skeleton_from_template`, then `bind_mesh`, then `rig_validate`, and each one operates on live `bpy.data`.

Caveats that bit me and will bite the implementation:
- `bpy.ops.wm.read_factory_settings(use_empty=True)` is the correct reset. A fresh `bpy` import has a default scene with cube/camera/light in some configurations.
- Operators need context. In-process, `bpy.context.view_layer.objects.active` and `select_set(True)` must be set before `parent_set`, `mode_set`, etc. There is no window, so anything requiring `bpy.context.area` fails; none of the rigging ops do.
- Rigify must be explicitly enabled: `addon_utils.enable("rigify", default_set=True)`. The extension-style id `bl_ext.blender_org.rigify` does **not** exist in the wheel — it's the legacy `addons_core` path. Verified.
- `bpy.ops.export_scene.gltf` requires `addon_utils.enable("io_scene_gltf2")` first.

### 1.4 What degrades on CPU

| operation | CPU behaviour (measured or sourced) |
|---|---|
| bone heat (`ARMATURE_AUTO`) | 0.62 s / 11 k verts. CPU-only algorithm anyway. **No degradation.** |
| BBW on tets | 0.08 s / 2.7 k tets. Direct sparse solve, CPU-native. **No degradation.** |
| heat geodesics precompute | 0.009 s / 1.8 k verts. **No degradation.** |
| Rigify generate | 6.5 s. Pure Python. **No degradation.** |
| Workbench render 512² | 0.71 s. Workbench is a CPU-capable rasterizer. **No degradation.** |
| EEVEE render | Works (engine id is `BLENDER_EEVEE` in 5.3, **not** `BLENDER_EEVEE_NEXT` — that identifier throws). Needs a GL context; in a headless container it may fall back or fail. **Don't rely on it.** Use Workbench for all validation renders. |
| Cycles render | Available, CPU-only, slow. Only for a final beauty shot, never for validation. |
| UniRig / MagicArticulate / Anymate | **Degrades hard.** UniRig is a 350M-param transformer wanting ≥8 GB VRAM. Greedy decode on CPU is reported at one to several minutes per mesh. Also needs `spconv` built against a CUDA version and `flash-attn`, which the UniRig README itself warns will probably fail to install. **Out of scope for v0/v1.** |

**Conclusion: everything in the recommended rigging path runs at full speed on 4 CPU cores with no GPU.** That is the single best thing about this design.

---

## 2. SKELETON CONSTRUCTION, HEADLESS

### 2.1 Don't use `armature_add`

`bpy.ops.object.armature_add` creates a single "Bone" and requires operator context. Build from the data API instead:

```python
arm_data = bpy.data.armatures.new("forge_arm")
arm_obj  = bpy.data.objects.new("Armature", arm_data)
bpy.context.scene.collection.objects.link(arm_obj)
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.mode_set(mode='EDIT')        # edit_bones only exist in EDIT mode
eb = arm_data.edit_bones
b = eb.new("pelvis"); b.head = (0,0,1.0); b.tail = (0,0,1.1)
c = eb.new("spine_01"); c.head = (0,0,1.1); c.tail = (0,0,1.3)
c.parent = b; c.use_connect = True          # connected: child head follows parent tail
bpy.ops.object.mode_set(mode='OBJECT')      # edit_bones are invalidated here
```

Three bone views, and you must know which one you hold:
- `armature.edit_bones` — EDIT mode only. `head`, `tail`, `roll`, `parent`, `use_connect`, `use_deform`, `align_roll()`. The **only** place you can move a rest pose.
- `armature.bones` — `Bone`, read-mostly rest data, valid in all modes. `matrix_local`, `head_local`, `tail_local`, `color`.
- `object.pose.bones` — `PoseBone`, the animated layer. `constraints`, `custom_shape`, `rotation_euler`, `location`, `matrix`, `color`.

`edit_bones` references go stale the instant you leave EDIT mode. Store names, not objects. This is the #1 source of `ReferenceError: StructRNA of type EditBone has been removed` in headless rigging code.

### 2.2 Bone collections — layers and groups are gone

Verified on 5.3: `hasattr(armature, 'layers')` → **False**. `hasattr(object.pose, 'bone_groups')` → **False**. Both were removed in 4.0 and are not coming back.

```python
deform  = arm_data.collections.new("DEF")
mech    = arm_data.collections.new("MCH")
fingers = arm_data.collections.new("Fingers", parent=deform)   # nestable
deform.assign(arm_data.bones["spine_01"])
deform.unassign(arm_data.bones["spine_01"])
arm_data.collections        # root-level only
arm_data.collections_all    # flattened, includes nested
deform.bones                # direct members
deform.bones_recursive      # members of this + descendants
```

Collection attributes, verified full list: `assign`, `bones`, `bones_recursive`, `child_number`, `children`, `index`, `is_editable`, `is_expanded`, `is_local_override`, `is_solo`, `is_visible`, `is_visible_ancestors`, `is_visible_effectively`, `name`, `parent`, `unassign`.

Collections live on the **armature data**, not the pose, so they're reachable in EDIT mode. That's the whole reason for the change and it's useful: you can assign a bone to DEF while you're still placing it.

Color replaced bone groups. Both `Bone.color` and `PoseBone.color` exist; `PoseBone.color` overrides. `pb.color.palette = 'THEME04'` works, verified. `'DEFAULT'`, `'CUSTOM'`, `THEME01`–`THEME20`.

### 2.3 Custom shapes

```python
pb = arm_obj.pose.bones["hand_ik.L"]
pb.custom_shape = widget_mesh_object
pb.custom_shape_scale_xyz = (1.2, 1.2, 1.2)
pb.custom_shape_translation = (0, 0.1, 0)
pb.custom_shape_rotation_euler = (0, 0, 0)
pb.use_custom_shape_bone_size = True
pb.custom_shape_wire_width = 2.0
```
Widgets are real mesh objects; park them in a hidden collection. Rigify generates 220 of these automatically into a `WGTS_` collection. For hand-built control rigs, generate widgets procedurally (circle, cube, arrow, sphere-of-3-circles) rather than shipping .blend assets — it keeps forge self-contained.

### 2.4 Symmetrize

`bpy.ops.armature.symmetrize(direction='POSITIVE_X'|'NEGATIVE_X', copy_bone_colors=True)`. EDIT mode. It mirrors across local X using the `.L`/`.R` naming to pair bones. It will silently do nothing useful if your names don't follow Blender's suffix convention — which is the next section.

---

## 3. NAMING CONVENTIONS, AND WHY THEY'RE NOT COSMETIC

| convention | side marker | example |
|---|---|---|
| Blender native | `.L` / `.R` suffix (also `_L`, `-L`, `.l`, ` L`, and `Left`/`Right` prefix/suffix) | `upper_arm.L` |
| Unreal Engine | `_l` / `_r` suffix, lowercase | `upperarm_l` |
| Mixamo | `Left`/`Right` **prefix**, PascalCase, `mixamorig:` namespace | `mixamorig:LeftArm` |
| VRM | `left`/`right` **prefix**, camelCase, these are *spec slots* not bone names | `leftUpperArm` |
| SMPL-X | `left_`/`right_` prefix, snake_case | `left_shoulder` |

What breaks when you get it wrong:

- **`armature.symmetrize` and `vertex_group_mirror` both key off Blender's suffix table.** `upperarm_l` does not symmetrize. `upper_arm.L` does. If forge builds a UE-named skeleton directly, it loses mirroring for the rest of the session.
- **Rigify's entire generator** assumes `.L`/`.R`. Its metarigs are named `upper_arm.L`, `f_index.01.L`. You cannot feed it UE names.
- **Blender's FBX exporter does not rename bones.** A rig authored as `upper_arm.L` arrives in Unreal as `upper_arm.L`, which matches nothing in the UE5 skeleton and breaks every retarget asset and every animation blueprint that references bones by name.

**Therefore forge's rule: always author in Blender convention (`.L`/`.R`, snake_case), and rename to the target convention as the *last* step before export.** That's a pure string transform plus vertex-group rename, and it's reversible. The tool is `skeleton_rename_convention` (§11) and it must rename, in lockstep:
1. `armature.bones[*].name`
2. `mesh.vertex_groups[*].name` on every bound mesh
3. driver variable bone targets (`driver.variables[*].targets[*].bone_target`)
4. constraint `subtarget` fields on every pose bone
5. `pb.custom_shape` is by reference, no rename needed

Miss any of 2–4 and you get a rig that looks right and deforms nothing.

Suffix detection, because Claude will need to classify names it didn't author:

```python
_SIDE = [(".L",".R"),("_L","_R"),("-L","-R"),(".l",".r"),("_l","_r"),("-l","-r"),
         (" L"," R"),("Left","Right"),("left","right"),("LEFT","RIGHT")]
```
Check suffixes before prefixes, longest first, and treat `Left`/`Right` as matching anywhere in the string (Mixamo puts it at the front, some rigs at the back).

---

## 4. BONE TABLES

These are verbatim data. Claude needs them exactly, so they're in the doc rather than fetched at runtime.

### 4.1 Mixamo — 65 bones

Every bone carries the `mixamorig:` prefix (some exporters strip the colon to `mixamorig`). Hierarchy is strict; `Hips` is the root.

```
mixamorig:Hips
├── mixamorig:Spine
│   └── mixamorig:Spine1
│       └── mixamorig:Spine2
│           ├── mixamorig:Neck
│           │   └── mixamorig:Head
│           │       └── mixamorig:HeadTop_End
│           ├── mixamorig:LeftShoulder
│           │   └── mixamorig:LeftArm
│           │       └── mixamorig:LeftForeArm
│           │           └── mixamorig:LeftHand
│           │               ├── mixamorig:LeftHandThumb1 → Thumb2 → Thumb3 → Thumb4
│           │               ├── mixamorig:LeftHandIndex1 → Index2 → Index3 → Index4
│           │               ├── mixamorig:LeftHandMiddle1 → Middle2 → Middle3 → Middle4
│           │               ├── mixamorig:LeftHandRing1 → Ring2 → Ring3 → Ring4
│           │               └── mixamorig:LeftHandPinky1 → Pinky2 → Pinky3 → Pinky4
│           └── mixamorig:RightShoulder
│               └── ... (mirror of Left, identical structure)
├── mixamorig:LeftUpLeg
│   └── mixamorig:LeftLeg
│       └── mixamorig:LeftFoot
│           └── mixamorig:LeftToeBase
│               └── mixamorig:LeftToe_End
└── mixamorig:RightUpLeg
    └── ... (mirror)
```

Flat list, 65:

| # | name | # | name |
|---|---|---|---|
| 1 | Hips | 34 | RightHandThumb1 |
| 2 | Spine | 35 | RightHandThumb2 |
| 3 | Spine1 | 36 | RightHandThumb3 |
| 4 | Spine2 | 37 | RightHandThumb4 |
| 5 | Neck | 38 | RightHandIndex1 |
| 6 | Head | 39 | RightHandIndex2 |
| 7 | HeadTop_End | 40 | RightHandIndex3 |
| 8 | LeftShoulder | 41 | RightHandIndex4 |
| 9 | LeftArm | 42 | RightHandMiddle1 |
| 10 | LeftForeArm | 43 | RightHandMiddle2 |
| 11 | LeftHand | 44 | RightHandMiddle3 |
| 12 | LeftHandThumb1 | 45 | RightHandMiddle4 |
| 13 | LeftHandThumb2 | 46 | RightHandRing1 |
| 14 | LeftHandThumb3 | 47 | RightHandRing2 |
| 15 | LeftHandThumb4 | 48 | RightHandRing3 |
| 16 | LeftHandIndex1 | 49 | RightHandRing4 |
| 17 | LeftHandIndex2 | 50 | RightHandPinky1 |
| 18 | LeftHandIndex3 | 51 | RightHandPinky2 |
| 19 | LeftHandIndex4 | 52 | RightHandPinky3 |
| 20 | LeftHandMiddle1 | 53 | RightHandPinky4 |
| 21 | LeftHandMiddle2 | 54 | LeftUpLeg |
| 22 | LeftHandMiddle3 | 55 | LeftLeg |
| 23 | LeftHandMiddle4 | 56 | LeftFoot |
| 24 | LeftHandRing1 | 57 | LeftToeBase |
| 25 | LeftHandRing2 | 58 | LeftToe_End |
| 26 | LeftHandRing3 | 59 | RightUpLeg |
| 27 | LeftHandRing4 | 60 | RightLeg |
| 28 | LeftHandPinky1 | 61 | RightFoot |
| 29 | LeftHandPinky2 | 62 | RightToeBase |
| 30 | LeftHandPinky3 | 63 | RightToe_End |
| 31 | LeftHandPinky4 | 64 | RightShoulder |
| 32 | RightArm | 65 | RightForeArm |
| 33 | RightHand | | |

(Rows 32–33, 64–65 are the right-arm chain, listed out of visual order to fit the two-column table; the hierarchy above is authoritative.)

Mixamo accepts uploads with a roughly biped layout and will retarget onto skeletons with a different spine-joint count or fewer than five fingers, so exact-match is not required for *their* service — but it is required for consuming their animation library.

### 4.2 UE5 Mannequin (SKM_Manny / SK_Mannequin) — 87 named bones

Suffix `_l` / `_r`, all lowercase. Root is `root` at the origin, and `root` must be at world origin with zero rotation or root motion breaks.

**Core (10)**
```
root
└── pelvis
    ├── spine_01 → spine_02 → spine_03 → spine_04 → spine_05
    │   ├── neck_01 → neck_02 → head
    │   ├── clavicle_l → (arm chain)
    │   └── clavicle_r → (arm chain)
    ├── thigh_l → (leg chain)
    └── thigh_r → (leg chain)
```
UE5 has **five** spine bones (`spine_01`..`spine_05`) and **two** neck bones (`neck_01`, `neck_02`). UE4 had three spine and one neck. This is the single most common retarget failure.

**Arm chain, per side (8 × 2 = 16)**
```
clavicle_l
└── upperarm_l
    ├── upperarm_twist_01_l      ← LEAF, child of upperarm_l
    ├── upperarm_twist_02_l      ← LEAF, also child of upperarm_l, NOT of twist_01
    └── lowerarm_l
        ├── lowerarm_twist_01_l  ← LEAF
        ├── lowerarm_twist_02_l  ← LEAF
        └── hand_l
```
**The twist bones are sibling leaves, not a chain.** `upperarm_twist_02_l` is a child of `upperarm_l`, not of `upperarm_twist_01_l`. Exporters that chain them (Character Creator does, with `cc_base_`-prefixed names) produce a skeleton that imports but animates wrong. Same pattern on lowerarm, thigh and calf.

**Hand, per side (19 × 2 = 38)**
```
hand_l
├── thumb_01_l → thumb_02_l → thumb_03_l
├── index_metacarpal_l → index_01_l → index_02_l → index_03_l
├── middle_metacarpal_l → middle_01_l → middle_02_l → middle_03_l
├── ring_metacarpal_l → ring_01_l → ring_02_l → ring_03_l
└── pinky_metacarpal_l → pinky_01_l → pinky_02_l → pinky_03_l
```
Note: thumb has **no** metacarpal; the other four do. UE4 had no metacarpals at all.

**Leg chain, per side (8 × 2 = 16)**
```
thigh_l
├── thigh_twist_01_l    ← LEAF
├── thigh_twist_02_l    ← LEAF
└── calf_l
    ├── calf_twist_01_l ← LEAF
    ├── calf_twist_02_l ← LEAF
    └── foot_l
        └── ball_l
```

**IK bones (7)** — no skin weight, pure animation targets
```
ik_foot_root           ← child of root
├── ik_foot_l
└── ik_foot_r
ik_hand_root           ← child of root
└── ik_hand_gun        ← constrained to hand_r in the AnimBP
    ├── ik_hand_l
    └── ik_hand_r
```
`ik_hand_gun` is a weapon-offset bone; `WeaponSocket` sits on it. These are legacy from UE3/UE4 but every stock AnimBP and retarget asset references them, so **ship them or the asset is not UE5-compatible.**

**Count discrepancy, flagged:** 10 + 16 + 38 + 16 + 7 = **87**. Reallusion's comparison documents state **89** for the UE5 Mannequin. The two extra are most likely `interaction` and `center_of_mass` (virtual/utility bones present on SKM_Manny in UE 5.1+). **I could not verify those two names against an Epic source.** Build 87, and make `skeleton_from_template("ue5")` take an `extras: list[str]` so the two can be added once someone confirms them in a Skeleton Editor.

### 4.3 VRM 1.0 humanoid — 55 slots

These are **spec slot names, not bone names**. VRM maps your actual bone nodes onto these slots via `VRMC_vrm.humanoid.humanBones`. So forge keeps Blender names and emits a mapping dict.

| slot | parent | required |
|---|---|---|
| hips | — (root of torso) | **required** |
| spine | hips | **required** |
| chest | spine | optional |
| upperChest | chest | optional (only if chest exists) |
| neck | upperChest | optional |
| head | neck | **required** |
| leftEye | head | optional |
| rightEye | head | optional |
| jaw | head | optional |
| leftUpperLeg | hips | **required** |
| leftLowerLeg | leftUpperLeg | **required** |
| leftFoot | leftLowerLeg | **required** |
| leftToes | leftFoot | optional |
| rightUpperLeg | hips | **required** |
| rightLowerLeg | rightUpperLeg | **required** |
| rightFoot | rightLowerLeg | **required** |
| rightToes | rightFoot | optional |
| leftShoulder | upperChest | optional |
| leftUpperArm | leftShoulder | **required** |
| leftLowerArm | leftUpperArm | **required** |
| leftHand | leftLowerArm | **required** |
| rightShoulder | upperChest | optional |
| rightUpperArm | rightShoulder | **required** |
| rightLowerArm | rightUpperArm | **required** |
| rightHand | rightLowerArm | **required** |
| leftThumbMetacarpal | leftHand | optional |
| leftThumbProximal | leftThumbMetacarpal | optional |
| leftThumbDistal | leftThumbProximal | optional |
| leftIndexProximal | leftHand | optional |
| leftIndexIntermediate | leftIndexProximal | optional |
| leftIndexDistal | leftIndexIntermediate | optional |
| leftMiddleProximal | leftHand | optional |
| leftMiddleIntermediate | leftMiddleProximal | optional |
| leftMiddleDistal | leftMiddleIntermediate | optional |
| leftRingProximal | leftHand | optional |
| leftRingIntermediate | leftRingProximal | optional |
| leftRingDistal | leftRingIntermediate | optional |
| leftLittleProximal | leftHand | optional |
| leftLittleIntermediate | leftLittleProximal | optional |
| leftLittleDistal | leftLittleIntermediate | optional |
| rightThumbMetacarpal | rightHand | optional |
| rightThumbProximal | rightThumbMetacarpal | optional |
| rightThumbDistal | rightThumbProximal | optional |
| rightIndexProximal | rightHand | optional |
| rightIndexIntermediate | rightIndexProximal | optional |
| rightIndexDistal | rightIndexIntermediate | optional |
| rightMiddleProximal | rightHand | optional |
| rightMiddleIntermediate | rightMiddleProximal | optional |
| rightMiddleDistal | rightMiddleIntermediate | optional |
| rightRingProximal | rightHand | optional |
| rightRingIntermediate | rightRingProximal | optional |
| rightRingDistal | rightRingIntermediate | optional |
| rightLittleProximal | rightHand | optional |
| rightLittleIntermediate | rightLittleProximal | optional |
| rightLittleDistal | rightLittleIntermediate | optional |

**19 required, 36 optional.** Spec quirks forge must handle:
- `neck` is **optional** but `head`'s listed parent is `neck`. If a bone is absent, its parent becomes the parent of its children — so without `neck`, `head`'s parent is `upperChest` (or `chest`, or `spine`, cascading).
- `chest` was **required in VRM 0.x, optional in 1.0**. Some consumer platforms (VRChat has been reported to reject uploads without it) still want it. **Emit `chest` by default.**
- `upperChest` may only exist if `chest` exists.
- Finger naming differs from Mixamo's: VRM thumb is Metacarpal/Proximal/Distal (3 bones); Mixamo's `Thumb1/2/3` maps onto those three, and Mixamo's `Thumb4` is a leaf tip with no VRM slot.

### 4.4 Mixamo → VRM 1.0 map (52 pairs, from three-vrm)

```python
MIXAMO_TO_VRM = {
  "Hips":"hips","Spine":"spine","Spine1":"chest","Spine2":"upperChest",
  "Neck":"neck","Head":"head",
  "LeftShoulder":"leftShoulder","LeftArm":"leftUpperArm","LeftForeArm":"leftLowerArm","LeftHand":"leftHand",
  "LeftHandThumb1":"leftThumbMetacarpal","LeftHandThumb2":"leftThumbProximal","LeftHandThumb3":"leftThumbDistal",
  "LeftHandIndex1":"leftIndexProximal","LeftHandIndex2":"leftIndexIntermediate","LeftHandIndex3":"leftIndexDistal",
  "LeftHandMiddle1":"leftMiddleProximal","LeftHandMiddle2":"leftMiddleIntermediate","LeftHandMiddle3":"leftMiddleDistal",
  "LeftHandRing1":"leftRingProximal","LeftHandRing2":"leftRingIntermediate","LeftHandRing3":"leftRingDistal",
  "LeftHandPinky1":"leftLittleProximal","LeftHandPinky2":"leftLittleIntermediate","LeftHandPinky3":"leftLittleDistal",
  "RightShoulder":"rightShoulder","RightArm":"rightUpperArm","RightForeArm":"rightLowerArm","RightHand":"rightHand",
  "RightHandThumb1":"rightThumbMetacarpal","RightHandThumb2":"rightThumbProximal","RightHandThumb3":"rightThumbDistal",
  "RightHandIndex1":"rightIndexProximal","RightHandIndex2":"rightIndexIntermediate","RightHandIndex3":"rightIndexDistal",
  "RightHandMiddle1":"rightMiddleProximal","RightHandMiddle2":"rightMiddleIntermediate","RightHandMiddle3":"rightMiddleDistal",
  "RightHandRing1":"rightRingProximal","RightHandRing2":"rightRingIntermediate","RightHandRing3":"rightRingDistal",
  "RightHandPinky1":"rightLittleProximal","RightHandPinky2":"rightLittleIntermediate","RightHandPinky3":"rightLittleDistal",
  "LeftUpLeg":"leftUpperLeg","LeftLeg":"leftLowerLeg","LeftFoot":"leftFoot","LeftToeBase":"leftToes",
  "RightUpLeg":"rightUpperLeg","RightLeg":"rightLowerLeg","RightFoot":"rightFoot","RightToeBase":"rightToes",
}
```
Unmapped Mixamo bones (13): `HeadTop_End`, `Left/RightHand{Thumb,Index,Middle,Ring,Pinky}4` (10), `Left/RightToe_End` (2). 52 + 13 = 65, which is how the Mixamo count checks out.

### 4.5 SMPL-X — 55 joints, canonical index order

From `smplx/joint_names.py`, `JOINT_NAMES[0:55]`. Order is load-bearing: pose parameters are a flat (55×3) axis-angle vector indexed exactly this way.

```
 0 pelvis           14 right_collar      28 left_middle1     42 right_index3
 1 left_hip         15 head              29 left_middle2     43 right_middle1
 2 right_hip        16 left_shoulder     30 left_middle3     44 right_middle2
 3 spine1           17 right_shoulder    31 left_pinky1      45 right_middle3
 4 left_knee        18 left_elbow        32 left_pinky2      46 right_pinky1
 5 right_knee       19 right_elbow       33 left_pinky3      47 right_pinky2
 6 spine2           20 left_wrist        34 left_ring1       48 right_pinky3
 7 left_ankle       21 right_wrist       35 left_ring2       49 right_ring1
 8 right_ankle      22 jaw               36 left_ring3       50 right_ring2
 9 spine3           23 left_eye_smplhf   37 left_thumb1      51 right_ring3
10 left_foot        24 right_eye_smplhf  38 left_thumb2      52 right_thumb1
11 right_foot       25 left_index1       39 left_thumb3      53 right_thumb2
12 neck             26 left_index2       40 right_index1      54 right_thumb3
13 left_collar      27 left_index3       41 right_index2
```
Notes: the body is joints 0–21 (22 incl. pelvis as global), 22–24 are jaw + two eyes, 25–54 are 15 per hand. SMPL-X vertex count is 10,475. Finger order is **index, middle, pinky, ring, thumb** — pinky before ring, which is not what anyone expects and is a classic off-by-three bug.

### 4.6 Unity Humanoid — 15 required bones

Unity's Avatar needs at minimum 15 bones "loosely conforming to a human skeleton":

`Hips`, `Spine`, `Head`, `LeftUpperArm`, `RightUpperArm`, `LeftLowerArm`, `RightLowerArm`, `LeftHand`, `RightHand`, `LeftUpperLeg`, `RightUpperLeg`, `LeftLowerLeg`, `RightLowerLeg`, `LeftFoot`, `RightFoot` = 1+1+1+2+2+2+2+2+2 = **15**.

Required for forge: **T-pose**, not A-pose. Unity's importer shows "Character not in T-Pose" and the automapper fails. Unity reads names heuristically (green = matched, red = not), so Blender-convention names with `.L`/`.R` map fine. Optional bones (Chest, UpperChest, Neck, Shoulders, Toes, Eyes, Jaw, all fingers) get interpolated if absent — but IK quality degrades without Chest and Shoulders.

### 4.7 CMU / BVH and glTF conventions

- **BVH**: Y-up, hierarchical `JOINT` blocks with `OFFSET` and `CHANNELS`. Rotation order is per-joint and declared in `CHANNELS` (CMU is `Zrotation Xrotation Yrotation`). End sites are `End Site { OFFSET ... }` with no channels. CMU's mocap set is in inches at 120 Hz; scale by 0.0254 for metres. **No roll concept** — BVH joints are offset vectors, orientation is implied by the offset to the child. Round-tripping Blender → BVH → Blender loses roll.
- **glTF skin**: a `skin` has `joints` (array of node indices), optional `skeleton` (common root), and `inverseBindMatrices` (one 4×4 per joint). Mesh primitives carry `JOINTS_0` (u8/u16 vec4) and `WEIGHTS_0` (f32/u8/u16 normalized vec4). Y-up, right-handed, metres. **Bone roll does not exist** — a joint is a node with a TRS transform. Blender's exporter bakes roll into the node rotation, which is correct and lossless for deformation but means re-importing gives you different `roll` values with identical world matrices.

---

## 5. BONE ROLL / ORIENTATION MATH

### 5.1 What roll is

A Blender bone's Y axis runs head→tail. That fixes two of three degrees of freedom. `roll` is the rotation about that Y axis, and it determines where local X and Z point. Blender stores it as a scalar on the EditBone.

### 5.2 Why wrong roll destroys things

- **IK pole/plane**: an IK chain solves in the plane defined by the chain's bend axis. For a two-bone limb, the elbow bends about local X. If the upper arm's roll is 0 and the forearm's roll is 1.2 rad, the chain has no consistent bend plane and the solver produces a corkscrew. Pole targets correct the *rotation about the root–tip line*, not an inconsistent per-bone basis.
- **`Limit Rotation` constraints** are expressed in the bone's local axes. A knee limited to `X: 0..160°` with a bad roll limits some diagonal instead, and the knee hyperextends sideways.
- **Retargeting** copies local rotations bone-to-bone. If source and target rolls differ, a clean elbow bend becomes elbow bend + twist. This is why Mixamo animations applied to a hand-built rig look drunk.
- **Twist/roll bone distribution** (`Copy Rotation` with `Y` only, influence 0.5) copies about the *local Y*. Wrong roll means you distribute the wrong component.
- **Mirror symmetry**: `.L`/`.R` pairs must have mirrored rolls (negated, with the X axis flipped). Unmirrored rolls make a symmetric pose asymmetric.

### 5.3 Computing a sane roll

Blender's own operator, verified present in 5.3:

```python
bpy.ops.armature.calculate_roll(type=...)
# types: POS_X POS_Z NEG_X NEG_Z
#        GLOBAL_POS_X GLOBAL_POS_Y GLOBAL_POS_Z
#        GLOBAL_NEG_X GLOBAL_NEG_Y GLOBAL_NEG_Z
#        ACTIVE VIEW CURSOR
```
`GLOBAL_*` aligns the bone's local Z (or X) as closely as possible to a world axis. `POS_X`/`POS_Z` align to the bone's own parent-relative or connected-chain geometry. `ACTIVE` copies from the active bone.

Or do it directly, which is what forge should do because it's deterministic and doesn't need operator context:

```python
import mathutils
eb.align_roll(mathutils.Vector((0, 0, 1)))   # verified: sets roll so local Z is as close to +Z as possible
```
`align_roll(vec)` takes a target direction for the bone's local Z and solves the scalar. Verified: a bone from (0.2,0,1.4) to (0.5,0,1.4) with roll set to 0.5 returned `roll == 0.0` after `align_roll((0,0,1))`.

**forge's roll policy, per bone class:**

| bone class | roll target | reason |
|---|---|---|
| spine, neck, head | local Z → world `-Y` (character's forward) | spine bends forward about local X; keeps `Limit Rotation` axes meaningful |
| upper/lower arm | local Z → the limb's bend plane normal, computed as `normalize(cross(shoulder→elbow, elbow→wrist))` | the elbow's actual hinge axis is local X, exactly |
| thigh/shin | same, from `cross(hip→knee, knee→ankle)` | same |
| hand, foot | local Z → world `+Z` | arbitrary but consistent; foot roll rigs want a predictable up |
| fingers | local Z → the palm normal | curl drivers then act on a single clean axis |
| clavicle | local Z → world `+Z` | |
| twist bones | copy the roll of their parent segment exactly | otherwise the `Copy Rotation Y` distribution is wrong |
| `.R` of any pair | mirror of `.L` | enforce with `symmetrize`, never compute independently |

The limb case is the important one and it's three lines:

```python
def limb_roll(eb_upper, eb_lower):
    """Set roll on both bones so local X is the hinge axis."""
    a = (eb_upper.tail - eb_upper.head).normalized()
    b = (eb_lower.tail - eb_lower.head).normalized()
    n = a.cross(b)
    if n.length < 1e-4:                 # straight limb, no bend plane
        n = mathutils.Vector((1, 0, 0)) if abs(a.z) > 0.9 else mathutils.Vector((0, 0, 1))
        n = a.cross(n)
    n.normalize()
    eb_upper.align_roll(n)
    eb_lower.align_roll(n)
```

**The degenerate case matters.** A perfectly straight limb in a T-pose has `cross(a,b) ≈ 0` and the bend plane is undefined. This is why rigs should be built in a **slightly bent A-pose**, or why forge must inject a tiny pre-bend before computing rolls (see §13, the geometry contract). A 5° knee/elbow bend in the source mesh is worth more than any amount of clever math.

### 5.4 Validating roll numerically

Three checks, all cheap, all in the validator:

1. **Mirror consistency.** For every `.L`/`.R` pair, compute `M_L = bone_L.matrix_local` and `M_R = bone_R.matrix_local`. Mirror `M_L` through the YZ plane (`S = diag(-1,1,1)`), i.e. `M_L' = S @ M_L @ S`. Compare the rotation parts: `angle = acos((trace(R_L'^T @ R_R) - 1)/2)`. **Flag if > 2°.** This catches hand-authored rolls and bad symmetrize in one number.
2. **Hinge-axis alignment.** For each two-bone limb, compute `n` as above and check `abs(dot(n, bone.x_axis))`. **Flag if < 0.95.** Reported as a per-limb scalar, which tells Claude exactly which limb is wrong.
3. **Roll outliers within a chain.** For a chain (spine, finger), compute roll deltas between consecutive bones. Report `max |Δroll|`. **Flag if > 15°** on a chain that should be smooth. A single bone with a 90° roll in the middle of a spine is almost always a mistake.

Report these as three numbers, not three essays.

---
## 6. AUTOMATIC RIGGING ALGORITHMS — SURVEY

Scored on: free, headless, CPU, input requirements, quality, and whether it's maintainable in 2026.

### 6.1 Pinocchio (Baran & Popović, SIGGRAPH 2007)

*"Automatic Rigging and Animation of 3D Characters."* The classic. Two stages: (a) discretize the mesh interior into a sphere-packing "approximate medial surface", build a graph over sphere centres, then fit a given skeleton by a discrete penalty-minimizing embedding (bone length, symmetry, and a "no bone outside the mesh" constraint); (b) skin with a heat-equilibrium diffusion solve — which is literally the algorithm Blender's `ARMATURE_AUTO` implements.

- **Free?** Original source was released; it's old C++ (circa 2007), no active upstream. Various forks exist.
- **Headless?** Yes, it's a command-line tool by design.
- **CPU?** Yes, entirely.
- **Input:** watertight, single-component, roughly upright mesh. **Requires a template skeleton** — it embeds, it doesn't invent.
- **Quality:** good skeleton placement for humanoids in a canonical pose; the skinning is the weaker half.
- **Verdict for forge: do not integrate. Steal the algorithm instead.** Stage (a) — sphere-pack the interior, build a graph, embed a template with a cost function — is exactly the landmark-fitting approach in §6.8, and we can do it in numpy + libigl in a few hundred lines with no C++ build. Stage (b) is already in Blender.

### 6.2 RigNet (Xu et al., SIGGRAPH 2020)

Learned. A GMEdgeNet predicts joint locations by clustering displaced vertices, then a learned module predicts the bone connectivity (a minimum spanning tree over predicted joints with learned edge costs), then a skinning branch predicts weights from per-vertex volumetric geodesic features.

- **Free?** **GPLv3**, or a commercial licence via UMass Amherst TTO. GPLv3 is a real problem if forge is ever distributed as anything other than GPL.
- **Maintained?** **No.** Last meaningful update 2023 (tested on Ubuntu 22.04, CUDA 11.3, `pytorch==1.12.0`). Forks are older still (`pytorch 1.7.1`, `pytorch_geometric 1.7.2`). The Blender add-on `pKrime/brignet` is self-labelled "DEAD AND WILL HARDLY WORK".
- **Headless/CPU?** Nominally yes; in practice the pinned `torch_geometric`/`torch_scatter` stack from 2021–2022 will not build against Python 3.13.
- **Verdict: dead end in 2026.** Cite it for the skinning-feature idea (volumetric geodesic distance as the input feature is the right idea and we use it in §7.8) and move on.

### 6.3 UniRig (VAST-AI + Tsinghua, SIGGRAPH 2025 / TOG)

The live thread, and the best of the learned crop. Autoregressive skeleton generation (tokenized bone sequence) plus a separate skinning stage using Bone-Point Cross Attention. Trained on **Rig-XL** (14,000+ rigged models); the public checkpoint is trained on **Articulation-XL 2.0**.

- **Licence: MIT.** That's the headline — it's the only strong 2025 auto-rigger with a permissive licence on the code.
- **Requirements, from the repo:** Python **3.11** (not 3.13), `torch >= 2.3.1`, `numpy==1.26.4` pinned, `spconv-{cuda-version}`, `torch_scatter` + `torch_cluster` from the PyG wheel index, and **`flash-attn`** — for which the README itself warns you will likely hit an installation error. **"For generation: CUDA-enabled GPU with at least 8GB VRAM."**
- **Model size:** ~350M parameters.
- **CPU?** Not documented by the authors. Third-party integration work reports the transformer stages run deterministically with greedy decode on CPU at **one to several minutes per mesh**. Unverified by me.
- **Input formats:** `.obj`, `.fbx`, `.glb`, `.vrm`.
- **CLI, verbatim:**
  ```
  bash launch/inference/generate_skeleton.sh --input examples/giraffe.glb --output results/giraffe_skeleton.fbx
  bash launch/inference/generate_skin.sh --input examples/skeleton/giraffe.fbx --output results/giraffe_skin.fbx
  bash launch/inference/merge.sh --source ... --target ... --output ...
  ```
  Both accept `--input_dir`/`--output_dir`; skeleton generation accepts `--seed`.
- **Known failure mode, from the repo:** skeleton errors propagate into skinning. The README explicitly recommends refining the skeleton before skinning, noting that missing tail or wing bones "significantly degrade" skin results.
- **Successor: SkinTokens** (`VAST-AI-Research/SkinTokens`), announced as unifying both stages into one autoregressive sequence with learned discrete skin tokens, plus RL and a skinning compression module. Authors claim **98–133% gain in skinning accuracy** and **17–22% gain in bone prediction**. I did not verify those numbers or read its licence.
- **Verdict: optional v2 backend behind a flag, never the default.** Python 3.11 vs our 3.13, a CUDA-specific `spconv`, and `flash-attn` make it a separate venv and a separate container at minimum. Its real value is non-humanoids where no template exists — a giraffe, a dragon, a six-legged thing. For those, UniRig is genuinely better than anything template-based.

### 6.4 Other 2025–2026 learned auto-riggers

Chased this hard; here's the landscape as of now.

| system | org / venue | licence (as found) | notes |
|---|---|---|---|
| **MagicArticulate** | ByteDance Seed3D, 2025 | **Apache-2.0** (code) | Autoregressive skeleton generation; introduced the **Articulation-XL** dataset. Critiqued by Puppeteer's authors for losing fine detail, misaligned skeletons on some shapes, slow inference, poor generalization in its skinning step. |
| **RigAnything** | Adobe Research, SIGGRAPH TOG 2025 | **Adobe Research License — noncommercial research only** | Template-free autoregressive; predicts hierarchy + weights. AniGen's eval found it failed to cover one test shape's full structure and showed regional skin artifacts. **Licence kills it for forge.** |
| **Anymate** | Deng et al., 2025 | **Apache-2.0** | Modular: separate joint / connectivity / skinning heads. AniGen found joint placement close to ground truth but some bone connections wrong. Rigel3D reports beating it on skeleton and skinning metrics. |
| **Make-It-Animatable** | 2025 | not verified | Predicts skeleton + weights + pose rectification; reported at **a few seconds per shape**, which is the fastest claim in the set. Benchmarked against Anymate and MagicArticulate on ShapeNet and RigNet data. |
| **Puppeteer** | ByteDance Seed3D, 2025 (arXiv 2508.10898) | not verified | Rig + animate; its paper is the most useful comparative read because it critiques the others concretely. |
| **AniGen** | 2026 (arXiv 2604.08746) | not verified | Unified S³ fields for animatable 3D asset generation; contains a useful qualitative eval of RigAnything and Anymate. |
| **Rigel3D** | 2026 (arXiv 2605.13129) | not verified | Rig-aware latents; claims SOTA over Anymate. |
| **SkinTokens** | VAST-AI, 2026 | not verified | UniRig successor, see above. |

Reference implementation worth reading: **`zeljkovranjes/auto-rigger`** — an in-editor C# auto-rigger for s&box that integrates seven of these neural models plus a non-neural "geodesic voxel skinner" fallback. It's the only place I found a single project that has actually wired more than two of these up, and its model table is the source of several licence facts above.

**Honest read on the whole category:** the papers are a year or less apart and each one's eval section says the previous one is broken. That's a field that hasn't settled. Do not build forge's spine on it. Build the spine on Rigify + landmark fitting, which has been stable for a decade, and add a learned backend as a plug-in when one of these ships a CPU path and a permissive licence in the same release.

### 6.5 Blender Rigify — the actual recommendation

**Verified working headless on this container.** Numbers, measured:

```
addon_utils.enable("rigify", default_set=True)
bpy.ops.object.armature_human_metarig_add()      # 0.01 s, 159 bones
bpy.ops.pose.rigify_generate()                   # 6.5 s  →  706 bones
```
Breakdown of the generated rig: **160 DEF-, 159 ORG-, 167 MCH-, 220 control bones. 533 constraints** (types: `ARMATURE`, `COPY_LOCATION`, `COPY_ROTATION`, `COPY_SCALE`, `COPY_TRANSFORMS`, `DAMPED_TRACK`, `IK`, `LIMIT_DISTANCE`, `LIMIT_ROTATION`, `STRETCH_TO`), **114 drivers**, **220 custom shapes**, **23 bone collections** (`Face`, `Face (Primary)`, `Face (Secondary)`, `Torso`, `Torso (Tweak)`, `Fingers`, `Fingers (Detail)`, `Arm.L (IK)`, `Arm.L (FK)`, `Arm.L (Tweak)`, same ×4 limbs, `Root`, `ORG`, `MCH`, `DEF`).

That is a full IK/FK-switchable, foot-rolled, finger-curled, face-rigged production biped. For free. In six and a half seconds. On a CPU.

**Available metarig operators, verified:**
```
bpy.ops.object.armature_human_metarig_add()            # full biped w/ face + fingers, 159 bones
bpy.ops.object.armature_basic_human_metarig_add()      # biped, no face
bpy.ops.object.armature_basic_quadruped_metarig_add()
bpy.ops.object.armature_bird_metarig_add()
bpy.ops.object.armature_cat_metarig_add()
bpy.ops.object.armature_horse_metarig_add()
bpy.ops.object.armature_wolf_metarig_add()
bpy.ops.object.armature_shark_metarig_add()
bpy.ops.armature.metarig_sample_add(metarig_type=...)  # add a single limb/spine/finger sample
```
Plus the encode path: `bpy.ops.armature.rigify_encode_metarig()` turns a hand-built metarig into a Python script, and `rigify_encode_metarig_sample()` does the same for a reusable sample. That's how forge can ship its own metarigs (a dragon, a mech) as generated Python.

**Can metarig placement be automated from mesh landmarks?** Yes, and this is the key insight: a metarig is just an armature with `pose_bone.rigify_type` strings set. Verified samples from the human metarig:
```
spine        → 'spines.basic_spine'
spine.004    → 'spines.super_head'
face         → 'faces.super_face'
shoulder.L   → 'basic.super_copy'
upper_arm.L  → 'limbs.arm'
palm.01.L    → 'limbs.super_palm'
f_index.01.L → 'limbs.super_finger'
thumb.01.L   → 'limbs.super_finger'
```
So the automation is: **add the stock metarig, then move its edit_bone head/tail positions to match landmarks detected on the target mesh, then generate.** The `rigify_type` strings, bone collection assignments and UI rows all come along for free. This is dramatically less work than authoring a control rig from scratch and it's what §6.8 designs.

Caveats:
- Rigify writes `armature.rigify_target_rig`; re-running generate updates the existing rig in place rather than making a second one. Good for iteration.
- Generate is sensitive to metarig validity. Bad `rigify_type` on a bone whose chain length doesn't match the rig type throws. forge should validate the metarig (chain lengths per rig type) before calling generate, and surface the error string.
- The generated rig's **deform set is `DEF-*` only** — 160 of 706 bones. Everything else is authoring-side. See §8.6.

### 6.6 Hosted / commercial auto-riggers

| service | free? | API? | headless? | verdict |
|---|---|---|---|---|
| **Mixamo** (Adobe) | free with an Adobe ID | **no public API** | web only | **Dead end for automation.** Will retarget onto roughly-biped custom skeletons, which is useful manually. Its *output naming* is the valuable part, and we reproduce that in §4.1 for free. |
| **AccuRIG** (Reallusion / ActorCore) | **yes**, free standalone app; free tier handles one mesh in A/T pose | **no public API** (directories report 0/20 on API availability) | desktop GUI, Windows | Good quality, genuinely free, but GUI-only and Windows-only. **Dead end for forge.** |
| **Anything World** | has a free tier historically | has an API | yes | **Could not verify current status, pricing or API shape.** Flagged as unverified. |
| **Meshy** | credit-based | has an API; rigging feature exists | yes | **Could not verify the rigging endpoint or its cost.** Flagged as unverified. |
| **Tripo** (same org as UniRig) | credit-based | **yes — documented rig endpoint** | yes | `prerigcheck` (Rig Check) is **free**; Auto Rigging costs **25 credits** (listed as ¥1.75 — unclear whether per-rig). Since Tripo is VAST-AI, this is effectively hosted UniRig. **Worth a thin optional client** for the non-humanoid case where we'd otherwise need an 8 GB GPU. |
| **DeepMotion** | paid, free trial | yes (animation-focused) | yes | Their strength is video→motion, not rigging. Belongs in the animation researcher's doc, not here. |

**Recommendation: no hosted service in the default path.** Add `tripo_rig` as an optional, explicitly-opt-in tool for non-humanoids, with a clear "this uploads your mesh to a third party" note in the docstring. Everything else is local.

### 6.7 Paid Blender add-ons (capability note, cost flagged)

- **Auto-Rig Pro** (~$40 on Blender Market / Superhive, GPL-compatible but paid distribution). Its "Smart" feature does exactly what §6.8 designs: detect neck/chest/shoulders/wrists/ankles on the mesh via a markers + guess pass, then fit a reference armature. It also has a mature **Game Engine export** module — and its docs are a useful source of truth: *4 spine bones + 1 neck to match the UE4 Mannequin, 6 spine bones + 2 neck to match UE5* (its count includes a `spine_00`, which is why it reads 6 against our 5; verify against the engine). **Flag the cost; don't depend on it. Read its docs.**
- **Rigify** is free and in-tree. Use it.
- **ARP's smart-detect is the single most useful thing to clone**, and it's clonable because the algorithm is published in outline and the hard part (the reference armature) is Rigify's metarig.

### 6.8 Template fitting from mesh landmarks — **the recommended path**

This is what forge should actually do. Design:

**Step 0 — Normalize.** Needed from geometry (§13): single mesh or named parts, Z-up, metres, origin at feet, facing −Y, A- or T-pose. If not, fix it: PCA the vertices, take the longest principal axis as "up", check the mesh's convex-hull footprint for which end is feet (feet end is wider in X, narrower in Y).

**Step 1 — Symmetry plane.** Fit a plane by minimizing reflection error. Cheap version: assume YZ (x=0), measure `mean |d(reflect(v), mesh)|` with `igl.signed_distance` (verified available, with `SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER` for robustness on imperfect meshes). Better version: optimize plane normal + offset over 3 DOF with scipy. Report the residual — a high residual means the mesh isn't symmetric and mirroring ops must be disabled.

**Step 2 — Extremity detection.** Compute a geodesic field from the mesh centroid's nearest vertex using `igl.heat_geodesics_precompute` + `_solve` (verified: 0.009 s precompute, 0.0003 s solve on 1,800 verts). Local maxima of that field, after non-maximum suppression at a radius of ~5% of the bounding diagonal, are the extremities: head, two hands, two feet, plus tail/wings/extras on non-humanoids. This is the classic approach and it's robust because geodesic distance follows the limbs.
  - Classify: the extremity nearest the up-axis maximum and closest to the symmetry plane is the **head**. The two lowest are **feet**. The two with the largest lateral offset at mid-height are **hands**. Anything left over is extra and goes to the non-humanoid path.
  - Confidence score per landmark = (geodesic field value) / (max field value), plus the mirror-pair distance residual. Report these; let Claude decide to override.

**Step 3 — Interior medial points.** Sphere-pack or voxel-thin the interior to get a centreline. Two options:
  - `skeletor` (`pip install skeletor`): implements mesh contraction (Au et al.), edge collapse, TEASAR, wavefront propagation, and mean-curvature skeletons (Tagliasacchi et al.). `sk.pre.fix_mesh()` then `sk.skeletonize.by_wavefront()` → a `Skeleton` object. Optional `navis-fastcore` gives sizeable speedups. **This is the ready-made curve-skeleton library and it's maintained under navis-org.**
  - Or do it by hand: voxelize with `igl.fast_winding_number` for inside/outside (verified available), then distance-transform the voxel interior and ridge-walk. More control, more code.

**Step 4 — Fit the Rigify metarig.** Add `armature_human_metarig_add()`, then solve for each metarig bone's head/tail. Do it as a constrained least-squares:
  - **Hard constraints**: hands at the hand landmarks, feet at the foot landmarks, head top at the head landmark, hips at the centroid of the lower-torso cross-section.
  - **Soft objectives**: every joint lies on or near the medial centreline; `.L`/`.R` pairs are mirror-symmetric about the fitted plane; bone-length ratios stay within ±25% of the template's (this is the regularizer that keeps a stylized character from getting a 4-head-tall skeleton's proportions).
  - **Barrier**: every joint is strictly inside the mesh. Check with `igl.winding_number` or `signed_distance`; penalize positive SDF hard.
  - Solve the torso chain first (it's the most constrained), then limbs outward, then fingers from the hand landmark by splaying along the hand's local frame.
  - Fingers are the part that landmark detection will not get right. **Fall back to proportional placement from the hand's oriented bounding box** and let the validator's heatmap render tell Claude whether to fix them.

**Step 5 — Rolls.** Apply §5.3's per-class policy. Pre-bend elbows/knees 5° first if the pose is a dead-straight T.

**Step 6 — `symmetrize`** to guarantee the `.R` side is an exact mirror, then **`rigify_generate`**.

**Step 7 — Bind** (§7), **validate** (§10), report.

**Why this is the right path:** it needs no GPU, no model weights, no licence worry, and it produces a *Rigify* rig — which means IK/FK switching, foot rolls, finger drivers, and a face rig all come for free. The learned auto-riggers produce a bare skeleton + weights, after which you still have to build the control rig. Template fitting skips that entirely.

**Where it fails:** anything with no humanoid template. Dragons, spiders, tentacled things, vehicles. For those: Rigify's bird/cat/horse/wolf/shark metarigs cover a lot; beyond that, go to the `skeletor` curve skeleton + generic chain rigging path (§9.8), or call out to UniRig/Tripo.

---

## 7. SKINNING WEIGHTS

The weights decide whether a rig is good. Everything else is scaffolding.

### 7.1 Blender automatic weights (bone heat)

`bpy.ops.object.parent_set(type='ARMATURE_AUTO')`. Implements the heat-equilibrium diffusion from Baran & Popović: treat each bone as a heat source, solve the Laplace equation over the mesh with the bone's region held at temperature 1, and the equilibrium temperature field is the weight.

**Measured:** 11,088 verts, 2 bones → **0.62 s**. Weight row sums **0.97913 to 1.00003**. Max influences per vertex: **2**. Verts with zero total weight: **0**.

Note the row sums: **bone heat does not produce exact partition of unity.** 0.979 means ~2% of that vertex's motion is lost. Always follow with `vertex_group_normalize_all`.

`parent_set` armature types, verified full list: `ARMATURE`, `ARMATURE_NAME`, `ARMATURE_AUTO`, `ARMATURE_ENVELOPE`.
- `ARMATURE` — parent with no weights at all.
- `ARMATURE_NAME` — create empty vertex groups named after bones. Useful when forge computes weights itself.
- `ARMATURE_AUTO` — bone heat.
- `ARMATURE_ENVELOPE` — envelope weights.

**Failure modes of bone heat, in order of how often they'll bite:**
1. **Non-manifold geometry.** The Laplacian isn't well-defined. Result: operator reports success, weights are garbage or missing on whole regions. **Must be pre-checked.**
2. **Multiple disconnected components.** Each component is solved independently; a component containing no bone gets no weight at all → orphan vertices → those verts stay at the rest position while the mesh moves. Catastrophic and visually obvious, invisible numerically unless you check.
3. **Limbs that touch at rest.** Heat diffuses across the contact. An arm resting against the torso gets torso weight on the arm and vice versa. This is *the* reason A-pose beats a tight pose, and why T-pose beats A-pose for the armpit specifically. **Geodesic distance would avoid this; Blender's implementation does not fully.**
4. **Interior geometry** (teeth inside a head, a skeleton inside a body) picks up weights from whichever bone is geometrically nearest, often wrong.
5. **High-genus / thin shells.** Thin double-sided surfaces (cloth, hair cards) get weights blended across the shell.
6. **Very dense meshes** — it's a sparse solve, so it scales fine, but memory grows; 1M+ verts is where you want to decimate, weight, and transfer.

### 7.2 Envelope weights

`ARMATURE_ENVELOPE`, or the Armature modifier's `use_bone_envelopes=True` (verified property). Each bone has a capsule (`bone.envelope_distance`, `bone.head_radius`, `bone.tail_radius`) and weight falls off with distance from the capsule.

Fast, trivially predictable, and **bad** — it's Euclidean, so it does exactly the wrong thing where limbs touch. Its one good use: a deliberate fallback when bone heat fails (non-manifold input) so forge produces *something* bindable rather than nothing. Report clearly that envelope was used; the quality score should take a hit for it.

The Armature modifier can run both at once with `use_bone_envelopes=True` and `use_vertex_groups=True` — envelope fills in where vertex groups have no weight. Useful as a safety net; dangerous because it hides orphan verts from the validator. **forge should bind with `use_bone_envelopes=False` so orphans are detectable, and only enable it as an explicit, reported repair.**

### 7.3 Bounded Biharmonic Weights (Jacobson et al. 2011) via libigl

**Status in 2026: available, and it works.** Verified on this container.

```bash
pip install libigl     # the PyPI package is `libigl`, imported as `igl`. NOT `pip install igl`.
```
The bindings are nanobind-based, 302 exposed functions, and the project's own docs still say "under active development, alpha quality". `igl.__version__` does not exist (removed in 2.5.4.dev0). Probe with `dir(igl)`.

Verified present and relevant: `bbw`, `boundary_conditions`, `harmonic`, `biharmonic_coordinates`, `lbs_matrix`, `heat_geodesics_precompute`, `heat_geodesics_solve`, `exact_geodesic`, `fast_winding_number`, `winding_number`, `signed_distance`. **Not present: any tetrahedralizer.** You bring your own.

Signatures, verified:
```python
igl.bbw(V, Ele, b, bc,
        W0=empty, partition_unity=False, verbosity=0, max_iter=100,
        inactive_threshold=1e-14, constraint_threshold=1e-14,
        solution_diff_threshold=1e-14) -> W            # (#V, #handles)

igl.boundary_conditions(V, Ele, C, P=empty, BE=empty, CE=empty, CF=empty) -> (b, bc)
# C  = (#handles, 3) handle positions
# P  = point-handle indices into C
# BE = (#bones, 2) bone-edge indices into C
```

**The trap, and it's a real one.** Calling `boundary_conditions` on a raw **surface** mesh with bones running through the interior throws:
```
RuntimeError: boundary_conditions: failed to compute
```
Verified, reproducibly, on a 1,800-vert / 3,596-face cylinder with a 2-segment bone down the axis. The docstring says why: *"This function expects that the given mesh (V,Ele) has sufficient samples (vertices) exactly at point handle locations and exact [samples along bone edges]."* A surface mesh has no vertices on an interior bone, so there is nothing to constrain.

**The recipe that works, verified end to end:**

```python
# 1. sample points along every bone segment
N = 12
samples = [a + (b - a) * k / N
           for (a, b) in bone_segments      # (head, tail) per bone, world space
           for k in range(1, N)]            # interior samples only; endpoints are joints

# 2. tetrahedralize the interior WITH those samples as input points
tg = tetgen.TetGen(np.vstack([V_surface, joint_positions, samples]), F_surface)
tg.tetrahedralize(order=1, mindihedral=10, minratio=1.5, switches='pq1.5/10Y')
Vt, Tt = np.asarray(tg.node), np.asarray(tg.elem)
# 'Y' preserves the input surface triangulation so the first len(V_surface)
#  rows of Vt are still your original surface verts, in order. This matters.

# 3. locate the handle nodes in the tet mesh and solve
b, bc = igl.boundary_conditions(Vt, Tt, Cs, np.array([], np.int64), BE)
W = igl.bbw(Vt, Tt, b, bc, verbosity=0, max_iter=30)

# 4. slice the surface rows back out
W_surface = W[:len(V_surface)]
```

**Measured results** on a 506-vert / 1,008-face cylinder with 22 bone samples inserted:
```
tetgen:              715 nodes, 2,742 tets, 186 Steiner points, 0.05 s
boundary_conditions: b = 25 constrained nodes  (3 joints + 22 samples)
BBW:                 0.08 s, max_iter=30
row sums:            1.00000 to 1.00000   ← exact partition of unity
negatives:           0                    ← bounded, by construction
surface slice:       1.00000 to 1.00000
falloff (2-bone limb, bone0/bone1):
  z=0.1 → [1.000, 0.000]
  z=0.5 → [1.000, 0.000]
  z=1.0 → [0.503, 0.497]   ← the joint
  z=1.5 → [0.000, 1.000]
  z=1.9 → [0.000, 1.000]
```
That is textbook-correct BBW behaviour for *line* handles: rigid along each bone, blend band localized at the joint. (If you want a wider blend, use point handles at the joints instead of bone edges, or raise `max_iter` and relax with `W0`.)

**Why BBW is worth the tetrahedralization:**
- **Exact partition of unity** (1.00000, verified) — bone heat gives 0.979.
- **Non-negative by construction** — no shrinkage artifacts from negative weights.
- **Shape-aware via the interior**, not the surface. Two limbs touching at rest have far-apart interior tet paths even though their surfaces are adjacent. **This is the fix for bone heat's worst failure mode.** This is also why interior tets beat surface distance generally: the metric follows the volume the flesh actually occupies.
- Smooth (biharmonic, C¹), so no weight-gradient discontinuities.

**Costs:** needs a watertight, self-intersection-free, single-component surface to tetrahedralize. tetgen will fail or produce nonsense otherwise. Pre-repair is mandatory. Budget: tetgen at 0.05 s / 2.7 k tets scales roughly linearly; a 50 k-vert character will tetrahedralize to maybe 300 k tets in a couple of seconds and BBW will take a few seconds more. Still comfortably interactive.

**Fallback inside the same machinery:** `igl.harmonic(V, Ele, b, bc, k=2)` gives plain biharmonic weights on the same tets in **0.01 s**, also with row sums 1.0000 and zero negatives on this test case. It is *not* bounded in general (it can go negative on complex shapes) but it's 8× faster and a fine first pass. Check `(W < 0).sum()` and only escalate to `bbw` if it's nonzero.

### 7.4 Geodesic Voxel Binding, heat geodesics, Delta Mush

- **Geodesic Voxel Binding** is Maya's (Dionne & de Lasa 2013): voxelize the interior, compute geodesic distance from each bone through the voxel interior, convert distance to weight. **No FOSS equivalent exists as a library.** But the components are all here: `igl.fast_winding_number` for the inside test, a numpy distance transform or Dijkstra over the voxel grid for the geodesic, done. The `zeljkovranjes/auto-rigger` project ships exactly this as its non-neural "geodesic voxel skinner" fallback. **Worth implementing in forge as `method="geodesic_voxel"`** — it's cheaper than BBW (no tet mesh, no quadratic program), robust to non-watertight input (winding number handles that), and it fixes the touching-limbs problem. Roughly: voxelize at ~1% of the bbox diagonal, BFS/Dijkstra from each bone's rasterized voxels, weight = `exp(-(d/σ)²)` or `1/d^p`, normalize.
- **Heat geodesics** (Crane et al. 2013) via `igl.heat_geodesics_precompute` / `_solve`. Verified: 0.009 s precompute, 0.0003 s per solve on 1,800 verts. **Surface** geodesics, not volumetric, so it does not fix touching limbs — but it's the right tool for the *validator* (§10.5, geodesic-vs-euclidean mismatch detection) and for falloff painting along the surface.
- **Delta Mush** (Mancewicz et al. 2014): Laplacian-smooth the rest pose, store per-vertex deltas in a local surface frame, re-apply the deltas to the smoothed deformed pose. The result is that crude weights produce smooth deformation. **There is no maintained FOSS Delta Mush for Blender.** The 2015 `temp-modifier-deltamush-experimental` branch was never merged. Paid add-ons exist (Multi Delta Mush). Maya and C4D implementations exist.
  - **But Blender's `CORRECTIVE_SMOOTH` modifier is a close functional cousin and it ships.** Verified properties: `factor`, `iterations`, `scale`, `smooth_type` (`SIMPLE` | `LENGTH_WEIGHTED`), `rest_source` (`ORCO` | `BIND`), `use_only_smooth`, `use_pin_boundary`, `vertex_group`, `invert_vertex_group`. With `rest_source='BIND'` it stores a bind state and corrects toward it — which is the Delta Mush idea. **Use it.** Add it after the Armature modifier, `factor≈0.5`, `iterations≈5`, scoped to a vertex group covering the joints. It rescues mediocre weights cheaply.
  - Also available: `LAPLACIANSMOOTH` (with `use_volume_preserve`, `use_normalized`), `SMOOTH`, `LAPLACIANDEFORM`, `SURFACE_DEFORM`, `MESH_DEFORM`, `SHRINKWRAP`.

### 7.5 Dual quaternion skinning — the candy-wrapper fix

Verified Armature modifier properties: `object`, `use_bone_envelopes`, `use_vertex_groups`, **`use_deform_preserve_volume`**, `use_multi_modifier`, `vertex_group`, `invert_vertex_group`.

`use_deform_preserve_volume=True` switches the modifier from linear blend skinning to **dual quaternion** skinning. That is the single-flag fix for candy-wrapper collapse on twisted joints (forearm, thigh) and for volume loss on bends. Costs: DQS can bulge at high bend angles and does not export to glTF (glTF skinning is LBS, period).

**forge policy:** enable it for authoring and for the validation poses, because it makes the twist tests meaningful. For glTF/FBX export, **disable it and re-run the twist validation**, because the consumer will use LBS. If the LBS twist test fails, the fix is twist bones (§8.4), not DQS.

Also present: the **`MAINTAIN_VOLUME`** bone constraint, which clamps a bone's scale to preserve volume along a free axis. Useful on squash-stretch rigs; not part of the default path.

### 7.6 Learned skinning

- **RigNet's skinning branch**: per-vertex features are *volumetric geodesic distances to each bone* fed to a learned predictor. The idea — volumetric geodesic distance is the right feature — is correct and reusable without the model (that's §7.4's geodesic voxel binding).
- **NeuroSkinning** (Liu et al. 2019) and successors: graph-conv networks on the mesh predicting weights. Mostly superseded.
- **UniRig's Bone-Point Cross Attention** and **SkinTokens' discrete skin tokens** are the current state of the art. SkinTokens claims 98–133% skinning-accuracy gain over UniRig. GPU, not verified.
- **Verdict:** none of these are in forge's v0/v1. The honest comparison is that BBW on tets gives you *provably* non-negative, partition-of-unity, shape-aware weights in 0.08 s with no model download, and the learned methods give you plausible weights on shapes BBW's tetrahedralizer can't handle. Keep them for that case only.

### 7.7 Weight post-processing Claude can run blind

Every one of these is a verified Blender operator with a verified parameter list.

| op | params (verified) | what it's for |
|---|---|---|
| `vertex_group_normalize_all` | `group_select_mode`, `lock_active` | **Partition of unity. Always run this last.** Fixes bone heat's 0.979. |
| `vertex_group_normalize` | — | normalize one group to peak 1.0 |
| `vertex_group_limit_total` | `group_select_mode`, `limit` | **`limit=4` for glTF.** Drops the smallest influences. Run *before* the final normalize. |
| `vertex_group_clean` | `group_select_mode`, `limit`, `keep_single` | remove weights below `limit`. `limit=0.001`, `keep_single=True` (so a vertex never loses its last influence). |
| `vertex_group_smooth` | `group_select_mode`, `factor`, `repeat`, `expand` | blur weights across the surface. `factor=0.5, repeat=3` is a sane default; `expand` grows/shrinks the group boundary (negative shrinks). **The main repair tool.** |
| `vertex_group_levels` | `group_select_mode`, `offset`, `gain` | contrast curve on weights. `gain>1` sharpens the falloff, `offset` shifts it. |
| `vertex_group_quantize` | `group_select_mode`, `steps` | snap to N levels. Niche; good for stylized/rigid-looking rigs. |
| `vertex_group_mirror` | `mirror_weights`, `flip_group_names`, `all_groups`, `use_topology` | **symmetrize weights.** `use_topology=True` matches by mesh topology instead of position, which works on asymmetric-vertex-position meshes. |
| `vertex_group_invert` | — | 1−w |
| `vertex_group_copy` / `copy_to_selected` | — | duplicate a group / push to other objects |
| `vertex_group_sort` | — | alphabetical or by bone hierarchy |

**Weight transfer between meshes**, verified:
```python
bpy.ops.object.data_transfer(
    data_type='VGROUP_WEIGHTS',
    vert_mapping='POLYINTERP_NEAREST',   # or NEAREST, EDGEINTERP_NEAREST, POLYINTERP_VNORPROJ, TOPOLOGY
    layers_select_src='BONE_DEFORM',     # or ACTIVE, ALL, BONE_SELECT
    layers_select_dst='NAME',
    use_create=True, mix_mode='REPLACE', mix_factor=1.0,
    use_max_distance=True, max_distance=0.05, ray_radius=0.01,
    use_object_transform=True)
```
`layers_select_src='BONE_DEFORM'` is the gift here — it transfers only the groups that correspond to deforming bones, skipping junk groups. `vert_mapping` options verified: `TOPOLOGY`, `NEAREST`, `EDGE_NEAREST`, `EDGEINTERP_NEAREST`, `POLY_NEAREST`, `POLYINTERP_NEAREST`, `POLYINTERP_VNORPROJ`. Use `TOPOLOGY` when the meshes are topologically identical (an LOD chain from the same base), `POLYINTERP_NEAREST` otherwise.

**This is how forge handles dense meshes:** decimate to ~20 k, bind and weight the decimated proxy (fast BBW), then `data_transfer` with `POLYINTERP_NEAREST` back to the full-resolution mesh. Verified operator; the quality loss is small and the time saved is large.

**Recommended post-process pipeline, in order:**
1. `vertex_group_clean(limit=0.001, keep_single=True)` — kill numerical dust
2. `vertex_group_smooth(factor=0.5, repeat=2)` on joint regions only — kill gradient discontinuities
3. `vertex_group_mirror(all_groups=True, use_topology=False)` if the symmetry residual from §6.8 step 1 is low — enforce symmetry
4. `vertex_group_limit_total(limit=4)` **if** the export target is glTF
5. `vertex_group_normalize_all()` — **always last**

Order matters. Normalizing before limiting means the limit breaks normalization again.

### 7.8 Numeric validation of weights

Read weights fast — do not iterate `vertex_groups[i].weight(v)` in Python for a 100 k mesh, it's an O(n·m) exception-driven lookup. Build the matrix once:

```python
import numpy as np
gi = {g.index: k for k, g in enumerate(mesh.vertex_groups)}
W = np.zeros((len(mesh.data.vertices), len(mesh.vertex_groups)))
for v in mesh.data.vertices:                 # ~0.5 s per 100k verts, acceptable
    for g in v.groups:
        W[v.index, gi[g.group]] = g.weight
```
(There is no `foreach_get` for vertex-group weights; `v.groups` is the only path. If this becomes a bottleneck, write the weights to a float color attribute per bone and `foreach_get` that — ugly but 50× faster.)

Then, every check is one line of numpy:

| check | expression | threshold |
|---|---|---|
| **partition of unity** | `s = W.sum(1)`; report `s.min(), s.max(), np.abs(s-1).mean()` | fail if `min < 0.999` or `max > 1.001` |
| **max influences / vertex** | `(W > 1e-6).sum(1).max()` | fail if `> 4` and target is glTF |
| **orphan vertices** | `(W.sum(1) < 1e-6).sum()` | **fail if > 0, always.** |
| **negative weights** | `(W < -1e-9).sum()` | fail if > 0 |
| **dead bones** | `[name for k,name in enumerate(groups) if (W[:,k] > 1e-4).sum() == 0]` | warn — a bone with no weighted verts is either a control bone (fine) or a mistake |
| **weight gradient discontinuity** | for each edge `(i,j)`: `np.abs(W[i]-W[j]).max(1)`; report the 99.9th percentile and the count over 0.4 | a single edge where a weight jumps 0.4+ is a visible crease |
| **per-bone island count** | for bone k, take `V[W[:,k] > 0.1]`, build the induced subgraph on mesh edges, count connected components with `scipy.sparse.csgraph.connected_components` | **fail if > 1.** A bone owning two disconnected regions is the "left hand bone also weights the right thigh" bug, and it is the single most valuable check in this table. |
| **geodesic-vs-euclidean mismatch** | for bone k, let `g = igl.heat_geodesics_solve(data, bone_k_nearest_verts)`. Flag vertices where `W[:,k] > 0.3` but `g > 3 × (median g over W[:,k]>0.3)`. | these are the verts bone heat grabbed across a contact. **Report the count and a sample of 5 indices**, not the list. |
| **symmetry residual** | for each `.L`/`.R` group pair, mirror the L weights onto the mirrored vertex correspondence and report `mean |w_L_mirrored - w_R|` | warn above 0.02 |

All of these are numbers. All of them fit in a few hundred tokens. That's the point.

---
## 8. RIG CONTROLS AND DEFORMATION QUALITY

A bound skeleton is not a rig. This section is what makes an animator accept it.

### 8.1 Constraints available in Blender 5.3

Verified, complete list from `bpy.types.Constraint`:
```
CAMERA_SOLVER, FOLLOW_TRACK, OBJECT_SOLVER,
COPY_LOCATION, COPY_ROTATION, COPY_SCALE, COPY_TRANSFORMS,
LIMIT_DISTANCE, LIMIT_LOCATION, LIMIT_ROTATION, LIMIT_SCALE,
MAINTAIN_VOLUME, TRANSFORM, TRANSFORM_CACHE,
CLAMP_TO, DAMPED_TRACK, IK, LOCKED_TRACK, SPLINE_IK, STRETCH_TO, TRACK_TO,
ACTION, ARMATURE, CHILD_OF, FLOOR, FOLLOW_PATH, GEOMETRY_ATTRIBUTE, PIVOT, SHRINKWRAP
```
The rigging-relevant set: `IK`, `SPLINE_IK`, `COPY_ROTATION`, `COPY_LOCATION`, `COPY_TRANSFORMS`, `COPY_SCALE`, `LIMIT_ROTATION`, `LIMIT_DISTANCE`, `DAMPED_TRACK`, `STRETCH_TO`, `ARMATURE`, `ACTION`, `CHILD_OF`, `TRANSFORM`, `MAINTAIN_VOLUME`, `FLOOR`.

`ARMATURE` deserves a note: it's a constraint that blends *multiple* bone targets with weights, and it's how Rigify does its parent-switching without `CHILD_OF` hierarchy churn. Verified present in Rigify's generated output (one of its 10 constraint types).

### 8.2 IK/FK chains and switching

A two-bone IK limb, minimum viable:

```python
pb = rig.pose.bones["forearm.L"]            # the LAST bone of the chain
c = pb.constraints.new('IK')
c.target = rig
c.subtarget = "hand_ik.L"                   # the control
c.pole_target = rig
c.pole_subtarget = "elbow_pole.L"
c.pole_angle = math.radians(-90)            # depends on roll; see below
c.chain_count = 2                           # upperarm + forearm
c.use_tail = True
c.use_stretch = False                       # True only if you want stretchy limbs
```
`pole_angle` is the thing that goes wrong. It's the rotation of the solve plane about the root→tip axis, and the correct value depends on the chain's bone rolls. **Compute it, don't guess:** solve the chain with `pole_angle=0`, read the resulting `upperarm.L` world matrix, measure the signed angle between its local X and the desired bend direction, and set `pole_angle` to the negative of that. Then re-evaluate and assert the chain's bend direction matches within 1°. This is a loop forge can run blind, and it's one of the few places where an automated fix is strictly better than a human guessing in the viewport.

**IK/FK switching** is a float property on a control bone plus `COPY_TRANSFORMS` constraints driven by it:

```python
# custom property on the limb's parent control
rig.pose.bones["thigh_parent.L"]["IK_FK"] = 0.0      # 0 = IK, 1 = FK
# the DEF/ORG bone gets two COPY_TRANSFORMS, to the IK result and the FK bone
c_ik = org.constraints.new('COPY_TRANSFORMS'); c_ik.subtarget = "thigh_ik_result.L"
c_fk = org.constraints.new('COPY_TRANSFORMS'); c_fk.subtarget = "thigh_fk.L"
# drive c_fk.influence from the property
d = c_fk.driver_add("influence").driver
d.type = 'AVERAGE'
var = d.variables.new(); var.type = 'SINGLE_PROP'
var.targets[0].id = rig
var.targets[0].data_path = 'pose.bones["thigh_parent.L"]["IK_FK"]'
```
`driver_add` verified available on ID types. This is exactly Rigify's pattern, and if forge uses Rigify it gets 114 of these for free. **Only hand-build this for non-humanoid rigs Rigify doesn't cover.**

### 8.3 Foot roll / reverse foot

The reverse-foot rig, which is what every game engine expects:

```
foot_ik.L                      ← the animator's control
└── roll_heel.L                ← pivot at the heel, rotates X+ for heel-down roll
    └── roll_toe.L             ← pivot at the toe tip, rotates X− for toe-up roll
        └── roll_ball.L        ← pivot at the ball, rotates X− for ball roll
            └── foot_ik_tgt.L  ← what the leg IK actually targets
```
Drive the three roll bones from a single `foot_roll` property on `foot_ik.L` with `LIMIT_ROTATION`-clamped drivers: negative values drive the heel, positive values drive ball-then-toe in sequence. Pivot placement comes from the mesh: heel = the most −Y vertex of the foot region's bounding box at min Z; toe = most +Y at min Z; ball = the foot's width-maximum cross-section, typically 70–75% along the heel→toe axis. **All three are computable from the foot vertex group's geometry, blind.** That's a tool: `control_foot_roll(side, auto_pivots=True)`.

`FLOOR` constraint is the alternative for ground contact, but it fights hand-keyed animation. Use the reverse-foot.

### 8.4 Twist / roll bones

The forearm problem: rotating the wrist 180° about the forearm's axis with a single bone makes the whole forearm twist from the elbow, which looks like a wrung towel (candy-wrapper, the other direction).

The fix: two or more twist bones distributing the rotation.
```python
tw = rig.pose.bones["forearm_twist.L"]
c = tw.constraints.new('COPY_ROTATION')
c.target, c.subtarget = rig, "hand.L"
c.use_x = c.use_z = False; c.use_y = True       # Y is along the bone — the twist axis
c.target_space = c.owner_space = 'LOCAL'
c.influence = 0.5                               # 0.5 for one twist bone, 0.33/0.67 for two
```
**This requires correct roll.** `use_y=True` copies the component about the bone's local Y, and if the twist bone's roll differs from the parent's, you copy a diagonal. §5.3's rule — twist bones copy their parent's roll exactly — exists for this constraint.

UE5's mannequin has two twist bones per segment, as leaf siblings (§4.2). For UE5 export, forge must generate `upperarm_twist_01_l` / `_02_l` at influence 0.33 / 0.67 and weight them, or the engine-side skeleton is incomplete.

Thighs get the same treatment for hip rotation. Rigify handles it with `DEF-upper_arm.L` / `DEF-upper_arm.L.001` pairs (visible in the verified DEF list in §6.5 — every limb segment is split in two).

### 8.5 Spine, neck, head, fingers, tails

- **Spine, FK chain**: simplest, most predictable, animator-hostile for arcs. Five bones, each a child of the last, `LIMIT_ROTATION` per bone.
- **Spine, bendy-bones (B-Bones)**: `bone.bbone_segments = 8`, plus `bbone_handle_type_start/end = 'TANGENT'` and `bbone_custom_handle_start/end` pointing at control bones. Gives a genuinely smooth curve from two controls. **Does not export** — the subdivision is a deform-time feature. For export you need the DEF chain weighted and the B-Bone segments baked, which Blender's exporters do *not* do. **So: B-Bones are authoring-side only.**
- **Spine, Spline IK**: `SPLINE_IK` constraint with a Bezier/NURBS curve target, `chain_count` bones, `y_scale_mode` to control stretching. Controls are hooked to the curve's control points. **Best option for tails, tentacles, ropes, necks on long-necked creatures.** Also does not export as a constraint — bake it.
- **Neck/head**: 2–3 bone chain, head control with a `COPY_ROTATION` from a world-space "head_look" bone at adjustable influence so the head can isolate from the spine. Rigify's `spines.super_head` does this.
- **Fingers**: full hierarchy is metacarpal (optional) → proximal → intermediate → distal, ×5. The curl driver is one property per finger driving `rotation_euler[0]` on all three segments with scaled factors (proximal 1.0, intermediate 1.2, distal 0.9 is a reasonable natural curl):
  ```python
for seg, k in (("f_index.01.L", 1.0), ("f_index.02.L", 1.2), ("f_index.03.L", 0.9)):
    d = rig.pose.bones[seg].driver_add("rotation_euler", 0).driver
    d.type = 'SCRIPTED'
    d.expression = f"curl * {k} * 1.5708"            # 1.5708 rad = 90 deg at curl == 1.0
    v = d.variables.new()
    v.type = 'SINGLE_PROP'
    v.name = 'curl'
    v.targets[0].id = rig
    v.targets[0].data_path = 'pose.bones["hand_ik.L"]["index_curl"]'
  ```
  Plus a `spread` property driving `rotation_euler[2]` on the proximal only. Rigify's `limbs.super_finger` gives this plus per-segment tweak controls.
- **Pose libraries**: Blender 4.x+ uses Asset Browser pose assets (Actions marked as assets). Headless, forge can create an `Action` with keyframes on the control bones and `action.asset_mark()`. Low priority — an animator can make their own.

### 8.6 Deform vs control separation, and what export needs

Rigify's naming is the convention to adopt, verified from the generated rig:

| prefix | count in human rig | role | exported? |
|---|---|---|---|
| `DEF-` | 160 | **the only bones with skin weights** | **yes** |
| `ORG-` | 159 | copies of the metarig, the glue layer | no |
| `MCH-` | 167 | mechanism bones — IK targets, pole math, parent switches | no |
| (unprefixed) | 220 | animator controls, with custom shapes | no (unless keyed) |

So a 706-bone authoring rig exports as a **160-bone deform skeleton**. That ratio — roughly 4.4:1 — is normal and healthy.

**forge's rule: weights are only ever assigned to bones in the `DEF` bone collection.** The validator checks this: any vertex group whose name isn't a DEF bone is a bug.

For a target-skeleton export (UE5, Mixamo, VRM), the DEF set must have the *target's* names and hierarchy, which is why §4's tables exist and why `skeleton_rename_convention` exists.

### 8.7 What survives export — be explicit

**glTF 2.0:**

| authoring feature | survives? |
|---|---|
| skeleton hierarchy + rest transforms | **yes** |
| skin weights | **yes**, as `JOINTS_n`/`WEIGHTS_n` |
| shape keys / morph targets | **yes** (`export_morph=True`, with `export_morph_normal`, `export_morph_tangent`) |
| baked animation on bones | **yes** |
| **bone constraints (IK, Copy Rotation, Spline IK, Armature, …)** | **NO.** Not represented in glTF at all. |
| **drivers** | **NO.** |
| **B-Bone segments** | **NO.** The curve is lost; you get straight bones. |
| **custom shapes / widgets** | **NO** (they're separate mesh objects; they'd export as meshes if not filtered). |
| **bone collections, colors, UI** | **NO.** |
| **IK/FK switch properties** | **NO.** |

So: **constraints and drivers are authoring-side only. The only things that cross the export boundary are the skeleton, the skin, and morph targets.** Anything a constraint does must be *baked into keyframes* before export, or recreated engine-side.

Verified glTF exporter options relevant to rigging (`bpy.ops.export_scene.gltf`):
```
export_skins              = True     # emit the skin at all
export_def_bones          = False    # ← set True: export ONLY bones in a "DEF" collection
export_influence_nb       = 4        # weights per vertex
export_all_influences     = False    # ← True emits JOINTS_1/WEIGHTS_1 for >4 influences
export_leaf_bone          = False    # add a terminal leaf bone per chain
export_hierarchy_flatten_bones = False
export_armature_object_remove  = False
export_rest_position_armature  = True   # export the rest pose, not the current pose
export_reset_pose_bones        = True
export_morph              = True
export_morph_normal       = True
export_morph_tangent      = False
export_yup                = True     # Z-up Blender → Y-up glTF
export_animations         = True
export_animation_mode     = 'ACTIONS'   # ACTIONS | ACTIVE_ACTIONS | BROADCAST | NLA_TRACKS | SCENE
export_force_sampling     = True     # bake constraint-driven motion. REQUIRED if constraints drive bones.
export_bake_animation     = False
export_optimize_animation_size = True
export_apply              = False    # apply modifiers (will apply the Armature mod too — leave False)
```

**`export_def_bones=True` is the single most important flag.** It exports only bones in a collection named `DEF`, which is exactly Rigify's deform set, which is exactly what forge should produce. Without it you ship 706 bones to a game engine.

**`export_force_sampling=True` is the second most important.** Constraint-driven motion is only captured if the exporter samples the evaluated pose per frame rather than reading keyframes. Default is already True; never set it False on a constrained rig.

**glTF's 4-weight limit, precisely:** the spec stores skinning as `vec4` attribute sets. A single `JOINTS_0`/`WEIGHTS_0` pair caps at four influences; additional `JOINTS_n`/`WEIGHTS_n` sets carry influences 4–7, 8–11, etc. So glTF itself is *not* hard-capped at four per vertex. **But a conformant viewer is only required to support four and may ignore the rest**, and real consumers do: Unreal Engine's Interchange glTF importer truncates skeletal-mesh bone influences to 4 per vertex (open UE issue). **So forge's practical rule: `vertex_group_limit_total(limit=4)` then normalize, and leave `export_all_influences=False`.** Report it in the rig report so Claude knows weights were quantized.

**glTF joint count per primitive:** the spec sets no hard limit, but `JOINTS_n` is typically `u8` (256 joints) or `u16` (65,536). Blender picks the narrowest that fits. Real-world limits come from the consumer's shader uniform budget — many mobile/WebGL renderers cap at 64–256 bones per draw call. **forge should warn above 128 joints on a single mesh primitive** and suggest splitting.

**FBX**, verified options (`bpy.ops.export_scene.fbx`):
```
add_leaf_bones           = True      # ← SET TO FALSE. Adds a "_end" bone per chain; breaks UE import.
primary_bone_axis        = 'Y'       # ← 'X' for UE5
secondary_bone_axis      = 'X'       # ← '-Z' for UE5  (classic UE/Blender axis pair)
use_armature_deform_only = False     # ← SET TO TRUE. Exports only bones with weights.
armature_nodetype        = 'NULL'    # 'NULL' is correct for UE; 'ROOT'/'LIMBNODE' for other DCCs
apply_scale_options      = 'FBX_SCALE_NONE'
global_scale             = 1.0
axis_forward             = '-Z'
axis_up                  = 'Y'
bake_anim                = True
```
`add_leaf_bones=True` is the default and it is **wrong for every game engine**. It appends a zero-length `bonename_end` to every chain tip; UE imports them as real bones and every retarget asset then has extra entries. Always `False`.

`use_armature_deform_only=True` is FBX's equivalent of `export_def_bones` — it filters to bones that actually have weights. Slightly different semantics (weight-based, not collection-based) but the same effect if forge's invariant from §8.6 holds.

### 8.8 Corrective shape keys and drivers

The shoulder is the hard joint. No weight distribution fixes a deltoid; you need a corrective.

```python
sk = mesh.shape_key_add(name="shoulder_up.L", from_mix=False)
# sculpt/offset the verts in sk.data
d = sk.driver_add("value").driver
d.type = 'SCRIPTED'
d.expression = "max(0, (rot - 0.3) / 1.2)"     # ramps in from ~17° to ~86°
v = d.variables.new(); v.type = 'TRANSFORMS'
v.targets[0].id = rig
v.targets[0].bone_target = "upper_arm.L"
v.targets[0].transform_type = 'ROT_Z'
v.targets[0].transform_space = 'LOCAL_SPACE'
v.targets[0].rotation_mode = 'SWING_TWIST_Y'
```
`TRANSFORMS` driver variables with `rotation_mode='SWING_TWIST_*'` are the correct way to read a bone's bend angle without gimbal nonsense. This matters: `ROT_Z` with Euler mode gives discontinuous values past 90°.

**Shape keys and their drivers DO export to glTF as morph targets + animation** (`export_morph=True`), but the *driver relationship* does not — the morph exists, nothing drives it. For a game engine the corrective must be reconstructed engine-side (UE's `PoseAsset` / `Pose Driver` node, Unity's blend-shape script). **Report in the rig report which correctives exist and what drives them**, so the downstream consumer can rebuild it. That's a table of (shape key name, driver bone, transform type, ramp range) — maybe 10 lines of output.

Cheaper alternative that needs no sculpting: `CORRECTIVE_SMOOTH` (§7.4) scoped to a `joints` vertex group. It won't give you a deltoid but it will stop the crease.

### 8.9 Non-humanoid rigs

| subject | approach |
|---|---|
| **quadruped** | `armature_basic_quadruped_metarig_add()`, or the `horse`/`wolf`/`cat` metarigs. The digitigrade hind leg needs a 3-segment IK (femur, tibia, metatarsus) with the IK on the metatarsus and a `DAMPED_TRACK` hock control. Rigify's `limbs.rear_paw` rig type handles this. |
| **bird** | `armature_bird_metarig_add()`. Wings are `limbs.simple_tentacle` or arm chains; the key extra is feather chains, which are best done as a `SPLINE_IK` per primary group rather than per feather. |
| **tail / tentacle** | `SPLINE_IK` on a bone chain, with a NURBS curve whose control points are hooked to 3–5 control bones. `y_scale_mode='BONE_ORIGINAL'` to stop stretching. Or Rigify's `limbs.simple_tentacle` for an FK chain with tweaks. Bake before export. |
| **snake / rope** | same as tentacle, longer chain, `chain_count` = all of it. |
| **vehicle** | body bone + 4 wheel bones with `COPY_ROTATION` from a driver on forward distance travelled, plus suspension as `LIMIT_LOCATION`-clamped children with `SHRINKWRAP` to the ground mesh. Pure constraint work, no skinning subtlety. |
| **mechanical / piston** | `IK` with `chain_count=1` plus `DAMPED_TRACK` gives a two-part piston that always points at its target. `STRETCH_TO` for telescoping. Hard-rigid, so weights are 1.0 per part — use `ARMATURE_NAME` binding and assign each part's vertex group manually by connected component. **This is the easy case and forge should detect it**: if the mesh has N disconnected components and N bones, assign component i to bone i at weight 1.0 and skip weight solving entirely. |
| **prop, two-bone** | root + one bone. `ARMATURE_NAME` bind, weight everything to the single bone at 1.0. Done. |
| **anything with no template** | `skeletor` curve skeleton → prune short branches → classify the longest path as the "spine" → branches as limbs → generic FK chains with optional IK on branches longer than 3 bones. Quality will be mediocre but it will be riggable, and the heatmap render lets Claude iterate. |

---

## 9. FACIAL RIGGING

### 9.1 The two approaches

- **Blendshape / morph-target faces**: 52 ARKit shapes or a FACS subset, sculpted or generated. **Exports to glTF and FBX.** Works with ARKit/LiveLink face capture out of the box. This is what forge should default to for any face that needs to be consumed by an engine or a VTuber pipeline.
- **Bone-based faces**: jaw, eyes, and a lattice of small bones for brows/lips/cheeks. Rigify's `faces.super_face` generates this (visible in the verified DEF list: `DEF-jaw`, `DEF-lip.T.L`, `DEF-brow.B.L.001`, `DEF-lid.T.L.002`, `DEF-cheek.T.L`, `DEF-tongue`, 88 face DEF bones out of 160 total). **Exports as a skeleton**, so it survives, and it's animatable in-engine without morph-target budget.
- **Hybrid** is what production does: bones for jaw/eyes/tongue (big rigid motions), blendshapes for everything else.

### 9.2 ARKit's 52 blendshapes — full list

`ARFaceAnchor.BlendShapeLocation`. These are the de facto standard: ARKit, Unreal LiveLink Face, VRM expression authoring, Ready Player Me, and most VTuber software all consume exactly these names.

**Eyes (14)**
```
eyeBlinkLeft        eyeBlinkRight
eyeLookDownLeft     eyeLookDownRight
eyeLookInLeft       eyeLookInRight
eyeLookOutLeft      eyeLookOutRight
eyeLookUpLeft       eyeLookUpRight
eyeSquintLeft       eyeSquintRight
eyeWideLeft         eyeWideRight
```
**Brows (5)**
```
browDownLeft        browDownRight
browInnerUp
browOuterUpLeft     browOuterUpRight
```
**Cheeks (3)**
```
cheekPuff
cheekSquintLeft     cheekSquintRight
```
**Nose (2)**
```
noseSneerLeft       noseSneerRight
```
**Jaw (4)**
```
jawForward
jawLeft             jawRight
jawOpen
```
**Mouth (23)**
```
mouthClose
mouthFunnel         mouthPucker
mouthLeft           mouthRight
mouthSmileLeft      mouthSmileRight
mouthFrownLeft      mouthFrownRight
mouthDimpleLeft     mouthDimpleRight
mouthStretchLeft    mouthStretchRight
mouthRollLower      mouthRollUpper
mouthShrugLower     mouthShrugUpper
mouthPressLeft      mouthPressRight
mouthLowerDownLeft  mouthLowerDownRight
mouthUpperUpLeft    mouthUpperUpRight
```
**Tongue (1)**
```
tongueOut
```
**Total: 14 + 5 + 3 + 2 + 4 + 23 + 1 = 52.**

Gotchas: `mouthLeft`/`mouthRight` and `jawLeft`/`jawRight` are **directional**, not per-side — `mouthLeft` means "mouth shifted to the subject's left", and only one of the pair is ever nonzero. Every other `*Left`/`*Right` pair is per-side and both can fire at once. `browInnerUp` is **single**, not paired, which trips up anyone generating names programmatically from a `[Left, Right]` product. *(Cross-checked against two independent reproductions of Apple's grouping; I could not scrape the live Apple page, which returned title-only. Counts and grouping agree in both. Flagged as high-confidence but not first-party-verified.)*

### 9.3 FACS action units

ARKit's 52 are roughly a subset/recombination of FACS AUs. The mapping matters when forge is asked for "FACS" rather than ARKit. Key correspondences: AU1 inner brow raiser → `browInnerUp`; AU2 outer brow raiser → `browOuterUp*`; AU4 brow lowerer → `browDown*`; AU5 upper lid raiser → `eyeWide*`; AU6 cheek raiser → `cheekSquint*`; AU7 lid tightener → `eyeSquint*`; AU9 nose wrinkler → `noseSneer*`; AU12 lip corner puller → `mouthSmile*`; AU15 lip corner depressor → `mouthFrown*`; AU17 chin raiser → `mouthShrugLower`; AU18 lip pucker → `mouthPucker`; AU20 lip stretcher → `mouthStretch*`; AU22 lip funneler → `mouthFunnel`; AU23 lip tightener → `mouthPress*`; AU25/26/27 lips part / jaw drop / mouth stretch → `jawOpen` + `mouthClose`; AU43 eyes closed → `eyeBlink*`. **FACS has no eye-gaze AUs** — ARKit's 8 `eyeLook*` shapes have no FACS equivalent and are a pose, not an expression.

### 9.4 VRM expression presets

VRM 1.0 defines preset expressions distinct from blendshapes: `happy`, `angry`, `sad`, `relaxed`, `surprised`, `aa`, `ih`, `ou`, `ee`, `oh`, `blink`, `blinkLeft`, `blinkRight`, `lookUp`, `lookDown`, `lookLeft`, `lookRight`, `neutral`. The five vowels (`aa ih ou ee oh`) are Japanese-phoneme visemes, which is a different decomposition from ARKit's muscle-based shapes. **An ARKit-driven face does not give you VRM visemes for free** — you compose them: `aa` ≈ `jawOpen 0.7 + mouthFunnel 0.2`, `ih` ≈ `mouthStretch* 0.6 + jawOpen 0.15`, `ou` ≈ `mouthPucker 0.8 + jawOpen 0.3`, `ee` ≈ `mouthSmile* 0.5 + mouthStretch* 0.4`, `oh` ≈ `mouthFunnel 0.7 + jawOpen 0.5`. forge should emit these as VRM expression bindings referencing the ARKit morph targets, which is legal and standard.

### 9.5 Generating a face rig procedurally

Blind, from geometry, in order of reliability:

1. **Jaw bone.** Highly reliable. The jaw pivot is just below and behind the ear canal. Find it from the head region's bounding box: `y ≈ bbox.y_min + 0.25 * depth`, `z ≈ bbox.z_min + 0.35 * height`, `x = 0`. Bone points forward-down to the chin (the most +Y, lowest-Z vertex of the head). Weight the lower face to it with a smooth falloff from a horizontal plane through the mouth corners.
2. **Eye bones.** Reliable *if* the eyes are separate mesh components (usually they are — eyeballs are almost always separate spheres). Detect spherical components inside the head bbox, fit sphere centres, place one bone per eye from the centre pointing forward. Weight each eyeball 1.0 to its bone. If the eyes are welded into the head mesh, this fails and should be reported, not faked.
3. **Tongue.** Separate component, inside the mouth cavity. 2–3 bone chain along its length.
4. **Blendshape generation.** This is the honest limit. forge *cannot* sculpt 52 anatomically correct face shapes from a bare mesh. What it *can* do:
   - **Generate the 52 shape key slots, empty**, correctly named, so the file is ARKit-schema-valid and a human or another tool can fill them.
   - **Generate the ~14 mechanically derivable ones** from bone motion: `jawOpen`, `jawLeft/Right/Forward` (bake the jaw bone's pose into a shape key), `eyeBlinkLeft/Right` (lid vertices translated along the eyeball's surface — needs lid vertex groups, which needs landmark detection on the head), `eyeLookUp/Down/In/OutLeft/Right` (pure eyeball bone rotation — these should be *bone-driven, not blendshapes*, and ARKit consumers accept that).
   - **Report the rest as `pending`**, with a count. "52 ARKit slots created, 14 generated from bone motion, 38 empty" is an honest, actionable line in the rig report.
   - **If the mesh came from a generator with a known topology** (SMPL-X, a Rigify-face-compatible head, a MetaHuman-derived mesh), transfer shapes from a template using `data_transfer` on the shape-key deltas. This is the only path to a full 52 and it depends entirely on the geometry researcher's output having known topology. **Flag this as a seam (§13).**
5. **Rigify's face.** `faces.super_face` gives 88 face DEF bones and a full control lattice, placed from the metarig's face bones. If the metarig's face bones can be fitted to landmarks (brow line, eye corners, lip contour, nose tip, ear positions — all detectable as curvature extrema on a head mesh), **this is a better face rig than empty blendshapes and it exports as a skeleton.** Harder to fit than the body; the metarig has ~70 face bones to place. **Recommended for v2, not v1.**

---

## 10. VERIFICATION WITHOUT EYES

Claude cannot look at a rig. So the rig has to describe itself in numbers and pictures.

### 10.1 Static checks

All cheap, all run on every validate call.

| check | method | fail condition |
|---|---|---|
| zero-length bones | `(bone.tail_local - bone.head_local).length` | `< 1e-5` |
| bones with no weighted verts | §7.8 dead-bone check, restricted to the DEF collection | any DEF bone with 0 verts above 1e-4 |
| vertices with no weight | `(W.sum(1) < 1e-6).sum()` | `> 0` |
| partition of unity | `W.sum(1)` min/max | outside `[0.999, 1.001]` |
| max influences | `(W>1e-6).sum(1).max()` | `> 4` when target is glTF |
| mirrored-name mismatch | for every bone matching a side pattern, assert the opposite exists | any unpaired sided bone |
| mirrored-transform mismatch | §5.4 check 1 | `> 2°` |
| bone roll outliers | §5.4 checks 2 and 3 | hinge alignment `< 0.95`, chain `Δroll > 15°` |
| non-uniform armature scale | `armature_obj.scale` | `max/min > 1.0001`, or any component `≠ 1.0` |
| non-identity armature rotation | `armature_obj.rotation_euler` | any component `≠ 0` |
| root at origin | `bones[root].head_local` | `length > 1e-5` |
| armature/mesh transform mismatch | `mesh.matrix_world` vs `armature.matrix_world` | not equal |
| scale, per target | bbox height in metres | humanoid outside 1.4–2.1 m → warn |
| up axis, per target | Blender Z-up; glTF needs `export_yup=True`; FBX needs `axis_up='Y'` | flag if exporter settings don't match target |
| deform-set purity | every vertex group name is a bone in the `DEF` collection | any group that isn't |
| orphan modifiers | Armature modifier `object` is set and points at the right armature | missing or wrong |
| negative-determinant bones | `bone.matrix_local.determinant()` | `< 0` (mirrored basis; breaks retargeting) |

### 10.2 Dynamic checks — the pose battery

Pose the rig, measure the mesh. Verified that this works and that the numbers are meaningful.

**The battery** (per side where applicable, so ~20 poses total):

| pose | bone | rotation |
|---|---|---|
| arm raised | `upper_arm.L` | +180° about the bend-plane normal's perpendicular (overhead) |
| arm forward | `upper_arm.L` | +90° forward |
| arm across chest | `upper_arm.L` | −60° (adduction past the torso) |
| elbow bent | `forearm.L` | 135° |
| elbow full | `forearm.L` | 170° |
| forearm twist | `hand.L` | ±180° about local Y |
| wrist flex/extend | `hand.L` | ±70° |
| knee bent | `shin.L` | 130° |
| hip flexion | `thigh.L` | +120° |
| hip split | `thigh.L` | ±85° abduction |
| hip rotation | `thigh.L` | ±45° about local Y |
| ankle | `foot.L` | ±40° |
| head turn | `head` | ±80° about Z |
| head nod | `head` | ±45° |
| spine twist | `spine_03` | ±60° about Y |
| spine bend | `spine_02` | ±45° forward, ±30° side |
| fingers curled | all finger bones | 90° |
| crouch | `thigh` + `shin` + `foot` | a compound pose |
| rest | — | identity, as the control |

**Metrics per pose**, all verified computable:

```python
dg = bpy.context.evaluated_depsgraph_get()
bm = bmesh.new(); bm.from_object(mesh, dg)        # evaluated = post-armature-modifier
tree = mathutils.bvhtree.BVHTree.FromBMesh(bm)
overlap_pairs = len(tree.overlap(tree))           # self-intersection
volume = bm.calc_volume(signed=True)
```

- **Self-intersection count.** Measured, on a 3,840-vert cylinder with a 2-bone chain:
  ```
  rest (0°):   0 pairs   volume 0.24492
  45°:        20 pairs   volume 0.24183
  90°:       193 pairs   volume 0.23437
  135°:      297 pairs   volume 0.22691
  170°:      474 pairs   volume 0.22398
  ```
  Monotone, 0.06 s per evaluation. **Report the raw count and the delta over rest.** A good rig on a well-topologized mesh keeps the 135° elbow under ~0.5% of face count. Report it as a fraction of faces so it's mesh-size-independent.
- **Volume loss.** `posed_volume / rest_volume`. Measured 0.9528 at a 120° bend on a 2-bone cylinder with bone-heat weights. **Target: > 0.92 at 135°.** Below 0.85 means the joint is collapsing — usually too-sharp a weight falloff or not enough edge loops. This is the single best scalar for "is this rig pinching".
- **Normal flips.** Compare each face's posed normal to its rest normal in the bone's local frame; count faces where `dot < 0`. A flipped normal means the mesh has turned inside out locally. **Any nonzero count is a failure.**
- **Vertex velocity outliers.** Interpolate the pose in 10 steps; per vertex compute the max frame-to-frame displacement. Report the 99.9th percentile and the ratio to the median. A ratio above ~20 means some vertices are snapping — usually a weight discontinuity or a vertex weighted to the wrong bone.
- **Candy-wrapper detection** on twist joints. For the forearm-twist pose: take cross-sections perpendicular to the bone at 10 stations along it, compute each section's area and its centroid-to-hull radius. **Candy-wrapper = a monotone collapse in area toward the middle of the segment.** Report `min(section_area) / rest_section_area_at_same_station`. Below 0.6 is a wrap. This is the test that justifies twist bones, and it's the test that `use_deform_preserve_volume` (§7.5) passes trivially and LBS fails — which is exactly why you run it with DQS off.
- **Joint gap / interpenetration at the armpit and crotch.** For the arm-across-chest and hip-adduction poses, measure the minimum distance between the limb's surface and the torso's surface using `igl.signed_distance` on a sampled subset. Negative means penetration. Report the min.

### 10.3 Render-based verification — **the highest-value tool**

Claude can't look at a rig, but Claude can look at a picture. Verified working, CPU-only, sub-second.

**Weight heatmap render.** Verified end to end:

```python
# bake one bone's weights to a point-domain color attribute
ca = mesh.data.color_attributes.new(name="forge_wmap", type='FLOAT_COLOR', domain='POINT')
w = weights_for_bone(bone_name)                       # (nv,) in [0,1]
cols = np.zeros((nv, 4))
cols[:, 0] = w                                        # R  = weight
cols[:, 1] = 1 - np.abs(2*w - 1)                      # G  = mid-band (turbo-ish)
cols[:, 2] = 1 - w                                    # B  = inverse
cols[:, 3] = 1.0
ca.data.foreach_set("color", cols.ravel())
mesh.data.color_attributes.active_color = ca

scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'FLAT'                  # no shading, pure color
scene.display.shading.color_type = 'VERTEX'           # read the color attribute
scene.render.film_transparent = True
cam.data.type = 'ORTHO'                               # orthographic = no perspective lies
```
**Measured: 400×400 PNG in 0.87 s. 512×512 in 0.71 s.** No GPU, no X server.

`display.shading.color_type` verified options: `MATERIAL`, `OBJECT`, `RANDOM`, `VERTEX`, `TEXTURE`, `SINGLE`. `display.shading.light` verified options: `STUDIO`, `MATCAP`, `FLAT`.

**Why this is the key tool:** a weight heatmap tells Claude everything the numbers can't. Is the falloff centred on the joint or offset? Is it symmetric? Does it bleed onto the torso? Is the gradient smooth or blotchy? Does the hand bone own a patch of thigh? One 400×400 PNG per bone answers all of that, and a 4-up contact sheet of (front, back, left, right) for a named bone is maybe 60 KB.

**Design the tool as:**
- `rig_render_weights(bone, views=["front","side"], size=400)` → a small PNG per view. Default to a 2×2 montage in one image to save round-trips.
- **Dual-bone mode**: render two bones as R and G channels so Claude sees the blend band between them directly. Yellow = both, which is exactly where the joint should be.
- **"All bones" mode**: assign each bone a distinct hue and render the argmax bone per vertex. One image shows the whole weight partition. **This is the fastest way to spot a bone owning the wrong limb** and it should be the first render in any rig report.
- **Delta mode**: render `|W_before − W_after|` after a weight edit, so Claude can confirm an edit did what it intended and nothing else.

**Pose renders.** Same machinery, `color_type='SINGLE'`, `light='STUDIO'`, fixed ortho cameras at front/side/three-quarter. One render per battery pose, 0.7 s each, ~20 poses = 14 s for the full sheet. **Emit as a contact sheet, not 20 separate images.** Put the pose name and the numeric metrics (overlap count, volume ratio) into the report text beside the image, not burned into the pixels.

**Camera setup that doesn't lie:** orthographic, `ortho_scale` set to 1.1 × the mesh's bbox diagonal, positioned on the axis, `film_transparent=True`. Perspective cameras make a limb's foreshortening look like volume loss.

### 10.4 The scored rig report

Target: **under 400 tokens**, readable, actionable. Something like:

```
RIG REPORT  handle=rig_7f3a  armature=metarig_gen  mesh=char_body
score 78/100  (skeleton 92, weights 71, controls 85, export 60)

SKELETON   87 bones | 42 DEF | root at origin ok | scale 1.0 uniform ok
  roll: hinge-align min 0.97 (thigh.R) ok | chain max dRoll 4.2deg ok
  mirror: 21/21 pairs, max transform delta 0.3deg ok
WEIGHTS    partition 0.9994-1.0001 ok | max influences 4 ok | orphans 0 ok
  FAIL  bone island count: DEF-hand.L owns 2 regions (1842 + 61 verts)
  WARN  geodesic mismatch: 61 verts weighted to DEF-hand.L are 0.41m away
        (sample verts 8812 9041 9102 9377 9412)
  WARN  gradient: 14 edges with weight jump > 0.4, worst 0.71 at edge 21043
POSES      20/20 evaluated
  worst volume ratio 0.887 @ knee_bent_135 (DEF-shin.L)  [target >0.92]
  worst overlap 1.8% of faces @ hip_split_85  [target <0.5%]
  candy-wrapper forearm_twist_180: section area ratio 0.52  FAIL
  normal flips 0 ok | velocity outlier ratio 7.2 ok
CONTROLS   ik chains 4 | foot rolls 2 | finger drivers 10 | ikfk switches 4
EXPORT     gltf: 42 joints, 4 influences ok, 0 constraints exported (31 baked)
  WARN  2 shape-key drivers will not survive glTF (shoulder_up.L, shoulder_up.R)

NEXT  1) DEF-hand.L stray island -> weight_clear_island(bone="DEF-hand.L", keep="largest")
      2) forearm twist -> control_add_twist(segment="forearm.L", count=2)
      3) knee volume -> weight_smooth(bone="DEF-shin.L", region="joint", factor=0.4)
```
Every WARN/FAIL names the tool that fixes it. That's what makes the report actionable rather than decorative.

Scoring: weight each category, subtract per failure. Keep the formula in the code and *documented*, so Claude can predict whether a fix will move the score. An opaque score is useless.

---
## 11. MCP TOOL SURFACE — RIGGING

Design rules, applied throughout:
- **Handles, never data.** Every tool returns short opaque IDs (`rig_7f3a`, `ckpt_02`) and numbers. No vertex arrays, no bone lists longer than ~50 entries, no matrices.
- **Names, never indices**, for anything Claude authors. Bone names and vertex-group names are stable; indices are not.
- **Every mutating tool returns what changed**, as counts.
- **Every tool that can fail reports `ok: bool` plus a short `reason`**, never a traceback.
- **Images are returned as artifact references**, not base64 inline, unless the host requires inline.

```python
# ============================================================================
# forge.rigging — MCP tool surface
# All coordinates are Blender-native: Z-up, right-handed, metres.
# All bone names are Blender convention (.L/.R) until export_* is called.
# ============================================================================

from typing import Literal, Optional, Any

Vec3 = tuple[float, float, float]
Handle = str                       # opaque, e.g. "rig_7f3a"
SkeletonTemplate = Literal[
    "humanoid_basic",              # 23 bones: root, pelvis, 3 spine, neck, head, 2x(clav,arm,fore,hand), 2x(thigh,shin,foot,toe)
    "humanoid_full",               # + 5-finger hands, twist bones, breast/pelvis helpers
    "rigify_human",                # Rigify metarig, 159 bones incl. face + fingers
    "rigify_human_basic",          # Rigify metarig, no face
    "rigify_quadruped", "rigify_bird", "rigify_cat", "rigify_horse",
    "rigify_wolf", "rigify_shark",
    "mixamo", "ue5", "vrm1", "unity_humanoid", "smplx",
]
NamingConvention = Literal["blender", "ue", "mixamo", "vrm", "smplx", "snake_lr"]
WeightMethod = Literal["bone_heat", "bbw", "biharmonic", "geodesic_voxel",
                       "envelope", "rigid_components", "transfer"]


# ---------------------------------------------------------------------------
# SKELETON CREATION
# ---------------------------------------------------------------------------

def skeleton_from_template(
    mesh: Handle,
    template: SkeletonTemplate,
    fit: Literal["none", "bbox", "landmarks"] = "landmarks",
    pose_hint: Literal["auto", "tpose", "apose"] = "auto",
    pre_bend_deg: float = 5.0,
    name: Optional[str] = None,
) -> dict:
    """Create a skeleton for `mesh` from a named template.

    `fit="landmarks"` runs the full pipeline: symmetry-plane fit, geodesic
    extremity detection, medial-axis centreline, then constrained least-squares
    placement of every template bone (hard constraints at hands/feet/head,
    soft objectives for centreline adherence, mirror symmetry, and bone-length
    ratio regularization; a barrier keeps every joint inside the mesh).
    `fit="bbox"` scales the template to the mesh bounding box only -- fast,
    crude, use when landmark detection has already failed.
    `pre_bend_deg` puts a small bend in elbows and knees so bend-plane normals
    (and therefore bone rolls and IK pole angles) are well defined.

    Returns:
      {ok, rig: Handle, bone_count: int, deform_count: int,
       landmarks: {name: Vec3},            # <= 12 entries
       landmark_confidence: {name: float}, # 0..1 per landmark
       symmetry_residual: float,           # mean mirror distance, metres
       fit_rms: float,                     # metres, joints vs centreline
       warnings: list[str]}
    Does NOT bind the mesh. Call bind_mesh next.
    """

def skeleton_from_landmarks(
    mesh: Handle,
    landmarks: dict[str, Vec3],
    template: SkeletonTemplate = "humanoid_full",
    lock: list[str] = (),
) -> dict:
    """Same fit as above but with landmarks supplied by Claude instead of detected.
    Use after `mesh_detect_landmarks` when auto-detection got one wrong, or for
    meshes where geodesic extremity detection fails (no limbs, fused parts).
    `lock` names landmarks treated as hard constraints; the rest are soft.
    Returns: same shape as skeleton_from_template.
    """

def skeleton_create(
    name: str,
    bones: list[dict],
    collections: dict[str, list[str]] = None,
) -> dict:
    """Create a skeleton from scratch. Each bone dict:
      {name: str, head: Vec3, tail: Vec3, parent: str|None,
       connect: bool = False, deform: bool = True,
       roll: float|None = None, roll_align: Vec3|None = None}
    `roll_align` sets roll so the bone's local Z points as near that vector as
    possible (wraps EditBone.align_roll). `roll` sets the scalar directly.
    One of the two, not both.
    `collections` maps collection name -> bone names; nest with "parent/child".
    Returns: {ok, rig: Handle, bone_count, warnings}
    """

def mesh_detect_landmarks(mesh: Handle, expect: Literal["humanoid","quadruped","auto"] = "auto") -> dict:
    """Run landmark detection alone, without building a skeleton.
    Geodesic field from the mesh centroid, non-maximum suppression at 5% of the
    bbox diagonal, then classification by height / lateral offset / symmetry.
    Returns:
      {ok, landmarks: {name: Vec3}, confidence: {name: float},
       extremity_count: int,            # raw maxima found, before classification
       unclassified: list[Vec3],        # extremities that matched no slot (tails, wings)
       symmetry_plane: {normal: Vec3, offset: float, residual: float},
       up_axis: Vec3, forward_axis: Vec3,
       bbox: {min: Vec3, max: Vec3}, height_m: float,
       pose_guess: Literal["tpose","apose","other"]}
    """

def mesh_curve_skeleton(mesh: Handle, method: Literal["wavefront","contraction","teasar","mcs"] = "wavefront",
                        prune_ratio: float = 0.05) -> dict:
    """Extract a curve skeleton via `skeletor` for subjects with no template.
    `prune_ratio` drops branches shorter than that fraction of the longest path.
    Returns:
      {ok, node_count, edge_count, branch_count,
       longest_path_nodes: int,         # the "spine" candidate
       branches: [{root_node: int, length_m: float, node_count: int}],
       radius_min: float, radius_max: float}
    Feed into skeleton_from_curve to turn it into an armature.
    """

def skeleton_from_curve(mesh: Handle, segments_per_branch: int = 4,
                        name: str = "creature") -> dict:
    """Build an armature from mesh_curve_skeleton output: longest path becomes
    a spine chain, each branch becomes a limb chain, resampled to
    `segments_per_branch` bones. Rolls aligned to each branch's bend plane.
    Returns: same shape as skeleton_from_template.
    """


# ---------------------------------------------------------------------------
# BONE EDIT OPS
# ---------------------------------------------------------------------------

def bone_edit(rig: Handle, edits: list[dict]) -> dict:
    """Apply a batch of bone edits in one EDIT-mode round trip.
    Each edit dict is one of:
      {op:"move",      bone, head?: Vec3, tail?: Vec3}
      {op:"translate", bone, delta: Vec3, include_children?: bool}
      {op:"reparent",  bone, parent: str|None, connect?: bool}
      {op:"rename",    bone, to: str}
      {op:"add",       name, head, tail, parent?, connect?, deform?}
      {op:"delete",    bone, reparent_children?: bool}
      {op:"split",     bone, count: int}          # subdivide into N, names .001 etc
      {op:"roll",      bone, value?: float, align?: Vec3}
      {op:"deform",    bone, value: bool}
      {op:"collection",bone, assign?: list[str], unassign?: list[str]}
    Batching matters: each EDIT/OBJECT mode switch costs time and invalidates
    EditBone references, so one call with 50 edits beats 50 calls.
    Returns: {ok, applied: int, failed: [{index, reason}], bone_count}
    """

def skeleton_set_rolls(rig: Handle,
                       policy: Literal["forge_default","world_z","parent","bend_plane"] = "forge_default",
                       bones: Optional[list[str]] = None) -> dict:
    """Recompute bone rolls. `forge_default` applies the per-class policy:
    spine/neck local-Z toward -Y; limb segments local-Z along the limb's bend
    plane normal (cross of the two segment directions); hands/feet local-Z to
    +Z; fingers to the palm normal; twist bones copy their parent exactly;
    .R bones mirrored from .L.
    Returns:
      {ok, changed: int,
       hinge_alignment: {bone: float},   # |dot(bend_normal, bone.x_axis)|, only bones < 0.99
       degenerate: list[str],            # limbs with no definable bend plane
       max_change_deg: float}
    """

def skeleton_symmetrize(rig: Handle, direction: Literal["+X","-X"] = "+X",
                        copy_colors: bool = True, weights_too: bool = False) -> dict:
    """Mirror the named side onto the other, for bones and (optionally) weights.
    Requires Blender-convention .L/.R names -- call skeleton_rename_convention
    to "blender" first if the rig came in with UE or Mixamo names.
    Returns: {ok, bones_mirrored, groups_mirrored, unpaired: list[str]}
    """

def skeleton_rename_convention(rig: Handle, to: NamingConvention,
                               meshes: list[Handle] = (),
                               map_override: dict[str,str] = None) -> dict:
    """Rename every bone to the target convention, and rename in lockstep:
    vertex groups on every mesh in `meshes`, every constraint `subtarget`,
    every driver variable `bone_target`, and every custom-property data path.
    Missing any of those four leaves a rig that looks right and deforms nothing,
    so this tool is the only sanctioned way to rename.
    `map_override` forces specific pairs when the automatic map is wrong.
    Returns:
      {ok, renamed: int, groups_renamed: int,
       subtargets_fixed: int, drivers_fixed: int,
       unmapped: list[str],      # bones with no slot in the target convention
       missing_required: list[str]}  # target slots with no source bone
    """

def skeleton_retarget_names(rig: Handle, to: SkeletonTemplate,
                            meshes: list[Handle] = (),
                            strict: bool = False) -> dict:
    """Rename AND restructure toward a target skeleton spec: insert missing
    required bones (e.g. spine_04/spine_05 for UE5, chest for VRM), add the
    target's IK bones (ik_foot_root etc. for UE5), and emit the slot mapping.
    `strict=True` fails rather than inventing bones.
    Returns:
      {ok, mapping: {source_bone: target_slot},   # the map, <= 90 entries
       inserted: list[str], removed: list[str],
       missing_required: list[str], extra: list[str],
       hierarchy_ok: bool, hierarchy_problems: list[str]}
    """


# ---------------------------------------------------------------------------
# BINDING AND WEIGHTS
# ---------------------------------------------------------------------------

def bind_mesh(mesh: Handle, rig: Handle,
              method: WeightMethod = "bone_heat",
              deform_collection: str = "DEF",
              limit_influences: Optional[int] = None,
              normalize: bool = True,
              bbw_samples_per_bone: int = 12,
              tet_quality: float = 1.5,
              dual_quaternion: bool = False) -> dict:
    """Bind `mesh` to `rig` and compute skin weights.

    method:
      "bone_heat"        Blender ARMATURE_AUTO, heat equilibrium. ~0.6 s / 11k verts.
                         Default. Fails on non-manifold input; bleeds across
                         touching limbs.
      "bbw"              Bounded Biharmonic Weights. Tetrahedralizes the interior
                         (tetgen) with `bbw_samples_per_bone` Steiner points
                         inserted along each bone segment, solves igl.bbw on the
                         tets, slices the surface rows back. Exact partition of
                         unity, provably non-negative, volume-aware so touching
                         limbs do not bleed. Needs a watertight, self-intersection-
                         free, single-component mesh.
      "biharmonic"       igl.harmonic(k=2) on the same tets. ~8x faster than bbw,
                         not guaranteed non-negative. Tried first when
                         method="bbw"; escalates automatically if any weight < 0.
      "geodesic_voxel"   Voxelize the interior via generalized winding number,
                         Dijkstra geodesic from each bone through the voxel
                         interior, weight = exp(-(d/sigma)^2). Robust to
                         non-watertight input. Fixes touching limbs. No tet mesh.
      "envelope"         Bone capsules. Fast, Euclidean, bad. Fallback only.
      "rigid_components" One bone per connected mesh component at weight 1.0.
                         For mechanical and prop rigs. Skips weight solving.
      "transfer"         Requires `source_mesh` via weight_transfer instead.

    `dual_quaternion` sets the Armature modifier's use_deform_preserve_volume.
    Enable for authoring; DISABLE before glTF/FBX export and re-validate,
    because consumers use linear blend skinning.

    Returns:
      {ok, method_used: str, fallback_reason: str|None,
       groups: int, verts: int, solve_seconds: float,
       tet_count: int|None, steiner_points: int|None,
       partition_min: float, partition_max: float,
       max_influences: int, orphan_verts: int, negative_weights: int,
       dead_bones: list[str]}
    """

def weight_compute(mesh: Handle, rig: Handle, bones: list[str],
                   method: WeightMethod = "bbw",
                   blend_with_existing: float = 0.0) -> dict:
    """Recompute weights for a SUBSET of bones, leaving the rest alone.
    Remaining weight is redistributed proportionally among the named bones only.
    `blend_with_existing` in [0,1] lerps the new solve against what is there.
    Use to repair one limb without disturbing a hand-tuned face.
    Returns: {ok, bones_updated, verts_changed, solve_seconds,
              partition_min, partition_max}
    """

def weight_falloff(mesh: Handle, bone: str,
                   mode: Literal["geodesic","euclidean","axial"] = "geodesic",
                   inner: float = 0.0, outer: float = 0.15,
                   curve: Literal["linear","smooth","sharp","sphere"] = "smooth",
                   region: Optional[str] = None,
                   replace: bool = True) -> dict:
    """Paint a bone's weights numerically by distance falloff -- the expressible
    form of weight painting. Weight is 1.0 within `inner` metres of the bone
    segment and 0.0 beyond `outer`, with `curve` shaping the band.
    `mode="geodesic"` measures along the surface (igl heat geodesics) so it does
    not leak across a contact; `euclidean` is straight-line; `axial` projects
    onto the bone's own axis, which is what you want for a cylindrical limb.
    `region` limits the edit to an existing vertex group.
    Returns: {ok, verts_affected, weight_min, weight_max,
              needs_normalize: bool}
    """

def weight_transfer(target: Handle, source: Handle,
                    mapping: Literal["topology","nearest","poly_interp","vnorproj"] = "poly_interp",
                    bones: Optional[list[str]] = None,
                    max_distance: float = 0.05,
                    mix: Literal["replace","add","multiply"] = "replace",
                    factor: float = 1.0) -> dict:
    """Transfer weights from another mesh (wraps object.data_transfer with
    data_type='VGROUP_WEIGHTS', layers_select_src='BONE_DEFORM').
    `mapping="topology"` for topologically identical meshes (LOD chains from one
    base); `poly_interp` otherwise.
    This is also the high-resolution path: decimate to ~20k, solve BBW on the
    proxy, transfer back to the full mesh.
    Returns: {ok, groups_created, groups_updated, verts_unmatched,
              partition_min, partition_max}
    """

def weight_blend(mesh: Handle, bone_a: str, bone_b: str,
                 center: Optional[Vec3] = None, width: float = 0.08,
                 ratio: float = 0.5) -> dict:
    """Rebuild the blend band between two adjacent bones. Within `width` metres
    of `center` (default: the shared joint position) weights ramp smoothly from
    bone_a to bone_b with the midpoint at `ratio`. Outside the band, the
    dominant bone goes to its prior full value.
    The fix for a joint whose falloff is too sharp (volume collapse) or too wide
    (mushy bend).
    Returns: {ok, verts_affected, band_width_actual: float}
    """

def weight_cleanup(mesh: Handle,
                   clean_limit: float = 0.001,
                   smooth_factor: float = 0.0, smooth_repeat: int = 0,
                   smooth_region: Optional[str] = None,
                   mirror: bool = False, mirror_topology: bool = False,
                   limit_total: Optional[int] = None,
                   normalize: bool = True) -> dict:
    """Run the standard post-process chain, in the only order that is correct:
    clean (drop dust, keep_single=True) -> smooth -> mirror -> limit_total ->
    normalize. Normalizing before limiting breaks normalization again, so this
    tool exists to stop that happening.
    Set `limit_total=4` when the export target is glTF.
    Returns: {ok, verts_cleaned, verts_smoothed, groups_mirrored,
              influences_dropped, partition_min, partition_max, max_influences}
    """

def weight_clear_island(mesh: Handle, bone: str,
                        keep: Literal["largest","nearest_bone","all_above"] = "largest",
                        min_verts: int = 10) -> dict:
    """Delete stray weight islands: find the connected components of the region
    a bone weights above 0.1, keep one, zero the rest, then renormalize the
    affected vertices across their remaining influences.
    This fixes the single worst auto-weight bug -- the hand bone that also owns
    a patch of thigh because the arm was touching the leg at rest.
    Returns: {ok, islands_found: int, islands_removed: int,
              verts_zeroed: int, kept_island_verts: int}
    """

def weight_report(mesh: Handle, rig: Handle, detail: Literal["summary","per_bone"] = "summary") -> dict:
    """All of section 7.8's numeric checks, in one call. No vertex dumps.
    Returns:
      {ok, verts, bones,
       partition: {min, max, mean_abs_error},
       max_influences, orphan_verts, negative_weights,
       dead_bones: list[str],
       gradient: {p999: float, edges_over_0p4: int, worst_edge: int},
       islands: [{bone, count, sizes: list[int]}],           # bones with > 1 island
       geodesic_mismatch: [{bone, vert_count, sample: list[int]}],   # sample <= 5
       symmetry: {worst_pair: str, worst_residual: float},
       per_bone: [{bone, verts, weight_mean, weight_max}] | None}
    """


# ---------------------------------------------------------------------------
# CONTROL RIG GENERATION
# ---------------------------------------------------------------------------

def rigify_generate(rig: Handle, validate_metarig: bool = True) -> dict:
    """Run Rigify's generator on a metarig. 6.5 s for the full human metarig on
    4 CPU cores, producing a 706-bone rig: 160 DEF, 159 ORG, 167 MCH, 220
    controls, 533 constraints, 114 drivers, 220 custom shapes, 23 bone
    collections with IK/FK/Tweak layers per limb.
    This is the single highest-leverage tool in the rigging surface: it is a
    production control rig for free, and it cannot be matched by hand-building.
    `validate_metarig` pre-checks chain lengths against each bone's rigify_type
    and returns a readable error instead of Rigify's exception.
    Returns:
      {ok, rig: Handle, bone_count, deform_count, control_count, mch_count,
       constraints: int, drivers: int, custom_shapes: int,
       collections: list[str], generate_seconds: float,
       errors: list[str]}
    """

def control_ik_chain(rig: Handle, tip_bone: str, chain_count: int = 2,
                     control_name: Optional[str] = None,
                     pole_name: Optional[str] = None,
                     auto_pole_angle: bool = True,
                     stretch: bool = False,
                     fk_switch: bool = True) -> dict:
    """Add an IK chain with a target control, a pole target, and (optionally) an
    IK/FK switch driven by a custom property.
    `auto_pole_angle` solves the pole angle numerically rather than guessing:
    solve at 0, measure the chain's actual bend direction against the limb's
    bend plane, set the angle to the correction, re-evaluate, assert within 1
    degree. Guessing this is the most common hand-rigging error.
    Returns:
      {ok, control: str, pole: str, pole_angle_deg: float,
       fk_bones: list[str], switch_property: str|None,
       bend_error_deg: float}        # post-solve residual; should be < 1.0
    """

def control_foot_roll(rig: Handle, side: Literal["L","R"],
                      foot_bone: str = None, toe_bone: str = None,
                      auto_pivots: bool = True,
                      mesh: Optional[Handle] = None) -> dict:
    """Build a reverse-foot roll rig: ik_foot control -> heel pivot -> toe pivot
    -> ball pivot -> the leg IK's actual target, driven by one `foot_roll`
    property with clamped drivers (negative = heel, positive = ball then toe).
    `auto_pivots` derives all three pivot positions from the foot vertex group's
    geometry: heel = most -Y at min Z, toe = most +Y at min Z, ball = the
    width-maximum cross-section along the heel->toe axis (~72%).
    Returns: {ok, bones_added: list[str], pivots: {heel: Vec3, ball: Vec3, toe: Vec3},
              property: str, drivers: int}
    """

def control_finger_drivers(rig: Handle, hand_bone: str,
                           curl_factors: tuple[float,float,float] = (1.0, 1.2, 0.9),
                           max_curl_deg: float = 90.0,
                           spread: bool = True) -> dict:
    """Add per-finger `curl` and `spread` custom properties on the hand control,
    driving each segment's local X rotation scaled by `curl_factors`
    (proximal/intermediate/distal) and the proximal's Z for spread.
    Returns: {ok, fingers: list[str], properties: list[str], drivers: int}
    """

def control_twist_bones(rig: Handle, segment: str, count: int = 2,
                        source: Optional[str] = None,
                        influences: Optional[list[float]] = None,
                        layout: Literal["chained","leaf_siblings"] = "chained") -> dict:
    """Add twist/roll bones distributing `source`'s local-Y rotation along
    `segment` via COPY_ROTATION constraints with Y-only, LOCAL/LOCAL spaces.
    `influences` defaults to evenly spaced (0.33, 0.67 for count=2).
    `layout="leaf_siblings"` reproduces UE5's mannequin structure, where
    upperarm_twist_01_l and _02_l are BOTH children of upperarm_l rather than
    chained -- required for UE5 skeleton compatibility.
    Requires the twist bones' roll to equal the parent segment's, which this
    tool enforces. Without that, COPY_ROTATION Y-only copies a diagonal.
    Returns: {ok, bones_added: list[str], influences: list[float],
              parent_roll_matched: bool}
    """

def control_spline_ik(rig: Handle, chain_root: str, chain_count: int,
                      control_count: int = 4,
                      y_scale: Literal["none","original","bone","fit"] = "original") -> dict:
    """Spline IK for tails, tentacles, ropes, long necks. Builds a NURBS curve
    through the chain, hooks `control_count` control bones to its points, adds a
    SPLINE_IK constraint on the chain tip.
    Does NOT survive export -- bake before export_rig.
    Returns: {ok, curve: str, controls: list[str], constraint_on: str}
    """

def control_spine(rig: Handle, chain_root: str, chain_count: int,
                  mode: Literal["fk","bbone","spline"] = "fk",
                  bbone_segments: int = 8,
                  hip_chest_controls: bool = True) -> dict:
    """Build a spine rig. `fk` is plain parented bones (exports cleanly).
    `bbone` sets bbone_segments with TANGENT handles from hip/chest controls for
    a genuinely smooth curve -- but B-Bone subdivision does NOT export; the
    curve is lost and you get straight bones. `spline` uses Spline IK; also
    authoring-only.
    Returns: {ok, mode, controls: list[str], exports_faithfully: bool}
    """

def control_corrective_shape(mesh: Handle, rig: Handle, name: str,
                             driver_bone: str,
                             transform: Literal["rot_x","rot_y","rot_z","swing","twist"] = "swing",
                             ramp_deg: tuple[float,float] = (20.0, 90.0),
                             from_pose: bool = True) -> dict:
    """Create a corrective shape key driven by a bone's bend angle, using a
    TRANSFORMS driver variable with rotation_mode='SWING_TWIST_Y' so the angle
    is continuous past 90 degrees (plain ROT_Z is not).
    `from_pose=True` snapshots the current evaluated deformation as the shape's
    starting point, so Claude can pose the rig, call this, then edit offsets.
    The shape key exports to glTF as a morph target; the DRIVER does not --
    export_rig reports it so the consumer can rebuild it engine-side.
    Returns: {ok, shape_key: str, driver_expression: str,
              ramp: tuple[float,float], exports_as_morph: bool}
    """

def control_corrective_smooth(mesh: Handle, region: Optional[str] = None,
                              factor: float = 0.5, iterations: int = 5,
                              rest_source: Literal["orco","bind"] = "bind",
                              smooth_type: Literal["simple","length_weighted"] = "length_weighted") -> dict:
    """Add a CORRECTIVE_SMOOTH modifier after the Armature modifier -- Blender's
    shipping stand-in for Delta Mush. With rest_source='bind' it stores a bind
    state and corrects toward it, which rescues mediocre weights cheaply.
    Scope it to a `joints` vertex group; applying it everywhere flattens detail.
    Does not export. Bake or accept the loss.
    Returns: {ok, modifier: str, exports: bool}
    """

def control_face_rig(mesh: Handle, rig: Handle,
                     mode: Literal["bones","arkit_blendshapes","hybrid"] = "hybrid",
                     eye_components: Optional[list[Handle]] = None) -> dict:
    """Generate a face rig.
    `bones`: jaw + eye + tongue bones placed from head geometry (jaw pivot below
      and behind the ear canal, eye bones from fitted eyeball sphere centres,
      tongue chain in the mouth cavity), weighted with a falloff from the mouth
      plane. Exports as a skeleton.
    `arkit_blendshapes`: create all 52 ARKit-named shape key slots; fill the
      ~14 mechanically derivable ones (jaw*, eyeBlink*, eyeLook*) by baking bone
      motion; leave the rest empty and report the count honestly. forge cannot
      sculpt a believable deltoid, let alone 38 face shapes.
    `hybrid`: both, which is what production does.
    Returns:
      {ok, bones_added: list[str], shape_keys_created: int,
       shape_keys_filled: int, shape_keys_empty: list[str],
       eyes_found: bool, jaw_pivot: Vec3|None,
       vrm_expression_bindings: dict[str, dict[str,float]]}  # visemes from ARKit shapes
    """


# ---------------------------------------------------------------------------
# VALIDATION AND VISUAL FEEDBACK
# ---------------------------------------------------------------------------

def rig_validate(rig: Handle, mesh: Optional[Handle] = None,
                 target: Optional[Literal["gltf","fbx_ue5","fbx_unity","vrm","none"]] = None,
                 static_only: bool = False) -> dict:
    """Every static check in section 10.1 plus export-target conformance.
    Returns:
      {ok, score: int,                                  # 0..100
       subscores: {skeleton: int, weights: int, controls: int, export: int},
       fails: [{check: str, detail: str, fix_tool: str}],
       warns: [{check: str, detail: str, fix_tool: str}],
       skeleton: {bones, deform_bones, zero_length: list[str],
                  root_at_origin: bool, uniform_scale: bool,
                  negative_determinant: list[str],
                  roll_hinge_min: float, roll_hinge_worst: str,
                  roll_chain_max_delta_deg: float,
                  mirror_pairs: int, mirror_unpaired: list[str],
                  mirror_worst_delta_deg: float},
       weights: {...},                                  # weight_report summary
       target_conformance: {missing_required: list[str], extra: list[str],
                            naming_ok: bool, axis_ok: bool, scale_ok: bool,
                            influences_ok: bool, joint_count_warning: str|None}}
    Every fail and warn names the tool that fixes it. A report that does not
    is decoration.
    """

def rig_pose_test(rig: Handle, mesh: Handle,
                  poses: Literal["standard","limbs","twist","spine","custom"] = "standard",
                  custom: Optional[list[dict]] = None,
                  interp_steps: int = 10,
                  dual_quaternion: bool = False) -> dict:
    """Pose the rig through the battery (section 10.2) and measure the mesh.
    `dual_quaternion=False` by default BECAUSE consumers use linear blend
    skinning -- testing with DQS on hides exactly the twist failures that
    twist bones exist to fix.
    Returns:
      {ok, poses_run: int, seconds: float,
       results: [{pose: str, bone: str, angle_deg: float,
                  self_overlap_pairs: int, self_overlap_face_frac: float,
                  volume_ratio: float, normal_flips: int,
                  velocity_outlier_ratio: float,
                  min_surface_gap_m: float}],
       worst: {volume: {pose, ratio}, overlap: {pose, frac},
               flips: {pose, count}, velocity: {pose, ratio}},
       candy_wrapper: [{segment: str, section_area_ratio: float, pass: bool}]}
    Numbers, 20 rows, a few hundred tokens. No meshes.
    """

def rig_render_weights(mesh: Handle, rig: Handle,
                       bones: Optional[list[str]] = None,
                       mode: Literal["single","dual","argmax","delta"] = "argmax",
                       views: list[Literal["front","back","left","right","threequarter"]] = ("front","back"),
                       size: int = 400, montage: bool = True) -> dict:
    """Render weight heatmaps. THE highest-value tool in this surface: numbers
    cannot tell Claude whether a falloff is centred, symmetric, or bleeding, and
    one 400x400 PNG can.
    Bakes weights to a point-domain FLOAT_COLOR attribute, renders with
    BLENDER_WORKBENCH, shading.light='FLAT', shading.color_type='VERTEX', an
    orthographic camera, transparent film. Measured 0.87 s per 400x400 view on
    4 CPU cores, no GPU.
    mode:
      "argmax"  one hue per bone, showing the whole weight partition in one
                image. Run this FIRST on any new rig -- it is the fastest way to
                see a bone owning the wrong limb.
      "single"  one bone, red=1 blue=0 with a mid-band green.
      "dual"    two bones as R and G; yellow is the blend band, which is exactly
                where the joint should be and nowhere else.
      "delta"   |W_before - W_after| from the last weight edit, to confirm an
                edit did what it intended and nothing more.
    Returns: {ok, images: list[str], bones_rendered: list[str],
              legend: dict[str,str], seconds: float}
    """

def rig_render_poses(rig: Handle, mesh: Handle,
                     poses: Literal["standard","limbs","twist","spine","custom"] = "standard",
                     custom: Optional[list[dict]] = None,
                     views: list[str] = ("front","side"),
                     size: int = 384, montage: bool = True,
                     annotate_metrics: bool = True) -> dict:
    """Headless renders of each battery pose from fixed orthographic cameras,
    as a contact sheet. ~0.7 s per view per pose on CPU.
    Orthographic matters: a perspective camera's foreshortening reads as volume
    loss and will send Claude chasing a problem that is not there.
    `annotate_metrics` puts each pose's overlap/volume numbers in the returned
    text beside the image rather than burning them into the pixels.
    Returns: {ok, images: list[str], poses: list[str],
              metrics: list[dict], seconds: float}
    """

def rig_report(rig: Handle, mesh: Handle,
               target: Optional[str] = None,
               include_renders: bool = True) -> dict:
    """The one call Claude makes to find out where it stands. Runs validate +
    weight_report + pose_test, renders an argmax weight map and a pose contact
    sheet, and formats everything into the under-400-token report in section
    10.4, with a ranked NEXT list naming the exact tool for each problem.
    Returns: {ok, text: str, score: int, images: list[str],
              next_actions: [{priority: int, problem: str, tool: str, args: dict}]}
    """


# ---------------------------------------------------------------------------
# CHECKPOINTS
# ---------------------------------------------------------------------------

def rig_checkpoint(rig: Handle, meshes: list[Handle] = (),
                   label: str = "") -> dict:
    """Snapshot the armature (bones, rolls, collections, constraints, drivers,
    custom properties, custom shapes) and every named mesh's vertex groups and
    shape keys. Cheap -- it is a .blend library write of two datablocks, not a
    scene save.
    Returns: {ok, checkpoint: Handle, label: str, bytes: int, bone_count: int}
    """

def rig_rollback(checkpoint: Handle) -> dict:
    """Restore a checkpoint in place, keeping the same rig and mesh handles so
    nothing downstream has to be re-wired.
    Returns: {ok, rig: Handle, meshes: list[Handle], bones_restored: int,
              groups_restored: int}
    """

def rig_checkpoints(rig: Handle) -> dict:
    """List checkpoints for a rig, newest first.
    Returns: {ok, checkpoints: [{handle, label, created, bytes, score: int|None}]}
    """


# ---------------------------------------------------------------------------
# COMPILE AND EXPORT
# ---------------------------------------------------------------------------

def rig_compile(mesh: Handle, ir: dict,
                dry_run: bool = False,
                stop_on_error: bool = True) -> dict:
    """Compile a declarative rig IR (section 12) into a finished rig in one call:
    skeleton -> rolls -> symmetrize -> bind -> weight post-process -> controls
    -> validate. This is the one-shot path; the incremental tools above are the
    repair path.
    `dry_run` validates the IR and reports what it would do without touching
    the scene.
    Returns:
      {ok, rig: Handle, stages: [{stage: str, ok: bool, seconds: float,
                                  detail: str}],
       report: dict,                 # the rig_report payload
       ir_errors: list[str]}
    """

def export_rig(rig: Handle, meshes: list[Handle], path: str,
               fmt: Literal["gltf","glb","fbx","vrm","blend"] = "glb",
               target: Optional[Literal["generic","ue5","unity","mixamo","vrm1","threejs"]] = "generic",
               deform_only: bool = True,
               bake_constraints: bool = True,
               limit_influences: int = 4,
               apply_naming: bool = True) -> dict:
    """Export, with the per-target settings already correct.

    glTF/GLB: sets export_def_bones=True (ships the DEF collection only -- the
      difference between a 160-bone game asset and a 706-bone authoring rig),
      export_skins=True, export_influence_nb=4, export_all_influences=False,
      export_leaf_bone=False, export_yup=True, export_rest_position_armature=True,
      export_force_sampling=True (required, or constraint-driven motion is lost),
      export_morph=True.
    FBX for UE5: add_leaf_bones=False (the default True appends a zero-length
      *_end bone per chain and UE imports them as real bones, polluting every
      retarget asset), use_armature_deform_only=True, primary_bone_axis='X',
      secondary_bone_axis='-Z', armature_nodetype='NULL',
      apply_scale_options='FBX_SCALE_NONE'.
    `apply_naming` renames to the target's convention immediately before export
    and restores Blender names after, so the authoring rig stays mirrorable.

    Returns:
      {ok, path, bytes, joints_exported: int, influences_max: int,
       morph_targets: int,
       dropped: {constraints: int, drivers: int, bbone_segments: int,
                 custom_shapes: int, bone_collections: int},
       baked: {bones: int, frames: int},
       rebuild_engine_side: [{kind: str, name: str, driver: str, detail: str}],
       warnings: list[str]}
    `dropped` and `rebuild_engine_side` are the honest part: constraints and
    drivers do not exist in glTF or FBX, and the consumer has to be told what
    was lost and what has to be reconstructed.
    """
```

---
## 12. THE RIG IR — AND WHETHER IT BEATS INCREMENTAL CALLS

### 12.1 The argument

**Declarative IR wins for structure.** A skeleton is a tree with ~90 nodes, each with a parent, a head, a tail, a roll policy and a collection. Expressing that as 90 `bone_edit` calls is absurd — it's one document, and Claude is good at writing one document. Same for the control rig: "four IK chains, two foot rolls, ten finger driver sets, twist bones on four segments" is a list, and the compiler knows the right order to build them in (skeleton before rolls before symmetrize before bind before constraints, because each stage invalidates the previous one's references).

**Incremental calls win for repair.** After the first compile, the work is: look at an argmax weight render, notice `DEF-hand.L` owns a patch of thigh, call `weight_clear_island`, re-render, confirm. That is a feedback loop, and a loop cannot be expressed in a document authored before the loop started. Re-compiling the whole IR to fix one weight island would discard every hand-tuned weight.

**So: both, with a clean seam.** `rig_compile(mesh, ir)` is the one-shot structural pass. Everything after that is incremental tools driven by `rig_report`. The IR is a *constructor*, not a persistent source of truth, and forge should not try to round-trip arbitrary weight edits back into it.

**Where the seam gets tested:** the `weights` block in the IR covers *strategy* (method, falloff widths, cleanup parameters) and never per-vertex data. Strategy is re-compilable; hand edits are not. If Claude changes strategy it re-compiles from the checkpoint before binding, which `rig_checkpoint`/`rig_rollback` make cheap. If Claude is repairing, it stays incremental. The IR has no `vertices:` key and must never grow one.

**Second-order benefit of the IR:** it's a diff-able artifact. Claude can author `v2` of a rig IR as a patch on `v1`, and `rig_compile(dry_run=True)` reports what would change. That's a far better iteration story than replaying a tool-call transcript.

### 12.2 Schema

YAML shown for readability; JSON is the wire format. Every field's default is the forge default, so a minimal IR is ~10 lines.

```yaml
forge_rig: 1                        # schema version, required

meta:
  name: knight_body
  subject: humanoid                 # humanoid | quadruped | bird | creature | mechanical | prop
  units: m
  up: +Z                            # source mesh convention; compiler converts
  forward: -Y
  rest_pose: apose                  # tpose | apose | other
  target: ue5                       # generic | ue5 | unity | mixamo | vrm1 | threejs
                                    # drives naming, required bones, axes, influence limit

skeleton:
  source: template                  # template | landmarks | explicit | curve
  template: rigify_human            # see SkeletonTemplate
  fit: landmarks                    # none | bbox | landmarks
  pre_bend_deg: 5                   # elbows/knees, so bend planes are defined

  landmarks:                        # optional overrides; detected otherwise
    head_top:  [0.0, 0.0, 1.78]
    hand_L:    [0.74, 0.0, 1.42]
    hand_R:    [-0.74, 0.0, 1.42]
    foot_L:    [0.11, 0.02, 0.04]
    foot_R:    [-0.11, 0.02, 0.04]
  lock_landmarks: [hand_L, hand_R, foot_L, foot_R]

  bones: []                         # only when source: explicit
    # - {name: root, head: [0,0,0], tail: [0,0,0.1], parent: null}
    # - {name: pelvis, head: [0,0,0.98], tail: [0,0,1.06], parent: root,
    #    deform: true, roll_align: [0,0,1], collections: [DEF]}

  rolls:
    policy: forge_default           # forge_default | world_z | parent | bend_plane | explicit
    overrides:
      hand.L: {align: [0, 0, 1]}
      thumb.01.L: {align: [0.4, 0.9, 0]}

  symmetrize:
    enabled: true
    direction: +X
    require_residual_below: 0.01    # skip and warn if the mesh isn't symmetric enough

  collections:                      # name -> bone name globs
    DEF:  ["DEF-*"]
    MCH:  ["MCH-*"]
    ORG:  ["ORG-*"]
    Fingers: ["f_*", "thumb.*"]
  deform_collection: DEF            # the ONLY collection that may carry weights

weights:
  method: bbw                       # bone_heat | bbw | biharmonic | geodesic_voxel
                                    # | envelope | rigid_components | transfer
  fallback: [biharmonic, bone_heat, envelope]   # tried in order on failure
  bbw:
    samples_per_bone: 12            # Steiner points inserted along each bone segment
    tet_quality: 1.5                # tetgen minratio
    max_iter: 30
  proxy:                            # high-res path: solve on a decimate, transfer back
    enabled: auto                   # auto = enable above proxy.threshold_verts
    threshold_verts: 60000
    target_verts: 20000
    transfer_mapping: poly_interp
  per_region:                       # override method for named vertex groups
    - {group: face, method: bone_heat}
    - {group: eyes, method: rigid_components}
  cleanup:
    clean_limit: 0.001
    smooth: {factor: 0.4, repeat: 2, region: joints}
    mirror: true
    limit_total: 4                  # from meta.target; explicit here wins
    normalize: true                 # always last
  dual_quaternion: false            # authoring aid; MUST be false for export validation

controls:
  ik:
    - {tip: forearm.L, chain: 2, control: hand_ik.L, pole: elbow_pole.L,
       auto_pole_angle: true, fk_switch: true, stretch: false}
    - {tip: forearm.R, chain: 2, mirror_of: forearm.L}
    - {tip: shin.L,    chain: 2, control: foot_ik.L, pole: knee_pole.L}
    - {tip: shin.R,    chain: 2, mirror_of: shin.L}
  foot_roll:
    - {side: L, auto_pivots: true}
    - {side: R, auto_pivots: true}
  twist:
    - {segment: forearm.L, count: 2, source: hand.L, layout: leaf_siblings}
    - {segment: forearm.R, mirror_of: forearm.L}
    - {segment: thigh.L,   count: 2, source: thigh.L, layout: leaf_siblings}
    - {segment: thigh.R,   mirror_of: thigh.L}
  fingers:
    - {hand: hand.L, curl_factors: [1.0, 1.2, 0.9], max_curl_deg: 90, spread: true}
    - {hand: hand.R, mirror_of: hand.L}
  spine:
    {root: spine_01, chain: 5, mode: fk, hip_chest_controls: true}
  spline_ik: []
    # - {chain_root: tail.001, chain: 8, controls: 4, y_scale: original}
  correctives:
    - {name: shoulder_up.L, driver_bone: upper_arm.L, transform: swing,
       ramp_deg: [20, 90]}
    - {name: shoulder_up.R, mirror_of: shoulder_up.L}
  corrective_smooth:
    {enabled: true, region: joints, factor: 0.5, iterations: 5, rest_source: bind}
  face:
    mode: hybrid                    # none | bones | arkit_blendshapes | hybrid
    arkit: true                     # create all 52 slots, fill the derivable ones
    vrm_expressions: false

validation:
  poses: standard                   # standard | limbs | twist | spine | custom | none
  custom_poses: []
    # - {name: shoulder_shrug, bone: clavicle.L, euler_deg: [0, 0, 25]}
  thresholds:
    volume_ratio_min: 0.92
    self_overlap_face_frac_max: 0.005
    candy_wrapper_min: 0.60
    partition_tolerance: 0.001
    max_influences: 4
    roll_hinge_min: 0.95
    mirror_delta_deg_max: 2.0
  renders:
    weights: {mode: argmax, views: [front, back], size: 400}
    poses:   {views: [front, side], size: 384, montage: true}
  fail_on: [orphan_verts, negative_weights, normal_flips, bone_islands]

export:
  - {fmt: glb, path: out/knight.glb, target: generic,
     deform_only: true, bake_constraints: true, limit_influences: 4}
  - {fmt: fbx, path: out/knight_ue5.fbx, target: ue5,
     deform_only: true, apply_naming: true}
```

### 12.3 Compiler stages and their order

`rig_compile` runs these in exactly this order, because each one invalidates the previous stage's references:

| # | stage | why it must be here |
|---|---|---|
| 0 | validate IR | catch schema errors before touching the scene |
| 1 | normalize mesh transform | rolls and landmarks are meaningless in the wrong basis |
| 2 | checkpoint `pre_skeleton` | so a bad fit is one rollback away |
| 3 | detect landmarks | needs the normalized mesh |
| 4 | build skeleton | needs landmarks |
| 5 | pre-bend elbows/knees | **before** rolls — a straight limb has no bend plane |
| 6 | set rolls | **before** symmetrize, so the mirror copies correct rolls |
| 7 | assign bone collections | before generate; Rigify reads them |
| 8 | symmetrize | needs rolls; guarantees `.R` is exact |
| 9 | checkpoint `pre_bind` | the re-strategize point |
| 10 | rigify_generate (if template is a metarig) | **before** bind — generate rebuilds the armature |
| 11 | bind + weights | needs the final deform bones to exist |
| 12 | weight cleanup | clean → smooth → mirror → limit → **normalize last** |
| 13 | checkpoint `post_weights` | the repair baseline |
| 14 | controls: twist → ik → foot roll → fingers → spine → spline | twist first so IK chains see the final bone set |
| 15 | correctives + corrective_smooth | needs a deforming rig to snapshot from |
| 16 | face | independent; last so a face failure doesn't block the body |
| 17 | validate + pose test + renders | |
| 18 | export | per `export` list |

**Stage 10 is the one people get wrong.** Rigify's generator creates a *new* armature object from the metarig. Binding before generate binds to the metarig and the weights are on the wrong skeleton. Bind after.

Each stage returns `{stage, ok, seconds, detail}` so the compile result is a readable timeline, and `stop_on_error=False` lets Claude get a partial rig plus a list of what failed rather than nothing.

---

## 13. SEAMS WITH THE OTHER TWO RESEARCHERS

Short enough to quote in the other docs.

### 13.1 CONTRACT: geometry → rigging

**What rigging requires of a mesh before it can be rigged well.** Numbered so the geometry doc can reference them as `GR-1` etc.

| id | requirement | why | severity |
|---|---|---|---|
| **GR-1** | **Manifold, no non-manifold edges or vertices** | bone heat solves a Laplacian; non-manifold makes it ill-defined and the operator fails *silently*, producing garbage weights with no error | **blocking** |
| **GR-2** | **No self-intersections at rest** | tetgen refuses or produces nonsense; the self-intersection validator's rest baseline must be 0 or every pose metric is meaningless | **blocking for BBW**, degrades bone heat |
| **GR-3** | **Watertight, single connected component per skinned body** | a component containing no bone gets zero weight and stays frozen at rest. Separate parts are fine *if named*, see GR-4 | **blocking** |
| **GR-4** | **Part separation declared, not implied.** Eyes, teeth, tongue, hair, cloth, armour shipped as named objects or named vertex groups | rigid parts (eyeballs) want `rigid_components` weighting at 1.0; soft parts want BBW. forge cannot guess which sphere inside a head is an eye vs a bubble | **blocking for faces**, high elsewhere |
| **GR-5** | **Symmetric vertex positions across the symmetry plane** (positions, not necessarily topology) | `vertex_group_mirror`, `armature.symmetrize`, and the mirror-residual validator all depend on it. Report the residual; above ~1% of bbox, mirroring must be disabled and every weight solved twice | high |
| **GR-6** | **≥ 3 edge loops across every bend joint** (elbow, knee, wrist, ankle, finger joints, shoulder, hip) | below 3 loops, no weight distribution avoids volume collapse. This is *the* most common cause of a bad-looking rig and it is a geometry problem, not a rigging problem. 4–5 loops at the shoulder and hip | **blocking for quality** |
| **GR-7** | **Quad-dominant, evenly-spaced topology at joints**; no long thin triangles, no poles inside a bend zone | triangle fans and poles create weight gradient discontinuities that read as creases | high |
| **GR-8** | **A-pose preferred, 30–45° arm declination.** T-pose acceptable. Never a tight/relaxed pose with limbs touching | limbs touching at rest make bone heat bleed across the contact (the arm gets torso weight). A-pose also gives better armpit topology than T-pose | high |
| **GR-9** | **5° pre-bend in elbows and knees**, or accept that forge injects it | a dead-straight limb has `cross(a,b) ≈ 0`, so there is no bend plane, so bone roll and IK pole angle are undefined. This is cheap in the mesh and annoying to fix after | medium |
| **GR-10** | **Z-up, right-handed, metres, origin between the feet at ground level, facing −Y** | every landmark heuristic, every roll policy, and every export axis conversion assumes it. Report and auto-correct if wrong, but correcting costs a guess | **blocking** |
| **GR-11** | **Realistic scale.** Humanoid 1.4–2.1 m | engines assume metres; Unity's Humanoid and UE's retargeting both misbehave at 100× scale. Also: `max_distance` defaults in weight transfer are absolute | medium |
| **GR-12** | **Vertex count budget.** Under ~60 k for a direct solve; above that forge auto-uses the decimate-solve-transfer proxy path and loses a little weight fidelity | performance, not correctness | low |
| **GR-13** | **No shape keys on the input mesh before binding**, or declare them | binding and `data_transfer` interact badly with existing shape keys; the basis can shift | medium |
| **GR-14** | **UVs and materials already final** | re-UVing after binding is safe, but re-topologizing is not — it invalidates every weight. If geometry plans a retopo pass, it must happen before rigging | medium |
| **GR-15** | **Report a `landmark_hints` block if the generator knows it**: head/hand/foot positions, symmetry plane, part semantics. An SDF or ML generator often knows these for free | turns landmark detection from a guess into a lookup, and raises fit quality more than any algorithm change | **high value, optional** |

**What rigging gives back to geometry:** a `rig_feasibility(mesh)` tool that runs GR-1 through GR-11 as checks and returns a pass/fail list with the exact failing element counts, so geometry can gate its own output before handing it over. That tool belongs in the rigging surface and should be called by the geometry pipeline, not after it.

### 13.2 CONTRACT: rigging → animation

**What animation can rely on.** Numbered `RA-n`.

| id | guarantee |
|---|---|
| **RA-1** | **Naming.** Bones are Blender convention (`snake_case`, `.L`/`.R` suffix) until `export_rig(apply_naming=True)`, which renames to the target convention *and restores afterward*. The authoring rig is always `.L`/`.R`. Animation should key by name against the authoring names. |
| **RA-2** | **Bone classes by prefix.** `DEF-` = deform (weighted, exported). `ORG-` = metarig copies. `MCH-` = mechanism. Unprefixed = animator controls. **Never key `DEF-`, `ORG-` or `MCH-` bones.** Rigify's convention; forge adopts it for hand-built rigs too. |
| **RA-3** | **Bone collections as the UI contract.** Collections are named `Torso`, `Torso (Tweak)`, `Fingers`, `Fingers (Detail)`, `Face`, `Face (Primary)`, `Face (Secondary)`, `Arm.L (IK)`, `Arm.L (FK)`, `Arm.L (Tweak)`, same for `Arm.R`/`Leg.L`/`Leg.R`, plus `Root`, `DEF`, `MCH`, `ORG`. Animation can enumerate `armature.collections_all` to discover the control set without hardcoding names. |
| **RA-4** | **Root motion bone.** Exactly one bone named `root`, at world origin, zero rotation, unit scale, with **no skin weights**, parent of everything. Root motion is keyed on `root` only. For UE5 export it is literally named `root`. |
| **RA-5** | **IK controls.** Named `<limb>_ik.<side>` (`hand_ik.L`, `foot_ik.L`). Each has a pole target `<joint>_pole.<side>`. Pole angles are numerically solved, so the chain bends in the anatomically correct direction with a pole target at its default position — animation does not need to hunt for a magic pole angle. |
| **RA-6** | **IK/FK switching.** A float custom property named `IK_FK` on `<limb>_parent.<side>` (Rigify's convention), 0.0 = IK, 1.0 = FK. Keyable. `rig_report` lists every switch property and its bone. |
| **RA-7** | **Foot roll.** A float custom property `foot_roll` on `foot_ik.<side>`. Negative rolls the heel, positive rolls ball then toe. Range is clamped by constraints, so out-of-range values are safe. |
| **RA-8** | **Finger controls.** Per finger, `<finger>_curl` and `<finger>_spread` float properties on `hand_ik.<side>`. Curl 0..1 maps to 0..`max_curl_deg`. |
| **RA-9** | **What is keyable.** Every unprefixed control bone's `location`, `rotation_*`, `scale`, plus every custom property listed in `rig_report().controls`. Rotation mode is `QUATERNION` on IK controls and `XYZ` on FK bones — animation should not change rotation modes, it breaks the drivers. |
| **RA-10** | **What is NOT keyable.** `DEF-`/`ORG-`/`MCH-` bones, the armature object transform (key `root` instead), and anything driven by a driver (keying over a driver silently wins and desyncs the rig). `rig_report` lists every driven channel. |
| **RA-11** | **Rest pose is the bind pose.** `export_rest_position_armature=True`. Any animation is relative to it. Don't apply poses as rest. |
| **RA-12** | **Units and frame rate.** Metres, Z-up in Blender; export converts. Scene FPS is whatever animation sets; rigging does not key anything, so there is no FPS assumption to break. |
| **RA-13** | **Export fidelity, stated plainly.** glTF and FBX carry **skeleton + skin + morph targets + baked keyframes only**. Constraints, drivers, B-Bone segments, custom shapes and bone collections are **dropped**. `export_rig` returns a `dropped` count and a `rebuild_engine_side` list naming every corrective shape's driver so animation or engine integration can reconstruct it. **`export_force_sampling=True` is mandatory** or constraint-driven bones export as static. |
| **RA-14** | **Twist bones are driven, not keyed.** `forearm_twist.L` follows `hand.L` at 0.33/0.67 influence. Keying them fights the constraint. |
| **RA-15** | **Face.** If `controls.face.arkit` was set, the mesh has all 52 ARKit-named shape keys, of which `rig_report` says how many are actually filled. `jawOpen`, `jawLeft/Right/Forward` and the eight `eyeLook*` shapes may be **bone-driven instead of morphs** — the report says which. VRM viseme bindings (`aa ih ou ee oh`) are emitted as weighted combinations of ARKit shapes. |
| **RA-16** | **Checkpoints are shared.** `rig_checkpoint` / `rig_rollback` handles are valid across the session, so animation can snapshot before an experiment. Rolling back a rig does **not** roll back actions. |

### 13.3 What rigging needs from animation

Short list, because the dependency is mostly one-way:
- **The pose battery should be animation's too.** If animation has a standard test-motion set (walk, run, idle, crouch), rigging wants to run the deformation metrics over those frames instead of synthetic poses. Synthetic extremes catch the worst cases; real motion catches the ones that matter.
- **Tell rigging the target FPS and whether root motion is in-place or travelling**, because that decides whether `root` needs to be keyed at all and whether the hip should be constrained to it.
- **Tell rigging if retargeting is planned and from what source** (Mixamo library, a mocap set, a CMU BVH dump). That fixes the naming convention and the required bone set at skeleton-build time instead of after.

---

## 14. INSTALL COMMANDS

Everything verified on this container: Linux 6.18, Python 3.13.18, 4 cores, 15 GB RAM, **no GPU**.

### 14.1 Required (the whole recommended path)

```bash
python3 -m venv /opt/forge/venv
V=/opt/forge/venv/bin

$V/pip install --upgrade pip
$V/pip install bpy==5.3.0            # Blender 5.3 as a Python module. 927 MB, ~9 s.
$V/pip install numpy libigl          # 43 MB + 16 MB
$V/pip install tetgen --no-deps      # 908 KB.  --no-deps is REQUIRED; without it
                                     # pip pulls pyvista + vtkmodules (565 MB).
$V/pip install trimesh               # mesh IO, convex hull, PCA helpers
$V/pip install skeletor              # curve skeletons for non-humanoid subjects
$V/pip install scipy                 # 139 MB; only for sparse solves + connected_components
```

Smoke test, verifies every load-bearing claim in this doc:

```bash
$V/python -c "
import bpy, addon_utils, igl, tetgen, numpy as np
print('blender', bpy.app.version_string)
addon_utils.enable('rigify', default_set=True)
addon_utils.enable('io_scene_gltf2', default_set=True)
assert hasattr(bpy.ops.pose, 'rigify_generate')
assert not hasattr(bpy.data.armatures, 'layers')     # bone collections era
assert callable(igl.bbw) and callable(igl.boundary_conditions)
assert callable(igl.heat_geodesics_precompute)
print('rigify ok, igl ok, tetgen ok')
"
```

### 14.2 Optional

```bash
$V/pip install wildmeshing           # 23 MB, fTetWild. Fallback tetrahedralizer when
                                     # tetgen fails on dirty input. API is
                                     # tetwild(options: dict, meshes: dict) -- needs a wrapper.
```

### 14.3 Learned auto-riggers — separate environment, GPU

Do **not** put these in forge's main venv. Python 3.11 vs our 3.13, a CUDA-pinned `spconv`, and `flash-attn`.

```bash
conda create -n unirig python=3.11 && conda activate unirig
python -m pip install torch torchvision                     # >= 2.3.1
python -m pip install -r requirements.txt
python -m pip install spconv-{your-cuda-version}
python -m pip install torch_scatter torch_cluster -f https://data.pyg.org/whl/torch-{your-torch-version}+{your-cuda-version}.html --no-cache-dir
python -m pip install numpy==1.26.4
# flash-attn: the UniRig README warns you will likely hit an install error here.
```
Inference:
```bash
bash launch/inference/generate_skeleton.sh --input mesh.glb --output skel.fbx
bash launch/inference/generate_skin.sh     --input skel.fbx --output skinned.fbx
bash launch/inference/merge.sh --source skinned.fbx --target mesh.glb --output final.fbx
```
Needs ≥ 8 GB VRAM. Checkpoint from `huggingface.co/VAST-AI/UniRig`, ~350 M params. **Not part of v0 or v1.**

### 14.4 Footprint summary

| set | size |
|---|---|
| required (bpy + igl + tetgen + numpy + scipy + trimesh + skeletor) | **≈ 1.16 GB** |
| + wildmeshing | +23 MB |
| UniRig env (separate, GPU) | ~8–12 GB with torch + CUDA wheels + checkpoint |

Install wall time for the required set: **under 60 seconds** (bpy itself was 9 s).

---

## 15. PHASED BUILD PLAN

### v0 — usable (target: 2–3 days of work)

The smallest thing that produces a rig a professional would accept for a simple humanoid.

**Build:**
- The `bpy`-module host: scene lifecycle, handle registry (`mesh_*`, `rig_*`, `ckpt_*`), mode-switch discipline, name-not-reference rule for edit bones.
- `skeleton_from_template` with `template="rigify_human_basic"`, `fit="landmarks"` — symmetry plane, geodesic extremity detection, torso-first constrained fit. **Skip fingers; place them proportionally from the hand OBB.**
- `skeleton_set_rolls(policy="forge_default")` and `skeleton_symmetrize`.
- `rigify_generate`.
- `bind_mesh(method="bone_heat")` + `weight_cleanup`.
- `weight_report`, `rig_validate` (static checks only).
- **`rig_render_weights(mode="argmax")`** — ship this in v0, it is the thing that makes the whole system workable.
- `rig_checkpoint` / `rig_rollback`.
- `export_rig` for `glb` (generic) and `fbx` (ue5), with the per-target flags from §8.7 hardcoded correctly.
- `rig_feasibility` — the GR-1..GR-11 gate, so geometry can self-check.

**Deliberately not in v0:** BBW, pose battery, face, correctives, spline IK, non-humanoids, the IR.

**v0 acceptance test:** take a clean A-pose humanoid mesh with proper joint loops, run it through, and get a Rigify rig that passes static validation, exports a 160-joint GLB, and whose argmax weight render shows every bone owning one contiguous sane region. If that works, v0 is done.

### v1 — good (target: 1–2 weeks)

**Add:**
- `bind_mesh(method="bbw")` with the full tetgen + Steiner-sample pipeline, `biharmonic` as the fast first pass with auto-escalation, and `geodesic_voxel` as the robust fallback.
- The proxy path (decimate → solve → `weight_transfer`) for meshes over 60 k verts.
- `rig_pose_test` — the full 20-pose battery with self-intersection, volume ratio, normal flips, velocity outliers, candy-wrapper section analysis.
- `rig_render_poses` contact sheets.
- `rig_report` — the scored, under-400-token report with a ranked NEXT list.
- The repair tools: `weight_falloff`, `weight_blend`, `weight_clear_island`, `weight_compute` (subset), `weight_transfer`.
- `rig_compile` + the IR, stages 0–13.
- `control_twist_bones` with `layout="leaf_siblings"` for real UE5 compatibility.
- `control_ik_chain` with numeric `auto_pole_angle`.
- `control_foot_roll` with geometric auto-pivots.
- `control_finger_drivers`.
- `skeleton_rename_convention` and `skeleton_retarget_names` with the §4 tables — the full lockstep rename including subtargets and driver bone targets.
- Full `rigify_human` (face metarig included, even if the face bones are only roughly placed).

**v1 acceptance test:** a stylized non-realistic humanoid (4-head-tall, oversized hands) rigs successfully, the pose battery flags its elbow volume loss, `weight_blend` fixes it, the re-render confirms, and the report score goes up. **The loop closing is the acceptance criterion, not any single number.**

### v2 — complete

**Add:**
- Non-humanoids: `mesh_curve_skeleton` + `skeleton_from_curve`, plus the Rigify quadruped/bird/cat/horse/wolf/shark metarig fitting.
- `control_spline_ik`, `control_spine(mode="bbone"|"spline")`.
- `control_corrective_shape` with SWING_TWIST drivers, `control_corrective_smooth`.
- `control_face_rig` — ARKit 52 slot generation, the ~14 derivable shapes from bone motion, jaw/eye/tongue bone placement, VRM viseme bindings, and template shape-key transfer when the mesh has known topology.
- Rigify face-metarig landmark fitting (the ~70 face bones).
- `rig_render_weights(mode="dual"|"delta")`.
- Optional learned backends behind explicit flags: a `unirig_rig` tool that shells out to a separate GPU env, and `tripo_rig` for the hosted path (with a loud docstring note that it uploads the mesh to a third party).
- `mechanical`/`prop` fast paths: `rigid_components` weighting by connected component, piston rigs, vehicle wheel drivers.
- VRM export with the full humanoid bone map and expression bindings.
- Round-trip: import an existing rigged asset, validate it, report, repair.

---

## 16. OPEN QUESTIONS AND WHAT I COULDN'T VERIFY

**Verified claims are marked throughout with measured numbers. These are the gaps.**

1. **UE5 Mannequin bone count: 87 vs 89.** My assembled list is 87 (§4.2) and arithmetic checks out. Reallusion's comparison docs say 89. I believe the two extra are `interaction` and `center_of_mass` on SKM_Manny in UE 5.1+, but **I could not verify those names against an Epic source** — there is no published official bone list, and every community list I found is partial or derived. **Action: someone opens SK_Mannequin in a Skeleton Editor and reads it.** Until then, make `skeleton_from_template("ue5")` take an `extras: list[str]`.

2. **ARKit's 52 names are high-confidence but not first-party-verified.** Apple's `ARFaceAnchor.BlendShapeLocation` page returned title-only to every fetch I tried. Two independent reproductions agree exactly on grouping and count (14/5/3/2/4/23/1 = 52) and on every identifier. **Risk is low but nonzero** — a single misspelled identifier makes an ARKit face silently not drive. **Action: diff the list against a known-good ARKit-compatible asset's morph-target names once.**

3. **UniRig on CPU is unverified.** The "greedy decode on CPU, one to several minutes per mesh" figure is from a third-party integration project, not the authors. The authors document a GPU requirement only. If CPU inference actually works at that speed it changes the v2 calculus significantly — it would make a learned backend viable without a GPU. **Action: test it. It's a weekend, in a separate container.**

4. **SkinTokens' claimed gains (98–133% skinning accuracy, 17–22% bone prediction over UniRig) are unverified**, as is its licence. UniRig is MIT; the successor may not be. **Action: read the repo before planning around it.**

5. **Meshy and Anything World rigging APIs: could not verify** current existence, shape, pricing, or free tier. Tripo's is documented (free `prerigcheck`, 25 credits for auto-rigging) and AccuRIG has no public API. **Action: low priority, these are all optional paths.**

6. **BBW blend-band width.** My verified test gave a very sharp falloff (1.0/0.0 at 0.5 of the segment, 0.503/0.497 exactly at the joint) because line handles cover the full bone length. That is correct BBW behaviour, but **I did not test whether it produces acceptable deformation on a real limb with real topology.** It may need point handles at the joints instead of bone-edge handles, or a `W0` warm start, or a post-smooth. **Action: this is the single most important thing to test in v1 — compare BBW vs bone heat on the same mesh with the pose battery and see which actually scores better.** I would not be shocked if bone heat wins on well-topologized meshes and BBW only wins where limbs touch.

7. **Rigify metarig landmark fitting is designed but not prototyped.** The constrained least-squares formulation in §6.8 step 4 is sound in principle; the open question is whether it converges reliably on stylized proportions, and whether the face metarig's ~70 bones can be fitted at all without a landmark detector trained on heads. **Action: prototype the body fit in v0 and measure `fit_rms` across a dozen varied meshes before committing to the face.**

8. **Tetrahedralization robustness on generated meshes.** tetgen is verified working on a clean synthetic cylinder (0.05 s, 2,742 tets). It is *not* verified on SDF-marching-cubes output, remeshed ML-generated meshes, or anything with near-degenerate triangles. This is the main failure risk for the BBW path and it's entirely in the geometry researcher's domain. **Action: GR-1/GR-2 must be enforced, and `bind_mesh` must have a working fallback chain (it does: `[biharmonic, bone_heat, envelope]`).**

9. **Weight read performance.** There is no `foreach_get` for vertex-group weights; `v.groups` iteration is the only path, and I estimated ~0.5 s per 100 k verts without measuring it at that size. If it's worse, the workaround is baking weights to float color attributes per bone and `foreach_get`-ing those — ugly, ~50× faster. **Action: measure at 100 k and 500 k before v1's validation suite ships.**

10. **EEVEE in a headless container.** `BLENDER_EEVEE` sets without error (verified) but I did not render with it. It needs a GL context; in a container without one it may fall back, hang, or produce black. **This doesn't matter** — every validation render uses Workbench, which is verified at 0.71–0.87 s. **Action: none, just never put EEVEE in the validation path.**

11. **`armature.symmetrize` behaviour with Rigify-generated rigs.** Symmetrizing a *metarig* is safe. Symmetrizing a generated rig with 533 constraints and 114 drivers is almost certainly not — the constraint subtargets won't flip. **Policy: symmetrize before generate, never after.** Encoded in §12.3's stage order. Unverified that it actually breaks, but the design avoids finding out.

12. **glTF joint-count ceiling in real consumers.** The spec sets none; `JOINTS_n` is u8 or u16. Real limits are shader uniform budgets, and I found no authoritative table. The 128-joint warning threshold in §8.7 is my judgement call, not a sourced number. **Action: if a consumer complains, that's the data point.**

13. **Whether `rig_report` actually fits in 400 tokens on a real rig.** The §10.4 mock-up does. A rig with 40 warnings will not. **Action: the report needs hard truncation with a "+N more" tail, and the NEXT list capped at 5. Design that in, don't discover it.**

14. **`export_def_bones=True` interaction with hand-built (non-Rigify) rigs.** It filters by a collection named `DEF`. forge's invariant (§8.6) is that weights only go to DEF-collection bones, so this should just work — but **unverified for a rig where bones are named without the `DEF-` prefix but are in the `DEF` collection.** The flag's documentation is ambiguous about whether it reads the collection name or the bone-name prefix. **Action: test both. If it reads prefixes, forge must prefix its deform bones too, which conflicts with target-naming for UE5. That would be an annoying discovery late.**

---

## 17. RECONCILIATION WITH THE SIBLING DOCS

`00-environment.md`, `01-geometry.md` and `03-animation.md` landed alongside this one. Three places they touch this doc, resolved here so there is one answer, not three.

### 17.1 The GL/Vulkan startup dependency — environment is right, I got lucky

`00-environment.md` reports that Blender 5.x aborts at startup without a GL/EGL context (`Couldn't open libEGL.so.1`) and prescribes an apt preamble plus `LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe`.

Every probe in this doc ran clean without that preamble — `import bpy`, Rigify generate, and sub-second Workbench renders all worked first try. **Reason: this container already has `/usr/lib/x86_64-linux-gnu/libEGL.so.1`, `libGL.so.1` and the mesa Vulkan ICDs (`libvulkan_lvp.so`) installed.** Verified. So my numbers are real, but they are not evidence that the apt step is optional.

**Resolution: `00-environment.md`'s apt preamble is the authority.** On a clean image it is required. Every install command in §14 of this doc should be preceded by it. My timings stand because the libs were present; a fresh container will not have them.

Also confirmed independently by both docs: **the render engine enum in 5.3 is `BLENDER_EEVEE`, and `BLENDER_EEVEE_NEXT` throws.** And environment's benchmark settles the engine question harder than mine did — EEVEE on lavapipe is **43.19 s** against Workbench's **0.62 s** and Cycles CPU's **5.11 s**. EEVEE is not merely unreliable headless, it is *slower than CPU path tracing*. §10.3's "never put EEVEE in the validation path" is upgraded from caution to a hard rule, and Cycles CPU becomes the hero-render path.

### 17.2 Naming: animation's manifest wins, and rigging emits it

`03-animation.md` §4 specifies a `rig_manifest.json` that forge/animation reads, and it is non-negotiable from their side because every generator and metric is defined over it. It uses `_L`/`_R` suffixes and control names like `foot_L_ik`, `knee_L_pole`, `leg_L_props["ik_fk"]`.

This doc (§13.2 RA-1, RA-5, RA-6) specified Rigify's convention: `.L`/`.R`, `foot_ik.L`, `elbow_pole.L`, `thigh_parent.L["IK_FK"]`.

**Both cannot be right. Resolution, in rigging's favour on mechanism and animation's favour on interface:**

1. **Rigging authors internally in Rigify convention (`.L`/`.R`).** This is not a preference — `bpy.ops.armature.symmetrize`, `object.vertex_group_mirror`, and the entire Rigify generator key off Blender's suffix table, and `foot_L_ik` does not symmetrize. Changing this would cost mirroring and Rigify, which is most of the value in this doc.
2. **Rigging emits `rig_manifest.json` to animation's exact schema**, with every name as it actually is in the file. Animation never infers a name; it reads the manifest. The `mirror.prefix_pairs` field becomes `[[".L", ".R"]]` and the manifest is self-describing.
3. **The manifest carries an explicit `naming_convention` field** (`"blender" | "ue" | "mixamo" | "vrm" | "smplx"`) so animation knows which table applies without pattern-matching.
4. **`skeleton_rename_convention` regenerates the manifest**, because renaming invalidates every path in it — including the `switch` property paths, which are string data paths and the easiest thing to leave stale.

So RA-1, RA-5, RA-6 and RA-7 above are amended: the *names* are whatever the manifest says, and the manifest is the contract. The patterns in §13.2 are rigging's defaults, not a promise animation should hardcode.

### 17.3 Manifest fields this doc did not cover — gaps to close

Animation's schema requires seven things the tool surface in §11 does not currently produce. All are cheap; all must be added to `rig_report` and to a new `rig_manifest` tool.

| field | what rigging must do | where it comes from |
|---|---|---|
| `cog_bone` | name a bone whose world head tracks the centre of mass closely enough for support-polygon and gravity checks | the pelvis/hips bone. Validate it: compare its world head against the mesh's volume centroid at rest; **warn if the offset exceeds 8% of character height** |
| `chains` | named ordered bone lists for spine, each arm, each leg, tail, etc. | **rigging already knows these** — they are the template's chain structure, and `skeleton_from_template` builds them. They just were not being emitted. For Rigify rigs, derive from the `rigify_type` groupings |
| `contacts` | per foot: `heel`, `ball`, `toe` bones plus the `plant_bone` that is actually keyed when planted | §8.3's `control_foot_roll(auto_pivots=True)` **already computes all three pivot positions** from foot geometry. It currently creates them as unnamed internal pivots. Fix: name them `heel.L`/`ball.L`/`toe.L` and expose them. Animation cannot measure foot slide without these, which means it cannot validate locomotion at all |
| `rotation_modes` | per-bone rotation mode, declared and then never changed | emit from `pose.bones[*].rotation_mode`. RA-9 already said not to change them; now it is machine-checkable |
| `limits` | per-bone rotation limits in radians, e.g. `knee_L: {x: [-2.44, 0.0]}` | read from each bone's `LIMIT_ROTATION` constraint. Rigify sets these; hand-built rigs need `control_ik_chain` to add them. **New requirement on rigging: every IK chain gets anatomical `LIMIT_ROTATION` on its joints**, because animation's solver reads them as hard bounds |
| `bone_lengths_m`, `rest_matrices` | per-bone length and 4×4 rest matrix | `bone.length` and `bone.matrix_local`. Trivial, just not emitted. Animation's retargeting is defined relative to these |
| `keyable` | explicit whitelist of bone → animatable channels; animation **refuses** to key anything not listed | derive from the control-bone set plus every custom property. This is the cheapest guard against an agent keying a `DEF-` bone and producing a double transform, and it is strictly better than my RA-10 "what is NOT keyable" prose, which relied on animation reading a list of prefixes correctly |

`shape_keys` also differs: animation wants `visemes: ["AA","EE","OO"]` while §9 produces ARKit's 52 plus VRM's five-vowel bindings. **Resolution: emit both** — `shape_keys.arkit` with the 52 names and their filled/empty status, and `shape_keys.visemes` as the VRM-style short names mapped to ARKit combinations, so animation gets the simple handles it wants without losing the ARKit schema a face-capture pipeline needs.

### 17.4 Added tool

```python
def rig_manifest(rig: Handle, meshes: list[Handle] = (),
                 path: Optional[str] = None,
                 validate: bool = True) -> dict:
    """Emit `rig_manifest.json` to the schema in 03-animation.md section 4 --
    the contract forge/animation reads. Must be regenerated after any rename,
    any control addition, and any export with apply_naming=True, because every
    `switch` and `keyable` entry is a string data path that a rename
    invalidates silently.

    Adds, beyond 03-animation.md's required set:
      naming_convention: which bone-name table applies, so animation never
                         pattern-matches names.
      shape_keys.arkit:  all 52 ARKit slots with filled/empty status.
      shape_keys.visemes: VRM-style short names as weighted ARKit combinations.

    `validate=True` checks animation's nine hard requirements before writing and
    refuses on failure rather than emitting a manifest that lies:
      1 root motion bone unparented at origin, identity rest, no weights
      2 cog_bone head within 8% of character height of the rest volume centroid
      3 every bone reachable from root belongs to exactly one named chain
      4 heel/ball/toe + plant_bone present per foot
      5 rotation_modes complete for every keyable bone
      6 mirror pairs resolve for every sided bone
      7 ik switch property paths resolve and the IK targets are keyable controls
      8 keyable whitelist excludes every DEF-/ORG-/MCH- bone and every driven channel
      9 rest pose declared as tpose or apose, with rest matrices present

    Returns:
      {ok, path: str|None, manifest: dict,
       chains: int, contacts: int, keyable_bones: int,
       fails: [{requirement: int, detail: str, fix_tool: str}],
       warns: list[str]}
    """
```

Two consequences for the build plan in §15, both small:
- **v0 gains `rig_manifest`.** Without it animation is blocked on its first task, so it is not optional and it is maybe an hour of work once the chain data is already in hand.
- **v1's `control_ik_chain` gains anatomical `LIMIT_ROTATION`** on every chain joint, and `control_foot_roll` names and exposes its heel/ball/toe pivots instead of keeping them internal.

### 17.5 Geometry doc — no conflicts

`01-geometry.md` independently confirms Python 3.13 matching Blender 5.1+'s pin, `bpy==5.3.0` as the install, and the `BLENDER_EEVEE` enum. Its MCP architecture section argues against a pure `execute_blender_python` surface in favour of typed tools with compact returns, which is the same conclusion §11 reaches from the rigging side (handles and numbers, never vertex dumps). The GR-1..GR-15 requirements in §13.1 are written as a numbered list specifically so that doc can reference them; `rig_feasibility` is the tool that enforces them and it belongs in v0.

---


## SOURCES

Verified in-container (Blender 5.3.0 via `pip install bpy`, `libigl`, `tetgen`): bone collections API, `align_roll`/`calculate_roll`, `symmetrize`, Rigify headless generation and its bone/constraint/driver counts, `parent_set` armature types, bone-heat timing and partition-of-unity, vertex-group operator parameter lists, `data_transfer` enums, modifier and constraint type lists, `CORRECTIVE_SMOOTH` and Armature modifier properties, glTF/FBX exporter option names and defaults, `igl.bbw`/`boundary_conditions`/`harmonic`/heat-geodesics signatures and timings, the `boundary_conditions` surface-mesh failure, the tet + Steiner-sample recipe, `BVHTree.overlap` self-intersection counts, `bmesh.calc_volume` ratios, Workbench `FLAT`/`VERTEX` heatmap rendering, and all install footprints.

Web:
- [VAST-AI-Research/UniRig](https://github.com/VAST-AI-Research/UniRig) — licence, requirements, CLI, SkinTokens
- [UniRig paper, arXiv 2504.12451](https://arxiv.org/html/2504.12451)
- [Blender bone collections dev docs](https://developer.blender.org/docs/features/animation/armatures/bone_collections/) and [manual](https://docs.blender.org/manual/en/latest/animation/armatures/bones/bone_collections.html)
- [Replacing bone layers + groups with bone collections #108941](https://projects.blender.org/blender/blender/issues/108941)
- [zhan-xu/RigNet](https://github.com/zhan-xu/RigNet), [project page](https://zhan-xu.github.io/rig-net), [pKrime/brignet](https://github.com/pKrime/brignet) — GPLv3, unmaintained
- [zeljkovranjes/auto-rigger](https://github.com/zeljkovranjes/auto-rigger) — the model/licence table for the 2025–26 auto-riggers
- [MagicArticulate](https://www.researchgate.net/publication/394637914_MagicArticulate_Make_Your_3D_Models_Articulation-Ready), [RigAnything](https://www.researchgate.net/publication/394036203_RigAnything_Template-Free_Autoregressive_Rigging_for_Diverse_3D_Assets), [Make-It-Animatable](https://www.researchgate.net/publication/394566208_Make-It-Animatable_An_Efficient_Framework_for_Authoring_Animation-Ready_3D_Characters), [Puppeteer](https://arxiv.org/pdf/2508.10898), [AniGen](https://arxiv.org/html/2604.08746), [Rigel3D](https://arxiv.org/html/2605.13129v1)
- [VRM 1.0 humanoid spec](https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_vrm-1.0/humanoid.md), [vrm.dev humanoid](https://vrm.dev/en/vrm1/humanoid)
- [three-vrm mixamoVRMRigMap](https://huggingface.co/spaces/AIbee999/CharacterGen_test/blob/604b72580a799f334d82bcc49c9075dce80694f4/render_script/three-js/src/mixamoVRMRigMap.js) — the 52-pair Mixamo→VRM map
- [UE5 Manny skeleton bone list](https://cosindra.ai/guides/ue5-manny-skeleton) (community, 87 bones), [Reallusion CC↔UE skeleton comparison](https://manual.reallusion.com/Metahuman-Interoperability/Content/MetaHuman-Interoperability/17-Metahuman-Interoperability/Skeleton-between-CC-and-UE-Characters.htm) (89 bones, 5 spine / 2 neck), [Reallusion twist-bone hierarchy report](https://cdn-www.reallusion.com/FeedBackTracker/Issue/Inconsistent-twist-bone-hierarchy-in-Unreal-Engine-skeletons) (twist bones are leaf siblings)
- [Chiro UE4 bone groups](https://vespero-group.github.io/chiro-ue4/select-bone-group/) and [Epic forums: mannequin IK bones](https://forums.unrealengine.com/t/explanation-of-the-mannequin-ik-bones/45166) — the 7 IK bones
- [Auto-Rig Pro game-engine export docs](https://www.lucky3d.fr/auto-rig-pro/doc/ge_export_doc.html) — UE4 vs UE5 spine/neck counts
- [smplx joint_names.py](https://github.com/vchoutas/smplx/blob/main/smplx/joint_names.py) — the 55-joint order
- [Unity ConfiguringtheAvatar](https://docs.unity3d.com/Manual/ConfiguringtheAvatar.html) — 15-bone minimum, T-pose
- [Apple ARFaceAnchor.blendShapes](https://developer.apple.com/documentation/arkit/arfaceanchor/blendshapes) (fetch returned title only), [arkit-face-blendshapes](https://github.com/suchipi/arkit-face-blendshapes)
- [glTF Tutorial: Skins](https://github.khronos.org/glTF-Tutorials/gltfTutorial/gltfTutorial_020_Skins.html), [UE forums: Interchange glTF importer truncates to 4 influences](https://forums.unrealengine.com/t/interchange-gltf-importer-truncates-skeletal-mesh-bone-influences-to-4-per-vertex/2745391)
- [libigl Python bindings](https://libigl.github.io/libigl-python-bindings), [PyPI libigl](https://pypi.org/project/libigl/)
- [skeletor on PyPI](https://pypi.org/project/skeletor/) — mesh contraction, edge collapse, TEASAR, wavefront, mean-curvature skeletons
- [Tripo rig API](https://developers.tripo3d.com/en/models/rig), [AccuRIG](https://cdn-www.reallusion.com/auto-rig/accurig)
- [Mikan Delta Mush reference](https://citrus-software.github.io/mikan-docs/references/deformers/deltamush), [Blender temp-modifier-deltamush-experimental](https://miikahweb.com/fi/blender/git-statistics/branches/temp-modifier-deltamush-experimental) (2015, never merged)
- [bpy on PyPI](https://pypi.org/project/bpy/), [Building Blender as a Python Module](https://wiki.blender.org/wiki/Building_Blender/python_module/)
