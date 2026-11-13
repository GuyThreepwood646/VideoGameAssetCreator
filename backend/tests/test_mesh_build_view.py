"""Which three-quarter view the 3D step is built from (stages/mesh.choose_build_view): `quarter` unless it lost the
pose of the person's picture and `quarter2` kept it; never over a view the person chose. Real-data proof on the stored
M1-clothed run is in tools/verify_build_view.py (the library is not in the test environment)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from vgac.stages import mesh
from test_pose_held import figure

ARMS_OUT, ARMS_DOWN = True, False


def check(ok):
    return {"ok": ok}


def test_pick_build_view():
    assert mesh.pick_build_view(check(True), check(True)) == ("quarter", "kept")
    assert mesh.pick_build_view(check(True), None) == ("quarter", "kept")
    assert mesh.pick_build_view(check(False), check(True)) == ("quarter2", "switched")
    assert mesh.pick_build_view(check(False), check(False)) == ("quarter", "both_lost")
    assert mesh.pick_build_view(check(False), None) == ("quarter", "kept")
    assert mesh.pick_build_view(None, check(True)) == ("quarter", "kept")


def narrowed(src: Path, dest: Path, scale: float) -> Path:
    """The figure squeezed sideways, as a turned view is."""
    im = Image.open(src)
    w, h = im.size
    small = im.resize((max(1, int(w * scale)), h))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(small, ((w - small.size[0]) // 2, 0))
    out.save(dest)
    return dest


def fake_sc(tmp_path, quarter_arms_out, category="humanoid", edited=False):
    ref = figure(tmp_path / "ref.png", ARMS_OUT)
    q = narrowed(figure(tmp_path / "q0.png", quarter_arms_out), tmp_path / "q.png", 0.75)
    q2 = narrowed(figure(tmp_path / "q20.png", ARMS_OUT), tmp_path / "q2.png", 0.75)
    files = {"cutout": ref, "quarter_cutout": q, "quarter2_cutout": q2}
    sc = SimpleNamespace(asset=SimpleNamespace(category=category), version=SimpleNamespace(params={"picture_edit": True} if edited else {}),
                         file=lambda k: ref if edited else None, lineage_file=lambda k, stages=None: files.get(k))
    return sc


def run(sc, view="quarter"):
    return asyncio.run(mesh.choose_build_view(sc, view))


def test_lost_pose_switches_to_the_other_view(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh.hairlib, "is_library", lambda sc: False)
    r = run(fake_sc(tmp_path, ARMS_DOWN))
    assert r["view"] == "quarter2" and r["why"] == "switched"
    assert "other three-quarter view" in mesh.build_view_words(r)
    assert mesh.build_view_check({"build_view": r})["passed"]


def test_held_pose_keeps_quarter(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh.hairlib, "is_library", lambda sc: False)
    r = run(fake_sc(tmp_path, ARMS_OUT))
    assert r["view"] == "quarter" and r["why"] == "kept"
    assert mesh.build_view_check({"build_view": r})["passed"]


def test_not_a_humanoid_other_default_or_edited_picture_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh.hairlib, "is_library", lambda sc: False)
    assert run(fake_sc(tmp_path, ARMS_DOWN, category="prop_rigid"))["why"] == "none"
    assert run(fake_sc(tmp_path, ARMS_DOWN), view="front")["why"] == "none"
    assert run(fake_sc(tmp_path, ARMS_DOWN, edited=True))["why"] == "none"


def test_missing_second_view_keeps_quarter_and_both_lost_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh.hairlib, "is_library", lambda sc: False)
    sc = fake_sc(tmp_path, ARMS_DOWN)
    files = {"cutout": tmp_path / "ref.png", "quarter_cutout": tmp_path / "q.png"}
    sc.lineage_file = lambda k, stages=None: files.get(k)
    assert run(sc)["view"] == "quarter" and run(sc)["why"] == "kept"
    sc = fake_sc(tmp_path, ARMS_DOWN)
    files = {"cutout": tmp_path / "ref.png", "quarter_cutout": tmp_path / "q.png",
             "quarter2_cutout": narrowed(figure(tmp_path / "d.png", ARMS_DOWN), tmp_path / "q2d.png", 0.75)}
    sc.lineage_file = lambda k, stages=None: files.get(k)
    r = run(sc)
    c = mesh.build_view_check({"build_view": r})
    assert r["why"] == "both_lost" and not c["passed"] and c["fix"]["stage"] == "turnaround"
