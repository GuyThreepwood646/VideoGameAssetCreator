"""Your own close-up of the face: the endpoints, the framing search, and the face pass using it instead of drawing one."""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from vgac import face
from vgac.stages import face_detail as fd

from test_api import _project_asset, client, fake_execute  # noqa: F401  (fixtures)
from test_face_sharper import REC, _SC, stubbed  # noqa: F401  (fixtures)


def _png_b64(size=(256, 256), colour=(180, 140, 120)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# --- the endpoints ----------------------------------------------------------------------------------------------


def test_add_read_and_remove_your_own_close_up(client):
    p, a = _project_asset(client)
    url = f"/api/assets/{a['id']}/face-closeup"
    assert client.get(url).json()["image"] is None
    r = client.post(url, json={"image": _png_b64()})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["image"].endswith("own/face_closeup.png") and st["size"] == [256, 256]
    assert client.get("/api/library/" + st["image"]).status_code == 200
    assert client.get(url).json()["image"] == st["image"]
    assert client.delete(url).json()["image"] is None


def test_a_close_up_is_refused_for_a_prop_and_when_tiny(client):
    _, prop = _project_asset(client, category="prop_rigid")
    assert client.post(f"/api/assets/{prop['id']}/face-closeup", json={"image": _png_b64()}).status_code == 400
    _, a = _project_asset(client)
    r = client.post(f"/api/assets/{a['id']}/face-closeup", json={"image": _png_b64((64, 64))})
    assert r.status_code == 400 and "too small" in r.json()["detail"]


# --- the framing search -----------------------------------------------------------------------------------------


def test_coarse_frame_finds_a_head_drawn_much_larger_and_off_centre():
    rng = np.random.default_rng(3)
    S = 512
    base = face.blur(rng.random((S, S)), 6.0)
    dst = np.repeat(base[..., None], 3, axis=2)
    region = np.zeros((S, S), dtype=bool)
    Y, X = np.mgrid[0:S, 0:S]
    region[(X - 256) ** 2 + (Y - 256) ** 2 < 120 ** 2] = True
    # the person's picture: the same face 1.6 times larger and moved right and down
    src = face.scale_image(dst, 1.6, 1.6, 60.0, 40.0, (256.0, 256.0))
    a = fd.coarse_frame(src, dst, region, work=256)
    back = face.apply_alignment(src, a)
    err = np.abs(back - dst)[region].mean()
    assert abs(a["sx"] - 1 / 1.6) < 0.06 and err < 0.03, (a, err)


# --- the pass with your own close-up -------------------------------------------------------------------------------


def _own(tmp_path: Path, monkeypatch) -> Path:
    own = tmp_path / "own_src.png"
    Image.new("RGB", (300, 400), (180, 140, 120)).save(own)
    monkeypatch.setattr(fd, "own_closeup_file", lambda sc, params: None if params.get("face_closeup_own") is False else own)

    def frame_own(glb, base, crop, own_, dest):
        Image.new("RGB", (64, 64)).save(dest)
        return {"ok": True, "reason": None, "score": 0.5, "coarse_score": 0.4, "alignment": {"sx": 0.7}}

    monkeypatch.setattr(fd, "frame_own", frame_own)
    monkeypatch.setattr(fd, "closeup_source", lambda crop, cu, px, dest: (Image.new("RGB", (8, 8)).save(dest), dest)[1])
    return own


def test_your_own_close_up_is_used_instead_of_drawing_one(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    _own(tmp_path, monkeypatch)
    fixed = tmp_path / "face" / "fixed.png"
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: (Image.new("RGB", (8, 8)).save(fixed), {
        "ok": True, "reason": None, "likeness": 0.98, "feature_move": 0.01, "registered": str(fixed), "source_face_px": 459.0})[1])
    res = asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard", "face_closeup_guided": False}))
    r = res["metrics"]["face_detail"]
    assert calls["redraw"] == []  # no close-up drawn, no sharpening job
    assert r["source"] == "closeup" and r["closeup"]["own"] and r["closeup"]["used"] and r["method"] == fd.METHOD_OWN
    assert calls["paint"][0]["fit_from_crop"] is True
    assert res["files"]["face_closeup"].name == fd.OWN_FILE and res["files"]["face_closeup"].is_file()
    assert fd.source_words(r).startswith("your own close-up")


def test_your_own_close_up_guides_a_drawn_close_up_first(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    _own(tmp_path, monkeypatch)
    guarded = []

    def guard(glb_, base, crop, cu, px, photo=False, guided=False):
        guarded.append(guided)
        return {"ok": True, "reason": None, "likeness": 0.95, "feature_move": 0.003, "registered": None, "source_face_px": 459.0}

    monkeypatch.setattr(fd, "closeup_guard", guard)
    res = asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard"}))
    r = res["metrics"]["face_detail"]
    assert [k for k, _ in calls["redraw"]] == ["face_closeup_guided"] and calls["references"] == [fd.GUIDE_FILE]
    assert guarded == [True] and "Picture 2" in calls["prompts"][0]
    assert r["source"] == "closeup" and r["closeup"]["guided"] and r["closeup"]["guide"] == "own" and r["method"] == fd.METHOD_GUIDED
    assert res["files"]["face_closeup"].name == "painted_head_closeup_guided.png" and res["files"]["face_guide"].is_file()
    assert "guided by your picture" in fd.source_words(r)


def test_a_refused_guided_close_up_falls_back_to_lining_up_your_own(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    _own(tmp_path, monkeypatch)
    fixed = tmp_path / "face" / "fixed.png"

    def guard(glb_, base, crop, cu, px, photo=False, guided=False):
        if guided:
            return {"ok": False, "reason": "likeness", "likeness": 0.8, "feature_move": 0.003, "registered": None}
        Image.new("RGB", (8, 8)).save(fixed)
        return {"ok": True, "reason": None, "likeness": 0.98, "feature_move": 0.01, "registered": str(fixed), "source_face_px": 459.0}

    monkeypatch.setattr(fd, "closeup_guard", guard)
    res = asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard"}))
    r = res["metrics"]["face_detail"]
    assert [k for k, _ in calls["redraw"]] == ["face_closeup_guided", "face_closeup_guided_2"]  # two guided tries
    assert r["closeup"]["own"] and r["closeup"]["used"] and r["method"] == fd.METHOD_OWN
    assert [t["prompt"] for t in r["closeup"]["tries"]] == ["guided", "guided"]


def test_a_guided_close_up_may_differ_more_in_hair_colour():
    m = {"feature_move": 0.003, "likeness": 0.95, "feature_score_min": 0.9, "hair_shift": 12.0, "skin_texture": 25.0}
    assert fd.closeup_verdict(m) == "style"
    assert fd.closeup_verdict(m, photo=True) == "hair_colour"
    assert fd.closeup_verdict(m, photo=True, guided=True) is None


def test_your_own_close_up_in_another_style_is_still_used_with_a_note(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    _own(tmp_path, monkeypatch)
    fixed = tmp_path / "face" / "fixed.png"
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: (Image.new("RGB", (8, 8)).save(fixed), {
        "ok": False, "reason": "likeness", "likeness": 0.7, "skin_texture": 40.0, "feature_move": 0.01,
        "registered": str(fixed), "source_face_px": 459.0})[1])
    res = asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard", "face_closeup_guided": False}))
    r = res["metrics"]["face_detail"]
    assert r["closeup"]["used"] and r["closeup"]["reason"] is None and calls["redraw"] == []
    assert fd.CLOSEUP_REASONS["likeness"] in r["closeup"]["notes"] and fd.CLOSEUP_REASONS["style"] in r["closeup"]["notes"]
    check = fd.face_checks(res["metrics"])[0]
    assert "likeness" not in check["vars"]["closeup_not_used"] and check["vars"]["closeup_notes"]


def test_your_own_close_up_whose_features_cannot_be_placed_falls_back_and_says_why(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    _own(tmp_path, monkeypatch)
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: {"ok": False, "reason": "likeness", "likeness": 0.5, "feature_move": 0.3,
                                                         "registered": None, "source_face_px": 459.0})
    res = asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard", "face_closeup_guided": False}))
    r = res["metrics"]["face_detail"]
    assert r["source"] == "painting" and r["closeup"]["own"] and r["closeup"]["used"] is False and r["closeup"]["reason"] == "no_features"
    assert [k for k, _ in calls["redraw"]] == ["face_redraw"]  # today's sharpening, no close-up drawn
    check = fd.face_checks(res["metrics"])[0]
    assert check["vars"]["closeup_own"] and check["vars"]["closeup_not_used"]


def test_a_close_up_that_cannot_be_lined_up_is_not_used(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    _own(tmp_path, monkeypatch)
    monkeypatch.setattr(fd, "frame_own", lambda *a: {"ok": False, "reason": "not_found", "score": 0.05})
    res = asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard", "face_closeup_guided": False}))
    r = res["metrics"]["face_detail"]
    assert r["closeup"]["reason"] == "not_found" and r["source"] == "painting"


def test_face_closeup_own_false_ignores_the_picture(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    _own(tmp_path, monkeypatch)
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: {"ok": False, "reason": "likeness", "likeness": 0.84, "source_face_px": 459.0})
    res = asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard", "face_closeup_own": False}))
    assert not res["metrics"]["face_detail"]["closeup"].get("own")
    assert [k for k, _ in calls["redraw"]][0] == "face_closeup"


def test_own_closeup_file_reads_the_assets_picture(tmp_path, monkeypatch, settings):
    from vgac import library

    a = SimpleNamespace(project_id="p1", id="a1")
    sc = SimpleNamespace(asset=a, settings=settings)
    assert fd.own_closeup_file(sc, {}) is None
    p = library.own_face_closeup("p1", "a1", settings)
    p.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(p)
    assert fd.own_closeup_file(sc, {}) == p and fd.own_closeup_file(sc, {"face_closeup_own": False}) is None


def test_a_refused_guided_close_up_without_your_own_falls_back_to_the_plain_close_up(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    guide = tmp_path / "compose_face.png"
    Image.new("RGB", (200, 260), (180, 140, 120)).save(guide)
    monkeypatch.setattr(fd, "compose_face_picture", lambda sc: guide)

    def guard(glb_, base, crop, cu, px, photo=False, guided=False):
        if guided:
            return {"ok": False, "reason": "likeness", "likeness": 0.39, "feature_move": 0.03, "registered": None}
        return {"ok": True, "reason": None, "likeness": 0.97, "feature_move": 0.005, "registered": None, "source_face_px": 459.0}

    monkeypatch.setattr(fd, "closeup_guard", guard)
    monkeypatch.setattr(fd, "closeup_source", lambda crop, cu, px, dest: (Image.new("RGB", (8, 8)).save(dest), dest)[1])
    # a small painted face (crop_px about 68 against 256 texels wanted): a plain close-up is wanted
    res = asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard"}))
    r = res["metrics"]["face_detail"]
    assert [k for k, _ in calls["redraw"]] == ["face_closeup_guided", "face_closeup_guided_2", "face_closeup"]
    assert r["source"] == "closeup" and r["method"] == fd.METHOD_CLOSEUP and r["closeup"]["guide"] == "compose"
    assert [t["prompt"] for t in r["closeup"]["guided_tries"]] == ["guided", "guided"]


def test_a_tight_crop_gets_a_margin_and_the_close_up_is_cut_back(tmp_path):
    assert fd.closeup_margin(688, guided=True) == round(540 / 688, 3)  # step 0.5's man: the head filled the crop
    assert fd.closeup_margin(519, guided=True) == 1.0  # the T-pose man: guided without a margin
    assert fd.closeup_margin(519, guided=False, photo=True) == fd.PLAIN_MARGIN
    assert fd.closeup_margin(437, guided=False) == 1.0  # a painted knight: as before
    assert fd.closeup_margin(2000, guided=True) == fd.MARGIN_MIN
    src = tmp_path / "crop.png"
    a = np.zeros((1024, 1024, 3), dtype=np.uint8)
    a[300:700, 400:600] = 220
    Image.fromarray(a).save(src)
    padded = fd.add_margin(src, 0.8, tmp_path / "padded.png")
    back = np.asarray(Image.open(fd.remove_margin(padded, 0.8, tmp_path / "back.png"))).astype(float)
    assert np.abs(back - a).mean() < 3.0  # the same framing again


def test_a_guided_close_up_passes_at_the_guided_likeness():
    m = {"feature_move": 0.004, "likeness": 0.89, "feature_score_min": 0.86, "hair_shift": 5.0}
    assert fd.closeup_verdict(m, photo=True) == "likeness"
    assert fd.closeup_verdict(m, photo=True, guided=True) is None
    assert fd.closeup_verdict({**m, "feature_score_min": 0.8}, photo=True, guided=True) == "features"


def test_a_face_painted_onto_the_wrong_part_of_the_head_fails_the_check():
    fdm = {"ran": True, "sharpness_after": 2.1, "feature_match_after": 0.79, "eyes_moved": 0.63}
    c = fd.face_checks({"face_detail": fdm})[0]
    assert not c["ok"] and c["vars"]["reason"] == "misplaced" and "wrong part of the head" in c["help"]
    assert c["fix"]["stage"] == "mesh"
    assert fd.face_checks({"face_detail": {**fdm, "eyes_moved": 0.12}})[0]["ok"]
