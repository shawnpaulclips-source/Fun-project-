"""Bake procedural materials to images so they survive glTF export.

Procedural shader trees (noise, pointiness, mix nodes) are not part of glTF.
The exporter walks back from the Principled BSDF looking for Image Texture
nodes; anything else collapses to a flat factor. So: unwrap, bake each PBR
channel to an image with Cycles CPU, rewire the material to those images, then
export and verify the file actually carries the textures.

    from forgekit import bake as BK
    BK.uv_unwrap(ob)                                  # smart, or xatlas if asked
    baked = BK.bake_material(ob, mat, resolution=1024)
    BK.rewire_to_images(mat, baked)
    BK.export_textured_glb(ob, None, "out/asset.glb") # re-imports and checks

Traps handled here (Blender 5.3):
  * A DIFFUSE/COLOR bake of a metal returns BLACK: Principled scales the diffuse
    colour by (1 - metallic). Base colour and metallic are baked by routing the
    socket that feeds the BSDF into an Emission shader and baking EMIT.
  * `bpy.ops.object.bake` bakes into the *active* Image Texture node of every
    material on the object, and fails with "No active image found" if one has
    none. The target node is added to every slot and set active.
  * Adding a light leaves it active and the bake fails with
    'Object "Sun" is not a mesh'. The object is made active and solely selected
    before every bake.
  * glTF wants metallic in B and roughness in G of ONE texture (occlusion may sit
    in R). Separate images make the exporter re-pack them; ORM is packed here
    instead, so the file layout is decided by us, not by the exporter.
"""
import os, time, json, struct, math, tempfile, subprocess, sys

import bpy
import numpy as np

CHANNELS = ("base_color", "roughness", "metallic", "normal", "ao")
_NON_COLOR = "Non-Color"


# ----------------------------------------------------------------- utilities
def _activate(obj):
    """Make obj the only selected object and the active one, in OBJECT mode."""
    vl = bpy.context.view_layer
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in vl.objects:
        if o.select_get():
            o.select_set(False)
    obj.hide_set(False)
    obj.hide_render = False
    obj.select_set(True)
    vl.objects.active = obj


def xatlas_available():
    try:
        import xatlas  # noqa: F401
        return True
    except Exception:
        return False


# ----------------------------------------------------------------- unwrap
def uv_unwrap(obj, method="smart", angle_limit_deg=66.0, island_margin=0.02,
              uv_name="UVMap", triangulate_on_conflict=True):
    """Unwrap obj. Returns a dict that says which unwrapper actually ran.

    method='smart'  : Blender Smart UV Project. ~15-20 ms on 7.5k faces. Not
                      guaranteed deterministic across Blender versions.
    method='xatlas' : xatlas charting + packing, deterministic for fixed input.
                      Falls back to smart if the `xatlas` module is not importable,
                      and the result says so (`fallback=True`).

    xatlas works on triangles. UVs are written back per loop through the
    triangulation; if xatlas cut a seam across a quad diagonal, the two
    triangles disagree on a shared loop and that loop keeps the first value.
    `loop_conflicts` counts those. With triangulate_on_conflict (default) the
    mesh is triangulated in place and re-charted so the count is 0 and the UVs
    are exact; the result says `triangulated=True`. glTF stores triangles
    anyway, so for an export target this costs nothing.
    """
    if obj.type != "MESH":
        raise TypeError(f"uv_unwrap needs a MESH, got {obj.type} ({obj.name})")
    me = obj.data
    want = method
    have_x = xatlas_available()
    ran = "xatlas" if (method == "xatlas" and have_x) else "smart"
    t = time.time()
    info = {"requested": want, "method": ran, "fallback": want == "xatlas" and not have_x,
            "xatlas_available": have_x, "faces": len(me.polygons)}

    if uv_name not in me.uv_layers:
        me.uv_layers.new(name=uv_name)
    uvl = me.uv_layers[uv_name]
    me.uv_layers.active = uvl

    if ran == "smart":
        _activate(obj)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(angle_limit_deg),
                                 island_margin=island_margin)
        bpy.ops.object.mode_set(mode="OBJECT")
        info["loop_conflicts"] = 0
    else:
        conflicts = _xatlas_unwrap(me, uvl, island_margin, info)
        if conflicts and triangulate_on_conflict:
            import bmesh
            bm = bmesh.new()
            bm.from_mesh(me)
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            bm.to_mesh(me)
            bm.free()
            uvl = me.uv_layers[uv_name]
            info["conflicts_before_triangulate"] = int(conflicts)
            conflicts = _xatlas_unwrap(me, uvl, island_margin, info)
            info["triangulated"] = True
            info["faces"] = len(me.polygons)
        info["loop_conflicts"] = int(conflicts)
    me.update()
    info["seconds"] = round(time.time() - t, 4)
    info["uv_layer"] = uv_name
    return info


