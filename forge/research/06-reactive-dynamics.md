# forge — 06 Reactive dynamics

Design doc. Siblings: `02-rigging.md` (skeletons, controls), `03-animation.md` (F-curves, critique loop).

Measured numbers are marked **[measured]**. Anything from a web page or from memory is marked **[read]** or **[unverified]**. Measurements were taken in this container: CPython 3.13.18, bpy 5.3.0, 4 cores, no GPU, llvmpipe env from `forgekit/env.py`.

---

## 1. Verdict (10 lines)

1. A spring-chain integrator in numpy is the right core. It runs headless, has no bpy dependency at run time, and costs **0.35–0.5 ms per frame** for 8 chains of 8 bones at 4 substeps × 2 iterations. **[measured]**
2. Cost scales with **bone count × iterations × substeps**, not with mesh vertex count. That is the entire speed argument.
3. Use **position-based dynamics with XPBD-style compliance** for the chain constraints, plus **angular springs** toward a rest pose for the rig-facing behaviour. Positional springs alone make bones stretch and look like jelly.
4. Blender's softbody on a tube of 160 verts costs **1.33 ms/frame**, 576 verts **4.25 ms**, 2176 verts **24.6 ms**. **[measured]** A `100x faster than sim` figure does not hold against that. At 160 verts the ratio is about 3x. The ratio only grows past that if you compare against a cloth sim with thousands of verts, which I did not measure.
5. Stability is a function of substeps and iterations. At 60 fps over 10 s on an 8-bone chain: 4 substeps × 2 iters gives 1.0 % max stretch; 8 × 4 gives 0.12 %. **[measured]**
6. Touch is an impulse on bone velocities plus sphere/capsule/plane colliders. No mesh collision. Contact cost is a few dot products per bone per substep.
7. Layer dynamics **additively** on keyframed animation: compute `pose_out = pose_anim + w · (pose_sim − pose_anim)` per bone, with `w` animator-controlled. Poses stay intact at `w = 0`.
8. Ship it as (a) a live depsgraph handler for review, (b) a baked-keyframe post-pass for final, and (c) VRM `VRMC_springBone` JSON for engines. GlTF core carries none of it.
9. `anim.offset_chain` is a keyframe time-shift and cannot react to anything. Dynamics replaces it for live reaction. `anim.add_noise` is idle life and complements dynamics as an excitation source, it is not replaced.
10. Unverified: Unity, Unreal and Godot behaviour beyond what the search results showed; per-frame timing inside a live Blender viewport; softbody numbers on a real cloak mesh; licence terms for each addon (not checked).

---

## 2. What exists in the wild

| System | Algorithm | Exposed parameters | Licence | Headless Python |
|---|---|---|---|---|
| **VRM SpringBone 1.0** (`VRMC_springBone`) | Per-joint verlet-style inertia with drag, a stiffness term toward rest direction, and gravity. Bevy's implementation computes next tail = inertia × (1 − drag) + stiffness toward rest + gravity, then renormalises to bone length. **[read]** | stiffness, gravityPower, gravityDir, dragForce, hitRadius, per-chain colliders | VRM spec is open; check `vrm-c/vrm-specification` for the exact licence **[unverified]** | Yes. The data is JSON inside the glTF extension, so any Python that reads glTF can read it. Running it needs a port. |
| **Unity Dynamic Bone** and successors (incl. Magica Cloth, PhysBone) | Verlet per-particle with distance and angular constraints, collision spheres and capsules | Damping, elasticity, stiffness, inert, radius, gravity, freeze, end-length | Commercial (Dynamic Bone) **[unverified]** | No. C# runtime only. |
| **Unreal AnimDynamics** and Rigid Body with Kinematic | AnimDynamics: per-node spring/damper driven by component space transforms. Rigid Body nodes: PhysX bodies with constraints | Spring stiffness, damping, gravity override, limits | Engine EULA **[unverified]** | No. |
| **Godot `SpringBoneSimulator3D`** | Per-joint stiffness, drag and gravity, with chain-level get/set and damping curves. External force is a separate vector. **[read]** | stiffness, drag, gravity (magnitude + direction), external_force, per-joint config | MIT engine **[unverified for this class]** | No. Godot runtime. |
| **Blender Wiggle 2** (shteeve3d) | Per-bone wiggle with mass, stiff, damp, sync to bone sets. Frame-stepped in the viewport. | mass, stiff, damp, limits, sync | GPL-type addon **[unverified]** | Partial. Addon operator runs inside Blender only. |
| **Wiggle Bones / Wiggle 2 RTX** (forks) | Forks of Wiggle 2 aimed at Blender 4.5/5.0. Wiggle Bones 1.0.1 lists 4.5 LTS+. Wiggle 2 RTX v2.2.4 lists 4.2+ and claims 5.0/5.1. **[read]** | As Wiggle 2 | Check per fork **[unverified]** | Same as Wiggle 2. |
| **Jiggle Physics** (naelstrof fork) | Verlet, real-time in viewport. 2025 release, Blender 5.0 support not stated. **[read]** | As Wiggle 2 | **[unverified]** | Same. |
| **Spring Bones** addon | One guide says the original works up to 4.5 and is broken in 5.0, with a fork for 5.0. Date of the guide unknown. **[read]** | | **[unverified]** | Same. |
| **Jiggle Armature** | Not searched. | | | |

