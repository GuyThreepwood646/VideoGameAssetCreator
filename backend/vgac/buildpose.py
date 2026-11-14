"""Pose for the 3D build: will the person's picture's pose join limbs or lose fingers in the 3D model, the A-pose
wording the redraw sends, and the guard that refuses a redraw that changed the character. Processor only.

Why: the 3D builder joins parts wherever its picture shows them touching or nearly touching (docs/MESH-RESEARCH.md).
On the 36 stored rigged models an upper arm under 20 degrees from the body was joined past its middle in 23 percent,
over 45 degrees never; thighs with the ankles under 0.20 of the height apart joined to mid-thigh in 57 percent, over
0.27 in 8 percent. A fist or a hidden hand gets no finger bones.

The measure (measure()) reads the person's cut-out:
- legs, from the outline: the gap between the two feet in the row 8 percent of the height above the soles, over the
  height (0.93 correlation with the rig's ankle gap on the 36), and how high above the soles the legs part (a long robe
  or skirt parts them only near the feet);
- arms: the upper arm's angle from the body (shoulder to elbow against shoulders-to-hips) from the MediaPipe pose
  landmarker (runtime/models/pose_landmarker_heavy.task); without it, or when it is unsure, from the outline (the line
  through the arm where it stands clear of the body; no clear rows = the arm touches the body; a T-pose passes);
- hands: the MediaPipe hand landmarker (runtime/models/hand_landmarker.task) near each wrist (or, without the pose,
  over tiles of the upper body): open, fist, or not found (gloves, gauntlets, sleeves, hidden).
Anything it cannot read is "not measured" and said so; it never passes silently.

pose_check() turns the measure into the pose_for_3d check (warn) with its sentence (vars.words), whether the guarded
redraw is offered (vars.offer) and its cautions. pose_prompt() is the redraw's wording. guard() compares a redraw with
the person's picture. Nothing here raises: a failure is recorded as not measured.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# --- lines (validated on the 36 stored rigged models, bakeoff/mesh_research and the notes of 2026-11-13) --------------

# Upper arm against the body, degrees, from the pose landmarks. On DWPose joints 22 to 25 flags all 13 arms under 20
# degrees on the rig and none of the 6 over 45. MediaPipe: see ARM_LINE_SOURCE.
# MediaPipe pose (heavy) on the same 36: correlation 0.939 with the rig (1.5 degrees from DWPose on average); lines 22
# and 23 both flag 13 of 13 and 0 of 6, with Vivi through the outline (pose score 0.02) and TheHeir not measured (no
# person found). The lowest at-risk arm reads 21.7.
ARM_LINE = 23.0
ARM_LINE_SOURCE = "MediaPipe pose heavy and DWPose joints on the 36 stored rigged models (2026-11-13)"
OUTLINE_ARM_LINE = 20.0  # the outline's arm line (12 of 13 flagged, TheHeir's tool arms wrongly)
TPOSE_RATIO = 2.5  # arm span over waist width that counts as arms held straight out (as readiness.arms_away)
ANKLE_WILL = 0.21  # under this: the thighs will probably join (7 of 7 under 0.20 on the rig, no other)
ANKLE_MAY = 0.23  # under this: they may join (the boy, thighs joined 9 and 9, is at 0.223)
ANKLE_ROW = 0.08  # the row the ankle gap is read on, as a share of the height above the soles
ROBE_SPLIT = 0.15  # legs that part only below this share of the height are inside a long robe or skirt
ROBE_FEET = 0.08  # ... when the feet below the hem are this far apart (feet together is legs together, not a robe)
POSE_SCORE_MIN = 0.5  # MediaPipe visibility of shoulders, elbows and hips under this: the outline decides the arms
HAND_SPREAD = 0.22  # neighbouring fingertips' mean gap over the palm length: under it the fingers are together
GREY_CHROMA = 5.0  # chroma of the most colourful tenth of a black-and-white picture is under this

# The guard's lines (8 stored redraw pairs: knight and viking kept, caped hero, mage, TheHeir and three soldiers refused)
GUARD_PART_DE = 10.0  # a body part's middle colour moved more than this (Lab, lightness counted half)
# The hands' own line: small, and drawn open after the redraw (gaps and shading between the fingers); the viking's
# gloved hands moved 10.0, the caped hero's and the mage's gloves 41.1 and 29.6 (caught by the skin rule as well).
GUARD_HAND_DE = 15.0
GUARD_SKIN_BEFORE = 10.0  # hand within this of the face's skin: a bare hand
GUARD_SKIN_AFTER = 15.0  # ... and beyond this after the redraw: gloves added
GUARD_PALETTE = 0.6  # colour histogram overlap under this: the colours changed
GUARD_COLOUR_ADDED = 10.0  # a black-and-white picture whose redraw's colourful tenth is over this was coloured in
GUARD_AREA = 0.92  # figure area over height squared shrank under this share: parts were removed (TheHeir 0.87)
GUARD_FACE_SHAPE = 0.12  # a face proportion (eyes apart, eyes to mouth, mouth width) changed by more than this share

POSE_MODEL = "pose_landmarker_heavy.task"
HAND_MODEL = "hand_landmarker.task"
_models: dict[str, Any] = {}
_failed: set[str] = set()


def model_path(name: str) -> Path:
    env = os.environ.get({"pose": "VGAC_POSE_LANDMARKER", "hand": "VGAC_HAND_LANDMARKER"}[name])
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runtime" / "models" / (POSE_MODEL if name == "pose" else HAND_MODEL)


def _model(name: str) -> Any:
    """The MediaPipe pose or hand landmarker, or None (package or model file missing)."""
    if name in _models or name in _failed:
        return _models.get(name)
    try:
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision

        base = mpt.BaseOptions(model_asset_buffer=model_path(name).read_bytes())
        if name == "pose":
            opts = vision.PoseLandmarkerOptions(base_options=base, running_mode=vision.RunningMode.IMAGE, num_poses=1,
                                                min_pose_detection_confidence=0.2, min_pose_presence_confidence=0.2)
            _models[name] = vision.PoseLandmarker.create_from_options(opts)
        else:
            opts = vision.HandLandmarkerOptions(base_options=base, running_mode=vision.RunningMode.IMAGE, num_hands=2,
                                                min_hand_detection_confidence=0.3, min_hand_presence_confidence=0.3)
            _models[name] = vision.HandLandmarker.create_from_options(opts)
    except Exception:  # noqa: BLE001  (package or model missing: the measure says what it could not read)
        _failed.add(name)
    return _models.get(name)


def available(name: str) -> bool:
    return _model(name) is not None


# --- the picture ----------------------------------------------------------------------------------------------------


def load(cutout: Path | str | Image.Image | np.ndarray) -> dict[str, Any]:
    """The figure: rgb (uint8, on white, cropped to the figure with a margin), mask, and the figure's height in pixels."""
    if isinstance(cutout, np.ndarray):
        a = cutout
    else:
        im = cutout if isinstance(cutout, Image.Image) else Image.open(cutout)
        a = np.asarray(im.convert("RGBA"))
    if a.shape[2] == 3:
        a = np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
    m = a[..., 3] > 127
    ys, xs = np.nonzero(m)
    if not len(ys):
        raise ValueError("the cut-out is empty")
    h = int(ys.max() - ys.min() + 1)
    pad = int(0.08 * h)
    y0, y1 = max(0, ys.min() - pad), min(a.shape[0], ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(a.shape[1], xs.max() + 1 + pad)
    a = a[y0:y1, x0:x1]
    al = a[..., 3:4].astype(np.float64) / 255.0
    rgb = (a[..., :3] * al + 255.0 * (1 - al)).round().astype(np.uint8)
    return {"rgb": np.ascontiguousarray(rgb), "mask": a[..., 3] > 127, "height": h}


def _tight(mask: np.ndarray, rows: int = 700) -> np.ndarray:
    """The mask cropped to the figure and scaled to `rows` tall (nearest), for the outline measures."""
    ys, xs = np.nonzero(mask)
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    s = rows / m.shape[0]
    w = max(1, int(round(m.shape[1] * s)))
    yi = np.minimum((np.arange(rows) / s).astype(int), m.shape[0] - 1)
    xi = np.minimum((np.arange(w) / s).astype(int), m.shape[1] - 1)
    return m[yi][:, xi]


def _runs(row: np.ndarray, min_len: int = 3) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b - a >= min_len]


