"""The setup check (preflight.py, GET /api/setup). Every ComfyUI is a stub that answers /object_info for one node at a
time; Blender's version comes from a stub runner. The model file choices include a name that stands for a personal
file: it must never appear in any result."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from vgac import preflight
from vgac.config import DiscoverConfig

PERSONAL = "my_private_lora_do_not_list.safetensors"
DEV_3060 = "cuda:1 NVIDIA GeForce RTX 3060 : cudaMallocAsync"
DEV_3080 = "cuda:0 NVIDIA GeForce RTX 3080 : cudaMallocAsync"


class NodeClient:
    """A ComfyUI that has every node except those in `missing`, and the model files in `files` (by loader input)."""

    def __init__(self, url: str, device: str | None, missing: set[str] = frozenset(), files: list[str] | None = None,
                 new_combo: bool = False) -> None:
        self.base_url = url
        self.device = device
        self.missing = set(missing)
        self.files = files if files is not None else []
        self.new_combo = new_combo
        self.asked: list[str] = []

    async def cached_vram_summary(self, max_age: float = 5.0) -> dict[str, Any]:
        if self.device is None:
            return {"reachable": False, "devices": []}
        return {"reachable": True, "devices": [{"name": self.device}]}

    def set_base_url(self, url: str) -> None:
        self.base_url = url

    async def object_info(self, cls: str):
        self.asked.append(cls)
        if self.device is None:
            raise ConnectionRefusedError("refused")
        if cls in self.missing:
            return None
        choices = [PERSONAL, *self.files]
        spec = ["COMBO", {"options": choices}] if self.new_combo else [choices, {}]
        inputs = {k: spec for k in ("ckpt_name", "unet_name", "lora_name", "bg_removal_name", "clip_name", "vae_name", "model_name")}
        return {"input": {"required": inputs}, "name": cls}


def all_model_files() -> list[str]:
    models = preflight.load_models()
    # Stored in subfolders, as on a real install.
    return ["Some Folder\\" + str(models[mid]["file"]) for mid in preflight.MODEL_LOADERS]


def by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in items}


def test_graph_nodes_cover_every_stage_and_the_custom_nodes():
    uses = preflight.graph_nodes()
    stages = {u["stage"] for u in uses}
    assert stages >= {"concept", "turnaround", "mesh", "rig", "judge", "material"}
    rig = next(u for u in uses if u["stage"] == "rig")
    assert {"SkinTokenRigTrimesh", "VGACLoadTrimesh"} <= set(rig["classes"]) and rig["required"]
    trellis = next(u for u in uses if u["stage"] == "mesh" and u["required"])
    assert {"Trellis2ShapeStage", "BakeTextureFromVoxel", "BakeNormalMapFromMesh", "UnwrapMesh"} <= set(trellis["classes"])
    hy3d = next(u for u in uses if u["stage"] == "mesh" and not u["required"])
    assert "Hunyuan3Dv2ConditioningMultiView" in hy3d["classes"]
    pixel = next(u for u in uses if "pixel" in u["what"])
    assert "PixelArtDetectorToImage" in pixel["classes"] and not pixel["required"]
    zturbo = next(u for u in uses if u["what"] == "the Z-Image Turbo image model")
    assert "PixelArtDetectorToImage" not in zturbo["classes"] and zturbo["required"]


def test_combo_choices_and_has_file_in_both_forms():
    old = {"input": {"required": {"ckpt_name": [["a/b.safetensors", "c.ckpt"], {}]}}}
    new = {"input": {"optional": {"ckpt_name": ["COMBO", {"options": ["x\\y.ckpt"]}]}}}
    text = {"input": {"required": {"ckpt_name": ["STRING", {}]}}}
    assert preflight.combo_choices(old, "ckpt_name") == ["a/b.safetensors", "c.ckpt"]
    assert preflight.combo_choices(new, "ckpt_name") == ["x\\y.ckpt"]
    assert preflight.combo_choices(text, "ckpt_name") is None and preflight.combo_choices(None, "x") is None
    assert preflight.has_file(["Z-Image\\B.SafeTensors"], "b.safetensors")
    assert preflight.has_file(["experiments/x/grpo_1400.ckpt"], "grpo_1400.ckpt")
    assert not preflight.has_file(["b.safetensors.bak", "other.safetensors"], "b.safetensors")


@pytest.mark.parametrize("new_combo", [False, True])
def test_instance_with_everything_installed(settings, new_combo):
    client = NodeClient("http://127.0.0.1:10", DEV_3080, files=all_model_files(), new_combo=new_combo)
    items = asyncio.run(preflight.check_instance(settings, "b", client, set()))
    assert items[0]["id"] == "engine.b" and items[0]["state"] == "ok"
    assert all(i["state"] == "ok" for i in items), [i for i in items if i["state"] != "ok"]
    ids = by_id(items)
    # b runs pictures, sides, rigging, materials and the review: their models are checked there, the mesh ones not.
    assert {"model.b.zturbo", "model.b.qwen_edit", "model.b.skintoken", "model.b.birefnet"} <= set(ids)
    assert "model.b.trellis2" not in ids
    # One node at a time, each only once.
    assert len(client.asked) == len(set(client.asked))
    assert PERSONAL not in json.dumps(items)


def test_missing_nodes_and_models_fail_or_warn(settings):
    files = [f for f in all_model_files() if "qwen-image-edit-2511-multiple-angles" not in f and "Chroma1-HD" not in f]
    client = NodeClient("http://127.0.0.1:10", DEV_3080, missing={"SkinTokenRigTrimesh", "PixelArtDetectorToImage"}, files=files)
    items = asyncio.run(preflight.check_instance(settings, "b", client, set()))
    ids = by_id(items)
    rig = next(i for i in items if i["id"].startswith("nodes.b.rig"))
    assert rig["state"] == "fail" and rig["needed_for"] == ["rig"]
    assert "missing 1 part needed for rigging (SkinToken), so rigging cannot run" in rig["detail"]
    assert rig["technical"].endswith("missing: SkinTokenRigTrimesh")
    pixel = next(i for i in items if "pixel art image model" in i["label"])
    assert pixel["state"] == "warn" and "still works with the other choices" in pixel["detail"]
    assert ids["model.b.qwen_angles_lora"]["state"] == "fail"
    assert "Download qwen-image-edit-2511-multiple-angles-lora.safetensors" in ids["model.b.qwen_angles_lora"]["fix"]
    assert ids["model.b.chroma"]["state"] == "warn"
    # The loader node of SkinToken is missing, so its model cannot be checked: unknown, not a second failure.
    assert ids["model.b.skintoken"]["state"] == "unknown"
    assert PERSONAL not in json.dumps(items)


def test_off_and_not_running_are_unknown(settings, tmp_path):
    client = NodeClient("http://127.0.0.1:9", None)
    off = asyncio.run(preflight.check_instance(settings, "a", client, {"a"}))
    assert off[0]["state"] == "unknown" and off[0]["detail"] == "Not checked: the card is off."
    assert all(i["state"] == "unknown" for i in off) and client.asked == []
    s = settings.model_copy(update={"instances": {**settings.instances, "a": settings.instances["a"].model_copy(
        update={"start_script": tmp_path / "start.ps1"})}})
    down = asyncio.run(preflight.check_instance(s, "a", client, set()))
    assert down[0]["state"] == "unknown" and down[0]["detail"] == "Not running now; it starts when needed."
    assert all(i["state"] == "unknown" for i in down)
    bare = asyncio.run(preflight.check_instance(settings, "a", client, set()))
    assert bare[0]["state"] == "unknown" and "cannot start it" in bare[0]["detail"]


def test_b_port_held_by_the_other_card_fails_with_a_fix(settings):
    inst = {
        "a": settings.instances["a"].model_copy(update={"url": "http://127.0.0.1:8188", "gpu": "RTX 3060",
                                                        "external_owner": "ComfyUI Desktop",
                                                        "discover": DiscoverConfig(gpu_match="3060", ports=[8188, 8189])}),
        "b": settings.instances["b"].model_copy(update={"url": "http://127.0.0.1:8189", "gpu": "RTX 3080",
                                                        "discover": DiscoverConfig(gpu_match="3080")}),
    }
    s = settings.model_copy(update={"instances": inst})
    b = NodeClient("http://127.0.0.1:8189", DEV_3060)

    async def probe(url):
        return {"reachable": True, "devices": [{"name": DEV_3060}]} if url.endswith(":8189") else None

    items = asyncio.run(preflight.check_instance(s, "b", b, set(), probe=probe))
    eng = items[0]
    assert eng["state"] == "fail"
    assert eng["detail"] == "ComfyUI Desktop is using port 8189 on the RTX 3060, so the app will not send the RTX 3080's work to it."
    assert eng["fix"] == "Close it or change its port so the RTX 3080 can use port 8189."
    assert b.asked == []  # nothing is asked of the wrong card
    # Instance a (configured 8188, silent) is found on 8189 by its card.
    a = NodeClient("http://127.0.0.1:8188", None)
    a_items = asyncio.run(preflight.check_instance(s, "a", a, set(), probe=probe))
    assert a.base_url == "http://127.0.0.1:8189"
    assert a_items[0]["state"] == "ok" and "found on another port" in a_items[0]["detail"]
    assert "found by its card" in a_items[0]["technical"]
    # (This stub's own requests still fail, so its parts come out as not checked.)
    assert all(i["state"] == "unknown" for i in a_items[1:])


def test_blender_checks(settings, tmp_path):
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"x")
    s = settings.model_copy(update={"blender_exe": exe})
    assert preflight.check_blender(s, lambda p: "Blender 5.2.3\nbuild date: x")["state"] == "ok"
    old = tmp_path / "old" / "blender.exe"
    old.parent.mkdir()
    old.write_bytes(b"y")
    r = preflight.check_blender(s.model_copy(update={"blender_exe": old}), lambda p: "Blender 4.2.1 LTS")
    assert r["state"] == "fail" and "4.2.1 is too old" in r["detail"] and "blender_exe" in r["fix"]
    calls: list[Path] = []

    def runner(p: Path) -> str:
        calls.append(p)
        return "Blender 5.0.0"

    new = tmp_path / "new" / "blender.exe"
    new.parent.mkdir()
    new.write_bytes(b"z")
    s3 = s.model_copy(update={"blender_exe": new})
    preflight.check_blender(s3, runner)
    preflight.check_blender(s3, runner)
    assert len(calls) == 1  # asked once per file
    gone = preflight.check_blender(s.model_copy(update={"blender_exe": tmp_path / "nope.exe"}), runner)
    assert gone["state"] == "fail" and "blender_exe" in gone["fix"]
    assert preflight.check_blender(settings, runner)["state"] == "fail"  # not set at all


def test_unity_and_clips_warn_when_missing(settings, tmp_path):
    u = preflight.check_unity(settings)
    assert [i["state"] for i in u] == ["warn", "warn"] and u[0]["needed_for"] == ["export"]
    lib = tmp_path / "lib.fbx"
    s = settings.model_copy(update={"clips_libraries": {"ual1": lib}})
    assert preflight.check_clips(s)[0]["state"] == "warn"
    lib.write_bytes(b"fbx")
    assert preflight.check_clips(s)[0]["state"] == "ok"


def test_skintoken_blender_bin(settings, tmp_path):
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"x")
    script = tmp_path / "start-b.ps1"
    s = settings.model_copy(update={"instances": {**settings.instances, "b": settings.instances["b"].model_copy(
        update={"start_script": script})}})
    assert preflight.check_skintoken_blender(s, tmp_path)["state"] == "fail"  # no script
    script.write_text("python main.py --port 8189\n", encoding="utf-8")
    r = preflight.check_skintoken_blender(s, tmp_path)
    assert r["state"] == "fail" and "SKINTOKEN_BLENDER_BIN" in r["fix"]
    script.write_text(f'$env:SKINTOKEN_BLENDER_BIN = "{tmp_path / "missing.exe"}"\n', encoding="utf-8")
    assert preflight.check_skintoken_blender(s, tmp_path)["state"] == "fail"
    script.write_text(f'$env:SKINTOKEN_BLENDER_BIN = "{blender}"\n', encoding="utf-8")
    assert preflight.check_skintoken_blender(s, tmp_path)["state"] == "ok"
    assert preflight.check_skintoken_blender(settings, tmp_path)["state"] == "warn"  # no start script: cannot tell


def test_skintoken_blender_from_engine_settings(settings, tmp_path):
    """An engine the backend starts from its settings gets SKINTOKEN_BLENDER_BIN = blender_exe; no script is read."""
    from vgac.config import EngineConfig

    blender = tmp_path / "blender.exe"
    eng = EngineConfig(comfy_dir=tmp_path)
    b = settings.instances["b"].model_copy(update={"engine": eng, "start_script": tmp_path / "gone.ps1"})
    s = settings.model_copy(update={"instances": {**settings.instances, "b": b}, "blender_exe": None})
    r = preflight.check_skintoken_blender(s, tmp_path)
    assert r["state"] == "fail" and "blender_exe" in r["fix"]
    s = s.model_copy(update={"blender_exe": blender})
    assert preflight.check_skintoken_blender(s, tmp_path)["state"] == "fail"  # set, but not there
    blender.write_bytes(b"x")
    r = preflight.check_skintoken_blender(s, tmp_path)
    assert r["state"] == "ok" and "engine" in r["technical"]


def test_disk_and_memory_thresholds(settings, tmp_path):
    class U:
        def __init__(self, gb: float) -> None:
            self.free = gb * preflight.GB

    s = settings.model_copy(update={"export_path": tmp_path / "not-yet" / "exports"})
    one = preflight.check_disks(s, lambda p: U(3))
    assert len(one) == 1  # the library and the export folder share a drive: one line
    assert one[0]["state"] == "fail" and "library_path, export_path" in one[0]["technical"]
    assert preflight.check_disks(s, lambda p: U(12))[0]["state"] == "warn"
    assert preflight.check_disks(s, lambda p: U(200))[0]["state"] == "ok"
    s2 = settings.model_copy(update={"min_free_ram_gb": 24})
    assert preflight.check_memory(s2, lambda: 10.0)["state"] == "warn"
    assert preflight.check_memory(s2, lambda: 40.0)["state"] == "ok"


def test_summary_counts_and_affected_stages():
    items = [
        preflight.item("x", "tools", "X", "fail", "d", "f", ["rig"]),
        preflight.item("y", "tools", "Y", "warn", "d", "f", ["export", "concept"]),
        preflight.item("z", "engines", "Z", "unknown", "d", "", ["mesh"]),
        preflight.item("w", "storage", "W", "ok", "d", "", ["mesh"]),
    ]
    s = preflight.summarize(items)
    assert s == {"ok": False, "fails": 1, "warns": 1, "unknown": 1, "affected": ["concept", "rig", "export"]}
    assert preflight.summarize(items[1:])["ok"] is True


def test_setup_endpoint_shape_cache_and_refresh(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from vgac import main

    runs: list[int] = []
    real = preflight.run_checks

    async def counted(*args, **kwargs):
        runs.append(1)
        kwargs["blender_runner"] = lambda p: "Blender 5.2.3"
        return await real(*args, **kwargs)

    monkeypatch.setattr(preflight, "run_checks", counted)
    monkeypatch.setattr(main, "_setup_cache", {"at": 0.0, "report": None})
    with TestClient(main.app) as c:
        r = c.get("/api/setup").json()
        assert set(r) >= {"summary", "items", "groups", "checked_at"}
        assert [g["id"] for g in r["groups"]] == ["tools", "engines", "models", "storage"]
        for it in r["items"]:
            assert set(it) - {"kit", "offer"} == {"id", "group", "label", "state", "detail", "fix", "needed_for", "technical"}  # kit and offer: optional extras
            assert it["state"] in ("ok", "warn", "fail", "unknown")
        ids = by_id(r["items"])
        assert ids["blender"]["state"] == "fail"  # the test config has no Blender
        assert ids["engine.a"]["state"] == "unknown" and ids["engine.b"]["state"] == "unknown"  # nothing answers
        assert r["summary"]["fails"] >= 1 and r["summary"]["ok"] is False and "rig" in r["summary"]["affected"]
        c.get("/api/setup")
        assert len(runs) == 1  # kept for 60 s
        c.get("/api/setup?refresh=1")
        assert len(runs) == 2
