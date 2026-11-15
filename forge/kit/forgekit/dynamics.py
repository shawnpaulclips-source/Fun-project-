"""Reactive secondary dynamics: position-based bone chains with angular springs.

The integrator (ChainState, new_state, step, impulse, colliders) is pure numpy
and never imports bpy, so it runs and tests anywhere. The rig-facing functions
(chains_from_rig, write_pose, bake) import bpy lazily.

Model, per substep h = dt / substeps (research/06 sections 3-6):

    v  += h * (angular_spring + angular_damping + gravity + forcing)
    v  *= (1 - drag) ** h                    # drag is "fraction lost per second"
    |v| <= 0.5 * L / h                       # velocity clamp, stops tunnelling
    p  += h * v
    colliders (sphere / capsule / plane), friction on the tangential step
    XPBD distance projection, red-black, `iterations` sweeps, lambda reset
    every substep
    follow-the-leader stretch clamp, root to tip, only when needed
    v   = (p - p_start) / h                  # PBD velocity

The angular spring pulls each bone's tail toward its *rest direction carried by
the simulated parent* (the animated direction rotated by the parent's current
deviation), so a stiff chain bends as a unit and lengths are never touched by
the spring. Damping is on the tail's tangential velocity relative to its head,
c = 2 * damping_ratio * sqrt(stiffness); damping_ratio 1.0 is critical.

Two ways to drive a chain:
  * world-space: set targets each frame (the animated particle positions). The
    root particle is kinematic and follows targets[:, 0]; inertia emerges from
    the root's real motion. This is what bake() does.
  * in-place: leave the root fixed and pass root_accel to step(). The chain
    feels -root_accel (projected perpendicular to its axis), i.e. it leans into
    a stop. Use this for in-place cycles, never both at once.

Additive layering (write_pose / bake):
    q_out = q_anim * slerp(identity, q_anim^-1 * q_sim, w)
At w = 0 the keyframed pose is returned exactly.

Measured cost: see notes/agent_a.md and tests/test_dynamics.py (it prints the
ms/frame on this machine).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

GRAVITY = np.array([0.0, 0.0, -9.81])
_add = np.add.reduce

# ---------------------------------------------------------------- presets
# research/06 section 11, verbatim ranges. "value" is the chosen working point
# inside each range; ranges are what the research table states. The ear preset
# is NOT from the research table: it is interpolated between hair and antenna
# (cartilage is stiffer than hair, far softer than an antenna) and is untuned.
PRESETS: dict[str, dict] = {
    "spine": dict(stiffness=90.0, drag=0.4, gravity=0.15, stretch_limit=0.0,
                  substeps=4, iterations=2,
                  ranges=dict(stiffness=(40, 150), drag=(0.3, 0.5), gravity=(0.0, 0.3),
                              stretch_limit=(0.0, 0.0), substeps=(4, 4), iterations=(2, 2)),
                  notes="Lean and counter-rotation come from root accel forcing, not "
                        "gravity. Keep gravity low so the spine stays upright."),
    "tail": dict(stiffness=50.0, drag=0.2, gravity=1.0, stretch_limit=0.01,
                 substeps=8, iterations=4,
                 ranges=dict(stiffness=(20, 80), drag=(0.1, 0.3), gravity=(1.0, 1.0),
                             stretch_limit=(0.01, 0.01), substeps=(4, 8), iterations=(4, 4)),
                 notes="Long chains need more iterations. Tail whip wants low drag."),
    "hair": dict(stiffness=20.0, drag=0.3, gravity=1.0, stretch_limit=0.02,
                 substeps=8, iterations=4,
                 ranges=dict(stiffness=(5, 40), drag=(0.2, 0.4), gravity=(1.0, 1.0),
                             stretch_limit=(0.02, 0.02), substeps=(8, 8), iterations=(4, 4)),
                 notes="Use compliance on root-to-tip springs so long strands can "
                       "stretch slightly."),
    "cloak": dict(stiffness=10.0, drag=0.1, gravity=1.0, stretch_limit=0.02,
                  substeps=8, iterations=4,
                  ranges=dict(stiffness=(3, 20), drag=(0.05, 0.2), gravity=(1.0, 1.0),
                              stretch_limit=(0.02, 0.02), substeps=(8, 8), iterations=(4, 4)),
                  notes="Needs sphere colliders on the body. Without them it falls "
                        "through the torso."),
    "antenna": dict(stiffness=800.0, drag=0.05, gravity=0.0, stretch_limit=0.0,
                    substeps=8, iterations=4,
                    ranges=dict(stiffness=(200, 2000), drag=(0.02, 0.1), gravity=(0.0, 0.0),
                                stretch_limit=(0.0, 0.0), substeps=(8, 8), iterations=(4, 4)),
                    notes="Highest frequency. Keep substeps high so the spring stays "
                          "stable (h * sqrt(k) < 2)."),
    "belly": dict(stiffness=40.0, drag=0.7, gravity=1.0, stretch_limit=0.005,
                  substeps=4, iterations=2,
                  ranges=dict(stiffness=(20, 60), drag=(0.6, 0.8), gravity=(1.0, 1.0),
                              stretch_limit=(0.005, 0.005), substeps=(4, 4), iterations=(2, 2)),
                  notes="Heavy drag, jiggle without ring-down. Wobble is the goal, "
                        "not the spring."),
    "ear": dict(stiffness=220.0, drag=0.3, gravity=0.4, stretch_limit=0.0,
                substeps=8, iterations=2, damping_ratio=0.6,
                ranges=dict(stiffness=(120, 400), drag=(0.2, 0.4), gravity=(0.3, 0.6),
                            stretch_limit=(0.0, 0.0), substeps=(8, 8), iterations=(2, 2)),
                notes="NOT from research/06: derived between hair and antenna. "
                      "Underdamped (ratio 0.6) on purpose so a head turn gives one "
                      "visible flop. Untuned against a real character."),
}


# ---------------------------------------------------------------- specs
@dataclass
class ChainSpec:
    """Static description of one dynamics chain.

    name:          chain id, e.g. "tail", "hair_L_front".
    bones:         bone names, root first. Each bone should be the parent of the next.
    stiffness:     angular spring stiffness, 1/s^2. Range 2..2000.
    damping_ratio: 1.0 = critical (settles without overshoot), <1 wobbles.
    drag:          fraction of velocity lost per second, 0..1 (air drag).
    gravity:       gravity scale, 0 = none, 1 = 9.81 m/s^2.
    stretch_limit: max fractional stretch after projection (follow-the-leader clamp).
    compliance:    XPBD compliance on the length constraint (m/N). 0 = inextensible.
    substeps, iterations: integrator substeps per frame / projection sweeps per substep.
    collider_names: ColliderSpec names this chain collides with (empty = all).
    blend:         additive blend over the animator pose, 0..1.
    """
    name: str
    bones: list
    stiffness: float = 200.0
    damping_ratio: float = 1.0
    drag: float = 0.3
    gravity: float = 1.0
    stretch_limit: float = 0.01
    compliance: float = 0.0
    substeps: int = 4
    iterations: int = 2
    collider_names: list = field(default_factory=list)
    blend: float = 1.0

    @classmethod
    def from_preset(cls, name, bones, preset, **overrides):
        """ChainSpec from a PRESETS entry; keyword overrides win."""
        p = {k: v for k, v in PRESETS[preset].items() if k not in ("ranges", "notes")}
        p.update(overrides)
        return cls(name=name, bones=list(bones), **p)


@dataclass
class ColliderSpec:
    """Collision proxy, world space unless attached to a bone (bake() moves it).

    kind:   "sphere", "capsule" or "plane".
    bone:   bone the collider follows (bake only), or None for world space.
    radius: sphere/capsule radius; for a plane, a skin thickness.
    offset: sphere centre / capsule first end / plane point. Bone space if bone set.
    tail:   capsule second end. Bone space if bone set.
    normal: plane normal (world, or bone space if bone set).
    friction: 0..1 fraction of tangential motion removed on contact.
    """
    name: str
    kind: str
    bone: str | None = None
    radius: float = 0.0
    offset: tuple = (0.0, 0.0, 0.0)
    tail: tuple = (0.0, 0.0, 0.0)
    normal: tuple = (0.0, 0.0, 1.0)
    friction: float = 0.3


# ---------------------------------------------------------------- state
@dataclass
class ChainState:
    """Particle state for a batch of C chains padded to N bones.

    Particle 0 of each chain is the root joint (kinematic), particle i+1 is the
    tail of bone i. Chains shorter than N are padded; `active` masks real bones.
    """
    P: np.ndarray            # (C, N+1, 3) positions
    V: np.ndarray            # (C, N+1, 3) velocities
    T: np.ndarray            # (C, N+1, 3) targets (animated particle positions)
    T_prev: np.ndarray       # (C, N+1, 3) targets at the previous frame
    rest_len: np.ndarray     # (C, N)
    active: np.ndarray       # (C, N) 1.0 for real bones
    inv_mass: np.ndarray     # (C, N+1) 0 for the root and padding
    stiffness: np.ndarray    # (C,)
    damping: np.ndarray      # (C,) absolute angular damping coefficient (1/s)
    drag: np.ndarray         # (C,)
    gravity: np.ndarray      # (C,)
    stretch_limit: np.ndarray
    compliance: np.ndarray
    blend: np.ndarray        # (C,)
    substeps: int = 4
    iterations: int = 2
    names: list = field(default_factory=list)       # chain names
    bones: list = field(default_factory=list)       # bone names per chain
    colliders: list = field(default_factory=list)   # world-space collider dicts
    collider_mask: np.ndarray | None = None         # (C, K) 1 if chain c hits collider k
    time: float = 0.0
    stats: dict = field(default_factory=dict)
    _padded: bool = False

    @property
    def shape(self):
        return self.P.shape[0], self.P.shape[1] - 1


def new_state(specs, rest_pose, substeps=None, iterations=None):
    """Build a batched ChainState.

    specs:     list of ChainSpec.
    rest_pose: {bone_name: (head(3), tail(3))} world space, or for an unnamed
               synthetic chain {spec.name: (N+1, 3) particle array}.
    substeps / iterations default to the max over the specs (one batch shares
    one step size; the stiffest chain decides).
    """
    if not specs:
        raise ValueError("no chains")
    chains = []
    for s in specs:
        if s.name in rest_pose and np.asarray(rest_pose[s.name]).ndim == 2 \
                and len(rest_pose[s.name]) == len(s.bones) + 1:
            pts = np.asarray(rest_pose[s.name], dtype=float)
        else:
            missing = [b for b in s.bones if b not in rest_pose]
            if missing:
                raise ValueError(f"chain {s.name}: no rest pose for {missing}")
            pts = [np.asarray(rest_pose[s.bones[0]][0], float)]
            pts += [np.asarray(rest_pose[b][1], float) for b in s.bones]
            pts = np.array(pts)
        chains.append(pts)
    C = len(specs)
    N = max(len(s.bones) for s in specs)
    P = np.zeros((C, N + 1, 3))
    active = np.zeros((C, N))
    for c, pts in enumerate(chains):
        n = len(pts) - 1
        P[c, :n + 1] = pts
        P[c, n + 1:] = pts[-1]
        active[c, :n] = 1.0
    seg = P[:, 1:] - P[:, :-1]
    rest_len = np.sqrt((seg * seg).sum(-1))
    if np.any((rest_len < 1e-9) & (active > 0)):
        raise ValueError("zero-length bone in a chain")
    inv_mass = np.zeros((C, N + 1))
    inv_mass[:, 1:] = active
    arr = lambda a: np.array([getattr(s, a) for s in specs], dtype=float)
    k = arr("stiffness")
    st = ChainState(
        P=P.copy(), V=np.zeros_like(P), T=P.copy(), T_prev=P.copy(),
        rest_len=rest_len, active=active, inv_mass=inv_mass,
        stiffness=k, damping=2.0 * arr("damping_ratio") * np.sqrt(np.maximum(k, 0.0)),
        drag=np.clip(arr("drag"), 0.0, 0.999), gravity=arr("gravity"),
        stretch_limit=arr("stretch_limit"), compliance=arr("compliance"),
        blend=np.clip(arr("blend"), 0.0, 1.0),
        substeps=int(substeps or max(s.substeps for s in specs)),
        iterations=int(iterations or max(s.iterations for s in specs)),
        names=[s.name for s in specs], bones=[list(s.bones) for s in specs],
    )
    st._specs = list(specs)
    st._padded = bool(active.min() < 1.0)
    return st


def chain_from_points(points, preset=None, name="chain", **kw):
    """Convenience: a single synthetic chain from an (N+1, 3) particle array."""
    pts = np.asarray(points, dtype=float)
    bones = [f"{name}_{i}" for i in range(len(pts) - 1)]
    spec = ChainSpec.from_preset(name, bones, preset, **kw) if preset else \
        ChainSpec(name=name, bones=bones, **kw)
    return spec, pts


def set_targets(state, targets):
    """Set this frame's animated particle positions, (C, N+1, 3)."""
    state.T_prev[:] = state.T
    state.T[:] = targets


