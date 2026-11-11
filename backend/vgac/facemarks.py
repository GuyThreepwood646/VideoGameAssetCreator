"""Eyes and mouth found by MediaPipe's face landmarker, run on the image card's ComfyUI (its built-in nodes).

The face pass's own finder (face.landmarks) takes each eye as the darkest spot of its side of the eye band and the
mouth as the darkest row under the nose. On dark brows over light eyes it puts an eye on the brow, and on a beard or
moustache it puts the mouth on the moustache (the T-pose man: one eye 50 px high, the mouth 170 px high on a 1024
crop), so a good close-up was refused as "its eyes and mouth could not be found" and the paint was fitted by the
whole face instead. MediaPipe finds each eye's outline and the lips as a face model, whatever the brows and beard do.

face.landmarks asks here first. Only while a face pass has set a card (use()); otherwise, or when MediaPipe finds no
face or a face that is not where the model's is, the darkest-spot finder is used as before, so tests and a missing
model change nothing. Model: Comfy-Org/mediapipe detection/mediapipe_face_fp32.safetensors (Apache-2.0, Google
MediaPipe), in ComfyUI's models/detection folder.
"""

from __future__ import annotations

import contextvars
import hashlib
import io
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image

MODEL = "mediapipe_face_fp32.safetensors"
TIMEOUT_S = 60.0
_card: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar("facemarks_card", default=None)
_cache: dict[str, dict[str, Any] | None] = {}
_CACHE_MAX = 64
stats = {"asked": 0, "found": 0, "failed": 0}


def use(base_url: str | None, input_dir: Path | str | None) -> contextvars.Token | None:
    """Ask this card while the returned token is set (asyncio.to_thread carries it into the worker threads)."""
    if not base_url or not input_dir:
        return None
    return _card.set((base_url.rstrip("/"), str(input_dir)))


def done(token: contextvars.Token | None) -> None:
    if token is not None:
        _card.reset(token)


def active() -> bool:
    return _card.get() is not None


def graph(name: str) -> dict[str, Any]:
    g: dict[str, Any] = {
        "m": {"class_type": "LoadMediaPipeFaceLandmarker", "inputs": {"model_name": MODEL}},
        "i": {"class_type": "LoadImage", "inputs": {"image": name}},
        "d": {"class_type": "MediaPipeFaceLandmarker", "inputs": {"face_detection_model": ["m", 0], "image": ["i", 0],
                                                                  "detector_variant": "both", "num_faces": 1,
                                                                  "min_confidence": 0.3, "missing_frame_fallback": "empty"}},
    }
    for key, region in (("a", "left_eye"), ("b", "right_eye"), ("c", "lips")):
        ins: dict[str, Any] = {"face_landmarks": ["d", 0], "regions": "custom"}
        for r in ("face_oval", "lips", "left_eye", "right_eye", "irises"):
            ins["regions." + r] = r == region
        g["k" + key] = {"class_type": "MediaPipeFaceMask", "inputs": ins}
        g["t" + key] = {"class_type": "MaskToImage", "inputs": {"mask": ["k" + key, 0]}}
        g["p" + key] = {"class_type": "PreviewImage", "inputs": {"images": ["t" + key, 0]}}
    return g


def centroid(mask: np.ndarray) -> tuple[float, float] | None:
    ys, xs = np.where(mask)
    if len(xs) < 4:
        return None
    return float(xs.mean() + 0.5), float(ys.mean() + 0.5)


def from_masks(eye_a: np.ndarray, eye_b: np.ndarray, lips: np.ndarray) -> dict[str, Any] | None:
    """{eyes: [left in the picture, right in the picture], mouth, eye_px (mean eye width)} from the three masks."""
    ea, eb, m = centroid(eye_a), centroid(eye_b), centroid(lips)
    if ea is None or eb is None or m is None:
        return None
    eyes = sorted([ea, eb])
    widths = []
    for e in (eye_a, eye_b):
        xs = np.where(e.any(axis=0))[0]
        widths.append(float(xs.max() - xs.min() + 1))
    return {"eyes": eyes, "mouth": m, "how": "mediapipe", "eye_px": round(float(np.mean(widths)), 1)}


def ask(img: np.ndarray, base_url: str, input_dir: str) -> dict[str, Any] | None:
    """One run on the card. None when no face was found or the card did not answer."""
    arr = np.clip(np.asarray(img, dtype=np.float64)[..., :3] * 255.0 + 0.5, 0, 255).astype(np.uint8)
    key = hashlib.md5(arr.tobytes()).hexdigest()
    if key in _cache:
        return _cache[key]
    stats["asked"] += 1
    name = f"vgac_marks_{key[:12]}.png"
    path = Path(input_dir) / name
    out: dict[str, Any] | None = None
    try:
        Image.fromarray(arr).save(path)
        with httpx.Client(base_url=base_url, timeout=15.0) as c:
            r = c.post("/prompt", json={"prompt": graph(name)})
            r.raise_for_status()
            pid = r.json()["prompt_id"]
            t0 = time.time()
            while True:
                h = c.get(f"/history/{pid}").json()
                if pid in h:
                    break
                if time.time() - t0 > TIMEOUT_S:
                    raise TimeoutError("the face landmarker did not answer")
                time.sleep(0.15)
            entry = h[pid]
            if (entry.get("status") or {}).get("status_str") != "success":
                raise RuntimeError("the face landmarker failed")
            masks = []
            for k in "abc":
                im = entry["outputs"]["p" + k]["images"][0]
                data = c.get("/view", params=im).content
                masks.append(np.asarray(Image.open(io.BytesIO(data)).convert("L")) > 127)
        out = from_masks(*masks)
        stats["found" if out else "failed"] += 1
    except Exception:  # noqa: BLE001  (no card, no model: the darkest-spot finder is used)
        stats["failed"] += 1
        out = None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = out
    return out


def plausible(m: dict[str, Any], info: dict[str, Any]) -> bool:
    """The found face is the model's: eyes above the nose and either side of it, the mouth below it, and the eyes a
    sensible distance apart for the face's width (another face in the picture, or a found pattern, is not used)."""
    nx, ny = info["nose"]
    w = float(info["width_px"])
    (lx, ly), (rx, ry) = m["eyes"]
    mx, my = m["mouth"]
    if not (lx < nx + 0.1 * w and rx > nx - 0.1 * w):
        return False
    if not (ny - 0.7 * w < ly < ny + 0.05 * w and ny - 0.7 * w < ry < ny + 0.05 * w):
        return False
    if not (ny - 0.05 * w < my < ny + 0.6 * w and abs(mx - nx) < 0.3 * w):
        return False
    gap = rx - lx
    return 0.2 * w < gap < 0.8 * w


def find(img: np.ndarray, info: dict[str, Any]) -> dict[str, Any] | None:
    """Eyes and mouth by MediaPipe for a face picture framed like the model's render, or None (no card set, no face,
    or not the model's face)."""
    card = _card.get()
    if card is None:
        return None
    m = ask(img, *card)
    if m is None or not plausible(m, info):
        return None
    return m
