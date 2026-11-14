# forge — Geometry, Materials, and Server Architecture

Research + design document. Section owner: geometry + materials + tool/server architecture.
Rigging and animation are owned elsewhere; see **§14 Seams** for the bolt-on points.

Date: 2026-11-14. Everything marked **[measured]** was run in this container today
(Ubuntu 24.04.5, Linux 6.18.44, Python 3.13.18, 4 cores, 15 GB RAM, no GPU, no X server).
Everything marked **[unverified]** is secondary-source and flagged as such.

---

## 0. Verdict (ten lines)

1. **Primary backend: the `bpy` pip wheel, version 5.3.0.** It installs and runs headless on
   this exact container — Python 3.13.18, no GPU, no display — **[measured]**. 427 MB wheel,
   927 MB installed, `import bpy` costs 0.55 s. That kills the "install Blender from apt and
   shell out to `blender --background`" plan dead: in-process is strictly better.
2. **One OS process per session, not per call.** `bpy` is a process-global singleton — you
   cannot hold two independent scenes in one interpreter. Process-per-session is forced by the
   library, and it conveniently gives crash isolation too.
3. **State lives in a `.blend` on disk; the process is a cache.** Save is 9 ms, load is 13 ms
   **[measured]**. Checkpoints are effectively free, so give Claude unlimited undo.
4. **Never render with EEVEE on a CPU-only box.** EEVEE at 512² took **24.7 s**; Cycles CPU at
   512²/32 spp took **1.95 s**; Workbench at 1024² took **0.78 s** **[measured]**. The usual
   "EEVEE fast, Cycles slow" intuition inverts completely without a GPU.
5. **The feedback loop is the product.** Workbench quad-view + wireframe + silhouette for
   structure, Cycles for material truth, and a numeric report (manifold/genus/volume/symmetry/
   Hausdorff) for everything a 512-pixel thumbnail hides.
6. **Hybrid tool surface: a declarative IR for ~90%, a typed op layer for the awkward 10%, one
   `forge_exec` escape hatch.** The IR exists because `bpy.ops` is context-dependent and that is
   the single largest source of model-written errors.
7. **Satellite libraries, not replacements:** `manifold3d` 3.5.5 for guaranteed-manifold CSG
   (3 ms for sphere−cube), `trimesh` 5.2.1 for analysis, `xatlas` 0.0.11 for deterministic UV
   charts, `pymeshlab` 2025.7 for decimation/sampling/Hausdorff **[measured]**.
8. **CAD: `build123d` 0.13.0**, not CadQuery directly, not OpenSCAD. Phase 2, behind the same IR.
9. **ML generation is a v2 accessory, not the engine.** TRELLIS.2-4B (MIT) needs ≥24 GB VRAM and
   outputs meshes the authors themselves say "may have small holes" — it's a blockout generator
   you then remesh, never a shipping asset.
10. **MCP target: revision `2026-07-28`, stateless.** Server-minted handles passed as ordinary
    tool arguments is now the *spec-blessed* way to keep state (SEP-2567) — which is exactly the
    design forge wants anyway.

---

## 1. Headless geometry backends

### 1.1 `bpy` pip wheel — **the pick**

| fact | value | source |
|---|---|---|
| Latest version | **5.3.0**, uploaded 2026-11-10 | PyPI JSON API **[measured]** |
| Python pin | `requires_python == 3.13.*` | PyPI **[measured]** |
| Wheel size | 427.5 MB (cp313, manylinux x86_64) | PyPI **[measured]** |
| Installed size | **927 MB** for `site-packages/bpy` | `du -sh` **[measured]** |
| License | **GPL-3.0** (as declared on PyPI) | PyPI `info.license` **[measured]** |
| `import bpy` | 0.549 s cold | **[measured]** |

Python version history of the wheel, straight off the PyPI API **[measured]**:

```
5.0.1  2025-12-16  cp311   374 MB
5.1.0  2026-03-17  cp313   391 MB   <- the jump to 3.13
5.2.4  2026-11-10  cp313   403 MB
5.3.0  2026-11-10  cp313   427 MB
```

So the brief's worry is resolved in the best possible direction: **bpy wheels moved to CPython
3.13 at Blender 5.1 (March 2026)** and this container's 3.13.18 is a direct match. Note the pin is
exact — `==3.13.*`. A container that upgrades to 3.14 breaks forge until the next Blender minor.
Pin the interpreter.

Verified working in-container, no display, no GPU **[measured]**:

- `bpy.ops.wm.read_factory_settings(use_empty=True)` — 43 ms
- 83 modifier types, 292 `GeometryNode*` RNA types, 108 `ShaderNode*` RNA types
- Subdivision (Catmull-Clark) L3 apply on Suzanne: **44 ms → 31,488 faces**; one more L2 on top:
  **249 ms → 503,808 faces**
- Voxel remesh @ 0.01: **1.65 s → 153,390 faces** (from 503 k)
- QuadriFlow `target_faces=8000`: **6.63 s → 7,514 faces** (from 153 k)
- `uv.smart_project` on 7.5 k faces: **18 ms**
- `object.bake(type='NORMAL')` to a 2048² image, Cycles CPU, 8 spp: **1.90 s**
- Exports all succeed: GLB, USDC, USDZ, FBX, OBJ, ABC, STL
- OpenVDB is in: modifiers `MESH_TO_VOLUME`, `VOLUME_TO_MESH`, `VOLUME_DISPLACE`;
  `RemeshModifier` modes `BLOCKS / SMOOTH / SHARP / VOXEL`
- Bake passes available: `COMBINED, AO, SHADOW, POSITION, NORMAL, UV, ROUGHNESS, EMIT,
  ENVIRONMENT, DIFFUSE, GLOSSY, TRANSMISSION`

**Three gotchas found by actually running it, all of which would have bitten us later:**

1. **The render engine enum in Blender 5.3 is `BLENDER_EEVEE`, not `BLENDER_EEVEE_NEXT`.** Setting
   `BLENDER_EEVEE_NEXT` raises
   `TypeError: enum "BLENDER_EEVEE_NEXT" not found in ('BLENDER_EEVEE','BLENDER_WORKBENCH','CYCLES')`
   **[measured]**. Every tutorial and every LLM trained on 4.2–4.5 will get this wrong. The IR must
   normalise engine names.
2. **Draco and MeshOptimizer are absent from the wheel.** The glTF exporter logs
   `ERROR Draco is not available because library could not be found at .../libbf_intern_draco_bridge.so`
   and the same for `libbf_intern_meshopt_bridge.so` **[measured]**. So **no GLB geometry
   compression from the bpy wheel.** Fix: post-process with `gltfpack` / `gltf-transform` on Node
   (Node v22.23.4 is already present **[measured]**).
3. **`Material.use_nodes` emits a DeprecationWarning: removal in Blender 6.0** **[measured]**.
   Don't write it into the IR compiler.

**GPL reality.** The wheel declares GPL-3.0, and forge `import`s it in-process. The conservative
reading — and the only safe one to build on — is that the forge server, as distributed, is a
GPL-3.0 work. I could find **no official Blender Foundation statement about the pip wheel
specifically** **[unverified]**; the Extensions Platform requires GPL-compatible licensing, and the
2012 `bf-committers` proposal for a Python-script GPL exception was never adopted. Practical
consequence for a self-hosted free tool: none. Just ship forge GPL-3.0 and stop thinking about it.
If a closed component is ever wanted, it goes behind the socket as a separate process, not an
import.

- PyPI: https://pypi.org/project/bpy/
- Blender Python API: https://docs.blender.org/api/5.3/
- Extensions licensing thread: https://projects.blender.org/blender/blender-manual/issues/104850

### 1.2 `blender --background --python` (the subprocess route) — rejected

Same engine, strictly worse ergonomics: you cannot keep a live Python object graph across calls,
you pay full application startup instead of 0.55 s, stdout is polluted with render progress that
has to be parsed out, and errors arrive as text rather than as exceptions you can introspect.
Keep it as a *fallback only* for the case where the bpy wheel's Python pin and the host Python
disagree. And note apt's `blender` on Noble is old; you'd be installing the tarball anyway.

### 1.3 Geometry Nodes from Python — useful, but not the authoring surface

Buildable: `bpy.data.node_groups.new(...)`, nodes by RNA name, `tree.links.new(...)`, sockets via
`tree.interface.new_socket(...)`. 292 node types exist in 5.3 **[measured]**. `nodebpy` on PyPI
wraps this and offers `to_python()` to dump an existing tree back to code **[unverified — secondary
source]**.

**It's a trap as a primary authoring target for an LLM.** Reasons:

- A useful procedural tree is 30–80 nodes; emitting it as ~200 lines of `links.new` calls burns
  tokens with a per-line failure mode and no type checking.
- Socket addressing is positional-vs-named and inconsistent (two sockets both named `Value` on a
  Math node — index is the only reliable key).
- Known Python-side link bug on group output sockets (blender#105619) **[unverified]**.

**Where it does belong:** as *compiled output of the IR*, and as a library of a dozen
hand-authored, parameterised node groups shipped inside a `forge_assets.blend` — `panel_greeble`,
`edge_wear_mask`, `scatter_rivets`, `cable_run`. Claude sets parameters on a known-good group. It
does not wire graphs node by node.

### 1.4 Hard-surface / mechanical: `build123d` > CadQuery > OpenSCAD

**[measured]** PyPI state:

| lib | version | date | license | python |
|---|---|---|---|---|
| `build123d` | 0.13.0 | 2026-09-21 | Apache-2.0 | `>=3.11,<3.15` |
| `cadquery` | 2.8.0 | 2026-06-21 | Apache-2.0 | `>=3.11` |
| `ocp-vscode` | 4.2.0 | 2026-10-13 | Apache-2.0 | viewer |

Both sit on Open CASCADE (LGPL-2.1-with-exception) via the OCP bindings. `build123d` wins for this
project: it is plain Python with context managers instead of CadQuery's method-chaining fluent API,
which means an LLM writes it with far fewer "which object does `.faces()` return now" errors, and
its `>=3.11,<3.15` range covers 3.13. True B-rep gives exact fillets/chamfers/drafts and analytic
booleans — things that are a nightmare on triangle soup.

OpenSCAD: CSG-only, no fillets worth having, its own non-Python language, and tessellation quality
you don't control. Skip it. The one virtue of the existing OpenSCAD-based MCP servers is that they
were easy to write.

**Integration seam:** B-rep → STEP → mesh is a one-way door. Tessellate in build123d, hand the
triangles to Blender as a new object, keep the B-rep alongside for re-tessellation at a different
LOD. Phase 2.

### 1.5 SDF / implicit modelling

**A correction the brief needs:** the PyPI package named **`sdf` (0.3.8) is not the SDF modelling
library.** It's a scientific-data-format package — `Dataset`, `hdf5`, `units`, `convert_unit`
**[measured]**. fogleman's `sdf` has to come from git. Don't put `pip install sdf` in a setup
script expecting primitives.

- **libfive** — MPL-2.0, PyPI `0.1.0a3` from **2020-10-11** **[measured]**, repo copyright ends
  2021 **[unverified]**. Dead for Python purposes. Its live descendant is **Fidget** (Rust,
  © 2022–2026), which has no Python story worth building on today.
- **`sdfx`** — turned up nothing in search **[unverified]**; treat as not available.
- **manifold3d 3.5.5** **[measured]** — this is the real answer for CSG. `Manifold.sphere() −
  Manifold.cube()` in **3 ms**, exact genus/volume queries, manifold-by-construction. It has
  `level_set` for implicit → mesh. Also the boolean engine `trimesh` dispatches to (9 ms on a 5 k
  mesh **[measured]**).

**Extractor verdict** (the part of the brief that actually matters):

| extractor | sharp features | watertight/manifold | use in forge |
|---|---|---|---|
| Marching cubes | no — corners always rounded | can emit non-manifold verts in ambiguous configs | thumbnails only |
| Surface nets | no — smoother than MC, still rounds | usually | never |
| Dual contouring | **yes** — one vertex per cell, QEF-placed | **no guarantee** — can self-intersect | geometry pass |
| Manifold DC / marching tets on BCC | no | **yes by construction** | repair pass |
| CGAL Isosurfacing (2025) TMC | no | **guaranteed 2-manifold + watertight** | repair pass |

So: **no single extractor gives you both.** The pipeline is two-stage — dual contouring for the
sharp geometry, then a manifold-guaranteed pass (`manifold3d`, or ManifoldPlus-style remesh) to
make it legal. Anyone who tells you one algorithm does both is selling something.
A 2026 preprint (arXiv 2604.00157) beats both MC and standard DC on the ABC CAD set and still
states it does not inherently guarantee manifoldness **[unverified]**.

- CGAL Isosurfacing: https://www.cgal.org/2025/10/10/Isosurfacing/
- Dual contouring primer: https://www.boristhebrave.com/2018/04/15/dual-contouring-tutorial/
- ManifoldPlus: https://arxiv.org/abs/2005.11621

**Practical note:** because Blender ships `MESH_TO_VOLUME` / `VOLUME_DISPLACE` /
`VOLUME_TO_MESH` **[measured]**, a lot of SDF-flavoured work (volumetric fuse, VDB displacement,
voxel-level smoothing) happens in-process with no extra dependency at all. Reach for an external
SDF stack only for analytic CSG trees, and when you do, reach for `manifold3d`.

### 1.6 Mesh utility libraries — all installed and working **[measured]**

| lib | version | license | size | role | measured |
|---|---|---|---|---|---|
| `trimesh` | 5.2.1 (2026-11-12) | MIT | 4.6 MB | analysis, I/O, proximity, convex hull | watertight/winding/euler/volume/area in 8 ms |
| `manifold3d` | 3.5.5 (2026-11-13) | Apache-2.0 | 3.3 MB `.so` | guaranteed CSG, genus, level sets | sphere−cube 3 ms |
| `xatlas` | 0.0.11 (2025-07-04) | MIT | 568 KB `.so` | UV charting + packing | 2,562-vert icosphere 0.32 s |
| `pymeshlab` | 2025.7.post1 | **GPL-3.0** | **280 MB** | 281 filters: decimate, remesh, curvature, sampling | decimate→500 faces 29 ms |
| `open3d` | 0.20.0 | MIT | large | point clouds, Poisson recon | not installed — only needed if ML path lands |
| `libigl` | 2.6.3 | MPL-2.0 | — | **no cp313 wheel** (cp310/11/12/38/39 only) | **blocked on 3.13** |
| `vtk` | 9.7.2 | BSD | large | not needed | — |

Two findings worth writing down:

- **`libigl` 2.6.3 has no cp313 wheel** **[measured]** — cp310, cp311, cp312, cp38, cp39 only. So
  no libigl on this stack without building from source. Everything we'd want it for (curvature,
  geodesics, parametrisation) is covered by pymeshlab + trimesh. Drop it.
- **`pymeshlab` needs `libOpenGL.so.0`,** which Ubuntu 24.04 minimal does not ship (it has
  `libGL.so.1`). Without it, plugin loading fails and you silently get **235** filters; with
  `apt install libopengl0 libegl1` you get **281**, and `meshing_decimation_quadric_edge_collapse`
  starts existing **[measured]**. This is exactly the class of bug that eats an afternoon.

### 1.7 Not relevant / not free

Houdini Engine (license cost, no free headless commercial path), Plasticity (paid, no API),
Unity/Unreal headless (engines, not modellers; the heavyweight way to bake). Godot 4 headless is
genuinely free and scriptable but its mesh-authoring API is far thinner than Blender's — no reason.

---

## 2. Detail pipeline — blockout to "ultra detailed" with no hand sculpting

The pipeline, in the order forge runs it:

