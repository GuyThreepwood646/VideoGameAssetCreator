"""API contract tests with the job execution mocked: the queue runs, but stages.execute is replaced by a fake that
writes files and metrics onto the version like a real stage would."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vgac import library, stages
from vgac.db import Version, session_factory


def wait_job(client: TestClient, job_id: str, timeout: float = 10.0) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {j}")


@pytest.fixture
def fake_execute(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []

    async def execute(ctx, kind, payload):
        calls.append({"kind": kind, "payload": payload, "instance": ctx.instance})
        from vgac.db import Asset

        ctx.progress(0.5, "halfway")
        with session_factory()() as db:
            v = db.get(Version, payload["version_id"])
            a = db.get(Asset, v.asset_id)
            d = library.version_dir(a, v, ctx.settings)
            if payload.get("params", {}).get("fail"):
                raise RuntimeError("boom")
            if payload.get("op") == "judge":
                m = v.metrics
                m["judge"] = {"summary": "ok", "model": "fake"}
                v.metrics = m
                db.commit()
                return
            stage = v.stage
            if stage in ("concept",):
                (d / "image.png").write_bytes(b"png")
                (d / "cutout.png").write_bytes(b"png")
                library.register_files(v, {"image": d / "image.png", "cutout": d / "cutout.png"}, ctx.settings)
                v.metrics = {"seed": v.params.get("seed", 1), "instruction": payload.get("instruction")}
            else:
                (d / "model.glb").write_bytes(b"glTF")
                library.register_files(v, {"glb": d / "model.glb"}, ctx.settings)
                v.metrics = {"checks": [{"name": "triangle_budget", "passed": True, "message": "ok"}]}
            db.commit()
        if payload.get("params", {}).get("slow"):
            await asyncio.sleep(5)

    monkeypatch.setattr(stages, "execute", execute)
    return calls


@pytest.fixture
def client(settings, fake_execute):
    from vgac.main import app

    with TestClient(app) as c:
        yield c


def test_reference_endpoints(client):
    assert len(client.get("/api/categories").json()) == 11
    cat = client.get("/api/categories").json()[0]
    assert set(cat) >= {"id", "label", "rig_engine", "symmetric", "pose_block", "size_m", "parts_default", "budgets"}
    assert len(client.get("/api/styles").json()) == 7
    assert [p["id"] for p in client.get("/api/platforms").json()] == ["mobile_low", "pc_mid", "high"]
    st = client.get("/api/stages").json()
    assert [s["id"] for s in st] == ["concept", "turnaround", "mesh", "prep", "rig", "export"]
    h = client.get("/api/health").json()
    assert h["ok"] and set(h["queue"]) == {"a", "b", "host"}


def test_project_and_asset_crud(client):
    r = client.post("/api/projects", json={"name": "P", "style_lock": {"prompt_block": "painted", "model": "zturbo"}})
    assert r.status_code == 201
    p = r.json()
    assert p["style_lock"]["model"] == "zturbo" and p["platform_target"] == "pc_mid"
    assert client.get("/api/projects").json()[0]["id"] == p["id"]
    r = client.put(f"/api/projects/{p['id']}", json={"platform_target": "high"})
    assert r.json()["platform_target"] == "high"
    assert client.put(f"/api/projects/{p['id']}", json={"platform_target": "nope"}).status_code == 400
    r = client.post(f"/api/projects/{p['id']}/assets", json={"name": "chest", "category": "prop_articulated", "style_preset": "painted", "prompt": "a chest"})
    assert r.status_code == 201
    a = r.json()
    assert a["platform_target"] == "high" and a["parts"] == ["lid"] and a["stages"]["concept"] == [] and a["selected"] == {}
    assert client.post(f"/api/projects/{p['id']}/assets", json={"name": "x", "category": "nope", "style_preset": "painted"}).status_code == 400
    r = client.put(f"/api/assets/{a['id']}", json={"prompt": "a big chest", "parts": ["lid", "lock"]})
    assert r.json()["prompt"] == "a big chest" and r.json()["parts"] == ["lid", "lock"]
    listing = client.get(f"/api/projects/{p['id']}/assets").json()
    assert listing[0]["id"] == a["id"] and "stages" in listing[0] and "selected" in listing[0]
    assert client.delete(f"/api/assets/{a['id']}").json()["ok"]
    assert client.get(f"/api/assets/{a['id']}").status_code == 404
    assert client.delete(f"/api/projects/{p['id']}").json()["ok"]
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def _project_asset(client, category="humanoid"):
    p = client.post("/api/projects", json={"name": "P"}).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "knight", "category": category, "style_preset": "painted", "prompt": "a knight"}).json()
    return p, a


def test_run_concept_then_next_stages(client, fake_execute):
    p, a = _project_asset(client)
    r = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"seed": 5}})
    assert r.status_code == 200
    out = r.json()
    assert set(out) == {"job", "version"}
    assert out["job"]["instance"] == "b" and out["job"]["kind"] == "concept.run" and out["version"]["status"] == "queued"
    assert out["version"]["params"]["seed"] == 5 and out["version"]["params"]["readiness_block"] is True
    j = wait_job(client, out["job"]["id"])
    assert j["status"] == "done" and j["progress"] == 1.0
    v = client.get(f"/api/versions/{out['version']['id']}").json()
    assert v["status"] == "done" and set(v["files"]) == {"image", "cutout"} and v["selected"] is True
    assert client.get("/api/library/" + v["files"]["image"]).status_code == 200
    # Next stage builds on the selected concept, routed to the right instance.
    for stage, instance, kind in (("turnaround", "b", "turnaround.run"), ("mesh", "a", "mesh.run"), ("prep", "host", "prep.run"), ("rig", "b", "rig.run"), ("export", "host", "export.run")):
        r = client.post(f"/api/assets/{a['id']}/stages/{stage}/run", json={})
        assert r.status_code == 200, (stage, r.text)
        out = r.json()
        assert out["job"]["instance"] == instance and out["job"]["kind"] == kind
        assert wait_job(client, out["job"]["id"])["status"] == "done"
    full = client.get(f"/api/assets/{a['id']}").json()
    assert set(full["selected"]) == {"concept", "turnaround", "mesh", "prep", "rig", "export"}
    assert full["stages"]["export"][0]["parent_version_id"] == full["selected"]["rig"]
    assert full["stages"]["export"][0]["metrics"]["checks"][0]["name"] == "triangle_budget"


def test_run_without_parent_is_409_and_count(client):
    p, a = _project_asset(client)
    assert client.post(f"/api/assets/{a['id']}/stages/mesh/run", json={}).status_code == 409
    assert client.post(f"/api/assets/{a['id']}/stages/nope/run", json={}).status_code == 400
    r = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"count": 3, "params": {"seed": 10}})
    out = r.json()
    assert isinstance(out, list) and len(out) == 3
    assert [o["version"]["params"]["seed"] for o in out] == [10, 11, 12]
    for o in out:
        wait_job(client, o["job"]["id"])
    full = client.get(f"/api/assets/{a['id']}").json()
    assert len(full["stages"]["concept"]) == 3
    assert sum(1 for v in full["stages"]["concept"] if v["selected"]) == 1
    # Mesh stage on the second concept explicitly.
    second = full["stages"]["concept"][1]["id"]
    client.post(f"/api/versions/{second}/select")
    assert client.get(f"/api/assets/{a['id']}").json()["selected"]["concept"] == second
    r = client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={"parent_version_id": second})
    assert r.json()["version"]["parent_version_id"] == second


def test_refine_adjust_reject_judge(client, fake_execute):
    p, a = _project_asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    wait_job(client, out["job"]["id"])
    vid = out["version"]["id"]
    r = client.post(f"/api/versions/{vid}/refine", json={"instruction": "red cloak", "mask_png": "aGVsbG8="})
    assert r.status_code == 200
    ref = r.json()
    assert ref["job"]["kind"] == "concept.refine" and ref["version"]["parent_version_id"] == vid
    wait_job(client, ref["job"]["id"])
    assert fake_execute[-1]["payload"]["instruction"] == "red cloak" and fake_execute[-1]["payload"]["mask_png"] == "aGVsbG8="
    assert client.get(f"/api/versions/{ref['version']['id']}").json()["metrics"]["instruction"] == "red cloak"
    r = client.post(f"/api/versions/{vid}/adjust", json={"params": {"seed": 99}})
    adj = r.json()
    assert adj["job"]["kind"] == "concept.adjust" and adj["version"]["params"]["seed"] == 99
    wait_job(client, adj["job"]["id"])
    rej = client.post(f"/api/versions/{adj['version']['id']}/reject").json()
    assert rej["status"] == "rejected" and rej["selected"] is False
    # metrics judge is synchronous; local judge is a job on instance b.
    v = client.post(f"/api/versions/{vid}/judge", json={"mode": "metrics"}).json()
    assert v["id"] == vid
    v = client.post(f"/api/versions/{vid}/judge", json={"mode": "local"}).json()
    assert v["judge_job"]["instance"] == "b" and v["judge_job"]["kind"] == "concept.judge"
    wait_job(client, v["judge_job"]["id"])
    v = client.get(f"/api/versions/{vid}").json()
    assert v["metrics"]["judge"]["model"] == "fake" and v["status"] == "done"
    assert client.post(f"/api/versions/{vid}/judge", json={"mode": "nope"}).status_code == 400


def test_job_error_marks_version_error(client):
    p, a = _project_asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"fail": True}}).json()
    j = wait_job(client, out["job"]["id"])
    assert j["status"] == "error" and "boom" in j["error"]
    v = client.get(f"/api/versions/{out['version']['id']}").json()
    assert v["status"] == "error" and "boom" in v["error"]
    assert client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={}).status_code == 409


def test_cancel_running_and_queued(client):
    p, a = _project_asset(client)
    first = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"slow": True}}).json()
    second = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    t0 = time.time()
    while client.get(f"/api/jobs/{first['job']['id']}").json()["status"] != "running" and time.time() - t0 < 5:
        time.sleep(0.05)
    c2 = client.post(f"/api/jobs/{second['job']['id']}/cancel").json()
    assert c2["status"] == "cancelled"
    assert client.get(f"/api/versions/{second['version']['id']}").json()["status"] == "error"
    client.post(f"/api/jobs/{first['job']['id']}/cancel")
    j = wait_job(client, first["job"]["id"])
    assert j["status"] == "cancelled"
    jobs = client.get("/api/jobs", params={"asset_id": a["id"], "status": "cancelled"}).json()
    assert {j["id"] for j in jobs} == {first["job"]["id"], second["job"]["id"]}
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_material_routing(client, fake_execute):
    p, a = _project_asset(client, category="material")
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    assert out["job"]["kind"] == "material.concept.run" and out["job"]["instance"] == "b"
    wait_job(client, out["job"]["id"])
    for stage, instance in (("turnaround", "b"), ("mesh", "b"), ("prep", "host"), ("rig", "host"), ("export", "host")):
        out = client.post(f"/api/assets/{a['id']}/stages/{stage}/run", json={}).json()
        assert out["job"]["instance"] == instance, stage
        wait_job(client, out["job"]["id"])


def test_websocket_events(client):
    p, a = _project_asset(client)
    with client.websocket_connect("/api/ws/events") as ws:
        assert ws.receive_json()["type"] == "hello"
        out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
        seen_types, seen_statuses = set(), set()
        for _ in range(20):
            ev = ws.receive_json()
            seen_types.add(ev["type"])
            if ev["type"] == "job":
                seen_statuses.add(ev["job"]["status"])
            if ev["type"] == "version" and ev["version"]["status"] == "done":
                break
        assert seen_types == {"job", "version"}
        assert {"queued", "running", "done"} <= seen_statuses
        wait_job(client, out["job"]["id"])


def test_library_path_traversal_blocked(client):
    assert client.get("/api/library/../config.yaml").status_code in (403, 404)
    assert client.get("/api/library/nothing.png").status_code == 404



def test_variants(client, tmp_path):
    p = client.post("/api/projects", json={"name": "v"}).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "knight", "category": "humanoid", "style_preset": "painted", "prompt": "a knight"}).json()
    # No concept yet: 409.
    assert client.post(f"/api/assets/{a['id']}/variants", json={"change": "winter cloak"}).status_code == 409
    run = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    wait_job(client, run["job"]["id"])
    res = client.post(f"/api/assets/{a['id']}/variants", json={"change": "winter cloak"})
    assert res.status_code == 201, res.text
    body = res.json()
    v = body["asset"]
    assert v["base_asset_id"] == a["id"] and v["variant_change"] == "winter cloak" and v["project_id"] == p["id"]
    assert v["prompt"].endswith("winter cloak") and v["name"].startswith("knight - ")
    assert body["version"]["stage"] == "concept" and body["job"]["kind"].startswith("concept")
    full = client.get(f"/api/assets/{v['id']}").json()
    concepts = full["stages"]["concept"]
    assert len(concepts) == 2 and concepts[0]["status"] == "done" and concepts[0]["params"]["copied_from_asset"] == a["id"]
    assert client.post(f"/api/assets/{a['id']}/variants", json={"change": "  "}).status_code == 400
    listed = client.get(f"/api/projects/{p['id']}/assets").json()
    assert any(x["base_asset_id"] == a["id"] for x in listed)
