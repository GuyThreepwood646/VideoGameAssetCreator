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

    loras = tmp_path / "loras"
    loras.mkdir(exist_ok=True)
    machine = tmp_path / "machine.json"
    machine.write_text(json.dumps({"backend": {"lora_folders": [str(loras)]}}), encoding="utf-8")
    monkeypatch.setenv("VGAC_MACHINE", str(machine))
    monkeypatch.setenv("VGAC_CONFIG", str(settings_path))
    config.reset_settings()
    db.reset_engine()
    reference.reload()
    yield config.get_settings()
    db.reset_engine()
    config.reset_settings()


def own_lora(mine_settings, name: str, label: str, meta: dict[str, str] | None = None) -> dict[str, Any]:
    """Register a picture-edit LoRA the person owns; -> the public entry."""
    from vgac import config, user_addons

    d = Path(config.get_settings().lora_folders[0])
    (d / name).write_bytes(safetensors_bytes(meta or {"modelspec.architecture": "qwen-image-edit-2511/lora"}))
    mp = pytest.MonkeyPatch()  # ComfyUI is not running here: its LoRA list is the folder's own files
    mp.setattr(user_addons, "_engine_choices", lambda: [p.name for p in d.glob("*.safetensors")])
    try:
        return user_addons.register(name, "", label, ["qwen_edit"])
    finally:
        mp.undo()


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
    assert "view_addons_applied" not in sc.version.params  # no record written when nothing is chosen (today's params)
    # an earlier run's record is cleared, so it never reads as this run's
    sc.version.params["view_addons_applied"] = {"mine_x": "normal"}
    assert await edit_loras(sc, "view_addons", {}) == []
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


@arun
async def test_add_views_redraws_a_kept_view_drawn_with_other_loras_but_keeps_a_supplied_one(mine_settings, tmp_path,
                                                                                              monkeypatch):
    """The add-views call site: a kept view whose record differs from the choice now in force moves from keep into the
    views drawn; a supplied picture stays kept (it is the person's own and is never redrawn)."""
    from vgac import library
    from vgac.stages import turnaround as t

    pid = own_lora(mine_settings, "view.safetensors", "View look")["id"]
    drawn: dict[str, Any] = {}

    async def spy(sc_, views, params, source, keep=None, **kw):
        drawn.update(views=list(views), keep=dict(keep or {}))

    monkeypatch.setattr(t, "_run_views", spy)
    monkeypatch.setattr(t, "_copy_bald", lambda sc, parent: None)
    monkeypatch.setattr(library, "version_file", lambda v, key, settings: None)
    parent = SimpleNamespace(id="par00001", stage="turnaround", files={}, params={"views": ["front", "left", "right"]},
                             metrics={"views": {"front": {"loras": []}, "left": {"supplied": True, "loras": []},
                                                "right": {"loras": []}}})
    sc = FakeSC(tmp_path, {"A": FakeClient([])})
    sc.settings = SimpleNamespace()
    # no targets of its own (every wanted view is there): only the stale rule can send a view to _run_views
    await t._add_views(sc, parent, None, {"add_views": [], "view_addons": {pid: "normal"}})
    assert drawn["views"] == ["front", "right"]  # both drawn with no LoRA before: redrawn with the choice now in force
    assert set(drawn["keep"]) == {"left"}  # the supplied picture stays kept


def test_clean_edit_choices_checks_the_request_the_way_the_schema_does(mine_settings):
    from vgac import reference

    assert reference.clean_edit_choices({"mine_a": "Normal", "mine_b": "off"}) == {"mine_a": "normal", "mine_b": "off"}
    with pytest.raises(ValueError, match="at most 2"):
        reference.clean_edit_choices({"a": "normal", "b": "normal", "c": "subtle"})
    with pytest.raises(ValueError, match="must be one of"):
        reference.clean_edit_choices({"a": "max"})
    with pytest.raises(ValueError, match="must map"):
        reference.clean_edit_choices(["a"])


