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
from mathutils import Vector, Euler, Quaternion, Matrix

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


def _stance_xy(pts, lows):
    """xy displacement vectors between consecutive contact frames."""
    lows = sorted(lows)
    return [((pts[b] - pts[a]).xy) for a, b in zip(lows, lows[1:]) if b == a + 1]


def stance_velocity(pts, frac=0.25):
    """Estimate the per-frame xy velocity of a foot while it is planted.

    Contact frames are the lowest `frac` of the samples by height (the same
    set foot_slide uses). The stance velocity is the component-wise MEDIAN of
    the xy displacement between consecutive contact frames, so one slipping
    frame does not drag the estimate. Returns (Vector2 velocity, lows).

    On an in-place cycle this is the treadmill belt speed; on a cycle with
    root motion and planted feet it is ~0.
    """
    if len(pts) < 3:
        return Vector((0.0, 0.0)), []
    lows = sorted(range(len(pts)), key=lambda i: pts[i].z)[: max(2, int(len(pts) * frac))]
    ds = _stance_xy(pts, lows)
    if not ds:
        return Vector((0.0, 0.0)), lows
    xs = sorted(d.x for d in ds)
    ys = sorted(d.y for d in ds)
    m = len(ds) // 2
    med = (lambda s: s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m]))
    return Vector((med(xs), med(ys))), lows


def metrics(rig, bone_names, start, end, fps=None, contact_bones=(),
            expected_root_speed="auto", root_bone=None):
    """Blind animation critique. Thresholds are the actionable part.

    Pass DEFORM bone names (see deform_name) - not control names.

      jerk_max        > 0.08 m/f3  -> pops; decimate or soften handles
      foot_slide      > 0.010 m    -> plant the foot: anim.plant_feet()
      loop_seam_error > 0.001 m    -> frame 1 and frame N+1 disagree; fix the cycle
      arc_straightness< 0.06       -> end effector travels a straight line, looks robotic

    foot_slide: the worst per-frame xy deviation of a contact bone from its
    stance velocity, over consecutive contact frames (lowest quarter by height).

      expected_root_speed="auto" (default)  the stance velocity is ESTIMATED
          from the contact frames themselves (stance_velocity()). On an
          in-place cycle that is the treadmill speed, on a root-motion cycle
          it is ~0. This is the correct number for both.
      expected_root_speed=<float>  legacy: subtract a scalar speed from the
          per-frame travel. Do NOT pass the foot's path_length / N here - that
          is the average over the swing too, not the stance speed, and it
          produced a fake 101 mm -> 64 mm "improvement" in examples/03.
      expected_root_speed=0.0  raw world-space slide.

    root_bone: if given, loop seam and arc_straightness are measured after
    subtracting that bone's travel, so a root-motion cycle is neither reported
    as an open loop nor as feet 'travelling a straight line'. Jerk, speed,
    path length and foot slide stay in world space.

    NOTE: names are de-duplicated before sampling. The pre-fix version sampled
    a bone twice per frame when it was in both bone_names and contact_bones,
    which zeroed every other velocity and inflated jerk (0.340 reported vs
    0.155 real on examples/03).
    """
    fps = fps or bpy.context.scene.render.fps
    names = list(dict.fromkeys(list(bone_names) + list(contact_bones)))
    if root_bone:
        names.append(root_bone)
    # sample one frame PAST the end so the loop seam is measurable
    data = sample_world(rig, names, start, end + 1)
    rr = data.pop(root_bone) if root_bone else None
    out = {}
    for n, raw in data.items():
        if len(raw) < 5:
            continue
        pts = raw[:-1]              # frames start..end: the cycle itself
        # seam and arc shape are judged relative to the root, so root travel
        # neither opens the loop nor makes every arc look like a straight line
        relp = [p - (rr[i] - rr[0]) for i, p in enumerate(raw)] if rr else raw
        seam = (relp[-1] - relp[0]).length    # frame end+1 vs frame start
        vel = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
        acc = [vel[i + 1] - vel[i] for i in range(len(vel) - 1)]
        jerk = [abs(acc[i + 1] - acc[i]) for i in range(len(acc) - 1)]
        rv = [(relp[i + 1] - relp[i]).length for i in range(len(pts) - 1)]
        chord = (relp[len(pts) - 1] - relp[0]).length
        path = sum(vel)
        out[n] = {
            "speed_max_m_per_frame": round(max(vel), 5),
            "jerk_max": round(max(jerk), 5) if jerk else 0.0,
            "path_length_m": round(path, 4),
            "arc_straightness": round(1.0 - (chord / sum(rv)), 4) if sum(rv) > 1e-9 else 0.0,
            "loop_seam_error_m": round(seam, 5),
        }
    for n in contact_bones:
        pts = data.get(n, [])[:-1]
        if len(pts) < 3:
            continue
        slide = 0.0
        if isinstance(expected_root_speed, str):
            belt, lows = stance_velocity(pts)
            for d in _stance_xy(pts, lows):
                slide = max(slide, (d - belt).length)
            out.setdefault(n, {})["stance_speed_m_per_frame"] = round(belt.length, 5)
        else:
            lows = sorted(range(len(pts)), key=lambda i: pts[i].z)[: max(2, len(pts) // 4)]
            for d in _stance_xy(pts, lows):
                slide = max(slide, abs(d.length - float(expected_root_speed)))
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
            fails.append(f"{bone}: foot slide {m['foot_slide_m']*1000:.0f}mm - anim.plant_feet()")
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


# --------------------------------------------------------------- foot planting
def _runs(mask, cyclic=True):
    """Contiguous True runs of a boolean list, as index lists. Cyclic runs may
    wrap from the last index to the first."""
    n = len(mask)
    if n == 0 or not any(mask):
        return []
    if all(mask):
        return [list(range(n))]
    runs = []
    for i in range(n):
        prev = mask[i - 1] if (cyclic or i > 0) else False
        if mask[i] and not prev:
            r, j = [i], i + 1
            while True:
                if not cyclic and j >= n:
                    break
                jj = j % n
                if not mask[jj] or jj == i:
                    break
                r.append(jj)
                j += 1
            runs.append(r)
    return runs


def _median_vec(vs):
    if not vs:
        return Vector((0.0, 0.0))
    xs = sorted(v.x for v in vs)
    ys = sorted(v.y for v in vs)
    m = len(vs) // 2
    med = (lambda s: s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m]))
    return Vector((med(xs), med(ys)))


