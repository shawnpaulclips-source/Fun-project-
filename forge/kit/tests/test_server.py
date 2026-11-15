"""Acceptance test for forgekit.server: a real MCP round trip over stdio.

Launches `python -m forgekit.server` as a subprocess through the mcp SDK's own
stdio client, then: list tools, open a session, exec a cube, scene, inspect,
render (image block), checkpoint, mutate, rollback, a classified failure with
automatic rollback, compare, export, cycles render, unknown-session error,
close. Every response is checked and every call timed. A second, raw JSON-RPC
pass checks that every byte the server writes to stdout is protocol JSON.

Run:  /home/user/bpyenv/bin/python tests/test_server.py
"""
import os, sys, time, json, base64, struct, asyncio, tempfile, subprocess

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from mcp import Client, StdioServerParameters

FAILS = []
LAT = []
SESS_DIR = tempfile.mkdtemp(prefix="forge_test_sessions_")


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def text_of(r):
    return " ".join(c.text for c in r.content if getattr(c, "type", "") == "text")


def images_of(r):
    return [c for c in r.content if getattr(c, "type", "") == "image"]


def png_size(b64):
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", raw[16:24])


def server_params():
    env = dict(os.environ)
    env["PYTHONPATH"] = KIT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["FORGE_SESSION_DIR"] = SESS_DIR
    return StdioServerParameters(command=sys.executable, args=["-m", "forgekit.server"], env=env, cwd=KIT)


async def timed(client, name, args=None, label=None):
    t = time.perf_counter()
    r = await client.call_tool(name, args or {})
    dt = time.perf_counter() - t
    LAT.append((label or name, dt))
    return r, dt


