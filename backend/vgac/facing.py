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

A near-tie with no toes to read (a robe, a boxy figure, a flat model) used to stand on the outline alone: in the looks
research of 2026-11-16 that turned a Retro 64 figure 165 degrees (margin 0.014) and a robed mage 180 (0.003). Then
the half the 3D step built as the front is kept (the builder faces nearly every figure forward: of the 98 library
builds the outline or the toes settle clearly, all but one face within 30 degrees of as built, the one at 285), the
outline choosing the turn within that half; the colour of the drawn front and back views against step 3's own renders (colour_evidence)
can confirm it, or overrule it when it is clearly the other way round. When nothing confirms it, the decision says it is
not sure (`sure` False) and the facing_tests check warns, with the turn the other way round as its fix.

A flat model (FLAT_DEPTH: the step 3 builder made a card, as it did on five library builds) has no front or
back in its shape: the toes test does not read it (its "feet" point through the card), and the decision is never sure.

Angles: a turn is degrees counter-clockwise seen from above that brings the front to face forward (quality.best_turn);
a yaw is where a face.Camera stands (0: in front, +Z; positive toward +X). A front seen from yaw y needs turn -y.
Coordinates are the glTF ones (Y up). numpy and Pillow.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

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
# A model thinner than this share of its height through the chest (the horizontal axis it is thinnest along) is flat:
# the library's flat cards measure 0.011 to 0.014, every other humanoid build 0.13 or more.
FLAT_DEPTH = 0.05
# On a near-tie with no toes, the turn is taken in the half within this of as built (turn 0).
AS_BUILT_HALF = 90.0
# The colour of the drawn front and back views against step 3's renders (colour_evidence), in Lab: the head band's
# difference between the two turns and the whole figure's, both at least these, settle a near-tie. Over the library's
# 98 clearly settled builds (78 models) the head band leaned the wrong way at most 1.8 (a bald library-hair model) and
# the whole figure at most 1.2; the two near-ties the outline had backwards (R1, a library-hair mage) read 11.2 and 9.1
# for the head and 3.2 and 4.2 for the whole figure the right way round.
COLOUR_HEAD_MIN = 4.0
COLOUR_ALL_MIN = 1.5
HEAD_ROWS = 0.2           # the head band: the top share of the figure's height
COLOUR_N = 128            # rows the figures are compared at


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


def depth(verts: np.ndarray) -> float | None:
    """How thick the figure is through the chest (45 to 75 percent of its height), along the horizontal axis it is
    thinnest along, as a share of its height (2nd to 98th percentile). None when there is no model."""
    v = np.asarray(verts, dtype=np.float64)
    if v.ndim != 2 or len(v) < 50:
        return None
    lo, h = float(v[:, 1].min()), float(v[:, 1].max() - v[:, 1].min())
    if h <= 0:
        return None
    xz = v[:, [0, 2]] - v[:, [0, 2]].mean(0)
    evals, evecs = np.linalg.eigh(np.cov(xz.T))
    t = xz @ evecs[:, int(np.argmin(evals))]
    y = (v[:, 1] - lo) / h
    band = (y > 0.45) & (y < 0.75)
    if band.sum() < 20:
        band = np.ones(len(v), bool)
    return round(float(np.percentile(t[band], 98) - np.percentile(t[band], 2)) / h, 4)


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
    d = depth(v)
    if d is not None and d < FLAT_DEPTH:
        return _no("the model is flat, like a card, so its feet do not point anywhere", flat=True, depth=d)
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


# --- the colour of the drawn views against step 3's renders ---------------------------------------------------


