# 05 — Face rigging, face generation, spine. forge-kit extension

## Verdict (10 lines)

1. The kit is not face-blind. `RIG.add_human_metarig()` already carries a Rigify face: 46 face-region bones with `rigify_type = faces.super_face`, and a headless `generate()` produces eye, brow, lip, jaw, cheek, nose and tongue control bones inside the same 706-bone rig. Measured, see §A.
2. What is missing is everything that makes that face work on a *specific mesh*: topology, landmark fitting of the face bones, and blendshapes. `fit_metarig()` only fits the bounding box, so the face bones land at the wrong place on any head that is not already human-proportioned.
3. Face topology: build a loop-correct quad head from a hand-authored loop graph (concentric eye and mouth loops, a nose-to-jaw loop, poles at nose wing and brow), then shrinkwrap it onto a generated head. Headless shrinkwrap apply is measured to work (§A).
4. Procedural blendshapes are real, not fantasy, with a precise caveat. Bone-driven pose baking into a shape key works headless (measured). Shrinkwrap-based wrap onto a donor works (measured the mechanism). What is NOT real is "52 anatomically correct shapes for free": the procedural output is only as good as the per-shape bone drives or displacement fields you author, and those are numeric design work, not sculpting. Expect it to be correct in silhouette and direction, wrong in fine muscle detail.
5. Recommended blendshape path: a vector-field authoring method. Each ARKit shape is a set of anchors (vertex region, displacement vector, falloff radius), baked into a shape key with a Gaussian falloff. It is numeric, deterministic, and verifiable blind. Bone-driven baking covers the jaw, eyelids and brow; vector fields cover lips and cheeks.
6. Smoothing: Catmull-Clark via a Subdivision Surface modifier, with crease where brow and lip edges must survive. Callable headless. Weighted normals and bevel-harden for the rest. Details in §3.
7. Eyes: separate sclera, iris and cornea geometry, a shared eye target with Damped Track, convergence from IPD and focus distance, and eyelid follow via a fractional Copy Rotation. Blink timing is a frame count, not a feeling. Numbers in §5.
8. Jaw pivot sits behind the chin at roughly the ear-canal line, not at the chin. Lips are shape-key driven with a sealed-mouth shape plus a contact fix. The tongue is a 3–4 bone chain.
9. Spine: Spline IK with 3 to 5 control points plus B-bone segments for the mesh, one FK chain for the animator, twist distributed across segments, and chest counter-rotation driven from the pelvis. Measured: nothing; read-only from the Rigify docs and code.
10. Validation without eyes is achievable blind: symmetry error via KDTree mirror-match, shape range via bounding-box and normal checks, interpenetration via BVH overlap, and a 52-shape contact sheet rendered with Workbench. The contact-sheet render cost is estimated from the README's 0.62 s figure, not measured for 52 frames.

---

## A. What was run and what was only read

### Ran in this container (bpy 5.3.0, `/home/user/bpyenv/bin/python`, `env.prepare()` before import)

| Test | Result |
|---|---|
| `enable_rigify()` + `add_human_metarig()` | 159 metarig bones in 0.2 s |
| List `rigify_type` on metarig bones | Only `face` has a face type: `faces.super_face` (other rig types exist in the registry: `face.basic_tongue`, `face.skin_eye`, `face.skin_jaw`) |
| `generate(meta)` on default human metarig | 7.38 s total, 706 bones, the 6.96 s "Generate bones" stage dominates. Face-region control bones present: `eye.L/R`, `eyes`, `master_eye.L/R`, `jaw`, `jaw.L/.R`, `jaw_master`, `lip.T/B` (+ `.L/.R` sides), `lips.L/.R`, `nose` + `nose.001–.005` + `nose.L/R`, `nose_master`, `brow.T/B` (×4 each side), `cheek.T/B` (side), `tongue`, `tongue.001–.003`, `tongue_master`. DEF face bones: 46. |
| Pose bake to shape key | Jaw bone rotated −25° on a 64×48 UV sphere with a two-group weight map. Evaluated mesh read via `evaluated_get` + `new_from_object`, written to a new shape key. 1409 of 3010 vertices moved more than 1 mm, max displacement 49.7 mm. Run 0.064 s. Toy head, but the mechanism works headless. |
| Shrinkwrap apply headless | 16×12 base sphere, shrinkwrap NEAREST_SURFACEPOINT onto r=1.1 target, `modifier_apply`. Post radius 1.0997–1.0999. 0.051 s. |
| ARKit 52 list | Count 52, unique 52. Written out in §7 from this check. |

Measured correction to the kit's documentation: `README.md` lists the 706-bone rig as "a face rig included, for free". That is true for the default human metarig. The kit simply never checked the face bones. The doc's "nothing for faces" framing is wrong on the rig side and right on the topology and blendshape side.

### Read only (not executed)

- Rigify `super_face` parameter set and its exact bone-by-bone placement rules (read the registry name, did not inspect the generator source line by line).
- Catmull-Clark creases in Blender 5.x (API naming verified only by memory; see §3, flagged).
- Deformation Transfer add-on in 5.3 (not tested; the bpy path is flagged as unverified).
- Eyelid and blink numbers in §5 (general animation knowledge, not measured on this rig).
- Landmark index maps in §6 (standard conventions, not checked against a file in this container).
- FACS to ARKit mapping in §7 (standard correspondence, not checked against a published table in this session).
- VRM 1.0 preset names in §7 (recalled; the search did not return the spec).
- The Apple ARKit documentation: web search returned Apple's `jawOpen` and `mouthSmileLeft` pages, and third-party lists, but no single full official list. The 52 identifiers in §7 were checked for count and uniqueness here, not against Apple's own page.