async def main():
    t0 = time.perf_counter()
    async with Client(server_params(), read_timeout_seconds=600) as c:
        LAT.append(("connect (spawn server + discover/initialize)", time.perf_counter() - t0))
        t = time.perf_counter()
        tools = await c.list_tools()
        LAT.append(("tools/list", time.perf_counter() - t))
        names = [x.name for x in tools.tools]
        want = ["forge_session_open", "forge_exec", "forge_scene", "forge_inspect", "forge_render",
                "forge_checkpoint", "forge_rollback", "forge_compare", "forge_export"]
        check("tools/list exposes every forge tool", all(w in names for w in want), f"{names}")
        ro = {x.name: x.annotations.read_only_hint for x in tools.tools if x.annotations}
        check("read-only hints on scene/inspect/render", ro.get("forge_scene") and ro.get("forge_render")
              and not ro.get("forge_exec"), f"{ro}")

        r, dt = await timed(c, "forge_session_open", {"label": "acceptance"})
        sc = r.structured_content
        sid = sc["session_id"]
        check("forge_session_open returns a handle", not r.is_error and sid.startswith("fs_"),
              f"{sid} blender={sc['blender']} worker startup={sc['startup_seconds']}s modules={sc['modules']}")

        r, dt = await timed(c, "forge_exec", {"session_id": sid, "code":
                            "bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0.5))\n"
                            "print('made', bpy.context.active_object.name)\n"
                            "result = {'name': bpy.context.active_object.name, 'verts': len(bpy.context.active_object.data.vertices)}"},
                            "forge_exec (create cube)")
        check("forge_exec creates a cube", not r.is_error and r.structured_content["result"] == {"name": "Cube", "verts": 8}
              and "made Cube" in r.structured_content["stdout"], text_of(r)[:120])

        r, dt = await timed(c, "forge_scene", {"session_id": sid})
        objs = [o["name"] for o in r.structured_content["objects"]]
        check("forge_scene lists the cube, no geometry", objs == ["Cube"] and "verts" not in json.dumps(r.structured_content),
              text_of(r)[:160])

        r, dt = await timed(c, "forge_inspect", {"session_id": sid, "object_name": "Cube"})
        st = r.structured_content
        check("forge_inspect returns mesh_stats", st["faces"] == 6 and st["watertight"] and st["genus"] == 0
              and abs(st["volume_m3"] - 1.0) < 1e-6, text_of(r))

        r, dt = await timed(c, "forge_render", {"session_id": sid, "mode": "workbench", "view": "persp",
                                                "resolution": 768}, "forge_render (workbench 768)")
        imgs = images_of(r)
        ok_img = len(imgs) == 1 and imgs[0].mime_type == "image/png"
        w, h = png_size(imgs[0].data) if ok_img else (0, 0)
        check("forge_render returns one PNG image block", ok_img and (w, h) == (768, 768), f"{w}x{h} {text_of(r)}")
        ref_path = r.structured_content["path"]

        r, dt = await timed(c, "forge_render", {"session_id": sid, "resolution": 4000},
                            "forge_render (request 4000 px)")
        w2, h2 = png_size(images_of(r)[0].data)
        check("forge_render caps resolution (workbench 1000 px, <=2500 tokens)", max(w2, h2) == 1000
              and w2 * h2 / 750 <= 2500, f"{w2}x{h2} ~{int(w2*h2/750)} tokens")

        r, dt = await timed(c, "forge_checkpoint", {"session_id": sid, "label": "cube"})
        ck = r.structured_content["checkpoint_id"]
        check("forge_checkpoint returns an id", not r.is_error and ck.endswith("cube"), text_of(r))

        r, dt = await timed(c, "forge_exec", {"session_id": sid, "code":
                            "bpy.data.objects.remove(bpy.data.objects['Cube'])\n"
                            "bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5)\n"
                            "result = sorted(o.name for o in bpy.context.scene.objects)"},
                            "forge_exec (replace cube by sphere)")
        check("mutation applied", r.structured_content.get("result") == ["Sphere"], text_of(r))

        r, dt = await timed(c, "forge_rollback", {"session_id": sid, "checkpoint_id": ck})
        check("forge_rollback restores the cube", not r.is_error and r.structured_content["objects"] == ["Cube"], text_of(r))
        r, _ = await timed(c, "forge_scene", {"session_id": sid}, "forge_scene (after rollback)")
        check("scene after rollback is the checkpoint", [o["name"] for o in r.structured_content["objects"]] == ["Cube"])

        # the canonical failure: adding a light leaves it active, then bake fails on it
        r, dt = await timed(c, "forge_exec", {"session_id": sid, "code":
                            "bpy.ops.object.light_add(type='SUN')\n"
                            "bpy.context.scene.render.engine = 'CYCLES'\n"
                            "bpy.ops.object.bake(type='NORMAL')"}, "forge_exec (classified failure)")
        e = r.structured_content.get("error", {})
        check("failure is an MCP tool error", r.is_error)
        check("failure classified with state_at_failure", e.get("kind") == "bpy_operator_context_active"
              and e.get("state_at_failure", {}).get("active_object", {}).get("type") == "LIGHT",
              f"kind={e.get('kind')} state={e.get('state_at_failure')}")
        check("likely_cause + suggested_fix present", bool(e.get("likely_cause")) and "active" in e.get("suggested_fix", ""),
              f"cause={e.get('likely_cause')!r}")
        r2, _ = await timed(c, "forge_scene", {"session_id": sid}, "forge_scene (after failure)")
        check("failed exec rolled back (no stray Sun)", e.get("rolled_back") and
              [o["name"] for o in r2.structured_content["objects"]] == ["Cube"], text_of(r2))

        r, dt = await timed(c, "forge_exec", {"session_id": sid, "code": "bpy.data.objects['Cueb']"},
                            "forge_exec (name typo)")
        e = r.structured_content.get("error", {})
        check("name typo -> name_not_found with close match", e.get("kind") == "name_not_found" and "Cube" in e.get("suggested_fix", ""),
              e.get("suggested_fix", "")[:100])

        # supervision: a segfault in bpy must cost one call, not the session
        r, dt = await timed(c, "forge_exec", {"session_id": sid, "code":
                            "bpy.ops.mesh.primitive_monkey_add()\nimport ctypes\nctypes.string_at(0)"},
                            "forge_exec (segfault -> respawn)")
        e = r.structured_content.get("error", {})
        check("worker segfault -> worker_crashed, respawned from pre-exec checkpoint",
              r.is_error and e.get("kind") == "worker_crashed" and e.get("rolled_back"),
              f"{e.get('likely_cause')} recovered_to={os.path.basename(str(e.get('recovered_to')))}")
        r, _ = await timed(c, "forge_scene", {"session_id": sid}, "forge_scene (after crash recovery)")
        check("session survives the crash with the pre-exec scene",
              [o["name"] for o in r.structured_content["objects"]] == ["Cube"], text_of(r)[:80])

        r, dt = await timed(c, "forge_exec", {"session_id": sid, "code": "import time\ntime.sleep(30)",
                                              "timeout_s": 2.0}, "forge_exec (timeout 2 s)")
        e = r.structured_content.get("error", {})
        check("runaway exec -> timeout error and recovery", r.is_error and e.get("kind") == "timeout"
              and e.get("rolled_back"), f"{dt:.2f}s")

        r, dt = await timed(c, "forge_compare", {"session_id": sid, "reference_path": ref_path, "view": "persp"})
        cmpr = r.structured_content.get("compare", {})
        check("forge_compare vs own render: IoU ~1", not r.is_error and cmpr.get("silhouette_iou", 0) > 0.97
              and len(images_of(r)) == 1, text_of(r)[:200])

        r, dt = await timed(c, "forge_export", {"session_id": sid, "path": "cube.glb", "format": "glb"})
        check("forge_export glb", not r.is_error and r.structured_content["bytes"] > 500
              and r.structured_content["glb"]["meshes"] == 1, text_of(r)[:160])

        r, dt = await timed(c, "forge_render", {"session_id": sid, "mode": "cycles", "view": "front",
                                                "resolution": 384, "samples": 8}, "forge_render (cycles 384 8spp)")
        check("forge_render cycles", not r.is_error and len(images_of(r)) == 1, text_of(r)[:120])

        r, dt = await timed(c, "forge_scene", {"session_id": "fs_000000000000"}, "forge_scene (unknown session)")
        check("unknown session -> structured error", r.is_error and r.structured_content["error"]["kind"] == "unknown_session")

        r, dt = await timed(c, "forge_session_close", {"session_id": sid})
        check("forge_session_close", not r.is_error and r.structured_content["closed"])