```
blockout (IR primitives / build123d / ML mesh)
  → boolean + bevel (hard surface)        manifold3d or Blender BOOLEAN+exact
  → voxel remesh                          unify into one watertight shell
  → quad remesh                           QuadriFlow, target ~5–20k quads
  → subdivision                           Catmull-Clark L2–L3 for the HIGH mesh
  → procedural displacement               noise × curvature/cavity/AO masks
  → bake HIGH → LOW                       normal, AO, curvature, position
  → LOD chain                             decimate 1.0 / 0.5 / 0.25 / 0.1
  → UV + material + texture pack
  → export + validate
```

### 2.1 Subdivision and displacement

`SUBSURF` is Catmull-Clark on OpenSubdiv and it is *fast*: L3 on Suzanne = 44 ms → 31,488 faces;
stacking another L2 = 249 ms → 503,808 faces **[measured]**. Memory is the limit, not time: budget
~1 GB per ~4 M faces and cap the high mesh at 2–4 M on a 15 GB box.

Displacement options, in increasing fidelity:
- **`DISPLACE` modifier + procedural texture** — cheapest, needs enough base density.
- **Vector displacement via Geometry Nodes** `Set Position` with a 3-vector field — handles
  overhangs that scalar displacement can't.
- **Multires + `VOLUME_DISPLACE`** — VDB-space displacement, good for erosion-like forms.

### 2.2 Procedural detail (the part that replaces sculpting)

Three masks do most of the work, all computable headless:

- **Curvature / convexity** — per-vertex, from `pymeshlab`'s
  `compute_curvature_and_color_*_per_vertex` family **[measured: filters present]**, or from
  dihedral angles in `bmesh`. Drives edge wear: convex high → paint chips, exposed metal.
- **Cavity / AO** — bake `type='AO'` **[measured: pass available]** into a vertex colour or a map.
  Drives grime accumulation.
- **Position / gradient** — world-Z or a custom axis. Drives streaking, rust running downward,
  dust settling on up-faces.

Combine as `wear = noise(scale) * curvature_mask ** k * (1 - ao)`. That's a shader node graph *and*
a displacement input, and it's the same recipe in both, which is why `forge_detail` takes a recipe
dict rather than a free-form graph.

Edge damage geometry (not just texture): `BEVEL` with a small width and a noise-modulated weight,
or boolean-subtract scattered small spheres along high-curvature edges via Geometry Nodes scatter.

### 2.3 Quad remeshing — the quality gate

| method | determinism | CLI/headless | measured | notes |
|---|---|---|---|---|
| **Blender QuadriFlow** (`bpy.ops.object.quadriflow_remesh`) | seeded; stable with fixed seed | in-process | **6.63 s, 153 k → 7,514 faces** **[measured]** | honours `target_faces`; `use_preserve_sharp`, `use_preserve_boundary` |
| Blender voxel remesh | deterministic | in-process | **1.65 s, 503 k → 153,390 faces** **[measured]** | OpenVDB; unifies topology, destroys edge flow |
| `RemeshModifier` SHARP/BLOCKS | deterministic | in-process | — | SHARP keeps creases better than VOXEL |
| Instant Meshes | seeded | external binary, no pip | — | nicer edge flow than QuadriFlow on organics; one more binary to ship |
| pymeshlab isotropic remesh | deterministic | in-process | — | **triangles**, not quads; good for even density before baking |

**Call:** QuadriFlow in-process is the default, with a fixed seed recorded in the checkpoint so a
rerun reproduces. The measured 7,514-from-8,000-requested is normal — QuadriFlow approximates the
target, so the tool must *report the actual count* and never let Claude assume it got what it asked
for. Instant Meshes is a v2 opt-in for organic shapes.

Euler check on the QuadriFlow output **[measured]**: `V=3708 E=7412 F=3706`, Euler characteristic
**2**, genus **0**, non-manifold edges **0**, non-manifold verts **0**, degenerate faces **0**.
Clean, closed, correct. That is the number forge should report after every remesh.

### 2.4 Decimation, LODs, and baking high → low

- `DECIMATE` COLLAPSE with `ratio` for the LOD chain; `PLANAR` for hard-surface flat regions.
- `pymeshlab meshing_decimation_quadric_edge_collapse` to 500 faces in **29 ms** **[measured]** —
  faster and with better quadric weighting than Blender's for aggressive ratios.
- **Normal-map bake, FOSS xNormal equivalent:** `bpy.ops.object.bake(type='NORMAL')` with
  `use_selected_to_active=True` and a `cage_extrusion`. 2048² at 8 spp Cycles CPU: **1.90 s**
  **[measured]**. That is cheap enough to bake on every iteration, which changes the whole
  interaction model — Claude can bake-and-look, not bake-once-at-the-end.
- Bake AO, curvature (via a pointiness-driven emit shader), and position in the same pass set.
- Tangent-space convention: Blender bakes **OpenGL +Y (green up)**. Unreal wants **DirectX −Y**.
  forge must flip G on export when `target_engine="unreal"` — a silent-corruption bug otherwise.

### 2.5 Boolean-heavy hard surface, and keeping topology sane

Order matters: boolean → *then* voxel remesh → *then* quad remesh. Booleans leave long thin
triangles and T-junctions at the seams; a voxel pass launders them into a uniform shell; QuadriFlow
then gives you quads with usable flow.

Use `manifold3d` for the booleans where correctness matters — it is manifold by construction and
30× faster than going through Blender's modifier stack on small meshes **[measured: 3 ms vs. a
modifier-apply round trip]**. Use Blender's `BOOLEAN` with `solver='EXACT'` when the operands carry
materials/UVs you want preserved.

Always re-run the `forge_inspect` numeric report after a boolean. Booleans are where watertightness
dies.

### 2.6 Hair / fur / particles

Blender curves (the new `CURVES` type) are the authoring form. glTF has **no hair primitive**, so
there are exactly two export paths: convert curves to a mesh (`Curve to Mesh` in Geometry Nodes →
real tubes, expensive) or convert to **camera-facing card geometry with an alpha-tested texture**,
which is what games actually ship. Both are mesh conversion, so both export fine. Alembic carries
curves natively if the target is DCC-to-DCC rather than a game engine.

Out of scope for v0/v1. The tool surface leaves `forge_detail(recipe="hair_cards")` as the hook.

---

## 3. UV + texturing

### 3.1 Unwrapping headless

| option | determinism | measured | verdict |
|---|---|---|---|
| **`xatlas` 0.0.11** (Python bindings, `mworchel/xatlas-python`, MIT) | **deterministic** for fixed input + options | 2,562-vert icosphere → 2,794 UV verts in **0.32 s** **[measured]** | **default.** Runs without Blender, charts + packs in one call, and Claude can trust it blind |
| Blender `uv.smart_project` | **not reliably deterministic** — reported to give differing island counts across runs/versions **[unverified, forum]** | 7.5 k faces in **18 ms** **[measured]** | fast, fine for a throwaway bake target; not for a shipping asset |
| Blender `uv.pack_islands` | deterministic given islands | — | use it *after* xatlas when you want Blender's packer and margin control |
| UVAtlas (DirectXMesh) | deterministic | — | Windows-centric, no maintained Python path. Skip |
| Ministry of Flat | — | — | **no free path.** Commercial; there is a free non-commercial build historically but no licence to rely on. Skip |

**Call:** `xatlas` for the real unwrap, with `forge_uv(method="xatlas")` as the default and
`method="smart_project"` as the quick option. The reason is not quality — a vendor benchmark of 13
models compared Smart UV Project, xatlas 0.0.11 and their own tool but packed them with *different
packers*, so it proves nothing **[unverified]** — the reason is **determinism**. Claude iterates;
an unwrap that changes under it invalidates every baked map from the previous turn.

`xatlas.parametrize(vertices, faces)` returns `(vmapping, indices, uvs)` **[measured]** — note the
vertex count changes (2,562 → 2,794) because seams split vertices. The tool must carry the mapping
back so vertex attributes survive.

### 3.2 Procedural PBR material authoring from code

**Shader node trees via Python** is the practical answer — 108 `ShaderNode*` types in 5.3
**[measured]**. The same objection as geometry nodes applies, with the same fix: forge ships a
small set of parameterised node groups and the IR sets their inputs.

The material IR covers what actually matters:

```yaml
material:
  id: mat_hull
  base: { color: [0.18, 0.19, 0.21], roughness: 0.42, metallic: 1.0 }
  layers:
    - { type: edge_wear,   mask: curvature, amount: 0.35, color: [0.55,0.55,0.58], roughness: 0.25 }
    - { type: grime,       mask: cavity,    amount: 0.5,  color: [0.08,0.07,0.06], roughness: 0.8 }
    - { type: noise_rough, scale: 12.0,     amount: 0.15 }
  normal: { source: baked, strength: 1.0 }
```

That compiles to a node graph deterministically. Claude never names a node.

Alternatives considered:
- **Material Maker** (GPL-3.0, Godot-based) — genuinely good procedural PBR, but it's a GUI app
  with a weak headless story. Its `.ptex` graphs can't be authored sanely from an LLM.
- **`texturenodes`** — nothing current found **[unverified]**.
- **SVBRDF generators** (image → PBR stack) — needs a GPU and gives you photo-derived maps, not
  authored ones. v2, alongside the ML path.

### 3.3 Baking headless, GPU vs CPU

**[measured]** on this container, 4 cores, no GPU:

| engine | 512² | 1024² | 2048² |
|---|---|---|---|
| **Workbench** | **0.151 s** | **0.783 s** | **2.455 s** |
| **Cycles CPU** (32 spp) | **1.95 s** | **7.99 s** | — |
| Cycles CPU (128 spp) | — | 13.85 s | — |
| **EEVEE** | **24.70 s** | **28.66 s** | **43.92 s** |

`gpu.platform.backend_type_get()` returns **`VULKAN`** — software Vulkan (lavapipe), no hardware
**[measured]**. Cycles device enumeration: `CUDA → []`, `OPTIX → []`, with
`WARNING CUEW initialization failed: Error opening the library` **[measured]**.

**This table is the single most important empirical result in the document.** EEVEE on a software
Vulkan rasteriser is 12–30× *slower* than Cycles on the CPU for the same frame. Every "use EEVEE
for fast previews" instinct is wrong here. The rule for forge:

- **structure / wireframe / silhouette / quad views → Workbench** (sub-second)
- **material truth, AO, normals, any bake → Cycles CPU, low spp** (seconds)
- **EEVEE → only if `forge_capabilities()` reports a real GPU.** Otherwise refuse it and say why.

With a GPU: OptiX (NVIDIA) or HIP (AMD) make Cycles 10–50× faster and EEVEE becomes instant. The
degradation matrix is in §8.

Bake is Cycles-only in Blender, so a bake always costs CPU seconds. 2048² normal at 8 spp = 1.90 s
**[measured]**; a full 4-map set at 4096² will be roughly 30–60 s. That crosses the threshold where
MCP **progress notifications** matter (§6.9).

### 3.4 Texel density, UDIMs, channel packing

- **Texel density target:** 1024 px/m for hero props, 512 px/m for environment, 2048 px/m for
  first-person-visible detail. forge computes it as
  `density = sqrt(sum(uv_triangle_area) * texture_px²) / sqrt(sum(world_triangle_area))` and
  reports px/m per material, plus the min/max across islands so Claude can see inconsistency.
  This is a *measurable* property, which is exactly the kind of thing that replaces eyes.
- **UDIMs:** Blender supports tiled images natively. Worth it above ~4 materials or for film work;
  **glTF does not support UDIMs at all**, so for a game target forge must flatten to a single
  atlas per material. Make `forge_uv(udim=False)` the default and warn loudly if a UDIM asset is
  exported to glTF.
- **Channel packing** — the conventions that matter:
  - **glTF 2.0 / Khronos "ORM"**: occlusion **R**, roughness **G**, metallic **B**, texture set to
    Non-Color. Base colour separate, sRGB. (Khronos art pipeline guide; Blender's glTF docs
    confirm G=roughness, B=metallic.)
  - **Unreal**: same R=AO / G=Roughness / B=Metallic packing, but **normal maps are DirectX
    (−Y)** — flip green.
  - **Unity URP (Lit)**: metallic in **R**, smoothness in **A** of the MetallicGloss map;
    smoothness is `1 − roughness`. Not ORM. forge must *convert*, not relabel.
  - Height/displacement stays its own single-channel 16-bit file. Packing it into an 8-bit channel
    produces visible stepping.

Sources:
- Khronos art pipeline: https://www.khronos.org/blog/art-pipeline-for-gltf
- Blender glTF materials: https://docs.blender.org/manual/en/5.3/addons/scene_gltf2.html
- xatlas: https://github.com/jpcy/xatlas · bindings https://github.com/mworchel/xatlas-python

---

## 4. ML generation — what's actually usable, free, local, late 2026

### 4.1 Open-weights image-to-3D

| model | license | weights | VRAM | output | verdict |
|---|---|---|---|---|---|
| **TRELLIS.2-4B** (Microsoft) | **MIT** | open, HF `microsoft/TRELLIS.2-4B` | **≥24 GB**, NVIDIA, Linux only, verified A100/H100 | mesh + **PBR materials**, GLB; "O-Voxel" field-free sparse voxels | **best open option.** 512³≈3 s, 1024³≈17 s, 1536³≈60 s on H100 |
| Hunyuan3D **2.1** | Tencent Community License | open (last open release) | ~10 GB shape, **~29 GB** shape+texture **[unverified, conflicting: 6/16 GB also cited]** | mesh + texture | good quality, **licence is the problem** |
| Hunyuan3D 2.5 / 3.0 / 3.1 / PolyGen | — | **hosted only, no weights** **[unverified but consistent across sources]** | — | — | not self-hostable. Rules 3.0 out of forge |
| Stable Fast 3D (SF3D) | Stability community | open | modest | fast, lower fidelity | sub-second; good for a rough blockout |
| TripoSR | MIT | open | low | rough | the "runs on anything" option |
| InstantMesh / CRM / Unique3D | mixed | open | 16–24 GB | marching-cubes-ish | 2024-era; superseded by TRELLIS.2 |
| Hi3DGen | — | open | — | best geometry per community testers **[unverified, single blog]** | worth a look if geometry > texture |

TRELLIS.2's own model card is unusually honest and the quote should go straight into forge's docs:
O-Voxels "handle arbitrary topology, including open surfaces and non-manifold geometry", but **"raw
generated meshes may have small holes or minor topological discontinuities"** and watertight
applications "need post-processing like hole-filling", for which they ship scripts. Input is a
**single image only** — no text conditioning; text-to-3D means running a text-to-image model first.

- https://huggingface.co/microsoft/TRELLIS.2-4B (paper arXiv 2512.14692)

### 4.2 Native mesh generation — the interesting thread, chased

This is the line of work that outputs *face sequences* rather than isosurfaces, so the topology is
artist-shaped rather than marching-cubes blob:

- **QuadGPT** — ICLR 2026, arXiv **2509.21420**. Presented as the **first autoregressive model to
  generate native quad-dominant meshes end-to-end**. Point cloud in, structured face sequence out.
  Its own paper flags the deployment gap that matters to us: it's trained on point clouds sampled
  from clean artist assets but gets deployed on **noisy point clouds derived from AI-generated
  implicit fields**. So the natural pairing — TRELLIS.2 for shape, QuadGPT for retopology — is
  precisely the case the authors warn about.
- **Mesh-Pro** — arXiv **2603.00526** (March 2026). Artist-style quad generation with
  diagonal-aware mixed tri/quad tokenisation and a ray-based geometric-integrity reward.
