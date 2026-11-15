"""forge MCP server: forge-kit as tools an agent calls natively.

    /home/user/bpyenv/bin/python -m forgekit.server            # stdio MCP server
    (run from forge/kit, or with forge/kit on PYTHONPATH)

Shape (research 01 section 6, ARCHITECTURE section 3):

* Stateless at the MCP level. No protocol session is relied on: every tool takes
  an explicit, server-minted `session_id` handle as an ordinary argument
  (SEP-2567). Works the same over the 2026-07-28 stateless protocol and the
  older initialize handshake.
* bpy is a process-global singleton, so each session handle owns ONE worker
  subprocess running bpy (this same module with --worker). The MCP process
  itself never imports bpy; it routes, enforces the image budget and owns the
  session registry. Worker <-> server is newline-delimited JSON over a private
  socketpair (no TCP, no network surface).
* Sessions are durable, processes are not: before every forge_exec the worker
  writes an automatic checkpoint. If a worker dies (bpy segfaults on
  pathological geometry) or times out, the server respawns it from the newest
  checkpoint and returns a structured error naming the recovery point. An idle
  worker is reaped after FORGE_IDLE_S seconds (default 1800) and rehydrated on
  the next call; a session id survives a server restart as long as its
  directory does.
* stdout belongs to the MCP transport. The server logs to stderr; worker stdout
  (Blender's own prints) is redirected to the server's stderr.
* Every tool returns a short text summary + structuredContent; renders also
  return an image content block, capped (see IMAGE_* below).
* Errors are the product: forge_exec failures come back classified with
  state_at_failure (active object, mode, selection, engine), likely_cause and
  suggested_fix, and by default the scene is rolled back to the pre-exec
  checkpoint so a failure never leaves a partial mutation.

SDK: mcp 2.3.0. `FastMCP` does not exist in 2.x (mcp/server/fastmcp.py raises
ModuleNotFoundError pointing at the migration guide); the class is
`mcp.server.MCPServer`. Tools annotated `-> CallToolResult` are returned
verbatim, which is how text + structured_content + image blocks are combined.
"""
import os, sys, json, time, uuid, base64, socket, subprocess, threading, tempfile, logging, re
import traceback, difflib, io, contextlib, math

log = logging.getLogger("forge.server")
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Image budget. Images cost ~w*h/750 tokens. Workbench renders are capped at
# 1000 px on the long side (1000x1000 = 1333 tokens) and every image block at
# 2500 tokens; Cycles at 640 px because CPU path tracing time grows with pixels.
IMAGE_TOKEN_CAP = 2500
WORKBENCH_MAX_PX = 1000
CYCLES_MAX_PX = 640
DEFAULT_PX = 768
IDLE_S = float(os.environ.get("FORGE_IDLE_S", "1800"))
SESSION_ROOT = os.environ.get("FORGE_SESSION_DIR", os.path.join(tempfile.gettempdir(), "forge_sessions"))


def image_tokens(w, h):
    return int(round(w * h / 750.0))


def capped_size(resolution, aspect, mode):
    """(w, h) for a requested long side and aspect, inside the engine and token caps."""
    cap = WORKBENCH_MAX_PX if mode == "workbench" else CYCLES_MAX_PX
    long_side = max(64, min(int(resolution), cap))
    aspect = max(0.25, min(4.0, float(aspect)))
    if aspect >= 1:
        w, h = long_side, int(round(long_side / aspect))
    else:
        w, h = int(round(long_side * aspect)), long_side
    while image_tokens(w, h) > IMAGE_TOKEN_CAP:
        w, h = int(w * 0.95), int(h * 0.95)
    return w, h


# =====================================================================================
# error classification (runs in the worker, where bpy state can be read)
# =====================================================================================
_SOCKET_RENAMES = ("Principled BSDF sockets renamed in 4.x/5.x: 'Specular' -> 'Specular IOR Level', "
                   "'Emission' -> 'Emission Color', 'Transmission' -> 'Transmission Weight', "
                   "'Subsurface' -> 'Subsurface Weight', 'Sheen' -> 'Sheen Weight', 'Clearcoat' -> 'Coat Weight'")

# operators that only make sense with a MESH active
_MESH_OPS = (r"bpy\.ops\.(object\.(bake|modifier_apply|vertex_group_\w+|shade_\w+|parent_set|join)"
             r"|mesh\.\w+|uv\.\w+)")

