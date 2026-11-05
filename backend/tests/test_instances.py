"""On-demand start and idle stop of the ComfyUI instances (instances.py and its use in jobs.py). Every process call is
a stub (StubOps) and every ComfyUI answer is a fake, so nothing real is started, stopped or reached."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from vgac import comfy, instances, jobs, prefs, stages
from vgac.config import InstanceConfig
from vgac.instances import NotOwned, StartFailed, Supervisor

GPU = "test"
STARTING = f"Starting the graphics engine on {GPU} (about a minute)"


class StubOps:
    """Stands in for instances.ProcessOps: records spawns and kills, never touches a real process."""

    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.killed: list[int] = []
        self.alive_pids: set[int] = set()
        self.matches: dict[str, list[int]] = {}
        self.open_ports: set[str] = set()
        self.next_pid = 5000
        self.on_spawn = None

    def spawn(self, script: Path, cwd: Path, out_log: Path, err_log: Path) -> int:
        self.next_pid += 1
        pid = self.next_pid
        self.alive_pids.add(pid)
        self.spawned.append({"pid": pid, "script": Path(script), "out": Path(out_log), "err": Path(err_log)})
        if self.on_spawn is not None:
            self.on_spawn(pid, Path(script))
        return pid

    def alive(self, pid: int) -> bool:
        return pid in self.alive_pids

    def create_time(self, pid: int) -> float | None:
        return 100.0 if pid in self.alive_pids else None

    def kill_tree(self, pid: int) -> None:
        self.killed.append(pid)
        self.alive_pids.discard(pid)

    def find(self, match: str) -> list[int]:
        return list(self.matches.get(match, []))

    def port_open(self, url: str) -> bool:
        return url in self.open_ports


class FakeClient:
    def __init__(self, up_after: int | None = None) -> None:
        self.up = False
        self.calls = 0
        self.up_after = up_after  # comes up on this many-th /system_stats

    async def cached_vram_summary(self, max_age: float = 5.0) -> dict[str, Any]:
        self.calls += 1
        if self.up_after is not None and self.calls >= self.up_after:
            self.up = True
        return {"reachable": self.up, "devices": []}

    def forget_cache(self) -> None:
        pass


def _scripted(settings, tmp_path: Path, timeout: float = 5.0):
    script = tmp_path / "start-comfy-x.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    inst = {n: i.model_copy(update={"start_script": script, "start_timeout_seconds": timeout, "process_match": f"--port-of-{n}"})
            for n, i in settings.instances.items()}
    return settings.model_copy(update={"instances": inst})


def _unscripted(settings):
    return settings.model_copy(update={"instances": {n: i.model_copy(update={"start_script": None})
                                                     for n, i in settings.instances.items()}})


# --- the supervisor on its own ---------------------------------------------------------------------------------------


def test_ensure_running_starts_stub_and_waits(settings, tmp_path):
    s = _scripted(settings, tmp_path)
    ops = StubOps()
    client = FakeClient(up_after=4)
    changes: list[str] = []
    sup = Supervisor(s, {"a": client, "b": FakeClient()}, ops=ops, on_change=changes.append, poll_seconds=0.01,
                     project_dir=tmp_path)
    assert sup.can_start("a") and sup.state("a") == "stopped" and not sup.owned("a")
    asyncio.run(sup.ensure_running("a"))
    assert len(ops.spawned) == 1
    sp = ops.spawned[0]
    assert sp["script"].name == "start-comfy-x.ps1"
    assert sp["out"] == tmp_path / "runtime" / "logs" / "comfy-a.out.log"
    assert sp["err"] == tmp_path / "runtime" / "logs" / "comfy-a.err.log"
    assert sup.state("a") == "running" and sup.owned("a") and client.up
    assert changes == ["a", "a"]  # starting, then running
    # Ownership survives a backend restart (engines.json), and a pid that is no longer ours is not adopted.
    stored = json.loads((s.library_path / instances.OWNED_FILE).read_text(encoding="utf-8"))
    assert stored["a"]["pid"] == sp["pid"]
    again = Supervisor(s, {"a": client, "b": FakeClient()}, ops=ops, project_dir=tmp_path)
    assert again.owned("a") and not again.owned("b")
    ops.alive_pids.clear()
    assert not Supervisor(s, {"a": client, "b": FakeClient()}, ops=ops, project_dir=tmp_path).owned("a")
    # Already answering: nothing is started.
    ops2 = StubOps()
    up = FakeClient()
    up.up = True
    sup2 = Supervisor(s, {"a": up, "b": FakeClient()}, ops=ops2, poll_seconds=0.01, project_dir=tmp_path)
    asyncio.run(sup2.ensure_running("a"))
    assert ops2.spawned == [] and sup2.state("a") == "running" and not sup2.owned("a")


def test_no_start_when_a_matching_process_or_open_port_exists(settings, tmp_path):
    s = _scripted(settings, tmp_path)
    ops = StubOps()
    ops.matches["--port-of-b"] = [4242]  # someone else (the launcher) is starting it
    client = FakeClient(up_after=3)
    sup = Supervisor(s, {"a": FakeClient(), "b": client}, ops=ops, poll_seconds=0.01, project_dir=tmp_path)
    asyncio.run(sup.ensure_running("b"))
    assert ops.spawned == [] and sup.state("b") == "running" and not sup.owned("b")
    # The port listening counts the same.
    ops2 = StubOps()
    ops2.open_ports.add(s.instances["a"].url)
    sup2 = Supervisor(s, {"a": FakeClient(up_after=2), "b": FakeClient()}, ops=ops2, poll_seconds=0.01, project_dir=tmp_path)
    asyncio.run(sup2.ensure_running("a"))
    assert ops2.spawned == []


def test_failed_starts_give_plain_reasons(settings, tmp_path):
    s = _scripted(settings, tmp_path, timeout=0.2)
    ops = StubOps()
    sup = Supervisor(s, {"a": FakeClient(), "b": FakeClient()}, ops=ops, poll_seconds=0.01, project_dir=tmp_path)
    with pytest.raises(StartFailed, match="did not answer within 0.2 seconds"):
        asyncio.run(sup.ensure_running("a"))
    assert sup.state("a") == "failed" and "comfy-a.err.log" in sup.last_error("a")
    assert sup.failed() == {"a"}
    # Timed out but still loading: it stays ours, and the next try waits for it instead of starting a second copy.
    assert sup.owned("a")
    sup.clear_failed("a")
    assert sup.state("a") == "stopped" and sup.last_error("a") == ""
    with pytest.raises(StartFailed):
        asyncio.run(sup.ensure_running("a"))
    assert len(ops.spawned) == 1
    # The process closes while starting.
    ops.on_spawn = lambda pid, script: ops.alive_pids.discard(pid)
    with pytest.raises(StartFailed, match="closed while starting"):
        asyncio.run(sup.ensure_running("b"))
    # No script on disk.
    missing = s.model_copy(update={"instances": {**s.instances, "a": s.instances["a"].model_copy(
        update={"start_script": tmp_path / "nope.ps1"})}})
    sup3 = Supervisor(missing, {"a": FakeClient(), "b": FakeClient()}, ops=StubOps(), poll_seconds=0.01, project_dir=tmp_path)
    with pytest.raises(StartFailed, match="was not found"):
        asyncio.run(sup3.ensure_running("a"))
    # No script configured at all: nothing can be started.
    bare = Supervisor(_unscripted(settings), {"a": FakeClient(), "b": FakeClient()}, ops=StubOps(), project_dir=tmp_path)
    assert not bare.can_start("a")


def test_stop_refuses_a_process_it_does_not_own(settings, tmp_path):
    s = _scripted(settings, tmp_path)
    ops = StubOps()
    ops.matches["--port-of-a"] = [777]
    ops.alive_pids.add(777)
    sup = Supervisor(s, {"a": FakeClient(up_after=1), "b": FakeClient(up_after=3)}, ops=ops, poll_seconds=0.01,
                     project_dir=tmp_path)
    asyncio.run(sup.ensure_running("a"))  # running, started by someone else
    with pytest.raises(NotOwned):
        sup.stop("a")
    assert not sup.stop_if_owned("a")
    assert ops.killed == []
    # One it started is stopped, whole tree.
    asyncio.run(sup.ensure_running("b"))
    pid = ops.spawned[0]["pid"]
    sup.stop("b")
    assert ops.killed == [pid] and sup.state("b") == "stopped" and not sup.owned("b")


# --- through the job queue and the API --------------------------------------------------------------------------


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    script = tmp_path / "start-comfy-x.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    cfg = {
        "instances": {
            "a": {"url": "http://127.0.0.1:9", "gpu": GPU, "role": "3d", "start_script": str(script),
                  "start_timeout_seconds": 30, "process_match": "--port-of-a", "external_owner": "ComfyUI Desktop"},
            "b": {"url": "http://127.0.0.1:10", "gpu": GPU, "role": "images", "output_dir": str(tmp_path / "comfy_out"),
                  "start_script": str(script), "start_timeout_seconds": 30, "process_match": "--port-of-b"},
        },
        "library_path": str(tmp_path / "library"),
        "blender_exe": None,
        "unity_exe": None,
        "comfy_input_dir": str(tmp_path / "comfy_in"),
        "min_free_ram_gb": 0,
        "job_timeout_seconds": 60,
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch):
    """Stub processes, fake ComfyUI answers (`up` by instance), fake job execution."""
    ops = StubOps()
    up = {"a": False, "b": False}
    ran: list[dict[str, Any]] = []
    freed: list[str] = []

    async def vram_summary(self):
        return {"reachable": bool(up.get(self.name)), "devices": [{"vram_total": 8 * 1024**3, "vram_free": 6 * 1024**3}]}

    async def free(self):
        freed.append(self.name)
        return True

    async def execute(ctx, kind, payload):
        ran.append({"kind": kind, "instance": ctx.instance, "job_id": ctx.job_id})

    monkeypatch.setattr(instances, "ProcessOps", lambda: ops)
    monkeypatch.setattr(instances, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(jobs, "IDLE_CHECK_SECONDS", 3600.0)
    monkeypatch.setattr(comfy.ComfyClient, "vram_summary", vram_summary)
    monkeypatch.setattr(comfy.ComfyClient, "free", free)
    monkeypatch.setattr(stages, "execute", execute)
    return {"ops": ops, "up": up, "ran": ran, "freed": freed}


@pytest.fixture
def client(settings, world):
    from vgac.main import app

    with TestClient(app) as c:
        yield c


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
    return client.post(f"/api/projects/{p['id']}/assets", json={"name": "Knight", "category": "humanoid", "style_preset": "painted", "prompt": "a knight"}).json()


def _gpu(client: TestClient, name: str) -> dict[str, Any]:
    return next(r for r in client.get("/api/gpus").json() if r["instance"] == name)


def _job(client: TestClient, job_id: str) -> dict[str, Any]:
    return client.get(f"/api/jobs/{job_id}").json()


def test_job_waits_while_engine_starts_then_runs(client, world):
    ops, up = world["ops"], world["up"]
    assert _gpu(client, "b")["state"] == "stopped" and _gpu(client, "b")["can_start"] is True
    a = _asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    jid = out["job"]["id"]
    assert out["job"]["instance"] == "b"
    j = wait_for(lambda: (lambda j: j if j["message"] == STARTING else None)(_job(client, jid)), what="the starting message")
    assert j["status"] == "queued"
    assert client.get(f"/api/versions/{out['version']['id']}").json()["status"] == "queued"
    g = _gpu(client, "b")
    assert g["state"] == "starting" and g["owned"] is True
    assert [s["pid"] for s in ops.spawned] == [ops.next_pid] and world["ran"] == []
    up["b"] = True
    wait_for(lambda: _job(client, jid)["status"] == "done", what="the job to finish")
    assert [r["instance"] for r in world["ran"]] == ["b"]
    g = _gpu(client, "b")
    assert g["state"] == "running" and g["owned"] is True and g["last_error"] == ""
    # The next job finds it running and starts nothing more.
    nxt = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    wait_for(lambda: _job(client, nxt["job"]["id"])["status"] == "done", what="the second job")
    assert len(ops.spawned) == 1


def test_failed_start_reroutes_or_parks_with_reason(client, world):
    ops, up = world["ops"], world["up"]
    q = jobs.active_queue()
    q.settings.instances["b"].start_timeout_seconds = 0.3
    up["a"] = True  # a is running (ComfyUI Desktop); b never comes up
    a = _asset(client)
    out = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    jid = out["job"]["id"]
    assert out["job"]["instance"] == "b"
    j = wait_for(lambda: (lambda j: j if j["status"] == "done" else None)(_job(client, jid)), what="the job to finish on a")
    assert j["instance"] == "a" and j["status"] == "done"
    assert [r["instance"] for r in world["ran"]] == ["a"]
    g = _gpu(client, "b")
    assert g["state"] == "failed" and "did not answer within 0.3 seconds" in g["last_error"]
    # Rigging only runs on b: a new rig job waits there with the reason; nothing fails.
    target, message = q.place("rig", "humanoid")
    assert target == "b"
    assert message.startswith(f"Could not start the graphics engine on {GPU}: it did not answer within")
    assert message.endswith("It will try again when you press Try again or turn the card off and on.")
    rig = q.submit("b", "rig.run", a["id"], None, {"version_id": None}, message=message)
    time.sleep(0.5)
    assert _job(client, rig.id)["status"] == "queued" and len(ops.spawned) == 1
    # a stays running and is not ours: its note says so.
    ga = _gpu(client, "a")
    assert ga["state"] == "running" and ga["owned"] is False
    assert ga["note"].endswith("Started by ComfyUI Desktop; the app leaves it running.")
    # Try again: b starts (its first copy is still ours and loading, so no second copy), comes up, the rig runs.
    q.settings.instances["b"].start_timeout_seconds = 30
    rows = client.post("/api/gpus/b/start").json()
    assert next(r for r in rows if r["instance"] == "b")["state"] in ("starting", "stopped")
    up["b"] = True
    wait_for(lambda: _job(client, rig.id)["status"] == "done", what="the rig job")
    assert len(ops.spawned) == 1 and _gpu(client, "b")["state"] == "running"


def test_card_that_is_off_is_never_started(client, world):
    ops = world["ops"]
    client.put("/api/gpus/b", json={"enabled": False})
    assert _gpu(client, "b")["state"] == "off"
    q = jobs.active_queue()
    a = _asset(client)
    rig = q.submit("b", "rig.run", a["id"], None, {"version_id": None}, message=jobs.waiting_message("b", q.settings))
    r = client.post("/api/gpus/b/start")
    assert r.status_code == 409 and "turned off" in r.json()["detail"]
    time.sleep(0.6)
    assert ops.spawned == [] and _job(client, rig.id)["status"] == "queued"
    # A host job never starts anything either.
    q.submit("host", "prep.run", a["id"], None, {"version_id": None})
    wait_for(lambda: any(x["instance"] == "host" for x in world["ran"]), what="the host job")
    assert ops.spawned == []


def test_idle_stop_only_for_owned_and_after_the_delay(client, world, settings):
    ops, up = world["ops"], world["up"]
    q = jobs.active_queue()
    sup = q.supervisor
    now = [1000.0]
    sup.clock = lambda: now[0]
    # a: started by the app. b: already running, started by someone else.
    up["b"] = True
    client.post("/api/gpus/a/start")
    wait_for(lambda: sup.state("a") == "starting", what="a starting")
    up["a"] = True
    wait_for(lambda: _gpu(client, "a")["state"] == "running", what="a running")
    assert _gpu(client, "a")["owned"] is True and _gpu(client, "b")["owned"] is False
    for name in ("a", "b"):
        rows = client.put(f"/api/gpus/{name}", json={"stop_when_idle_minutes": 5}).json()
    assert all(r["stop_when_idle_minutes"] == 5 for r in rows)
    assert json.loads((settings.library_path / prefs.PREFS_FILE).read_text(encoding="utf-8"))["stop_when_idle_minutes"] == {"a": 5, "b": 5}
    assert client.put("/api/gpus/a", json={"stop_when_idle_minutes": -1}).status_code == 422
    now[0] += 4 * 60
    assert q.idle_check() == [] and ops.killed == []
    now[0] += 2 * 60
    assert q.idle_check() == ["a"]
    assert ops.killed == [ops.spawned[0]["pid"]]
    up["a"] = False
    assert _gpu(client, "a")["state"] == "stopped" and _gpu(client, "a")["owned"] is False
    # b is never stopped: not by the idle check, not by the Stop button.
    now[0] += 60 * 60
    assert q.idle_check() == []
    r = client.post("/api/gpus/b/stop")
    assert r.status_code == 409 and "not started by the app" in r.json()["detail"]
    assert ops.killed == [ops.spawned[0]["pid"]]
    # 0 = never.
    client.put("/api/gpus/a", json={"stop_when_idle_minutes": 0})
    client.post("/api/gpus/a/start")
    wait_for(lambda: len(ops.spawned) == 2, what="a second start")
    up["a"] = True
    wait_for(lambda: _gpu(client, "a")["state"] == "running", what="a running again")
    now[0] += 24 * 60 * 60
    assert q.idle_check() == []


def test_turning_a_card_off_stops_an_owned_engine_after_freeing(client, world):
    ops, up = world["ops"], world["up"]
    client.post("/api/gpus/a/start")
    wait_for(lambda: len(ops.spawned) == 1, what="the start")
    up["a"] = True
    wait_for(lambda: _gpu(client, "a")["state"] == "running", what="a running")
    client.put("/api/gpus/a", json={"enabled": False})
    wait_for(lambda: ops.killed == [ops.spawned[0]["pid"]], what="the stop")
    assert "a" in world["freed"]
    assert _gpu(client, "a")["state"] == "off"
    # A card running outside the app is freed but left running when turned off.
    up["b"] = True
    client.put("/api/gpus/b", json={"enabled": False})
    wait_for(lambda: "b" in world["freed"], what="b freed")
    time.sleep(0.2)
    assert ops.killed == [ops.spawned[0]["pid"]]


def test_gpus_rows_and_events_carry_engine_state(client, world):
    rows = client.get("/api/gpus").json()
    for r in rows:
        assert {"state", "owned", "can_start", "stop_when_idle_minutes", "last_error"} <= set(r)
        assert r["state"] == "stopped" and r["owned"] is False and r["can_start"] is True and r["stop_when_idle_minutes"] == 0
    with client.websocket_connect("/api/ws/events") as ws:
        assert ws.receive_json()["type"] == "hello"
        client.post("/api/gpus/b/start")
        for _ in range(30):
            ev = ws.receive_json()
            if ev["type"] == "gpus" and next(g for g in ev["gpus"] if g["instance"] == "b")["state"] == "starting":
                break
        else:
            raise AssertionError("no gpus event with b starting")
    r = client.post("/api/gpus/z/start")
    assert r.status_code == 404


def test_no_start_script_means_no_start(settings, monkeypatch: pytest.MonkeyPatch):
    """Without a start_script (the default, and the other test modules' config) nothing is ever spawned."""
    ops = StubOps()
    monkeypatch.setattr(instances, "ProcessOps", lambda: ops)
    bare = settings.model_copy(update={"instances": {"a": InstanceConfig(url="http://127.0.0.1:9")}})
    sup = Supervisor(bare, {"a": FakeClient()}, ops=ops)
    assert not sup.can_start("a")
    unscripted = _unscripted(settings)
    q = jobs.JobQueue(unscripted)

    async def go():
        await q.start()
        try:
            assert all(not q.supervisor.can_start(n) for n in unscripted.instances)
            assert await q._prepare("b", "nope") is True
            assert all(x["can_start"] is False for x in q.gpus())
            with pytest.raises(ValueError, match="no start script"):
                q.start_engine("b")
        finally:
            await q.stop()

    asyncio.run(go())
    assert ops.spawned == []
