"""Declared parts (WP-20): the structured entries, the placed locations on a rig version, the endpoints, and the
declared-point search in blender/springs.py (find_parts with declared points, on the synthetic character of
test_springs.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vgac" / "blender"))
import springs as sp  # noqa: E402

from vgac import declared_parts as dp  # noqa: E402
from test_springs import PONYTAIL, character, tube  # noqa: E402


# --- the rules ---------------------------------------------------------------------------------------------------------


def test_normalize_gives_name_and_kind_and_refuses_the_label_form():
    assert dp.normalize([{"name": "  Cape  hem ", "kind": "sway"}], "humanoid") == [{"name": "Cape hem", "kind": "sway"}]
    assert dp.normalize([{"name": "lid"}], "prop_articulated") == [{"name": "lid", "kind": "hinge"}]
    assert dp.normalize([{"name": "belt"}], "humanoid") == [{"name": "belt", "kind": "separate"}]
    with pytest.raises(dp.PartError, match="kind now"):
        dp.normalize(["lid"], "prop_articulated")


def test_sway_is_for_humanoids_only_and_names_are_unique():
    with pytest.raises(dp.PartError, match="only for humanoid"):
        dp.normalize([{"name": "tail", "kind": "sway"}], "quadruped")
    with pytest.raises(dp.PartError, match="listed twice"):
        dp.normalize([{"name": "Cape", "kind": "sway"}, {"name": "cape", "kind": "sway"}], "humanoid")
    with pytest.raises(dp.PartError, match="Unknown part kind"):
        dp.normalize([{"name": "cape", "kind": "flap"}], "humanoid")


def test_location_is_three_metres_and_bounded():
    assert dp.clean_location(None) is None
    assert dp.clean_location([0.1, 0.25, -0.3333333]) == [0.1, 0.25, -0.3333]
    for bad in ([1, 2], [1, 2, "x"], [1, 2, float("nan")], [1, 2, 25], [True, 0, 0]):
        with pytest.raises(dp.PartError):
            dp.clean_location(bad)


def test_placed_must_match_a_declared_part_with_the_same_kind():
    declared = [{"name": "Cape", "kind": "sway"}, {"name": "belt", "kind": "separate"}]
    rows = dp.placed([{"name": "cape", "kind": "sway", "location": [0, 0.9, 0.1]}], declared, "humanoid")
    assert rows == [{"name": "cape", "kind": "sway", "location": [0, 0.9, 0.1], "locations": []}]
    with pytest.raises(dp.PartError, match="not a declared part"):
        dp.placed([{"name": "hat", "kind": "sway"}], declared, "humanoid")
    with pytest.raises(dp.PartError, match="declared as sway"):
        dp.placed([{"name": "cape", "kind": "separate"}], declared, "humanoid")


def test_only_sway_parts_with_a_location_reach_the_spring_pass():
    rows = [{"name": "cape", "kind": "sway", "location": [0, 0.9, 0.1]},
            {"name": "sash", "kind": "sway", "location": None},
            {"name": "belt", "kind": "separate", "location": [0, 1, 0]}]
    assert dp.for_springs(rows) == [{"name": "cape", "kind": "sway", "location": [0, 0.9, 0.1]}]


def test_stored_label_rows_read_as_no_parts():
    assert dp.read_stored(["lid", {"name": "cape", "kind": "sway"}]) == [{"name": "cape", "kind": "sway"}]


# --- the endpoints -----------------------------------------------------------------------------------------------------


@pytest.fixture
def api(settings):
    from vgac.db import Asset, Project, Version, session_factory
    from vgac.main import app

    with TestClient(app) as client:
        with session_factory()() as db:
            p = Project(name="p")
            db.add(p)
            db.flush()
            a = Asset(project_id=p.id, name="mage", category="humanoid", parts_json=json.dumps(
                [{"name": "cape", "kind": "sway"}, {"name": "belt", "kind": "separate"}]))
            db.add(a)
            db.flush()
            rig = Version(asset_id=a.id, stage="rig", status="done")
            mesh = Version(asset_id=a.id, stage="mesh", status="done")
            db.add_all([rig, mesh])
            db.flush()
            again = Version(asset_id=a.id, stage="rig", status="done", parent_version_id=rig.id)
            db.add(again)
            db.flush()
            ids = {"asset": a.id, "rig": rig.id, "mesh": mesh.id, "again": again.id}
            db.commit()
        yield client, ids


def test_asset_parts_are_structured_on_create_and_refused_as_labels(api):
    client, ids = api
    body = {"name": "Imp", "category": "humanoid", "style_preset": "painted", "prompt": "a small imp",
            "parts": [{"name": "tail", "kind": "sway"}]}
    r = client.post(f"/api/projects/{client.get('/api/projects').json()[0]['id']}/assets", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["parts"] == [{"name": "tail", "kind": "sway"}]
    bad = dict(body, parts=["tail"])
    r = client.post(f"/api/projects/{client.get('/api/projects').json()[0]['id']}/assets", json=bad)
    assert r.status_code == 422
    r = client.put(f"/api/assets/{ids['asset']}", json={"parts": [{"name": "tusk", "kind": "sway"}, {"name": "x", "kind": "bad"}]})
    assert r.status_code == 400 and "Unknown part kind" in r.text


def test_get_and_put_a_rig_versions_placed_parts(api):
    client, ids = api
    got = client.get(f"/api/versions/{ids['rig']}/parts").json()
    assert got["applies_to_rig"] is True
    assert got["parts"] == [{"name": "cape", "kind": "sway", "location": None, "locations": []},
                            {"name": "belt", "kind": "separate", "location": None, "locations": []}]
    r = client.put(f"/api/versions/{ids['rig']}/parts",
                   json={"parts": [{"name": "cape", "kind": "sway", "location": [0.0, 0.85, 0.12]}]})
    assert r.status_code == 200, r.text
    assert r.json()["parts"][0]["location"] == [0.0, 0.85, 0.12]
    assert client.get(f"/api/versions/{ids['rig']}/parts").json()["parts"][0]["location"] == [0.0, 0.85, 0.12]


def test_a_rig_made_again_shows_the_points_it_was_made_from_until_it_has_its_own(api):
    """The rig step reads the points of the rig it was made from (stages/rig.py stiff_parts_path), so the page shows those."""
    client, ids = api
    assert client.get(f"/api/versions/{ids['again']}/parts").json()["parts"][0]["location"] is None
    client.put(f"/api/versions/{ids['rig']}/parts", json={"parts": [{"name": "cape", "kind": "sway", "location": [0.0, 0.85, 0.12]}]})
    assert client.get(f"/api/versions/{ids['again']}/parts").json()["parts"][0]["location"] == [0.0, 0.85, 0.12]
    client.put(f"/api/versions/{ids['again']}/parts", json={"parts": [{"name": "cape", "kind": "sway", "location": [0.0, 0.9, 0.1]}]})
    assert client.get(f"/api/versions/{ids['again']}/parts").json()["parts"][0]["location"] == [0.0, 0.9, 0.1]
    assert client.get(f"/api/versions/{ids['rig']}/parts").json()["parts"][0]["location"] == [0.0, 0.85, 0.12]


def test_put_refuses_a_non_rig_version_an_unknown_part_and_a_bad_point(api):
    client, ids = api
    r = client.put(f"/api/versions/{ids['mesh']}/parts", json={"parts": []})
    assert r.status_code == 400
    r = client.put(f"/api/versions/{ids['rig']}/parts", json={"parts": [{"name": "hat", "kind": "sway"}]})
    assert r.status_code == 400 and "not a declared part" in r.text
    r = client.put(f"/api/versions/{ids['rig']}/parts", json={"parts": [{"name": "cape", "kind": "sway", "location": [0, 1]}]})
    assert r.status_code == 400


# --- the declared-point search (blender/springs.py find_parts) ---------------------------------------------------------

# A cloth band from the waist to the knee, joined to the body at both ends: the search leaves it alone ("both_ends").
HEM = [(0.0, -0.02, 1.0), (0.0, -0.02, 0.7), (0.0, -0.02, 0.5)]  # front of the hips, down to the thighs


# A cape-like strip behind the legs, clear of the body the whole way: nothing joins it, so the search calls it loose.
LOOSE = [(0.0, 0.25, 0.95), (0.0, 0.25, 0.7), (0.0, 0.25, 0.5)]


@pytest.fixture(scope="module")
def hem_case():
    band = tube(HEM, 0.05)
    V, F, bones, W = character(band)
    hair = np.zeros(len(V), bool)
    return V, F, bones, W, hair


@pytest.fixture(scope="module")
def loose_case():
    strip = tube(LOOSE, 0.04)
    V, F, bones, W = character(strip)
    hair = np.zeros(len(V), bool)
    return V, F, bones, W, hair


def test_the_search_leaves_a_cloth_band_joined_at_both_ends_alone(hem_case):
    V, F, bones, W, hair = hem_case
    found = sp.find_parts(V, F, bones, W, hair, None)
    assert found["parts"] == []
    assert found["declared"] == []


def test_the_search_leaves_a_loose_strip_alone_and_a_declared_point_adds_it(loose_case):
    V, F, bones, W, hair = loose_case
    assert sp.find_parts(V, F, bones, W, hair, None)["parts"] == []
    P = [0.0, 0.25, 0.75]  # Blender world space, on the strip's middle
    found = sp.find_parts(V, F, bones, W, hair, None, declared=[{"name": "cape", "kind": "sway", "location": [0, 0.75, -0.25], "P": P}])
    rows = found["declared"]
    assert rows[0]["state"] == "added" and rows[0]["name"] == "cape"
    assert len(found["parts"]) == 1
    p = found["parts"][0]
    assert p["declared"] is True and p["declared_name"] == "cape"
    assert p["kind"] in ("Cloth", "Cape")
    assert p["parent"] in {b["name"] for b in bones}
    col = np.array(p["columns"][0])
    assert col[0][2] > col[-1][2]  # root at the top of the region, tip below


def test_a_declared_point_on_a_part_the_search_already_found_is_reported_not_repeated():
    pt = tube(PONYTAIL, 0.03)
    V, F, bones, W = character(pt)
    hair = np.zeros(len(V), bool)
    hair[len(V) - len(pt[0]):] = True
    hair[V[:, 2] > 1.72] = True
    P = list(PONYTAIL[1])  # on the ponytail, which the search finds by itself
    found = sp.find_parts(V, F, bones, W, hair, None, declared=[{"name": "ponytail", "kind": "sway", "location": None, "P": P}])
    assert found["declared"][0]["state"] == "found"
    assert len(found["parts"]) == 1 and found["parts"][0].get("declared") is False


def test_a_declared_point_inside_the_body_adds_nothing(hem_case):
    V, F, bones, W, hair = hem_case
    found = sp.find_parts(V, F, bones, W, hair, None, declared=[{"name": "x", "kind": "sway", "location": None, "P": [0, 0, 1.2]}])
    assert found["declared"][0]["state"] == "not_added"
    assert found["parts"] == []


def test_a_declared_point_on_cloth_joined_to_the_body_is_not_given_a_chain(hem_case):
    V, F, bones, W, hair = hem_case
    found = sp.find_parts(V, F, bones, W, hair, None, declared=[{"name": "cape", "kind": "sway", "location": None, "P": [0.0, -0.02, 0.8]}])
    assert found["declared"][0]["state"] == "not_added" and found["parts"] == []


def test_the_declared_chain_is_kept_when_the_bone_cap_would_drop_the_others(loose_case):
    V, F, bones, W, hair = loose_case
    decl = [{"name": "cape", "kind": "sway", "location": None, "P": [0.0, 0.25, 0.75]}]
    found = sp.find_parts(V, F, bones, W, hair, None, declared=decl, max_bones=4)
    assert found["parts"] and found["parts"][0].get("declared_name") == "cape"


def test_declared_points_convert_from_the_rig_frame():
    # blender/springs.py converts a glTF point (y up) to Blender (z up): (x, y, z) -> (x, -z, y)
    assert sp.gltf_to_blender([[0.1, 0.9, 0.2]]).tolist() == [[0.1, -0.2, 0.9]]


def test_blender_args_pass_the_declared_file(tmp_path):
    from vgac import springs as springs_mod

    d = tmp_path / "declared.json"
    d.write_text("{}", encoding="utf-8")
    args = springs_mod.blender_args(tmp_path / "a.glb", tmp_path / "o.glb", tmp_path, None, None, None, None, declared=d)
    assert "--declared" in args and str(d) in [str(a) for a in args]
    plain = springs_mod.blender_args(tmp_path / "a.glb", tmp_path / "o.glb", tmp_path, None, None, None, None)
    assert "--declared" not in plain


# --- moving parts (WP-29) ------------------------------------------------------------------------------------------


def test_a_moving_kind_comes_from_the_name_on_objects_only():
    assert dp.kind_from_name("Front wheels", "vehicle") == "spin"
    assert dp.kind_from_name("top drawer", "prop_rigid") == "slide"
    assert dp.kind_from_name("Lid", "prop_articulated") == "hinge"
    assert dp.kind_from_name("door", "environment") == "hinge"
    assert dp.kind_from_name("turret", "vehicle") == "separate"
    assert dp.kind_from_name("wheel", "humanoid") == "separate"  # people have no moving kinds
    assert dp.kind_from_name("lid", "foliage") == "separate"


def test_moving_kinds_are_refused_on_characters_and_stiff_on_objects():
    with pytest.raises(dp.PartError, match="no skeleton"):
        dp.normalize([{"name": "lid", "kind": "hinge"}], "humanoid")
    with pytest.raises(dp.PartError, match="only for humanoid"):
        dp.normalize([{"name": "pack", "kind": "stiff"}], "vehicle")


def test_a_placed_moving_part_keeps_points_and_settings_with_defaults():
    declared = [{"name": "lid", "kind": "hinge"}, {"name": "door", "kind": "hinge"}, {"name": "drawer", "kind": "slide"},
                {"name": "wheels", "kind": "spin"}]
    rows = dp.placed([{"name": "lid", "locations": [[0, 0.8, 0]]}, {"name": "door", "locations": [[0, 1, 0.3]], "side": "left"},
                      {"name": "drawer", "locations": [[0, 0.3, 0.3]], "distance": 0.5},
                      {"name": "wheels", "locations": [[1, 0.3, 1], [-1, 0.3, 1]], "open_at_rest": True}], declared, "vehicle")
    lid, door, drawer, wheels = rows
    assert lid == {"name": "lid", "kind": "hinge", "location": None, "locations": [[0, 0.8, 0]], "side": "auto",
                   "open_at_rest": False, "angle": 100.0}
    assert door["angle"] == 90.0 and door["side"] == "left"
    assert drawer["distance"] == 0.5 and "angle" not in drawer
    assert wheels["open_at_rest"] is True and "angle" not in wheels and "distance" not in wheels
    assert [r["name"] for r in dp.for_moving(rows)] == ["lid", "door", "drawer", "wheels"]
    assert dp.for_moving(rows)[0] == {"name": "lid", "kind": "hinge", "locations": [[0, 0.8, 0]], "side": "auto",
                                      "open_at_rest": False, "angle": 100.0}


def test_moving_settings_are_checked():
    declared = [{"name": "lid", "kind": "hinge"}, {"name": "drawer", "kind": "slide"}]
    for bad, match in (({"side": "under"}, "hinge side"), ({"angle": 200}, "opening angle"), ({"angle": True}, "opening angle")):
        with pytest.raises(dp.PartError, match=match):
            dp.placed([{"name": "lid", "locations": [[0, 0, 0]], **bad}], declared, "prop_articulated")
    with pytest.raises(dp.PartError, match="sliding distance"):
        dp.placed([{"name": "drawer", "locations": [[0, 0, 0]], "distance": 1.5}], declared, "prop_articulated")
    with pytest.raises(dp.PartError, match="at most 8"):
        dp.placed([{"name": "lid", "locations": [[0, 0, 0]] * 9}], declared, "prop_articulated")


def test_unplaced_moving_parts_are_named_and_the_view_shows_settings():
    declared = [{"name": "lid", "kind": "hinge"}, {"name": "wheels", "kind": "spin"}, {"name": "sign", "kind": "separate"}]
    rows = dp.placed([{"name": "lid", "locations": [[0, 1, 0]]}], declared, "prop_articulated")
    assert dp.unplaced_moving(declared, rows) == ["wheels"]
    v = dp.view(declared, rows, True)["parts"]
    assert v[0]["angle"] == 100.0 and v[0]["side"] == "auto"
    assert v[1]["locations"] == [] and v[1]["side"] == "auto" and "angle" not in v[1]
    assert "side" not in v[2]


# --- the item's own moving part (F15): a gate is not a chest's lid -------------------------------------------------------


def test_item_default_names_the_moving_part_from_the_item():
    assert dp.item_defaults("prop_articulated", "Garden gate", "A wooden garden gate", []) == [
        {"name": "gate leaf", "kind": "hinge"}]
    assert dp.item_defaults("prop_articulated", "Treasure chest", "A chest with iron bands", []) == [
        {"name": "lid", "kind": "hinge"}]
    assert dp.item_defaults("prop_articulated", "Old wardrobe", "A tall wardrobe with a door", []) == [
        {"name": "door", "kind": "hinge"}]


def test_item_default_for_a_wheelbarrow_is_a_wheel_not_doors():
    assert dp.item_defaults("vehicle", "Wheelbarrow", "A garden wheelbarrow", ["wheels"]) == [
        {"name": "wheel", "kind": "spin"}]


def test_item_default_falls_back_to_the_category_and_is_empty_for_an_unnamed_object():
    assert dp.item_defaults("vehicle", "Old van", "A van", ["wheels"]) == [{"name": "wheels", "kind": "spin"}]
    assert dp.item_defaults("prop_articulated", "Mystery box", "A plain box", []) == []


def test_category_defaults_no_longer_name_a_lid_or_doors():
    from vgac import reference

    assert reference.category("prop_articulated").get("parts_default") == []
    assert reference.category("vehicle").get("parts_default") == ["wheels"]
