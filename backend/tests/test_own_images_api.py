"""Start from your own images, through the API. The queue and the real stage code run; only the graphics-card calls
are replaced: the cutout is a Pillow background removal, every edit or view the model would draw is a synthetic
A-pose figure, and the review engine answers with a fixed paragraph. Nothing reaches ComfyUI."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from own_images_helpers import b64, cut, figure
from vgac import autopilot, imaging
from vgac.comfy import ComfyClient
from vgac.stages import base as stage_base


def wait_job(client: TestClient, job_id: str, timeout: float = 20.0) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {j}")


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch):
    """Graphics-card calls of StageContext, done with Pillow. Records what was drawn."""
    calls: dict[str, list[Any]] = {"comfy": [], "staged": []}

    async def stage_input(self, path, name, instance=None):
        calls["staged"].append(name)
        return name

    async def comfy(self, graph, instance=None, poll=1.0, message="", base=0.0, span=1.0):
        calls["comfy"].append({"message": message, "graph": graph})
        return {"outputs": {}, "elapsed_seconds": 0.1}

    async def download_image(self, entry, node, dest, instance=None):
        figure("a").save(dest)  # whatever the model was asked for, it draws a ready A-pose figure
        return Path(dest)

    async def cutout(self, image_path, name, instance=None, base=0.0, span=0.1):
        dest = self.dir / f"{name}.png"
        cut(Image.open(image_path).convert("RGB")).save(dest)
        return {"cutout": dest, "white": imaging.white_composite(dest), "stats": imaging.alpha_bbox(dest)}

    monkeypatch.setattr(stage_base.StageContext, "stage_input", stage_input)
    monkeypatch.setattr(stage_base.StageContext, "comfy", comfy)
    monkeypatch.setattr(stage_base.StageContext, "download_image", download_image)
    monkeypatch.setattr(stage_base.StageContext, "cutout", cutout)

    async def c_stage_input(self, src, name=None):
        calls["staged"].append(name)
        return name

    async def c_run(self, graph, poll_seconds=1.0, timeout=None, on_poll=None):
        calls["comfy"].append({"message": "review", "graph": graph})
        return {"outputs": {"show": {"text": ['"Gouache on rough paper, soft two-tone shading, thin dark outlines, '
                                              'chunky rounded shapes, warm muted colours."']}}}

    monkeypatch.setattr(ComfyClient, "stage_input", c_stage_input)
    monkeypatch.setattr(ComfyClient, "run", c_run)
    monkeypatch.setattr(autopilot, "POLL_SECONDS", 0.1)
    return calls


@pytest.fixture
def client(settings, engine):
    from vgac.main import app

    with TestClient(app) as c:
        yield c


def _project(client):
    return client.post("/api/projects", json={"name": "P"}).json()


def _asset(client, pid, category="humanoid", **extra):
    body = {"name": "knight", "category": category, "style_preset": "painted", "prompt": "", **extra}
    r = client.post(f"/api/projects/{pid}/assets", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_upload_creates_a_concept_the_turnaround_builds_on(client, engine, settings):
    p = _project(client)
    a = _asset(client, p["id"], prompt="a knight")
    r = client.post(f"/api/assets/{a['id']}/stages/concept/upload", json={"image": b64(cut(figure("a")))})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["job"]["kind"] == "concept.upload" and out["version"]["params"]["source"] == "upload"
    assert out["version"]["params"]["model"] is None
    assert wait_job(client, out["job"]["id"])["status"] == "done"
    v = client.get(f"/api/versions/{out['version']['id']}").json()
    assert v["status"] == "done" and v["selected"] is True
    assert {"image", "original", "cutout", "cutout_white"} <= set(v["files"])
    assert v["metrics"]["source"] == "upload" and v["metrics"]["gate_ok"] is True
    assert {c["name"] for c in v["metrics"]["checks"]} == {"one_subject", "in_frame", "big_enough", "arms_away"}
    # The stored picture is a plain PNG on light grey, no metadata.
    img = Image.open(settings.library_path / v["files"]["image"])
    assert img.format == "PNG" and img.mode == "RGB" and img.getpixel((2, 2)) == (235, 235, 235)
    assert not any(c["message"] for c in engine["comfy"])  # no picture was painted
    # The turnaround builds on the uploaded picture.
    t = client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={}).json()
    assert wait_job(client, t["job"]["id"])["status"] == "done"
    tv = client.get(f"/api/versions/{t['version']['id']}").json()
    assert tv["parent_version_id"] == v["id"] and tv["metrics"]["views_total"] == 5
    assert any(n.endswith("_concept.png") for n in engine["staged"])


def test_upload_errors_are_plain_400s(client):
    p = _project(client)
    a = _asset(client, p["id"])
    for body, word in (({"image": "@@@"}, "could not be read"), ({"image": b64(figure(), "GIF")}, "GIF")):
        r = client.post(f"/api/assets/{a['id']}/stages/concept/upload", json=body)
        assert r.status_code == 400 and word in r.json()["detail"]
    assert client.post("/api/assets/nope/stages/concept/upload", json={"image": b64(figure())}).status_code == 404
    # A bad picture on asset creation creates nothing.
    r = client.post(f"/api/projects/{p['id']}/assets", json={"name": "x", "category": "humanoid", "style_preset": "painted", "image": "@@@"})
    assert r.status_code == 400
    assert len(client.get(f"/api/projects/{p['id']}/assets").json()) == 1


def test_create_from_a_picture_and_autopilot_continues_from_it(client, engine):
    p = _project(client)
    a = _asset(client, p["id"], image=b64(figure("a"), "JPEG"))
    assert a["upload"]["job"]["kind"] == "concept.upload" and a["prompt"] == ""
    assert a["stages"]["concept"][0]["params"]["source"] == "upload"
    r = client.post(f"/api/assets/{a['id']}/autopilot", json={"until_stage": "turnaround"})
    assert r.status_code == 200
    t0 = time.time()
    while time.time() - t0 < 30:
        st = client.get(f"/api/assets/{a['id']}/autopilot").json()
        if st["status"] in ("done", "error", "paused"):
            break
        time.sleep(0.1)
    assert st["status"] == "done", st
    full = client.get(f"/api/assets/{a['id']}").json()
    assert len(full["stages"]["concept"]) == 1  # the uploaded concept counted as the concept step: nothing painted
    assert full["selected"]["concept"] == a["stages"]["concept"][0]["id"]
    assert full["stages"]["turnaround"][0]["status"] == "done"
    assert not any("concept image" == c["message"] for c in engine["comfy"])


def test_note_fills_an_empty_description(client):
    p = _project(client)
    a = _asset(client, p["id"])
    out = client.post(f"/api/assets/{a['id']}/stages/concept/upload", json={"image": b64(figure()), "note": "a red knight"}).json()
    wait_job(client, out["job"]["id"])
    assert client.get(f"/api/assets/{a['id']}").json()["prompt"] == "a red knight"


def test_the_redraw_fix_runs_a_refine(client, engine):
    p = _project(client)
    a = _asset(client, p["id"])
    out = client.post(f"/api/assets/{a['id']}/stages/concept/upload", json={"image": b64(figure("down"))}).json()
    wait_job(client, out["job"]["id"])
    v = client.get(f"/api/versions/{out['version']['id']}").json()
    arms = next(c for c in v["metrics"]["checks"] if c["name"] == "arms_away")
    assert not arms["passed"] and v["metrics"]["gate_ok"] is False
    fix = arms["fix"]
    assert fix["label"] == "Redraw it ready for 3D" and fix["op"] == "refine"
    r = client.post(f"/api/versions/{v['id']}/refine", json={"instruction": fix["instruction"], "params": fix["params"]})
    assert r.status_code == 200
    ref = r.json()
    assert ref["job"]["kind"] == "concept.refine"
    assert wait_job(client, ref["job"]["id"])["status"] == "done"
    nv = client.get(f"/api/versions/{ref['version']['id']}").json()
    assert nv["params"]["instruction"].startswith("Redraw this same character") and nv["params"]["source"] == "upload_edit"
    assert nv["metrics"]["gate_ok"] is True
    edit = next(c for c in engine["comfy"] if c["message"] == "instruction edit")
    assert edit["graph"]["pos"]["inputs"]["prompt"] == fix["instruction"] and "img2" not in edit["graph"]


def test_supplied_views_are_kept_and_missing_ones_drawn(client, engine):
    p = _project(client)
    a = _asset(client, p["id"])
    out = client.post(f"/api/assets/{a['id']}/stages/concept/upload", json={"image": b64(figure("a"))}).json()
    wait_job(client, out["job"]["id"])
    back = figure("down")  # a back view that does not match the front: it warns and is kept
    r = client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={"params": {"provided_views": {"back": b64(back)}}})
    assert r.status_code == 200, r.text
    t = r.json()
    assert "provided_views" not in t["version"]["params"] and t["version"]["params"]["supplied_views"] == ["back"]
    assert wait_job(client, t["job"]["id"])["status"] == "done"
    tv = client.get(f"/api/versions/{t['version']['id']}").json()
    views = tv["metrics"]["views"]
    assert views["back"]["supplied"] is True and views["back"]["rerolls"] == 0
    assert tv["metrics"]["supplied_views"] == ["back"]
    drawn = [c["message"] for c in engine["comfy"] if c["message"].startswith("view ")]
    assert "view back" not in drawn and {"view front", "view left", "view right", "view quarter"} <= set(drawn)
    assert "supplied_back" in tv["files"] and "back_cutout" in tv["files"]
    # Bad pictures and views are refused before anything runs.
    bad = client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={"params": {"provided_views": {"back": "@@@"}}})
    assert bad.status_code == 400 and "back view picture" in bad.json()["detail"]
    assert client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={"params": {"provided_views": {"under": b64(back)}}}).status_code == 400

    # One more view on the finished turnaround: a new version; every other view is copied, only that one is cut out.
    before = len(engine["comfy"])
    r = client.post(f"/api/versions/{tv['id']}/views/left/upload", json={"image": b64(figure("a"))})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["job"]["kind"] == "turnaround.upload" and up["version"]["parent_version_id"] == tv["id"]
    assert wait_job(client, up["job"]["id"])["status"] == "done"
    uv = client.get(f"/api/versions/{up['version']['id']}").json()
    assert uv["metrics"]["views"]["left"]["supplied"] and uv["metrics"]["views"]["back"]["supplied"]
    assert set(uv["metrics"]["views"]) == {"front", "left", "back", "right", "quarter"}
    assert not [c for c in engine["comfy"][before:] if c["message"].startswith("view ")]
    assert client.post(f"/api/versions/{out['version']['id']}/views/left/upload", json={"image": b64(figure())}).status_code == 400


def test_material_from_a_photo(client):
    import numpy as np

    p = _project(client)
    a = _asset(client, p["id"], category="material")
    rng = np.random.default_rng(5)
    photo = Image.fromarray(rng.integers(80, 170, (256, 256, 3), dtype=np.uint8))
    out = client.post(f"/api/assets/{a['id']}/stages/concept/upload", json={"image": b64(photo, "JPEG")}).json()
    assert out["job"]["kind"] == "material.concept.upload"
    assert wait_job(client, out["job"]["id"])["status"] == "done"
    v = client.get(f"/api/versions/{out['version']['id']}").json()
    assert {c["name"] for c in v["metrics"]["checks"]} == {"even_lighting", "straight_on"}
    assert "raw" in v["files"] and "cutout" not in v["files"]
    # The seamless and derived-maps step runs on the photo.
    m = client.post(f"/api/assets/{a['id']}/stages/mesh/run", json={}).json()
    assert wait_job(client, m["job"]["id"])["status"] == "done"
    mv = client.get(f"/api/versions/{m['version']['id']}").json()
    assert {"base_color", "normal", "roughness"} <= set(mv["files"])


def test_palette_endpoint(client):
    im = Image.new("RGB", (200, 200), (235, 235, 235))
    im.paste((200, 20, 20), (40, 40, 160, 160))
    r = client.post("/api/palette-from-image", json={"image": b64(im), "colors": 8})
    assert r.status_code == 200
    out = r.json()
    assert out["palette"][0] == "#c81414" and out["swatches"][0]["share"] > 0.9 and out["background"] == "#ebebeb"
    assert client.post("/api/palette-from-image", json={"image": b64(im), "colors": 5}).status_code == 400
    assert client.post("/api/palette-from-image", json={"image": "nope"}).status_code == 400


def test_style_from_a_picture_is_returned_not_applied(client, engine):
    p = _project(client)
    r = client.post(f"/api/projects/{p['id']}/style-from-image", json={"image": b64(figure("a"))})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["palette"] and out["needs_review_engine"] is False and out["job"]["kind"] == "concept.judge"
    j = wait_job(client, out["job"]["id"])
    assert j["status"] == "done", j
    res = j["result"]
    assert res["description"].startswith("Gouache on rough paper") and '"' not in res["description"]
    assert res["palette"] == out["palette"] and res["words"] < 80
    graph = engine["comfy"][-1]["graph"]
    assert "no brand" in graph["gen"]["inputs"]["prompt"].lower() or "Do not name any brand" in graph["gen"]["inputs"]["prompt"]
    # Nothing was applied to the project.
    assert client.get(f"/api/projects/{p['id']}").json()["style_lock"].get("prompt_block", "") == ""
    assert client.post("/api/projects/nope/style-from-image", json={"image": b64(figure())}).status_code == 404


def test_style_without_a_review_engine_still_gives_the_colours(client, monkeypatch):
    from vgac import jobs

    q = jobs.active_queue()
    monkeypatch.setattr(q, "place", lambda stage, category: ("b", "Waiting for test to be turned back on"))
    p = _project(client)
    out = client.post(f"/api/projects/{p['id']}/style-from-image", json={"image": b64(figure("a"))}).json()
    assert out["job"] is None and out["needs_review_engine"] is True and out["palette"]
    assert "review engine" in out["message"]


def test_refine_with_a_reference_picture_is_wired_as_picture_two(client, engine):
    p = _project(client)
    a = _asset(client, p["id"])
    out = client.post(f"/api/assets/{a['id']}/stages/concept/upload", json={"image": b64(figure("a"))}).json()
    wait_job(client, out["job"]["id"])
    vid = out["version"]["id"]
    r = client.post(f"/api/versions/{vid}/refine", json={"instruction": "give him this helmet", "reference_image": b64(figure("t"))})
    assert r.status_code == 200, r.text
    ref = r.json()
    assert "reference" in ref["version"]["files"] and ref["version"]["params"]["reference_experimental"] is True
    assert wait_job(client, ref["job"]["id"])["status"] == "done"
    edit = next(c for c in engine["comfy"] if c["message"].startswith("instruction edit with a reference"))
    assert edit["graph"]["pos"]["inputs"]["image2"] == ["sc2", 0]
    # The next refine without one does not carry the flag over.
    nxt = client.post(f"/api/versions/{ref['version']['id']}/refine", json={"instruction": "bluer"}).json()
    assert "reference_experimental" not in nxt["version"]["params"]
    # Only the first picture of a character, creature or prop takes one.
    m = _asset(client, p["id"], category="material")
    assert client.post(f"/api/versions/{vid}/refine", json={"instruction": "x", "reference_image": "@@@"}).status_code == 400
    t = client.post(f"/api/assets/{a['id']}/stages/turnaround/run", json={}).json()
    wait_job(client, t["job"]["id"])
    bad = client.post(f"/api/versions/{t['version']['id']}/refine", json={"instruction": "x", "reference_image": b64(figure())})
    assert bad.status_code == 400 and "first picture" in bad.json()["detail"]
    assert m["category"] == "material"
