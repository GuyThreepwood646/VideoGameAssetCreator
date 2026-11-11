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


def test_project_list_summary(client, fake_execute):
    empty = client.post("/api/projects", json={"name": "Empty"}).json()
    p, a = _project_asset(client)
    row = next(x for x in client.get("/api/projects").json() if x["id"] == empty["id"])
    assert row["asset_count"] == 0 and row["covers"] == [] and row["last_activity"] == empty["created_at"]
    r = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"seed": 5}}).json()
    assert wait_job(client, r["job"]["id"])["status"] == "done"
    row = next(x for x in client.get("/api/projects").json() if x["id"] == p["id"])
    v = client.get(f"/api/versions/{r['version']['id']}").json()
    assert row["asset_count"] == 1 and row["covers"] == [v["files"]["cutout"]]
    assert row["last_activity"] >= p["created_at"]


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
    import base64

    mask = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode()
    r = client.post(f"/api/versions/{vid}/refine", json={"instruction": "red cloak", "mask_png": mask})
    assert r.status_code == 200
    ref = r.json()
    assert ref["job"]["kind"] == "concept.refine" and ref["version"]["parent_version_id"] == vid
    wait_job(client, ref["job"]["id"])
    assert fake_execute[-1]["payload"]["instruction"] == "red cloak" and fake_execute[-1]["payload"]["mask_png"] == mask
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
    assert client.get(f"/api/versions/{second['version']['id']}").json()["status"] == "rejected"
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



def test_style_presets(client):
    presets = client.get("/api/style-presets").json()
    assert len(presets) >= 5 and all({"id", "label", "style_lock", "style_preset"} <= set(p) for p in presets)
    p = client.post("/api/projects", json={"name": "s"}).json()
    lock = presets[0]["style_lock"]
    upd = client.put(f"/api/projects/{p['id']}", json={"style_lock": lock}).json()
    assert upd["style_lock"]["model"] == lock["model"] and upd["style_lock"].get("preset") == presets[0]["id"]


def test_autopilot_runs_all_stages(client):
    p = client.post("/api/projects", json={"name": "ap"}).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "bot", "category": "prop_rigid", "style_preset": "painted", "prompt": "a crate"}).json()
    st = client.post(f"/api/assets/{a['id']}/autopilot", json={"stop_on": "never"}).json()
    assert st["status"] == "running"
    import time
    for _ in range(200):
        st = client.get(f"/api/assets/{a['id']}/autopilot").json()
        if st["status"] in ("done", "error", "paused"):
            break
        time.sleep(0.1)
    assert st["status"] == "done", st
    assert st["stages_done"] == ["concept", "turnaround", "mesh", "prep", "rig", "export"]
    full = client.get(f"/api/assets/{a['id']}").json()
    assert len(full["stages"]["concept"]) == 2 and all(full["selected"].get(s) for s in full["stages"] if full["stages"][s])
    assert full["autopilot"]["status"] == "done"
    assert client.post(f"/api/assets/{a['id']}/autopilot", json={"stop_on": "bogus"}).status_code == 400



def test_autopilot_fix_and_continue(client):
    import time
    p = client.post("/api/projects", json={"name": "apf"}).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "crate", "category": "prop_rigid", "style_preset": "painted", "prompt": "a crate"}).json()
    client.post(f"/api/assets/{a['id']}/autopilot", json={"stop_on": "never"})
    for _ in range(200):
        st = client.get(f"/api/assets/{a['id']}/autopilot").json()
        if st["status"] == "done":
            break
        time.sleep(0.1)
    assert st["status"] == "done"
    # A fix on mesh re-runs mesh as an adjust, unkeeps prep..export and rebuilds them.
    st = client.post(f"/api/assets/{a['id']}/autopilot", json={"stop_on": "never", "fix": {"stage": "mesh", "params": {"target_faces": 1234}}}).json()
    assert st["status"] == "running"
    for _ in range(300):
        st = client.get(f"/api/assets/{a['id']}/autopilot").json()
        if st["status"] in ("done", "error", "paused"):
            break
        time.sleep(0.1)
    assert st["status"] == "done", st
    full = client.get(f"/api/assets/{a['id']}").json()
    meshes = full["stages"]["mesh"]
    assert len(meshes) == 2 and meshes[-1]["selected"] and meshes[-1]["params"].get("target_faces") == 1234
    assert len(full["stages"]["export"]) == 2 and full["stages"]["export"][-1]["selected"]



