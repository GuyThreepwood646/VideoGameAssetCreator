"""Lending a second card to a running step (jobs.JobQueue.lend, Lease; docs/AFTER-TEST-CHANGES.md D2 items 17 and 19).
A card is lent only when it is on, allowed to draw pictures, idle, has nothing waiting (on it, or with sharing on,
anywhere it would take over), its engine answers and it has the memory; while lent its worker picks nothing; it comes
back when the step hands it back or the job ends; turning it off takes it back. The queue is built by hand (no worker
loops, no ComfyUI) except where a worker is started to show it waits."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vgac import jobs, stages
from vgac.db import Job, session_factory

GB = 1024**3
KIND = "turnaround.run"


class FakeClient:
    def __init__(self, name: str, free_gb: float = 8.8, held_gb: float = 0.0, up: bool = True):
        self.name, self.free_gb, self.held_gb, self.up = name, free_gb, held_gb, up
        self.interrupts = 0

    async def cached_vram_summary(self, max_age: float = 5.0) -> dict[str, Any]:
        if not self.up:
            return {"reachable": False, "devices": []}
        return {"reachable": True, "devices": [{"vram_total": 10 * GB, "vram_free": self.free_gb * GB,
                                                "torch_vram_total": (self.held_gb + 0.5) * GB, "torch_vram_free": 0.5 * GB}]}

    def last_vram_summary(self):
        return None

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def free(self) -> bool:
        return True


def make_queue(settings, **clients: FakeClient) -> jobs.JobQueue:
    q = jobs.JobQueue(settings)
    for name in settings.instances:
        q.clients[name] = clients.get(name) or FakeClient(name)
    for name in list(settings.instances) + [jobs.HOST]:
        gpu = name in settings.instances
        q.workers[name] = jobs.InstanceWorker(
            name=name, settings=settings, clients=q.clients,
            allows=(lambda kind, n=name: q.kind_allowed_here(n, kind)) if gpu else None,
            share=(lambda n=name: q.take_shared(n)) if gpu else None,
            lend=q.lend if gpu else None, after_job=q.end_leases)
    return q


def queued_job(instance: str, kind: str = "concept.run") -> str:
    with session_factory()() as db:
        j = Job(instance=instance, kind=kind, status="queued", payload_json="{}", message="")
        db.add(j)
        db.commit()
        return j.id


def lend(q, borrower="b", job="job1"):
    return asyncio.run(q.lend(borrower, job, KIND))


def test_a_free_card_is_lent_and_handed_back(settings):
    q = make_queue(settings)
    lease, why = lend(q)
    assert lease is not None and why == "" and lease.instance == "a" and lease.job_id == "job1"
    assert q.workers["a"].lent_to == "job1" and q.leases == {"a": lease}
    assert q.status()["a"]["lent_to"] == "job1"
    # while lent, nothing else is given to it
    assert q._card_takes("a") is False and q.take_shared("a") is None
    again, why = lend(q, borrower="b", job="job2")
    assert again is None and "already helping another step" in why
    asyncio.run(lease.end())
    assert q.workers["a"].lent_to is None and q.leases == {} and lease.ended
    asyncio.run(lease.end())   # twice is harmless


def test_one_card(settings, monkeypatch):
    monkeypatch.delitem(settings.instances, "a")
    q = make_queue(settings)
    lease, why = lend(q)
    assert lease is None and why == "this computer has one graphics card"


def test_a_busy_card_is_not_lent(settings):
    q = make_queue(settings)
    q.workers["a"].current_job_id = "other"
    lease, why = lend(q)
    assert lease is None and "is busy with another step" in why


def test_a_card_with_work_waiting_is_not_lent(settings):
    q = make_queue(settings)
    queued_job("a")
    lease, why = lend(q)
    assert lease is None and "has work waiting" in why


def test_with_sharing_on_work_waiting_elsewhere_goes_first(settings):
    q = make_queue(settings)
    queued_job("b", "concept.run")          # waits behind the step on b; a would take it over with sharing on
    q.share_when_busy = False
    assert lend(q)[0] is not None
    q.end_leases("job1")
    q.share_when_busy = True
    lease, why = lend(q)
    assert lease is None and "work is waiting that" in why
    # work a may not take (rigging) does not hold it back
    with session_factory()() as db:
        for j in db.query(Job).all():
            j.kind = "rig.run"
        db.commit()
    assert lend(q)[0] is not None


@pytest.mark.parametrize("client, words", [
    (FakeClient("a", free_gb=3.0), "3.0 GB of memory for drawing, 7 GB are needed"),
    (FakeClient("a", up=False), "is not running"),
])
def test_low_memory_or_a_stopped_engine(settings, client, words):
    q = make_queue(settings, a=client)
    lease, why = lend(q)
    assert lease is None and words in why and q.workers["a"].lent_to is None


def test_memory_held_by_the_engines_own_models_counts_as_room(settings):
    # 1.5 GB free, but the engine itself holds 7 GB (the drawing model from the last step): it can draw
    q = make_queue(settings, a=FakeClient("a", free_gb=1.5, held_gb=7.0))
    assert lend(q)[0] is not None


def test_cards_that_are_off_paused_or_not_allowed_pictures(settings):
    q = make_queue(settings)
    q.disabled.add("a")
    assert "is turned off" in lend(q)[1]
    q.disabled.clear()
    q.workers["a"].paused = True
    assert "is paused" in lend(q)[1]
    q.workers["a"].paused = False
    q.allowed_kinds["a"] = {"model"}
    assert "not allowed to draw pictures" in lend(q)[1]


def test_the_card_frees_up_only_after_the_memory_answer(settings):
    """The engine's answer takes a moment; a job that lands on the card meanwhile wins and the card is not lent."""
    q = make_queue(settings)
    slow = q.clients["a"]
    real = slow.cached_vram_summary

    async def answer_late(max_age=5.0):
        queued_job("a")                       # someone queued work on it while the queue waited for the answer
        return await real(max_age)

    slow.cached_vram_summary = answer_late
    lease, why = lend(q)
    assert lease is None and "has work waiting" in why and q.workers["a"].lent_to is None


