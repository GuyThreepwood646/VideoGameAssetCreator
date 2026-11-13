"""Which way a model faces: the outline test's table, a second test from the feet, and the decision between them.

The 3D step's audit (stages/mesh.py) matches the model's outline from every 15 degrees against the drawn front, left,
back and right views and takes the best turn (imaging.best_alignment). A standing figure's outline is nearly the same
from the front and from the back, so that test alone can pick the turn half a circle off by a hair: in the realistic
test of 2026-11-13 one run's side views were both drawn facing the same way, "turned 180" beat "turned 0" by 0.011
(every other run: 0.067 to 0.16), and the whole model came out backwards with nothing saying so.

The toes test is independent of the pictures: on a standing figure the feet stick out in front of the shins, so the
direction from the middle of the shins to the middle of the feet, seen from above, is where the figure faces. It needs
two legs it can tell apart and two feet that agree; robes, long skirts, sitting poses and anything that is not a
person give no answer, and then the outline decision stands as before.

decide() uses the toes when the outline's margin over the opposite turn is small or when the two tests disagree by
more than DISAGREE_DEG, and says so; quality.facing_agree_check turns a disagreement into a check that fails.

Angles: a turn is degrees counter-clockwise seen from above that brings the front to face forward (quality.best_turn);
a yaw is where a face.Camera stands (0: in front, +Z; positive toward +X). A front seen from yaw y needs turn -y.
Coordinates are the glTF ones (Y up). Pure numpy.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

# The outline's best turn must beat the turn half a circle away by this much mean overlap to stand without the toes.
# The 11 runs of the realistic test: 0.011 on the one that came out backwards, 0.067 to 0.16 on the other ten.
MARGIN_MIN = 0.04
# The two tests disagree when their turns differ by more than this (the feet splay, and the outline works in steps
# of 15 degrees; the ten good runs differ by 1 to 15 degrees, the bad one by 179).
DISAGREE_DEG = 60.0
FEET_BAND = 0.035         # the feet: the lowest share of the figure's height
SHIN_BAND = (0.07, 0.14)  # the shins: this band of the height, above the feet and the ankles
# Each foot's middle must sit at least this far in front of its shin's, as a share of the height (27 mm on a 1.8 m
# figure). The 11 runs: 0.027 to 0.062 for the two feet together.
OFFSET_MIN = 0.015
# The two feet may point this far apart and still give one direction (the one between them). Figures drawn in an A-pose
# stand with the feet turned out: the 11 runs sit at 32 to 84 degrees apart.
FEET_AGREE_DEG = 110.0
GAP_MAX = 0.04            # share of the shins' surface allowed in the gap between the two legs
TOES_CATEGORIES = ("humanoid",)


def angle_diff(a: float, b: float) -> float:
    d = abs(float(a) - float(b)) % 360
    return min(d, 360 - d)


def signed(a: float) -> float:
    """An angle as -180 to 180."""
    return (float(a) + 180.0) % 360.0 - 180.0


# --- the outline test's table -----------------------------------------------------------------------------------


def turn_scores(refs: dict[str, np.ndarray], rendered: dict[int, np.ndarray], step: int = 15) -> dict[int, float]:
    """{turn: mean overlap}: for every turn, the better of the two fits imaging.best_alignment tries for it (the plain
    one and its mirrored twin). The best entry is best_alignment's choice; the others say by how much it won."""
    from . import quality
    from .imaging import VIEW_YAW, iou

    out: dict[int, float] = {}
    views = [v for v in refs if v in VIEW_YAW]
    for offset in range(0, 360, step):
        for sign in (1, -1):
            scores = [iou(refs[v], rendered[(offset + sign * VIEW_YAW[v]) % 360]) for v in views
                      if (offset + sign * VIEW_YAW[v]) % 360 in rendered]
            if not scores:
                continue
            mean = sum(scores) / len(scores)
            turn = quality.best_turn(offset, sign)
            if mean > out.get(turn, -1.0):
                out[turn] = round(mean, 4)
    return out


