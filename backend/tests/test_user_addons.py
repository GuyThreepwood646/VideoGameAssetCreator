"""Your own add-ons (user_addons.py): the user registry merged into reference.py, a LoRA chosen from one of the person's LoRA
folders and used where it is, the header-based prefill, and the add-on reaching the concept graph."""

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

    loras = tmp_path / "loras"
    loras.mkdir(exist_ok=True)
    machine = tmp_path / "machine.json"
    machine.write_text(json.dumps({"backend": {"lora_folders": [str(loras)]}}), encoding="utf-8")
    monkeypatch.setenv("VGAC_MACHINE", str(machine))
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
def mine(mine_settings, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The person's one LoRA folder (saved in machine.json). ComfyUI's list is faked: every LoRA under the saved folders."""
    from vgac import config, user_addons

    folder = Path(config.get_settings().lora_folders[0])

    def fake_choices():
        return [p.relative_to(folder).as_posix() for p in folder.rglob("*.safetensors")]

    monkeypatch.setattr(user_addons, "_engine_choices", fake_choices)
    return folder


def register(file_name, label, base_models, **kw):
    """The add-on set up from a LoRA in one of the saved folders (folder not named: the one file is found by its name)."""
    from vgac import user_addons

    return user_addons.register(file_name, "", label, base_models, **kw)


def put_file(mine: Path, name: str, data: bytes | None = None) -> Path:
    p = mine / name
    p.write_bytes(data if data is not None else safetensors_bytes())
    return p


# --- Registry merge and shadowing ------------------------------------------------------------------------------------


def test_user_addon_merges_into_registry(mine):
    from vgac import reference, user_addons

    put_file(mine, "my_look.safetensors")
    e = register("my_look.safetensors", "My look", ["zbase"], trigger="mylook style", default_strength="strong")
    assert e["id"].startswith("mine_") and e["mine"] is True
    ids = [a["id"] for a in reference.addons()]
    assert e["id"] in ids and "clay_finish" in ids
    a = reference.addon(e["id"])
    assert a["file"] == "my_look.safetensors"
    assert a["strengths"] == {"subtle": 0.6, "normal": 0.8, "strong": 1.0}
    rec = reference.addon_record(e["id"])
    assert rec["mine"] is True and rec["file"] == "my_look.safetensors" and "responsible" in rec["licence"]
    # in the concept catalog of the stage schema
    concept = next(s for s in reference.stages() if s["id"] == "concept")
    catalog = next(p for p in concept["adjust_schema"] if p["name"] == "addons")["catalog"]
    assert any(c["id"] == e["id"] and c["mine"] for c in catalog)


def test_user_entry_never_shadows_builtin(mine_settings):
    from vgac import reference, user_addons

    builtin = reference.addon("clay_finish")
    # a hand-edited registry that tries to reuse a built-in id, or a non-"mine_" id
    user_addons.registry_path().write_text(json.dumps({"addons": [
        {"id": "clay_finish", "label": "Fake", "file": "x.safetensors", "base_models": ["zturbo"]},
        {"id": "mine_clay_finish", "label": "Mine", "file": "y.safetensors", "placement": {"mode": "in_place", "path": "F:/loras/y.safetensors"}, "base_models": ["zturbo"]},
        {"id": "mine_escape", "label": "Escape", "file": "../../other.safetensors", "placement": {"mode": "in_place", "path": "F:/loras/other.safetensors"}, "base_models": ["zturbo"]},
        {"id": "mine_elsewhere", "label": "Elsewhere", "file": "other/thing.safetensors", "placement": {"mode": "in_place", "path": "F:/loras/thing.safetensors"}, "base_models": ["zturbo"]},
    ]}), encoding="utf-8")
    assert reference.addon("clay_finish") is builtin
    assert reference.addon("mine_clay_finish")["label"] == "Mine"
    assert reference.addon("mine_escape") is None and reference.addon("mine_elsewhere")["file_name"] == "thing.safetensors"
    assert [a["id"] for a in reference.addons()].count("clay_finish") == 1


def test_new_id_avoids_builtin_and_taken(mine, monkeypatch):
    from vgac import reference, user_addons

    put_file(mine, "a.safetensors")
    put_file(mine, "b.safetensors")
    first = register("a.safetensors", "Clay finish", ["zturbo"])
    second = register("b.safetensors", "Clay finish", ["zturbo"])
    assert first["id"] == "mine_clay_finish" and second["id"] == "mine_clay_finish_2"
    assert reference.addon("clay_finish")["file"].startswith("VGAC/zit_")


def test_register_checks_the_form(mine):
    from vgac.user_addons import AddonFileError

    put_file(mine, "a.safetensors")
    with pytest.raises(AddonFileError, match="at least one model"):
        register("a.safetensors", "A", [])
    with pytest.raises(AddonFileError, match="Unknown model"):
        register("a.safetensors", "A", ["sdxl"])
    with pytest.raises(AddonFileError, match="name"):
        register("a.safetensors", "  ", ["zturbo"])
    with pytest.raises(AddonFileError, match="Subtle"):
        register("a.safetensors", "A", ["zturbo"], default_strength="max")
    with pytest.raises(AddonFileError, match="LoRA folders"):
        register("../a.safetensors", "A", ["zturbo"])
    with pytest.raises(AddonFileError) as e:
        register("missing.safetensors", "A", ["zturbo"])
    assert e.value.status == 404
    register("a.safetensors", "A", ["zturbo"])
    with pytest.raises(AddonFileError) as e:
        register("a.safetensors", "A again", ["zturbo"])
    assert e.value.status == 409


def test_register_picture_edit_lora_is_edit_stage_and_kept_out_of_concept(mine):
    from vgac import reference, user_addons

    put_file(mine, "edit.safetensors", safetensors_bytes({"modelspec.architecture": "qwen-image-edit-2511/lora"}))
    e = register("edit.safetensors", "Edit look", [reference.EDIT_MODEL])
    assert e["base_models"] == ["qwen_edit"] and e["warning"] == "" and e["stage"] == "edit"
    a = reference.addon(e["id"])
    assert a["stage"] == "edit" and a["made_for"] == "qwen_image_edit"
    concept = next(s for s in reference.stages() if s["id"] == "concept")
    assert e["id"] not in [c["id"] for c in next(p for p in concept["adjust_schema"] if p["name"] == "addons")["catalog"]]
    # never applied to a painter's concept step, applied to picture edits at its strength
    assert reference.resolve_addons("zturbo", "concept", None, None, {e["id"]: "normal"})["applied"] == []
    applied = reference.resolve_addons(reference.EDIT_MODEL, "edit", None, None, {e["id"]: "normal"})["applied"]
    assert [x["id"] for x in applied] == [e["id"]] and applied[0]["value"] == 0.8
    assert {"id": "qwen_edit", "label": "Picture edits (Qwen-Image-Edit)"} in user_addons.listing()["edit_models"]


def test_register_plain_qwen_warns_and_made_for_comes_from_header(mine):
    from vgac import reference, user_addons

    plain = safetensors_bytes({"modelspec.architecture": "qwen-image/lora"})
    put_file(mine, "plain.safetensors", plain)
    e = register("plain.safetensors", "Plain", [reference.EDIT_MODEL])
    assert "Try it at Subtle first" in e["warning"]
    assert user_addons.listing()["addons"][0]["warning"] == e["warning"]
    assert reference.addon(e["id"])["made_for"] == "qwen_image"
    # the form says painter, but the header says plain Qwen-Image: made_for still comes from the header, no edit warning
    put_file(mine, "plain2.safetensors", plain)
    p = register("plain2.safetensors", "Plain two", ["zturbo"])
    assert reference.addon(p["id"])["made_for"] == "qwen_image" and p["warning"] == ""


def test_register_refuses_mixing_and_other_families_for_edits(mine):
    from vgac.user_addons import AddonFileError

    put_file(mine, "x.safetensors", safetensors_bytes({"modelspec.architecture": "qwen-image-edit/lora"}))
    with pytest.raises(AddonFileError, match="either for picture edits or for the painters"):
        register("x.safetensors", "X", ["qwen_edit", "zturbo"])
    put_file(mine, "z.safetensors", safetensors_bytes({"modelspec.architecture": "z-image-base/lora"}))
    with pytest.raises(AddonFileError, match="cannot use"):
        register("z.safetensors", "Z", ["qwen_edit"])


# --- resolve_addons and the concept graph ----------------------------------------------------------------------------


def test_resolve_applies_user_addon_only_for_its_painters(mine):
    from vgac import reference, user_addons

    put_file(mine, "mine1.safetensors")
    e = register("mine1.safetensors", "Mine one", ["zbase"], trigger="minestyle")
    on = reference.resolve_addons("zbase", "concept", [], None, {e["id"]: "normal"})
    assert [a["id"] for a in on["applied"]] == [e["id"]]
    assert on["applied"][0]["value"] == 0.8 and on["applied"][0]["file"] == "mine1.safetensors"
    off = reference.resolve_addons("zturbo", "concept", [], None, {e["id"]: "normal"})
    assert off["applied"] == [] and off["ignored"][0]["id"] == e["id"]
    # through the project style lock too
    lock = reference.resolve_addons("zbase", "concept", [], [f"{e['id']}:strong"], None)
    assert lock["applied"][0]["value"] == 1.0


def test_user_addon_with_missing_file_is_never_sent(mine):
    """A missing file would make ComfyUI answer "not in list" with every LoRA file name; it is skipped instead."""
    from vgac import reference, user_addons

    p = put_file(mine, "vanish.safetensors")
    e = register("vanish.safetensors", "Vanish", ["zturbo"])
    p.unlink()
    r = reference.resolve_addons("zturbo", "concept", [], None, {e["id"]: "normal"})
    assert r["applied"] == []
    assert r["ignored"] == [{"id": e["id"], "reason": "its file is no longer in the LoRA folder", "source": "version"}]


def test_concept_graph_gets_loader_with_user_file(mine):
    from vgac import reference, user_addons
    from vgac.graphs.concept import concept_graph

    put_file(mine, "mine2.safetensors")
    e = register("mine2.safetensors", "Mine two", ["zbase", "zturbo"], trigger="minetwo")
    applied = reference.resolve_addons("zbase", "concept", [], None, {e["id"]: "subtle"})["applied"]
    g = concept_graph("zbase", "a knight", 1, "x/concept", addons=applied)
    loaders = [n for n in g.values() if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(loaders) == 1
    assert loaders[0]["inputs"]["lora_name"] == "mine2.safetensors"
    assert loaders[0]["inputs"]["strength_model"] == 0.6
    assert any("minetwo" in str(n["inputs"].get("text", "")) for n in g.values())


def test_removed_user_addon_reads_as_unknown(mine):
    from vgac import reference, user_addons

    p = put_file(mine, "gone.safetensors")
    e = register("gone.safetensors", "Gone", ["zturbo"])
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
    with pytest.raises(AddonFileError, match="whole image model"):
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


@pytest.mark.parametrize("arch,family,warn", [
    ("qwen-image-edit-2511/lora", "Qwen-Image-Edit", False),
    ("qwen_image_edit/lora", "Qwen-Image-Edit", False),
    ("qwen-image/lora", "Qwen-Image", True),
])
def test_prefill_reads_qwen_edit_and_plain_qwen_image(mine_settings, arch, family, warn):
    from vgac.user_addons import QWEN_PLAIN_WARNING, prefill

    out = prefill({"modelspec.architecture": arch}, {LORA_KEY: {}}, "q.safetensors")
    assert out["family"] == family
    assert out["base_models"] == ["qwen_edit"]
    assert out["painter_note"] == ""
    assert out["warning"] == (QWEN_PLAIN_WARNING if warn else "")
    if warn:
        assert "Try it at Subtle first" in out["warning"]


def test_prefill_reads_qwen_edit_from_file_name(mine_settings):
    from vgac.user_addons import prefill

    out = prefill({}, {LORA_KEY: {}}, "qwen-image-edit_inkstyle.safetensors")
    assert out["family"] == "Qwen-Image-Edit" and out["base_models"] == ["qwen_edit"] and out["warning"] == ""


@pytest.mark.parametrize("name", ["qwen-edit-style.safetensors", "qie2511_x.safetensors", "my_qie2511_x.safetensors",
                                  "Qwen_Image_Edit_2511.safetensors"])
def test_prefill_reads_common_qwen_edit_names(mine_settings, name):
    from vgac.user_addons import prefill

    out = prefill({}, {LORA_KEY: {}}, name)
    assert out["family"] == "Qwen-Image-Edit" and out["base_models"] == ["qwen_edit"]


def test_prefill_metadata_beats_a_qwen_file_name(mine_settings):
    from vgac.user_addons import prefill

    out = prefill({"modelspec.architecture": "flux-1-dev/lora"}, {LORA_KEY: {}}, "flux_like_qwen_image.safetensors")
    assert out["family"] == "FLUX.1" and out["base_models"] == [] and "FLUX.1" in out["painter_note"]


def test_prefill_qwen_in_metadata_beats_other_family_in_metadata(mine_settings):
    from vgac.user_addons import prefill

    out = prefill({"modelspec.architecture": "qwen-image-edit-2511/lora", "modelspec.title": "flux-1-dev style"},
                  {LORA_KEY: {}}, "x.safetensors")
    assert out["family"] == "Qwen-Image-Edit" and out["base_models"] == ["qwen_edit"]


def test_clay_name_is_not_read_as_anima(mine_settings):
    from vgac.user_addons import prefill

    out = prefill({"name": "clayanimation"}, {LORA_KEY: {}}, "clayanimation.safetensors")
    assert "anima" not in out["base_models"]


# --- Upload ----------------------------------------------------------------------------------------------------------


# --- Taking a LoRA off the list: the file stays where it is


def test_delete_endpoint_keeps_file(client, mine):
    put_file(mine, "d.safetensors")
    e = client.post("/api/my-addons", json={"file_name": "d.safetensors", "label": "D", "base_models": ["klein"]}).json()
    r = client.delete(f"/api/my-addons/{e['id']}")
    assert r.status_code == 200 and r.json()["file_kept"] is True
    assert (mine / "d.safetensors").is_file()
    assert client.get("/api/my-addons").json()["addons"] == []
    assert client.delete("/api/my-addons/clay_finish").status_code == 400


# --- What a picture-edit LoRA was made for: the name or header decides; the person answers when they cannot tell -----

EDIT_KEYS = ["diffusion_model.transformer_blocks.0.attn.add_k_proj.lora_A.weight",
             "diffusion_model.transformer_blocks.0.attn.add_k_proj.lora_B.weight"]
NEUTRAL_KEYS = ["lora_unet_block.lora_A.weight", "lora_unet_block.lora_B.weight"]
TRAINER_BASE = {"ss_base_model_version": "qwen_image"}  # written by training tools on edit LoRAs too: not proof of plain


def _prefill(mine: Path, name: str) -> dict[str, Any]:
    from vgac import user_addons

    meta, tensors = user_addons.read_header(mine / name)
    return user_addons.prefill(meta, tensors, name)


def test_an_edit_name_wins_over_a_qwen_image_header(mine):
    from vgac import reference, user_addons

    name = "Look_QwenEdit_2511.safetensors"
    put_file(mine, name, safetensors_bytes(dict(TRAINER_BASE), keys=EDIT_KEYS))
    out = _prefill(mine, name)
    assert out["made_for"] == "qwen_image_edit" and out["family"] == "Qwen-Image-Edit" and out["warning"] == ""
    e = register(name, "Edit look", ["qwen_edit"])  # no answer needed: the file says so
    assert e["warning"] == "" and reference.addon(e["id"])["made_for"] == "qwen_image_edit"


def test_an_edit_word_in_the_name_with_a_qwen_image_header_is_an_edit(mine):
    name = "Edit_2511_look.safetensors"
    put_file(mine, name, safetensors_bytes(dict(TRAINER_BASE), keys=EDIT_KEYS))
    assert _prefill(mine, name)["made_for"] == "qwen_image_edit"


def test_no_signal_asks_and_a_plain_answer_gives_the_warning(mine):
    from vgac import reference, user_addons
    from vgac.user_addons import AddonFileError

    name = "studio_look.safetensors"
    put_file(mine, name, safetensors_bytes({}, keys=NEUTRAL_KEYS))
    out = _prefill(mine, name)
    assert out["made_for"] == "" and out["base_models"] == [] and out["warning"] == ""
    with pytest.raises(AddonFileError, match="Choose what it was made for"):
        register(name, "Studio look", ["qwen_edit"])
    e = register(name, "Studio look", ["qwen_edit"], made_for_answer="qwen_image")
    assert "Try it at Subtle first" in e["warning"] and reference.addon(e["id"])["made_for"] == "qwen_image"


def test_no_signal_with_an_edit_answer_has_no_warning(mine):
    from vgac import reference, user_addons

    name = "studio_two.safetensors"
    put_file(mine, name, safetensors_bytes({}, keys=NEUTRAL_KEYS))
    e = register(name, "Studio two", ["qwen_edit"], made_for_answer="qwen_image_edit")
    assert e["warning"] == "" and reference.addon(e["id"])["made_for"] == "qwen_image_edit"


def test_the_trainer_key_alone_is_not_proof_of_plain(mine):
    name = "studio_three.safetensors"
    put_file(mine, name, safetensors_bytes(dict(TRAINER_BASE), keys=NEUTRAL_KEYS))
    assert _prefill(mine, name)["made_for"] == ""


def test_a_sure_plain_file_stays_plain_whatever_the_form_says(mine):
    from vgac import reference, user_addons

    name = "qwen-image-style.safetensors"  # the name says plain Qwen-Image and no Edit anywhere
    put_file(mine, name, safetensors_bytes({}, keys=NEUTRAL_KEYS))
    assert _prefill(mine, name)["made_for"] == "qwen_image"
    e = register(name, "Plain look", ["qwen_edit"], made_for_answer="qwen_image_edit")
    assert reference.addon(e["id"])["made_for"] == "qwen_image" and "Try it at Subtle first" in e["warning"]


def test_the_route_takes_the_answer_and_says_when_it_is_missing(client, mine):
    name = "studio_route.safetensors"
    put_file(mine, name, safetensors_bytes({}, keys=NEUTRAL_KEYS))
    r = client.post("/api/my-addons", json={"file_name": name, "label": "Route look", "base_models": ["qwen_edit"]})
    assert r.status_code == 400 and "Choose what it was made for" in r.json()["detail"]
    r = client.post("/api/my-addons", json={"file_name": name, "label": "Route look", "base_models": ["qwen_edit"],
                                            "made_for": "qwen_image"})
    assert r.status_code == 200 and r.json()["made_for"] == "qwen_image" and "Try it at Subtle first" in r.json()["warning"]
