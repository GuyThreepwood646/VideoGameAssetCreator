"""Z-Image: the publisher's files when the engine lists them, the earlier copies when it does not (zimage_fallback.py).

The engine's own lists (object_info) decide, in any subfolder and on any drive. No engine and no model file is used here."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest

from vgac import downloader, model_choices, models_setup, zimage_fallback as zf
from vgac.comfy import ComfyClient
from vgac.graphs import concept, material

TURBO_SPLIT = "Z-Image\\z_image_turbo_int8_convrot.safetensors"
BASE_SPLIT = "Z-Image\\z_image_base_int8_convrot.safetensors"
OLD_AIO = "Z-Image\\z-image-turbo-fp8-aio.safetensors"
OLD_BF16 = "Z-Image\\z-image-turbo-bf16-aio.safetensors"
OLD_BASE = "Z-Image\\z_image_int8_convrot.safetensors"
OTHER_CKPTS = ["DreamShaper_8_pruned.safetensors", "Z-Image\\zimageTurboByStable_xmasBf16.safetensors", "zImage_turbo.safetensors"]


def turbo():
    return concept.zimage_graph("a knight", 7, "x")


def test_originals_present_the_graph_is_not_touched_and_a_listed_subfolder_is_followed():
    g = turbo()
    new, note = zf.adapt(g, [TURBO_SPLIT, OLD_BASE], [OLD_AIO])
    assert new is g and note == ""  # even with the earlier files listed too
    moved, _ = zf.adapt(g, ["Elsewhere/sub/z_image_turbo_int8_convrot.safetensors"], [])
    assert moved["1"]["inputs"]["unet_name"] == "Elsewhere/sub/z_image_turbo_int8_convrot.safetensors"
    assert g["1"]["inputs"]["unet_name"] == TURBO_SPLIT  # the caller's graph is never changed in place
    base = concept.zbase_graph("a knight", 7, "x")
    same, notes = via_aliases(base, [BASE_SPLIT, OLD_BASE])
    assert same is base and notes == []


def via_aliases(graph, unets):
    """The general name swap (model_choices) with the real list's `older` files and what the engine's UNETLoader lists."""
    table = model_choices.alias_table(lambda p: False)
    lists = {k: unets for k in model_choices.wanted_classes(graph, table)}
    return model_choices.apply_aliases(graph, table, lists)


def test_only_the_earlier_files_present_turbo_becomes_the_all_in_one_checkpoint_and_base_names_the_earlier_file():
    g = turbo()
    before = copy.deepcopy(g)
    new, note = zf.adapt(g, [OLD_BASE, "Anima\\x.safetensors"], OTHER_CKPTS + [OLD_BF16, OLD_AIO])
    assert g == before
    assert new["1"] == {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": OLD_AIO}}  # fp8 first, as before
    assert "1c" not in new and "1v" not in new and "all-in-one" in note
    assert new["3"]["inputs"]["clip"] == ["1", 1] and new["4"]["inputs"]["clip"] == ["1", 1] and new["7"]["inputs"]["vae"] == ["1", 2]
    assert new["2"]["inputs"]["model"] == ["1", 0]
    # only the bf16 one is there: that one
    only_bf16, _ = zf.adapt(g, [], [OLD_BF16])
    assert only_bf16["1"]["inputs"]["ckpt_name"] == OLD_BF16
    # the same for the pixel route and the material route, which share the loaders
    for other in (concept.pixel_graph("a coin", 3, "p"), material.t2i_graph("rust", 3, 512, "m")):
        folded, _ = zf.adapt(other, [], [OLD_AIO])
        assert folded["1"]["class_type"] == "CheckpointLoaderSimple" and "1c" not in folded
        assert not any(isinstance(v, list) and v[0] in ("1c", "1v") for n in folded.values() for v in n["inputs"].values())
    base = concept.zbase_graph("a knight", 7, "x")
    swapped, notes = via_aliases(base, [OLD_BASE])
    assert swapped["1"]["inputs"]["unet_name"] == OLD_BASE and notes
    assert base["1"]["inputs"]["unet_name"] == BASE_SPLIT


def test_neither_present_the_graph_is_left_for_the_engine_to_refuse_and_an_engine_that_cannot_be_asked_changes_nothing():
    g = turbo()
    for unets, ckpts in (([], []), (["Anima\\x.safetensors"], OTHER_CKPTS), (None, None)):
        new, note = zf.adapt(g, unets, ckpts)
        assert new is g and note == ""
    base = concept.zbase_graph("a knight", 7, "x")
    assert via_aliases(base, [])[0] is base and via_aliases(base, ["Anima\\x.safetensors"])[0] is base
    assert not zf.wants({"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": OLD_AIO}}})


class FakeEngine(ComfyClient):
    """Answers /object_info from lists and takes the prompt; no network."""

    def __init__(self, unets, ckpts):
        super().__init__("http://127.0.0.1:1")
        self.lists = {"UNETLoader": ("unet_name", unets), "CheckpointLoaderSimple": ("ckpt_name", ckpts)}
        self.asked = 0

    async def object_info(self, node_class):
        self.asked += 1
        inp, items = self.lists[node_class]
        if items is None:
            raise ConnectionError("down")
        return {"input": {"required": {inp: [items, {}]}}}


def test_the_client_asks_the_engine_for_its_lists_just_before_it_queues(monkeypatch):
    engine = FakeEngine([OLD_BASE], [OLD_AIO])
    new = asyncio.run(engine._adapt_names(turbo()))
    assert new["1"]["class_type"] == "CheckpointLoaderSimple"
    asyncio.run(engine._adapt_names(turbo()))
    assert engine.asked == 2  # one question per loader, kept for a while
    # a graph that has nothing to do with Z-Image never asks
    quiet = FakeEngine([], [])
    other = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}}}
    assert asyncio.run(quiet._adapt_names(other)) is other and quiet.asked == 0
    # an engine that does not answer: the graph goes as it is
    down = FakeEngine(None, None)
    g = turbo()
    assert asyncio.run(down._adapt_names(g)) is g


# --- the models panel ----------------------------------------------------------------------------------------------------


def _list(tmp_path: Path):
    data = b"z" * 64
    ent = {"url": "http://fake/z", "page": "http://p", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "dest": "diffusion_models/Z-Image",
           "save_as": "z_image_base_int8_convrot.safetensors", "publisher": True, "older": ["diffusion_models/Z-Image/z_image_int8_convrot.safetensors"]}
    (tmp_path / "setup").mkdir()
    (tmp_path / "setup" / "downloads.json").write_text(json.dumps({"models": {"zbase": ent}, "addons": {}, "project_files": {},
                                                                    "sets": [{"id": "s", "name": "S", "items": ["model:zbase"]}], "gates": {}}), encoding="utf-8")
    return data


@pytest.fixture
def panel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    models = tmp_path / "ComfyUI" / "models"
    models.mkdir(parents=True)
    other = tmp_path / "Shared" / "models"  # a second folder the engine reads (extra model paths)
    other.mkdir(parents=True)
    data = _list(tmp_path)
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    monkeypatch.setattr(downloader, "models_root", lambda: models)
    monkeypatch.setattr(downloader, "free_bytes", lambda p: 10**13)
    yml = tmp_path / "extra.yaml"
    yml.write_text(f"shared:\n  base_path: '{other}'\n  diffusion_models: 'diffusion_models/'\n", encoding="utf-8")
    monkeypatch.setattr(models_setup, "engine_extra_paths", lambda: [yml])
    models_setup.VERIFIED.clear()
    return models, other, data


def _item():
    st = models_setup.set_status(downloader.models_root())
    return st["sets"][0]["items"][0], st["sets"][0]


def test_panel_originals_present_they_are_there_and_no_older_copy_is_mentioned(panel):
    models, other, data = panel
    (models / "diffusion_models" / "Z-Image").mkdir(parents=True)
    (models / "diffusion_models" / "Z-Image" / "z_image_base_int8_convrot.safetensors").write_bytes(data)
    it, s = _item()
    assert it["state"] == "present" and "older" not in it and s["complete"]


def test_panel_only_the_older_copy_present_the_originals_show_as_not_here_and_the_older_copy_is_named(panel):
    models, other, data = panel
    old = other / "diffusion_models" / "Z-Image" / "z_image_int8_convrot.safetensors"  # on another drive's folder
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    it, s = _item()
    assert it["state"] == "missing" and it["older"] == str(old) and not s["complete"]
    assert s["remaining"] == it["size"]  # still offered: the originals are what the set is


def test_panel_neither_present_it_is_missing_with_no_older_copy(panel):
    it, s = _item()
    assert it["state"] == "missing" and "older" not in it


def test_the_shipped_list_names_the_earlier_copies_of_both_diffusion_files():
    d = json.loads((Path(__file__).resolve().parents[2] / "setup" / "downloads.json").read_text(encoding="utf-8"))
    assert d["models"]["zturbo"]["older"] == ["checkpoints/Z-Image/z-image-turbo-fp8-aio.safetensors", "checkpoints/Z-Image/z-image-turbo-bf16-aio.safetensors"]
    assert d["models"]["zbase"]["older"] == ["diffusion_models/Z-Image/z_image_int8_convrot.safetensors"]
