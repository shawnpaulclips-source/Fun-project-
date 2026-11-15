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
