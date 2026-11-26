"""A job that repeats a version's seed goes back to the card that made that version (one seed gives a different but
repeatable result on each card). Cards are stubbed; nothing here reaches an engine."""

from __future__ import annotations

import time
from types import SimpleNamespace

from vgac import guidance, jobs, pipeline
from vgac.db import Asset, Job, Version, session_factory
from test_host_lanes import add_job, make_assets


def fresh(q, **cards):
    now = time.monotonic()
    q.foreign = {n: (c, now) for n, c in cards.items()}


def make_version(asset_id, stage="mesh", seed=11, cards=None, status="done", parent=None, job_result=None):
    with session_factory()() as db:
        v = Version(asset_id=asset_id, stage=stage, status=status, parent_version_id=parent)
        v.params = {"seed": seed} if seed is not None else {}
        v.metrics = {"cards": [{"instance": c, "gpu": "x"} for c in cards]} if cards else {}
        db.add(v)
        db.flush()
        if job_result is not None:
            j = Job(instance="a", kind=f"{stage}.run", asset_id=asset_id, version_id=v.id, status="done", payload_json="{}", message="")
            j.result = job_result
            db.add(j)
            db.flush()
            v.job_id = j.id
        db.commit()
        return v.id


def pin_for(version_id, settings):
    with session_factory()() as db:
        return jobs.seed_pin(db, db.get(Version, version_id), settings)


# --- routing ---------------------------------------------------------------------------------------------------------


def test_pinned_card_free_wins_over_the_default_and_the_usual_card(settings):
    q = jobs.JobQueue(settings)
    q.default_instance = "b"
    assert q.place("turnaround", "", pin="a") == ("a", None)
    assert q.place("turnaround", "")[0] == "b"  # no pin: the default card, as before


def test_pinned_card_busy_with_foreign_work_stays_pinned(settings):
    q = jobs.JobQueue(settings)
    fresh(q, a=3, b=0)
    q.default_instance = "a"
    assert q.place("turnaround", "", pin="a")[0] == "a"  # the load spread would send it to b
    assert q.place("turnaround", "")[0] == "b"           # no pin: spread as before


def test_pinned_card_off_or_not_allowed_falls_back_without_waiting(settings):
    q = jobs.JobQueue(settings)
    q.disabled = {"a"}
    assert q.place("turnaround", "", pin="a") == ("b", None)
    q.disabled = set()
    q.allowed_kinds["a"] = set()
    assert q.place("turnaround", "", pin="a") == ("b", None)


def test_unknown_pin_is_ignored(settings):
    q = jobs.JobQueue(settings)
    assert q.place("turnaround", "", pin="zzz") == q.place("turnaround", "")


def test_single_card_install_has_no_pin_effect(settings):
    q = jobs.JobQueue(settings)
    q.disabled = {"b"}
    assert q.place("turnaround", "", pin="a")[0] == "a"
    assert q.place("turnaround", "", pin="b")[0] == "a"  # the pinned card is off: the only card runs it
    assert jobs.route("turnaround", "", settings, ("a", "b"), pin="a") is None


def test_host_steps_ignore_the_pin(settings):
    assert jobs.route("prep", "", settings, pin="a") == jobs.HOST


# --- which card a repeat goes back to ------------------------------------------------------------------------------


def test_repeat_of_a_parent_version_pins_to_the_parents_card_not_the_latest(settings):
    (aid,) = make_assets(1)
    older = make_version(aid, seed=5, cards=["a"])
    make_version(aid, seed=5, cards=["b"])  # a later same-seed version on another card
    child = make_version(aid, seed=5, status="queued", parent=older)
    assert pin_for(child, settings) == "a"


def test_no_same_step_parent_uses_the_latest_same_seed_version_with_the_same_input(settings):
    (aid,) = make_assets(1)
    inp = make_version(aid, stage="turnaround", seed=1, cards=["a"])
    make_version(aid, seed=5, cards=["a"], parent=inp)
    make_version(aid, seed=5, cards=["b"], parent=inp)
    redo = make_version(aid, seed=5, status="queued", parent=inp)
    assert pin_for(redo, settings) == "b"
    assert pin_for(make_version(aid, seed=77, status="queued", parent=inp), settings) is None  # new seed


def test_same_seed_on_a_different_parent_does_not_pin(settings):
    (aid,) = make_assets(1)
    old_in = make_version(aid, stage="turnaround", seed=1, cards=["a"])
    new_in = make_version(aid, stage="turnaround", seed=1, cards=["a"])
    make_version(aid, seed=5, cards=["a"], parent=old_in)
    assert pin_for(make_version(aid, seed=5, status="queued", parent=new_in), settings) is None


def test_parentless_step_pins_only_with_the_same_settings_apart_from_the_seed(settings):
    (aid,) = make_assets(1)

    def concept(seed, prompt, status="done"):
        with session_factory()() as db:
            v = Version(asset_id=aid, stage="concept", status=status)
            v.params = {"seed": seed, "prompt": prompt}
            v.metrics = {"cards": [{"instance": "a", "gpu": "x"}]} if status == "done" else {}
            db.add(v)
            db.commit()
            return v.id

    concept(5, "a knight")
    assert pin_for(concept(5, "a knight", "queued"), settings) == "a"
    assert pin_for(concept(5, "a mage", "queued"), settings) is None


