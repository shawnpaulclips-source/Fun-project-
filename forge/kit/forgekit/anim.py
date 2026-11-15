"""Animation: keyframes, curve craft, and the agent's critique loop.

TWO API FACTS THAT BREAK EVERY PRE-4.4 TUTORIAL:

1. `action.fcurves` WAS DELETED in Blender 4.4+/5.x. Verified:
       hasattr(action, 'fcurves') == False
   Curves now live at: action.layers[0].strips[0].channelbag(slot).fcurves
   Use fcurves() below; never touch action.fcurves.

2. The Graph Editor operators work headless, but ONLY because the `bpy` pip
   module loads the startup file and keeps a window. `blender --background`
   does not. Every bpy.ops.graph.* call fails poll() until you retype an area
   to GRAPH_EDITOR and wrap it in context.temp_override(). graph_context()
   does that. Verified working: decimate, ease, euler_filter,
   butterworth_smooth, breakdown, blend_to_neighbor, match_slope, push_pull.

   This depends on an implementation detail, not a documented API. Pin your
   Blender version and keep it in your test suite as a canary.
"""
import bpy, math, os, contextlib
from mathutils import Vector, Euler, Quaternion

R = math.radians
D = math.degrees

# Measured Bezier handle fractions. 'snap' puts 30% of the move in by frame 3
# of 13 and 90% by frame 9 - that is what "snappy with a settle" actually means
# numerically. Blender's own default handle extent is exactly 1/3 of the span.
EASE = {
    "linear":   (0.00, 0.00),
    "smooth":   (0.33, 0.33),
    "snap":     (0.10, 0.70),
    "ease_in":  (0.60, 0.20),
    "ease_out": (0.20, 0.60),
    "slow":     (0.55, 0.55),
    "hold":     (0.85, 0.85),
}

# Richard Williams' walk-cycle pose frames for a 24-frame (2-step) cycle on 24fps.
WALK_24 = {"contact": (1, 13, 25), "down": (4, 16), "passing": (7, 19), "up": (10, 22)}

# Quadruped footfall phase offsets as fractions of one cycle (LF, RF, LH, RH).
GAITS = {
    "walk":  (0.00, 0.50, 0.25, 0.75),
    "trot":  (0.00, 0.50, 0.50, 0.00),
    "pace":  (0.00, 0.50, 0.00, 0.50),
    "bound": (0.00, 0.00, 0.50, 0.50),
}


# --------------------------------------------------------------- curve access
def fcurves(obj, slot=None):
    """Return the F-curve collection, 5.x-safe. ALWAYS use this."""
    ad = obj.animation_data
    if not ad or not ad.action:
        return []
    act = ad.action
    if hasattr(act, "fcurves"):           # Blender <= 4.3
        return act.fcurves
    if not act.layers:
        return []
    strip = act.layers[0].strips[0]
    slot = slot or getattr(ad, "action_slot", None) or (act.slots[0] if act.slots else None)
    if slot is None:
        return []
    cb = strip.channelbag(slot)
    return cb.fcurves if cb else []


def find_curve(obj, data_path, index=-1):
    for f in fcurves(obj):
        if f.data_path == data_path and (index < 0 or f.array_index == index):
            return f
    return None


def select_for_graph(obj):
    """Put `obj` in the state the Graph Editor requires.

    The operators poll against VISIBLE, SELECTED channels. For a mesh that means
    the object must be selected and active. For an ARMATURE it must also be in
    POSE mode with bones selected, or every channel is filtered out and
    bpy.ops.graph.select_all() fails poll with 'context is incorrect'.
    """
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if obj.type == "ARMATURE":
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.pose.select_all(action="SELECT")
        return "POSE"
    return "OBJECT"