def test_turning_the_lent_card_off_takes_it_back(settings, monkeypatch):
    q = make_queue(settings)
    monkeypatch.setattr(q, "_save_prefs", lambda: None)
    monkeypatch.setattr(q, "_release", lambda instance: None)
    lease, _ = lend(q)
    q.configure("a", enabled=False)
    assert lease.revoked and "was turned off" in lease.revoked_reason
    q2 = make_queue(settings)
    monkeypatch.setattr(q2, "_save_prefs", lambda: None)
    lease2, _ = lend(q2)
    q2.configure("a", allowed_kinds=["model", "rigging", "review"])
    assert lease2.revoked and "no longer allowed to draw pictures" in lease2.revoked_reason


def test_the_lent_cards_engine_is_not_stopped_while_lent(settings):
    q = make_queue(settings)
    lend(q)
    st = q.status()
    assert st["a"]["lent_to"] == "job1"
    # the idle stop sees it as busy, the Stop button refuses it
    with pytest.raises(ValueError, match="busy with a step"):
        q.supervisor = type("S", (), {"owned": lambda self, n: True})()
        q.stop_engine("a")


def test_a_lent_cards_worker_waits_and_runs_its_work_after_the_card_comes_back(settings, monkeypatch):
    ran: list[str] = []

    async def execute(ctx, kind, payload):
        ran.append(ctx.instance)

    monkeypatch.setattr(stages, "execute", execute)
    q = make_queue(settings)

    async def go():
        lease, _ = await q.lend("b", "job1", KIND)
        w = q.workers["a"]
        w.start()
        try:
            jid = queued_job("a")
            q.wake("a")
            await asyncio.sleep(0.5)
            assert ran == []              # lent: the waiting job does not start
            await lease.end()             # handed back: the worker is woken and runs it
            for _ in range(100):
                if ran:
                    break
                await asyncio.sleep(0.05)
            with session_factory()() as db:
                return ran, db.get(Job, jid).status
        finally:
            await w.stop()

    ran_on, status = asyncio.run(go())
    assert ran_on == ["a"] and status == "done"


def test_a_job_that_ends_hands_back_what_it_still_holds(settings, monkeypatch):
    """The safety net: the worker hands back every card lent to a job when the job ends, whatever the step did."""
    async def execute(ctx, kind, payload):
        lease, _ = await ctx.borrow()
        assert lease is not None and lease.instance == "a"
        raise RuntimeError("the step failed without handing the card back")

    monkeypatch.setattr(stages, "execute", execute)
    q = make_queue(settings)
    jid = queued_job("b", KIND)
    asyncio.run(q.workers["b"]._execute(jid))
    assert q.leases == {} and q.workers["a"].lent_to is None
    with session_factory()() as db:
        assert db.get(Job, jid).status == "error"
