"""WP-69 follow-up: the suggested worn item. The trigger rule (only a humanoid with a kept back-worn item on a finished rig),
dismiss and keep (no further suggestion for that rig version), the not-installed case (no suggestion, no error), and the
placement helpers on a small mesh. The renders and the detector are not run here (they need Blender and the models)."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from vgac import item_region, item_suggest


def _asset(category="humanoid", equipment=None):
    return SimpleNamespace(category=category, equipment=equipment)


def _version(stage="rig", status="done"):
    return SimpleNamespace(id="v1", stage=stage, status=status)


KEEP_BACKPACK = {"choice": "keep", "found": [{"type": "backpack", "label": "backpack", "phrase": "a backpack"}]}


def test_a_kept_back_item_makes_a_suggestion_for_a_finished_humanoid_rig():
    got = item_suggest.eligible(_asset(equipment=KEEP_BACKPACK), _version())
    assert got == [{"type": "backpack", "label": "backpack"}]


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


def test_dismiss_and_keep_are_remembered_for_the_rig_version(tmp_path):
    item_suggest.write_state(tmp_path, {"state": "done", "item": "backpack", "label": "backpack", "points": [[0, 1, 0]]})
    assert item_suggest.set_state(tmp_path, "dismissed")["state"] == "dismissed"
    assert item_suggest.read_state(tmp_path)["state"] == "dismissed"
    item_suggest.set_state(tmp_path, "kept")
    assert item_suggest.read_state(tmp_path)["state"] == "kept"


def test_a_dismissed_suggestion_is_not_run_again(tmp_path, monkeypatch):
    item_suggest.write_state(tmp_path, {"state": "dismissed", "item": "backpack"})
    settings = SimpleNamespace(finder_python=tmp_path / "py.exe", finder_models=tmp_path / "hf")
    monkeypatch.setattr(item_suggest, "compute", lambda *a, **k: pytest.fail("a dismissed suggestion must not run"))
    got = item_suggest.start(settings, _asset(equipment=KEEP_BACKPACK), _version(), tmp_path, tmp_path / "rig.glb")
    assert got["state"] == "dismissed"


def test_public_state_hides_the_region_list():
    got = item_suggest.public({"state": "done", "region": [1, 2, 3], "points": [[0, 1, 0]]})
    assert "region" not in got and got["points"] == [[0, 1, 0]]
    assert item_suggest.public(None) is None


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
