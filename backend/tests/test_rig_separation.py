"""WP-75 part B: the rig step separates an upper arm the joined-limb measure finds grown into the body, with the Shape
tab's engine (separate.separate, faked here), keeps the cut only where raising the arm is not made worse (cut_check,
faked here), and keeps the cut model, its caps file and its pictures. Where nothing is cut the rig carries on as
before, and the rig page's line says why in plain words."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import separate, shape
from vgac.stages import rig


def _sc(tmp_path: Path, states: dict[str, str]):
    version = SimpleNamespace(metrics={"limb_joins": {"limbs": {f"{k}_upperarm": {"state": v} for k, v in states.items()}}},
                              files={})
    sc = SimpleNamespace(dir=tmp_path, version=version, settings=SimpleNamespace(blender_timeout_seconds=60))
    sc.progress = lambda *a, **k: None
    sc.register = lambda key, path: version.files.__setitem__(key, str(path))
    sc.set_metrics = lambda **v: version.metrics.update(v)
    return sc


@pytest.fixture
def files(tmp_path):
    src = tmp_path / "prep.glb"
    src.write_bytes(b"clean-up")
    rig_glb = tmp_path / "rig.glb"
    rig_glb.write_bytes(b"rig before")
    return src, rig_glb


def _engine(calls, changed=True, raises=None):
    def fake(settings, step4, rig_glb, out_glb, side="both", gap=None, start=None, rigged_out=None, timeout=None):
        calls.append(side)
        if raises:
            raise raises
        out_glb.parent.mkdir(parents=True, exist_ok=True)
        if changed:
            out_glb.write_bytes(b"cut")
            Path(rigged_out).write_bytes(f"rig cut {side}".encode())
            (out_glb.parent / separate.CAPS_FILE).write_text(json.dumps({"format": 1, "caps": [{"side": side, "uv_tris": []}]}))
        return {"changed": changed, "ok": changed, "reason": None if changed else "refused", "sides": {},
                "messages": ["The left arm is separate."] if changed else ["The left arm was not changed: its weights start on the forearm."]}
    return fake


@pytest.fixture
def passes(monkeypatch):
    """The shoulder skin pass and the joined-limb measure, recorded; the cut check answers `worse`."""
    log = {"skin": [], "shoulder_skin": [], "limb_joins": [], "worse": {}}

    async def skin_pass(sc, glb, keep_before=None, below=None):
        log["skin"].append(Path(glb).read_bytes())
        log.setdefault("below", []).append(below)
        return {"ok": True, "changed": 0}

    async def shoulder_skin(sc, glb):
        log["shoulder_skin"].append(Path(glb).read_bytes())

    async def limb_joins(sc, glb):
        log["limb_joins"].append(Path(glb).read_bytes())

    async def cut_worse(sc, before, after):
        return log["worse"]
    monkeypatch.setattr(rig, "_skin_pass", skin_pass)
    monkeypatch.setattr(rig, "_shoulder_skin", shoulder_skin)
    monkeypatch.setattr(rig, "_limb_joins", limb_joins)
    monkeypatch.setattr(rig, "_cut_worse", cut_worse)
    return log


def test_a_joined_arm_that_the_cut_helps_is_separated(tmp_path, files, monkeypatch, passes):
    src, rig_glb = files
    calls = []
    monkeypatch.setattr(separate, "separate", _engine(calls))
    sc = _sc(tmp_path, {"Left": "joined", "Right": "free"})
    cut = asyncio.run(rig._separate_joined(sc, src, rig_glb))
    assert calls == ["L"]
    assert rig_glb.read_bytes() == b"rig cut L" and (tmp_path / "rig_before_separation.glb").read_bytes() == b"rig before"
    assert passes["skin"] == [b"rig cut L"] and passes["shoulder_skin"] == [b"rig before"]  # both rigs get the pass
    assert passes["below"] == [["L"]]  # the band under the collarbone only on the cut side
    assert passes["limb_joins"] == [b"rig cut L"]  # measured again on the cut rig
    m = sc.version.metrics["separation"]
    assert m["changed"] is True and m["sides"] == ["L"] and m["worse"] == {}
    assert m["message"].startswith("The left arm was separated from the body")
    assert Path(sc.version.files[shape.CAPS_KEY]).is_file()
    assert cut["caps"] == [{"side": "L", "uv_tris": []}] and Path(cut["cut_glb"]).read_bytes() == b"cut"


def test_both_joined_arms_go_in_one_run(tmp_path, files, monkeypatch, passes):
    calls = []
    monkeypatch.setattr(separate, "separate", _engine(calls))
    sc = _sc(tmp_path, {"Left": "joined", "Right": "joined"})
    asyncio.run(rig._separate_joined(sc, *files))
    assert calls == ["both"] and sc.version.metrics["separation"]["message"].startswith("The left and right arms were")


def test_a_cut_that_makes_both_arms_worse_is_dropped_and_the_rig_carries_on(tmp_path, files, monkeypatch, passes):
    src, rig_glb = files
    calls = []
    monkeypatch.setattr(separate, "separate", _engine(calls))
    passes["worse"] = {"L": ["shoulder", "crossings"], "R": ["shoulder"]}
    sc = _sc(tmp_path, {"Left": "joined", "Right": "joined"})
    assert asyncio.run(rig._separate_joined(sc, src, rig_glb)) is None
    assert calls == ["both"] and rig_glb.read_bytes() == b"rig before"
    assert passes["shoulder_skin"] == [b"rig before"] and passes["limb_joins"] == []
    m = sc.version.metrics["separation"]
    assert m["changed"] is False and m["reason"] == "made_worse" and shape.CAPS_KEY not in sc.version.files
    assert "The left arm was left joined to the body: separating it made the shoulder stretch more and the skin fold " \
           "through itself when the arm is raised." in m["message"]
    assert "The right arm was left joined" in m["message"] and not (tmp_path / rig.SEPARATION_WORK).exists()


def test_a_cut_that_makes_one_arm_worse_is_made_again_on_the_other_only(tmp_path, files, monkeypatch, passes):
    src, rig_glb = files
    calls = []
    monkeypatch.setattr(separate, "separate", _engine(calls))
    passes["worse"] = {"L": ["crossings"]}
    sc = _sc(tmp_path, {"Left": "joined", "Right": "joined"})
    assert asyncio.run(rig._separate_joined(sc, src, rig_glb)) is not None
    assert calls == ["both", "R"] and rig_glb.read_bytes() == b"rig cut R"
    m = sc.version.metrics["separation"]
    assert m["changed"] is True and m["sides"] == ["R"]
    assert m["message"].startswith("The right arm was separated") and "The left arm was left joined" in m["message"]


@pytest.mark.parametrize("state", ["free", "clothing"])
def test_an_arm_that_is_not_joined_is_left_alone(tmp_path, files, monkeypatch, passes, state):
    calls = []
    monkeypatch.setattr(separate, "separate", _engine(calls))
    sc = _sc(tmp_path, {"Left": state, "Right": "free"})
    assert asyncio.run(rig._separate_joined(sc, *files)) is None
    assert calls == [] and sc.version.metrics["separation"]["changed"] is False
    assert sc.version.metrics["separation"]["message"] is None and passes["shoulder_skin"] == [b"rig before"]
    assert files[1].read_bytes() == b"rig before"


def test_a_refusal_leaves_the_rig_as_it_was_with_the_engines_words(tmp_path, files, monkeypatch, passes):
    monkeypatch.setattr(separate, "separate", _engine([], changed=False))
    sc = _sc(tmp_path, {"Left": "joined"})
    assert asyncio.run(rig._separate_joined(sc, *files)) is None
    assert files[1].read_bytes() == b"rig before" and passes["shoulder_skin"] == [b"rig before"]
    m = sc.version.metrics["separation"]
    assert m["messages"] == ["The left arm was not changed: its weights start on the forearm."]
    assert m["message"] == "The arm was left joined to the body. The left arm was not changed: its weights start on the forearm."
    assert passes["limb_joins"] == [] and shape.CAPS_KEY not in sc.version.files and not (tmp_path / rig.SEPARATION_WORK).exists()


def test_an_engine_error_never_stops_the_rig(tmp_path, files, monkeypatch, passes):
    monkeypatch.setattr(separate, "separate", _engine([], raises=RuntimeError("Blender stopped")))
    sc = _sc(tmp_path, {"Right": "joined"})
    assert asyncio.run(rig._separate_joined(sc, *files)) is None
    m = sc.version.metrics["separation"]
    assert m["changed"] is False and m["reason"] == "error" and m["error"] == "Blender stopped"
    assert m["message"] == "The arm was left joined to the body. The separation could not run, so the rig was made as before."
    assert files[1].read_bytes() == b"rig before" and passes["shoulder_skin"] == [b"rig before"]


def test_a_check_that_cannot_run_keeps_the_rig_as_it_was(tmp_path, files, monkeypatch, passes):
    monkeypatch.setattr(separate, "separate", _engine([]))
    passes["worse"] = None
    sc = _sc(tmp_path, {"Left": "joined"})
    assert asyncio.run(rig._separate_joined(sc, *files)) is None
    assert files[1].read_bytes() == b"rig before" and sc.version.metrics["separation"]["reason"] == "check_failed"
    assert "could not be measured" in sc.version.metrics["separation"]["message"]


def test_the_rig_version_gets_the_pictures_with_the_caps(tmp_path, monkeypatch):
    work = tmp_path / rig.SEPARATION_WORK
    work.mkdir()
    (work / "prep.glb").write_bytes(b"cut")
    seen = {}

    def refresh(glb, caps, sources, out_dir):
        seen.update(glb=Path(glb), caps=caps, sources=sources)
        f = Path(out_dir) / "shape_base_color.png"
        f.write_bytes(b"png")
        return {"base_color": f}
    monkeypatch.setattr(shape, "refresh_pictures", refresh)
    sc = _sc(tmp_path, {})
    sc.lineage_file = lambda key, stages=None: tmp_path / f"{key}.png" if key == "base_color" else None
    asyncio.run(rig._cut_pictures(sc, {"cut_glb": work / "prep.glb", "caps": [{"side": "L"}]}))
    assert seen["glb"] == work / "prep.glb" and seen["caps"] == [{"side": "L"}]
    assert seen["sources"]["base_color"] == tmp_path / "base_color.png" and seen["sources"]["normal"] is None
    assert sc.version.files["base_color"].endswith("shape_base_color.png") and not work.exists()
    asyncio.run(rig._cut_pictures(sc, None))  # nothing was cut: nothing to do
