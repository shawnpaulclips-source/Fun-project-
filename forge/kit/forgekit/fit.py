"""Optimizer-driven fitting: let CMA-ES hit a target instead of guessing.

This is the piece that changes the agent's role from sculptor to art director.
You define a parameter space and a loss; the optimizer makes the thousands of
micro-corrections. Measured on 4 CPU cores (forge/research/08-form-quality.md):

    silhouette fit, 6 hidden hand params      IoU 0.993   360 evals  11.5 s
    cage fit to tape-measure targets          4.1% -> 0.26%          11.9 s
    skin-weight blend fit                     knuckle isect 88 -> 0  64 s

RULE: never put a render in the loss. A numpy silhouette costs ~29 ms per
eval; a 128px Workbench render costs ~570 ms. Rasterise vertices yourself.
"""
import numpy as np


class CMAES:
    """(mu/mu_w, lambda)-CMA-ES, Hansen's tutorial defaults (arXiv:1604.00772).
    Optimises in a box (default [0,1]^n) by clipping - correct for procedural
    parameters with hard anatomical ranges."""

    def __init__(self, x0, sigma0=0.25, popsize=None, seed=0, bounds=(0.0, 1.0)):
        self.n = n = len(x0)
        self.rng = np.random.default_rng(seed)
        self.lam = popsize or 4 + int(3 * np.log(n))
        self.mu = self.lam // 2
        w = np.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1))
        self.w = w / w.sum()
        self.mueff = 1.0 / (self.w ** 2).sum()
        self.cc = (4 + self.mueff / n) / (n + 4 + 2 * self.mueff / n)
        self.cs = (self.mueff + 2) / (n + self.mueff + 5)
        self.c1 = 2 / ((n + 1.3) ** 2 + self.mueff)
        self.cmu = min(1 - self.c1, 2 * (self.mueff - 2 + 1 / self.mueff) / ((n + 2) ** 2 + self.mueff))
        self.damps = 1 + 2 * max(0, np.sqrt((self.mueff - 1) / (n + 1)) - 1) + self.cs
        self.chiN = np.sqrt(n) * (1 - 1 / (4 * n) + 1 / (21 * n * n))
        self.m = np.asarray(x0, float).copy()
        self.sigma = sigma0
        self.C = np.eye(n); self.pc = np.zeros(n); self.ps = np.zeros(n)
        self.B = np.eye(n); self.D = np.ones(n)
        self.gen = 0; self.evals = 0
        self.lo, self.hi = bounds
        self.best_x, self.best_f = self.m.copy(), np.inf
        self.history = []

    def ask(self):
        self.Z = self.rng.standard_normal((self.lam, self.n))
        self.Y = self.Z @ np.diag(self.D) @ self.B.T
        return np.clip(self.m + self.sigma * self.Y, self.lo, self.hi)

    def tell(self, X, f):
        f = np.asarray(f, float)
        self.evals += len(f)
        idx = np.argsort(f)
        if f[idx[0]] < self.best_f:
            self.best_f = float(f[idx[0]]); self.best_x = X[idx[0]].copy()
        Ysel = self.Y[idx[: self.mu]]
        yw = self.w @ Ysel
        self.m = np.clip(self.m + self.sigma * yw, self.lo, self.hi)
        Cinvsqrt = self.B @ np.diag(1 / self.D) @ self.B.T
        self.ps = (1 - self.cs) * self.ps + np.sqrt(self.cs * (2 - self.cs) * self.mueff) * (Cinvsqrt @ yw)
        hsig = (np.linalg.norm(self.ps) / np.sqrt(1 - (1 - self.cs) ** (2 * (self.gen + 1))) / self.chiN
                < 1.4 + 2 / (self.n + 1))
        self.pc = (1 - self.cc) * self.pc + hsig * np.sqrt(self.cc * (2 - self.cc) * self.mueff) * yw
        rank_mu = (Ysel.T * self.w) @ Ysel
        self.C = ((1 - self.c1 - self.cmu) * self.C
                  + self.c1 * (np.outer(self.pc, self.pc) + (1 - hsig) * self.cc * (2 - self.cc) * self.C)
                  + self.cmu * rank_mu)
        self.sigma *= np.exp((self.cs / self.damps) * (np.linalg.norm(self.ps) / self.chiN - 1))
        self.C = np.triu(self.C) + np.triu(self.C, 1).T
        Dsq, self.B = np.linalg.eigh(self.C)
        self.D = np.sqrt(np.maximum(Dsq, 1e-20))
        self.gen += 1
        self.history.append((self.evals, float(f[idx[0]]), self.best_f, float(self.sigma)))

    def stop(self, max_evals=400, ftol=1e-4, sigma_tol=1e-3):
        if self.evals >= max_evals:
            return True
        if self.sigma * self.D.max() < sigma_tol:
            return True
        if len(self.history) > 8:
            recent = [h[1] for h in self.history[-8:]]
            if max(recent) - min(recent) < ftol:
                return True
        return False


