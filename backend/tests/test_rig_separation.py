"""WP-75 part B: the rig step separates an upper arm the joined-limb measure finds grown into the body, with the Shape
tab's engine (separate.separate, faked here), and keeps the cut model, its caps file and its pictures."""
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
        calls.append({"side": side, "step4": Path(step4), "rig": Path(rig_glb)})
        if raises:
            raise raises
        out_glb.parent.mkdir(parents=True, exist_ok=True)
        if changed:
            out_glb.write_bytes(b"cut")
            Path(rigged_out).write_bytes(b"rig after")
            (out_glb.parent / separate.CAPS_FILE).write_text(json.dumps({"format": 1, "caps": [{"side": side, "uv_tris": []}]}))
        return {"changed": changed, "ok": changed, "reason": None if changed else "refused", "sides": {},
                "messages": ["The left arm is separate."] if changed else ["The left arm was not changed: x."]}
    return fake


def _measured(monkeypatch):
    runs = []

    async def limb_joins(sc, rig_glb):
        runs.append(Path(rig_glb).read_bytes())
    monkeypatch.setattr(rig, "_limb_joins", limb_joins)
    return runs


def test_a_joined_arm_is_separated_and_the_rig_keeps_the_cut(tmp_path, files, monkeypatch):
    src, rig_glb = files
    calls = []
    monkeypatch.setattr(separate, "separate", _engine(calls))
    runs = _measured(monkeypatch)
    sc = _sc(tmp_path, {"Left": "joined", "Right": "free"})
    cut = asyncio.run(rig._separate_joined(sc, src, rig_glb))
    assert calls == [{"side": "L", "step4": src, "rig": rig_glb}]
    assert rig_glb.read_bytes() == b"rig after" and (tmp_path / "rig_before_separation.glb").read_bytes() == b"rig before"
    assert runs == [b"rig after"]  # the joined-limb measure runs again on the cut rig
    m = sc.version.metrics["separation"]
    assert m["changed"] is True and m["sides"] == ["L"] and m["messages"] == ["The left arm is separate."]
    assert Path(sc.version.files[shape.CAPS_KEY]).is_file()
    assert cut["caps"] == [{"side": "L", "uv_tris": []}] and Path(cut["cut_glb"]).read_bytes() == b"cut"


def test_both_joined_arms_go_in_one_run(tmp_path, files, monkeypatch):
    calls = []
    monkeypatch.setattr(separate, "separate", _engine(calls))
    _measured(monkeypatch)
    asyncio.run(rig._separate_joined(_sc(tmp_path, {"Left": "joined", "Right": "joined"}), *files))
    assert [c["side"] for c in calls] == ["both"]


@pytest.mark.parametrize("state", ["free", "clothing"])
def test_an_arm_that_is_not_joined_is_left_alone(tmp_path, files, monkeypatch, state):
    calls = []
    monkeypatch.setattr(separate, "separate", _engine(calls))
    sc = _sc(tmp_path, {"Left": state, "Right": "free"})
    assert asyncio.run(rig._separate_joined(sc, *files)) is None
    assert calls == [] and sc.version.metrics["separation"]["changed"] is False
    assert files[1].read_bytes() == b"rig before"


def test_a_refusal_leaves_the_rig_as_it_was_with_the_engines_words(tmp_path, files, monkeypatch):
    monkeypatch.setattr(separate, "separate", _engine([], changed=False))
    runs = _measured(monkeypatch)
    sc = _sc(tmp_path, {"Left": "joined"})
    assert asyncio.run(rig._separate_joined(sc, *files)) is None
    assert files[1].read_bytes() == b"rig before" and not (tmp_path / "rig_before_separation.glb").exists()
    assert sc.version.metrics["separation"]["messages"] == ["The left arm was not changed: x."]
    assert runs == [] and shape.CAPS_KEY not in sc.version.files and not (tmp_path / rig.SEPARATION_WORK).exists()


def test_an_engine_error_never_stops_the_rig(tmp_path, files, monkeypatch):
    monkeypatch.setattr(separate, "separate", _engine([], raises=RuntimeError("Blender stopped")))
    sc = _sc(tmp_path, {"Right": "joined"})
    assert asyncio.run(rig._separate_joined(sc, *files)) is None
    assert sc.version.metrics["separation"] == {"changed": False, "reason": "error", "error": "Blender stopped"}
    assert files[1].read_bytes() == b"rig before"


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
