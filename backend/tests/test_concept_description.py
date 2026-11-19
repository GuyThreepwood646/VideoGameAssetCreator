"""A description edit reaches the next step 1 picture. A concept's one-attempt prompt_override is not inherited by the
refines and adjusts made on it, so the asset's description (the one the owner edits) is used again."""
from __future__ import annotations

from types import SimpleNamespace

from vgac import reference
from vgac.stages import concept

from test_api import _project_asset, client, fake_execute, wait_job  # noqa: F401  (fixtures)

OLD = "a knight with the visor down"
NEW = "a mage in green robes"


def _sc(prompt):
    return SimpleNamespace(project=SimpleNamespace(style_lock={}),
                           asset=SimpleNamespace(prompt=prompt, parts=[], category="humanoid", equipment=None, held_item=None),
                           style=reference.style("cel"), category=reference.category("humanoid"))


def test_inherited_params_drop_a_concept_override_only():
    assert "prompt_override" not in reference.inherited_params("concept", {"prompt_override": OLD, "seed": 4})
    assert reference.inherited_params("concept", {"prompt_override": OLD, "seed": 4})["seed"] == 4
    # A material refine builds on the prompt it was given, so its override is kept.
    assert reference.inherited_params("material", {"prompt_override": OLD})["prompt_override"] == OLD
    assert reference.inherited_params("concept", None) == {}


def test_the_description_used_after_an_inherited_override_is_the_asset_one():
    # Only the one attempt's override is dropped; the description is what a child then paints from.
    assert concept._description(_sc(NEW), reference.inherited_params("concept", {"prompt_override": OLD})) == NEW
    assert concept._description(_sc(NEW), {"prompt_override": OLD}) == OLD


def test_refine_and_adjust_children_do_not_carry_an_old_override(client, fake_execute):  # noqa: F811
    _, a = _project_asset(client)
    first = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"prompt_override": OLD}}).json()
    wait_job(client, first["job"]["id"])
    v1 = first["version"]["id"]
    assert client.get(f"/api/versions/{v1}").json()["params"]["prompt_override"] == OLD

    ref = client.post(f"/api/versions/{v1}/refine", json={"instruction": "red cloak"}).json()
    wait_job(client, ref["job"]["id"])
    assert not ref["version"]["params"].get("prompt_override")
    c1 = ref["version"]["id"]

    # The owner edits the description; the next refine and adjust read it, not the old one-attempt override.
    assert client.put(f"/api/assets/{a['id']}", json={"prompt": NEW}).status_code == 200
    ref2 = client.post(f"/api/versions/{c1}/refine", json={"instruction": "a hat"}).json()
    wait_job(client, ref2["job"]["id"])
    assert not ref2["version"]["params"].get("prompt_override")
    adj = client.post(f"/api/versions/{ref2['version']['id']}/adjust", json={"params": {"seed": 9}}).json()
    wait_job(client, adj["job"]["id"])
    assert not adj["version"]["params"].get("prompt_override")
    assert adj["version"]["params"]["seed"] == 9
    assert not fake_execute[-1]["payload"]["params"].get("prompt_override")

    # An override given on the refine itself still applies to that one attempt.
    explicit = client.post(f"/api/versions/{c1}/refine", json={"instruction": "boots", "params": {"prompt_override": OLD}}).json()
    wait_job(client, explicit["job"]["id"])
    assert explicit["version"]["params"]["prompt_override"] == OLD


def test_a_new_step_one_version_after_an_edit_uses_the_edited_description(client, fake_execute):  # noqa: F811
    _, a = _project_asset(client)
    first = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={"params": {"prompt_override": OLD}}).json()
    wait_job(client, first["job"]["id"])
    assert client.put(f"/api/assets/{a['id']}", json={"prompt": NEW}).status_code == 200

    fresh = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    wait_job(client, fresh["job"]["id"])
    assert not fresh["version"]["params"].get("prompt_override")
    assert not fake_execute[-1]["payload"]["params"].get("prompt_override")
    assert client.get(f"/api/assets/{a['id']}").json()["prompt"] == NEW