# ---------------------------------------------------------------- colliders
def set_colliders(state, colliders, specs=None):
    """Attach world-space colliders. colliders: list of ColliderSpec (bone=None)
    or dicts from _collider_world(). Chains with collider_names only hit those."""
    out = []
    for c in colliders:
        out.append(_collider_world(c) if isinstance(c, ColliderSpec) else c)
    state.colliders = out
    specs = specs or getattr(state, "_specs", None)
    mask = np.ones((state.P.shape[0], len(out)))
    if specs:
        for i, s in enumerate(specs):
            if s.collider_names:
                mask[i] = [1.0 if c["name"] in s.collider_names else 0.0 for c in out]
    state.collider_mask = mask


def _collider_world(c, matrix=None):
    def xf(p, w=1.0):
        p = np.asarray(p, float)
        if matrix is None:
            return p
        M = np.asarray(matrix, float)
        return M[:3, :3] @ p + (M[:3, 3] if w else 0.0)
    d = {"name": c.name, "kind": c.kind, "radius": float(c.radius),
         "friction": float(c.friction)}
    if c.kind == "sphere":
        d["center"] = xf(c.offset)
    elif c.kind == "capsule":
        d["a"], d["b"] = xf(c.offset), xf(c.tail)
    elif c.kind == "plane":
        n = xf(c.normal, 0.0)
        d["normal"] = n / max(np.linalg.norm(n), 1e-12)
        d["point"] = xf(c.offset)
    else:
        raise ValueError(f"unknown collider kind {c.kind!r}")
    return d


