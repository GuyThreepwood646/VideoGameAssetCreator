"""Your own 3D model on step 3 (vgac/model_import.py, blender/modelimport.py).

Unit tests for the file checks run everywhere. The API tests run the real queue, the real stage code and real Blender
on a temporary library (skipped when Blender is not installed); nothing reaches a graphics engine.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vgac import model_import as mi

BLENDER = Path("F:/Tools/blender-5.2.3-windows-x64/blender.exe")

# Two boxes side by side, one red and one blue material: the bake path (several materials, no pictures).
OBJ = """# two boxes
mtllib boxes.mtl
o red
v -1 -0.5 0
v -0.2 -0.5 0
v -0.2 0.5 0
v -1 0.5 0
v -1 -0.5 1.6
v -0.2 -0.5 1.6
v -0.2 0.5 1.6
v -1 0.5 1.6
usemtl red
f 1 2 3 4
f 5 8 7 6
f 1 5 6 2
f 2 6 7 3
f 3 7 8 4
f 4 8 5 1
o blue
v 0.2 -0.5 0
v 1 -0.5 0
v 1 0.5 0
v 0.2 0.5 0
v 0.2 -0.5 1.0
v 1 -0.5 1.0
v 1 0.5 1.0
v 0.2 0.5 1.0
usemtl blue
f 9 10 11 12
f 13 16 15 14
f 9 13 14 10
f 10 14 15 11
f 11 15 16 12
f 12 16 13 9
"""
MTL = """newmtl red
Kd 0.8 0.05 0.05
newmtl blue
Kd 0.05 0.1 0.8
"""


def obj_zip(extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("boxes/boxes.obj", OBJ)
        z.writestr("boxes/boxes.mtl", MTL)
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


# --- units ---------------------------------------------------------------------------------------------------------


def test_safe_name_keeps_plain_names_and_refuses_folders_and_other_kinds():
    assert mi.safe_name("My Knight.FBX") == "My Knight.fbx"
    assert mi.safe_name("con.glb") == "model_con.glb"
    for bad in ("../x.glb", "a/b.glb", "c:x.glb", "x.blend", "", "x.png"):
        with pytest.raises(mi.ModelError):
            mi.safe_name(bad)


@pytest.mark.parametrize("ext,first", [(".glb", b"glTF\x02\x00\x00\x00"), (".gltf", b' {"asset": {}}'),
                                       (".fbx", b"Kaydara FBX Binary  \x00"), (".obj", b"# made by hand\nv 0 0 0\n"),
                                       (".obj", b"mtllib a.mtl\n"), (".zip", b"PK\x03\x04rest")])
def test_sniff_accepts_each_kind(ext, first):
    mi.sniff(first, ext)


@pytest.mark.parametrize("ext,first", [(".glb", b"PK\x03\x04"), (".obj", b"\x89PNG\r\n"), (".zip", b"glTF"), (".fbx", b"hello")])
def test_sniff_refuses_a_file_that_is_not_what_its_name_says(ext, first):
    with pytest.raises(mi.ModelError):
        mi.sniff(first, ext)


def test_unpack_zip_refuses_names_that_climb_out(tmp_path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.obj", "v 0 0 0")
    p = tmp_path / "x.zip"
    p.write_bytes(buf.getvalue())
    with pytest.raises(mi.ModelError):
        mi.unpack_zip(p, tmp_path / "out")
    assert not (tmp_path / "evil.obj").exists()


def test_find_model_picks_the_one_model_and_refuses_two_or_none(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "m.obj").write_text("v 0 0 0")
    (tmp_path / "a" / "m.mtl").write_text("")
    assert mi.find_model(tmp_path).name == "m.obj"
    (tmp_path / "b.glb").write_bytes(b"glTF")
    assert mi.find_model(tmp_path).name == "b.glb"  # GLB is preferred over OBJ
    (tmp_path / "c.glb").write_bytes(b"glTF")
    with pytest.raises(mi.ModelError):
        mi.find_model(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(mi.ModelError):
        mi.find_model(empty)


def test_view_yaws_match_the_facing_words():
    # quarter faces you and to your right: camera at -45; quarter2 the other way; the sides at 90 and 270.
    assert mi.VIEW_RENDER_YAW == {"front": 0, "left": 90, "back": 180, "right": 270, "quarter": 315, "quarter2": 45}


# --- through the API, with real Blender ----------------------------------------------------------------------------

needs_blender = pytest.mark.skipif(not BLENDER.is_file(), reason="Blender 5.2.3 not installed at the usual place")


def wait_job(client: TestClient, job_id: str, timeout: float = 240.0) -> dict[str, Any]:
    t0 = time.time()
    j: dict[str, Any] = {}
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish: {j}")


@pytest.fixture
def client(settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "blender_exe", BLENDER)
    from vgac.main import app

    with TestClient(app) as c:
        yield c


def new_asset(client: TestClient, category: str = "prop_rigid") -> dict[str, Any]:
    p = client.post("/api/projects", json={"name": "Imports"}).json()
    return client.post(f"/api/projects/{p['id']}/assets", json={"name": "Boxes", "category": category,
                                                                 "style_preset": "painted", "prompt": ""}).json()


@needs_blender
def test_a_zipped_obj_becomes_step_3_with_pictures_for_steps_1_and_2(client: TestClient, settings):
    a = new_asset(client)
    r = client.post(f"/api/assets/{a['id']}/stages/mesh/upload?name=boxes.zip", content=obj_zip())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"]["instance"] == "host" and body["job"]["kind"] == "mesh.upload"
    j = wait_job(client, body["job"]["id"])
    assert j["status"] == "done", j
    asset = client.get(f"/api/assets/{a['id']}").json()
    mesh = next(v for v in asset["stages"]["mesh"] if v["id"] == body["version"]["id"])
    assert mesh["status"] == "done" and mesh["selected"]
    assert mesh["params"]["source"] == "upload" and mesh["params"]["hair_source"] == "model"
    imported = mesh["metrics"]["imported"]
    assert imported["texture_mode"] == "baked" and imported["materials"] == 2 and imported["uv_made"]
    assert {"glb", "base_color", "import_report", "preview"} <= set(mesh["files"])
    # Steps 1 and 2 hold renders of the model, kept, and the model sits on them.
    concept = [v for v in asset["stages"]["concept"] if v["selected"]][0]
    turn = [v for v in asset["stages"]["turnaround"] if v["selected"]][0]
    assert concept["params"]["source"] == "from_model" and turn["params"]["source"] == "from_model"
    assert {"image", "cutout", "cutout_white"} <= set(concept["files"])
    for view in mi.TURN_VIEWS:
        assert {view, f"{view}_cutout", f"{view}_white"} <= set(turn["files"])
    assert turn["parent_version_id"] == concept["id"] and mesh["parent_version_id"] == turn["id"]
    # The audit compares the model with its own renders: a near-perfect match, facing straight on.
    assert mesh["metrics"]["silhouette_iou"] > 0.9
    # The baked colours are the material colours: red on one box, blue on the other.
    from PIL import Image

    from vgac import library

    base = Image.open(library.abs_path(mesh["files"]["base_color"], settings)).convert("RGB")
    colours = {c for _, c in base.getcolors(1 << 20) or []}
    assert any(r > 200 and g < 100 and b < 100 for r, g, b in colours)
    assert any(b > 200 and r < 100 for r, g, b in colours)


@needs_blender
def test_turn_model_makes_a_turned_version_from_the_same_file(client: TestClient):
    a = new_asset(client)
    body = client.post(f"/api/assets/{a['id']}/stages/mesh/upload?name=boxes.zip", content=obj_zip()).json()
    assert wait_job(client, body["job"]["id"])["status"] == "done"
    r = client.post(f"/api/versions/{body['version']['id']}/turn-model", json={"turn": 180})
    assert r.status_code == 200, r.text
    j = wait_job(client, r.json()["job"]["id"])
    assert j["status"] == "done", j
    v = client.get(f"/api/versions/{r.json()['version']['id']}").json()
    assert v["params"]["turn"] == 180 and v["metrics"]["imported"]["turn"] == 180 and v["selected"]
    assert client.post(f"/api/versions/{body['version']['id']}/turn-model", json={"turn": 45}).status_code == 400


@needs_blender
def test_a_file_without_a_mesh_fails_with_a_plain_sentence(client: TestClient):
    a = new_asset(client)
    body = client.post(f"/api/assets/{a['id']}/stages/mesh/upload?name=empty.obj", content=b"# nothing here\n").json()
    j = wait_job(client, body["job"]["id"])
    assert j["status"] == "error"
    assert mi.REFUSALS["no_mesh"] in (j["error"] or "")


def test_upload_refusals_leave_no_version(settings):
    from vgac.main import app

    with TestClient(app) as client:
        a = new_asset(client)
        r = client.post(f"/api/assets/{a['id']}/stages/mesh/upload?name=model.glb", content=b"not a glb at all")
        assert r.status_code == 400 and "not a readable GLB" in r.json()["detail"]
        r = client.post(f"/api/assets/{a['id']}/stages/mesh/upload?name=model.blend", content=b"BLENDER")
        assert r.status_code == 400
        r = client.post(f"/api/assets/{a['id']}/stages/mesh/upload?name=x.zip", content=obj_zip({"../up.txt": b"x"}))
        assert r.status_code == 400
        asset = client.get(f"/api/assets/{a['id']}").json()
        assert not (asset.get("stages") or {}).get("mesh")


def test_a_material_takes_no_model(settings):
    from vgac.main import app

    with TestClient(app) as client:
        p = client.post("/api/projects", json={"name": "M"}).json()
        a = client.post(f"/api/projects/{p['id']}/assets", json={"name": "Stone", "category": "material",
                                                                 "style_preset": "painted", "prompt": "stone"}).json()
        r = client.post(f"/api/assets/{a['id']}/stages/mesh/upload?name=m.glb", content=b"glTF\x02\x00\x00\x00")
        assert r.status_code == 400


@needs_blender
def test_step_4_cleans_up_an_imported_model(client: TestClient):
    a = new_asset(client)
    body = client.post(f"/api/assets/{a['id']}/stages/mesh/upload?name=boxes.zip", content=obj_zip()).json()
    assert wait_job(client, body["job"]["id"])["status"] == "done"
    r = client.post(f"/api/assets/{a['id']}/stages/prep/run", json={"params": {}})
    assert r.status_code in (200, 202), r.text
    jobs = r.json().get("jobs") or [r.json().get("job")]
    j = wait_job(client, jobs[0]["id"])
    assert j["status"] == "done", j
    prep = client.get(f"/api/assets/{a['id']}").json()["stages"]["prep"][0]
    assert prep["parent_version_id"] == body["version"]["id"] and "glb" in prep["files"]


def test_a_build_from_an_imported_model_drops_the_import_settings():
    from vgac.stages import mesh

    parent = {"source": "upload", "engine": "upload", "file_name": "x.glb", "turn": 180, "texture_size": 1024,
              "hair_source": "model", "seed": 5}
    merged = mesh.inherit(parent, {"target_faces": 20000})
    assert merged == {"seed": 5, "target_faces": 20000}
