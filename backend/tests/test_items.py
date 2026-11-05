"""Items (vgac/items.py, vgac/blender/grip.py): characters made empty-handed, linked item assets, grip detection,
attachments and nudges, the item checks and the autopilot chain."""
from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import item_shapes as S  # noqa: E402

from vgac import guidance, items, sockets  # noqa: E402
from vgac.blender import grip  # noqa: E402
from vgac.db import Asset, Version, session_factory  # noqa: E402

RANGER = ("A stylized fantasy ranger for a video game, a lean young man with a short brown beard, green hooded cloak over "
          "worn leather armour, a short sword at his hip and a quiver of arrows on his back, tall boots, about 1.8 m tall.")


@pytest.fixture
def client(settings, monkeypatch):
    from vgac.main import app

    with TestClient(app) as c:
        yield c


# --- Half 1: words ---------------------------------------------------------------------------------------------


def test_detect_finds_carried_items_not_clothes():
    d = items.detect(RANGER, "humanoid")
    assert d["checked"]
    assert [(i["type"], i["phrase"]) for i in d["items"]] == [("sword", "a short sword"), ("quiver", "a quiver of arrows")]
    assert {"cloak", "armour", "boots"} <= set(d["worn"])
    assert all(i["type"] not in ("cloak", "armour") for i in d["items"])
    assert d["items"][0]["place"] == "In the right hand" and d["items"][1]["place"] == "On the back"


def test_detect_skips_negations_lookalikes_and_other_categories():
    assert items.detect("a wizard without a staff, in a blue robe and a bow tie", "humanoid")["items"] == []
    assert items.detect("a treasure chest with a sword on the lid", "prop_rigid") == {"checked": False, "items": [], "worn": []}
    # Arrows and a quiver are one quiver; a bow is its own item.
    d = items.detect("an archer with a bow, arrows and a quiver", "humanoid")
    assert sorted(i["type"] for i in d["items"]) == ["bow", "quiver"]
    assert [i["type"] for i in items.detect("a wolf with a saddle bag", "quadruped")["items"]] == ["bag"]


@pytest.mark.parametrize("text,expected", [
    (RANGER, "A stylized fantasy ranger for a video game, a lean young man with a short brown beard, green hooded cloak over "
             "worn leather armour, tall boots, about 1.8 m tall."),
    ("A knight holding a sword and shield, wearing plate armour", "A knight, wearing plate armour"),
    ("A dwarf with a big axe and a red beard", "A dwarf with a red beard"),
    ("An elf archer with a longbow, a quiver on her back and a dagger at her belt", "An elf archer"),
])
def test_strip_items_leaves_a_clean_description(text, expected):
    d = items.detect(text, "humanoid")
    assert items.strip_items(text, d["items"]) == expected


def test_item_prompt_and_size():
    assert items.item_prompt("sword", "a short sword") == "Short sword, a game item, about 0.63 m."
    assert items.item_size("sword", "a great sword") == pytest.approx(1.17)
    assert items.item_size("staff", "a staff") == 1.7


def test_item_words_are_served_from_the_one_data_file(client):
    r = client.get("/api/item-words").json()
    ids = {t["id"] for t in r["types"]}
    for word in ("sword", "axe", "mace", "dagger", "bow", "crossbow", "quiver", "shield", "staff", "wand", "spear",
                 "backpack", "bag", "satchel", "hat", "helmet", "crown", "lantern", "torch"):
        assert any(word in t["words"] for t in r["types"]), word
    assert "arrows" in next(t for t in r["types"] if t["id"] == "quiver")["words"]
    assert {"cloak", "armour", "boots", "belt"} <= set(r["worn_words"])
    assert "sword" in ids and "Clothes that bend" in r["help"]
    d = client.post("/api/items/detect", json={"prompt": "a knight with a sword", "category": "humanoid"}).json()
    assert d["items"][0]["type"] == "sword" and "separate item" in d["sentence"]


def _sc_for(asset, category="humanoid", pose="POSE BLOCK."):
    return SimpleNamespace(project=SimpleNamespace(style_lock={}), asset=asset, style={"prompt_block": "STYLE."},
                           category={"pose_block": pose})