Blender addons all drive a viewport-stepped sim inside the UI. None of them exposes a clean, headless, batched step function, so the kit cannot call them from a batch job without a bpy session and a window. **That is the reason to write a new integrator and not wrap one.**

---

## 3. The maths

### 3.1 State and step

For a chain of `N` bones there are `N+1` particles: particle 0 is the root joint, particle `i` is the tail of bone `i-1`. Positions `p_i`, previous positions `p̄_i`, inverse masses `w_i` (0 for pinned), rest lengths `L_i` between `p_i` and `p_{i+1}`.

Step with substep `h = dt / S`:

```
v_i   = (p_i − p̄_i) / h · (1 − c)                  # velocity with drag c per substep
p̄_i   = p_i
p_i   = p_i + h·v_i + h²·g · [w_i > 0]               # semi-implicit Euler, gravity
p_i   = p_i + h² · k_a(θ_rest − θ_i) · ...           # angular spring (see 3.4)
```

Then `I` iterations of the distance projection (3.2), then `p_i` is final for the substep. Velocity for the next substep comes from the post-projection positions, which is the PBD convention and what keeps the chain consistent.

Semi-implicit Euler over Verlet: same cost, and the drag term is a direct multiplier on velocity, so the drag range is in obvious units. Verlet gives identical positions when there is no drag, so the choice only changes how drag is expressed. RK4 is wrong here: four force evaluations per substep for no gain, because the constraint projection is non-smooth and dominates the error anyway. Games use semi-implicit or verlet with a fixed small step because the cost is in the projection, not the integrator.

### 3.2 Distance constraint, PBD and XPBD

PBD projects each constraint `C(p) = |p_j − p_i| − L` by moving the particles directly:

```
Δp_i = −w_i · C · n / (w_i + w_j),       Δp_j = +w_j · C · n / (w_i + w_j)      with n = (p_j − p_i)/|p_j − p_i|
```

PBD has no stiffness. Stiffness depends on iteration count and substep size, so it looks different at different frame rates. XPBD fixes this with a compliance `α` (units m/N, the inverse of stiffness) and an accumulated multiplier `λ`:

```
α̃ = α / h²
Δλ = (−C − α̃ · λ) / (w_i + w_j + α̃)
λ  = λ + Δλ
Δp_i = −w_i · Δλ · n,      Δp_j = +w_j · Δλ · n
```

Set `α = 0` for an inextensible bone (behaves like PBD with infinite stiffness). For a stretch limit `s`, use `α` such that the maximum stretch under a known load is `s · L`. In practice: `α = 0` on the chain itself, and stretch is limited by an explicit clamp (3.6). Use compliance only on the springy connections (hair length, cloak attachment).

`λ` resets to zero at the start of each substep, not each frame. Resetting per frame is the usual bug.

Substeps versus iterations: substeps change the physics (they set how fast forces integrate). Iterations only tighten the constraint. Substeps are the knob for stability and realism. Iterations are the knob for stretch. Measured below in §5.

