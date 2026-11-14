"""A card short of memory whose engine is idle is asked to let go of its models, then measured again (jobs.JobQueue.lend,
live check 2026-11-13: the idle 3060 kept the drawing model loaded under DynamicVRAM, which ComfyUI's torch_vram
figures do not show, and read 3.1 GB of room). It is lent only if the 7 GB floor is met afterwards; an engine with
anything in its own queue is never asked; memory another program holds stays a refusal; the running job's message says
what happened."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vgac import jobs
from vgac.db import Job, session_factory
from test_card_lending import GB, KIND, FakeClient, make_queue, queued_job


class HoldingClient(FakeClient):
    """An engine that keeps `own_gb` of models loaded outside its torch figures; /free gives that back. `other_gb` is
    held by another program on the same card and never comes back."""

    def __init__(self, name: str, free_gb: float, own_gb: float = 0.0, queue: dict | None = None,
                 free_ok: bool = True) -> None:
        super().__init__(name, free_gb=free_gb)
        self.own_gb = own_gb
        self.queue = queue if queue is not None else {"queue_running": [], "queue_pending": []}
        self.free_ok = free_ok
        self.frees = 0
        self.fresh_reads = 0

    async def queue_state(self) -> dict[str, Any]:
        return self.queue

    async def free(self) -> bool:
        self.frees += 1
        if self.free_ok:
            self.free_gb += self.own_gb
            self.own_gb = 0.0
        return self.free_ok

    async def vram_summary(self) -> dict[str, Any]:
        self.fresh_reads += 1
        return await super().cached_vram_summary(0)


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
    monkeypatch.setattr(jobs, "LEND_FREE_SETTLE_SECONDS", 0.0)


def running_job(instance: str = "b") -> str:
    with session_factory()() as db:
        j = Job(instance=instance, kind=KIND, status="running", payload_json="{}", message="view front")
        db.add(j)
        db.commit()
        return j.id


def test_an_idle_engine_holding_its_own_model_lets_go_and_is_lent(settings):
    a = HoldingClient("a", free_gb=3.0, own_gb=8.3)
    q = make_queue(settings, a=a)
    jid = running_job()
    lease, why = asyncio.run(q.lend("b", jid, KIND))
    assert lease is not None and why == "" and lease.instance == "a"
    assert a.frees == 1 and a.fresh_reads == 1
    assert "let go of the models it was keeping loaded" in lease.note and "3.0 GB before, 11.3 GB after" in lease.note
    with session_factory()() as db:
        assert db.get(Job, jid).message == lease.note


def test_memory_another_program_holds_is_still_refused(settings):
    # unloading frees nothing: the engine held no models of its own
    a = HoldingClient("a", free_gb=3.0, own_gb=0.0)
    q = make_queue(settings, a=a)
    lease, why = asyncio.run(q.lend("b", running_job(), KIND))
    assert lease is None and a.frees == 1
    assert "3.0 GB of memory for drawing, 7 GB are needed" in why and "another program holds the rest" in why
    assert q.workers["a"].lent_to is None and q.leases == {}


def test_freeing_that_is_not_enough_is_refused_with_the_new_figure(settings):
    a = HoldingClient("a", free_gb=3.0, own_gb=2.0)
    q = make_queue(settings, a=a)
    lease, why = asyncio.run(q.lend("b", running_job(), KIND))
    assert lease is None and "5.0 GB of memory for drawing" in why


@pytest.mark.parametrize("queue", [
    {"queue_running": [["x"]], "queue_pending": []},     # someone's prompt running on it (not the app's)
    {"queue_running": [], "queue_pending": [["y"]]},     # someone's prompt waiting
])
def test_an_engine_with_anything_in_its_queue_is_never_asked(settings, queue):
    a = HoldingClient("a", free_gb=3.0, own_gb=8.3, queue=queue)
    q = make_queue(settings, a=a)
    lease, why = asyncio.run(q.lend("b", running_job(), KIND))
    assert lease is None and a.frees == 0 and "3.0 GB of memory for drawing" in why
    assert "even after" not in why


def test_an_engine_that_cannot_say_its_queue_is_not_asked(settings):
    a = HoldingClient("a", free_gb=3.0, own_gb=8.3)

    async def broken():
        raise RuntimeError("no answer")

    a.queue_state = broken
    q = make_queue(settings, a=a)
    lease, _ = asyncio.run(q.lend("b", running_job(), KIND))
    assert lease is None and a.frees == 0


def test_a_refused_free_is_a_plain_refusal(settings):
    a = HoldingClient("a", free_gb=3.0, own_gb=8.3, free_ok=False)
    q = make_queue(settings, a=a)
    lease, why = asyncio.run(q.lend("b", running_job(), KIND))
    assert lease is None and a.frees == 1 and "even after" not in why


def test_work_that_lands_on_the_card_while_it_lets_go_wins(settings):
    a = HoldingClient("a", free_gb=3.0, own_gb=8.3)
    real = a.free

    async def free_then_job():
        queued_job("a")                       # someone queued work on it while it unloaded
        return await real()

    a.free = free_then_job
    q = make_queue(settings, a=a)
    lease, why = asyncio.run(q.lend("b", running_job(), KIND))
    assert lease is None and "has work waiting" in why and q.workers["a"].lent_to is None


def test_enough_room_already_asks_nothing(settings):
    a = HoldingClient("a", free_gb=8.8, own_gb=1.0)
    q = make_queue(settings, a=a)
    lease, _ = asyncio.run(q.lend("b", running_job(), KIND))
    assert lease is not None and a.frees == 0 and lease.note == ""


def test_one_card_asks_nothing(settings, monkeypatch):
    monkeypatch.delitem(settings.instances, "a")
    q = make_queue(settings)
    lease, why = asyncio.run(q.lend("b", running_job(), KIND))
    assert lease is None and why == "this computer has one graphics card"
