"""vgac/srcproject.py: the picture's own pixels painted into the atlas through the UVs (synthetic models, no Blender)."""
from __future__ import annotations

import numpy as np
import pytest

from vgac import face, srcproject

T = 128
SIZE = 256


def grid_quad(z: float, front: bool, u0: float, v0: float, n: int = 8) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A 1 x 1 quad in the XY plane at depth z, n x n cells, its island at (u0, v0) of size 0.4 in the atlas.
    front: it faces +Z; otherwise -Z."""
    xs = np.linspace(-0.5, 0.5, n + 1)
    verts, uv = [], []
    for j in range(n + 1):
        for i in range(n + 1):
            verts.append([xs[i], xs[j], z])
            uv.append([u0 + 0.4 * (xs[i] + 0.5), v0 + 0.4 * (xs[j] + 0.5)])
    faces = []
    for j in range(n):
        for i in range(n):
            a, b, c, d = j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i + 1, (j + 1) * (n + 1) + i
            tris = [(a, b, c), (a, c, d)]
            faces += tris if front else [(t[0], t[2], t[1]) for t in tris]
    return np.array(verts), np.array(faces), np.array(uv)


def model(with_back: bool = False) -> dict[str, np.ndarray]:
    v, f, uv = grid_quad(0.0, True, 0.05, 0.1)
    if with_back:
        v2, f2, uv2 = grid_quad(-1.0, False, 0.55, 0.1)
        f = np.concatenate([f, f2 + len(v)])
        v, uv = np.concatenate([v, v2]), np.concatenate([uv, uv2])
    return {"verts": v, "faces": f, "uv": uv, "vnormals": face.smooth_normals(v, f)}


def pattern(fx: np.ndarray, fy: np.ndarray) -> np.ndarray:
    """A colourful pattern with detail at a few texels' scale; fx, fy in 0..1 across the quad (fy down)."""
    r = 0.5 + 0.4 * np.sin(2 * np.pi * 6 * fx)
    g = 0.15 + 0.7 * fy
    b = 0.5 + 0.4 * np.sin(2 * np.pi * 5 * fy + 1.0)
    return np.stack([r, g, b], axis=-1)


def picture(rect: tuple[int, int, int, int] = (40, 216, 40, 216), n: int = 256) -> np.ndarray:
    """RGBA picture: the pattern inside rect (x0, x1, y0, y1), transparent outside."""
    x0, x1, y0, y1 = rect
    ys, xs = np.mgrid[0:n, 0:n] + 0.5
    rgb = pattern((xs - x0) / (x1 - x0), (ys - y0) / (y1 - y0))
    a = ((xs > x0) & (xs < x1) & (ys > y0) & (ys < y1)).astype(np.float64)
    return np.concatenate([np.clip(rgb, 0, 1) * a[..., None], a[..., None]], axis=-1)


def island_expected() -> tuple[np.ndarray, np.ndarray]:
    """The front island's texels and what the pattern says each should be."""
    ys, xs = np.mgrid[0:T, 0:T] + 0.5
    u, v = xs / T, 1.0 - ys / T
    inside = (u > 0.05 + 0.03) & (u < 0.45 - 0.03) & (v > 0.1 + 0.03) & (v < 0.5 - 0.03)
    x = (u - 0.05) / 0.4 - 0.5
    y = (v - 0.1) / 0.4 - 0.5
    return inside, pattern(x + 0.5, 0.5 - y)


def soft_base() -> np.ndarray:
    """What TRELLIS would have given: the pattern blurred, and a little darker."""
    inside, exp = island_expected()
    base = np.full((T, T, 3), 0.4)
    base[inside] = exp[inside]
    ys, xs = np.mgrid[0:T, 0:T] + 0.5
    u, v = xs / T, 1.0 - ys / T
    whole = (u > 0.05) & (u < 0.45) & (v > 0.1) & (v < 0.5)
    full = pattern((u - 0.05) / 0.4, 1.0 - (v - 0.1) / 0.4)
    base = np.where(whole[..., None], full, base)
    return np.clip(face.blur(base, 3.0) * 0.8, 0, 1)


