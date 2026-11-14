"""Lending a card to the host lane (item 20b): the clean-up job runs on no card, and its face pass borrows one to draw a
second close-up at the same time (stages/face_detail.borrow_card). Same guardrails as step 2 (test_card_lending.py):
the memory audit, nothing waiting on the card, on and allowed to draw pictures; plus the host names the card its first
close-up is drawn on (`avoid`) and borrows nothing while a step 2 waits. No second card free: (None, why), one after
another, as before. Queue built by hand, no worker loops and no ComfyUI."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from vgac import jobs, stages
from vgac.db import Job, session_factory
from vgac.stages import face_detail as fd
from test_card_lending import FakeClient, queued_job

PICTURES = fd.LEND_KIND
HOST = jobs.HOST


def make_queue(settings, **clients: FakeClient) -> jobs.JobQueue:
    """As in the app: every worker, the host's too, may borrow."""
    q = jobs.JobQueue(settings)
    for name in settings.instances:
        q.clients[name] = clients.get(name) or FakeClient(name)
    for name in list(settings.instances) + [HOST]:
        gpu = name in settings.instances
        q.workers[name] = jobs.InstanceWorker(
            name=name, settings=settings, clients=q.clients,
            allows=(lambda kind, n=name: q.kind_allowed_here(n, kind)) if gpu else None,
            share=(lambda n=name: q.take_shared(n)) if gpu else None,
            lend=q.lend, after_job=q.end_leases)
    return q


def host_lend(q, avoid="b", kind=PICTURES, job="prep1"):
    return asyncio.run(q.lend(HOST, job, kind, avoid))


def test_the_host_borrows_the_card_it_is_not_drawing_on(settings):
    q = make_queue(settings)
    lease, why = host_lend(q, avoid="b")
    assert lease is not None and why == "" and lease.instance == "a" and lease.job_id == "prep1"
    assert q.workers["a"].lent_to == "prep1" and q._card_takes("a") is False
    asyncio.run(lease.end())
    assert q.workers["a"].lent_to is None and q.leases == {}


def test_the_card_to_avoid_is_never_lent(settings):
    q = make_queue(settings)
    q.workers["b"].current_job_id = "x"            # not that it matters: b is avoided whatever its state
    lease, _ = host_lend(q, avoid="a")
    assert lease is None and q.workers["a"].lent_to is None


def test_one_card_lends_nothing_and_says_so(settings, monkeypatch):
    monkeypatch.delitem(settings.instances, "a")
    q = make_queue(settings)
    lease, why = host_lend(q, avoid="b")
    assert lease is None and why == "this computer has one graphics card"
    assert q.leases == {} and q.workers["b"].lent_to is None


def test_a_card_that_is_off_busy_low_on_memory_or_with_work_waiting_is_not_lent(settings):
    q = make_queue(settings)
    q.disabled.add("a")
    assert "is turned off" in host_lend(q)[1]
    q.disabled.clear()
    q.workers["a"].current_job_id = "other"
    assert "busy with another step" in host_lend(q)[1]
    q.workers["a"].current_job_id = None
    jid = queued_job("a", "concept.run")
    assert "has work waiting" in host_lend(q)[1]
    with session_factory()() as db:
        db.delete(db.get(Job, jid))
        db.commit()
    q.clients["a"] = FakeClient("a", free_gb=3.0)
    lease, why = host_lend(q)
    assert lease is None and "3.0 GB of memory for drawing, 7 GB are needed" in why
    assert q.leases == {} and q.workers["a"].lent_to is None


def test_a_card_not_allowed_pictures_is_not_lent_and_the_hosts_own_kind_is_never_enough(settings):
    q = make_queue(settings)
    q.allowed_kinds["a"] = {"model"}
    assert "not allowed to draw pictures" in host_lend(q)[1]
    q.allowed_kinds["a"] = set(jobs.KIND_GROUPS)
    # a host step that asks without naming the work borrows by its own kind (clean-up), which no card runs
    lease, why = host_lend(q, kind="prep.run")
    assert lease is None and "not allowed to draw pictures" in why