---

## 1. Face topology that deforms

### Why a remeshed blob cannot deform

A voxel remesh or marching-cubes head is a triangle soup with no edge flow. A joint or a shape key pulls vertices in a direction; with no loops around the eye or mouth, the vertices have no ring to slide along, so the weights either collapse the volume or stretch triangles past recovery. The kit's own contract (§4 of the README) already states this for limbs: at least three loops across every bend. For a face the requirement is stricter, because the eye and mouth are sphincters that must compress radially. A sphincter needs concentric loops that shrink around a point. A blob has none.

Minimum topology that fixes this:
- Concentric loops around each eye (at least three), and around the mouth (at least three).
- One loop running nose wing, cheek, jaw, chin (the "nose-to-jaw" loop), so the cheek can slide.
- A pole or a 3-valence point at the nose wing (where the nasolabial fold begins) and at the inner brow.
- Edge flow that runs with the muscle: forehead horizontal, brow vertical, cheek radial from the mouth.

### Loop map (data)

Each row is a named loop. Counts are the target vertex count per loop for a head of about 1.8 m scale. These are design targets, not measured from a shipped asset.

| Loop id | Region | Shape | Loops (concentric) | Purpose |
|---|---|---|---|---|
| `eye_L_outer`, `eye_R_outer` | Each eye | Ring around eye aperture | 3 | Blink closure, squint |
| `eye_L_mid`, `eye_R_mid` | Each eye | Ring | (same as outer) | Eyelid fold |
| `eye_L_inner`, `eye_R_inner` | Each eye | Tight ring, eyeball edge | 1 | Lid margin, eyeball socket |
| `mouth_outer` | Mouth | Ring around lip aperture | 3 | Funnel, pucker, smile |
| `mouth_inner` | Mouth | Ring at lip seal | 2 | Sealing, lip contact |
| `nose_wing_L`, `nose_wing_R` | Nostril | Small ring | 1 | Sneer, flare |
| `nasolabial_L`, `nasolabial_R` | Nose to mouth corner | Radial line | 1 | Fold, cheek puff |
| `nose_to_jaw_L`, `nose_to_jaw_R` | Nose, cheek, jaw | Continuous loop | 1 | Cheek slide, jaw flow |
| `brow_L`, `brow_R` | Brow | Horizontal line plus vertical rungs | 3 rungs | Brow raise, furrow |
| `temple_L`, `temple_R` | Temple | Ring | 1 | Smooth temple transition |
| `chin` | Chin | Pole | pole | Chin raise, pucker |

Poles: one at each nose wing, one at the inner brow top (inner corner of each brow), one at the chin, one at the top of the head.

The minimum viable set for a blendshape rig is the eye loops, the mouth loops and the nose-to-jaw loops. Without those three you cannot pass the blind checks in §8. The rest is refinement.

### Shrinkwrap a topology-correct base onto a generated head

This is the headless path. The base is a loop-correct quad head built from the loop map above. The target is a sculpted or parametric head (a metaball, a generated SDF surface, or a shape you build from `lathe` and `loft_bracket`). The procedure:
1. Build the base in bmesh from the loop map (data, see §1 and §10).
2. Shrinkwrap the base onto the target, `NEAREST_SURFACEPOINT` (measured to apply headless).
3. Relax the base to remove folds (§3).
4. Lock the loops. Vertices on loop ids have their shrinkwrap offset clamped so eyelid margins and lip seals do not drift.
5. Bind the base to the rig. Only then do the face shape keys go on.

Limit: shrinkwrap projects, it does not fit. A head whose eye sockets are deeper than the base can reach will project the eye loop onto the cheek. Fit landmarks first (§2).

---

## 2. Generating a face procedurally

### Proportion canons

Use the classic canons, as numbers.
- Head height H. The face spans from hairline to chin, about 0.6 H on the front.
- Eyes sit at the vertical midpoint of the head. Eye width = 1/5 of face width. The interocular distance equals one eye width.
- Nose base sits at 1/3 of the way from the eye line to the chin. Mouth corners sit at the line through the pupils, roughly.
- Ear top aligns with the brow line, ear bottom with the nose base.

These are the numbers to hard-code. A landmark set, then a canon-scaled head.

### Landmark conventions

Two standards matter.

**dlib 68 points** (0-indexed): jaw contour 0–16, right brow 17–21, left brow 22–26, nose bridge 27–30, nostrils 31–35, right eye 36–41, left eye 42–47, outer lip 48–59, inner lip 60–67. Table in §6.

**MediaPipe Face Mesh, 468 points** (plus 10 iris points in the refined model, indices 468–477). Table in §6 with the points the face generator needs. The 468-point model is the one that matters for shaping because it has dense eye, lip and nose coverage.

Note the left/right convention: dlib and MediaPipe index the *subject's* right side first when viewed from the front. The face generator must mirror this explicitly, or every `.L` lands on `.R`.

### Parametric construction

Build the head from the canons, not a sculpted blob:
1. Start from the loop map.
2. Place each loop vertex from landmark coordinates. Anchor the loop points on the landmarks. Interpolate between landmarks with a 2D thin-plate spline (smooth, no overshoot).
3. Mirror the left half across X to guarantee symmetry, then average the two halves' landmark positions.
4. Shrinkwrap to a generated head shape for surface detail.

This gives a head whose landmarks sit where the canon says they sit, with loops that match the topology. That is the "blob to loop-correct topology" path, done with numbers.

