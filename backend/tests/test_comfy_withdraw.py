"""Stopping a job takes only the job's own prompts off a ComfyUI card (ComfyClient.withdraw, comfy.JOB_PROMPTS).

POST /interrupt takes no prompt id and stops whatever is running on the card, so a stop interrupts only when the job's
own prompt is the one running, deletes its prompt when it waits, and never interrupts for someone else's prompt. The
fake ComfyUI here answers HTTP through httpx.MockTransport: GET /queue, POST /interrupt, POST /queue (delete) and
POST /prompt. No graphics card is used."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from vgac import comfy, jobs
from vgac.comfy import ComfyClient
from vgac.stages import face_detail

# conftest.py replaces ComfyClient.interrupt with a no-op for every test; these tests need the real one, which sends
# POST /interrupt to the fake ComfyUI and so shows whether a stop interrupted the card.
_REAL_INTERRUPT = ComfyClient.interrupt


@pytest.fixture(autouse=True)
def real_interrupt(monkeypatch):
    monkeypatch.setattr(ComfyClient, "interrupt", _REAL_INTERRUPT)


class FakeComfy:
    """What ComfyUI would answer: `running` and `pending` are prompt ids. Records every request it gets."""

    def __init__(self, running=(), pending=()):
        self.running, self.pending = list(running), list(pending)
        self.calls: list[tuple[str, str, dict]] = []
        self.submitted = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, body))
        if request.method == "GET" and path == "/queue":
            return httpx.Response(200, json={
                "queue_running": [[0, pid, {}, {}, []] for pid in self.running],
                "queue_pending": [[1, pid, {}, {}, []] for pid in self.pending],
            })
        if request.method == "POST" and path == "/interrupt":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/queue":
            self.pending = [pid for pid in self.pending if pid not in body.get("delete", [])]
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/prompt":
            self.submitted += 1
            return httpx.Response(200, json={"prompt_id": f"submitted-{self.submitted}"})
        return httpx.Response(404, json={})

    @property
    def interrupts(self) -> int:
        return sum(1 for _, path, _ in self.calls if path == "/interrupt")

    @property
    def deleted(self) -> list[str]:
        return [pid for _, path, body in self.calls if path == "/queue" for pid in body.get("delete", [])]


async def card(fake: FakeComfy) -> ComfyClient:
    """A ComfyClient whose HTTP goes to `fake`. Made inside the running loop, as the app does."""
    c = ComfyClient("http://fake-comfy.invalid:8188", name="a")
    c._http = httpx.AsyncClient(base_url=c.base_url, transport=httpx.MockTransport(fake.handler))
    c._http_loop = asyncio.get_running_loop()
    return c


async def stop_job(settings, fake: FakeComfy, sent_ids: list[str]) -> None:
    """Stop a running job whose own prompts on the card are `sent_ids`, the way JobQueue does
    (InstanceWorker.cancel_current). The job's prompts are (client, prompt_id) pairs, as queue_prompt records them."""
    client = await card(fake)
    worker = jobs.InstanceWorker(name="a", settings=settings, clients={"a": client})
    worker._loop = asyncio.get_running_loop()
    worker.current_job_id = "job1"
    ctx = jobs.JobContext(job_id="job1", instance="a", payload={}, settings=settings, clients={"a": client},
                          _progress_cb=lambda fraction, message: None)
    ctx.prompts = [(client, pid) for pid in sent_ids]
    worker.current_ctx = ctx
    assert worker.cancel_current("job1")
    await asyncio.sleep(0.05)  # the withdrawal runs as a task on the loop


def test_stopping_a_job_whose_prompt_is_running_interrupts(settings):
    fake = FakeComfy(running=["mine"])
    asyncio.run(stop_job(settings, fake, ["mine"]))
    assert fake.interrupts == 1
    assert fake.deleted == []


def test_stopping_a_job_whose_prompt_is_pending_deletes_it_and_does_not_interrupt(settings):
    fake = FakeComfy(running=["someone-else"], pending=["mine"])
    asyncio.run(stop_job(settings, fake, ["mine"]))
    assert fake.interrupts == 0
    assert fake.deleted == ["mine"]
    assert fake.running == ["someone-else"]


def test_stopping_a_job_while_another_prompt_runs_never_interrupts(settings):
    # The job's own prompt "mine" is not on the card any more; "someone-else" is running there. A stop must not send
    # /interrupt, which would cut that prompt short. (In master the stop path interrupted whatever was running.)
    fake = FakeComfy(running=["someone-else"])
    asyncio.run(stop_job(settings, fake, ["mine"]))
    assert fake.interrupts == 0
    assert fake.deleted == []


def test_a_job_with_no_prompt_on_the_card_leaves_the_card_alone(settings):
    fake = FakeComfy(running=["someone-else"])
    asyncio.run(stop_job(settings, fake, []))
    assert fake.interrupts == 0 and fake.deleted == []


def test_a_queue_that_cannot_be_read_never_interrupts(settings):
    class Broken(FakeComfy):
        def handler(self, request):
            if request.url.path == "/queue":
                raise httpx.ConnectError("card gone")
            return super().handler(request)

    fake = Broken(running=["mine"])

    async def scene():
        c = await card(fake)
        assert await c.withdraw("mine") == "failed"

    asyncio.run(scene())
    assert fake.interrupts == 0


def test_face_detail_withdraw_keeps_its_behaviour(settings):
    fake = FakeComfy(running=["someone-else"], pending=["mine"])

    async def scene():
        c = await card(fake)
        await face_detail._withdraw(c, "mine")
        assert fake.interrupts == 0 and fake.deleted == ["mine"]
        fake.running, fake.pending = ["mine"], []
        await face_detail._withdraw(c, "mine")

    asyncio.run(scene())
    assert fake.interrupts == 1


def test_a_prompt_is_interrupted_once_even_when_withdrawn_twice(settings):
    # The prompt still shows as running after the first interrupt (ComfyUI has not yet dropped it). A second withdraw
    # must not send a second interrupt: by then another client's prompt may be the one running.
    fake = FakeComfy(running=["mine"])

    async def scene():
        c = await card(fake)
        assert await c.withdraw("mine") == "interrupted"
        assert await c.withdraw("mine") == "interrupted"

    asyncio.run(scene())
    assert fake.interrupts == 1


def test_queue_prompt_records_the_prompt_in_the_running_jobs_list(settings):
    fake = FakeComfy()
    sent: list = []

    async def scene():
        c = await card(fake)
        token = comfy.JOB_PROMPTS.set(sent)
        try:
            pid = await c.queue_prompt({"1": {"class_type": "X", "inputs": {}}})
        finally:
            comfy.JOB_PROMPTS.reset(token)
        return c, pid

    client, pid = asyncio.run(scene())
    assert sent == [(client, pid)]
