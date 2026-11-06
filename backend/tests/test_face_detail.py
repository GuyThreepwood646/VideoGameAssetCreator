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


SKIN, HAIR = (0.8, 0.55, 0.45), (0.15, 0.1, 0.08)
HEAD_TOP, NOSE_Y = 1.72, 1.635  # nose tip 0.085 m under the top, as on the bake-off knights


def synthetic_head(haired: bool, n: int = 96) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """A front half-ellipsoid head (0.15 m wide, 0.2 m tall, 0.07 m deep) with a nose bump and an ear on each side,
    set back behind the head's edge as on a real head. Bald: all skin. Haired: hair over the top (from 0.35 of the
    height from the nose tip to the top), down the sides of the face and over the ears. Every triangle has its own
    corners, so it takes one colour: the left half of the texture is skin, the right half hair."""
    a, b, c, cy = 0.075, 0.1, 0.07, 1.62
    xs = np.linspace(-a, a, n)
    ys = np.linspace(cy - b, cy + b, n)
    X, Y = np.meshgrid(xs, ys)
    r2 = (X / a) ** 2 + ((Y - cy) / b) ** 2
    Z = c * np.sqrt(np.clip(1 - r2, 0, None)) + 0.02 * np.exp(-(X ** 2 + (Y - NOSE_Y) ** 2) / (2 * 0.008 ** 2))
    grid = np.stack([X, Y, Z], axis=-1)
    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            q = [grid[i, j], grid[i, j + 1], grid[i + 1, j + 1], grid[i + 1, j]]
            if all(r2[k] < 1 for k in ((i, j), (i, j + 1), (i + 1, j + 1), (i + 1, j))):
                tris += [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
    tris = np.array(tris)
    centre = tris.mean(axis=1)
    L = HEAD_TOP - NOSE_Y
    hair = (centre[:, 1] > NOSE_Y + 0.35 * L) | (np.abs(centre[:, 0]) > 0.045) if haired else np.zeros(len(tris), bool)
    ears = []
    for side in (-1, 1):  # discs facing the camera, at the depth of the head's edge
        t = np.linspace(0, 2 * np.pi, 25)[:-1]
        cx, cyy, r = side * 0.08, 1.625, 0.018
        for k in range(len(t)):
            k2 = (k + 1) % len(t)
            ears.append([[cx, cyy, 0.0], [cx + r * np.cos(t[k]), cyy + r * np.sin(t[k]), 0.0],
                         [cx + r * np.cos(t[k2]), cyy + r * np.sin(t[k2]), 0.0]])
    tris = np.concatenate([tris, np.array(ears)])
    hair = np.concatenate([hair, np.full(len(ears), haired)])
    verts = tris.reshape(-1, 3)
    faces = np.arange(len(verts)).reshape(-1, 3)
    uv = np.where(np.repeat(hair, 3)[:, None], [0.75, 0.5], [0.25, 0.5])
    tex = np.zeros((16, 16, 3))
    tex[:, :8], tex[:, 8:] = SKIN, HAIR
    return {"verts": verts, "faces": faces, "uv": uv, "vnormals": face.smooth_normals(verts, faces)}, tex


def find_synthetic_face(haired: bool, S: int = 256) -> tuple[dict, face.Camera]:
    mesh, tex = synthetic_head(haired)
    head = face.Head(centre=np.array([0.0, 1.62, 0.0]), width=0.2, top=HEAD_TOP, height=1.2)
    c = face.head_camera(head, 0, S)
    return face.find_face(face.render_view(mesh, tex, c), mesh, c, head), c


def test_find_face_on_a_bald_head_stops_at_the_forehead_and_before_the_ears(monkeypatch):
    info, c = find_synthetic_face(haired=False)
    assert info["found"]
    px = c.size / c.scale  # pixels per metre
    # the face is narrower than the head's 0.15 m (the ears reach 0.196 m) ...
    assert info["width_px"] < 0.135 * px
    ear_x = int(round(c.size / 2 + 0.09 * px))
    ear_y = int(round(c.size / 2 - (1.625 - c.target[1]) * px))
    assert not info["region"][ear_y, ear_x] and not info["region"][ear_y, c.size - 1 - ear_x]
    # ... and stops above the brows, well under the top of the head
    top_y = c.size / 2 - (NOSE_Y + face.FACE_TOP * (HEAD_TOP - NOSE_Y) - c.target[1]) * px
    assert info["box"][1] >= top_y - 2
    # without the bounds the skin-coloured scalp and ears all joined the face (the bug)
    monkeypatch.setattr(face, "FACE_TOP", 99.0)
    monkeypatch.setattr(face, "FACE_DEPTH", 99.0)
    unbounded, _ = find_synthetic_face(haired=False)
    assert unbounded["width_px"] > 0.155 * px and unbounded["region"][ear_y, ear_x]
    assert unbounded["box"][1] < top_y - 10


def test_find_face_on_a_haired_head_is_what_the_hair_bounds_it_to(monkeypatch):
    info, c = find_synthetic_face(haired=True)
    assert info["found"]
    monkeypatch.setattr(face, "FACE_TOP", 99.0)
    monkeypatch.setattr(face, "FACE_DEPTH", 99.0)
    unbounded, _ = find_synthetic_face(haired=True)
    assert info["box"] == unbounded["box"] and (info["region"] == unbounded["region"]).all()


def test_face_bounds_keep_the_jaw_and_neck_and_cut_only_deep_or_high_points():
    head = face.Head(centre=np.zeros(3), width=0.2, top=1.0, height=1.7)  # L = 0.1 from a nose tip at y 0.9
    z = np.array([[0.0, 0.05, 0.07, 0.07, 0.0]])  # depth behind the camera's plane; the nose tip is nearest
    y = np.array([[0.9, 0.9, 0.9, 0.85, 0.97]])
    pos = np.stack([np.zeros_like(y), y, -z], axis=-1)
    ok = face.face_bounds(z, pos, head, (0, 0))
    # the nose; a cheek 0.5 L back; an ear 0.7 L back (out); the neck 0.7 L back under the jaw (in); the scalp (out)
    assert ok.tolist() == [[True, True, False, True, False]]


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


# --- hair and skin, fills, tone at the edges (second round on the knight) ----------------------------------


def _face_info(S: int, region: np.ndarray, skin_rgb) -> dict:
    ys, xs = np.where(region)
    return {"region": region, "nose": (S // 2, int(ys.mean())), "width_px": float(xs.max() - xs.min()),
            "box": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
            "skin": face.to_lab(np.array([[skin_rgb]], dtype=np.float64))[0, 0].tolist()}


def test_hair_is_hair_only_when_it_joins_the_hair_around_the_face():
    S = 128
    skin, hair = (0.9, 0.7, 0.6), (0.7, 0.15, 0.05)
    old = np.empty((S, S, 3))
    old[:] = skin
    old[:40] = hair  # hair on top of the head
    old[90:96, 50:78] = (0.75, 0.2, 0.15)  # a red mouth on its own
    region = np.zeros((S, S), dtype=bool)
    region[40:120, 24:104] = True
    mask = np.zeros((S, S), dtype=bool)
    mask[:, 16:112] = True
    pic = old.copy()
    pic[40:70, 60:66] = hair  # a painted strand hanging from the hair onto the skin
    info = _face_info(S, region, skin)
    seg = face.hair_and_skin(old, pic, info, mask)
    assert seg["found"]
    assert seg["old_hair"][10, 64] and not seg["old_hair"][92, 64]  # the mouth is not hair
    assert seg["pic_hair"][55, 63] and not seg["pic_hair"][92, 64]  # the strand is, the mouth is not
    assert not seg["old_hair"][60, 40]


def test_inpaint_fills_smoothly_and_keeps_the_rest():
    img = gradient(64)
    hole = np.zeros((64, 64), dtype=bool)
    hole[24:40, 24:40] = True
    broken = img.copy()
    broken[hole] = 1.0
    out = face.inpaint(broken, ~hole)
    assert np.array_equal(out[~hole], img[~hole])
    assert np.abs(out[hole] - img[hole]).max() < 0.2


def test_paint_fades_out_where_it_stops_short():
    # The front square is painted; the right half is hidden behind a strip nearer the camera. With softening the
    # paint fades out before the hidden part instead of stopping in a hard line.
    m = combine(plane(0.0, 0.0, 0.5), plane(0.2, 0.5, 1.0))
    m["verts"][4:, 0] = m["verts"][4:, 0] * 0.5 + 0.25  # the strip covers x 0..0.5 of the square
    tex = np.zeros((64, 64, 3))
    hard = face.project_back(m, tex, cam(), np.ones((64, 64, 3)), np.ones((64, 64)), facing=(0.0, 0.1))
    soft = face.project_back(m, tex, cam(), np.ones((64, 64, 3)), np.ones((64, 64)), facing=(0.0, 0.1), soften_m=0.3)
    row_h, row_s = hard["texel_weight"][32, :32], soft["texel_weight"][32, :32]
    assert row_h[12] > 0.95 and row_h[20] < 0.05  # hard: painted up to the strip
    assert row_s[4] > row_s[12] > row_s[15]  # soft: fading toward it


def test_spread_tone_carries_the_painted_tone_onto_nearby_skin_only():
    m = combine(plane(0.0, 0.0, 1.0))
    T = 64
    skin = (0.8, 0.6, 0.5)
    old = np.empty((T, T, 3))
    old[:] = skin
    old[:, 56:] = (0.1, 0.1, 0.6)  # not skin
    new = old.copy()
    tw = np.zeros((T, T))
    tw[:, :24] = 1.0
    new[:, :24] = np.clip(old[:, :24] + 0.1, 0, 1)
    out, n = face.spread_tone(m, old, new, tw, face.to_lab(np.array([[skin]]))[0, 0].tolist(), radius_m=0.3, cell_m=0.05)
    assert n > 0
    assert out[32, 28, 0] - old[32, 28, 0] > out[32, 44, 0] - old[32, 44, 0] >= 0  # fades with distance
    assert out[32, 28, 0] - old[32, 28, 0] > 0.02
    assert np.allclose(out[:, 58:], old[:, 58:])  # not skin: untouched
    assert np.allclose(out[:, :24], new[:, :24])  # painted: as painted


# --- third round: the painting decides colour --------------------------------------------------------------


def test_merge_detail_keeps_the_paintings_colour_and_takes_only_fine_detail():
    rng = np.random.default_rng(5)
    painted = np.empty((256, 256, 3))
    painted[:] = (0.5, 0.6, 0.55)  # grey-green
    sharp = np.empty((256, 256, 3))
    sharp[:] = (0.45, 0.3, 0.2)  # brown: a redraw that changed the colour
    sharp += rng.normal(0, 0.05, (256, 256, 1))  # and fine detail
    out = face.merge_detail(painted, sharp, sigma=3.0 * 1024 / 256)
    assert np.abs(out.mean(axis=(0, 1)) - painted[0, 0]).max() < 0.01  # the painting's colour
    assert out[..., 0].std() > 0.02  # the redraw's fine detail
    assert face.colour_shift(face.eye_colour(out, [(128, 128)], 200), face.eye_colour(painted, [(128, 128)], 200)) < 1.0


def test_eye_colour_check_fails_softly_when_the_eyes_drift():
    bad = fd.face_checks({"face_detail": {"ran": True, "sharpness_after": 1.4, "feature_match_after": 0.85, "eye_colour_shift": 12.4}})[0]
    assert not bad["ok"] and "eyes' colour" in bad["help"] and bad["fix_label"] == "Paint the face again"
    good = fd.face_checks({"face_detail": {"ran": True, "sharpness_after": 1.4, "feature_match_after": 0.85, "eye_colour_shift": 5.3}})[0]
    assert good["ok"]
    assert fd.face_checks({"face_detail": {"ran": True, "sharpness_after": 1.4, "feature_match_after": 0.85, "eye_colour_shift": None}})[0]["ok"]


# --- fourth round: painting the cheeks from the side pictures ----------------------------------------------


def quarter_cylinder(n: int = 32, r: float = 0.4) -> dict[str, np.ndarray]:
    """A quarter of a cylinder (axis Y) from facing +Z (u = 0) round to facing +X (u = 1), its UVs along the arc."""
    verts, uv, faces = [], [], []
    for i in range(n + 1):
        a = (np.pi / 2) * i / n
        for y in (-0.4, 0.4):
            verts.append([r * np.sin(a), y, r * np.cos(a)])
            uv.append([i / n, 0.0 if y < 0 else 1.0])
    for i in range(n):
        a0, a1, b0, b1 = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        faces += [[a0, b0, b1], [a0, b1, a1]]
    v = np.array(verts, dtype=np.float64)
    f = np.array(faces)
    return {"verts": v, "faces": f, "uv": np.array(uv), "vnormals": face.smooth_normals(v, f)}


def test_the_side_camera_paints_what_the_front_sees_edge_on_and_the_overlap_has_no_step():
    m = quarter_cylinder()
    T = 128
    tex = np.zeros((T, T, 3))
    front = face.Camera(target=np.zeros(3), yaw_deg=0.0, scale=1.2, size=128)
    side = face.Camera(target=np.zeros(3), yaw_deg=90.0, scale=1.2, size=128)
    a = face.project_back(m, tex, front, np.full((128, 128, 3), 0.3), np.ones((128, 128)), facing=(0.25, 0.7))
    b = face.project_back(m, a["texture"], side, np.full((128, 128, 3), 0.7), np.ones((128, 128)), facing=(0.25, 0.7),
                          cover=a["texel_weight"])
    row = b["texture"][64, 2:-2, 0]
    cover = np.clip(a["texel_weight"] + b["texel_weight"], 0, 1)[64, 2:-2]
    assert abs(row[0] - 0.3) < 0.02  # facing the front camera: the front painting, untouched by the side
    assert abs(row[-1] - 0.7) < 0.02  # facing the side camera, edge-on to the front: the side painting
    assert np.abs(np.diff(row)).max() < 0.05  # the hand-over is gradual, no step
    assert cover.min() > 0.95  # every texel is fully painted by one camera or the two together


def test_cover_limits_a_later_camera_to_what_is_left():
    m = combine(plane(0.0, 0.0, 1.0))
    tex = np.zeros((64, 64, 3))
    cover = np.zeros((64, 64))
    cover[:, :32] = 1.0
    res = face.project_back(m, tex, cam(), np.ones((64, 64, 3)), np.ones((64, 64)), facing=(0.0, 0.1), cover=cover)
    assert res["texture"][32, 10, 0] < 0.01 and res["texture"][32, 50, 0] > 0.99


def test_a_side_picture_whose_outline_does_not_match_is_not_used():
    S = 256
    Y, X = np.mgrid[0:S, 0:S]
    head = ((X - 128) ** 2 / 60 ** 2 + (Y - 120) ** 2 / 80 ** 2) <= 1  # the model's head from the side
    same = ((X - 136) ** 2 / 62 ** 2 + (Y - 116) ** 2 / 82 ** 2) <= 1  # the picture's: the same shape, a little off
    other = (np.abs(X - 128) < 20) & (np.abs(Y - 120) < 110)  # a thin post: not a head
    good = face.silhouette_fit(same.astype(np.float64), head, (30, 210))
    bad = face.silhouette_fit(other.astype(np.float64), head, (30, 210))
    assert good["iou"] >= fd.SIDE_IOU_MIN > bad["iou"]


def test_no_side_pictures_changes_nothing():
    m = combine(plane(0.0, 0.0, 1.0))
    tex = np.full((64, 64, 3), 0.4)
    head = face.Head(centre=np.zeros(3), width=0.2, top=0.5, height=1.0)
    out, record = fd.paint_sides(m, tex, tex, np.zeros((64, 64)), head, {"skin": [60, 15, 15]}, [30, 40, 35], {},
                                 np.ones((64, 64), dtype=bool))
    assert record == {} and np.array_equal(out, tex)
    out, record = fd.paint_sides(m, tex, tex, np.zeros((64, 64)), head, {"skin": [60, 15, 15]}, [30, 40, 35],
                                 {"left": Path("does/not/exist.png")}, np.ones((64, 64), dtype=bool))
    assert record == {} and np.array_equal(out, tex)


def test_the_check_names_the_sides_used():
    c = fd.face_checks({"face_detail": {"ran": True, "sharpness_before": 0.8, "sharpness_after": 1.3, "feature_match_after": 0.8,
                                        "eye_colour_shift": 5.0, "sides_used": ["quarter", "right"]}})[0]
    assert c["ok"] and "the three-quarter view and the right side" in c["help"]
    c = fd.face_checks({"face_detail": {"ran": True, "sharpness_before": 0.8, "sharpness_after": 1.3, "feature_match_after": 0.8}})[0]
    assert "side" not in c["help"]