def test_autopilot_uses_first_build_settings(client):
    """Settings chosen for a step's first build (stage_params) go into that step when Autopilot starts it."""
    import time
    p = client.post("/api/projects", json={"name": "apsp"}).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "crate", "category": "prop_rigid", "style_preset": "painted", "prompt": "a crate"}).json()
    assert client.post(f"/api/assets/{a['id']}/autopilot", json={"stage_params": {"nope": {}}}).status_code == 400
    st = client.post(f"/api/assets/{a['id']}/autopilot", json={"stop_on": "never", "stage_params": {"mesh": {"target_faces": 4321}}}).json()
    assert st["status"] == "running" and st["stage_params"] == {"mesh": {"target_faces": 4321}}
    for _ in range(200):
        st = client.get(f"/api/assets/{a['id']}/autopilot").json()
        if st["status"] in ("done", "error", "paused"):
            break
        time.sleep(0.1)
    assert st["status"] == "done", st
    full = client.get(f"/api/assets/{a['id']}").json()
    assert full["stages"]["mesh"][0]["params"].get("target_faces") == 4321
    assert full["stages"]["prep"][0]["params"].get("target_faces") != 4321  # other steps keep their own settings



def test_feedback_and_gallery(client):
    assert client.get("/api/gallery").json() == []
    p, a = _project_asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    wait_job(client, out["job"]["id"])
    vid = out["version"]["id"]
    r = client.post(f"/api/versions/{vid}/feedback", json={"thumbs": "down", "note": "  too dark "})
    assert r.status_code == 200
    fb = r.json()["metrics"]["feedback"]
    assert fb["thumbs"] == "down" and fb["note"] == "too dark" and fb["at"]
    assert client.post(f"/api/versions/{vid}/feedback", json={"thumbs": "sideways"}).status_code == 422
    assert client.post(f"/api/versions/{vid}/feedback", json={}).json()["metrics"]["feedback"] is None
    # Not in the gallery until export finishes.
    assert client.get("/api/gallery").json() == []
    for stage in ("turnaround", "mesh", "prep", "rig", "export"):
        wait_job(client, client.post(f"/api/assets/{a['id']}/stages/{stage}/run", json={}).json()["job"]["id"])
    g = client.get("/api/gallery").json()
    assert len(g) == 1
    card = g[0]
    assert card["asset_id"] == a["id"] and card["asset_name"] == "knight" and card["project_name"] == "P"
    assert card["thumbnail"].endswith("image.png") and card["glb"].endswith("model.glb") and card["preview"] == card["glb"]
    assert card["fbx"] is None and card["delivered_to"] is None and card["finished_at"]
    assert client.get("/api/library/" + card["thumbnail"]).status_code == 200