def _collide(state, P, P0):
    """Project free particles out of colliders; friction on the tangential step."""
    free = state.inv_mass[..., None] > 0
    for k, c in enumerate(state.colliders):
        hit_chain = state.collider_mask[:, k][:, None, None] > 0
        if c["kind"] == "plane":
            n = c["normal"]
            dist = (P - c["point"]) @ n - c["radius"]
            pen = np.minimum(dist, 0.0)[..., None]
            nrm = np.broadcast_to(n, P.shape)
            inside = (pen < 0) & free & hit_chain
            push = -pen
        else:
            if c["kind"] == "sphere":
                closest = c["center"]
            else:
                a, b = c["a"], c["b"]
                ab = b - a
                t = np.clip(((P - a) @ ab) / max(ab @ ab, 1e-12), 0.0, 1.0)
                closest = a + t[..., None] * ab
            d = P - closest
            dist = np.sqrt((d * d).sum(-1, keepdims=True))
            nrm = d / np.maximum(dist, 1e-12)
            push = c["radius"] - dist
            inside = (push > 0) & free & hit_chain
        if not inside.any():
            continue
        P += np.where(inside, push * nrm, 0.0)
        step = P - P0
        tang = step - (step * nrm).sum(-1, keepdims=True) * nrm
        P -= np.where(inside, c["friction"] * tang, 0.0)


# ---------------------------------------------------------------- core
def _normalize(v):
    n = np.sqrt((v * v).sum(-1, keepdims=True))
    return v / np.maximum(n, 1e-12), n