def _smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _remove_curves(obj, data_paths):
    """Delete whole F-curves (not just their keys) from the 5.x channelbag, so a
    later frame_set() cannot re-apply them over a hand-set pose."""
    ad = obj.animation_data
    if not ad or not ad.action:
        return 0
    act = ad.action
    removed = 0
    if hasattr(act, "fcurves"):
        coll = act.fcurves
    else:
        slot = getattr(ad, "action_slot", None) or (act.slots[0] if act.slots else None)
        cb = act.layers[0].strips[0].channelbag(slot) if (act.layers and slot) else None
        if cb is None:
            return 0
        coll = cb.fcurves
    for fc in [f for f in coll if f.data_path in data_paths]:
        coll.remove(fc)
        removed += 1
    return removed


def contact_frames(heights, rel_xy, belt=None, height_tol=0.15, vel_tol=0.5,
                   min_speed_tol=0.004, cyclic=True, min_run=2):
    """Find planted frames of ONE foot from its height and its velocity.

    heights   per-frame height of the ankle (m)
    rel_xy    per-frame xy position RELATIVE TO THE BODY/ROOT (Vector2). On an
              in-place cycle a planted foot is not still in world space - it
              slides backward at the treadmill speed - so velocity is judged
              against `belt`, the shared stance velocity, not against zero.
    belt      stance velocity (m/frame, Vector2). None -> estimated here as the
              median velocity over low frames.

    A segment i -> i+1 is a contact segment when both ends are within
    height_tol * (height range) of the lowest point AND its velocity is within
    max(vel_tol * |belt|, min_speed_tol) of belt. Returns (runs, belt) where
    runs is a list of frame-index lists (cyclic runs may wrap).
    """
    n = len(heights)
    zmin, zmax = min(heights), max(heights)
    thr = zmin + height_tol * max(zmax - zmin, 1e-6)
    low = [h <= thr for h in heights]
    nxt = (lambda i: (i + 1) % n) if cyclic else (lambda i: min(i + 1, n - 1))
    vel = [rel_xy[nxt(i)] - rel_xy[i] for i in range(n)]
    seg_low = [low[i] and low[nxt(i)] and (cyclic or i < n - 1) for i in range(n)]
    if belt is None:
        belt = _median_vec([vel[i] for i in range(n) if seg_low[i]])
    tol = max(vel_tol * belt.length, min_speed_tol)
    seg = [seg_low[i] and (vel[i] - belt).length <= tol for i in range(n)]
    mask = [seg[i] or seg[(i - 1) % n if cyclic else max(i - 1, 0)] and (cyclic or i > 0)
            for i in range(n)]
    runs = [r for r in _runs(mask, cyclic) if len(r) >= min_run]
    return runs, belt


