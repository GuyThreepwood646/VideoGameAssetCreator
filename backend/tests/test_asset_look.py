"""An asset follows its project's look unless the request says the person chose another on purpose."""

from __future__ import annotations

from test_api import client, fake_execute  # noqa: F401  (fixtures)


def _project(client, preset):
    lock = {"preset": preset, "model": "zturbo"} if preset else {"model": "zturbo"}
    return client.post("/api/projects", json={"name": "L", "style_lock": lock}).json()


def _asset(client, pid, look, **extra):
    return client.post(f"/api/projects/{pid}/assets", json={"name": "a", "category": "prop_rigid", "style_preset": look, "prompt": "a crate", **extra})


def test_same_look_is_accepted(client):
    p = _project(client, "realistic_pbr")
    r = _asset(client, p["id"], "realistic_pbr")
    assert r.status_code == 201 and r.json()["style_preset"] == "realistic_pbr"


def test_different_look_is_refused_in_plain_words(client):
    p = _project(client, "realistic_pbr")
    r = _asset(client, p["id"], "painted")
    assert r.status_code == 400
    msg = r.json()["detail"]
    assert "Photoreal" in msg and "Painted" in msg and "different look" in msg
    assert client.get(f"/api/projects/{p['id']}").json() and not client.get(f"/api/projects/{p['id']}/assets").json()


def test_different_look_on_purpose_is_accepted(client):
    p = _project(client, "realistic_pbr")
    r = _asset(client, p["id"], "painted", different_look=True)
    assert r.status_code == 201 and r.json()["style_preset"] == "painted"


def test_different_look_false_is_still_refused(client):
    p = _project(client, "realistic_pbr")
    assert _asset(client, p["id"], "painted", different_look=False).status_code == 400


def test_project_without_a_look_accepts_any(client):
    p = _project(client, None)
    assert _asset(client, p["id"], "painted").status_code == 201
    assert _asset(client, p["id"], "clay").status_code == 201


def test_unknown_look_is_still_refused_first(client):
    p = _project(client, "realistic_pbr")
    assert _asset(client, p["id"], "nope", different_look=True).status_code == 400