@contextlib.contextmanager
def graph_context():
    """Make bpy.ops.graph.* callable. See module docstring.

    Call select_for_graph(obj) first - this only fixes the AREA, not the
    selection state the operators also poll against.
    """
    win = bpy.context.window
    if win is None:
        raise RuntimeError("no window: you are on `blender --background`, "
                           "not the bpy module. Graph operators are unavailable.")
    scr = win.screen
    area = max(scr.areas, key=lambda a: a.width * a.height)
    old = area.type
    area.type = "GRAPH_EDITOR"
    try:
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=win, screen=scr, area=area,
                                       region=region, space_data=area.spaces.active):
            yield
    finally:
        area.type = old


# --------------------------------------------------------------- keyframing
def key_pose(obj, frame, bones=None, channels=("location", "rotation_quaternion",
                                               "rotation_euler", "scale")):
    """Key a pose. Pass bones=[names] to key a subset, None for everything."""
    if obj.type == "ARMATURE":
        targets = [obj.pose.bones[b] for b in bones] if bones else list(obj.pose.bones)
        for pb in targets:
            for ch in channels:
                if ch == "rotation_quaternion" and pb.rotation_mode != "QUATERNION":
                    continue
                if ch == "rotation_euler" and pb.rotation_mode == "QUATERNION":
                    continue
                try:
                    pb.keyframe_insert(ch, frame=frame)
                except (RuntimeError, TypeError):
                    pass
    else:
        for ch in channels:
            try:
                obj.keyframe_insert(ch, frame=frame)
            except (RuntimeError, TypeError):
                pass


def set_bone(rig, name, rot_deg=None, loc=None, scale=None, mode="XYZ"):
    """Pose one bone in degrees. Degrees in, radians stored - an agent reasoning
    in degrees against a radian channel is a reliable 57x bug."""
    pb = rig.pose.bones.get(name)
    if pb is None:
        return None
    if rot_deg is not None:
        pb.rotation_mode = mode
        pb.rotation_euler = Euler([R(a) for a in rot_deg], mode)
    if loc is not None:
        pb.location = loc
    if scale is not None:
        pb.scale = scale
    return pb


def apply_pose(rig, pose, frame=None):
    """pose: {bone_name: {'rot': (x,y,z) degrees, 'loc': (..), 'scale': (..)}}"""
    for name, spec in pose.items():
        set_bone(rig, name, spec.get("rot"), spec.get("loc"), spec.get("scale"))
    if frame is not None:
        key_pose(rig, frame, bones=list(pose.keys()))


def mirror_pose(pose, mirror_map):
    """Mirror a pose dict through the rig manifest's mirror_map. Rotations flip
    sign on Y and Z, locations flip on X - the standard Blender mirror."""
    inv = {v: k for k, v in mirror_map.items()}
    out = {}
    for name, spec in pose.items():
        tgt = mirror_map.get(name) or inv.get(name) or name
        s = dict(spec)
        if "rot" in s:
            x, y, z = s["rot"]
            s["rot"] = (x, -y, -z)
        if "loc" in s:
            lx, ly, lz = s["loc"]
            s["loc"] = (-lx, ly, lz)
        out[tgt] = s
    return out


# --------------------------------------------------------------- curve craft
def set_interpolation(obj, mode="BEZIER", only_selected=False):
    for f in fcurves(obj):
        for k in f.keyframe_points:
            k.interpolation = mode
    return obj


def stage_block(obj):
    """Blocking pass: stepped keys, no interpolation. This is how an animator
    starts, and it is what stops an agent's default behaviour of two keys and
    a lerp."""
    set_interpolation(obj, "CONSTANT")
    for f in fcurves(obj):
        for k in f.keyframe_points:
            k.type = "EXTREME"
    return obj


def stage_spline(obj, ease="smooth"):
    """Splining pass: convert stepped blocking to Bezier with real handles."""
    out_f, in_f = EASE[ease]
    for f in fcurves(obj):
        pts = f.keyframe_points
        for i, k in enumerate(pts):
            k.interpolation = "BEZIER"
            k.handle_left_type = "FREE"
            k.handle_right_type = "FREE"
            prev_x = pts[i - 1].co.x if i > 0 else k.co.x - 12
            next_x = pts[i + 1].co.x if i < len(pts) - 1 else k.co.x + 12
            k.handle_left = (k.co.x - (k.co.x - prev_x) * max(in_f, 1e-3), k.co.y)
            k.handle_right = (k.co.x + (next_x - k.co.x) * max(out_f, 1e-3), k.co.y)
        f.update()
    return obj


