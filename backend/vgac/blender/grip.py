"""Where the hand holds an item: grip point, grip frame and a confidence, from the item's prepared mesh.

Pure numpy (no bpy), so the backend tests import it as vgac.blender.grip and item_pivot.py (inside Blender) loads it
from this folder. Everything is in glTF model space: Y up, +Z the item's front as painted, metres.

The item frame this returns (columns of `rotation`, in model space; the item's exported file uses it as its own axes,
with the grip at the origin):

    +Z  along the item toward its business end: the blade's tip, the axe's head, the top of a staff or bow, the front
        of a shield (its face), the front of a backpack or hat
    +Y  across the item: the blade's width (its edges), the bow's curve toward the string, a shield's or hat's up
    +X  Y x Z (right-handed, glTF)

An attachment point's frame is +Z forward along the grip (out of the index-finger side of the fist) and +Y up toward
the thumb (vgac/sockets.py), so an item in this frame placed on a hand socket with no extra turn has its blade
pointing out of the fist with its edges in line with the arm: the hammer grip.

Rules by kind (research docs/CHARACTER-PARTS-RESEARCH.md 4.1 item 3):

    blade   long axis by PCA; 64 slices along it, each measured for width, thickness and roundness. The handle is the
            longest run of thin, nearly constant, roughly round slices in one end's 45 percent; the guard is the width
            jump beside it; the blade is the long flat run beyond. Grip: the middle of the handle run. Direction:
            from the handle toward the guard.
    haft    as blade, but the head is the wider end and the grip sits 30 percent along the handle run from its far end.
    pole    the head is the wider end (the narrower one for a wand); grip at grip_fraction of the length from the butt.
    bow     grip at the thickest slice in the middle 30 percent of the length; +Y from the grip toward the line
            between the tips (the string side).
    shield  the face normal is the thinnest PCA axis, pointed to the side the middle bulges toward; grip at the middle
            of the back face.
    worn    the mesh's own axes; pivot at the middle of the back face (the side that touches the body).
    head    the mesh's own axes; pivot at the middle of the bottom (the inner rim).
    hang    the mesh's own axes; pivot at the middle of the top (the handle a hand holds).

The picture was painted upright with the handle at the bottom (data/items.yaml pose_blocks), so for long items the
lower end is the first guess for the handle end; the slices confirm or overrule it, and the confidence says which.
"""
from __future__ import annotations

from typing import Any

import numpy as np

SLICES = 64
SURE = 0.6            # below this the guess is shown and flagged, never silently used
UP = np.array([0.0, 1.0, 0.0])
FRONT = np.array([0.0, 0.0, 1.0])
LONG_KINDS = ("blade", "haft", "pole", "bow")


def _unit(v):
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else None


def pca(P):
    """(centre, axes (3, 3) as columns sorted longest first, extents along each axis)."""
    P = np.asarray(P, float)
    c = P.mean(axis=0)
    Q = P - c
    w, vec = np.linalg.eigh(Q.T @ Q / max(len(Q), 1))
    order = np.argsort(w)[::-1]
    vec = vec[:, order]
    ext = np.array([float(np.ptp(Q @ vec[:, i])) for i in range(3)])
    return c, vec, ext


def slices(P, c, axis, n=SLICES):
    """Measure n slices along `axis`. -> list of {t (centre along the axis from c), lo, hi, count, width, thickness,
    round, centre (model space), major (unit, model space)}."""
    t = (P - c) @ axis
    lo, hi = float(t.min()), float(t.max())
    edges = np.linspace(lo, hi, n + 1)
    out = []
    # A plane basis perpendicular to the axis.
    a = _unit(np.cross(axis, UP if abs(float(axis @ UP)) < 0.9 else FRONT))
    b = np.cross(axis, a)
    for i in range(n):
        m = (t >= edges[i]) & (t <= edges[i + 1])
        Q = P[m]
        row = {"t": float(0.5 * (edges[i] + edges[i + 1])), "lo": float(edges[i]), "hi": float(edges[i + 1]), "count": int(m.sum()),
               "width": 0.0, "thickness": 0.0, "round": 0.0, "centre": c + axis * float(0.5 * (edges[i] + edges[i + 1])), "major": a}
        if len(Q) >= 4:
            uv = np.column_stack([(Q - c) @ a, (Q - c) @ b])
            mu = uv.mean(axis=0)
            C = np.cov((uv - mu).T) if len(uv) > 2 else np.eye(2)
            w, vec = np.linalg.eigh(C)
            major = vec[:, 1]
            minor = vec[:, 0]
            pu = (uv - mu) @ major
            pv = (uv - mu) @ minor
            width = float(np.percentile(pu, 98) - np.percentile(pu, 2))
            thick = float(np.percentile(pv, 98) - np.percentile(pv, 2))
            row.update(width=width, thickness=thick, round=thick / width if width > 1e-9 else 0.0,
                       centre=c + axis * row["t"] + a * mu[0] + b * mu[1], major=_unit(a * major[0] + b * major[1]))
        out.append(row)
    return out


