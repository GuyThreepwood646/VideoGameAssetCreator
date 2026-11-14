"""The host's two workers: clean-up and the like on the main one, exports on a second, so a short job does not wait
behind another asset's clean-up. Measured in round 4 (2026-11-13): W1's export (2.9 minutes of work) waited 24.3
minutes behind M1's 26.4-minute clean-up, with only two assets live."""

from __future__ import annotations

import asyncio

from vgac import jobs, stages
from vgac.db import Asset, Job, Project, session_factory


def make_assets(n: int = 2) -> list[str]:
    with session_factory()() as db:
        p = Project(name="P")
        db.add(p)
        db.flush()
        out = []
        for i in range(n):
            a = Asset(project_id=p.id, name=f"asset{i}", category="humanoid")
            db.add(a)
            db.flush()
            out.append(a.id)
        db.commit()
        return out


def add_job(kind: str, asset_id: str, instance: str = "host", status: str = "queued") -> str:
    with session_factory()() as db:
        j = Job(instance=instance, kind=kind, asset_id=asset_id, status=status, payload_json="{}", message="")
        db.add(j)
        db.commit()
        return j.id


def status_of(job_id: str) -> str:
    with session_factory()() as db:
        return db.get(Job, job_id).status


def picks(lane: str) -> str | None:
    with session_factory()() as db:
        return jobs._host_next(db, lane)


# --- who picks what ------------------------------------------------------------------------------------------------


def test_the_quick_lane_takes_exports_only_and_the_main_lane_everything_else(settings):
    a, b = make_assets()
    prep = add_job("prep.run", a)
    exp = add_job("export.run", b)
    assert picks(jobs.HOST_QUICK) == exp
    assert picks(jobs.HOST_MAIN) == prep
    # without a quick worker (lane "") one worker takes everything, an export first
    assert picks("") == exp


def test_neither_lane_picks_a_job_the_other_takes(settings):
    a, b = make_assets()
    add_job("prep.run", a)
    add_job("prep.adjust", b)
    assert picks(jobs.HOST_QUICK) is None
    only_exports = add_job("export.run", a)  # blocked by a's queued prep (created before it)
    assert picks(jobs.HOST_QUICK) is None
    assert picks(jobs.HOST_MAIN) not in (None, only_exports)


def test_an_export_still_waits_for_its_own_assets_earlier_jobs(settings):
    a, b = make_assets()
    add_job("prep.run", a, status="running")
    mine = add_job("export.run", a)
    other = add_job("export.run", b)
    assert picks(jobs.HOST_QUICK) == other  # a's export is blocked by a's clean-up, b's is free
    with session_factory()() as db:
        db.get(Job, other).status = "done"
        db.commit()
    assert picks(jobs.HOST_QUICK) is None
    with session_factory()() as db:
        j = db.scalar(__import__("sqlalchemy").select(Job).where(Job.kind == "prep.run"))
        j.status = "done"
        db.commit()
    assert picks(jobs.HOST_QUICK) == mine


def test_exports_among_themselves_stay_in_order(settings):
    a, b = make_assets()
    first = add_job("export.run", a)
    add_job("export.run", b)
    assert picks(jobs.HOST_QUICK) == first


# --- running ------------------------------------------------------------------------------------------------------


def run_lanes(settings, monkeypatch, scenario):
    """Both host workers on a fake step runner; `scenario(q, gate, log)` drives it."""
    log: list[tuple[str, str]] = []
    gates: dict[str, asyncio.Event] = {}

    async def execute(ctx, kind, payload):
        log.append(("start", ctx.job_id))
        ev = gates.setdefault(ctx.job_id, asyncio.Event())
        if kind.startswith("prep."):
            await ev.wait()
        await asyncio.sleep(0.05)
        log.append(("end", ctx.job_id))

    monkeypatch.setattr(stages, "execute", execute)

    async def main():
        q = jobs.JobQueue(settings)
        main_w = jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={}, lane=jobs.HOST_MAIN, after_job=q.end_leases)
        quick = jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={}, lane=jobs.HOST_QUICK, after_job=q.end_leases)
        q.workers[jobs.HOST] = main_w
        q.quick = quick
        main_w.start()
        quick.start()
        try:
            await scenario(q, gates, log)
        finally:
            await main_w.stop()
            await quick.stop()

    asyncio.run(main())
    return log


async def until(pred, seconds=15.0):
    t = 0.0
    while not pred() and t < seconds:
        await asyncio.sleep(0.05)
        t += 0.05
    assert pred(), "timed out"