def _xatlas_unwrap(me, uvl, island_margin, info):
    """Chart + pack with xatlas and write per-loop UVs. Returns loop conflicts."""
    import xatlas
    me.calc_loop_triangles()
    verts = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", verts)
    nt = len(me.loop_triangles)
    tri_v = np.empty(nt * 3, dtype=np.int32)
    tri_l = np.empty(nt * 3, dtype=np.int32)
    me.loop_triangles.foreach_get("vertices", tri_v)
    me.loop_triangles.foreach_get("loops", tri_l)
    atlas = xatlas.Atlas()
    atlas.add_mesh(verts.reshape(-1, 3), tri_v.reshape(-1, 3).astype(np.uint32))
    pack = xatlas.PackOptions()
    pack.padding = max(1, int(round(island_margin * 64)))
    atlas.generate(pack_options=pack)
    _vmap, idx, uvs = atlas.get_mesh(0)
    corner_uv = uvs[idx.reshape(-1)].astype(np.float32)
    loop_uv = np.zeros((len(me.loops), 2), dtype=np.float32)
    # fancy assignment keeps the LAST write; reversed, the first corner wins
    loop_uv[tri_l[::-1]] = corner_uv[::-1]
    conflicts = int((np.abs(loop_uv[tri_l] - corner_uv).max(axis=1) > 1e-6).sum())
    uvl.data.foreach_set("uv", loop_uv.reshape(-1))
    info["charts"] = int(atlas.chart_count)
    info["atlas_size"] = (int(atlas.width), int(atlas.height))
    return conflicts


# ----------------------------------------------------------------- bake
def _new_image(name, res, non_color):
    img = bpy.data.images.get(name)
    if img is not None:
        bpy.data.images.remove(img)
    # alpha=True so pixels the bake never touched keep alpha 0: that is the UV
    # coverage mask. Alpha is forced back to 1 before saving.
    img = bpy.data.images.new(name, width=res, height=res, alpha=True,
                              float_buffer=False, is_data=non_color)
    img.colorspace_settings.name = _NON_COLOR if non_color else "sRGB"
    return img


def _output_node(nt):
    out = nt.get_output_node("CYCLES") or nt.get_output_node("ALL")
    if out is None:
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        raise RuntimeError("material has no Material Output node")
    return out


def _principled(nt):
    b = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if b is None:
        raise RuntimeError("material has no Principled BSDF; nothing to bake channels from")
    return b