def legs_outline(mask: np.ndarray) -> dict[str, float]:
    """gap: the two feet's centres apart in the row ANKLE_ROW above the soles, over the height (0 when one run: feet
    together or one piece); split: how high above the soles the legs are still two runs, over the height."""
    m = _tight(mask)
    h = m.shape[0]
    row = _runs(m[int(h * (1 - ANKLE_ROW))])
    gap = 0.0
    if len(row) >= 2:
        two = sorted(sorted(row, key=lambda r: r[1] - r[0])[-2:])
        gap = ((two[1][0] + two[1][1]) / 2 - (two[0][0] + two[0][1]) / 2) / h
    top = None
    for y in range(h - 1, int(0.45 * h), -1):
        if len(_runs(m[y], 4)) >= 2:
            top = y
        elif top is not None and y < top - 6:
            break
    split = 0.0 if top is None else (h - top) / h
    return {"gap": round(float(gap), 3), "split": round(float(split), 3)}


def arms_outline(mask: np.ndarray) -> dict[str, Any]:
    """The arms from the outline, per side of the picture ("img_left", "img_right"): the angle (degrees from straight
    down) of the line through the arm where it stands clear of the body over 18 to 55 percent of the height, or None
    when it never stands clear (touching the body); and tpose, arms straight out (span over TPOSE_RATIO x the waist)."""
    m = _tight(mask)
    h = m.shape[0]
    widths = m.sum(axis=1)
    shoulders = widths[int(h * 0.15):int(h * 0.35)]
    waist = widths[int(h * 0.40):int(h * 0.55)]
    span = float(shoulders.max()) / max(1.0, float(np.median(waist))) if shoulders.size and waist.size else 0.0
    out: dict[str, Any] = {"span_ratio": round(span, 2), "tpose": span >= TPOSE_RATIO}
    for side in ("img_left", "img_right"):
        pts = []
        for y in range(int(0.18 * h), int(0.55 * h)):
            r = _runs(m[y])
            if len(r) < 2:
                continue
            body = max(r, key=lambda x: x[1] - x[0])
            outer = [x for x in r if (x[1] <= body[0] if side == "img_left" else x[0] >= body[1])]
            if outer:
                o = outer[0] if side == "img_left" else outer[-1]
                if o[1] - o[0] < 0.12 * h:
                    pts.append((y, (o[0] + o[1]) / 2))
        if len(pts) < 0.04 * h:
            out[side] = None
            continue
        ys_, xs_ = np.array(pts, dtype=np.float64).T
        out[side] = round(math.degrees(math.atan(abs(np.polyfit(ys_, xs_, 1)[0]))), 1)
    return out


