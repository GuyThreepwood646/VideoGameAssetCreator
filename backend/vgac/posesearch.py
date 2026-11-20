"""Find the camera a picture was taken from: turn, tilt and lens (WP-61 part C, first version).

The search scores each candidate pose by how well the model's outline, drawn from that pose, matches the picture's
silhouette. A coarse grid (turn in 10 degree steps, tilt -20 to 60 in 10 degree steps, lens orthographic, 85, 50 or
35 mm) is scored at a small size; the best poses are then refined locally to about 2 degrees.

Not yet in this version: the inner-edge and colour-structure terms named in the brief. Paint does not call this module
yet; it exists to be measured (bakeoff/wp61bc) before it is wired in behind a version switch."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vgac import face
from vgac import srcproject as sp

TURNS = list(range(0, 360, 10))
TILTS = list(range(-20, 61, 10))  # pictures taken from slightly below are real (WP-61 part C)
LENSES: list[float | None] = [None, 85.0, 50.0, 35.0]
COARSE_N = 128


@dataclass
class Pose:
    turn: float
    tilt: float
    lens: float | None
    iou: float


def _mask_small(alpha: np.ndarray, n: int) -> np.ndarray:
    return sp.small(alpha, n) > 0.5


def _score(mesh: dict, cam: face.Camera, pm: np.ndarray) -> float:
    return sp.iou(pm, sp.outline(mesh, cam))


def coarse(mesh: dict, alpha: np.ndarray, centre: np.ndarray, scale: float, n: int = COARSE_N,
           lenses: list[float | None] | None = None) -> list[Pose]:
    """Every coarse pose scored, best first."""
    pm = _mask_small(alpha, n)
    out: list[Pose] = []
    for lens in (LENSES if lenses is None else lenses):
        for tilt in TILTS:
            for turn in TURNS:
                cam = face.Camera(target=np.asarray(centre, dtype=np.float64), yaw_deg=float(turn), scale=float(scale),
                                  size=n, tilt_deg=float(tilt), lens_mm=lens)
                out.append(Pose(float(turn), float(tilt), lens, _score(mesh, cam, pm)))
    out.sort(key=lambda p: -p.iou)
    return out


def refine(mesh: dict, alpha: np.ndarray, centre: np.ndarray, scale: float, start: Pose, n: int = 256,
           step: float = 2.0, span: float = 10.0) -> Pose:
    """Local search around a coarse pose: turn and tilt in `step` degrees within `span`, at size n."""
    pm = _mask_small(alpha, n)
    best = None
    for dt in np.arange(-span, span + 1e-9, step):
        for dl in np.arange(-span, span + 1e-9, step):
            cam = face.Camera(target=np.asarray(centre, dtype=np.float64), yaw_deg=float(start.turn + dt),
                              scale=float(scale), size=n, tilt_deg=float(start.tilt + dl), lens_mm=start.lens)
            p = Pose(float(start.turn + dt), float(start.tilt + dl), start.lens, _score(mesh, cam, pm))
            if best is None or p.iou > best.iou:
                best = p
    return best


def search(mesh: dict, alpha: np.ndarray, centre: np.ndarray, scale: float, keep: int = 3) -> list[Pose]:
    """Coarse grid, then a local refinement of the `keep` best coarse poses. Best first."""
    coarse_best = coarse(mesh, alpha, centre, scale)[:keep]
    refined = [refine(mesh, alpha, centre, scale, p) for p in coarse_best]
    refined.sort(key=lambda p: -p.iou)
    return refined