def _rotate_min(v, a, b):
    """Rotate v by the minimal rotation taking unit a onto unit b (batched)."""
    c = (a * b).sum(-1, keepdims=True)
    k = np.cross(a, b)
    kv = np.cross(k, v)
    return v * c + kv + k * (k * v).sum(-1, keepdims=True) / np.maximum(1.0 + c, 1e-6)


def drive_from_root_accel(state, root_accel_world, chain_axes=None):
    """-root_accel projected perpendicular to each chain's axis, (C, 3).

    root_accel_world: (3,) or (C, 3) m/s^2. chain_axes: (C, 3) unit vectors;
    default is each chain's current root-to-tip direction.
    """
    a = np.broadcast_to(np.asarray(root_accel_world, float), (state.P.shape[0], 3))
    if chain_axes is None:
        chain_axes, _ = _normalize(state.P[:, -1] - state.P[:, 0])
    ax = np.asarray(chain_axes, float)
    return -(a - (a * ax).sum(-1, keepdims=True) * ax)


def impulse(state, chain_idx, particle_idx, delta_v, sigma_particles=1.0):
    """Poke: v_i += delta_v * exp(-|i - particle_idx| / sigma), lands next substep."""
    n = state.P.shape[1]
    i = np.arange(n)
    fall = np.exp(-np.abs(i - particle_idx) / max(sigma_particles, 1e-6))
    fall *= state.inv_mass[chain_idx] > 0
    state.V[chain_idx] += fall[:, None] * np.asarray(delta_v, float)[None, :]


def impulse_at(state, point, delta_v, radius):
    """Touch in world space: every particle within ~radius gets delta_v with a
    Gaussian falloff. Returns the number of particles affected (> 1 % weight)."""
    d = state.P - np.asarray(point, float)
    w = np.exp(-(d * d).sum(-1) / max(radius * radius, 1e-12)) * (state.inv_mass > 0)
    state.V += w[..., None] * np.asarray(delta_v, float)
    return int((w > 0.01).sum())


def _ftl_clamp(state, P):
    """Follow-the-leader: root to tip, pull each tail back inside the stretch
    band. Only runs when some segment is outside it (cheap check first)."""
    lim = np.maximum(state.stretch_limit, 1e-3)[:, None]
    L = state.rest_len
    seg = P[:, 1:] - P[:, :-1]
    ln = np.sqrt(_add(seg * seg, axis=-1))
    if not np.any(np.abs(ln - L) > lim * L):
        return False
    lo, hi = L * (1 - lim), L * (1 + lim)
    for i in range(L.shape[1]):
        d = P[:, i + 1] - P[:, i]
        n = np.sqrt(_add(d * d, axis=-1))
        tgt = np.clip(n, lo[:, i], hi[:, i])
        scale = np.where(n > 1e-12, tgt / np.maximum(n, 1e-12), 1.0)
        act = state.active[:, i][:, None]
        P[:, i + 1] = np.where(act > 0, P[:, i] + d * scale[:, None], P[:, i + 1])
    return True