def offset_chain(obj, bone_names, frames_per_bone=2.0):
    """Overlap / follow-through: delay each bone down the chain.

    Hips lead, shoulders follow, head last. This single pass is the difference
    between mechanical and alive, and it is pure arithmetic on key x-positions.
    """
    for depth, name in enumerate(bone_names):
        shift = depth * frames_per_bone
        if shift == 0:
            continue
        for f in fcurves(obj):
            if f'["{name}"]' not in f.data_path and f'bones["{name}"]' not in f.data_path:
                continue
            for k in f.keyframe_points:
                k.co.x += shift
                k.handle_left.x += shift
                k.handle_right.x += shift
            f.update()
    return obj


def add_noise(obj, data_path, strength_deg=1.5, scale=18.0, index=-1):
    """Idle life: breathing, micro-sway. strength is in DEGREES and converted,
    because a raw radian amplitude of 1.5 is a 86-degree seizure."""
    for f in fcurves(obj):
        if f.data_path != data_path or (index >= 0 and f.array_index != index):
            continue
        m = f.modifiers.new("NOISE")
        m.strength = R(strength_deg) if "rotation" in data_path else strength_deg
        m.scale = scale
        m.phase = hash(f.data_path + str(f.array_index)) % 100
    return obj


def cycle(obj, mode="REPEAT"):
    """Make every curve loop forever."""
    for f in fcurves(obj):
        if not any(m.type == "CYCLES" for m in f.modifiers):
            f.modifiers.new("CYCLES")
    return obj


def close_loop(obj, start, end):
    """Force frame `end+1` to equal frame `start` on every curve.

    A cycle that does not close pops once per loop. Any pass that shifts keys in
    time - offset_chain() above all - breaks the seam, so run this AFTER
    offsetting, never before.
    """
    fixed = 0
    for f in fcurves(obj):
        if not f.keyframe_points:
            continue
        y0 = f.evaluate(start)
        kp = f.keyframe_points
        seam = [k for k in kp if abs(k.co.x - (end + 1)) < 1e-4]
        if seam:
            for k in seam:
                if abs(k.co.y - y0) > 1e-9:
                    dy = y0 - k.co.y
                    k.co.y = y0
                    k.handle_left.y += dy
                    k.handle_right.y += dy
                    fixed += 1
        else:
            k = kp.insert(end + 1, y0)
            k.interpolation = "BEZIER"
            fixed += 1
        f.update()
    return {"curves": len(fcurves(obj)), "seam_keys_fixed": fixed}


def soften_handles(obj, factor=0.55):
    """Shrink Bezier handle extents toward the key.

    Long handles overshoot between sparse keys, which shows up as high jerk on
    fast arcs. This is the cheapest jerk fix and it does not remove keys.
    """
    for f in fcurves(obj):
        for k in f.keyframe_points:
            if k.interpolation != "BEZIER":
                continue
            k.handle_left_type = "FREE"
            k.handle_right_type = "FREE"
            k.handle_left.x = k.co.x - (k.co.x - k.handle_left.x) * factor
            k.handle_right.x = k.co.x + (k.handle_right.x - k.co.x) * factor
            k.handle_left.y = k.co.y - (k.co.y - k.handle_left.y) * factor
            k.handle_right.y = k.co.y + (k.handle_right.y - k.co.y) * factor
        f.update()
    return obj


