"""Your LoRAs per step and per asset (lora_defaults.py): which LoRAs start on for which kinds of assets, the defaults filled
into a new version's LoRA settings, the job getting them, and that a LoRA switched off never reaches the queued graph."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from test_api import fake_execute  # noqa: F401  (fixture)
from test_picture_loras import FakeClient, FakeSC, arun, mine_settings, own_lora  # noqa: F401  (fixture)


def painter_lora(mine_settings, name: str, label: str) -> dict[str, Any]:  # noqa: F811
    """A painter LoRA (step 1 picture) the person owns."""
    from pathlib import Path

    from test_picture_loras import safetensors_bytes
    from vgac import config, user_addons

    d = Path(config.get_settings().lora_folders[0])
    (d / name).write_bytes(safetensors_bytes({"ss_base_model_version": "zimage"}))
    mp = pytest.MonkeyPatch()
    mp.setattr(user_addons, "_engine_choices", lambda: [p.name for p in d.glob("*.safetensors")])
    try:
        return user_addons.register(name, "", label, ["zturbo"])
    finally:
        mp.undo()


# --- saving which LoRAs start on ----------------------------------------------------------------------------------


def test_start_on_is_saved_listed_and_checked(mine_settings):  # noqa: F811
    from vgac import user_addons

    a = own_lora(mine_settings, "ink.safetensors", "Ink look")
    assert a["start_on"] == {"categories": [], "uses": []}  # nothing starts on until the person says so
    out = user_addons.set_start_on(a["id"], ["humanoid"], ["view_addons", "face_addons"])
    assert out["start_on"] == {"categories": ["humanoid"], "uses": ["view_addons", "face_addons"]}
    listed = next(x for x in user_addons.listing()["addons"] if x["id"] == a["id"])
    assert listed["start_on"] == out["start_on"]

    with pytest.raises(user_addons.AddonFileError, match="kinds of assets"):
        user_addons.set_start_on(a["id"], ["dragon"], ["view_addons"])
    with pytest.raises(user_addons.AddonFileError, match="steps"):
        user_addons.set_start_on(a["id"], ["humanoid"], ["mesh"])
    with pytest.raises(user_addons.AddonFileError, match="at least one step"):
        user_addons.set_start_on(a["id"], ["humanoid"], [])
    with pytest.raises(user_addons.AddonFileError, match="not on your list"):
        user_addons.set_start_on("mine_nope", ["humanoid"], ["view_addons"])
    # Turning every kind off is allowed and starts it on nowhere.
    assert user_addons.set_start_on(a["id"], [], [])["start_on"] == {"categories": [], "uses": []}


def test_one_step_and_kind_takes_at_most_two_lora_defaults(mine_settings):  # noqa: F811
    from vgac import user_addons

    ids = [own_lora(mine_settings, f"l{i}.safetensors", f"Look {i}")["id"] for i in range(3)]
    user_addons.set_start_on(ids[0], ["humanoid"], ["view_addons"])
    user_addons.set_start_on(ids[1], ["humanoid", "quadruped"], ["view_addons"])
    with pytest.raises(user_addons.AddonFileError, match="at most 2"):
        user_addons.set_start_on(ids[2], ["humanoid"], ["view_addons"])
    # Another step, or another kind of asset, still has room.
    user_addons.set_start_on(ids[2], ["humanoid"], ["pose_addons"])
    user_addons.set_start_on(ids[2], ["prop_rigid"], ["view_addons"])


def test_the_api_saves_start_on(mine_settings):  # noqa: F811
    from fastapi.testclient import TestClient

    from vgac.main import app

    a = own_lora(mine_settings, "api.safetensors", "Api look")
    with TestClient(app) as c:
        r = c.put(f"/api/my-addons/{a['id']}/start-on", json={"categories": ["humanoid"], "uses": ["view_addons"]})
        assert r.status_code == 200 and r.json()["start_on"]["categories"] == ["humanoid"]
        bad = c.put(f"/api/my-addons/{a['id']}/start-on", json={"categories": ["nope"], "uses": ["view_addons"]})
        assert bad.status_code == 400 and "kinds of assets" in bad.json()["detail"]


# --- the defaults filled into a version's settings ----------------------------------------------------------------------


def test_fill_gives_each_step_only_its_own_defaults_for_the_assets_kind(mine_settings):  # noqa: F811
    from vgac import lora_defaults, user_addons

    view = own_lora(mine_settings, "view.safetensors", "View look")["id"]
    face = own_lora(mine_settings, "face.safetensors", "Face look")["id"]
    paint = painter_lora(mine_settings, "paint.safetensors", "Paint look")["id"]
    user_addons.set_start_on(view, ["humanoid", "prop_rigid"], ["view_addons"])
    user_addons.set_start_on(face, ["humanoid"], ["face_addons", "edit_addons"])
    user_addons.set_start_on(paint, ["humanoid"], [])

    # Each filled setting also lists the LoRAs that are on because of their default (the step form says so).
    assert lora_defaults.fill("turnaround", "humanoid", {}) == {"view_addons": {view: "normal"}, "view_addons_by_default": [view]}
    assert lora_defaults.fill("turnaround", "prop_rigid", {})["view_addons"] == {view: "normal"}
    assert lora_defaults.fill("turnaround", "quadruped", {}) == {}  # not a kind it starts on for
    assert lora_defaults.fill("prep", "humanoid", {}) == {"face_addons": {face: "normal"}, "face_addons_by_default": [face]}
    assert lora_defaults.fill("concept", "humanoid", {})["addons"] == {paint: "normal"}
    assert lora_defaults.fill("concept", "humanoid", {}, knobs=["edit_addons"])["edit_addons"] == {face: "normal"}
    assert lora_defaults.fill("mesh", "humanoid", {"seed": 1}) == {"seed": 1}  # a step without LoRAs is left alone
    # The person's own choice, even "none of your LoRAs", is never changed.
    assert lora_defaults.fill("turnaround", "humanoid", {"view_addons": {}}) == {"view_addons": {}}
    assert lora_defaults.fill("turnaround", "humanoid", {"view_addons": {view: "strong"}}) == {"view_addons": {view: "strong"}}
    # A LoRA the person changed (another strength) stops being "on by default"; one left as it was stays so.
    changed = lora_defaults.fill("turnaround", "humanoid", {"view_addons": {view: "strong"}, "view_addons_by_default": [view]})
    assert changed["view_addons_by_default"] == []
    kept = lora_defaults.fill("turnaround", "humanoid", {"view_addons": {view: "normal"}, "view_addons_by_default": [view]})
    assert kept["view_addons_by_default"] == [view]
    # A list sent by the form is checked the same way: an id that is not a default (or not on) is dropped.
    sent = lora_defaults.fill("turnaround", "humanoid", {"view_addons": {view: "normal", face: "strong"}, "view_addons_by_default": [view, face, "mine_x"]})
    assert sent["view_addons_by_default"] == [view]
    # Filling never writes what a run applied: that is the run's own record.
    assert not any(k.endswith("_applied") for k in lora_defaults.fill("prep", "humanoid", {}))


def test_new_version_records_the_defaults_and_the_job_gets_them(mine_settings, monkeypatch):  # noqa: F811
    from vgac import db as dbm, firstrun, pipeline, user_addons

    view = own_lora(mine_settings, "nv.safetensors", "Nv look")["id"]
    user_addons.set_start_on(view, ["humanoid"], ["view_addons"])
    sent: list[dict[str, Any]] = []

    class Q:
        settings, supervisor, disabled = None, None, set()

        def place(self, stage, category, params=None):
            return "A", None

        def submit(self, instance, kind, aid, vid, body, message=None):
            sent.append(body)
            return SimpleNamespace(id="j")

    monkeypatch.setattr(pipeline, "queue", lambda: Q())
    monkeypatch.setattr(firstrun, "blockers", lambda *a, **k: [])
    dbm.init_db()
    with dbm.session_factory()() as s:
        p = dbm.Project(name="p")
        s.add(p)
        s.commit()
        a = dbm.Asset(project_id=p.id, name="hero", category="humanoid", style_preset="stylized_pbr", platform_target="pc", prompt="x")
        s.add(a)
        s.commit()
        v = pipeline.new_version(s, a, "turnaround", None, {})
        assert v.params["view_addons"] == {view: "normal"}  # recorded on the version, so later versions see it
        assert v.params["view_addons_by_default"] == [view]
        pipeline.submit(s, a, v, "run", {"params": {}})
        assert sent[-1]["params"]["view_addons"] == {view: "normal"} and sent[-1]["params"]["view_addons_by_default"] == [view]
        # A child that inherits the parent's choice keeps it; one with the LoRA switched off stays off.
        off = pipeline.new_version(s, a, "turnaround", v, {**v.params, "view_addons": {}})
        assert off.params["view_addons"] == {} and off.params["view_addons_by_default"] == []
        # An adjust that keeps the LoRA as it was keeps it "on by default"; the parent's record is not changed.
        same = pipeline.new_version(s, a, "turnaround", v, {**v.params, "seed": 3})
        assert same.params["view_addons_by_default"] == [view] and v.params["view_addons_by_default"] == [view]
        pipeline.submit(s, a, off, "adjust", {"params": {"view_addons": {}}})
        assert sent[-1]["params"]["view_addons"] == {} and sent[-1]["params"]["view_addons_by_default"] == []
        # A paint job does not take step settings: nothing is added to it.
        pipeline.submit(s, a, v, "paint", {})
        assert "params" not in sent[-1]


# --- the queued graph ---------------------------------------------------------------------------------------------------


def _lora_files(graph: dict[str, Any]) -> list[str]:
    return [n["inputs"]["lora_name"] for n in graph.values() if isinstance(n, dict) and "lora_name" in (n.get("inputs") or {})]


@arun
async def test_a_lora_switched_off_is_not_in_the_submitted_graph(mine_settings, tmp_path):  # noqa: F811
    from vgac import lora_defaults, reference, user_addons
    from vgac.graphs.concept import lora_name
    from vgac.graphs.qwen_edit import edit_graph
    from vgac.stages.picture_loras import edit_loras

    on = own_lora(mine_settings, "on.safetensors", "On look")["id"]
    off = own_lora(mine_settings, "off.safetensors", "Off look")["id"]
    for aid in (on, off):
        user_addons.set_start_on(aid, ["humanoid"], ["view_addons"])
    files = {aid: lora_name(reference.addon(aid)["file"]) for aid in (on, off)}
    listed = list(files.values())

    # Both start on: both are chained.
    params = lora_defaults.fill("turnaround", "humanoid", {})
    sc = FakeSC(tmp_path, {"A": FakeClient(listed)}, params=params)
    g = edit_graph("src.png", "turn", 1, "p", loras=await edit_loras(sc, "view_addons", params))
    assert set(files.values()) <= set(_lora_files(g))

    # The person switched one off for this run (the form sends the others only): it is not in the graph at all.
    params = lora_defaults.fill("turnaround", "humanoid", {"view_addons": {on: "subtle"}})
    sc = FakeSC(tmp_path, {"A": FakeClient(listed)}, params=params)
    g = edit_graph("src.png", "turn", 1, "p", loras=await edit_loras(sc, "view_addons", params))
    assert files[on] in _lora_files(g) and files[off] not in _lora_files(g)
    assert sc.version.params["view_addons_applied"] == {on: "subtle"}  # the run records what it used

    # Switched off explicitly ("off") gives the same; none on gives a graph without any of the person's LoRAs.
    for choice in ({on: "off", off: "off"}, {}):
        params = lora_defaults.fill("turnaround", "humanoid", {"view_addons": choice})
        sc = FakeSC(tmp_path, {"A": FakeClient(listed)}, params=params)
        g = edit_graph("src.png", "turn", 1, "p", loras=await edit_loras(sc, "view_addons", params))
        assert not set(files.values()) & set(_lora_files(g))


def test_a_refine_and_a_step_run_through_the_api_use_the_defaults_and_keep_a_choice(mine_settings, fake_execute):  # noqa: F811
    """The API routes: a refine with no choice gets the refine defaults, one with a choice keeps it; step 2's run gets the
    side view defaults on its version and in its job."""
    from fastapi.testclient import TestClient

    from test_api import _project_asset, wait_job
    from vgac import user_addons
    from vgac.main import app

    ink = own_lora(mine_settings, "ref.safetensors", "Ref look")["id"]
    user_addons.set_start_on(ink, ["humanoid"], ["edit_addons", "view_addons"])
    with TestClient(app) as c:
        _, a = _project_asset(c)
        out = c.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
        wait_job(c, out["job"]["id"])
        vid = out["version"]["id"]
        ref = c.post(f"/api/versions/{vid}/refine", json={"instruction": "red cloak"}).json()
        assert ref["version"]["params"]["edit_addons"] == {ink: "normal"} and ref["version"]["params"]["edit_addons_by_default"] == [ink]
        wait_job(c, ref["job"]["id"])
        assert fake_execute[-1]["payload"]["params"]["edit_addons"] == {ink: "normal"}
        mine = c.post(f"/api/versions/{vid}/refine", json={"instruction": "blue", "edit_addons": {}}).json()
        assert mine["version"]["params"]["edit_addons"] == {} and not mine["version"]["params"].get("edit_addons_by_default")
        wait_job(c, mine["job"]["id"])
        assert fake_execute[-1]["payload"]["params"]["edit_addons"] == {}
        run = c.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={}).json()
        assert run["version"]["params"]["view_addons"] == {ink: "normal"}
        wait_job(c, run["job"]["id"])
        assert fake_execute[-1]["payload"]["params"]["view_addons"] == {ink: "normal"}


def test_the_limit_refusal_names_the_loras_and_uses_the_apps_category_name(mine_settings):  # noqa: F811
    from vgac import user_addons

    a, b, c = (own_lora(mine_settings, f"n{i}.safetensors", f"Name {i}")["id"] for i in range(3))
    user_addons.set_start_on(a, ["humanoid"], ["view_addons"])
    user_addons.set_start_on(b, ["humanoid"], ["view_addons"])
    with pytest.raises(user_addons.AddonFileError) as e:
        user_addons.set_start_on(c, ["humanoid"], ["view_addons"])
    assert str(e.value).startswith("Name 0 and Name 1 are already on by default for characters in the side views.")