---

## 3. Smoothing without losing the brow and lip edges

### Options

| Method | What it does | Use for | Headless? |
|---|---|---|---|
| Catmull-Clark (Subdivision Surface modifier) | Quad-to-quad refinement, limit surface | The whole face, after topology is correct | Yes (`subdivide()` in mesh.py) |
| Creases | Edge crease weights keep hard edges sharp under subdivision | Brow edge, lip margin, eyelid margin | Yes, with the caveat below |
| Laplacian | Averages each vertex toward its neighbours | Quick relaxation of shrinkwrap folds | Yes, via bmesh `smooth_vert` or a Smooth modifier |
| HC (Humphrey / Laplacian-HC) | Laplacian with a volume-preservation pass | Smoothing where volume must not collapse | Yes, custom implementation needed (~30 lines) |
| Shrinkwrap-and-relax | Alternate shrink and relax passes | Fixing a base after projection | Yes |
| Normal smoothing (Weighted Normal + bevel harden) | Shades a surface as smooth without moving vertices | Final shading | Yes (`shade_smooth`) |
| Geometric smoothing | Moves vertices | Fixing bad topology | Yes |

### The rule

Normal smoothing is the first tool, not the last. A surface that is already quad-correct should be shaded smooth with weighted normals and never geometrically smoothed, because geometric smoothing shrinks the volume and blunts the brow and the lip margin. Geometric smoothing (Laplacian or HC) is for correcting a base that came out of shrinkwrap with folds.

For a face that is smooth without losing the edges:
1. Quad topology with correct loops.
2. Crease the brow edges and the lip margin edges (edge crease 0.8–1.0 for lip and brow, 0 elsewhere).
3. Subdivision Surface, 2 levels viewport, 3 render. Creases hold the edges under subdivision.
4. Weighted normals with keep-sharp on.

### Callable API and parameters

- `subdivide(ob, viewport=2, render=3)` already exists in `mesh.py`.
- Edge crease in Blender 5.x: the attribute name has changed across releases. Verify the attribute name on 5.3 before writing the crease function. Flagged as unverified in this doc.
- Laplacian smoothing via `bmesh.ops.smooth_vert(bm, verts, factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)`. Factor 0.3–0.5 per iteration, 2–4 iterations, on the shrinkwrap base only. Not on the final mesh.
- Use `keep_sharp=True` on the weighted normal modifier so the crease edges still read as edges.

---

## 4. Face bone rig

### Rigify's face rig

From the measured output in §A:
- The default human metarig carries the face in one bone group: `face` (rigify_type `faces.super_face`), with children for brows, eyes, lips, nose, cheeks, jaw and tongue.
- Generated control bones: the face-region control list from §A runs past 60 names (brows, eyes, jaw, lips, nose, cheeks, tongue, plus masters), with 46 DEF bones driving the mesh. Per-family counts were not tallied this session.
- Face bone rigify types: only `faces.super_face` is set on the metarig root. Sub-bones inherit from it.

The face metarig generates headless. It is not free of the placement problem: `super_face` expects the face metarig bones to sit on the head where the metarig's default face is. A head that is not the default proportion needs the face bones moved.

### Fitting the face metarig from landmarks

This is the missing piece. `fit_metarig()` moves the whole metarig by bounding box, which is wrong for the face. The proposed function is `fit_face_metarig` (§10): it moves the face metarig bones so the eye, nose, lip and jaw anchors sit on the landmark coordinates from §6, using the metarig's own bone head and tail as the anchor pair. Rigify's face generator then rebuilds the controls from those positions.

Not tested: whether `super_face` regenerates correctly after a landmark-based move. Test it before you rely on it. The eye and jaw bones are the first thing to check.

### Alternatives if Rigify's face is not enough

- `face.skin_eye` and `face.skin_jaw` are standalone Rigify rig types. They generate headless too (registry names verified; not generated in this session).
- `face.basic_tongue` for the tongue.
- A hand-built bone rig: 2 eye bones, 1 jaw bone, 4 lip-corner bones, 2 brow bones, 1 nose bone, 3–4 tongue bones. Roughly 15 bones total. Cheaper, faster, and fully under your control.

### Bone-based vs blendshape-based

| Method | Wins when | Loses when |
|---|---|---|
| Bones | Large motion: jaw, eyes, tongue, head, big cheek slides. Real-time pose changes. Motion capture retargeting with a bone map. | Fine muscle detail. Lip shape transitions. Anything you cannot model as rotation about a point. |
| Blendshapes | Fine shape: lip shape, brow ridge, cheek puff, sneer. Anything that needs exact shape. ARKit tracking, which expects blendshapes. | Large movement: a jaw of 30 degrees on blendshapes needs a big delta and looks wrong at the limits. Heavy memory. |

The rig that works uses both. Bones for the jaw, eyes and tongue. Blendshapes for the lips, cheeks, brows and sneer. Expect the ARKit list in §7 to need both.

---

## 5. Eyes, properly

### Geometry

- Sclera: a sphere, radius about 12 mm (diameter 24 mm). Slightly flattened in the Y axis (depth 22–23 mm).
- Cornea: a dome over the front of the sclera. A cap of radius about 8 mm, protruding about 1 mm.
- Iris: a disc of radius about 5.5 mm, set into the sclera with a shallow concave recess (about 0.3–0.5 mm) so it casts a shadow and reads as sunk.
- Pupil: a dark disc at the centre of the iris, radius 2–3 mm depending on light.

The "refraction trick" is fake and cheap: a shader parallax on the iris and pupil, or a cornea normal bump that bends the iris highlight. You do not need real refraction. A cornea dome with a clear specular highlight gets most of the look.