### 3.3 Follow-the-leader and FABRIK

For an inextensible chain driven by a moving root and no spring, FABRIK gives the same result as a single PBD sweep from root to tip, in one forward and one backward pass, and it is exact for inextensible lengths. FABRIK has no velocity, so it cannot give inertia or overshoot. Use it only as a shape-only follow-through for a chain that must stay at length and has no dynamics. Dynamics needs velocity, so the PBD integrator is the default.

### 3.4 Positional springs versus angular springs on a bone chain

- **Positional spring**: `F = −k (p − p_target)`, where `p_target` is the tip of the rest pose transformed by the parent. Cheap. Gives a spring on the joint position. On a bone chain it stretches the bone length if the spring is stiff and the length constraint is soft. It fights IK and looks wrong on limbs.
- **Angular spring**: torque `τ_i = −k_θ · θ_i − c_θ · ω_i`, where `θ_i` is the bone's deviation from its rest direction in the parent frame and `ω_i` its angular velocity. Realised by moving the tail along the sphere of radius `L_i` toward the rest direction, which keeps length exact. **This is the one to use on a rig.** It gives natural bending, cannot stretch bones, and maps directly to the animator's notion of stiffness.

Recommended: angular spring for every bone, positional constraint only on the root and collision contacts.

Angular spring update per bone (after the velocity step, before the distance projection):

```
d_rest  = normalize(parent_frame · rest_dir_i)           # rest direction in world
d_cur   = normalize(p_{i+1} − p_i)
axis    = normalize(cross(d_cur, d_rest))
angle   = acos(clamp(dot(d_cur, d_rest), −1, 1))
p_{i+1} += h² · (k_θ · angle) · axis · L_i               # tangential pull on the tail
p_{i+1} −= h · c_θ · (ω_i × L_i)                         # angular damping
```

The `h²` and `h` factors are the explicit-Euler form. With `k_θ` in units of `1/s²` the integrator is stable for `h · sqrt(k_θ) < 2` (see 5.1).

### 3.5 Contact

Collision is a sphere, capsule or plane test per particle per substep. For sphere with centre `c`, radius `r` (including hitRadius):

```
d = p − c;  if |d| < r:  n = d/|d|;  p += (r − |d|) · n      # projection
v_n = dot(v, n);  if v_n < 0:  v −= (1 + e) · v_n · n          # restitution e
v_t = v − dot(v, n) · n;  v −= μ · |v_n| · normalize(v_t) · clamp(…)   # Coulomb friction
```

Impulse from touch: an external contact sets `v_i += J / m_i` on the particles at the contact point, where `J` is taken from the velocity of the touching object or a user-specified impulse. For "poke it and it wobbles": one impulse per poke event, `J = m_contact · Δv` for `Δv` measured from the hand bone, applied to the nearest 2–3 particles with a falloff `exp(−d/σ)` with σ ≈ one bone length.

Cost is one sphere test per collider per particle per substep. For 8 chains × 8 particles × 2 spheres × 4 substeps that is 512 dot products per frame. It is not measured here; it is well under a tenth of the projection cost.

### 3.6 Stability

Why chains explode:
- Explicit springs are stable only when `h · ω < 2`, where `ω = sqrt(k/m)`. With `m = 1` and `k = 10⁴`, `ω = 100` and `h` must be `< 0.02`. At 60 fps / 4 substeps, `h = 1/240 ≈ 0.0042`, so `k ≤ 2.2 × 10⁵` is stable. By this bound `k_θ` up to about 2000 should be stable at 4 substeps, but that was not tested; §5 only covers the stiffness used there.
- Constraint iterations too low for chain length: stretch under load grows with chain length when iterations are fixed. The scaling law is read, not fitted. Measured trend in §5.
- Velocity explosions from a large impulse: clamp the per-substep displacement to `0.5 · L_i`.

Fixes, with working ranges:

| Fix | Range | Why |
|---|---|---|
| Critical damping | `c = 2 · sqrt(k · m)`; use `c = 0.8 · c_crit` to `1.0 · c_crit` | Settles with at most one overshoot. Below 0.5 · c_crit it buzzes. |
| Velocity clamp | `|v| ≤ 0.5 · L_i / h` | Stops a single impulse from tunnelling a collider. |
| Stretch clamp (post-projection) | `|L − L_rest| ≤ s · L_rest`, `s` in 0.005–0.05 | Hard floor on stretch regardless of iteration count. |
| Iterations | `I ≥ N/2` for N-bone chains, minimum 2 | Below this, stretch grows with chain length (§5). |
| Substeps | 4 minimum, 8 for antennae and cloak | Explicit stability and frequency response. |

---

## 4. Blender, the cost, and where time goes

**Where softbody time goes [read, plausibly]**: Blender's softbody is a mass-spring system on mesh vertices plus edges and faces, with a collision step over the whole mesh. Cost per step is vertices + edges + faces, plus collision broadphase. Its step count is adaptive (`step_min`, `step_max`), so a bake costs more at high speed. I did not profile Blender's internals, so the breakdown above is read, not measured.

**Why the integrator is cheaper [measured, mechanism partly read]**: it works on `C × N` particles and `C × N` segments. It has no mesh, no broadphase and no adaptive step. The numpy form vectorises over all chains at once, so cost scales with `N × I × S` vector operations, not with `C × N` loop iterations.

The first version looped in Python, one numpy call per segment per iteration. It took 1.7 ms for 8 chains × 8 bones at 4 × 2, against 0.42 ms for the red-black (even/odd) version, which uses two numpy calls per iteration. **[measured]** The red-black form is the one to keep.

### Target numbers, one CPU core, 60 fps, 4 substeps, 2 iterations

| Chains × bones | Particles | Measured ms/frame, red-black numpy | Notes |
|---|---|---|---|
| 1 × 8 | 9 | 0.35 | **[measured]** |
| 8 × 8 | 72 | 0.42–0.47 | **[measured]** — the design target |
| 16 × 8 | 144 | 0.48–0.51 | **[measured]** |
| 64 × 16 | 1088 | 1.10 | **[measured]** |
| 256 × 16 | 4352 | 3.36 | **[measured]** |
| 1024 × 8 | 9216 | 6.7–6.9 | **[measured]** |

Frame budget: 1 ms of CPU on one core covers about 100 chains of 8 bones at 4 × 2 with headroom for the bpy frame handler. A character with 6 chains (spine, tail, two hair strands, cloak, antenna) runs in about 0.3 ms.

Earlier loop-form numbers, same machine: 8 × 8 = 1.7 ms, 16 × 16 = 3.6 ms, 64 × 16 = 2.9 ms. The 64 × 16 figure being below the 16 × 16 figure is measurement noise at this size, not a real effect; treat the loop-form column as an upper bound.

A real armature, built with bpy (`armature_add`, 8 chains of 8 edit bones, 64 bones total), read into the integrator via `bone.tail_local`: **0.99–1.01 ms/frame** with the loop-form code. This is the only number here that comes from an actual rig rather than a synthetic chain. **[measured]**

### Blender softbody on a tube, same machine, 60 fps, step 5 (sequential `frame_set`, 20 frames timed)

| Verts | ms/frame | Note |
|---|---|---|
| 160 | 1.33 | **[measured]** |
| 576 | 4.25 | **[measured]** |
| 2176 | 24.57 | **[measured]** |

Comparison on the same hardware: at 160 verts softbody is 1.33 ms and the red-black integrator on a comparable bone count is 0.42 ms. That is 3x. At 576 verts the gap is about 10x against the integrator at 8 × 8 (0.42 ms). A clean `100x` claim needs a cloth mesh with several thousand verts against a 16-bone integrator. I did not measure that pair.

**Verdict on "100x"**: marketing. The speed comes from skipping the mesh, not from a constant factor you can quote. The honest figure is "about 3x at tube scale, growing with mesh size, and not comparable to a rigged mesh's softbody" until measured otherwise.

Caveat on the softbody numbers: I stepped with `frame_set` once per frame from frame 1, which should force a real sim each step. I did not check the point cache, so a cached step could make them optimistic. The softbody modifier's pin group, mass and goal settings were set but not tuned for a realistic look; the timing is about cost, not behaviour.

