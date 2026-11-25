"""A rig step that will skip rigging (the material category, template "none", or a category with no rig engine such
as rigid props) needs no graphics card, so it runs on the host and a turned-off card cannot hold it up. A rig that
needs a card goes to any card that is on and can rig; with none on, the submit fails at once with a plain sentence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vgac import jobs, reference
from vgac.db import Asset, Project, session_factory
from vgac.stages import rig

from test_card_lending import make_queue


def test_a_rigid_prop_rig_runs_on_the_host_when_card_b_is_off(settings):
    assert jobs.route("rig", "prop_rigid", settings, {"b"}) == jobs.HOST
    assert jobs.route("rig", "prop_rigid", settings, {"b"}, params={"template": "auto"}) == jobs.HOST


def test_a_rigid_prop_rig_needs_no_card_even_with_both_cards_off(settings):
    assert jobs.route("rig", "prop_rigid", settings, {"a", "b"}) == jobs.HOST


def test_a_rig_that_skips_by_choice_runs_on_the_host(settings):
    # template "none" on a character: the step skips, so it needs no card either.
    assert jobs.route("rig", "humanoid", settings, {"b"}, params={"template": "none"}) == jobs.HOST


def test_an_explicit_template_on_a_rigid_prop_still_needs_a_card(settings):
    # Asking for a rig template makes the step rig, so it goes to a card that can rig.
    assert jobs.route("rig", "prop_rigid", settings, {"b"}, params={"template": "mixamo"}) == "a"


def test_a_rig_that_needs_a_card_moves_to_card_a_when_b_is_off(settings):
    assert jobs.route("rig", "humanoid", settings, {"b"}) == "a"
    assert jobs.route("rig", "humanoid", settings, {"b"}, params={"template": "auto"}) == "a"


def test_a_rig_that_needs_a_card_has_none_when_no_card_on_can_rig(settings):
    assert jobs.route("rig", "humanoid", settings, {"a", "b"}) is None
    assert jobs.route("rig", "humanoid", settings, (), {"a": ["pictures"], "b": ["pictures"]}) is None


def test_card_a_is_rig_capable_by_default(settings):
    assert "rig" in jobs.capabilities("a", settings)
    assert "rigging" in jobs.possible_kinds("a", settings)


def test_rig_skips_matches_the_stage_template_rule_for_every_category(settings):
    for cid in reference._data()["categories"]:
        for params in ({}, {"template": "auto"}, {"template": "none"}, {"template": "mixamo"}, {"template": "generic"}):
            sc = SimpleNamespace(category=reference.category(cid))
            skips = rig.resolve_template(sc, params) is None or cid == "material"
            assert rig.rig_skips(cid, params) == skips, (cid, params)


def _rig_version(asset_category: str, name: str):
    from vgac.db import Version

    with session_factory()() as db:
        p = Project(name="P")
        db.add(p)
        db.flush()
        a = Asset(project_id=p.id, name=name, category=asset_category)
        db.add(a)
        db.flush()
        v = Version(asset_id=a.id, stage="rig", status="queued")
        db.add(v)
        db.commit()
        return a.id, v.id


def test_submit_fails_at_once_when_no_configured_card_can_rig(settings, monkeypatch):
    """Only when no configured card can rig at all (on or off) is the rig refused with the plain sentence; the
    version is set to error and no job is queued."""
    from vgac import guidance, pipeline
    from vgac.db import Job, Version

    no_rig = settings.model_copy(update={"instance_capabilities": {"a": ["concept"], "b": ["concept"]}})
    q = make_queue(no_rig)
    monkeypatch.setattr(pipeline, "queue", lambda: q)
    a_id, v_id = _rig_version("humanoid", "hero")
    with session_factory()() as db:
        a = db.get(Asset, a_id)
        v = db.get(Version, v_id)
        with pytest.raises(pipeline.Conflict) as err:
            pipeline.submit(db, a, v, "run", {"params": {}})
        assert str(err.value) == guidance.NO_RIG_CARD
        assert v.status == "error" and v.error == guidance.NO_RIG_CARD
        assert db.query(Job).count() == 0


def test_submit_queues_a_rig_when_the_only_capable_cards_are_off(settings, monkeypatch):
    """Both cards can rig but are off: the rig waits like every other step, with the waiting message."""
    from vgac import pipeline
    from vgac.db import Version

    q = make_queue(settings)
    q.disabled = {"a", "b"}
    monkeypatch.setattr(pipeline, "queue", lambda: q)
    a_id, v_id = _rig_version("humanoid", "hero")
    with session_factory()() as db:
        job = pipeline.submit(db, db.get(Asset, a_id), db.get(Version, v_id), "run", {"params": {}})
        assert job.status == "queued" and job.instance == "b"
        assert job.message.startswith("Waiting for")


def test_a_resumed_rigid_rig_runs_on_the_host(settings):
    """A stopped rig on a rigid asset comes back in the queue on the host, even with card b off, and does not park."""
    from vgac.db import Job, STOPPED_ERROR, Version

    q = make_queue(settings)
    q.disabled = {"b"}
    a_id, v_id = _rig_version("prop_rigid", "sword")
    with session_factory()() as db:
        job = Job(instance="b", kind="rig.run", asset_id=a_id, version_id=v_id, status="cancelled",
                  payload_json='{"version_id": "%s", "op": "run", "params": {}}' % v_id, message="")
        db.add(job)
        db.flush()
        v = db.get(Version, v_id)
        v.status, v.error, v.job_id = "rejected", STOPPED_ERROR, job.id
        db.commit()
        job_id = job.id
    q.resume_asset(a_id, "rig")
    with session_factory()() as db:
        resumed = db.get(Job, job_id)
        assert resumed.status == "queued"
        assert resumed.instance == jobs.HOST


def test_submit_sends_a_rigid_rig_to_the_host_with_card_b_off(settings, monkeypatch):
    from vgac import pipeline
    from vgac.db import Job, Version

    q = make_queue(settings)
    q.disabled = {"b"}
    monkeypatch.setattr(pipeline, "queue", lambda: q)
    with session_factory()() as db:
        p = Project(name="P")
        db.add(p)
        db.flush()
        a = Asset(project_id=p.id, name="sword", category="prop_rigid")
        db.add(a)
        db.flush()
        v = Version(asset_id=a.id, stage="rig", status="queued")
        db.add(v)
        db.commit()
        job = pipeline.submit(db, a, v, "run", {"params": {}})
        assert job.instance == jobs.HOST
        assert job.message == ""