def step(state, root_accel=None, dt=1.0 / 60.0, targets=None, forcing=None,
         substeps=None, iterations=None, ftl=True, carry=True):
    """Advance every chain by dt seconds. Returns state.P (updated in place).

    root_accel: (3,) or (C, 3) root acceleration for in-place driving; it becomes
                forcing = -root_accel perpendicular to each chain. None = 0.
    dt:         frame time in seconds.
    targets:    optional (C, N+1, 3) animated particle positions for this frame
                (world-space driving). The root follows targets[:, 0],
                interpolated per substep; bone rest directions come from them.
    forcing:    optional extra (C, 3) acceleration on free particles.
    ftl:        follow-the-leader stretch clamp. True/"frame" = after the last
                substep (output is always inside the band, cheapest);
                "substep" = after every substep; False = off (pure PBD).
    carry:      scale each bone's spring by the number of particles it carries,
                so `stiffness` means the same angular response at root and tip.
                Without it an upright chain under gravity buckles.
    """
    if targets is not None:
        set_targets(state, targets)
    S = int(substeps or state.substeps)
    I = int(iterations or state.iterations)
    h = dt / S
    C, N1, _ = state.P.shape
    N = N1 - 1
    P, V = state.P, state.V
    act = state.active
    w = state.inv_mass
    L = state.rest_len

    # ---- per-step constants (no per-substep allocation of these)
    acc = state.gravity[:, None] * GRAVITY[None, :]
    if root_accel is not None:
        acc = acc + drive_from_root_accel(state, root_accel)
    if forcing is not None:
        acc = acc + np.asarray(forcing, float)
    hacc = (h * acc)[:, None, :] * (w > 0)[..., None]
    scale = act * (np.cumsum(act[:, ::-1], axis=1)[:, ::-1] if carry else 1.0)
    k_h = (h * state.stiffness[:, None] * L * scale)[..., None]
    c_h = (h * state.damping[:, None] * scale)[..., None]
    drag_f = ((1.0 - state.drag) ** h)[:, None, None]
    vmax2 = (0.5 * L / h) ** 2

    dtg = state.T[:, 1:] - state.T[:, :-1]
    dtg /= np.maximum(np.sqrt(_add(dtg * dtg, axis=-1, keepdims=True)), 1e-12)
    A = np.empty_like(dtg)
    A[:, 0] = dtg[:, 0]
    A[:, 1:] = dtg[:, :-1]
    B = A.copy()
    twoAv = 2.0 * _add(A * dtg, axis=-1, keepdims=True)

    root0 = state.T_prev[:, 0].copy()
    droot = state.T[:, 0] - root0
    P0 = np.empty_like(P)

    xpbd = bool(np.any(state.compliance > 0))
    alpha_t = (state.compliance / (h * h))[:, None]
    groups = []
    for start in (0, 1):
        sa, sb, sl = slice(start, N, 2), slice(start + 1, N1, 2), slice(start, N, 2)
        wa, wb = w[:, sa], w[:, sb]
        if wa.shape[1] == 0:
            continue
        ws = wa + wb
        inv = np.where(ws > 0, act[:, sl] / np.maximum(ws, 1e-12), 0.0)
        groups.append((sa, sb, L[:, sl], (wa * inv)[..., None], (wb * inv)[..., None],
                       wa, wb, ws, act[:, sl]))

    for s in range(S):
        np.copyto(P0, P)

        # ---- angular spring toward the rest direction carried by the sim parent
        seg = P[:, 1:] - P[:, :-1]
        dcur = seg / np.maximum(np.sqrt(_add(seg * seg, axis=-1, keepdims=True)), 1e-12)
        B[:, 1:] = dcur[:, :-1]
        cAB = _add(A * B, axis=-1, keepdims=True)
        ApB = A + B
        r = _add(ApB * dtg, axis=-1, keepdims=True) / np.maximum(1.0 + cAB, 1e-6)
        drest = dtg + B * twoAv - ApB * r          # R(A->B) applied to dtg
        dv = k_h * (drest - dcur)
        vr = V[:, 1:] - V[:, :-1]
        vr -= _add(vr * dcur, axis=-1, keepdims=True) * dcur
        dv -= c_h * vr
        V[:, 1:] += dv

        V += hacc
        V *= drag_f
        vv = _add(V[:, 1:] * V[:, 1:], axis=-1)
        if np.any(vv > vmax2):
            V[:, 1:] *= np.minimum(1.0, np.sqrt(vmax2 / np.maximum(vv, 1e-24)))[..., None]

        P += h * V
        P[:, 0] = root0 + droot * ((s + 1) / S)

        if state.colliders:
            _collide(state, P, P0)

        # ---- distance projection, red-black
        if not xpbd:
            for _ in range(I):
                for sa, sb, Lg, fa, fb, *_rest in groups:
                    pa, pb = P[:, sa], P[:, sb]
                    d = pb - pa
                    ln = np.sqrt(_add(d * d, axis=-1))
                    g = (1.0 - Lg / np.maximum(ln, 1e-12))[..., None] * d
                    pa += fa * g
                    pb -= fb * g
        else:
            lams = [np.zeros_like(gp[7]) for gp in groups]
            for _ in range(I):
                for gp, lam in zip(groups, lams):
                    sa, sb, Lg, _fa, _fb, wa, wb, ws, ag = gp
                    pa, pb = P[:, sa], P[:, sb]
                    d = pb - pa
                    ln = np.sqrt(_add(d * d, axis=-1))
                    n = d / np.maximum(ln, 1e-12)[..., None]
                    dl = (-(ln - Lg) - alpha_t * lam) / np.maximum(ws + alpha_t, 1e-12) * ag
                    lam += dl
                    pa -= (wa * dl)[..., None] * n
                    pb += (wb * dl)[..., None] * n

        if ftl == "substep" or (ftl and s == S - 1):
            _ftl_clamp(state, P)
        if state._padded:
            last = act.sum(1).astype(int)
            for c in np.nonzero(last < N)[0]:
                P[c, last[c] + 1:] = P[c, last[c]]

        np.subtract(P, P0, out=V)
        V /= h
    state.time += dt
    return P


# ---------------------------------------------------------------- measurement
def max_stretch(state):
    """Largest |len - rest| / rest over real bones, as a fraction."""
    seg = state.P[:, 1:] - state.P[:, :-1]
    ln = np.sqrt((seg * seg).sum(-1))
    err = np.abs(ln - state.rest_len) / state.rest_len
    return float((err * state.active).max())


def benchmark(chains=8, bones=8, substeps=4, iterations=2, frames=600, dt=1 / 60.0,
              repeats=3):
    """ms per frame for a synthetic batch (horizontal chains swinging under
    gravity with an oscillating root). Best of `repeats`."""
    best = math.inf
    for _ in range(repeats):
        specs, rest = [], {}
        for c in range(chains):
            pts = np.array([[c * 0.3, 0.0, 0.0]] + [[c * 0.3 + 0.1 * (i + 1), 0.0, 0.0]
                                                    for i in range(bones)])
            sp, _ = chain_from_points(pts, name=f"c{c}", stiffness=30.0, drag=0.2,
                                      gravity=1.0, stretch_limit=0.01)
            specs.append(sp)
            rest[sp.name] = pts
        st = new_state(specs, rest, substeps=substeps, iterations=iterations)
        base = st.T.copy()
        t0 = time.perf_counter()
        for f in range(frames):
            tg = base.copy()
            tg[:, :, 1] += 0.1 * math.sin(f * dt * 6.0)
            step(st, dt=dt, targets=tg)
        best = min(best, (time.perf_counter() - t0) * 1000.0 / frames)
    return best


# ---------------------------------------------------------------- layering
def quat_mul(a, b):
    """Hamilton product, (..., 4) wxyz."""
    aw, ax, ay, az = np.moveaxis(np.asarray(a, float), -1, 0)
    bw, bx, by, bz = np.moveaxis(np.asarray(b, float), -1, 0)
    return np.stack([aw * bw - ax * bx - ay * by - az * bz,
                     aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw], -1)


