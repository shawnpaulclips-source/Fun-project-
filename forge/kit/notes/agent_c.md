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