def _two_bone(H, K, A, X, T, side_ref):
    """Analytic two-bone IK in armature space.

    H hip, K knee, A ankle (current pose), X the thigh's hinge axis, T target
    ankle, side_ref the sign the knee bend must have about X (taken from the
    rest pose, so a hyperextended knee is solved back to the natural side).
    Returns (K_new, T_reached, clamped).
    """
    L1 = (K - H).length
    L2 = (A - K).length
    dv = T - H
    d0 = dv.length
    lo, hi = abs(L1 - L2) + 1e-6, (L1 + L2) * 0.99999
    d = min(max(d0, lo), hi)
    clamped = d != d0
    u = dv.normalized() if d0 > 1e-9 else (A - H).normalized()
    n = X - u * X.dot(u)
    if n.length < 1e-6:
        n = u.orthogonal()
    n.normalize()
    a = (L1 * L1 - L2 * L2 + d * d) / (2 * d)
    b = math.sqrt(max(L1 * L1 - a * a, 0.0))
    w = n.cross(u)
    Tn = H + u * d
    Kn = H + u * a + w * b
    if side_ref and (Kn - H).cross(Tn - Kn).dot(n) * side_ref < 0:
        Kn = H + u * a - w * b
    return Kn, Tn, clamped, n


def _frame(X, Y):
    X = (X - Y * X.dot(Y)).normalized()
    Z = X.cross(Y)
    return Matrix((X, Y, Z)).transposed()


