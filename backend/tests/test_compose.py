"""Feature photos: one first picture combined from the person's own pictures, given in the New asset form
(vgac/compose.py). The queue and the stage code run; the
graphics-card calls are the Pillow stand-ins of test_own_images_api.py, so nothing reaches ComfyUI."""

from __future__ import annotations

from PIL import Image

from own_images_helpers import b64, cut, figure
from vgac import library
from vgac.config import get_settings
from test_own_images_api import _asset, _project, client, engine, wait_job  # noqa: F401  (fixtures)
from vgac import compose
from vgac.graphs.qwen_edit import compose_graph


def _pic(colour) -> str:
    return b64(Image.new("RGB", (200, 260), colour))


def test_compose_graph_wires_each_picture_into_both_encoders():
    g = compose_graph(["a.png", "b.png", "c.png"], "draw", 7, "p", width=896, height=1152)
    for n in ("pos", "neg"):
        assert g[n]["inputs"]["image1"] == ["sc", 0]
        assert g[n]["inputs"]["image2"] == ["sc2", 0] and g[n]["inputs"]["image3"] == ["sc3", 0]
    assert g["img3"]["inputs"]["image"] == "c.png"
    assert "l2" not in g and g["lat"]["inputs"]["width"] == 896 and g["ks"]["inputs"]["seed"] == 7
    assert "image2" not in compose_graph(["a.png"], "draw", 1, "p")["pos"]["inputs"]
    try:
        compose_graph(["a"] * 4, "x", 1, "p")
    except ValueError:
        pass
    else:
        raise AssertionError("four pictures must be refused")


def test_plan_puts_roles_in_order_and_sheets_the_rest():
    roles = [{"role": r} for r in ("other", "outfit", "face", "body")]
    assert compose.plan_inputs(roles[1:]) == [[1], [2], [0]]  # face, body, outfit (indexes into roles[1:])
    assert compose.plan_inputs(roles) == [[2], [3], [1, 0]]
    # The main picture of "A picture I have" always comes first.
    assert compose.plan_inputs([*roles, {"role": "character"}]) == [[4], [2], [3, 1, 0]]
    prompt = compose.compose_prompt(roles, compose.plan_inputs(roles), "A knight. Painted look.")
    assert "face, the head and the hair from Picture 1" in prompt and "sheet of 2 pictures" in prompt
    assert prompt.index("Painted look.") < prompt.index("Plain light grey background")
    assert compose.sheet([Image.new("RGB", (100, 300)), Image.new("RGBA", (50, 50))]).size == (1024, 512)


def _features():
    return [{"role": "outfit", "image": _pic((10, 80, 10)), "note": "green cloak"},
            {"role": "face", "image": b64(cut(figure("a")))},
            {"role": "body", "image": _pic((80, 10, 10))},
            {"role": "other", "image": _pic((10, 10, 80))}]


def test_feature_photos_make_one_first_picture_the_turnaround_builds_on(client, engine):  # noqa: F811
    p = _project(client)
    a = _asset(client, p["id"], prompt="a tall ranger", features=_features())
    assert a["face_closeup"] is True
    started = a["compose"]
    assert started["job"]["kind"] == "concept.compose"
    # Four pictures, one picture drawn: one version, one job.
    concepts = client.get(f"/api/assets/{a['id']}").json()["stages"]["concept"]
    assert [v["id"] for v in concepts] == [started["version"]["id"]]
    assert wait_job(client, started["job"]["id"])["status"] == "done"
    v = client.get(f"/api/versions/{started['version']['id']}").json()
    assert v["status"] == "done" and v["params"]["source"] == "compose"
    assert {"compose_1", "compose_4", "compose_sheet", "image", "cutout"} <= set(v["files"])
    assert v["metrics"]["compose"] == {"pictures": 4, "inputs": [["face"], ["body"], ["outfit", "other"]], "sheet": True}
    assert "green cloak" in v["params"]["prompt"] and "a tall ranger" in v["params"]["prompt"]
    drawn = [c for c in engine["comfy"] if c["message"].startswith("combining")]
    assert len(drawn) == 1 and "image3" in drawn[0]["graph"]["pos"]["inputs"]
    # The face picture is also the asset's own close-up for the face pass.
    assert client.get(f"/api/assets/{a['id']}/face-closeup").json()["image"]
    # Step 1 continues as usual: the turnaround builds on the combined picture.
    t = client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={}).json()
    assert wait_job(client, t["job"]["id"])["status"] == "done"
    assert client.get(f"/api/versions/{t['version']['id']}").json()["parent_version_id"] == v["id"]
    # A fine-tune combines the same pictures again.
    adj = client.post(f"/api/versions/{v['id']}/adjust", json={"params": {"seed": 5}})
    assert adj.status_code == 200, adj.text
    adj_out = adj.json()
    assert wait_job(client, adj_out["job"]["id"])["status"] == "done"
    av = client.get(f"/api/versions/{adj_out['version']['id']}").json()
    assert av["params"]["source"] == "compose" and "compose_4" in av["files"]


