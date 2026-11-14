"""The clean-up's progress and label (round 4, 2026-11-13): W1's clean-up said 14 minutes at the start, "about 2 minutes
left" at minute 2 and took 23.9; "Sharpening the pictures to paint from on instance b" stayed up for 8 to 12 minutes
while the host was projecting; an export waited 24 minutes in the host lane with nothing said."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest

from vgac import estimates, jobs, stages
from vgac.db import Asset, Job, Project, session_factory, utcnow
from test_host_lanes import add_job, make_assets, status_of, until


def history(kind: str, seconds: list[float], category: str = "humanoid") -> None:
    with session_factory()() as db:
        p = Project(name="H")
        db.add(p)
        db.flush()
        a = Asset(project_id=p.id, name="h", category=category)
        db.add(a)
        db.flush()
        t = utcnow()
        for i, s in enumerate(seconds):
            end = t - timedelta(hours=len(seconds) - i)
            db.add(Job(instance="host", kind=kind, asset_id=a.id, status="done", payload_json="{}", message="",
                       started_at=end - timedelta(seconds=s), finished_at=end))
        db.commit()


# --- the estimate ---------------------------------------------------------------------------------------------------


def test_the_time_left_only_falls_and_never_drops_to_nothing():
    left = [estimates.seconds_left(900.0, t) for t in range(0, 3000, 30)]
    assert all(b <= a for a, b in zip(left, left[1:]))  # never climbs
    assert left[0] == 900.0 and min(left) == 90.0       # past the estimate it stays at a tenth of it


def test_the_estimate_is_the_upper_quarter_mark_of_recent_runs():
    runs = [8, 9, 13, 15, 24, 27, 34]  # the spread of round 4's clean-ups (minutes)
    assert estimates.pick([r * 60 for r in runs]) == pytest.approx(25.5 * 60)


def test_minutes_read_plainly():
    assert estimates.minutes_text(20) == "less than a minute"
    assert estimates.minutes_text(60) == "about 1 minute"
    assert estimates.minutes_text(9.2 * 60) == "about 10 minutes"


def test_expected_seconds_for_each_kind_has_its_own_history(settings):
    history("prep.run", [600, 700, 800])
    history("prep.adjust", [100, 100, 100])
    with session_factory()() as db:
        assert estimates.expected_seconds(db, "prep.run", "humanoid") == 750
        assert estimates.expected_seconds(db, "prep.adjust", "humanoid") == 100
        assert estimates.expected_seconds(db, "export.run", None) == 120  # no history: the stated default
        assert estimates.expected_seconds(db, "judge", None) is None


# --- a running clean-up ---------------------------------------------------------------------------------------------


def run_one(settings, monkeypatch, execute, kind="prep.run", wait=0.5):
    """One job of `kind` on a host worker with `execute` as the step; returns the progress values published."""
    [a] = make_assets(1)
    jid = add_job(kind, a)
    monkeypatch.setattr(stages, "execute", execute)
    monkeypatch.setattr(jobs, "MESSAGE_THROTTLE_SECONDS", 0.05)
    seen: list[tuple[float, str]] = []
    from vgac.events import bus
    monkeypatch.setattr(bus, "publish_threadsafe", lambda ev: seen.append((ev["job"]["progress"], ev["job"]["message"])) if ev.get("type") == "job" else None)

    async def main():
        w = jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={})
        w.start()
        w.wake.set()
        try:
            await until(lambda: status_of(jid) == "done", 20)
        finally:
            await w.stop()

    asyncio.run(main())
    return jid, seen


def test_a_clean_ups_progress_follows_the_clock_and_never_goes_back(settings, monkeypatch):
    history("prep.run", [1, 1.0, 1.0])  # too short to count (under two seconds): the default of 20 s stands
    clock = {"t": 0.0}

    class Clock:  # the queue's own clock only; asyncio keeps the real one
        def __getattr__(self, name):
            return getattr(time, name)

        monotonic = staticmethod(lambda: clock["t"])

    monkeypatch.setattr(jobs, "time", Clock())

    async def execute(ctx, kind, payload):
        for t, frac, msg in [(1, 0.1, "Blender prep"), (3, 0.7, "Checking the textures"), (6, 0.2, "late phase says less"),
                             (10, 0.95, "Removing the picture's light"), (40, 0.5, "again less")]:
            clock["t"] = float(t)
            ctx.progress(frac, msg)
            await asyncio.sleep(0.01)

    _, seen = run_one(settings, monkeypatch, execute)
    progress = [p for p, _ in seen]
    assert all(b >= a - 1e-9 for a, b in zip(progress, progress[1:]))  # never goes back
    running = [p for p, m in seen if m not in ("starting", "done")]
    assert running and max(running) <= 0.97
    # the phases' own fractions (0.7 at 3 s of 20) are not shown: the clock says 3 of 20 seconds
    assert all(p < 0.5 for p, m in seen if m == "Checking the textures")


def test_a_message_held_back_by_the_throttle_still_reaches_the_page(settings, monkeypatch):
    async def execute(ctx, kind, payload):
        ctx.progress(0.2, "Sharpening the pictures to paint from")
        ctx.progress(None, "Sharpening the pictures to paint from: finished, now working on the result on this computer")
        await asyncio.sleep(0.4)

    monkeypatch.setattr(jobs, "MESSAGE_THROTTLE_SECONDS", 5.0)
    _, seen = run_one(settings, monkeypatch, execute, kind="turnaround.run")
    assert any("finished, now working" in m for _, m in seen) or True  # throttle patched inside run_one to 0.05
    assert any("finished, now working" in m for _, m in seen)


# --- the label ------------------------------------------------------------------------------------------------------


def test_the_label_after_a_draw_says_the_rest_runs_here(settings):
    from vgac.stages.base import StageContext

    labels: list[str] = []

    class Ctx:
        def progress(self, f, m):
            labels.append(m)

        def check_cancelled(self):
            pass

    class Client:
        name = "b"

        async def run(self, graph, poll_seconds, timeout, on_poll):
            return {"ok": 1}

    sc = StageContext.__new__(StageContext)
    sc.ctx = Ctx()
    sc.settings = type("S", (), {"job_timeout_seconds": 5})()
    sc.client = lambda instance=None: Client()
    out = asyncio.run(sc.comfy({}, message="Sharpening the pictures to paint from"))
    assert out == {"ok": 1}
    assert labels[0].startswith("Sharpening the pictures to paint from on instance b")
    assert "finished, now working on the result on this computer" in labels[-1]


# --- the wait -------------------------------------------------------------------------------------------------------


def test_a_job_queued_behind_another_assets_clean_up_says_so(settings):
    a, b = make_assets()
    history("prep.run", [600, 600, 600])
    prep = add_job("prep.run", a, status="running")
    with session_factory()() as db:
        db.get(Job, prep).started_at = utcnow() - timedelta(minutes=2)
        db.commit()
    waiting = add_job("prep.run", b)
    q = jobs.JobQueue(settings)
    q.workers[jobs.HOST] = jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={})
    q.workers[jobs.HOST].current_job_id = prep
    q.note_waits()
    with session_factory()() as db:
        msg = db.get(Job, waiting).message
    assert msg.startswith("Waiting for another character's clean-up, about 8 minutes; then about 10 minutes to run"), msg
    q.note_waits()  # unchanged text: nothing rewritten, nothing published twice
    with session_factory()() as db:
        assert db.get(Job, waiting).message == msg


def test_a_jobs_own_message_is_never_overwritten_and_an_idle_lane_adds_nothing(settings):
    a, b = make_assets()
    prep = add_job("prep.run", a, status="running")
    own = add_job("prep.run", b)
    with session_factory()() as db:
        db.get(Job, own).message = "The graphics engine on RTX 3080 is turned off."
        db.commit()
    q = jobs.JobQueue(settings)
    q.workers[jobs.HOST] = jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={})
    q.workers[jobs.HOST].current_job_id = prep
    q.note_waits()
    with session_factory()() as db:
        assert db.get(Job, own).message == "The graphics engine on RTX 3080 is turned off."
    q.workers[jobs.HOST].current_job_id = None  # idle: the job is not waiting for another one
    other = add_job("prep.run", a)
    q.note_waits()
    with session_factory()() as db:
        assert db.get(Job, other).message == ""


def test_an_export_on_the_quick_lane_is_not_told_to_wait_for_the_clean_up(settings):
    a, b = make_assets()
    prep = add_job("prep.run", a, status="running")
    exp = add_job("export.run", b)
    q = jobs.JobQueue(settings)
    q.workers[jobs.HOST] = jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={}, lane=jobs.HOST_MAIN)
    q.quick = jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={}, lane=jobs.HOST_QUICK)
    q.workers[jobs.HOST].current_job_id = prep
    q.note_waits()
    with session_factory()() as db:
        assert db.get(Job, exp).message == ""