def _figure(path: Path | str, n: int = COLOUR_N) -> tuple[np.ndarray, np.ndarray] | None:
    """A cut-out cropped to its figure and scaled to n rows (its height), centred in a frame 2n wide: (rgb, mask)."""
    from PIL import Image

    im = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float64) / 255.0
    ys, xs = np.where(im[..., 3] > 0.5)
    if len(xs) == 0:
        return None
    c = im[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = c.shape[:2]
    nw = max(1, int(round(w * n / h)))
    big = Image.fromarray((c * 255).astype(np.uint8), "RGBA").resize((nw, n), Image.BOX)
    a = np.asarray(big, dtype=np.float64) / 255.0
    W = 2 * n
    if nw > W:
        x = (nw - W) // 2
        a, nw = a[:, x:x + W], W
    rgb, mask = np.zeros((n, W, 3)), np.zeros((n, W), bool)
    x0 = (W - nw) // 2
    rgb[:, x0:x0 + nw] = a[..., :3]
    mask[:, x0:x0 + nw] = a[..., 3] > 0.9
    return rgb, mask


def _colour_diff(a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray], rows: int) -> float | None:
    """Mean Lab difference of two figures, softened a little, where both are, in the top `rows` rows."""
    from . import face

    (ra, ma), (rb, mb) = a, b
    m = ma & mb
    m[rows:] = False
    if m.sum() < 30:
        return None
    la = face.to_lab(np.clip(face.masked_blur(ra, ma, 1.5), 0, 1))
    lb = face.to_lab(np.clip(face.masked_blur(rb, mb, 1.5), 0, 1))
    return float(np.linalg.norm(la - lb, axis=-1)[m].mean())


def colour_evidence(pictures: dict[str, Path | str], renders: dict[int, Path | str], turn: float) -> dict[str, Any] | None:
    """How much better the drawn front and back views match step 3's own colour renders at `turn` than at the turn half a
    circle away (positive: `turn` is the right way round), in Lab, for the head band and for the whole figure.

    The outline of a standing figure is about the same from in front and from behind, its colour is not: the face against
    the back of the hair, a shirt front against its back. pictures: {"front"/"back": cut-out}; renders: {camera yaw: the
    mesh audit's render at that yaw (stages/mesh.py, every 15 degrees)}. A view seen at turn t is the render at
    VIEW_YAW - t. None when there is nothing to compare."""
    from .imaging import VIEW_YAW

    t = int(round(float(turn) / 15.0) * 15) % 360
    head_rows = max(4, int(round(HEAD_ROWS * COLOUR_N)))
    head, whole, per = [], [], {}
    for v in ("front", "back"):
        p = pictures.get(v)
        y0 = (VIEW_YAW[v] - t) % 360
        y1 = (y0 + 180) % 360
        if p is None or renders.get(y0) is None or renders.get(y1) is None:
            continue
        try:
            fp, f0, f1 = _figure(p), _figure(renders[y0]), _figure(renders[y1])
        except Exception:  # noqa: BLE001  (an unreadable picture gives no evidence)
            continue
        if fp is None or f0 is None or f1 is None:
            continue
        for rows, out in ((head_rows, head), (COLOUR_N, whole)):
            d0, d1 = _colour_diff(fp, f0, rows), _colour_diff(fp, f1, rows)
            if d0 is not None and d1 is not None:
                out.append(d1 - d0)
                per[f"{v}_{'head' if rows == head_rows else 'all'}"] = (round(d0, 2), round(d1, 2))
    if not head or not whole:
        return None
    return {"turn": t, "head": round(float(np.mean(head)), 2), "all": round(float(np.mean(whole)), 2), "views": per}


def colour_says(ev: dict[str, Any] | None) -> int:
    """+1 when the colour evidence clearly says its turn is right, -1 when it clearly says the opposite, else 0."""
    if not ev:
        return 0
    if ev["head"] >= COLOUR_HEAD_MIN and ev["all"] >= COLOUR_ALL_MIN:
        return 1
    if ev["head"] <= -COLOUR_HEAD_MIN and ev["all"] <= -COLOUR_ALL_MIN:
        return -1
    return 0


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


def _best_in(scores: dict[Any, float] | None, centre: float, half: float) -> int | None:
    """The outline test's best turn within `half` degrees of `centre`, or None."""
    near = {int(k) % 360: float(s) for k, s in (scores or {}).items() if angle_diff(int(k), centre) <= half}
    return max(near, key=lambda k: (near[k], -angle_diff(k, centre))) if near else None