# (kind, regex over "ExcType: message", likely_cause, suggested_fix). First match wins.
ERROR_TABLE = [
    ("bpy_operator_context_active", r'is not a mesh|Object ".*" is not a (mesh|curve)|requires an active (mesh|object)',
     "the operator acts on the ACTIVE object, and the active object is {active_desc}",
     "make the target active and solely selected first: for o in bpy.context.selected_objects: o.select_set(False); "
     "ob = bpy.data.objects['<name>']; ob.select_set(True); bpy.context.view_layer.objects.active = ob"),
    ("bpy_operator_wrong_mode", r'poll\(\) failed.*|context is incorrect',
     "{poll_cause}",
     "{poll_fix}"),
    ("api_removed_auto_smooth", r"use_auto_smooth",
     "mesh.use_auto_smooth was removed in Blender 4.1",
     "forgekit.mesh.shade_smooth(ob) (smooth shading + Weighted Normal modifier), or bpy.ops.object.shade_auto_smooth()"),
    ("api_removed_fcurves", r"'Action' object has no attribute 'fcurves'|has no attribute 'fcurves'",
     "action.fcurves was removed in 4.4+/5.x (slotted actions: slot -> layer -> strip -> channelbag)",
     "forgekit.anim.fcurves(obj)"),
    ("enum_removed_eevee_next", r"BLENDER_EEVEE_NEXT",
     "the BLENDER_EEVEE_NEXT enum was removed in 5.3",
     "use 'BLENDER_WORKBENCH' for inspection or 'CYCLES' for materials; EEVEE is 12-30x slower than Cycles without a GPU"),
    ("api_removed_bone_layers", r"bone_groups|'Armature' object has no attribute 'layers'",
     "armature layers and bone groups were replaced by bone collections in 4.x",
     "use armature.collections_all / bone.collections"),
    ("name_not_found", r'bpy_prop_collection\[key\]: key "(?P<key>.*)" not found|KeyError: .*not found',
     "no datablock/socket named '{key}' {where}",
     "list names with forge_scene; Blender appends .001 to duplicates. {close}"),
    ("bake_no_image", r"[Nn]o active image|[Cc]ircular dependency for image",
     "bake writes into the ACTIVE Image Texture node of every material on the object, and one has none",
     "forgekit.bake.bake_material(ob, mat) handles this; or add an Image Texture node and set node_tree.nodes.active to it in EVERY material slot"),
    ("bake_no_uv", r"[Nn]o UV|UV map|uv layer|has no UV",
     "the object has no UV layer to bake into",
     "forgekit.bake.uv_unwrap(ob, method='smart') before baking"),
    ("stale_reference", r"StructRNA of type .* has been removed|ReferenceError",
     "a Python reference outlived its datablock (deleted, or the file was reloaded by forge_rollback / crash recovery)",
     "re-fetch by name: ob = bpy.data.objects['<name>']; never keep bpy references across forge_rollback"),
    ("bad_enum", r"enum \"(?P<val>[^\"]*)\" not found in \((?P<opts>[^)]*)\)",
     "'{val}' is not a valid value for that enum property",
     "valid values: {opts}"),
    ("modifier_apply", r"[Mm]odifier .*(cannot|disabled)|Cannot apply|Modifiers cannot be applied",
     "modifier_apply needs the object active, in OBJECT mode, with the modifier enabled",
     "apply without context: me = bpy.data.meshes.new_from_object(ob.evaluated_get(bpy.context.evaluated_depsgraph_get())); ob.modifiers.clear(); ob.data = me"),
    ("boolean_nonmanifold", r"[Bb]oolean.*|non-manifold|self.?intersect",
     "boolean input is probably non-manifold or self-intersecting",
     "check forgekit.inspect.mesh_stats(ob)['non_manifold_edges'] == 0 on both operands; voxel-remesh first or use solver='EXACT'; checkpoint before booleans"),
    ("no_active_object", r"'NoneType' object has no attribute|context\.object is None|Context missing active object",
     "something returned None - usually bpy.context.object/active_object with nothing active ({active_desc})",
     "set bpy.context.view_layer.objects.active explicitly, or look the object up with bpy.data.objects['<name>']"),
    ("file_not_found", r"FileNotFoundError|No such file|Cannot read file|cannot open file",
     "a path does not exist in the worker's filesystem",
     "use absolute paths; session files live under the session directory given by forge_session_open"),
    ("syntax", r"^(SyntaxError|IndentationError|TabError)",
     "the code did not parse",
     "fix the syntax at the reported line; nothing ran"),
    ("name_error", r"^NameError",
     "a name is undefined in the session namespace (namespaces reset after crash recovery; bpy references are cleared after rollback)",
     "preloaded names: {preloaded}. Import anything else explicitly."),
    ("api_attribute", r"^AttributeError: '(?P<type>[^']+)' object has no attribute '(?P<attr>[^']+)'",
     "'{type}' has no attribute '{attr}' in Blender 5.3 (the API moved, or the object is not the type you think)",
     "{close}"),
]


def _state_at_failure():
    import bpy
    ctx = bpy.context
    act = ctx.view_layer.objects.active if ctx.view_layer else None
    return {
        "active_object": {"name": act.name, "type": act.type} if act else None,
        "mode": act.mode if act else "OBJECT",
        "selected": [o.name for o in ctx.selected_objects][:12],
        "engine": ctx.scene.render.engine,
        "scene": ctx.scene.name,
        "frame": ctx.scene.frame_current,
        "objects": len(ctx.scene.objects),
    }


def classify_error(exc, tb_text, state, preloaded=()):
    """Turn an exception into {kind, message, python_type, likely_cause, suggested_fix}."""
    etype = type(exc).__name__
    msg = str(exc)
    head = f"{etype}: {msg}"
    act = state.get("active_object")
    active_desc = f"{act['type']} '{act['name']}'" if act else "nothing (no active object)"
    fmt = {"active_desc": active_desc, "preloaded": ", ".join(sorted(preloaded)), "key": "", "where": "",
           "close": "", "val": "", "opts": "", "type": "", "attr": "", "poll_cause": "", "poll_fix": ""}
    op = None
    m_op = re.search(r"(bpy\.ops\.[a-z_]+\.[a-z_]+)", tb_text + " " + msg)
    if m_op:
        op = m_op.group(1)
    for kind, pat, cause, fix in ERROR_TABLE:
        m = re.search(pat, head, re.S)
        if not m:
            continue
        fmt.update({k: v for k, v in m.groupdict().items() if v is not None})
        if kind == "name_not_found":
            mk = re.search(r'key "([^"]*)"', msg)
            key = mk.group(1) if mk else msg.strip("'\"")
            fmt["key"] = key
            pool = _all_names()
            near = list(dict.fromkeys(difflib.get_close_matches(key, pool, n=6, cutoff=0.55)))[:4]
            fmt["where"] = "in bpy.data or on that node"
            fmt["close"] = (f"Close names: {near}. " if near else "") + _SOCKET_RENAMES
        elif kind == "api_attribute":
            fmt["close"] = _attr_suggestion(fmt["type"], fmt["attr"])
        elif kind == "bpy_operator_wrong_mode":
            mode = state.get("mode", "OBJECT")
            if act is not None and act["type"] != "MESH" and op and re.match(_MESH_OPS, op):
                # the canonical one: light_add/camera_add left a non-mesh active, then a mesh operator polls false
                c_kind, _p, c_cause, c_fix = ERROR_TABLE[0]
                return {"kind": c_kind, "message": msg[:600], "python_type": etype, "op": op,
                        "likely_cause": f"{op} needs an active MESH; " + c_cause.format(**fmt)
                        + " (adding a light or camera makes it active)",
                        "suggested_fix": c_fix.format(**fmt)}
            if act is None:
                fmt["poll_cause"] = f"{op or 'the operator'} needs an active object and there is none"
                fmt["poll_fix"] = "bpy.context.view_layer.objects.active = bpy.data.objects['<name>'] then retry"
            elif op and (op.startswith("bpy.ops.mesh.") or op.startswith("bpy.ops.uv.")) and mode != "EDIT":
                fmt["poll_cause"] = f"{op} only runs in EDIT mode; mode is {mode}"
                fmt["poll_fix"] = "bpy.ops.object.mode_set(mode='EDIT'), run it, then mode_set(mode='OBJECT'); or use bmesh, which needs no mode"
            elif op and op.startswith("bpy.ops.graph."):
                fmt["poll_cause"] = f"{op} needs a Graph Editor context and selected keys"
                fmt["poll_fix"] = "forgekit.anim.select_for_graph(obj) then forgekit.anim.graph_op(obj, ...)"
            else:
                fmt["poll_cause"] = (f"{op or 'the operator'} poll failed: active is {active_desc}, mode {mode}; "
                                     "operators read the active object, mode and editor area")
                fmt["poll_fix"] = ("set the active object and mode explicitly, or wrap in bpy.context.temp_override(...); "
                                   "prefer bpy.data / bmesh APIs that do not depend on context")
        return {"kind": kind, "message": msg[:600], "python_type": etype, "op": op,
                "likely_cause": cause.format(**fmt), "suggested_fix": fix.format(**fmt)}
    return {"kind": "unclassified", "message": msg[:600], "python_type": etype, "op": op,
            "likely_cause": "not in the classified-failure table; read the traceback's last frame",
            "suggested_fix": "inspect state_at_failure; run forge_scene to confirm names and the active object"}


