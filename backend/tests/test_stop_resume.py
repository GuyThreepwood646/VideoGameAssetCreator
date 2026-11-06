"""Stop and Resume on every step: a stopped version is marked, Resume puts the same job back with the same
settings, the autopilot pauses on Stop and carries on from the same step on Resume."""

from __future__ import annotations

import asyncio
import time

import pytest

from vgac import stages
from vgac.db import Job, session_factory

from test_api import _project_asset, client, fake_execute, wait_job  # noqa: F401  (fixtures)


@pytest.fixture
def gate(client, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """Every job waits while gate["hold"] is true, so a test can stop a step that is really running."""
    state = {"hold": True}
    inner = stages.execute

    async def execute(ctx, kind, payload):
        while state["hold"]:
            ctx.check_cancelled()
            await asyncio.sleep(0.02)
        await inner(ctx, kind, payload)

    monkeypatch.setattr(stages, "execute", execute)
    yield state
    state["hold"] = False


def _until(fn, timeout: float = 10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def _versions(client, aid: str, stage: str) -> list[dict]:  # noqa: F811
    return client.get(f"/api/assets/{aid}").json()["stages"][stage]


def test_stop_then_resume_one_step(client, gate):  # noqa: F811
    p, a = _project_asset(client)
    aid = a["id"]
    pairs = client.post(f"/api/assets/{aid}/stages/concept/run", json={"count": 2, "params": {"seed": 7}}).json()
    ids = [x["version"]["id"] for x in pairs]
    job_ids = [x["job"]["id"] for x in pairs]
    _until(lambda: any(v["status"] == "running" for v in _versions(client, aid, "concept")))

    out = client.post(f"/api/assets/{aid}/stop", json={"stage": "concept"}).json()
    assert sorted(out["stopped"]) == sorted(ids)
    _until(lambda: all(v["status"] == "rejected" for v in _versions(client, aid, "concept")))
    vs = _versions(client, aid, "concept")
    assert all(v["stopped"] is True and v["error"] == "cancelled" for v in vs)
    assert all(client.get(f"/api/jobs/{j}").json()["status"] == "cancelled" for j in job_ids)

    # Resume: the same versions and the same jobs go back in the queue, with the same settings.
    gate["hold"] = False
    out = client.post(f"/api/assets/{aid}/resume", json={"stage": "concept"}).json()
    assert sorted(v["id"] for v in out["resumed"]) == sorted(ids)
    assert all(v["status"] == "queued" and v["stopped"] is False for v in out["resumed"])
    for j in job_ids:
        assert wait_job(client, j)["status"] == "done"
    vs = _versions(client, aid, "concept")
    assert [v["id"] for v in vs] == ids and all(v["status"] == "done" for v in vs)
    assert [v["params"] for v in vs] == [x["version"]["params"] for x in pairs]
    # Nothing left to resume.
    assert client.post(f"/api/assets/{aid}/resume", json={"stage": "concept"}).json()["resumed"] == []


def test_stop_only_touches_the_named_step_and_dismiss(client, gate):  # noqa: F811
    p, a = _project_asset(client)
    aid = a["id"]
    gate["hold"] = False
    first = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    wait_job(client, first["job"]["id"])
    client.post(f"/api/versions/{first['version']['id']}/select")
    gate["hold"] = True
    second = client.post(f"/api/assets/{aid}/stages/turnaround/run", json={}).json()
    _until(lambda: client.get(f"/api/jobs/{second['job']['id']}").json()["status"] == "running")
    # Stopping another step does nothing to this one.
    assert client.post(f"/api/assets/{aid}/stop", json={"stage": "mesh"}).json()["stopped"] == []
    assert client.get(f"/api/jobs/{second['job']['id']}").json()["status"] == "running"
    assert client.post(f"/api/assets/{aid}/stop", json={"stage": "bogus"}).status_code == 400
    client.post(f"/api/assets/{aid}/stop", json={"stage": "turnaround"})
    _until(lambda: _versions(client, aid, "turnaround")[0]["stopped"])
    # The kept concept is untouched.
    assert _versions(client, aid, "concept")[0]["status"] == "done"
    # Leave it stopped: no longer offered for Resume, still in the history.
    assert client.post(f"/api/assets/{aid}/stopped/dismiss", json={"stage": "turnaround"}).json() == {"dismissed": 1}
    v = _versions(client, aid, "turnaround")[0]
    assert v["status"] == "rejected" and v["stopped"] is False
    assert client.post(f"/api/assets/{aid}/resume", json={"stage": "turnaround"}).json()["resumed"] == []


def test_a_stop_followed_by_a_fresh_run_is_history(client, gate):  # noqa: F811
    p, a = _project_asset(client)
    aid = a["id"]
    old = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    _until(lambda: client.get(f"/api/jobs/{old['job']['id']}").json()["status"] == "running")
    client.post(f"/api/assets/{aid}/stop", json={})
    _until(lambda: _versions(client, aid, "concept")[0]["stopped"])
    gate["hold"] = False
    new = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    wait_job(client, new["job"]["id"])
    # The fresh result is the newest version, so the older stop is not resumed.
    assert client.post(f"/api/assets/{aid}/resume", json={}).json()["resumed"] == []


def test_autopilot_stop_and_resume(client, gate):  # noqa: F811
    p, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    assert client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never"}).json()["status"] == "running"
    _until(lambda: any(v["status"] == "running" for v in _versions(client, aid, "concept")))
    first_ids = [v["id"] for v in _versions(client, aid, "concept")]
    assert len(first_ids) == 2

    out = client.post(f"/api/assets/{aid}/stop", json={}).json()
    assert out["autopilot"]["status"] == "paused" and out["autopilot"]["stopped_by_user"] is True
    assert "Resume" in out["autopilot"]["reason"]
    _until(lambda: all(v["stopped"] for v in _versions(client, aid, "concept")))

    gate["hold"] = False
    out = client.post(f"/api/assets/{aid}/resume", json={}).json()
    assert out["autopilot"]["status"] == "running" and out["autopilot"]["stopped_by_user"] is False
    st = _until(lambda: (s := client.get(f"/api/assets/{aid}/autopilot").json())["status"] in ("done", "error", "paused") and s, timeout=30)
    assert st["status"] == "done", st
    full = client.get(f"/api/assets/{aid}").json()
    # The stopped candidates were resumed, not made a second time.
    assert [v["id"] for v in full["stages"]["concept"]] == first_ids
    assert all(len(full["stages"][s]) == 1 for s in ("turnaround", "mesh", "prep", "export"))


def test_stopping_from_the_job_log_pauses_the_autopilot(client, gate):  # noqa: F811
    p, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never"})
    _until(lambda: len(_versions(client, aid, "concept")) == 2)
    for v in _versions(client, aid, "concept"):
        client.post(f"/api/jobs/{v['job_id']}/cancel")
    st = _until(lambda: (s := client.get(f"/api/assets/{aid}/autopilot").json())["status"] != "running" and s)
    assert st["status"] == "paused" and st["stopped_by_user"] is True, st
    # "Make it" after a stop resumes the same candidates.
    gate["hold"] = False
    ids = [v["id"] for v in _versions(client, aid, "concept")]
    client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never"})
    st = _until(lambda: (s := client.get(f"/api/assets/{aid}/autopilot").json())["status"] in ("done", "error", "paused") and s, timeout=30)
    assert st["status"] == "done", st
    assert [v["id"] for v in client.get(f"/api/assets/{aid}").json()["stages"]["concept"]] == ids


def test_cancelling_a_queued_review_keeps_the_finished_version(client, gate):  # noqa: F811
    p, a = _project_asset(client)
    aid = a["id"]
    gate["hold"] = False
    first = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    wait_job(client, first["job"]["id"])
    gate["hold"] = True
    blocker = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    _until(lambda: client.get(f"/api/jobs/{blocker['job']['id']}").json()["status"] == "running")
    r = client.post(f"/api/versions/{first['version']['id']}/judge", params={"mode": "metrics"})
    if r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("job"):
        client.post(f"/api/jobs/{r.json()['job']['id']}/cancel")
    client.post(f"/api/assets/{aid}/stop", json={})
    assert client.get(f"/api/versions/{first['version']['id']}").json()["status"] == "done"


def test_autopilot_resume_from_an_earlier_step(client, gate):  # noqa: F811
    """Stopped on a later step because an earlier one looked off: going back makes that step again and rebuilds
    everything after it; the step before it is left alone."""
    p, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    gate["hold"] = False
    client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never", "until_stage": "mesh"})
    st = _until(lambda: (s := client.get(f"/api/assets/{aid}/autopilot").json())["status"] in ("done", "error", "paused") and s, timeout=30)
    assert st["status"] == "done", st
    before = client.get(f"/api/assets/{aid}").json()
    kept_concept = before["selected"]["concept"]
    old_turn, old_mesh = before["selected"]["turnaround"], before["selected"]["mesh"]
    # Carry on, and stop while prep is running.
    gate["hold"] = True
    client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never"})
    _until(lambda: any(v["status"] == "running" for v in _versions(client, aid, "prep")))
    client.post(f"/api/assets/{aid}/stop", json={})
    _until(lambda: _versions(client, aid, "prep")[0]["stopped"])
    assert client.post(f"/api/assets/{aid}/autopilot", json={"from_stage": "bogus"}).status_code == 400
    # Go back to the turnaround.
    gate["hold"] = False
    out = client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never", "from_stage": "turnaround"}).json()
    assert out["status"] == "running" and out["stopped_by_user"] is False
    st = _until(lambda: (s := client.get(f"/api/assets/{aid}/autopilot").json())["status"] in ("done", "error", "paused") and s, timeout=30)
    assert st["status"] == "done", st
    after = client.get(f"/api/assets/{aid}").json()
    assert after["selected"]["concept"] == kept_concept
    assert after["selected"]["turnaround"] != old_turn and after["selected"]["mesh"] != old_mesh
    by_id = {v["id"]: v for s in after["stages"].values() for v in s}
    assert by_id[old_turn]["status"] == "rejected" and by_id[old_mesh]["status"] == "rejected"
    # The new mesh is built on the new turnaround, and the stopped prep was not resumed: a fresh one was made.
    assert by_id[after["selected"]["mesh"]]["parent_version_id"] == after["selected"]["turnaround"]
    prep = after["stages"]["prep"]
    assert len(prep) == 2 and prep[0]["status"] == "rejected" and prep[0]["stopped"] is False and prep[1]["status"] == "done"


def test_autopilot_fix_can_be_a_redraw(client, gate):  # noqa: F811
    """Fix and continue with a refine fix runs a refine of the kept result with the instruction, then carries on."""
    p, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    gate["hold"] = False
    client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never", "until_stage": "concept"})
    st = _until(lambda: (s := client.get(f"/api/assets/{aid}/autopilot").json())["status"] in ("done", "error", "paused") and s, timeout=30)
    assert st["status"] == "done", st
    kept = client.get(f"/api/assets/{aid}").json()["selected"]["concept"]
    bad = client.post(f"/api/assets/{aid}/autopilot", json={"fix": {"stage": "concept", "op": "refine", "params": {}}})
    assert bad.status_code == 400
    client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never", "until_stage": "concept",
                                                      "fix": {"stage": "concept", "op": "refine", "instruction": "Redraw it in an A-pose", "params": {}}})
    st = _until(lambda: (s := client.get(f"/api/assets/{aid}/autopilot").json())["status"] in ("done", "error", "paused") and s, timeout=30)
    assert st["status"] == "done", st
    full = client.get(f"/api/assets/{aid}").json()
    new = [v for v in full["stages"]["concept"] if v["parent_version_id"] == kept]
    assert len(new) == 1 and new[0]["status"] == "done" and new[0]["metrics"].get("instruction") == "Redraw it in an A-pose"
    assert full["selected"]["concept"] == new[0]["id"]


