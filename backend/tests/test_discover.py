"""Finding instance A by its card instead of a fixed port (config.DiscoverConfig, instances.Supervisor.locate).

Every ComfyUI is a stub: `world` maps an address to the device name its /system_stats reports (None: nothing
answers there), so nothing real is reached or started."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vgac.config import DiscoverConfig
from vgac.instances import StartFailed, Supervisor, conflict_message, search_ports

A_URL, B_URL = "http://127.0.0.1:8188", "http://127.0.0.1:8189"
DEV_3060 = "cuda:1 NVIDIA GeForce RTX 3060 : cudaMallocAsync"
DEV_3080 = "cuda:0 NVIDIA GeForce RTX 3080 : cudaMallocAsync"


def summary_of(dev: str | None) -> dict[str, Any]:
    return {"reachable": dev is not None, "devices": [{"name": dev}] if dev else []}


class CardClient:
    def __init__(self, url: str, world: dict[str, str | None]) -> None:
        self.base_url = url.rstrip("/")
        self.world = world
        self.cache_forgotten = 0

    async def cached_vram_summary(self, max_age: float = 5.0) -> dict[str, Any]:
        return summary_of(self.world.get(self.base_url))

    def last_vram_summary(self) -> dict[str, Any]:
        return summary_of(self.world.get(self.base_url))

    def set_base_url(self, url: str) -> None:
        self.base_url = url.rstrip("/")

    def forget_cache(self) -> None:
        self.cache_forgotten += 1


class NoOps:
    """Process calls must not happen in these tests except where a test says so."""

    def __init__(self) -> None:
        self.spawned: list[Any] = []

    def spawn(self, script, cwd, out_log, err_log) -> int:
        self.spawned.append(script)
        return 1

    def alive(self, pid: int) -> bool:
        return False

    def create_time(self, pid: int):
        return None

    def kill_tree(self, pid: int) -> None:
        raise AssertionError("nothing may be killed")

    def find(self, match: str) -> list[int]:
        return []

    def port_open(self, url: str) -> bool:
        return True  # something listens: the supervisor waits instead of starting a copy


def card_settings(settings, tmp_path, a_ports=(8188, 8189, 8190, 8191, 8000)):
    script = tmp_path / "start.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    inst = {
        "a": settings.instances["a"].model_copy(update={
            "url": A_URL, "gpu": "RTX 3060", "start_script": script, "start_timeout_seconds": 0.3,
            "external_owner": "ComfyUI Desktop", "discover": DiscoverConfig(gpu_match="3060", ports=list(a_ports))}),
        "b": settings.instances["b"].model_copy(update={
            "url": B_URL, "gpu": "RTX 3080", "start_script": script, "start_timeout_seconds": 0.3,
            "discover": DiscoverConfig(gpu_match="3080", ports=[])}),
    }
    return settings.model_copy(update={"instances": inst})


def make(settings, tmp_path, world):
    s = card_settings(settings, tmp_path)
    clients = {"a": CardClient(A_URL, world), "b": CardClient(B_URL, world)}
    probed: list[str] = []

    async def probe(url: str):
        probed.append(url)
        dev = world.get(url.rstrip("/"))
        return summary_of(dev) if dev else None

    sup = Supervisor(s, clients, ops=NoOps(), poll_seconds=0.01, project_dir=tmp_path, probe=probe)
    return s, clients, sup, probed


def test_a_is_found_on_another_port_by_its_card(settings, tmp_path):
    # ComfyUI Desktop came up on 8190 this time; 8188 is silent.
    world = {"http://127.0.0.1:8190": DEV_3060}
    s, clients, sup, probed = make(settings, tmp_path, world)
    assert asyncio.run(sup.answers("a")) is True
    assert clients["a"].base_url == "http://127.0.0.1:8190"
    assert sup.current_url("a") == "http://127.0.0.1:8190" and sup.found == {"a": "http://127.0.0.1:8190"}
    assert sup.conflict("a") == ""
    # The configured url was not re-asked as a candidate of its own (it was the current address).
    assert A_URL not in probed
    # The next question goes to the new address and needs no probing.
    probed.clear()
    assert asyncio.run(sup.answers("a")) is True and probed == []


def test_a_never_takes_the_port_of_the_other_card(settings, tmp_path):
    # 8188 silent, 8189 is instance b (3080), 8191 is the 3060.
    world = {B_URL: DEV_3080, "http://127.0.0.1:8191": DEV_3060}
    s, clients, sup, _ = make(settings, tmp_path, world)
    assert asyncio.run(sup.answers("a")) is True
    assert clients["a"].base_url == "http://127.0.0.1:8191"
    # Only the 3080 answers anywhere: a is not found, and b keeps its port.
    world2 = {B_URL: DEV_3080}
    s2, clients2, sup2, _ = make(settings, tmp_path, world2)
    assert asyncio.run(sup2.answers("a")) is False
    assert clients2["a"].base_url == A_URL and sup2.found == {}
    assert asyncio.run(sup2.answers("b")) is True


def test_a_answering_on_its_own_port_from_its_card_needs_no_probe(settings, tmp_path):
    world = {A_URL: DEV_3060}
    s, clients, sup, probed = make(settings, tmp_path, world)
    assert asyncio.run(sup.answers("a")) is True and probed == [] and sup.found == {}


def test_probes_are_rate_limited(settings, tmp_path):
    world: dict[str, str | None] = {}
    s, clients, sup, probed = make(settings, tmp_path, world)
    assert asyncio.run(sup.answers("a")) is False
    first = len(probed)
    assert first == 4  # 8189, 8190, 8191, 8000; 8188 is the current address
    assert asyncio.run(sup.answers("a")) is False
    assert len(probed) == first  # within PROBE_SECONDS: no second round
    assert asyncio.run(sup.locate("a", force=True)) is False and len(probed) == 2 * first


def test_b_port_taken_by_the_other_card_is_a_setup_failure(settings, tmp_path):
    # ComfyUI Desktop (the 3060) took 8189, b's port.
    world = {B_URL: DEV_3060}
    s, clients, sup, _ = make(settings, tmp_path, world)
    assert asyncio.run(sup.answers("b")) is False
    msg = sup.conflict("b")
    assert msg == ("ComfyUI Desktop is using port 8189 on the RTX 3060; close it or change its port so the RTX 3080 can "
                   "use port 8189.")
    # The start fails at once with that reason; nothing is started and no work goes to the wrong card.
    with pytest.raises(StartFailed, match="ComfyUI Desktop is using port 8189"):
        asyncio.run(sup.ensure_running("b"))
    assert sup.state("b") == "failed" and sup.ops.spawned == []
    assert not sup.identity_ok("b", summary_of(DEV_3060)) and sup.identity_ok("b", summary_of(DEV_3080))
    # Meanwhile a is found there, on its own card.
    assert asyncio.run(sup.answers("a")) is True and clients["a"].base_url == B_URL
    # Desktop closes and b's own copy comes up: the problem clears.
    world[B_URL] = DEV_3080
    assert asyncio.run(sup.answers("b")) is True and sup.conflict("b") == ""


def test_conflict_message_for_an_unknown_card(settings, tmp_path):
    s = card_settings(settings, tmp_path)
    msg = conflict_message(s, "b", B_URL, "cuda:0 Some Other GPU")
    assert msg.startswith("The graphics engine on port 8189 runs on another card (cuda:0 Some Other GPU), not the RTX 3080")


def test_search_ports_without_discover_is_none(settings):
    async def probe(url):
        raise AssertionError("no probe without discover")

    assert asyncio.run(search_ports(settings, "a", settings.instances["a"].url, probe)) is None


def test_instances_without_discover_behave_as_before(settings, tmp_path):
    clients = {"a": CardClient("http://127.0.0.1:9", {"http://127.0.0.1:9": DEV_3080}), "b": CardClient("http://127.0.0.1:10", {})}

    async def probe(url):
        raise AssertionError("no probe without discover")

    sup = Supervisor(settings, clients, ops=NoOps(), project_dir=tmp_path, probe=probe)
    assert asyncio.run(sup.answers("a")) is True  # any answer counts
    assert asyncio.run(sup.answers("b")) is False and sup.conflict("b") == ""


# --- through the queue and GET /api/gpus -----------------------------------------------------------------------------


def test_gpus_show_the_found_address_and_the_setup_problem(settings, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vgac import comfy, config, instances, jobs

    s = card_settings(settings, tmp_path)
    world = {B_URL: DEV_3060}  # Desktop on the 3060 took 8189; nothing on 8188

    async def vram_summary(self):
        return summary_of(world.get(self.base_url))

    async def probe(url):
        dev = world.get(url.rstrip("/"))
        return summary_of(dev) if dev else None

    monkeypatch.setattr(comfy.ComfyClient, "vram_summary", vram_summary)
    monkeypatch.setattr(instances, "probe_url", probe)
    monkeypatch.setattr(instances, "ProcessOps", NoOps)
    monkeypatch.setattr(jobs, "IDLE_CHECK_SECONDS", 3600.0)
    import yaml

    cfg = tmp_path / "config-cards.yaml"
    cfg.write_text(yaml.safe_dump(s.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("VGAC_CONFIG", str(cfg))
    config.reset_settings()
    from vgac import main

    with TestClient(main.app) as c:
        q = jobs.active_queue()
        assert q is not None and q._locate_task is not None  # the start looks for the cards with discover
        import time

        t0 = time.time()
        while time.time() - t0 < 5 and q.supervisor.found.get("a") != B_URL:
            time.sleep(0.05)
        rows = {r["instance"]: r for r in c.get("/api/gpus").json()}
        assert rows["a"]["url"] == B_URL and rows["a"]["found_by_card"] is True and rows["a"]["setup_problem"] == ""
        assert rows["a"]["reachable"] is True
        # b: its port answers from the 3060. Not reachable for its work, with the plain problem.
        asyncio.run_coroutine_threadsafe(q.supervisor.locate("b", force=True), q.workers["b"]._loop).result(5)
        rows = {r["instance"]: r for r in c.get("/api/gpus").json()}
        assert rows["b"]["reachable"] is False
        assert rows["b"]["setup_problem"].startswith("ComfyUI Desktop is using port 8189")