### Eye bones and look-at

- One bone per eye, at the eyeball centre, pointing along the gaze direction.
- One shared target empty, placed at the focus point.
- Each eye bone gets a Damped Track constraint to the target (track axis Y, up axis Z), with influence 1.0.
- Shared target means both eyes point at the same point, so they converge. The default with independent targets gives divergent eyes.

Convergence for near focus: the yaw of each eye toward the centre line is `atan((IPD / 2) / d)`, where IPD is the interpupillary distance (63 mm, adult average) and d is the focus distance.
- d = 0.5 m: 3.6° per eye.
- d = 0.3 m: 6.0° per eye.
- d = 0.2 m: 9.0° per eye.

Compute these from the formula; do not hard-code.

### Eyelid follow

Eyelids rotate with the gaze. The upper lid follows the eye's vertical rotation at about 0.5–0.8 of the eye's pitch. The lower lid follows at about 0.2–0.3.
- Implementation: a Copy Rotation constraint on each lid bone, X-axis only, influence 0.5 for the upper lid and 0.25 for the lower.
- Without it, the eye looks at the top of the room with a closed-looking lid.

### Saccades and blink timing

These are general biology numbers, not measured on this rig.

- Saccade duration: 20–40 ms for a small shift, up to 80–120 ms for a large one. Peak velocity scales with amplitude.
- Fixation: 200–400 ms between saccades in conversation.
- Blink duration: total 100–150 ms. Closing 50–70 ms, held 10–30 ms, opening 80–100 ms.
- Blink frequency: 15–20 per minute at rest, lower when focused, higher when speaking.

At 24 fps: a blink is 3 frames closing, 1 held, 2–3 opening. At 30 fps: 4 closing, 1–2 held, 3 opening. A saccade of 30 ms is about 1 frame at 24 fps, so it reads as a cut. Small saccades should be keyed as cuts. Large ones get 2–3 frames of ease.

### Eye darts

Idle eye darts: 1–3 small saccades every 1–2 s, amplitude under 5°, duration 20–40 ms. Key on the target empty, not the bones directly.

---

## 6. Landmark index maps (data)

### dlib 68 (0-indexed)

| Range | Region | Count |
|---|---|---|
| 0–16 | Jaw contour, ear to ear | 17 |
| 17–21 | Right brow (subject right, viewer left) | 5 |
| 22–26 | Left brow | 5 |
| 27–30 | Nose bridge, top to tip | 4 |
| 31–35 | Nostrils and base | 5 |
| 36–41 | Right eye, six points, outer corner 36 | 6 |
| 42–47 | Left eye, six points, outer corner 42 | 6 |
| 48–59 | Outer lip, 48 right corner, 54 left corner | 12 |
| 60–67 | Inner lip | 8 |

Key anchors for shaping:
- Eye centre: mean of 36–41 and 42–47 respectively.
- Mouth corners: 48 (right), 54 (left).
- Chin: 8.
- Nose tip: 30.

### MediaPipe Face Mesh, selected from 468

The full set is 468 plus 10 iris points (468–477). Anchors the face generator uses:

| Index | Region |
|---|---|
| 1 | Nose tip |
| 4 | Nose base centre |
| 10 | Forehead top centre |
| 13 | Upper inner lip centre |
| 14 | Lower inner lip centre |
| 33 | Right eye outer corner |
| 133 | Right eye inner corner |
| 61 | Right mouth corner |
| 291 | Left mouth corner |
| 152 | Chin |
| 234 | Left face edge, cheek |
| 454 | Right face edge, cheek |
| 263 | Left eye outer corner |
| 362 | Left eye inner corner |
| 468–472 | Right iris (centre 468) |
| 473–477 | Left iris (centre 473) |

The index assignments above are the common convention for the 468 Face Mesh. They were not checked against a file in this container. Verify them against the MediaPipe model card or a loaded landmark file before shipping.

---

## 7. Blendshapes

### ARKit 52, exact identifiers

Verified in this container: count 52, unique 52. Casing is camelCase, as Apple and most tools use.

| Group | Identifiers |
|---|---|
| Eyes, left (7) | `eyeBlinkLeft`, `eyeLookDownLeft`, `eyeLookInLeft`, `eyeLookOutLeft`, `eyeLookUpLeft`, `eyeSquintLeft`, `eyeWideLeft` |
| Eyes, right (7) | `eyeBlinkRight`, `eyeLookDownRight`, `eyeLookInRight`, `eyeLookOutRight`, `eyeLookUpRight`, `eyeSquintRight`, `eyeWideRight` |
| Jaw (4) | `jawForward`, `jawLeft`, `jawRight`, `jawOpen` |
| Mouth (23) | `mouthClose`, `mouthFunnel`, `mouthPucker`, `mouthLeft`, `mouthRight`, `mouthSmileLeft`, `mouthSmileRight`, `mouthFrownLeft`, `mouthFrownRight`, `mouthDimpleLeft`, `mouthDimpleRight`, `mouthStretchLeft`, `mouthStretchRight`, `mouthRollLower`, `mouthRollUpper`, `mouthShrugLower`, `mouthShrugUpper`, `mouthPressLeft`, `mouthPressRight`, `mouthLowerDownLeft`, `mouthLowerDownRight`, `mouthUpperUpLeft`, `mouthUpperUpRight` |
| Brow (5) | `browDownLeft`, `browDownRight`, `browInnerUp`, `browOuterUpLeft`, `browOuterUpRight` |
| Cheek (3) | `cheekPuff`, `cheekSquintLeft`, `cheekSquintRight` |
| Nose (2) | `noseSneerLeft`, `noseSneerRight` |
| Tongue (1) | `tongueOut` |

