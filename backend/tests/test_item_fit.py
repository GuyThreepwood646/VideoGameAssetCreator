"""Fit an item to the motion (vgac/blender/itemfit.py, the search equip.py runs in Blender), the rule that a placement
the person adjusted by hand is never fitted unless they ask, the shield's move to the forearm, and the item checks
added with it (item_above_ground, items_apart)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from fastapi.testclient import TestClient

from vgac import guidance, items
from vgac.blender import itemfit


@pytest.fixture
def client(settings, monkeypatch):
    from vgac.main import app

    with TestClient(app) as c:
        yield c


# --- the search on simple shapes ----------------------------------------------------------------------------------


def _plate(radius=0.2, n=9):
    """A flat round plate in the item's own frame: face +Z, back face at z 0 (the pivot), 1 cm thick."""
    g = np.linspace(-radius, radius, n)
    P = [(x, y, z) for x in g for y in g for z in (0.0, 0.01) if x * x + y * y <= radius * radius]
    return np.array(P)


def _capsule_depth(P, centre_z=-0.06, r=0.05, half=0.3):
    """An arm: a capsule along the point's Y axis, centre line at x 0, z centre_z. -> how deep each point is inside."""
    y = np.clip(P[:, 1], -half, half)
    d = np.sqrt(P[:, 0] ** 2 + (P[:, 1] - y) ** 2 + (P[:, 2] - centre_z) ** 2)
    return np.maximum(0.0, r - d)


def _worst(depths, k=3):
    d = np.sort(depths)[::-1]
    return float(d[k - 1]) if len(d) >= k else 0.0


def _depth_fn(P, pos0, R0, outward, shape):
    def depth(c, stop_above):
        pos, R = (np.asarray(pos0, float), np.asarray(R0, float)) if c is None else itemfit.apply(c, pos0, R0, outward)
        return _worst(shape((R @ P.T).T + pos))
    return depth


def test_a_plate_sunk_into_an_arm_ends_outside_with_the_least_push():
    P = _plate()
    pos0, R0, out = [0.0, 0.0, -0.03], np.eye(3), [0.0, 0.0, 1.0]
    depth = _depth_fn(P, pos0, R0, out, _capsule_depth)
    limit = 0.012
    assert depth(None, None) > limit  # it starts 2 cm inside the arm's surface
    cands = itemfit.candidates(False, out)
    res = itemfit.search(cands, depth, limit)
    assert res["cleared"] and res["changed"] and res["depth"] <= limit
    # The least change: a 1 cm push (2 cm inside, 1.2 cm allowed, in half-centimetre steps), no tilt; half a centimetre is not enough.
    assert res["cand"]["outward_cm"] == 1 and res["cand"]["tilt_deg"] == [0, 0]
    half = next(c for c in cands if c["outward_cm"] == 0.5 and c["tilt_deg"] == [0, 0])
    assert depth(half, None) > limit
    # Nothing cheaper clears.
    assert all(depth(c, None) > limit for c in cands if c["cost"] < res["cand"]["cost"])
    pos, _ = itemfit.apply(res["cand"], pos0, R0, out)
    assert pos == pytest.approx([0.0, 0.0, -0.02])


def test_pushes_and_tilts_stay_within_their_bounds():
    for c in itemfit.candidates(False, [1.0, 0.0, 0.0]):
        assert 0 <= c["outward_cm"] <= itemfit.PUSH_MAX_CM and all(abs(t) <= itemfit.TILT_MAX_DEG for t in c["tilt_deg"])
        pos, R = itemfit.apply(c, [0.0, 0.0, 0.0], np.eye(3), [1.0, 0.0, 0.0])
        assert pos[1] == pytest.approx(0) and pos[2] == pytest.approx(0)  # pushed only along the outward axis
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-9)
    costs = [c["cost"] for c in itemfit.candidates(False)]
    assert costs == sorted(costs) and costs[0] == 0


