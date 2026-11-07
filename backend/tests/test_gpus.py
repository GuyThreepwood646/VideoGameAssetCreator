"""Graphics cards on and off, and what each card may do: routing, moving queued and running jobs, persistence in
prefs.json, and a restart that keeps a card off. Job execution is a fake (as in test_api.py)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vgac import comfy, jobs, library, prefs, stages
from vgac.config import InstanceConfig
from vgac.db import Asset, Version, session_factory


def wait_job(client: TestClient, job_id: str, timeout: float = 20.0, until=("done", "error", "cancelled")) -> dict[str, Any]:
    t0 = time.time()
    j: dict[str, Any] = {}
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in until:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach {until}: {j}")


@pytest.fixture
def fake_execute(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []

    async def execute(ctx, kind, payload):
        calls.append({"kind": kind, "instance": ctx.instance, "job_id": ctx.job_id})
        with session_factory()() as db:
            v = db.get(Version, payload["version_id"])
            a = db.get(Asset, v.asset_id)
            d = library.version_dir(a, v, ctx.settings)
            (d / "image.png").write_bytes(b"png")
            library.register_files(v, {"image": d / "image.png"}, ctx.settings)
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


def _asset(client: TestClient) -> dict[str, Any]:
    p = client.post("/api/projects", json={"name": "P"}).json()
    return client.post(f"/api/projects/{p['id']}/assets", json={"name": "Knight", "category": "humanoid", "style_preset": "painted", "prompt": "a knight"}).json()


def _gpu(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(r for r in rows if r["instance"] == name)


# --- route() -----------------------------------------------------------------------------------------------------


def test_route_preferences_and_fallbacks(settings):
    r = jobs.route
    # Everything on: the stage tables.
    assert [r(s, "humanoid", settings) for s in ("concept", "turnaround", "mesh", "prep", "rig", "export")] == ["b", "b", "a", "host", "b", "host"]
    assert r("judge", "humanoid", settings) == "b"
    # b off: pictures, judge and material move to a; rig has nowhere to go.
    off_b = {"b"}
    assert r("concept", "humanoid", settings, off_b) == "a"
    assert r("turnaround", "humanoid", settings, off_b) == "a"
    assert r("judge", "humanoid", settings, off_b) == "a"
    assert r("mesh", "material", settings, off_b) == "a"
    assert r("rig", "humanoid", settings, off_b) is None
    # a off: mesh moves to b.
    assert r("mesh", "humanoid", settings, {"a"}) == "b"
    # Both off: nothing on a card runs; the host still does.
    assert r("concept", "humanoid", settings, {"a", "b"}) is None
    assert r("prep", "humanoid", settings, {"a", "b"}) == "host"
    assert r("rig", "material", settings, {"a", "b"}) == "host"
    # Allowed kinds: b may not make pictures -> a; neither may -> None; a may never rig, whatever is ticked.
    assert r("concept", "humanoid", settings, (), {"b": ["model", "rigging", "review"]}) == "a"
    assert r("concept", "humanoid", settings, (), {"a": ["model"], "b": ["model"]}) is None
    assert r("rig", "humanoid", settings, (), {"b": ["pictures"], "a": ["pictures", "model", "rigging", "review"]}) is None
    assert r("judge", "humanoid", settings, (), {"b": ["pictures"]}) == "a"
    assert r("mesh", "humanoid", settings, {"b"}, {"a": ["pictures"]}) is None
    # Capabilities: config intersected with allowed kinds.
    assert jobs.capabilities("a", settings) == ["concept", "turnaround", "mesh", "material", "judge"]
    assert jobs.capabilities("host", settings) == []
    assert jobs.effective_capabilities("b", settings, {"b": ["rigging"]}) == ["rig"]
    assert jobs.possible_kinds("a", settings) == ["pictures", "model", "review"]
    # An instance missing from instance_capabilities can run what the stage tables give it.
    bare = settings.model_copy(update={"instance_capabilities": {}})
    assert set(jobs.capabilities("a", bare)) == {"mesh"}
    assert jobs.route("concept", "humanoid", bare, {"b"}) is None


def test_notes_name_the_card(settings):
    named = settings.model_copy(update={"instances": {
        "a": InstanceConfig(url="http://127.0.0.1:9", gpu="RTX 3060"),
        "b": InstanceConfig(url="http://127.0.0.1:10", gpu="RTX 3080")}})
    assert jobs.gpu_note("b", named) == "Rigging only runs on this card."
    assert jobs.gpu_note("a", named) == "The other card can take over everything this card does."
    assert jobs.kind_notes("a", named) == {"rigging": "Rigging only runs on the RTX 3080's instance."}
    assert jobs.kind_notes("b", named) == {}
    assert jobs.waiting_message("b", named) == "Waiting for RTX 3080 to be turned back on"


# --- moving jobs -------------------------------------------------------------------------------------------------


def test_disable_moves_queued_jobs_and_parks_rig(client, fake_execute):
    a = _asset(client)
    client.post("/api/queue/b/pause", params={"paused": True})
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"count": 2}).json()
    assert [o["job"]["instance"] for o in out] == ["b", "b"]
    q = jobs.active_queue()
    rig = q.submit("b", "rig.run", a["id"], None, {"version_id": None})
    rows = client.put("/api/gpus/b", json={"enabled": False}).json()
    assert _gpu(rows, "b")["enabled"] is False and _gpu(rows, "a")["enabled"] is True
    for o in out:
        j = wait_job(client, o["job"]["id"])
        assert j["status"] == "done" and j["instance"] == "a"
        assert client.get(f"/api/versions/{o['version']['id']}").json()["status"] == "done"
    assert {c["instance"] for c in fake_execute} == {"a"}
    parked = client.get(f"/api/jobs/{rig.id}").json()
    assert parked["status"] == "queued" and parked["instance"] == "b"
    assert parked["message"] == "Waiting for test to be turned back on"
    b = _gpu(client.get("/api/gpus").json(), "b")
    assert b["queued"] == 1 and b["current_job_id"] is None and b["note"] == "Rigging only runs on this card."
    # A new concept goes straight to a while b is off; a new rig job waits on b with the message.
    nxt = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    assert nxt["job"]["instance"] == "a"
    wait_job(client, nxt["job"]["id"])
    # Turning b back on clears the message on the parked job; new pictures go to b again.
    client.post(f"/api/jobs/{rig.id}/cancel")
    client.post("/api/queue/b/pause", params={"paused": False})
    rows = client.put("/api/gpus/b", json={"enabled": True}).json()
    assert _gpu(rows, "b")["enabled"] is True
    again = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    assert again["job"]["instance"] == "b"
    j = wait_job(client, again["job"]["id"])
    assert j["status"] == "done" and j["instance"] == "b"


def test_running_job_moves_when_card_turned_off(client, fake_execute, monkeypatch: pytest.MonkeyPatch):
    freed: list[str] = []

    async def free(self):
        freed.append(self.name)
        return True

    monkeypatch.setattr(comfy.ComfyClient, "free", free)
    a = _asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"slow": True}}).json()
    wait_job(client, out["job"]["id"], timeout=5, until=("running",))
    assert _gpu(client.get("/api/gpus").json(), "b")["current_job"]["asset_name"] == "Knight"
    client.put("/api/gpus/b", json={"enabled": False})
    j = wait_job(client, out["job"]["id"], timeout=20)
    assert j["status"] == "done" and j["instance"] == "a"
    assert [c["instance"] for c in fake_execute] == ["b", "a"]
    assert client.get(f"/api/versions/{out['version']['id']}").json()["status"] == "done"
    assert "b" in freed
    # Back on: the next picture runs on b.
    client.put("/api/gpus/b", json={"enabled": True})
    nxt = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    assert wait_job(client, nxt["job"]["id"])["instance"] == "b"


def test_allowed_kinds_reroute_and_park(client, fake_execute):
    a = _asset(client)
    client.post("/api/queue/a/pause", params={"paused": True})
    client.post("/api/queue/b/pause", params={"paused": True})
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    jid = out["job"]["id"]
    assert out["job"]["instance"] == "b"
    # b may no longer make pictures: the queued picture moves to a.
    rows = client.put("/api/gpus/b", json={"allowed_kinds": ["model", "rigging", "review"]}).json()
    b = _gpu(rows, "b")
    assert b["allowed_kinds"] == ["model", "rigging", "review"] and b["effective_kinds"] == ["model", "rigging", "review"]
    assert "concept" not in b["can_run"] and "rig" in b["can_run"]
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["instance"] == "a" and j["status"] == "queued"
    # Nor a: nothing can take it, so it waits where it is and the worker passes over it.
    client.put("/api/gpus/a", json={"allowed_kinds": ["model", "review"]})
    client.post("/api/queue/a/pause", params={"paused": False})
    time.sleep(1.0)
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "queued" and j["instance"] == "a"
    assert j["message"] == "Waiting for a graphics card that is allowed to do pictures"
    # A new picture is created parked too (on its preferred card), and the version stays queued.
    parked = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    assert parked["job"]["message"] == "Waiting for a graphics card that is allowed to do pictures"
    assert parked["version"]["status"] == "queued"
    # a may make pictures again: both run there (the parked one on b stays on b, since b still may not).
    client.put("/api/gpus/a", json={"allowed_kinds": ["pictures", "model", "review"]})
    assert wait_job(client, jid)["status"] == "done"
    pj = wait_job(client, parked["job"]["id"])
    assert pj["status"] == "done" and pj["instance"] == "a"
    # a cannot rig whatever is ticked: rigging stays out of its effective kinds, with the note.
    rows = client.put("/api/gpus/a", json={"allowed_kinds": ["pictures", "model", "rigging", "review"]}).json()
    ga = _gpu(rows, "a")
    assert ga["effective_kinds"] == ["pictures", "model", "review"] and ga["possible_kinds"] == ["pictures", "model", "review"]
    assert ga["kind_notes"] == {"rigging": "Rigging only runs on the test's instance."}
    assert client.put("/api/gpus/a", json={"allowed_kinds": ["nope"]}).status_code == 400
    assert client.put("/api/gpus/z", json={"enabled": False}).status_code == 404


def test_free_after_job(client, fake_execute, monkeypatch: pytest.MonkeyPatch):
    freed: list[str] = []

    async def free(self):
        freed.append(self.name)
        return True

    monkeypatch.setattr(comfy.ComfyClient, "free", free)
    a = _asset(client)
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()["job"]["id"])
    time.sleep(0.2)
    assert freed == []
    rows = client.put("/api/gpus/b", json={"free_after_job": True}).json()
    assert _gpu(rows, "b")["free_after_job"] is True and _gpu(rows, "a")["free_after_job"] is False
    wait_job(client, client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()["job"]["id"])
    t0 = time.time()
    while "b" not in freed and time.time() - t0 < 5:
        time.sleep(0.05)
    assert freed == ["b"]


# --- persistence and restart ----------------------------------------------------------------------------------------


def test_put_persists_and_get_reflects(client, settings):
    rows = client.get("/api/gpus").json()
    assert [r["instance"] for r in rows] == ["a", "b"] and all(r["enabled"] for r in rows)
    assert set(rows[0]) >= {"instance", "gpu", "url", "enabled", "reachable", "current_job_id", "queued", "can_run", "note",
                            "allowed_kinds", "effective_kinds", "free_after_job"}
    assert rows[0]["reachable"] is False and "vram_total_mb" not in rows[0]
    client.put("/api/gpus/b", json={"enabled": False})
    client.put("/api/gpus/a", json={"allowed_kinds": ["model"], "free_after_job": True})
    stored = json.loads((settings.library_path / prefs.PREFS_FILE).read_text(encoding="utf-8"))
    assert stored["disabled_instances"] == ["b"]
    assert stored["allowed_kinds"]["a"] == ["model"]
    assert stored["allowed_kinds"]["b"] == ["pictures", "model", "rigging", "review"]
    assert stored["free_after_job"] == {"a": True, "b": False}
    rows = client.get("/api/gpus").json()
    assert _gpu(rows, "b")["enabled"] is False and _gpu(rows, "a")["allowed_kinds"] == ["model"]
    h = client.get("/api/health").json()
    assert h["instances"]["b"]["enabled"] is False and h["instances"]["a"]["enabled"] is True
    assert h["queue"]["b"]["enabled"] is False
    client.put("/api/gpus/b", json={"enabled": True})
    assert json.loads((settings.library_path / prefs.PREFS_FILE).read_text(encoding="utf-8"))["disabled_instances"] == []


def test_gpus_event_on_change(client):
    with client.websocket_connect("/api/ws/events") as ws:
        assert ws.receive_json()["type"] == "hello"
        client.put("/api/gpus/b", json={"enabled": False})
        for _ in range(20):
            ev = ws.receive_json()
            if ev["type"] == "gpus":
                assert _gpu(ev["gpus"], "b")["enabled"] is False
                break
        else:
            raise AssertionError("no gpus event")


def test_restart_keeps_card_off_and_reroutes_parked(settings, fake_execute):
    from vgac.main import app

    with TestClient(app) as c:
        a = _asset(c)
        c.post("/api/queue/b/pause", params={"paused": True})
        out = c.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
        assert out["job"]["instance"] == "b"
    # While the server is down, the card is turned off (prefs.json written as the app writes it).
    prefs.set_disabled_instances(["b"], settings)
    with TestClient(app) as c:
        rows = c.get("/api/gpus").json()
        assert _gpu(rows, "b")["enabled"] is False
        j = wait_job(c, out["job"]["id"])
        assert j["status"] == "done" and j["instance"] == "a"
    assert [x["instance"] for x in fake_execute] == ["a"]


def test_prefs_file_tolerates_garbage(settings):
    (settings.library_path / prefs.PREFS_FILE).write_text("{not json", encoding="utf-8")
    assert prefs.load(settings) == prefs.defaults()
    prefs.update(settings, allowed_kinds={"a": ["model"]})
    assert prefs.load(settings)["allowed_kinds"] == {"a": ["model"]} and prefs.load(settings)["disabled_instances"] == []


# --- default card and sharing work when a card is busy -----------------------------------------------------------


def test_route_tries_the_default_card_first(settings):
    assert jobs.route("concept", "humanoid", settings) == "b"
    assert jobs.route("concept", "humanoid", settings, default="a") == "a"
    assert jobs.route("mesh", "humanoid", settings, default="b") == "b"
    assert jobs.route("rig", "humanoid", settings, default="a") == "b"  # only b can rig
    assert jobs.route("prep", "humanoid", settings, default="a") == jobs.HOST
    assert jobs.route("concept", "humanoid", settings, {"a"}, default="a") == "b"  # the default is off
    assert jobs.route("concept", "humanoid", settings, (), {"a": ["model"]}, default="a") == "b"  # not allowed pictures
    assert jobs.route("concept", "humanoid", settings, default="zz") == "b"


def test_default_card_takes_new_and_queued_work_and_persists(client, settings):
    a = _asset(client)
    client.post("/api/queue/b/pause", params={"paused": True})
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"count": 2}).json()
    assert [o["job"]["instance"] for o in out] == ["b", "b"]
    rows = client.put("/api/gpus", json={"default_instance": "a"}).json()
    assert _gpu(rows, "a")["is_default"] is True and _gpu(rows, "b")["is_default"] is False
    for o in out:
        j = wait_job(client, o["job"]["id"])
        assert j["status"] == "done" and j["instance"] == "a"
    nxt = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    assert nxt["job"]["instance"] == "a"
    wait_job(client, nxt["job"]["id"])
    assert prefs.load(settings)["default_instance"] == "a"
    # Back to each step's usual card.
    client.post("/api/queue/b/pause", params={"paused": False})
    rows = client.put("/api/gpus", json={"default_instance": None}).json()
    assert not any(r["is_default"] for r in rows)
    again = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    assert again["job"]["instance"] == "b"
    wait_job(client, again["job"]["id"])
    assert prefs.load(settings)["default_instance"] is None
    assert client.put("/api/gpus", json={"default_instance": "zz"}).status_code == 404


def test_idle_card_takes_work_from_a_busy_one_only_when_sharing_is_on(client, fake_execute, settings):
    a = _asset(client)
    # Off (the default): both pictures wait for b in turn.
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"count": 2, "params": {"slow": True}}).json()
    done = [wait_job(client, o["job"]["id"], timeout=30) for o in out]
    assert [j["instance"] for j in done] == ["b", "b"]
    # On: while b runs the first, a takes the second.
    rows = client.put("/api/gpus", json={"share_when_busy": True}).json()
    assert all(r["share_when_busy"] for r in rows)
    fake_execute.clear()
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"count": 2, "params": {"slow": True}}).json()
    done = [wait_job(client, o["job"]["id"], timeout=30) for o in out]
    assert sorted(j["instance"] for j in done) == ["a", "b"]
    moved = next(j for j in done if j["instance"] == "a")
    assert all(j["status"] == "done" for j in done)
    assert prefs.load(settings)["share_when_busy"] is True
    # A rig job is never taken by a: only b can rig.
    q = jobs.active_queue()
    assert q._can_take("a", "rig.run") is False and q._can_take("a", "concept.run") is True
    assert moved["id"] in {c["job_id"] for c in fake_execute if c["instance"] == "a"}