def chroma(rgb: np.ndarray, mask: np.ndarray, q: float = 50) -> float:
    """The q-th percentile of the figure's chroma (Lab): 50 the middle colour's strength; 90 the most colourful tenth,
    which is near 0 only in a black-and-white picture (a person in grey clothes still has skin)."""
    from . import face

    lab = face.to_lab(rgb.astype(np.float64) / 255.0)[mask]
    return float(np.percentile(np.hypot(lab[:, 1], lab[:, 2]), q)) if len(lab) else 0.0


# --- landmarks ------------------------------------------------------------------------------------------------------

# MediaPipe pose landmark numbers (the person's own left and right)
L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 11, 12, 13, 14, 15, 16
L_HIP, R_HIP, L_KN, R_KN, L_AN, R_AN = 23, 24, 25, 26, 27, 28
NOSE, L_EYE, R_EYE = 0, 2, 5
HAND_TIPS = {"left": (L_WR, 17, 19), "right": (R_WR, 18, 20)}  # wrist, pinky, index


def pose_points(rgb: np.ndarray) -> np.ndarray | None:
    """(33, 3) pixel x, y and visibility of the one person, or None (no model, no person)."""
    lm = _model("pose")
    if lm is None:
        return None
    import mediapipe as mp

    res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
    if not res.pose_landmarks:
        return None
    h, w = rgb.shape[:2]
    return np.array([[p.x * w, p.y * h, p.visibility] for p in res.pose_landmarks[0]], dtype=np.float64)


def pose_score(P: np.ndarray) -> float:
    return float(P[[L_SH, R_SH, L_EL, R_EL, L_HIP, R_HIP], 2].min())


def _angle(v: np.ndarray, ref: np.ndarray) -> float:
    c = float(np.dot(v, ref) / (np.linalg.norm(v) * np.linalg.norm(ref) + 1e-9))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def arm_angles(P: np.ndarray) -> dict[str, float]:
    """Upper arm (shoulder to elbow) against the body's line (shoulders' middle to hips' middle), degrees, per side."""
    down = (P[L_HIP, :2] + P[R_HIP, :2]) / 2 - (P[L_SH, :2] + P[R_SH, :2]) / 2
    return {"left": round(_angle(P[L_EL, :2] - P[L_SH, :2], down), 1),
            "right": round(_angle(P[R_EL, :2] - P[R_SH, :2], down), 1)}


def hand_state(points: np.ndarray) -> dict[str, Any]:
    """21 hand points (MediaPipe numbering): fingers straight (the tip past the middle joint, the finger nearly
    straight there), and how far apart the fingertips are over the palm length. open: four fingers straight; fist: at
    most one; part: the rest. open with tips under HAND_SPREAD apart is open_together."""
    P = np.asarray(points, dtype=np.float64)[:, :2]
    palm = float(np.linalg.norm(P[9] - P[0])) + 1e-9
    straight = 0
    for mcp, pip, tip in ((5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20)):
        bent = 180.0 - _angle(P[mcp] - P[pip], P[tip] - P[pip])
        far = np.linalg.norm(P[tip] - P[0]) > 1.1 * np.linalg.norm(P[pip] - P[0])
        straight += int(bent < 50 and far)
    tips = (8, 12, 16, 20)
    spread = float(np.mean([np.linalg.norm(P[a] - P[b]) for a, b in zip(tips, tips[1:])])) / palm
    if straight >= 4:
        state = "open" if spread >= HAND_SPREAD else "open_together"
    elif straight <= 1:
        state = "fist"
    else:
        state = "part"
    return {"state": state, "straight": straight, "spread": round(spread, 3)}


def _detect_hands(rgb: np.ndarray, box: tuple[int, int, int, int]) -> list[dict[str, Any]]:
    """Hands found in rgb[box] scaled to 384 or 512, as {points (21, 2) in rgb's pixels, score}."""
    lm = _model("hand")
    if lm is None:
        return []
    import mediapipe as mp

    x0, y0, x1, y1 = box
    hh, ww = rgb.shape[:2]
    side = max(x1 - x0, y1 - y0)
    canvas = np.full((side, side, 3), 255, np.uint8)
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(ww, x1), min(hh, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return []
    canvas[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = rgb[cy0:cy1, cx0:cx1]
    size = 384 if side < 384 else 512
    from PIL import Image as _I

    img = np.asarray(_I.fromarray(canvas).resize((size, size), _I.Resampling.BICUBIC))
    res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img)))
    out = []
    for i, hl in enumerate(res.hand_landmarks):
        pts = np.array([[p.x * side + x0, p.y * side + y0] for p in hl])
        score = res.handedness[i][0].score if res.handedness else 0.0
        out.append({"points": pts, "score": float(score)})
    return out


