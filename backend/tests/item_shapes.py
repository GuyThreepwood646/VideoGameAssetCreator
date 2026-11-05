"""Synthetic item point clouds (glTF space, Y up, metres) for the grip tests."""
from __future__ import annotations

import numpy as np


def _cyl(rng, n, r, y0, y1, x=0.0, z=0.0):
    a = rng.uniform(0, 2 * np.pi, n)
    y = rng.uniform(y0, y1, n)
    return np.column_stack([x + r * np.cos(a), y, z + r * np.sin(a)])


def _box(rng, n, lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    P = rng.uniform(lo, hi, (n, 3))
    # push each point to the nearest face so it is a surface sample
    d = np.minimum(P - lo, hi - P)
    k = np.argmin(d, axis=1)
    for i in range(len(P)):
        j = k[i]
        P[i, j] = lo[j] if (P[i, j] - lo[j]) < (hi[j] - P[i, j]) else hi[j]
    return P


def _sphere(rng, n, r, c):
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return np.asarray(c) + r * v


def sword(seed=0, flip=False):
    """Handle 0 to 0.15 m, guard 0.15 to 0.18, blade to 0.9 (edges along X), pommel below. flip: tip down."""
    rng = np.random.default_rng(seed)
    P = np.vstack([
        _cyl(rng, 900, 0.016, 0.0, 0.15),
        _box(rng, 900, [-0.10, 0.15, -0.015], [0.10, 0.18, 0.015]),
        _box(rng, 4000, [-0.028, 0.18, -0.004], [0.028, 0.90, 0.004]),
        _sphere(rng, 300, 0.024, [0, -0.015, 0]),
    ])
    if flip:
        P[:, 1] = 0.9 - P[:, 1]
    return P


def staff(seed=0):
    """A 1.7 m shaft with a wide head at the top."""
    rng = np.random.default_rng(seed)
    return np.vstack([_cyl(rng, 3000, 0.018, 0.0, 1.55), _sphere(rng, 800, 0.08, [0, 1.62, 0])])


def bow(seed=0):
    """A 1.2 m bow curved toward -Z (the string is on +Z), grip thicker in the middle."""
    rng = np.random.default_rng(seed)
    t = rng.uniform(-0.6, 0.6, 4000)
    z = -0.12 * (1 - (t / 0.6) ** 2)
    r = 0.01 + 0.012 * np.exp(-(t / 0.08) ** 2)
    a = rng.uniform(0, 2 * np.pi, len(t))
    P = np.column_stack([r * np.cos(a), t + 0.6, z + r * np.sin(a)])
    return P


def shield(seed=0):
    """A round shield of radius 0.3 m in the XY plane, domed toward +Z, with a handle bar on the back."""
    rng = np.random.default_rng(seed)
    n = 5000
    rr = np.sqrt(rng.uniform(0, 1, n)) * 0.3
    a = rng.uniform(0, 2 * np.pi, n)
    x, y = rr * np.cos(a), rr * np.sin(a)
    z = 0.06 * (1 - (rr / 0.3) ** 2)
    front = np.column_stack([x, y + 0.3, z])
    back = np.column_stack([x, y + 0.3, z - 0.01])
    bar = _box(rng, 300, [-0.08, 0.28, -0.05], [0.08, 0.32, -0.03])
    return np.vstack([front, back, bar])
