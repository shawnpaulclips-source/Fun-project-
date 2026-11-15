# Sample outputs

Produced by the examples on a 4-core container with no GPU.

- `character_walk.glb` — the rigged, animated character from examples 02 + 03.
  160 deform bones (down from 706 via `export_def_bones`), 4 influences per
  vertex, baked animation. Drop it in any glTF viewer.
- `walk.mp4` — 24-frame Workbench playblast, ~0.11 s/frame to render.
- `rig_manifest.json` — the contract the animation layer reads so it never has
  to pattern-match a bone name.
- `critique.json` — before/after metrics from the polish pass. The FK walk scores
  52/100; the remaining failures are foot slide and jerk, which need IK foot
  planting. See README section 8.