def test_an_export_runs_while_another_assets_clean_up_is_running(settings, monkeypatch):
    a, b = make_assets()
    prep = add_job("prep.run", a)
    exp = add_job("export.run", b)

    async def scenario(q, gates, log):
        q.workers[jobs.HOST].wake.set()
        q.quick.wake.set()
        await until(lambda: status_of(exp) == "done")
        assert status_of(prep) == "running"  # the clean-up is still going
        assert q.status()[jobs.HOST]["current_job_id"] == prep
        gates.setdefault(prep, asyncio.Event()).set()
        await until(lambda: status_of(prep) == "done")

    log = run_lanes(settings, monkeypatch, scenario)
    assert [e for e in log if e[0] == "start"].count(("start", exp)) == 1 and [e for e in log if e[0] == "start"].count(("start", prep)) == 1


def test_two_exports_never_run_at_once_and_nothing_runs_twice(settings, monkeypatch):
    a, b = make_assets()
    e1 = add_job("export.run", a)
    e2 = add_job("export.run", b)
    p1 = add_job("prep.run", a)  # created after a's export, so the export is not held by it
    p2 = add_job("prep.run", b)

    async def scenario(q, gates, log):
        q.workers[jobs.HOST].wake.set()
        q.quick.wake.set()
        await until(lambda: status_of(e1) == "done" and status_of(e2) == "done")
        # the clean-ups: one at a time on the main worker
        await until(lambda: status_of(p1) == "running" or status_of(p2) == "running")
        assert {status_of(p1), status_of(p2)} == {"running", "queued"}
        for p in (p1, p2):
            gates.setdefault(p, asyncio.Event()).set()
        await until(lambda: status_of(p1) == "done" and status_of(p2) == "done")

    log = run_lanes(settings, monkeypatch, scenario)
    starts = [j for k, j in log if k == "start"]
    assert sorted(starts) == sorted([e1, e2, p1, p2]) and len(starts) == len(set(starts))
    # the two exports did not overlap
    open_ = set()
    for kind, j in log:
        if kind == "start" and j in (e1, e2):
            assert not (open_ & {e1, e2})
            open_.add(j)
        elif kind == "end":
            open_.discard(j)


def test_stop_and_pause_reach_the_quick_worker_too(settings, monkeypatch):
    a, b = make_assets()
    exp = add_job("export.run", b)

    async def scenario(q, gates, log):
        q.set_paused(jobs.HOST, True)
        q.quick.wake.set()
        await asyncio.sleep(0.5)
        assert status_of(exp) == "queued"
        q.set_paused(jobs.HOST, False)
        await until(lambda: status_of(exp) == "done")

    run_lanes(settings, monkeypatch, scenario)


# --- more than one clean-up at a time (host_cleanup_workers above 1, off by default) -------------------------------------


def test_the_default_is_one_clean_up_at_a_time(settings):
    assert settings.host_cleanup_workers == 1


def test_a_second_clean_up_worker_takes_another_assets_clean_up_but_never_the_same_assets(settings, monkeypatch):
    a, b = make_assets()
    p1 = add_job("prep.run", a)
    p1b = add_job("prep.adjust", a)  # the same asset's next job
    p2 = add_job("prep.run", b)
    log: list[tuple[str, str]] = []
    gates: dict[str, asyncio.Event] = {}

    async def execute(ctx, kind, payload):
        log.append(("start", ctx.job_id))
        await gates.setdefault(ctx.job_id, asyncio.Event()).wait()
        log.append(("end", ctx.job_id))

    monkeypatch.setattr(stages, "execute", execute)

    async def main():
        ws = [jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={}, lane=jobs.HOST_MAIN) for _ in range(2)]
        for w in ws:
            w.start()
            w.wake.set()
        try:
            await until(lambda: status_of(p1) == "running" and status_of(p2) == "running")
            await asyncio.sleep(0.5)
            assert status_of(p1b) == "queued"  # a's own next job waits for a's running one
            gates.setdefault(p1, asyncio.Event()).set()
            await until(lambda: status_of(p1b) == "running")
            for j in (p2, p1b):
                gates.setdefault(j, asyncio.Event()).set()
            await until(lambda: all(status_of(j) == "done" for j in (p1, p1b, p2)))
        finally:
            for w in ws:
                await w.stop()

    asyncio.run(main())
    starts = [j for k, j in log if k == "start"]
    assert sorted(starts) == sorted([p1, p1b, p2])  # each exactly once