def test_own_picture_comes_first_and_a_face_alone_does_not_redraw_it(client, engine):  # noqa: F811
    p = _project(client)
    own = b64(cut(figure("wide", legs="apart")))
    a = _asset(client, p["id"], image=own, features=[{"role": "outfit", "image": _pic((10, 80, 10))}])
    assert "upload" not in a and "face_closeup" not in a
    assert wait_job(client, a["compose"]["job"]["id"])["status"] == "done"
    v = client.get(f"/api/versions/{a['compose']['version']['id']}").json()
    assert [r["role"] for r in v["params"]["compose_roles"]] == ["character", "outfit"]
    assert "the whole character" in v["params"]["prompt"]
    # Only a face: the picture is step 1 as it came and the face goes to the close-up.
    b = _asset(client, p["id"], image=own, features=[{"role": "face", "image": b64(cut(figure("a")))}])
    assert "compose" not in b and b["upload"]["version"]["params"]["source"] == "upload" and b["face_closeup"] is True


def test_face_closeup_only_for_humans_and_big_enough_faces(client):  # noqa: F811
    p = _project(client)
    small = b64(Image.new("RGB", (100, 100), (200, 170, 150)))
    a = _asset(client, p["id"], prompt="a knight", features=[{"role": "face", "image": small}])
    assert "face_closeup" not in a and "compose" in a
    w = _asset(client, p["id"], category="quadruped", prompt="a wolf", features=[{"role": "face", "image": b64(cut(figure("a")))}])
    assert "face_closeup" not in w
    assert not library.own_face_closeup(p["id"], w["id"], get_settings()).is_file()


def test_feature_photo_errors_make_no_asset(client):  # noqa: F811
    p = _project(client)
    url = f"/api/projects/{p['id']}/assets"
    body = {"name": "x", "category": "humanoid", "style_preset": "painted", "prompt": "a knight"}
    r = client.post(url, json={**body, "features": [{"role": "hat", "image": _pic((1, 2, 3))}]})
    assert r.status_code == 400 and "role" in r.json()["detail"]
    r = client.post(url, json={**body, "features": [{"role": "character", "image": _pic((1, 2, 3))}]})
    assert r.status_code == 400
    r = client.post(url, json={**body, "features": [{"role": "face", "image": "@@@"}]})
    assert r.status_code == 400 and "face picture" in r.json()["detail"]
    r = client.post(url, json={**body, "category": "material", "features": [{"role": "face", "image": _pic((1, 2, 3))}]})
    assert r.status_code == 400
    assert client.post(url, json={**body, "features": [{"role": "face", "image": _pic((1, 2, 3))}] * 7}).status_code == 422
    assert client.get(url).json() == []
    # The old step 0.5 endpoint is gone.
    a = _asset(client, p["id"])
    assert client.post(f"/api/assets/{a['id']}/stages/concept/compose", json={"pictures": []}).status_code in (404, 405)
