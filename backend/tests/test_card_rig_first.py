"""A queued rig job (1 to 2 minutes) goes ahead of other assets' long jobs on its card (round 4, 2026-11-13: rigs waited
behind 8 to 12 minute 3D builds on the 3080). Never ahead of its own asset's unfinished earlier jobs, never interrupting
a running job, and at most RIG_PASS_LIMIT rigs ahead of one waiting job."""

from __future__ import annotations

import asyncio

from vgac import jobs, stages
from vgac.db import Asset, Job, Project, session_factory
from test_host_lanes import add_job, make_assets, status_of, until


def pick(instance: str = "b"):
    w = jobs.InstanceWorker(name=instance, settings=None, clients={})
    with session_factory()() as db:
        rows = db.execute(jobs.select(Job.id, Job.kind, Job.asset_id, Job.created_at)
                          .where(Job.instance == instance, Job.status == "queued").order_by(Job.created_at, Job.id)).all()
        return jobs._card_pick(db, rows, w._passed)[0]


def test_a_rig_goes_ahead_of_another_assets_build(settings):
    a, b = make_assets()
    mesh = add_job("mesh.run", a, instance="b")
    rig = add_job("rig.run", b, instance="b")
    assert pick() == rig
    assert mesh != rig


def test_first_come_first_served_otherwise(settings):
    a, b = make_assets()
    m1 = add_job("mesh.run", a, instance="b")
    m2 = add_job("turnaround.run", b, instance="b")
    assert pick() == m1
    r = add_job("rig.run", b, instance="b")  # b's own turnaround is queued before it: blocked
    assert pick() == m1
    assert r != m1 and m2 != m1


def test_a_rig_waits_for_its_own_assets_earlier_jobs_anywhere(settings):
    a, b = make_assets()
    add_job("mesh.run", b, instance="a", status="running")   # b's build runs on the other card
    mesh = add_job("mesh.run", a, instance="b")
    rig = add_job("rig.run", b, instance="b")
    assert pick() == mesh                                      # rig of b waits for b's running build
    with session_factory()() as db:
        for j in db.query(Job).filter(Job.instance == "a"):
            j.status = "done"
        db.commit()
    assert pick() == rig


def test_a_rig_at_the_head_is_just_the_head(settings):
    a, b = make_assets()
    r1 = add_job("rig.run", a, instance="b")
    add_job("mesh.run", b, instance="b")
    assert pick() == r1


def test_rig_jobs_go_ahead_in_their_own_order_and_other_cards_are_not_touched(settings):
    a, b = make_assets()
    mesh = add_job("mesh.run", a, instance="b")
    r1 = add_job("rig.run", b, instance="b")
    add_job("mesh.run", b, instance="a")
    assert pick("b") == r1
    assert pick("a") is not None and pick("a") != r1


def run_card(settings, monkeypatch, scenario, gate_kind=None):
    log: list[str] = []
    gates: dict[str, asyncio.Event] = {}

    async def execute(ctx, kind, payload):
        log.append(ctx.job_id)
        if kind.startswith(gate_kind or "never."):
            await gates.setdefault(ctx.job_id, asyncio.Event()).wait()
        await asyncio.sleep(0.02)

    monkeypatch.setattr(stages, "execute", execute)

    async def main():
        w = jobs.InstanceWorker(name="b", settings=settings, clients={})
        w.start()
        try:
            await scenario(w, gates, log)
        finally:
            await w.stop()

    asyncio.run(main())
    return log


def test_at_most_two_rigs_pass_one_waiting_build_and_nothing_runs_twice(settings, monkeypatch):
    a, b, c, d = make_assets(4)
    mesh = add_job("mesh.run", a, instance="b")
    rigs = [add_job("rig.run", x, instance="b") for x in (b, c, d)]

    async def scenario(w, gates, log):
        w.wake.set()
        await until(lambda: all(status_of(j) == "done" for j in [mesh] + rigs))

    log = run_card(settings, monkeypatch, scenario)
    assert log == [rigs[0], rigs[1], mesh, rigs[2]]           # two rigs, then the build, then the third rig
    assert len(log) == len(set(log))


def test_a_running_build_is_never_interrupted(settings, monkeypatch):
    a, b = make_assets()
    mesh = add_job("mesh.run", a, instance="b")

    async def scenario(w, gates, log):
        w.wake.set()
        await until(lambda: status_of(mesh) == "running")
        rig = add_job("rig.run", b, instance="b")
        w.wake.set()
        await asyncio.sleep(0.6)
        assert status_of(mesh) == "running" and status_of(rig) == "queued"
        gates.setdefault(mesh, asyncio.Event()).set()
        await until(lambda: status_of(rig) == "done")
        assert log == [mesh, rig]

    run_card(settings, monkeypatch, scenario, gate_kind="mesh.")