- **QuadLink** — arXiv **2605.16813** (May 2026). Autoregressive quad-dominant via point-relation
  learning.
- **MeshAnything / MeshAnythingV2** — VQ-VAE mesh vocabulary + autoregressive transformer; the
  lineage these build on. **Meshtron** scales it with an hourglass transformer.
- **LLaMA-Mesh** — mesh-as-text in an LLM. Interesting, low face budget, no credible successor
  found **[unverified]**.
- **Tripo P2.0** — commercial, Sept 2026. Claims the first native quad meshes in the industry: up
  to **50 k faces triangle / 25 k quad** **[unverified, company claim]**.

**Honest status:** every one of these is a paper or a product. I could not confirm downloadable,
permissively-licensed weights with a working inference path for any of QuadGPT / Mesh-Pro /
QuadLink **[unverified]**. Face-budget ceilings in this family are also low (thousands, not
hundreds of thousands), which suits game props and nothing larger.

**So the honest verdict on native-mesh ML in forge: watch it, don't build on it.** `forge_remesh`
gets a `method="learned"` slot that raises `NotImplementedError` with a pointer to this section
until weights exist. QuadriFlow at 6.6 s and genus-0, zero-non-manifold output **[measured]** is a
hard baseline to beat.

### 4.3 Part-aware generation (matters for rigging)

**PartCrafter** — NeurIPS 2025, arXiv **2506.05573**, code at `github.com/wgsxm/PartCrafter`.
Compositional latent diffusion transformer, built on a pretrained whole-object mesh DiT. Generates
**multiple separate parts from a single RGB image with no segmentation step**, up to **16 parts**
**[unverified, secondary]**. HF Space exists (`alexnasa/PartCrafter`) and pulls weights from
`wgsxm/PartCrafter`; the NeurIPS version states code and training data released. No VRAM figure
published **[unverified]**.

**This is the one ML result the rigging researcher should care about**, because separable parts are
what a rig needs. Flag it to them.

### 4.4 Paid APIs — note and flag

Tripo, Meshy, Hyper3D Rodin, Tencent hosted Hunyuan3D. All have free tiers; all are **not
self-hosted** and therefore fail the project's stated requirement. `blender-mcp` wires all four
into one `generate_3d` tool, which is a reasonable product decision and the wrong one for forge.
If they go in at all they go behind an explicitly-labelled `forge_generate(backend="cloud:tripo")`
that refuses to run without an opt-in flag, so nobody's geometry silently leaves the box.

### 4.5 Where ML belongs in forge

**Nowhere in v0 or v1, and only at the front of v2.**

- **It cannot run here.** No GPU; TRELLIS.2 needs ≥24 GB VRAM and is Linux+NVIDIA only. There is no
  CPU fallback worth having — these are multi-billion-parameter diffusion/flow models; CPU
  inference is hours, not minutes.
- **It's a blockout generator.** Its output goes in at the top of §2's pipeline and gets voxel +
  quad remeshed, UV'd and baked by procedural code. Which means **the procedural pipeline is the
  product and ML is one optional input to step 1.**
- **Procedural beats it outright** for: anything mechanical or parametric, anything needing exact
  dimensions, anything needing clean edge flow, LOD chains, UVs, texel density, and every form of
  determinism and reproducibility.
- **ML beats procedural** for: "I have a concept image of an organic thing and no reference
  geometry." That's a real need. It's just not most needs.

Fallback when no GPU is present: `forge_generate` returns a structured refusal naming the missing
capability, and suggests `forge_apply_ir` with primitives instead. It does not silently produce
something worse.

---

## 5. The feedback loop — making Claude good at 3D

Claude writes code and cannot see. Everything else in this document is plumbing; this is the part
that decides whether forge works.

### 5.1 Render-to-image, with measured costs

**[measured]** from §3.3. The views forge offers and what each costs on this CPU-only box:

| view | engine | res | cost | what it answers |
|---|---|---|---|---|
| `quad_ortho` — front/side/top/persp in one 2×2 image | Workbench | 4× 512² | **~0.6 s** | proportion, alignment, silhouette, symmetry |
| `wireframe` | Workbench, wire overlay | 1024² | **0.78 s** | topology density, edge flow, stray geometry |
| `matcap` / `solid` | Workbench studio light | 1024² | **0.78 s** | form reading without material noise |
| `turntable` — 8 frames | Workbench | 8× 512² | **~1.2 s** | hidden-side errors, asymmetry |
| `shaded` | Cycles CPU 32 spp | 1024² | **7.99 s** | material truth, roughness, metalness |
| `silhouette` — flat black on white | Workbench | 512² | **0.15 s** | compare against a reference shape numerically |
| `uv_layout` | SVG/PNG from UV coords | 1024² | <0.1 s | seams, overlap, wasted atlas space |

A full "look at it" pass — quad ortho + wireframe + one Cycles shaded — is **~9 s and three
images**. That is the right granularity for an iteration.

### 5.2 Feeding images back

MCP tool results carry `ImageContent` (base64 + mimeType) — supported since the very first spec
revision, `2024-11-05`, and still current in `2026-07-28`. So `forge_render` returns real image
blocks and Claude genuinely sees its model.

**Budget discipline, because images are expensive in context:**

- Default to **512²**, PNG, and a 2×2 composite rather than four separate images. One image block
  instead of four.
- Hard cap: **3 image blocks per tool result**. More than that and the tool returns handles plus a
  contact sheet.
- Every render also returns a numeric block (bbox, face count, the inspect summary) so Claude can
  reason without re-looking.
- Never auto-render. Rendering is an explicit tool call, because Claude should be deciding *when*
  it needs eyes.

### 5.3 Numeric introspection — eyes are the backup, not the primary

Cheap, exact, token-small. All of these are computed in **8 ms** on a 5 k mesh via
`trimesh` / `bmesh` **[measured]**:

- `is_watertight`, `is_winding_consistent` — **[measured: True/True on an icosphere]**
- `euler_number`, derived **genus** — **[measured: Euler 2, genus 0 on QuadriFlow output]**
- `volume` (signed — negative means inverted normals), `area` — **[measured: 4.17974 / 12.55135]**
- non-manifold **edge** count, non-manifold **vertex** count — **[measured: 0 / 0]**
- degenerate faces (area < 1e-9), zero-length edges, duplicate verts
- loose/floating geometry: connected-component count and per-component volume
- n-gon histogram: tri / quad / 5+ counts — the quad-purity number
- **symmetry error**: mirror across a plane, nearest-neighbour distance, report RMS and max
- **silhouette signature**: Workbench black-on-white at 6 fixed angles → Hu moments or an IoU
  against a reference. Lets Claude ask "is this the right shape?" numerically.
- **Hausdorff / Chamfer** against a reference: `trimesh.proximity.signed_distance` gave a max
  deviation in **0.1 s** on a 500-point sample **[measured]**; `pymeshlab`'s sampling filters do
  the proper symmetric version.
- **curvature histogram** from pymeshlab's `compute_curvature_*` family **[measured: present]**
- **texel density** per material, min/mean/max across islands (§3.4)
- UV: overlap area, out-of-bounds islands, island count, packing efficiency

A `forge_inspect` result is maybe 400 tokens and catches the large majority of real defects —
inverted normals, holes, floaters, a boolean that failed, a mirror that didn't. Renders catch the
rest: bad proportion, ugly form, wrong read.

### 5.4 Scene query that doesn't blow the context

`forge_scene` returns a **projection**, never a dump: the caller names the `fields` it wants, a
`root` to start from, a `depth`, and a `limit`+`cursor`. Default projection is
`id, type, parent, face_count, bbox_size` — one line per object. 200 objects ≈ 1,200 tokens.
Mesh data is **never** in the response; it's behind a handle.

`blender-mcp` landed on the same answer independently — its `get_scene_info` is "a compact summary
with one line per object, using the fields you select", with `root`/`query` to drill in. Converging
designs are usually right.

### 5.5 What the existing projects got right and wrong

**`ahujasid/blender-mcp`** (MIT, ~10.5 k stars **[unverified]**) — the reference implementation.
Ten tools: `execute_blender_code`, `look`, `get_scene_info`, `generate_3d`,
`generate_gaussian_world`, `search_assets`, `import_asset`, `get_addon_status`,
`disable_telemetry`, `record_trajectory_feedback`. Architecture: a Blender **addon** runs a socket
server inside a **GUI Blender**; the MCP server speaks JSON over TCP to `localhost:9876`.

Right:
- **`look` is the correct abstraction** — viewport or camera or *auto-framed multi-angle*, several
  shading modes, and it can show a render or an image file. That's §5.1 as one tool.
- **Field-selected, one-line-per-object `get_scene_info`** — §5.4.
- **An escape hatch exists** and isn't apologised for.
- Asset-library integration (Poly Haven / Sketchfab / Poly Pizza) is a real force multiplier.
- A safe-mode script check, and telemetry you can turn off.

Wrong, or just not their problem:
- **Requires a GUI Blender.** Dead on arrival for a headless self-hosted server.
- **Unauthenticated, unencrypted socket**, their docs say use an SSH tunnel. Fine for a desktop
  toy; not fine as a service.
- **Downloads block Blender's main thread** — the UI freezes. A symptom of bolting onto an
  interactive app.
- **Single instance only**, across all clients.
- **No checkpoint/rollback.** Their docs say "save your work first", which means Claude cannot
  experiment.
- **No geometry validation at all.** No manifold check, no genus, no symmetry, no Hausdorff. Claude
  can look but cannot measure.
- **No UV, bake, LOD, texel-density or export-validation tools.** The whole §2–§3 pipeline is
  left to `execute_blender_code`.
- Generation is wired to four **paid cloud** APIs, so geometry leaves the machine.

**Forks** report 80–92 tools across 26 categories **[unverified]**. That's the other failure mode:
a tool per Blender operator, a surface too wide to fit in a prompt, each tool thin enough that
`execute` would have been shorter. Don't.

**`jagjerez-org/3d-mcp-server`** — OpenSCAD-backed, `create_primitive`, `create_text`. Honest and
tiny; shows how little you get from a CSG-only backend **[unverified]**.

**Others:** Revit MCP (BIM), SketchUp MCP (also port 9876, same socket pattern), UModeler PicoBerry
(generate/remesh/texture/animate, cloud). **Zoo / KittyCAD text-to-CAD** and a `cad-mcp` did not
turn up in search and I could not verify their current state **[unverified]**.

**The gap forge fills:** headless, measurable, checkpointed, with the §2 pipeline as first-class
typed tools instead of prose inside an `exec` call.

---

## 6. MCP server architecture

### 6.1 Protocol target — confirmed, not guessed

Current revision is **`2026-07-28`** (the `/specification/latest` page serves it). From the
official changelog, the things that change forge's design:

- **Protocol-level sessions are gone.** No `Mcp-Session-Id`. "Servers that need cross-call state use
  explicit, **server-minted handles passed as ordinary tool arguments**" (SEP-2567). forge's
  `session_id` handle design is now *the spec's own recommendation*.
- **MCP is stateless.** No `initialize` / `notifications/initialized`. Every request carries
  `io.modelcontextprotocol/protocolVersion` and `clientCapabilities` in `_meta` (SEP-2575).
- **`server/discover` is a MUST** — advertise supported versions, capabilities, identity.
- **All results carry `resultType`**: `"complete"` or `"input_required"` (SEP-2322).
- **Multi Round-Trip Requests (MRTR)** replace server-initiated requests. No more server→client
  `sampling/createMessage`, `elicitation/create`, `roots/list`. You return an
  `InputRequiredResult` with `inputRequests`; the client retries the original request with
  `inputResponses`.
- **Sampling, Roots and Logging are DEPRECATED** (SEP-2577), minimum 12-month window. Migration
  guidance: pass paths as tool params instead of Roots; **log to `stderr` on stdio**; integrate an
  LLM API directly instead of Sampling.
- **`structuredContent` may be any JSON value**, and `inputSchema`/`outputSchema` accept any JSON
  Schema 2020-12 keyword with `$ref` resolution (SEP-2106).
- **`CacheableResult`**: `ttlMs` + `cacheScope` are **required** on `tools/list`, `prompts/list`,
  `resources/list`, `resources/read`, `resources/templates/list` (SEP-2549).
- **`tools/list` SHOULD be in deterministic order** — explicitly to improve prompt-cache hits.
- **`subscriptions/listen`** replaces the HTTP GET endpoint and `resources/subscribe`.
  Request-scoped `notifications/progress` and `notifications/message` still flow on the **response
  stream of their own request**.
- **HTTP+SSE transport is Deprecated**; use Streamable HTTP. SSE resumability (`Last-Event-ID`) is
  gone — a broken stream means re-issue with a new request id.
- **Tasks** moved out of core into the `io.modelcontextprotocol/tasks` extension: poll `tasks/get`,
  push input with `tasks/update` (SEP-2663).
- **Image content in tool results** has been in since `2024-11-05` and is unchanged. Good.

Changelog: https://modelcontextprotocol.io/specification/2026-07-28/changelog

**SDK:** `mcp` **2.3.0** (2026-10-02, MIT) and `fastmcp` **4.2.0** (2026-11-13, Apache-2.0)
**[measured from PyPI]**. Secondary sources say SDK v2 renames `FastMCP` → `MCPServer`
(`from mcp.server.mcpserver import MCPServer`), with `mcp<2` keeping the old path, and that the SDK
answers `server/discover` automatically from registered tools **[unverified — blogs disagree on
version numbers; read `py.sdk.modelcontextprotocol.io/v2/migration` before writing the first
import]**. **Decide this on day one by reading the installed SDK, not a blog.**

**Transport:** **stdio** for v0. One client, one server, no auth surface, logs to stderr per the new
guidance. Streamable HTTP in v1 when forge needs to be shared or to serve long bakes to multiple
clients. Never HTTP+SSE.

### 6.2 Tool surface: one escape hatch vs. a wide typed API — the argument

**Pure `execute_blender_python`** (what blender-mcp mostly is):
- *For:* infinite capability, zero schema maintenance, one tool in the prompt.
- *Against:* `bpy.ops` is **context-dependent** — the active object, the selection, and the mode all
  change what an operator does, and this is empirically where the errors are. My own probe hit it:
  `bpy.ops.object.bake` failed with `RuntimeError: Object "Sun" is not a mesh` **purely because
  `light_add` had left the Sun active** **[measured]**. That's the canonical failure and it will
  happen to Claude constantly. Also: no validation, no idempotency, tracebacks instead of
  structured errors, and no way to report progress on a long bake.

**Pure wide typed API** (the 92-tool forks):
- *For:* each call validated, discoverable, structured.
- *Against:* doesn't fit in a prompt, each tool is a thin operator wrapper so you've just renamed
  `bpy.ops` with worse documentation, and the first thing a real asset needs is the combination you
  didn't expose.

**The answer is a hybrid, and the split is not 50/50:**

1. **A declarative IR covering ~90%** — `forge_apply_ir`. Claude authors a scene description; forge
   compiles it to `bpy` with correct context handling, applied in a deterministic order,
   validated before anything mutates, and idempotent by object id. This is where context bugs go to
   die, because the compiler sets the active object and mode, not Claude.
2. **~20 typed ops for the stateful, measurable, or long-running things** — remesh, uv, bake, lod,
   render, inspect, export, validate, checkpoint. These need progress reporting, structured
   results, and capability gating, which an IR blob can't express well.
3. **One `forge_exec`** — real, documented, unapologetic, and *always available*. It runs in the
   session process, gets a timeout, returns structured tracebacks, and is automatically preceded by
   an implicit checkpoint so a segfault costs one turn.

Target: **22 tools**. That fits in a prompt, and the IR means the long tail doesn't need a tool.