@arun
async def test_combining_the_photos_gets_the_combine_choice_and_no_other(mine_settings, tmp_path, monkeypatch):
    """compose.run draws with the LoRAs in its own edit_addons and nothing from the other uses."""
    from vgac import compose
    from vgac.stages import concept

    seen: dict[str, Any] = {}

    def spy(names, prompt, seed, prefix, width=1024, height=1024, loras=None):
        seen["loras"] = loras
        return {}

    async def no_cutout(sc, image):
        return None

    monkeypatch.setattr(compose, "compose_graph", spy)
    monkeypatch.setattr(concept, "build_prompt", lambda sc, p: "a look")
    monkeypatch.setattr(concept, "_cutout", no_cutout)
    from PIL import Image

    from vgac import reference
    from vgac.graphs.concept import lora_name

    pid = own_lora(mine_settings, "combine.safetensors", "Combine look")["id"]
    sc = FakeSC(tmp_path, {"A": FakeClient([lora_name(reference.addon(pid)["file"])])})
    Image.new("RGB", (8, 8)).save(tmp_path / "c1.png")
    files = {"compose_1": tmp_path / "c1.png"}
    sc.file = lambda key: files.get(key)  # type: ignore[assignment]
    sc.register = lambda key, path: files.__setitem__(key, Path(path))  # type: ignore[assignment]
    sc.progress = lambda *a, **k: None  # type: ignore[assignment]
    sc.version.params.update({"compose_roles": [{"role": "character", "note": None}], "words": None,
                              "edit_addons": {pid: "normal"}, "view_addons": {"mine_other": "strong"}})
    sc.settings = SimpleNamespace()
    await compose.run(sc, {})
    assert [a["id"] for a in seen["loras"]] == [pid]  # the combine choice reaches the graph, the other uses' do not
    assert sc.version.params["edit_addons_applied"] == {pid: "normal"}


# --- the face close-up's LoRA check: one retry, then a warning that says the LoRA was not used ------------------------


class FlakyClient(FakeClient):
    """Does not answer the first `fails` questions (a card busy or restarting), then answers like FakeClient."""

    def __init__(self, listed: list[str], fails: int):
        super().__init__(listed)
        self.fails = fails

    async def object_info(self, node_class: str) -> dict[str, Any]:
        if self.fails > 0:
            self.fails -= 1
            self.asked += 1
            raise ConnectionError("the engine did not answer")
        return await super().object_info(node_class)


@arun
async def test_a_raise_then_success_still_uses_the_face_lora(mine_settings, tmp_path, monkeypatch):
    from vgac import reference
    from vgac.graphs.concept import lora_name
    from vgac.stages import picture_loras
    from vgac.stages.picture_loras import edit_loras

    monkeypatch.setattr(picture_loras, "LORA_RETRY_WAIT_S", 0)
    entry = own_lora(mine_settings, "faceskin.safetensors", "Face skin")
    file_ = reference.addon(entry["id"])["file"]
    client = FlakyClient([lora_name(file_)], fails=1)
    sc = FakeSC(tmp_path, {"A": client}, params={"face_addons": {entry["id"]: "normal"}})
    applied = await edit_loras(sc, "face_addons", sc.version.params)
    assert [a["id"] for a in applied] == [entry["id"]]
    assert client.asked == 2  # the first question failed, the retry answered
    assert sc.metrics["face_addons"]["applied"] and sc.jobs == []


@arun
async def test_two_raises_give_the_face_lora_failure_and_the_warning(mine_settings, tmp_path, monkeypatch):
    from vgac import reference
    from vgac.graphs.concept import lora_name
    from vgac.stages import picture_loras
    from vgac.stages.face_detail import face_checks
    from vgac.stages.picture_loras import LoraCheckFailed, edit_loras

    monkeypatch.setattr(picture_loras, "LORA_RETRY_WAIT_S", 0)
    entry = own_lora(mine_settings, "faceskin.safetensors", "Face skin")
    file_ = reference.addon(entry["id"])["file"]
    client = FlakyClient([lora_name(file_)], fails=2)
    sc = FakeSC(tmp_path, {"A": client}, params={"face_addons": {entry["id"]: "normal"}})
    with pytest.raises(LoraCheckFailed, match="did not answer"):
        await edit_loras(sc, "face_addons", sc.version.params)
    assert client.asked == 2 and sc.jobs == []
    # the face pass records it on the close-up, and the prep shows the warning with the fix note
    metrics = {"face_detail": {"ran": False, "skipped": "error", "closeup": {"used": False, "reason": "error", "face_lora_failed": True}}}
    checks = [c for c in face_checks(metrics) if c["id"] == "face_lora"]
    assert len(checks) == 1
    assert checks[0]["ok"] is False and checks[0]["severity"] == "warn"
    from vgac import guidance
    described = guidance.describe_check(checks[0])
    assert "Your face LoRA was not used: the graphics engine did not answer. Run the clean-up again to try again." in str(described)
    assert "Run the clean-up again" in str(described)


def test_no_face_lora_chosen_gives_no_check_and_no_engine_call(mine_settings, tmp_path):
    import asyncio as _asyncio

    from vgac.stages.face_detail import face_checks
    from vgac.stages.picture_loras import edit_loras

    client = FakeClient([])
    sc = FakeSC(tmp_path, {"A": client}, params={})
    assert _asyncio.run(edit_loras(sc, "face_addons", {})) == []
    assert client.asked == 0 and sc.jobs == []
    # no flag on the close-up, so no face_lora check (a close-up that drew normally, or a pass with no face LoRA)
    assert [c for c in face_checks({"face_detail": {"ran": True, "closeup": {"used": True}}}) if c["id"] == "face_lora"] == []
    assert [c for c in face_checks({}) if c["id"] == "face_lora"] == []


