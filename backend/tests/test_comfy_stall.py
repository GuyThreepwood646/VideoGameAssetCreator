"""A ComfyUI prompt that gets no result (the card hung: 2026-11-20 card B stopped on a BiRefNet cut-out after many Qwen
edit rounds, the log shows "Model BiRefNet prepared" and nothing after it) must fail in bounded time with a plain
message that names the card and says the card may need restarting. The app never restarts ComfyUI itself."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from vgac import comfy, jobs
from vgac.graphs.birefnet import cutout_graph
from vgac.graphs.qwen_edit import edit_graph


def _silent_client(monkeypatch):
    """A client whose /history never reports the prompt as finished (a hung engine that still answers HTTP)."""
    client = comfy.ComfyClient("http://127.0.0.1:9", "b")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9")
    monkeypatch.setattr(client, "_client", lambda: http)
    return client, http


def test_a_cutout_graph_gets_the_short_cutout_limit():
    g = cutout_graph("in.png", "out/x")
    assert comfy.time_limit_for(g, 3600.0) == comfy.CUTOUT_TIME_LIMIT_SECONDS
    assert comfy.time_limit_for(g, None) == comfy.CUTOUT_TIME_LIMIT_SECONDS
    # a default already shorter than the cut-out limit is kept
    assert comfy.time_limit_for(g, 60.0) == 60.0


def test_other_graphs_keep_the_job_time_limit():
    g = edit_graph("in.png", "make it blue", seed=1, prefix="out/e")
    assert comfy.time_limit_for(g, 3600.0) == 3600.0
    assert comfy.time_limit_for(g, None) is None


def test_a_prompt_with_no_result_past_its_limit_raises_stalled(monkeypatch):
    monkeypatch.setattr(comfy, "CUTOUT_TIME_LIMIT_SECONDS", 0.2)
    client, http = _silent_client(monkeypatch)

    async def run():
        try:
            await client.wait("p1", poll_seconds=0.01, timeout=0.2)
        finally:
            await http.aclose()

    with pytest.raises(comfy.ComfyStalled) as ei:
        asyncio.run(run())
    # still a ComfyError, so existing handlers that catch the workflow error keep working
    assert isinstance(ei.value, comfy.ComfyError)
    # a stalled prompt is not the engine going away: it is not counted as a lost engine
    assert not jobs.is_engine_lost(ei.value)


def test_the_stalled_message_names_the_card_and_says_restart_by_hand(monkeypatch):
    monkeypatch.setattr(jobs, "gpu_name", lambda instance, settings=None: "RTX 3080")
    msg = jobs.stalled_message("b", "cutting out the background", 300.0)
    assert "RTX 3080" in msg
    assert "cutting out the background" in msg
    assert "within 5 minutes" in msg
    assert "Restart ComfyUI for that card" in msg
    assert "has not restarted it" in msg
    assert "restart.ps1" not in msg
    assert all(ord(ch) < 0x2600 for ch in msg)  # no emoji


def test_the_stalled_message_for_a_short_limit_uses_seconds(monkeypatch):
    monkeypatch.setattr(jobs, "gpu_name", lambda instance, settings=None: "RTX 3060")
    msg = jobs.stalled_message("a", "running", 45.0)
    assert "within 45 seconds" in msg
    assert "RTX 3060" in msg


def test_time_waiting_in_the_pending_queue_does_not_count(monkeypatch):
    """A cut-out queued behind another client's long job on a shared card is not hung: the limit starts when the
    prompt leaves ComfyUI's pending queue."""
    client = comfy.ComfyClient("http://127.0.0.1:9", "b")
    queue_reads = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/queue":
            queue_reads["n"] += 1
            pending = [[1, "p1", {}, {}, []]] if queue_reads["n"] <= 20 else []
            return httpx.Response(200, json={"queue_running": [[0, "other", {}, {}, []]], "queue_pending": pending})
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9")
    monkeypatch.setattr(client, "_client", lambda: http)
    monkeypatch.setattr(client, "_client_at", lambda url: http)

    async def run():
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            await client.wait("p1", poll_seconds=0.01, timeout=0.2)
        except comfy.ComfyStalled:
            return queue_reads["n"], loop.time() - t0
        finally:
            await http.aclose()
        raise AssertionError("expected a stall")

    reads, _ = asyncio.run(run())
    # it kept waiting through every pending read, then the limit ran from the first read that found it running
    assert reads == 21