def plant_feet(rig, foot_bones, start=None, end=None, mode="treadmill",
               height_tol=0.15, vel_tol=0.5, blend_frames=3, swing="smooth",
               root=None, chains=None, cyclic=True, min_run=2):
    """Find contacts from foot height and velocity, then lock each planted foot.

    foot_bones  FK foot CONTROLS (e.g. ['foot_fk.L', 'foot_fk.R']). The thigh
                and shin are resolved by geometry (rig.leg_chain), or pass
                chains={foot: (thigh, shin, foot)}.
    mode
      'treadmill'  no root motion. Velocity is measured relative to the root,
                   and a planted foot is locked to an exact constant-velocity
                   line at the shared stance (belt) speed, height pinned to the
                   ground. foot_slide (auto) measures ~0 on the result.
      'root'       as treadmill, then key the root bone with -belt per frame so
                   the body travels and every planted foot is stationary in
                   WORLD space. Root location is keyed LINEAR, no cycle modifier.
    swing
      'smooth'     rebuild each swing as a C1 Hermite path from lift-off to
                   touch-down with the belt velocity at both ends, keeping the
                   original peak clearance. Removes the velocity pops at
                   lift-off/strike that the authored FK keys carry.
      'keep'       keep the authored swing; blend the lock offset out over
                   blend_frames frames each side (smoothstep).

    THE SOLVE (no IK constraints): per frame, an analytic two-bone IK places
    the knee in the plane of the thigh's hinge axis, on the side the REST pose
    bends (hyperextended frames are corrected, counted in knee_side_fixed).
    The foot keeps its authored world orientation. The new pose matrices are
    then written to the FK controls PARENTS-FIRST with rig.convert_space(POSE
    -> LOCAL) and a depsgraph update between bones, keyed every frame, with
    frame end+1 = frame start and a CYCLES modifier (cyclic=True).

    The rig must be in FK for the legs (rig.set_ik_fk(rig, 1.0)).
    Measured on the 706-bone Rigify rig, 24 frames, 2 feet: ~1.5 s.

    Returns a report: belt, contacts per foot, residual between the deform
    ankle and its target after baking, reach clamps, root keyed.
    """
    from . import rig as RIG
    sc = bpy.context.scene
    start = sc.frame_start if start is None else start
    end = sc.frame_end if end is None else end
    if mode not in ("treadmill", "root"):
        raise ValueError("mode must be 'treadmill' or 'root'")
    if swing not in ("smooth", "keep"):
        raise ValueError("swing must be 'smooth' or 'keep'")
    frames = list(range(start, end + 1))
    N = len(frames)
    chains = chains or {}
    legs = {f: tuple(chains.get(f) or RIG.leg_chain(rig, f)) for f in foot_bones}
    root = root or RIG.root_bone(rig)
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    switches = RIG.ik_fk_switches(rig)
    in_ik = [b for b, k in switches.items()
             if any(t in b for t in ("thigh", "leg")) and float(rig.pose.bones[b][k]) < 0.999]

    # ---- sample the authored motion (armature space)
    smp = {f: {"Mt": [], "Ms": [], "Mf": [], "A": []} for f in legs}
    R0, Mroot = [], []
    for fr in frames:
        sc.frame_set(fr)
        pr = rig.pose.bones[root]
        R0.append(pr.head.copy())
        Mroot.append(pr.matrix.copy())
        for foot, (t, s, ft) in legs.items():
            pb = rig.pose.bones
            smp[foot]["Mt"].append(pb[t].matrix.copy())
            smp[foot]["Ms"].append(pb[s].matrix.copy())
            smp[foot]["Mf"].append(pb[ft].matrix.copy())
            smp[foot]["A"].append(pb[s].tail.copy())

    # ---- contacts, with ONE shared belt (all feet stand on the same ground)
    rel = {}
    for foot in legs:
        A_ = smp[foot]["A"]
        rel[foot] = [Vector((A_[i].x - R0[i].x, A_[i].y - R0[i].y)) for i in range(N)]
    pooled = []
    for foot in legs:
        _, b = contact_frames([a.z for a in smp[foot]["A"]], rel[foot], None,
                              height_tol, vel_tol, cyclic=cyclic, min_run=min_run)
        pooled.append(b)
    belt = _median_vec(pooled) if len(pooled) > 1 else pooled[0]
    runs = {}
    for foot in legs:
        runs[foot], _ = contact_frames([a.z for a in smp[foot]["A"]], rel[foot], belt,
                                       height_tol, vel_tol, cyclic=cyclic, min_run=min_run)
    # refine the belt: pooled least-squares slope over every contact run
    num = Vector((0.0, 0.0)); den = 0.0
    for foot in legs:
        for r in runs[foot]:
            L = len(r); kb = (L - 1) / 2
            pbar = sum((rel[foot][i] for i in r), Vector((0.0, 0.0))) / L
            for k, i in enumerate(r):
                num += (rel[foot][i] - pbar) * (k - kb)
                den += (k - kb) ** 2
    if den > 0:
        belt = num / den

    # ---- targets in root-relative xy + absolute z
    targets, contact = {}, {}
    for foot in legs:
        A_ = smp[foot]["A"]
        P = [Vector((rel[foot][i].x, rel[foot][i].y, A_[i].z)) for i in range(N)]
        T = [None] * N
        inc = [False] * N
        for r in runs[foot]:
            L = len(r); kb = (L - 1) / 2
            c = sum((rel[foot][i] - belt * (k - kb) for k, i in enumerate(r)),
                    Vector((0.0, 0.0))) / L
            # ground = the lowest contact height, raised just enough that the
            # leg can reach it on every frame of the run (no hip drop here)
            ground = min(A_[i].z for i in r)
            for k, i in enumerate(r):
                xy = c + belt * (k - kb)
                Hh = smp[foot]["Mt"][i].translation
                reach = ((smp[foot]["Ms"][i].translation - Hh).length
                         + (A_[i] - smp[foot]["Ms"][i].translation).length) * 0.9995
                dxy = (Vector((xy.x + R0[i].x, xy.y + R0[i].y)) - Hh.xy).length
                if dxy < reach:
                    ground = max(ground, Hh.z - math.sqrt(reach * reach - dxy * dxy))
            for k, i in enumerate(r):
                xy = c + belt * (k - kb)
                T[i] = Vector((xy.x, xy.y, ground))
                inc[i] = True
        rs = runs[foot]
        if not rs:
            T = [p.copy() for p in P]
        else:
            order = sorted(rs, key=lambda r: r[0])
            for ri, r in enumerate(order):
                if ri + 1 < len(order):
                    nxt = order[ri + 1]
                elif cyclic:
                    nxt = order[0]
                else:
                    nxt = None
                a_end = r[-1]
                if nxt is None:
                    gap = list(range(a_end + 1, N))
                else:
                    b0 = nxt[0]
                    m = (b0 - a_end - 1) % N if cyclic else b0 - a_end - 1
                    gap = [(a_end + 1 + j) % N for j in range(m)]
                if not gap:
                    continue
                h = len(gap) + 1
                p0 = T[a_end]
                p1 = T[nxt[0]] if nxt is not None else None
                if swing == "smooth" and p1 is not None:
                    clear = max(P[g].z for g in gap) - max(p0.z, p1.z)
                    for j, g in enumerate(gap, start=1):
                        t = j / h
                        h00 = 2 * t ** 3 - 3 * t ** 2 + 1
                        h10 = t ** 3 - 2 * t ** 2 + t
                        h01 = -2 * t ** 3 + 3 * t ** 2
                        h11 = t ** 3 - t ** 2
                        xy = (p0.xy * h00 + belt * (h * h10) + p1.xy * h01 + belt * (h * h11))
                        z = p0.z + (p1.z - p0.z) * _smoothstep(t) + max(clear, 0.0) * math.sin(math.pi * t) ** 2
                        T[g] = Vector((xy.x, xy.y, z))
                else:
                    d0 = T[a_end] - P[a_end]
                    d1 = (T[nxt[0]] - P[nxt[0]]) if nxt is not None else Vector((0, 0, 0))
                    B = max(1, blend_frames)
                    for j, g in enumerate(gap, start=1):
                        w0 = _smoothstep(1 - j / (B + 1))
                        w1 = _smoothstep(1 - (h - j) / (B + 1)) if nxt is not None else 0.0
                        T[g] = P[g] + d0 * w0 + d1 * w1
            if not cyclic:
                first = order[0][0]
                d1 = T[first] - P[first]
                B = max(1, blend_frames)
                for i in range(first):
                    w = _smoothstep(1 - (first - i) / (B + 1))
                    T[i] = P[i] + d1 * w
        targets[foot] = [Vector((T[i].x + R0[i].x, T[i].y + R0[i].y, T[i].z)) for i in range(N)]
        contact[foot] = inc

    # ---- root motion
    D = [Vector((0.0, 0.0, 0.0)) for _ in range(N)]
    if mode == "root":
        D = [Vector((-belt.x * i, -belt.y * i, 0.0)) for i in range(N)]

    # ---- solve every frame
    solved = {f: [] for f in legs}
    clamps, side_fixed, contact_clamps = 0, 0, 0
    for foot, (t, s, ft) in legs.items():
        bt, bs = rig.data.bones[t], rig.data.bones[s]
        Hr, Kr, Ar = bt.head_local, bs.head_local, bs.tail_local
        Xr = bt.matrix_local.to_3x3().col[0]
        sr = (Kr - Hr).cross(Ar - Kr).dot(Xr)
        side_ref = (1 if sr > 0 else -1) if abs(sr) > 1e-9 else 0
        for i in range(N):
            Mt, Ms, Mf = smp[foot]["Mt"][i], smp[foot]["Ms"][i], smp[foot]["Mf"][i]
            H, K, A_ = Mt.translation, Ms.translation, smp[foot]["A"][i]
            X = Mt.to_3x3().normalized().col[0]
            so = (K - H).cross(A_ - K).dot(X)
            if side_ref and so * side_ref < 0:
                side_fixed += 1
            Kn, Tn, cl, n = _two_bone(H, K, A_, X, targets[foot][i], side_ref or (1 if so >= 0 else -1))
            clamps += int(cl)
            if cl and contact[foot][i]:
                contact_clamps += 1
            Rt_old = Mt.to_3x3().normalized()
            Rt_new = _frame(n, (Kn - H).normalized())
            Rrel = Rt_new @ Rt_old.transposed()
            Rs1 = Rrel @ Ms.to_3x3().normalized()
            Rs_new = Rs1.col[1].rotation_difference((Tn - Kn).normalized()).to_matrix() @ Rs1
            out = []
            for R3, pos, old in ((Rt_new, H, Mt), (Rs_new, Kn, Ms), (Mf.to_3x3().normalized(), Tn, Mf)):
                M4 = (R3 @ Matrix.Diagonal(old.to_scale())).to_4x4()
                M4.translation = pos + D[i]
                out.append(M4)
            solved[foot].append(out)

    # ---- bake parents-first with convert_space
    rot_paths = set()
    for foot, names in legs.items():
        for nme in names:
            pb = rig.pose.bones[nme]
            ch = "rotation_quaternion" if pb.rotation_mode == "QUATERNION" else (
                "rotation_axis_angle" if pb.rotation_mode == "AXIS_ANGLE" else "rotation_euler")
            rot_paths.add(f'pose.bones["{nme}"].{ch}')
    if mode == "root":
        rot_paths.add(f'pose.bones["{root}"].location')
    removed = _remove_curves(rig, rot_paths)

    prev = {}
    pr = rig.pose.bones[root]
    for i, fr in enumerate(frames):
        sc.frame_set(fr)
        if mode == "root":
            want = Matrix.Translation(D[i]) @ Mroot[i]
            pr.matrix_basis = rig.convert_space(pose_bone=pr, matrix=want,
                                                from_space="POSE", to_space="LOCAL")
            bpy.context.view_layer.update()
            pr.keyframe_insert("location", frame=fr, group=root)
        for foot, names in legs.items():
            for nme, M4 in zip(names, solved[foot][i]):
                pb = rig.pose.bones[nme]
                Mb = rig.convert_space(pose_bone=pb, matrix=M4, from_space="POSE", to_space="LOCAL")
                if pb.rotation_mode == "QUATERNION":
                    q = Mb.to_quaternion()
                    if nme in prev:
                        q.make_compatible(prev[nme])
                    pb.rotation_quaternion = q
                    prev[nme] = q.copy()
                    ch = "rotation_quaternion"
                else:
                    e = Mb.to_euler(pb.rotation_mode, prev[nme]) if nme in prev else Mb.to_euler(pb.rotation_mode)
                    pb.rotation_euler = e
                    prev[nme] = e.copy()
                    ch = "rotation_euler"
                bpy.context.view_layer.update()
                pb.keyframe_insert(ch, frame=fr, group=nme)

    # close the cycle and loop the new curves; root travels linearly
    for fc in fcurves(rig):
        if fc.data_path not in rot_paths:
            continue
        if fc.data_path.endswith(".location") and mode == "root":
            fc.keyframe_points.insert(end + 1, fc.evaluate(end) + (fc.evaluate(end) - fc.evaluate(end - 1)))
            for k in fc.keyframe_points:
                k.interpolation = "LINEAR"
            fc.extrapolation = "LINEAR"
        elif cyclic:
            fc.keyframe_points.insert(end + 1, fc.evaluate(start))
            if not any(m.type == "CYCLES" for m in fc.modifiers):
                fc.modifiers.new("CYCLES")
        fc.update()

    # ---- verify on the DEFORM bones: where did the ankle actually land?
    resid = {}
    for foot, (t, s, ft) in legs.items():
        dn = deform_name(rig, ft)
        errs = []
        for i, fr in enumerate(frames):
            sc.frame_set(fr)
            pb = rig.pose.bones.get(dn) or rig.pose.bones[ft]
            want = targets[foot][i] + D[i]
            errs.append((pb.head - want).length)
        cerrs = [e for e, c in zip(errs, contact[foot]) if c]
        resid[foot] = {"deform_bone": dn,
                       "max_err_m": round(max(errs), 6),
                       "contact_max_err_m": round(max(cerrs), 6) if cerrs else None}
    sc.frame_set(start)
    return {
        "mode": mode, "swing": swing,
        "belt_m_per_frame": (round(belt.x, 5), round(belt.y, 5)),
        "stance_speed_m_per_frame": round(belt.length, 5),
        "root_bone": root if mode == "root" else None,
        "root_travel_m": round((D[-1] - D[0]).length + belt.length, 5) if mode == "root" else 0.0,
        "chains": {f: list(v) for f, v in legs.items()},
        "contacts": {f: [[frames[r[0]], frames[r[-1]]] for r in runs[f]] for f in legs},
        "contact_frames": {f: sum(contact[f]) for f in legs},
        "reach_clamped_frames": clamps,
        "reach_clamped_contact_frames": contact_clamps,
        "knee_side_fixed": side_fixed,
        "curves_replaced": removed,
        "legs_in_ik": in_ik,
        "residual": resid,
    }