def test_a_waiting_step_2_goes_first(settings):
    q = make_queue(settings)
    queued_job("b", "turnaround.run")          # waits behind work on b; it will want the second card when it starts
    q.share_when_busy = False                   # so the card's own "work waiting it would take over" check is quiet
    lease, why = host_lend(q)
    assert lease is None and "side views of another character are waiting" in why
    # a card-run step is not held back by this rule (its own rules are test_card_lending.py's)
    lease2, _ = asyncio.run(q.lend("b", "job2", PICTURES))
    assert lease2 is not None


def test_turning_the_lent_card_off_takes_it_back_from_the_host(settings, monkeypatch):
    q = make_queue(settings)
    monkeypatch.setattr(q, "_save_prefs", lambda: None)
    monkeypatch.setattr(q, "_release", lambda instance: None)
    lease, _ = host_lend(q)
    q.configure("a", enabled=False)
    assert lease.revoked and "was turned off" in lease.revoked_reason


def _run_host_job(q, execute, monkeypatch):
    monkeypatch.setattr(stages, "execute", execute)
    jid = queued_job(HOST, "prep.run")
    asyncio.run(q.workers[HOST]._execute(jid))
    with session_factory()() as db:
        return db.get(Job, jid).status


def test_the_face_pass_borrows_through_the_host_worker_and_the_job_end_hands_it_back(settings, monkeypatch):
    seen = {}

    async def execute(ctx, kind, payload):
        lease, why = await fd.borrow_card(SimpleNamespace(ctx=ctx), "b")
        seen["lease"], seen["why"] = lease, why
        seen["lent_to"] = q.workers["a"].lent_to
        raise RuntimeError("the clean-up failed before handing the card back")

    q = make_queue(settings)
    status = _run_host_job(q, execute, monkeypatch)
    assert seen["lease"] is not None and seen["lease"].instance == "a" and seen["why"] == ""
    assert seen["lent_to"] == seen["lease"].job_id
    assert status == "error" and q.leases == {} and q.workers["a"].lent_to is None


def test_a_stopped_clean_up_hands_the_card_back(settings, monkeypatch):
    async def execute(ctx, kind, payload):
        lease, _ = await ctx.borrow(kind=PICTURES, avoid="b")
        assert lease is not None
        raise jobs.JobCancelled()

    q = make_queue(settings)
    status = _run_host_job(q, execute, monkeypatch)
    assert status == "cancelled"
    assert q.leases == {} and q.workers["a"].lent_to is None


def test_no_second_card_free_is_todays_one_after_another(settings, monkeypatch):
    """The other card busy: the face pass gets (None, why), nothing is marked lent, nothing changes on the cards."""
    seen = {}

    async def execute(ctx, kind, payload):
        seen["got"] = await fd.borrow_card(SimpleNamespace(ctx=ctx), "b")

    q = make_queue(settings)
    q.workers["a"].current_job_id = "someone else's step"
    status = _run_host_job(q, execute, monkeypatch)
    lease, why = seen["got"]
    assert lease is None and "busy with another step" in why
    assert status == "done" and q.leases == {} and q.workers["a"].lent_to is None
    assert q.workers["a"].current_job_id == "someone else's step"


def test_step_2_still_borrows_with_no_arguments(settings, monkeypatch):
    """The card-run step 2 calls ctx.borrow() bare: its own kind, nothing avoided, as before."""
    seen = {}

    async def execute(ctx, kind, payload):
        lease, _ = await ctx.borrow()
        seen["instance"] = lease.instance if lease else None
        await lease.end()

    monkeypatch.setattr(stages, "execute", execute)
    q = make_queue(settings)
    jid = queued_job("b", "turnaround.run")
    asyncio.run(q.workers["b"]._execute(jid))
    assert seen["instance"] == "a" and q.leases == {}