def _runs(flags):
    """[(start, end_exclusive)] of consecutive True."""
    runs, s = [], None
    for i, f in enumerate(list(flags) + [False]):
        if f and s is None:
            s = i
        elif not f and s is not None:
            runs.append((s, i))
            s = None
    return runs


def _frame(z, y_hint):
    """Columns X, Y, Z with Z kept and Y made perpendicular to it."""
    z = _unit(z)
    for cand in (y_hint, UP, FRONT, np.array([1.0, 0.0, 0.0])):
        if cand is None:
            continue
        y = np.asarray(cand, float) - z * float(np.dot(cand, z))
        y = _unit(y)
        if y is not None:
            break
    x = np.cross(y, z)
    return np.column_stack([x, y, z])


def _clip01(x):
    return float(min(1.0, max(0.0, x)))


def _handle_end(sl, flip_ok=True):
    """Which end the handle is at from the slices alone: 'lo', 'hi' or None, with the handle run and the guard jump.
    -> (end, run (start, end), jump ratio, handle width)."""
    w = np.array([s["width"] for s in sl])
    r = np.array([s["round"] for s in sl])
    n = len(sl)
    wmax = float(w.max()) or 1.0
    thin = w < 0.45 * wmax
    roundish = r > 0.35
    steady = np.ones(n, bool)
    for i in range(1, n - 1):
        loc = max(w[i], 1e-6)
        steady[i] = abs(w[i + 1] - w[i - 1]) < 0.5 * loc + 0.02 * wmax
    ok = thin & roundish & steady & (w > 0)
    best = None
    for s, e in _runs(ok):
        if e - s < 3:
            continue
        # A pommel at the end of the run is wider than the handle: trim the run to its steady middle part.
        med = float(np.median(w[s:e]))
        keep = [(a, b) for a, b in _runs(w[s:e] <= 1.25 * med)]
        if keep:
            a, b = max(keep, key=lambda r: r[1] - r[0])
            s, e = s + a, s + b
        if e - s < 3:
            continue
        # The handle sits in one end's 45 percent (a pommel may cap it).
        if e <= int(0.45 * n):
            end = "lo"
        elif s >= int(0.55 * n):
            end = "hi"
        else:
            continue
        hw = float(np.median(w[s:e]))
        guard_i = e if end == "lo" else s - 1
        neighbourhood = w[max(0, guard_i - 1):guard_i + 2] if end == "lo" else w[max(0, guard_i - 1):guard_i + 1]
        jump = float(neighbourhood.max() / max(hw, 1e-6)) if len(neighbourhood) else 1.0
        score = (e - s) * (1.0 + min(jump, 4.0))
        if best is None or score > best[0]:
            best = (score, end, (s, e), jump, hw)
    if best is None:
        return None, None, 1.0, 0.0
    return best[1], best[2], best[3], best[4]