def quat_inv(q):
    q = np.asarray(q, float)
    return q * np.array([1, -1, -1, -1]) / (q * q).sum(-1, keepdims=True)


def quat_slerp_identity(q, t):
    """slerp(identity, q, t), shortest path."""
    q = np.asarray(q, float)
    q = np.where(q[..., :1] < 0, -q, q)
    wq = np.clip(q[..., 0], -1.0, 1.0)
    ang = np.arccos(wq)
    s = np.sin(ang)
    small = s < 1e-8
    k1 = np.where(small, 1 - t, np.sin((1 - t) * ang) / np.where(small, 1, s))
    k2 = np.where(small, t, np.sin(t * ang) / np.where(small, 1, s))
    ident = np.zeros_like(q)
    ident[..., 0] = 1.0
    out = k1[..., None] * ident + k2[..., None] * q
    return out / np.sqrt((out * out).sum(-1, keepdims=True))


def additive_layer(q_anim, q_sim, w):
    """q_out = q_anim * slerp(identity, q_anim^-1 * q_sim, w). wxyz quaternions."""
    delta = quat_mul(quat_inv(q_anim), q_sim)
    return quat_mul(q_anim, quat_slerp_identity(delta, w))


# ---------------------------------------------------------------- bpy side
def _bpy():
    import bpy  # noqa: lazy on purpose
    return bpy


def chains_from_rig(rig, bone_list, preset=None, substeps=None, iterations=None, **overrides):
    """Read rest positions from a bpy armature and build a ChainState.

    bone_list: a list of bone names (one chain), a list of lists (several
               chains), or a list of ChainSpec.
    preset:    PRESETS key used for chains given as bare name lists.
    Positions are world space at the current pose (call at rest or at your
    first frame). Raises ValueError naming any missing bone or a broken chain.
    """
    if rig is None or rig.type != "ARMATURE":
        raise ValueError("rig must be an armature object")
    if bone_list and isinstance(bone_list[0], str):
        bone_list = [bone_list]
    specs = []
    for i, b in enumerate(bone_list):
        if isinstance(b, ChainSpec):
            specs.append(b)
        elif preset:
            specs.append(ChainSpec.from_preset(f"chain{i}", b, preset, **overrides))
        else:
            specs.append(ChainSpec(name=f"chain{i}", bones=list(b), **overrides))
    names = set(rig.data.bones.keys())
    for s in specs:
        missing = [b for b in s.bones if b not in names]
        if missing:
            raise ValueError(f"chain {s.name}: bones not in rig: {missing}")
        for a, b in zip(s.bones, s.bones[1:]):
            par = rig.data.bones[b].parent
            if par is None or par.name != a:
                raise ValueError(f"chain {s.name}: {b} is not a child of {a}")
    targets, _ = _read_targets(rig, specs)
    rest = {}
    for s, pts in zip(specs, targets):
        rest[s.name] = pts
    st = new_state(specs, rest, substeps=substeps, iterations=iterations)
    st._rig_name = rig.name
    st._static_basis = {b: tuple(rig.pose.bones[b].rotation_quaternion)
                        for s in specs for b in s.bones}
    return st


def _read_targets(rig, specs):
    """World-space particle positions of the current evaluated pose, plus the
    armature-space pose matrices per bone."""
    Mw = np.array(rig.matrix_world)
    pts_all, mats = [], {}
    for s in specs:
        pb0 = rig.pose.bones[s.bones[0]]
        pts = [np.array(pb0.head)]
        for b in s.bones:
            pb = rig.pose.bones[b]
            pts.append(np.array(pb.tail))
            mats[b] = np.array(pb.matrix)
        pts = np.array(pts)
        pts = pts @ Mw[:3, :3].T + Mw[:3, 3]
        pts_all.append(pts)
    return pts_all, mats


def _pad(state, pts_list):
    C, N1, _ = state.P.shape
    out = np.zeros((C, N1, 3))
    for c, pts in enumerate(pts_list):
        out[c, :len(pts)] = pts
        out[c, len(pts):] = pts[-1]
    return out


def _mat3_to_quat(m):
    from mathutils import Matrix
    q = Matrix([list(r) for r in m]).to_quaternion()
    return np.array([q.w, q.x, q.y, q.z])


def _minrot_mat(a, b):
    """3x3 minimal rotation taking unit a to unit b."""
    a = a / max(np.linalg.norm(a), 1e-12)
    b = b / max(np.linalg.norm(b), 1e-12)
    v = np.cross(a, b)
    c = float(a @ b)
    if c < -0.999999:
        ax = np.cross(a, [1.0, 0, 0])
        if np.linalg.norm(ax) < 1e-6:
            ax = np.cross(a, [0, 1.0, 0])
        ax /= np.linalg.norm(ax)
        return 2 * np.outer(ax, ax) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