---

## 5. Stability measurements

Setup: 4 chains × 8 bones, rest straight down, root pinned, gravity 9.81, damping 0.98 per substep, 600 frames (10 s at 60 fps). Max stretch is the largest segment deviation from rest, as a percentage of rest length.

| Substeps | Iterations | Max stretch % after 10 s | ms/frame |
|---|---|---|---|
| 4 | 2 | 1.00 | 0.59 |
| 4 | 4 | 0.48 | 0.80 |
| 4 | 8 | 0.22 | 1.63 |
| 8 | 2 | 0.25 | 0.95 |
| 8 | 4 | 0.12 | 1.58 |

**[measured]** Two findings:
- Substeps buy stretch and stability for less cost than iterations at this length: 8 × 2 beats 4 × 4 on stretch and costs about the same.
- The harness had a bug that I found and fixed while measuring: the rest length was recomputed from the current positions each call, which lets error accumulate. The table above is after the fix. Before the fix the same run showed 374 % stretch, which was wrong and should not be quoted.

Working recommendation: **8 substeps × 2 iterations** for hair and cloak, **4 × 2** for spine and belly, **8 × 4** for antenna.

---

## 6. Reacting to touch

- **Impulse**: `v_i += J / m_i` on the nearest particles, falloff `exp(−d/σ)`, `σ ≈ 1 bone`. One call per touch event.
- **Contact**: sphere or capsule collider per body part (head, chest, arm), positioned from the rig's deform bones at each frame. `hitRadius` in VRM terms.
- **Restitution `e`**: 0.0–0.3. Above 0.4 the chain bounces on the collider and looks like rubber.
- **Friction `μ`**: 0.3–0.8 for cloth on a body, 0.0–0.2 for hair on a sphere.
- **Wobble**: a poke gives an impulse and critical damping at `c ≈ 0.9 · c_crit` gives one clean wobble and settle in about 0.5 s.

Cost per frame for contacts is one dot product per particle per substep per collider. Estimated, not measured.

---

## 7. The reactive spine

The spine is a chain of `N` bones (typically `spine`, `chest`, `neck`, `head`), driven additively over the animator's keys.

**Additive layering.** For each bone `b` with animator rotation `q_anim(t)` and simulated rotation `q_sim(t)` from the integrator:

```
q_out = q_anim · slerp(identity, q_anim⁻¹ · q_sim, w_b)
```

`w_b` in `[0, 1]` is the per-bone blend. At `w_b = 0` the animator's pose is untouched. At `w_b = 1` the sim fully drives the bone. The animator's keys define the pose and dynamics only adds the delta. This is the same idea as an additive layer in an animation graph.

**Per-bone blend.** Controlled by a float per bone, keyed by the animator or driven by a limit. Ramp-in over 3–5 frames, ramp-out over 8–12 frames, so the sim does not pop when turned on.

**Lean and counter-rotation.** The sim is driven by the root's acceleration. Feed the root's frame-to-frame acceleration into the spine as an external force `F = −m · a_root` projected on the spine's local frame. The chest then leans into a sudden stop and counter-rotates when the root settles.

**Recovery.** Critical damping on the angular springs, so the spine returns to pose without overshoot once the forcing stops.

**Stopping IK fighting the sim.** Two rules:
1. Limbs with IK stay out of the sim. The dynamics chain runs only from spine to head, and the hands and feet remain IK-driven.
2. The dynamics chain's root is the pelvis/hips control, not a deform bone. The sim writes to the **dynamics** bones (an extra chain of bones parented to the spine's deform bones), and the deform bones copy from the dynamics bones with a Copy Rotation constraint at influence `w_b`. That way the rig's IK constraints never see a dynamics pose in their chain.

---

## 8. Three ways to ship it