def hands_at(rgb: np.ndarray, P: np.ndarray, height: int) -> dict[str, dict[str, Any]]:
    """Each hand searched in a square around the pose's hand (wrist, index and pinky), the one nearest its wrist kept."""
    out: dict[str, dict[str, Any]] = {}
    for side, (wr, pk, ix) in HAND_TIPS.items():
        el = L_EL if side == "left" else R_EL
        c = (P[wr, :2] + (P[pk, :2] + P[ix, :2]) / 2) / 2
        half = int(max(0.6 * np.linalg.norm(P[wr, :2] - P[el, :2]), 0.06 * height))
        found = _detect_hands(rgb, (int(c[0]) - half, int(c[1]) - half, int(c[0]) + half, int(c[1]) + half))
        found = [f for f in found if np.linalg.norm(f["points"][0] - P[wr, :2]) < 1.5 * half]
        if not found:
            out[side] = {"state": "not_found", "wrist_seen": round(float(P[wr, 2]), 2)}
            continue
        best = min(found, key=lambda f: np.linalg.norm(f["points"][0] - P[wr, :2]))
        out[side] = {**hand_state(best["points"]), "score": round(best["score"], 2), "points": best["points"]}
    return out


def hands_tiles(rgb: np.ndarray, mask: np.ndarray) -> dict[str, dict[str, Any]]:
    """No pose: hands searched over the whole figure and 12 overlapping tiles; hands below 0.72 of the height (feet)
    dropped; the two best, by the side of the figure they are on (picture left = the person's right, facing us)."""
    ys, xs = np.nonzero(mask)
    top, h = int(ys.min()), int(ys.max() - ys.min() + 1)
    H, W = rgb.shape[:2]
    tiles = [(0, 0, W, H)]
    t = H // 2
    for ty in np.linspace(0, H - t, 4).astype(int):
        for tx in np.linspace(0, max(0, W - t), 3).astype(int):
            tiles.append((int(tx), int(ty), int(tx) + t, int(ty) + t))
    found: list[dict[str, Any]] = []
    for box in tiles:
        for f in _detect_hands(rgb, box):
            palm = float(np.linalg.norm(f["points"][9] - f["points"][0]))
            if palm < 0.01 * h or (f["points"][0][1] - top) / h > 0.72:
                continue
            same = [g for g in found if np.linalg.norm(g["points"][0] - f["points"][0]) < 0.6 * palm]
            if same and same[0]["score"] >= f["score"]:
                continue
            found = [g for g in found if not any(g is s for s in same)] + [f]
    found = sorted(found, key=lambda f: -f["score"])[:2]
    mid = float(xs.mean())
    out: dict[str, dict[str, Any]] = {"left": {"state": "not_found"}, "right": {"state": "not_found"}}
    for f in found:
        side = "right" if f["points"][0][0] < mid else "left"
        out[side] = {**hand_state(f["points"]), "score": round(f["score"], 2), "points": f["points"]}
    return out


# --- the measure ----------------------------------------------------------------------------------------------------