def test_security_hardening(client, tmp_path):
    # Foreign Origin on a state-changing request is refused; local and absent origins pass.
    p, a = _project_asset(client)
    url = f"/api/assets/{a['id']}/stages/concept/run"
    assert client.post(url, json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post(url, json={}, headers={"Origin": "http://localhost:5174"}).status_code == 200
    assert client.get("/api/projects", headers={"Origin": "http://evil.example"}).status_code == 200
    # Unknown Host (DNS rebinding) is refused.
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 400
    # The websocket refuses a foreign origin.
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/events", headers={"Origin": "http://evil.example"}) as ws:
            ws.receive_json()
    # The database file is never served even though it sits in the library folder.
    assert client.get("/api/library/vgac.sqlite").status_code in (403, 404)
    # Params are checked against the stage schema.
    assert client.post(url, json={"params": {"width": 99999}}).status_code == 400
    assert client.post(url, json={"params": {"model": "nope"}}).status_code == 400
    assert client.post(url, json={"params": {"readiness_block": "yes"}}).status_code == 400
    out = client.post(url, json={"params": {"width": 1024, "seed": 3}}).json()
    wait_job(client, out["job"]["id"])
    vid = out["version"]["id"]
    assert client.post(f"/api/versions/{vid}/adjust", json={"params": {"steps": 0}}).status_code == 400
    # Masks must be real base64 PNG data.
    assert client.post(f"/api/versions/{vid}/refine", json={"instruction": "x", "mask_png": "not base64!"}).status_code == 400
    assert client.post(f"/api/versions/{vid}/refine", json={"instruction": "x", "mask_png": "aGVsbG8="}).status_code == 400
    import base64

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode()
    assert client.post(f"/api/versions/{vid}/refine", json={"instruction": "x", "mask_png": "data:image/png;base64," + png}).status_code == 200
    # Unity project paths must point at a real Unity project.
    assert client.put(f"/api/projects/{p['id']}", json={"unity_project_path": str(tmp_path)}).status_code == 400
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    assert client.put(f"/api/projects/{p['id']}", json={"unity_project_path": str(tmp_path)}).json()["unity_project_path"]
    assert client.put(f"/api/projects/{p['id']}", json={"unity_project_path": ""}).json()["unity_project_path"] is None


def test_delivery_dir_never_escapes(tmp_path):
    from vgac.stages.export import delivery_dir

    root = tmp_path / "exports"
    for name in ("..", "../../x", "C:\\Windows", "..\\..", ".hidden"):
        d = delivery_dir(root, name, name)
        assert str(d).startswith(str(root.resolve()))
    assert delivery_dir(root, "My Game", "Sir Knight 2") == (root / "My_Game" / "Sir_Knight_2").resolve()



def test_job_id_and_reject_sticks(client):
    p, a = _project_asset(client)
    first = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"slow": True}}).json()
    second = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    # B1: the version knows its job.
    assert first["version"]["job_id"] == first["job"]["id"]
    assert client.get(f"/api/versions/{first['version']['id']}").json()["job_id"] == first["job"]["id"]
    # B2: rejecting a queued version cancels its job and the rejection sticks after the worker looks at it.
    r = client.post(f"/api/versions/{second['version']['id']}/reject").json()
    assert r["status"] == "rejected"
    wait_job(client, first["job"]["id"], timeout=30)
    time.sleep(0.5)
    assert client.get(f"/api/versions/{second['version']['id']}").json()["status"] == "rejected"
    assert client.get(f"/api/jobs/{second['job']['id']}").json()["status"] == "cancelled"
    # Rejecting the running one: the job ends cancelled and the version stays rejected.
    third = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"slow": True}}).json()
    t0 = time.time()
    while client.get(f"/api/jobs/{third['job']['id']}").json()["status"] != "running" and time.time() - t0 < 5:
        time.sleep(0.05)
    client.post(f"/api/versions/{third['version']['id']}/reject")
    assert wait_job(client, third["job"]["id"])["status"] == "cancelled"
    assert client.get(f"/api/versions/{third['version']['id']}").json()["status"] == "rejected"


def test_parent_walkback_only_over_skipped_stages(client):
    p, a = _project_asset(client)
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()["job"]["id"])
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={}).json()["job"]["id"])
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/mesh/run", json={}).json()["job"]["id"])
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/prep/run", json={}).json()["job"]["id"])
    # A humanoid export with no rig kept is refused and names the rig step, not concept.
    r = client.post(f"/api/assets/{a['id']}/stages/export/run", json={})
    assert r.status_code == 409 and "rig" in r.json()["detail"]
    # Bad style presets are refused like bad categories.
    assert client.post(f"/api/projects/{p['id']}/assets", json={"name": "x", "category": "humanoid", "style_preset": "nope", "prompt": "p"}).status_code == 400
    assert client.put(f"/api/assets/{a['id']}", json={"name": ""}).status_code == 422


def test_suggest_and_pause_and_autopilot_stop(client):
    s = client.get("/api/suggest", params={"category": "quadruped", "style": "painted", "platform": "mobile_low"}).json()
    assert s["model"] in ("zturbo", "zbase", "klein", "anima", "pixel", "chroma") and s["budgets"]["triangles"] and s["total_minutes"] > 0
    h = client.post("/api/queue/b/pause", params={"paused": True}).json()
    assert h["b"]["paused"] is True
    assert client.post("/api/queue/b/pause", params={"paused": False}).json()["b"]["paused"] is False
    p, a = _project_asset(client, category="prop_rigid")
    st = client.post(f"/api/assets/{a['id']}/autopilot", json={"stop_on": "never"}).json()
    assert st["status"] == "running"
    st = client.post(f"/api/assets/{a['id']}/autopilot/stop").json()
    assert st["status"] == "paused" and "stopped" in st["reason"] and st["stopped_by_user"] is True



def test_spa_fallback(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vgac.main import SpaStaticFiles

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "x.js").write_text("1", encoding="utf-8")
    app2 = FastAPI()

    @app2.get("/api/ping")
    def ping():
        return {"ok": True}

    app2.mount("/", SpaStaticFiles(directory=str(dist), html=True), name="frontend")
    with TestClient(app2) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/gallery").text == "<html>app</html>"
        assert c.get("/assets/abc").text == "<html>app</html>"
        assert c.get("/assets/x.js").text == "1"
        assert c.get("/api/ping").json() == {"ok": True}
        assert c.get("/api/nope").status_code == 404