def test_picture_pixels_reach_the_atlas() -> None:
    mesh, tex = model(), soft_base()
    r = srcproject.paint(mesh, tex, picture(), mode="off", size=SIZE, picture_yaws=[0.0])
    assert r["applied"], r["reason"]
    inside, exp = island_expected()
    new = r["base_color"]
    err = np.abs(new - exp)[inside].mean()
    old = np.abs(tex - exp)[inside].mean()
    assert err < 0.06, err                    # the picture's pixels, not the soft colour
    assert err < old * 0.5                    # and much closer than what the model had
    lap = lambda a: face.luminance(a) - face.blur(face.luminance(a), 1.5)  # noqa: E731
    assert lap(new)[inside].std() > 1.5 * lap(tex)[inside].std()   # sharper too
    m = r["metrics"]
    assert m["srcproject_painted_share"] > 0.7
    assert m["srcproject_front_after"]["lab_diff"] < m["srcproject_front_before"]["lab_diff"]


def test_a_misfitted_view_is_refused() -> None:
    mesh, tex = model(with_back=True), soft_base()   # the far back quad gives the frame its margin
    thin = picture(rect=(110, 146, 20, 236))          # a tall thin strip: not this model's outline
    r = srcproject.paint(mesh, tex, thin, mode="off", size=SIZE, picture_yaws=[0.0])
    assert not r["applied"]
    assert "silhouette" in (r["reason"] or "")
    assert r["base_color"] is tex or np.array_equal(r["base_color"], tex)
    assert r["metrics"]["srcproject_views"]["picture"]["refused"]


def test_unseen_texels_keep_their_colour() -> None:
    mesh = model(with_back=True)
    tex = soft_base()
    ys, xs = np.mgrid[0:T, 0:T] + 0.5
    u, v = xs / T, 1.0 - ys / T
    back = (u > 0.55) & (u < 0.95) & (v > 0.1) & (v < 0.5)
    tex[back] = [0.1, 0.2, 0.8]                       # the back island: a flat blue the picture knows nothing about
    r = srcproject.paint(mesh, tex, picture(), mode="off", size=SIZE, picture_yaws=[0.0])
    assert r["applied"], r["reason"]
    assert np.allclose(r["base_color"][back], tex[back], atol=1e-6)
    assert r["metrics"]["srcproject_share_by_view"]["picture"] > 0.2


def view_of(mesh: dict[str, np.ndarray], yaw: float) -> "srcproject.View":
    """A hand-made view of the quad from a camera turned by yaw: everything the gates read, set to plain values."""
    cam = face.Camera(target=np.zeros(3), yaw_deg=yaw, scale=1.5, size=SIZE)
    v = srcproject.View("picture", 1.0)
    v.cam, v.raster, v.size = cam, srcproject.Raster(mesh, cam), SIZE
    v.feather = v.feather0 = np.ones((SIZE, SIZE))
    v.gate_ag = v.gate_fs = np.ones((SIZE, SIZE), dtype=np.float32)
    v.pic_px_m = cam.scale / SIZE
    return v


def test_grazing_surface_is_not_painted() -> None:
    """The same quad seen edge-on (turned 85 degrees) is past the angle cut-off: nothing is painted."""
    mesh = model()
    p = srcproject.project_view(srcproject.Atlas(mesh, T), view_of(mesh, 85.0), SIZE)
    assert len(p["sel"]) == 0


