"""vgac/srcproject.py, the face region (docs/AFTER-TEST-CHANGES.md D2 items 5 to 8), on synthetic models (no Blender, no
card): side and back views stay off the face, contradicting turns are refused, unpainted face texels take a skin
tone, and the head is fitted on its own."""
from __future__ import annotations

import numpy as np
import pytest

from vgac import face, facemarks, lipmarks, srcproject

from tests.test_srcproject import SIZE, T, model, picture, soft_base


def fitted(name: str, yaw: float = 0.0, **kw):  # noqa: ANN003, ANN201
    mesh = model()
    tex = soft_base()
    centre, scale = srcproject.model_frame(mesh)
    pic, alpha = srcproject.prepare_picture(picture(), SIZE)
    v = srcproject.fit_view(mesh, tex, pic, alpha, name, [yaw], centre, scale, size=SIZE, use_flow=False, **kw)
    return mesh, tex, v


def disc_zone(mesh, tex, r_px: float = 50.0) -> dict:  # noqa: ANN001
    """A face region by hand: a disc in the middle of a front picture of the quad."""
    centre, scale = srcproject.model_frame(mesh)
    cam = face.Camera(target=centre, yaw_deg=0.0, scale=scale, size=SIZE)
    view = face.render_view(mesh, tex, cam)
    ys, xs = np.mgrid[0:SIZE, 0:SIZE] + 0.5
    disc = (np.hypot(xs - SIZE / 2, ys - SIZE / 2) < r_px) & view["mask"]
    return {"found": True, "cam": cam, "z": view["raster"]["z"], "weight": disc.astype(np.float64), "view": view}


def test_zone_texels_are_the_region_seen_from_the_front() -> None:
    mesh = model(with_back=True)
    tex = soft_base()
    atlas = srcproject.Atlas(mesh, T)
    zone = disc_zone(mesh, tex)
    zt = srcproject.zone_texels(atlas, zone)
    front = atlas.pos[:, 2] > -0.5
    centre = front & (np.hypot(atlas.pos[:, 0], atlas.pos[:, 1]) < 0.15)
    assert zt["face"][centre].min() > 0.9
    assert zt["face"][~front].max() == 0.0  # the back quad is behind: never in the face region
    assert zt["face"][front & (np.hypot(atlas.pos[:, 0], atlas.pos[:, 1]) > 0.35)].max() == 0.0
    assert srcproject.zone_texels(atlas, None) is None
    assert srcproject.zone_texels(atlas, {"found": False}) is None


def test_side_and_back_views_stay_off_the_face_region() -> None:
    mesh, tex, v = fitted("left")
    assert v.ok
    srcproject.prepare_gates(v)
    atlas = srcproject.Atlas(mesh, T)
    free = srcproject.project_view(atlas, v)
    atlas.zone = srcproject.zone_texels(atlas, disc_zone(mesh, tex))
    kept_off = srcproject.project_view(atlas, v)
    w_free, w_off = np.zeros(atlas.idx.size), np.zeros(atlas.idx.size)
    w_free[free["sel"]], w_off[kept_off["sel"]] = free["w"], kept_off["w"]
    inside = atlas.zone["face"] > 0.99
    assert w_free[inside].min() > 0.01
    assert w_off[inside].max() == 0.0
    assert v.stats["removed_by_face_region"] > 0.0
    outside = atlas.zone["face"] == 0.0
    assert np.allclose(w_off[outside], w_free[outside])


def test_the_front_and_three_quarter_views_still_paint_the_face() -> None:
    for name in ("source", "quarter", "quarter2"):
        mesh, tex, v = fitted(name)
        srcproject.prepare_gates(v)
        atlas = srcproject.Atlas(mesh, T)
        atlas.zone = srcproject.zone_texels(atlas, disc_zone(mesh, tex))
        p = srcproject.project_view(atlas, v)
        w = np.zeros(atlas.idx.size)
        w[p["sel"]] = p["w"]
        assert w[atlas.zone["face"] > 0.99].min() > 0.01, name
        assert v.stats["removed_by_face_region"] == 0.0


def test_no_head_no_face_region() -> None:
    z = srcproject.face_zone(model(), soft_base())
    assert z["found"] is False and z["reason"] == "no_head"


def turned(**yaws: float) -> dict:
    out = {}
    for n, y in yaws.items():
        v = srcproject.View(n, 1.0)
        v.ok, v.info = True, {"name": n, "yaw": y}
        out[n] = v
    return out