def compute_layered(rig, state, anim_mats, anim_basis, blend_by_bone=None):
    """Per bone, the layered local rotation (wxyz) for the current sim state.

    anim_mats:  {bone: 4x4 armature-space pose matrix} of the animator's pose.
    anim_basis: {bone: wxyz} animator local rotation.
    """
    Mw = np.array(rig.matrix_world)
    Rw_inv = np.linalg.inv(Mw[:3, :3])
    out = {}
    for c, bones in enumerate(state.bones):
        new_arm = {}
        for i, b in enumerate(bones):
            pb = rig.pose.bones[b]
            bone = pb.bone
            M_anim = anim_mats[b]
            d_anim = M_anim[:3, 1] / max(np.linalg.norm(M_anim[:3, 1]), 1e-12)
            d_sim = Rw_inv @ (state.P[c, i + 1] - state.P[c, i])
            R_des = _minrot_mat(d_anim, d_sim) @ M_anim[:3, :3]
            rest = np.array(bone.matrix_local)[:3, :3]
            if bone.parent is not None:
                par = bone.parent.name
                R_par = new_arm.get(par, anim_mats.get(par, np.array(pb.parent.matrix))[:3, :3])
                rel = np.linalg.inv(np.array(bone.parent.matrix_local)[:3, :3]) @ rest
                base = R_par @ rel
            else:
                base = rest
            R_basis_sim = np.linalg.inv(base) @ R_des
            # strip scale before quaternion conversion
            U, _, Vt = np.linalg.svd(R_basis_sim)
            q_sim = _mat3_to_quat(U @ Vt)
            q_anim = np.asarray(anim_basis[b], float)
            if q_sim @ q_anim < 0:
                q_sim = -q_sim
            wgt = state.blend[c] if blend_by_bone is None else blend_by_bone.get(b, state.blend[c])
            q_out = additive_layer(q_anim, q_sim, float(wgt))
            out[b] = q_out
            R_out = base @ _quat_to_mat3(q_out)
            new_arm[b] = R_out
    return out


def _quat_to_mat3(q):
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _set_rotation(pb, q):
    from mathutils import Quaternion
    qq = Quaternion((float(q[0]), float(q[1]), float(q[2]), float(q[3])))
    if pb.rotation_mode == "QUATERNION":
        pb.rotation_quaternion = qq
        return "rotation_quaternion"
    if pb.rotation_mode == "AXIS_ANGLE":
        ax, ang = qq.to_axis_angle()
        pb.rotation_axis_angle = (ang, ax.x, ax.y, ax.z)
        return "rotation_axis_angle"
    pb.rotation_euler = qq.to_euler(pb.rotation_mode, pb.rotation_euler)
    return "rotation_euler"


def _get_rotation(pb):
    if pb.rotation_mode == "QUATERNION":
        q = pb.rotation_quaternion
    elif pb.rotation_mode == "AXIS_ANGLE":
        from mathutils import Quaternion, Vector
        a = pb.rotation_axis_angle
        q = Quaternion(Vector(a[1:]), a[0])
    else:
        q = pb.rotation_euler.to_quaternion()
    return np.array([q.w, q.x, q.y, q.z])


def _has_rot_curve(rig, bone):
    ad = rig.animation_data
    if not ad or not ad.action:
        return False
    try:
        from forgekit import anim as A
        curves = A.fcurves(rig)
    except Exception:
        curves = getattr(ad.action, "fcurves", [])
    key = f'pose.bones["{bone}"].rotation'
    return any(fc.data_path.startswith(key) for fc in curves)


def write_pose(rig, state, blend_by_bone=None, anim_mats=None, anim_basis=None):
    """Write the layered pose for the current frame onto the pose bones.

    Call after scene.frame_set(f) and step(). The animator pose is read from
    the pose bones as evaluated now; for bones with no rotation F-curve the
    rotation captured by chains_from_rig is used instead, so repeated writes
    never feed back into themselves. Returns {bone: wxyz written}.
    """
    if anim_mats is None or anim_basis is None:
        anim_basis = {}
        for bones in state.bones:
            for b in bones:
                pb = rig.pose.bones[b]
                anim_basis[b] = _get_rotation(pb) if _has_rot_curve(rig, b) \
                    else np.array(state._static_basis.get(b, (1, 0, 0, 0)), float)
        anim_mats = _anim_matrices(rig, state, anim_basis)
    q = compute_layered(rig, state, anim_mats, anim_basis, blend_by_bone)
    for b, qv in q.items():
        _set_rotation(rig.pose.bones[b], qv)
    return q


def _anim_matrices(rig, state, anim_basis):
    """Armature-space matrices of the animator pose (basis rotations only),
    computed root to tip so a previous dynamics write does not leak in."""
    mats = {}
    for bones in state.bones:
        for b in bones:
            pb = rig.pose.bones[b]
            bone = pb.bone
            Rb = _quat_to_mat3(anim_basis[b])
            loc = np.array(pb.location)
            Mb = np.eye(4)
            Mb[:3, :3] = Rb
            Mb[:3, 3] = loc
            rest = np.array(bone.matrix_local)
            if bone.parent is not None:
                par = bone.parent.name
                Mp = mats.get(par, np.array(pb.parent.matrix))
                M = Mp @ np.linalg.inv(np.array(bone.parent.matrix_local)) @ rest @ Mb
            else:
                M = rest @ Mb
            mats[b] = M
    return mats