# --------------------------------------------------------------- param spaces
def decode(x, space):
    """[0,1]^n -> {name: value} using space {name: (lo, hi)}."""
    return {k: lo + float(xi) * (hi - lo) for xi, (k, (lo, hi)) in zip(x, space.items())}


def encode(params, space):
    return np.array([(params[k] - lo) / (hi - lo) for k, (lo, hi) in space.items()])


def fit(loss, space, x0=None, sigma0=0.25, max_evals=400, seed=0, ftol=1e-5, verbose=False):
    """Minimise `loss(params_dict) -> float` over a box `space`.

    Returns {"params", "loss", "evals", "history"}. `history` is a list of
    (evals, gen_best, best_so_far, sigma) - feed it to an agent as a
    convergence report instead of re-running anything.
    """
    x0 = encode(x0, space) if isinstance(x0, dict) else (np.full(len(space), 0.5) if x0 is None else x0)
    opt = CMAES(x0, sigma0=sigma0, seed=seed)
    while not opt.stop(max_evals=max_evals, ftol=ftol):
        X = opt.ask()
        opt.tell(X, [loss(decode(x, space)) for x in X])
        if verbose:
            e, g, b, s = opt.history[-1]
            print(f"  [fit] evals={e:4d} gen_best={g:.5f} best={b:.5f} sigma={s:.4f}")
    return {"params": decode(opt.best_x, space), "loss": opt.best_f,
            "evals": opt.evals, "history": opt.history}


# --------------------------------------------------------------- silhouettes
def silhouette(V, axes=(0, 1), window=((-1, 1), (-1, 1)), res=128):
    """Rasterise vertex positions to a boolean silhouette, no renderer.

    V        (n,3) world-space vertices (use formq.arrays(ob)["V"])
    axes     which two world axes form the image plane: (0,1)=top, (0,2)=front, (1,2)=side
    window   ((u0,u1),(v0,v1)) world extents mapped onto the image
    A 3x3 close (dilate then erode) fills splat gaps without growing the outline,
    so the mesh must be dense enough that neighbouring verts land <=2px apart -
    subdivide before calling if not.
    """
    (x0, x1), (y0, y1) = window
    u, v = V[:, axes[0]], V[:, axes[1]]
    i = ((u - x0) / (x1 - x0) * res).astype(int)
    j = ((v - y0) / (y1 - y0) * res).astype(int)
    m = (i >= 0) & (i < res) & (j >= 0) & (j < res)
    img = np.zeros((res, res), bool)
    img[j[m], i[m]] = True
    p = np.pad(img, 1)
    dil = np.zeros_like(img)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            dil |= p[dy:dy + res, dx:dx + res]
    p = np.pad(dil, 1, constant_values=True)
    ero = np.ones_like(img)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            ero &= p[dy:dy + res, dx:dx + res]
    return ero


def iou(a, b):
    """Intersection over union of two boolean silhouettes. 1.0 = identical."""
    return float((a & b).sum() / max((a | b).sum(), 1))


def silhouette_from_image(path, res=128, threshold=0.5, invert=False):
    """Load a reference image (PNG with alpha, or a dark subject on light
    background) as a boolean silhouette target. Uses bpy's image loader so no
    PIL dependency."""
    import bpy
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    if px[..., 3].min() < 0.99:
        mask = px[..., 3] > threshold
    else:
        lum = px[..., :3].mean(axis=2)
        mask = lum < threshold
    if invert:
        mask = ~mask
    yi = (np.linspace(0, h - 1, res)).astype(int)
    xi = (np.linspace(0, w - 1, res)).astype(int)
    return mask[np.ix_(yi, xi)]