def test_concept_prompt_is_empty_handed_and_items_get_their_own_pose():
    from vgac.stages.concept import build_prompt

    ch = SimpleNamespace(prompt=RANGER, category="humanoid", parts=[], held_item=None,
                         equipment={"choice": "separate", "set_aside": [{"type": "sword", "label": "sword"}, {"type": "quiver", "label": "quiver"}]})
    p = build_prompt(_sc_for(ch), {})
    assert "sword" not in p.lower() and "quiver" not in p.lower()
    assert "green hooded cloak" in p and p.endswith(items.data()["empty_hands"])
    parts = items.concept_prompt_parts(ch)
    assert "sword" in parts["negative"] and "quiver" in parts["negative"]
    # Kept on the body: the description is used as written.
    keep = SimpleNamespace(prompt=RANGER, category="humanoid", parts=[], held_item=None, equipment={"choice": "keep", "set_aside": []})
    assert "short sword" in build_prompt(_sc_for(keep), {})
    item = SimpleNamespace(prompt="Short sword, a game item, about 0.63 m.", category="prop_rigid", parts=[], equipment=None,
                           held_item={"type": "sword", "label": "sword"})
    p = build_prompt(_sc_for(item, pose="RIGID PROP POSE."), {})
    assert "RIGID PROP POSE" not in p and "The sword is shown alone, standing upright" in p


# --- grip detection ----------------------------------------------------------------------------------------------


def test_grip_on_a_sword_is_the_middle_of_the_handle_blade_up():
    r = grip.find(S.sword(), "blade")
    assert r["sure"] and r["method"] == "the middle of the handle"
    assert 0.02 < r["point"][1] < 0.13  # handle runs 0 to 0.15 m
    assert r["rotation"][:, 2] @ np.array([0, 1, 0]) > 0.99  # +Z toward the tip
    assert abs(r["rotation"][:, 1] @ np.array([1, 0, 0])) > 0.95  # +Y across the blade (its edges)
    R = r["rotation"]
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-6) and np.linalg.det(R) > 0


def test_grip_on_a_sword_painted_tip_down_still_points_to_the_tip():
    r = grip.find(S.sword(flip=True), "blade")
    assert r["sure"] and 0.77 < r["point"][1] < 0.88
    assert r["rotation"][:, 2] @ np.array([0, -1, 0]) > 0.99


def test_grip_on_staff_bow_and_shield():
    st = grip.find(S.staff(), "pole", grip_fraction=0.38)
    assert st["sure"] and st["point"][1] == pytest.approx(0.38 * 1.70, abs=0.06) and st["rotation"][1, 2] > 0.99
    bw = grip.find(S.bow(), "bow")
    assert bw["sure"] and abs(bw["point"][1] - 0.6) < 0.05
    assert bw["rotation"][:, 1] @ np.array([0, 0, 1]) > 0.9  # +Y toward the string
    sh = grip.find(S.shield(), "shield")
    assert sh["sure"] and sh["rotation"][:, 2] @ np.array([0, 0, 1]) > 0.95  # face toward the dome
    assert sh["point"][2] < 0.0  # on the back


def test_grip_on_a_blob_is_flagged_not_trusted():
    rng = np.random.default_rng(1)
    blob = rng.normal(size=(2000, 3)) * [0.1, 0.12, 0.1]
    r = grip.find(blob, "blade")
    assert not r["sure"] and r["reasons"]
    worn = grip.find(S.shield(), "worn")
    assert worn["sure"] and worn["point"][2] == pytest.approx(S.shield()[:, 2].min(), abs=1e-6)
    doc = grip.to_json(r)
    assert doc["threshold"] == grip.SURE and len(doc["rotation"]) == 4 and doc["sure"] is False


# --- attachment maths --------------------------------------------------------------------------------------------


def test_euler_quaternion_round_trip_and_mirroring():
    for deg in ([0, 0, 0], [10, 20, 30], [0, 90, 0], [-45, 5, 170]):
        assert np.allclose(items.quat_to_euler(items.euler_to_quat(deg)), deg, atol=0.05)
    assert items.default_rotation("quiver", "Socket_Back") == [0.0, 0.0, -20.0]
    t = items.item_type("quiver")
    assert t["socket"] == "Socket_Back"
    # A usual-on-the-right item moved to the left hand is mirrored.
    items.types()["_test"] = {"socket": "Socket_Hand_R", "rotation": [10, 20, 30]}
    try:
        assert items.default_rotation("_test", "Socket_Hand_L") == [10.0, -20.0, -30.0]
    finally:
        items.types().pop("_test")


