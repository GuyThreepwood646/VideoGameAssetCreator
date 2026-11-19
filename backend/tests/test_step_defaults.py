"""Saved step settings per platform and look (WP-14): round trip, only the matching platform and look, refusals with a
plain 400, look limits clamp on apply, partial save and clear, and a bad prefs file entry is dropped."""

from __future__ import annotations

from test_api import client, fake_execute  # noqa: F401  (fixtures)

URL = "/api/step-defaults"
Q = {"platform": "pc_mid", "look": "painted", "category": "humanoid"}


def _put(client, stages, **q):  # noqa: F811
    return client.put(URL, params={**Q, **q}, json={"stages": stages})


def test_round_trip_and_clear(client):  # noqa: F811
    assert client.get(URL, params=Q).json()["saved"] == {}
    r = _put(client, {"mesh": {"upsample": 1536}, "rig": {"arm_pose": "open"}})
    assert r.status_code == 200
    assert r.json()["saved"] == {"mesh": {"upsample": 1536}, "rig": {"arm_pose": "open"}}
    assert client.get(URL, params=Q).json()["applied"]["rig"] == {"arm_pose": "open"}
    # one step cleared, the other kept
    r = client.delete(URL, params={**Q, "stage": "rig"}).json()
    assert r["saved"] == {"mesh": {"upsample": 1536}}
    # an empty set for a step clears it too
    assert _put(client, {"mesh": {}}).json()["saved"] == {}
    _put(client, {"mesh": {"upsample": 1536}})
    assert client.delete(URL, params=Q).json()["saved"] == {}


def test_only_the_same_platform_and_look(client):  # noqa: F811
    _put(client, {"mesh": {"upsample": 1536}})
    assert client.get(URL, params={**Q, "platform": "high"}).json()["saved"] == {}
    assert client.get(URL, params={**Q, "look": "cel"}).json()["saved"] == {}
    assert client.get(URL, params=Q).json()["saved"] == {"mesh": {"upsample": 1536}}


def test_saves_the_other_steps_it_does_not_name(client):  # noqa: F811
    _put(client, {"mesh": {"upsample": 1536}})
    _put(client, {"rig": {"arm_pose": "close"}})
    assert set(client.get(URL, params=Q).json()["saved"]) == {"mesh", "rig"}


def test_bad_values_are_refused_and_nothing_is_saved(client):  # noqa: F811
    _put(client, {"mesh": {"upsample": 1536}})
    for stages, word in [
        ({"nonsense": {"a": 1}}, "unknown step"),
        ({"mesh": {"no_such_setting": 1}}, "no_such_setting"),
        ({"mesh": {"target_faces": 5}}, "between"),
        ({"mesh": {"target_faces": "many"}}, "number"),
        ({"rig": {"arm_pose": "sideways"}}, "one of"),
        ({"mesh": {"seed": 4}}, "seed"),
        ({"mesh": {"upsample": None}}, "needs a value"),
        # a good step next to a bad one saves neither
        ({"rig": {"arm_pose": "open"}, "mesh": {"target_faces": 5}}, "between"),
    ]:
        r = _put(client, stages)
        assert r.status_code == 400, stages
        assert word in r.json()["detail"], r.json()
    assert client.get(URL, params=Q).json()["saved"] == {"mesh": {"upsample": 1536}}


def test_unknown_platform_or_look_is_refused(client):  # noqa: F811
    assert client.get(URL, params={**Q, "platform": "console"}).status_code == 400
    assert client.get(URL, params={**Q, "look": "nope"}).status_code == 400
    assert client.delete(URL, params={**Q, "stage": "nope"}).status_code == 400


def test_look_limits_win_when_applied(client):  # noqa: F811
    q = {"platform": "high", "look": "retro64", "category": "humanoid"}
    r = _put(client, {"mesh": {"target_faces": 50000}}, **q).json()
    # saved as typed, applied at the look's own ceiling, with a plain sentence
    assert r["saved"]["mesh"]["target_faces"] == 50000
    assert r["applied"]["mesh"]["target_faces"] == 3000
    assert "3,000" in r["notes"][0] and "triangle" in r["notes"][0]
    # within the limit: used as saved, no note
    r = _put(client, {"mesh": {"target_faces": 2000}}, **q).json()
    assert r["applied"]["mesh"]["target_faces"] == 2000 and r["notes"] == []


def test_defaults_survive_a_prefs_round_trip(client):  # noqa: F811
    from vgac import prefs

    _put(client, {"mesh": {"upsample": 1536}})
    assert prefs.load()["step_defaults"] == {"pc_mid|painted": {"mesh": {"upsample": 1536}}}
    # malformed entries are dropped on load, good ones stay
    assert prefs._normalise({"step_defaults": {"x": {"mesh": {"a": 1}}, "pc_mid|cel": {"mesh": {"a": 1}, "rig": {}}, "pc_mid|clay": "bad"}})[
        "step_defaults"] == {"pc_mid|cel": {"mesh": {"a": 1}}}