# --------------------------------------------------------------- export
def _glb_summary(path):
    """Parse a .glb's JSON chunk and decode animation samplers. No deps."""
    import struct, json
    import numpy as np
    with open(path, "rb") as fh:
        blob = fh.read()
    magic, ver, total = struct.unpack_from("<III", blob, 0)
    if magic != 0x46546C67:
        raise ValueError("not a GLB")
    off, js, binc = 12, None, b""
    while off < len(blob):
        ln, typ = struct.unpack_from("<II", blob, off)
        chunk = blob[off + 8: off + 8 + ln]
        if typ == 0x4E4F534A:
            js = json.loads(chunk)
        elif typ == 0x004E4942:
            binc = chunk
        off += 8 + ln
    comp = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

    def acc(i):
        a = js["accessors"][i]
        bv = js["bufferViews"][a["bufferView"]]
        dt = comp[a["componentType"]]
        n = a["count"] * width[a["type"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        arr = np.frombuffer(binc, dtype=dt, count=n, offset=start)
        return arr.reshape(a["count"], width[a["type"]])

    anims = []
    for an in js.get("animations", []):
        t0, t1, travel = 1e9, 0.0, {}
        for ch in an["channels"]:
            smp = an["samplers"][ch["sampler"]]
            t = acc(smp["input"])
            t0, t1 = min(t0, float(t.min())), max(t1, float(t.max()))
            if ch["target"]["path"] == "translation":
                v = acc(smp["output"])
                node = js["nodes"][ch["target"]["node"]].get("name", str(ch["target"]["node"]))
                travel[node] = float(np.linalg.norm(v[-1] - v[0]))
        top = max(travel.items(), key=lambda kv: kv[1]) if travel else (None, 0.0)
        anims.append({"name": an.get("name"), "channels": len(an["channels"]),
                      "start_s": round(t0, 4), "duration_s": round(t1 - t0, 4),
                      "max_translation_travel": (top[0], round(top[1], 4)),
                      "hips_travel": {k: round(v, 4) for k, v in travel.items()
                                      if k in ("DEF-spine", "DEF-hips", "DEF-pelvis", "hips", "pelvis")}})
    skins = js.get("skins", [])
    return {"animations": anims, "joints": len(skins[0]["joints"]) if skins else 0,
            "nodes": len(js.get("nodes", [])), "bytes": len(blob)}


def export_clip_gltf(rig, mesh, path, start=None, end=None, clip_name=None, fmt="GLB"):
    """Export the rig's animation as named glTF clip(s).

    Uses export_animation_mode='ACTIONS': every action that is active or on an
    unmuted NLA strip becomes ONE named glTF animation. Do not use
    gltf_action_filter - it matches actions by Python id(), which is fragile.
    In 5.x a strip whose action_slot is unset is SKIPPED by the exporter, and so
    is an active action with no slot, so both are assigned here.

    Deform bones only, 4 influences, force sampling (constraints are evaluated
    and baked per frame by the exporter). Returns the parsed GLB summary:
    animations (name, channels, duration, the translation channel that travels
    furthest - i.e. where root motion landed), joints, bytes.
    """
    sc = bpy.context.scene
    ad = rig.animation_data
    if ad is None or ad.action is None and not ad.nla_tracks:
        raise RuntimeError(f"{rig.name} has no animation to export")
    if ad.action is not None:
        if clip_name:
            ad.action.name = clip_name
        # a cycle carries a seam key at end+1; without a manual range the clip
        # would include it and play the first pose twice per loop
        if start is not None and end is not None:
            ad.action.use_frame_range = True
            ad.action.frame_start = start
            ad.action.frame_end = end
        if getattr(ad, "action_slot", None) is None and ad.action.slots:
            ad.action_slot = ad.action.slots[0]
    for tr in ad.nla_tracks:
        for st in tr.strips:
            if st.action and getattr(st, "action_slot", None) is None and st.action.slots:
                st.action_slot = st.action.slots[0]
    if start is not None:
        sc.frame_start = start
    if end is not None:
        sc.frame_end = end
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB" if fmt.upper() == "GLB" else "GLTF_SEPARATE",
        use_selection=True,
        export_apply=True,
        export_skins=True,
        export_def_bones=True,
        export_influence_nb=4,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_force_sampling=True,
        export_frame_range=True,
        export_anim_slide_to_zero=True,
    )
    out = {"path": path}
    if fmt.upper() == "GLB":
        out.update(_glb_summary(path))
    return out