### 6.3 Statefulness: persistent process, forced

**`bpy` is a process-global singleton.** There is one `bpy.data`, one `bpy.context`. You cannot
hold two independent scenes in one interpreter. So:

- **One subprocess per forge session.** `python -c "import forge.worker"`, 0.55 s startup
  **[measured]**, ~300 MB RSS idle plus mesh data.
- **JSON-RPC over a UNIX domain socket** (not TCP — no network surface, no auth problem;
  blender-mcp's unauthenticated `localhost:9876` is a mistake not worth copying).
- **The MCP server process is thin** and holds no geometry — it routes, enforces budgets, and owns
  the session registry.
- **Idle reaping:** worker exits after N minutes idle; its `.blend` is on disk, so the next call
  rehydrates in 0.55 s + 13 ms **[measured]**. Sessions are durable, processes are not.
- **Crash recovery:** `bpy` *does* segfault on pathological geometry. The supervisor notices the
  dead socket, restarts the worker, loads the newest checkpoint, and returns a structured error
  saying exactly which checkpoint it recovered to and which op killed it. That error is *useful* —
  Claude learns the op is dangerous on that mesh.
- One-shot-subprocess-per-call is a legitimate fallback (0.55 s + 13 ms ≈ 0.57 s overhead per
  call), and it's what you use if the supervisor turns out to be flaky. It just loses in-memory
  caches and makes long bakes awkward.

### 6.4 Idempotency and stable naming

- Every created thing gets a **stable forge id**: `obj_hull`, `mat_hull`, `img_hull_normal`,
  `ck_0007`. Claude supplies the slug or forge derives it from the IR node name; it never changes
  under Claude.
- Ids are stored in the object's custom properties (`obj["forge_id"]`) so they survive save/load,
  renames, and Blender's own `.001` suffixing — which is the thing that breaks every naive
  name-based pipeline.
- `forge_apply_ir` is **idempotent by id**: re-applying the same IR with one changed parameter
  updates that object in place rather than creating `Cube.001`. This is what lets Claude iterate
  without re-querying the scene every turn.
- Handles returned to Claude are opaque-ish strings that are *also* readable. `obj_hull` beats
  `7f3a9c21`. Claude reasons about names.

### 6.5 Checkpoints and rollback

Save is **9 ms**, load is **13 ms**, a Suzanne-scale `.blend` is **264 KB** **[measured]**. So:

- **Auto-checkpoint before every mutating call.** Ring buffer of 50, plus unlimited **named**
  checkpoints that never get evicted.
- `forge_rollback(session, checkpoint)` reloads and returns a scene summary.
- `forge_list_checkpoints` returns id, label, timestamp, the op that caused it, and face count — so
  Claude can see the shape of its own history.
- `forge_diff(ck_a, ck_b)` returns objects added/removed/modified with per-object face-count and
  bbox deltas. **This is the tool that lets Claude verify an op did what it claimed** without
  rendering.
- Checkpoints are disk-cheap but not free: compress and cap total bytes per session, evict unnamed
  ones oldest-first.

### 6.6 Token economy

Rules, enforced in the server rather than suggested in a docstring:

- **Never return vertex/face arrays.** There is no tool that can. Geometry is reachable only via
  handles, renders, exports, and statistics.
- Every list result is **paginated**: `limit` (default 50, max 500) + opaque `cursor`, and a
  `total` so Claude knows what it's not seeing.
- Results are **structured content** against a declared `outputSchema`, with a short human-readable
  text summary alongside (the spec's backward-compat recommendation).
- **Image budget:** ≤3 blocks per result, 512² default, composites preferred.
- `forge_scene` default projection is 5 fields. Asking for everything is possible and costs.
- Numeric reports are rounded to 5 significant figures. Nobody needs `4.179742893215`.

### 6.7 Error surfaces

A `bpy` traceback is the most useful thing forge can hand back, *if it's structured*. The shape:

```json
{
  "ok": false,
  "error": {
    "kind": "bpy_operator_context",
    "message": "Object \"Sun\" is not a mesh",
    "op": "bpy.ops.object.bake",
    "python_type": "RuntimeError",
    "traceback": ["...frame...", "...frame..."],
    "state_at_failure": {
      "active_object": {"id": "light_sun", "type": "LIGHT"},
      "mode": "OBJECT",
      "selected": ["light_sun"]
    },
    "likely_cause": "bake requires an active MESH object; the active object is a LIGHT",
    "suggested_fix": "forge_op(op='set_active', target='obj_hull') then retry",
    "checkpoint": "ck_0042",
    "rolled_back": true
  }
}
```

`state_at_failure` is the part nobody else does and it's worth more than the traceback. The real
measured failure **[measured]** was exactly this class, and a bare `RuntimeError: Object "Sun" is
not a mesh` would send Claude hunting in the wrong place. Classify the top ~15 recurring `bpy`
failure modes (wrong mode, no active object, nothing selected, no UV layer, no active image node,
engine mismatch, non-manifold input to boolean, modifier on wrong object type, missing material
slot, …) with a `likely_cause` and a `suggested_fix` for each. That table *is* the product's
intelligence.

Also: **never return a partial mutation.** Every mutating tool checkpoints first and rolls back on
exception, so a failure leaves the scene exactly where it was. Claude's mental model stays valid.

### 6.8 The IR vs. raw bpy — argued, and combined

**Raw `bpy` only** fails on context (measured, §6.2), on the lack of validation-before-mutation, on
non-idempotency (`Cube.001`), and on verbosity — a ten-part asset is 300 lines of operator calls,
each of which can silently no-op.

**IR only** fails on reach. There will always be a Blender feature the schema doesn't cover, and a
hard "no" there makes forge useless at the exact moment it got interesting.

**Combined, with a clean seam:**

- The **IR is the default and covers the asset graph**: objects, primitives, transforms, modifier
  stacks, booleans, materials, UV strategy, LODs, export targets. Declarative, diffable,
  idempotent, validated against a JSON Schema *before* a single `bpy` call. Claude can also *read*
  the IR back for any session, which gives it a token-cheap, complete picture of the scene — far
  better than re-querying.
- **Typed ops** handle the stateful/measurable/slow (§6.2 item 2).
- **`forge_exec`** handles everything else and can **write back into the IR** by setting
  `obj["forge_id"]`, so escape-hatch results become first-class scene members rather than orphans.

The 90/10 split is the point: the IR absorbs the repetitive bulk where mistakes are mechanical, and
the escape hatch absorbs the novel work where Claude is actually useful.

### 6.9 Long operations

A 4096² four-map bake is tens of seconds; a 2 M-face QuadriFlow is minutes. Options under
`2026-07-28`:

1. **`notifications/progress` on the request's own response stream** — simple, works on stdio, and
   is what v0 uses. Emit on every bake pass / remesh stage with a `progressToken`.
2. **The `io.modelcontextprotocol/tasks` extension** — return a task handle, Claude polls
   `tasks/get`, pushes input with `tasks/update`. Right answer for multi-minute work, and it's an
   *extension* now, so it's opt-in and must be negotiated. v1.
3. **forge-native async**: `forge_bake(..., async_=True)` returns a `job_id` immediately and
   `forge_job(job_id)` polls. Works regardless of client extension support. **Do this one too** —
   it's ten lines and it never depends on the client.

Ship (1) + (3) in v0; add (2) in v1 when the SDK's extension support is confirmed.

### 6.10 Resources, prompts, capabilities

- **Resources:** expose rendered images, baked textures, exported files, and checkpoint `.blend`s as
  resources with `file://` or `forge://` URIs, so Claude can hand a URI to the user instead of
  pushing bytes through context. Remember `CacheableResult` — `resources/list` now needs `ttlMs` and
  `cacheScope`.
- **Prompts:** three worth shipping — `forge:hard_surface_prop`, `forge:organic_prop`,
  `forge:game_ready_checklist`. Each a short workflow that names the tool order. This is where the
  §2 pipeline gets taught rather than documented.
- **`forge_capabilities()`** — not optional. Reports Blender version, Python version, GPU presence
  and Cycles device list, which engines are usable, whether Draco/meshopt exist, whether ML backends
  are installed, and the *measured* render cost per view. Every capability claim in §3.3 and §8 is
  something Claude should be able to ask about rather than assume.
- **Tool annotations:** mark `readOnlyHint` on `forge_scene` / `forge_inspect` / `forge_render` /
  `forge_capabilities`, `destructiveHint` on `forge_rollback` and `forge_op`, `idempotentHint` on
  `forge_apply_ir`. Hosts use these to decide what to auto-approve. The spec also warns that
  annotations from an untrusted server should be treated as untrusted — forge is self-hosted, so
  this is fine, and it's worth saying so in the README.

---

## 7. Export and validation

### 7.1 Formats — all verified working from the bpy wheel **[measured]**

| format | operator | result | notes |
|---|---|---|---|
| **GLB** | `export_scene.gltf` | 520 KB, 18 ms | **no Draco, no meshopt** — bridge `.so`s absent **[measured]** |
| **USDC** | `wm.usd_export` | 165 KB, 36 ms | works |
| **USDZ** | `wm.usd_export` | 166 KB, 6 ms | works |
| **FBX** | `export_scene.fbx` | 234 KB, 18 ms | works |
| **OBJ** | `wm.obj_export` | 521 KB, 14 ms | works |
| **ABC** | `wm.alembic_export` | 396 KB, 5 ms | works |
| **STL** | `wm.stl_export` | 371 KB, 1 ms | works |

Export is free. Export *correctly* is not.

### 7.2 glTF 2.0 extensions that matter

- `KHR_materials_emissive_strength`, `KHR_materials_clearcoat`, `KHR_materials_transmission`,
  `KHR_materials_volume`, `KHR_materials_ior`, `KHR_materials_sheen`, `KHR_materials_specular`,
  `KHR_materials_unlit`, `KHR_texture_transform`, `KHR_materials_variants`
- `KHR_draco_mesh_compression` and `EXT_meshopt_compression` — **unavailable in-process**
  **[measured]**. Post-process with `gltfpack` or `gltf-transform` on Node v22.23.4 (present
  **[measured]**).
- `KHR_texture_basisu` (KTX2/Basis) — needs `toktx`/`basisu`; also an external step.
- **No UDIM support at all** (§3.4). **No hair primitive** (§2.6).

### 7.3 Axis and unit conventions — where things break silently

| target | up | forward | units | handedness |
|---|---|---|---|---|
| **Blender / forge canonical** | **+Z** | **+Y** | metres | right |
| **glTF 2.0** | **+Y** | **+Z** | metres | right |
| **USD** | configurable (`upAxis`), default **+Y** | — | `metersPerUnit` | right |
| **Unity** | +Y | +Z | metres | **left** |
| **Unreal** | +Z | **+X** | **centimetres** | left |
| **FBX** | per-file axis+unit metadata | — | per-file | — |

Blender's glTF exporter does the Z-up→Y-up rotation itself. The silent killers:

1. **Unreal's centimetres.** A 2 m crate arrives 2 cm tall. `forge_export(target_engine="unreal")`
   must bake the ×100 scale and *say so in the report*.
2. **Unreal's +X forward.** A character faces sideways. −90° Z for character-like assets.
3. **Unity's left-handed basis** flips winding; glTF import handles it, FBX import is where it goes
   wrong.
4. **Unapplied transforms.** Object-level scale survives into glTF as a node transform; non-uniform
   scale then corrupts normals downstream. **Always apply transforms on export** and report that
   you did.
5. **Normal map green channel** — OpenGL +Y vs DirectX −Y (§2.4). Silent, and it looks like bad
   lighting rather than a bad file.
6. **FBX is ASCII-or-binary, version-dependent, and lossy.** Use it only when the target demands it.

### 7.4 Validators

- **`gltf-validator`** (Khronos, `KhronosGroup/glTF-Validator`): CLI `gltf_validator [opts] <in>`,
  validates against glTF 2.0, emits a JSON report of issues plus asset stats. Installed cleanly
  here via `npm install gltf-validator` **[measured]**. npm latest is `2.0.0-dev.3.10`
  **[unverified]**. **Run it on every glTF/GLB export, always.**
- **`usdchecker`** — ships with OpenUSD. `pxr` is **not** importable from the bpy wheel
  **[measured]**, so USD validation needs `pip install usd-core` as a separate dependency (and it's
  only needed if USD is a target).
- **Khronos 3D Commerce Asset Validator** (`@mikefesta/3dc-validator`) — checks a *product* profile:
  triangle budgets, texture sizes, dimensions, UV ranges. Worth wiring as a second, stricter
  profile.
- **Khronos glTF Sample Viewer** — the reference render. Useful as a visual cross-check that your
  material survived the trip; needs a browser, so it's a human step, not a tool.
- **forge's own validator** — the §5.3 numeric report *is* a validator, and it catches things no
  format validator does: a non-manifold shell, a 0.4 px/m texel density, a 40%-wasted atlas, a
  mirrored half that's 3 mm off. `forge_validate(profile="game_ready")` runs format validation +
  the numeric suite + the convention checks in one call and returns a pass/warn/fail list.

---

## 8. Install and environment reality check

### 8.1 What this container is **[measured]**

```
Ubuntu 24.04.5 LTS, Linux 6.18.44-fc-v470, x86_64
Python 3.13.18, pip 24.0
4 cores, 15 GB RAM, 30 GB free on /
Node v22.23.4, npm, npx present
libGL.so.1 present; libOpenGL.so.0 ABSENT (needed by pymeshlab)
No Blender. No GPU: Cycles CUDA=[] OPTIX=[], "CUEW initialization failed"
gpu.platform.backend_type_get() == 'VULKAN'  (software / lavapipe)
```

### 8.2 Disk footprint **[measured]**

| component | size |
|---|---|
| `bpy` 5.3.0 | **927 MB** |
| `pymeshlab` 2025.7 | **280 MB** |
| `trimesh` + deps | ~5 MB |
| `manifold3d` | 3.3 MB |
| `xatlas` | 568 KB |
| numpy, pillow, requests, misc | ~120 MB |
| **venv total, all of the above** | **1.7 GB** |
| `node_modules` for gltf-validator | ~15 MB |
| **forge total** | **≈1.8 GB** |

Comfortable in 30 GB. Add ~15–30 GB if ML weights ever land (TRELLIS.2-4B is multi-GB), which they
won't here without a GPU.

### 8.3 Feature degradation without a GPU

| feature | with GPU | **here, no GPU** |
|---|---|---|
| Workbench render | instant | **0.15 s @512², 0.78 s @1024², 2.46 s @2048²** — fine **[measured]** |
| Cycles render | 10–50× faster via OptiX/HIP | **1.95 s @512²/32spp, 7.99 s @1024²/32spp** — usable **[measured]** |
| **EEVEE** | real-time | **24.7 s @512² — unusable. Disable it.** **[measured]** |
| Bake (Cycles) | seconds at 4K | **1.90 s for 2048² normal @8spp** — usable **[measured]** |
| Viewport GPU subdiv | yes | CPU subdiv only; still 44 ms for L3 **[measured]** |
| ML generation | TRELLIS.2 etc. | **impossible.** ≥24 GB VRAM required |
| QuadriFlow / voxel remesh | CPU anyway | **unaffected** **[measured]** |
| manifold3d / trimesh / xatlas / pymeshlab | CPU anyway | **unaffected** **[measured]** |

**Summary: forge's entire procedural pipeline works at acceptable speed on this CPU-only box.** The
only hard losses are EEVEE and ML generation. That is a much better position than expected going
in, and it's the reason the procedural-first verdict holds.

### 8.4 Install script — actually runs here

```bash
#!/usr/bin/env bash
# forge/install.sh  —  verified on Ubuntu 24.04.5 / Python 3.13.18 / no GPU
set -euo pipefail

FORGE_ROOT="${FORGE_ROOT:-/opt/forge}"
VENV="$FORGE_ROOT/venv"

# --- system deps -------------------------------------------------------------
# libopengl0 + libegl1 are NOT optional: without libOpenGL.so.0 pymeshlab loads
# 235 filters instead of 281 and quadric decimation silently does not exist.
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3.13 python3.13-venv python3-pip \
  libopengl0 libegl1 libxkbcommon0 libxcb-cursor0 \
  libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 \
  libsm6 libice6 libgomp1 \
  ca-certificates curl git

# --- python env --------------------------------------------------------------
# bpy pins requires_python == 3.13.*  — the interpreter version is load-bearing.
python3.13 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel

# Blender as a library. 427 MB download, 927 MB installed.
"$VENV/bin/pip" install "bpy==5.3.0"

# geometry + analysis
"$VENV/bin/pip" install \
  "trimesh[easy]==5.2.1" \
  "manifold3d==3.5.5" \
  "xatlas==0.0.11" \
  "pymeshlab==2025.7.post1" \
  "mapbox-earcut==2.1.0" \
  "numpy==2.5.4" \
  "pillow" \
  "scipy"

# MCP server. Read the installed SDK's own migration notes before writing
# imports: mcp>=2 reportedly renames FastMCP -> MCPServer.
"$VENV/bin/pip" install "mcp==2.3.0" "pydantic>=2"

# optional: USD validation (bpy ships USD export but NOT the pxr python module)
"$VENV/bin/pip" install "usd-core" || echo "usd-core unavailable; skipping usdchecker"

# optional: CAD backend (phase 2)
# "$VENV/bin/pip" install "build123d==0.13.0"

# --- node tooling: glTF validation + GLB compression -------------------------
# bpy's wheel has no Draco/meshopt bridge libs, so compression happens here.
mkdir -p "$FORGE_ROOT/node"
cd "$FORGE_ROOT/node"
npm init -y >/dev/null
npm install gltf-validator @gltf-transform/cli

# --- smoke test --------------------------------------------------------------
"$VENV/bin/python" - <<'PY'
import bpy, trimesh, manifold3d, xatlas, pymeshlab, sys
print("python     ", sys.version.split()[0])
print("blender    ", bpy.app.version_string, bpy.app.build_date.decode())
print("engines    ", [i.identifier for i in
      bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items])
print("pymeshlab  ", pymeshlab.__version__, len(pymeshlab.filter_list()), "filters")
assert len(pymeshlab.filter_list()) > 270, "libOpenGL.so.0 missing - install libopengl0"
cy = bpy.context.preferences.addons.get('cycles')
print("gpu        ", [d.name for d in
      cy.preferences.get_devices_for_type('OPTIX')] if cy else "n/a")
print("OK")
PY
echo "forge installed at $FORGE_ROOT"
```

Expected smoke-test output here **[measured]**:
```
python      3.13.18
blender     5.3.0 2026-11-10
engines     ['BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES']
pymeshlab   2025.7.post1 281 filters
gpu         []
OK
```

---
## 9. Proposed tool surface

22 tools. Declared in this order, which is also the order `tools/list` returns them (the spec SHOULD
for deterministic ordering, for prompt-cache hits). Types are the actual annotations; every return
type is a Pydantic model so `outputSchema` is generated rather than hand-written.

Conventions used throughout:
- `session: SessionId` — an opaque server-minted handle, passed as an ordinary argument (SEP-2567).
- `ObjectId` / `MaterialId` / `CheckpointId` / `ImageId` — readable stable slugs (`obj_hull`).
- Every mutating tool auto-checkpoints first and rolls back fully on exception.
- Every result carries `ok`, and on failure the §6.7 structured error.
- No tool returns vertex or face arrays. Ever.

### 9.0 Shared types

```python
from typing import Literal, Annotated, Any
from pydantic import BaseModel, Field

SessionId    = Annotated[str, Field(pattern=r"^fs_[a-z0-9]{10}$")]
ObjectId     = Annotated[str, Field(pattern=r"^obj_[a-z0-9_]{1,48}$")]
MaterialId   = Annotated[str, Field(pattern=r"^mat_[a-z0-9_]{1,48}$")]
CheckpointId = Annotated[str, Field(pattern=r"^ck_[0-9]{4,6}$|^ck_[a-z0-9_]{1,48}$")]
JobId        = Annotated[str, Field(pattern=r"^job_[a-z0-9]{10}$")]

UpAxis       = Literal["Z", "Y"]
Units        = Literal["m", "cm", "mm", "in", "ft"]
TargetEngine = Literal["generic", "unity", "unreal", "godot", "web", "film"]

class ForgeError(BaseModel):
    kind: str                      # "bpy_operator_context" | "validation" | "capability" | ...
    message: str
    op: str | None = None
    python_type: str | None = None
    traceback: list[str] = []
    state_at_failure: dict[str, Any] = {}   # active object, mode, selection, engine
    likely_cause: str | None = None         # from the classified-failure table
    suggested_fix: str | None = None        # a concrete forge_* call
    checkpoint: CheckpointId | None = None
    rolled_back: bool = True

class MeshStats(BaseModel):
    """The numeric substitute for eyes. ~400 tokens. Cheap: ~8 ms on a 5k mesh."""
    faces: int
    verts: int
    edges: int
    tris: int
    quads: int
    ngons: int
    quad_purity: float                      # quads / faces
    euler: int
    genus: float | None                     # None when not closed
    components: int
    watertight: bool
    winding_consistent: bool
    volume: float | None                    # signed; negative => inverted normals
    area: float
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    bbox_size: tuple[float, float, float]
    non_manifold_edges: int
    non_manifold_verts: int
    degenerate_faces: int
    zero_length_edges: int
    loose_verts: int
    symmetry_axis: Literal["X", "Y", "Z"] | None = None
    symmetry_rms: float | None = None
    symmetry_max: float | None = None
    has_uvs: bool = False
    uv_overlap_area: float | None = None
    uv_out_of_bounds: int | None = None
    uv_islands: int | None = None
    uv_packing_efficiency: float | None = None
    texel_density_px_per_m: dict[str, float] | None = None   # per material: min/mean/max
```

### 9.1 Session and state

```python
def forge_open(
    name: str,
    units: Units = "m",
    up_axis: UpAxis = "Z",
    from_checkpoint: str | None = None,
) -> OpenResult:
    """Start or resume a forge session and return its handle.

    Spawns a dedicated worker process holding one Blender state. `bpy` is a
    process-global singleton, so there is exactly one scene per session and one
    process per session; this is a library constraint, not a design choice.

    Startup measured at 0.55 s (import bpy) + 0.04 s (factory reset). Resuming
    from a checkpoint adds ~0.013 s.

    `up_axis` and `units` set the session's canonical frame. forge works in
    Z-up metres internally regardless, and converts on export; this argument
    only controls what the numbers in reports mean to you.

    `from_checkpoint` accepts a path to a .blend saved by a previous session,
    which is how work survives a container restart.

    Returns: session handle, resolved capabilities (GPU presence, usable render
    engines with their measured costs, available export formats, whether Draco
    and meshopt exist, which ML backends are installed), and an empty scene
    summary.
    """

def forge_capabilities(session: SessionId | None = None) -> Capabilities:
    """Report what this install can actually do, with measured costs.

    Call this before assuming anything. It is cheap, read-only, and it is the
    difference between Claude planning a workflow that runs and one that times
    out. Reports: Blender version, Python version, GPU presence and Cycles
    device list, which render engines are usable AND their measured cost per
    view at 512/1024/2048, bake throughput, whether libOpenGL.so.0 is present
    (pymeshlab filter count), Draco/meshopt availability, installed ML backends,
    and the per-session memory headroom.

    On a CPU-only box this will tell you EEVEE is disabled. Believe it:
    measured 24.7 s for a 512x512 frame versus 1.95 s for Cycles.
    """

def forge_checkpoint(
    session: SessionId,
    label: str | None = None,
    note: str | None = None,
) -> CheckpointResult:
    """Save a named, durable snapshot of the whole session.

    Measured cost: 9 ms to save, 264 KB for a Suzanne-scale scene. Effectively
    free, so checkpoint liberally. Unnamed auto-checkpoints are taken before
    every mutating call and kept in a 50-deep ring buffer; labelled checkpoints
    are never evicted.

    Returns: checkpoint id, byte size, and the MeshStats-level scene totals at
    that moment, so the checkpoint list doubles as a history of what changed.
    """

def forge_rollback(session: SessionId, checkpoint: CheckpointId) -> SceneView:
    """Restore the session to a checkpoint. Destructive and deliberate.

    Measured cost: 13 ms. This is what makes experimentation free — try the
    aggressive boolean, inspect it, roll back if the genus went wrong.

    Returns the scene view after restore, so one call both reverts and re-orients.
    """

def forge_list_checkpoints(
    session: SessionId, limit: int = 50, cursor: str | None = None
) -> CheckpointList:
    """List checkpoints newest-first: id, label, timestamp, the op that caused
    it, face-count total, byte size, and whether it is pinned.

    Paginated. This is Claude's view of its own history; keep it readable.
    """

def forge_diff(
    session: SessionId,
    a: CheckpointId,
    b: CheckpointId | Literal["now"] = "now",
) -> DiffReport:
    """Compare two checkpoints structurally.

    Returns objects added / removed / modified, and for each modified object the
    deltas: face count, vert count, bbox size, volume, watertightness,
    material assignment, UV presence.

    Use this instead of a render to answer "did that op do what it said". It is
    free and exact, where a 512px thumbnail is neither.
    """
```

### 9.2 Authoring

```python
def forge_apply_ir(
    session: SessionId,
    ir: dict,                                  # validated against the forge IR schema
    mode: Literal["merge", "replace", "dry_run"] = "merge",
    strict: bool = True,
) -> BuildReport:
    """Compile a declarative asset description into the scene. THE main authoring tool.

    This exists because `bpy.ops` is context-dependent and that is where almost
    every model-written error comes from — the active object, the selection and
    the mode silently change what an operator does. The IR compiler owns context
    handling: it sets the active object and mode around every operation, applies
    nodes in dependency order, and validates the entire document against a JSON
    Schema BEFORE mutating anything.

    Idempotent by object id: re-applying the same IR with one changed parameter
    updates that object in place. It does not create `Cube.001`. This is what
    lets you iterate across turns without re-querying the scene.

    mode="merge"    — upsert the nodes present, leave everything else alone (default)
    mode="replace"  — the IR becomes the whole scene; anything absent is deleted
    mode="dry_run"  — validate and report the plan, change nothing

    strict=True rejects unknown keys instead of ignoring them, so a typo is an
    error rather than a silently-missing bevel.

    Returns: per-node created/updated/skipped/failed, the resolved object ids,
    MeshStats for every affected mesh, total wall time, and a warning list
    (e.g. "node 'hull': subsurf level 4 on 500k faces will exceed the 4M face
    budget"). On any failure with strict=True, nothing is applied.
    """

def forge_op(
    session: SessionId,
    op: str,
    target: ObjectId | list[ObjectId] | None = None,
    params: dict | None = None,
) -> OpResult:
    """Run one typed, context-safe operation. The imperative counterpart to the IR.

    For the stateful things an IR document expresses badly: selection changes,
    applying a specific modifier, joining or separating objects, setting origins,
    applying transforms, setting the active object, shade smooth/flat with an
    angle, recalculating normals, separating by loose parts or by material.

    `op` is one of a closed enumeration (~40 names), not a Python expression.
    Each op declares its own params schema, which is discoverable via
    forge_op(session, op="help", params={"of": "separate_parts"}).

    forge handles the context dance for you: it sets the active object, enters
    the required mode, performs the op, and restores the prior mode. The measured
    failure this prevents: `bpy.ops.object.bake` raising
    `RuntimeError: Object "Sun" is not a mesh` purely because adding a light had
    left the light active.

    Returns: affected object ids and their MeshStats after the op.
    """

def forge_exec(
    session: SessionId,
    code: str,
    timeout_s: int = 120,
    capture: Literal["repr", "json", "none"] = "repr",
) -> ExecResult:
    """Run arbitrary Python in the session's Blender process. The escape hatch.

    This is not a failure mode — it is a designed, documented, always-available
    part of the surface. Use it for anything the IR and the typed ops do not
    cover: a bespoke bmesh walk, a geometry-nodes graph forge does not ship, a
    one-off numeric probe.

    An automatic checkpoint is taken immediately before execution, so a segfault
    costs one turn and nothing else. `bpy` does crash on pathological geometry;
    the supervisor restarts the worker, reloads that checkpoint, and returns a
    structured error naming the op that killed it.

    `bpy`, `bmesh`, `mathutils`, `numpy`, `trimesh`, `manifold3d`, `xatlas` and
    `pymeshlab` are pre-imported. A module-level `forge` object exposes the id
    registry: `forge.obj("hull")` resolves a forge id to a bpy object, and
    `forge.register(bpy_object, "hull")` adopts a newly created object into the
    IR so escape-hatch results are first-class rather than orphans.

    stdout and stderr are captured and truncated to 8 KB each. Tracebacks come
    back structured (see ForgeError), including the scene state at the moment of
    failure — the active object, the mode, the selection, the render engine —
    which is usually more diagnostic than the traceback itself.

    Returns: value (per `capture`), stdout, stderr, wall time, the checkpoint
    taken beforehand, and MeshStats for every object whose geometry changed.
    """
```

### 9.3 Query

```python
def forge_scene(
    session: SessionId,
    root: ObjectId | None = None,
    fields: list[str] = ["id", "type", "parent", "face_count", "bbox_size"],
    depth: int = 2,
    query: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> SceneView:
    """A token-cheap projection of the scene graph. Never a dump.

    One line per object, carrying only the `fields` asked for. The default
    projection is five fields and costs roughly 6 tokens per object, so 200
    objects is about 1,200 tokens. Available fields include: id, name, type,
    parent, children_count, collection, face_count, vert_count, tri_count,
    bbox_min, bbox_max, bbox_size, world_location, world_rotation, world_scale,
    materials, modifiers, uv_layers, vertex_groups, shape_keys, visible,
    forge_ir_node.

    `query` is a small filter expression: "type=MESH and face_count>5000",
    "materials contains mat_hull", "name~^bolt_".

    Paginated with an opaque cursor; `total` is always returned so you know what
    you are not seeing.

    Never returns geometry. Mesh data is reachable only through handles, renders,
    exports and forge_inspect.
    """

def forge_inspect(
    session: SessionId,
    target: ObjectId | list[ObjectId],
    checks: list[str] = ["topology", "manifold", "bounds"],
    reference: ObjectId | str | None = None,
    symmetry_axis: UpAxis | Literal["X"] | None = None,
) -> InspectReport:
    """Measure a mesh instead of looking at it. Run this constantly.

    check groups:
      "topology"  — face/vert/edge counts, tri/quad/ngon split, quad purity,
                    Euler characteristic, genus, component count
      "manifold"  — watertight, winding consistency, non-manifold edge and vert
                    counts, degenerate faces, zero-length edges, loose verts
      "bounds"    — bbox, dimensions, volume (signed: negative means inverted
                    normals), surface area, centre of mass
      "symmetry"  — mirror across `symmetry_axis`, nearest-neighbour distance,
                    RMS and max error
      "uv"        — island count, overlap area, out-of-bounds islands, packing
                    efficiency, per-material texel density in px/m (min/mean/max
                    across islands, so inconsistency is visible)
      "curvature" — mean/Gaussian curvature histogram via pymeshlab
      "silhouette"— Workbench black-on-white at 6 fixed angles, reduced to Hu
                    moments; with `reference`, an IoU per angle
      "deviation" — Hausdorff and Chamfer distance against `reference`
                    (measured: a 500-sample signed-distance pass costs 0.1 s)

    Measured: the topology + manifold + bounds set costs about 8 ms on a 5k mesh.

    This catches inverted normals, holes, floaters, failed booleans, off-centre
    mirrors and bad texel density — everything a 512px thumbnail hides. Renders
    catch what this cannot: proportion, form, and whether it reads correctly.
    Use both; reach for this one first because it is 1000x cheaper.
    """
```

### 9.4 Vision

```python
def forge_render(
    session: SessionId,
    view: Literal["quad_ortho", "turntable", "persp", "front", "side", "top",
                  "wireframe", "matcap", "silhouette", "normals", "uv_checker"] = "quad_ortho",
    objects: list[ObjectId] | None = None,
    shading: Literal["solid", "material", "rendered", "wire"] = "solid",
    engine: Literal["auto", "workbench", "cycles", "eevee"] = "auto",
    resolution: int = 512,
    samples: int = 32,
    frames: int = 8,
    overlays: list[Literal["wireframe", "normals", "uv_seams", "sharp_edges",
                           "statistics", "origin", "bbox"]] = [],
    composite: bool = True,
) -> RenderResult:
    """Render the scene and return it as image content blocks. This is the eyes.

    engine="auto" picks per the measured costs on this machine. On the CPU-only
    reference box that means Workbench for structural views (0.15 s at 512,
    0.78 s at 1024, 2.46 s at 2048) and Cycles for `shading="rendered"` (1.95 s
    at 512/32spp, 7.99 s at 1024/32spp). It will NOT pick EEVEE without a real
    GPU: measured 24.7 s for a single 512x512 frame on software Vulkan, which is
    12x slower than Cycles for the same image. Passing engine="eevee" without a
    GPU returns a capability error rather than hanging.

    composite=True packs multi-view output into one image (2x2 for quad_ortho,
    a contact sheet for turntable), because one image block costs far less
    context than four. At most 3 image blocks are ever returned; beyond that you
    get a contact sheet plus resource URIs.

    A full look-at-it pass — quad_ortho plus wireframe plus one Cycles shaded —
    is about 9 s and 3 images on the reference box.

    Every render also returns the numeric block: bbox, face count, and the
    forge_inspect summary, so you can keep reasoning without looking again.

    Returns: ImageContent blocks, plus per-view metadata (camera transform,
    engine actually used, wall time, resource URI for the full-resolution file).
    """

def forge_uv_preview(
    session: SessionId,
    target: ObjectId,
    resolution: int = 1024,
    show: list[Literal["islands", "seams", "overlap", "density_heatmap",
                       "checker"]] = ["islands", "overlap"],
) -> RenderResult:
    """Render the UV layout as an image, plus the UV numbers.

    "overlap" shades overlapping faces red; "density_heatmap" colours islands by
    texel density so inconsistent scaling is visible at a glance; "checker"
    applies a UV grid to the 3D mesh and renders it, which is the only way to
    see stretching.

    Costs under 0.1 s for the layout image. Returns the UV portion of MeshStats
    alongside: island count, overlap area, out-of-bounds count, packing
    efficiency, texel density min/mean/max.
    """

def forge_compare(
    session: SessionId,
    target: ObjectId,
    reference: ObjectId | str,
    metrics: list[Literal["hausdorff", "chamfer", "volume_ratio", "silhouette_iou",
                          "curvature_emd", "bbox_ratio"]] = ["hausdorff", "silhouette_iou"],
    samples: int = 5000,
) -> CompareReport:
    """Score a mesh against a reference, numerically.

    `reference` is another object in the session or a path to a mesh file. This
    is how you verify a decimation, an LOD, a remesh or a generated mesh against
    the thing it is supposed to approximate, without a human judging thumbnails.

    "hausdorff"      — max and 95th-percentile deviation, both directions
    "chamfer"        — symmetric mean distance
    "silhouette_iou" — per-angle IoU of black-on-white Workbench silhouettes
    "curvature_emd"  — earth-mover distance between curvature histograms, which
                       is the one that notices lost surface detail

    Measured: a 500-point signed-distance pass is 0.1 s; 5000 samples is a few
    hundred ms. Cheap enough to gate every LOD step.
    """
```

### 9.5 Topology and detail

```python
def forge_remesh(
    session: SessionId,
    target: ObjectId,
    method: Literal["quadriflow", "voxel", "sharp", "blocks", "isotropic_tri",
                    "manifold_repair", "learned"] = "quadriflow",
    target_faces: int | None = None,
    voxel_size: float | None = None,
    preserve_sharp: bool = True,
    preserve_boundary: bool = True,
    seed: int = 0,
    in_place: bool = False,
) -> RemeshResult:
    """Rebuild topology. The quality gate of the whole pipeline.

    method="quadriflow"  — quads with usable edge flow. Measured: 153k faces
                           down to 7,514 in 6.63 s, and the result was Euler 2,
                           genus 0, zero non-manifold edges, zero degenerate
                           faces. `seed` is recorded in the checkpoint so a
                           rerun reproduces.
    method="voxel"       — OpenVDB unify. Measured: 503k to 153,390 faces in
                           1.65 s. Destroys edge flow; use it to launder a
                           boolean result before quadriflow.
    method="sharp"       — Blender RemeshModifier SHARP; keeps creases better
                           than voxel.
    method="isotropic_tri" — pymeshlab even-density triangles. Right before a
                           bake, wrong before a game export.
    method="manifold_repair" — route through manifold3d to force a
                           manifold-by-construction shell. Use after any
                           dual-contouring or ML-generated input.
    method="learned"     — reserved for native quad-mesh models (QuadGPT,
                           Mesh-Pro, QuadLink). Currently raises
                           NotImplementedError: no permissively-licensed weights
                           with a working inference path were found as of
                           2026-11. QuadriFlow is the baseline to beat.

    IMPORTANT: quadriflow approximates `target_faces`. Asking for 8,000 gave
    7,514. The result always reports the ACTUAL count; never assume you got what
    you asked for.

    in_place=False keeps the original as `{target}_high` so it can be the bake
    source. That is almost always what you want.

    Returns: new object id, MeshStats before and after, and the deviation from
    the original (Hausdorff) so you can see what the remesh cost you.
    """

def forge_detail(
    session: SessionId,
    target: ObjectId,
    recipe: dict,
    subdivide_to: int | None = None,
    max_faces: int = 4_000_000,
) -> DetailResult:
    """Add procedural surface detail. The thing that replaces hand sculpting.

    A recipe is a layer list, each layer a generator plus a mask:

      {"layers": [
        {"gen": "noise_displace", "scale": 18.0, "amount": 0.004, "mask": "curvature_convex"},
        {"gen": "edge_damage",    "width": 0.002, "jitter": 0.6,  "mask": "sharp_edges>30deg"},
        {"gen": "panel_lines",    "spacing": 0.08, "depth": 0.0015, "axis": "Z"},
        {"gen": "rivets",         "density": 40.0, "radius": 0.003, "mask": "panel_border"},
        {"gen": "erosion",        "iterations": 3, "mask": "ao_cavity"}
      ]}

    Masks are the computable ones from the numeric suite: curvature_convex,
    curvature_concave, ao_cavity, position_gradient, sharp_edges>Ndeg,
    panel_border, vertex_group:NAME. They are the same masks the material layers
    use, deliberately, so geometry wear and texture wear agree.

    `subdivide_to` Catmull-Clarks the mesh first — measured 44 ms for L3 (31k
    faces) and 249 ms for a further L2 (504k faces), so density is cheap. The
    `max_faces` guard exists because memory, not time, is the limit: budget
    roughly 1 GB per 4M faces.

    Generators are implemented as parameterised geometry-node groups shipped in
    forge_assets.blend, not as graphs assembled per call. Wiring 80 nodes from a
    tool call is a trap; setting 6 parameters on a known-good group is not.

    Returns: new object id (the high-poly), MeshStats, per-layer wall time, and
    peak memory.
    """

def forge_lod(
    session: SessionId,
    target: ObjectId,
    ratios: list[float] = [1.0, 0.5, 0.25, 0.1],
    method: Literal["collapse", "planar", "quadric", "unsubdivide"] = "collapse",
    preserve_boundary: bool = True,
    preserve_uvs: bool = True,
    verify: bool = True,
) -> LODSet:
    """Build an LOD chain and verify each level against LOD0.

    method="quadric" routes to pymeshlab's quadric edge collapse, which is both
    faster and better-weighted at aggressive ratios — measured 29 ms down to 500
    faces. "collapse" is Blender's DECIMATE. "planar" is for hard surface with
    large flat regions. "unsubdivide" only works on a subdivision-derived cage.

    verify=True runs forge_compare per level and fails the call if silhouette IoU
    drops below 0.97 or Hausdorff exceeds 1% of the bounding sphere, which is
    what stops a plausible-looking LOD3 from shipping.

    Returns: one object id per level with its MeshStats and its deviation
    metrics, plus suggested screen-coverage switch distances.
    """
```

### 9.6 UV and materials

```python
def forge_uv(
    session: SessionId,
    target: ObjectId | list[ObjectId],
    method: Literal["xatlas", "smart_project", "cube", "cylinder", "sphere",
                    "follow_active_quads", "keep"] = "xatlas",
    texture_resolution: int = 2048,
    target_density_px_per_m: float | None = None,
    margin: float = 0.01,
    max_angle_deg: float = 66.0,
    pack_together: bool = False,
    udim: bool = False,
    repack_with_blender: bool = False,
) -> UVReport:
    """Unwrap and pack. Default to xatlas, and the reason is determinism, not quality.

    method="xatlas" (0.0.11, MIT) charts and packs in one deterministic call.
    Measured: 0.32 s for a 2,562-vertex icosphere, producing 2,794 UV vertices —
    note the count rises because seams split vertices; forge carries the mapping
    back so vertex attributes survive.

    method="smart_project" is Blender's, and it is fast (18 ms on 7.5k faces) but
    reported to give differing island counts across runs and versions. For a
    throwaway bake target that is fine. For a shipping asset it is not: you
    iterate, and an unwrap that shifts under you invalidates every map baked on
    the previous turn.

    `target_density_px_per_m` scales islands to a uniform texel density instead
    of maximising atlas fill — the right default for anything that sits next to
    other assets. Suggested targets: 512 px/m environment, 1024 px/m hero prop,
    2048 px/m first-person detail.

    pack_together=True packs several objects into one atlas, which is how you get
    one material for a multi-part asset.

    udim=False is the default and should stay that way for game targets: glTF
    has no UDIM support whatsoever. Passing udim=True with a glTF export target
    produces a loud warning, not a silent flatten.

    repack_with_blender=True runs uv.pack_islands after xatlas when you want
    Blender's packer and margin behaviour over xatlas's.

    Returns: island count, overlap area, out-of-bounds count, packing efficiency,
    per-island and per-material texel density (min/mean/max), and the UV preview
    image URI.
    """

def forge_material(
    session: SessionId,
    target: ObjectId | list[ObjectId],
    spec: dict,
    material_id: MaterialId | None = None,
    slot: int = 0,
) -> MaterialResult:
    """Author a PBR material from a declarative spec. No node names in the API.

    spec is the material IR (see section 10):

      {"base": {"color": [0.18,0.19,0.21], "roughness": 0.42, "metallic": 1.0},
       "layers": [
         {"type": "edge_wear", "mask": "curvature_convex", "amount": 0.35,
          "color": [0.55,0.55,0.58], "roughness": 0.25},
         {"type": "grime",     "mask": "ao_cavity", "amount": 0.5,
          "color": [0.08,0.07,0.06], "roughness": 0.8},
         {"type": "noise_rough", "scale": 12.0, "amount": 0.15}
       ],
       "normal": {"source": "baked", "strength": 1.0}}

    This compiles to a shader node graph deterministically, using parameterised
    node groups from forge_assets.blend. The masks are the same ones forge_detail
    uses, on purpose: geometry wear and texture wear should agree, and they only
    do if they share a definition.

    Do not write `Material.use_nodes` anywhere near this: it is deprecated in
    5.3 and scheduled for removal in Blender 6.0.

    Returns: material id, the resolved node-group parameter set, and the list of
    texture slots the subsequent bake will need to fill.
    """

def forge_bake(
    session: SessionId,
    low: ObjectId,
    high: ObjectId | None = None,
    passes: list[Literal["normal", "ao", "curvature", "position", "diffuse",
                         "roughness", "metallic", "emit", "combined",
                         "thickness", "id"]] = ["normal", "ao"],
    resolution: int = 2048,
    samples: int = 16,
    cage_extrusion: float = 0.02,
    max_ray_distance: float = 0.0,
    packing: Literal["none", "gltf_orm", "unreal_orm", "unity_metallic_smoothness"] = "none",
    normal_space: Literal["opengl", "directx"] = "opengl",
    denoise: bool = True,
    async_: bool = False,
) -> BakeResult | JobHandle:
    """Bake maps from `high` onto `low`'s UVs. Cycles CPU only; Blender has no
    other baker.

    Measured on the CPU-only reference box: a 2048x2048 NORMAL pass at 8 samples
    is 1.90 s. That is cheap enough to bake on every iteration, which changes the
    interaction model entirely — bake and look, rather than bake once at the end.
    A four-map set at 4096 will be roughly 30-60 s, which is where async_ and
    progress notifications start mattering.

    normal_space="opengl" is Blender's native (+Y green up). Unreal needs
    "directx" (-Y). Getting this wrong does not look like a broken file, it looks
    like bad lighting, which is why it goes undetected for days. forge_export
    with target_engine="unreal" will flip it for you and say so.

    `packing` writes the channel-packed composites as well as the raw maps:
      "gltf_orm"   — occlusion R, roughness G, metallic B, Non-Color (Khronos)
      "unreal_orm" — same channels, but normal maps flipped to DirectX
      "unity_metallic_smoothness" — metallic R, smoothness A, where
                     smoothness = 1 - roughness. A conversion, not a relabel.
    Height stays its own 16-bit single-channel file; packed into 8 bits it steps
    visibly.

    async_=True returns a JobHandle immediately; poll forge_job. Synchronous
    calls emit notifications/progress on their own response stream per pass.

    Returns: one image id and resource URI per pass, plus a quality report —
    per-pass coverage (fraction of the atlas written), ray-miss count, and the
    UV islands that failed to receive rays, which is the actual diagnostic when
    a bake looks wrong.
    """
```

### 9.7 Export, validation, jobs, generation

```python
def forge_export(
    session: SessionId,
    targets: list[ObjectId] | None = None,
    format: Literal["glb", "gltf", "usdc", "usdz", "usda", "fbx", "obj",
                    "abc", "stl", "ply"] = "glb",
    path: str | None = None,
    target_engine: TargetEngine = "generic",
    apply_modifiers: bool = True,
    apply_transforms: bool = True,
    include_textures: bool = True,
    compression: Literal["none", "draco", "meshopt", "ktx2"] = "none",
    lod_suffix_convention: str | None = None,
    validate: bool = True,
) -> ExportReport:
    """Write the asset out with the right axis, units and conventions for the target.

    All formats verified working from the bpy 5.3.0 wheel: GLB (18 ms), USDC
    (36 ms), USDZ (6 ms), FBX (18 ms), OBJ (14 ms), ABC (5 ms), STL (1 ms).

    `target_engine` is the whole point of this tool, because these are the things
    that break silently:
      "unreal"  — scale x100 (centimetres), -90 deg Z for character-forward (+X),
                  normal maps flipped to DirectX green
      "unity"   — metres, +Z forward, left-handed winding handled on import for
                  glTF but NOT for FBX
      "web"     — glTF Y-up, aggressive texture budget, compression recommended
      "film"    — USD, metersPerUnit written explicitly, Y-up for DCC interop
    Every conversion applied is listed in the report. Nothing is converted silently.

    apply_transforms=True is the default and should stay that way: object-level
    non-uniform scale survives into glTF as a node transform and corrupts normals
    downstream.

    compression: NOTE that the bpy pip wheel ships WITHOUT the Draco and
    MeshOptimizer bridge libraries (verified: both libbf_intern_draco_bridge.so
    and libbf_intern_meshopt_bridge.so are absent). forge therefore post-processes
    with gltf-transform / gltfpack on Node. "ktx2" needs toktx. If the tooling is
    missing, the call fails loudly rather than exporting uncompressed and saying
    nothing.

    Returns: file path and resource URI, byte size, per-object triangle counts,
    the full list of conversions applied, the extension list written, and the
    validation report when validate=True.
    """

def forge_validate(
    session: SessionId,
    target: str | ObjectId,
    profile: Literal["gltf_spec", "3d_commerce", "game_ready", "film_ready",
                     "printable"] = "game_ready",
    budgets: dict | None = None,
) -> ValidationReport:
    """Validate an exported file and/or a live object against a profile.

    "gltf_spec"    — Khronos gltf-validator (installs via npm; verified working
                     here). JSON issue report plus asset stats.
    "3d_commerce"  — the Khronos 3D Commerce product profile: triangle budgets,
                     texture sizes, real-world dimensions, UV ranges.
    "game_ready"   — forge's own: watertight, zero non-manifold edges, zero
                     degenerate faces, quad purity above threshold, UV overlap
                     zero, texel density within tolerance of target, LOD chain
                     present and verified, triangle budget, texture power-of-two,
                     no unapplied transforms, correct normal-map space for the
                     declared engine.
    "film_ready"   — looser poly budget, stricter on UDIM layout and attribute
                     preservation.
    "printable"    — watertight, manifold, minimum wall thickness, no
                     self-intersection, consistent outward normals.

    This is the tool that catches what format validators do not: a non-manifold
    shell, a 0.4 px/m texel density, a 40%-wasted atlas, a mirrored half that is
    3 mm off. USD validation needs `usd-core` installed separately — the bpy
    wheel exports USD but does not expose the `pxr` Python module.

    Returns: pass/warn/fail per check with the measured value, the threshold, and
    a concrete remediation call for each failure.
    """

def forge_job(job_id: JobId, wait_s: int = 0) -> JobStatus:
    """Poll an async job (bake, heavy remesh, ML generation).

    wait_s>0 long-polls up to that many seconds, which keeps turn count down.
    Returns: state, percent, current stage, elapsed, the partial result if one
    exists, and the final result when done.

    This exists in addition to the MCP tasks extension deliberately: it works
    regardless of whether the client negotiated io.modelcontextprotocol/tasks,
    and it costs ten lines.
    """

def forge_generate(
    session: SessionId,
    image: str | None = None,
    prompt: str | None = None,
    backend: Literal["trellis2", "hunyuan3d_21", "sf3d", "partcrafter",
                     "cloud:tripo", "cloud:meshy"] = "trellis2",
    parts: int = 1,
    resolution: int = 1024,
    post: Literal["none", "repair", "repair_remesh"] = "repair_remesh",
    async_: bool = True,
) -> JobHandle | GenerateResult:
    """Generate a blockout mesh from an image. v2, GPU-only, and explicitly a
    blockout — not a shipping asset.

    Local backends and what they need:
      "trellis2"       — microsoft/TRELLIS.2-4B, MIT, >=24 GB VRAM, NVIDIA,
                         Linux only. Image input only; no text conditioning.
                         ~3 s at 512^3, ~17 s at 1024^3, ~60 s at 1536^3 on H100.
                         The authors state raw meshes may have small holes and
                         minor topological discontinuities.
      "hunyuan3d_21"   — last open-weights Hunyuan3D. Tencent Community License,
                         which excludes EU/UK/South Korea for commercial use.
                         ~10 GB shape, ~29 GB shape+texture.
      "sf3d"           — Stable Fast 3D. Sub-second, low fidelity. Good enough
                         for a proportion check.
      "partcrafter"    — separable multi-part output from a single image, up to
                         ~16 parts, no segmentation step. This is the one that
                         matters for rigging; `parts` controls the count.

    `prompt` requires a text-to-image step first, because none of the local
    backends take text directly.

    post="repair_remesh" is the default and runs the pipeline these outputs
    actually need: hole-fill, manifold repair through manifold3d, voxel remesh,
    then quadriflow. Raw ML output is an isosurface-flavoured mesh, not topology.

    Cloud backends send your geometry and images off the machine. They are
    disabled unless FORGE_ALLOW_CLOUD=1 and refuse with a capability error
    otherwise. This is deliberate: forge is self-hosted, and `blender-mcp`
    wiring four paid cloud generators behind one tool is the decision we are
    not copying.

    On a machine with no GPU this returns a structured capability error naming
    what is missing and suggesting forge_apply_ir with primitives instead. It
    never silently produces something worse.
    """
```

### 9.8 Tool count and annotations

| group | tools |
|---|---|
| session/state | `forge_open`, `forge_capabilities`, `forge_checkpoint`, `forge_rollback`, `forge_list_checkpoints`, `forge_diff` |
| authoring | `forge_apply_ir`, `forge_op`, `forge_exec` |
| query | `forge_scene`, `forge_inspect` |
| vision | `forge_render`, `forge_uv_preview`, `forge_compare` |
| topology/detail | `forge_remesh`, `forge_detail`, `forge_lod` |
| uv/material | `forge_uv`, `forge_material`, `forge_bake` |
| export | `forge_export`, `forge_validate` |
| jobs/ML | `forge_job`, `forge_generate` |

That's 24 as listed — `forge_job` and `forge_generate` are v1/v2, so the v0+v1 surface is 22.

Annotations: `readOnlyHint` on `forge_capabilities`, `forge_scene`, `forge_inspect`,
`forge_render`, `forge_uv_preview`, `forge_compare`, `forge_list_checkpoints`, `forge_diff`,
`forge_job`. `idempotentHint` on `forge_apply_ir`. `destructiveHint` on `forge_rollback`,
`forge_op`, `forge_remesh` (when `in_place=True`), `forge_exec`.

---

## 10. The asset IR

**Conclusion: yes, worth having, and it is the centre of the design.** The argument is in §6.8; the
short version is that it absorbs the repetitive bulk where mistakes are mechanical, is validated
before it mutates anything, is idempotent by id, and can be read back as a complete token-cheap
picture of the scene.

### 10.1 Schema sketch

```yaml
forge_ir: 1                      # schema version, required
meta:
  name: cargo_crate_mk2
  units: m                        # m | cm | mm | in | ft
  up_axis: Z                      # forge canonical; conversion happens on export
  forward_axis: Y
  target_engines: [unity, web]
  texel_density_px_per_m: 1024

nodes:
  # ---- primitives and blockout -------------------------------------------
  - id: hull
    kind: primitive
    shape: cube                   # cube|sphere|cylinder|cone|torus|plane|ico|grid|monkey
    size: [1.2, 0.8, 0.8]
    location: [0, 0, 0.4]
    rotation_euler: [0, 0, 0]
    collection: body

  - id: lid
    kind: primitive
    shape: cube
    size: [1.22, 0.82, 0.06]
    location: [0, 0, 0.83]
    collection: body

  # ---- CAD (phase 2) ------------------------------------------------------
  - id: hinge_body
    kind: cad
    backend: build123d
    script: |
      with BuildPart() as p:
          Box(0.08, 0.02, 0.02)
          Cylinder(0.006, 0.03, mode=Mode.SUBTRACT)
          fillet(p.edges().filter_by(Axis.Z), 0.002)
    tessellation: { linear_deflection: 0.0005, angular_deflection: 0.15 }

  # ---- generated (phase 2) ------------------------------------------------
  - id: tarp
    kind: generated
    backend: trellis2
    image: refs/tarp.png
    post: repair_remesh

  # ---- modifier stacks ----------------------------------------------------
  - id: hull_shaped
    kind: modify
    source: hull
    stack:
      - { op: bevel, width: 0.008, segments: 3, limit_method: ANGLE, angle: 30 }
      - { op: boolean, operation: DIFFERENCE, with: lid, solver: EXACT }
      - { op: solidify, thickness: 0.012, offset: -1 }
      - { op: weighted_normal }

  - id: hull_joined
    kind: combine
    operation: union              # union|difference|intersect|join
    engine: manifold3d            # manifold3d (manifold by construction) | blender_exact
    sources: [hull_shaped, hinge_body]

  # ---- topology -----------------------------------------------------------
  - id: hull_high
    kind: remesh
    source: hull_joined
    method: voxel
    voxel_size: 0.004

  - id: hull_quad
    kind: remesh
    source: hull_high
    method: quadriflow
    target_faces: 6000
    seed: 7                       # recorded so a rerun reproduces
    preserve_sharp: true

  # ---- procedural detail --------------------------------------------------
  - id: hull_detailed
    kind: detail
    source: hull_quad
    subdivide_to: 3
    layers:
      - { gen: panel_lines,    spacing: 0.12, depth: 0.0012, axis: Z }
      - { gen: edge_damage,    width: 0.0015, jitter: 0.6, mask: "sharp_edges>35deg" }
      - { gen: noise_displace, scale: 24.0, amount: 0.0008, mask: curvature_convex }
      - { gen: rivets,         density: 36.0, radius: 0.0025, mask: panel_border }

  # ---- UV -----------------------------------------------------------------
  - id: hull_uv
    kind: uv
    source: hull_quad
    method: xatlas
    texture_resolution: 2048
    target_density_px_per_m: 1024
    margin: 0.01

  # ---- material -----------------------------------------------------------
  - id: mat_hull
    kind: material
    assign_to: [hull_uv]
    base: { color: [0.21, 0.19, 0.16], roughness: 0.55, metallic: 0.0 }
    layers:
      - { type: edge_wear, mask: curvature_convex, amount: 0.3,
          color: [0.42, 0.40, 0.36], roughness: 0.3 }
      - { type: grime,     mask: ao_cavity, amount: 0.45,
          color: [0.06, 0.05, 0.04], roughness: 0.85 }
      - { type: noise_rough, scale: 14.0, amount: 0.12 }
    normal: { source: baked, strength: 1.0 }

  # ---- bake ---------------------------------------------------------------
  - id: bake_hull
    kind: bake
    low: hull_uv
    high: hull_detailed
    passes: [normal, ao, curvature, roughness]
    resolution: 2048
    samples: 16
    packing: gltf_orm
    normal_space: opengl

  # ---- LODs ---------------------------------------------------------------
  - id: hull_lods
    kind: lod
    source: hull_uv
    ratios: [1.0, 0.5, 0.25, 0.1]
    method: quadric
    verify: true

  # ---- escape hatch, adopted into the graph -------------------------------
  - id: weird_bit
    kind: exec
    code: |
      import bmesh
      ob = forge.obj("hull_uv")
      bm = bmesh.new(); bm.from_mesh(ob.data)
      # ...bespoke bmesh work...
      bm.to_mesh(ob.data); bm.free()
      forge.register(ob, "weird_bit")
    depends_on: [hull_uv]

exports:
  - { format: glb,  target_engine: web,   path: out/crate.glb,
      compression: meshopt, validate: true }
  - { format: fbx,  target_engine: unity, path: out/crate.fbx, validate: true }

# Rigging/animation hand-off (owned by the other two researchers; see section 14)
rig_hints:
  parts:
    - { id: hull_uv, role: body,  pivot: [0, 0, 0] }
    - { id: lid,     role: hinge, pivot: [0, -0.4, 0.8], axis: X, limits: [0, 95] }
  bind_pose: default
  origin_convention: base_center
```

### 10.2 Why this shape

- **Flat node list with explicit `id`s and `source`/`sources` edges**, not nesting. A DAG an LLM can
  patch one node at a time without re-emitting the document, and `depends_on` makes the topological
  order explicit so the compiler never guesses.
- **Each node names its own `kind`**, with a per-kind schema. Validation errors point at
  `nodes[7].stack[2].width`, not "something in your JSON".
- **Every intermediate stage is a named node** (`hull_quad`, `hull_high`, `hull_detailed`). That is
  what lets the bake find its high-poly, lets `forge_compare` score a remesh against its input, and
  lets Claude change one stage without rebuilding the chain.
- **`kind: exec` is in the graph**, not outside it, and `forge.register()` adopts its output. Escape
  hatch results are first-class scene members with ids, not orphans the next IR apply will clobber.
- **Both YAML and JSON** are accepted; JSON Schema 2020-12 is the single source of truth, which
  SEP-2106 now lets forge put straight into `inputSchema` with `$ref`s intact.
- **`rig_hints` is in the IR but owned by someone else.** It is the seam, declared where both halves
  can see it (§14).

---

## 11. Phased build plan

### v0 — "Claude can see and measure" (the smallest thing that is actually useful)

Nine tools: `forge_open`, `forge_capabilities`, `forge_exec`, `forge_scene`, `forge_inspect`,
`forge_render`, `forge_checkpoint`, `forge_rollback`, `forge_export`.

- stdio transport, `mcp` SDK, process-per-session over a UNIX socket, supervisor with crash recovery
- auto-checkpoint before every mutating call; ring buffer of 50
- the §6.7 structured error layer including `state_at_failure` and the first ~15 classified failures
- `forge_render`: `quad_ortho`, `wireframe`, `silhouette`, `persp`, Workbench + Cycles, auto engine
  selection with EEVEE hard-disabled on CPU
- `forge_inspect`: topology, manifold, bounds, symmetry
- `forge_export`: glb/fbx/obj/usdc with correct axis+unit conversion per `target_engine`

**Why this is the v0 line:** with `forge_exec` plus eyes plus numbers plus free rollback, Claude can
already build real assets. Everything after v0 is about removing the ways `exec` goes wrong.

### v1 — "the pipeline is typed" (the actual product)

Adds thirteen: `forge_apply_ir`, `forge_op`, `forge_diff`, `forge_list_checkpoints`,
`forge_compare`, `forge_uv_preview`, `forge_remesh`, `forge_detail`, `forge_lod`, `forge_uv`,
`forge_material`, `forge_bake`, `forge_validate`, `forge_job`.

- the IR compiler + JSON Schema + idempotency-by-id + dry-run
- `forge_assets.blend`: the parameterised geometry-node and shader node groups `forge_detail` and
  `forge_material` drive
- xatlas integration with the vertex mapping carried through
- bake with channel packing and the per-engine normal-space flip
- `gltf-validator` + `3d-commerce` + the `game_ready` numeric profile
- Streamable HTTP transport as an option; `notifications/progress` on bakes and remeshes;
  forge-native async jobs
- the `forge:hard_surface_prop` / `forge:organic_prop` / `forge:game_ready_checklist` prompts

### v2 — "reach"

- `forge_generate` with TRELLIS.2 and PartCrafter, GPU-gated, `post="repair_remesh"` mandatory
- `kind: cad` nodes on build123d, STEP round-trip, re-tessellation at multiple LODs
- `method="learned"` in `forge_remesh` if and when native quad-mesh weights become available
- UDIM support, hair cards, Instant Meshes as a remesh option
- the `io.modelcontextprotocol/tasks` extension
- resource-based texture/asset library; a shared forge asset cache across sessions

---

## 12. Open questions and known risks

**Flagged honestly. The first three are the ones that could change the design.**

1. **GPL-3.0 inheritance.** forge imports a GPL-3.0 wheel in-process, so forge is GPL-3.0 as
   distributed. Fine for a free self-hosted tool, fatal for anything proprietary later. I found no
   official Blender Foundation statement about the pip wheel specifically **[unverified]**. *Risk:
   low for this project, high if the intent ever changes. Decide now and write it in the README.*
2. **The Python version pin is exact: `bpy==5.3.0` requires `python==3.13.*`** **[measured]**. A
   container that moves to 3.14 breaks forge until the next Blender minor ships a cp314 wheel. *Risk:
   medium. Mitigation: pin the interpreter in the image, and keep the `blender --background`
   subprocess path as a documented fallback even though it's worse.*
3. **MCP SDK naming and version are unverified.** Secondary sources say `mcp>=2` renames `FastMCP`
   to `MCPServer` and auto-answers `server/discover`, but the blogs disagree on version numbers
   **[unverified]**. `mcp` 2.3.0 and `fastmcp` 4.2.0 exist on PyPI **[measured]** and that's all I
   confirmed. *Mitigation: read the installed SDK's own migration docs before the first import. Do
   not write this from memory.*
4. **`bpy` segfaults.** Pathological geometry (self-intersecting booleans, enormous subdivisions)
   can take the process down with no Python exception. *Mitigation: the supervisor + auto-checkpoint
   design handles it, but it must be built in v0, not retrofitted.*
5. **Blender API churn.** Three breaking details found in one afternoon: the engine enum lost its
   `_NEXT` suffix, `Material.use_nodes` is going away in 6.0, Draco/meshopt bridges are absent
   **[all measured]**. Blender ships a minor every ~2 months. *Mitigation: a version-gated
   compatibility shim and a CI smoke test per Blender release. Budget real time for this.*
6. **QuadriFlow's target is approximate** (asked 8,000, got 7,514 **[measured]**) and seeded. If the
   seed isn't recorded, results aren't reproducible. *Mitigated in the tool signature; easy to
   forget in the implementation.*
7. **Smart UV Project non-determinism** **[unverified, forum report]** — the reason xatlas is the
   default. *Open question: is xatlas 0.0.11 bit-identical across runs on the same input? I did not
   test it twice. Verify before relying on it for bake stability.*
8. **No GPU means no ML, full stop.** TRELLIS.2 needs ≥24 GB VRAM; there is no useful CPU fallback
   for a 4B flow-matching model. *Accepted: it's a v2 accessory, and the procedural path doesn't
   need it.*
9. **Image token cost.** Three 512² PNGs per render is real context. If Claude renders every turn
   it will exhaust context before finishing an asset. *Mitigation: the ≤3-block cap, composites by
   default, and the numeric report attached to every render so looking is rarely necessary twice.
   Open question: what's the right default resolution? 512 is a guess, not a measurement.*
10. **Memory, not time, is the ceiling on detail.** 504k faces in 249 ms **[measured]**, but ~1 GB
    per 4M faces against 15 GB total with a ~930 MB resident Blender. A 4M-face high-poly plus a
    2048² bake plus a Cycles render is close to the edge. *Mitigation: the `max_faces` guard and
    peak-memory reporting. Open question: what happens under the OOM killer mid-bake — does the
    checkpoint survive? Untested.*
11. **Unverified, listed plainly:** Hunyuan3D 2.x VRAM figures (sources give 6/16 GB, 10/29 GB);
    whether 3.0/3.1 weights are truly closed; PartCrafter's VRAM; whether any native quad-mesh model
    has usable open weights; `blender-mcp`'s exact current tool count (10 in the README, directories
    claim 22, forks claim 80–92); Zoo/KittyCAD's and cad-mcp's current state; npm
    `gltf-validator`'s latest release.
12. **Not researched, flagged as a gap:** multi-user concurrency and quotas; authentication for the
    HTTP transport; whether a shared asset cache across sessions is worth the complexity; Windows
    support (Wren's local box is Windows — the bpy wheel ships win_amd64, but nothing here was tested
    there).

---

## 13. One thing I'd do differently from every existing project

Every 3D MCP server I looked at treats rendering as "show the human what happened". forge should
treat it as **closing a control loop for a model that cannot see**, and that changes three
decisions: renders carry their numeric report inline so looking twice is unnecessary; the default
view is a composite rather than one hero angle because errors hide on the far side; and the numeric
suite is a first-class tool rather than something you could write in `exec`. The measured
8-milliseconds-versus-8-seconds gap between `forge_inspect` and a Cycles render is the whole
argument — 1000× cheaper, and it catches most real defects.

---

## 14. Seams for rigging and animation

Written so the other two designs can bolt on without renegotiating anything.

### 14.1 Part naming and separation (rigging's primary need)

- **Every mesh has a stable `forge_id`** stored in `obj["forge_id"]`, surviving save/load and
  Blender's `.001` suffixing. Rigging binds to forge ids, never to Blender object names.
- **`rig_hints.parts` in the IR** (§10.1) is where geometry declares roles: `{id, role, pivot,
  axis, limits}`. Geometry owns `id` and `pivot`; **rigging owns `role`, `axis`, `limits`, and
  anything about bones.** The schema reserves the block; the rigging design fills it in.
- **`forge_op(op="separate_parts")`** splits by loose parts, by material, or by vertex group, and
  returns new forge ids derived from the parent (`obj_crate__lid`). Deterministic, so a rig
  referencing them doesn't break on rebuild.
- **PartCrafter** (§4.3) is the ML path that outputs separable parts directly — the one generative
  result rigging should care about.
- **Collections mirror the part hierarchy** (`body/`, `limbs/`, `attachments/`), so rigging gets a
  tree, not a flat list.

### 14.2 Deformation-friendly topology (geometry's obligation)

Things geometry must guarantee, measurably, for a rig to work:

- **Quad-dominant.** `forge_inspect` reports `quad_purity`; the `game_ready` profile can gate on it.
  Triangles at a bending joint pinch.
- **Edge loops at deformation zones.** `forge_remesh(method="quadriflow")` does not know where the
  elbows are. This needs either a density mask in `forge_detail`, or hand-placed loops via
  `forge_exec`, driven by `rig_hints`. **Open seam: rigging should declare deformation zones in
  `rig_hints` so geometry can densify them.** That's a cross-design decision, not a geometry one.
- **At least 3 edge loops across every bend**, 4+ for shoulders and hips. Geometry can verify this
  once rigging names the zones.
- **No non-manifold geometry, no degenerate faces, no loose verts** — all reported and gate-able.
  Weight painting and automatic weighting both break on them.
- **Watertight where skinning crosses a boundary.** Reported.
- **Symmetry.** `forge_inspect(checks=["symmetry"])` gives RMS and max error. Mirrored rigs and
  mirrored weights need this near zero; a 3 mm asymmetry is invisible in a render and obvious in a
  pose.
- **Bake the high-poly, rig the low-poly.** `forge_remesh(in_place=False)` keeps `{id}_high` so the
  rig always binds to the clean quad mesh and detail lives in the normal map. Non-negotiable.

### 14.3 Scale and orientation (the shared contract)

**forge canonical, internal, always: Z-up, +Y forward, metres, right-handed.** Everything else is a
conversion applied at export and reported.

- **Origin conventions** by asset class, declared in `rig_hints.origin_convention`:
  `base_center` (props, buildings — sits on the ground plane), `feet_center` (characters — world
  origin between the feet), `pivot` (doors, hinges — at the axis of rotation),
  `volume_center` (free-floating).
- **Transforms are applied on export** (`apply_transforms=True` default), so no object-level scale
  reaches the rig. Non-uniform object scale plus a skeleton is a corruption bug, not a style choice.
- **Real-world scale is mandatory.** A 1.8 m character is 1.8 units in forge. Animation retargeting
  and physics both assume it.
- **Per-engine conversions** (§7.3) are geometry's responsibility and are listed in the export
  report: Unreal ×100 cm and −90° Z for +X forward; Unity +Z forward; USD `metersPerUnit` written
  explicitly. Rigging should read the report, not re-derive it.
- **Bind pose:** geometry ships A-pose for characters unless `rig_hints.bind_pose` says otherwise.
  T-pose is easier to rig and worse for shoulder weights; A-pose is the default for that reason.
  **Rigging owns this value; geometry just honours it.**

### 14.4 What geometry hands over

A `forge_export` with `target_engine="generic"` plus:

- the low-poly quad mesh with stable forge ids and real-world scale, transforms applied
- the `{id}_high` detail mesh, retained in the session for re-baking after any rig-driven topology
  change
- baked normal/AO/curvature/ORM maps and a material spec
- `forge_inspect` reports per part: quad purity, manifold status, symmetry error, bbox
- the IR document itself, including the `rig_hints` block — so rigging can rebuild the geometry
  deterministically rather than receiving a frozen file
- vertex groups, if `rig_hints` named any. Geometry creates empty named groups on request;
  **weights are rigging's.**

### 14.5 What geometry needs back

- **Deformation zones** to densify (§14.2) — the one real blocker.
- The **bind pose** choice.
- Whether the rig needs **separate** parts or a **single skinned** mesh, because that changes
  whether `forge_remesh` runs per part or on the joined shell. Joining before remeshing gives better
  topology; separating after is lossy. Decide once, early.

---

## Appendix A — raw measurements

All from this container, 2026-11-14, Ubuntu 24.04.5 / Python 3.13.18 / 4 cores / 15 GB / no GPU.

```
bpy 5.3.0 (build 2026-11-10), cp313 wheel, 427.5 MB download, 927 MB installed
import bpy                                     0.549 s
wm.read_factory_settings(use_empty=True)        0.043 s
render engine enum          ['BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES']
gpu.platform.backend_type_get()                 VULKAN   (software)
cycles CUDA devices                             []       ("CUEW initialization failed")
cycles OPTIX devices                            []
modifier types / GeometryNode* / ShaderNode*    83 / 292 / 108
bake types   COMBINED AO SHADOW POSITION NORMAL UV ROUGHNESS EMIT ENVIRONMENT
             DIFFUSE GLOSSY TRANSMISSION

Workbench   512^2                               0.151 s
Workbench  1024^2                               0.783 s
Workbench  2048^2                               2.455 s
EEVEE       512^2                              24.699 s     <-- unusable, CPU only
EEVEE      1024^2                              28.656 s
EEVEE      2048^2                              43.924 s
Cycles CPU  512^2  32 spp                       1.949 s
Cycles CPU 1024^2  32 spp                       7.989 s
Cycles CPU 1024^2 128 spp                      13.854 s

SUBSURF L3 apply (Suzanne)                      0.044 s ->  31,488 faces
SUBSURF +L2 apply                               0.249 s -> 503,808 faces
voxel_remesh 0.01                               1.650 s -> 153,390 faces
quadriflow_remesh target_faces=8000             6.633 s ->   7,514 faces
uv.smart_project (7.5k faces)                   0.018 s
object.bake NORMAL 2048^2 @ 8 spp               1.897 s
wm.save_as_mainfile                             0.009 s (264 KB)
wm.open_mainfile                                0.013 s

bmesh stats, quadriflow output:
  V=3708 E=7412 F=3706  euler=2  genus=0  volume=2.00544  area=10.1823
  non_manifold_edges=0  non_manifold_verts=0  degenerate_faces=0

export glb  520,076 B (18 ms)   usdc 165,458 B (36 ms)   usdz 165,625 B (6 ms)
export fbx  233,868 B (18 ms)   obj  520,888 B (14 ms)   abc  396,487 B (5 ms)
export stl  370,684 B (1 ms)
glTF exporter:  Draco bridge MISSING, MeshOptimizer bridge MISSING

trimesh 5.2.1    watertight/winding/euler/volume/area      0.008 s
trimesh          boolean difference (manifold engine)      0.009 s
trimesh          convex hull                               0.019 s
trimesh          signed_distance, 500 samples              0.100 s
manifold3d 3.5.5 sphere(64) - cube                         0.003 s
xatlas 0.0.11    parametrize 2562-vert icosphere           0.320 s  -> 2794 UV verts
pymeshlab 2025.7 quadric decimate to 500 faces             0.029 s
pymeshlab        filter count   235 without libOpenGL.so.0 / 281 with

libigl 2.6.3     NO cp313 wheel (cp310/311/312/38/39 only) -> unavailable on 3.13
PyPI "sdf" 0.3.8 is a scientific data format package, NOT the SDF modelling lib
libfive PyPI     0.1.0a3, 2020-10-11 -> dead for Python
node             v22.23.4; npm install gltf-validator OK
pxr (OpenUSD)    not importable from the bpy wheel -> needs pip install usd-core

venv total with bpy + trimesh + manifold3d + xatlas + pymeshlab:   1.7 GB
```

## Appendix B — sources

**Blender / bpy**
- https://pypi.org/project/bpy/
- https://docs.blender.org/api/5.3/
- https://docs.blender.org/manual/en/5.3/addons/scene_gltf2.html
- https://projects.blender.org/blender/blender-manual/issues/104850 (extension licensing)
- https://projects.blender.org/blender/blender/issues/105619 (python node-link bug)
- https://blog.cg-wire.com/blender-scripting-geometry-nodes-2/
- https://pypi.org/project/nodebpy/

**MCP**
- https://modelcontextprotocol.io/specification/2026-07-28/changelog
- https://modelcontextprotocol.io/specification/latest
- https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr.md
- https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http.md
- https://modelcontextprotocol.io/extensions/tasks/overview.md
- https://pypi.org/project/mcp/ · https://pypi.org/project/fastmcp/

**Geometry libraries**
- https://github.com/jpcy/xatlas · https://github.com/mworchel/xatlas-python
- https://pypi.org/project/manifold3d/ · https://pypi.org/project/trimesh/
- https://pypi.org/project/pymeshlab/ · https://pypi.org/project/build123d/
- https://pypi.org/project/cadquery/ · https://libfive.com/ · https://mattkeeter.com/projects/fidget

**Extraction / remeshing**
- https://www.cgal.org/2025/10/10/Isosurfacing/
- https://www.boristhebrave.com/2018/04/15/dual-contouring-tutorial/
- https://arxiv.org/abs/2005.11621 (ManifoldPlus)
- https://www.alphaxiv.org/overview/2604.00157 (surfaces from discrete distance fields, 2026)

**ML 3D generation**
- https://huggingface.co/microsoft/TRELLIS.2-4B (arXiv 2512.14692)
- https://arxiv.org/abs/2509.21420 (QuadGPT, ICLR 2026)
- https://arxiv.org/html/2603.00526v1 (Mesh-Pro)
- https://arxiv.org/pdf/2605.16813 (QuadLink)
- https://arxiv.org/abs/2506.05573 · https://github.com/wgsxm/PartCrafter (PartCrafter, NeurIPS 2025)
- https://3dprintingindustry.com/news/native-quad-meshes-arrive-in-ai-3d-generation-with-tripo-p2-0-255368/
- https://triposr.org/blog/hunyuan3d-versions (Hunyuan3D open vs hosted — secondary)

**Existing MCP 3D servers**
- https://github.com/ahujasid/blender-mcp
- https://github.com/jagjerez-org/3d-mcp-server
- https://snyk.io/articles/6-mcp-servers-for-using-ai-to-generate-3d-models

**Export / validation**
- https://www.khronos.org/blog/art-pipeline-for-gltf
- https://github.com/KhronosGroup/glTF-Validator
- https://www.npmjs.com/package/gltf-validator
- https://cdn.jsdelivr.net/npm/@mikefesta/3dc-validator@1.0.0/README.md
- https://github.com/KhronosGroup/glTF-Sample-Assets
