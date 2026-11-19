"""Settings chosen before an automatic build (stage_params): they reach the step's first build, survive Resume and going
back to an earlier step, a start with settings replaces them, and bad values or unknown steps are refused (400)."""

from __future__ import annotations

import time

from test_api import _project_asset, client, fake_execute, wait_job  # noqa: F401  (fixtures)
from test_stop_resume import _until, _versions, gate  # noqa: F401  (fixture and helpers)


def _wait_autopilot(client, aid: str, timeout: float = 30.0) -> dict:  # noqa: F811
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/assets/{aid}/autopilot").json()
        if st["status"] in ("done", "error", "paused"):
            return st
        time.sleep(0.1)
    raise AssertionError("autopilot did not finish")


def _start(client, aid: str, body: dict):  # noqa: F811
    return client.post(f"/api/assets/{aid}/autopilot", json={"stop_on": "never", **body})


def test_settings_reach_only_their_steps_first_build(client):  # noqa: F811
    _, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    chosen = {"mesh": {"target_faces": 4321}, "rig": {"target_faces": 1500}}
    st = _start(client, aid, {"stage_params": chosen}).json()
    assert st["status"] == "running" and st["stage_params"] == chosen
    assert _wait_autopilot(client, aid)["status"] == "done"
    full = client.get(f"/api/assets/{aid}").json()
    assert full["stages"]["mesh"][0]["params"].get("target_faces") == 4321
    assert full["stages"]["rig"][0]["params"].get("target_faces") == 1500
    # The other steps keep their own settings.
    assert full["stages"]["prep"][0]["params"].get("target_faces") is None
    assert full["stages"]["turnaround"][0]["params"].get("target_faces") is None


def test_settings_survive_resume_after_a_stop(client, gate):  # noqa: F811
    _, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    chosen = {"mesh": {"target_faces": 4321}}
    _start(client, aid, {"stage_params": chosen})
    _until(lambda: any(v["status"] == "running" for v in _versions(client, aid, "concept")))
    out = client.post(f"/api/assets/{aid}/stop", json={}).json()
    assert out["autopilot"]["status"] == "paused"
    _until(lambda: all(v["stopped"] for v in _versions(client, aid, "concept")))

    gate["hold"] = False
    # Resume sends no settings; the stored ones carry on.
    out = client.post(f"/api/assets/{aid}/resume", json={}).json()
    assert out["autopilot"]["status"] == "running"
    assert out["autopilot"]["stage_params"] == chosen
    assert _wait_autopilot(client, aid)["status"] == "done"
    full = client.get(f"/api/assets/{aid}").json()
    assert full["stages"]["mesh"][0]["params"].get("target_faces") == 4321


def test_going_back_keeps_settings_and_a_new_start_replaces_them(client):  # noqa: F811
    _, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    _start(client, aid, {"stage_params": {"mesh": {"target_faces": 4321}}})
    assert _wait_autopilot(client, aid)["status"] == "done"

    # Going back to the mesh step without settings: the stored choice is used for the rebuilt mesh.
    out = _start(client, aid, {"from_stage": "mesh"}).json()
    assert out["stage_params"] == {"mesh": {"target_faces": 4321}}
    assert _wait_autopilot(client, aid)["status"] == "done"
    meshes = client.get(f"/api/assets/{aid}").json()["stages"]["mesh"]
    assert len(meshes) == 2 and meshes[-1]["selected"] and meshes[-1]["params"].get("target_faces") == 4321

    # Going back again with new settings replaces the stored ones.
    out = _start(client, aid, {"from_stage": "mesh", "stage_params": {"mesh": {"target_faces": 999}}}).json()
    assert out["stage_params"] == {"mesh": {"target_faces": 999}}
    assert _wait_autopilot(client, aid)["status"] == "done"
    meshes = client.get(f"/api/assets/{aid}").json()["stages"]["mesh"]
    assert meshes[-1]["params"].get("target_faces") == 999

    # An empty choice clears the stored settings: the next rebuild uses the usual ones.
    out = _start(client, aid, {"from_stage": "mesh", "stage_params": {}}).json()
    assert out["stage_params"] is None
    assert _wait_autopilot(client, aid)["status"] == "done"
    meshes = client.get(f"/api/assets/{aid}").json()["stages"]["mesh"]
    assert meshes[-1]["params"].get("target_faces") != 999


def test_invalid_values_and_unknown_steps_are_refused(client):  # noqa: F811
    _, a = _project_asset(client, category="prop_rigid")
    aid = a["id"]
    # Unknown step.
    assert _start(client, aid, {"stage_params": {"bogus": {}}}).status_code == 400
    # Value out of range (target_faces needs at least 200).
    r = _start(client, aid, {"stage_params": {"mesh": {"target_faces": 5}}})
    assert r.status_code == 400
    # Value of the wrong kind.
    assert _start(client, aid, {"stage_params": {"mesh": {"target_faces": "lots"}}}).status_code == 400
    # A refused start changes nothing: the autopilot is still idle, with no settings stored.
    st = client.get(f"/api/assets/{aid}/autopilot").json()
    assert st["status"] == "idle" and not st.get("stage_params")