def decimate(obj, error=0.05):
    """Reduce key count. Blender's decimate beats hand-rolled RDP: it removes
    the least-influential key and re-evaluates the real Bezier, where RDP only
    measures point-to-chord distance. Measured: 240 keys -> 36 at error 0.05."""
    mode = select_for_graph(obj)
    before = sum(len(f.keyframe_points) for f in fcurves(obj))
    with graph_context():
        bpy.ops.graph.select_all(action="SELECT")
        bpy.ops.graph.decimate(mode="ERROR", remove_error_margin=error)
    after = sum(len(f.keyframe_points) for f in fcurves(obj))
    if mode == "POSE":
        bpy.ops.object.mode_set(mode="OBJECT")
    return {"before": before, "after": after, "removed": before - after,
            "error": error}


def graph_op(obj, op, **kwargs):
    """Run any bpy.ops.graph.* operator on obj's curves, safely.

    Verified working headless on Blender 5.3: decimate, ease, euler_filter,
    butterworth_smooth, breakdown, blend_to_neighbor, match_slope, push_pull,
    shear, equalize_handles, clean, smooth.

        graph_op(rig, "ease", factor=0.4)
        graph_op(rig, "butterworth_smooth")
    """
    mode = select_for_graph(obj)
    fn = getattr(bpy.ops.graph, op)
    with graph_context():
        bpy.ops.graph.select_all(action="SELECT")
        fn(**kwargs)
    if mode == "POSE":
        bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def bake(obj, start, end, step=1, visual=True, clear_constraints=False):
    """Bake constraints and drivers down to raw keys. Mandatory before export:
    glTF carries baked joint TRS and morph weights only - no constraints,
    no drivers, no NLA complexity."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    if obj.type == "ARMATURE":
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.pose.select_all(action="SELECT")
    bpy.ops.nla.bake(frame_start=start, frame_end=end, step=step,
                     only_selected=True, visual_keying=visual,
                     clear_constraints=clear_constraints, clear_parents=False,
                     bake_types={"POSE"} if obj.type == "ARMATURE" else {"OBJECT"})
    if obj.type == "ARMATURE":
        bpy.ops.object.mode_set(mode="OBJECT")
    return obj


# --------------------------------------------------------------- critique
def sample_world(rig, bone_names, start, end):
    """World-space positions per frame. The raw material for every metric."""
    sc = bpy.context.scene
    data = {n: [] for n in bone_names}
    for f in range(start, end + 1):
        sc.frame_set(f)
        for n in bone_names:
            pb = rig.pose.bones.get(n)
            data[n].append((rig.matrix_world @ pb.matrix).translation.copy()
                           if pb else Vector((0, 0, 0)))
    return data


def deform_name(rig, control_name):
    """Map a control bone to the deform bone it drives, if one exists.

    ALWAYS MEASURE DEFORM BONES. A control can move while the deform chain
    ignores it completely - an FK control keyed on a limb that is still in IK
    produces full, healthy-looking curves and a character that does not move.
    Measuring the control tells you the curve exists; measuring the deform bone
    tells you the mesh actually deformed.
    """
    for cand in (f"DEF-{control_name}",
                 f"DEF-{control_name.replace('_fk', '')}",
                 control_name.replace("_fk", "")):
        if cand in rig.pose.bones:
            return cand
    return control_name


def metrics(rig, bone_names, start, end, fps=None, contact_bones=(),
            expected_root_speed=0.0):
    """Blind animation critique. Thresholds are the actionable part.

    Pass DEFORM bone names (see deform_name) - not control names.

      jerk_max        > 0.08 m/f3  -> pops; decimate or soften handles
      foot_slide      > 0.010 m    -> plant the foot; IK or a contact key
                                     CAVEAT: this measures world-space travel of
                                     the planted foot. An in-place cycle with no
                                     root translation will ALWAYS fail it - the
                                     foot genuinely does move relative to a static
                                     root. Either add root motion before measuring,
                                     or pass expected_root_speed below.
      loop_seam_error > 0.001 m    -> frame 1 and frame N+1 disagree; fix the cycle
      arc_straightness< 0.06       -> end effector travels a straight line, looks robotic
    """
    fps = fps or bpy.context.scene.render.fps
    # sample one frame PAST the end so the loop seam is measurable
    data = sample_world(rig, list(bone_names) + list(contact_bones), start, end + 1)
    out = {}
    for n, raw in data.items():
        if len(raw) < 5:
            continue
        pts = raw[:-1]              # frames start..end: the cycle itself
        seam = (raw[-1] - raw[0]).length    # frame end+1 vs frame start
        vel = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
        acc = [vel[i + 1] - vel[i] for i in range(len(vel) - 1)]
        jerk = [abs(acc[i + 1] - acc[i]) for i in range(len(acc) - 1)]
        chord = (pts[-1] - pts[0]).length
        path = sum(vel)
        out[n] = {
            "speed_max_m_per_frame": round(max(vel), 5),
            "jerk_max": round(max(jerk), 5) if jerk else 0.0,
            "path_length_m": round(path, 4),
            "arc_straightness": round(1.0 - (chord / path), 4) if path > 1e-9 else 0.0,
            "loop_seam_error_m": round(seam, 5),
        }
    for n in contact_bones:
        pts = data.get(n, [])[:-1]
        if len(pts) < 2:
            continue
        lows = sorted(range(len(pts)), key=lambda i: pts[i].z)[: max(2, len(pts) // 4)]
        slide = 0.0
        for a, b in zip(sorted(lows), sorted(lows)[1:]):
            if b == a + 1:
                # subtract the travel the root is expected to cover in one frame
                d = (pts[b] - pts[a]).xy.length - expected_root_speed
                slide = max(slide, abs(d))
        out.setdefault(n, {})["foot_slide_m"] = round(slide, 5)
    return out


def critique(metrics_dict, thresholds=None):
    """Turn metrics into a short prioritised report an agent can act on."""
    th = thresholds or {"jerk_max": 0.08, "foot_slide_m": 0.010,
                        "loop_seam_error_m": 0.001, "arc_straightness": 0.06}
    fails, warns = [], []
    for bone, m in metrics_dict.items():
        if m.get("jerk_max", 0) > th["jerk_max"]:
            fails.append(f"{bone}: jerk {m['jerk_max']:.3f} > {th['jerk_max']} - pops. "
                         f"anim.decimate() or soften handles")
        if m.get("foot_slide_m", 0) > th["foot_slide_m"]:
            fails.append(f"{bone}: foot slide {m['foot_slide_m']*1000:.0f}mm - plant the contact")
        if m.get("loop_seam_error_m", 1) > th["loop_seam_error_m"]:
            warns.append(f"{bone}: loop seam {m['loop_seam_error_m']*1000:.1f}mm - cycle does not close")
        if 0 < m.get("arc_straightness", 1) < th["arc_straightness"]:
            warns.append(f"{bone}: arc {m['arc_straightness']:.3f} - travelling a straight line")
    failed_bones = {l.split(":")[0] for l in fails}
    score = max(0, 100 - 12 * len(fails) - 4 * len(warns))
    return {"score": score, "fails": fails, "warns": warns,
            "bones_checked": len(metrics_dict),
            "bones_clean": len([b for b in metrics_dict if b not in failed_bones])}


def playblast(outdir, prefix, start, end, step=1, resolution=(480, 270)):
    """Workbench frames for motion review. ~0.11 s/frame at 480x270.

    MCP has no video content type: tile these with render.contact_sheet() to
    see motion in one image, and hand the mp4 back as a resource_link.
    """
    from . import render as fkrender
    sc = bpy.context.scene
    fkrender.workbench(shading="STUDIO", color_type="MATERIAL", cavity=False)
    sc.render.resolution_x, sc.render.resolution_y = resolution
    paths = []
    for f in range(start, end + 1, step):
        sc.frame_set(f)
        p = os.path.join(outdir, f"{prefix}_{f:04d}")
        sc.render.filepath = p
        bpy.ops.render.render(write_still=True)
        paths.append(p + ".png")
    return paths
