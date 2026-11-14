"""A job whose graphics engine dies mid-step goes back to the queue once (jobs.is_engine_lost, ENGINE_RETRY_KEY); the
start-on-demand path then brings the engine back. A second loss, a workflow error ComfyUI reported, or a cancel do
not retry. stages.execute is a fake and every process call a stub."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from vgac import comfy, instances, jobs, stages
from vgac.comfy import ComfyError
from vgac.db import Job, session_factory


def wait_for(fn, timeout: float = 15.0, what: str = "condition"):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = fn()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}; last {last!r}")


def _asset(client: TestClient) -> dict[str, Any]:
    p = client.post("/api/projects", json={"name": "P"}).json()
    return client.post(f"/api/projects/{p['id']}/assets", json={"name": "Knight", "category": "humanoid",
                                                                "style_preset": "painted", "prompt": "a knight"}).json()


def _finished(client: TestClient, jid: str):
    j = client.get(f"/api/jobs/{jid}").json()
    return j if j["status"] in ("done", "error", "cancelled") else None


@pytest.fixture
def flaky(monkeypatch: pytest.MonkeyPatch):
    """stages.execute raising whatever `plan` says, one entry per call (None: succeed)."""
    state: dict[str, Any] = {"plan": [], "calls": [], "payloads": []}

    async def execute(ctx, kind, payload):
        state["calls"].append(ctx.instance)
        state["payloads"].append(dict(payload))
        step = state["plan"].pop(0) if state["plan"] else None
        if callable(step):
            step = step()
        if step is not None:
            raise step

    monkeypatch.setattr(stages, "execute", execute)
    return state


@pytest.fixture
def client(settings, flaky):
    from vgac.main import app

    with TestClient(app) as c:
        yield c


def test_is_engine_lost_classifies_errors():
    req = httpx.Request("GET", "http://127.0.0.1:8189/history/x")
    assert jobs.is_engine_lost(httpx.ConnectError("refused", request=req))
    assert jobs.is_engine_lost(httpx.ReadError("reset", request=req))
    assert jobs.is_engine_lost(httpx.RemoteProtocolError("closed", request=req))
    assert jobs.is_engine_lost(httpx.ReadTimeout("no answer", request=req))
    assert jobs.is_engine_lost(ConnectionResetError(10054, "reset by peer"))
    # Wrapped by a stage: the cause counts.
    try:
        try:
            raise httpx.ConnectError("refused", request=req)
        except httpx.ConnectError as e:
            raise RuntimeError("download failed") from e
    except RuntimeError as wrapped:
        assert jobs.is_engine_lost(wrapped)
    # Workflow errors ComfyUI reported, its own time limit, and ordinary bugs are not.
    assert not jobs.is_engine_lost(ComfyError("prompt x failed", status={"status_str": "error"}))
    assert not jobs.is_engine_lost(ComfyError("prompt x timed out after 60s"))
    assert not jobs.is_engine_lost(ValueError("bad"))
    assert not jobs.is_engine_lost(FileNotFoundError("no cutout"))


def test_connection_error_requeues_once_then_succeeds(client, flaky):
    req = httpx.Request("GET", "http://127.0.0.1:10/history/x")
    flaky["plan"] = [httpx.ConnectError("[WinError 10061] refused", request=req), None]
    a = _asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    jid = out["job"]["id"]
    j = wait_for(lambda: _finished(client, jid), what="the job to finish")
    assert j["status"] == "done" and j["error"] is None
    assert flaky["calls"] == ["b", "b"]
    assert flaky["payloads"][0].get(jobs.ENGINE_RETRY_KEY) is None
    assert flaky["payloads"][1][jobs.ENGINE_RETRY_KEY] == 1
    assert client.get(f"/api/versions/{out['version']['id']}").json()["status"] == "done"


def test_second_connection_error_fails_with_a_plain_message(client, flaky):
    req = httpx.Request("GET", "http://127.0.0.1:10/history/x")
    flaky["plan"] = [httpx.ReadError("connection reset", request=req), httpx.ConnectError("refused", request=req), None]
    a = _asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    jid = out["job"]["id"]
    j = wait_for(lambda: _finished(client, jid), what="the job to fail")
    assert j["status"] == "error" and flaky["calls"] == ["b", "b"]
    # This engine was started by someone else (the app has no way to start it): the message says so.
    assert j["message"].startswith("The graphics engine on test stopped answering, and the app does not know where ComfyUI is")
    assert "ConnectError: refused" in j["error"]
    v = client.get(f"/api/versions/{out['version']['id']}").json()
    assert v["status"] == "error"


def test_workflow_error_is_not_retried(client, flaky):
    flaky["plan"] = [ComfyError("prompt x failed: node 7", status={"status_str": "error"})]
    a = _asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    j = wait_for(lambda: _finished(client, out["job"]["id"]), what="the job to fail")
    assert j["status"] == "error" and j["error"].startswith("ComfyError") and flaky["calls"] == ["b"]


def test_retry_message_while_queued(client, flaky):
    """Between the loss and the next try the job is queued with the plain message (the worker is held paused here)."""
    req = httpx.Request("GET", "http://127.0.0.1:10/history/x")
    q = jobs.active_queue()

    def lose_and_pause():
        q.set_paused("b", True)
        return httpx.ConnectError("refused", request=req)

    flaky["plan"] = [lose_and_pause, None]
    a = _asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    jid = out["job"]["id"]
    j = wait_for(lambda: (lambda j: j if j["status"] == "queued" and j["message"] == jobs.ENGINE_RETRY_MESSAGE else None)(
        client.get(f"/api/jobs/{jid}").json()), what="the retry message")
    assert j["error"] is None
    with session_factory()() as db:
        assert db.get(Job, jid).payload[jobs.ENGINE_RETRY_KEY] == 1
    assert client.get(f"/api/versions/{out['version']['id']}").json()["status"] == "queued"
    q.set_paused("b", False)
    assert wait_for(lambda: _finished(client, jid), what="the retry")["status"] == "done"


# --- with on-demand start: the engine is brought back before the retry ---------------------------------------------


class StubOps:
    def __init__(self) -> None:
        self.spawned: list[int] = []
        self.alive_pids: set[int] = set()
        self.on_spawn = None

    def spawn(self, script: Path, cwd: Path, out_log: Path, err_log: Path) -> int:
        pid = 7000 + len(self.spawned)
        self.spawned.append(pid)
        self.alive_pids.add(pid)
        if self.on_spawn is not None:
            self.on_spawn()
        return pid

    def alive(self, pid: int) -> bool:
        return pid in self.alive_pids

    def create_time(self, pid: int):
        return 100.0 if pid in self.alive_pids else None

    def kill_tree(self, pid: int) -> None:
        self.alive_pids.discard(pid)

    def find(self, match: str) -> list[int]:
        return []

    def port_open(self, url: str) -> bool:
        return False


def test_crashed_engine_is_started_again_for_the_retry(settings, flaky, monkeypatch, tmp_path):
    script = tmp_path / "start-comfy-x.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    for n, inst in settings.instances.items():
        inst.start_script = script
        inst.start_timeout_seconds = 10
        inst.process_match = f"--port-of-{n}"
    ops = StubOps()
    up = {"a": False, "b": True}  # b runs (started elsewhere) until it crashes

    async def vram_summary(self):
        return {"reachable": bool(up.get(self.name)), "devices": []}

    monkeypatch.setattr(instances, "ProcessOps", lambda: ops)
    monkeypatch.setattr(instances, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(jobs, "IDLE_CHECK_SECONDS", 3600.0)
    monkeypatch.setattr(comfy.ComfyClient, "vram_summary", vram_summary)
    req = httpx.Request("GET", "http://127.0.0.1:10/history/x")

    def crash():
        up["b"] = False  # the engine died mid-step
        return httpx.ConnectError("refused", request=req)

    flaky["plan"] = [crash, None]
    ops.on_spawn = lambda: up.__setitem__("b", True)  # the start script brings it back
    from vgac.main import app

    with TestClient(app) as c:
        a = _asset(c)
        out = c.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
        j = wait_for(lambda: _finished(c, out["job"]["id"]), what="the retried job")
        assert j["status"] == "done" and flaky["calls"] == ["b", "b"]
        assert len(ops.spawned) == 1  # started again once, by the normal start-on-demand path