def test_a_back_view_fitted_near_the_front_is_refused() -> None:
    f = turned(source=0.0, back=140.0)
    assert srcproject.refuse_contradicting_turns(f, 0.0) == {}
    f = turned(source=60.0, back=160.0)
    out = srcproject.refuse_contradicting_turns(f, *srcproject.front_turn(f)[:1])
    assert set(out) == {"back"} and not f["back"].ok
    assert "contradicts the front" in f["back"].info["refused"] and f["back"].info["turn_from_front_deg"] == 100.0


def test_left_and_right_on_one_side_refuse_the_one_that_belongs_on_the_other() -> None:
    f = turned(left=95.0, right=102.5)  # the stored M1-shorts run: both side views drawn facing the same way
    assert set(srcproject.refuse_contradicting_turns(f, 0.0)) == {"right"}
    assert f["left"].ok and not f["right"].ok
    f = turned(left=-80.0, right=-100.0)
    assert set(srcproject.refuse_contradicting_turns(f, 0.0)) == {"left"}
    f = turned(left=65.0, right=-105.0)  # turned with the model: judged against the front's own turn
    assert srcproject.refuse_contradicting_turns(f, -27.5) == {}
    f = turned(left=20.0, right=-90.0)
    assert set(srcproject.refuse_contradicting_turns(f, 0.0)) == {"left"}  # a side view seen from the front


def test_three_quarter_views_take_their_sides_from_the_built_from_picture() -> None:
    pic = turned(picture=-45.0)["picture"]
    pic.info["front_view"] = False
    f = turned(quarter=-42.5, quarter2=-20.0)  # the stored M1-clothed run: both drawn from the picture's side
    out = srcproject.refuse_contradicting_turns(f, 0.0, pic)
    assert set(out) == {"quarter2"} and f["quarter"].ok
    f = turned(quarter=40.0, quarter2=-30.0)  # no built-from three-quarter picture: the usual sides, nothing refused
    assert srcproject.refuse_contradicting_turns(f, 0.0) == {}


def test_the_front_turn_comes_from_the_first_frontal_picture() -> None:
    f = turned(source=332.5, front=-2.5, picture=40.0)
    assert srcproject.front_turn(f, 5.0) == (332.5, "source")
    f["source"].ok = False
    assert srcproject.front_turn(f, 5.0) == (-2.5, "front")
    f["front"].ok = False
    assert srcproject.front_turn(f, 5.0) == (5.0, "head")  # a three-quarter picture is not a front


SKIN = np.array([0.82, 0.62, 0.52])


def test_unpainted_face_texels_take_the_painted_skin_tone() -> None:
    mesh = model()
    atlas = srcproject.Atlas(mesh, T)
    x, y = atlas.pos[:, 0], atlas.pos[:, 1]
    base = np.full((T, T, 3), 0.45)
    flat = base.reshape(-1, 3)
    eye = (np.hypot(x - 0.12, y - 0.05) < 0.06)  # a dark feature of the model's own colour, right of the middle
    flat[atlas.idx[eye]] = (0.1, 0.08, 0.06)
    alpha = (x < 0.0).astype(np.float64)  # the picture painted the left half only, in skin
    paint = np.tile(SKIN, (atlas.idx.size, 1)) * (1 + 0.02 * np.sin(40 * y))[:, None]
    face_w = (np.hypot(x, y) < 0.3).astype(np.float64)
    hair = (y > 0.2) & (np.abs(x) < 0.1)  # the model's hair inside the face region: never filled
    zt = {"face": face_w, "keep": hair}
    info: dict = {}
    new = srcproject.finish(atlas, base, paint, alpha, zt, info).reshape(-1, 3)[atlas.idx]
    plain = srcproject.finish(atlas, base, paint, alpha).reshape(-1, 3)[atlas.idx]
    inner = ~atlas.is_gut
    assert np.abs(new[eye & inner] - SKIN).max() < 0.05  # the model's own eye is gone under skin
    assert np.abs(plain[eye & inner] - SKIN).max() > 0.3  # (it stayed before)
    assert np.allclose(new[hair & inner & (alpha < 0.5)], plain[hair & inner & (alpha < 0.5)])
    out = (face_w == 0) & inner
    assert np.allclose(new[out], plain[out])
    assert info["fill"][eye & inner].min() > 0.99 and info["fill"][alpha > 0.5].max() == 0.0