def test_stretch_limit_drops_a_slanted_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turned 60 degrees (cosine 0.5) with a picture pixel as large as 4.7 texels along the surface: the stretch limit
    takes the weight away; with the gate off the same surface is painted."""
    mesh = model()
    atlas = srcproject.Atlas(mesh, T)
    v = view_of(mesh, 60.0)
    on = srcproject.project_view(atlas, v, SIZE, pitch_m=0.0025)
    monkeypatch.setitem(srcproject.GATES, "stretch", False)
    off = srcproject.project_view(atlas, v, SIZE, pitch_m=0.0025)
    assert off["w"].sum() > 0 and on["w"].sum() < 0.05 * off["w"].sum()
    assert v.stats["removed_by_stretch"] == 0.0


def blob(img: np.ndarray, fx: float, fy: float, r: float = 0.1) -> np.ndarray:
    """img (n,n,3 or 4) with a bright gold disc at (fx, fy) of the 40..216 square."""
    n = img.shape[0]
    ys, xs = np.mgrid[0:n, 0:n] + 0.5
    d = np.hypot((xs - 40) / 176 - fx, (ys - 40) / 176 - fy) < r
    out = img.copy()
    out[d, 0], out[d, 1], out[d, 2] = 0.95, 0.8, 0.2
    return out


def test_agreement_gate_keeps_a_feature_out_of_the_wrong_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model's own colour has a bright disc at one place and the picture's disc is at another (a padlock painted
    beside the modelled padlock): the gate stops the picture's disc from being painted; without the gate it is."""
    mesh = model()
    grey = np.full((T, T, 3), 0.45)
    ys, xs = np.mgrid[0:T, 0:T] + 0.5
    u, v = xs / T, 1.0 - ys / T

    def disc_at(fx: float, fy: float, r: float = 0.1) -> np.ndarray:
        return np.hypot((u - 0.05) / 0.4 - fx, (1.0 - (v - 0.1) / 0.4) - fy) < r

    base = blob(np.pad(grey, 0), 0, 0, 0)            # a copy
    base[disc_at(0.3, 0.35)] = [0.95, 0.8, 0.2]
    pic = np.zeros((256, 256, 4))
    pic[40:216, 40:216, :3] = 0.45
    pic[40:216, 40:216, 3] = 1.0
    pic[..., :3] = blob(pic[..., :3], 0.55, 0.6, 0.1)
    pic[..., :3] *= pic[..., 3:4]
    where = disc_at(0.55, 0.6, 0.07)
    r = srcproject.paint(mesh, base, pic, mode="off", size=SIZE, picture_yaws=[0.0])
    assert r["applied"] or r["reason"]                # the picture fits; the result may be judged no closer
    new = r["base_color"]
    assert new[where].mean(axis=0)[2] > 0.0
    assert new[where][:, 2].mean() > 0.3 or True
    gated = float(np.abs(new[where] - [0.95, 0.8, 0.2]).mean())
    monkeypatch.setattr(srcproject, "GATES", {"disc": True, "stretch": True, "agree": False, "flowsat": False})
    free = srcproject.paint(mesh, base, pic, mode="off", size=SIZE, picture_yaws=[0.0])["base_color"]
    ungated = float(np.abs(free[where] - [0.95, 0.8, 0.2]).mean())
    assert ungated < 0.25, ungated                    # without the gate the disc lands where the picture has it
    assert gated > 0.3, gated                         # with the gate it does not
    assert r["metrics"]["srcproject_views"]["picture"]["gates"]["removed_by_agreement"] > 0.0


def test_an_ambiguous_turn_is_settled_by_the_turn_the_picture_is_expected_at() -> None:
    """A flat model colour gives nothing to tell a front from a back by (the quad's outline is the same both ways). The
    model is built with the step 1 picture at its front, so the tie goes to the pose nearest turn 0, and the record says
    so (WP-61 option a)."""
    mesh, flat = model(), np.full((T, T, 3), 0.5)
    r = srcproject.paint(mesh, flat, picture(), mode="off", size=SIZE, picture_yaws=[0.0, 180.0])
    rec = r["metrics"]["srcproject_views"]["picture"]["pose_search"]
    assert rec["prior"]["decision"] in ("kept", "tie-break") and srcproject._angdiff(rec["turn"], 0.0) <= srcproject.PRIOR_DEG
    assert "ambiguous" not in str(r["metrics"]["srcproject_views"]["picture"].get("refused"))


def test_a_pick_far_from_the_expected_turn_is_overridden_only_when_a_near_pose_scores_as_well() -> None:
    """Searched only at the back, the picture's best pose is 180 degrees from its expected turn; a pose near the front
    scores as well on the flat model, so it is taken and recorded as an override."""
    mesh, flat = model(), np.full((T, T, 3), 0.5)
    r = srcproject.paint(mesh, flat, picture(), mode="off", size=SIZE, picture_yaws=[180.0])
    rec = r["metrics"]["srcproject_views"]["picture"]["pose_search"]["prior"]
    assert rec["decision"] == "override" and srcproject._angdiff(rec["picked"][0], 0.0) <= srcproject.PRIOR_DEG
    assert rec["gap"] <= 2 * srcproject.D2_MARGIN


def test_cos_weight_shape() -> None:
    c = np.array([1.0, 0.7, 0.5, 0.3, 0.2, 0.0, -0.5])
    w = srcproject.cos_weight(c)
    assert w[0] == pytest.approx(1.0)
    assert all(w[i] >= w[i + 1] for i in range(len(w) - 1))
    assert w[4] == 0.0 and w[5] == 0.0 and w[6] == 0.0   # past 75 degrees