def measure(cutout: Path | str | Image.Image | np.ndarray) -> dict[str, Any]:
    """Arms, legs and hands of the person's cut-out, each with a state, plus what could not be read and why. Never
    raises: an unreadable cut-out gives {"error"}."""
    try:
        fig = load(cutout)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}
    rgb, mask, height = fig["rgb"], fig["mask"], fig["height"]
    out: dict[str, Any] = {"not_read": [], "finder": {"pose": available("pose"), "hand": available("hand")}}
    # legs: the outline
    legs = legs_outline(mask)
    if legs["split"] < ROBE_SPLIT and legs["gap"] >= ROBE_FEET:
        legs["state"] = "clothing"
    elif legs["gap"] < ANKLE_WILL:
        legs["state"] = "will_join"
    elif legs["gap"] < ANKLE_MAY:
        legs["state"] = "may_join"
    else:
        legs["state"] = "free"
    out["legs"] = legs
    # arms: the pose landmarks, else the outline
    P = None
    try:
        P = pose_points(rgb)
    except Exception as e:  # noqa: BLE001
        out["pose_error"] = str(e)[:200]
    score = pose_score(P) if P is not None else None
    out["pose_score"] = None if score is None else round(score, 2)
    if P is not None and score is not None and score >= POSE_SCORE_MIN:
        ang = arm_angles(P)
        out["arms"] = {s: {"angle": a, "how": "joints", "state": "join" if a < ARM_LINE else "free"} for s, a in ang.items()}
        out["points"] = P[:, :2].round(1).tolist()
    else:
        if not available("pose"):
            out["not_read"].append("pose_model")
        elif P is None:
            out["not_read"].append("pose_not_found")
        else:
            out["not_read"].append("pose_unsure")
        ol = arms_outline(mask)
        out["arms_outline"] = ol
        arms = {}
        # The pose finder is installed and found no person at all: a robot, a creature or a costume that hides the
        # body (TheHeir: its tool arms read as arms close to the body on the outline). The arms are not measured and
        # no pose redraw is offered (pose_check), since it would remove designed arms, tools or claws.
        no_person = "pose_not_found" in out["not_read"]
        for img_side, side in (("img_left", "right"), ("img_right", "left")):  # facing us: picture left = their right
            a = ol.get(img_side)
            if no_person:
                arms[side] = {"angle": None, "how": "outline", "state": "not_measured"}
            elif ol["tpose"]:
                arms[side] = {"angle": None, "how": "outline", "state": "free"}
            elif a is None:
                arms[side] = {"angle": None, "how": "outline", "state": "touching"}
            else:
                arms[side] = {"angle": a, "how": "outline", "state": "join" if a < OUTLINE_ARM_LINE else "free"}
        out["arms"] = arms
    # hands
    try:
        if not available("hand"):
            out["hands"] = {s: {"state": "not_measured"} for s in ("left", "right")}
            out["not_read"].append("hand_model")
        elif P is not None and score is not None and score >= POSE_SCORE_MIN:
            out["hands"] = hands_at(rgb, P, height)
        else:
            out["hands"] = hands_tiles(rgb, mask)
    except Exception as e:  # noqa: BLE001
        out["hands"] = {s: {"state": "not_measured"} for s in ("left", "right")}
        out["hand_error"] = str(e)[:200]
    # colours the wording and the guard read: black and white; bare or gloved hands against the face's skin
    out["chroma"] = round(chroma(rgb, mask, 90), 1)
    out["grey"] = out["chroma"] < GREY_CHROMA
    if P is not None and score is not None and score >= POSE_SCORE_MIN:
        skin = _face_colour(rgb, mask, P)
        for side in ("left", "right"):
            hc = _hand_colour(rgb, mask, P, side, out["hands"][side].get("points"))
            out["hands"][side]["skin_gap"] = None if skin is None or hc is None else _de(skin, hc)
    for s in ("left", "right"):
        if isinstance(out["hands"][s].get("points"), np.ndarray):
            out["hands"][s]["points"] = out["hands"][s]["points"].round(1).tolist()
    return out


# --- colours of body parts (the wording's bare-hands sentence and the guard) ------------------------------------------


def _lab(rgb: np.ndarray) -> np.ndarray:
    from . import face

    return face.to_lab(rgb.astype(np.float64) / 255.0)


def _de(a: np.ndarray, b: np.ndarray) -> float:
    return round(float(np.linalg.norm((np.asarray(a) - np.asarray(b)) * np.array([0.5, 1.0, 1.0]))), 1)


def _median(rgb: np.ndarray, region: np.ndarray) -> np.ndarray | None:
    if region.sum() < 30:
        return None
    return np.median(_lab(rgb)[region], axis=0)


def _disc(shape: tuple[int, ...], c: np.ndarray, r: float) -> np.ndarray:
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    return (xx - c[0]) ** 2 + (yy - c[1]) ** 2 <= r * r


def _segment(shape: tuple[int, ...], a: np.ndarray, b: np.ndarray, r: float) -> np.ndarray:
    """Pixels within r of the middle 60 percent of the segment a-b."""
    a2, b2 = a + 0.2 * (b - a), b - 0.2 * (b - a)
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    d = b2 - a2
    t = np.clip(((xx - a2[0]) * d[0] + (yy - a2[1]) * d[1]) / max(1e-9, float(d @ d)), 0, 1)
    return (xx - (a2[0] + t * d[0])) ** 2 + (yy - (a2[1] + t * d[1])) ** 2 <= r * r


def _face_colour(rgb: np.ndarray, mask: np.ndarray, P: np.ndarray) -> np.ndarray | None:
    eyes = float(np.linalg.norm(P[L_EYE, :2] - P[R_EYE, :2]))
    c = (P[NOSE, :2] * 2 + (P[L_EYE, :2] + P[R_EYE, :2]) / 2) / 3
    return _median(rgb, _disc(mask.shape, c, max(2.0, 0.6 * eyes)) & mask)


def _hand_colour(rgb: np.ndarray, mask: np.ndarray, P: np.ndarray, side: str, points: Any = None) -> np.ndarray | None:
    if points is not None:
        pts = np.asarray(points)
        c, r = pts.mean(axis=0), 0.5 * float(np.linalg.norm(pts[9] - pts[0]))
    else:
        wr, pk, ix = HAND_TIPS[side]
        c = (P[wr, :2] + (P[pk, :2] + P[ix, :2]) / 2) / 2
        r = 0.6 * float(np.linalg.norm(P[ix, :2] - P[wr, :2]))
    return _median(rgb, _disc(mask.shape, c, max(2.0, r)) & mask)