Total: 7+7+4+23+5+3+2+1 = 52.

Note on casing: Unity uses PascalCase (`JawOpen`). Check the target tool's casing before export.

### FACS to ARKit (standard correspondence, not verified against a table this session)

| AU | Name | ARKit |
|---|---|---|
| 1 | Inner brow raiser | `browInnerUp` |
| 2 | Outer brow raiser | `browOuterUpLeft/Right` |
| 4 | Brow lowerer | `browDownLeft/Right` |
| 5 | Upper lid raiser | `eyeWideLeft/Right` |
| 6 | Cheek raiser | `cheekSquintLeft/Right` |
| 7 | Lid tightener | `eyeSquintLeft/Right` |
| 9 | Nose wrinkler | `noseSneerLeft/Right` |
| 10 | Upper lip raiser | `mouthUpperUpLeft/Right` |
| 12 | Lip corner puller | `mouthSmileLeft/Right` |
| 14 | Dimpler | `mouthDimpleLeft/Right` |
| 15 | Lip corner depressor | `mouthFrownLeft/Right` |
| 17 | Chin raiser | `mouthShrugLower` |
| 18 | Lip pucker | `mouthPucker` |
| 20 | Lip stretcher | `mouthStretchLeft/Right` |
| 22 | Lip funneler | `mouthFunnel` |
| 23 | Lip tightener | `mouthPressLeft/Right` |
| 25 | Lips part | `mouthLowerDownLeft/Right` |
| 26 | Jaw drop | `jawOpen` |
| 43 / 45 | Eyes closed / blink | `eyeBlinkLeft/Right` |

Some ARKit shapes have no single AU (`cheekPuff`, `mouthRoll*`, `tongueOut`, `mouthClose`). Those are authored directly.

### VRM 1.0 expression presets (recalled, not verified against the spec)

`happy`, `angry`, `sad`, `relaxed`, `surprised`, `aa`, `ih`, `ou`, `ee`, `oh`, `blink`, `blinkLeft`, `blinkRight`, `lookUp`, `lookDown`, `lookLeft`, `lookRight`, `neutral`. Eighteen presets. Map ARKit to these by combining shapes: `aa` is mostly `jawOpen` plus `mouthFunnel` at low weight; `ih` is `mouthSmile` with `jawOpen` low; `ou` is `mouthFunnel` plus `mouthPucker`; `ee` is `mouthSmile` plus `mouthStretch`, jaw slightly open; `oh` is `mouthFunnel` with `jawOpen`.

### Can an AI generate 52 correct blendshapes without sculpting any?

Answer: yes, to a defined quality bar, and no, to the "correct as a sculptor would" bar.

What a generator can do, and what this session measured:
- Bone-driven baking: rotate a bone, read the evaluated mesh, store as a shape key. Measured headless (§A).
- Shrinkwrap-based projection onto a donor surface. Measured headless (mechanism only).

What it can do that is not measured here and is a design task:
- Vector-field authoring. Each shape is anchors (vertex or region, displacement vector, Gaussian falloff). Deterministic, cheap, verifiable blind. This is the recommended path for lips and cheeks.
- Deformation transfer from a donor neutral-to-expression pair. Blender's Deformation Transfer add-on is the reference method. Not tested in 5.3 headless. Flagged. Alternative: barycentric vertex-delta transfer, which is an easy 40 lines and does not depend on the add-on.

What it cannot do:
- Produce anatomically subtle shapes (the crease under the lower lip in `mouthRollLower`, the crescent of `cheekPuff`) from numbers alone without authored anchors. Those need a human to choose the anchors, or a donor.

So the answer is: procedural generation of all 52 is real, and the quality is set by how many anchors you author per shape. A default set of anchors (about 6–10 per shape) gives plausible shapes that pass the blind checks in §8. Fine detail needs a donor.

### Procedure for a 52-shape set

1. Define the anchor set per shape in a data file (`face_shapes.json`): region, vector, falloff.
2. For the bone-driven shapes (`jawOpen`, `jawLeft`, `jawRight`, `jawForward`, `eyeLook*`, `eyeBlink*`, `browInnerUp`), pose-bake (§A).
3. For the remaining shapes, apply the vector field to the neutral vertices and store as shape keys.
4. Clamp each shape to a defined range (typically −0.2 to 1.2 on `value`).
5. Run the validation in §8. Reject and re-author any shape that fails.

---

## 8. Validation without eyes

| Check | Method | Pass threshold |
|---|---|---|
| Symmetry error | Mirror the mesh across X, match each vertex to its nearest partner with KDTree (`mathutils.kdtree`). Report the mean and max distance. | Mean below 0.5 mm, max below 2 mm at head scale |
| Blendshape range | Per shape, bounding-box and displacement max. Flag any shape where max displacement exceeds 1.5× the head's inter-eye distance. | No flags on the default range |
| Interpenetration | BVH overlap between upper lip and teeth, lower lip and teeth, tongue and palate, at each shape. `bpy.types.BVHTree` with `overlap`. | Zero overlaps at `mouthClose` and at `jawOpen` less than 0.5 |
| Normal flips | Per shape, count faces whose normal flipped relative to the neutral face. | Zero at all shapes up to 1.0 weight |
| Contact sheet | Render all 52 shapes in one image with Workbench, flat shading, vertex colour by displacement magnitude. | Visual check, Wren's eyes, or a simple heuristic: no shape renders as a black frame |