async def reap_check():
    """Idle reaping + rehydration: a reaped worker comes back with its scene."""
    p = server_params()
    p.env["FORGE_IDLE_S"] = "1"
    async with Client(p, read_timeout_seconds=300) as c:
        r = await c.call_tool("forge_session_open", {})
        sid = r.structured_content["session_id"]
        await c.call_tool("forge_exec", {"session_id": sid, "code": "bpy.ops.mesh.primitive_cylinder_add()"})
        await asyncio.sleep(3.0)
        t = time.perf_counter()
        r = await c.call_tool("forge_scene", {"session_id": sid})
        LAT.append(("forge_scene (rehydrate a reaped worker)", time.perf_counter() - t))
        sc = r.structured_content
        check("idle worker reaped and rehydrated with its scene", not r.is_error and sc.get("rehydrated_from")
              and [o["name"] for o in sc["objects"]] == ["Cylinder"], f"from {os.path.basename(str(sc.get('rehydrated_from')))}")


def raw_stdout_check():
    """Handshake-era JSON-RPC by hand: every stdout line must parse as JSON-RPC."""
    p = server_params()
    proc = subprocess.Popen([p.command, *p.args], cwd=p.cwd, env=p.env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "raw", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "forge_session_open", "arguments": {}}},
    ]
    out_lines = []
    for m in msgs:
        proc.stdin.write((json.dumps(m) + "\n").encode())
        proc.stdin.flush()
        if "id" in m:
            out_lines.append(proc.stdout.readline())
    sid = None
    try:
        sid = json.loads(out_lines[-1])["result"]["structuredContent"]["session_id"]
    except Exception:
        pass
    if sid:
        m = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "forge_render", "arguments": {
            "session_id": sid, "resolution": 256}}}
        proc.stdin.write((json.dumps(m) + "\n").encode())
        proc.stdin.flush()
        out_lines.append(proc.stdout.readline())
    proc.stdin.close()
    rest = proc.stdout.read()
    proc.wait(30)
    parsed = []
    for ln in out_lines + [l for l in rest.splitlines() if l.strip()]:
        try:
            parsed.append(json.loads(ln))
        except ValueError:
            parsed.append(None)
    clean = all(isinstance(x, dict) and x.get("jsonrpc") == "2.0" for x in parsed)
    check("server stdout carries only JSON-RPC (Blender noise goes to stderr)", clean and len(parsed) >= 3,
          f"{len(parsed)} lines, session {sid}, render ok={bool(parsed and parsed[-1] and 'result' in parsed[-1])}")


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(reap_check())
    raw_stdout_check()
    print("\nlatency per call (wall clock, client side):")
    for name, dt in LAT:
        print(f"  {name:48s} {dt*1000:9.1f} ms")
    print(f"{len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
