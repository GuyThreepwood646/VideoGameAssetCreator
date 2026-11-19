"""Finding the people in a feature photo (the New asset form), for a suggested box round the main person.

  POST /api/people   {image}  ->  {status, picture: {width, height}, people: [{box, score}], main, suggested}

status is "ok" (people found), "none" (no person found), "unavailable" (the pose model or MediaPipe is not on this
machine) or "failed" (the detector raised). Only "ok" gives boxes; the form then keeps the whole photo when the person
does not want a box, and never blocks on any status.

The detector is the MediaPipe pose landmarker the pose check already uses (buildpose.py, pose_landmarker_heavy.task),
run with room for several people. No new model or package.

Boxes in pixels of the picture: {x, y, w, h}. A person's box is the extent of the landmarks they show, plus a margin.
The main person is the largest and most central of those found (main_person).
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
from fastapi import APIRouter
from PIL import Image
from pydantic import BaseModel

from . import buildpose
from .own_images import decode_or_400, image_field

router = APIRouter()

MAX_PEOPLE = 4  # how many people the detector is allowed to find in one picture
VIS_MIN = 0.5  # a landmark counts as shown when this sure, and when it lies inside the picture (a point outside is not on the photo)
MIN_LANDMARKS = 4  # fewer shown points than this: too little of the person to box
MIN_SIDE = 64  # the smallest side of a box, in picture pixels (or the whole picture side, when smaller)
MARGIN_X = 0.10  # room round the person, as a share of the person's width
MARGIN_Y = 0.06  # and of the person's height

_lock = threading.Lock()
_detector: Any = None
_failed = False


def _pose_detector() -> Any:
    """The multi-person pose landmarker, or None when the package or the model file is missing."""
    global _detector, _failed
    with _lock:
        if _detector is not None or _failed:
            return _detector
        try:
            from mediapipe.tasks import python as mpt
            from mediapipe.tasks.python import vision

            base = mpt.BaseOptions(model_asset_buffer=buildpose.model_path("pose").read_bytes())
            opts = vision.PoseLandmarkerOptions(base_options=base, running_mode=vision.RunningMode.IMAGE, num_poses=MAX_PEOPLE,
                                                min_pose_detection_confidence=0.3, min_pose_presence_confidence=0.3)
            _detector = vision.PoseLandmarker.create_from_options(opts)
        except Exception:  # noqa: BLE001  (package or model missing: the form keeps the whole photo)
            _failed = True
        return _detector


def find(img: Image.Image) -> dict[str, Any]:
    """The people in the picture as normalised boxes [x0, y0, x1, y1] (0 to 1) with a score: {status, people}."""
    det = _pose_detector()
    if det is None:
        return {"status": "unavailable", "people": []}
    try:
        import mediapipe as mp

        rgb = np.array(img.convert("RGB"))
        with _lock:
            res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    except Exception:  # noqa: BLE001
        return {"status": "failed", "people": []}
    people: list[dict[str, Any]] = []
    for pose in res.pose_landmarks or []:
        shown = [(p.x, p.y, p.visibility) for p in pose if p.visibility >= VIS_MIN and 0.0 <= p.x <= 1.0 and 0.0 <= p.y <= 1.0]
        if len(shown) < MIN_LANDMARKS:
            continue
        xs = np.clip([s[0] for s in shown], 0.0, 1.0)
        ys = np.clip([s[1] for s in shown], 0.0, 1.0)
        people.append({"box": [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
                       "score": round(float(np.mean([s[2] for s in shown])), 3)})
    return {"status": "ok" if people else "none", "people": people}


def _area_centre(person: dict[str, Any]) -> tuple[float, float]:
    x0, y0, x1, y1 = person["box"]
    return (x1 - x0) * (y1 - y0), (x0 + x1) / 2


def main_person(people: list[dict[str, Any]]) -> int | None:
    """The index of the main person: the largest, weighted up when nearer the middle of the picture. None for none."""
    if not people:
        return None
    best, best_score = 0, -1.0
    for i, p in enumerate(people):
        area, cx = _area_centre(p)
        central = 1.0 - min(1.0, abs(cx - 0.5) * 2)  # 1 in the middle, 0 at the left or right edge
        score = area * (0.5 + 0.5 * central)
        if score > best_score:
            best, best_score = i, score
    return best


def box_px(person: dict[str, Any], width: int, height: int) -> dict[str, int]:
    """The person's own extent in picture pixels (no margin), as {x, y, w, h}."""
    x0, y0, x1, y1 = person["box"]
    return {"x": int(round(x0 * width)), "y": int(round(y0 * height)),
            "w": max(1, int(round((x1 - x0) * width))), "h": max(1, int(round((y1 - y0) * height)))}


def suggest_box(person: dict[str, Any], width: int, height: int) -> dict[str, int]:
    """The suggested box round the person: their extent plus a margin, kept inside the picture and at least MIN_SIDE."""
    b = box_px(person, width, height)
    padx, pady = b["w"] * MARGIN_X, b["h"] * MARGIN_Y
    x0, y0 = max(0.0, b["x"] - padx), max(0.0, b["y"] - pady)
    x1, y1 = min(float(width), b["x"] + b["w"] + padx), min(float(height), b["y"] + b["h"] + pady)
    w = max(x1 - x0, float(min(MIN_SIDE, width)))  # a small person still gets a usable box
    h = max(y1 - y0, float(min(MIN_SIDE, height)))
    x = min(max(0.0, (x0 + x1) / 2 - w / 2), width - w)
    y = min(max(0.0, (y0 + y1) / 2 - h / 2), height - h)
    return clamp_box({"x": x, "y": y, "w": w, "h": h}, width, height) or {"x": 0, "y": 0, "w": width, "h": height}


def clamp_box(box: dict[str, Any], width: int, height: int) -> dict[str, int] | None:
    """A box as whole pixels inside the picture, at least MIN_SIDE on each side (or the picture side when smaller).
    None when the box is not a usable size or lies outside the picture."""
    try:
        x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if min(w, h) <= 0 or x < 0 or y < 0 or x + w > width + 0.5 or y + h > height + 0.5:
        return None
    need_w, need_h = min(MIN_SIDE, width), min(MIN_SIDE, height)
    if w < need_w or h < need_h:
        return None
    x, y = int(round(x)), int(round(y))
    w, h = min(int(round(w)), width - x), min(int(round(h)), height - y)
    return {"x": x, "y": y, "w": w, "h": h}


class PeopleIn(BaseModel):
    image: str = image_field(min_length=1)


@router.post("/api/people")
def people_in_picture(body: PeopleIn) -> dict[str, Any]:
    img = decode_or_400(body.image, "picture")
    W, H = img.size
    found = find(img)
    people = found["people"]
    main = main_person(people)
    suggested = suggest_box(people[main], W, H) if main is not None else None
    return {"status": found["status"], "picture": {"width": W, "height": H},
            "people": [{"box": box_px(p, W, H), "score": p["score"]} for p in people],
            "main": main, "suggested": suggested}