def test_a_held_item_keeps_its_grip_in_the_palm():
    """A blade along +X from the palm runs into a body block to that side; only turns about the grip and small tilts
    are tried, the grip point never moves, and the least turn that clears is kept."""
    blade = np.array([[d, 0.0, 0.0] for d in np.linspace(0.0, 0.6, 61)])

    def block(Q):
        inside = (Q[:, 0] > 0.2) & (np.abs(Q[:, 1]) < 0.1)
        return np.where(inside, np.minimum(Q[:, 0] - 0.2, 0.1 - np.abs(Q[:, 1])), 0.0)

    cands = itemfit.candidates(True)
    for c in cands:
        assert c["outward_cm"] == 0
        pos, _ = itemfit.apply(c, [0.0, 0.0, 0.0], np.eye(3))
        assert np.allclose(pos, 0.0)  # the grip stays in the palm
    depth = _depth_fn(blade, [0.0, 0.0, 0.0], np.eye(3), [0, 0, 1], block)
    res = itemfit.search(cands, depth, 0.01)
    assert res["cleared"] and res["cand"]["turn_deg"] != 0
    clearing = [c for c in cands if depth(c, None) <= 0.01]
    assert res["cand"]["cost"] == min(c["cost"] for c in clearing)


def _thin_limb_eval(Q, cz=-0.045, r=0.025, half=0.3, escape=True):
    """The check's measure (itemfit.point_depth and frame_score) on a thin limb: a capsule of radius r along Y, centre
    line at z cz; the push-out direction is +Z. escape False is the old measure (nearest surface only)."""
    y = np.clip(Q[:, 1], -half, half)
    d = np.sqrt(Q[:, 0] ** 2 + (Q[:, 1] - y) ** 2 + (Q[:, 2] - cz) ** 2)
    inside = d < r
    depths = []
    for q, di in zip(Q[inside], d[inside]):
        # Along +Z the point leaves the tube where z reaches the top of the circle at its x.
        out = cz + np.sqrt(max(r * r - q[0] ** 2, 0.0)) - q[2] if escape else None
        depths.append(itemfit.point_depth(r - di, out))
    return itemfit.frame_score(depths, len(Q), items.CLEAR_LIMIT_M, items.CLEAR_SHARE_LIMIT)


def test_a_plate_through_a_thin_limb_fails_where_it_used_to_pass():
    """A shield pushed through a forearm 5 cm thick: the nearest surface is never more than 2.5 cm away, so the old
    measure passed it; the way back out along the push direction is 4 cm, and a large part of it is inside."""
    P = _plate()
    through = P + [0.0, 0.0, -0.06]  # back face 4 cm below the top of the limb, front face 3 cm below
    old = _thin_limb_eval(through, escape=False)
    assert old["depth"] <= items.CLEAR_LIMIT_M  # what the check used to see
    new = _thin_limb_eval(through)
    assert new["depth"] == pytest.approx(0.04, abs=1e-6) and new["score"] > items.CLEAR_LIMIT_M
    assert new["share"] > items.CLEAR_SHARE_LIMIT
    # Just outside: nothing inside, it passes.
    clear = _thin_limb_eval(P + [0.0, 0.0, -0.015])
    assert clear == {"depth": 0.0, "share": 0.0, "score": 0.0}
    assert items.CLEAR_LIMIT_M == itemfit.DEPTH_LIMIT_M and items.CLEAR_SHARE_LIMIT == itemfit.SHARE_LIMIT


def test_the_fit_clears_the_thin_limb_with_the_least_push():
    P = _plate()
    pos0, out = [0.0, 0.0, -0.06], [0.0, 0.0, 1.0]

    def depth(c, stop_above):
        pos, R = (np.asarray(pos0), np.eye(3)) if c is None else itemfit.apply(c, pos0, np.eye(3), out)
        return _thin_limb_eval((R @ P.T).T + pos)["score"]

    cands = itemfit.candidates(False, out)
    res = itemfit.search(cands, depth, items.CLEAR_LIMIT_M)
    assert res["cleared"] and res["changed"]
    clearing = [c for c in cands if depth(c, None) <= items.CLEAR_LIMIT_M]
    assert res["cand"]["cost"] == min(c["cost"] for c in clearing)
    assert res["cand"]["outward_cm"] == 4 and res["cand"]["tilt_deg"] == [0, 0]  # out of the limb: 4 cm, no tilt
    assert all(depth(c, None) > items.CLEAR_LIMIT_M for c in cands if c["tilt_deg"] == [0, 0] and 0 < c["outward_cm"] < 4)