def margin(scores: dict[Any, float] | None, turn: float | None) -> float | None:
    """By how much `turn` beats the turn half a circle away in the outline test, or None when either is missing."""
    if not scores or turn is None:
        return None
    s = {int(k) % 360: float(v) for k, v in scores.items()}
    a, b = s.get(int(round(turn)) % 360), s.get((int(round(turn)) + 180) % 360)
    return None if a is None or b is None else round(a - b, 4)


# --- the toes test ----------------------------------------------------------------------------------------------


def _no(reason: str, **more: Any) -> dict[str, Any]:
    return {"found": False, "reason": reason, **more}


def toes(verts: np.ndarray, faces: np.ndarray | None = None) -> dict[str, Any]:
    """Where a standing figure's toes point. {found, yaw (the camera yaw the front is seen from), turn (the turn that
    brings it forward), offset (how far the feet sit in front of the shins, share of the height), feet_apart_deg (the
    angle between the two feet), reason (why not, when not found)}.

    Surface area is what is averaged (triangle middles weighted by area) when faces are given, so a mesh thinned out
    later gives the same answer; without faces, the vertices."""
    v = np.asarray(verts, dtype=np.float64)
    if v.ndim != 2 or len(v) < 50:
        return _no("no model")
    if faces is not None and len(faces):
        f = np.asarray(faces, dtype=np.int64)
        a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        pts = (a + b + c) / 3.0
        w = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    else:
        pts, w = v, np.ones(len(v))
    lo, h = float(v[:, 1].min()), float(v[:, 1].max() - v[:, 1].min())
    if h <= 0:
        return _no("no model")
    y = pts[:, 1]
    feet = y < lo + FEET_BAND * h
    shin = (y > lo + SHIN_BAND[0] * h) & (y < lo + SHIN_BAND[1] * h)
    if w[feet].sum() <= 0 or w[shin].sum() <= 0 or feet.sum() < 12 or shin.sum() < 12:
        return _no("no feet or shins at the bottom of the model")
    # Two legs: the shins' surface, seen from above, falls into two groups with a clear gap between them.
    sp, sw = pts[shin][:, [0, 2]], w[shin]
    mid = (sp * sw[:, None]).sum(0) / sw.sum()
    d = sp - mid
    cov = (d * sw[:, None]).T @ d / sw.sum()
    evals, evecs = np.linalg.eigh(cov)
    axis = evecs[:, int(np.argmax(evals))]            # across the two legs
    t = d @ axis
    left, right = t < 0, t >= 0
    if sw[left].sum() <= 0 or sw[right].sum() <= 0:
        return _no("the legs cannot be told apart")
    cl, cr = float((t[left] * sw[left]).sum() / sw[left].sum()), float((t[right] * sw[right]).sum() / sw[right].sum())
    sep = cr - cl
    gap = np.abs(t - 0.5 * (cl + cr)) < 0.15 * sep     # the middle third or so between the two legs' middles
    gap_share = float(sw[gap].sum() / sw.sum())
    if sep < 0.02 * h or gap_share > GAP_MAX:
        return _no("the legs cannot be told apart (a robe, a long skirt, or legs together)", gap_share=round(gap_share, 4))
    centre_t = 0.5 * (cl + cr)
    fp, fw = pts[feet][:, [0, 2]], w[feet]
    ft = (fp - mid) @ axis
    vecs = []
    for leg_s, leg_f in ((t < centre_t, ft < centre_t), (t >= centre_t, ft >= centre_t)):
        if fw[leg_f].sum() <= 0 or leg_f.sum() < 6:
            return _no("one leg has no foot on the ground (a pose that is not standing)")
        s_mid = (sp[leg_s] * sw[leg_s][:, None]).sum(0) / sw[leg_s].sum()
        f_mid = (fp[leg_f] * fw[leg_f][:, None]).sum(0) / fw[leg_f].sum()
        vecs.append((f_mid - s_mid) / h)
    lens = [float(np.hypot(*u)) for u in vecs]
    if min(lens) < OFFSET_MIN:
        return _no("the feet do not stick out in front of the shins", offset=round(min(lens), 4))
    yaws = [math.degrees(math.atan2(u[0], u[1])) for u in vecs]
    apart = angle_diff(yaws[0], yaws[1])
    if apart > FEET_AGREE_DEG:
        return _no("the two feet point different ways", feet_apart_deg=round(apart, 1))
    both = vecs[0] + vecs[1]
    yaw = math.degrees(math.atan2(both[0], both[1])) % 360.0
    return {"found": True, "yaw": round(yaw, 1), "turn": round((-yaw) % 360.0, 1), "offset": round(float(np.hypot(*both)) / 2.0, 4),
            "feet_apart_deg": round(apart, 1), "reason": None}


