"""The 3D step's refine edits the picture from the instruction before building (vgac/stages/mesh.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from vgac.stages import mesh as mesh_stage


def _rgba(path: Path, size=(64, 64)) -> Path:
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    for x in range(16, 48):
        for y in range(8, 60):
            im.putpixel((x, y), (200, 150, 40, 255))
    im.save(path)
    return path


class _SC:
    def __init__(self, d: Path, params: dict, parent=None, fail_on: set | None = None, empty=False):
        self.dir = d
        self.asset = SimpleNamespace(category="humanoid", id="asset0001")
        self.category = {"input_view_default": "concept"}
        self.settings = SimpleNamespace(stage_instances={"concept": "b", "mesh": "a"})
        self.ctx = SimpleNamespace(instance="a", clients={"a": object(), "b": object()})
        self.version = SimpleNamespace(params=dict(params), metrics={}, id="ver000001", stage="mesh")
        self.parent = parent
        self.registered: dict = {}
        self.graphs: list = []
        self.fail_on = fail_on or set()
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

    def progress(self, *_a, **_k):
        pass

    def prefix(self, tag=""):
        return "p/" + tag

    def copy_in(self, src, name):
        dest = self.dir / name
        dest.write_bytes(Path(src).read_bytes())
        return dest

    async def stage_input(self, _p, name, instance=None):
        if instance in self.fail_on:
            raise ConnectionError(f"card {instance} unreachable")
        return name

    async def comfy(self, graph, instance=None, **_k):
        self.graphs.append((instance, graph))
        return {"elapsed_seconds": 3.0}

    async def download_image(self, _entry, _node, dest, instance=None):
        _rgba(Path(dest)) if not self.empty else Image.new("RGBA", (64, 64)).save(dest)
        return Path(dest)

    async def cutout(self, image_path, name, instance=None, **_k):
        dest = self.dir / f"{name}.png"
        dest.write_bytes(Path(image_path).read_bytes())
        return {"cutout": dest, "stats": {"empty": self.empty, "coverage": 0.0 if self.empty else 0.4}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    pic = _rgba(tmp_path / "quarter_cutout.png")
    built: list = []

    def lineage_input(sc, view):
        return pic

    real_input = mesh_stage._input_image
    monkeypatch.setattr(mesh_stage, "_input_image", lambda sc, view: mesh_stage._edited_cutout(sc) or lineage_input(sc, view))

    async def fake_run(sc, params):
        built.append((dict(params), mesh_stage._input_image(sc, params.get("input_view") or "concept")))

    monkeypatch.setattr(mesh_stage, "run", fake_run)
    monkeypatch.setattr(mesh_stage.library, "version_file", lambda v, key, _s: (getattr(v, "files_on_disk", {}) or {}).get(key))
    return SimpleNamespace(pic=pic, built=built, tmp=tmp_path, real_input=real_input)


def _parent(params, files=None):
    return SimpleNamespace(id="parent01", stage="mesh", params=params, files_on_disk=files or {})


def test_prompt_and_size():
    p = mesh_stage.edit_prompt("  hands  apart from the arms ")
    assert p.startswith("hands apart from the arms. Change only what is asked. Keep everything else")
    assert mesh_stage.edit_prompt("Five fingers!").startswith("Five fingers! Change only")
    assert mesh_stage.edit_size(1024, 1024) == (1024, 1024)
    w, h = mesh_stage.edit_size(1536, 2048)
    assert w % 16 == 0 and h % 16 == 0 and abs(w * h - 1024 * 1024) < 60000


def test_refine_edits_the_picture_and_builds_from_it(env, tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    sc = _SC(d, {}, parent=_parent({"engine": "trellis", "input_view": "quarter", "seed": 5}))
    asyncio.run(mesh_stage.refine(sc, "Hands apart from the backpack arms, five fingers", None, None, {}))
    card, graph = sc.graphs[0]
    assert card == "b"  # the picture card, not the 3D card this job runs on
    texts = [n["inputs"].get("prompt") for n in graph.values() if isinstance(n, dict) and "prompt" in n.get("inputs", {})]
    assert any(t and t.startswith("Hands apart from the backpack arms, five fingers. Change only what is asked") for t in texts)
    params, built_from = env.built[0]
    assert built_from == d / "edited_cutout.png"
    assert sc.version.params["picture_edit"] is True and sc.version.params["edit_from"] is None
    assert "edited_picture" in sc.registered and params["input_view"] == "quarter"
    assert sc.version.metrics["picture_edit"]["card"] == "b"


def test_refine_falls_back_to_the_jobs_own_card(env, tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    sc = _SC(d, {}, parent=_parent({"input_view": "quarter"}), fail_on={"b"})
    asyncio.run(mesh_stage.refine(sc, "five fingers", None, None, {}))
    assert sc.graphs[0][0] == "a" and sc.version.metrics["picture_edit"]["card"] == "a"


def test_empty_edit_is_an_error(env, tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    sc = _SC(d, {}, parent=_parent({"input_view": "quarter"}), empty=True)
    with pytest.raises(RuntimeError, match="without the character"):
        asyncio.run(mesh_stage.refine(sc, "five fingers", None, None, {}))
    assert not env.built


def test_refine_of_an_edited_version_edits_its_picture_again(env, tmp_path):
    prev = _rgba(tmp_path / "prev_edit.png", size=(80, 80))
    d = tmp_path / "v"
    d.mkdir()
    parent = _parent({"input_view": "quarter", "picture_edit": True, "edit_seed": 9}, {"edited_cutout": prev})
    sc = _SC(d, {}, parent=parent)
    asyncio.run(mesh_stage.refine(sc, "longer fingers", None, None, {}))
    src = Image.open(d / "edit_source.png")
    assert src.size == (80, 80)  # the parent's edited picture, not the turnaround view
    assert sc.version.params["edit_from"] == "parent01" and sc.version.params["edit_seed"] != 9
    assert "picture_edit" not in env.built[0][0]


def test_resume_keeps_the_edit_already_made(env, tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    sc = _SC(d, {}, parent=_parent({"input_view": "quarter"}))
    sc.register("edited_cutout", _rgba(d / "edited_cutout.png"))
    asyncio.run(mesh_stage.refine(sc, "five fingers", None, None, {}))
    assert sc.graphs == [] and env.built[0][1] == d / "edited_cutout.png"


def test_hunyuan_refine_does_not_edit(env, tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    sc = _SC(d, {}, parent=_parent({"engine": "hy3d"}))
    asyncio.run(mesh_stage.refine(sc, "five fingers", None, None, {}))
    assert sc.graphs == [] and "picture_edit" not in sc.version.params


def test_edit_can_be_turned_off(env, tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    sc = _SC(d, {}, parent=_parent({"input_view": "quarter"}))
    asyncio.run(mesh_stage.refine(sc, "five fingers", None, None, {"edit_picture": False}))
    assert sc.graphs == [] and env.built[0][1] == env.pic


def test_fine_tune_keeps_the_edited_picture(env, tmp_path):
    prev = _rgba(tmp_path / "prev_edit.png")
    prev_pic = _rgba(tmp_path / "prev_pic.png")
    d = tmp_path / "v"
    d.mkdir()
    parent = _parent({"input_view": "quarter", "picture_edit": True, "edit_seed": 9},
                     {"edited_cutout": prev, "edited_picture": prev_pic})
    sc = _SC(d, {}, parent=parent)
    asyncio.run(mesh_stage.adjust(sc, {"target_faces": 8000}))
    assert sc.graphs == []
    assert env.built[0][1] == d / "edited_cutout.png" and sc.version.params["edit_seed"] == 9
    assert "picture_edit" not in env.built[0][0]


def test_fine_tune_with_another_view_drops_the_edit(env, tmp_path):
    prev = _rgba(tmp_path / "prev_edit.png")
    d = tmp_path / "v"
    d.mkdir()
    parent = _parent({"input_view": "quarter", "picture_edit": True}, {"edited_cutout": prev})
    sc = _SC(d, {}, parent=parent)
    asyncio.run(mesh_stage.adjust(sc, {"input_view": "front"}))
    assert env.built[0][1] == env.pic and "picture_edit" not in sc.version.params
