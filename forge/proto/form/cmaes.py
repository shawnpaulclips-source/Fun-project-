"""Minimal (mu/mu_w, lambda)-CMA-ES in numpy. No dependencies beyond numpy.

Hansen's tutorial defaults (arXiv:1604.00772). Box bounds are handled by
optimising in [0,1]^n and clipping, which is what you want for procedural
parameters that have hard anatomical ranges.

    opt = CMAES(x0, sigma0=0.25, popsize=None, seed=0)
    while not opt.stop():
        X = opt.ask()
        opt.tell(X, [loss(x) for x in X])
    opt.best_x, opt.best_f
"""
import numpy as np


class CMAES:
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
        self.C = np.eye(n)
        self.pc = np.zeros(n)
        self.ps = np.zeros(n)
        self.B = np.eye(n)
        self.D = np.ones(n)
        self.gen = 0
        self.evals = 0
        self.lo, self.hi = bounds
        self.best_x, self.best_f = self.m.copy(), np.inf
        self.history = []

    def ask(self):
        self.Z = self.rng.standard_normal((self.lam, self.n))
        self.Y = self.Z @ np.diag(self.D) @ self.B.T
        X = self.m + self.sigma * self.Y
        return np.clip(X, self.lo, self.hi)

    def tell(self, X, f):
        f = np.asarray(f, float)
        self.evals += len(f)
        idx = np.argsort(f)
        if f[idx[0]] < self.best_f:
            self.best_f = float(f[idx[0]])
            self.best_x = X[idx[0]].copy()
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
