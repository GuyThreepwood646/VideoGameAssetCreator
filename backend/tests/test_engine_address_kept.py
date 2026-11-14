"""An engine's address is never changed while it works for the app (live check 2026-11-13: the 3060's engine was slow
to answer while it loaded the drawing model, the search found another ComfyUI on the same card on port 8190, the
client was pointed there mid-draw and polled 8190 for 13 minutes for a prompt that had finished on 8188 in 82 s).

Rules: (1) instances.Supervisor.locate never searches other ports or switches the address of an instance in use: a
job running on its card, the card lent, or a prompt being waited on; a missing answer then means busy, not gone.
(2) comfy.ComfyClient.wait keeps asking the address the prompt was sent to. (3) An engine that really went away still
ends the wait at once (refused connection), which the queue counts as the engine lost, and the search runs again
once the instance is no longer in use."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from vgac import comfy, jobs
from test_discover import A_URL, DEV_3060, DEV_3080, B_URL, make

OTHER = "http://127.0.0.1:8190"
DONE = {"status": {"status_str": "success", "completed": True}, "outputs": {"save": {"images": []}}}


# --- rule 1: locate --------------------------------------------------------------------------------------------------

def test_a_prompt_being_waited_on_keeps_the_address(settings, tmp_path):
    world = {OTHER: DEV_3060}          # 8188 silent (loading a model), another ComfyUI on the 3060 at 8190
    s, clients, sup, probed = make(settings, tmp_path, world)
    clients["a"].waiting = 1
    assert asyncio.run(sup.answers("a")) is True           # busy, not gone: no start, no "stopped"
    assert clients["a"].base_url == A_URL and sup.found == {} and probed == []


def test_a_job_on_the_card_or_a_lent_card_keeps_the_address(settings, tmp_path):
    world = {OTHER: DEV_3060}
    s, clients, sup, probed = make(settings, tmp_path, world)
    in_use = {"a"}
    sup.busy = lambda n: n in in_use
    assert asyncio.run(sup.locate("a", force=True)) is True
    assert clients["a"].base_url == A_URL and probed == []
    # no longer in use: the search runs as before and finds the engine on its new port
    in_use.clear()
    assert asyncio.run(sup.locate("a", force=True)) is True
    assert clients["a"].base_url == OTHER and sup.found == {"a": OTHER}


def test_in_use_answering_from_another_card_is_not_moved_either(settings, tmp_path):
    world = {A_URL: DEV_3080, OTHER: DEV_3060}
    s, clients, sup, probed = make(settings, tmp_path, world)
    sup.busy = lambda n: True
    assert asyncio.run(sup.locate("a", force=True)) is False
    assert clients["a"].base_url == A_URL and probed == [] and sup.conflict("a")


def test_a_broken_busy_hook_counts_as_not_in_use(settings, tmp_path):
    s, clients, sup, _ = make(settings, tmp_path, {OTHER: DEV_3060})

    def boom(n):
        raise RuntimeError("x")

    sup.busy = boom
    assert sup.in_use("a") is False
    assert asyncio.run(sup.answers("a")) is True and clients["a"].base_url == OTHER


def test_the_queue_says_which_cards_are_in_use(settings):
    q = jobs.JobQueue(settings)
    for name in list(settings.instances) + [jobs.HOST]:
        q.workers[name] = jobs.InstanceWorker(name=name, settings=settings, clients=q.clients)
    assert not q.card_in_use("a") and not q.card_in_use("b")
    q.workers["a"].current_job_id = "j1"
    assert q.card_in_use("a")
    q.workers["a"].current_job_id = None
    q.workers["b"].lent_to = "j2"
    assert q.card_in_use("b") and not q.card_in_use("a")


# --- rule 2 and 3: the client ----------------------------------------------------------------------------------------

@pytest.fixture
def engines(monkeypatch):
    """Two fake ComfyUIs: 8188 takes prompt p1 and finishes it on the third poll; 8190 never heard of it."""
    seen: list[str] = []
    state = {"polls": 0, "dead": False}
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        url = f"{request.url.scheme}://{request.url.host}:{request.url.port}"
        seen.append(f"{request.method} {url}{request.url.path}")
        if state["dead"] and url == A_URL:
            raise httpx.ConnectError("refused", request=request)
        if request.method == "POST" and request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p1"})
        if request.url.path == "/history/p1":
            if url != A_URL:
                return httpx.Response(200, json={})
            state["polls"] += 1
            return httpx.Response(200, json={"p1": DONE} if state["polls"] >= 3 else {})
        return httpx.Response(404)

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setattr(comfy, "POLL_FAST_SECONDS", 0.01)
    return seen, state


def test_wait_keeps_asking_where_the_prompt_was_sent(engines):
    seen, state = engines
    client = comfy.ComfyClient(A_URL, "a")
    during: list[int] = []

    async def go():
        pid = await client.queue_prompt({"1": {}})
        client.set_base_url(OTHER)                 # pointed elsewhere mid-draw
        try:
            return await client.wait(pid, poll_seconds=0.01, on_poll=lambda _e: during.append(client.waiting))
        finally:
            await client.aclose()

    entry = asyncio.run(go())
    assert entry["status"]["completed"]
    assert not any(OTHER in s and "/history" in s for s in seen)
    assert during and set(during) == {1} and client.waiting == 0
    assert client.prompt_url("p1") == client.base_url == OTHER   # forgotten once the wait ended


def test_an_engine_that_really_went_away_still_ends_the_wait_at_once(engines):
    seen, state = engines
    client = comfy.ComfyClient(A_URL, "a")

    async def go():
        pid = await client.queue_prompt({"1": {}})
        state["dead"] = True
        try:
            await client.wait(pid, poll_seconds=0.01)
        finally:
            await client.aclose()

    with pytest.raises(httpx.ConnectError) as e:
        asyncio.run(go())
    assert jobs.is_engine_lost(e.value)          # the queue requeues it once, as before
