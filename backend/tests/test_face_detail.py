"""Face detail pass: the projection maths and masks on synthetic data, the island fitting, the skip rules and the
check (vgac/face.py, vgac/blender/uvfit.py, vgac/stages/face_detail.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from vgac import face, guidance
from vgac.stages import face_detail as fd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vgac" / "blender"))
import uvfit  # noqa: E402


def plane(z: float, u0: float, u1: float) -> dict[str, np.ndarray]:
    """A square facing +Z at depth z, x and y in -0.5..0.5, its UVs in columns u0..u1 of the texture."""
    verts = np.array([[-0.5, -0.5, z], [0.5, -0.5, z], [0.5, 0.5, z], [-0.5, 0.5, z]], dtype=np.float64)
    uv = np.array([[u0, 0.0], [u1, 0.0], [u1, 1.0], [u0, 1.0]])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    return {"verts": verts, "faces": faces, "uv": uv}


def combine(*parts: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    verts, faces, uv, off = [], [], [], 0
    for p in parts:
        verts.append(p["verts"])
        faces.append(p["faces"] + off)
        uv.append(p["uv"])
        off += len(p["verts"])
    v = np.concatenate(verts)
    f = np.concatenate(faces)
    return {"verts": v, "faces": f, "uv": np.concatenate(uv), "vnormals": face.smooth_normals(v, f)}


def cam(size: int = 64) -> face.Camera:
    return face.Camera(target=np.zeros(3), yaw_deg=0.0, scale=1.0, size=size)


def gradient(size: int) -> np.ndarray:
    y, x = np.mgrid[0:size, 0:size] / (size - 1)
    return np.stack([x, y, 0.5 * np.ones_like(x)], axis=2)


def test_camera_projects_front_and_depth():
    c = cam(100)
    pts, depth = c.project(np.array([[0.0, 0.0, 0.0], [0.5, 0.5, 0.2], [-0.5, -0.5, -0.2]]))
    assert np.allclose(pts[0], [50, 50]) and np.allclose(pts[1], [100, 0]) and np.allclose(pts[2], [0, 100])
    assert depth[1] < depth[0] < depth[2]  # +Z is toward the camera: nearer is smaller


def test_render_then_project_back_is_registered():
    # The render and the projection use the same camera and rasteriser, so painting a picture back and rendering again
    # gives the picture (inside, away from the edge texels).
    m = combine(plane(0.0, 0.0, 1.0))
    T = 64
    tex = np.full((T, T, 3), 0.2)
    pic = gradient(64)
    res = face.project_back(m, tex, cam(), pic, np.ones((64, 64)), facing=(0.0, 0.1))
    assert np.abs(res["texture"][4:-4, 4:-4] - pic[4:-4, 4:-4]).max() < 0.03
    again = face.render_view(m, res["texture"], cam())
    assert np.abs(again["image"][4:-4, 4:-4] - pic[4:-4, 4:-4]).max() < 0.05


def test_hidden_and_edge_on_surfaces_keep_their_colour():
    front = plane(0.0, 0.0, 0.5)
    back = plane(-0.3, 0.5, 1.0)  # straight behind the front one: hidden
    m = combine(front, back)
    T = 64
    tex = np.full((T, T, 3), 0.2)
    res = face.project_back(m, tex, cam(), np.ones((64, 64, 3)), np.ones((64, 64)), facing=(0.0, 0.1))
    assert res["texture"][4:-4, 4:28].min() > 0.95  # front: painted
    assert np.allclose(res["texture"][4:-4, 38:-4], 0.2)  # back: untouched
    # a square turned edge-on to the camera keeps its colour
    side = plane(0.0, 0.0, 1.0)
    side["verts"] = side["verts"][:, [2, 1, 0]]  # now in the y-z plane, facing +X
    m2 = combine(side)
    res2 = face.project_back(m2, tex, cam(), np.ones((64, 64, 3)), np.ones((64, 64)))
    assert np.allclose(res2["texture"], 0.2)


def test_weight_and_strength_limit_the_paint():
    m = combine(plane(0.0, 0.0, 1.0))
    tex = np.zeros((64, 64, 3))
    w = np.zeros((64, 64))
    w[:, :32] = 1.0
    res = face.project_back(m, tex, cam(), np.ones((64, 64, 3)), w, facing=(0.0, 0.1), strength=0.5)
    assert abs(res["texture"][32, 10, 0] - 0.5) < 0.02
    assert res["texture"][32, 50, 0] < 0.02


def test_masks_grow_fill_erode_and_feather():
    ring = np.zeros((40, 40), dtype=bool)
    ring[10:30, 10:30] = True
    ring[15:25, 15:25] = False
    filled = face.fill_holes(ring)
    assert filled[20, 20] and not filled[5, 5]
    seed = np.zeros_like(ring)
    seed[10, 10] = True
    assert face.grow(seed, ring).sum() == ring.sum()
    assert face.erode(filled, 2).sum() < filled.sum() < face.dilate_mask(filled, 2).sum()
    w = face.screen_weight(np.pad(np.ones((60, 60), dtype=bool), 20), feather_frac=0.05)
    # fades out by the region edge (row 20) and is gone a few pixels past it
    assert w[50, 50] > 0.99 and w[20, 50] < 0.05 and w[19, 50] == 0.0 and w.max() <= 1.0


def test_tone_match_takes_the_old_broad_tone_and_keeps_the_new_detail():
    rng = np.random.default_rng(1)
    old = np.full((64, 64, 3), 0.6)
    detail = rng.normal(0, 0.05, (64, 64, 3))
    new = np.clip(0.3 + detail, 0, 1)
    region = np.ones((64, 64), dtype=bool)
    out = face.tone_match(new, old, region, sigma_frac=0.2)
    assert abs(out.mean() - 0.6) < 0.02
    assert np.corrcoef((out - out.mean()).ravel(), detail.ravel())[0, 1] > 0.8


def test_fit_landmarks_recovers_scale_and_shift_and_refuses_nonsense():
    src = {"eyes": [(40.0, 40.0), (80.0, 40.0)], "mouth": (60.0, 80.0)}
    k, t = 1.1, np.array([5.0, -3.0])
    c = np.array([60.0, 60.0])
    move = lambda p: tuple(c + k * (np.array(p) - c) + t)  # noqa: E731
    dst = {"eyes": [move(p) for p in src["eyes"]], "mouth": move(src["mouth"])}
    a = face.fit_landmarks(src, dst, (60.0, 60.0))
    assert a is not None and abs(a["sx"] - 1.1) < 1e-3 and a["residual_px"] < 0.01
    out = face.scale_image(np.zeros((8, 8)), a["sx"], a["sy"], a["tx"], a["ty"], a["centre"])
    assert out.shape == (8, 8)
    far = {"eyes": [(40.0, 40.0), (80.0, 40.0)], "mouth": (60.0, 140.0)}  # mouth twice as far: not the same face
    assert face.fit_landmarks(src, far, (60.0, 60.0)) is None


def test_align_finds_a_shift():
    rng = np.random.default_rng(3)
    base = face.blur(rng.random((256, 256, 3)), 3.0)
    moved = face.scale_image(base, 1.0, 1.0, 12.0, -8.0, (128.0, 128.0))
    region = np.zeros((256, 256), dtype=bool)
    region[64:192, 64:192] = True
    a = face.align(base, moved, region, work=128, scales=(0.94, 1.06))
    assert abs(a["tx"] - 12) <= 3 and abs(a["ty"] + 8) <= 3 and a["score"] > 0.5


def test_find_nose_prefers_a_small_bump_over_a_broad_fringe():
    S = 200
    c = face.Camera(target=np.array([0.0, 1.6, 0.0]), yaw_deg=0.0, scale=0.3, size=S)
    head = face.Head(centre=np.array([0.0, 1.6, 0.0]), width=0.2, top=1.7, height=1.7)
    Y, X = np.mgrid[0:S, 0:S]
    world_y = 1.6 - (Y + 0.5 - S / 2) * 0.3 / S
    world_x = (X + 0.5 - S / 2) * 0.3 / S
    z = 0.1 + 0.5 * (world_x ** 2 + (world_y - 1.6) ** 2)  # a rounded face
    nose_y = 1.7 - 0.1 * 1.7
    z -= 0.02 * np.exp(-((world_x ** 2 + (world_y - nose_y) ** 2) / (2 * 0.008 ** 2)))  # the nose
    pos = np.stack([world_x, world_y, -z], axis=2)
    nose = face.find_nose(z, np.ones((S, S), dtype=bool), pos, c, head)
    assert nose is not None
    assert abs(world_y[nose[1], nose[0]] - nose_y) < 0.01 and abs(world_x[nose[1], nose[0]]) < 0.01


def test_gain_needed():
    assert face.gain_needed(133, 256) == pytest.approx(256 / 133)
    assert face.gain_needed(300, 256) == 1.0
    assert face.gain_needed(20, 256) == 3.0
    assert fd.target_texels(2048) == 256 and fd.target_texels(1024) == 128


# --- island fitting (blender/uvfit.py) ---------------------------------------------------------------------


def square(u0: float, v0: float, s: float) -> np.ndarray:
    return np.array([[[u0, v0], [u0 + s, v0], [u0 + s, v0 + s]], [[u0, v0], [u0 + s, v0 + s], [u0, v0 + s]]])


def test_islands_go_into_free_space_without_touching_the_rest():
    fixed = np.concatenate([square(0.0, 0.0, 0.5), square(0.5, 0.5, 0.5)])
    island = square(0.05, 0.05, 0.3)  # where the fixed piece is: must move
    got = uvfit.place_islands(fixed, [island], res=64, pad_cells=1)
    assert got is not None
    moved = uvfit.apply_placement(island.reshape(-1, 2), got[0])
    occ = uvfit.rasterise_tris(fixed, 64)
    mine = uvfit.rasterise_tris(moved.reshape(-1, 3, 2), 64)
    assert not (occ & mine).any()
    assert moved.min() >= 0 and moved.max() <= 1


def test_islands_that_cannot_fit_say_so():
    fixed = square(0.0, 0.0, 1.0)
    assert uvfit.place_islands(fixed, [square(0.1, 0.1, 0.2)], res=32, pad_cells=1) is None


# --- skip rules and the check ------------------------------------------------------------------------------


REC = {"surface": {"texture_filter": "smooth", "shading": "smooth"}, "mesh": {"texture": 2048}}


def test_skip_rules():
    assert fd.skip_reason("humanoid", REC, {}, True) is None
    assert fd.skip_reason("humanoid", REC, {"face_detail": False}, True) == "off"
    assert fd.skip_reason("quadruped", REC, {}, True) == "not_humanoid"
    assert fd.skip_reason("humanoid", {"surface": {"texture_filter": "crisp"}}, {}, True) == "style"
    assert fd.skip_reason("humanoid", {"surface": {"shading": "flat"}}, {}, True) == "style"
    assert fd.skip_reason("humanoid", REC, {}, False) == "no_picture"
    assert fd.strength_of({"face_strength": 3}) == 1.0 and fd.strength_of({"face_strength": "x"}) == 1.0


def test_covered_head():
    head = face.Head(centre=np.zeros(3), width=0.2, top=1.7, height=1.7)
    c = face.Camera(target=np.zeros(3), yaw_deg=0, scale=0.3, size=300)  # head 200 px wide
    assert face.covered_head({"found": True, "width_px": 40.0}, head, c)
    assert not face.covered_head({"found": True, "width_px": 150.0}, head, c)
    assert face.covered_head({"found": False}, head, c)


def test_face_check():
    assert fd.face_checks({}) == []
    for reason in ("off", "not_humanoid", "style", "no_picture", "no_face", "covered"):
        assert fd.face_checks({"face_detail": {"ran": False, "skipped": reason}}) == []
    c = fd.face_checks({"face_detail": {"ran": False, "skipped": "no_card"}})[0]
    assert not c["ok"] and c["severity"] == "warn" and "no graphics card" in c["help"]
    assert c["fix"]["stage"] == "prep" and c["fix_label"] == "Paint the face again" and c["fix"]["params"]["face_seed"] is None
    good = fd.face_checks({"face_detail": {"ran": True, "sharpness_before": 0.82, "sharpness_after": 1.44, "feature_match_after": 0.84}})[0]
    assert good["ok"] and "0.82" in good["help"] and "1.44" in good["help"]
    blurry = fd.face_checks({"face_detail": {"ran": True, "sharpness_after": 0.7, "feature_match_after": 0.9}})[0]
    assert not blurry["ok"] and "still blurry" in blurry["help"]
    off = fd.face_checks({"face_detail": {"ran": True, "sharpness_after": 1.5, "feature_match_after": 0.3}})[0]
    assert not off["ok"] and "follows the picture poorly" in off["help"]
    assert "face_matches_picture" in guidance.CHECK_GUIDE
