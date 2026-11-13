"""Autopilot stops once after clean-up when the facing tests disagree or the face paint was held (autopilot.STOP_AND_ASK),
without those checks becoming "block" severity. Checks are the real ones (quality.facing_tests_check, face_detail.face_checks);
the step builds are faked like the other autopilot tests."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from vgac import autopilot, quality, stages
from vgac.db import Version, session_factory
from vgac.stages import face_detail as fd

from test_api import _project_asset, client, fake_execute  # noqa: F401  (fixtures)


def facing_fail():
    return quality.facing_tests_check(0.0, 180.0, 180.0, 0.0, 0.0)


def held_face():
    return fd.face_checks({"face_detail": {"ran": False, "skipped": "poor_fit", "alignment": {"score": 0.4}}})[0]


def blurry_face():
    return fd.face_checks({"face_detail": {"ran": True, "sharpness_after": 0.7, "feature_match_after": 0.9}})[0]


def _v(stage="prep", checks=()):
    return SimpleNamespace(stage=stage, metrics={"checks": list(checks)})


def test_the_two_conditions_are_not_block_severity():
    assert facing_fail()["severity"] == "warn" and held_face()["severity"] == "warn"
    assert not facing_fail()["passed"] and not held_face()["passed"]


def test_each_condition_stops_under_block_with_a_plain_message():
    r = autopilot.attention_reason(_v(checks=[facing_fail()]), "block")
    assert r.startswith("The two ways of working out which way the character faces disagree by about 180 degrees")
    assert "facing the wrong way" in r and "Resume" in r and "step 4" in r
    r = autopilot.attention_reason(_v(checks=[held_face()]), "block")
    assert r.startswith("The face was left as it was") and "fit 0.4 of 1, needs 0.65" in r
    assert "clearer picture" in r and "try it again" in r and "Resume" in r
    both = autopilot.attention_reason(_v(checks=[facing_fail(), held_face()]), "block")
    assert "facing the wrong way" in both and "left as it was" in both


def test_nothing_else_stops_it():
    assert autopilot.attention_reason(_v(checks=[blurry_face()]), "block") is None  # the face check failing for another reason
    assert autopilot.attention_reason(_v(checks=[]), "block") is None
    ok = quality.facing_tests_check(0.0, 10.0, 10.0, 0.0, 0.0)
    assert ok["passed"] and autopilot.attention_reason(_v(checks=[ok]), "block") is None
    assert autopilot.attention_reason(_v(stage="mesh", checks=[facing_fail()]), "block") is None  # only the clean-up step


def test_never_and_warn_are_as_before():
    v = _v(checks=[facing_fail(), held_face()])
    assert autopilot.attention_reason(v, "never") is None
    assert autopilot.attention_reason(v, "warn") is None  # "warn" stops on any failed check through _passes, as today
    assert autopilot._passes(v, "never") == (True, None)
    ok, reason = autopilot._passes(v, "warn")
    assert not ok and reason
    assert autopilot._passes(v, "block") == (True, None)  # still no block-severity check: _passes itself is unchanged


# --- through the autopilot, with the prep step's checks faked ----------------------------------------------------


@pytest.fixture
def prep_checks(monkeypatch):
    """The checks the fake clean-up step records."""
    box = {"checks": []}
    inner = stages.execute

    async def execute(ctx, kind, payload):
        await inner(ctx, kind, payload)
        with session_factory()() as db:
            v = db.get(Version, payload["version_id"])
            if v is not None and v.stage == "prep":
                m = dict(v.metrics)
                m["checks"] = list(box["checks"])
                v.metrics = m
                db.commit()

    monkeypatch.setattr(stages, "execute", execute)
    monkeypatch.setattr(autopilot, "POLL_SECONDS", 0.05)
    return box


def _wait(client, aid, until=("done", "error", "paused"), timeout=40.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/assets/{aid}/autopilot").json()
        if st["status"] in until:
            return st
        time.sleep(0.1)
    raise AssertionError(st)


def _run(client, stop_on):
    p, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": stop_on})
    return aid, _wait(client, aid)


@pytest.mark.parametrize("make,words", [(facing_fail, "facing the wrong way"), (held_face, "left as it was")])
def test_autopilot_stops_after_clean_up_and_carries_on_by_hand_or_on_resume(client, prep_checks, make, words):
    prep_checks["checks"] = [make()]
    aid, st = _run(client, "block")
    assert st["status"] == "paused" and st["stage"] == "prep" and words in st["reason"]
    full = client.get(f"/api/assets/{aid}").json()
    assert not full["stages"].get("rig")  # stopped after the clean-up step, before the next one was built
    vid = st["version_id"]
    # by hand: the person keeps the version and runs the next step; nothing in the pages blocks it
    assert client.post(f"/api/versions/{vid}/select").status_code == 200
    assert client.post(f"/api/assets/{aid}/stages/rig/run", json={}).status_code == 200
    # on Resume: the same version is judged again without the list and the run goes on to the end
    st = client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "block"}).json()
    st = _wait(client, aid, until=("done", "error", "paused"))
    assert st["status"] == "done", st


def test_autopilot_resume_without_selecting_carries_on(client, prep_checks):
    prep_checks["checks"] = [held_face()]
    aid, st = _run(client, "block")
    assert st["status"] == "paused"
    client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "block"})
    st = _wait(client, aid)
    assert st["status"] == "done", st


def test_another_face_failure_does_not_stop_autopilot(client, prep_checks):
    prep_checks["checks"] = [blurry_face()]
    aid, st = _run(client, "block")
    assert st["status"] == "done", st


@pytest.mark.parametrize("stop_on,status", [("never", "done"), ("warn", "paused")])
def test_never_runs_through_and_warn_stops_as_before(client, prep_checks, stop_on, status):
    prep_checks["checks"] = [facing_fail(), held_face()]
    aid, st = _run(client, stop_on)
    assert st["status"] == status, st
    if stop_on == "warn":
        assert st["stage"] == "prep" and st["reason"] == facing_fail()["help"]  # the first failed check's own help, as today
