"""Lip points: both mouth corners, the middle of the upper and the lower lip and the inner opening, found on a face
picture, for measuring how far the model's mouth is from the picture's (docs/MOUTH-PLAN.md, phase 0).

facemarks.py asks the ComfyUI MediaPipe nodes, but those return masks only (MediaPipeFaceLandmarker's outputs are
FACE_LANDMARKS and BOUNDING_BOX, which no node turns into numbers; the lips come out as a mask, and the face pass reduces
it to a centroid). So the points come from the `mediapipe` Python package (Apache-2.0, CPU) and its face landmarker
model, kept in the project at runtime/models/face_landmarker.task (or the path in VGAC_FACE_LANDMARKER). Without the
package or the model, find() falls back to the extremes of a lip mask when one is given, and measure code falls back to
valley_row(), the dark crease between the lips on a shaded render.

Points are dicts of pixel coordinates (x right, y down): left and right (the corners, left = smaller x), upper and
lower (the outer lips' middle, top of the upper and bottom of the lower), inner_upper and inner_lower (the lips'
inner edges in the middle), opening_px (their gap, 0 when closed), line_y (the lip line, between the inner edges), eye_mid
and eye_px (the eyes' middle and distance), face_w (the face contour's width) and how.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

# indices of MediaPipe's 478-point face mesh
CORNER_A, CORNER_B = 61, 291
UPPER_OUTER, LOWER_OUTER = 0, 17
UPPER_INNER, LOWER_INNER = 13, 14
EYE_A = (33, 133)  # outer and inner corner of one eye
EYE_B = (362, 263)
FACE_A, FACE_B = 234, 454

MODEL_NAME = "face_landmarker.task"
_landmarker: Any = None
_landmarker_failed = False


def model_path() -> Path:
    env = os.environ.get("VGAC_FACE_LANDMARKER")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runtime" / "models" / MODEL_NAME


# --- pure functions -------------------------------------------------------------------------------------------


def from_mesh_points(P: np.ndarray) -> dict[str, Any]:
    """Lip points from a face mesh's pixel coordinates P (>= 468, 2) (MediaPipe's numbering)."""
    P = np.asarray(P, dtype=np.float64)
    a, b = P[CORNER_A], P[CORNER_B]
    left, right = (a, b) if a[0] <= b[0] else (b, a)
    iu, il = P[UPPER_INNER], P[LOWER_INNER]
    eye_a = P[list(EYE_A)].mean(axis=0)
    eye_b = P[list(EYE_B)].mean(axis=0)
    ea, eb = sorted([eye_a, eye_b], key=lambda p: p[0])
    return {
        "left": _pt(left), "right": _pt(right), "upper": _pt(P[UPPER_OUTER]), "lower": _pt(P[LOWER_OUTER]),
        "inner_upper": _pt(iu), "inner_lower": _pt(il), "opening_px": float(max(0.0, il[1] - iu[1])),
        "line_y": float((iu[1] + il[1]) / 2), "eye_mid": _pt((ea + eb) / 2), "eye_px": float(np.linalg.norm(eb - ea)),
        "face_w": float(np.linalg.norm(P[FACE_B] - P[FACE_A])), "how": "mediapipe",
        "eyes": [_pt(ea), _pt(eb)], "mouth": _pt([(left[0] + right[0]) / 2, (P[UPPER_OUTER][1] + P[LOWER_OUTER][1]) / 2]),
    }


def _pt(p: Any) -> tuple[float, float]:
    return (float(p[0]), float(p[1]))


def from_mask(mask: np.ndarray) -> dict[str, Any] | None:
    """Lip points from a lip mask (bool, H x W): the corners are the mask's left and right extremes, the outer lips the
    top and bottom of its middle column, the opening the gap in that column when the mask is split in two there."""
    m = np.asarray(mask, dtype=bool)
    ys, xs = np.where(m)
    if len(xs) < 20:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    left = (float(x0), float(ys[xs == x0].mean()))
    right = (float(x1), float(ys[xs == x1].mean()))
    cx = int(round((x0 + x1) / 2))
    col = np.where(m[:, max(0, cx - 1): cx + 2].any(axis=1))[0]
    if len(col) == 0:
        return None
    top, bot = float(col.min()), float(col.max())
    gaps = np.where(np.diff(col) > 1)[0]
    if len(gaps):
        k = int(gaps[np.argmax(np.diff(col)[gaps])])
        iu_y, il_y = float(col[k]), float(col[k + 1])
    else:
        iu_y = il_y = (top + bot) / 2
    return {"left": left, "right": right, "upper": (float(cx), top), "lower": (float(cx), bot),
            "inner_upper": (float(cx), iu_y), "inner_lower": (float(cx), il_y), "opening_px": max(0.0, il_y - iu_y - 1),
            "line_y": (iu_y + il_y) / 2, "eye_mid": None, "eye_px": None, "face_w": None, "how": "mask"}


def width_px(lp: dict[str, Any]) -> float:
    return float(abs(lp["right"][0] - lp["left"][0]))


def compare(a: dict[str, Any], b: dict[str, Any], face_width: float, eye_align: bool = False) -> dict[str, float | None]:
    """How far b's lips are from a's, as shares of face_width (a pixel length): corner_dist (mean distance between the
    matching corners), line_dy (b's lip line below a's, signed; negative is higher), mid_dx (b's mouth middle right of
    a's), width_ratio (b's mouth width over a's), open_a and open_b (the inner opening), open_diff (b minus a).
    Only the lip line (line_dy) is known when either is a valley_row result; the rest is None.
    eye_align: first move b so its eyes' middle lands on a's (the measure of picture against paint, where the paint's
    registration is the fit's business and the question is whether the mouth follows the eyes alike)."""
    dx = dy = 0.0
    if eye_align and a.get("eye_mid") and b.get("eye_mid"):
        dx = a["eye_mid"][0] - b["eye_mid"][0]
        dy = a["eye_mid"][1] - b["eye_mid"][1]
    w = float(face_width)
    if "valley" in (a.get("how"), b.get("how")):  # only a lip line was found on one side: nothing else to compare
        return {"corner_dist": None, "line_dy": float((b["line_y"] + dy - a["line_y"]) / w), "mid_dx": None,
                "width_ratio": None, "open_a": None, "open_b": None, "open_diff": None}

    def moved(p: tuple[float, float]) -> np.ndarray:
        return np.array([p[0] + dx, p[1] + dy])

    cl = np.linalg.norm(moved(b["left"]) - np.array(a["left"]))
    cr = np.linalg.norm(moved(b["right"]) - np.array(a["right"]))
    mid_a = (np.array(a["left"]) + np.array(a["right"])) / 2
    mid_b = (moved(b["left"]) + moved(b["right"])) / 2
    return {
        "corner_dist": float((cl + cr) / 2 / w),
        "line_dy": float((b["line_y"] + dy - a["line_y"]) / w),
        "mid_dx": float((mid_b[0] - mid_a[0]) / w),
        "width_ratio": float(width_px(b) / max(width_px(a), 1e-9)),
        "open_a": float(a["opening_px"] / w),
        "open_b": float(b["opening_px"] / w),
        "open_diff": float((b["opening_px"] - a["opening_px"]) / w),
    }


def opening_state(opening_px: float, face_width: float, closed_below: float = 0.008) -> str:
    """'closed' when the inner gap is under closed_below of the face width (about 8 px of 1000), else 'open'."""
    return "closed" if opening_px / face_width < closed_below else "open"


def valley_row(lum: np.ndarray, centre_x: float, nose_y: float, face_w: float) -> float | None:
    """The row of the lip line on a shaded render: the darkest crease (the strongest local minimum of the middle
    columns' mean brightness) between 0.06 and 0.34 face widths under the nose tip. None when the band falls outside
    the picture or has no minimum."""
    H, W = lum.shape
    y0, y1 = int(nose_y + 0.06 * face_w), int(nose_y + 0.34 * face_w)
    x0, x1 = max(0, int(centre_x - 0.15 * face_w)), min(W, int(centre_x + 0.15 * face_w))
    if y0 < 2 or y1 >= H - 2 or x1 - x0 < 4 or y1 - y0 < 6:
        return None
    prof = lum[y0 - 2: y1 + 3, x0:x1].mean(axis=1)
    k = max(1, int(face_w / 120))
    prof = np.convolve(np.pad(prof, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    inner = prof[2:-2]
    d2 = prof[4:] - 2 * prof[2:-2] + prof[:-4]  # large and positive at a valley
    ok = (inner[1:-1] < inner[:-2]) & (inner[1:-1] <= inner[2:])
    score = np.where(ok, d2[1:-1], -np.inf)
    if not np.isfinite(score.max()) or score.max() <= 0:
        return None
    return float(y0 + int(np.argmax(score)) + 1 + 0.5)


# --- MediaPipe ------------------------------------------------------------------------------------------------


def available() -> bool:
    return _get_landmarker() is not None


def _get_landmarker() -> Any:
    global _landmarker, _landmarker_failed
    if _landmarker is not None or _landmarker_failed:
        return _landmarker
    try:
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision

        path = model_path()
        opts = vision.FaceLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_buffer=path.read_bytes()),
            running_mode=vision.RunningMode.IMAGE, num_faces=1,
            min_face_detection_confidence=0.2, min_face_presence_confidence=0.2, min_tracking_confidence=0.2)
        _landmarker = vision.FaceLandmarker.create_from_options(opts)
    except Exception:  # noqa: BLE001  (package or model missing: callers fall back)
        _landmarker_failed = True
        _landmarker = None
    return _landmarker


def find(img: np.ndarray) -> dict[str, Any] | None:
    """Lip points of the one face in img ((H, W, 3) floats 0..1 or uint8), or None (no package, no model, no face)."""
    lm = _get_landmarker()
    if lm is None:
        return None
    import mediapipe as mp

    arr = np.asarray(img)
    if arr.dtype != np.uint8:
        arr = np.clip(arr[..., :3] * 255.0 + 0.5, 0, 255).astype(np.uint8)
    arr = np.ascontiguousarray(arr[..., :3])
    res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=arr))
    if not res.face_landmarks:
        return None
    h, w = arr.shape[:2]
    P = np.array([[p.x * w, p.y * h] for p in res.face_landmarks[0]])
    return from_mesh_points(P)