def _all_names():
    import bpy
    names = []
    for coll in ("objects", "meshes", "materials", "images", "node_groups", "cameras", "lights", "collections", "actions"):
        names += [d.name for d in getattr(bpy.data, coll)]
    return names


def _attr_suggestion(type_name, attr):
    import bpy
    t = getattr(bpy.types, type_name, None)
    if t is None:
        return "check dir(obj) for the attribute; the Blender 5.3 API differs from pre-2024 tutorials"
    props = list(t.bl_rna.properties.keys()) + [n for n in dir(t) if not n.startswith("_")]
    near = difflib.get_close_matches(attr, props, n=5, cutoff=0.5)
    return (f"close attributes on {type_name}: {near}" if near else
            f"{type_name} has no similar attribute; check {type_name}.bl_rna.properties.keys()")


# =====================================================================================
# worker: one per session, owns bpy
# =====================================================================================
class Worker:
    PRELOAD = ("mesh", "materials", "render", "inspect", "checkpoint", "bake", "detail", "compare",
               "rig", "anim", "ornament", "shading", "dynamics", "face", "anatomy", "fit", "formq", "hand")

    def __init__(self, session_dir, restore=None):
        from forgekit import env
        env.prepare()
        import bpy
        self.bpy = bpy
        self.dir = session_dir
        for sub in ("checkpoints", "auto", "renders", "exports"):
            os.makedirs(os.path.join(session_dir, sub), exist_ok=True)
        if restore and os.path.exists(restore):
            bpy.ops.wm.open_mainfile(filepath=restore)
            self.restored_from = restore
        else:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            self.restored_from = None
        self.auto_n = 0
        self.ns = {"bpy": bpy, "__name__": "forge_session"}
        self.module_status = {}
        import importlib
        for name in self.PRELOAD:
            try:
                self.ns[name] = importlib.import_module(f"forgekit.{name}")
                self.module_status[name] = "ok"
            except ImportError as e:
                self.module_status[name] = f"missing ({e.name or e})"
            except Exception as e:  # a parallel agent's half-written module must not kill the session
                self.module_status[name] = f"error: {type(e).__name__}: {str(e)[:120]}"
        import mathutils, bmesh, math
        self.ns.update(mathutils=mathutils, bmesh=bmesh, math=math, Vector=mathutils.Vector)
        from forgekit import checkpoint as CK
        self.CK = CK

    # ------------------------------------------------------------- ops
    def op_hello(self):
        bpy = self.bpy
        return {"blender": bpy.app.version_string, "python": sys.version.split()[0], "pid": os.getpid(),
                "session_dir": self.dir, "modules": self.module_status, "restored_from": self.restored_from}

    def _auto_checkpoint(self):
        path = os.path.join(self.dir, "auto", f"auto_{self.auto_n % 20:02d}.blend")
        self.auto_n += 1
        self.bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        return path

    def _clear_stale_refs(self):
        bs = self.bpy.types.bpy_struct
        for k in [k for k, v in self.ns.items() if isinstance(v, bs)]:
            del self.ns[k]

    def op_exec(self, code, rollback_on_error=True):
        bpy = self.bpy
        t0 = time.time()
        auto = self._auto_checkpoint()
        out = io.StringIO()
        self.ns.pop("result", None)
        try:
            compiled = compile(code, "<forge_exec>", "exec")
            with contextlib.redirect_stdout(out):
                exec(compiled, self.ns)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            tb = traceback.format_exc()
            state = _state_at_failure()
            preloaded = [k for k in self.ns if not k.startswith("__")]
            err = classify_error(exc, tb, state, preloaded)
            frames = [l for l in tb.strip().splitlines() if l.strip()][-8:]
            err["traceback"] = frames
            err["state_at_failure"] = state
            err["checkpoint"] = auto
            rolled = False
            if rollback_on_error and not isinstance(exc, SyntaxError):
                bpy.ops.wm.open_mainfile(filepath=auto)
                self._clear_stale_refs()
                rolled = True
            err["rolled_back"] = rolled
            return {"ok": False, "error": err, "stdout": out.getvalue()[-3000:],
                    "seconds": round(time.time() - t0, 4), "recovery_point": auto}
        res = self.ns.pop("result", None)
        try:
            json.dumps(res)
        except (TypeError, ValueError):
            res = repr(res)[:2000]
        return {"ok": True, "stdout": out.getvalue()[-3000:], "result": res,
                "seconds": round(time.time() - t0, 4), "recovery_point": auto,
                "objects": len(bpy.context.scene.objects)}

    def op_scene(self, fields=None, limit=50, offset=0):
        from forgekit import inspect as INS
        s = INS.scene_summary(max_objects=10_000)
        fields = fields or ["name", "type", "loc", "dims", "faces"]
        objs = s["objects"]
        page = [{k: o[k] for k in fields if k in o} for o in objs[offset:offset + limit]]
        act = self.bpy.context.view_layer.objects.active
        return {"engine": s["engine"], "frame_range": s["frame_range"], "object_count": s["object_count"],
                "active": act.name if act else None, "fields": fields, "offset": offset, "limit": limit,
                "objects": page, "next_offset": offset + limit if offset + limit < len(objs) else None}

    def op_inspect(self, object_name):
        bpy = self.bpy
        ob = bpy.data.objects.get(object_name)
        if ob is None:
            near = difflib.get_close_matches(object_name, [o.name for o in bpy.data.objects], n=5, cutoff=0.4)
            return {"ok": False, "error": {"kind": "name_not_found", "message": f"no object '{object_name}'",
                                           "likely_cause": "wrong name or Blender .001 suffixing",
                                           "suggested_fix": f"close names: {near}" if near else "run forge_scene"}}
        if ob.type != "MESH":
            return {"ok": True, "name": ob.name, "type": ob.type,
                    "dimensions_m": tuple(round(v, 5) for v in ob.dimensions),
                    "note": "mesh_stats only applies to MESH objects"}
        from forgekit import inspect as INS
        st = INS.mesh_stats(ob)
        st["ok"] = True
        st["type"] = "MESH"
        return st

    # ------------------------------------------------------------- render
    def _view_camera(self, view, camera_location, target, lens=50.0):
        from mathutils import Vector
        from forgekit import inspect as INS
        bpy = self.bpy
        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.hide_render]
        b = INS.scene_bounds(meshes) if meshes else None
        centre = Vector(target) if target else (Vector(b["center"]) if b else Vector((0, 0, 0)))
        if view == "custom":
            if not camera_location:
                raise ValueError("view='custom' needs camera_location [x, y, z]")
            loc = Vector(camera_location)
        else:
            if b is None:
                raise ValueError("nothing to frame: the scene has no visible mesh; pass view='custom' with camera_location")
            radius = max(0.5 * Vector(b["size"]).length, 1e-3)
            half = math.atan(18.0 / lens)
            dist = radius / math.sin(half) * 1.08
            dirs = {"front": (0, -1, 0), "side": (1, 0, 0), "top": (0, -0.001, 1),
                    "persp": (0.62, -0.72, 0.34), "back": (0, 1, 0)}
            if view not in dirs:
                raise ValueError(f"view must be one of {sorted(dirs) + ['custom']}")
            loc = centre + Vector(dirs[view]).normalized() * dist
        cd = bpy.data.cameras.new("ForgeViewCam")
        cd.lens = lens
        cd.clip_start = max(1e-4, (loc - centre).length * 0.001)
        cd.clip_end = max(100.0, (loc - centre).length * 20)
        cam = bpy.data.objects.new("ForgeViewCam", cd)
        bpy.context.scene.collection.objects.link(cam)
        cam.location = loc
        cam.rotation_euler = (centre - loc).to_track_quat("-Z", "Y").to_euler()
        return cam, cd, b

    def _render(self, mode, view, w, h, camera_location=None, target=None, samples=16, transparent=False,
                out_path=None):
        bpy = self.bpy
        from forgekit import render as RN
        sc = bpy.context.scene
        sh = sc.display.shading
        saved = dict(camera=sc.camera, engine=sc.render.engine, rx=sc.render.resolution_x,
                     ry=sc.render.resolution_y, pct=sc.render.resolution_percentage,
                     film=sc.render.film_transparent, path=sc.render.filepath,
                     cmode=sc.render.image_settings.color_mode,
                     shading=(sh.light, sh.color_type, sh.show_object_outline, sh.show_cavity),
                     samples=sc.cycles.samples, denoise=sc.cycles.use_denoising,
                     vt=sc.view_settings.view_transform, look=sc.view_settings.look, world=sc.world)
        temp_objs, temp_data = [], []
        cam, cd, bounds = self._view_camera(view, camera_location, target)
        temp_objs.append(cam)
        temp_data.append(cd)
        try:
            sc.camera = cam
            if mode == "workbench":
                RN.workbench()
            elif mode == "cycles":
                sc.render.engine = "CYCLES"
                sc.cycles.device = "CPU"
                sc.cycles.samples = int(samples)
                sc.cycles.use_denoising = True
                sc.cycles.use_adaptive_sampling = True
                if not any(o.type == "LIGHT" for o in sc.objects):
                    size = max(bounds["size"]) if bounds else 1.0
                    k = max(size, 0.05) / 1.5
                    # three_point scales positions by k and energy by k; irradiance falls as 1/d^2,
                    # so energy needs k^2 to light every object size the same. 0.5 keeps a
                    # head-on 0.8-albedo face below clipping under AgX.
                    for L in RN.three_point(key=2600 * k * 0.5, fill=700 * k * 0.5, rim=1500 * k * 0.5, scale=k):
                        temp_objs.append(L)
                        temp_data.append(L.data)
                if sc.world is None:
                    RN.setup_world()
            else:
                raise ValueError("mode must be 'workbench' or 'cycles'")
            sc.render.resolution_x, sc.render.resolution_y = w, h
            sc.render.resolution_percentage = 100
            sc.render.film_transparent = transparent
            sc.render.image_settings.file_format = "PNG"
            sc.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
            out_path = out_path or os.path.join(self.dir, "renders", f"{mode}_{view}_{int(time.time()*1000)}.png")
            path, secs = RN.shot(out_path, verbose=False)
        finally:
            sc.camera = saved["camera"]
            sc.render.engine = saved["engine"]
            sc.render.resolution_x, sc.render.resolution_y = saved["rx"], saved["ry"]
            sc.render.resolution_percentage = saved["pct"]
            sc.render.film_transparent = saved["film"]
            sc.render.filepath = saved["path"]
            sc.render.image_settings.color_mode = saved["cmode"]
            sh.light, sh.color_type, sh.show_object_outline, sh.show_cavity = saved["shading"]
            sc.cycles.samples = saved["samples"]
            sc.cycles.use_denoising = saved["denoise"]
            sc.world = saved["world"]
            for o in temp_objs:
                bpy.data.objects.remove(o, do_unlink=True)
            for d in temp_data:
                if isinstance(d, bpy.types.Camera):
                    bpy.data.cameras.remove(d)
                elif isinstance(d, bpy.types.Light):
                    bpy.data.lights.remove(d)
        return path, secs

    def op_render(self, mode="workbench", view="persp", width=DEFAULT_PX, height=DEFAULT_PX,
                  camera_location=None, target=None, samples=16):
        path, secs = self._render(mode, view, width, height, camera_location, target, samples)
        return {"ok": True, "path": path, "seconds": round(secs, 3), "width": width, "height": height,
                "mode": mode, "view": view, "image_tokens_est": image_tokens(width, height)}

    def op_checkpoint(self, label):
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", label)[:40] or "ck"
        e = self.CK.checkpoint(os.path.join(self.dir, "checkpoints"), safe)
        return {"ok": True, **e, "recovery_point": e["path"]}

    def op_rollback(self, checkpoint_id):
        path = checkpoint_id
        if not os.path.exists(path):
            hits = [e for e in self.CK.history() if e["id"] == checkpoint_id]
            if not hits:
                ck_dir = os.path.join(self.dir, "checkpoints")
                cand = os.path.join(ck_dir, f"{checkpoint_id}.blend")
                if os.path.exists(cand):
                    path = cand
                else:
                    avail = sorted(f[:-6] for f in os.listdir(ck_dir) if f.endswith(".blend"))
                    return {"ok": False, "error": {"kind": "checkpoint_not_found",
                                                   "message": f"no checkpoint '{checkpoint_id}'",
                                                   "likely_cause": "wrong id",
                                                   "suggested_fix": f"available: {avail}"}}
            else:
                path = hits[0]["path"]
        t = time.time()
        self.bpy.ops.wm.open_mainfile(filepath=path)
        self._clear_stale_refs()
        sc = self.bpy.context.scene
        return {"ok": True, "checkpoint": checkpoint_id, "path": path, "seconds": round(time.time() - t, 4),
                "objects": [o.name for o in sc.objects][:50], "object_count": len(sc.objects),
                "note": "bpy references held in the forge_exec namespace were cleared; re-fetch by name",
                "recovery_point": path}

    def op_compare(self, reference_path, view="front", camera_location=None, target=None, grid=(4, 4)):
        from forgekit import compare as CMP
        if not os.path.exists(reference_path):
            return {"ok": False, "error": {"kind": "file_not_found", "message": f"no file {reference_path}",
                                           "likely_cause": "path does not exist on the server machine",
                                           "suggested_fix": "pass an absolute path readable by the server"}}
        ref = CMP.load_image(reference_path)
        h, w = ref.shape[:2]
        scale = min(1.0, WORKBENCH_MAX_PX / max(w, h))
        rw, rh = max(16, int(w * scale)), max(16, int(h * scale))
        path, secs = self._render("workbench", view, rw, rh, camera_location, target, transparent=True,
                                     out_path=os.path.join(self.dir, "renders", f"compare_{view}_{int(time.time()*1000)}.png"))
        res = CMP.compare_renders(reference_path, path)
        loc = CMP.localise_error(reference_path, path, grid=tuple(grid))
        return {"ok": True, "candidate_path": path, "render_seconds": round(secs, 3), "compare": res,
                "worst_cells": loc["worst"], "grid": loc["grid"], "width": rw, "height": rh}

    def op_export(self, path, format="glb"):
        bpy = self.bpy
        fmt = format.lower()
        if not os.path.isabs(path):
            path = os.path.join(self.dir, "exports", path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        objs = [o for o in bpy.context.scene.objects if o.type in ("MESH", "ARMATURE") and not o.hide_render]
        if not objs:
            return {"ok": False, "error": {"kind": "nothing_to_export", "message": "no mesh or armature in scene",
                                           "likely_cause": "empty scene", "suggested_fix": "build something first"}}
        for o in bpy.context.view_layer.objects:
            o.select_set(o in objs)
        bpy.context.view_layer.objects.active = objs[0]
        has_rig = any(o.type == "ARMATURE" for o in objs)
        t = time.time()
        if fmt == "glb":
            kw = dict(filepath=path, export_format="GLB", use_selection=True, export_apply=True)
            if has_rig:
                kw.update(export_skins=True, export_def_bones=True, export_influence_nb=4, export_force_sampling=True)
            bpy.ops.export_scene.gltf(**kw)
        elif fmt == "fbx":
            bpy.ops.export_scene.fbx(filepath=path, use_selection=True, add_leaf_bones=False,
                                     bake_anim_use_nla_strips=False, bake_anim_use_all_actions=False)
        else:
            return {"ok": False, "error": {"kind": "bad_format", "message": f"format {format!r}",
                                           "likely_cause": "unsupported", "suggested_fix": "format='glb' or 'fbx'"}}
        out = {"ok": True, "path": path, "format": fmt, "bytes": os.path.getsize(path),
               "seconds": round(time.time() - t, 3), "objects": [o.name for o in objs]}
        if fmt == "glb":
            from forgekit import bake as BK
            g = BK.read_glb_json(path)
            rep = BK.glb_texture_report(path)
            out["glb"] = {"meshes": len(g.get("meshes", [])), "materials": len(g.get("materials", [])),
                          "images": len(g.get("images", [])), "skins": len(g.get("skins", [])),
                          "animations": len(g.get("animations", [])),
                          "textured_materials": [m["name"] for m in rep["materials"] if m["baseColorTexture"]]}
        return out

    def op_save(self):
        return {"ok": True, "recovery_point": self._auto_checkpoint()}

    # ------------------------------------------------------------- loop
    def serve(self, sock):
        f = sock.makefile("rwb", buffering=0)
        while True:
            line = f.readline()
            if not line:
                return
            req = json.loads(line)
            try:
                fn = getattr(self, "op_" + req["op"])
                res = fn(**req.get("args", {}))
                if isinstance(res, dict) and "ok" not in res:
                    res["ok"] = True
            except Exception as exc:
                tb = traceback.format_exc()
                try:
                    state = _state_at_failure()
                except Exception:
                    state = {}
                err = classify_error(exc, tb, state)
                err["traceback"] = [l for l in tb.strip().splitlines() if l.strip()][-6:]
                err["state_at_failure"] = state
                res = {"ok": False, "error": err}
            f.write((json.dumps(res, default=str) + "\n").encode())


def worker_main(fd, session_dir, restore):
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[forge-worker] %(message)s")
    sock = socket.socket(fileno=fd)
    w = Worker(session_dir, restore)
    w.serve(sock)


# =====================================================================================
# server side: session registry, supervision
# =====================================================================================
class WorkerDied(RuntimeError):
    pass


class Session:
    def __init__(self, sid, directory):
        self.id = sid
        self.dir = directory
        self.proc = None
        self.sock = None
        self.file = None
        self.lock = threading.Lock()
        self.last_used = time.time()
        self.recovery_point = None
        self.hello = None
        self.events = []
        self._load_meta()

    # persistent pointer so a session survives server restarts and reaping
    def _meta_path(self):
        return os.path.join(self.dir, "session.json")

    def _load_meta(self):
        try:
            with open(self._meta_path()) as f:
                self.recovery_point = json.load(f).get("recovery_point")
        except (OSError, ValueError):
            pass

    def _save_meta(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(self._meta_path(), "w") as f:
            json.dump({"id": self.id, "recovery_point": self.recovery_point, "updated": time.time()}, f)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def spawn(self):
        a, b = socket.socketpair()
        cmd = [sys.executable, "-m", "forgekit.server", "--worker", str(b.fileno()), "--dir", self.dir]
        restore = self.recovery_point if self.recovery_point and os.path.exists(self.recovery_point) else None
        if restore:
            cmd += ["--restore", restore]
        env = dict(os.environ)
        env["PYTHONPATH"] = KIT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        t = time.time()
        # stdout=stderr: Blender prints to fd 1; it must never reach the MCP stdout stream
        self.proc = subprocess.Popen(cmd, pass_fds=(b.fileno(),), stdin=subprocess.DEVNULL,
                                     stdout=sys.stderr, stderr=sys.stderr, cwd=KIT, env=env)
        b.close()
        self.sock = a
        self.file = a.makefile("rwb", buffering=0)
        self.hello = self._call_raw("hello", {}, timeout=180)
        self.hello["startup_seconds"] = round(time.time() - t, 3)
        log.info("session %s worker pid %s up in %.2fs (restore=%s)", self.id, self.proc.pid,
                 time.time() - t, restore)
        return restore

    def _call_raw(self, op, args, timeout):
        self.sock.settimeout(timeout)
        try:
            self.file.write((json.dumps({"op": op, "args": args}) + "\n").encode())
            line = self.file.readline()
        except socket.timeout:
            raise TimeoutError(f"{op} exceeded {timeout}s")
        except OSError as e:
            raise WorkerDied(str(e))
        if not line:
            raise WorkerDied("worker closed the socket")
        return json.loads(line)

    def kill(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.kill()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
        for h in (self.file, self.sock):
            try:
                h and h.close()
            except OSError:
                pass
        self.proc = self.sock = self.file = None

    def call(self, op, args=None, timeout=300):
        """Run op in the worker. Spawns/rehydrates as needed; on crash or timeout,
        respawns from the newest checkpoint and returns a structured error."""
        args = args or {}
        with self.lock:
            self.last_used = time.time()
            rehydrated = None
            if not self.alive():
                rehydrated = self.spawn() or "fresh"
            try:
                res = self._call_raw(op, args, timeout)
            except (WorkerDied, TimeoutError) as e:
                code = self.proc.poll() if self.proc else None
                timed_out = isinstance(e, TimeoutError)
                self.kill()
                recovered = None
                try:
                    recovered = self.spawn()
                except Exception as e2:
                    log.error("respawn failed: %s", e2)
                kind = "timeout" if timed_out else "worker_crashed"
                cause = (f"{op} ran longer than {timeout}s; the worker was killed" if timed_out else
                         f"the bpy worker died during {op} (exit code {code}; negative = signal, -11 = segfault)")
                return {"ok": False, "error": {
                    "kind": kind, "message": str(e), "op": op, "likely_cause": cause,
                    "suggested_fix": ("the scene was restored to the recovery point below; that operation is "
                                      "dangerous on this geometry - checkpoint first and try a smaller step "
                                      "(check inspect.mesh_stats for non-manifold input before booleans/remesh)"),
                    "recovered_to": recovered, "rolled_back": bool(recovered)}}
            if isinstance(res, dict) and res.get("recovery_point"):
                self.recovery_point = res["recovery_point"]
                self._save_meta()
            if rehydrated and isinstance(res, dict):
                res["rehydrated_from"] = rehydrated
            self.last_used = time.time()
            return res

    def reap(self):
        """Save and stop the worker. The next call rehydrates from the save."""
        with self.lock:
            if not self.alive():
                return False
            try:
                r = self._call_raw("save", {}, timeout=60)
                self.recovery_point = r.get("recovery_point", self.recovery_point)
                self._save_meta()
            except Exception as e:
                log.warning("save before reap failed for %s: %s", self.id, e)
            self.kill()
            return True


class Registry:
    def __init__(self, root=SESSION_ROOT):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.sessions = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._reaper, daemon=True).start()

    def open(self, label=""):
        sid = "fs_" + uuid.uuid4().hex[:12]
        s = Session(sid, os.path.join(self.root, sid))
        with self.lock:
            self.sessions[sid] = s
        t = time.time()
        with s.lock:
            s.spawn()
        s._save_meta()
        return s, time.time() - t

    def get(self, sid):
        if not isinstance(sid, str) or not re.fullmatch(r"fs_[0-9a-f]{12}", sid):
            raise KeyError(sid)
        with self.lock:
            s = self.sessions.get(sid)
            if s is None:
                d = os.path.join(self.root, sid)
                if not os.path.isdir(d):
                    raise KeyError(sid)
                s = self.sessions[sid] = Session(sid, d)  # rehydrates lazily from session.json
            return s

    def close(self, sid):
        with self.lock:
            s = self.sessions.pop(sid, None)
        if s:
            with s.lock:
                s.kill()
        return s is not None

    def _reaper(self):
        while not self._stop.wait(max(0.5, min(30.0, IDLE_S / 2))):
            now = time.time()
            for s in list(self.sessions.values()):
                if s.alive() and now - s.last_used > IDLE_S:
                    log.info("reaping idle session %s", s.id)
                    s.reap()

    def shutdown(self):
        self._stop.set()
        for s in list(self.sessions.values()):
            s.kill()


# =====================================================================================
# MCP surface
# =====================================================================================
def build_server(registry=None):
    from mcp.server import MCPServer
    from mcp.types import CallToolResult, TextContent, ImageContent, ToolAnnotations
    import anyio

    reg = registry or Registry()
    app = MCPServer(
        "forge",
        version="0.1.0",
        instructions=("forge-kit over MCP: headless Blender 5.3 (no GPU). Call forge_session_open first and pass "
                      "its session_id to every other tool. Check numbers (forge_scene, forge_inspect) before "
                      "rendering; Workbench renders are ~0.6 s, Cycles seconds. forge_exec runs Python with bpy and "
                      "forge-kit preloaded (mesh, materials, render, inspect, checkpoint, bake, detail, compare); "
                      "assign `result = ...` to return a value. Failures roll back and come back classified."),
    )

    def ok(summary, data, images=()):
        content = [TextContent(type="text", text=summary)]
        for png in images:
            content.append(ImageContent(type="image", data=png, mime_type="image/png"))
        return CallToolResult(content=content, structured_content=data)

    def fail(summary, data):
        return CallToolResult(content=[TextContent(type="text", text=summary)], structured_content=data, is_error=True)

    def err_summary(res):
        e = res.get("error", {})
        return (f"FAILED [{e.get('kind')}] {e.get('message', '')[:200]}\n"
                f"likely cause: {e.get('likely_cause')}\nsuggested fix: {e.get('suggested_fix')}"
                + ("\n(scene rolled back to the pre-call checkpoint)" if e.get("rolled_back") else ""))

    async def run(session_id, op, args=None, timeout=300):
        try:
            s = reg.get(session_id)
        except KeyError:
            return None, fail(f"unknown session_id {session_id!r}; call forge_session_open",
                              {"ok": False, "error": {"kind": "unknown_session", "message": str(session_id),
                                                      "likely_cause": "handle not minted by this server or deleted",
                                                      "suggested_fix": "forge_session_open() and use the returned session_id"}})
        res = await anyio.to_thread.run_sync(lambda: s.call(op, args, timeout))
        if not res.get("ok", False):
            return None, fail(err_summary(res), res)
        return res, None

    def png_b64(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    RO = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

    @app.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    async def forge_session_open(label: str = "") -> CallToolResult:
        """Start a bpy session (one Blender worker process). Returns session_id: pass it to every other forge tool."""
        s, dt = await anyio.to_thread.run_sync(lambda: reg.open(label))
        h = s.hello
        missing = {k: v for k, v in h["modules"].items() if v != "ok"}
        data = {"ok": True, "session_id": s.id, "label": label, "blender": h["blender"], "python": h["python"],
                "session_dir": s.dir, "startup_seconds": round(dt, 3), "modules": h["modules"],
                "image_caps": {"workbench_max_px": WORKBENCH_MAX_PX, "cycles_max_px": CYCLES_MAX_PX,
                               "token_cap": IMAGE_TOKEN_CAP}}
        return ok(f"session {s.id} open (Blender {h['blender']}, {dt:.2f}s)."
                  + (f" Optional modules unavailable: {sorted(missing)}" if missing else ""), data)

    @app.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
    async def forge_session_close(session_id: str) -> CallToolResult:
        """Stop a session's worker and forget the handle. Files in its session_dir stay on disk."""
        closed = await anyio.to_thread.run_sync(lambda: reg.close(session_id))
        return ok(f"session {session_id} {'closed' if closed else 'was not open'}", {"ok": True, "closed": closed})

    @app.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
    async def forge_exec(session_id: str, code: str, rollback_on_error: bool = True,
                         timeout_s: float = 120.0) -> CallToolResult:
        """Run Python in the session's bpy. bpy, bmesh, mathutils and forge-kit modules are preloaded.
        Assign `result = <json-able>` to return a value. A checkpoint is taken first; on an exception the error
        is classified (state_at_failure, likely_cause, suggested_fix) and the scene rolled back."""
        res, f = await run(session_id, "exec", {"code": code, "rollback_on_error": rollback_on_error},
                           timeout=timeout_s)
        if f:
            return f
        out = res.get("stdout", "").strip()
        summ = f"ok in {res['seconds']}s, {res.get('objects')} objects."
        if res.get("result") is not None:
            summ += f" result={json.dumps(res['result'])[:500]}"
        if out:
            summ += f"\nstdout:\n{out[-1500:]}"
        return ok(summ, res)

    @app.tool(annotations=RO)
    async def forge_scene(session_id: str, fields: list[str] | None = None, limit: int = 50,
                          offset: int = 0) -> CallToolResult:
        """Compact scene summary, field-projected and paginated. Never geometry.
        fields from: name, type, loc, dims, faces, modifiers, materials, bones (default name,type,loc,dims,faces)."""
        res, f = await run(session_id, "scene", {"fields": fields, "limit": max(1, min(limit, 500)),
                                                 "offset": max(0, offset)})
        if f:
            return f
        lines = [f"{o.get('name')} {o.get('type', '')} " + " ".join(f"{k}={o[k]}" for k in res["fields"]
                                                                      if k not in ("name", "type") and k in o)
                 for o in res["objects"]]
        head = f"{res['object_count']} objects, engine {res['engine']}, active {res['active']}"
        return ok(head + ("\n" + "\n".join(lines) if lines else ""), res)

    @app.tool(annotations=RO)
    async def forge_inspect(session_id: str, object_name: str) -> CallToolResult:
        """Topology numbers for one object (forgekit.inspect.mesh_stats): faces, manifoldness, euler, genus,
        volume, area, dimensions. Use before rendering."""
        res, f = await run(session_id, "inspect", {"object_name": object_name})
        if f:
            return f
        if res.get("type") != "MESH":
            return ok(f"{res['name']} is {res['type']}, dims {res['dimensions_m']}", res)
        return ok(f"{res['name']}: {res['faces']} faces, watertight={res['watertight']}, "
                  f"non-manifold edges={res['non_manifold_edges']}, genus={res['genus']}, "
                  f"volume={res['volume_m3']} m3, dims={res['dimensions_m']}", res)

    @app.tool(annotations=RO)
    async def forge_render(session_id: str, mode: str = "workbench", view: str = "persp",
                           resolution: int = DEFAULT_PX, aspect: float = 1.0,
                           camera_location: list[float] | None = None, target: list[float] | None = None,
                           samples: int = 16) -> CallToolResult:
        """Render the scene and return a PNG image block. mode: workbench (structure, ~0.6 s) | cycles (materials).
        view: front | side | top | back | persp | custom (custom needs camera_location; target defaults to the
        scene centre). resolution = long side in px, capped at 1000 (workbench) / 640 (cycles) and ~2500 image
        tokens. The scene's own camera and settings are restored afterwards."""
        if mode not in ("workbench", "cycles"):
            return fail("mode must be 'workbench' or 'cycles' (EEVEE is not offered: 12-30x slower than Cycles "
                        "without a GPU)", {"ok": False, "error": {"kind": "bad_argument", "message": mode}})
        w, h = capped_size(resolution, aspect, mode)
        res, f = await run(session_id, "render", {"mode": mode, "view": view, "width": w, "height": h,
                                                  "camera_location": camera_location, "target": target,
                                                  "samples": samples})
        if f:
            return f
        b64 = png_b64(res["path"])
        res["capped"] = resolution > max(w, h)
        return ok(f"{mode} {view} {w}x{h} in {res['seconds']}s (~{res['image_tokens_est']} image tokens). "
                  f"file: {res['path']}", res, images=[b64])

    @app.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    async def forge_checkpoint(session_id: str, label: str) -> CallToolResult:
        """Save a named snapshot of the session (~10 ms). Returns checkpoint_id for forge_rollback."""
        res, f = await run(session_id, "checkpoint", {"label": label})
        if f:
            return f
        return ok(f"checkpoint {res['id']} ({res['bytes']} bytes, {res['seconds']}s, {res['objects']} objects)",
                  {"ok": True, "checkpoint_id": res["id"], **res})

    @app.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
    async def forge_rollback(session_id: str, checkpoint_id: str) -> CallToolResult:
        """Reload a checkpoint. Everything since is discarded; bpy references in the exec namespace are cleared."""
        res, f = await run(session_id, "rollback", {"checkpoint_id": checkpoint_id})
        if f:
            return f
        return ok(f"rolled back to {checkpoint_id} in {res['seconds']}s: {res['object_count']} objects "
                  f"{res['objects'][:12]}", res)

    @app.tool(annotations=RO)
    async def forge_compare(session_id: str, reference_path: str, view: str = "front",
                            camera_location: list[float] | None = None, target: list[float] | None = None,
                            grid_rows: int = 4, grid_cols: int = 4) -> CallToolResult:
        """Render the scene from `view` at the reference image's aspect and compare: silhouette IoU,
        value-structure delta, edge overlap, and the grid cells where it disagrees most. Returns the candidate
        render as an image block (<=512 px) so you can see what was compared."""
        res, f = await run(session_id, "compare", {"reference_path": reference_path, "view": view,
                                                   "camera_location": camera_location, "target": target,
                                                   "grid": [max(1, grid_rows), max(1, grid_cols)]})
        if f:
            return f
        c = res["compare"]
        worst = res["worst_cells"][:3]
        summ = (f"silhouette IoU {c['silhouette_iou']} (extra {c['silhouette']['extra_frac']}, missing "
                f"{c['silhouette']['missing_frac']}, centroid shift {c['silhouette']['centroid_shift_px']} px); "
                f"value EMD {c['value']['hist_emd']}, band delta s/m/h {c['value']['band_delta']}; "
                f"edge F1 {c['edges']['f1']}.\nworst cells: "
                + "; ".join(f"r{w['row']}c{w['col']} {w['kind']} {w['score']} - {w['hint']}" for w in worst))
        thumb = _thumbnail_b64(res["candidate_path"], 512)
        return ok(summ, res, images=[thumb])

    @app.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    async def forge_export(session_id: str, path: str, format: str = "glb") -> CallToolResult:
        """Export visible meshes (and armatures) to glb or fbx. Relative paths land in session_dir/exports.
        GLB exports are re-read and summarised (meshes, materials, textured materials, skins)."""
        res, f = await run(session_id, "export", {"path": path, "format": format})
        if f:
            return f
        extra = f", glb: {res['glb']}" if "glb" in res else ""
        return ok(f"exported {res['format']} {res['bytes']} bytes to {res['path']} in {res['seconds']}s{extra}", res)

    app._forge_registry = reg
    return app


def _thumbnail_b64(path, max_px):
    """Downscale a PNG for an image block (no Pillow needed; Pillow used if present)."""
    try:
        from PIL import Image
        im = Image.open(path)
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        from forgekit import compare as CMP
        import numpy as np
        a = CMP.load_image(path)
        h, w = a.shape[:2]
        s = min(1.0, max_px / max(h, w))
        a = CMP._resize(a, max(1, int(h * s)), max(1, int(w * s)))
        tmp = path[:-4] + "_thumb.png"
        CMP.write_png(tmp, a)
        with open(tmp, "rb") as f:
            return base64.b64encode(f.read()).decode()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--worker" in argv:
        fd = int(argv[argv.index("--worker") + 1])
        d = argv[argv.index("--dir") + 1]
        restore = argv[argv.index("--restore") + 1] if "--restore" in argv else None
        worker_main(fd, d, restore)
        return
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[forge] %(asctime)s %(message)s")
    app = build_server()
    try:
        app.run("stdio")
    finally:
        app._forge_registry.shutdown()


if __name__ == "__main__":
    main()