def toes_of_file(glb: Path | str, category: str | None = None) -> dict[str, Any]:
    """toes() on a model file. Only for the categories with two feet; never raises."""
    if category is not None and category not in TOES_CATEGORIES:
        return _no("not a two-legged character")
    try:
        import trimesh

        m = trimesh.load(str(glb), force="mesh", process=False)
        return toes(np.asarray(m.vertices), np.asarray(m.faces))
    except Exception as e:  # noqa: BLE001  (the outline decision stands)
        return _no(f"the model could not be read: {str(e)[:120]}")


# --- the decision -----------------------------------------------------------------------------------------------


def decide(outline_turn: float | None, scores: dict[Any, float] | None, toes_result: dict[str, Any] | None) -> dict[str, Any]:
    """The turn that brings the model's front forward, from both tests.

    Returns {turn (None when neither test has one), source ("outline", "toes" or None), outline_turn, outline_margin,
    toes_turn, toes_found, toes_reason, differ_deg (between the two tests, None when either is missing), agree (False
    only when both tests have an answer and they differ by more than DISAGREE_DEG), why}.

    The outline's turn stands unless the toes were found and either its margin over the opposite turn is under
    MARGIN_MIN or the toes point more than DISAGREE_DEG away from it. Then the turn is the outline test's best one
    among the turns within DISAGREE_DEG of the toes (the feet say which half, the outline says where in it), or the
    toes' own direction to the nearest 15 degrees when the outline has no table."""
    t = toes_result or {}
    found = bool(t.get("found")) and t.get("turn") is not None
    out: dict[str, Any] = {"turn": None if outline_turn is None else int(round(outline_turn)) % 360, "source": None if outline_turn is None else "outline",
                           "outline_turn": None if outline_turn is None else int(round(outline_turn)) % 360,
                           "outline_margin": margin(scores, outline_turn), "toes_turn": float(t["turn"]) if found else None,
                           "toes_found": found, "toes_reason": None if found else (t.get("reason") or "not measured"),
                           "differ_deg": None, "agree": True, "why": None}
    if not found or outline_turn is None:
        # With no trusted outline the model is left as built, as before: the toes alone do not turn it.
        return out
    differ = angle_diff(float(t["turn"]), outline_turn)
    out["differ_deg"] = round(differ, 1)
    out["agree"] = differ <= DISAGREE_DEG
    m = out["outline_margin"]
    close = m is not None and m < MARGIN_MIN
    if out["agree"] and not close:
        return out
    near = {int(k) % 360: float(s) for k, s in (scores or {}).items() if angle_diff(int(k), float(t["turn"])) <= DISAGREE_DEG}
    turn = max(near, key=lambda k: near[k]) if near else int(round(float(t["turn"]) / 15.0) * 15) % 360
    out.update(turn=turn, source="toes" if turn != out["outline_turn"] else "outline",
               why="the outline test was nearly a tie" if out["agree"] else "the outline and the feet disagree")
    return out


def metrics_of(d: dict[str, Any]) -> dict[str, Any]:
    """decide()'s result as the clean-up step's metrics (both tests' numbers)."""
    return {"face_best_turn": d["turn"], "face_outline_turn": d["outline_turn"], "face_outline_margin": d["outline_margin"],
            "face_toes_turn": d["toes_turn"], "face_toes_found": d["toes_found"], "face_toes_reason": d["toes_reason"],
            "face_tests_differ_deg": d["differ_deg"], "face_tests_agree": d["agree"], "face_decided_by": d["source"],
            "face_decision_note": d["why"]}
