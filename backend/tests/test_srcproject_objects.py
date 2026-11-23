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


def box(alike: bool = False) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """The box and its true texture: each wall an 8 x 8 grid with its own island, coloured with its own stripes. alike:
    the back wall is coloured and striped as the front, and the left as the right, so the box looks the same after a
    half turn (a crate)."""
    verts, faces, uv = [], [], []
    tex = np.full((T, T, 3), 0.4)
    n = 8
    walls = list(WALLS.values())
    if alike:
        walls[2] = walls[2][:3] + walls[0][3:]
        walls[3] = walls[3][:3] + walls[1][3:]
    for k, (o, e1, e2, col, stripe) in enumerate(walls):
        u0, v0 = 0.04 + 0.32 * (k % 3), 0.04 + 0.48 * (k // 3)
        ks = {2: 0, 3: 1}.get(k, k) if alike else k  # the stripes' slant: the same on walls that are alike
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
        tex[y0:y1, x0:x1][((xs * (ks + 1) + ys * 2) // 6) % 3 == 0] = stripe
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
            tilts=None, lenses=None, prior=None, window=srcproject.PRIOR_DEG, keep=3, turn_limit=None, breadth=1,
            half_turn_ref=None):
        seen[(cat, name)] = (prior, sorted(turns) if turns else None, turn_limit, breadth, half_turn_ref)
        return real(mesh, tex, prepared, src, src_n, name, centre, scale, min_iou, atlas_job, log, hint, turns, tilts,
                    lenses, prior, window, keep, turn_limit, breadth, half_turn_ref)

    monkeypatch.setattr(srcproject, "_pose_pick", spy)
    pic = drawn(mesh, tex, 40.0, 0.0)
    for cat in ("humanoid", None, "prop_rigid"):
        paint_as(cat, mesh, soft, pic, source=pic, mode="off", size=SIZE, built_from="quarter")
    q = srcproject.INTENDED_TURN["quarter"]
    want = sorted(q + float(d) for d in np.arange(-srcproject.PICTURE_SEARCH_DEG, srcproject.PICTURE_SEARCH_DEG + 1e-6, 10.0))
    for cat in ("humanoid", None):
        assert seen[(cat, "picture")] == (q, want, (q, srcproject.PICTURE_SEARCH_DEG), 1, None)
        assert seen[(cat, "source")][0] is not None and seen[(cat, "source")][3:] == (1, None)
    assert seen[("humanoid", "source")] == seen[(None, "source")]
    assert seen[("prop_rigid", "picture")][0] is None and len(seen[("prop_rigid", "picture")][1]) == 36
    assert seen[("prop_rigid", "picture")][3] == 4   # the whole circle: four times the outline poses get the colour terms
    assert seen[("prop_rigid", "source")][0] is None
    assert seen[("prop_rigid", "picture")][4] == q and seen[("prop_rigid", "source")][4] == 0.0  # the half-turn guard's facing


def wall_texels(k: int) -> tuple[slice, slice]:
    """The texture rows and columns inside wall k's island (box(): wall k's square, without its border)."""
    u0, v0 = 0.04 + 0.32 * (k % 3), 0.04 + 0.48 * (k // 3)
    return slice(int((1 - v0 - 0.28) * T) + 3, int((1 - v0) * T) - 3), slice(int(u0 * T) + 3, int((u0 + 0.28) * T) - 3)


def test_a_prop_paints_no_drawn_side_view_and_a_character_still_does(scene) -> None:  # noqa: ANN001
    """A drawn "left" view (turn 90) in a colour the box does not have, beside a front picture. A prop (OBJECT_CATEGORIES)
    paints from the pictures only: the drawn view paints nothing, and the turn 90 wall keeps the model's own colour (before:
    it painted that wall magenta, as the shop's drawn right view put a second vending machine on the wrong wall). A
    character still paints from it."""
    mesh, tex, soft = scene
    magenta = np.zeros_like(tex) + np.array([0.9, 0.1, 0.9])
    pic, left = drawn(mesh, tex, 0.0, 20.0), drawn(mesh, magenta, 90.0, 20.0)
    rows, cols = wall_texels(1)   # the turn 90 wall
    for cat, paints in (("prop_rigid", False), ("environment", False), ("humanoid", True)):
        r = paint_as(cat, mesh, soft, pic, {"left": left}, mode="full", size=SIZE, built_from="front")
        assert r["applied"], (cat, r["reason"])
        share = r["metrics"]["srcproject_share_by_view"]
        wall = r["base_color"][rows, cols]
        took = float((np.abs(wall - magenta[rows, cols]).max(-1) < 0.15).mean())
        if paints:
            assert share.get("left", 0.0) > 0.0 and took > 0.3, (cat, share, took)
        else:
            assert "left" not in share and took == 0.0, (cat, share, took)


def test_a_prop_keeps_its_own_colour_on_a_wall_seen_at_a_steep_angle(scene) -> None:  # noqa: ANN001
    """A prop's picture taken at turn 55 sees the turn 90 wall 35 degrees off square and the turn 0 wall 55 degrees off.
    The near wall is painted; the far one keeps the model's own colour (OBJECT_COS_CUT: before, a view painted in full to
    about 60 degrees, and the shop's original painting smeared its vending machine across a wall it saw at 50)."""
    mesh, tex, soft = scene
    r = paint_as("prop_rigid", mesh, soft, drawn(mesh, tex, 55.0, 0.0), mode="off", size=SIZE, built_from="front")
    assert r["applied"], r["reason"]
    new = r["base_color"]

    def moved(k: int) -> float:  # the share of the wall's texels the paint changed from the model's own colour
        rows, cols = wall_texels(k)
        return float((np.abs(new[rows, cols] - soft[rows, cols]).max(-1) > 0.02).mean())

    assert moved(1) > 0.5, moved(1)    # the turn 90 wall, 35 degrees off
    assert moved(0) < 0.05, moved(0)   # the turn 0 wall, 55 degrees off


@pytest.mark.parametrize("head_yaw, want", [(0.0, 30.0), (180.0, 210.0)])
def test_a_box_alike_after_a_half_turn_paints_on_the_half_nearer_the_facing(head_yaw: float, want: float) -> None:
    """A crate-like box (the back looks like the front) drawn at turn 30: the outline and the colour fit turn 210 as well,
    and the half-turn guard takes the half nearer the facing the app knows (head_yaw plus the build view's turn). With
    the facing turned a half turn the other half is taken, so the guard, not chance, decides."""
    mesh, tex = box(alike=True)
    soft = np.clip(np.stack([face.blur(tex[..., c], 1.5) for c in range(3)], -1) * 0.9, 0, 1)
    lines: list[str] = []  # with a log, as the clean-up calls it (a record read for the log must exist for objects too)
    r = paint_as("prop_rigid", mesh, soft, drawn(mesh, tex, 30.0, 20.0), mode="off", size=SIZE, built_from="front",
                 head_yaw=head_yaw, log=lines.append)
    v = r["metrics"]["srcproject_views"]["picture"]
    assert "refused" not in v, v.get("refused")
    assert srcproject._angdiff(v["yaw"], want) <= 10.0, (v["yaw"], v["pose_search"].get("half_turn"))