def test_resume_keeps_the_seed_the_stopped_run_picked(client, monkeypatch):  # noqa: F811
    from vgac.db import Job, Version, session_factory

    state = {"hold": True}
    inner = stages.execute

    async def execute(ctx, kind, payload):
        with session_factory()() as db:  # like a real stage: resolve the random seed and write it on the version
            v = db.get(Version, payload["version_id"])
            v.params = {**v.params, "seed": payload.get("params", {}).get("seed") or 4242}
            db.commit()
        while state["hold"]:
            ctx.check_cancelled()
            await asyncio.sleep(0.02)
        await inner(ctx, kind, payload)

    monkeypatch.setattr(stages, "execute", execute)
    p, a = _project_asset(client)
    aid = a["id"]
    out = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    _until(lambda: client.get(f"/api/jobs/{out['job']['id']}").json()["status"] == "running")
    client.post(f"/api/assets/{aid}/stop", json={"stage": "concept"})
    _until(lambda: _versions(client, aid, "concept")[0]["stopped"])
    state["hold"] = False
    client.post(f"/api/assets/{aid}/resume", json={"stage": "concept"})
    assert wait_job(client, out["job"]["id"])["status"] == "done"
    with session_factory()() as db:
        assert db.get(Job, out["job"]["id"]).payload["params"]["seed"] == 4242
    assert _versions(client, aid, "concept")[0]["params"]["seed"] == 4242


