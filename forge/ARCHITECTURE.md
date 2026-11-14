# forge — architecture

A free, self-hosted MCP server that Claude drives to build, rig and animate production 3D assets.

Synthesis of `research/00-environment.md` (measured baseline), `research/01-geometry.md`,
`research/02-rigging.md`, `research/03-animation.md`. Those are the detail. This is the decision record.

---

## 1. The thesis

Claude is good at writing code and bad at seeing. Every text-to-3D product bets on the second
problem being solved by a generative model. It isn't — the open checkpoints produce isosurface
blobs with no edge loops, which is unriggable and unanimatable by construction.

forge bets the other way: **the generator is a model that writes code, and the feedback is a
render loop fast enough to run on every call.** Measured, no GPU:

| Pass | Cost |
|---|---|
| Workbench inspection render, 512² | 0.62 s |
| Weight heatmap, 400², argmax per bone | 0.87 s |
| Cycles CPU hero render, 512²/32spp | 1.95–5.11 s |
| 2048² normal bake, 8 spp | 1.90 s |
| `.blend` checkpoint save / load | 9 ms / 13 ms |
| Full animation metric suite, 40 bones × 240 frames | 0.04 s |
| 24-frame Workbench playblast @ 480×270 | 2.7 s |

Sub-second sight and 9 ms undo are the whole product. They mean forge can put an image in every
mutating response and auto-checkpoint before every destructive one, so Claude works the way an
artist works: do a thing, look at it, fix it, keep the good version.

---

## 2. Stack

**`pip install bpy==5.3.0`, in-process, one OS process per session.** Not `blender --background`.

- `requires_python == 3.13.*`. Container is 3.13.18. Exact match.
- 427 MB wheel, ~930 MB installed, 0.55 s import.
- The pip module loads the factory startup file and **keeps a window and 11 screens**. The CLI
  does not. That window is what makes the Graph Editor operators reachable (§5).

Satellites, all verified installed and working on cp313:

| Package | Job | Note |
|---|---|---|
| `manifold3d` 3.5.5 | guaranteed-manifold CSG | 3 ms sphere − cube |
| `trimesh` 5.2.1 | numeric suite | 8 ms watertight/euler/volume/area |
| `xatlas` 0.0.11 | deterministic UV unwrap | 0.32 s |
| `pymeshlab` 2025.7 | decimation, sampling | **needs `libOpenGL.so.0`** or 46 filters vanish silently |
| `tetgen` | tets for BBW | `--no-deps` → 908 KB; without → 565 MB of VTK |
| `build123d` 0.13.0 | parametric CAD | phase 2 |

