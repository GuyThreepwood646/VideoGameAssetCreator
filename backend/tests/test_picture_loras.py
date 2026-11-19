"""The person's LoRAs for the picture-edit uses (stages/picture_loras.py, docs/PLAN-qwen-edit-loras-2026-11-18.md section 8).

Covered: the validation of the choices, the shared check (every card, before any job, plain message), the record kept
on the version, and that each route passes only its own choice: refine (edit), the side views and the sheet and camera
turns, the pose redraw, and the face close-up only (never the sharpening redraw). The bald pictures pass none.
"""

from __future__ import annotations

import asyncio
import functools
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

LORA_KEY = "diffusion_model.layers.0.attention.to_k.lora_A.weight"


def arun(fn):
    """Run an async test with asyncio.run (the project's convention; no async pytest plugin)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def safetensors_bytes(meta: dict[str, str] | None = None) -> bytes:
    header: dict[str, Any] = {LORA_KEY: {"dtype": "F16", "shape": [4, 8], "data_offsets": [0, 64]}}
    if meta is not None:
        header["__metadata__"] = meta
    raw = json.dumps(header).encode("utf-8")
    raw += b" " * (-len(raw) % 8)
    return struct.pack("<Q", len(raw)) + raw + b"\x01" * 64


@pytest.fixture
def mine_settings(settings_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vgac import config, db, reference

    cfg = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    cfg["loras_dir"] = str(tmp_path / "loras")
    settings_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("VGAC_CONFIG", str(settings_path))
    config.reset_settings()
    db.reset_engine()
    reference.reload()
    yield config.get_settings()
    db.reset_engine()
    config.reset_settings()


def own_lora(mine_settings, name: str, label: str, meta: dict[str, str] | None = None) -> dict[str, Any]:
    """Register a picture-edit LoRA the person owns; -> the public entry."""
    from vgac import user_addons

    d = user_addons.mine_dir(create=True)
    (d / name).write_bytes(safetensors_bytes(meta or {"modelspec.architecture": "qwen-image-edit-2511/lora"}))
    return user_addons.register(name, label, ["qwen_edit"])


class FakeClient:
    """Answers the LoRA list (object_info) and the staging call the stages make; counts the questions asked."""

    def __init__(self, listed: list[str], down: bool = False):
        self.listed = list(listed)
        self.down = down  # an engine that is switched off: the question raises a connection error
        self.asked = 0

    async def object_info(self, node_class: str) -> dict[str, Any]:
        self.asked += 1
        if self.down:
            raise ConnectionError("the engine is not running")
        return {"input": {"required": {"lora_name": [list(self.listed), {}]}}}

    async def stage_input(self, path: Any, name: str, *args: Any, **kw: Any) -> str:
        return name


class FakeSC:
    def __init__(self, tmp_path: Path, clients: dict[str, FakeClient], params: dict[str, Any] | None = None,
                 category: str = "humanoid"):
        self.ctx = SimpleNamespace(clients=clients, instance=next(iter(clients), None))
        self.version = SimpleNamespace(id="abcd1234efgh", params=dict(params or {}))
        self.asset = SimpleNamespace(category=category)
        self.parent = None
        self.dir = tmp_path
        self.metrics: dict[str, Any] = {}
        self.models_used: dict[str, Any] = {}
        self.jobs: list[Any] = []

    def set_params(self, **values: Any) -> None:
        self.version.params.update(values)

    def set_metrics(self, **values: Any) -> None:
        self.metrics.update(values)

    def client(self, instance: str | None = None) -> FakeClient:
        return self.ctx.clients[instance or self.ctx.instance]

    def prefix(self, tag: str = "") -> str:
        return f"vgac/{tag}"

    async def stage_input(self, path: Any, name: str, *args: Any, **kw: Any) -> str:
        return name

    async def comfy(self, graph: Any, **kw: Any) -> dict[str, Any]:
        self.jobs.append(graph)
        return {"node": {}}

    async def download_image(self, entry: Any, node: str, path: Path, **kw: Any) -> Path:
        return Path(path)

    async def cutout(self, raw: Path, name: str, **kw: Any) -> dict[str, Any]:
        return {"cutout": Path(raw), "white": Path(raw), "stats": {}}

    def use_model(self, model_id: str) -> None:
        return None


# --- validation ----------------------------------------------------------------------------------------------------------


def test_choices_are_checked_and_limited_to_two_active(mine_settings):
    from vgac import reference

    ok = reference.validate_params("turnaround", {"view_addons": {"mine_a": "normal", "mine_b": "off", "mine_c": "strong"}},
                                   "humanoid")
    assert ok["view_addons"] == {"mine_a": "normal", "mine_b": "off", "mine_c": "strong"}  # two active, one off
    with pytest.raises(ValueError, match="at most 2"):
        reference.validate_params("turnaround", {"view_addons": {"a": "normal", "b": "normal", "c": "subtle"}}, "humanoid")
    with pytest.raises(ValueError, match="must be one of"):
        reference.validate_params("turnaround", {"pose_addons": {"a": "max"}}, "humanoid")
    with pytest.raises(ValueError, match="face_addons"):
        reference.validate_params("prep", {"face_addons": ["a"]}, "humanoid")


def test_pose_and_face_knobs_only_for_humanoids(mine_settings):
    from vgac import reference

    dropped = reference.validate_params("turnaround", {"pose_addons": {"a": "normal"}}, "vehicle")
    assert "pose_addons" not in dropped  # the category leaves the knob out: accepted and dropped


# --- the shared check ----------------------------------------------------------------------------------------------------


@arun
async def test_no_choice_makes_no_engine_call_and_records_nothing_applied(mine_settings, tmp_path):
    from vgac.stages.picture_loras import edit_loras

    client = FakeClient([])
    sc = FakeSC(tmp_path, {"A": client})
    assert await edit_loras(sc, "view_addons", {}) == []
    assert client.asked == 0 and sc.jobs == []
    assert sc.version.params["view_addons_applied"] == {}


@arun
async def test_an_engine_that_is_off_is_skipped_but_the_job_card_must_answer(mine_settings, tmp_path):
    from vgac import reference
    from vgac.graphs.concept import lora_name
    from vgac.stages.picture_loras import edit_loras

    entry = own_lora(mine_settings, "inkstyle.safetensors", "Ink style")
    file_ = reference.addon(entry["id"])["file"]
    choice = {"view_addons": {entry["id"]: "normal"}}
    # card B is switched off: it cannot take work, so the step goes on on the job's card A
    sc = FakeSC(tmp_path, {"A": FakeClient([lora_name(file_)]), "B": FakeClient([], down=True)})
    assert [a["id"] for a in await edit_loras(sc, "view_addons", choice)] == [entry["id"]]
    # the job's own card A does not answer: a plain stop before any job, not a crash inside the step
    sc = FakeSC(tmp_path, {"A": FakeClient([lora_name(file_)], down=True), "B": FakeClient([lora_name(file_)])})
    with pytest.raises(RuntimeError, match="did not answer") as e:
        await edit_loras(sc, "view_addons", choice)
    assert "ConnectionError" not in str(e.value) and sc.jobs == []


@arun
async def test_missing_file_on_any_card_stops_before_the_job_and_names_only_the_label(mine_settings, tmp_path):
    from vgac import reference
    from vgac.graphs.concept import lora_name
    from vgac.stages.picture_loras import edit_loras

    entry = own_lora(mine_settings, "inkstyle.safetensors", "Ink style")
    listed_a = [lora_name(reference.addon(entry["id"])["file"]), "other\\secret.safetensors"]
    a, b = FakeClient(listed_a), FakeClient(["other\\secret.safetensors"])  # card B does not list the file
    sc = FakeSC(tmp_path, {"A": a, "B": b})
    with pytest.raises(RuntimeError) as e:
        await edit_loras(sc, "view_addons", {"view_addons": {entry["id"]: "normal"}})
    msg = str(e.value)
    assert "Ink style" in msg and "secret" not in msg and "inkstyle" not in msg
    assert a.asked == 1 and b.asked == 1 and sc.jobs == []  # both cards asked before anything is queued


@arun
async def test_chosen_lora_on_every_card_is_applied_and_recorded(mine_settings, tmp_path):
    from vgac import reference
    from vgac.graphs.concept import lora_name
    from vgac.stages.picture_loras import edit_loras

    entry = own_lora(mine_settings, "inkstyle.safetensors", "Ink style")
    file_ = reference.addon(entry["id"])["file"]
    cards = {"A": FakeClient([lora_name(file_)]), "B": FakeClient([lora_name(file_)])}
    sc = FakeSC(tmp_path, cards)
    applied = await edit_loras(sc, "pose_addons", {"pose_addons": {entry["id"]: "normal"}})
    assert [a["id"] for a in applied] == [entry["id"]] and applied[0]["value"] == 0.8
    assert sc.version.params["pose_addons_applied"] == {entry["id"]: "normal"}
    assert sc.metrics["pose_addons"]["applied"][0]["id"] == entry["id"]
    assert f"addon:{entry['id']}" in sc.models_used


@arun
async def test_a_painter_lora_or_a_built_in_add_on_is_never_applied_to_picture_edits(mine_settings, tmp_path):
    from vgac.stages.picture_loras import edit_loras

    client = FakeClient([])
    sc = FakeSC(tmp_path, {"A": client})
    applied = await edit_loras(sc, "view_addons", {"view_addons": {"clay_finish": "normal"}})
    assert applied == [] and client.asked == 0  # a concept add-on is not a picture-edit add-on


# --- what each route passes ------------------------------------------------------------------------------------------------


@arun
async def test_side_view_gets_the_view_choice_and_the_bald_front_gets_none(mine_settings, tmp_path, monkeypatch):
    from vgac.stages import turnaround as t

    seen: list[Any] = []

    def spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("loras"))
        return {}

    monkeypatch.setattr(t, "angle_graph", spy)
    sc = FakeSC(tmp_path, {"A": FakeClient([])}, params={"cutout_in_graph": False})
    chosen = [{"id": "mine_x", "file": "VGAC/mine/x.safetensors", "value": 0.8, "trigger": ""}]
    await t._generate_view(sc, "src.png", "left", 1, 0.9, "", 0.0, 1.0, loras=chosen)
    await t._generate_view(sc, "src.png", "left", 1, 0.9, "", 0.0, 1.0)  # the bald front and other callers pass none
    assert seen == [chosen, None]


@arun
async def test_pose_redraw_gets_only_the_pose_choice(mine_settings, tmp_path, monkeypatch):
    from vgac.stages import turnaround as t

    seen: list[Any] = []

    def spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("loras"))
        raise RuntimeError("stop after the spy")

    monkeypatch.setattr(t, "edit_graph", spy)
    monkeypatch.setattr(t, "mesh_stage", lambda: SimpleNamespace(_picture_cards=lambda sc: ["A"]))
    sc = FakeSC(tmp_path, {"A": FakeClient([])})
    chosen = [{"id": "mine_p", "file": "VGAC/mine/p.safetensors", "value": 0.6, "trigger": ""}]
    with pytest.raises(RuntimeError, match="could not be posed"):
        await t._pose_edit(sc, tmp_path / "w.png", "pose", 1, 0, 1024, 1024, loras=chosen)
    assert seen == [chosen]


def test_a_kept_pose_is_reused_only_with_the_same_pose_choice(mine_settings, tmp_path):
    from vgac.stages import turnaround as t

    pid = own_lora(mine_settings, "pose.safetensors", "Pose look")["id"]
    parent = SimpleNamespace(params={"pose_addons_applied": {pid: "normal"}})
    sc = FakeSC(tmp_path, {"A": FakeClient([])})
    sc.parent = parent
    assert t._pose_choice_changed(sc, {"pose_addons": {pid: "normal"}}) is False
    assert t._pose_choice_changed(sc, {"pose_addons": {pid: "strong"}}) is True
    assert t._pose_choice_changed(sc, {}) is True  # the parent had one and this run has none


def test_the_parents_applied_records_are_never_inherited(mine_settings):
    from vgac.stages import turnaround as t

    out = t.inherit({"views": ["front"], "view_addons_applied": {"a": "normal"}, "pose_addons_applied": {"b": "subtle"}}, {})
    assert "view_addons_applied" not in out and "pose_addons_applied" not in out and out["views"] == ["front"]


@arun
async def test_face_closeup_gets_the_face_choice_and_the_sharpening_redraw_gets_none(mine_settings, tmp_path, monkeypatch):
    from vgac.stages import face_detail as fd

    seen: list[Any] = []

    def spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("loras"))
        raise RuntimeError("stop after the spy")

    monkeypatch.setattr(fd, "edit_graph", spy)
    sc = FakeSC(tmp_path, {"A": FakeClient([])})
    chosen = [{"id": "mine_f", "file": "VGAC/mine/f.safetensors", "value": 0.8, "trigger": ""}]
    crop = tmp_path / "crop.png"
    crop.write_bytes(b"")
    with pytest.raises(RuntimeError, match="stop after"):
        await fd.redraw(sc, "A", crop, 1, tmp_path / "out.png", loras=chosen)  # the close-up passes its choice
    with pytest.raises(RuntimeError, match="stop after"):
        await fd.redraw(sc, "A", crop, 1, tmp_path / "sharp.png")  # the sharpening redraw passes none
    assert seen == [chosen, None]


class _Stop(Exception):
    """Raised by a spy to end a stage right after the call under test."""


class _RefineSC(FakeSC):
    async def stage_input(self, path: Any, name: str, *args: Any, **kw: Any) -> str:
        raise _Stop()  # refine writes its parameters before its first staging call


@arun
async def test_refine_keeps_only_the_requests_own_choice_not_the_parents(mine_settings, tmp_path, monkeypatch):
    from vgac import library
    from vgac.stages import concept

    img = tmp_path / "img.png"
    img.write_bytes(b"")
    monkeypatch.setattr(library, "version_file", lambda v, key, settings: img)
    parent = SimpleNamespace(id="par00001", files={"image": img},
                             params={"edit_addons": {"mine_x": "normal"}, "edit_addons_applied": {"mine_x": "normal"}, "seed": 4})

    sc = _RefineSC(tmp_path, {"A": FakeClient([])}, category="humanoid")
    sc.parent = parent
    sc.settings = SimpleNamespace()
    with pytest.raises(_Stop):
        await concept.refine(sc, "make it red", None, None, {})  # the request sent no choice
    assert sc.version.params["edit_addons"] is None and sc.version.params["edit_addons_applied"] is None

    sc2 = _RefineSC(tmp_path, {"A": FakeClient([])}, category="humanoid")
    sc2.parent = parent
    sc2.settings = SimpleNamespace()
    with pytest.raises(_Stop):
        await concept.refine(sc2, "make it red", None, None, {"edit_addons": {"mine_x": "strong"}})
    assert sc2.version.params["edit_addons"] == {"mine_x": "strong"}


def test_stale_kept_views_are_the_ones_drawn_with_other_loras(mine_settings):
    from vgac.stages import turnaround as t

    keep = {"front": {"loras": ["x"]}, "left": {"loras": []}, "back": {"supplied": True, "loras": []}, "right": {}}
    assert t.stale_kept_views(keep, ["x"]) == ["left", "right"]  # a supplied picture is never redrawn
    assert t.stale_kept_views(keep, []) == ["front"]
    assert t.stale_kept_views(None, ["x"]) == []


@arun
async def test_adjust_with_a_changed_view_choice_redraws_every_view(mine_settings, tmp_path, monkeypatch):
    from vgac.stages import turnaround as t

    drawn: dict[str, Any] = {}

    async def spy(sc_, views, params, source, keep=None, **kw):
        drawn.update(views=list(views), keep=keep)

    monkeypatch.setattr(t, "_run_views", spy)
    sc = FakeSC(tmp_path, {"A": FakeClient([])})
    sc.parent = None  # no kept views: adjust draws the views it lists, all of them
    await t.adjust(sc, {"views": ["front", "left", "back", "right", "quarter"], "view_addons": {"mine_x": "strong"}})
    assert drawn["views"] == ["front", "left", "back", "right", "quarter"] and not drawn["keep"]
