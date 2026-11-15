# 08 — Form quality: smoothness, hands, spine, priors, wrap, fitting

Siblings: `05-face-rigging.md` (face topology, eyes, blendshapes, spine *design*), `06-reactive-dynamics.md` (secondary motion). This doc owns **form**: geometry, smoothness, anatomy, hands, spine. Colour, materials, lighting are out of scope.

Every number marked **[measured]** was produced in this container (bpy 5.3.0, CPython 3.13.18, 4 cores, no GPU) by a script under `forge/proto/form/`, with the raw output in `forge/proto/form/results/`. **[read]** means from a web page or a paper abstract. **[recalled]** means from memory, not re-verified this session.

---

## 0. Verdict

1. **The A/B is won, and it isn't close, but it's not "10x" on a bounded rubric.** Same anatomy, same joint positions, same face count (~13.4k quads). On a 15-metric blind form rubric (0–100) the kit hand scores **83.8**; a competent raw-bpy hand (capsules + voxel remesh + bone heat, i.e. the kit's own `examples/02` recipe) scores **43.5**. That's **16.2 vs 56.5 defect points: 3.5x fewer defects**. Per defect class the gaps are an order of magnitude: **39x fewer poles**, **3.4x lower curvature-jump p95**, **0 vs 79 self-intersections per bent knuckle**, **19/19 vs 0/19 bones whose +X curls toward the palm**, worst knuckle volume at 90° **0.88 vs −0.29** (the raw hand's ring MCP folds through its own joint centre). [measured] (§2)
2. **The optimizer loop is practical on 4 cores. This is the real unlock.** CMA-ES (60-line numpy, no deps) recovers 6 hidden hand parameters from two silhouettes to **mean IoU 0.993 in 360 evals / 11.5 s**, every parameter within 2.3 % of its range. Fitting the cage so the *subdivided* skin hits tape-measure targets: **4.1 % → 0.26 % error in 306 evals / 11.9 s**. Fitting skin-weight blend parameters against the knuckle battery: **knuckle self-intersections 88 → 0, worst knuckle volume 0.83 → 0.89, 70 evals / 64 s**. A numpy point-splat silhouette costs **28.6 ms**; a Workbench 128² render costs **568 ms**, so don't put renders in the loss. [measured] (§8)
3. **Hands: topology + bone roll + dual-quaternion skinning + a dorsal/volar-aware weight blend is the whole recipe.** The generator (`hand.py`) emits a 100 %-quad cage, 840 verts, 76 deliberate poles, genus 0, in **40 ms**. Corrective Smooth — the modifier everyone reaches for — **made every knuckle worse** (worst-knuckle volume 0.83 → 0.60). DQS alone took it 0.66 → 0.83. [measured] (§3)
4. **Smoothness: don't damage it in the first place.** Of every fixer measured on a voxel-remeshed, noisy hand, none got back to the procedural skin's curvature continuity without either shrinking it (Laplacian, Corrective Smooth: volume −2.6 to −8 %) or moving it ≥1 mm (QuadriFlow). **Taubin λ/μ is the only smoother that preserves volume (1.000) and detail (0.08 mm mean)**, but it only gets curvature-jump from 5.3 to 3.3. **QuadriFlow cancels silently** on the raw noisy voxel mesh and works after a Taubin pass; QuadriFlow 12k + subsurf gave the smoothest surface measured (curv-jump p95 1.05) at 1.1 mm p99 error. [measured] (§4)
5. **Spine: bone count stops mattering at ~5; B-bones buy the smoothness of 3x the bones; flexion loses ~10 % torso volume whatever you do.** 5 FK bones with 6-segment B-bones match 17 FK bones on bend smoothness (peak added curvature 3.4 vs 3.4 m⁻¹). Concentrating flexion in the lumbar levels (anatomical distribution) raises flexion volume 0.878 → 0.905. No bone layout fixes the remaining loss: that's a belly corrective, not more joints. [measured] (§5)
6. **Parametric body models: only two are clean.** SMPL-X, SUPR, STAR, MANO are MPI non-commercial; FLAME's licence wasn't confirmed but historically follows suit. **Anny (NAVER LABS, Nov 2025) is Apache-2.0** and covers all ages; **Meta's MHR is Apache-2.0**; **MakeHuman's base mesh and targets are CC0**. Build our own generators anyway — the hand generator here proves you can — and use Anny/MakeHuman as *reference targets* for the optimizer. [read] (§6)
7. **Wrap works, and it's the ML bridge.** Fitting the procedural prior to a blob first (CMA-ES chamfer, 243 evals / 10.3 s), then iterating cage vertices against the residual of their *limit-surface* positions (6 iterations, 0.08 s), lands the kit topology on a capsule-blob hand at **0.30 mm mean / 0 flipped faces**. Naive shrinkwrap leaves a **22 mm p99** gap and 3.2 % flipped faces. The rig transfers because vertex indices never change; the transferred weights are the weak link (worst knuckle 0.45, 0 self-intersections). [measured] (§7)

---

## A. What ran, what was read

| Ran here | Script | Result file |
|---|---|---|
| Procedural hand + forearm generator, rig, analytic weights, 6-pose library | `hand.py` | — |
| Raw-bpy control hand | `baseline_hand.py` | — |
| Full blind battery, 7 arms (kit / kit_fit / kit_heat / kit_lbs / kit_cs / base / base_dqs) | `ab_hand.py` | `results/ab_*.json`, `results/ab_hand_kit_top_vs_raw_bottom.png` |
| Form-quality metrics + rubric | `formq.py` | — |
| Weight-blend parameter sweep, 26 configs | `sweep_weights.py` | `results/sweep_weights.json` |
| CMA-ES (numpy) + three fitting problems | `cmaes.py`, `fit_hand.py` | `results/fit_*.json` |
| Spine: 12 rig variants × 4 poses | `spine.py` | `results/spine.json` |
| Smoothing / remeshing benchmark | `smooth_bench.py` | `results/smooth_bench.json` |
| Topology transfer prototype | `wrap.py` | `results/wrap.json`, `results/wrap_blob_to_kit_topology.png` |

Run any of them as `/home/user/bpyenv/bin/python forge/proto/form/<script>.py ... <out>`. `smooth_bench.py` optionally reads `FORM_PYLIB` for `pynanoinstantmeshes` (pip-installed to a scratch `--target`, not into the venv).

Read only: licence pages (§6), anatomy numbers marked [recalled], Rigify finger rig-type docs, everything in §9.

---

## 1. The form-quality rubric

`formq.py`. Everything is computed on the **evaluated** mesh (armature + subsurf applied, world space), because that's what renders and exports. One call each:

| Metric | How | Why it's the right blind signal |
|---|---|---|
| `quad_frac`, `pole_frac`, valence histogram | interior vertices with valence ≠ 4 / all interior | Every pole is a spot where Catmull-Clark loses G2. You want tens, placed on purpose, not thousands |
| `quad_angle_dev_p95` | corner angle deviation from 90° | Skewed quads shade badly and pinch under deformation |
| `normal_dev_p99` | angle between edge-adjacent face normals | Kinks and voxel stair-steps live in the tail |
| `curv_jump_p95` | cotangent-Laplacian mean curvature, |H_i − H_j| across each edge / median |H| | **G2 continuity, numerically.** This is the metric that best separates "expensive" from "blobby" |
| `zebra_jump_p95` | zebra analysis without rendering: stripe phase φ = acos(r·axis), r the reflected view vector; per-face gradient of φ; its jump across edges / median | Broken reflection lines = G1/G2 breaks, the exact thing a surfacer checks |
| `knuckle_isect_mean` | BVH self-overlap (adjacent pairs excluded) per single joint at 90° | Fold-through at the crease |
| `full_curl_isect` | all 12 finger joints at 90° + thumb at 60° | Includes legitimate tip-to-palm contact, so low weight |
| `curl_joint_vol_min` | **local volume per joint**: signed cone volume from the posed joint centre over the faces within ~0.6 finger-widths of it, posed / rest | Rigid hinge = 1.0 exactly (rotation about the centre preserves it). < 1 = pinch. < 0 = the surface folded through the joint |
| `curl_vol_ratio_min`, `curl_strain_p99_max` | whole-mesh volume, edge-length strain | Rubber-sheeting |
| `twist_vol_min` | hand pronated 90°, local volume at wrist and mid-forearm | Candy-wrapper detector |
| `proportion_err` | tape-measure skin measurements vs anthropometric targets (hand length, knuckle breadth, per-finger length, PIP breadth) | Proportions measured on the *skin*, not the skeleton |
| `pose_battery_isect` | mean over the 6-pose library | Saturates at 0 for everyone (fist contact) — currently uninformative, kept for the record |
| `pose_crease_gain` | mean posed `normal_dev_p99` − rest | New sharp creases introduced by posing |

`rubric(flat)` maps each to 0..1 between a "good" and "bad" threshold, weighted, to 0–100. Missing metrics score 0 on purpose: an asset that can't be posed has no deformation quality.

**Honest caveat:** I set the thresholds before the A/B, then changed two things after seeing results — split the curl self-intersection metric into single-joint and full-curl (full curl is dominated by fingertip-palm contact that both arms have), and added `pose_crease_gain` after noticing in a render that wide blends fold the dorsal MCP skin. Both changes are defensible and both are visible in `formq.RUBRIC`. Re-weight it if you disagree; the raw metrics are in the JSON.

Cost: rest metrics 0.56 s, full battery (14 single-joint bends, full curl, 6 poses, twist, wrist flex) **1.9 s** on 13.4k quads. [measured]

---

## 2. The A/B — hand + forearm with rig

**Control arm** (`baseline_hand.py`): cylinders per phalanx, spheres at joints, a bevelled scaled cube palm (charitable — the unbevelled cube is worse), cone forearm, join, voxel remesh at the largest voxel size that keeps genus 0 (searched: 3.0 mm), shade smooth, armature with Blender's default roll at the **same joint positions as the kit**, bone-heat weights, limit 4, normalise. That's exactly the kit's own `examples/02_rig_character.py` recipe applied to a hand. To keep the comparison about structure, the control is posed with the anatomically correct flexion axes converted into its bones' local frames (otherwise its fingers curl sideways — see roll below).

**Kit arm** (`hand.py`): procedural quad cage → analytic weights → DQS armature → Subdivision 2. `kit_fit` uses the CMA-ES-fitted blend parameters.

| metric | kit_fit | kit | kit_heat | kit_lbs | kit_cs | base_dqs | base |
|---|---|---|---|---|---|---|---|
| **SCORE (0–100)** | **83.8** | 80.9 | 77.8 | 69.2 | 66.0 | 51.5 | **43.5** |
| pole_frac | 0.0057 | 0.0057 | 0.0057 | 0.0057 | 0.0057 | 0.2252 | 0.2252 |
| quad_angle_dev_p95 (°) | 24.4 | 24.4 | 24.4 | 24.4 | 24.4 | 31.8 | 31.8 |
| normal_dev_p99 (°) | 21.3 | 21.3 | 21.3 | 21.3 | 21.3 | 34.8 | 34.8 |
| curv_jump_p95 | **1.90** | 1.90 | 1.90 | 1.90 | 1.90 | 6.36 | **6.36** |
| zebra_jump_p95 | 1.59 | 1.59 | 1.59 | 1.59 | 1.59 | 3.20 | 3.20 |
| knuckle_isect_mean | **0** | 0 | 57.9 | 57.0 | 55.9 | 67.0 | **78.6** |
| full_curl_isect | 626 | 628 | 936 | 1246 | 1202 | 1580 | 1748 |
| curl_joint_vol_min | **0.884** | 0.828 | 0.858 | 0.657 | 0.605 | −0.331 | **−0.289** |
| curl_strain_p99_max | 0.32 | 0.43 | 0.27 | 0.40 | 0.30 | 0.26 | 0.23 |
| twist_vol_min | 0.936 | 0.936 | 0.957 | 0.917 | 0.825 | 0.997 | **0.579** |
| proportion_err | 0.0037 | 0.0037 | 0.0037 | 0.0037 | 0.0037 | 0.072 | 0.072 |
| pose_crease_gain (°) | 2.47 | 3.48 | 2.67 | 4.31 | 4.53 | 5.73 | 5.25 |
| bones that flex toward palm on +X | 19/19 | 19/19 | 19/19 | 19/19 | 19/19 | **0/19** | **0/19** |
| faces (evaluated) | 13 376 | 13 376 | 13 376 | 13 376 | 13 376 | 13 820 | 13 820 |
| build time | 0.04 s | 0.04 s | 0.04 s | 0.04 s | 0.04 s | 0.79 s | 0.71 s |

[measured, `results/ab_*.json`]

Per-knuckle at 90°, kit_fit: MCP 0.92–1.02, PIP 0.88–0.90, DIP 0.89–0.90, thumb MCP 0.99, IP 0.88. Control: MCP −0.29 to 0.65, PIP 0.66–0.67, DIP ~0.70. Wrist pronated 90°: kit 1.00 (twist bones carry it), control 0.58 — the textbook candy-wrapper. Wrist flexed 70°: kit 0 self-intersections, control 128.

What the attribution columns say:

- **Topology alone** (kit_heat: kit cage, bone heat) takes the rubric 43.5 → 77.8. Most of the win is the cage.
- **Skinning mode** matters more than any modifier: LBS → DQS on the kit cage, 69.2 → 80.9.
- **Corrective Smooth hurts** at large finger bends on this topology: 80.9 → 66.0, worst knuckle 0.83 → 0.60. It smooths the deformed crease and the volume goes with it.
- **DQS doesn't rescue the control**: 43.5 → 51.5. It fixes the twist (0.58 → 1.00) and nothing else, because the remeshed blob has no loops to deform along.
- **The optimizer adds the last 3 points** (80.9 → 83.8) and cuts crease gain 3.5° → 2.5°.

**Bone roll is a silent killer.** `edit_bones.new()` + head/tail leaves roll at Blender's default; on this hand **0 of 19** digit bones flex toward the palm on +X. An agent that keys `rotation_euler.x` on raw-bpy fingers gets fingers curling backward or sideways, and the F-curves look perfectly healthy. The kit sets `align_roll(volar)` on every digit bone so **+X is flexion and Z is spread** everywhere, thumb included.

**On "10x":** the rubric is bounded and the control isn't a straw man, so 2x on score / 3.5x on defects is the honest headline. Per failure class it's 10x or more: poles 39x, knuckle fold-through 79 → 0, candy-wrapper 0.58 → 1.00, roll 0/19 → 19/19. The render (`results/ab_hand_kit_top_vs_raw_bottom.png`, kit on top) is the night-and-day version: a hand vs a block with sausages.

**Where the kit hand is still weak (seen in renders, partly visible in metrics):** in the fist and point poses the dorsal skin over the MCPs shows small folds — that's what `pose_crease_gain` now catches. The thumb's exit from the palm has a visible kink at the port, and the resting thumb reads slightly peg-like. Those are the next iteration, and they're exactly the kind of thing the optimizer should be pointed at.

---

## 3. Hands

### 3.1 Topology the generator emits (`HandBuilder`)

All quads, 840 cage verts, 838 faces, valence histogram {3: 43, 4: 762, 5: 35}, genus 0, manifold, no boundary. [measured]

- **Palm + forearm is one tube** with a 26-vertex ring: 12 dorsal columns, 12 palmar, one mid-vertex per side wall. Same ring layout from elbow to knuckles, so the forearm→wrist→palm transition has no poles at all.
- **Knuckle cap = 11×2 quad grid.** Each finger leaves through a **2×2 port (8-vertex boundary)**. Between ports, **one quad column of web**. Delete the port's centre vertex, extrude.
- **Palm skin covers the base of the finger:** the palmar edge of each port sits 38 % down the proximal phalanx, the dorsal edge at the knuckle, the web mid-row at 24 %. That's the anatomy (the palmar digital crease is well distal of the MCP joint), and it's why the MCP measures 0.92–1.02 at 90° instead of collapsing.
- **Three loops across every finger joint** (joint centre and ±0.55 r), one mid-phalanx loop between. Joint loops carry the secondary shapes: dorsal knuckle bump, palmar flexion crease (pulled in 0.7–0.9 mm), finger pads (pushed out 0.5–0.8 mm). The first two finger loops blend between the slanted port and a perpendicular section so nothing folds.
- **Thumb** leaves from a 2×2 port straddling the radial palm corner at stations 0.42–0.80 of the metacarpal span (the first web space), on its own oblique axis: abducted 42° from the index line, 12° palmar, **pronated 70° about its own axis** so its nail faces outward. Thenar mass = thumb-metacarpal girth ×1.6 plus a 7 mm thenar bulge in the palm profile.
- **Fingertip cap**: 4 quads around one valence-4 vertex. No triangles anywhere.
- **Fingernails**: the distal dorsal quads (2×2) are inset (1.1 mm) and raised (0.35 mm) — a real nail-bed loop — and the border edges get crease 0.55 via the Blender 5.x `crease_edge` float attribute on the EDGE domain. [measured: attribute creation works headless]
- **Cage inflation** `cc_comp`: a coarse regular 8-gon ring subdivides to ~0.90 of its radius (cubic B-spline limit, (4+2cos(2π/8))/6), so the cage is inflated to compensate. The CMA-ES canon fit settled on **1.0985** — the formula predicts 1.108. [measured vs derived]

### 3.2 Proportions as numbers

Skeletal segment lengths, mm, adult (radiographic studies, e.g. Buryanov & Kotiuk 2010, Int J Morphol 28(3); exact table values **[recalled]** — the robust part is the ratio **P:M:D ≈ 1 : 0.6 : 0.4**):

| | MC | P | M | D | skin width at P base |
|---|---|---|---|---|---|
| thumb | 46 | 32 | — | 24 | 23 |
| index | 68 | 40 | 23 | 16 | 19.5 |
| middle | 65 | 45 | 28 | 18 | 19.5 |
| ring | 58 | 42 | 27 | 17 | 18.0 |
| little | 54 | 33 | 19 | 15 | 16.0 |

Layout: knuckle arc (MCP x) 93/95/88/78 mm from the wrist crease, metacarpal fan −5/0/+6/+12°, rest spread −4/0/+3/+6° beyond the MC line, transverse arch 4 mm, little-finger MCP 4 mm volar at rest (palm cupping). Tape targets: hand length 190 mm, knuckle breadth 86 mm (typical adult male, ANSUR-style, **[recalled]**). After the canon fit the skin measures **188.6 / 86.2 mm**, finger lengths within 0.2–0.8 mm, PIP breadths within 0.1 mm. [measured]

Thumb CMC: the flexion/extension and abduction/adduction axes are non-orthogonal and non-intersecting; the resting CMC posture is quoted as ~80° pronation, 40° abduction, 50° flexion relative to the 2nd–3rd CMC axis; ROM roughly 53° F/E, 42° Ab/Ad, 17° rotation. [read — secondary sources, treat as ballpark]. The generator's single oblique axis + 70° pronation is the 80 % version; a two-axis CMC (two bones, offset hinge axes) is the real one.

### 3.3 The hand rig

`build_rig(hb)`: `forearm` → three twist bones `forearm_tw1..3` (Copy Rotation Y from `hand`, influence 1/3, 2/3, 1, local space) → `hand` → `mc_<finger>` ×4 → `<finger>.01/.02/.03`; thumb `thumb.01` (CMC→MCP, unconnected to `hand`) → `.02` → `.03`. 25 bones. **Every digit bone: local Z = volar, so +X = flexion, local Z rotation = spread.** Metacarpals are real bones: ring/little MCs flex 3–14° in the pose library for palm cupping.

Forearm weights are smoothstep hats at 0.85/0.60/0.35/0.10 of forearm length, so adjacent influences differ by 30° of twist at a 90° pronation: worst-case LBS radius loss cos(15°) = 0.966. Measured mid-forearm local volume at 90° pronation: **0.936**. [measured]

**Analytic weights** (`analytic_weights`): the generator knows where every vertex sits along its digit, so weights are closed-form — a telescoping product of smoothsteps across each joint, partition of unity by construction, limited to 4. Each vertex's blend half-width and centre depend on which side of the finger it's on (`side` = projection on the dorsal axis): `blend_asym` widens the dorsal blend and narrows the volar one; `blend_shift` moves the dorsal transition distally. Palm vertices get Gaussian lateral weights across the four metacarpal lines, faded into `hand` near the wrist; thenar tissue gives up to 85 % to `thumb.01`.

**Sweep** (26 configs, 23.7 s, `results/sweep_weights.json`): with DQS, `blend_shift = 0.5` takes single-joint self-intersections **from ~108 to 0** in 4 of 6 blend settings (0.4 and 33.6 in the other two). LBS never gets below 27. Corrective Smooth, tested at the default blend with LBS and with DQS, is worse than either without it (worst knuckle 0.51 / 0.60). **CMA-ES** (`fit_hand.py skin`) then found `blend_scale 1.8 (upper bound), asym −0.31, shift 0` — wider blends, slightly wider volar — giving worst knuckle 0.887, mean 0.920, 0 self-intersections. It hit the bound, so widen the range next run. [measured]

**Rigify, if you want the control layer:** the default human metarig already has `limbs.super_finger` chains (bendy, stretchy, master control, Bend Rotation Axis Automatic/X/Y/Z/−X/−Y/−Z — Automatic recalculates roll, manual modes copy the metarig roll) and `limbs.super_palm` (palm system from palm bone spacing, primary rotation axis X or Z). [read, Blender manual]. The path: generate the hand with `hand.py`, then write its joint positions *and rolls* into the metarig's `palm.0x` / `f_index.0x` / `thumb.0x` bones before `rigify_generate()`. Not built this session.

### 3.4 Pose library (degrees, flexion = +X, spread = Z)

`hand.POSES`: `open`, `relaxed`, `fist`, `point`, `grip`, `high_five`. The relaxed pose is a cascade (MCP 18/24/30/36°, PIP 28/36/42/48°, index→little), and every flexed pose converges the fingers toward the scaphoid (index +3°, ring −4°, little −9° spread in the fist). Metacarpal flexion for cupping (ring 3–6°, little 6–14°). High-five carries 8° MCP hyperextension. `apply_pose(rig, pose, axes=None)` — pass `axes=flexion_axes(reference_rig)` to drive a rig with wrong roll correctly (that's how the control arm is posed).

Measured kit_fit: open 0 self-intersections, relaxed 18, high-five 34; worst local joint volume across all poses 0.48 (point pose, at a contact). [measured]

---

## 4. Smoothness — what reads as expensive, and how to fix it blind

**What makes it read smooth** (the principles, then the measurements that back them):

- Quad-dominant flow, loops along principal curvature: on a finger that's rings around and rails along, which is exactly what the generator emits. Evidence: the kit skin's curvature-jump p95 is 1.9 vs 6.4 for a voxel skin at the same density.
- Few, deliberate poles at low-curvature or hidden spots (fingertip cap centre, nail corners, web corners). Evidence: pole fraction 0.006 vs 0.23.
- Subdivision with creases where a hard line must survive (nail border, lip margin, brow) rather than more geometry.
- G1 is necessary, G2 is what "expensive" means. Zebra jumps measure G1; curvature jumps measure G2. Both are in the rubric.

**Fixer benchmark** (`smooth_bench.py`). Truth = kit skin (26.8k tris). Damaged = voxel remesh 2.5 mm + 0.25 mm normal noise. [measured, `results/smooth_bench.json`]

| method | s | faces | pole_frac | nd99 ° | curv-jump p95 | zebra p95 | mean err mm | truth→mesh p99 mm | volume |
|---|---|---|---|---|---|---|---|---|---|
| truth (kit skin) | — | 26 752 | — | 20.9 | 1.81 | 1.57 | 0 | 0 | 1.0000 |
| damaged | — | 18 724 | 0.234 | 55.7 | 5.30 | 3.58 | 0.209 | 0.81 | 0.9949 |
| Laplacian ×10 (λ .5) | 0.02 | 18 724 | 0.234 | 21.0 | 3.08 | 2.29 | 0.343 | 2.09 | 0.9725 |
| Laplacian ×40 | 0.06 | 18 724 | 0.234 | 20.2 | 2.97 | 2.59 | 1.106 | 4.83 | 0.9173 |
| **Taubin ×10 (.5/−.53)** | 0.04 | 18 724 | 0.234 | 24.3 | 3.63 | 2.40 | **0.098** | 0.83 | 0.9962 |
| **Taubin ×40** | 0.14 | 18 724 | 0.234 | 23.0 | 3.29 | 2.29 | **0.082** | 0.90 | **1.0000** |
| HC ×10 (α0 β.5) | 0.04 | 18 724 | 0.234 | 23.2 | 3.67 | 2.31 | 0.096 | 0.98 | 0.9946 |
| HC ×40 | 0.12 | 18 724 | 0.234 | 21.7 | 3.08 | 2.26 | 0.101 | 1.25 | 0.9937 |
| Blender LaplacianSmooth ×10 λ.8 | 0.05 | — | — | 55.7 | 5.30 | 3.58 | 0.209 | 0.81 | 0.9949 |
| Blender CorrectiveSmooth only-smooth ×20 | 0.01 | 18 724 | 0.234 | 19.8 | 1.98 | 2.07 | 0.328 | 2.11 | 0.9739 |
| QuadriFlow 4k on damaged | — | — | — | **CANCELLED** | | | | | |
| QuadriFlow 4k on Taubin ×10 | 2.35 | 4 209 | 0.011 | 47.0 | 2.44 | 3.06 | 0.099 | 1.20 | 0.9888 |
| … + subsurf 1 | | 16 836 | 0.003 | 22.9 | 1.15 | 1.80 | 0.241 | 1.64 | 0.9789 |
| QuadriFlow 12k on Taubin ×10 | 7.48 | 11 309 | 0.005 | 30.5 | 2.49 | 2.25 | 0.101 | 0.93 | 0.9933 |
| **… + subsurf 1** | | 45 236 | 0.001 | 14.2 | **1.05** | **1.27** | 0.145 | 1.11 | 0.9894 |
| Instant Meshes (4000) | 0.69 | 15 848 | 0.017 | 43.2 | 12.35 | 2.31 | 0.174 | 1.22 | 0.9902 |
| … + subsurf 1 | | 63 392 | 0.004 | 15.5 | 2.97 | 1.90 | 0.185 | 1.32 | 0.9876 |
| Instant Meshes (12000) | 1.68 | 46 948 | 0.015 | 32.0 | 10.41 | 2.19 | 0.155 | 0.90 | 0.9933 |
| … + subsurf 1 | | 187 792 | 0.004 | 10.9 | 2.71 | 1.60 | 0.147 | 0.95 | 0.9924 |

Rules that fall out of it:

1. **Taubin for denoising, always.** Volume-neutral, sub-0.1 mm drift. Laplacian is a shrink operator with a smoothing side effect: ×40 loses 8 % volume and 4.8 mm of finger.
2. **HC ≈ Taubin** here, slightly worse at ×40. Not worth a second code path.
3. **QuadriFlow returns `{'CANCELLED'}` with no exception on the raw noisy voxel mesh** ("needs to be manifold and have face normals that point in a consistent direction"), even with 0 non-manifold verts — noise-folded faces are enough. **Always Taubin first, check the return set.** Call it as `bpy.ops.object.quadriflow_remesh('EXEC_DEFAULT', ...)`; it runs synchronously headless (sphere: 0.26 s). [measured]
4. **QuadriFlow + one subsurf level is the best retopo-then-smooth path measured** (curv-jump 1.05 — smoother than the procedural truth, because it's coarser and loses detail: p99 1.1 mm).
5. **Instant Meshes** (`pynanoinstantmeshes` 0.0.3, cp313 wheel works; signature `remesh(V, F, vertex_count, ..., deterministic=True)`) is 4x faster than QuadriFlow at similar size, cleaner pole fraction before subdivision, but its raw output has high curvature jumps (10–12) until subdivided. Fallback, not first choice.
6. **Blender's LaplacianSmooth modifier was a no-op on this input** (bit-identical output) with λ 0.8, 10 iterations, volume-preserve, normalised. On Suzanne it moved vertices (max 35 mm). Unexplained — don't depend on it without checking the output moved.
7. **Corrective Smooth "only smooth"** gives the best curvature of the in-place smoothers (1.98) by shrinking (−2.6 %). Use it as a relax step inside a wrap loop, never as a finisher.
8. Smoothness numbers are density-dependent (187k-face Instant Meshes wins nd99 by being dense). Compare at matched face counts.

**When each wrecks detail:** Laplacian on anything thin (fingers lose ~1 mm radius per 10 iterations at λ 0.5 on a 2.5 mm grid); QuadriFlow on features under ~2 edge lengths (nails, lip margins vanish); Corrective Smooth on deforming creases (§2: it cost the knuckles 0.23 of local volume).

---

## 5. Spine and back

`spine.py`: torso proxy on the real S-curve (sacrum→C7 0.56 m; lumbar lordosis apex u≈0.2, −2.5 cm; thoracic kyphosis apex u≈0.62, +2.8 cm; waist narrowest u≈0.30, ribcage widest u≈0.72), 36×32 quad rings, subsurf 1, piecewise-linear hat weights along arc length. Each variant gets **the same total rotation**. Metrics: torso volume, minimum local slab volume along the spine, and **peak added curvature along the back midline** (signed planar curvature, posed − rest, m⁻¹; lower = bend spread smoothly instead of kinking at joints). [measured, `results/spine.json`, 12 variants in 4.2 s total]

| variant | flex70 vol | flex70 slab min | flex70 peak Δκ | lat40 peak Δκ | twist60 vol | flex45+twist45 vol |
|---|---|---|---|---|---|---|
| 2 FK | 0.910 | 0.766 | 6.3 | 4.8 | 0.977 | 0.933 |
| 3 FK | 0.896 | 0.838 | 5.2 | 5.2 | 0.986 | 0.927 |
| 5 FK | 0.880 | 0.861 | 4.5 | 2.6 | 0.994 | 0.920 |
| 8 FK | 0.872 | 0.856 | 4.5 | 2.5 | 0.997 | 0.916 |
| 17 FK (one per level) | 0.863 | 0.862 | 3.4 | 2.1 | 0.999 | 0.911 |
| 3 bones × 8 B-bone segs | 0.901 | 0.867 | 4.5 | 2.5 | 1.001 | 0.935 |
| **5 bones × 6 B-bone segs** | 0.878 | 0.853 | **3.4** | **2.0** | 1.000 | 0.921 |
| 5 FK, anatomical distribution | **0.909** | 0.885 | 5.7 | 2.7 | 0.991 | 0.937 |
| **5 × 6 B-bone, anatomical** | 0.905 | **0.890** | 4.8 | 2.1 | 1.000 | **0.938** |
| 5 × 6 B-bone, anatomical, DQS | 0.905 | 0.890 | 4.8 | 2.1 | 1.000 | 0.939 |

(1-bone rows score volume 1.000 because nothing deforms — the torso rotates rigidly. That's the metric's blind spot, not a result.)

What it says:

- **B-bones buy ~3x the bone count**: 5 bones with 6 segments bend as smoothly as 17 FK bones; 3×8 matches 5–8 FK.
- **Past 5 bones, flexion volume gets slightly worse**, not better — spreading the bend over more of the torso compresses more of the belly. Bone count is not the lever.
- **Anatomical distribution** (flexion concentrated in the five lumbar levels, twist in the thoracic, lateral bend even — per-level shares after White & Panjabi, **[recalled]**) is the best volume result and the only one that looks like a human bending. The higher peak Δκ is by design: the lumbar *should* bend more.
- **Every variant loses ~9–12 % torso volume in a 70° forward bend.** No skinning layout fixes that; real abdomens bulge forward. The fix is a corrective — a shape key or a scaled "belly" helper bone driven by lumbar flexion — and it's the next thing to build.
- **Twist with hat weights is a non-problem** (≥0.99 for ≥3 bones). DQS adds nothing on the spine.

**Recommended rig:** 5 deform bones (2 lumbar, 3 thoracic) with 6 B-bone segments, anatomical distribution baked into the control → deform mapping (a driver or Copy Rotation split per bone using the `FLEX/TWIST/LAT` profiles in `spine.py`), ribcage region weighted to the 3 thoracic bones only so it stays rigid while the lumbar bends, plus a lumbar-flexion belly corrective. Spline IK (3–5 controls) per `05-face-rigging.md` §9 is a control-layer choice on top; it wasn't measured here.

**Not built: scapula, shoulder correctives, back muscle masses.** The standard design, **[recalled]**: scapulohumeral rhythm ~2:1 (for every 3° of arm elevation, ~2° glenohumeral + ~1° scapular upward rotation), so a scapula bone driven at ~1/3 of the arm's abduction, sliding on the ribcage via a Shrinkwrap/Limit Distance constraint to a ribcage proxy; deltoid/trapezius correctives as pose-space shape keys triggered by arm elevation > 90°. That's a direct follow-up with `spine.py`'s harness.

---

## 6. Anatomical priors and licences

| Model | What | Licence | Usable commercially? |
|---|---|---|---|
| SMPL-X | body + MANO hands + FLAME face | Non-commercial scientific research / education / art; commercial via Meshcapade | **No** (without a paid licence) [read] |
| SUPR | body, foot, MANO/FLAME-topology hands/head | Non-commercial research; business via ps-licensing@tue.mpg.de | **No** [read] |
| STAR | SMPL drop-in, sparse correctives | Commercial via Max Planck Innovation; own terms not found in this search | **Assume no** [read, partial] |
| MANO | hand | Non-commercial scientific research | **No** [read] |
| FLAME | face | Not confirmed this session; historically MPI non-commercial | **Assume no** [unverified] |
| NIMBLE | hand, bones + muscles | Paper CC BY 4.0; model-file licence not confirmed | **Unknown** [read, partial] |
| Handy | hand shape + appearance, 1200 subjects | Not found | **Unknown** [read] |
| ATLAS (Meta, Aug 2025) | body, decoupled skeleton/shape, 600k scans | Not found | **Unknown** [read] |
| **MHR — Momentum Human Rig (Meta, Nov 2025)** | ATLAS follow-on rig | **Apache-2.0** (repo) | **Yes** [read] |
| **Anny (NAVER LABS Europe, Nov 2025)** | all-ages body built on MakeHuman, WHO-calibrated, no scans | **Apache-2.0**, no registration, github.com/naver/anny | **Yes** [read] |
| **MakeHuman / MPFB2 system assets** | base mesh, targets, proxies | **CC0** (assets); code AGPL (MakeHuman) / GPL (MPFB) | **Yes** (assets) [read] |

**Recommendation.** The MPI family is dead for a commercial forge. Two ways forward, both clean:

1. **Our own parametric generators** (this doc's `hand.py` is the template): topology as code, anatomy as a parameter table, measured against tape targets by the optimizer. Head and torso generators follow the same pattern: a fixed loop graph (face loops from `05-face-rigging.md` §1), rings on landmark-driven curves, canon-driven proportions, inflation fitted so the *subdivided* skin hits the measurements.
2. **Anny / MakeHuman CC0 as reference targets, not runtime dependencies.** Sample an Anny body at a phenotype, measure it (lengths, girths, landmark positions), and let CMA-ES fit our generator to those numbers. We inherit their anthropometric calibration without inheriting their topology or their code licence. (Anny's Apache licence also permits shipping its mesh; check its own asset provenance before doing that.)

Muscle volumes as implicit primitives anchored to insertions — the classic Scheepers/anatomy-based approach — slot into option 1 as additive displacement fields on the cage (ellipsoids with origin/insertion on bones, bulge driven by joint angle). Not built here.

---

## 7. Wrap / topology transfer

`wrap.py`. Target: a capsule + voxel-remesh hand built with hidden proportions (scale 1.05, girth 1.12, thumb abduction 55°, phalanges 0.93, spread ×2, breadth 1.06) — the kind of no-edge-flow surface an ML generator or metaballs give you. [measured, `results/wrap.json`, 12 s total]

| method | skin→blob mean / p99 mm | blob→skin mean / p99 mm | faces flipped vs target | cage quad-angle p95 |
|---|---|---|---|---|
| default kit hand, no wrap | 3.24 / 15.4 | 4.63 / 22.4 | 3.3 % | 41.1° |
| naive: Shrinkwrap NEAREST_SURFACEPOINT on the cage | 1.03 / 6.3 | 3.30 / **22.0** | 3.2 % | 41.1° |
| prior: CMA-ES param fit, chamfer loss (243 evals, 10.3 s) | 1.42 / 7.7 | 2.12 / 11.7 | 0.3 % | 40.5° |
| **prior + limit-surface residual fit ×6 + tangential relax (0.08 s)** | **0.30** / 4.3 | **0.83** / 7.6 | **0.0 %** | 29.8° |

The steps that matter:

1. **Fit the prior first.** Naive shrinkwrap pulls finger vertices onto whatever surface is nearest, so mismatched spread leaves whole fingers unwrapped (22 mm p99). Parameter-fitting first puts each finger tube on its finger.
2. **Fit the limit surface, not the cage.** Each iteration: evaluate the subdivided mesh, take the first N vertices (OpenSubdiv keeps cage vertices first — checked: max cage-to-limit offset 7.8 mm at iteration 0, consistent with cap/tip shrink, not a mis-order), move each cage vertex by 0.85× the residual of its limit position, then relax tangentially (0.35, normal component removed). Shrinkwrapping the cage directly leaves the skin inside the target by the subdivision shrink.
3. **The rig comes along for free** because vertex indices never change. Measured on the wrapped mesh with the transferred rig and weights: 12 finger joints at 90°, **0 self-intersections**, local volume mean 0.74, **worst 0.45**. The weights were computed on the pre-wrap cage and the capsule blob has bulbous knuckles, so the worst joint suffers. Fix: recompute analytic weights on the wrapped positions (they're closed-form, ~0.1 s), or add the knuckle battery to the wrap loss.

Fitted vs hidden parameters: scale 1.06/1.05, girth 1.13/1.12, thumb 54.2°/55°, but phalanges 1.01 vs 0.93 — the capsule blob's sphere-capped fingertips really are longer, and chamfer matches the surface, not the generator. Landmark constraints (fingertip, knuckle and wrist points) would pin that; non-rigid ICP is the general version and wasn't needed here.

---

## 8. Optimizer-driven fitting on 4 cores

`cmaes.py` is a 60-line (μ/μ_w, λ)-CMA-ES (Hansen's tutorial defaults) in [0,1]ⁿ with clipping. `pycma` 4.5 also installs fine but isn't needed. [measured]

| problem | params | loss | evals | wall | per eval | result |
|---|---|---|---|---|---|---|
| silhouette recovery | 6 | 1 − mean IoU, top + side, 128² point splat | 360 | 11.5 s | 28.6 ms | IoU **0.993**; loss < 0.05 at 126 evals (4.0 s), < 0.02 at 297 (9.0 s); every param within 0.2–2.3 % of range |
| skin canon | 6 | mean relative error on 10 tape measurements of the subdivided skin | 306 | 11.9 s | 35.8 ms | **4.1 % → 0.26 %** |
| skin weights | 3 | (1 − worst knuckle vol) + ½(1 − mean) + isect/200 + strain penalty | 70 | 63.6 s | 880 ms | worst knuckle 0.83 → 0.89, isect 88 → 0 |
| wrap prior | 6 | chamfer to blob | 243 | 10.3 s | ~42 ms | blob→skin p99 22 → 11.7 mm |

**Practical on 4 cores? Yes, for geometry losses.** ~30 ms per eval means a 6–10 parameter problem converges in 10–20 s single-threaded; three ran concurrently here with no slowdown worth noting. Deformation losses are ~1 s per eval (bpy evaluation of 14 poses dominates), so budget ~1 minute per 70 evals — fine for a once-per-asset tune, too slow for an inner loop.

**Rules:**
- **Never put a render in the loss.** Workbench at 128² is 568 ms; a numpy point-splat of the subdivided vertices plus 3×3 close is 28.6 ms and gives the same silhouette.
- **Measure the evaluated skin**, never the parameters. Subdivision shrink, inflation and blended ports make "what the parameter says" and "what the tape measures" differ by 4 %.
- Clip to anatomical bounds and watch for bound hits (the skin fit hit its `blend_scale` ceiling — widen and rerun).
- Parallelism: evaluate a CMA-ES population across processes (λ = 9–10 for n = 6) for a near-linear 3–4x. Not built.

**What a GPU would add:** differentiable rasterisation (nvdiffrast, PyTorch3D, Mitsuba 3) gives exact gradients of silhouette or shading loss with respect to every vertex, so you could optimise the cage vertices directly (hundreds of DOF) instead of 6–10 parameters, and photometric losses against reference images become tractable. Here, with no GPU, the right design is low-dimensional parametric generators + derivative-free search, and that's what the numbers support.

---

## 9. What else makes it look hand-made (design notes, not built)

These are what top character and technical artists do that an agent can encode as numbers. All **[recalled]** craft knowledge; each comes with the blind check that would enforce it.

1. **Primary → secondary → tertiary, in that order, with size ratios.** Primary forms read at thumbnail size; secondary (knuckle bumps, pads, thenar, tendons on the back of the hand) are ~1/5–1/10 of primary; tertiary (skin creases, nail detail, pores) live in normal maps. The generator already does primary + secondary as cage offsets. Check: band-pass the displacement from a smoothed version of the skin and verify energy per band.
2. **Silhouette first.** Every pose should read in pure black. The point-splat silhouette from §8 doubles as a readability check: silhouette area change and convex-hull defects per pose.
3. **Asymmetry on purpose.** Real hands and faces are 1–3 % asymmetric; perfect symmetry reads as CG. Add a low-frequency random field (seeded, 1–2 % of size) after mirroring. Check: `inspect.symmetry_error` between 0.005 and 0.02 instead of ~0.
4. **Straights against curves.** The back of the hand, the shin and the forearm's ulnar border have a straight line opposed by a curve; mannequins are curve-against-curve. Encode as per-ring offset profiles (the generator's `prof` hook).
5. **Gesture.** A rest pose with a slight cascade (the `relaxed` pose) reads alive; dead-straight fingers read as a rig T-pose. Ship the asset posed in `relaxed` for hero renders.
6. **Overlap, not butt joints.** Forms tuck behind each other: the web over the proximal phalanx (done), the thenar over the palm (done), muscle bellies over tendons. Butt-jointed primitives are what makes a capsule character.
7. **Weight and gravity.** Soft tissue sags toward gravity in the rest pose (palm pads, belly, cheeks): a 1–2 mm volar bias on soft regions.
8. **Corrective shapes where bones can't.** Belly in flexion (§5), deltoid in elevation, dorsal MCP skin in a fist (§2). Pose-space shape keys driven by joint angle, generated from the measured defect (move flagged vertices toward the rest-relative shape).

---

## 10. Integration into forgekit

Proposed, not merged. The prototypes are written to drop in.

- `forgekit/formq.py` ← `proto/form/formq.py` as-is (metrics + rubric).
- `forgekit/organic/hand.py` ← `HandBuilder`, `build_rig`, `analytic_weights`, `POSES`, `measure_hand`, `canon_targets`.
- `forgekit/fit.py` ← `cmaes.py` + the silhouette splat and the three loss patterns from `fit_hand.py`.
- `forgekit/wrap.py` ← prior-fit + limit-surface residual loop from `wrap.py`.
- `forgekit/smooth.py` ← `taubin()`, `hc()`, and a `quadriflow()` wrapper that Taubin-cleans first, calls with `'EXEC_DEFAULT'` and raises on `{'CANCELLED'}`.
- `rig.py` additions: `align_roll(volar)` for any digit chain; `pose_test()` should report **local joint volume**, not just whole-mesh volume — whole-mesh volume ratio stayed ≥ 0.994 for every arm here, including the one whose knuckle folded inside out. The current kit metric can't see the failure.
- README trap-table rows to add: default bone roll curls fingers the wrong way (0/19); QuadriFlow cancels silently on noisy voxel input; Corrective Smooth worsens large finger bends; LaplacianSmooth modifier can be a silent no-op; `crease_edge` is the 5.x crease attribute.

---

## 11. Not finished, not verified

- **Head, full humanoid generators: not built.** Only the hand + forearm. The pattern is proven; the head loop graph is specified in `05-face-rigging.md` §1.
- **Scapula, shoulder correctives, back muscle masses, belly corrective: not built** (§5 design only).
- Thumb: single oblique CMC axis, visible kink at the port exit, slightly peg-like at rest. Dorsal MCP folds in fist/point. Both visible in renders; `pose_crease_gain` partly measures the second.
- Rubric thresholds are mine; two changes were made after seeing results (§1). `pose_battery_isect` saturates and carries no signal.
- Rigify `super_finger` / `super_palm` integration not run.
- Anatomy tables marked [recalled]: phalanx and metacarpal lengths, ANSUR-style hand length/breadth, spinal per-level ROM, scapulohumeral rhythm. The Buryanov & Kotiuk full-text table wasn't retrieved.
- Licences for STAR, FLAME, NIMBLE model files, Handy, ATLAS not confirmed.
- Blender LaplacianSmooth no-op not explained.
- Wrap: transferred weights not recomputed; no landmark constraints; forearm not wrapped (masked out); only one target blob tested.
- No parallel CMA-ES evaluation; no GPU / differentiable-rendering comparison (none available).

---

## Sources

- Anny body model: [arXiv 2511.03589](https://arxiv.org/abs/2511.03589) · [NAVER LABS Europe blog](https://europe.naverlabs.com/blog/anny-a-free-to-use-3d-human-parametric-model-for-all-ages/)
- MHR / ATLAS: [MHR arXiv 2511.15586](https://arxiv.org/pdf/2511.15586) · [ATLAS arXiv 2508.15767](https://www.arxiv.org/abs/2508.15767)
- SMPL-X licence: [smpl-x.is.tue.mpg.de/modellicense.html](https://smpl-x.is.tue.mpg.de/modellicense.html) · SUPR: [github.com/ahmedosman/SUPR](https://github.com/ahmedosman/SUPR) · STAR / SMPL: [smpl.is.tue.mpg.de](https://smpl.is.tue.mpg.de/) · [Meshcapade wiki](https://meshcapade.wiki/SMPL)
- MANO: [mano.is.tue.mpg.de](https://mano.is.tue.mpg.de/)
- NIMBLE: [arXiv 2202.04533](https://arxiv.org/abs/2202.04533) · [github.com/reyuwei/NIMBLE_model](https://github.com/reyuwei/NIMBLE_model) · Handy: [CVPR 2023 paper](https://openaccess.thecvf.com/content/CVPR2023/papers/Potamias_Handy_Towards_a_High_Fidelity_3D_Hand_Shape_and_Appearance_CVPR_2023_paper.pdf)
- MakeHuman licence: [makehumancommunity.org licence](https://static.makehumancommunity.org/about/license.html) · [2020 licence change](https://static.makehumancommunity.org/oldsite/faq/what_changed_regarding_the_license_in_2020.html) · [MPFB on Blender extensions](https://extensions.blender.org/add-ons/mpfb/)
- Hand segment proportions: [Buryanov & Kotiuk 2010, SciELO](http://www.scielo.cl/scielo.php?script=sci_arttext&pid=S0717-95022010000300015)
- Thumb CMC: [OUHSC thumb CMC](https://ouhsc.edu/bserdac/dthompso/web/namics/firstcmc.htm) · [PMC4574857](https://pmc.ncbi.nlm.nih.gov/articles/PMC4574857) · [Kenhub trapeziometacarpal](https://kenhub.com/en/library/anatomy/trapeziometacarpal-joint)
- Rigify limb rig types: [Blender manual, Rigify limbs (5.3)](https://docs.blender.org/manual/de/5.3/addons/rigify/rig_types/limbs.html)
- CMA-ES: Hansen, "The CMA Evolution Strategy: A Tutorial", arXiv:1604.00772 [recalled citation]
- HC-Laplacian: Vollmer, Mencl & Müller 1999; Taubin λ/μ: Taubin 1995 [recalled citations]
