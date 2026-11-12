"""The side-views step's pose-ready edit (vgac/stages/turnaround.py _pose_ready_picture) and how the 3D step uses it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from vgac import reference
from vgac.stages import mesh as mesh_stage
from vgac.stages import turnaround


def _png(path: Path, size=(64, 64)) -> Path:
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    for x in range(16, 48):
        for y in range(8, 60):
            im.putpixel((x, y), (200, 150, 40, 255))
    im.save(path)
    return path


class _SC:
    def __init__(self, d: Path, parent=None, category="humanoid", empty=False):
        self.dir = d
        self.asset = SimpleNamespace(category=category, id="asset0001")
        self.settings = SimpleNamespace(stage_instances={"concept": "b"})
        self.ctx = SimpleNamespace(instance="a", clients={"a": object(), "b": object()})
        self.version = SimpleNamespace(params={}, metrics={}, id="ver000001", stage="turnaround")
        self.parent = parent
        self.registered: dict = {}
        self.graphs: list = []
        self.empty = empty

    def file(self, key):
        return self.registered.get(key)

    def register(self, key, p):
        self.registered[key] = Path(p)

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    def use_model(self, _m):
        pass

    def prefix(self, tag=""):
        return "p/" + tag

    def copy_in(self, src, name):
        dest = self.dir / name
        dest.write_bytes(Path(src).read_bytes())
        return dest

    async def stage_input(self, _p, name, instance=None):
        return name

    async def comfy(self, graph, instance=None, **_k):
        self.graphs.append((instance, graph))
        return {"elapsed_seconds": 2.0}

    async def download_image(self, _entry, _node, dest, instance=None):
        (Image.new("RGBA", (64, 64)) if self.empty else Image.open(_png(Path(dest)))).save(dest)
        return Path(dest)

    async def cutout(self, image_path, name, instance=None, **_k):
        dest = self.dir / f"{name}.png"
        dest.write_bytes(Path(image_path).read_bytes())
        return {"cutout": dest, "stats": {"empty": self.empty, "coverage": 0.0 if self.empty else 0.4}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(turnaround.library, "version_file", lambda v, key, _s: (getattr(v, "files_on_disk", {}) or {}).get(key))
    d = tmp_path / "v"
    d.mkdir()
    return SimpleNamespace(dir=d, src=_png(tmp_path / "concept.png"), tmp=tmp_path)


def _prompts(sc):
    return [n["inputs"].get("prompt") for _c, g in sc.graphs for n in g.values() if isinstance(n, dict) and "prompt" in n.get("inputs", {})]


def test_off_by_default_and_for_other_categories(env):
    sc = _SC(env.dir)
    assert asyncio.run(turnaround._pose_ready_picture(sc, {}, env.src)) is None
    assert sc.graphs == []
    sc = _SC(env.dir, category="prop_rigid")
    assert asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, env.src)) is None
    assert not turnaround.pose_ready_on({"pose_ready": True}, "creature")
    assert turnaround.pose_ready_on({"pose_ready": True}, "humanoid")


def test_edits_once_and_records_it(env):
    sc = _SC(env.dir)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, env.src))
    assert out == env.dir / "pose_ready_picture.png"
    assert set(sc.registered) == {"pose_ready_picture", "pose_ready_cutout"}
    assert sc.version.params["pose_ready"] is True and sc.version.params["neutral_mouth"] is False
    (text,) = [t for t in _prompts(sc) if t]
    assert "nothing held" in text and "A pose" in text and "closed mouth" not in text
    assert "every tool, weapon" not in text  # the 3D step's suffix would keep a held object
    assert sc.graphs[0][0] == "b"  # the picture card
    assert sc.version.metrics["pose_ready"]["card"] == "b"


def test_neutral_mouth_adds_the_sentence(env):
    sc = _SC(env.dir)
    asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True, "neutral_mouth": True}, env.src))
    (text,) = [t for t in _prompts(sc) if t]
    assert "relaxed, closed mouth" in text and sc.version.params["neutral_mouth"] is True


def test_resume_keeps_the_edit_already_made(env):
    sc = _SC(env.dir)
    sc.register("pose_ready_picture", _png(env.dir / "pose_ready_picture.png"))
    sc.register("pose_ready_cutout", _png(env.dir / "pose_ready_cutout.png"))
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, env.src))
    assert out == env.dir / "pose_ready_picture.png" and sc.graphs == []


def test_a_redraw_copies_the_parents_picture_and_does_not_edit_again(env):
    pd = env.tmp / "parent"
    pd.mkdir()
    files = {"pose_ready_picture": _png(pd / "pose_ready_picture.png"), "pose_ready_cutout": _png(pd / "pose_ready_cutout.png")}
    parent = SimpleNamespace(id="parent01", stage="turnaround", params={"pose_ready": True}, files_on_disk=files)
    sc = _SC(env.dir, parent=parent)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, env.src))
    assert sc.graphs == [] and out == env.dir / "pose_ready_picture.png" and out.is_file()
    assert sc.version.params["pose_ready"] is True
    # A changed mouth setting is a new edit.
    sc2 = _SC(env.dir, parent=parent)
    asyncio.run(turnaround._pose_ready_picture(sc2, {"pose_ready": True, "neutral_mouth": True}, env.src))
    assert len(sc2.graphs) == 1


def test_empty_result_is_an_error(env):
    sc = _SC(env.dir, empty=True)
    with pytest.raises(RuntimeError, match="without the character"):
        asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, env.src))


def test_sheet_and_camera_start_from_the_posed_cutout(env):
    sc = _SC(env.dir)
    sc.register("pose_ready_cutout", _png(env.dir / "pose_ready_cutout.png"))
    assert asyncio.run(turnaround._concept_cutout(sc, None, env.src)) == env.dir / "pose_ready_cutout.png"


def test_params_are_in_the_schema_for_humanoids_only():
    by = {p["name"]: p for p in reference.STAGE_SCHEMAS["turnaround"]}
    for name in ("pose_ready", "neutral_mouth"):
        assert by[name]["type"] == "boolean" and by[name]["default"] is None and by[name]["categories"] == ["humanoid"]


def test_mesh_uses_the_posed_picture_and_skips_its_own_hand_edit(tmp_path, monkeypatch):
    posed = _png(tmp_path / "pose_ready_cutout.png")
    seen = {}

    class Sc:
        version = SimpleNamespace(params={})

        def lineage_file(self, key, stages=None):
            seen[key] = stages
            return posed if key == "pose_ready_cutout" else None

        def file(self, key):
            return None

    monkeypatch.setattr(mesh_stage.hairlib, "is_library", lambda sc: False)
    assert mesh_stage._input_image(Sc(), "concept") == posed
    assert seen["pose_ready_cutout"] == ("turnaround",)
