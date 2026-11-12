"""Your own hairstyles (my_hair.py): the upload checks, the registry, the merge into the hair library, the routes.

Blender is replaced by a fake reader that writes the same files blender/hairimport.py writes, so these run without
Blender; the real reader is exercised by the real fit recorded in docs/ui-review-impl.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from vgac import hairstyles, my_hair

GLB = b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 120


def fake_import(result: dict[str, Any] | None = None, error: str | None = None):
    """A stand-in for my_hair.run_import: same files in the same places, or a refusal in plain words."""

    def run(settings, src, sid, folder):
        if error:
            raise my_hair.HairError(my_hair.REFUSALS[error])
        folder.mkdir(parents=True, exist_ok=True)
        q = np.zeros((4, 3), np.float32)
        for name in (f"{sid}.npz", f"{sid}_lite.npz"):
            np.savez_compressed(folder / name, q=q, rho_r=q[:, 0], tris=np.zeros((1, 3), np.int32), uv=np.zeros((1, 3, 2), np.float32),
                                role=np.zeros(4, np.int8), flow=q)
        (folder / "thumbs").mkdir(exist_ok=True)
        for v in ("front", "quarter"):
            Image.new("RGB", (8, 8), "grey").save(folder / "thumbs" / f"{v}.png")
        Image.merge("LA", (Image.new("L", (4, 4), 128), Image.new("L", (4, 4), 255))).save(folder / "grey.png")
        return result or dict(triangles=3000, source_triangles=3000, vertices=3100, lite_triangles=700, hang_groups=[], length_rel_head=0.9,
                              self_check_mm=0.0, kind="shell", has_texture=False)

    return run


@pytest.fixture
def client(settings, monkeypatch):
    from vgac.main import app

    monkeypatch.setattr(my_hair, "run_import", fake_import())
    with TestClient(app) as c:
        yield c


def add(client, name="wavy.glb", body=GLB, **q):
    params = {"name": name, "label": "Wavy", "family": "short", **q}
    return client.post("/api/my-hair/upload", params=params, content=body)


# --- names and headers -------------------------------------------------------------------------------------------------


def test_file_names_must_be_plain_and_a_hair_kind():
    assert my_hair.safe_upload_name("My Hair.GLB") == "My Hair.glb"
    for bad in ("../x.glb", "a/b.glb", "a\\b.glb", "x.obj", "x.exe", "", "a:b.glb"):
        with pytest.raises(my_hair.HairError):
            my_hair.safe_upload_name(bad)
    assert my_hair.safe_upload_name("con.glb").startswith("hair_")


def test_the_header_must_match_the_kind():
    assert my_hair.sniff_kind(GLB, ".glb") == "glb"
    assert my_hair.sniff_kind(b'{"asset": {"version": "2.0"}}', ".gltf") == "gltf"
    assert my_hair.sniff_kind(b"Kaydara FBX Binary  \x00", ".fbx") == "fbx"
    for first, ext in ((b"not a glb at all", ".glb"), (b"glTF....", ".fbx"), (b"MZ\x90\x00", ".glb"), (b"<html>", ".gltf")):
        with pytest.raises(my_hair.HairError):
            my_hair.sniff_kind(first, ext)


def test_a_gltf_that_points_at_other_files_is_refused(tmp_path: Path):
    f = tmp_path / "a.gltf"
    f.write_text(json.dumps({"asset": {}, "buffers": [{"uri": "a.bin"}]}))
    assert not my_hair.gltf_is_self_contained(f)
    f.write_text(json.dumps({"asset": {}, "buffers": [{"uri": "data:application/octet-stream;base64,AAAA"}]}))
    assert my_hair.gltf_is_self_contained(f)


# --- add, list, change, remove -----------------------------------------------------------------------------------------


def test_adding_a_hairstyle_registers_it_with_a_mine_id_and_its_files(client, settings):
    r = add(client, source="Quaternius, quaternius.com", licence="CC0")
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["id"].startswith("mine_") and e["name"] == "Wavy" and e["family"] == "short" and e["licence"] == "CC0"
    assert e["thumbs"]["front"].startswith("/api/hair-library/files/mine/mine_")
    folder = Path(settings.library_path) / "my_hair" / e["id"]
    assert (folder / f"{e['id']}.npz").is_file() and (folder / "grey.png").is_file() and not list(folder.glob("*.glb"))
    listed = client.get("/api/my-hair").json()
    assert [s["id"] for s in listed["styles"]] == [e["id"]] and "responsibility" in listed
    saved = json.loads((Path(settings.library_path) / "my_hair" / "my_hair.json").read_text())
    assert saved["styles"][e["id"]]["own"] is True


def test_an_own_id_never_shadows_a_built_in_and_names_are_unique(client):
    a = add(client).json()
    assert a["id"] not in hairstyles._load(str(hairstyles.DATA_DIR))["styles"]
    again = add(client)
    assert again.status_code == 400 and "already have" in again.json()["detail"]
    b = add(client, name="other.glb", label="Wavy 2").json()
    assert b["id"] != a["id"]


def test_the_form_is_checked_in_plain_words(client):
    blank = add(client, name="long fringe.glb", label="")       # no name given: the file's own name is used
    assert blank.status_code == 200 and blank.json()["name"] == "long fringe"
    r = add(client, family="mullet")
    assert r.status_code == 400 and "kind of hairstyle" in r.json()["detail"]
    r = add(client, licence="whatever")
    assert r.status_code == 400 and "licence" in r.json()["detail"]
    assert add(client, name="x.exe").status_code == 400


def test_bad_uploads_are_refused_and_leave_nothing_behind(client, settings):
    for body, name, want in ((b"", "a.glb", "empty"), (b"not a glb", "a.glb", "not a readable GLB"), (GLB, "a.png", "GLB, glTF or FBX")):
        r = add(client, name=name, body=body, label="Bad")
        assert r.status_code == 400 and want in r.json()["detail"], r.text
    root = Path(settings.library_path) / "my_hair"
    assert not list(root.glob("mine_*")) and client.get("/api/my-hair").json()["styles"] == []


def test_a_file_over_the_size_cap_is_refused(client, monkeypatch):
    monkeypatch.setattr(my_hair, "MAX_FILE_BYTES", 100)
    r = add(client, body=GLB + b"\x00" * 200)
    assert r.status_code == 413 and "larger than" in r.json()["detail"]


@pytest.mark.parametrize("code", sorted(my_hair.REFUSALS))
def test_every_reason_the_reader_refuses_is_a_plain_sentence(client, monkeypatch, code):
    monkeypatch.setattr(my_hair, "run_import", fake_import(error=code))
    r = add(client, label="Nope")
    assert r.status_code == 400
    assert r.json()["detail"] == my_hair.REFUSALS[code] and r.json()["detail"].endswith(".")
    assert not any(w in r.json()["detail"] for w in ("Traceback", "bpy", "Exception"))


def test_the_message_for_a_file_with_no_mesh_is_the_one_the_owner_asked_for():
    assert my_hair.REFUSALS["no_mesh"] == "This file has no mesh we can use as hair."


def test_rename_and_change_the_source(client):
    sid = add(client).json()["id"]
    r = client.put(f"/api/my-hair/{sid}", json={"label": "Curly one", "family": "afro", "curly": True, "fringe": True, "source": "https://example.org/hair",
                                                 "licence": "CC-BY"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["name"], e["family"], e["curly"], e["fringe"], e["licence"]) == ("Curly one", "afro", True, True, "CC-BY")
    assert client.put("/api/my-hair/mine_nope", json={"label": "x"}).status_code == 404
    other = add(client, name="b.glb", label="Second").json()["id"]
    assert client.put(f"/api/my-hair/{other}", json={"label": "curly one"}).status_code == 400


def test_remove_deletes_the_files_and_a_character_using_it_blocks_the_removal(client, settings, monkeypatch):
    sid = add(client).json()["id"]
    monkeypatch.setattr(my_hair, "_users_of", lambda s: ["Knight"])
    blocked = client.delete(f"/api/my-hair/{sid}")
    assert blocked.status_code == 409 and "Knight" in blocked.json()["detail"]
    monkeypatch.setattr(my_hair, "_users_of", lambda s: [])
    assert client.delete(f"/api/my-hair/{sid}").json() == {"removed": sid}
    assert not (Path(settings.library_path) / "my_hair" / sid).exists() and client.get("/api/my-hair").json()["styles"] == []
    assert client.delete(f"/api/my-hair/{sid}").status_code == 404


# --- the merge into the library ----------------------------------------------------------------------------------------


def test_own_styles_join_the_catalogue_the_picker_and_the_fit_read(client):
    e = add(client, fringe=True, source="my friend", licence="My own work").json()
    sid = e["id"]
    cat = hairstyles.catalogue()["styles"]
    assert sid in cat and cat[sid]["own"] and len(cat) > 20           # built-in styles are still there
    assert hairstyles.style(sid)["name"] == "Wavy"
    assert sid in {s["id"] for s in hairstyles.styles_for("short", "pc_mid", "painted")}
    assert hairstyles.styles_for("short", "pc_mid", "pixel") == []    # looks with no library offer none, own included
    assert sid in {s["id"] for s in hairstyles.styles_for(None, "mobile_low", None)}   # lite variant of 700 triangles
    p = hairstyles.paths(sid)
    assert p["npz"].is_file() and p["lite"].is_file() and p["grey"].is_file() and p["thumb_front"].is_file() and p["normal"] is None
    lib = client.get("/api/hair-library", params={"platform": "pc_mid"}).json()
    row = next(s for s in lib["styles"] if s["id"] == sid)
    assert row["own"] is True and row["credit"]["author"] == "You" and row["credit"]["licence"] == "My own work"


def test_the_hair_library_serves_own_thumbnails_and_nothing_outside(client):
    e = add(client).json()
    assert client.get(e["thumbs"]["front"]).status_code == 200
    for bad in ("mine/../../vgac.sqlite", "mine/mine_wavy/../../x", "mine/%2e%2e/%2e%2e/vgac.sqlite", "mine/other/x.png", "mine/"):
        assert client.get("/api/hair-library/files/" + bad).status_code == 404, bad


def test_resolve_never_leaves_the_own_folder(settings):
    for bad in ("mine/../x", "mine/mine_a/../../x", "mine/mine_a/C:/x", "style/x", "mine/mine_a", "mine/Mine_A/x.png", "mine//x"):
        with pytest.raises(ValueError):
            my_hair.resolve(bad)
    assert my_hair.resolve("mine/mine_a/thumbs/front.png").is_relative_to(Path(settings.library_path).resolve())


def test_a_broken_registry_file_never_takes_the_library_away(settings):
    root = my_hair.root(settings)
    root.mkdir(parents=True)
    (root / "my_hair.json").write_text("{not json")
    assert my_hair.entries() == {}
    assert len(hairstyles.catalogue()["styles"]) > 20


def test_own_hair_is_credited_with_its_licence(client):
    from vgac import credits

    add(client, source="Cortu, example.org", licence="CC-BY")
    hair = next(g for g in credits.credits()["groups"] if g["id"] == "hair")["entries"]
    mine = [e for e in hair if "Wavy" in e["name"]]
    assert mine and "CC-BY" in str(mine[0])


def test_the_crown_measure_matches_between_the_library_and_the_reader():
    q = np.array([[-1, 0, 0], [1, 0, 0], [-1.2, 0.2, 1.0], [1.2, 0.4, 1.0], [0, 0.3, 1.2]], float)
    w, y, z = my_hair.crown(q)
    assert z == pytest.approx(1.2)
    assert w == pytest.approx(2.4) and y == pytest.approx(0.3)        # top 40 percent: the three upper points


# --- run_import with a stand-in for the Blender call (it writes the file names blender/hairimport.py writes) -------------


def _blender_stub(result: dict[str, Any]):
    def run(settings, script, args, log_path=None, timeout=None, on_start=None):
        assert script == "hairimport.py" and args[0] == "import"
        job = json.loads(Path(args[1]).read_text())
        out, sid = Path(job["out_dir"]), job["id"]
        assert Path(job["reference"]).is_file() and set(job["stats"]) == {"w", "ycen", "ztop"}
        (out / "thumbs").mkdir(exist_ok=True)
        q = np.zeros((4, 3), np.float32)
        for n in (f"{sid}.npz", f"{sid}_lite.npz"):
            np.savez_compressed(out / n, q=q)
        for v in ("front", "quarter"):
            Image.new("RGB", (8, 8)).save(out / "thumbs" / f"{sid}_{v}.png")
        Image.new("RGBA", (4, 4), (200, 100, 50, 255)).save(out / "grey_rgba.png")
        (out / "result.json").write_text(json.dumps(result))
        return ""
    return run


def test_run_import_collects_the_files_the_reader_writes(settings, monkeypatch, tmp_path: Path):
    from vgac import tools

    res = dict(triangles=100, source_triangles=100, vertices=90, lite_triangles=100, hang_groups=[], length_rel_head=1.0, self_check_mm=0.0, kind="shell", has_texture=True)
    monkeypatch.setattr(tools, "run_blender", _blender_stub(res))
    src = tmp_path / "x.glb"
    src.write_bytes(GLB)
    folder = tmp_path / "out"
    doc = my_hair.run_import(settings, src, "mine_x", folder)
    assert doc["triangles"] == 100
    assert (folder / "mine_x.npz").is_file() and (folder / "mine_x_lite.npz").is_file() and (folder / "thumbs" / "front.png").is_file()
    g = Image.open(folder / "grey.png")
    assert g.mode == "LA" and g.getpixel((0, 0))[1] == 255          # a shell's grey texture is opaque


def test_a_refusal_written_by_the_reader_becomes_its_plain_sentence(settings, monkeypatch, tmp_path: Path):
    from vgac import tools

    def run(settings, script, args, log_path=None, timeout=None, on_start=None):
        out = Path(json.loads(Path(args[1]).read_text())["out_dir"])
        (out / "result.json").write_text(json.dumps({"error": "no_mesh", "detail": ""}))
        return ""

    monkeypatch.setattr(tools, "run_blender", run)
    src = tmp_path / "x.glb"
    src.write_bytes(GLB)
    with pytest.raises(my_hair.HairError) as e:
        my_hair.run_import(settings, src, "mine_x", tmp_path / "out")
    assert e.value.message == "This file has no mesh we can use as hair."
    assert not (tmp_path / "out").exists()


def test_the_reference_head_ships_with_the_app():
    assert my_hair.REFERENCE.is_file()
    ref = np.load(my_hair.REFERENCE)
    assert ref["V"].shape[1] == 3 and ref["T"].shape[1] == 3 and 1.6 < float(ref["V"][:, 2].max()) < 1.8
