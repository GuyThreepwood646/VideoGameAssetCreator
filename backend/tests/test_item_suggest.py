"""WP-69 follow-up: the suggested worn item, as a job. The trigger rule (only a humanoid with a kept back-worn item on a
finished rig), that a finished rig queues one suggest job (and an ineligible or not-installed one queues none), that the
button retries a failed one and never a live or answered one, that a queued job which is gone reads as failed (a
restart or a cancel), dismiss and keep, the folder named by the full version id, and the placement helpers on a small
mesh. The renders and the detector are not run here (they need Blender and the models)."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from vgac import item_region, item_suggest


def _asset(category="humanoid", equipment=None, aid="a1"):
    return SimpleNamespace(id=aid, category=category, equipment=equipment)


def _version(stage="rig", status="done", vid="11111111-2222-3333-4444-555555555555"):
    return SimpleNamespace(id=vid, stage=stage, status=status)


KEEP_BACKPACK = {"choice": "keep", "found": [{"type": "backpack", "label": "backpack", "phrase": "a backpack"}]}
INSTALLED = SimpleNamespace(finder_python=None, finder_models=None)


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(item_suggest, "installed", lambda settings: True)
    return True


@pytest.fixture
def submitted(monkeypatch):
    """Replaces the queue: records each suggest job it is asked to queue, with a fake job id."""
    calls = []

    def fake_submit(asset_id, payload, message):
        calls.append({"asset_id": asset_id, "payload": payload, "message": message})
        return SimpleNamespace(id=f"job-{len(calls)}")

    monkeypatch.setattr(item_suggest, "_submit", fake_submit)
    return calls


@pytest.fixture
def live_jobs(monkeypatch):
    """The job ids the test says are still queued or running in the app."""
    live = set()
    monkeypatch.setattr(item_suggest, "_job_live", lambda job_id: job_id in live)
    return live


# --- the trigger rule ----------------------------------------------------------------------------------------------

def test_a_kept_back_item_makes_a_suggestion_for_a_finished_humanoid_rig():
    assert item_suggest.eligible(_asset(equipment=KEEP_BACKPACK), _version()) == [{"type": "backpack", "label": "backpack"}]


def test_only_kept_back_worn_items_count():
    assert item_suggest.eligible(_asset(equipment={"choice": "set_aside", "found": KEEP_BACKPACK["found"]}), _version()) == []
    assert item_suggest.eligible(_asset(equipment={"choice": "keep", "found": [{"type": "helmet"}]}), _version()) == []
    assert item_suggest.eligible(_asset(equipment=None), _version()) == []


def test_never_for_other_categories_or_unfinished_rigs():
    assert item_suggest.eligible(_asset(category="creature", equipment=KEEP_BACKPACK), _version()) == []
    assert item_suggest.eligible(_asset(equipment=KEEP_BACKPACK), _version(stage="mesh")) == []
    assert item_suggest.eligible(_asset(equipment=KEEP_BACKPACK), _version(status="running")) == []


def test_not_installed_until_the_python_and_both_models_are_there(tmp_path):
    settings = SimpleNamespace(finder_python=tmp_path / "py.exe", finder_models=tmp_path / "hf")
    assert item_suggest.installed(settings) is False
    (tmp_path / "py.exe").write_text("", encoding="utf-8")
    assert item_suggest.installed(settings) is False
    hub = tmp_path / "hf" / "hub"
    (hub / "models--IDEA-Research--grounding-dino-tiny").mkdir(parents=True)
    assert item_suggest.installed(settings) is False
    (hub / "models--facebook--sam2.1-hiera-small").mkdir()
    assert item_suggest.installed(settings) is True


# --- a finished rig queues one suggest job ------------------------------------------------------------------------

def test_a_finished_rig_of_an_eligible_asset_queues_one_suggest_job(tmp_path, installed, submitted):
    job = item_suggest.maybe_enqueue_after_rig(INSTALLED, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path)
    assert job is not None
    assert len(submitted) == 1
    assert submitted[0]["asset_id"] == "a1"
    assert submitted[0]["payload"]["version_id"] == "11111111-2222-3333-4444-555555555555"
    assert submitted[0]["payload"]["item"] == {"type": "backpack", "label": "backpack"}
    state = json.loads((tmp_path / item_suggest.STATE_FILE).read_text(encoding="utf-8"))
    assert state["state"] == "queued" and state["job_id"] == "job-1"


def test_an_ineligible_asset_or_no_finder_queues_none(tmp_path, installed, submitted, monkeypatch):
    assert item_suggest.maybe_enqueue_after_rig(INSTALLED, _asset(category="creature", equipment=KEEP_BACKPACK), _version(), tmp_path) is None
    assert item_suggest.maybe_enqueue_after_rig(INSTALLED, _asset(equipment={"choice": "set_aside"}), _version(), tmp_path) is None
    monkeypatch.setattr(item_suggest, "installed", lambda settings: False)
    assert item_suggest.maybe_enqueue_after_rig(INSTALLED, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path) is None
    assert submitted == []
    assert not (tmp_path / item_suggest.STATE_FILE).exists()


def test_a_rig_with_a_suggestion_already_queues_no_second_job(tmp_path, installed, submitted):
    item_suggest.maybe_enqueue_after_rig(INSTALLED, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path)
    assert item_suggest.maybe_enqueue_after_rig(INSTALLED, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path) is None
    assert len(submitted) == 1


# --- the button ----------------------------------------------------------------------------------------------------

def test_the_button_retries_a_failed_one(tmp_path, installed, submitted, live_jobs):
    item_suggest.write_state(tmp_path, {"state": "failed", "item": "backpack", "label": "backpack", "reason": "boom"})
    got = item_suggest.request(INSTALLED, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path)
    assert got["state"] == "queued" and got["job_id"] == "job-1"
    assert len(submitted) == 1


def test_the_button_does_not_queue_a_live_or_answered_suggestion(tmp_path, installed, submitted, live_jobs):
    item_suggest.write_state(tmp_path, {"state": "queued", "job_id": "live-1", "item": "backpack"})
    live_jobs.add("live-1")
    assert item_suggest.request(INSTALLED, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path)["state"] == "queued"
    for answered in ("done", "kept", "dismissed"):
        item_suggest.write_state(tmp_path, {"state": answered, "item": "backpack", "job_id": "old"})
        assert item_suggest.request(INSTALLED, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path)["state"] == answered
    assert submitted == []


def test_the_button_queues_nothing_when_not_eligible_or_not_installed(tmp_path, installed, submitted, monkeypatch):
    assert item_suggest.request(INSTALLED, _asset(equipment=None), _version(), tmp_path) == {"state": "none"}
    monkeypatch.setattr(item_suggest, "installed", lambda settings: False)
    assert item_suggest.request(INSTALLED, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path) == {"state": "none"}
    assert submitted == []


def test_a_queued_job_that_is_gone_reads_as_failed_so_it_can_be_retried(tmp_path, live_jobs):
    item_suggest.write_state(tmp_path, {"state": "running", "job_id": "gone-1", "item": "backpack"})
    got = item_suggest.current_state(tmp_path)
    assert got["state"] == "failed" and "try again" in got["reason"]
    live_jobs.add("still-1")
    item_suggest.write_state(tmp_path, {"state": "queued", "job_id": "still-1", "item": "backpack"})
    assert item_suggest.current_state(tmp_path)["state"] == "queued"


# --- dismiss, keep, public state -----------------------------------------------------------------------------------

def test_dismiss_and_keep_are_remembered_for_the_rig_version(tmp_path):
    item_suggest.write_state(tmp_path, {"state": "done", "item": "backpack", "label": "backpack", "points": [[0, 1, 0]]})
    assert item_suggest.set_state(tmp_path, "dismissed")["state"] == "dismissed"
    assert item_suggest.read_state(tmp_path)["state"] == "dismissed"
    item_suggest.set_state(tmp_path, "kept")
    assert item_suggest.read_state(tmp_path)["state"] == "kept"


def test_public_state_hides_the_region_list():
    got = item_suggest.public({"state": "done", "region": [1, 2, 3], "points": [[0, 1, 0]]})
    assert "region" not in got and got["points"] == [[0, 1, 0]]
    assert item_suggest.public(None) is None


# --- the folder and the placement helpers ---------------------------------------------------------------------------

def test_the_renders_folder_is_named_by_the_full_version_id():
    a = item_suggest.work_dir("11111111-2222-3333-4444-555555555555")
    b = item_suggest.work_dir("11111111-2222-3333-4444-555555555556")
    assert a.name == "sg-11111111-2222-3333-4444-555555555555"
    assert a != b


def test_votes_keep_polygons_seen_by_enough_views_and_vertices_with_most_of_them():
    polys = [(0, 1, 2), (1, 2, 3), (2, 3, 4)]
    votes = np.array([2, 1, 0])
    v = item_region.vote_vertices(polys, votes, 5, min_votes=2)
    # polygon 0 has 2 votes (kept), polygon 1 has 1 (not kept), polygon 2 has none. Vertex 0 is in polygon 0 only (kept),
    # vertex 1 in polygons 0 and 1 (one of two kept: half), vertex 2 in all three (one of three: not half), vertex 4 in
    # polygon 2 only (not kept)
    assert v[0] and v[1]
    assert not v[2] and not v[4]


def test_the_stored_points_are_in_the_gltf_frame():
    # Blender (x, y, z) to glTF (x, z, -y): the inverse of worn_region.gltf_to_blender
    assert item_region.to_gltf([0.5, 0.25, 1.1]) == [0.5, 1.1, -0.25]


def test_frame_says_so_when_the_skeleton_is_missing_parts():
    fr = item_region.frame(["mixamorig:Hips"], [[0, 0, 0.9]], np.zeros((3, 3)))
    assert fr["ok"] is False and "reason" in fr


def test_expected_place_is_behind_the_spine_and_between_hips_and_top():
    fr = {"ok": True, "H": 1.8, "hips_z": 0.9, "chest_y": -0.06, "S": 0.3}
    co = np.array([[0.0, 0.2, 1.2],    # behind the spine, in place
                   [0.0, -0.2, 1.2],   # in front
                   [0.0, 0.2, 0.2],    # too low
                   [0.9, 0.2, 1.2]])   # too far to the side
    assert item_region.expected_place(co, fr).tolist() == [True, False, False, False]
