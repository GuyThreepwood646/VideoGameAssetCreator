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


def test_a_held_face_through_the_pass_changes_nothing_and_fails_the_check(tmp_path, stubbed, monkeypatch):
    """Item 9 through run(): a poor fit leaves the model's face as it was (no files, nothing replaced)."""
    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: _guard(None, 0.97))
    _fake_source(monkeypatch)
    monkeypatch.setattr(fd, "fit_and_paint", lambda *a, **k: {"ok": False, "reason": "poor_fit", "alignment": {"score": 0.31, "how": "whole"}})
    replaced = []
    monkeypatch.setattr(fd.imaging, "glb_replace_images", lambda *a, **k: replaced.append(1))
    before = glb.read_bytes()
    out = _run(_SC(tmp_path, 2048), glb, maps)
    r = out["metrics"]["face_detail"]
    assert r["ran"] is False and r["skipped"] == "poor_fit" and out["files"] == {} and out["relayout"] is False
    assert not replaced and glb.read_bytes() == before
    c = fd.face_checks(out["metrics"])[0]
    assert not c["ok"] and c["value"] == 0.31 and c["limit"] == 0.65 and "left as it was" in c["help"]


# --- the close-up shown on the step's card while clean-up still works ---------------------------------------------


class _Card(_SC):
    """A step context that keeps what the card would be told: each registered picture and metrics, in order."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.shown: list[tuple[str, dict]] = []
        self._live: dict = {}

    def register(self, key, path):
        self._file = (key, str(path))
        return str(path)

    def set_metrics(self, **kw):
        self._live = kw.get("face_closeup_live", self._live)

    def publish(self):
        self.shown.append((self._file[1].replace("\\", "/").rsplit("/", 1)[-1], dict(self._live)))


def _card_run(tmp_path, stubbed, monkeypatch, verdicts):
    calls, glb, maps = stubbed
    it = iter(verdicts)
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: next(it))
    _fake_source(monkeypatch)

    sc = _Card(tmp_path, 2048)
    out = _run(sc, glb, maps)
    return sc, out


def test_each_close_up_is_shown_when_drawn_and_again_with_its_verdict(tmp_path, stubbed, monkeypatch):
    sc, _ = _card_run(tmp_path, stubbed, monkeypatch, [_guard("likeness", 0.88), _guard(None, 0.97)])
    states = [(f, m["state"]) for f, m in sc.shown]
    assert states[:4] == [("painted_head_closeup.png", "checking"), ("painted_head_closeup.png", "again"),
                          ("painted_head_closeup_2.png", "checking"), ("painted_head_closeup_2.png", "used")]
    again = sc.shown[1][1]["caption"]
    assert "draw 1 of 3" in again and "did not look enough like the painted face" in again and "Drawing it again" in again
    assert sc.shown[-1][1]["state"] == "used" and "was used" in sc.shown[-1][1]["caption"]  # and at the end of the pass


def test_a_refused_close_up_says_why_and_a_held_face_says_so(tmp_path, stubbed, monkeypatch):
    sc, _ = _card_run(tmp_path, stubbed, monkeypatch, [_guard("likeness"), _guard("likeness"), _guard("features")])
    last = sc.shown[-1][1]
    assert last["state"] == "refused" and "draw 3 of 3" in last["caption"] and "eyes or mouth" in last["caption"]
    assert "Drawing it again" not in last["caption"]
    # a good close-up whose paint was held for a poor fit
    sc2 = _Card(tmp_path, 2048)
    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: _guard(None, 0.97))
    monkeypatch.setattr(fd, "fit_and_paint", lambda *a, **k: {"ok": False, "reason": "poor_fit", "alignment": {"score": 0.3}})
    _run(sc2, glb, maps)
    assert sc2.shown[-1][1]["state"] == "refused" and "left as it was" in sc2.shown[-1][1]["caption"]


def test_a_failing_card_never_fails_the_pass(tmp_path, stubbed, monkeypatch):
    class Broken(_Card):
        def publish(self):
            raise RuntimeError("no bus")

    calls, glb, maps = stubbed
    monkeypatch.setattr(fd, "closeup_guard", lambda *a: _guard(None, 0.97))
    _fake_source(monkeypatch)
    r = _run(Broken(tmp_path, 2048), glb, maps)["metrics"]["face_detail"]
    assert r["ran"] and r["closeup"]["used"]