PARTS = {"torso": ((L_SH, R_HIP), (R_SH, L_HIP)), "upper arms": ((L_SH, L_EL), (R_SH, R_EL)),
         "forearms": ((L_EL, L_WR), (R_EL, R_WR)), "thighs": ((L_HIP, L_KN), (R_HIP, R_KN)),
         "shins": ((L_KN, L_AN), (R_KN, R_AN))}


def part_colours(rgb: np.ndarray, mask: np.ndarray, P: np.ndarray, height: int) -> dict[str, Any]:
    """Middle Lab colour of each body part (face skin, torso, upper arms, forearms, hands, thighs, shins) placed by the
    pose landmarks."""
    r = max(2.0, 0.0125 * height)
    out: dict[str, Any] = {"face": _face_colour(rgb, mask, P)}
    for name, segs in PARTS.items():
        region = np.zeros(mask.shape, bool)
        for a, b in segs:
            region |= _segment(mask.shape, P[a, :2], P[b, :2], r)
        out[name] = _median(rgb, region & mask)
    hands = [_hand_colour(rgb, mask, P, s) for s in ("left", "right")]
    hands = [h for h in hands if h is not None]
    out["hands"] = np.mean(hands, axis=0) if hands else None
    return out


# --- the check ------------------------------------------------------------------------------------------------------

SIDE_WORD = {"left": "left", "right": "right"}


def _both(sides: list[str], one: str, both: str) -> str:
    return both if len(sides) == 2 else one.format(side=SIDE_WORD[sides[0]])


def pose_check(m: dict[str, Any], held_item: str | None = None, after_redraw: bool = False) -> dict[str, Any]:
    """The pose_for_3d check (warn) from measure(): vars.words is what the person reads, vars.offer whether the
    guarded redraw is offered, vars.cautions the sentences shown with it. after_redraw: measured on the pose redraw
    (step 2), so the words say so and no second redraw is offered."""
    from . import quality

    if m.get("error"):
        return quality.not_measured("pose_for_3d", "the pose for the 3D model", "the cut-out could not be read",
                                    vars={"error": m["error"]})
    arms, legs, hands = m["arms"], m["legs"], m["hands"]
    bad_arms = [s for s in ("left", "right") if arms[s]["state"] in ("join", "touching")]
    fists = [s for s in ("left", "right") if hands[s]["state"] == "fist"]
    unseen = [s for s in ("left", "right") if hands[s]["state"] == "not_found"]
    legs_bad = legs["state"] in ("will_join", "may_join")
    ok = not bad_arms and not fists and not legs_bad
    lines: list[str] = []
    if bad_arms:
        lines.append(_both(bad_arms, "Your character's {side} arm hangs close to the body", "Both arms hang close to the body")
                     + ". The 3D builder joins parts that touch or nearly touch in the picture, so "
                     + ("it" if len(bad_arms) == 1 else "they")
                     + " will probably be grown into the side from the armpit to past the middle of the arm, and stretch "
                     "like a web when the arm lifts in a game.")
    if legs["state"] == "will_join":
        lines.append("The feet are close together, so the 3D model will probably join the thighs to each other down to "
                     "the middle of the thigh; in a walk each leg drags the other.")
    elif legs["state"] == "may_join":
        lines.append("The feet are fairly close together, so the 3D model may join the thighs to each other; in a walk "
                     "each leg would drag the other.")
    elif legs["state"] == "clothing":
        lines.append("The legs are inside a long robe or skirt, which the 3D model makes as one piece, as clothing; a new "
                     "pose does not change that.")
    if fists:
        lines.append(_both(fists, "The {side} hand is a fist", "Both hands are fists")
                     + ", so the 3D model gets no separate fingers there and the skeleton gets no finger bones; "
                     + ("it" if len(fists) == 1 else "they") + " cannot grip or point in a game.")
    if unseen:
        lines.append(_both(unseen, "The {side} hand could not be found", "The hands could not be found")
                     + " (gloves, gauntlets, sleeves or hidden), so the fingers were not checked; hidden or gloved hands "
                     "usually get no finger bones.")
    if ok and not lines:
        lines.append("The arms stand well away from the body, the feet are apart and the hands are open: the 3D model "
                     "should get free arms, free legs and separate fingers.")
    elif ok:
        lines.insert(0, "The arms stand away from the body and the feet are apart.")
    no_person = "pose_not_found" in m["not_read"]
    if no_person:
        lines.append("No person's shape was found in the picture (a robot, a creature, or a costume that hides the body), so "
                     "the arms were not checked and no pose redraw is offered: it would remove extra arms, tools or claws "
                     "that are part of the design.")
    notes = []
    if "pose_model" in m["not_read"] or "pose_unsure" in m["not_read"]:
        notes.append("the arms were read from the outline, not from the joints"
                     + (" (the person's joints could not be read with confidence)" if "pose_unsure" in m["not_read"] else ""))
    if "hand_model" in m["not_read"]:
        notes.append("the hands were not checked (the hand finder is not installed)")
    if notes:
        lines.append("Not measured: " + "; ".join(notes) + ".")
    cautions = []
    if held_item:
        cautions.append(f"The redraw also takes the {held_item} out of the hands; add it back afterwards with Add an item.")
    if unseen:
        cautions.append("Gloves, gauntlets and claws are kept but opened; check them in the redrawn picture.")
    if m.get("grey"):
        cautions.append("Your picture is black and white; the redraw is asked to keep it so, and is not used if it adds colour.")
    fixable = bool(bad_arms or fists or legs_bad) and not after_redraw and not no_person
    if after_redraw:
        lines.insert(0, "After the pose redraw:")
    angles = [a["angle"] for a in arms.values() if a.get("angle") is not None]
    value = round(min(angles), 1) if angles else ("touching" if bad_arms else None)
    vars_ = {"words": " ".join(lines), "offer": fixable, "cautions": cautions,
             "arms": {s: {k: v for k, v in a.items()} for s, a in arms.items()},
             "legs": legs, "hands": {s: {k: v for k, v in h.items() if k != "points"} for s, h in hands.items()},
             "how": "joints" if all(a["how"] == "joints" for a in arms.values()) else "outline",
             "not_read": list(m["not_read"]), "grey": bool(m.get("grey"))}
    return quality.check("pose_for_3d", ok, value, ARM_LINE, vars=vars_)


