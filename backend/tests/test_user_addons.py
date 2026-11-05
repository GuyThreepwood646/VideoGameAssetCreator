"""Your own add-ons (user_addons.py): the user registry merged into reference.py, the streamed upload and its checks,
the header-based prefill, the folder listing, and the add-on reaching the concept graph."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

LORA_KEY = "diffusion_model.layers.0.attention.to_k.lora_A.weight"


def safetensors_bytes(meta: dict[str, str] | None = None, keys: list[str] | None = None, extra: bytes = b"",
                      header_len_override: int | None = None) -> bytes:
    """A small valid safetensors file (or a broken one through `extra` / `header_len_override`)."""
    keys = keys or [LORA_KEY, LORA_KEY.replace("lora_A", "lora_B")]
    header: dict[str, Any] = {}
    off = 0
    for k in keys:
        header[k] = {"dtype": "F16", "shape": [4, 8], "data_offsets": [off, off + 64]}
        off += 64
    if meta is not None:
        header["__metadata__"] = meta
    raw = json.dumps(header).encode("utf-8")
    raw += b" " * (-len(raw) % 8)
    n = header_len_override if header_len_override is not None else len(raw)
    return struct.pack("<Q", n) + raw + b"\x01" * off + extra


@pytest.fixture
def mine_settings(settings_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vgac import config, db, reference

    cfg = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    cfg["loras_dir"] = str(tmp_path / "loras")
    settings_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("VGAC_CONFIG", str(settings_path))
    config.reset_settings()
    db.reset_engine()
    reference.reload()
    s = config.get_settings()
    yield s
    db.reset_engine()
    config.reset_settings()


@pytest.fixture
def client(mine_settings):
    from vgac.main import app

    return TestClient(app)  # no lifespan: these routes need no job queue


@pytest.fixture
def mine(mine_settings) -> Path:
    from vgac import user_addons

    return user_addons.mine_dir(create=True)


def put_file(mine: Path, name: str, data: bytes | None = None) -> Path:
    p = mine / name
    p.write_bytes(data if data is not None else safetensors_bytes())
    return p


# --- Registry merge and shadowing ------------------------------------------------------------------------------------


def test_user_addon_merges_into_registry(mine):
    from vgac import reference, user_addons

    put_file(mine, "my_look.safetensors")
    e = user_addons.register("my_look.safetensors", "My look", ["zbase"], trigger="mylook style", default_strength="strong")
    assert e["id"].startswith("mine_") and e["mine"] is True
    ids = [a["id"] for a in reference.addons()]
    assert e["id"] in ids and "clay_finish" in ids
    a = reference.addon(e["id"])
    assert a["file"] == "VGAC/mine/my_look.safetensors"
    assert a["strengths"] == {"subtle": 0.6, "normal": 0.8, "strong": 1.0}
    rec = reference.addon_record(e["id"])
    assert rec["mine"] is True and rec["file"] == "VGAC/mine/my_look.safetensors" and "responsible" in rec["licence"]
    # in the concept catalog of the stage schema
    concept = next(s for s in reference.stages() if s["id"] == "concept")
    catalog = next(p for p in concept["adjust_schema"] if p["name"] == "addons")["catalog"]
    assert any(c["id"] == e["id"] and c["mine"] for c in catalog)


def test_user_entry_never_shadows_builtin(mine_settings):
    from vgac import reference, user_addons

    builtin = reference.addon("clay_finish")
    # a hand-edited registry that tries to reuse a built-in id, or a non-"mine_" id
    user_addons.registry_path().write_text(json.dumps({"addons": [
        {"id": "clay_finish", "label": "Fake", "file": "VGAC/mine/x.safetensors", "base_models": ["zturbo"]},
        {"id": "mine_clay_finish", "label": "Mine", "file": "VGAC/mine/y.safetensors", "base_models": ["zturbo"]},
        {"id": "mine_escape", "label": "Escape", "file": "VGAC/mine/../../other.safetensors", "base_models": ["zturbo"]},
        {"id": "mine_elsewhere", "label": "Elsewhere", "file": "other/thing.safetensors", "base_models": ["zturbo"]},
    ]}), encoding="utf-8")
    assert reference.addon("clay_finish") is builtin
    assert reference.addon("mine_clay_finish")["label"] == "Mine"
    assert reference.addon("mine_escape") is None and reference.addon("mine_elsewhere") is None
    assert [a["id"] for a in reference.addons()].count("clay_finish") == 1


def test_new_id_avoids_builtin_and_taken(mine, monkeypatch):
    from vgac import reference, user_addons

    put_file(mine, "a.safetensors")
    put_file(mine, "b.safetensors")
    first = user_addons.register("a.safetensors", "Clay finish", ["zturbo"])
    second = user_addons.register("b.safetensors", "Clay finish", ["zturbo"])
    assert first["id"] == "mine_clay_finish" and second["id"] == "mine_clay_finish_2"
    assert reference.addon("clay_finish")["file"].startswith("VGAC/zit_")


def test_register_checks_the_form(mine):
    from vgac.user_addons import AddonFileError, register

    put_file(mine, "a.safetensors")
    with pytest.raises(AddonFileError, match="painter"):
        register("a.safetensors", "A", [])
    with pytest.raises(AddonFileError, match="Unknown painter"):
        register("a.safetensors", "A", ["sdxl"])
    with pytest.raises(AddonFileError, match="name"):
        register("a.safetensors", "  ", ["zturbo"])
    with pytest.raises(AddonFileError, match="Subtle"):
        register("a.safetensors", "A", ["zturbo"], default_strength="max")
    with pytest.raises(AddonFileError, match="without folders"):
        register("../a.safetensors", "A", ["zturbo"])
    with pytest.raises(AddonFileError) as e:
        register("missing.safetensors", "A", ["zturbo"])
    assert e.value.status == 404
    register("a.safetensors", "A", ["zturbo"])
    with pytest.raises(AddonFileError) as e:
        register("a.safetensors", "A again", ["zturbo"])
    assert e.value.status == 409


# --- resolve_addons and the concept graph ----------------------------------------------------------------------------


def test_resolve_applies_user_addon_only_for_its_painters(mine):
    from vgac import reference, user_addons

    put_file(mine, "mine1.safetensors")
    e = user_addons.register("mine1.safetensors", "Mine one", ["zbase"], trigger="minestyle")
    on = reference.resolve_addons("zbase", "concept", [], None, {e["id"]: "normal"})
    assert [a["id"] for a in on["applied"]] == [e["id"]]
    assert on["applied"][0]["value"] == 0.8 and on["applied"][0]["file"] == "VGAC/mine/mine1.safetensors"
    off = reference.resolve_addons("zturbo", "concept", [], None, {e["id"]: "normal"})
    assert off["applied"] == [] and off["ignored"][0]["id"] == e["id"]
    # through the project style lock too
    lock = reference.resolve_addons("zbase", "concept", [], [f"{e['id']}:strong"], None)
    assert lock["applied"][0]["value"] == 1.0


def test_user_addon_with_missing_file_is_never_sent(mine):
    """A missing file would make ComfyUI answer "not in list" with every LoRA file name; it is skipped instead."""
    from vgac import reference, user_addons

    p = put_file(mine, "vanish.safetensors")
    e = user_addons.register("vanish.safetensors", "Vanish", ["zturbo"])
    p.unlink()
    r = reference.resolve_addons("zturbo", "concept", [], None, {e["id"]: "normal"})
    assert r["applied"] == []
    assert r["ignored"] == [{"id": e["id"], "reason": "its file is no longer in the add-on folder", "source": "version"}]


def test_concept_graph_gets_loader_with_user_file(mine):
    from vgac import reference, user_addons
    from vgac.graphs.concept import concept_graph

    put_file(mine, "mine2.safetensors")
    e = user_addons.register("mine2.safetensors", "Mine two", ["zbase", "zturbo"], trigger="minetwo")
    applied = reference.resolve_addons("zbase", "concept", [], None, {e["id"]: "subtle"})["applied"]
    g = concept_graph("zbase", "a knight", 1, "x/concept", addons=applied)
    loaders = [n for n in g.values() if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(loaders) == 1
    assert loaders[0]["inputs"]["lora_name"] == "VGAC\\mine\\mine2.safetensors"
    assert loaders[0]["inputs"]["strength_model"] == 0.6
    assert any("minetwo" in str(n["inputs"].get("text", "")) for n in g.values())


def test_removed_user_addon_reads_as_unknown(mine):
    from vgac import reference, user_addons

    p = put_file(mine, "gone.safetensors")
    e = user_addons.register("gone.safetensors", "Gone", ["zturbo"])
    out = user_addons.remove(e["id"])
    assert out["file_kept"] is True and p.is_file()
    assert reference.addon(e["id"]) is None
    r = reference.resolve_addons("zturbo", "concept", [], None, {e["id"]: "normal"})
    assert r["applied"] == [] and r["unknown"] == [e["id"]]
    from vgac.user_addons import AddonFileError

    with pytest.raises(AddonFileError, match="cannot be removed"):
        user_addons.remove("clay_finish")
    with pytest.raises(AddonFileError) as err:
        user_addons.remove(e["id"])
    assert err.value.status == 404


# --- Header checks and prefill ---------------------------------------------------------------------------------------


def test_parse_header_refuses_non_lora_and_damage():
    from vgac.user_addons import AddonFileError, header_length, parse_header

    with pytest.raises(AddonFileError):
        header_length(b"\x01\x00")
    with pytest.raises(AddonFileError):
        header_length(struct.pack("<Q", 200_000_000))
    with pytest.raises(AddonFileError, match="not readable"):
        parse_header(b"not json at all")
    with pytest.raises(AddonFileError, match="damaged"):
        parse_header(json.dumps({"w": {"dtype": "F16", "shape": [1], "data_offsets": [8, 2]}}).encode())
    with pytest.raises(AddonFileError, match="whole painter model"):
        parse_header(json.dumps({"model.diffusion_model.x.weight": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}).encode())
    meta, tensors, end = parse_header(json.dumps({LORA_KEY: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]},
                                                  "__metadata__": {"a": "b"}}).encode())
    assert meta == {"a": "b"} and end == 2 and list(tensors) == [LORA_KEY]


@pytest.mark.parametrize("meta,name,painters,trigger_part", [
    # the tested add-ons' own metadata shape (ai-toolkit): zimage + a de-turbo name -> Z-Image Turbo
    ({"ss_base_model_version": "zimage", "name": "clayanimation_z_deturbo",
      "ss_tag_frequency": json.dumps({"1_Clay animation. Clay.": {"Clay animation. Clay.": 1}})},
     "zit_clay.safetensors", ["zturbo"], "Clay animation. Clay."),
    ({"modelspec.architecture": "z-image-base/lora", "modelspec.title": "Ink wash", "modelspec.trigger_phrase": "inkwash"},
     "x.safetensors", ["zbase"], "inkwash"),
    ({"modelspec.architecture": "flux-2-klein-4b/lora"}, "k.safetensors", ["klein"], ""),
    ({"modelspec.architecture": "chroma/lora"}, "c.safetensors", ["chroma"], ""),
    ({"ss_base_model_version": "anima"}, "an.safetensors", ["anima"], ""),
])
def test_prefill_maps_metadata_to_painters(mine_settings, meta, name, painters, trigger_part):
    from vgac.user_addons import prefill

    out = prefill(meta, {LORA_KEY: {}}, name)
    assert out["base_models"] == painters
    assert out["painter_note"] == ""
    if trigger_part:
        assert trigger_part in out["trigger"]
    if "modelspec.title" in meta:
        assert out["label"] == meta["modelspec.title"]


@pytest.mark.parametrize("meta,keys,note_part", [
    ({"ss_base_model_version": "zimage"}, [LORA_KEY], "Z-Image Turbo or Z-Image Base"),
    ({"ss_base_model_version": "sdxl_base_v1-0"}, [LORA_KEY], "Stable Diffusion XL"),
    ({"modelspec.architecture": "flux-1-dev/lora"}, [LORA_KEY], "FLUX.1"),
    ({"modelspec.architecture": "flux-2-klein-9b/lora"}, [LORA_KEY], "9B"),
    ({}, ["lora_unet_input_blocks_1.lora_down.weight", "lora_te1_x.lora_down.weight"], "Stable Diffusion"),
    ({}, [LORA_KEY], "Z-Image"),
    ({}, ["something.lora_down.weight"], "We could not tell"),
])
def test_prefill_leaves_painter_empty_when_unsure(mine_settings, meta, keys, note_part):
    from vgac.user_addons import prefill

    out = prefill(meta, {k: {} for k in keys}, "my_style_v2.safetensors")
    assert out["base_models"] == []
    assert note_part in out["painter_note"]
    assert out["label"] == "My style v2"


def test_clay_name_is_not_read_as_anima(mine_settings):
    from vgac.user_addons import prefill

    out = prefill({"name": "clayanimation"}, {LORA_KEY: {}}, "clayanimation.safetensors")
    assert "anima" not in out["base_models"]


# --- Upload ----------------------------------------------------------------------------------------------------------


def test_upload_saves_into_mine_and_prefills(client, mine):
    meta = {"ss_base_model_version": "zimage", "name": "foo_deturbo", "modelspec.trigger_phrase": "foostyle"}
    data = safetensors_bytes(meta)
    r = client.post("/api/my-addons/upload", params={"name": "Foo Style!.safetensors"}, content=data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_name"] == "Foo Style_.safetensors" and body["bytes"] == len(data)
    assert body["prefill"]["base_models"] == ["zturbo"] and body["prefill"]["trigger"] == "foostyle"
    assert (mine / "Foo Style_.safetensors").read_bytes() == data
    assert not [p for p in mine.iterdir() if p.name.endswith(".part")]
    # then the short form
    r = client.post("/api/my-addons", json={"file_name": body["file_name"], "label": "Foo", "base_models": ["zturbo"],
                                             "trigger": "foostyle", "default_strength": "subtle", "source": "https://example.com/foo"})
    assert r.status_code == 200, r.text
    listed = client.get("/api/my-addons").json()
    assert [a["label"] for a in listed["addons"]] == ["Foo"]
    assert listed["addons"][0]["source"] == "https://example.com/foo" and listed["addons"][0]["default_strength"] == "subtle"
    assert {p["id"] for p in listed["painters"]} >= {"zturbo", "zbase"}


def test_upload_refuses_duplicate_name(client, mine):
    put_file(mine, "taken.safetensors", b"original")
    r = client.post("/api/my-addons/upload", params={"name": "taken.safetensors"}, content=safetensors_bytes())
    assert r.status_code == 409
    assert (mine / "taken.safetensors").read_bytes() == b"original"


@pytest.mark.parametrize("name", ["../evil.safetensors", "..\\evil.safetensors", "C:evil.safetensors", "sub/evil.safetensors",
                                  "evil.ckpt", ".safetensors", "evil.safetensors.exe"])
def test_upload_refuses_path_tricks_and_other_files(client, mine, name):
    r = client.post("/api/my-addons/upload", params={"name": name}, content=safetensors_bytes())
    assert r.status_code == 400, name
    assert list(mine.iterdir()) == []
    assert not (mine.parent / "evil.safetensors").exists()


@pytest.mark.parametrize("data", [
    b"",
    b"short",
    b"\xff" * 64,  # header length far too big
    struct.pack("<Q", 20) + b"{not json at all!!!}",
    safetensors_bytes(keys=["model.diffusion_model.w"]),  # a whole model, not a LoRA
    safetensors_bytes(extra=b"trailing"),  # extra bytes after the data
    safetensors_bytes()[:-10],  # cut short
])
def test_upload_refuses_bad_files(client, mine, data):
    r = client.post("/api/my-addons/upload", params={"name": "bad.safetensors"}, content=data)
    assert r.status_code == 400, r.text
    assert list(mine.iterdir()) == []


def test_upload_refuses_too_large(client, mine, monkeypatch):
    from vgac import user_addons

    monkeypatch.setattr(user_addons, "MAX_FILE_BYTES", 100)
    r = client.post("/api/my-addons/upload", params={"name": "big.safetensors"}, content=safetensors_bytes())
    assert r.status_code == 413
    assert list(mine.iterdir()) == []


def test_upload_refuses_foreign_origin(client, mine):
    r = client.post("/api/my-addons/upload", params={"name": "x.safetensors"}, content=safetensors_bytes(),
                    headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert list(mine.iterdir()) == []


# --- Already on this computer: the one folder ---------------------------------------------------------------------


def test_unregistered_lists_only_mine_folder(client, mine):
    from vgac import user_addons

    put_file(mine, "a.safetensors", safetensors_bytes({"modelspec.title": "Alpha"}))
    put_file(mine, "broken.safetensors", b"nope")
    put_file(mine, "notes.txt", b"hello")
    put_file(mine, ".upload-123.part", b"partial")
    put_file(mine.parent, "sibling.safetensors")  # VGAC/, outside mine: never listed
    user_addons.register("a.safetensors", "Alpha", ["zturbo"])
    put_file(mine, "b.safetensors", safetensors_bytes({"modelspec.title": "Beta"}))
    items = client.get("/api/my-addons/unregistered").json()
    names = [i["file_name"] for i in items]
    assert names == ["b.safetensors", "broken.safetensors"]
    assert items[0]["prefill"]["label"] == "Beta"
    assert "error" in items[1]


def test_unregistered_empty_without_folder(client, mine_settings):
    assert client.get("/api/my-addons/unregistered").json() == []


def test_open_folder_opens_only_mine(client, mine_settings, monkeypatch):
    from vgac import user_addons

    opened: list[Path] = []
    monkeypatch.setattr(user_addons, "_launch_explorer", lambda p: opened.append(p))
    r = client.post("/api/my-addons/open-folder")
    assert r.status_code == 200
    assert opened == [user_addons.mine_dir()] and opened[0].is_dir()
    assert opened[0].parts[-2:] == ("VGAC", "mine")


def test_delete_endpoint_keeps_file(client, mine):
    put_file(mine, "d.safetensors")
    e = client.post("/api/my-addons", json={"file_name": "d.safetensors", "label": "D", "base_models": ["klein"]}).json()
    r = client.delete(f"/api/my-addons/{e['id']}")
    assert r.status_code == 200 and r.json()["file_kept"] is True
    assert (mine / "d.safetensors").is_file()
    assert client.get("/api/my-addons").json()["addons"] == []
    assert client.delete("/api/my-addons/clay_finish").status_code == 400