def test_new_seed_and_no_seed_do_not_pin(settings):
    (aid,) = make_assets(1)
    older = make_version(aid, seed=5, cards=["a"])
    assert pin_for(make_version(aid, seed=9, status="queued", parent=older), settings) is None
    assert pin_for(make_version(aid, seed=None, status="queued", parent=older), settings) is None


def test_old_version_falls_back_to_the_jobs_card_record(settings):
    (aid,) = make_assets(1)
    one = make_version(aid, seed=5, job_result={"cards": [{"card": "a", "role": "own"}]})
    assert pin_for(make_version(aid, seed=5, status="queued", parent=one), settings) == "a"


def test_old_version_with_a_lent_second_card_is_not_pinned(settings):
    (aid,) = make_assets(1)
    two = make_version(aid, stage="turnaround", seed=5,
                       job_result={"cards": [{"card": "b", "role": "own"}, {"card": "a", "role": "lent"}]})
    assert pin_for(make_version(aid, stage="turnaround", seed=5, status="queued", parent=two), settings) is None


def test_version_that_drew_on_two_cards_does_not_pin(settings):
    (aid,) = make_assets(1)
    two = make_version(aid, stage="turnaround", seed=5, cards=["b", "a"])
    assert pin_for(make_version(aid, stage="turnaround", seed=5, status="queued", parent=two), settings) is None


def test_host_is_never_a_pin(settings):
    (aid,) = make_assets(1)
    h = make_version(aid, seed=5, job_result={"cards": [{"card": "host", "role": "own"}]})
    assert pin_for(make_version(aid, seed=5, status="queued", parent=h), settings) is None


# --- the job the queue gets ----------------------------------------------------------------------------------------


def submit_repeat(settings, q, monkeypatch, source_card, disabled=()):
    (aid,) = make_assets(1)
    src = make_version(aid, stage="turnaround", seed=5, cards=[source_card])
    q.disabled = set(disabled)
    q.supervisor = None
    sent = {}

    def fake_submit(instance, kind, asset_id, version_id, body, message=None):
        sent.update(instance=instance, body=body)
        return SimpleNamespace()

    monkeypatch.setattr(q, "submit", fake_submit)
    monkeypatch.setattr(pipeline, "queue", lambda: q)
    monkeypatch.setattr(pipeline.firstrun, "blockers", lambda *a, **k: [])
    with session_factory()() as db:
        a = db.get(Asset, aid)
        v = pipeline.new_version(db, a, "turnaround", db.get(Version, src), {"seed": 5})
        v.parent_version_id = src
        db.commit()
        pipeline.submit(db, a, v, "adjust", {"params": {"seed": 5}})
    return sent


def test_submit_marks_the_pin_and_the_fallback_note(settings, monkeypatch):
    q = jobs.JobQueue(settings)
    sent = submit_repeat(settings, q, monkeypatch, "a")
    assert sent["instance"] == "a" and sent["body"]["card_pin"] == "a" and "seed_card_note" not in sent["body"]
    sent = submit_repeat(settings, q, monkeypatch, "a", disabled=["a"])
    assert sent["instance"] == "b" and sent["body"]["seed_card_note"] == guidance.SEED_OTHER_CARD
    assert "card_pin" not in sent["body"]


def test_new_seed_job_carries_no_pin_or_note(settings, monkeypatch):
    q = jobs.JobQueue(settings)
    (aid,) = make_assets(1)
    sent = {}
    monkeypatch.setattr(q, "submit", lambda instance, kind, asset_id, version_id, body, message=None: sent.update(body=body))
    monkeypatch.setattr(pipeline, "queue", lambda: q)
    monkeypatch.setattr(pipeline.firstrun, "blockers", lambda *a, **k: [])
    with session_factory()() as db:
        a = db.get(Asset, aid)
        v = pipeline.new_version(db, a, "turnaround", None, {"seed": 5})
        pipeline.submit(db, a, v, "run", {"params": {"seed": 5}})
    assert "card_pin" not in sent["body"] and "seed_card_note" not in sent["body"]


def test_pinned_queued_job_is_not_moved_to_the_default_card(settings):
    q = jobs.JobQueue(settings)
    aid, aid2 = make_assets(2)
    pinned, free = add_job("turnaround.run", aid, instance="a"), add_job("turnaround.run", aid2, instance="a")
    with session_factory()() as db:
        db.get(Job, pinned).payload = {"card_pin": "a"}
        db.commit()
    q.default_instance = "b"
    q._move_to_default()
    with session_factory()() as db:
        assert db.get(Job, pinned).instance == "a"
        assert db.get(Job, free).instance == "b"


def test_record_cards_writes_every_card_and_the_note(settings):
    from vgac.stages.base import StageContext

    (aid,) = make_assets(1)
    vid = make_version(aid, stage="turnaround", seed=1, status="running")
    with session_factory()() as db:
        ctx = SimpleNamespace(payload={"seed_card_note": guidance.SEED_OTHER_CARD}, instance="b", clients={}, settings=settings)
        sc = StageContext.__new__(StageContext)
        sc.ctx, sc.db, sc.version, sc.settings = ctx, db, db.get(Version, vid), settings
        sc.note_card("b")
        sc.note_card("a")
        sc.note_card("b")
        sc.record_cards()
        m = db.get(Version, vid).metrics
    assert [c["instance"] for c in m["cards"]] == ["b", "a"]
    assert m["seed_card_note"] == guidance.SEED_OTHER_CARD