def test_redrawing_views_after_a_stopped_redraw_was_set_aside(client, gate):  # noqa: F811
    """Check all sides: redraw a view, Stop, Leave it stopped. The set-aside version cannot be redrawn from (409);
    the finished one still can, several views at once, and views it does not have are refused before anything is made."""
    p, a = _project_asset(client)
    aid = a["id"]
    gate["hold"] = False
    concept = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    wait_job(client, concept["job"]["id"])
    client.post(f"/api/versions/{concept['version']['id']}/select")
    turn = client.post(f"/api/assets/{aid}/stages/turnaround/run", json={}).json()
    assert wait_job(client, turn["job"]["id"])["status"] == "done"
    tid = turn["version"]["id"]

    gate["hold"] = True
    redraw = client.post(f"/api/versions/{tid}/refine", json={"instruction": "Redraw this view", "views": ["front"]}).json()
    _until(lambda: client.get(f"/api/jobs/{redraw['job']['id']}").json()["status"] == "running")
    client.post(f"/api/assets/{aid}/stop", json={"stage": "turnaround"})
    _until(lambda: _versions(client, aid, "turnaround")[-1]["stopped"])
    assert client.post(f"/api/assets/{aid}/stopped/dismiss", json={"stage": "turnaround"}).json() == {"dismissed": 1}
    aside = _versions(client, aid, "turnaround")[-1]
    assert aside["id"] == redraw["version"]["id"] and aside["status"] == "rejected" and aside["stopped"] is False

    # The set-aside version is not finished: no redraw from it.
    assert client.post(f"/api/versions/{aside['id']}/refine", json={"instruction": "Redraw this view", "views": ["left"]}).status_code == 409
    # Bad requests make no version.
    before = len(_versions(client, aid, "turnaround"))
    for body in ({"views": []}, {"views": ["sideways"]}, {"views": ["top"]}, {"view": "top"}):
        assert client.post(f"/api/versions/{tid}/refine", json={"instruction": "Redraw this view", **body}).status_code == 400
    assert client.post(f"/api/versions/{concept['version']['id']}/refine", json={"instruction": "x", "views": ["left"]}).status_code == 400
    assert len(_versions(client, aid, "turnaround")) == before

    # The finished version still redraws, two views in one new version, and that one redraws again.
    gate["hold"] = False
    both = client.post(f"/api/versions/{tid}/refine", json={"instruction": "Redraw this view", "views": ["left", "back"]}).json()
    assert wait_job(client, both["job"]["id"])["status"] == "done"
    assert both["job"]["kind"] == "turnaround.refine"
    with session_factory()() as db:  # the drawing itself is faked here; the stage tests cover what it does with `views`
        assert db.get(Job, both["job"]["id"]).payload["views"] == ["left", "back"]
    v = client.get(f"/api/versions/{both['version']['id']}").json()
    assert v["status"] == "done"
    again = client.post(f"/api/versions/{v['id']}/refine", json={"instruction": "Redraw this view", "views": ["right"]}).json()
    assert wait_job(client, again["job"]["id"])["status"] == "done"
    assert client.get(f"/api/versions/{again['version']['id']}").json()["status"] == "done"
    assert len(_versions(client, aid, "turnaround")) == before + 2

