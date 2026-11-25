"""The restart request: what the call checks and the file it writes for the launcher (vgac/restart.py)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from vgac import restart


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(restart, "PROJECT_DIR", tmp_path)
    monkeypatch.setenv("VGAC_LAUNCHER_RESTART", "1")
    return tmp_path


@pytest.fixture
def client(settings, root):
    from vgac.main import app

    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


def _request_file(root):
    return json.loads((root / "local" / "restart-request.json").read_text(encoding="utf-8"))


def test_writes_one_request_and_answers_with_its_id(client, root):
    r = client.post("/api/restart", json={})
    assert r.status_code == 200
    body = r.json()
    on_disk = _request_file(root)
    assert body["id"] == on_disk["id"] and len(body["id"]) == 16
    assert on_disk["engines"] == "keep" and abs(on_disk["time"] - time.time()) < 30
    # a second call while the first is waiting is refused: no stacked requests
    assert client.post("/api/restart", json={}).status_code == 409


def test_refused_while_work_is_running_unless_forced(client, root, monkeypatch):
    monkeypatch.setattr(restart, "work_in_flight", lambda status: 2)
    r = client.post("/api/restart", json={})
    assert r.status_code == 409 and "2 steps" in r.json()["detail"]
    assert not (root / "local" / "restart-request.json").exists()
    assert client.post("/api/restart", json={"force": True}).status_code == 200


def test_work_in_flight_counts_running_once_and_waiting_by_number():
    status = {"a": {"current_job_id": "j1", "lent_to": None, "queued": 1},
              "b": {"current_job_id": None, "lent_to": "j1", "queued": 2},
              "host": {"current_job_id": None, "queued": 0, "quick_job_id": "j9", "helper_job_ids": ["j8"]}}
    assert restart.work_in_flight(status) == 3 + 3
    assert restart.work_in_flight({"a": {"current_job_id": None, "queued": 0}}) == 0


def test_refused_when_no_launcher_can_act_on_it(client, root, monkeypatch):
    monkeypatch.delenv("VGAC_LAUNCHER_RESTART")
    r = client.post("/api/restart", json={})
    assert r.status_code == 409 and "VGAC.exe" in r.json()["detail"]
    assert not (root / "local" / "restart-request.json").exists()


def test_quit_writes_a_quit_request_with_the_same_guards(client, root, monkeypatch):
    r = client.post("/api/quit", json={})
    assert r.status_code == 200
    on_disk = _request_file(root)
    assert on_disk["kind"] == "quit" and on_disk["id"] == r.json()["id"]
    assert client.post("/api/quit", json={}).status_code == 409  # one at a time
    (root / "local" / "restart-request.json").unlink()
    monkeypatch.setattr(restart, "work_in_flight", lambda status: 1)
    r = client.post("/api/quit", json={})
    assert r.status_code == 409 and "working on 1 step" in r.json()["detail"] and "Closing" in r.json()["detail"]
    assert client.post("/api/quit", json={"force": True}).status_code == 200
    assert client.post("/api/quit", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    monkeypatch.delenv("VGAC_LAUNCHER_RESTART")
    (root / "local" / "restart-request.json").unlink()
    assert client.post("/api/quit", json={}).status_code == 409


def test_a_restart_request_says_it_is_one(client, root):
    client.post("/api/restart", json={})
    assert _request_file(root)["kind"] == "restart"


def test_unknown_engines_choice_is_refused(client):
    assert client.post("/api/restart", json={"engines": "maybe"}).status_code == 422


def test_engines_restart_lists_the_running_ones(root):
    out = restart.write_request("restart", False, 0, {"b": 123, "a": 99})
    assert out["restart_engines"] == ["a", "b"]
    assert _request_file(root)["keep_pids"] == []
    (root / "local" / "restart-request.json").unlink()
    restart.write_request("keep", False, 0, {"b": 123})
    on_disk = _request_file(root)
    assert on_disk["keep_pids"] == [123] and on_disk["restart_engines"] == []


def test_a_stale_request_does_not_block_a_new_one(root):
    restart.write_request("keep", False, 0, {}, now=time.time() - 600)
    assert restart.write_request("keep", False, 0, {})["id"]


def test_pages_of_other_sites_and_other_machines_are_refused(client, settings, root):
    r = client.post("/api/restart", json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    client._transport.client = ("192.168.1.9", 5)  # the same running app, called from another address
    assert client.post("/api/restart", json={}).status_code == 403
    assert not (root / "local" / "restart-request.json").exists()
    client._transport.client = ("127.0.0.1", 5)
    assert client.post("/api/restart", json={}, headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200


def test_health_names_the_backend_and_the_request_that_started_it(client, monkeypatch):
    monkeypatch.setenv("VGAC_RESTART_ID", "abc123")
    b = client.get("/api/health").json()["backend"]
    assert b["restart_id"] == "abc123" and b["started_at"] and b["pid"]
