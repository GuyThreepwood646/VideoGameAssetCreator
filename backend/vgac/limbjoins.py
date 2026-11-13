"""Limb joins: which upper arms, forearms, hands and thighs the 3D build grew into the body or into each other.

The 3D builder joins parts that touch or nearly touch in the picture it builds from (docs/MESH-RESEARCH.md, local).
Measured on the rigged model, with its bind-pose skeleton saying where each limb is: every limb segment is cut by
planes across the bone; in each cut the solid piece holding the bone is found, and the cut is "joined" when that piece
also holds another chain of the skeleton (the spine, a leg, the other arm) or is far bigger than the limb itself (a
cape, a robe, hair: things without bones). The rig check `limb_joins` (warn) reads the result.

How a cut is filled: the mesh's cross-section is drawn whole (no window: a joined piece is large, and cutting its
outline open would read it as free). Solid = inside an odd number of outlines and not reachable from outside, plus the
outline pixels; then each hole of a piece is filled unless another piece lies inside it (a hollow, double-walled build
becomes solid; a tunic or robe ring around the legs keeps its air). An outline end on a hole in the surface is closed
to the nearest other open end. numpy and OpenCV only; about half a second per character on the 38 stored rigs.
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path
from typing import Any

import numpy as np

SAMPLES = 16  # cuts per upper arm, forearm, thigh and calf
HAND_SAMPLES = 6  # cuts across the hand, from the wrist 0.07 of the height along the forearm's direction
PX_SHARE = 1 / 700  # pixel size over the model's height

# Lines set by eye on cut drawings of 38 stored rigs (docs/MESH-RESEARCH.md and the stage A note). Counted as "reach":
# cuts from the shoulder or hip to the end of the join, one free cut inside a join read as noise.
ARM_REACH = 10  # of 16: joined past 0.59 of the upper arm (the armpit, and a jacket, take the first 4 to 9)
THIGH_REACH = 8  # of 16: past 0.47 of the thigh (the crotch takes the first 2 to 4)
KNEE_REACH = 13  # of 16: the join runs to the knee
FOREARM_JOINED = 4  # of 16 forearm cuts joined (a free forearm has none on 35 of 38)
HAND_JOINED = 3  # of 6
# Clothing rule for the thighs: a join that runs to the knee while the feet stand apart is a robe, skirt, tunic or wide
# trousers, not a fault (on the 38 rigs every body join stopped by mid-thigh; every join to the knee was clothing by
# eye). UNTESTED on real models: legs pressed together knee to knee never occurred in the set. This guard (ankle gap
# over leg length under 0.3) reads such legs as joined, not as clothing; only a synthetic test covers it.
FEET_APART = 0.3
AROUND_CUTS = 4  # thigh cuts whose piece lies inside another piece (a robe hanging around free legs)

SEGMENTS = ("upperarm", "forearm", "hand", "thigh")
NEED = ("Hips", "Spine", "LeftArm", "LeftForeArm", "LeftHand", "RightArm", "RightForeArm", "RightHand",
        "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot")


# --- reading the rigged GLB ------------------------------------------------------------------------------------------

_CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _chunks(path: Path) -> tuple[dict[str, Any], bytes]:
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise ValueError("not a GLB file")
    off, js, bin_ = 12, None, b""
    while off < len(data):
        n, t = struct.unpack_from("<II", data, off)
        chunk = data[off + 8: off + 8 + n]
        if t == 0x4E4F534A:
            js = json.loads(chunk)
        elif t == 0x004E4942:
            bin_ = chunk
        off += 8 + n
    if js is None:
        raise ValueError("GLB without JSON")
    return js, bin_


def _accessor(js: dict[str, Any], bin_: bytes, i: int) -> np.ndarray:
    a = js["accessors"][i]
    bv = js["bufferViews"][a["bufferView"]]
    dt, nc = _CT[a["componentType"]], _NC[a["type"]]
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    item = np.dtype(dt).itemsize * nc
    stride = bv.get("byteStride")
    if stride and stride != item:
        raw = np.frombuffer(bin_, dtype=np.uint8, count=stride * (a["count"] - 1) + item, offset=start)
        idx = (np.arange(a["count"])[:, None] * stride + np.arange(item)[None, :]).ravel()
        return raw[idx].view(dt).reshape(a["count"], nc)
    return np.frombuffer(bin_, dtype=dt, count=a["count"] * nc, offset=start).reshape(a["count"], nc)


def _world_matrices(js: dict[str, Any]) -> list[np.ndarray]:
    nodes = js.get("nodes", [])
    local = []
    for n in nodes:
        if "matrix" in n:
            m = np.array(n["matrix"], dtype=float).reshape(4, 4).T
        else:
            x, y, z, w = n.get("rotation", [0, 0, 0, 1])
            r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
            m = np.eye(4)
            m[:3, :3] = r * np.array(n.get("scale", [1, 1, 1]), dtype=float)
            m[:3, 3] = n.get("translation", [0, 0, 0])
        local.append(m)
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    world: dict[int, np.ndarray] = {}

    def w(i: int) -> np.ndarray:
        if i not in world:
            world[i] = (w(parent[i]) @ local[i]) if i in parent else local[i]
        return world[i]
    return [w(i) for i in range(len(nodes))]


def load_rig(path: Path | str) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """(vertices n x 3, faces m x 3, joints {name without prefix: bind position}) of a rigged GLB, y up, with vertices
    split along texture seams welded back together. Joints {} when the GLB has no skin."""
    js, bin_ = _chunks(Path(path))
    world = _world_matrices(js)
    Vs, Fs, base, skin = [], [], 0, None
    for ni, n in enumerate(js.get("nodes", [])):
        if "mesh" not in n:
            continue
        for p in js["meshes"][n["mesh"]]["primitives"]:
            if p.get("mode", 4) != 4:
                continue
            v = _accessor(js, bin_, p["attributes"]["POSITION"]).astype(float)
            if "skin" in n:
                skin = n["skin"] if skin is None else skin
            else:
                v = v @ world[ni][:3, :3].T + world[ni][:3, 3]
            f = (_accessor(js, bin_, p["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in p
                 else np.arange(len(v)).reshape(-1, 3))
            Vs.append(v)
            Fs.append(f + base)
            base += len(v)
    if not Vs:
        raise ValueError("no triangles")
    V, F = np.vstack(Vs), np.vstack(Fs)
    key = np.round(V / 1e-5).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    F = inv.reshape(-1)[F]
    F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
    joints: dict[str, np.ndarray] = {}
    if skin is not None:
        sk = js["skins"][skin]
        ibm = _accessor(js, bin_, sk["inverseBindMatrices"]).astype(float).reshape(-1, 4, 4).transpose(0, 2, 1)
        for j, m in zip(sk["joints"], ibm):
            name = str(js["nodes"][j].get("name", j)).split(":")[-1]
            joints[name] = np.linalg.inv(m)[:3, 3]
    return V[first], F, joints


# --- cuts ----------------------------------------------------------------------------------------------------------

class _Mesh:
    def __init__(self, V: np.ndarray, F: np.ndarray):
        self.V = np.asarray(V, float)
        self.F = np.asarray(F, np.int64)
        self.H = float(self.V[:, 1].max() - self.V[:, 1].min())
        self.edge_key = np.int64(len(self.V) + 1)
        e = np.concatenate([self.F[:, [0, 1]], self.F[:, [1, 2]], self.F[:, [2, 0]]])
        e.sort(1)
        u, c = np.unique(e[:, 0] * self.edge_key + e[:, 1], return_counts=True)
        self.open_edges = set(u[c == 1].tolist())


def _crossings(M: _Mesh, d: np.ndarray):
    """Segments where the faces cross the plane (signed distance d per vertex), with the mesh edge each end lies on."""
    df = d[M.F]
    neg = df < 0
    cross = neg.any(1) & ~neg.all(1)
    F, df, neg = M.F[cross], df[cross], neg[cross]
    if len(F) == 0:
        z = np.zeros((0, 3))
        return z, z, np.zeros(0, np.int64), np.zeros(0, np.int64)
    ends, keys = [], []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        m = neg[:, a] != neg[:, b]
        s = df[:, a] / np.where(m, df[:, a] - df[:, b], 1.0)
        pa, pb = M.V[F[:, a]], M.V[F[:, b]]
        ends.append(pa + s[:, None] * (pb - pa))
        lo, hi = np.minimum(F[:, a], F[:, b]), np.maximum(F[:, a], F[:, b])
        keys.append(np.where(m, lo * M.edge_key + hi, -1))
    E, K = np.stack(ends, 1), np.stack(keys, 1)
    order = np.argsort(K < 0, axis=1, kind="stable")[:, :2]  # the two edges that cross
    r = np.arange(len(F))[:, None]
    P2, K2 = E[r, order], K[r, order]
    return P2[:, 0], P2[:, 1], K2[:, 0], K2[:, 1]


def _frame(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ref = np.array([0.0, 1.0, 0.0]) if abs(n[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(n, ref)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def _section(M: _Mesh, P: np.ndarray, n: np.ndarray):
    """-> labels of the solid pieces in the cut through P across n, a function points -> pixels, the pixel size."""
    import cv2

    a, b, ka, kb = _crossings(M, (M.V - P) @ n)
    px = M.H * PX_SHARE
    u, w = _frame(n)
    if len(a):
        allp = np.concatenate([a, b]) - P
        cu, cw = allp @ u, allp @ w
        lo = np.array([min(cu.min(), 0.0), min(cw.min(), 0.0)]) - 3 * px
        hi = np.array([max(cu.max(), 0.0), max(cw.max(), 0.0)]) + 3 * px
    else:
        lo, hi = np.array([-px, -px]), np.array([px, px])
    gx, gy = int(np.ceil((hi[0] - lo[0]) / px)) + 1, int(np.ceil((hi[1] - lo[1]) / px)) + 1

    def to_px(X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X) - P
        return np.stack([(X @ u - lo[0]) / px, (X @ w - lo[1]) / px], 1)
    img = np.zeros((gy, gx), np.uint8)
    if not len(a):
        _, lab = cv2.connectedComponents(img, connectivity=8)
        return lab, to_px, px
    A, B = to_px(a), to_px(b)
    cv2.polylines(img, list(np.round(np.stack([A, B], 1) * 8).astype(np.int32)), False, 255, 1, cv2.LINE_8, 3)
    segs = [(A, B)]
    if M.open_edges:  # an outline end on a hole in the surface: close it to the nearest other open end
        ends = np.concatenate([pts[np.fromiter((int(k) in M.open_edges for k in ks), bool, len(ks))]
                               for pts, ks in ((A, ka), (B, kb))])
        if len(ends) >= 2:
            lim = 0.03 * M.H / px
            used = np.zeros(len(ends), bool)
            D = np.linalg.norm(ends[:, None] - ends[None], axis=2)
            np.fill_diagonal(D, np.inf)
            ea, eb = [], []
            for i in np.argsort(D.min(1)):
                if used[i]:
                    continue
                Di = np.where(used, np.inf, D[i])
                j = int(np.argmin(Di))
                if Di[j] <= lim:
                    used[i] = used[j] = True
                    ea.append(ends[i])
                    eb.append(ends[j])
                    p, q = np.round(ends[i] * 8).astype(int), np.round(ends[j] * 8).astype(int)
                    cv2.line(img, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])), 255, 1, cv2.LINE_8, 3)
            if ea:
                segs.append((np.array(ea), np.array(eb)))
    pad = cv2.copyMakeBorder(img, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    cv2.floodFill(pad, None, (0, 0), 128)
    enclosed = (pad != 128)[1:-1, 1:-1]
    par = np.zeros((gy, gx + 1), np.int32)  # even-odd count at pixel centres
    for SA, SB in segs:
        y0, y1 = SA[:, 1], SB[:, 1]
        r0 = np.ceil(np.minimum(y0, y1) - 0.5).astype(int)
        r1 = np.ceil(np.maximum(y0, y1) - 0.5).astype(int)
        cnt = np.maximum(r1 - r0, 0)
        if cnt.sum() == 0:
            continue
        idx = np.repeat(np.arange(len(SA)), cnt)
        rows = np.concatenate([np.arange(p, q) for p, q in zip(r0[cnt > 0], r1[cnt > 0])])
        xc = SA[idx, 0] + (rows + 0.5 - y0[idx]) / (y1[idx] - y0[idx]) * (SB[idx, 0] - SA[idx, 0])
        cols = np.clip(np.ceil(xc - 0.5).astype(int), 0, gx)
        ok = (rows >= 0) & (rows < gy)
        np.add.at(par, (rows[ok], cols[ok]), 1)
    inside = (np.cumsum(par, 1)[:, :gx] % 2).astype(bool)
    solid = ((inside & enclosed) | (img > 0)).astype(np.uint8)
    solid = _fill_empty_holes(solid, min_px=(0.004 * M.H / px) ** 2)
    _, lab = cv2.connectedComponents(solid, connectivity=8)
    return lab, to_px, px


def _fill_empty_holes(solid: np.ndarray, min_px: float) -> np.ndarray:
    """Fill each hole of a solid piece unless another piece (over min_px pixels) lies inside it."""
    import cv2

    n, lab, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    out = solid.copy()
    big = np.zeros(n, bool)
    big[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_px
    for k in range(1, n):
        x, y, w, h = stats[k, :4]
        if w < 3 or h < 3:
            continue
        pad = cv2.copyMakeBorder((lab[y:y + h, x:x + w] == k).astype(np.uint8), 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
        cv2.floodFill(pad, None, (0, 0), 2)
        holes = (pad == 0)[1:-1, 1:-1]
        if not holes.any():
            continue
        nh, hl = cv2.connectedComponents(holes.astype(np.uint8), connectivity=4)
        others = lab[y:y + h, x:x + w]
        for j in range(1, nh):
            m = hl == j
            inner = np.unique(others[m])
            inner = inner[(inner > 0) & (inner != k)]
            if not big[inner].any():
                out[y:y + h, x:x + w][m] = 1
    return out


def _label_at(lab: np.ndarray, p: np.ndarray, reach_px: float) -> int:
    """The piece at pixel p, or the nearest one within reach_px; 0 when none."""
    gy, gx = lab.shape
    x, y = int(round(p[0])), int(round(p[1]))
    if not (0 <= x < gx and 0 <= y < gy):
        return 0
    if lab[y, x]:
        return int(lab[y, x])
    r = int(max(reach_px, 1))
    x0, x1, y0, y1 = max(0, x - r), min(gx, x + r + 1), max(0, y - r), min(gy, y + r + 1)
    ys, xs = np.nonzero(lab[y0:y1, x0:x1])
    if len(ys) == 0:
        return 0
    dd = (xs + x0 - x) ** 2 + (ys + y0 - y) ** 2
    k = int(np.argmin(dd))
    return int(lab[y0 + ys[k], x0 + xs[k]]) if dd[k] <= r * r else 0


def _chain_cross(chain: list[np.ndarray], P: np.ndarray, n: np.ndarray, R: float) -> list[np.ndarray]:
    out = []
    for a, b in zip(chain[:-1], chain[1:]):
        da, db = (a - P) @ n, (b - P) @ n
        if (da < 0) != (db < 0):
            x = a + da / (da - db) * (b - a)
            if np.linalg.norm(x - P) < R:
                out.append(x)
    return out


def _enclosed(lab: np.ndarray, mask: np.ndarray) -> bool:
    """The piece touches no open air around the cut: another piece surrounds it."""
    import cv2

    pad = cv2.copyMakeBorder((lab == 0).astype(np.uint8), 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=1)
    cv2.floodFill(pad, None, (0, 0), 2)
    outside = (pad == 2)[1:-1, 1:-1]
    ring = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    return not bool((ring & outside).any())


def _profile(M: _Mesh, A: np.ndarray, B: np.ndarray, others: dict[str, list[np.ndarray]], samples: int = SAMPLES,
             t0: float = 0.04, t1: float = 0.96) -> list[dict[str, Any]]:
    L = float(np.linalg.norm(B - A))
    n = (B - A) / max(L, 1e-9)
    R = 0.32 * M.H
    out = []
    for t in np.linspace(t0, t1, samples):
        P = A + t * (B - A)
        lab, to_px, px = _section(M, P, n)
        me = _label_at(lab, to_px(P)[0], 0.017 * M.H / px)
        rec: dict[str, Any] = {"solid": bool(me), "with": []}
        if me:
            mask = lab == me
            rec["r"] = float(np.sqrt(mask.sum() * px * px / np.pi)) / M.H
            for name, ch in others.items():
                if any(_label_at(lab, to_px(x)[0], 0.011 * M.H / px) == me for x in _chain_cross(ch, P, n, R)):
                    rec["with"].append(name)
            rec["enclosed"] = _enclosed(lab, mask)
        out.append(rec)
    return out


def _mark_big(profiles: list[list[dict[str, Any]]]) -> None:
    """A cut is also joined when its piece is far bigger than the limb: over 0.12 of the height, or over 0.05 and 1.8
    times the 25th percentile size of that segment on both sides (a sleeve grown into a robe all along still has a slim
    elbow to compare with)."""
    radii = [p["r"] for prof in profiles for p in prof if p.get("r")]
    ref = float(np.percentile(radii, 25)) if radii else 0.0
    for prof in profiles:
        for p in prof:
            r = p.get("r") or 0.0
            p["big"] = bool(r > 0.12 or (r > 0.05 and r > 1.8 * ref))
            p["joined"] = bool(p["with"]) or p["big"]


def reach(joined: list[bool]) -> int:
    """Cuts from the root to the end of the join: the last joined cut before two free cuts in a row."""
    last, gap = -1, 0
    for i, x in enumerate(joined):
        if x:
            last, gap = i, 0
        else:
            gap += 1
            if gap >= 2 and (last >= 0 or i >= 2):
                break
    return last + 1


def _bone_ok(J: dict[str, np.ndarray], H: float) -> dict[str, str | None]:
    """Per limb, why its skeleton cannot be measured (None when it can): a bone too short, or far shorter than the
    other side's (a skeleton squashed at the shoulder)."""
    out: dict[str, str | None] = {}
    for s, o in (("Left", "Right"), ("Right", "Left")):
        for seg, (a, b), lo in (("upperarm", ("Arm", "ForeArm"), 0.05), ("forearm", ("ForeArm", "Hand"), 0.05),
                                ("thigh", ("UpLeg", "Leg"), 0.06), ("calf", ("Leg", "Foot"), 0.06)):
            mine = float(np.linalg.norm(J[s + b] - J[s + a]))
            other = float(np.linalg.norm(J[o + b] - J[o + a]))
            out[f"{s}_{seg}"] = "short_bone" if mine < lo * H or mine < 0.5 * other else None
        out[f"{s}_hand"] = out[f"{s}_forearm"]
    return out


