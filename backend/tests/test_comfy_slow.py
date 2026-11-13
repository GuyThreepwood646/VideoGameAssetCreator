"""A busy engine that stops answering HTTP while it loads a large model is not an engine that went away (realistic
test, 2026-11-13: two side-view jobs on the 10 GB card died with a ReadTimeout on a /history poll during a 187 s cold
load). comfy.ComfyClient.wait keeps polling through read timeouts until nothing has been read for
ENGINE_STALL_SECONDS; a refused connection still raises at once."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from vgac import comfy, jobs

DONE = {"p1": {"status": {"status_str": "success", "completed": True}, "outputs": {"save": {"images": []}}}}


def fake_client(monkeypatch, handler):
    client = comfy.ComfyClient("http://127.0.0.1:9", "b")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9")
    monkeypatch.setattr(client, "_client", lambda: http)
    return client, http


def test_a_busy_engine_that_answers_late_finishes_the_job(monkeypatch):
    monkeypatch.setattr(comfy, "ENGINE_STALL_SECONDS", 5.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:   # three polls in a row get no answer, as while the model loads
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=DONE)

    client, http = fake_client(monkeypatch, handler)
    polled: list[float] = []

    async def run():
        try:
            return await client.wait("p1", poll_seconds=0.01, on_poll=polled.append)
        finally:
            await http.aclose()

    entry = asyncio.run(run())
    assert entry["status"]["completed"] and calls["n"] == 4
    assert len(polled) == 3          # the progress counter keeps going while the engine is silent


def test_an_engine_silent_past_the_limit_counts_as_gone(monkeypatch):
    monkeypatch.setattr(comfy, "ENGINE_STALL_SECONDS", 0.2)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client, http = fake_client(monkeypatch, handler)

    async def run():
        try:
            await client.wait("p1", poll_seconds=0.02)
        finally:
            await http.aclose()

    with pytest.raises(httpx.ReadTimeout) as ei:
        asyncio.run(run())
    assert jobs.is_engine_lost(ei.value)


def test_a_refused_connection_raises_at_once(monkeypatch):
    monkeypatch.setattr(comfy, "ENGINE_STALL_SECONDS", 60.0)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, http = fake_client(monkeypatch, handler)

    async def run():
        try:
            await client.wait("p1", poll_seconds=0.01)
        finally:
            await http.aclose()

    t0 = time.monotonic()
    with pytest.raises(httpx.ConnectError) as ei:
        asyncio.run(run())
    assert time.monotonic() - t0 < 1.0 and jobs.is_engine_lost(ei.value)


def test_a_submit_waits_as_long_as_a_poll_may_stall(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.extensions["timeout"])
        return httpx.Response(200, json={"prompt_id": "p1"})

    client, http = fake_client(monkeypatch, handler)

    async def run():
        try:
            return await client.queue_prompt({"1": {}})
        finally:
            await http.aclose()

    assert asyncio.run(run()) == "p1"
    assert seen["read"] == comfy.SUBMIT_READ_TIMEOUT >= 300 and seen["connect"] == client.timeout
