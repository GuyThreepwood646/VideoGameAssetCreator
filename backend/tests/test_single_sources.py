"""docs/CODE-AUDIT.md D1, D2, D5, D7, D9, H1, H3, P5, P7 and B11: one source for models, stages, estimates,
timestamps and instances; the pipeline module; the shared ComfyUI client; the cheap health check; category skips."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from vgac import guidance, quality, reference
from vgac.graphs import concept

BACKEND = Path(__file__).resolve().parent.parent

MEASURED = {"concept": 25, "turnaround": 180, "mesh": 200, "prep": 20, "rig": 90, "export": 120}  # STATUS.md


@pytest.fixture
def client(settings, monkeypatch):
    from vgac import stages

    async def execute(ctx, kind, payload):  # jobs never run in these tests
        return None

    monkeypatch.setattr(stages, "execute", execute)
    from vgac.main import app

    with TestClient(app) as c:
        yield c


# D1 ---------------------------------------------------------------------------------------------------------------


def test_concept_models_come_from_models_yaml():
    assert reference.concept_models() == ("zturbo", "zbase", "klein", "anima", "pixel", "chroma")
    # The values the literal dicts in graphs/concept.py used to hold.
    assert {m: concept.default_steps(m) for m in reference.concept_models()} == {"zturbo": 8, "zbase": 30, "klein": 4, "anima": 10, "pixel": 8, "chroma": 30}
    assert {m: concept.save_node(m) for m in reference.concept_models()} == {"zturbo": "8", "zbase": "9", "klein": "12", "anima": "9", "pixel": "11", "chroma": "9"}
    assert [m for m in reference.concept_models() if reference.concept_model(m)["negative"]] == ["zbase", "chroma"]
    g = concept.concept_graph("zturbo", "p", 1, "pre")
    assert g["6"]["inputs"]["steps"] == 8
    # Non-concept models are not concept models, and the manifest record keeps its four fields.
    with pytest.raises(ValueError):
        concept.concept_graph("qwen_edit", "p", 1, "pre")
    assert set(reference.model_record("zturbo")) == {"id", "name", "file", "licence", "version"}
    # The concept Adjust knob offers exactly the yaml models.
    schema = next(s for s in reference.stages() if s["id"] == "concept")["adjust_schema"]
    assert next(p for p in schema if p["name"] == "model")["options"] == list(reference.concept_models())
    with pytest.raises(ValueError):
        reference.validate_params("concept", {"model": "nope"})


def test_models_endpoint_and_validation(client):
    models = client.get("/api/models").json()
    assert [m["id"] for m in models] == list(reference.concept_models())
    z = models[0]
    assert z["label"] == "Z-Image Turbo" and z["description"] == "fast painted look" and z["licence"] == "Apache-2.0" and z["default_steps"] == 8
    assert client.post("/api/projects", json={"name": "P", "style_lock": {"model": "nope"}}).status_code == 400
    p = client.post("/api/projects", json={"name": "P", "style_lock": {"model": "chroma"}}).json()
    assert client.put(f"/api/projects/{p['id']}", json={"style_lock": {"model": "nope"}}).status_code == 400
    body = {"name": "x", "category": "humanoid", "style_preset": "painted", "prompt": "a"}
    assert client.post(f"/api/projects/{p['id']}/assets", json={**body, "concept_model": "nope"}).status_code == 400
    a = client.post(f"/api/projects/{p['id']}/assets", json={**body, "concept_model": "klein"}).json()
    r = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"model": "nope"}})
    assert r.status_code == 400 and "model" in r.json()["detail"]
    s = client.get("/api/suggest", params={"style": "painted"}).json()
    assert s["model_label"] == "Z-Image Turbo (fast painted look)"


# D2 ---------------------------------------------------------------------------------------------------------------


def test_stage_order_is_one_tuple_and_estimates_are_measured():
    from vgac import db, stages

    assert db.STAGES is reference.STAGE_ORDER
    assert tuple(stages.STAGE_MODULES) == reference.STAGE_ORDER
    assert tuple(guidance.STAGE_GUIDE) == reference.STAGE_ORDER
    assert {s["id"]: s["estimate_seconds"] for s in reference.stages()} == MEASURED
    assert set(guidance.MATERIAL_ESTIMATES) == set(reference.STAGE_ORDER)
    s = guidance.suggest("humanoid", "painted", "pc_mid", reference.categories(), reference.styles(), reference.platforms())
    assert s["estimates"] == MEASURED


def test_frontend_estimates_match_backend():
    api_ts = (BACKEND.parent / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
    line = next(l for l in api_ts.splitlines() if l.startswith("export const STAGE_ESTIMATE_FALLBACK"))
    for stage, secs in MEASURED.items():
        assert f"{stage}: {secs}" in line, (stage, line)
    help_ts = (BACKEND.parent / "frontend" / "src" / "help.ts").read_text(encoding="utf-8")
    assert "estimate:" not in help_ts


# D5 ---------------------------------------------------------------------------------------------------------------


def test_autopilot_timestamps_end_in_z():
    from vgac import autopilot

    st = autopilot.default_state()
    assert st["updated_at"].endswith("Z")


# H1 ---------------------------------------------------------------------------------------------------------------


def test_autopilot_does_not_import_main():
    code = "import sys, vgac.autopilot; assert 'vgac.main' not in sys.modules, 'autopilot imported main'"
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, check=True)


def test_previous_stage_walks_only_over_skipped_stages():
    from vgac import pipeline

    assert pipeline.previous_stage("material", "mesh") == "concept"
    assert pipeline.previous_stage("humanoid", "mesh") == "turnaround"
    assert pipeline.previous_stage("humanoid", "export") == "rig"


def test_pipeline_errors_map_to_http(client):
    p = client.post("/api/projects", json={"name": "P"}).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "x", "category": "humanoid", "style_preset": "painted", "prompt": "a"}).json()
    r = client.post(f"/api/assets/{a['id']}/stages/mesh/run", json={})
    assert r.status_code == 409 and r.json()["detail"] == "no selected turnaround version to build on"
    r = client.post(f"/api/assets/{a['id']}/stages/bogus/run", json={})
    assert r.status_code == 400
    r = client.post(f"/api/assets/{a['id']}/stages/mesh/run", json={"parent_version_id": "missing"})
    assert r.status_code == 404 and r.json()["detail"] == "version not found"


# H3 ---------------------------------------------------------------------------------------------------------------


def test_stage_instances_come_from_settings(settings):
    from vgac import jobs

    assert [jobs.instance_for(s, "humanoid", settings) for s in reference.STAGE_ORDER] == ["b", "b", "a", "host", "b", "host"]
    assert [jobs.instance_for(s, "material", settings) for s in reference.STAGE_ORDER] == ["b", "b", "b", "host", "host", "host"]
    assert settings.judge_instance == "b" and settings.blender_timeout_seconds == 1800
    jobs.check_stage_instances(settings)
    bad = settings.model_copy(update={"stage_instances": {**settings.stage_instances, "mesh": "z"}})
    with pytest.raises(ValueError):
        jobs.check_stage_instances(bad)
    short = settings.model_copy(update={"stage_instances": {"concept": "b"}})
    with pytest.raises(ValueError):
        jobs.check_stage_instances(short)


def test_max_influences_uses_group_per_vertex():
    info = {"bones": 10, "max_influences": 6}
    assert not quality.rig_checks(info, True)[-1]["passed"]
    c = quality.rig_checks(info, True, group_per_vertex=8)[-1]
    assert c["name"] == "max_influences" and c["passed"] and c["limit"] == 8
    assert quality.TRIANGLE_BUDGET_TOLERANCE == 0.05


# P5 and P7 --------------------------------------------------------------------------------------------------------


def test_comfy_client_keeps_one_http_client():
    from vgac.comfy import ComfyClient

    async def go():
        c = ComfyClient("http://127.0.0.1:9", name="t")
        first = c._client()
        assert c._client() is first
        await c.aclose()
        assert first.is_closed and c._http is None
        assert c._client() is not first
        await c.aclose()

    asyncio.run(go())


def test_vram_summary_is_cached(monkeypatch):
    from vgac.comfy import ComfyClient

    calls = []

    async def fake(self):
        calls.append(1)
        return {"reachable": False, "devices": []}

    monkeypatch.setattr(ComfyClient, "vram_summary", fake)

    async def go():
        c = ComfyClient("http://127.0.0.1:9")
        await c.cached_vram_summary(5.0)
        await c.cached_vram_summary(5.0)
        assert len(calls) == 1
        await c.cached_vram_summary(0.0)
        assert len(calls) == 2

    asyncio.run(go())


def test_health_reuses_queue_clients_and_status_orders(client):
    from vgac import jobs

    q = jobs.active_queue()
    assert q is not None
    calls = []
    for c in q.clients.values():
        orig = c.vram_summary

        async def counted(orig=orig):
            calls.append(1)
            return await orig()

        c.vram_summary = counted  # type: ignore[method-assign]
    client.get("/api/health")
    client.get("/api/health")
    assert len(calls) == len(q.clients)  # the second poll used the cache
    # Queue status: count and the next job the worker would pick, in one query.
    client.post("/api/queue/b/pause", params={"paused": True})
    p = client.post("/api/projects", json={"name": "P"}).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "x", "category": "humanoid", "style_preset": "painted", "prompt": "a"}).json()
    runs = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"count": 2}).json()
    st = client.get("/api/health").json()["queue"]
    assert st["b"]["queued"] == 2 and st["b"]["next_job_id"] == runs[0]["job"]["id"]
    assert st["a"]["queued"] == 0 and st["a"]["next_job_id"] is None
    client.post("/api/queue/b/pause", params={"paused": False})


# B11 --------------------------------------------------------------------------------------------------------------


def test_categories_expose_skips(client):
    cats = {c["id"]: c for c in client.get("/api/categories").json()}
    assert cats["material"]["skips"] == ["turnaround"]
    assert all(c["skips"] == [] for cid, c in cats.items() if cid != "material")


# D7 and D9 --------------------------------------------------------------------------------------------------------


def test_unity_args_one_helper(tmp_path):
    from vgac.stages import export

    lib = tmp_path / "clips.fbx"
    lib.write_bytes(b"x")
    rig = SimpleNamespace(metrics={"bones": 30})
    sc = SimpleNamespace(dir=tmp_path, category={"rig_engine": "skintoken_mixamo"}, stage_version=lambda s: rig,
                         settings=SimpleNamespace(clips_libraries={"ual1": lib}))
    args, flags, warnings = export._unity_args(sc, {"clips_library": "ual1"}, "knight")
    assert flags == ["-nographics", "-vgacHumanoid"] and args["-vgacClips"] == lib and args["-vgacName"] == "knight" and not warnings
    args, flags, warnings = export._unity_args(sc, {"clips_library": "ual2"}, "knight")
    assert "-vgacClips" not in args and warnings == ["clips library 'ual2' not found"]
    sc.category = {"rig_engine": "none"}
    args, flags, warnings = export._unity_args(sc, {}, "box")
    assert flags == ["-nographics"] and "-vgacClips" not in args and not warnings


def test_manifest_provenance_walks_lineage_once():
    from vgac.stages.base import manifest_provenance

    concept_v = SimpleNamespace(id="c", stage="concept", params={"seed": 7}, files={"image": "i.png"}, metrics={"models": [{"id": "zturbo", "licence": "Apache-2.0"}]})
    turn_v = SimpleNamespace(id="t", stage="turnaround", params={}, files={}, metrics={"views": {"front": {"seed": 3}}})
    export_v = SimpleNamespace(id="e", stage="export", params={}, files={}, metrics={})
    sc = SimpleNamespace(iter_lineage=lambda: iter([export_v, turn_v, concept_v]), models_used={"blender": {"id": "blender", "licence": "GPL"}})
    prov = manifest_provenance(sc)
    assert [e["stage"] for e in prov["lineage"]] == ["concept", "turnaround", "export"]
    assert prov["seeds"] == {"concept": 7, "turnaround_views": {"front": 3}}
    assert set(prov["models"]) == {"zturbo", "blender"}