Rejected: `libigl` (no cp313 wheel), `libfive` (PyPI release is a 2020 alpha), PyPI `sdf`
(**a scientific data format package, not the SDF modelling library** — fogleman's is git-only).

### Required apt preamble

The bpy wheel does not declare its GL dependency. Blender 5.x aborts at startup without a
GL/Vulkan context **even headless**:

```
Couldn't open libEGL.so.1
gpu.vulkan | WARNING Unable to initialize a Vulkan 1.1 instance.
```

```bash
apt-get install -y libegl1 libgl1 libglx-mesa0 libgl1-mesa-dri mesa-vulkan-drivers \
  libvulkan1 libopengl0 libxi6 libxxf86vm1 libxfixes3 libxrender1 libsm6 libxkbcommon0 xvfb
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
export VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json
```

This step is **not optional on a clean image**. (02-rigging.md §17 infers it might be, because its
probes ran after this session had already installed mesa. It had not been preinstalled.)

### EEVEE is banned

Three independent benchmarks in this container, same ordering every time: EEVEE on software
Vulkan is **12–30× slower than CPU path tracing** for the same frame (23–43 s vs 1.95–5.11 s).
The instinct that EEVEE is the fast preview engine is exactly backwards without a GPU.
**Workbench for structure, Cycles for light transport, EEVEE hard-disabled** unless
`forge_capabilities` reports a real GPU.

Note the enum is `BLENDER_EEVEE` in 5.3. `BLENDER_EEVEE_NEXT` was removed and raises `TypeError`.

---

## 3. Server shape

MCP revision **2026-07-28**, confirmed against the spec, not remembered:

- Stateless. No `initialize` handshake, no `Mcp-Session-Id`. The changelog directs servers needing
  cross-call state to use **"explicit, server-minted handles passed as ordinary tool arguments"**
  (SEP-2567) — which is the session-handle design forge wanted anyway.
- `server/discover` is a MUST. All results carry `resultType`. `CacheableResult` needs
  `ttlMs` + `cacheScope`.
- **Sampling, Roots and Logging are deprecated.** Log to stderr. Tasks moved to an extension.
- Content blocks: `text`, `image` (base64 + mimeType), `audio`, `resource_link`, `resource`,
  plus `structuredContent` against `outputSchema`. **No video type** — so contact sheets are the
  primary visual and MP4s go back as `resource_link`.

**Read the installed MCP Python SDK's own migration docs before the first import.** `mcp` 2.3.0 and
`fastmcp` 4.2.0 both exist on PyPI and third-party writeups disagree about whether `FastMCP` was
renamed. Do not write this from memory.

### Hybrid authoring: IR for structure, tools for repair

Each domain gets a declarative IR document Claude authors in one shot, plus typed incremental ops,
plus one unapologetic `forge_exec` escape hatch. The split is empirical, not aesthetic: a 90-node
asset or a 40-key clip is one coherent document, but a weight-fix or arc-fix feedback loop cannot
be authored before the loop has run.

### Errors are the product

A geometry probe failed with `RuntimeError: Object "Sun" is not a mesh` on `bpy.ops.object.bake`
**purely because adding a light left the Sun as the active object.** That is the canonical
`bpy.ops` context failure and Claude will hit it constantly.

So every forge error returns `state_at_failure` (active object, mode, selection, engine) plus a
`likely_cause` and a concrete `suggested_fix`. The classified-failure table is the actual
intelligence in this product, more than any single modelling feature.

### Token economy

Never return geometry. Handles and numbers only, paginated, field-projected. Images are
pixel-priced at roughly `w×h/750` tokens, so: pose thumb 384² ≈ 200 tokens, 6×4 contact sheet
1440×1080 ≈ 2100, **hard cap ~2500 image tokens per result, ≤3 blocks**, composites by default.
Three 512² PNGs a turn will eat the context before an asset is finished.

---

## 4. Rigging: Rigify, not a learned model

**Rigify ships inside the bpy wheel and generates headless.** Verified: stock human metarig
(159 bones) → `bpy.ops.pose.rigify_generate()` → **706-bone rig, 533 constraints, 114 drivers,
220 custom shapes, 23 bone collections with per-limb IK/FK/Tweak layers, in 6.5 s on 4 CPU cores.**

A metarig is just an armature carrying `rigify_type` strings on its pose bones. Fit those bones to
landmarks and you inherit IK/FK switching, foot rolls, finger drivers and a face rig. Every learned
auto-rigger hands over a bare skeleton plus weights and leaves the control rig unbuilt.

Path: symmetry-plane fit → geodesic extremity detection (head/hands/feet) → medial centreline →
constrained least-squares metarig placement → 5° pre-bend → rolls → symmetrize → generate → bind.

Learned crop surveyed and shelved as an optional v2 backend: **RigAnything is Adobe noncommercial**
(disqualifying), RigNet is GPLv3 and pinned to torch 1.12 since 2023, UniRig is MIT and current but
needs `spconv` + `flash-attn`, which its own README says will probably fail to install.

Weights: `bone_heat` default (0.62 s on 11k verts, but partition sums 0.979–1.000 — **always
`normalize_all` after**). BBW via `igl.bbw` works but only on tets; on a raw surface it throws
`boundary_conditions: failed to compute`. Recipe: ~12 Steiner samples per bone segment → tetgen →
BBW in 0.08 s with row sums exactly 1.0.

Export flags that silently ruin assets: **`export_def_bones`** is what turns 706 bones into the 160
that export; `export_influence_nb=4` for glTF's limit; `export_force_sampling` is mandatory on
constrained rigs; and FBX's **`add_leaf_bones=True` default breaks every UE import.**

`armature.layers` and `pose.bone_groups` are **gone** in 5.3. Bone collections only.

---

## 5. Animation: the Graph Editor is reachable

The biggest find in the research. Every `bpy.ops.graph.*` operator fails `poll()` until you retype
an area to `GRAPH_EDITOR` and wrap the call in `context.temp_override(window, screen, area, region,
space_data)` — which is only possible because the pip module keeps a window. Then **`decimate`,
`ease`, `breakdown`, `blend_to_neighbor`, `match_slope`, `push_pull`, `shear`, `butterworth_smooth`,
`euler_filter`, `equalize_handles`** all run headless.

Those are the pro animator's verbs, free. And they beat hand-rolled equivalents: `graph.decimate`
took 240 keys → 47 in 0.036 s within its error bound, where hand-written RDP kept 74 for the same
tolerance — because Blender removes the least-influential key and re-evaluates the actual Bézier,
while RDP measures point-to-chord distance. **Don't write RDP. Call decimate.**

This is also risk #2 (§8): it works because of an implementation detail, not a documented API.

**`action.fcurves` was deleted in Blender 5.x.** Verified `hasattr(action, 'fcurves') == False`.
It is slot → layer → strip → channelbag now, and every tutorial older than 4.4 is actively wrong —
including anything a model learned from.

The animation IR makes `stage ∈ {block, breakdown, spline, polish, offset}` first-class, because
that is how an animator actually works and it maps 1:1 onto an agent's iterate-and-critique loop.
It is also the thing that stops Claude's default behaviour: two keys and a lerp.

Easing presets are measured handle fractions, not vibes: `snap` = `out_frac 0.10, in_frac 0.70`,
which puts 30% of the move in by frame 3 of 13 and 90% by frame 9. Blender's default handle extent
is exactly 1/3 of the span.

Metrics derive rather than being invented where possible — `gravity_error` falls out of Garcia's
Odd Rule (g/(2·fps²) = 17.0 mm/frame² at 24 fps, matching his published figure to three decimals),
and `foot_slide`'s 10 mm tolerance sits deliberately above the 1.5 mm decimation tolerance so
decimation cannot itself cause a failure.

---

## 6. The cross-domain contract

Resolved between 02 and 03, which initially disagreed on naming.

- **Rigging authors internally in Rigify convention** (`.L`/`.R`, `foot_ik.L`,
  `thigh_parent.L["IK_FK"]`). Non-negotiable: `foot_L_ik` does not symmetrize and Rigify will not
  consume it.
- **Rigging emits `rig_manifest.json`** in animation's exact schema, so animation never infers a
  name. **forge never pattern-matches a bone name** — everything resolves through the manifest.
- Manifest carries: root motion bone at origin, COG bone, named chains, heel/ball/toe contact
  markers with plant bone, declared rotation modes, explicit mirror map, IK switch property paths,
  a `keyable` whitelist forge refuses to write outside, declared rest pose, bone lengths and rest
  matrices, and `naming_convention`.
- This moves `rig_manifest` into **v0** — animation is blocked on its first task without it — and
  requires v1's `control_ik_chain` to emit anatomical `LIMIT_ROTATION`, which animation's solver
  reads as hard bounds.

**Geometry owes rigging a 15-point contract** (`02-rigging.md` §13.1). The load-bearing item:
**≥3 edge loops across every bend joint.** Below that, no weight distribution avoids volume
collapse. Also: manifold input or bone heat fails silently; limbs must not touch at rest or weights
bleed across the contact; no self-intersection or tetgen refuses and the entire BBW path dies.

These are geometry failures that will be reported as rigging bugs every single time, so
`rig_feasibility` ships in v0 as a gate that blames the right thing.

---

## 7. Where ML actually belongs

**Watch it. Don't build on it.** This is the conclusion from all three docs independently.

- Best open mesh generator is **TRELLIS.2-4B** (Microsoft, MIT) — but ≥24 GB NVIDIA VRAM, image
  input only, and its own model card admits meshes "may have small holes or minor topological
  discontinuities."
- **Hunyuan3D 2.1 is the last open-weights release.** Everything after is hosted-only, and 2.1's
  licence excludes the EU, UK and South Korea for commercial use.
- Native quad-topology generation is real and entirely paper-stage: **QuadGPT** (ICLR 2026),
  Mesh-Pro, QuadLink. No usable weights for any of them. `forge_remesh(method="learned")` is a
  reserved slot that raises `NotImplementedError`.
- The boring tool wins anyway: QuadriFlow took 153k faces → 7,514 in 6.6 s, Euler 2, genus 0,
  **zero** non-manifold edges, zero degenerate faces.
- One worth wiring in later: **PartCrafter** (NeurIPS 2025) produces separable multi-part meshes
  from one image, ~16 parts, no segmentation step. Part separation is precisely what rigging needs
  and cannot get from a monolithic blob.
- The genuinely interesting open question: **SILK** (CVPR 2025 workshop) found a single transformer
  encoder matches diffusion models for in-betweening, and that data and representation matter more
  than architecture. That implies a compact in-betweener trained on CMU alone — commercially clean
  weights, which nobody has published. First thing to test in v2.

---

## 8. Risks

1. **Motion data licensing, and it is not close.** Nearly every free motion dataset and nearly every
   ML checkpoint traces to AMASS/SMPL, which are explicitly non-commercial and bar training models
   for commercial use. The *code* is MIT (MoMask, MDM, CondMDI, HumanML3D — LICENSE files fetched);
   the **weights are AMASS derivatives**, so "open source" means non-commercial in practice.
   LAFAN1 is CC BY-NC-**ND**, so retargeting it is a prohibited derivative, not merely restricted.
   The one clean source is **CMU**: free for all uses including commercial products, may not be
   resold directly even converted. ⇒ forge needs per-clip provenance tracking propagated through
   retarget/blend/export, and `export(commercial=True)` must refuse and name the tainted clip.
2. **Two load-bearing undocumented behaviours.** The Graph Editor `temp_override` (§5) and the pip
   module's window both depend on implementation details, not API. Pin the version, keep an RDP
   fallback, put both in the test suite as canaries.
3. **The Python pin is exact.** `bpy==5.3.0` requires `python==3.13.*`. The day this image moves to
   3.14, forge is dead until Blender ships cp314. Pin the interpreter in the image; keep
   `blender --background` documented as the worse fallback.
4. **API churn, already biting.** Three breaking changes found in one afternoon: the EEVEE enum,
   `action.fcurves` deleted, bone collections replacing layers. `Material.use_nodes` is deprecated
   for removal in 6.0. Blender ships a minor every ~2 months. Budget real time for a version-gated
   shim.
5. **`bpy` segfaults on pathological geometry with no Python exception.** The supervisor plus
   auto-checkpoint has to be v0, not retrofitted.
6. **Memory, not time, is the detail ceiling** — roughly 1 GB per 4M faces against 15 GB with
   Blender resident.
7. **Draco and MeshOptimizer bridge `.so`s are absent from the wheel.** No GLB compression
   in-process; post-process with `gltf-transform` on Node.

---

## 9. Decisions that are Wren's, not mine

- **GPL-3.0.** The bpy wheel is GPL-3.0 and forge imports it in-process, so forge is GPL-3.0 as
  distributed. Fine for a free self-hosted tool. Fatal if this is ever meant to go closed, and
  unpicking it later means ripping Blender out. Needs to be written into the README now.
- **Commercial intent.** If forge output will ever be sold, the motion pipeline is CMU-only from
  day one and the provenance system is v0, not v1. If it is personal/research, AMASS opens up and
  that's three days saved.
- **The name.** `forge` is a placeholder.

---

## 10. v0 — what makes it useful at all

Scope is deliberately small. Everything here is verified-possible in this container.

**Geometry** — session + capabilities, asset IR apply, `forge_exec`, scene query, numeric inspect,
Workbench/Cycles render, checkpoint/rollback/diff, xatlas UV, glTF export + validate.

**Rigging** — Rigify metarig fit + generate, bone heat bind with `normalize_all`,
static validation, **argmax weight heatmap**, **`rig_manifest`**, `rig_feasibility` gate,
GLB/FBX export with the four flags above set correctly.

**Animation** — IR + compiler + pose library, critique suite, contact sheet. Nothing else.
No generators, no retargeting, no ML.

**Acceptance tests, not metrics:**
- Geometry: a blockout survives remesh → UV → bake → export with zero non-manifold edges.
- Rigging: a clean A-pose humanoid produces a 160-joint GLB whose argmax render shows every bone
  owning one contiguous region.
- Animation: **the loop closes** — the pose battery flags an elbow, `weight_blend` fixes it, the
  re-render confirms it, the score goes up. Not any single number.

v1 adds procedural generators, the full metric suite, retargeting, BBW, control rig generation,
LOD chains, PBR authoring. v2 adds ML backends, sim, facial, and the CMU-only in-betweener
experiment.
