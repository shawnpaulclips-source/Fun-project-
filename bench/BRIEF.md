# Benchmark brief — identical for both agents

Build a short cinematic in Blender, headless, from procedural code. Characters,
environment, rigs, animation, camera and final render are all yours to make.
Nothing is provided as an asset. No downloads, no asset packs, no external meshes.

This is a fan recreation for a private technical benchmark. Stylised, low-poly,
N64-era proportions are the target — not photoreal.

## Cast you must model and rig

- **Mario** — red cap and shirt, blue overalls, white gloves, brown boots,
  moustache, big nose. Two states: SMALL (~0.9 units tall) and BIG (~1.5 units).
- **Luigi** — same construction, taller and thinner, green instead of red.
- **Koopa Troopa** — green shell with a rim, yellow-ish body, beak, feet. Needs a
  squash state and X eyes for the death beat.

## Environment you must model

- Ground blocks (tiled row), brick blocks, a **? / lucky block** with a visible
  question mark, a warp pipe, a coin or mushroom that pops out of the block,
  a spinning shell. Sky backdrop and a couple of clouds.

## Shot list — hit every beat, in this order

| # | Time | Beat |
|---|---|---|
| 1 | 0:00–0:03 | Side-on platformer framing. Ground, brick row, ? block floating. Small Mario and Luigi run in from screen left and stop under the ? block. |
| 2 | 0:03–0:06 | Both crouch and jump, heads hit the ? block from below. Block bounces up and settles, goes dim/used. A mushroom pops out of the top and arcs down. |
| 3 | 0:06–0:09 | Power-up. Both grow SMALL → BIG with the classic rapid size-flicker, then settle into the big pose. |
| 4 | 0:09–0:14 | Koopa Troopa walks in from screen right. Mario jumps, arcs, and stomps it. Koopa **squashes flat**, eyes become **X**, shell spins out sideways. |
| 5 | 0:14–0:18 | Mario and Luigi turn to face each other and **high-five**. The hands must actually meet in world space — anticipation, hard contact on one frame, follow-through, settle. |
| 6 | 0:18–0:20 | Camera pushes in on a hero pose. Hold, then out. |

## Camera

Mario-style. Mostly locked side-on platformer framing with the action parallel to
the camera plane. Earn two or three moves: a push-in on the stomp impact, a slow
dolly on the high five, a final push-in. No handheld shake, no random orbiting.

## Animation quality bar

- Anticipation before every jump and the high five. Follow-through after every landing.
- Arcs on every jump — a straight-line jump reads as broken.
- Squash and stretch on landings and on the stomp.
- Overlapping action: hips lead, chest follows, head last.
- Feet must not slide while planted.
- The high five must read as a real impact: approach, contact frame, recoil, settle.

## Deliverables, written to YOUR OWN directory only

1. `final.mp4` — 24 fps, 854×480 or better, Cycles. The whole shot list.
2. `contact_sheet.png` — tiled frames of the finished animation.
3. `stills/` — at least 4 Cycles beauty renders: Mario, Luigi, Koopa, and the high-five contact frame.
4. All your source `.py` files.
5. `REPORT.md` — see below.

## REPORT.md must contain

- Wall-clock time for each phase: modelling, rigging, animation, rendering.
- Every error you hit, what it cost you, and how you solved it. Be specific — the
  exact exception and the fix. This is the most valuable part of the report.
- What you had to cut and why.
- Honest self-assessment: which shots work, which don't, and what is wrong with
  the ones that don't.
- Final poly counts and render time per frame.

## Hard limits on scope

You have roughly 90 minutes of working time. **A complete 20-second video that
hits all six beats beats a gorgeous 5-second fragment.** If you are running long,
cut render quality and length, never beats. Minimum acceptable: 15 seconds
covering all six.

Render budget: this box has 4 CPU cores shared with another agent, so you get
about 2. Cycles at 854×480 with 24–32 samples and denoising is the target. Block
everything out with fast Workbench playblasts first and only commit to Cycles when
the timing is locked.

## Rules

- Work ONLY inside your assigned directory. Do not read or write the other
  agent's directory, and do not modify anything under `/home/user/Fun-project-/forge/`.
- Do not use the network for assets.
- Ship complete code. No stubs, no TODOs, no "rest of implementation".