def measure_arrays(V: np.ndarray, F: np.ndarray, joints: dict[str, np.ndarray]) -> dict[str, Any]:
    """The reading for a mesh and its bind-pose joints (names without a prefix). See measure()."""
    J = {k.split(":")[-1]: np.asarray(v, float) for k, v in joints.items()}
    missing = [k for k in NEED if k not in J]
    if missing:
        return {"measured": False, "reason": "unmapped", "missing": missing[:6]}
    M = _Mesh(V, F)
    sp = [J[k] for k in ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head") if k in J]
    sp = [sp[0] - (sp[1] - sp[0]) * 0.3] + sp + [sp[-1] + (sp[-1] - sp[-2]) * 1.5]
    leg = {s: [J[s + "UpLeg"], J[s + "Leg"], J[s + "Foot"]] + ([J[s + "ToeBase"]] if s + "ToeBase" in J else []) for s in ("Left", "Right")}
    arm = {s: [J[s + "Arm"], J[s + "ForeArm"], J[s + "Hand"]] for s in ("Left", "Right")}
    prof: dict[str, list[dict[str, Any]]] = {}
    for s, o in (("Left", "Right"), ("Right", "Left")):
        oa = {"body": sp, "leg": leg[s], "other_leg": leg[o], "other_arm": arm[o]}
        prof[s + "_upperarm"] = _profile(M, J[s + "Arm"], J[s + "ForeArm"], oa)
        prof[s + "_forearm"] = _profile(M, J[s + "ForeArm"], J[s + "Hand"], oa)
        fd = J[s + "Hand"] - J[s + "ForeArm"]
        tip = J[s + "Hand"] + fd / max(float(np.linalg.norm(fd)), 1e-9) * 0.07 * M.H
        prof[s + "_hand"] = _profile(M, J[s + "Hand"], tip, {"body": sp, "leg": leg[s], "other_leg": leg[o]}, HAND_SAMPLES, 0.15, 0.9)
        ol = {"other_leg": leg[o], "body": sp, "arm": arm[s]}
        prof[s + "_thigh"] = _profile(M, J[s + "UpLeg"], J[s + "Leg"], ol)
    for group in (("upperarm", "forearm"), ("hand",), ("thigh",)):
        _mark_big([prof[f"{s}_{g}"] for s in ("Left", "Right") for g in group])
    why_not = _bone_ok(J, M.H)
    leg_len = float(np.mean([np.linalg.norm(J[s + "UpLeg"] - J[s + "Foot"]) for s in ("Left", "Right")]))
    feet = float(np.linalg.norm((J["LeftFoot"] - J["RightFoot"])[[0, 2]])) / max(leg_len, 1e-9)
    limbs: dict[str, dict[str, Any]] = {}
    for k, p in prof.items():
        seg = k.split("_", 1)[1]
        j = [x["joined"] for x in p]
        r, nj = reach(j), int(sum(j))
        within = p[:max(r, 1)] if seg in ("upperarm", "thigh") else p
        what = sorted({w for x in within if x["joined"] for w in x["with"]}
                      | ({"other"} if any(x["big"] and not x["with"] for x in within if x["joined"]) else set()))
        rec: dict[str, Any] = {"reach": r, "joined": nj, "with": what, "cuts": "".join("X" if x else "." for x in j)}
        off_body = sum(not x["solid"] for x in p)
        if why_not.get(k) or off_body > len(p) // 4:
            rec["state"], rec["why"] = "unmeasured", why_not.get(k) or "bone_outside"
        elif seg == "upperarm":
            rec["state"] = "joined" if r >= ARM_REACH else "free"
            rec["partly"] = r == ARM_REACH
        elif seg == "forearm":
            rec["state"] = "joined" if nj >= FOREARM_JOINED else "free"
        elif seg == "hand":
            rec["state"] = "joined" if nj >= HAND_JOINED else "free"
        else:
            if r >= KNEE_REACH and feet >= FEET_APART:
                rec["state"] = "clothing"
            elif r >= THIGH_REACH:
                rec["state"] = "joined"
                rec["partly"] = r == THIGH_REACH
            elif sum(bool(x.get("enclosed")) for x in p) >= AROUND_CUTS:
                rec["state"] = "around"
            else:
                rec["state"] = "free"
        limbs[k] = rec
    return {"measured": True, "limbs": limbs, "feet_apart": round(feet, 3)}


def measure(glb: Path | str) -> dict[str, Any]:
    """metrics.limb_joins for a rigged humanoid GLB: {measured, limbs {Left_upperarm: {state, reach, joined, with,
    cuts, partly?, why?}, ...}, feet_apart, seconds} where state is free, joined, clothing (a robe, skirt or wide
    trousers joining the thighs to the knee), around (a robe hanging around free legs) or unmeasured (why: short_bone,
    bone_outside); or {measured: False, reason: unmapped | error}. Never raises."""
    t = time.perf_counter()
    try:
        V, F, joints = load_rig(glb)
        out = measure_arrays(V, F, joints) if joints else {"measured": False, "reason": "unmapped", "missing": ["skin"]}
    except Exception as e:  # noqa: BLE001
        out = {"measured": False, "reason": "error", "error": str(e)[:300]}
    out["seconds"] = round(time.perf_counter() - t, 2)
    return out


# --- the check's words ---------------------------------------------------------------------------------------------

_WITH_WORDS = {"body": "the body", "leg": "the leg", "other_leg": "the other leg", "arm": "the arm", "other_arm": "the other arm",
               "other": "something it wears or carries (a cape, cloak, hair or an item)"}
_LIMB_WORDS = {"upperarm": ("upper arm", "upper arms"), "forearm": ("forearm", "forearms"), "hand": ("hand", "hands"),
               "thigh": ("thigh", "thighs")}
_COST = {
    "upperarm": "When an arm lifts in a game, the joined strip stretches like a web between the arm and the side.",
    "forearm": "When the arm moves, it pulls the surface it is grown into along with it.",
    "hand": "When the hand moves, it pulls the surface it is grown into along with it, and it cannot let go of it.",
    "thigh": "In a walk each leg pulls the other along, and the inside of the thighs stretches.",
}
_WHY_WORDS = {"short_bone": "the skeleton's bones there are too short (the skeleton is broken there)",
              "bone_outside": "the skeleton's bones there lie outside the model"}
FIX = {"stage": "turnaround", "params": {"pose_ready": True}}
FIX_LABEL = "Make the side views again with Pose for 3D first"


def _join_words(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _sides(keys: list[str], seg: str) -> str:
    sides = sorted({k.split("_")[0].lower() for k in keys}, reverse=False)
    one, both = _LIMB_WORDS[seg]
    return f"both {both}" if len(sides) == 2 else f"the {sides[0]} {one}"


def _fault_sentence(seg: str, keys: list[str], limbs: dict[str, Any]) -> str:
    what = _sides(keys, seg)
    partly = all(limbs[k].get("partly") for k in keys)
    if seg == "thigh":
        return f"The thighs are {'partly ' if partly else ''}joined to each other, from the hip to past the middle of the thigh."
    withs = sorted({w for k in keys for w in limbs[k]["with"]} - {"other_arm"}) or ["body"]
    into = _join_words([_WITH_WORDS.get(w, w) for w in withs])
    verb = "are" if what.startswith("both") else "is"
    if seg == "upperarm":
        return f"{what[0].upper() + what[1:]} {verb} {'partly ' if partly else ''}grown into {into}, from the armpit to past the middle of the arm."
    return f"{what[0].upper() + what[1:]} {verb} grown into {into}."


def check_values(m: dict[str, Any] | None, legs_parted: int = 0, legs_left_joined: bool = False) -> dict[str, Any] | None:
    """The rig check limb_joins from metrics.limb_joins: {ok, value, limit, help, fix?, fix_label?, vars} or None when
    the metric is absent. A skeleton that cannot be measured passes with a visible note, never silently."""
    if not m:
        return None
    parted = (" The legs were also joined low down (a strand between the feet); that strand has been parted, so the "
              "feet move apart freely.") if legs_parted else (
        " The legs are joined low down (a strand between the feet); parting them is turned off." if legs_left_joined else "")
    if not m.get("measured"):
        why = ("the skeleton does not have the usual named arm and leg bones, so we cannot tell which part is an arm"
               if m.get("reason") == "unmapped" else "the rigged model could not be read")
        return {"ok": True, "value": "not measured", "limit": "free", "vars": {"measured": False, "reason": m.get("reason")},
                "help": f"Not measured: whether arms or legs are grown into the body was not checked, because {why}.{parted}"}
    limbs = m.get("limbs") or {}
    faults = {seg: [k for k, v in limbs.items() if k.endswith("_" + seg) and v.get("state") == "joined"] for seg in SEGMENTS}
    faults = {seg: keys for seg, keys in faults.items() if keys}
    notes = []
    if any(v.get("state") == "clothing" for k, v in limbs.items() if k.endswith("_thigh")):
        notes.append("The legs are inside a robe, skirt, tunic or wide trousers that joins them down to the knee. That is the "
                     "clothing, not a fault; it stretches between the legs in a walk, as such clothing does in most games.")
    elif any(v.get("state") == "around" for k, v in limbs.items() if k.endswith("_thigh")):
        notes.append("A robe or skirt hangs around the legs; the legs inside it are free. The clothing will stretch "
                     "between the legs in a walk, as such clothing does in most games.")
    unmeasured = [k for k, v in limbs.items() if v.get("state") == "unmeasured"]
    if unmeasured:
        parts = []
        for side in ("Left", "Right"):
            arm = [g for g in ("upperarm", "forearm", "hand") if f"{side}_{g}" in unmeasured]
            if len(arm) == 3:
                parts.append(f"{side.lower()} arm")
            else:
                parts += [f"{side.lower()} {_LIMB_WORDS[g][0]}" for g in arm]
            if f"{side}_thigh" in unmeasured:
                parts.append(f"{side.lower()} thigh")
        whys = sorted({_WHY_WORDS.get(limbs[k].get("why"), "the skeleton's bones there could not be used") for k in unmeasured})
        notes.append(f"Not measured: the {_join_words(parts)}, because {_join_words(whys)}.")
    tail = (" " + " ".join(notes) if notes else "") + parted
    vars_ = {"measured": True, "faults": {seg: sorted(k.split('_')[0].lower() for k in keys) for seg, keys in faults.items()},
             "unmeasured": unmeasured}
    if not faults:
        return {"ok": True, "value": 0, "limit": 0, "vars": vars_,
                "help": ("The measured arms and legs" if unmeasured else "The arms and legs")
                + " are free of the body and of each other in the 3D model, so they move on their own." + tail}
    said = " ".join(_fault_sentence(seg, keys, limbs) for seg, keys in faults.items())
    cost = " ".join(_COST[seg] for seg in faults)
    help_ = (f"{said} {cost} The 3D builder joins parts that touch or nearly touch in the picture it builds from. "
             "What fixes it: in step 2 (Check all sides) turn on Pose for 3D first, so the character is drawn with the arms "
             "held well away from the body and the feet apart, then make the 3D model and the rig again." + tail)
    return {"ok": False, "value": sum(len(k) for k in faults.values()), "limit": 0, "vars": vars_, "help": help_,
            "fix": dict(FIX), "fix_label": FIX_LABEL}