| Form | What it is | When right | Cost |
|---|---|---|---|
| **(a) Live** | `bpy.app.handlers.frame_change_post` or a depsgraph update handler that steps the integrator and writes pose bone rotations | Reviewing a motion in the viewport; interactive poking | Per-frame CPU, 0.3–1 ms plus Blender's own cost |
| **(b) Baked** | Run the integrator over a frame range, write the result as keyframes on the dynamics bones, then the additive pass | Final motion for export. Deterministic and portable. Works in any engine | Offline. One pass over the frame range. Then it is plain F-curves, so `anim.decimate` and the critique loop still apply |
| **(c) Runtime data** | Export chains (bones, lengths, stiffness, drag, colliders) as `VRMC_springBone` JSON, and let the engine run it | Game engine runtime with real-time contact | Engine-side. Needs a port of the integrator |

**GlTF**: core glTF carries joint TRS and morph weights, with no dynamics and no constraints. **VRM's `VRMC_springBone` extension** carries the chain definition in JSON: per-joint `stiffness`, `gravityPower`, `gravityDir`, `dragForce`, `hitRadius`, and collider references (spheres and capsules). That is the portable format for (c). Anything the kit wants to express as dynamics should export there, plus baked keys for engines that ignore the extension.

**Baked is the default for final output.** Live is for reviewing. Runtime data is for games.

---

## 9. Overlap with the existing kit

| Existing | What it does | Replaced or complemented |
|---|---|---|
| `anim.offset_chain` | Shifts keyframe times along a chain, so a root motion lands on children later. Pure keyframe arithmetic | **Replaced** for any reactive case. It cannot respond to contact or to a change in the root's speed. It stays for purely authored overlap. |
| `anim.add_noise` | Perlin-type noise modifier on F-curves for idle life | **Complemented.** Noise is a good excitation source for dynamics: use its output as a small external force on the spine's root. Do not remove it. |
| `anim.close_loop` | Forces frame `end+1` to equal `start` on every curve | **Still needed** for cyclic animation. Run it after any baked dynamics pass, because the pass shifts keys in time the same way `offset_chain` does. |
| `anim.soften_handles`, `decimate` | Curve craft on keyed motion | **Still needed.** Baked dynamics keys are F-curves and benefit from `decimate`. |
| `anim.metrics`, `critique` | Jerk, foot slide, seam, arc | **Still needed.** Use `jerk_max` to check the dynamics pass isn't popping. |

---

## 10. Proposed API: `forgekit/dynamics.py`

Drop-in module. Imports `numpy`, and `bpy` only inside the rig-facing functions. The integrator (`ChainState`, `step`) has no bpy dependency.

