"""/api/health answers within about a second while a ComfyUI engine or nvidia-smi stalls (comfy.health_vram_summary,
main._health_cards). The desktop launcher treats a missed answer after 3 s as a miss, so a slow read here shows as a
backend flap and reloads the app window. Engines that stall are the ones busy with a graph."""

from __future__ import annotations

import asyncio
import time

from vgac import cardmem, comfy
from test_gpus import client, fake_execute  # noqa: F401  (the app client fixture)


def test_engine_read_waits_at_most_the_limit_and_then_sends_the_last_answer(monkeypatch):
    stalled = asyncio.Event()
    calls: list[int] = []

    async def vram(self):
        calls.append(1)
        if len(calls) > 1:
            await stalled.wait()
        return {"reachable": True, "devices": [{"name": "cuda:0 test", "vram_total": 1, "vram_free": 1}]}

    monkeypatch.setattr(comfy.ComfyClient, "vram_summary", vram)

    async def go():
        c = comfy.ComfyClient("http://127.0.0.1:9", name="t")
        first = await c.health_vram_summary(max_age=0.0, wait=0.2)
        assert first["reachable"] is True and "stale" not in first
        t0 = time.monotonic()
        busy = await c.health_vram_summary(max_age=0.0, wait=0.2)
        took = time.monotonic() - t0
        assert took < 0.6, took
        assert busy["reachable"] is True and busy["stale"] is True and busy["devices"] == first["devices"]
        stalled.set()
        await asyncio.sleep(0.05)
        fresh = await c.health_vram_summary(max_age=0.0, wait=0.2)
        assert "stale" not in fresh

    asyncio.run(go())


def test_engine_that_never_answered_is_reported_as_not_answering_yet(monkeypatch):
    async def never(self):
        await asyncio.sleep(10)
        return {}

    monkeypatch.setattr(comfy.ComfyClient, "vram_summary", never)

    async def go():
        c = comfy.ComfyClient("http://127.0.0.1:9", name="t")
        t0 = time.monotonic()
        s = await c.health_vram_summary(max_age=0.0, wait=0.1)
        assert time.monotonic() - t0 < 0.5
        assert s["reachable"] is False and s["stale"] is True and s["error"] == "no answer yet"

    asyncio.run(go())


def test_health_answers_quickly_while_an_engine_stalls(client, monkeypatch):
    async def stalled(self):
        await asyncio.sleep(6)
        return {}

    monkeypatch.setattr(comfy.ComfyClient, "system_stats", stalled)
    t0 = time.monotonic()
    r = client.get("/api/health")
    took = time.monotonic() - t0
    assert r.status_code == 200 and r.json()["ok"] is True
    assert took < 1.5, took
    assert set(r.json()["instances"]) >= {"a", "b", "host"}


def test_health_answers_quickly_while_nvidia_smi_stalls(client, monkeypatch):
    def slow_read(max_age: float = cardmem.CACHE_SECONDS):
        time.sleep(2.5)
        return None

    monkeypatch.setattr(cardmem, "read", slow_read)
    t0 = time.monotonic()
    r = client.get("/api/health")
    took = time.monotonic() - t0
    assert r.status_code == 200 and r.json()["ok"] is True
    assert took < 1.5, took