def test_nudge_moves_in_the_characters_axes_whatever_the_socket_turn():
    # A socket turned a quarter about up: its forward is the character's left (+X).
    frame = np.column_stack([[0, 0, -1], [0, 1, 0], [1, 0, 0]]).astype(float)
    att = {"position": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": 1.0}
    a = items.nudge(att, frame, move=[1, 0, 0])  # one step to the character's left
    world = frame @ np.array(a["position"])
    assert np.allclose(world, [0.01, 0, 0], atol=1e-6)
    a = items.nudge(att, frame, move=[0, 1, 0], big=True)
    assert np.allclose(frame @ np.array(a["position"]), [0, 0.05, 0], atol=1e-6)
    # Turning about the character's up by 5 degrees turns the item's forward in the world by 5 degrees.
    a = items.nudge(att, frame, turn=[0, 1, 0])
    f_world = frame @ sockets.quat_to_matrix(a["rotation"]) @ np.array([0, 0, 1.0])
    assert math.degrees(math.acos(np.clip(f_world @ (frame @ [0, 0, 1.0]), -1, 1))) == pytest.approx(5, abs=0.01)
    # Slide: the hand moves toward the tip, so the item moves back along its own length.
    a = items.nudge(att, np.eye(3), slide=1)
    assert np.allclose(a["position"], [0, 0, -0.01])
    a = items.nudge({**att, "scale": 4.9}, np.eye(3), scale_steps=5)
    assert a["scale"] == items.SCALE_MAX


# --- checks ------------------------------------------------------------------------------------------------------


def test_grip_and_clearance_checks_speak_plainly():
    ok = items.grip_check({"found": True, "confidence": 0.8, "threshold": 0.6, "method": "the middle of the handle"}, "sword")
    assert ok["passed"] and ok["help"] == "The hand holds the sword by its handle (the middle of the handle)."
    bad = items.grip_check({"found": True, "confidence": 0.3, "threshold": 0.6}, "sword")
    assert not bad["passed"] and "guessed" in bad["help"] and bad["severity"] == "warn"
    sc = SimpleNamespace(asset=SimpleNamespace(held_item=None), version=SimpleNamespace(metrics={"equipped": {
        "items": [{"label": "sword", "grip": {"found": True, "confidence": 0.9, "threshold": 0.6}}],
        "clearance": {"measured": True, "worst_depth_m": 0.08, "worst_label": "sword", "worst_clip": "attack", "clips": ["attack", "walk"]}}}))
    checks = {c["name"]: c for c in items.export_checks(sc)}
    assert checks["grip_found"]["passed"]
    c = checks["item_clear_of_body"]
    assert not c["passed"] and c["help"] == ("The sword passes 8 cm into the body during attack. Move it under Add an item, "
                                             "or choose another place for it.")
    assert c["fix_label"] == "Fit it for me" and c["fix"]["params"] == {"fit_items": True}
    sc.version.metrics["equipped"]["clearance"] = {"measured": False}
    assert "item_clear_of_body" not in {c["name"] for c in items.export_checks(sc)}
    assert {"grip_found", "item_clear_of_body"} <= set(guidance.CHECK_GUIDE)


# --- API ---------------------------------------------------------------------------------------------------------


def _project(client):
    return client.post("/api/projects", json={"name": "Items"}).json()


def test_character_with_items_gets_linked_item_assets(client):
    p = _project(client)
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "Ranger", "category": "humanoid", "style_preset": "painted",
                                                             "platform_target": "mobile_low", "prompt": RANGER}).json()
    eq = a["equipment"]
    assert eq["choice"] == "separate" and [s["type"] for s in eq["set_aside"]] == ["sword", "quiver"]
    assert [x["socket"] for x in eq["attachments"]] == ["Socket_Hand_R", "Socket_Back"]
    assets = {x["name"]: x for x in client.get(f"/api/projects/{p['id']}/assets").json()}
    sword = assets["Ranger - sword"]
    assert sword["category"] == "prop_rigid" and sword["style_preset"] == "painted" and sword["platform_target"] == "mobile_low"
    assert sword["held_item"]["character_id"] == a["id"] and sword["prompt"].startswith("Short sword")
    # Created, not run.
    assert all(not vs for vs in sword["stages"].values())
    assert sword["autopilot"] is None
    # Keep them part of the body: nothing set aside, nothing made.
    k = client.post(f"/api/projects/{p['id']}/assets", json={"name": "Knight", "category": "humanoid", "style_preset": "painted",
                                                             "prompt": "a knight with a sword", "items": "keep"}).json()
    assert k["equipment"]["choice"] == "keep" and k["equipment"]["set_aside"] == []
    assert "Knight - sword" not in {x["name"] for x in client.get(f"/api/projects/{p['id']}/assets").json()}