```python
"""Reactive secondary dynamics: position-based chains with angular springs.

Runs headless. The integrator is pure numpy and does not import bpy.
The rig-facing functions (chains_from_rig, write_pose, bake) import bpy lazily.

Cost model, measured on 4 CPU cores with bpy 5.3.0:
    8 chains x 8 bones, 4 substeps x 2 iterations: 0.42 ms per frame.
Scales with chains x bones x substeps x iterations.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np


@dataclass
class ChainSpec:
    """Static description of one dynamics chain.

    name:            chain id, e.g. "spine", "tail", "hair_L_front".
    bones:           deform or dynamics bone names, root first.
    stiffness:       angular spring stiffness in 1/s^2. Range 2..2000.
    drag:            fraction of velocity lost per second, 0..1.
    gravity:         gravity scale, 0 = none, 1 = 9.81 m/s^2.
    stretch_limit:   max fractional stretch after projection, 0..0.05.
    compliance:      XPBD compliance on the length constraint (m/N). 0 = inextensible.
    substeps:        integrator substeps per frame. Default 4.
    iterations:      distance projection iterations per substep. Default 2.
    collider_names:  names of ColliderSpec entries this chain collides with.
    blend:           additive blend weight over the animator pose, 0..1.
    """
    name: str
    bones: list[str]
    stiffness: float = 200.0
    drag: float = 0.3
    gravity: float = 1.0
    stretch_limit: float = 0.01
    compliance: float = 0.0
    substeps: int = 4
    iterations: int = 2
    collider_names: list[str] = field(default_factory=list)
    blend: float = 1.0


@dataclass
class ColliderSpec:
    """Simple collision proxy attached to a bone.

    kind:   "sphere", "capsule" or "plane".
    bone:   bone name the collider follows, or None for world space.
    radius: sphere or capsule radius in metres.
    tail:   capsule second-end offset in bone space (capsules only).
    normal: plane normal (planes only).
    offset: plane offset, or the sphere centre offset in bone space.
    """
    name: str
    kind: str
    bone: str | None
    radius: float = 0.0
    tail: tuple = (0.0, 0.0, 0.0)
    normal: tuple = (0.0, 0.0, 1.0)
    offset: tuple = (0.0, 0.0, 0.0)


@dataclass
class ChainState:
    """Mutable particle state for a batch of chains.

    All chains in one batch must have the same bone count. Use chains_batch()
    to group them.
    """
    P: np.ndarray            # (C, N+1, 3) current particle positions
    Pprev: np.ndarray        # (C, N+1, 3) positions at start of substep
    rest_len: np.ndarray     # (C, N)     rest lengths
    rest_dir: np.ndarray     # (C, N, 3)  rest directions in parent frame
    inv_mass: np.ndarray     # (C, N+1)   0 for pinned particles
    stiffness: np.ndarray    # (C,)
    drag: np.ndarray         # (C,)
    gravity: np.ndarray      # (C,)
    stretch_limit: np.ndarray
    blend: np.ndarray        # (C,)


def new_state(specs: list[ChainSpec], rest_pose: dict[str, np.ndarray]) -> ChainState:
    """Build a batched ChainState from specs and a rest pose.

    rest_pose: {bone_name: (head(3), tail(3))} in world space at rest.
    The root particle of each chain is pinned (inv_mass = 0).
    Raises ValueError if chains in one batch disagree on bone count.
    """
    raise NotImplementedError  # filled in by the implementation pass


def step(state: ChainState, dt: float, targets: np.ndarray,
         forcing: np.ndarray | None = None,
         substeps: int = 4, iterations: int = 2,
         colliders: list[ColliderSpec] | None = None) -> np.ndarray:
    """Advance all chains by dt seconds.

    targets:  (C, N+1, 3) rest-pose particle positions transformed by the
              animator's pose at this frame. The angular spring pulls toward these.
    forcing:  (C, 3) external acceleration per chain (e.g. -root_accel for lean).
              None means zero.
    Returns the new particle positions, shape (C, N+1, 3). The state is updated in place.

    Semi-implicit Euler with angular spring, then red-black distance projection.
    Measured: 8x8 chains, 4 substeps x 2 iterations, 0.42 ms.
    """
    raise NotImplementedError


def drive_from_root_accel(state: ChainState, root_accel_world: np.ndarray,
                          chain_axes: np.ndarray) -> np.ndarray:
    """Turn root acceleration into per-chain forcing for a spine or tail.

    root_accel_world: (3,) root acceleration in world space, m/s^2.
    chain_axes:       (C, 3) unit vectors, chain rest directions in world space.
    Returns (C, 3) forcing: -root_accel projected onto the plane perpendicular
    to each chain's axis, so a chain leans into a stop and counter-rotates.
    """
    raise NotImplementedError


def impulse(state: ChainState, chain_idx: int, particle_idx: int,
            delta_v: np.ndarray, sigma_particles: float = 1.0) -> None:
    """Apply a poke: velocity change delta_v at a particle, falloff over neighbours.

    v_i += delta_v * exp(-|i - particle_idx| / sigma_particles).
    Implemented as an offset to Pprev so the velocity change lands on the
    next substep, which is the PBD convention.
    """
    raise NotImplementedError


def chains_from_rig(rig, specs: list[ChainSpec]):
    """Read rest pose and bone names from a bpy armature. Lazy bpy import.

    Returns (rest_pose dict, validated specs). Bone names are checked against
    rig.data.bones and a ValueError names any missing bone.
    """
    raise NotImplementedError


def write_pose(rig, state: ChainState, blend_by_bone: dict[str, float] | None = None) -> None:
    """Write simulated rotations to pose bones as an additive delta.

    q_out = q_anim * slerp(identity, q_anim^-1 * q_sim, w_b). Requires bpy.
    The dynamics bones are separate from deform bones; see section 7.
    """
    raise NotImplementedError


def bake(rig, specs: list[ChainSpec], frame_start: int, frame_end: int,
         colliders: list[ColliderSpec] | None = None,
         fps: float | None = None) -> dict:
    """Run the integrator over a frame range and key the dynamics bones.

    Reads the animator's pose at each frame via pose bone matrices, steps the
    integrator, writes the result with keyframe_insert. Returns a summary
    {"frames": n, "keys": k, "ms_per_frame": t}. Requires bpy.
    """
    raise NotImplementedError


def export_vrm_springs(specs: list[ChainSpec], colliders: list[ColliderSpec]) -> dict:
    """Serialise chains and colliders as a VRMC_springBone-shaped dict.

    Maps stiffness -> stiffness, drag -> dragForce, gravity -> gravityPower,
    and collider radius -> hitRadius. Check the output against the VRMC_springBone-1.0
    schema before shipping. Not verified against the schema in this pass.
    """
    raise NotImplementedError
```