def test_skin_comes_from_the_model_when_the_face_was_not_painted() -> None:
    mesh = model()
    atlas = srcproject.Atlas(mesh, T)
    x, y = atlas.pos[:, 0], atlas.pos[:, 1]
    base = np.full((T, T, 3), 0.0)
    flat = base.reshape(-1, 3)
    flat[atlas.idx] = SKIN
    eye = np.hypot(x - 0.12, y - 0.05) < 0.05
    flat[atlas.idx[eye]] = (0.1, 0.08, 0.06)
    alpha = np.zeros(atlas.idx.size)
    zt = {"face": (np.hypot(x, y) < 0.3).astype(np.float64), "keep": np.zeros(atlas.idx.size, dtype=bool)}
    new = srcproject.finish(atlas, base, np.zeros((atlas.idx.size, 3)), alpha, zt).reshape(-1, 3)[atlas.idx]
    assert np.abs(new[eye & ~atlas.is_gut] - SKIN).max() < 0.05


def head_view(z: np.ndarray, img: np.ndarray, region: np.ndarray) -> tuple[dict, dict, face.Camera]:
    S = z.shape[0]
    ys, xs = np.where(region)
    info = {"region": region, "skin": list(face.to_lab(SKIN[None, None])[0, 0]), "nose": (S // 2, S // 2 + 10),
            "box": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())), "width_px": float(xs.max() - xs.min())}
    view = {"image": img, "mask": np.isfinite(z), "raster": {"z": z}}
    return view, info, face.Camera(target=np.zeros(3), yaw_deg=0.0, scale=0.25, size=S)


def test_glasses_and_hair_keep_their_colour() -> None:
    S = 256
    Y, X = np.mgrid[0:S, 0:S] + 0.5
    head = np.hypot(X - S / 2, (Y - S / 2) / 1.3) < 100
    z = np.where(head, 0.0, np.inf)
    lens = np.hypot(X - 95, Y - 120) < 22  # a solid lens 1 cm in front of the face
    z = np.where(lens, -0.01, z)
    region = head & (Y > 70)
    img = np.where(head[..., None], SKIN, 0.25)
    img = np.where((head & (Y <= 70))[..., None], np.array([0.15, 0.1, 0.05]), img)  # dark hair on top
    view, info, cam = head_view(z, img, region)
    keep = srcproject.never_skin(view, info, cam)
    assert keep[lens].mean() > 0.95
    rim = (np.hypot(X - 95, Y - 120) > 20) & (np.hypot(X - 95, Y - 120) < 24)
    assert keep[rim].mean() > 0.95
    assert keep[head & (Y <= 60)].mean() > 0.95  # the hair
    other_eye = np.hypot(X - 161, Y - 120) < 15
    assert keep[other_eye].mean() == 0.0  # bare skin around the other eye is free to fill


