"""Retro 64, row K of the look research (2026-11-16): the late-90s add-on in the recipe at normal strength, no palette
sentence, look words without cube, voxel or glossy words; the add-on is a required download, and a missing file is
said plainly, both in the setup check and before step 1 draws anything."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vgac import preflight, reference
from vgac.stages import concept

from test_setup_check import DEV_3080, NodeClient, all_model_files, by_id

ROOT = Path(__file__).resolve().parents[2]


def test_the_recipe_loads_the_late_90s_add_on_at_normal_for_retro64_only():
    for plat in [p["id"] for p in reference.platforms()]:
        rec = reference.recipe("retro64", "humanoid", plat)
        assert {"id": "retro_lowpoly", "strength": "normal"} in rec["addons"]
    applied = reference.resolve_addons("zturbo", "concept", reference.recipe("retro64", "humanoid", "pc_mid")["addons"], [], None)["applied"]
    a = next(x for x in applied if x["id"] == "retro_lowpoly")
    assert a["value"] == 0.6 and a["source"] == "recipe" and a["file"].endswith("zit_ps1_redmond.safetensors")
    # the person can still switch it off for one picture
    off = reference.resolve_addons("zturbo", "concept", reference.recipe("retro64", "humanoid", "pc_mid")["addons"], [],
                                   {"retro_lowpoly": "off"})["applied"]
    assert all(x["id"] != "retro_lowpoly" for x in off)
    for sid in [s["id"] for s in reference.styles() if s["id"] != "retro64"]:
        assert all(x["id"] != "retro_lowpoly" for x in reference.recipe(sid, "humanoid", "pc_mid")["addons"])


def test_retro64_words_and_palette():
    s = reference.style("retro64")
    assert s["prompt_block"] == " ".join(s["lock"]["prompt_block"].split())
    words = s["prompt_block"].lower()
    for gone in ("cube", "voxel", "pixel", "glossy", "specular"):
        assert gone not in words
    # the colour-holding and plain-backdrop sentences were tested (row J) and did nothing measurable: not in the words
    assert "every part keeps" not in words and "spotlight" not in words
    assert s["lock"]["palette"] == []
    preset = next(p for p in reference.style_presets() if p["id"] == "retro64")
    assert preset["style_lock"]["palette"] == [] and concept.palette_sentence(preset["style_lock"]["palette"]) == ""


def test_the_add_on_is_in_the_required_starter_set_with_the_same_pinned_file():
    d = json.loads((ROOT / "setup" / "downloads.json").read_text(encoding="utf-8"))
    sets = {s["id"]: s for s in d["sets"]}
    assert sets["starter"]["required"] and "addon:retro_lowpoly" in sets["starter"]["items"]
    assert all("addon:retro_lowpoly" not in s["items"] for k, s in sets.items() if k != "starter")
    e = d["addons"]["retro_lowpoly"]
    assert e["url"].startswith("https://huggingface.co/artificialguybr/PS1Graphics-Redmond-ZimageTURBO/resolve/main/")
    assert e["sha256"] == "668fbbb6212c4ce2e1b10784e9dbd9182dfba4bba82c1bbb5b827647e4354953" and e["size"] == 85094928
    assert e["dest"] == "loras/VGAC/zit_ps1_redmond.safetensors"


def test_look_addons_lists_the_files_looks_load():
    rows = reference.look_addons("concept")
    assert [r["id"] for r in rows] == ["retro_lowpoly"] and rows[0]["looks"] == ["Retro 64"]
    assert reference.look_addons("turnaround") == []


class Engine:
    def __init__(self, files: list[str] | None, fail: bool = False) -> None:
        self.files, self.fail = files, fail

    async def object_info(self, cls: str) -> Any:
        if self.fail:
            raise ConnectionError("down")
        if self.files is None:
            return None
        return {"input": {"required": {"lora_name": [self.files, {}]}}}


def _sc(engine: Engine) -> Any:
    return SimpleNamespace(client=lambda instance=None: engine, style=reference.style("retro64"))


def _applied() -> list[dict[str, Any]]:
    rec = reference.recipe("retro64", "humanoid", "pc_mid")
    return reference.resolve_addons("zturbo", "concept", rec["addons"], [], None)["applied"]


def test_step_one_finds_the_missing_add_on_before_drawing_and_says_how_to_get_it():
    missing = asyncio.run(concept.missing_look_addons(_sc(Engine(["other.safetensors"])), _applied()))
    assert [a["id"] for a in missing] == ["retro_lowpoly"]
    msg = concept.missing_look_addons_message(missing, "Retro 64")
    assert "Retro 64 look needs an add-on that is not installed" in msg and "zit_ps1_redmond.safetensors" in msg
    assert "Starter set" in msg and "Setup-Extras.ps1 -Models -Install" in msg and "restart ComfyUI" in msg
    # installed (any subfolder, any case), an engine that cannot be asked, or a look with no add-on: nothing to say
    assert asyncio.run(concept.missing_look_addons(_sc(Engine(["VGAC\\ZIT_PS1_REDMOND.safetensors"])), _applied())) == []
    assert asyncio.run(concept.missing_look_addons(_sc(Engine(None, fail=True)), _applied())) == []
    assert asyncio.run(concept.missing_look_addons(_sc(Engine(None)), _applied())) == []
    painted = reference.resolve_addons("zturbo", "concept", reference.recipe("painted", "humanoid", "pc_mid")["addons"], [], None)["applied"]
    assert asyncio.run(concept.missing_look_addons(_sc(Engine([])), painted)) == []


def test_generate_stops_before_the_picture_is_queued(monkeypatch):
    queued: list[Any] = []

    class SC:
        def __init__(self) -> None:
            self.style = reference.style("retro64")
            self.project = SimpleNamespace(style_lock=reference.style("retro64")["lock"])
            self.asset = SimpleNamespace(prompt="A knight", parts=[], style_preset="retro64", category="humanoid", id="a" * 8)
            self.category = reference.category("humanoid")
            self.models_used: dict[str, Any] = {}

        def client(self, instance: str | None = None) -> Engine:
            return Engine([])

        def set_params(self, **kw: Any) -> None:
            pass

        def set_metrics(self, **kw: Any) -> None:
            pass

        def use_model(self, m: str) -> None:
            pass

        async def comfy(self, *a: Any, **kw: Any) -> Any:
            queued.append(a)
            return {}

    monkeypatch.setattr(concept, "stage_recipe", lambda sc: reference.recipe("retro64", "humanoid", "pc_mid"))
    monkeypatch.setattr(concept, "build_prompt", lambda sc, params: "A knight.")
    with pytest.raises(RuntimeError, match="Retro 64 look needs an add-on"):
        asyncio.run(concept._generate(SC(), {"seed": 1}))
    assert queued == []


def test_setup_check_warns_for_the_missing_add_on_and_says_which_look(settings):
    have = [f for f in all_model_files() if "zit_ps1_redmond" not in f]
    ids = by_id(asyncio.run(preflight.check_instance(settings, "b", NodeClient("http://127.0.0.1:10", DEV_3080, files=have), set())))
    row = ids["addon.b.retro_lowpoly"]
    assert row["state"] == "warn" and row["needed_for"] == ["concept"]
    assert "pictures in the Retro 64 look cannot be made" in row["detail"] and "Every other look works" in row["detail"]
    assert "Starter set" in row["fix"] and "Setup-Extras.ps1 -Models -Install" in row["fix"]
    ok = by_id(asyncio.run(preflight.check_instance(settings, "b", NodeClient("http://127.0.0.1:10", DEV_3080, files=all_model_files()), set())))
    assert ok["addon.b.retro_lowpoly"]["state"] == "ok"
    # the 3D engine runs no pictures: no row there
    a_ids = by_id(asyncio.run(preflight.check_instance(settings, "a", NodeClient("http://127.0.0.1:9", DEV_3080, files=all_model_files()), set())))
    assert "addon.a.retro_lowpoly" not in a_ids