The contact sheet cost is estimated from the README's Workbench time of 0.62 s per 512×512 frame: 52 frames ≈ 32 s, plus compose time. Not measured in this session.

Blind checks in order: symmetry first (catches a mirrored bone), then interpenetration (catches lip-tooth overlap), then normal flips (catches inverted faces at extreme weights).

---

## 9. Spine rigging

### Options

| Method | What it does | Use for |
|---|---|---|
| FK chain | Each spine bone rotates from its parent | Simple, predictable, animator-friendly for small poses |
| Spline IK | A chain driven by a spline curve with control points | Large curves, breathing, bending. The best default for a human spine |
| Bendy bones (B-bones) | A bone that bends along its length, with `bbone_segments` | Smooth deformation of the mesh along the spine without extra bones |

Recommended: Spline IK over 3 to 5 control points (pelvis, lower spine, mid spine, chest, upper chest) with B-bones of 8–12 segments on each deform bone. FK for the animator on the control bones. Bendy B-bones for the mesh.

### Segment count

- Lower spine (lumbar): 2–3 deform bones.
- Upper spine (thoracic): 2–3 deform bones.
- Chest: 1 control bone, counter-rotating.
- Hips: 1 control bone.

Total 5–7 deform bones. More bones than this bring no extra deformation and make the animator's life harder.

### Twist distribution