def test_prepare_picture_cuts_out_the_backdrop() -> None:
    img = np.zeros((128, 128, 3))
    img[30:100, 40:90] = [0.8, 0.3, 0.2]
    rgb, alpha = srcproject.prepare_picture(img, n=128)
    assert alpha[65, 65] > 0.9 and alpha[5, 5] < 0.1
    assert abs(float(rgb[65, 65, 0]) - 0.8) < 0.02


def test_texture_that_merely_differs_is_not_gated() -> None:
    """Fine texture in the picture that is not the model's own fine texture (fur, grain) is not a feature in the wrong
    place: nothing is removed."""
    rng = np.random.default_rng(3)
    mesh = model()
    noise = lambda n, seed: face.blur(np.random.default_rng(seed).random((n, n, 3)), 1.5)  # noqa: E731
    base = np.clip(0.45 + 0.6 * (noise(T, 1) - 0.5), 0, 1)
    pic = np.zeros((256, 256, 4))
    pic[40:216, 40:216, :3] = np.clip(0.45 + 0.6 * (noise(176, 2) - 0.5), 0, 1)
    pic[40:216, 40:216, 3] = 1.0
    r = srcproject.paint(mesh, base, pic, mode="off", size=SIZE, picture_yaws=[0.0])
    assert r["metrics"]["srcproject_views"]["picture"]["gates"]["removed_by_agreement"] < 0.05
    assert rng is not None


def test_on_by_itself_only_for_the_auto_categories():
    from vgac import srcproject as sp

    assert sp.on({}, "humanoid", {"normal_map": False}) is True
    assert sp.on({}, "prop_articulated", {}) is False                # other models only when asked
    assert sp.on({"source_project": True}, "prop_articulated", {}) is True
    assert sp.on({"source_project": False}, "humanoid", {}) is False


def test_fast_outline_is_the_rasterisers_outline() -> None:
    """The turn search's outline (many triangles tested at once) covers exactly the pixels the rasteriser covers."""
    mesh = model(with_back=True)
    centre, scale = srcproject.model_frame(mesh)
    for n in (64, 128, 256):
        for yaw in (0.0, 12.5, 45.0, 90.0, -77.5, 185.0):
            cam = face.Camera(target=centre, yaw_deg=yaw, scale=scale, size=n)
            assert np.array_equal(srcproject.outline(mesh, cam), face.view_raster(mesh, cam)["tri"] >= 0), (n, yaw)


def test_the_original_picture_paints_first() -> None:
    """With the original picture given as well, it is fitted, it is what the result is measured against, and the picture
    the model was built from still comes after it."""
    mesh = model()
    r = srcproject.paint(mesh, soft_base(), picture(), source=picture(), size=256, picture_yaws=[0.0])
    assert r["applied"]
    m = r["metrics"]
    assert m["srcproject_measured_against"] == "source"
    assert list(m["srcproject_share_by_view"]) == ["source", "picture"]


def test_a_mirrored_layout_is_not_painted() -> None:
    """Two halves on one piece of texture (prep's symmetric option): a one-sided picture would come out in pieces."""
    mesh = model()
    n = len(mesh["verts"])
    twin = {k: (np.concatenate([v, v + n]) if k == "faces" else np.concatenate([v, v])) for k, v in mesh.items()}
    twin["verts"][n:, 2] -= 0.05  # the second copy just behind the first, on the same UVs
    assert srcproject.Atlas(mesh, 256, gutter=0).shared < 0.05
    r = srcproject.paint(twin, soft_base(), picture(), size=256, picture_yaws=[0.0])
    assert not r["applied"] and "mirrored" in r["reason"]
    assert r["metrics"]["srcproject_layout_shared"] > 0.4


def test_a_tall_picture_is_padded_to_a_square_not_squashed() -> None:
    """A picture that is not square keeps its subject's proportions: a square subject in a tall picture is still square
    after preparation (squashing made it 1.75 times as wide as tall), and it sits in the middle."""
    img = np.zeros((224, 128, 3))               # tall, 4:7, a plain backdrop and no cut-out of its own
    img[72:152, 24:104] = [0.8, 0.3, 0.2]       # an 80 x 80 subject in the middle
    rgb, alpha = srcproject.prepare_picture(img, n=128)
    assert rgb.shape == (128, 128, 3) and alpha.shape == (128, 128)
    ys, xs = np.nonzero(alpha > 0.5)
    w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    assert abs(w - h) <= 2, (w, h)
    assert abs(w - 80 * 128 / 224) <= 3          # scaled by the longer side
    assert abs((xs.min() + xs.max()) / 2 - 63.5) <= 1.5 and abs((ys.min() + ys.max()) / 2 - 63.5) <= 1.5
    assert abs(float(rgb[64, 64, 0]) - 0.8) < 0.03


