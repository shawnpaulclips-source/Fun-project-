# Findings from the face/anim build agent (stopped early, nothing committed by it)

Measured in-container before the stop. Treat as measurements, not finished modules.

## 1. The foot_slide "after" number is mostly a metric artefact
examples/03_walk_cycle.py passes expected_root_speed = path_length / 24 (about 0.0635 m/frame).
That is the foot's whole path including swing, not stance speed. Planted frames move only
0.005-0.010 m/frame, yet abs(d - expected) reports about 0.0635 anyway.
=> The 101 mm -> 64 mm improvement reported earlier is NOT a real improvement. Fix the
   expected speed (estimate it from contact frames) before quoting any foot_slide figure.

## 2. The walk is in place, with no root motion
In world space the planted foot moves backward at stride speed during stance (about
0.06 m/frame, frames 2-9). A speed threshold measured in world space will not find
contacts. plant_feet needs either treadmill mode (measure foot velocity relative to the
body/root) or real root motion added before locking.

## 3. Rig structure for plant_feet
- FK chain: shin_fk.L -> parent thigh_fk.L -> parent MCH-thigh_parent.L; foot_fk.L parent MCH-foot_fk.L
- FK controls have no constraints, no rotation locks, XYZ Euler.
- DEF-foot.L follows foot_tweak.L.
- Plan: IK constraint (chain 2, use_tail) on shin_fk targeting a keyed empty, world Copy
  Rotation on foot_fk, then bake FK controls parents-first with convert_space.

## 4. Rigify face metarig (Blender 5.3), source landmarks
- 92 descendant bones under `face` (faces.super_face)
- eye.L head (0.052, -0.121, 1.894)
- nose tip (0, -0.197, 1.845)
- jaw tail / chin (0, -0.152, 1.739)
- ear loop ear.L .. ear.L.004, starting near (0.092, -0.031, 1.862)
- brow.B.L chain; mouth corner at lip.T.L.001 tail (0.035, -0.149, 1.807)
- Rigify's own `jaw` head sits at (0, -0.094, 1.744): midline, near chin height, below the
  ear-canal line. Consistent with research 05 (jaw pivot belongs at the ear-canal line).
  Moving it and regenerating is still untested.

## 5. glTF animation export, Blender 5.3
- gltf_action_filter matches actions by Python id(): fragile. Use
  export_animation_mode='ACTIONS' instead.
- The exporter skips muted NLA strips; each single-strip track becomes one named animation.
- A strip needs action_slot set or the exporter ignores it.

## Baseline reproduced by the agent
- examples/02 rebuilt: 706 bones, 160 deform, 16.7 s wall.
- examples/03 walk: score 52/100; foot_slide 0.101 m before polish, 0.064 m after (see 1);
  jerk 0.340 -> 0.299 on DEF-foot.L/R.