# --- the redraw's wording -------------------------------------------------------------------------------------------

# The A pose itself; the step 2 redraw (POSE_READY) and the concept step's "Redraw it ready for 3D"
# (readiness.HUMANOID_REDRAW) both use it.
A_POSE = ("both arms straight and held out from the body at about 45 degrees, halfway between hanging down and straight "
          "out to the sides, with empty space between each arm and the body from the armpit to the hand; the legs apart "
          "with the feet about a quarter of the body's height apart and empty space between the legs from the crotch "
          "down; standing upright with both feet flat on the ground. Each hand open with the back of the hand toward the "
          "camera, all five fingers straight and spread apart with clear gaps between them, thumb out to the side; "
          "nothing held in either hand; no hand in a pocket, behind the body or hidden by a sleeve.")
POSE_READY = "Redraw the character ready for 3D in a wide A pose: " + A_POSE
POSE_SUFFIX = (" Change only the pose. Keep everything else exactly the same: the same character, face, hair, skin colour, "
               "every garment and its colours and materials, everything worn on the arms, hands and legs, the body "
               "proportions and the art style, camera angle, size and framing, the whole body in frame, on a plain white "
               "background.")
BARE_HANDS = " Both hands are bare skin, as in the picture."
GLOVED_HANDS = " Keep the same gloves or gauntlets, opened."
KEEP_GREY = " Keep the picture black and white."


def pose_prompt(m: dict[str, Any] | None = None, neutral_mouth_sentence: str = "") -> str:
    """The redraw's instruction: the A pose, the sentences the measure supports (bare or gloved hands, black and
    white), the mouth sentence when asked, and the keep-everything suffix."""
    extra = ""
    if m and not m.get("error"):
        gaps = [h.get("skin_gap") for h in (m.get("hands") or {}).values() if h.get("skin_gap") is not None]
        states = [h.get("state") for h in (m.get("hands") or {}).values()]
        if gaps and max(gaps) < GUARD_SKIN_BEFORE:
            extra += BARE_HANDS
        elif (gaps and min(gaps) > GUARD_SKIN_AFTER) or (states and all(s == "not_found" for s in states)):
            extra += GLOVED_HANDS
        if m.get("grey"):
            extra += KEEP_GREY
    return POSE_READY + extra + neutral_mouth_sentence + POSE_SUFFIX


# --- the guard ------------------------------------------------------------------------------------------------------


