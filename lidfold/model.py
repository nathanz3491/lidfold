"""A tiny per-pixel MLP (features -> base logit), trained in NumPy with Adam.

Small enough to train in a few seconds during calibration and to run on all
19 200 pixels of a frame in about a millisecond.
"""
from __future__ import annotations

import numpy as np


class PixelMLP:
    def __init__(self, n_in: int, hidden: int = 24, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.mu = np.zeros(n_in, np.float32)
        self.sigma = np.ones(n_in, np.float32)
        self.w1 = (rng.standard_normal((n_in, hidden)) * np.sqrt(2 / n_in)).astype(np.float32)
        self.b1 = np.zeros(hidden, np.float32)
        self.w2 = (rng.standard_normal((hidden, 1)) * np.sqrt(1 / hidden)).astype(np.float32)
        self.b2 = np.zeros(1, np.float32)

    def logits(self, x: np.ndarray) -> np.ndarray:
        """x: (..., n_in) -> (...) logits."""
        # Input normalisation folded into the first layer: one matmul fewer
        # passes over the 19 200 x 12 frame.
        shape = x.shape[:-1]
        w1 = self.w1 / self.sigma[:, None]
        b1 = self.b1 - (self.mu / self.sigma) @ self.w1
        h = x.reshape(-1, x.shape[-1]) @ w1
        h += b1
        np.maximum(h, 0.0, out=h)
        return (h @ self.w2 + self.b2).reshape(shape)

    def fit(self, x: np.ndarray, y: np.ndarray, steps: int = 600, batch: int = 4096,
            lr: float = 3e-3, l2: float = 1e-4, seed: int = 0) -> float:
        """Balanced logistic loss. Returns final training accuracy."""
        rng = np.random.default_rng(seed)
        self.mu = x.mean(0).astype(np.float32)
        self.sigma = (x.std(0) + 1e-4).astype(np.float32)
        z_all = ((x - self.mu) / self.sigma).astype(np.float32)
        pos, neg = np.flatnonzero(y > 0.5), np.flatnonzero(y <= 0.5)
        params = [self.w1, self.b1, self.w2, self.b2]
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        for step in range(1, steps + 1):
            idx = np.concatenate([rng.choice(pos, batch // 2), rng.choice(neg, batch // 2)])
            z, t = z_all[idx], y[idx].astype(np.float32)[:, None]
            pre = z @ self.w1 + self.b1
            h = np.maximum(pre, 0.0)
            out = h @ self.w2 + self.b2
            p = 1 / (1 + np.exp(-np.clip(out, -30, 30)))
            d_out = (p - t) / len(idx)
            grads = [None, None, h.T @ d_out + l2 * self.w2, d_out.sum(0)]
            d_h = (d_out @ self.w2.T) * (pre > 0)
            grads[0] = z.T @ d_h + l2 * self.w1
            grads[1] = d_h.sum(0)
            for i, (p_, g) in enumerate(zip(params, grads)):
                m[i] = 0.9 * m[i] + 0.1 * g
                v[i] = 0.999 * v[i] + 0.001 * g * g
                p_ -= lr * (m[i] / (1 - 0.9 ** step)) / (np.sqrt(v[i] / (1 - 0.999 ** step)) + 1e-8)
        pred = self.logits(x) > 0
        truth = y > 0.5
        return float(0.5 * (pred[truth].mean() + (~pred[~truth]).mean()))

    def state(self) -> dict[str, np.ndarray]:
        return {k: getattr(self, k) for k in ("mu", "sigma", "w1", "b1", "w2", "b2")}

    @classmethod
    def from_state(cls, state) -> "PixelMLP":
        net = cls(int(state["w1"].shape[0]), int(state["w1"].shape[1]))
        for k in ("mu", "sigma", "w1", "b1", "w2", "b2"):
            setattr(net, k, np.asarray(state[k], np.float32))
        return net
