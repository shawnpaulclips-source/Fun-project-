"""Headless rendering: the eyes.

ENGINE CHOICE IS NOT A PREFERENCE. Measured in a GPU-less container:
    Workbench  512x512   0.62 s
    Cycles CPU 512x512   1.95 - 5.11 s  (32 spp)
    EEVEE      512x512   23.4 - 43.2 s  <-- 12-30x SLOWER than path tracing
EEVEE on software Vulkan (llvmpipe) is useless. Workbench for structure,
Cycles for light transport, EEVEE only if a real GPU is present.
"""
import bpy, os, time, math, subprocess, shutil
from mathutils import Euler

R = math.radians


def setup_world(color=(0.035, 0.040, 0.052), strength=1.0):
    sc = bpy.context.scene
    w = bpy.data.worlds.new("World") if sc.world is None else sc.world
    sc.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (*color, 1.0)
    bg.inputs["Strength"].default_value = strength
    return w


def three_point(key=2600, fill=700, rim=1500, scale=1.0):
    """Standard key/fill/rim. Key warm, fill cool, rim warm and behind.

    Energies are tuned for an object roughly 1-3 m across at these distances.
    Multiply `scale` with the object's size if yours differs a lot.
    """
    sc = bpy.context.scene
    specs = [
        ("Key",  (3.1, -3.4, 3.4),  key,  3.2, (R(52), 0, R(42)),   (1.00, 0.95, 0.88)),
        ("Fill", (-3.8, -1.6, 0.9), fill, 4.5, (R(78), 0, R(-62)),  (0.62, 0.72, 1.00)),
        ("Rim",  (-1.2, 3.9, 2.6),  rim,  2.4, (R(118), 0, R(192)), (1.00, 0.86, 0.72)),
    ]
    out = []
    for name, loc, energy, size, rot, col in specs:
        d = bpy.data.lights.new(name, "AREA")
        d.energy = energy * scale
        d.size = size
        d.color = col
        o = bpy.data.objects.new(name, d)
        sc.collection.objects.link(o)
        o.location = tuple(c * scale for c in loc)
        o.rotation_euler = Euler(rot, "XYZ")
        out.append(o)
    return out


def camera(lens=80, target=(0, 0, 0), location=(5, -6, 1.6), name="Camera"):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cam = bpy.data.objects.new(name, cd)
    sc.collection.objects.link(cam)
    cam.location = location
    sc.camera = cam
    tgt = bpy.data.objects.new("CamTarget", None)
    sc.collection.objects.link(tgt)
    tgt.location = target
    c = cam.constraints.new("TRACK_TO")
    c.target = tgt
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"
    return cam, tgt


def frame_distance(object_height, lens=80, sensor=36.0, margin=1.18):
    """How far back the camera must sit to fit an object of this height.

    Getting this wrong is the #1 first-render failure: the subject is cropped
    and you burn a whole render finding out. Compute it, do not guess.
    """
    half_fov = math.atan((sensor / 2) / lens)
    return (object_height * margin / 2) / math.tan(half_fov)


def configure(engine="CYCLES", samples=96, resolution=(900, 1100),
              denoise=True, transparent=False, look="AgX - Medium High Contrast"):
    sc = bpy.context.scene
    sc.render.engine = engine
    sc.render.resolution_x, sc.render.resolution_y = resolution
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = transparent
    if engine == "CYCLES":
        sc.cycles.device = "CPU"
        sc.cycles.samples = samples
        sc.cycles.use_adaptive_sampling = True
        sc.cycles.adaptive_threshold = 0.02
        sc.cycles.use_denoising = denoise
        sc.cycles.max_bounces = 8
    try:
        sc.view_settings.view_transform = "AgX"
        sc.view_settings.look = look
    except Exception:
        pass
    return sc


def workbench(shading="STUDIO", color_type="MATERIAL", outline=True, cavity=True):
    """Fast structural preview. Use for every inspection pass.

    color_type='VERTEX' + shading='FLAT' is how weight heatmaps are rendered.
    """
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sh = sc.display.shading
    sh.light = shading
    sh.color_type = color_type
    sh.show_object_outline = outline
    sh.show_cavity = cavity
    if cavity:
        sh.cavity_type = "BOTH"
    return sh


def shot(filepath, location=None, resolution=None, samples=None, verbose=True):
    """Render one still. Returns (path, seconds)."""
    sc = bpy.context.scene
    if location is not None:
        sc.camera.location = location
    if resolution is not None:
        sc.render.resolution_x, sc.render.resolution_y = resolution
    if samples is not None and sc.render.engine == "CYCLES":
        sc.cycles.samples = samples
    os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
    sc.render.filepath = filepath
    t = time.time()
    bpy.ops.render.render(write_still=True)
    dt = time.time() - t
    real = sc.render.filepath if sc.render.filepath.endswith(".png") else sc.render.filepath + ".png"
    if verbose:
        print(f"[render] {os.path.basename(real)} {dt:.1f}s")
    return real, dt


def quad_views(outdir, prefix, distance, height=0.0, resolution=(640, 640), samples=48):
    """Front / right / top / three-quarter. The orthographic-ish inspection set.

    Four cheap views catch proportion and symmetry errors that one hero render
    hides completely.
    """
    views = {
        "front": (0.0, -distance, height),
        "right": (distance, 0.0, height),
        "top":   (0.001, 0.0, distance),
        "persp": (distance * 0.62, -distance * 0.72, distance * 0.34 + height),
    }
    out = []
    for name, loc in views.items():
        p, _ = shot(os.path.join(outdir, f"{prefix}_{name}"), loc, resolution, samples)
        out.append(p)
    return out


def turntable(outdir, prefix, frames=24, distance=7.5, height=1.4,
              resolution=(480, 480), samples=32):
    """Orbit the camera. At Workbench speed a 24-frame turntable is ~3 s total."""
    sc = bpy.context.scene
    paths = []
    for i in range(frames):
        a = 2 * math.pi * i / frames
        loc = (math.cos(a) * distance, math.sin(a) * distance, height)
        p, _ = shot(os.path.join(outdir, f"{prefix}_{i:03d}"), loc, resolution,
                    samples, verbose=False)
        paths.append(p)
    return paths


def contact_sheet(image_paths, out_path, cols=6, tile=240):
    """Tile frames into ONE image.

    MCP has no video content type, so a contact sheet is the only way an agent
    sees motion in a single tool result. Keep it under ~1440x1080 (~2100 tokens).
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found")
    import tempfile
    rows = math.ceil(len(image_paths) / cols)
    with tempfile.TemporaryDirectory() as td:
        for i, p in enumerate(image_paths):
            os.link(p, os.path.join(td, f"f{i:04d}.png")) if os.path.exists(p) else None
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", "1",
               "-i", os.path.join(td, "f%04d.png"),
               "-vf", f"scale={tile}:-1,tile={cols}x{rows}", "-frames:v", "1", out_path]
        subprocess.run(cmd, check=True)
    return out_path


def encode(image_paths, out_path, fps=24):
    """Frames -> mp4 or gif. Returns the path; hand it back as a resource_link."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for i, p in enumerate(image_paths):
            if os.path.exists(p):
                shutil.copy(p, os.path.join(td, f"f{i:04d}.png"))
        if out_path.endswith(".gif"):
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
                   "-i", os.path.join(td, "f%04d.png"),
                   "-vf", "split[a][b];[a]palettegen[p];[b][p]paletteuse", out_path]
        else:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
                   "-i", os.path.join(td, "f%04d.png"),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out_path]
        subprocess.run(cmd, check=True)
    return out_path
