"""Face detail pass, sharper: the close-up source picture (item A) and the face texel density setting (item B)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from vgac import face, guidance, jobs
from vgac.stages import export
from vgac.stages import face_detail as fd

from test_api import _project_asset, client, fake_execute, wait_job  # noqa: F401  (fixtures)

# --- B. the setting's choice per platform budget ----------------------------------------------------------------


def test_automatic_is_standard_on_a_1024_budget():
    p = fd.plan_density("auto", 1024, 1024, 459)
    assert p["level"] == "standard" and p["target"] == 128 and p["ship"] == 1024 and p["why"] == "small_budget"


def test_automatic_is_high_on_2048_when_the_source_has_the_detail():
    p = fd.plan_density("auto", 2048, 2048, 459)
    assert p["level"] == "high" and p["target"] == 459 and p["ship"] == 2048
    # the painting alone (68 px across the face) cannot fill more than Standard gives: Standard
    p = fd.plan_density("auto", 2048, 2048, 68)
    assert p["level"] == "standard" and p["target"] == 256 and p["why"] == "small_source"
    # a source larger than 512 px across the face is capped at 512
    assert fd.plan_density("auto", 2048, 2048, 900)["target"] == 512


def test_high_ships_the_base_colour_at_4096_where_the_budget_allows():
    p = fd.plan_density("high", 4096, 2048, 459)
    assert p["ship"] == 4096 and p["level"] == "high"
    assert p["target"] == 512  # Standard at 4096 is already an eighth of 4096; High is never under it
    # an atlas already at 4096 stays at 4096
    assert fd.plan_density("high", 4096, 4096, 459)["ship"] == 4096
    # a 2048 budget never ships 4096
    assert fd.plan_density("high", 2048, 2048, 459)["ship"] == 2048


def test_standard_is_todays_behaviour_and_the_choice_is_read_safely():
    for budget, atlas in ((1024, 1024), (2048, 2048), (4096, 4096)):
        p = fd.plan_density("standard", budget, atlas, 459)
        assert p["level"] == "standard" and p["target"] == fd.target_texels(atlas) and p["ship"] == atlas
    assert fd.density_choice({"face_density": "high"}) == "high"
    assert fd.density_choice({"face_density": "bogus"}) == "auto" and fd.density_choice({}) == "auto"


def test_high_chosen_on_a_phone_budget_asks_for_what_the_source_allows():
    p = fd.plan_density("high", 1024, 1024, 300)
    assert p["level"] == "high" and p["target"] == 300 and p["ship"] == 1024


def test_the_cannot_meet_high_sentence():
    plan = fd.plan_density("high", 1024, 1024, 459)
    note = fd.density_note(plan, 212, 0.88)
    assert note is not None
    assert "about 459 texels" in note and "1024 px texture budget allowed 212" in note and "12 percent" in note
    assert "88 percent" in note
    # met (within 8 percent): no sentence; Standard: never a sentence
    assert fd.density_note(plan, 440, 0.95) is None
    assert fd.density_note(fd.plan_density("standard", 2048, 2048, 68), 100, None) is None


def test_the_check_sentence_names_the_source_and_the_density_note():
    m = {"face_detail": {"ran": True, "sharpness_after": 1.5, "sharpness_before": 0.8, "feature_match_after": 0.77,
                         "eye_colour_shift": 1.4, "face_texels_after": 212, "source": "closeup",
                         "closeup": {"wanted": True, "used": True},
                         "density": {"level": "high", "note": fd.density_note(fd.plan_density("high", 1024, 1024, 459), 212, 0.9)}}}
    c = guidance.describe_check(fd.face_checks(m)[0])
    assert c["ok"]
    assert "close-up of the painted face" in c["help"] and "212 texels (texture pixels) across the face" in c["help"]
    assert "budget allowed 212" in c["help"]
    m["face_detail"].update(source="painting", closeup={"wanted": True, "used": False, "reason": "likeness"}, density={})
    c = guidance.describe_check(fd.face_checks(m)[0])
    assert "painted from the painted picture" in c["help"]
    assert "A close-up was drawn but not used: it did not look enough like the painted face." in c["help"]


def test_new_knobs_are_declared_for_humanoids():
    from vgac import reference

    spec = {p["name"]: p for p in reference.STAGE_SCHEMAS["prep"]}
    assert spec["face_density"]["options"] == ["auto", "standard", "high"] and spec["face_density"]["default"] == "auto"
    assert spec["face_density"]["label"].startswith("Face texel density (face sharpness)")
    assert spec["face_closeup"]["categories"] == ["humanoid"]
    assert reference.validate_params("prep", {"face_density": "high"}, "humanoid")["face_density"] == "high"
    with pytest.raises(ValueError):
        reference.validate_params("prep", {"face_density": "ultra"}, "humanoid")
    assert "face_density" not in reference.validate_params("prep", {"face_density": "high"}, "quadruped")


# --- A. the threshold and skip rule -----------------------------------------------------------------------------


def test_a_close_up_is_drawn_only_when_the_painting_is_smaller_than_the_texture_holds():
    assert fd.source_face_px(459.0, 152.4) == 68.3  # the knight: 68 of the concept's pixels across her face
    assert fd.closeup_wanted({}, 68.3, 256)
    assert fd.closeup_wanted({}, 300, 459)  # High wants more than a mid-size painting has
    assert not fd.closeup_wanted({}, 520, 459)  # an uploaded portrait: the crop itself is the source
    assert not fd.closeup_wanted({"face_closeup": False}, 68.3, 256)  # the option off


# --- A. the drift guard -----------------------------------------------------------------------------------------


def _face_picture(seed: int, size: int = 256) -> np.ndarray:
    """A face-like picture: skin, two dark eyes, a mouth line and random blobs that stand for its shading."""
    rng = np.random.default_rng(seed)
    img = np.ones((size, size, 3)) * np.array([0.85, 0.7, 0.6])
    Y, X = np.mgrid[0:size, 0:size] / size
    for _ in range(14):
        cx, cy, r, d = rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8), rng.uniform(0.03, 0.12), rng.uniform(-0.25, 0.25)
        img += d * np.exp(-((X - cx) ** 2 + (Y - cy) ** 2) / (2 * r * r))[..., None]
    for ex in (0.38, 0.62):
        img[np.hypot(X - ex, Y - 0.42) < 0.035] = 0.1
    img[(np.abs(Y - 0.68) < 0.008) & (np.abs(X - 0.5) < 0.09)] = 0.3
    return np.clip(img, 0, 1)


def test_likeness_separates_a_sharper_redraw_from_a_different_face():
    original = _face_picture(1)
    small = face.resize(original, 48)
    crop = np.asarray(Image.fromarray(face.to_uint8(small)).resize((256, 256), Image.BILINEAR), dtype=np.float64) / 255.0
    region = np.zeros((256, 256), dtype=bool)
    region[40:216, 40:216] = True
    good = np.clip(original + np.random.default_rng(5).normal(0, 0.03, original.shape), 0, 1)  # sharp, fine detail added
    other = _face_picture(2)  # same eyes and mouth places, different face shading
    assert face.likeness(crop, good, region, 48) >= fd.LIKENESS_MIN
    assert face.likeness(crop, other, region, 48) < fd.LIKENESS_MIN


def test_the_verdict_names_the_first_thing_that_differs():
    ok = {"likeness": 0.98, "feature_move": 0.013, "eye_colour_shift": 3.4, "skin_shift": 3.8, "hair_shift": 3.5, "fit_residual": 0.11}
    assert fd.closeup_verdict(ok) is None
    assert fd.closeup_verdict({**ok, "likeness": 0.86}) == "likeness"
    assert fd.closeup_verdict({**ok, "feature_move": 0.1}) == "moved"
    assert fd.closeup_verdict({**ok, "eye_colour_shift": 13.0}) == "eye_colour"
    assert fd.closeup_verdict({**ok, "skin_shift": 9.0}) == "skin_colour"
    assert fd.closeup_verdict({**ok, "hair_shift": 54.0}) == "hair_colour"
    assert fd.closeup_verdict({**ok, "fit_residual": 0.2}) == "off_model"
    assert fd.closeup_verdict({**ok, "fit_residual": None, "hair_shift": None}) is None  # not measurable is not a fault
    assert fd.closeup_verdict({"likeness": 0.99}) == "no_features"
    assert set(fd.CLOSEUP_REASONS) >= {"likeness", "moved", "eye_colour", "skin_colour", "hair_colour", "off_model", "no_features", "error"}


def test_the_close_up_source_keeps_the_paintings_colour_and_takes_the_finer_detail(tmp_path):
    crop = _face_picture(1)
    sharp = np.clip(crop + np.random.default_rng(3).normal(0, 0.05, crop.shape), 0, 1)
    tinted = np.clip(sharp * np.array([0.9, 1.05, 1.0]), 0, 1)  # the close-up drifted a little in hue
    face.save_rgb(crop, tmp_path / "crop.png")
    face.save_rgb(tinted, tmp_path / "cu.png")
    out = face.read_rgb(fd.closeup_source(tmp_path / "crop.png", tmp_path / "cu.png", 128, tmp_path / "m.png"), 1024)
    big = lambda a: face.blur(a, 12)  # noqa: E731
    assert np.abs(big(out) - big(face.read_rgb(tmp_path / "crop.png", 1024))).mean() < 0.01  # broad colour: the painting's
    fine = lambda a: a - face.blur(a, 3)  # noqa: E731
    assert np.corrcoef(fine(out).ravel(), fine(face.read_rgb(tmp_path / "cu.png", 1024)).ravel())[0, 1] > 0.8  # fine: the close-up's


# --- A. the fallback, through run() with the picture jobs and the heavy steps stubbed -----------------------------


class _Client:
    async def vram_summary(self):
        return {"reachable": True}


class _SC:
    def __init__(self, tmp: Path, budget: int):
        self.dir = tmp
        self.asset = SimpleNamespace(category="humanoid")
        self.budget = {"texture": budget, "triangles": 20000}
        self.version = SimpleNamespace(id="v" * 36, params={})
        self.params: dict = {}
        self.models: list = []
        self.cutout = tmp / "cutout.png"
        Image.new("RGBA", (64, 64), (200, 150, 120, 255)).save(self.cutout)

    def lineage_file(self, key, stages):
        return self.cutout if key == "cutout" else None

    def progress(self, *a):
        pass

    def set_params(self, **kw):
        self.params.update(kw)

    def use_model(self, m):
        self.models.append(m)

    def client(self, inst):
        return _Client()

    async def blender(self, *a, **k):
        raise AssertionError("no relayout expected")


@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    calls: dict = {"redraw": [], "paint": []}
    base = tmp_path / "base_color.png"
    Image.new("RGB", (2048, 2048), (128, 100, 90)).save(base)
    glb = tmp_path / "prep.glb"
    glb.write_bytes(b"glb")

    def inspect(glb_, base_, cutout, out_dir):
        Image.new("RGB", (64, 64)).save(out_dir / "painted_head.png")
        # the face already has enough texels for Standard: no relayout in these tests
        return {"found": True, "texture_size": 2048, "face_width_px": 459.0, "painted_head_px": 152.4, "face_width_texels": 300,
                "face_triangles": []}

    async def redraw(sc, instance, crop, seed, dest, prompt=fd.REDRAW_PROMPT, key="face_redraw", message="", reference=None, margin=1.0):
        calls["redraw"].append((key, seed))
        calls.setdefault("references", []).append(None if reference is None else Path(reference).name)
        calls.setdefault("prompts", []).append(prompt)
        Image.new("RGB", (64, 64)).save(dest)
        return dest

    def fit_and_paint(work_glb, base_c, picture, before_glb, before_tex, crop, out_dir, strength, sides=None, colour_source=None,
                      require_landmarks=False, fit_from_crop=False):
        calls["paint"].append({"picture": Path(picture).name, "colour": None if colour_source is None else Path(colour_source).name,
                               "require": require_landmarks, "fit_from_crop": fit_from_crop})
        out = out_dir / "base_color_face.png"
        Image.new("RGB", (8, 8)).save(out)
        sheet = out_dir / "face_before_after.png"
        Image.new("RGB", (8, 8)).save(sheet)
        return {"ok": True, "base_color": out, "sheet": sheet, "metrics": {"face_texels_after": 300, "sharpness_after": 1.4}}

    monkeypatch.setattr(fd, "inspect", inspect)
    monkeypatch.setattr(fd, "redraw", redraw)
    monkeypatch.setattr(fd, "fit_and_paint", fit_and_paint)
    monkeypatch.setattr(fd, "redraw_kept", lambda a, b: 0.9)
    monkeypatch.setattr(fd, "image_instance", lambda sc: "b")
    monkeypatch.setattr(fd, "side_pictures", lambda sc: {})
    monkeypatch.setattr(fd.imaging, "glb_replace_images", lambda *a, **k: None)
    return calls, glb, {"base_color": base}


REC = {"surface": {"texture_filter": "smooth", "shading": "smooth"}, "mesh": {"texture": 2048}}


def test_a_close_up_that_fails_the_guard_falls_back_to_the_painting_and_says_why(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: {"ok": False, "reason": "likeness", "likeness": 0.84, "source_face_px": 459.0})
    sc = _SC(tmp_path, 2048)
    res = asyncio.run(fd.run(sc, glb, maps, REC, {"face_density": "standard"}))
    r = res["metrics"]["face_detail"]
    assert r["ran"] and r["source"] == "painting" and r["closeup"]["used"] is False and r["closeup"]["reason"] == "likeness"
    # the close-up is drawn again twice with new seeds (item 16), then today's sharpening
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_closeup_2", "face_closeup_3", "face_redraw"]
    assert calls["paint"][0]["colour"] is None and calls["paint"][0]["picture"] == "painted_head_sharp.png"
    # both seeds were written on the version at once, so a Stop and Resume redraws the same pictures
    assert {"face_closeup_seed", "face_seed"} <= set(sc.params)


def test_a_close_up_that_passes_is_the_source_and_the_sharpening_redraw_is_not_run(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: {"ok": True, "reason": None, "likeness": 0.98, "source_face_px": 459.0})
    monkeypatch.setattr(fd, "closeup_source", lambda crop, cu, px, dest: (Image.new("RGB", (8, 8)).save(dest), dest)[1])
    sc = _SC(tmp_path, 2048)
    res = asyncio.run(fd.run(sc, glb, maps, REC, {"face_density": "standard", "face_closeup_seed": 77}))
    r = res["metrics"]["face_detail"]
    assert r["source"] == "closeup" and r["closeup"]["used"] and r["method"] == fd.METHOD_CLOSEUP
    assert calls["redraw"] == [("face_closeup", 77)]  # the seed given (a Resume) is used, no sharpening job
    assert calls["paint"][0]["colour"] == "painted_head_closeup_merged.png"
    # the close-up's features sit where the painting's do (facecloseup), so it is painted through the painting's own fit
    assert calls["paint"][0]["fit_from_crop"] is True and calls["paint"][0]["require"] is False
    assert res["files"]["face_closeup"].name == "painted_head_closeup.png"


def test_the_corrected_close_up_is_the_one_painted_from(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    fixed = tmp_path / "painted_head_closeup_fixed.png"
    Image.new("RGB", (8, 8)).save(fixed)
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: {"ok": True, "reason": None, "likeness": 0.98, "source_face_px": 459.0,
                                                         "registered": str(fixed)})
    used = {}
    monkeypatch.setattr(fd, "closeup_source", lambda crop, cu, px, dest: (used.setdefault("cu", Path(cu).name), Image.new("RGB", (8, 8)).save(dest), dest)[2])
    asyncio.run(fd.run(_SC(tmp_path, 2048), glb, maps, REC, {"face_density": "standard"}))
    assert used["cu"] == "painted_head_closeup_fixed.png"


def test_a_close_up_off_the_paintings_look_is_drawn_once_more_in_the_looks_own_words(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    verdicts = iter([{"ok": False, "reason": "style", "likeness": 0.95, "source_face_px": 459.0},
                     {"ok": True, "reason": None, "likeness": 0.98, "source_face_px": 459.0}])
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: next(verdicts))
    monkeypatch.setattr(fd, "closeup_source", lambda crop, cu, px, dest: (Image.new("RGB", (8, 8)).save(dest), dest)[1])
    sc = _SC(tmp_path, 2048)
    sc.style = {"id": "painted"}
    res = asyncio.run(fd.run(sc, glb, maps, REC, {"face_density": "standard"}))
    r = res["metrics"]["face_detail"]
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_closeup_2"]  # no sharpening job: the second is used
    assert calls["prompts"][0] == fd.CLOSEUP_PROMPT and "smooth painterly shading" in calls["prompts"][1]
    assert r["source"] == "closeup" and [t["prompt"] for t in r["closeup"]["tries"]] == ["plain", "look"]
    assert {"face_closeup_seed", "face_closeup_seed_2"} <= set(sc.params)  # both seeds kept for a Resume


def test_no_second_close_up_for_moved_features_or_colours(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: {"ok": False, "reason": "eye_colour", "likeness": 0.97, "source_face_px": 459.0})
    sc = _SC(tmp_path, 2048)
    sc.style = {"id": "painted"}
    asyncio.run(fd.run(sc, glb, maps, REC, {"face_density": "standard"}))
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_redraw"]
    calls["redraw"].clear()
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: {"ok": False, "reason": "style", "likeness": 0.95, "source_face_px": 459.0})
    sc = _SC(tmp_path, 2048)
    sc.style = {"id": "cel"}
    res = asyncio.run(fd.run(sc, glb, maps, REC, {"face_density": "standard"}))
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_closeup_2", "face_closeup_3", "face_redraw"]
    c = guidance.describe_check(fd.face_checks({"face_detail": {"ran": True, "sharpness_after": 1.3, "feature_match_after": 0.8,
                                                                **res["metrics"]["face_detail"], "source": "painting"}})[0])
    assert "A close-up was drawn but not used: it was drawn in a different style, more like a photograph than the painting." in c["help"]


# --- Resume carries both picture jobs' seeds ------------------------------------------------------------------------


def test_resume_carries_both_picture_seeds():
    assert {"seed", "face_seed", "face_closeup_seed", "face_closeup_seed_2"} <= set(jobs.RESUME_SEEDS)


# --- downstream: packs at the data maps' size, Unity imports at full size ------------------------------------------


def test_packs_follow_the_data_maps_not_a_larger_base_colour(tmp_path):
    base = tmp_path / "base_color.png"
    Image.new("RGB", (64, 64)).save(base)
    rough = tmp_path / "roughness.png"
    Image.new("L", (32, 32), 100).save(rough)
    out = export.pack_texture_maps({"base_color": base, "roughness": rough}, tmp_path / "o", "K")
    assert Image.open(out["orm"]).size == (32, 32) and Image.open(out["metallic_smoothness"]).size == (32, 32)


def test_unity_full_size_check():
    u = {"ok": True, "texturesAssigned": ["base_color", "normal", "ao", "metallic_smoothness"],
         "textureSizes": ["base_color.png:4096:4096", "normal.png:2048:2048"]}
    c = next(c for c in export.unity_checks(u, False, False) if c["id"] == "textures_full_size")
    assert c["ok"] and "4096" in c["help"]
    u["textureSizes"] = ["base_color.png:4096:2048"]
    c = next(c for c in export.unity_checks(u, False, False) if c["id"] == "textures_full_size")
    assert not c["ok"] and "base color texture at 2048" in c["help"]
    assert not [c for c in export.unity_checks({"ok": True}, False, False) if c["id"] == "textures_full_size"]
    assert json.dumps(export.texture_import_sizes({"textureSizes": ["a.png:x:1", "b.png:8:8"]})) == '[["b.png", 8, 8]]'


def test_resume_puts_both_picture_seeds_back_in_the_job(client, monkeypatch):  # noqa: F811
    """A stopped clean-up that had picked the sharpening and the close-up seeds draws the same two pictures on Resume."""
    import time as _t

    from vgac import stages
    from vgac.db import Job, Version, session_factory

    state = {"hold": True}
    inner = stages.execute

    async def execute(ctx, kind, payload):
        with session_factory()() as db:  # like the face pass: both seeds written on the version as soon as picked
            v = db.get(Version, payload["version_id"])
            v.params = {**v.params, "face_seed": 111, "face_closeup_seed": 222, "face_closeup_seed_2": 333}
            db.commit()
        while state["hold"]:
            ctx.check_cancelled()
            await asyncio.sleep(0.02)
        await inner(ctx, kind, payload)

    def until(fn, timeout=10.0):
        t0 = _t.time()
        while _t.time() - t0 < timeout:
            if fn():
                return
            _t.sleep(0.05)
        raise AssertionError("condition not reached")

    monkeypatch.setattr(stages, "execute", execute)
    p, a = _project_asset(client)
    aid = a["id"]
    out = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    until(lambda: client.get(f"/api/jobs/{out['job']['id']}").json()["status"] == "running")
    client.post(f"/api/assets/{aid}/stop", json={"stage": "concept"})
    until(lambda: client.get(f"/api/assets/{aid}").json()["stages"]["concept"][0]["stopped"])
    state["hold"] = False
    client.post(f"/api/assets/{aid}/resume", json={"stage": "concept"})
    assert wait_job(client, out["job"]["id"])["status"] == "done"
    with session_factory()() as db:
        params = db.get(Job, out["job"]["id"]).payload["params"]
    assert params["face_seed"] == 111 and params["face_closeup_seed"] == 222 and params["face_closeup_seed_2"] == 333


def test_the_unity_metrics_carry_the_texture_sizes():
    m = export.unity_metrics({"ok": True, "textureSizes": ["base_color.png:4096:4096"]})
    assert m["textureSizes"] == ["base_color.png:4096:4096"]