def find(V, kind: str, grip_fraction: float = 0.38, head_end: str = "wider", up=UP, front=FRONT) -> dict[str, Any]:
    """The grip for an item mesh. V: (n, 3) vertices in glTF model space. -> {kind, method, point, rotation (3x3,
    columns X, Y, Z), confidence, sure, length, reasons, details}."""
    V = np.asarray(V, float)
    up = _unit(up) if _unit(up) is not None else UP
    front = _unit(front) if _unit(front) is not None else FRONT
    out: dict[str, Any] = {"kind": kind, "method": "", "point": V.mean(axis=0) if len(V) else np.zeros(3),
                           "rotation": np.eye(3), "confidence": 0.0, "sure": False, "length": 0.0, "reasons": [], "details": {}}
    if len(V) < 8:
        out["method"] = "too few points"
        out["reasons"].append("the item has almost no surface")
        return out
    lo, hi = V.min(axis=0), V.max(axis=0)
    out["length"] = float(np.max(hi - lo))
    if kind in ("worn", "head", "hang"):
        mid = 0.5 * (lo + hi)
        if kind == "worn":
            p = np.array([mid[0], mid[1], lo[2]])
            method = "the middle of the side that touches the body"
        elif kind == "head":
            p = np.array([mid[0], lo[1], mid[2]])
            method = "the middle of the inner rim"
        else:
            p = np.array([mid[0], hi[1], mid[2]])
            method = "the middle of the top, where the handle is"
        out.update(method=method, point=p, rotation=_frame(front, up), confidence=0.9, sure=True)
        return out
    c, axes, ext = pca(V)
    if kind == "shield":
        n = axes[:, 2]
        radial = (V - c) - np.outer((V - c) @ n, n)
        rad = np.linalg.norm(radial, axis=1)
        rmax = float(rad.max()) or 1.0
        inner = (V - c)[rad < 0.3 * rmax] @ n
        rim = (V - c)[rad > 0.8 * rmax] @ n
        if len(inner) and len(rim):
            bulge = float(inner.mean() - rim.mean())
            # A flat shield's bulge is noise: fall back to the painted front.
            sign = np.sign(bulge) if abs(bulge) > 0.02 * ext[0] else np.sign(float(n @ front)) or 1.0
        else:
            bulge, sign = 0.0, np.sign(float(n @ front)) or 1.0
        n = n * sign
        t = (V - c) @ n
        p = c + n * float(np.percentile(t, 2))
        flat = 1.0 - _clip01(ext[2] / max(ext[1], 1e-6) / 0.6)
        conf = 0.5 + 0.4 * flat + (0.1 if abs(bulge) > 0.02 * ext[0] else 0.0)
        out.update(method="the middle of the back of the shield", point=p, rotation=_frame(n, up), confidence=round(_clip01(conf), 3),
                   details={"flatness": round(flat, 3), "bulge": round(bulge, 4)})
        out["sure"] = out["confidence"] >= SURE
        return out
    axis = axes[:, 0]
    elong = float(ext[0] / max(ext[1], 1e-6))
    sl = slices(V, c, axis)
    n = len(sl)
    w = np.array([s["width"] for s in sl])
    # The painted picture is upright, handle at the bottom: that end is the first guess.
    low_is_lo = float(axis @ up) >= 0  # moving toward 'lo' goes down when the axis points up
    painted_end = "lo" if low_is_lo else "hi"
    reasons = []

    def toward(end):
        """+Z: away from the `end` the hand is at."""
        return axis if end == "lo" else -axis

    if kind in ("blade", "haft"):
        end, run, jump, hw = _handle_end(sl)
        if end is None:
            end = painted_end
            # No handle run: hold it a tenth of the way in from the painted handle end.
            k = int(0.1 * n)
            idx = k if end == "lo" else n - 1 - k
            p = sl[idx]["centre"]
            conf = 0.25
            reasons.append("no clear handle was found, so the lower end as painted is used")
            method = "near the lower end (no clear handle)"
        else:
            s, e = run
            if kind == "blade":
                mid = 0.5 * (sl[s]["t"] + sl[e - 1]["t"])
                method = "the middle of the handle"
            else:
                far = sl[s]["t"] if end == "lo" else sl[e - 1]["t"]
                near = sl[e - 1]["t"] if end == "lo" else sl[s]["t"]
                mid = far + 0.3 * (near - far)
                method = "low on the handle"
            pts = np.array([sl[i]["centre"] for i in range(s, e)])
            p = pts.mean(axis=0) + axis * (mid - float((pts.mean(axis=0) - c) @ axis))
            run_frac = (e - s) / n
            conf = 0.35
            conf += 0.25 * _clip01((jump - 1.2) / 1.0)          # a guard or head beside the handle
            conf += 0.2 * (1.0 if 0.08 <= run_frac <= 0.45 else 0.0)  # a handle of a plausible length
            # Agrees with the painted handle end. Painters often draw a sword tip down anyway, so a clear guard
            # overrules the picture without lowering the confidence.
            if end == painted_end:
                conf += 0.2
            elif jump < 2.0:
                conf -= 0.15
                reasons.append("the handle was found at the end painted at the top")
            if jump < 1.4:
                reasons.append("no clear guard or head next to the handle")
        z = toward(end)
        far = [s for s in sl if (s["t"] - float((p - c) @ axis)) * float(z @ axis) > 0.1 * float(ext[0])]
        if kind == "blade" and far:
            flat = float(np.median([s["round"] for s in far]))
            if flat > 0.6:
                reasons.append("the blade is not flat")
                conf -= 0.1
        y_hint = None
        if far:
            mj = [s["major"] for s in far if s["count"] >= 4]
            if mj:
                ref = mj[0]
                y_hint = _unit(np.sum([m if float(m @ ref) >= 0 else -m for m in mj], axis=0))
        R = _frame(z, y_hint if y_hint is not None else up)
        out.update(method=method, point=p, rotation=R, confidence=round(_clip01(conf), 3),
                   details={"handle_end": end, "painted_end": painted_end, "guard_ratio": round(float(jump), 2), "elongation": round(elong, 2)})
    elif kind == "pole":
        k = max(1, n // 5)
        wlo, whi = float(w[:k].mean()), float(w[-k:].mean())
        ratio = max(wlo, whi) / max(min(wlo, whi), 1e-6)
        if ratio > 1.15:
            wider = "lo" if wlo > whi else "hi"
            head = wider if head_end == "wider" else ("hi" if wider == "lo" else "lo")
            butt = "hi" if head == "lo" else "lo"
            conf = 0.55 + 0.25 * (1.0 if butt == painted_end else -1.0)
            if butt != painted_end:
                reasons.append("the wider end is at the bottom as painted")
        else:
            butt = painted_end
            conf = 0.55
            reasons.append("both ends are alike, so the lower end as painted is the bottom")
        conf += 0.2 * _clip01((elong - 4.0) / 6.0)
        t0 = sl[0]["lo"] if butt == "lo" else sl[-1]["hi"]
        sgn = 1.0 if butt == "lo" else -1.0
        tg = t0 + sgn * grip_fraction * float(ext[0])
        i = int(np.clip(round((tg - sl[0]["lo"]) / max(sl[0]["hi"] - sl[0]["lo"], 1e-9) - 0.5), 0, n - 1))
        p = sl[i]["centre"] + axis * (tg - sl[i]["t"])
        z = toward(butt)
        R = _frame(z, up if abs(float(z @ up)) < 0.95 else front)
        method = f"{round(grip_fraction * 100)} percent of the way up from the bottom"
        out.update(method=method, point=p, rotation=R, confidence=round(_clip01(conf), 3),
                   details={"butt_end": butt, "end_ratio": round(ratio, 2), "elongation": round(elong, 2)})
    elif kind == "bow":
        mid_lo, mid_hi = int(0.35 * n), int(0.65 * n)
        thick = np.array([s["thickness"] * max(s["width"], 1e-6) for s in sl])
        i = mid_lo + int(np.argmax(thick[mid_lo:mid_hi])) if mid_hi > mid_lo else n // 2
        p = sl[i]["centre"]
        tips = (sl[0]["centre"], sl[-1]["centre"])
        chord_mid = 0.5 * (tips[0] + tips[1])
        string = chord_mid - p
        string = string - axis * float(string @ axis)
        bulge = float(np.linalg.norm(string))
        top = "hi" if low_is_lo else "lo"
        z = axis if top == "hi" else -axis
        R = _frame(z, _unit(string) if bulge > 0.02 * ext[0] else None)
        conf = 0.4 + 0.3 * _clip01(bulge / (0.08 * ext[0])) + 0.2 * _clip01((elong - 4.0) / 6.0)
        if bulge <= 0.02 * ext[0]:
            reasons.append("the bow is nearly straight, so the string side is a guess")
        out.update(method="the thickest part near the middle", point=p, rotation=R, confidence=round(_clip01(conf), 3),
                   details={"bulge": round(bulge, 4), "elongation": round(elong, 2)})
    else:
        out.update(method="the middle (unknown kind)", point=c, rotation=_frame(axis, up), confidence=0.3)
        reasons.append(f"no rule for {kind!r}")
    if kind in LONG_KINDS and elong < 2.0:
        reasons.append("the item is not long and thin, so the handle is hard to find")
        out["confidence"] = round(min(out["confidence"], 0.45), 3)
    out["reasons"] = reasons
    out["sure"] = out["confidence"] >= SURE
    return out


def to_json(res: dict[str, Any]) -> dict[str, Any]:
    """grip.json: plain lists, rotation as columns and as a quaternion (x, y, z, w)."""
    R = np.asarray(res["rotation"], float)
    try:
        from ..sockets import quat_from_frame  # backend
    except ImportError:  # inside Blender
        from sockets import quat_from_frame  # type: ignore[no-redef]
    q = quat_from_frame(R[:, 0], R[:, 1], R[:, 2])
    return {"version": 1, "space": "gltf", "kind": res["kind"], "method": res["method"],
            "point": [round(float(x), 5) for x in res["point"]],
            "x": [round(float(x), 5) for x in R[:, 0]], "y": [round(float(x), 5) for x in R[:, 1]], "z": [round(float(x), 5) for x in R[:, 2]],
            "rotation": [round(float(x), 6) for x in q], "confidence": float(res["confidence"]), "sure": bool(res["sure"]),
            "threshold": SURE, "length": round(float(res["length"]), 4), "reasons": list(res.get("reasons") or []),
            "details": res.get("details") or {}}