class _EmissionRoute:
    """Temporarily feed one Principled input into an Emission shader on the output.

    Baking EMIT then records exactly what drives that input - a linked procedural
    tree or the constant default - independent of lighting and of metallic.
    """

    def __init__(self, mat, input_name):
        self.mat, self.input_name = mat, input_name

    def __enter__(self):
        nt = self.mat.node_tree
        self.out = _output_node(nt)
        b = _principled(nt)
        sock = b.inputs[self.input_name]
        self.prev = [(l.from_node, l.from_socket) for l in self.out.inputs["Surface"].links]
        self.em = nt.nodes.new("ShaderNodeEmission")
        self.em.inputs["Strength"].default_value = 1.0
        if sock.is_linked:
            src = sock.links[0].from_socket
            nt.links.new(src, self.em.inputs["Color"])
        else:
            v = sock.default_value
            if hasattr(v, "__len__"):
                self.em.inputs["Color"].default_value = (v[0], v[1], v[2], 1.0)
            else:
                self.em.inputs["Color"].default_value = (v, v, v, 1.0)
        nt.links.new(self.em.outputs["Emission"], self.out.inputs["Surface"])
        return self

    def __exit__(self, *exc):
        nt = self.mat.node_tree
        nt.nodes.remove(self.em)
        for node, sock in self.prev:
            nt.links.new(sock, self.out.inputs["Surface"])
        return False


def _pixels(img):
    a = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(img.size[1], img.size[0], 4)


