"""vgac/srcproject.py, the face region (docs/AFTER-TEST-CHANGES.md D2 items 5 to 8), on synthetic models (no Blender, no
card): side and back views stay off the face, contradicting turns are refused, unpainted face texels take a skin
tone, and the head is fitted on its own."""
from __future__ import annotations

import numpy as np

from vgac import face, srcproject

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
