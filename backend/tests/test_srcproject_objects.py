"""vgac/srcproject.py: the pictures of props and buildings have no expected turn (OBJECT_CATEGORIES); characters keep it.

The model is a box with a square footprint, so its outline is the same every quarter turn and only its colour tells the
walls apart; each wall has its own colour and stripes."""
from __future__ import annotations

import inspect

import numpy as np
import pytest

from vgac import face, srcproject

T = 128
SIZE = 256
# wall: (corner, edge along the wall, edge up, colour, stripe colour); outward normal = edge1 x edge2
WALLS = {
    "turn0": ((-0.5, 0.0, 0.5), (1, 0, 0), (0, 0.8, 0), (0.85, 0.2, 0.15), (0.95, 0.9, 0.85)),   # faces +Z
    "turn90": ((0.5, 0.0, 0.5), (0, 0, -1), (0, 0.8, 0), (0.2, 0.7, 0.25), (0.1, 0.2, 0.1)),     # faces +X
    "turn180": ((0.5, 0.0, -0.5), (-1, 0, 0), (0, 0.8, 0), (0.2, 0.3, 0.85), (0.9, 0.9, 0.2)),   # faces -Z
    "turn270": ((-0.5, 0.0, -0.5), (0, 0, 1), (0, 0.8, 0), (0.9, 0.8, 0.2), (0.5, 0.1, 0.5)),    # faces -X
    "top": ((-0.5, 0.8, 0.5), (1, 0, 0), (0, 0, -1), (0.55, 0.5, 0.45), (0.3, 0.25, 0.2)),       # faces +Y
}