@arun
async def test_a_host_job_checks_the_card_its_close_up_is_drawn_on(mine_settings, tmp_path, monkeypatch):
    """The clean-up runs on the host (no ComfyUI client of its own); its close-up is drawn on a card. The check must ask
    that card: asking the host's name raised 'no ComfyUI client' and every close-up was then skipped."""
    from vgac import reference
    from vgac.graphs.concept import lora_name
    from vgac.stages import picture_loras
    from vgac.stages.picture_loras import LoraCheckBug, edit_loras

    monkeypatch.setattr(picture_loras, "LORA_RETRY_WAIT_S", 0)
    entry = own_lora(mine_settings, "faceskin.safetensors", "Face skin")
    file_ = reference.addon(entry["id"])["file"]
    client = FakeClient([lora_name(file_)])
    sc = FakeSC(tmp_path, {"b": client}, params={"face_addons": {entry["id"]: "normal"}})
    sc.ctx.instance = "host"  # the job's own instance has no client
    applied = await edit_loras(sc, "face_addons", sc.version.params, card="b")
    assert [a["id"] for a in applied] == [entry["id"]]
    assert client.asked == 1 and sc.jobs == []
    # without the card the host's name is asked: a code fault with its own text, never the card message
    with pytest.raises(LoraCheckBug, match="could not run"):
        await edit_loras(sc, "face_addons", sc.version.params)


class BrokenClient(FakeClient):
    """Raises the given exception from object_info (a code fault, not an engine that is down)."""

    def __init__(self, error: Exception):
        super().__init__([])
        self.error = error

    async def object_info(self, node_class: str) -> dict[str, Any]:
        self.asked += 1
        raise self.error


@arun
async def test_a_code_fault_fails_with_its_own_text_not_the_card_message(mine_settings, tmp_path, monkeypatch):
    """A code fault (here: no client for the instance the job named) must not hide behind 'the engine did not answer'."""
    from vgac import reference
    from vgac.graphs.concept import lora_name
    from vgac.stages import picture_loras
    from vgac.stages.picture_loras import LoraCheckBug, LoraCheckFailed, edit_loras

    monkeypatch.setattr(picture_loras, "LORA_RETRY_WAIT_S", 0)
    entry = own_lora(mine_settings, "faceskin.safetensors", "Face skin")
    file_ = reference.addon(entry["id"])["file"]
    class NoClientSC(FakeSC):
        def client(self, instance: str | None = None) -> FakeClient:  # the same answer as base.StageContext.client
            name = instance or self.ctx.instance
            if name not in self.ctx.clients:
                raise RuntimeError(f"no ComfyUI client for instance {name!r}")
            return self.ctx.clients[name]

    sc = NoClientSC(tmp_path, {"b": FakeClient([lora_name(file_)])}, params={"face_addons": {entry["id"]: "normal"}})
    sc.ctx.instance = "host"  # the job's own instance: no ComfyUI client (base.StageContext.client raises for it)
    with pytest.raises(LoraCheckBug) as e:
        await edit_loras(sc, "face_addons", sc.version.params)  # no card given: the host's name is asked
    assert "no ComfyUI client for instance" in str(e.value)
    assert not isinstance(e.value, LoraCheckFailed)
    # a non-network error from the card's own answer is a fault too, never the card message
    sc2 = FakeSC(tmp_path, {"b": BrokenClient(ValueError("bad json"))}, params={"face_addons": {entry["id"]: "normal"}})
    with pytest.raises(LoraCheckBug, match="bad json"):
        await edit_loras(sc2, "face_addons", sc2.version.params, card="b")


@arun
async def test_a_connection_error_is_the_card_message_and_a_fault_is_not(mine_settings, tmp_path, monkeypatch):
    from vgac import reference
    from vgac.graphs.concept import lora_name
    from vgac.stages import picture_loras
    from vgac.stages.picture_loras import LoraCheckFailed, edit_loras

    monkeypatch.setattr(picture_loras, "LORA_RETRY_WAIT_S", 0)
    entry = own_lora(mine_settings, "faceskin.safetensors", "Face skin")
    file_ = reference.addon(entry["id"])["file"]
    sc = FakeSC(tmp_path, {"b": BrokenClient(ConnectionError("refused"))}, params={"face_addons": {entry["id"]: "normal"}})
    with pytest.raises(LoraCheckFailed, match="did not answer"):
        await edit_loras(sc, "face_addons", sc.version.params, card="b")
    assert sc.ctx.clients["b"].asked == 2  # the connection error was retried once