def targets_from_anim(rig, state, anim_basis=None):
    """World-space particle targets of the animator pose, computed from basis
    rotations (not from the evaluated pose, which may hold our last write)."""
    if anim_basis is None:
        anim_basis = {b: (_get_rotation(rig.pose.bones[b]) if _has_rot_curve(rig, b)
                          else np.array(state._static_basis.get(b, (1, 0, 0, 0)), float))
                      for bones in state.bones for b in bones}
    mats = _anim_matrices(rig, state, anim_basis)
    Mw = np.array(rig.matrix_world)
    pts_list = []
    for bones in state.bones:
        M0 = mats[bones[0]]
        pts = [M0[:3, 3]]
        for b in bones:
            M = mats[b]
            y = M[:3, 1] / max(np.linalg.norm(M[:3, 1]), 1e-12)
            pts.append(M[:3, 3] + y * rig.data.bones[b].length)
        pts = np.array(pts) @ Mw[:3, :3].T + Mw[:3, 3]
        pts_list.append(pts)
    return _pad(state, pts_list), mats, anim_basis


def live_frame(rig, state, dt=None, root_accel=None):
    """One live update (for a frame_change_post handler): animator targets ->
    step -> layered write. Returns {bone: wxyz}."""
    if dt is None:
        sc = _bpy().context.scene
        dt = sc.render.fps_base / sc.render.fps
    tg, mats, basis = targets_from_anim(rig, state)
    step(state, root_accel, dt, targets=tg)
    return write_pose(rig, state, anim_mats=mats, anim_basis=basis)


def bake(rig, chains, frame_start, frame_end, colliders=None, fps=None,
         preset=None, root_accel=None, warmup_frames=0, clear_existing=False, **overrides):
    """Simulate over a frame range and key the layered result.

    chains:   ChainSpec list, or bone-name list(s) with `preset`.
    colliders: ColliderSpec list; bone-attached ones follow their bone.
    root_accel: optional callable frame -> (3,) acceleration for in-place cycles.
    warmup_frames: settle the sim on the first frame's pose before baking.

    Pass 1 records the animator pose (no writes), pass 2 simulates, pass 3
    keys. Returns {"frames", "keys", "ms_per_frame_sim", "ms_per_frame_total",
    "max_stretch", "bones"}.
    """
    bpy = _bpy()
    sc = bpy.context.scene
    fps = fps or sc.render.fps / sc.render.fps_base
    dt = 1.0 / fps
    t_all = time.perf_counter()
    sc.frame_set(frame_start)
    st = chains_from_rig(rig, chains, preset=preset, **overrides)
    specs = st._specs
    all_bones = [b for bones in st.bones for b in bones]
    for b in all_bones:
        pb = rig.pose.bones[b]
        if pb.rotation_mode != "QUATERNION" and not _has_rot_curve(rig, b):
            pb.rotation_mode = "QUATERNION"
    has_curve = {b: _has_rot_curve(rig, b) for b in all_bones}
    static = {b: _get_rotation(rig.pose.bones[b]) for b in all_bones}

    rec = []
    for f in range(frame_start, frame_end + 1):
        sc.frame_set(f)
        basis = {b: (_get_rotation(rig.pose.bones[b]) if has_curve[b] else static[b])
                 for b in all_bones}
        tg, mats = _read_targets(rig, specs)
        col = []
        for c in colliders or []:
            M = None
            if c.bone:
                M = np.array(rig.matrix_world) @ np.array(rig.pose.bones[c.bone].matrix)
            col.append(_collider_world(c, M))
        rec.append((f, basis, mats, _pad(st, tg), col))

    t_sim = 0.0
    results = []
    st.T[:] = rec[0][3]
    st.T_prev[:] = rec[0][3]
    st.P[:] = rec[0][3]
    if colliders:
        set_colliders(st, rec[0][4], specs)
    for _ in range(warmup_frames):
        step(st, dt=dt, targets=rec[0][3])
    worst = 0.0
    for f, basis, mats, tg, col in rec:
        t0 = time.perf_counter()
        if colliders:
            set_colliders(st, col, specs)
        ra = root_accel(f) if callable(root_accel) else root_accel
        step(st, ra, dt, targets=tg)
        t_sim += time.perf_counter() - t0
        worst = max(worst, max_stretch(st))
        results.append((f, compute_layered(rig, st, mats, basis)))

    keys = 0
    for f, q in results:
        for b, qv in q.items():
            pb = rig.pose.bones[b]
            path = _set_rotation(pb, qv)
            pb.keyframe_insert(path, frame=f, group=b)
            keys += 1
    n = len(rec)
    return {"frames": n, "keys": keys,
            "ms_per_frame_sim": round(t_sim * 1000 / n, 4),
            "ms_per_frame_total": round((time.perf_counter() - t_all) * 1000 / n, 3),
            "max_stretch": round(worst, 5), "bones": len(all_bones)}


def export_vrm_springs(specs, colliders=()):
    """VRMC_springBone-shaped dict. Field names follow the VRM 1.0 spec as
    commonly cited; NOT validated against the schema. stiffness is normalised
    to VRM's 0..4-ish range by stiffness / 100; dragForce = drag."""
    cols = [{"node": c.bone, "shape": {c.kind if c.kind != "plane" else "sphere": {
        "offset": list(c.offset), "radius": c.radius,
        **({"tail": list(c.tail)} if c.kind == "capsule" else {})}}} for c in colliders]
    springs = []
    for s in specs:
        springs.append({"name": s.name, "joints": [
            {"node": b, "stiffness": s.stiffness / 100.0, "gravityPower": s.gravity,
             "gravityDir": [0.0, 0.0, -1.0], "dragForce": s.drag, "hitRadius": 0.02}
            for b in s.bones],
            "colliderGroups": [i for i, c in enumerate(colliders)
                               if not s.collider_names or c.name in s.collider_names]})
    return {"specVersion": "1.0", "colliders": cols, "springs": springs}