def test_nothing_clears_keeps_the_best_and_says_so():
    P = _plate()
    depth = _depth_fn(P, [0.0, 0.0, -0.09], np.eye(3), [0, 0, 1], _capsule_depth)  # deep in the arm: 5 cm is not enough
    res = itemfit.search(itemfit.candidates(False), depth, 0.005)
    assert not res["cleared"] and res["changed"] and res["depth"] < res["depth_before"]
    # A body the item cannot leave by any small change: nothing changes.
    res = itemfit.search(itemfit.candidates(False), lambda c, s: 0.2, 0.01)
    assert not res["cleared"] and not res["changed"] and res["cand"]["cost"] == 0


# --- whose placement it is -----------------------------------------------------------------------------------------


def _att(**k):
    a = {"id": "a1", "type": "shield", "label": "shield", "socket": "Socket_Forearm_L", "position": [0.0, 0.0, 0.0],
         "rotation": [0.0, 0.0, 0.0, 1.0], "scale": 1.0}
    a.update(k)
    return a


def test_hand_adjusted_placements_are_fitted_only_when_asked():
    fresh = _att(touched=False)
    assert items.wants_fit(fresh) and items.fit_base(fresh)["position"] == [0.0, 0.0, 0.0]
    # Already fitted once, never touched: the next build starts again from the usual hold, so fits never pile up.
    fitted = _att(touched=False, position=[0, 0, 0.02], fit={"base": {"position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
                                                             "result": {"position": [0, 0, 0.02], "rotation": [0, 0, 0, 1]}})
    assert items.wants_fit(fitted) and items.fit_base(fitted)["position"] == [0.0, 0.0, 0.0]
    mine = _att(touched=True, position=[0.0, 0.05, 0.0])
    assert not items.wants_fit(mine)
    assert items.wants_fit({**mine, "fit_request": True}) and items.wants_fit(mine, fit_all=True)
    assert items.fit_base({**mine, "fit_request": True})["position"] == [0.0, 0.05, 0.0]  # from where they put it
    # Saved before the flag existed: touched only when it differs from the usual hold.
    old = _att()
    assert not items.is_touched(old) and items.is_touched(_att(position=[0.01, 0, 0])) and items.is_touched(_att(scale=1.2))


def test_writing_a_fit_back_does_not_make_the_build_look_out_of_date():
    base = _att(touched=False)
    before = items.digest({"attachments": [base]}, {"i": "x"})
    fitted = _att(touched=False, position=[0, 0, 0.02], fit={"base": {"position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
                                                             "result": {"position": [0, 0, 0.02], "rotation": [0, 0, 0, 1]}})
    assert items.digest({"attachments": [fitted]}, {"i": "x"}) == before
    # Moved by hand after the fit: a real change.
    assert items.digest({"attachments": [{**fitted, "position": [0, 0.01, 0.02], "touched": True}]}, {"i": "x"}) != before
    assert items.digest({"attachments": [{**base, "fit_request": True}]}, {"i": "x"}) != before


def test_a_fit_never_overwrites_a_change_made_during_the_build():
    mine = _att(id="m", touched=True, position=[0.0, 0.05, 0.0], fit_request=True)
    other = _att(id="o", socket="Socket_Back", type="quiver", label="quiver")
    sc = SimpleNamespace(db=None, asset=SimpleNamespace(category="humanoid", equipment={"attachments": [mine, other]}))
    before = {a["id"]: items._placement_of(a) for a in (mine, other)}
    fits = {k: {"base": {"position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
                "result": {"position": [0, 0, 0.03], "rotation": [0, 0, 0, 1]}, "outward_cm": 3.0} for k in ("m", "o")}
    # While it ran, the person nudged the quiver.
    sc.asset.equipment["attachments"][1] = {**other, "position": [0.01, 0.0, 0.0], "touched": True}
    eq = items._store_fits(sc, before, fits, {"m"})
    by = {a["id"]: a for a in eq["attachments"]}
    assert by["m"]["position"] == [0, 0, 0.03] and "fit_request" not in by["m"] and by["m"]["fit"]["outward_cm"] == 3.0
    assert by["o"]["position"] == [0.01, 0.0, 0.0] and "fit" not in by["o"]


def test_fit_sentences_are_plain():
    s = items.fit_sentence({"changed": True, "cleared": True, "outward_cm": 2.0, "tilt_deg": [5, 0], "before_m": 0.071,
                            "before_clip": "run", "after_m": 0.0}, "shield", False)
    assert s == ("We moved it 2 cm out from the body and tilted it 5 degrees, so it stays clear of the body in every move "
                 "we checked (before, it went 7.1 cm in during run).")
    s = items.fit_sentence({"changed": False, "cleared": True, "before_m": 0.0}, "shield", False)
    assert "already stays clear" in s
    s = items.fit_sentence({"changed": True, "cleared": False, "turn_deg": -30, "before_m": 0.06, "after_m": 0.04,
                            "after_clip": "attack"}, "sword", True)
    assert "turned it 30 degrees in the hand" in s and "keeps the handle in the hand" in s and "4.0 cm" in s


# --- the shield moves to the forearm ------------------------------------------------------------------------------


def test_untouched_shields_move_to_the_forearm_and_adjusted_ones_get_an_offer():
    untouched = _att(id="u", socket="Socket_Hand_L")
    adjusted = _att(id="t", socket="Socket_Hand_L", position=[0.0, 0.03, 0.0])
    sword = _att(id="s", type="sword", label="sword", socket="Socket_Hand_R")
    eq = {"attachments": [untouched, adjusted, sword]}
    assert items.upgrade_places(eq, "humanoid")
    by = {a["id"]: a for a in eq["attachments"]}
    assert by["u"]["socket"] == "Socket_Forearm_L" and "moved the shield from the left hand to the left forearm" in by["u"]["notice"]
    assert by["t"]["socket"] == "Socket_Hand_L" and by["t"]["position"] == [0.0, 0.03, 0.0]
    assert by["t"]["offer"]["socket"] == "Socket_Forearm_L" and by["t"]["offer"]["place"] == "On the left forearm"
    assert by["s"] == sword
    assert not items.upgrade_places(eq, "humanoid")  # said once
    assert not items.upgrade_places({"attachments": [_att(socket="Socket_Hand_L")]}, "quadruped")
    declined = {"attachments": [_att(socket="Socket_Hand_L", position=[0, 0.03, 0], offer_declined=True)]}
    assert not items.upgrade_places(declined, "humanoid")


def test_forearm_places_and_mirror():
    assert items.default_socket("shield", "humanoid") == "Socket_Forearm_L"
    assert items.PLACES["Socket_Forearm_R"] == "On the right forearm" and items.MIRROR["Socket_Forearm_L"] == "Socket_Forearm_R"
    assert items.outward("Socket_Forearm_L", "humanoid") == [0.0, 0.0, 1.0] and items.outward("Socket_Hip_R", "humanoid")[0] == -1.0
    assert "Socket_Forearm_L" not in items.HELD


def test_fit_endpoint_needs_a_finished_character_and_hand_moves_mark_touched(client):
    p = client.post("/api/projects", json={"name": "Fit"}).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "Hero", "category": "humanoid", "style_preset": "painted",
                                                             "prompt": "a hero in a red coat"}).json()
    att = client.post(f"/api/assets/{a['id']}/items", json={"description": "a round wooden shield", "build": False}).json()["attachment"]
    aid, tid = a["id"], att["id"]
    assert client.post(f"/api/assets/{aid}/attachments/{tid}/fit").status_code == 409
    r = client.post(f"/api/assets/{aid}/attachments/{tid}/nudge", json={"move": [1, 0, 0]}).json()["attachment"]
    assert r["touched"] is True
    r = client.post(f"/api/assets/{aid}/attachments/{tid}/nudge", json={"reset": True}).json()["attachment"]
    assert r["touched"] is False
    r = client.put(f"/api/assets/{aid}/attachments/{tid}", json={"scale": 1.1}).json()["attachment"]
    assert r["touched"] is True
    r = client.put(f"/api/assets/{aid}/attachments/{tid}", json={"keep_place": True}).json()["attachment"]
    assert r["offer_declined"] is True
    panel = client.get(f"/api/assets/{aid}/items").json()
    assert panel["fit"]["push_max_cm"] == itemfit.PUSH_MAX_CM and "Fit it for me" in panel["fit"]["help"]


# --- the checks -------------------------------------------------------------------------------------------------


def _sc(equipped):
    return SimpleNamespace(asset=SimpleNamespace(held_item=None), version=SimpleNamespace(metrics={"equipped": equipped}))


def test_clear_of_body_says_what_was_tried_and_the_soft_checks():
    eq = {"items": [{"label": "sword", "grip": {"found": True, "confidence": 0.9}}, {"label": "shield", "grip": {"found": True, "confidence": 0.8}}],
          "clearance": {"measured": True, "worst_depth_m": 0.045, "worst_label": "shield", "worst_clip": "run", "clips": ["idle", "run"]},
          "fit": [{"label": "shield", "changed": True, "cleared": False, "outward_cm": 5.0, "tilt_deg": [10, 0], "after_m": 0.045,
                   "after_clip": "run"}],
          "ground": {"measured": True, "worst_below_m": 0.07, "worst_label": "sword", "worst_clip": "idle"},
          "between": {"measured": True, "worst_depth_m": 0.0, "pair": []}}
    checks = {c["name"]: c for c in items.export_checks(_sc(eq))}
    c = checks["item_clear_of_body"]
    assert not c["passed"] and "We already tried small changes automatically and kept the best one: we moved it 5 cm out" in c["help"]
    assert "fix" not in c  # fitted already: a rebuild would find the same answer
    g = checks["item_above_ground"]
    assert not g["passed"] and g["help"].startswith("The sword dips 7 cm below the floor during idle")
    b = checks["items_apart"]
    assert b["passed"] and b["help"] == "The sword and shield never pass through each other in the moves we checked."
    eq["between"] = {"measured": True, "worst_depth_m": 0.05, "pair": ["sword", "shield"], "worst_clip": "attack"}
    b = {c["name"]: c for c in items.export_checks(_sc(eq))}["items_apart"]
    assert not b["passed"] and b["help"] == "The sword passes 5 cm into the shield during attack. Move one of them under Add an item."
    for name in ("item_above_ground", "items_apart"):
        assert name in guidance.CHECK_GUIDE


def test_clear_of_body_fails_on_the_share_inside_too():
    eq = {"items": [{"label": "shield", "grip": {"found": True, "confidence": 0.8}}], "fit": [],
          "clearance": {"measured": True, "worst_depth_m": 0.026, "worst_share": 0.09, "worst_label": "shield", "worst_clip": "walk",
                        "clips": ["idle", "walk"], "per_item": {"shield": {"worst_depth_m": 0.026, "worst_share": 0.09, "score_m": 0.054, "clip": "walk"}}}}
    c = {x["name"]: x for x in items.export_checks(_sc(eq))}["item_clear_of_body"]
    assert not c["passed"] and c["help"].startswith("9% of the shield is inside the body during walk (up to 2.6 cm in).")
    assert c["fix_label"] == "Fit it for me"  # placed by hand, not fitted yet
    eq["clearance"].update(worst_share=0.04, per_item={"shield": {"worst_depth_m": 0.026, "worst_share": 0.04}})
    assert {x["name"]: x for x in items.export_checks(_sc(eq))}["item_clear_of_body"]["passed"]