Stubs above are for the spec only. `NotImplementedError` in this doc marks what the implementation pass has to write. This document does not claim the functions run.

---

## 11. Parameter table: working ranges per use case

Ranges come from engineering reasoning and the stability table in §5, not from a fitted model. Values in the **stability** column were measured. Everything else needs tuning against a real rig.

| Use case | stiffness (1/s²) | drag (per s) | gravity scale | stretch limit | substeps | iterations | Notes |
|---|---|---|---|---|---|---|---|
| **Spine** | 40–150 | 0.3–0.5 | 0.0–0.3 | 0.000 (angular only) | 4 | 2 | Lean and counter-rotation come from the root accel forcing, not from gravity. Keep gravity low so the spine stays upright. |
| **Tail** | 20–80 | 0.1–0.3 | 1.0 | 0.01 | 4–8 | 4 | Long chains need more iterations. Tail whip wants low drag. |
| **Hair** | 5–40 | 0.2–0.4 | 1.0 | 0.02 | 8 | 4 | Use compliance on root-to-tip springs so long strands can stretch slightly. |
| **Cloak** | 3–20 | 0.05–0.2 | 1.0 | 0.02 | 8 | 4 | Needs sphere colliders on the body. Without them it falls through the torso. |
| **Antenna** | 200–2000 | 0.02–0.1 | 0.0 | 0.00 | 8 | 4 | Highest frequency. Keep substeps high so the spring stays stable (`h · sqrt(k) < 2`). |
| **Belly** | 20–60 | 0.6–0.8 | 1.0 | 0.005 | 4 | 2 | Heavy drag, jiggle without ring-down. Wobble is the goal, not the spring. |

Measured stability at these values: 8 × 2 gives 0.25 % max stretch and 4 × 2 gives 1.0 % at 10 s (§5). The table's stretch limits should be above what those numbers show for the use case.

---

## 12. Not verified, and what was not checked

- **Unity Dynamic Bone, Unreal AnimDynamics, Godot SpringBoneSimulator3D**: described from search results and memory, not run. Godot's per-joint and chain-level settings are from the class reference, which I read as a search snippet only.
- **VRM SpringBone 1.0 schema**: the search did not return the spec text. The parameter names in §8 and §10 come from the VRM spec as commonly cited and from an implementation's source. Check against `vrm-c/vrm-specification` before shipping any export.
- **Licences**: not checked for any addon or engine. Do not assume a licence from the table above.
- **Blender addon compatibility with 5.3**: only what the search listed for 4.5/5.0. Not installed or run here.
- **Jiggle Armature**: not searched.
- **Cloak or hair on a real mesh**: all numbers are for synthetic chains. The bpy armature test used 64 bones with no mesh deformation, no skinning and no colliders.
- **Softbody**: one tube mesh at three sizes. Not tuned, not checked against the point cache, and not compared with a cloth sim at several thousand verts.
- **Contact cost**: estimated from operation count, not timed.
- **Stability**: 600 frames at one configuration per row. Not tested under large impulses, varying frame rates, or long-run drift past 10 s.
- **Live Blender handler cost**: not measured inside a running viewport. The 0.3–1 ms figure is the integrator alone.
- **The `dynamics.py` stubs** raise `NotImplementedError`. Writing and testing them is the next step. Nothing in §10 has been executed as a module.