def test_add_item_place_nudge_and_take_off(client):
    p = _project(client)
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "Hero", "category": "humanoid", "style_preset": "painted",
                                                             "prompt": "a hero in a red coat"}).json()
    assert a["equipment"] is None
    panel = client.get(f"/api/assets/{a['id']}/items").json()
    assert panel["can_add"] is False and "Finish the character first" in panel["reason"]
    r = client.post(f"/api/assets/{a['id']}/items", json={"description": "a round wooden shield", "build": False})
    assert r.status_code == 201
    att = r.json()["attachment"]
    assert att["type"] == "shield" and att["socket"] == "Socket_Forearm_L" and att["touched"] is False
    assert client.post(f"/api/assets/{a['id']}/items", json={"description": "something nice", "build": False}).status_code == 400
    aid, tid = a["id"], att["id"]
    r = client.post(f"/api/assets/{aid}/attachments/{tid}/nudge", json={"move": [0, 1, 0]}).json()["attachment"]
    assert np.linalg.norm(r["position"]) == pytest.approx(0.01)
    r = client.post(f"/api/assets/{aid}/attachments/{tid}/nudge", json={"scale_steps": -1}).json()["attachment"]
    assert r["scale"] == pytest.approx(1 / 1.05, abs=1e-3)
    r = client.post(f"/api/assets/{aid}/attachments/{tid}/nudge", json={"reset": True}).json()["attachment"]
    assert r["position"] == [0.0, 0.0, 0.0] and r["scale"] == 1.0
    r = client.put(f"/api/assets/{aid}/attachments/{tid}", json={"socket": "Socket_Back"}).json()["attachment"]
    assert r["socket"] == "Socket_Back"
    r = client.put(f"/api/assets/{aid}/attachments/{tid}", json={"position": [0.01, 0.02, 0.03], "rotation_deg": [0, 90, 0], "scale": 1.2}).json()["attachment"]
    assert r["position"] == [0.01, 0.02, 0.03] and r["rotation_deg"] == pytest.approx([0, 90, 0], abs=0.01) and r["scale"] == 1.2
    assert client.put(f"/api/assets/{aid}/attachments/{tid}", json={"socket": "Socket_Tail"}).status_code == 400
    assert client.post(f"/api/assets/{aid}/attachments/nope/nudge", json={}).status_code == 404
    assert client.post(f"/api/assets/{aid}/items/apply").status_code == 409
    gone = client.delete(f"/api/assets/{aid}/attachments/{tid}").json()
    assert gone["removed"]["id"] == tid and gone["panel"]["equipment"]["attachments"] == []
    # The item stays in the project and can be given back ("Use one you made").
    others = client.get(f"/api/assets/{aid}/items").json()["others"]
    assert [o["name"] for o in others] == ["Hero - shield"]
    r = client.post(f"/api/assets/{aid}/items", json={"item_asset_id": others[0]["id"]})
    assert r.status_code == 201
    assert client.post(f"/api/assets/{aid}/items", json={"item_asset_id": others[0]["id"]}).status_code == 409
    prop = client.post(f"/api/projects/{p['id']}/assets", json={"name": "Barrel", "category": "prop_rigid", "style_preset": "painted"}).json()
    assert client.post(f"/api/assets/{prop['id']}/items", json={"type": "sword"}).status_code == 400


def test_autopilot_builds_items_after_the_character_then_rebuilds_its_export(client, monkeypatch):
    from vgac import autopilot, pipeline

    p = _project(client)
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "Ranger", "category": "humanoid", "style_preset": "painted",
                                                             "prompt": RANGER}).json()
    started: list[str] = []
    monkeypatch.setattr(autopilot, "start", lambda aid, **k: started.append(aid) or {"status": "running"})
    submitted: list[tuple[str, str]] = []
    monkeypatch.setattr(pipeline, "submit", lambda db, asset, v, op, payload: submitted.append((v.stage, op)) or SimpleNamespace(id="job"))
    sword_id, quiver_id = (s["item_asset_id"] for s in a["equipment"]["set_aside"])
    # The character's autopilot is done: the first item is started.
    items.after_autopilot(a["id"])
    assert started == [sword_id]
    assert client.get(f"/api/assets/{a['id']}").json()["equipment"]["build"] == {"status": "items", "item": sword_id}

    def finish(aid, with_sockets=False):
        with session_factory()() as db:
            for stage in ("concept", "turnaround", "mesh", "prep", "rig", "export"):
                v = Version(asset_id=aid, stage=stage, status="done", selected=True)
                db.add(v)
            db.commit()

    finish(sword_id)
    items.after_autopilot(sword_id)  # the sword's own autopilot finished: on to the quiver
    assert started == [sword_id, quiver_id]
    finish(quiver_id)
    finish(a["id"])
    items.after_autopilot(quiver_id)  # both made: the character's export is rebuilt once
    assert submitted == [("export", "adjust")]
    assert client.get(f"/api/assets/{a['id']}").json()["equipment"]["build"]["status"] == "equipping"