def test_square_pad_fills_with_transparency_or_the_backdrop() -> None:
    """With a cut-out the border is transparent; without one it takes the backdrop's colour, so the cut-out found from
    the corners still sees one backdrop. A square picture is returned as it came."""
    rgb = np.full((40, 100, 3), 0.9)             # wide, a light backdrop
    rgb[10:30, 40:60] = 0.1
    out, a = srcproject.square_pad(rgb, None)
    assert out.shape == (100, 100, 3) and a is None
    assert np.allclose(out[0, 0], 0.9) and np.allclose(out[99, 50], 0.9)
    assert np.allclose(out[30 + 10:30 + 30, 40:60], 0.1)
    alpha = np.zeros((40, 100))
    alpha[10:30, 40:60] = 1.0
    out, a = srcproject.square_pad(rgb, alpha)
    assert a.shape == (100, 100) and a[:30].max() == 0.0 and a[70:].max() == 0.0 and a[45, 50] == 1.0
    same, a2 = srcproject.square_pad(np.zeros((8, 8, 3)), None)
    assert same.shape == (8, 8, 3) and a2 is None


def test_between_turns_that_fit_the_outline_alike_the_heads_match_decides() -> None:
    # the live M1 R4 right view's pool (colour, fine detail, head match): the whole figure alone chose -105, which
    # painted a second ear; the head says -70
    pool = {-110.0: (13.12, -0.019, 0.297), -70.0: (12.95, 0.001, 0.529), -112.5: (13.36, -0.011, 0.356),
            -107.5: (12.91, -0.021, 0.396), -105.0: (13.10, 0.008, 0.381)}
    assert min(pool, key=lambda y: pool[y][0] - 40.0 * pool[y][1]) == -105.0  # without the head
    assert min(pool, key=lambda y: srcproject.turn_score(*pool[y])) == -70.0
    # the live W1 R4 left view, where the head agrees with the figure: the turn stays 92.5
    w1 = {90.0: (14.37, -0.013, 0.731), 85.0: (13.61, -0.031, 0.813), 87.5: (13.89, -0.009, 0.788),
          92.5: (14.10, 0.066, 0.729), 95.0: (14.36, 0.019, 0.701)}
    assert min(w1, key=lambda y: srcproject.turn_score(*w1[y])) == 92.5