def head_from(mesh, tex, rect=(40, 216, 40, 216)):  # noqa: ANN001, ANN201
    """A stand-in head view: the picture fitted from the front, named as the head view, with its own zone picture."""
    centre, scale = srcproject.model_frame(mesh)
    pic, alpha = srcproject.prepare_picture(picture(rect), SIZE)
    v = srcproject.fit_view(mesh, tex, pic, alpha, srcproject.HEAD_VIEW, [0.0], centre, scale, size=SIZE, use_flow=False)
    v.zone_img = np.zeros((SIZE, SIZE))
    v.zone_img[: SIZE // 2] = 1.0  # the upper half only ("above the neck")
    srcproject.prepare_gates(v, {"disc": True, "stretch": True, "agree": False, "flowsat": False})
    return v


def test_a_view_paints_inside_its_own_zone_only() -> None:
    mesh, tex = model(), soft_base()
    v = head_from(mesh, tex)
    atlas = srcproject.Atlas(mesh, T)
    p = srcproject.project_view(atlas, v)
    y = atlas.pos[p["sel"], 1]
    assert y.min() > -0.05 and (y > 0.2).any()


def test_the_head_view_comes_first_even_when_the_whole_body_fit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    mesh, tex = model(), soft_base()
    hv = head_from(mesh, tex)
    rec = {"name": "head", "picture": "source", "how": "eyes and mouth"}
    hv.info = rec
    monkeypatch.setattr(srcproject, "face_zone", lambda *a, **k: {"found": False, "reason": "test"})
    monkeypatch.setattr(srcproject, "fit_head", lambda *a, **k: (hv, rec))
    # the body fits: the head view is first, the body's original after it, the built-from picture after that
    r = srcproject.paint(mesh, tex, picture(), source=picture(), size=SIZE, picture_yaws=[0.0])
    assert r["applied"]
    assert list(r["metrics"]["srcproject_share_by_view"]) == ["head", "source", "picture"]
    assert r["metrics"]["srcproject_measured_against"] == "source"
    assert r["metrics"]["srcproject_views"]["head"]["picture"] == "source"
    # every whole-body fit refused (an outline match nothing reaches): the head alone still paints
    r = srcproject.paint(mesh, tex, picture(), size=SIZE, picture_yaws=[0.0], min_iou=1.01)
    assert r["metrics"]["srcproject_views"]["picture"].get("refused")
    assert list(r["metrics"]["srcproject_share_by_view"]) == ["head"]
    assert r["metrics"]["srcproject_share_by_view"]["head"] > 0.05


def test_no_face_no_head_view() -> None:
    v, rec = srcproject.fit_head(model(), {"found": False, "reason": "no_head"}, [("source", picture())], 0.001)
    assert v is None and "not found" in rec["refused"]


def test_the_local_finder_answers_without_a_card(monkeypatch: pytest.MonkeyPatch) -> None:
    pts = {"eyes": [(60.0, 40.0), (40.0, 40.0)], "mouth": (50.0, 70.0), "left": (42.0, 70.0), "right": (58.0, 70.0)}
    monkeypatch.setattr(lipmarks, "available", lambda: True)
    monkeypatch.setattr(lipmarks, "find", lambda img: pts)
    info = {"nose": (50, 55), "width_px": 60.0}
    img = np.zeros((100, 100, 3))
    assert facemarks.find(img, info) is None  # nothing set: the darkest-spot finder is used, as before
    tok = facemarks.use_local()
    try:
        assert facemarks.active()
        m = facemarks.find(img, info)
    finally:
        facemarks.done_local(tok)
    assert m["how"] == "mediapipe" and m["eyes"] == [(40.0, 40.0), (60.0, 40.0)] and m["mouth_corners"] == [(42.0, 70.0), (58.0, 70.0)]
    assert not facemarks.active()
    monkeypatch.setattr(lipmarks, "find", lambda img: {**pts, "mouth": (50.0, 20.0)})  # a mouth above the nose: not the model's
    tok = facemarks.use_local()
    try:
        assert facemarks.find(img, info) is None
    finally:
        facemarks.done_local(tok)


def test_a_failed_upscale_is_said_and_shown_as_a_warning() -> None:
    from vgac.stages import prep

    fail = {"upscaled": [], "failed": {k: "All connection attempts failed" for k in ("picture", "source", "left")}}
    note = srcproject.upscale_note(fail)
    assert note.startswith("3 of 3 pictures could not be made sharper") and "smaller pictures" in note
    c = prep.upscale_check(fail)
    assert c["passed"] is False and c["severity"] == "warn" and c["help"] == note
    assert srcproject.upscale_note({"upscaled": [], "skipped": "no card to draw on"}).startswith("The pictures were not")
    assert prep.upscale_check({"upscaled": [], "skipped": "no card to draw on"}) is None  # noted, not warned
    assert prep.upscale_check({"upscaled": ["picture", "source"], "model": ["x"]}) is None
    assert prep.upscale_check(None) is None
    checks = prep.prep_checks({}, {}, {"srcproject_upscale": fail}, "humanoid", None)
    assert any(ch["name"] == "pictures_sharpened" and not ch["passed"] for ch in checks)


def test_the_fill_stays_inside_its_own_area_when_there_is_one() -> None:
    mesh = model()
    atlas = srcproject.Atlas(mesh, T)
    x, y = atlas.pos[:, 0], atlas.pos[:, 1]
    base = np.full((T, T, 3), 0.45)
    alpha = (x < 0.0).astype(np.float64)
    paint = np.tile(SKIN, (atlas.idx.size, 1))
    face_w = (np.hypot(x, y) < 0.3).astype(np.float64)
    box = ((np.abs(x - 0.1) < 0.08) & (np.abs(y) < 0.08)).astype(np.float64)
    info: dict = {}
    zt = {"face": face_w, "keep": np.zeros(atlas.idx.size, dtype=bool), "fill": box}
    new = srcproject.finish(atlas, base, paint, alpha, zt, info).reshape(-1, 3)[atlas.idx]
    plain = srcproject.finish(atlas, base, paint, alpha).reshape(-1, 3)[atlas.idx]
    inner = ~atlas.is_gut
    assert np.abs(new[(box > 0) & inner] - SKIN).max() < 0.05
    rim = (face_w > 0) & (box == 0) & (alpha < 0.5) & inner  # the rest of the face keeps the old colour, as before
    assert np.allclose(new[rim], plain[rim])


def test_one_scale_point_fit_and_where_it_moves_points() -> None:
    src = np.array([[400.0, 450.0], [600.0, 450.0], [500.0, 640.0]])
    dst = (src - 512.0) * 1.1 + 512.0 + np.array([12.0, -30.0])
    a = srcproject.fit_points(src, dst, 1024)
    assert abs(a["sx"] - 1.1) < 1e-3 and a["sx"] == a["sy"] and a["residual_px"] < 0.01
    assert np.allclose(srcproject.moved_points(src, a), dst, atol=0.01)
    img = np.zeros((1024, 1024))
    img[450, 400] = 1.0  # face.apply_alignment puts the pixel where moved_points says
    out = face.apply_alignment(img, a)
    y, x = np.unravel_index(int(np.argmax(out)), out.shape)
    assert abs(x + 0.5 - dst[0, 0]) <= 1.0 and abs(y + 0.5 - dst[0, 1]) <= 1.0


def test_a_squashed_head_fit_is_fitted_again_with_one_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    pts = np.array([[400.0, 450.0], [600.0, 450.0], [500.0, 640.0]])
    monkeypatch.setattr(srcproject, "head_points", lambda img: pts)
    even = {"sx": 0.9, "sy": 0.92, "tx": 0.0, "ty": 0.0, "centre": (512.0, 512.0), "score": 0.5}
    assert srcproject.limit_squash(None, None, even, 1024) is even
    squashed = {**even, "sx": 0.87, "sy": 0.81}
    a = srcproject.limit_squash(None, None, squashed, 1024)
    assert a["sx"] == a["sy"] and "one scale" in a["squash"] and a["score"] == 0.5


def test_no_body_fit_paints_where_a_head_view_paints() -> None:
    N = 6
    atlas = type("A", (), {"idx": np.arange(N)})()
    img = lambda c: np.full((4, 4, 3), c)  # noqa: E731
    sp = np.full((N, 2), 2.0)
    head = {"sel": np.arange(N), "sp": sp, "w": np.array([0.5, 0.2, 0.08, 0.05, 0.0, 0.0])}
    side = {"sel": np.arange(N), "sp": sp, "w": np.array([0.3, 0.3, 0.3, 0.3, 0.3, 0.3])}
    body = {"sel": np.arange(N), "sp": sp, "w": np.full(N, 0.6)}
    paint, alpha, dom = srcproject.blend(atlas, [head, side, body], [img(0.9), img(0.5), img(0.1)], 3, [True, True, False])
    assert dom[0] == 0 and dom[1] == 0  # the front head view where it sees well
    assert paint[0, 0] > 0.85 and (dom != 2).all()  # no body colour under the head views (they blend among themselves)
    assert paint[:, 0].min() >= 0.5 - 1e-6
    assert dom[4] == 1 and dom[5] == 1  # the side head view where the front does not reach
    assert np.allclose(paint[4:], 0.5, atol=0.01)  # and the body stays off under it too
    plain, _, dom2 = srcproject.blend(atlas, [head, body], [img(0.9), img(0.1)], 2, [True, False])
    assert dom2[5] == 1 and dom2[0] == 0  # no head paint at all: the body paints there


def test_a_three_quarter_head_is_framed_from_the_heads_turn_to_its_bodys_side() -> None:
    v = srcproject.View("quarter", 0.5)
    v.info = {"yaw": 47.5, "refused": "silhouette match 0.827 is under 0.85"}
    assert srcproject.side_head_yaw("quarter", v, 0.0) == (srcproject.SIDE_HEAD_YAW, "the side of its whole-body fit")
    v.info = {"yaw": -45.0}  # the stored W1-clothed run: 12 degrees from the turned face, still framed 40 round
    assert srcproject.side_head_yaw("quarter2", v, -33.4)[0] == -33.4 - srcproject.SIDE_HEAD_YAW
    v.info = {"yaw": -42.5}  # drawn on the other side from its usual one: framed on the side it fits
    assert srcproject.side_head_yaw("quarter", v, 0.0)[0] == -srcproject.SIDE_HEAD_YAW
    v.info = {"yaw": 20.0, "refused": "its turn contradicts the front: ..."}
    assert srcproject.side_head_yaw("quarter2", v, 0.0)[0] is None
    assert srcproject.side_head_yaw("quarter2", None, 5.0)[0] == 5.0 - srcproject.SIDE_HEAD_YAW


def test_one_head_view_per_side_and_within_its_turn() -> None:
    assert srcproject.one_head_per_side({"quarter": -42.5, "quarter2": -20.0}, -1.4) == {
        "quarter2": "both three-quarter views frame the head from the same side; this one is nearer the front"}
    assert srcproject.one_head_per_side({"quarter": 40.0, "quarter2": -30.0}, 0.0) == {}
    v = srcproject.View("quarter", 0.5)
    v.info = {"yaw": -30.0}
    assert srcproject.side_head_yaw("quarter", v, -180.0)[0] is None  # 150 degrees from the face: not a three-quarter view
