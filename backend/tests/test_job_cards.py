"""Which graphics cards a job used is on its record (vgac/cardlog.py): every card a job draws on, with the kind of work and
the step, the card it was planned on, and the cards it was moved from. Card lending is faked (no ComfyUI, no card)."""

from __future__ import annotations

import asyncio

from test_card_lending import FakeClient, make_queue, queued_job  # noqa: F401  (the queue helpers and fixtures)
from vgac import jobs
from vgac.db import Job, session_factory

LEND_KIND = "turnaround.run"


def _result(job_id: str) -> dict:
    with session_factory()() as db:
        j = db.get(Job, job_id)
        return dict(j.result) if isinstance(j.result, dict) else {}


def _host_job() -> str:
    """A running host job (the clean-up), as the host worker starts it."""
    with session_factory()() as db:
        j = Job(instance=jobs.HOST, kind="prep.run", status="running", payload_json="{}", message="")
        db.add(j)
        db.commit()
        return j.id


def test_P6_a_host_job_that_draws_on_a_card_lists_that_card_with_kind_and_step(settings):
    """Clean-up (host) lends card a for the face redraw: the record lists a with turnaround.run and the step's name."""
    q = make_queue(settings)
    job_id = _host_job()
    lease, why = asyncio.run(q.lend(jobs.HOST, job_id, LEND_KIND, avoid="b", step="face redraw"))
    assert lease is not None and why == ""
    asyncio.run(lease.end())
    cards = _result(job_id).get("cards") or []
    used = [c for c in cards if c.get("role") == "lent"]
    assert [(c["card"], c["kind"], c["step"]) for c in used] == [("a", LEND_KIND, "face redraw")]
    assert used[0]["started_at"] and used[0]["ended_at"]


def test_P6b_a_card_only_held_for_the_first_close_up_is_drawn_not_lent(settings):
    """The home card the face pass draws the first close-up on is a claim, not a loan: listed as used (role drew),
    and not in the job's lent list, which the estimates read as a second card."""
    q = make_queue(settings)
    job_id = _host_job()
    lease, _ = asyncio.run(q.lend(jobs.HOST, job_id, LEND_KIND, only="b", step="face redraw", claimed=True))
    assert lease is not None
    asyncio.run(lease.end())
    res = _result(job_id)
    assert [(c["card"], c["role"], c["step"]) for c in res.get("cards") or []] == [("b", "drew", "face redraw")]
    assert res.get("lent") in (None, [])


def test_P1_a_card_job_records_its_own_card_with_start_and_end(settings):
    """A job on card b is recorded with card b as its own card, once it starts and once it ends."""
    q = make_queue(settings)
    job_id = queued_job("b", "turnaround.run")
    from vgac import cardlog
    with session_factory()() as db:
        j = db.get(Job, job_id)
        cardlog.start_own(j)
        db.commit()
    with session_factory()() as db:
        j = db.get(Job, job_id)
        cardlog.end_own(j)
        db.commit()
    own = [c for c in _result(job_id)["cards"] if c["role"] == "own"]
    assert [(c["card"], c["kind"]) for c in own] == [("b", "turnaround.run")]
    assert own[0]["started_at"] and own[0]["ended_at"]
    assert all(c["role"] != "planned" for c in _result(job_id)["cards"])


def test_P1_planned_card_is_shown_before_start_and_replaced_by_the_real_card(settings):
    q = make_queue(settings)
    job = q.submit("b", "concept.run", None, None, {})
    assert _result(job.id)["cards"] == [{"card": "b", "role": "planned"}]
    from vgac import cardlog
    with session_factory()() as db:
        j = db.get(Job, job.id)
        cardlog.start_own(j)
        db.commit()
    cards = _result(job.id)["cards"]
    assert [c["role"] for c in cards] == ["own"] and cards[0]["card"] == "b"


def test_P7_a_job_moved_by_sharing_keeps_the_card_it_came_from(settings):
    """Sharing moves a waiting job from a busy card to a free one: the record keeps the move, not only the new card."""
    q = make_queue(settings)
    q.share_when_busy = True
    job = q.submit("a", "concept.run", None, None, {})   # a is turned to the job's card; b is free and may take it
    q.workers["a"].current_job_id = "busy"               # card a is busy with another job
    taken = q.take_shared("b")
    assert taken == job.id
    res = _result(job.id)
    assert [(m["from"], m["to"]) for m in res.get("moved") or []] == [("a", "b")]


def test_P3_mesh_runs_on_card_a_unless_the_person_says_otherwise(settings):
    """The 3D step (mesh) runs on card a, the card labelled 3D. Card b can also build models (instance_capabilities), so
    it takes a 3D build only when the person sets b as the default card or turns a off; the record must say which."""
    assert jobs.route("mesh", "", settings, (), None, None) == "a"
    assert jobs.route("mesh", "", settings, (), None, "b") == "b"        # the default card the person chose
    assert jobs.route("mesh", "", settings, ("a",), None, None) == "b"   # a off: the fallback card that can build it


def test_P4_each_stage_runs_on_the_card_the_stage_map_names(settings):
    for stage, card in settings.stage_instances.items():
        if card == jobs.HOST:
            assert jobs.route(stage, "", settings) == jobs.HOST
        else:
            assert jobs.route(stage, "", settings) == card