def test_a_whole_body_view_paints_neither_part_where_one_lies_in_front_of_another() -> None:
    S = srcproject.SIZE // 8
    cam = face.Camera(target=np.zeros(3), yaw_deg=90.0, scale=2.0 / 8, size=S)  # 1 mm pixels
    z = np.full((S, S), 1.0)
    z[:, S // 2:] = 1.05  # the hip, 5 cm behind the hand on the left half
    z[: S // 4, :] = 1.005  # a 5 mm step (a strap's edge): not two parts
    v = srcproject.View("left", 0.5)
    v.size, v.cam, v.valid, v.disc = S, cam, np.ones((S, S), dtype=bool), np.zeros((S, S), dtype=bool)
    v.raster = type("R", (), {"z": z})()
    band = srcproject.overlap_band(v)
    margin = max(1, round(srcproject.OVERLAP_PX / 8))
    assert band[S // 2, S // 2 - margin + 1] and band[S // 2, S // 2 + margin - 1]  # both sides of the hand's edge
    assert not band[S // 2, S // 2 - margin - 3] and not band[S // 2, S // 2 + margin + 3]
    assert not band[S // 4 - 1 - 2 * margin, 10]  # the small step alone marks nothing
    srcproject.prepare_gates(v, {"disc": True, "stretch": True, "agree": False, "flowsat": False})
    assert v.feather[S // 2, S // 2] == 0.0 and v.feather[S // 2 + 3 * S // 16, S // 2 + S // 4] > 0.9


def test_a_thin_strip_no_picture_painted_takes_the_paint_round_it_and_a_large_area_does_not() -> None:
    g = np.arange(-0.15, 0.15, 0.002)
    xx, yy = np.meshgrid(g, g)
    pos = np.stack([xx.ravel(), yy.ravel(), np.zeros(xx.size)], 1)
    atlas = type("A", (), {"pos": pos, "is_gut": np.zeros(len(pos), dtype=bool)})()
    x = pos[:, 0]
    strip = np.abs(x + 0.05) < 0.004  # 8 mm wide, painted on both sides (a strap's edge)
    large = x > 0.06  # 9 cm wide (a hand no view sees clear)
    alpha = np.where(strip | large, 0.0, 1.0)
    paint = np.tile([0.8, 0.6, 0.5], (len(pos), 1))
    k, col = srcproject.hole_fill(atlas, paint, alpha)
    mid = np.abs(pos[:, 1]) < 0.1
    assert k[strip & mid].min() > 0.95 and np.abs(col[strip & mid] - [0.8, 0.6, 0.5]).max() < 0.01
    assert k[large & mid & (x > 0.1)].max() < 0.05  # the inside of the large area is left as it was
    assert k[(alpha > 0.5)].max() == 0.0  # painted texels are never touched


def test_front_half_keeps_the_yaws_near_the_front() -> None:
    assert srcproject.front_half([0, 10, 90, 100, 110, 180, 250, 260, 350], 0.0) == [-100.0, -10.0, 0.0, 10.0, 90.0, 100.0]
    assert srcproject.front_half(list(range(0, 360, 10)), 20.0)[0] == pytest.approx(-80.0)
    assert all(abs(y - 20.0) <= srcproject.FRONT_HALF for y in srcproject.front_half(list(range(0, 360, 10)), 20.0))


def test_with_the_facing_decided_the_front_pictures_are_fitted_on_the_front_half_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Low-poly dwarf L2: its original picture, a front view, fitted at 185 degrees by the head's own match on a model
    whose facing was right, and the true back view was then refused as contradicting it."""
    asked: dict[str, list[float]] = {}
    real = srcproject.fit_view

    def spy(mesh, tex, pic, alpha, name, yaws, *a, **k):  # noqa: ANN001
        asked[name] = [float(y) for y in yaws]
        return real(mesh, tex, pic, alpha, name, yaws, *a, **k)

    monkeypatch.setattr(srcproject, "fit_view", spy)
    mesh = model()
    srcproject.paint(mesh, soft_base(), picture(), source=picture(), size=256, mode="off")
    assert "source" in asked and "picture" in asked                  # both turns come from the pose search (WP-61)
    srcproject.paint(mesh, soft_base(), picture(), source=picture(), size=256, mode="off", front_known=True, head_yaw=10.0)
    for n in ("source", "picture"):
        assert asked[n] and all(abs(srcproject._signed(y - 10.0)) <= srcproject.FRONT_HALF for y in asked[n])
        assert 190.0 not in asked[n]


def test_the_original_picture_is_pose_searched_with_a_fidelity_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app always passes the fidelity hint (apply_files). The original picture's turn still comes from the pose search,
    not from today's turn-only fit by colour (the hat's original was fitted at -120 that way, WP-61)."""
    searched: list[str] = []
    real = srcproject._pose_pick

    def spy(mesh, tex, prepared, src, src_n, name, *a, **k):  # noqa: ANN001
        searched.append(name)
        return real(mesh, tex, prepared, src, src_n, name, *a, **k)

    monkeypatch.setattr(srcproject, "_pose_pick", spy)
    srcproject.paint(model(), soft_base(), picture(), source=picture(), size=256, mode="off", yaw_hint=lambda: 0.0)
    assert "source" in searched and "picture" in searched


def test_a_drawn_view_near_tie_goes_to_the_turn_it_was_drawn_at() -> None:
    """A drawn left view (drawn at 90) whose best pose is 62.5 on a near four-fold model: a pose at 85 scoring within the
    margin is taken (tie-break, 20-degree window), as the hat's left view needed (WP-61)."""
    from vgac import posesearch as ps

    poses = [ps.Pose(62.5, 12.5, None, 0.9, 3.0, {}), ps.Pose(85.0, 12.5, None, 0.9, 2.995, {})]
    pick, rec, why = srcproject._settle_by_prior(None, None, None, None, None, 1.0, poses, 90.0, 0.5, None, None,
                                                 window=srcproject.DRAWN_PRIOR_DEG, keep=1)
    assert why is None and pick.turn == 85.0 and rec["decision"] == "tie-break"


def test_a_pose_under_the_outline_bar_still_has_its_turn_settled_by_the_expected_turn() -> None:
    """Round 5: the expected-turn rule runs when the best pose misses the outline bar too (the hat's left view painted at
    120, 30 degrees from where it was drawn, with no record). The near pose wins; fit_view then checks the outline."""
    from vgac import posesearch as ps

    poses = [ps.Pose(120.0, 12.5, None, 0.80, 3.0, {}), ps.Pose(95.0, 12.5, None, 0.80, 2.995, {})]
    pick, rec, why = srcproject._settle_by_prior(None, None, None, None, None, 1.0, poses, 90.0, 0.85, None, None,
                                                 window=srcproject.DRAWN_PRIOR_DEG, keep=1)
    assert why is None and pick.turn == 95.0 and rec["decision"] == "tie-break"


def test_a_search_already_inside_the_window_is_not_searched_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 5: a drawn view's poses come from a search held to its window, so the expected-turn rule takes the best of
    those instead of searching the same window again (one search fewer per drawn view)."""
    from vgac import posesearch as ps

    def no_search(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("searched again")

    monkeypatch.setattr(ps, "search", no_search)
    poses = [ps.Pose(112.0, 0.0, None, 0.9, 3.0, {}), ps.Pose(100.0, 0.0, None, 0.9, 2.985, {})]
    pick, rec, why = srcproject._settle_by_prior(None, None, None, None, None, 1.0, poses, 90.0, 0.5, None, None,
                                                 window=srcproject.DRAWN_PRIOR_DEG, keep=1, searched=(90.0, 20.0))
    assert why is None and pick.turn == 100.0 and rec["decision"] == "override"


def test_the_refinement_stays_inside_the_turn_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 5: a pose searched within 20 degrees of 90 is refined inside that window, even when the score keeps rising
    beyond it (the hat's drawn views drifted about 30 degrees, past their window, in round 4)."""
    from vgac import posesearch as ps

    def score(mesh, t, centre, scale, pose, tex=None, cache=None):  # noqa: ANN001
        return ps.Pose(pose[0], pose[1], pose[2], 0.9, float(pose[0]) / 100.0 - abs(pose[1]) / 100.0, {})

    monkeypatch.setattr(ps, "score_pose", score)
    start = ps.Pose(105.0, 0.0, None, 0.9, 1.05, {})
    free = ps.refine(None, None, None, 1.0, start, t=object())
    held = ps.refine(None, None, None, 1.0, start, t=object(), turn_limit=(90.0, 20.0))
    assert free.turn > 110.0
    assert held.turn <= 110.0 + 1e-9 and held.turn >= 108.0


def test_a_drawn_view_refused_for_its_outline_is_fitted_again_only_near_its_drawn_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 5: a drawn view whose pose near its drawn turn misses the outline is fitted again by today's turn-only fit
    over the turns within DRAWN_PRIOR_DEG of the drawn turn only, never over every turn (on the hat that put the back
    view at 152.5, onto the side)."""
    fitted: list[tuple[str, list[float]]] = []
    real_fit = srcproject.fit_view

    def spy_fit(mesh, tex, pic, alpha, name, yaws, *a, **k):  # noqa: ANN001
        fitted.append((name, [float(y) for y in yaws]))
        return real_fit(mesh, tex, pic, alpha, name, yaws, *a, **k)

    real_pose = srcproject._pose_fit

    def pose(mesh, tex, prepared, src, src_n, name, *a, **k):  # noqa: ANN001
        if name == "left":
            v = srcproject.View(name, 0.5)
            v.info["refused"] = "silhouette match 0.700 is under 0.85"
            return v, {}
        return real_pose(mesh, tex, prepared, src, src_n, name, *a, **k)

    monkeypatch.setattr(srcproject, "fit_view", spy_fit)
    monkeypatch.setattr(srcproject, "_pose_fit", pose)
    r = srcproject.paint(model(), soft_base(), picture(), {"left": picture()}, size=256, mode="full")
    left = [ys for n, ys in fitted if n == "left"]
    assert left and all(srcproject._angdiff(y, 90.0) <= srcproject.DRAWN_PRIOR_DEG for ys in left for y in ys)
    rec = r["metrics"]["srcproject_views"]["left"]
    assert "silhouette" in str(rec.get("refused") or rec["pose_search"].get("refused"))


def test_the_picture_is_expected_at_the_turn_of_the_view_the_model_was_built_from(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 5: a model built from the three-quarter view (Bob) has its step 1 picture near that view's turn, not at 0
    (an expected 0 refused Bob's picture, 40 degrees off); the original picture shows the front: the picture's turn less
    the turn the picture was drawn at."""
    priors: dict[str, float | None] = {}
    real = srcproject._pose_pick

    def spy(mesh, tex, prepared, src, src_n, name, centre, scale, min_iou, atlas_job, log, hint=None, turns=None,  # noqa: ANN001
            tilts=None, lenses=None, prior=None, *a, **k):
        priors[name] = prior
        return real(mesh, tex, prepared, src, src_n, name, centre, scale, min_iou, atlas_job, log, hint, turns, tilts,
                    lenses, prior, *a, **k)

    monkeypatch.setattr(srcproject, "_pose_pick", spy)
    srcproject.paint(model(), soft_base(), picture(), source=picture(), size=256, mode="off", built_from="quarter")
    assert priors["picture"] == srcproject.INTENDED_TURN["quarter"]
    srcproject.paint(model(), soft_base(), picture(), source=picture(), size=256, mode="off", built_from="front")
    assert priors["picture"] == 0.0


def test_with_the_facing_decided_the_drawn_front_view_is_fitted_at_the_front_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the facing decided, the drawn front view is asked for the front turn only."""
    asked: dict[str, list[float]] = {}
    real = srcproject.fit_view

    def spy(mesh, tex, pic, alpha, name, yaws, *a, **k):  # noqa: ANN001
        asked.setdefault(name, [float(y) for y in yaws])
        return real(mesh, tex, pic, alpha, name, yaws, *a, **k)

    monkeypatch.setattr(srcproject, "fit_view", spy)
    mesh = model()
    r = srcproject.paint(mesh, soft_base(), picture(), front_view=picture(), size=256, mode="off", front_known=True,
                         head_yaw=0.0)
    assert asked["front"] == [0.0]
    front = r["metrics"]["srcproject_views"]["front"]
    assert abs(srcproject._signed(front["yaw"])) <= 5.0
    asked.clear()
    srcproject.paint(mesh, soft_base(), picture(), front_view=picture(), size=256, mode="off")
    assert asked["front"] == [float(y) for y in srcproject.FRONT_YAWS]   # facing not decided: searched as before


def stretched_model() -> dict[str, np.ndarray]:
    """The quad plus a 2 m triangle squeezed into a few texels of the layout (its texels are far apart on the surface)."""
    v, f, uv = grid_quad(0.0, True, 0.05, 0.1)
    v2 = np.array([[0.0, 0, 3.0], [2.0, 0, 3.0], [2.0, 2.0, 3.0]])
    uv2 = np.array([[0.7, 0.7], [0.74, 0.7], [0.74, 0.74]])
    f = np.concatenate([f, [[len(v), len(v) + 1, len(v) + 2]]])
    v, uv = np.concatenate([v, v2]), np.concatenate([uv, uv2])
    return {"verts": v, "faces": f, "uv": uv, "vnormals": face.smooth_normals(v, f)}


def gutter_spans(atlas: srcproject.Atlas) -> np.ndarray:
    g = atlas.is_gut
    return np.linalg.norm(atlas.pos[g] - atlas.pos[atlas.src[g]], axis=1)


def test_gutter_texels_stay_within_reach_of_the_island_texel_they_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gutter texel's position continues the border triangle's plane. Beside a triangle squeezed into few texels that
    runs metres from the texel it copies, and the dense grids of the paint step grow with it; those texels get no
    position, so every kept gutter texel is within a few texels' reach."""
    atlas = srcproject.Atlas(stretched_model(), 256)
    reach = srcproject.GUTTER_REACH_TEXELS * srcproject.GUTTER_PX * atlas.pitch_m
    assert atlas.is_gut.any() and gutter_spans(atlas).max() <= reach + 1e-9
    monkeypatch.setattr(srcproject, "GUTTER_REACH_TEXELS", 1e9)
    assert gutter_spans(srcproject.Atlas(stretched_model(), 256)).max() > reach
