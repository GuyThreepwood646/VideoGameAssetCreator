"""Item 16: a rejected face close-up is drawn again with a new seed (up to two more draws) before the pass falls back to the
soft painted face. The drawing and the guard are faked, so the retry logic runs without a card."""

from __future__ import annotations

import asyncio

from PIL import Image

from vgac.stages import face_detail as fd
from test_face_sharper import REC, _SC, stubbed  # noqa: F401  (the stubbed pass and its fixture)


class _Say(_SC):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.said: list[str] = []

    def progress(self, frac, msg=None, *a):
        self.said.append(str(msg))


def _guard(reason, likeness=0.88, **extra):
    return {"ok": reason is None, "reason": reason, "likeness": likeness, "feature_scores": {"eye_l": 0.9},
            "feature_score_min": 0.9, "source_face_px": 459.0, **extra}


def _fake_source(monkeypatch):
    monkeypatch.setattr(fd, "closeup_source", lambda crop, cu, px, dest: (Image.new("RGB", (8, 8)).save(dest), dest)[1])


def _run(sc, glb, maps, params=None):
    return asyncio.run(fd.run(sc, glb, maps, REC, {"face_density": "standard", **(params or {})}))


def test_retry_reasons_and_why():
    for reason in ("likeness", "features", "style", "no_features"):
        assert fd.retry_wanted({"ok": False, "reason": reason})
    # the model's own face was not found: the same for every seed
    assert not fd.retry_wanted({"ok": False, "reason": "no_features", "model_face_missing": True})
    # colours, moved features and a failed drawing are not what a new seed is for
    for reason in ("eye_colour", "skin_colour", "hair_colour", "moved", "off_model", "error"):
        assert not fd.retry_wanted({"ok": False, "reason": reason})
    assert not fd.retry_wanted({"ok": True, "reason": None})
    assert len(fd.closeup_tries(None)) == fd.CLOSEUP_TRIES_MAX == 3
    assert len({k for k, _ in fd.closeup_tries("painted")}) == 3


def test_a_rejected_close_up_is_drawn_again_with_new_seeds_and_the_first_pass_is_used(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    verdicts = iter([_guard("likeness", 0.880), _guard("features", 0.95), _guard(None, 0.97)])
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: next(verdicts))
    _fake_source(monkeypatch)
    sc = _Say(tmp_path, 2048)
    r = _run(sc, glb, maps, {"face_detail_seed": 100})["metrics"]["face_detail"]
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_closeup_2", "face_closeup_3"]  # no sharpening job
    assert [s for _, s in calls["redraw"]] == [100, 101, 102]
    assert r["source"] == "closeup" and r["closeup"]["used"] and r["closeup"]["draws"] == 3
    tries = r["closeup"]["tries"]
    assert [t["reason"] for t in tries] == ["likeness", "features", None]
    assert [t["seed"] for t in tries] == [100, 101, 102] and tries[0]["likeness"] == 0.88 and tries[0]["feature_scores"]
    assert any("trying again" in m and "draw 2 of 3" in m for m in sc.said)
    assert any("draw 3 of 3" in m for m in sc.said)


def test_the_second_draw_passing_stops_the_retries(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    verdicts = iter([_guard("likeness"), _guard(None, 0.97)])
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: next(verdicts))
    _fake_source(monkeypatch)
    r = _run(_SC(tmp_path, 2048), glb, maps)["metrics"]["face_detail"]
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_closeup_2"]
    assert r["closeup"]["used"] and r["closeup"]["draws"] == 2


def test_three_rejections_fall_back_as_before_and_every_attempt_is_recorded(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: _guard("likeness", 0.85))
    r = _run(_SC(tmp_path, 2048), glb, maps)["metrics"]["face_detail"]
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_closeup_2", "face_closeup_3", "face_redraw"]
    assert r["source"] == "painting" and r["closeup"]["used"] is False and r["closeup"]["reason"] == "likeness"
    assert len(r["closeup"]["tries"]) == 3 and r["closeup"]["draws"] == 3
    assert calls["paint"][0]["picture"] == "painted_head_sharp.png"


def test_no_retry_when_a_retry_cannot_help(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: {"ok": False, "reason": "no_features", "model_face_missing": True})
    r = _run(_SC(tmp_path, 2048), glb, maps)["metrics"]["face_detail"]
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_redraw"]
    assert len(r["closeup"]["tries"]) == 1 and r["closeup"]["tries"][0]["reason"] == "no_features"
    calls["redraw"].clear()
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: _guard("skin_colour", 0.97))
    _run(_SC(tmp_path, 2048), glb, maps)
    assert [k for k, _ in calls["redraw"]] == ["face_closeup", "face_redraw"]


def test_a_fixed_seed_stays_reproducible(tmp_path, stubbed, monkeypatch):
    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: _guard("likeness"))
    params = {"face_closeup_seed": 7, "face_closeup_seed_2": 8, "face_closeup_seed_3": 9}
    sc = _SC(tmp_path, 2048)
    _run(sc, glb, maps, params)
    assert [s for k, s in calls["redraw"] if k.startswith("face_closeup")] == [7, 8, 9]
    # a Resume that carries only the second seed gets the third from it: the same draws again
    calls["redraw"].clear()
    sc = _SC(tmp_path, 2048)
    _run(sc, glb, maps, {"face_closeup_seed": 7, "face_closeup_seed_2": 8})
    assert [s for k, s in calls["redraw"] if k.startswith("face_closeup")] == [7, 8, 9]
    assert sc.params["face_closeup_seed_3"] == 9