def decide(outline_turn: float | None, scores: dict[Any, float] | None, toes_result: dict[str, Any] | None,
           colour: Callable[[float], dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """The turn that brings the model's front forward, from both tests.

    Returns {turn (None when neither test has one), source ("outline", "toes", "as built", "colour" or None),
    outline_turn, outline_margin, toes_turn, toes_found, toes_reason, flat, differ_deg (between the two tests, None when
    either is missing), agree (False only when both tests have an answer and they differ by more than DISAGREE_DEG),
    colour (colour_evidence at the decided turn, when it was asked), sure (False when the front could not be told from
    the back: a near-tie no other evidence settled), why}.

    The outline's turn stands unless its margin over the opposite turn is under MARGIN_MIN or the toes point more than
    DISAGREE_DEG away from it. With toes, the turn is the outline test's best one among the turns within DISAGREE_DEG of
    the toes (the feet say which half, the outline says where in it), or the toes' own direction to the nearest 15
    degrees when the outline has no table. A near-tie with no toes keeps the half the 3D step built as the front (the
    outline's best within AS_BUILT_HALF of 0) unless `colour` (turn -> colour_evidence) clearly says the other half; it
    is sure only when the colour clearly confirms the half it took."""
    t = toes_result or {}
    found = bool(t.get("found")) and t.get("turn") is not None
    out: dict[str, Any] = {"turn": None if outline_turn is None else int(round(outline_turn)) % 360, "source": None if outline_turn is None else "outline",
                           "outline_turn": None if outline_turn is None else int(round(outline_turn)) % 360,
                           "outline_margin": margin(scores, outline_turn), "toes_turn": float(t["turn"]) if found else None,
                           "toes_found": found, "toes_reason": None if found else (t.get("reason") or "not measured"),
                           "flat": bool(t.get("flat")), "differ_deg": None, "agree": True, "colour": None, "sure": True, "why": None}
    if outline_turn is None:
        # With no trusted outline the model is left as built, as before: the toes alone do not turn it.
        return out
    if not found:
        m = out["outline_margin"]
        if m is None or m >= MARGIN_MIN:
            return out
        # a flat model keeps exactly the turn it was built at: its outline, the same both ways round, cannot place it
        built = 0 if out["flat"] else _best_in(scores, 0.0, AS_BUILT_HALF)
        other = None if out["flat"] else _best_in(scores, 180.0, 180.0 - AS_BUILT_HALF)
        if built is None:
            return out
        ev = colour(built) if colour is not None else None
        says = colour_says(ev)
        turn = other if says < 0 and other is not None else built
        if says < 0 and other is not None:
            ev = {**ev, "turn": int(other), "head": -ev["head"], "all": -ev["all"]}
        flat = out["flat"]
        out.update(turn=int(turn), colour=ev, sure=says != 0 and not flat,
                   source=("colour" if says < 0 else ("outline" if turn == out["outline_turn"] else "as built")))
        if flat:
            out["why"] = "the model is flat, so its front cannot be told from its back; it keeps the side the 3D step built as the front"
        elif says > 0:
            out["why"] = "the outline test was nearly a tie; the colour of the front and back pictures confirms the side the 3D step built as the front"
        elif says < 0:
            out["why"] = "the outline test was nearly a tie; the colour of the front and back pictures says the model was built facing backwards"
        else:
            out["why"] = ("the outline test was nearly a tie and nothing else could tell the front from the back; it keeps the "
                          "side the 3D step built as the front")
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
    """decide()'s result as the clean-up step's metrics (both tests' numbers, the colour's, and whether it is sure)."""
    return {"face_best_turn": d["turn"], "face_outline_turn": d["outline_turn"], "face_outline_margin": d["outline_margin"],
            "face_toes_turn": d["toes_turn"], "face_toes_found": d["toes_found"], "face_toes_reason": d["toes_reason"],
            "face_tests_differ_deg": d["differ_deg"], "face_tests_agree": d["agree"], "face_decided_by": d["source"],
            "face_decision_note": d["why"], "face_decision_sure": d.get("sure", True), "face_model_flat": d.get("flat", False),
            "face_colour": d.get("colour")}


def colour_of_files(pictures: dict[str, Path | str | None], renders_dir: Path | str | None) -> Callable[[float], dict[str, Any] | None] | None:
    """colour_evidence over the mesh audit's renders on disk (shade_<yaw>.png in renders_dir), as decide() asks for it;
    None when there is nothing to compare. Never raises."""
    if renders_dir is None:
        return None
    d = Path(renders_dir)
    renders = {y: d / f"shade_{y}.png" for y in range(0, 360, 15) if (d / f"shade_{y}.png").is_file()}
    pics = {k: p for k, p in pictures.items() if p is not None and Path(p).is_file()}
    if not renders or not pics:
        return None

    def ask(turn: float) -> dict[str, Any] | None:
        try:
            return colour_evidence(pics, renders, turn)
        except Exception:  # noqa: BLE001  (no colour evidence: the decision stands on the rest)
            return None

    return ask