def _hist(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    lab = _lab(rgb)[mask]
    h, _ = np.histogramdd(lab, bins=6, range=[(0, 100), (-60, 60), (-60, 60)])
    return h.ravel() / max(1.0, h.sum())


def _face_shape(rgb: np.ndarray, P: np.ndarray | None = None) -> dict[str, float] | None:
    """Eyes apart over the face's width, eyes to mouth over eyes apart, mouth width over eyes apart (the face
    landmarker on the head, cropped by the pose's nose and shoulders and enlarged when the pose is known), or None."""
    from . import lipmarks

    img = rgb
    if P is not None:
        side = max(16, int(0.9 * np.linalg.norm(P[L_SH, :2] - P[R_SH, :2])))
        cx, cy = int(P[NOSE, 0]), int(P[NOSE, 1])
        canvas = np.full((2 * side, 2 * side, 3), 255, np.uint8)
        x0, y0 = cx - side, cy - side
        sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(rgb.shape[1], x0 + 2 * side), min(rgb.shape[0], y0 + 2 * side)
        if sx1 > sx0 and sy1 > sy0:
            canvas[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = rgb[sy0:sy1, sx0:sx1]
            img = np.asarray(Image.fromarray(canvas).resize((512, 512), Image.Resampling.BICUBIC))
    lp = lipmarks.find(img)
    if not lp or not lp.get("eye_px"):
        return None
    eye = float(lp["eye_px"])
    mouth_c = np.array([(lp["left"][0] + lp["right"][0]) / 2, (lp["left"][1] + lp["right"][1]) / 2])
    return {"eyes_face": eye / max(1e-6, float(lp["face_w"])),
            "eyes_mouth": float(np.linalg.norm(mouth_c - np.array(lp["eye_mid"]))) / eye,
            "mouth": float(np.linalg.norm(np.array(lp["right"]) - np.array(lp["left"]))) / eye}


def guard(original: Path | str | Image.Image | np.ndarray, redraw: Path | str | Image.Image | np.ndarray) -> dict[str, Any]:
    """Did the pose redraw keep the character? Compares the person's cut-out with the redraw's: colour added to a
    black-and-white picture, the colour mix, parts removed (figure area), each body part's colour, gloves added to bare
    hands, and the face's proportions. ok False with reasons (plain phrases) when any fails; not_compared names what
    could not be compared. Never raises."""
    out: dict[str, Any] = {"ok": True, "reasons": [], "not_compared": [], "measures": {}}
    try:
        a, b = load(original), load(redraw)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reasons": ["the pictures could not be read"], "not_compared": [], "measures": {"error": str(e)[:200]}}
    me = out["measures"]
    ca, cb = chroma(a["rgb"], a["mask"], 90), chroma(b["rgb"], b["mask"], 90)
    me["chroma"] = [round(ca, 1), round(cb, 1)]
    if ca < GREY_CHROMA and cb > GUARD_COLOUR_ADDED:
        out["reasons"].append("colour was added to a black-and-white picture")
    overlap = float(np.minimum(_hist(a["rgb"], a["mask"]), _hist(b["rgb"], b["mask"])).sum())
    me["palette_overlap"] = round(overlap, 3)
    if overlap < GUARD_PALETTE and not out["reasons"]:
        out["reasons"].append("the colours changed")
    area_a = a["mask"].sum() / float(a["height"]) ** 2
    area_b = b["mask"].sum() / float(b["height"]) ** 2
    me["area_ratio"] = round(float(area_b / max(1e-9, area_a)), 3)
    if me["area_ratio"] < GUARD_AREA:
        out["reasons"].append("parts of the character were removed")
    Pa = Pb = None
    try:
        Pa, Pb = pose_points(a["rgb"]), pose_points(b["rgb"])
    except Exception:  # noqa: BLE001
        pass
    if Pa is None or Pb is None or pose_score(Pa) < POSE_SCORE_MIN or pose_score(Pb) < POSE_SCORE_MIN:
        out["not_compared"].append("the colour of each garment, the skin and the hands (no person's joints found)")
    else:
        pa, pb = part_colours(a["rgb"], a["mask"], Pa, a["height"]), part_colours(b["rgb"], b["mask"], Pb, b["height"])
        parts = {}
        for name in ("face", "torso", "upper arms", "forearms", "hands", "thighs", "shins"):
            if pa.get(name) is None or pb.get(name) is None:
                out["not_compared"].append(f"the {name}" if name != "face" else "the skin of the face")
                continue
            parts[name] = _de(pa[name], pb[name])
        me["parts"] = parts
        changed = [n for n, d in parts.items() if d > (GUARD_HAND_DE if n == "hands" else GUARD_PART_DE)]
        if pa.get("hands") is not None and pa.get("face") is not None and pb.get("hands") is not None and pb.get("face") is not None:
            before, after = _de(pa["hands"], pa["face"]), _de(pb["hands"], pb["face"])
            me["hand_skin"] = [before, after]
            if before < GUARD_SKIN_BEFORE and after > GUARD_SKIN_AFTER:
                out["reasons"].append("gloves were added to bare hands")
                changed = [n for n in changed if n != "hands"]
        if changed:
            out["reasons"].append(("the colour of the " if len(changed) == 1 else "the colours of the ")
                                  + _list(["skin of the face" if n == "face" else n for n in changed]) + " changed")
    try:
        fa, fb = _face_shape(a["rgb"], Pa), _face_shape(b["rgb"], Pb)
    except Exception:  # noqa: BLE001
        fa = fb = None
    if fa is None or fb is None:
        out["not_compared"].append("the face's shape (no face found)")
    else:
        moved = {k: round(abs(fb[k] - fa[k]) / max(1e-6, fa[k]), 3) for k in fa}
        me["face_shape"] = moved
        if max(moved.values()) > GUARD_FACE_SHAPE:
            out["reasons"].append("the face changed")
    out["ok"] = not out["reasons"]
    return out


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def guard_words(g: dict[str, Any], kept_anyway: bool = False, retried: bool = False) -> str:
    """The sentence the pose_guard check shows."""
    nc = ("" if not g.get("not_compared") else
          " Not compared: " + _list(g["not_compared"]) + "; compare them yourself.")
    if kept_anyway:
        return ("You chose to keep the pose redraw although it changed your character (" + _list(g.get("reasons") or ["unknown"])
                + "). The sides were drawn from it." + nc)
    if g.get("ok"):
        return "The pose redraw kept your character: the same colours, parts and face." + nc
    return ("The pose redraw changed your character (" + _list(g["reasons"]) + ")"
            + (", also on a second try" if retried else "") + ", so it was not used. The sides were drawn from your own "
            "picture. You can look at the redraw and keep it anyway." + nc)
