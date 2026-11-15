"""\"Ready\" on the setup card means a test character ran to the end (models_setup.test_character_state, firstrun.first_run).

The state is read from the test character's own asset, not from what the models panel last remembered."""

from __future__ import annotations

import json

import pytest

from vgac import config, db, downloader, firstrun, models_setup
from vgac.db import Asset, Project, session_factory


@pytest.fixture
def world(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    with session_factory()() as s:
        p = Project(name="Setup test")
        s.add(p)
        s.commit()
        a = Asset(project_id=p.id, name="Setup test character", category="humanoid", style_preset="painted", platform_target="pc_mid", prompt="a knight")
        s.add(a)
        s.commit()
        aid = a.id

    def set_autopilot(**fields):
        with session_factory()() as s:
            a = s.get(Asset, aid)
            a.autopilot = {"status": "idle", "stage": None, "reason": None, **fields}
            s.commit()

    return aid, set_autopilot, tmp_path


def record(tmp, aid):
    (tmp / "local").mkdir(exist_ok=True)
    (tmp / "local" / "test_character.json").write_text(json.dumps({"asset_id": aid, "passed": None}), encoding="utf-8")


def test_nothing_made_is_not_run(world):
    assert models_setup.test_character_state()["state"] == "not_run"
    aid, _, tmp = world
    record(tmp, "no-such-asset")  # its asset is gone
    assert models_setup.test_character_state()["state"] == "not_run"


def test_running_passed_and_failed_come_from_the_asset_itself(world):
    aid, set_autopilot, tmp = world
    record(tmp, aid)  # the panel only wrote "passed: null" and then went away
    set_autopilot(status="running", stage="mesh")
    got = models_setup.test_character_state()
    assert got["state"] == "running" and got["stage"] == "mesh" and got["passed"] is None
    set_autopilot(status="done")
    got = models_setup.test_character_state()
    assert got["state"] == "passed" and got["passed"] is True
    set_autopilot(status="error", stage="rig", reason="The step failed: no card")
    got = models_setup.test_character_state()
    assert got["state"] == "failed" and got["passed"] is False and got["stage"] == "rig" and "no card" in got["reason"]
    set_autopilot(status="paused", reason=None)
    assert models_setup.test_character_state()["state"] == "failed" and models_setup.test_character_state()["reason"]  # stopped by hand is not "ready" either
    set_autopilot(status="idle")
    assert models_setup.test_character_state()["state"] == "not_run"


def test_the_setup_card_state_carries_it_so_ready_can_wait_for_it(world):
    aid, set_autopilot, tmp = world
    s = config.get_settings()
    fr = firstrun.first_run(s, [])
    assert fr["test_character"]["state"] == "not_run"
    record(tmp, aid)
    set_autopilot(status="done")
    assert firstrun.first_run(s, [])["test_character"]["state"] == "passed"
