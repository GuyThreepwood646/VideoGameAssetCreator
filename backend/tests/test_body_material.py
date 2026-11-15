"""What the character is made of: the two step 1 tick boxes stored on the asset (db.Asset.wears_armour / body_is_metal)."""
from __future__ import annotations

from test_api import _project_asset, client, fake_execute  # noqa: F401  (fixtures)


def _put(client, aid, **body):
    r = client.put(f"/api/assets/{aid}", json=body)
    assert r.status_code == 200
    return r.json()


def test_an_upload_with_no_description_starts_unticked_and_unanswered(client):
    p, a = _project_asset(client)
    r = client.post(f"/api/projects/{p['id']}/assets", json={"name": "k", "category": "humanoid", "style_preset": a["style_preset"], "prompt": ""})
    d = r.json()
    assert d["wears_armour"] is False and d["body_is_metal"] is False
    assert d["wears_armour_set"] is False and d["body_is_metal_set"] is False


def test_a_described_character_shows_what_the_description_says_until_the_person_answers(client):
    p, a = _project_asset(client)
    r = client.post(f"/api/projects/{p['id']}/assets", json={"name": "k", "category": "humanoid", "style_preset": a["style_preset"],
                                                             "prompt": "a knight in steel plate armour"})
    d = r.json()
    assert d["wears_armour"] is True and d["wears_armour_set"] is False
    assert d["body_is_metal"] is False
    d = _put(client, d["id"], wears_armour=False)
    assert d["wears_armour"] is False and d["wears_armour_set"] is True  # the person's untick beats the description
    assert d["prompt"] == "a knight in steel plate armour"  # the description is never rewritten
    assert client.get(f"/api/assets/{d['id']}").json()["wears_armour"] is False


def test_ticks_are_stored_and_kept_by_a_variant(client):
    p, a = _project_asset(client)
    d = _put(client, a["id"], wears_armour=True, body_is_metal=True)
    assert d["wears_armour"] is True and d["body_is_metal"] is True
    r = client.post(f"/api/projects/{p['id']}/assets", json={"name": "r", "category": "humanoid", "style_preset": a["style_preset"],
                                                             "prompt": "", "wears_armour": True})
    assert r.json()["wears_armour"] is True and r.json()["wears_armour_set"] is True
    assert r.json()["body_is_metal_set"] is False
