"""The host lane (jobs._host_next): first come, first served, but an export goes ahead of the clean-ups waiting before
it, unless a job of its own asset that was created earlier is still queued or running (its clean-up, its rig)."""

from __future__ import annotations

from datetime import timedelta

from vgac.db import Job, session_factory, utcnow
from vgac import jobs

T0 = utcnow()


def add(kind, asset, n, instance="host", status="queued"):
    with session_factory()() as db:
        j = Job(instance=instance, kind=kind, asset_id=asset, status=status, payload_json="{}", message="",
                created_at=T0 + timedelta(seconds=n))
        db.add(j)
        db.commit()
        return j.id


def nxt():
    with session_factory()() as db:
        return jobs._host_next(db)


def test_an_export_overtakes_other_assets_cleanups(settings):
    c1 = add("prep.run", "A1", 1)
    c2 = add("prep.run", "A2", 2)
    e = add("export.run", "A3", 3)
    assert nxt() == e
    # without it, first come first served
    with session_factory()() as db:
        db.get(Job, e).status = "done"
        db.commit()
    assert nxt() == c1 and c1 != c2


def test_an_export_does_not_overtake_its_own_cleanup_or_rig(settings):
    c = add("prep.run", "A1", 1)
    e = add("export.run", "A1", 2)
    assert nxt() == c                      # its clean-up is queued first
    with session_factory()() as db:
        db.get(Job, c).status = "done"
        db.commit()
    r = add("rig.run", "A1", 1.5, instance="b", status="running")   # created before the export: it depends on it
    assert nxt() is None or nxt() != e
    with session_factory()() as db:
        db.get(Job, r).status = "done"
        db.commit()
    assert nxt() == e


def test_exports_keep_their_order_and_the_rest_stays_first_come_first_served(settings):
    a = add("prep.run", "A1", 1)
    e1 = add("export.run", "A2", 2)
    e2 = add("export.adjust", "A3", 3)
    assert nxt() == e1
    with session_factory()() as db:
        db.get(Job, e1).status = "done"
        db.commit()
    assert nxt() == e2
    with session_factory()() as db:
        db.get(Job, e2).status = "done"
        db.commit()
    assert nxt() == a
    assert add("prep.run", "A9", 9) and nxt() == a


def test_an_export_may_overtake_a_later_job_of_its_own_asset(settings):
    e = add("export.run", "A1", 1)
    add("prep.adjust", "A1", 2)            # asked for after the export: not something it depends on
    add("prep.run", "A2", 0)
    assert nxt() == e


def test_empty(settings):
    assert nxt() is None