def box() -> tuple[dict[str, np.ndarray], np.ndarray]:
    """The box and its true texture: each wall an 8 x 8 grid with its own island, coloured with its own stripes."""
    verts, faces, uv = [], [], []
    tex = np.full((T, T, 3), 0.4)
    n = 8
    for k, (o, e1, e2, col, stripe) in enumerate(WALLS.values()):
        u0, v0 = 0.04 + 0.32 * (k % 3), 0.04 + 0.48 * (k // 3)
        base = len(verts)
        for j in range(n + 1):
            for i in range(n + 1):
                verts.append(np.add(o, np.multiply(e1, i / n) + np.multiply(e2, j / n)))
                uv.append([u0 + 0.28 * i / n, v0 + 0.28 * j / n])
        for j in range(n):
            for i in range(n):
                a, b, c, d = base + j * (n + 1) + i, base + j * (n + 1) + i + 1, base + (j + 1) * (n + 1) + i + 1, base + (j + 1) * (n + 1) + i
                faces += [(a, b, c), (a, c, d)]
        x0, x1 = int(u0 * T) - 2, int((u0 + 0.28) * T) + 2
        y0, y1 = int((1 - v0 - 0.28) * T) - 2, int((1 - v0) * T) + 2
        tex[y0:y1, x0:x1] = col
        # stripes at a slant that differs per wall, so no two walls look alike in detail either
        ys, xs = np.mgrid[y0:y1, x0:x1]
        tex[y0:y1, x0:x1][((xs * (k + 1) + ys * 2) // 6) % 3 == 0] = stripe
    v, f = np.array(verts, dtype=np.float64), np.array(faces)
    return {"verts": v, "faces": f, "uv": np.array(uv), "vnormals": face.smooth_normals(v, f)}, tex


def drawn(mesh: dict[str, np.ndarray], tex: np.ndarray, turn: float, tilt: float, n: int = SIZE) -> np.ndarray:
    """The box as a picture taken at a known turn and tilt (orthographic), RGBA, transparent around it."""
    centre, scale = srcproject.model_frame(mesh)
    ras = srcproject.Raster(mesh, srcproject._camera(centre, turn, scale * 1.2, n, tilt))
    rgb = srcproject.render_colour(ras, tex)
    a = ras.mask.astype(np.float64)
    return np.concatenate([np.clip(rgb, 0, 1) * a[..., None], a[..., None]], axis=-1)


def paint_as(category: str | None, *a, **k) -> dict:
    """srcproject.paint with the asset's category, where paint takes one (the code before it took none treats every
    asset as a character)."""
    if category is not None and "category" in inspect.signature(srcproject.paint).parameters:
        k["category"] = category
    return srcproject.paint(*a, **k)


@pytest.fixture(scope="module")
def scene() -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]:
    mesh, tex = box()
    soft = np.clip(np.stack([face.blur(tex[..., c], 1.5) for c in range(3)], -1) * 0.9, 0, 1)  # what the bake would be
    return mesh, tex, soft


def test_a_prop_picture_a_quarter_turn_from_the_expected_turn_paints_at_its_own_turn(scene) -> None:  # noqa: ANN001
    """A prop's picture taken at turn 90 and tilt 20, built from the front (expected turn 0): the outline fits every quarter
    turn alike and the colour tells them apart. The pose is found at 90 and painted (before: refused for being far from
    the expected turn, or fitted at a quarter turn that only matches the outline)."""
    mesh, tex, soft = scene
    r = paint_as("prop_rigid", mesh, soft, drawn(mesh, tex, 90.0, 20.0), mode="off", size=SIZE, built_from="front")
    v = r["metrics"]["srcproject_views"]["picture"]
    assert "refused" not in v, v.get("refused")
    assert srcproject._angdiff(v["yaw"], 90.0) <= 10.0
    assert abs(float(v["pose_search"]["tilt"]) - 20.0) <= 10.0
    assert v["pose_search"].get("prior") is None   # no expected turn for an object


def test_a_character_picture_keeps_its_expected_turn(scene, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """Characters are unchanged: their picture is expected at the turn of the view the model was built from and searched
    within PICTURE_SEARCH_DEG of it, and the original at the picture's turn less that; a prop's have no expected turn."""
    mesh, tex, soft = scene
    seen: dict[tuple[str | None, str], tuple] = {}
    real = srcproject._pose_pick

    def spy(mesh, tex, prepared, src, src_n, name, centre, scale, min_iou, atlas_job, log, hint=None, turns=None,  # noqa: ANN001
            tilts=None, lenses=None, prior=None, window=srcproject.PRIOR_DEG, keep=3, turn_limit=None, breadth=1):
        seen[(cat, name)] = (prior, sorted(turns) if turns else None, turn_limit, breadth)
        return real(mesh, tex, prepared, src, src_n, name, centre, scale, min_iou, atlas_job, log, hint, turns, tilts,
                    lenses, prior, window, keep, turn_limit, breadth)

    monkeypatch.setattr(srcproject, "_pose_pick", spy)
    pic = drawn(mesh, tex, 40.0, 0.0)
    for cat in ("humanoid", None, "prop_rigid"):
        paint_as(cat, mesh, soft, pic, source=pic, mode="off", size=SIZE, built_from="quarter")
    q = srcproject.INTENDED_TURN["quarter"]
    want = sorted(q + float(d) for d in np.arange(-srcproject.PICTURE_SEARCH_DEG, srcproject.PICTURE_SEARCH_DEG + 1e-6, 10.0))
    for cat in ("humanoid", None):
        assert seen[(cat, "picture")] == (q, want, (q, srcproject.PICTURE_SEARCH_DEG), 1)
        assert seen[(cat, "source")][0] is not None and seen[(cat, "source")][3] == 1
    assert seen[("humanoid", "source")] == seen[(None, "source")]
    assert seen[("prop_rigid", "picture")][0] is None and len(seen[("prop_rigid", "picture")][1]) == 36
    assert seen[("prop_rigid", "picture")][3] == 4   # the whole circle: four times the outline poses get the colour terms
    assert seen[("prop_rigid", "source")][0] is None