def test_stages_serve_option_labels_and_category_knobs(client):
    st = client.get("/api/stages").json()
    prep = {p["name"]: p for p in next(s for s in st if s["id"] == "prep")["adjust_schema"]}
    assert prep["face_yaw"]["option_labels"] == {"auto": "Automatic", "0": "As built", "90": "Turned a quarter to the left",
                                                 "180": "Turned around", "270": "Turned a quarter to the right"}
    assert "material" not in prep["size_m"]["categories"] and prep["size_m"]["by_category"]["quadruped"]["label"] == "How long is it?"
    export = {p["name"]: p for p in next(s for s in st if s["id"] == "export")["adjust_schema"]}
    assert export["clips_library"]["option_labels"]["none"] == "Only its own walk and idle"
    # A select value sent as text is matched to its option.
    p, a = _project_asset(client, category="prop_rigid")
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()["job"]["id"])
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={}).json()["job"]["id"])
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/mesh/run", json={}).json()["job"]["id"])
    r = client.post(f"/api/assets/{a['id']}/stages/prep/run", json={"params": {"face_yaw": "90", "size_m": 0.9}})
    assert r.status_code == 200 and r.json()["version"]["params"]["face_yaw"] == 90
    assert client.post(f"/api/assets/{a['id']}/stages/prep/run", json={"params": {"face_yaw": 45}}).status_code == 400


def test_remove_a_failed_version(client, fake_execute):
    """DELETE /api/versions/{vid}: a failed, stopped or rejected version goes, with its folder and job lines; a
    finished one and one that others were made from stay."""
    from vgac import library
    from vgac.db import Asset, Job

    p, a = _project_asset(client)
    aid = a["id"]
    good = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    wait_job(client, good["job"]["id"])
    bad = client.post(f"/api/assets/{aid}/stages/concept/run", json={"params": {"fail": True}}).json()
    assert wait_job(client, bad["job"]["id"])["status"] == "error"
    with session_factory()() as db:
        folder = library.version_dir(db.get(Asset, aid), db.get(Version, bad["version"]["id"]), create=False)
    assert folder.exists()

    # A finished version is not removable; neither is an unknown one.
    assert client.delete(f"/api/versions/{good['version']['id']}").status_code == 409
    assert client.delete("/api/versions/nope").status_code == 404

    r = client.delete(f"/api/versions/{bad['version']['id']}")
    assert r.status_code == 200 and r.json() == {"ok": True, "deleted": bad["version"]["id"], "deleted_ids": [bad["version"]["id"]]}
    ids = [v["id"] for v in client.get(f"/api/assets/{aid}").json()["stages"]["concept"]]
    assert ids == [good["version"]["id"]]
    assert not folder.exists()
    with session_factory()() as db:
        assert db.get(Job, bad["job"]["id"]) is None and db.get(Job, good["job"]["id"]) is not None
    assert client.get(f"/api/versions/{bad['version']['id']}").status_code == 404

    # A rejected version that another was made from stays; once that one is gone it can go too.
    ref = client.post(f"/api/versions/{good['version']['id']}/refine", json={"instruction": "red cloak"}).json()
    wait_job(client, ref["job"]["id"])
    child = client.post(f"/api/versions/{ref['version']['id']}/refine", json={"instruction": "x", "params": {"fail": True}}).json()
    assert wait_job(client, child["job"]["id"])["status"] == "error"
    client.post(f"/api/versions/{ref['version']['id']}/reject")
    assert client.delete(f"/api/versions/{ref['version']['id']}").status_code == 409
    assert client.delete(f"/api/versions/{child['version']['id']}").status_code == 200
    assert client.delete(f"/api/versions/{ref['version']['id']}").status_code == 200

    # With with_later the version goes together with what was made from it (here a finished change of it).
    ref2 = client.post(f"/api/versions/{good['version']['id']}/refine", json={"instruction": "blue cloak"}).json()
    wait_job(client, ref2["job"]["id"])
    child2 = client.post(f"/api/versions/{ref2['version']['id']}/refine", json={"instruction": "hat"}).json()
    wait_job(client, child2["job"]["id"])
    assert client.delete(f"/api/versions/{ref2['version']['id']}").status_code == 409
    r = client.delete(f"/api/versions/{ref2['version']['id']}?with_later=true")
    assert r.status_code == 200 and set(r.json()["deleted_ids"]) == {ref2["version"]["id"], child2["version"]["id"]}
    assert client.get(f"/api/versions/{child2['version']['id']}").status_code == 404
    # The kept version is untouched and the step still runs.
    kept = client.get(f"/api/versions/{good['version']['id']}").json()
    assert kept["status"] == "done" and kept["selected"] is True