Twist is distributed across the spine segments so the top does not rotate faster than the bottom. Each spine bone takes a fraction of the total twist:
- Copy Rotation constraint, Y-axis only (in the bone's local frame), with influence 0.2 to 0.33 per segment, cumulative to 1.0 at the top.
- Typical split over 4 segments: 0.25 each.

The exact values are a starting point and need a test on a pose with a 60° twist.

### Chest counter-rotation

The chest bone counter-rotates the pelvis so the upper body does not twist with the hips by accident. Implementation: Copy Rotation on the chest from the root or pelvis, with the inverse of the pelvis rotation. In practice, the chest control gets an opposite constraint so the animator can twist the chest without moving the pelvis, and the pelvis twist still moves the chest.

### Poseable for an animator

- Put FK controls on the spine bones and an IK-like control on the chest.
- Lock the spine's Y-axis rotation (twist) on the lower bones so the animator does not twist the pelvis by accident.
- Keep the total spine bone count low: 3 controls is enough for most animation.

Nothing in this section was measured. Read from the Rigify and Blender docs and general practice. Test before relying on it.

---

## 10. Proposed API

Two files. `face.py` is new. `rig.py` gets additions. All signatures, no bodies. Each function has a docstring that states its contract. These are proposals, not implemented code. Implement and test them before the kit depends on them.

### `forgekit/face.py` (new)

```python
"""Face topology, face generation, smoothing, eyes, blendshapes and validation.

Everything here builds from numbers. The loop map, landmark maps and ARKit list
are data, so the same code works for any head.
"""
from __future__ import annotations
from dataclasses import dataclass
import bpy
from mathutils import Vector

# ---------------------------------------------------------------- data tables

ARKIT52: tuple[str, ...] = (
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
    "eyeLookUpLeft", "eyeSquintLeft", "eyeWideLeft",
    "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight",
    "eyeLookUpRight", "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
    "mouthPressLeft", "mouthPressRight", "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight", "tongueOut",
)  # verified: 52 entries, unique

VRM_PRESETS: tuple[str, ...] = (
    "happy", "angry", "sad", "relaxed", "surprised", "aa", "ih", "ou", "ee", "oh",
    "blink", "blinkLeft", "blinkRight", "lookUp", "lookDown", "lookLeft", "lookRight",
    "neutral",
)  # recalled, not verified against spec

DLIB68_RANGES: dict[str, tuple[int, int]] = {
    "jaw": (0, 16), "brow_R": (17, 21), "brow_L": (22, 26), "nose_bridge": (27, 30),
    "nostrils": (31, 35), "eye_R": (36, 41), "eye_L": (42, 47),
    "lip_outer": (48, 59), "lip_inner": (60, 67),
}

MP468_ANCHORS: dict[str, int] = {
    "nose_tip": 1, "nose_base": 4, "forehead": 10, "lip_upper_inner": 13,
    "lip_lower_inner": 14, "eye_R_outer": 33, "eye_R_inner": 133,
    "mouth_R": 61, "mouth_L": 291, "chin": 152, "cheek_L": 234, "cheek_R": 454,
    "eye_L_outer": 263, "eye_L_inner": 362,
}  # common convention, not verified against a file this session

LOOP_MAP: dict[str, dict] = {
    # id: {"region": str, "rings": int, "role": str}
    "eye_L_outer": {"region": "eye_L", "rings": 3, "role": "blink, squint"},
    "eye_R_outer": {"region": "eye_R", "rings": 3, "role": "blink, squint"},
    "mouth_outer": {"region": "mouth", "rings": 3, "role": "funnel, pucker, smile"},
    "mouth_inner": {"region": "mouth", "rings": 2, "role": "lip seal"},
    "nose_to_jaw_L": {"region": "cheek_L", "rings": 1, "role": "cheek slide"},
    "nose_to_jaw_R": {"region": "cheek_R", "rings": 1, "role": "cheek slide"},
    "brow_L": {"region": "brow_L", "rings": 3, "role": "raise, furrow"},
    "brow_R": {"region": "brow_R", "rings": 3, "role": "raise, furrow"},
}  # full map: §1. Poles at nose wings, inner brows, chin, crown.

# ---------------------------------------------------------------- topology

def build_face_base(name: str = "FaceBase", scale: float = 0.09,
                    loop_map: dict | None = None) -> bpy.types.Object:
    """Build a loop-correct quad head base in bmesh from LOOP_MAP.

    The base has concentric eye and mouth loops, a nose-to-jaw loop per side,
    poles at the nose wings, inner brows and chin, and a crown pole. Returns a
    mesh object in the scene. Not shrinkwrapped; call shrinkwrap_to() next.

    scale: head half-width in metres. Default 0.09 gives a 0.18 m head width.
    """
    ...

def fit_landmarks(base: bpy.types.Object, landmarks: dict[str, Vector],
                  anchors: dict[str, int] | None = None) -> bpy.types.Object:
    """Move the base's loop vertices so its landmark anchors hit the given points.

    landmarks: name -> world-space Vector, keys matching MP468_ANCHORS or
    DLIB68_RANGES. Interpolates the displacement of the rest of the base with a
    2D thin-plate spline over the anchor set, so no vertex overshoots.
    Returns the base object.
    """
    ...

def shrinkwrap_to(base: bpy.types.Object, target: bpy.types.Object,
                  offset: float = 0.0, lock_loops: bool = True) -> bpy.types.Object:
    """Shrinkwrap the base onto target and apply the modifier headless.

    NEAREST_SURFACEPOINT, applied with modifier_apply. Measured to work
    headless (scratchpad sw_test.py). lock_loops clamps the displacement of
    loop vertices so eyelid margins and lip seals do not drift.
    """
    ...

# ---------------------------------------------------------------- smoothing

def relax(obj: bpy.types.Object, iterations: int = 3, factor: float = 0.4,
          preserve_creases: bool = True) -> bpy.types.Object:
    """Laplacian relax on a shrinkwrap base. Not for the final mesh.

    factor per iteration, 0.3–0.5. preserve_creases keeps edges with crease
    weight > 0 fixed. Use this only to remove folds left by shrinkwrap.
    """
    ...

def crease_edges(obj: bpy.types.Object, edges: list[int], weight: float = 0.9) -> None:
    """Set edge crease weights for the given edge indices (brow, lip margin).

    Attribute name differs across Blender releases. Verify on 5.3 before use.
    """
    ...

def smooth_face(obj: bpy.types.Object, viewport: int = 2, render: int = 3,
                keep_sharp: bool = True) -> bpy.types.Modifier:
    """Catmull-Clark via Subdivision Surface, weighted normals, keep-sharp.

    Creases hold the brow and lip margin under subdivision. Call after
    crease_edges(). Returns the subsurf modifier.
    """
    ...

# ---------------------------------------------------------------- eyes

def add_eyes(head: bpy.types.Object, ipd: float = 0.063,
             eye_radius: float = 0.012, iris_radius: float = 0.0055,
             cornea_bulge: float = 0.001) -> tuple[bpy.types.Object, bpy.types.Object]:
    """Add sclera and iris/cornea meshes into the two eye sockets.

    Positions come from the head's eye anchors. The iris is recessed by about
    0.4 mm. Returns (sclera, iris) for the left eye and right eye, in that order.
    """
    ...

def eye_target(rig: bpy.types.Object, focus: Vector,
               name: str = "eye_target") -> bpy.types.Object:
    """Create the shared look-at empty and parent it under the root control."""
    ...

def setup_eye_tracking(rig: bpy.types.Object, target: bpy.types.Object,
                       ipd: float = 0.063, lid_follow: tuple[float, float] = (0.6, 0.25)
                       ) -> None:
    """Damped Track each eye bone to the shared target, convergent by design.

    lid_follow: (upper, lower) fraction of the eye's pitch passed to the lids
    via Copy Rotation, X-axis only.
    """
    ...

def convergence_angle(ipd: float, focus_distance: float) -> float:
    """Yaw in radians for each eye toward the centre line: atan((ipd/2)/d)."""
    import math
    return math.atan((ipd / 2.0) / max(focus_distance, 1e-6))

BLINK_FRAMES = {24: (3, 1, 3), 30: (4, 2, 4)}  # (close, hold, open) at fps

def key_blink(rig: bpy.types.Object, frame: int, fps: int = 24) -> None:
    """Key one blink: close, hold, open, using the BLINK_FRAMES timing table."""
    ...

def key_saccade(target: bpy.types.Object, frame: int, to: Vector,
                duration_ms: float = 40.0, fps: int = 24) -> None:
    """Key one saccade on the eye target. Under ~1 frame at 24 fps reads as a cut."""
    ...

# ---------------------------------------------------------------- blendshapes

def bake_pose_to_shape_key(mesh: bpy.types.Object, rig: bpy.types.Object,
                           pose: dict[str, tuple[float, float, float]],
                           name: str) -> bpy.types.ShapeKey:
    """Pose the rig, read the evaluated mesh, store the result as a shape key.

    pose: bone name -> (rx, ry, rz) in radians, applied to the pose bones.
    Measured headless on a jaw-driven test head (scratchpad bake_test.py): the
    basis key is written first, the pose is reset to rest afterwards.
    """
    ...

def vector_field_shape(mesh: bpy.types.Object, name: str,
                       anchors: list[tuple[Vector, Vector, float]]) -> bpy.types.ShapeKey:
    """Create a shape key from anchors: (position, displacement, falloff radius).

    Each vertex takes the sum of Gaussian-weighted anchor displacements. Pure
    numeric, no sculpting. Use for lips, cheeks, brow and sneer.
    """
    ...

def wrap_shape(mesh: bpy.types.Object, donor_neutral: bpy.types.Object,
               donor_shape: bpy.types.Object, name: str) -> bpy.types.ShapeKey:
    """Transfer the donor's neutral-to-shape vertex delta onto mesh, barycentrically.

    The donor and mesh need a surface correspondence (shrinkwrap or nearest-point).
    Not the Deformation Transfer add-on. Not tested this session.
    """
    ...

def generate_arkit52(mesh: bpy.types.Object, rig: bpy.types.Object | None,
                     bone_drives: dict[str, dict[str, tuple[float, float, float]]],
                     anchors: dict[str, list[tuple[Vector, Vector, float]]]) -> list[str]:
    """Build all 52 ARKit shape keys: bone-driven where a drive exists, vector field otherwise.

    bone_drives: shape name -> {bone: (rx, ry, rz)}. anchors: shape name -> anchor list.
    Returns the list of created shape names in ARKIT52 order. Raises if a shape has neither.
    """
    ...

def vrm_map(arkit: dict[str, float]) -> dict[str, float]:
    """Combine ARKit weights into VRM 1.0 presets (aa, ih, ou, ee, oh, happy, ...)."""
    ...

# ---------------------------------------------------------------- validation

def symmetry_error(mesh: bpy.types.Object, axis: str = "X") -> dict[str, float]:
    """Mirror across the given axis, KDTree-match, return mean and max distance in metres."""
    ...

def interpenetration(mesh: bpy.types.Object, pairs: list[tuple[str, str]]) -> dict[str, int]:
    """BVH overlap count for named object pairs (lips vs teeth, tongue vs palate)."""
    ...

def normal_flips(mesh: bpy.types.Object, neutral_normals: list, threshold_dot: float = 0.0) -> int:
    """Count faces whose normal now points against the neutral face's normal."""
    ...

def contact_sheet(mesh: bpy.types.Object, shape_names: list[str], out_path: str,
                  cols: int = 8, cell: int = 256) -> str:
    """Render each shape with Workbench flat shading and compose one PNG grid.

    Estimated ~32 s for 52 frames at 512 px from the README's 0.62 s figure. Not
    measured for 52 frames this session.
    """
    ...

def validate_face(mesh: bpy.types.Object, rig: bpy.types.Object | None = None,
                  shape_names: list[str] | None = None) -> dict:
    """Run all blind checks and return one report: symmetry, interpenetration, flips, ranges."""
    ...
```

### `forgekit/rig.py` (additions)

```python
def fit_face_metarig(meta: bpy.types.Object, landmarks: dict[str, Vector]) -> None:
    """Move the face metarig's bones so eye, nose, lip and jaw anchors sit on landmarks.

    Uses the metarig's face bones (rigify_type faces.super_face, §A). Replaces
    fit_metarig()'s bbox-only placement for the face region. Test the regenerated
    super_face rig before relying on it; the eye and jaw bones first.
    """
    ...

def add_jaw_pivot(rig: bpy.types.Object, ear_canal: Vector, chin: Vector,
                  name: str = "jaw") -> bpy.types.EditBone:
    """Place the jaw bone pivot at the ear-canal line, behind and above the chin.

    The jaw bone head goes at roughly the ear-canal line (about 1–2 cm in front
    of and below the ear canal), not at the chin. The tail points at the chin.
    """
    ...

def add_tongue_chain(rig: bpy.types.Object, root: Vector, tip: Vector,
                     bones: int = 4, name: str = "tongue") -> list[str]:
    """Add a 3–4 bone tongue chain from root to tip, FK-driven by a single control."""
    ...

def add_spline_spine(rig: bpy.types.Object, points: list[Vector], segments: int = 3,
                     bbone_segments: int = 10, name: str = "spine") -> list[str]:
    """Spline IK spine over the given control points, B-bones on the deform chain.

    points: pelvis to upper chest, 3–5 points. Returns the deform bone names.
    """
    ...

def add_twist_distribution(rig: bpy.types.Object, bones: list[str],
                           axis: str = "Y") -> None:
    """Distribute twist across the chain: influence fractions summing to 1.0 at the top."""
    ...

def add_chest_counter_rotation(rig: bpy.types.Object, chest: str, pelvis: str) -> None:
    """Chest counter-rotates the pelvis so the upper body does not twist with the hips."""
    ...
```

Notes on the API:
- Nothing in `face.py` is implemented yet. The signatures and docstrings are the contract. Two behaviours are measured (bone-driven bake and shrinkwrap apply); everything else is design.
- `bake_pose_to_shape_key` has a working precedent in `scratchpad/bake_test.py`. Start there.
- `fit_face_metarig` and `add_spline_spine` need a real rig to test against. Not started.

---

## 11. Could not verify

- Rigify `super_face` regeneration after a landmark-based move of the face metarig bones. Not tested.
- Edge crease attribute name on Blender 5.3. Flagged; check before writing `crease_edges`.
- Deformation Transfer add-on availability and behaviour in 5.3 headless. Not tested. The barycentric path is the proposed alternative.
- 52-shape contact-sheet render time. Estimated only.
- The landmark index assignments in §6 against a loaded model file.
- VRM 1.0 preset names against the spec. Recalled.
- The FACS to ARKit correspondence against a published table.
- Eye timing and saccade numbers on a real rig. General biology, not measured.
- Spine twist fractions and counter-rotation values. Read, not tested.
- Apple's own full ARKit 52 page. The search returned only the two identifiers and third-party lists. The 52 identifiers are checked for count and uniqueness here, not against Apple's source.