def bake_material(obj, material, channels=CHANNELS, resolution=1024, samples=8,
                  out_dir=None, margin=None, uv_method="smart"):
    """Bake material's channels on obj to images. Cycles CPU.

    Returns {"channels": {name: {"image", "path", "seconds", "mean"}}, "total_seconds",
             "resolution", "samples", "uv": unwrap info or None}.

    Channels: base_color (sRGB), roughness, metallic, normal (tangent space,
    OpenGL +Y as glTF expects), ao (all Non-Color). Images are saved as PNG in
    out_dir (a temp dir if None) so the glTF exporter can embed them.
    If obj has no UV layer it is unwrapped first with `uv_method`.
    """
    unknown = set(channels) - set(CHANNELS)
    if unknown:
        raise ValueError(f"unknown channels {sorted(unknown)}; valid: {CHANNELS}")
    if obj.type != "MESH":
        raise TypeError(f"bake_material needs a MESH, got {obj.type}")
    if material.name not in [m.name for m in obj.data.materials if m]:
        obj.data.materials.append(material)
    uv_info = None
    if not obj.data.uv_layers:
        uv_info = uv_unwrap(obj, method=uv_method)

    out_dir = out_dir or tempfile.mkdtemp(prefix="forge_bake_")
    os.makedirs(out_dir, exist_ok=True)
    sc = bpy.context.scene
    prev_engine = sc.render.engine
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    prev_samples = sc.cycles.samples
    sc.cycles.samples = samples
    margin = margin if margin is not None else max(4, resolution // 64)
    bk = sc.render.bake
    bk.margin = margin
    bk.use_clear = True
    bk.target = "IMAGE_TEXTURES"
    bk.use_selected_to_active = False

    mats = [m for m in obj.data.materials if m]
    for m in mats:
        m.use_nodes = True
    safe = "".join(c if c.isalnum() else "_" for c in f"{obj.name}_{material.name}")
    results = {}
    t_total = time.time()
    try:
        for ch in channels:
            non_color = ch != "base_color"
            img = _new_image(f"{safe}_{ch}", resolution, non_color)
            nodes = []
            for m in mats:
                n = m.node_tree.nodes.new("ShaderNodeTexImage")
                n.image = img
                n.name = n.label = "forge_bake_target"
                m.node_tree.nodes.active = n
                n.select = True
                nodes.append((m, n))
            _activate(obj)
            t = time.time()
            try:
                if ch == "base_color":
                    with _EmissionRoute(material, "Base Color"):
                        bpy.ops.object.bake(type="EMIT")
                elif ch == "metallic":
                    with _EmissionRoute(material, "Metallic"):
                        bpy.ops.object.bake(type="EMIT")
                elif ch == "roughness":
                    bpy.ops.object.bake(type="ROUGHNESS")
                elif ch == "normal":
                    bk.normal_space = "TANGENT"
                    bk.normal_r, bk.normal_g, bk.normal_b = "POS_X", "POS_Y", "POS_Z"
                    bpy.ops.object.bake(type="NORMAL")
                elif ch == "ao":
                    bpy.ops.object.bake(type="AO")
            finally:
                for m, n in nodes:
                    m.node_tree.nodes.remove(n)
            dt = time.time() - t
            px = _pixels(img)
            covered = px[..., 3] > 0.5
            cov = float(covered.mean())
            mean = px[covered][:, :3].mean(axis=0) if covered.any() else np.zeros(3)
            px[..., 3] = 1.0
            img.pixels.foreach_set(px.reshape(-1))
            path = os.path.join(out_dir, f"{img.name}.png")
            img.filepath_raw = path
            img.file_format = "PNG"
            img.save()
            # mean is over covered texels only (UV islands + bake margin), in the
            # image's stored encoding: sRGB for base_color, linear for the rest.
            results[ch] = {"image": img.name, "path": path, "seconds": round(dt, 3),
                           "mean": [round(float(v), 4) for v in mean],
                           "coverage": round(cov, 4),
                           "bytes": os.path.getsize(path)}
    finally:
        sc.cycles.samples = prev_samples
        sc.render.engine = prev_engine
    return {"channels": results, "total_seconds": round(time.time() - t_total, 3),
            "resolution": resolution, "samples": samples, "margin_px": margin,
            "out_dir": out_dir, "uv": uv_info, "material": material.name, "object": obj.name}


# ----------------------------------------------------------------- rewire
def _gltf_settings_group():
    """The node group the glTF exporter reads Occlusion from."""
    name = "glTF Material Output"
    g = bpy.data.node_groups.get(name)
    if g is not None:
        return g
    try:
        from io_scene_gltf2.blender.com.material_helpers import create_settings_group
        return create_settings_group(name)
    except Exception:
        g = bpy.data.node_groups.new(name, "ShaderNodeTree")
        g.interface.new_socket("Occlusion", socket_type="NodeSocketFloat")
        return g


def _orm_image(baked, name, out_dir):
    """Pack occlusion R, roughness G, metallic B into one Non-Color image."""
    chans = baked["channels"]
    ref = bpy.data.images[chans["roughness"]["image"]]
    w, h = ref.size
    orm = np.ones((h, w, 4), dtype=np.float32)
    if "ao" in chans:
        orm[..., 0] = _pixels(bpy.data.images[chans["ao"]["image"]])[..., 0]
    orm[..., 1] = _pixels(ref)[..., 0]
    if "metallic" in chans:
        orm[..., 2] = _pixels(bpy.data.images[chans["metallic"]["image"]])[..., 0]
    else:
        orm[..., 2] = 0.0
    img = _new_image(name, w, True)
    img.pixels.foreach_set(orm.reshape(-1))
    path = os.path.join(out_dir, f"{name}.png")
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    return img, path


def rewire_to_images(material, baked_images, keep_procedural_copy=True):
    """Replace material's procedural tree with Image Texture nodes.

    baked_images is the dict bake_material returned. Builds:
      UV Map -> Image(base_color, sRGB) -> Principled.Base Color
      UV Map -> Image(ORM, Non-Color) -> Separate Color: G->Roughness, B->Metallic,
                                                         R->glTF Material Output.Occlusion
      UV Map -> Image(normal, Non-Color) -> Normal Map(tangent) -> Principled.Normal
    That is exactly the shape the glTF exporter recognises, so nothing is
    re-packed or dropped. With keep_procedural_copy the original tree is kept
    as '<name>_procedural' for further editing and re-baking.
    Returns a summary dict.
    """
    chans = baked_images["channels"] if "channels" in baked_images else baked_images
    copy_name = None
    if keep_procedural_copy:
        cp = material.copy()
        cp.name = f"{material.name}_procedural"
        cp.use_fake_user = True
        copy_name = cp.name
    material.use_nodes = True
    nt = material.node_tree
    old = _principled(nt)
    emission = (tuple(old.inputs["Emission Color"].default_value),
                old.inputs["Emission Strength"].default_value)
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.location = (250, 0)
    b.inputs["Emission Color"].default_value = emission[0]
    b.inputs["Emission Strength"].default_value = emission[1]
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.location = (-700, 0)
    wired = {}

    def tex(img_name, y, non_color):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = bpy.data.images[img_name]
        n.image.colorspace_settings.name = _NON_COLOR if non_color else "sRGB"
        n.location = (-400, y)
        nt.links.new(uv.outputs["UV"], n.inputs["Vector"])
        return n

    if "base_color" in chans:
        n = tex(chans["base_color"]["image"], 300, False)
        nt.links.new(n.outputs["Color"], b.inputs["Base Color"])
        wired["base_color"] = n.image.name
    if "roughness" in chans:
        out_dir = baked_images.get("out_dir") or os.path.dirname(chans["roughness"]["path"])
        orm_img, orm_path = _orm_image({"channels": chans}, f"{material.name}_ORM".replace(" ", "_"), out_dir)
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = orm_img
        n.location = (-400, 0)
        nt.links.new(uv.outputs["UV"], n.inputs["Vector"])
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = (-100, 0)
        nt.links.new(n.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Green"], b.inputs["Roughness"])
        nt.links.new(sep.outputs["Blue"], b.inputs["Metallic"])
        wired["orm"] = orm_img.name
        wired["orm_path"] = orm_path
        if "ao" in chans:
            g = nt.nodes.new("ShaderNodeGroup")
            g.node_tree = _gltf_settings_group()
            g.location = (250, -400)
            nt.links.new(sep.outputs["Red"], g.inputs["Occlusion"])
            wired["occlusion"] = "ORM.R"
    elif "metallic" in chans:
        n = tex(chans["metallic"]["image"], 0, True)
        nt.links.new(n.outputs["Color"], b.inputs["Metallic"])
        wired["metallic"] = n.image.name
    if "normal" in chans:
        n = tex(chans["normal"]["image"], -300, True)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.space = "TANGENT"
        nm.location = (-100, -300)
        nt.links.new(n.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], b.inputs["Normal"])
        wired["normal"] = n.image.name
    return {"material": material.name, "wired": wired, "procedural_copy": copy_name}


# ----------------------------------------------------------------- export + verify
def read_glb_json(path):
    """Parse the JSON chunk of a .glb without Blender. Returns the glTF dict."""
    with open(path, "rb") as f:
        magic, version, length = struct.unpack("<4sII", f.read(12))
        if magic != b"glTF":
            raise ValueError(f"{path} is not a GLB (magic {magic!r})")
        clen, ctype = struct.unpack("<I4s", f.read(8))
        if ctype != b"JSON":
            raise ValueError("first GLB chunk is not JSON")
        return json.loads(f.read(clen).decode("utf-8"))


def glb_texture_report(path):
    """What texture slots each material in a GLB actually carries, and image sizes."""
    g = read_glb_json(path)
    images = g.get("images", [])
    textures = g.get("textures", [])
    with open(path, "rb") as f:
        data = f.read()
    # BIN chunk starts after the JSON chunk.
    jlen = struct.unpack("<I", data[12:16])[0]
    bin_off = 12 + 8 + jlen + 8
    views = g.get("bufferViews", [])

    def image_dims(ii):
        im = images[ii]
        if "bufferView" not in im:
            return None
        bv = views[im["bufferView"]]
        blob = data[bin_off + bv.get("byteOffset", 0): bin_off + bv.get("byteOffset", 0) + bv["byteLength"]]
        if blob[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", blob[16:24])
            return {"mime": "image/png", "w": w, "h": h, "bytes": len(blob)}
        if blob[:2] == b"\xff\xd8":
            return {"mime": "image/jpeg", "bytes": len(blob)}
        return {"mime": im.get("mimeType"), "bytes": len(blob)}

    def slot(info):
        if not info:
            return None
        tex = textures[info["index"]]
        src = tex.get("source")
        return {"texture": info["index"], "image": src,
                "name": images[src].get("name") if src is not None else None,
                **(image_dims(src) or {})}

    mats = []
    for m in g.get("materials", []):
        pbr = m.get("pbrMetallicRoughness", {})
        mats.append({
            "name": m.get("name"),
            "baseColorTexture": slot(pbr.get("baseColorTexture")),
            "metallicRoughnessTexture": slot(pbr.get("metallicRoughnessTexture")),
            "normalTexture": slot(m.get("normalTexture")),
            "occlusionTexture": slot(m.get("occlusionTexture")),
        })
    return {"materials": mats, "image_count": len(images), "file_bytes": len(data)}


_REIMPORT_SCRIPT = r'''
import sys, json, os
sys.path.insert(0, sys.argv[2])
from forgekit import env; env.prepare()
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=sys.argv[1])
out = {"objects": [o.name for o in bpy.context.scene.objects], "materials": []}
for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    imgs = []
    for n in m.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.image is not None:
            dst = sorted({l.to_node.type + ":" + l.to_socket.name for o in n.outputs for l in o.links})
            imgs.append({"image": n.image.name, "size": list(n.image.size),
                         "colorspace": n.image.colorspace_settings.name, "feeds": dst})
    out["materials"].append({"name": m.name, "image_textures": imgs})
print("FORGE_JSON" + json.dumps(out))
'''


def reimport_check(path, timeout=120):
    """Re-import a GLB in a fresh bpy process with factory settings. Returns what
    each imported material's Image Texture nodes are and what they feed."""
    kit = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", _REIMPORT_SCRIPT, os.path.abspath(path), kit],
                       capture_output=True, text=True, timeout=timeout)
    line = next((l for l in r.stdout.splitlines() if l.startswith("FORGE_JSON")), None)
    if line is None:
        raise RuntimeError(f"re-import failed (rc={r.returncode}): {r.stderr[-1500:]}")
    return json.loads(line[len("FORGE_JSON"):])


def export_textured_glb(obj, rig_or_none, path, verify=True):
    """Export obj (and its rig, if given) to GLB with baked textures, then verify.

    Verification is two independent reads:
      1. the GLB's own JSON: each material must reference a baseColorTexture and
         a metallicRoughnessTexture (sizes read from the embedded PNG headers);
      2. a re-import into a clean factory-settings scene in a fresh process: the
         imported material must contain Image Texture nodes.
    Returns {"path", "bytes", "seconds", "glb": ..., "reimport": ..., "ok": bool, "problems": [...]}.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    vl = bpy.context.view_layer
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in vl.objects:
        o.select_set(False)
    obj.select_set(True)
    vl.objects.active = obj
    kw = dict(filepath=path, export_format="GLB", use_selection=True, export_apply=True,
              export_materials="EXPORT", export_image_format="AUTO", export_texcoords=True,
              export_normals=True, export_tangents=True)
    if rig_or_none is not None:
        rig_or_none.select_set(True)
        vl.objects.active = rig_or_none
        kw.update(export_skins=True, export_def_bones=True, export_influence_nb=4,
                  export_force_sampling=True)
    t = time.time()
    bpy.ops.export_scene.gltf(**kw)
    res = {"path": path, "bytes": os.path.getsize(path), "seconds": round(time.time() - t, 3)}
    if not verify:
        return res
    rep = glb_texture_report(path)
    res["glb"] = rep
    problems = []
    for m in rep["materials"]:
        if not m["baseColorTexture"]:
            problems.append(f"material {m['name']}: no baseColorTexture in GLB")
        if not m["metallicRoughnessTexture"]:
            problems.append(f"material {m['name']}: no metallicRoughnessTexture in GLB")
    if not rep["materials"]:
        problems.append("GLB has no materials")
    t = time.time()
    re = reimport_check(path)
    res["reimport"] = re
    res["reimport_seconds"] = round(time.time() - t, 3)
    if not any(m["image_textures"] for m in re["materials"]):
        problems.append("re-imported scene has no Image Texture nodes")
    res["problems"] = problems
    res["ok"] = not problems
    return res
