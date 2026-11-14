"""Time estimates from stored run durations (vgac.estimates) and what a card says it is doing. Measured 2026-11-14:
the clean-up step ("prep") was shown as "about 20 seconds" while its finished runs took a median of 409 seconds."""

from __future__ import annotations

from datetime import timedelta

from vgac import estimates, jobs
from vgac.db import Asset, Job, Project, session_factory, utcnow
from test_card_lending import make_queue
from test_gpus import client, fake_execute  # noqa: F401


def add_runs(kind: str, seconds: list[float], category: str = "humanoid", status: str = "done") -> None:
    with session_factory()() as db:
        p = Project(name="P")
        db.add(p)
        db.flush()
        a = Asset(project_id=p.id, name="x", category=category)
        db.add(a)
        db.flush()
        t = utcnow()
        for i, s in enumerate(seconds):
            end = t - timedelta(minutes=len(seconds) - i)
            db.add(Job(instance="host", kind=kind, asset_id=a.id, status=status, payload_json="{}", message="",
                       started_at=end - timedelta(seconds=s), finished_at=end))
        db.commit()


def stage(client, sid="prep"):
    return next(s for s in client.get("/api/stages").json() if s["id"] == sid)


def test_no_history_keeps_the_stated_default(client):
    s = stage(client)
    assert s["estimate_seconds"] == 20 and s["estimate_basis"] == "default" and s["estimate_runs"] == 0


def test_the_upper_quarter_mark_of_finished_runs_replaces_the_default(client):
    add_runs("prep.run", [300, 500, 900])
    s = stage(client)
    assert s["estimate_seconds"] == 700 and s["estimate_basis"] == "history" and s["estimate_runs"] == 3


def test_too_few_runs_failed_runs_and_instant_passes_do_not_count(client):
    add_runs("prep.run", [400, 600])
    add_runs("prep.run", [500, 500, 500], status="error")
    add_runs("prep.run", [0.2, 0.3, 0.1])
    assert stage(client)["estimate_basis"] == "default"


def test_only_the_last_runs_count(client):
    add_runs("prep.run", [900] * 20 + [100] * 15)
    assert stage(client)["estimate_seconds"] == 100


def test_the_assets_category_is_preferred_when_it_has_enough_runs(client):
    add_runs("prep.run", [100, 100, 100], category="prop")
    add_runs("prep.run", [800, 800, 800], category="humanoid")
    with session_factory()() as db:
        assert estimates.stage_seconds(db, "prep", "prop") == (100, 3)
        assert estimates.stage_seconds(db, "prep", "creature") == (800, 6)  # falls back to every category


def test_suggest_uses_history_but_keeps_the_high_platform_build_and_the_skipped_rig(client):
    add_runs("prep.run", [600, 600, 600])
    add_runs("mesh.run", [100, 100, 100])
    base = client.get("/api/suggest?category=humanoid&platform=pc_mid").json()
    assert base["estimates"]["prep"] == 600 and base["estimate_basis"]["prep"] == "history"
    high = client.get("/api/suggest?category=humanoid&platform=high").json()
    assert high["estimates"]["mesh"] >= 600  # never below the slower default
    assert base["total_minutes"] >= 10


def test_a_card_says_what_it_is_doing():
    act = estimates.activity
    assert act({"current_job_id": "j", "current_kind": "turnaround.run"}) == "Drawing: " + estimates_plain("turnaround")
    assert act({"current_job_id": None, "lent_to": "j", "lent_kind": "prep.run"}) == "Lent to " + estimates_plain("prep")
    assert act({"current_job_id": "j", "current_kind": None}) == "Working"
    assert act({"current_job_id": None, "lent_to": None, "in_use": False}) == "Free"


def estimates_plain(stage_id: str) -> str:
    from vgac import guidance
    return guidance.stage_guide(stage_id)["plain_name"]


def test_queue_status_names_the_job_on_a_card_and_the_job_it_is_lent_to(settings):
    q = make_queue(settings)
    with session_factory()() as db:
        j1, j2 = Job(instance="a", kind="turnaround.run", status="running", payload_json="{}", message=""), \
            Job(instance="host", kind="prep.run", status="running", payload_json="{}", message="")
        db.add_all([j1, j2])
        db.commit()
        i1, i2 = j1.id, j2.id
    q.workers["b"].current_job_id = i1
    q.workers["a"].lent_to = i2
    st = q.status()
    assert st["b"]["current_kind"] == "turnaround.run" and st["b"]["in_use"] is True
    assert st["a"]["lent_kind"] == "prep.run" and st["a"]["in_use"] is True
    assert estimates.activity(st["a"]) == "Lent to " + estimates_plain("prep")


def test_health_calls_a_card_with_a_job_working_even_when_its_engine_does_not_answer(client, monkeypatch):
    q = jobs.active_queue()
    with session_factory()() as db:
        j = Job(instance="a", kind="mesh.run", status="running", payload_json="{}", message="")
        db.add(j)
        db.commit()
        jid = j.id
    q.workers["a"].current_job_id = jid
    try:
        a = client.get("/api/health").json()["instances"]["a"]
        assert a["working"] is True and a["activity"] == "Drawing: " + estimates_plain("mesh")
    finally:
        q.workers["a"].current_job_id = None
