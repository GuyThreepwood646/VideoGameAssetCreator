"""How to turn the views (turnaround turn_method): standard, the character turnaround sheet and the shadow-free
camera turn (docs/RECIPES-BAKEOFF.md sections 5.1 and 5.2).

The stage runs against the small fake StageContext of test_stage_offloop.py: no database, no ComfyUI. The sheet job
and the camera jobs are replaced by fakes that write pictures, so the tests check which method draws which view,
what is recorded, and how the sheet is cut into views.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from vgac import imaging, reference
from vgac.graphs import qwen_edit
from vgac.stages import turnaround

from test_stage_offloop import FakeDB, FakeSC, _rgba


# --- the choice and where it is offered ---------------------------------------------------------------------------


def test_turn_method_is_checked_against_the_category():
    assert turnaround.turn_method("humanoid", {}) == "standard"
    assert turnaround.turn_method("humanoid", {"turn_method": "sheet"}) == "sheet"
    assert turnaround.turn_method("prop_rigid", {"turn_method": "shadow_free"}) == "shadow_free"
    assert turnaround.turn_method("vehicle", {"turn_method": "standard"}) == "standard"
    with pytest.raises(ValueError, match="characters only"):
        turnaround.turn_method("prop_rigid", {"turn_method": "sheet"})
    with pytest.raises(ValueError, match="rigid props only"):
        turnaround.turn_method("humanoid", {"turn_method": "shadow_free"})
    with pytest.raises(ValueError, match="turn_method must be one of"):
        turnaround.turn_method("humanoid", {"turn_method": "spin"})


def test_the_api_offers_each_category_only_its_own_ways():
    v = reference.validate_params
    assert v("turnaround", {"turn_method": "sheet"}, "humanoid") == {"turn_method": "sheet"}
    assert v("turnaround", {"turn_method": "shadow_free"}, "prop_rigid") == {"turn_method": "shadow_free"}
    with pytest.raises(ValueError):
        v("turnaround", {"turn_method": "shadow_free"}, "humanoid")
    with pytest.raises(ValueError):
        v("turnaround", {"turn_method": "sheet"}, "prop_rigid")
    # A category with one way has no knob: the value is dropped, as for every other knob that does not apply.
    assert v("turnaround", {"turn_method": "sheet"}, "quadruped") == {}
    knob = next(p for s in reference.stages() if s["id"] == "turnaround" for p in s["adjust_schema"] if p["name"] == "turn_method")
    assert knob["simple"] is True and knob["default"] == "standard"
    assert knob["options_by_category"] == {"humanoid": ["standard", "sheet"], "prop_rigid": ["standard", "shadow_free"]}
    assert set(knob["option_labels"]) == {"standard", "sheet", "shadow_free"}


def test_method_draws():
    assert all(turnaround.method_draws("sheet", v) for v in ("front", "left", "back", "right", "quarter"))
    assert not turnaround.method_draws("sheet", "top")
    assert turnaround.method_draws("shadow_free", "front") and turnaround.method_draws("shadow_free", "quarter")
    assert not turnaround.method_draws("shadow_free", "top")
    assert turnaround.method_draws("standard", "top")


def test_both_loras_are_registered_for_their_way():
    for aid, method, model in (("turnaround_sheet", "sheet", "qwen_sheet_lora"), ("turnaround_white", "shadow_free", "qwen_camera_lora")):
        a = reference.addon(aid)
        assert a["stage"] == "turnaround" and a["turn_method"] == method and not a.get("required")
        assert turnaround.METHOD_ADDON[method] == (aid, model)
        assert reference.model_record(model)["licence"] == "Apache-2.0"
    assert reference.addon("qwen_angles")["turn_method"] == "standard"


# --- the graphs ------------------------------------------------------------------------------------------------------


def test_sheet_graph_swaps_the_angles_lora_and_draws_one_wide_picture():
    base = qwen_edit.angle_graph("in.png", "x", 5, "p")
    g = qwen_edit.sheet_graph("in.png", 5, "p")
    assert set(g) == set(base)  # the same pipeline, one LoRA swapped
    assert g["l2"]["inputs"]["lora_name"] == qwen_edit.SHEET_LORA and g["l2"]["inputs"]["strength_model"] == 1.0
    assert (g["lat"]["inputs"]["width"], g["lat"]["inputs"]["height"]) == (2592, 1088)
    assert g["sc"]["inputs"]["megapixels"] == 0.65 and g["pos"]["inputs"]["prompt"] == "Character turnaround sheet"
    assert g["l1"] == base["l1"]  # Lightning stays


def test_camera_graph_turns_relative_to_the_front():
    g = qwen_edit.camera_graph("in.png", "left", 5, "p")
    assert g["l2"]["inputs"]["lora_name"] == qwen_edit.CAMERA_LORA
    assert g["pos"]["inputs"]["prompt"].startswith("Move the camera 90 degrees to the right around the object.")
    assert "180 degrees to the right" in qwen_edit.camera_prompt("back")
    assert "90 degrees to the left" in qwen_edit.camera_prompt("right")
    assert "45 degrees to the left" in qwen_edit.camera_prompt("quarter")  # +45 drew a front-left quarter
    assert qwen_edit.camera_prompt("left", "red cape").endswith("the same. red cape")
    with pytest.raises(ValueError):
        qwen_edit.camera_prompt("top")


# --- the sheet picture ------------------------------------------------------------------------------------------------


def _sheet(tmp: Path, figures: int) -> tuple[Path, Path]:
    """A 2592 x 1088 sheet with `figures` upright blobs, each a different red, and its cut-out."""
    W, H = qwen_edit.SHEET_SIZE
    raw = Image.new("RGB", (W, H), (255, 255, 255))
    cut = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    step = W // max(1, figures)
    for i in range(figures):
        x0 = i * step + step // 3
        blob = Image.new("RGBA", (step // 3, 900), (40 * i + 50, 20, 20, 255))
        raw.paste(blob.convert("RGB"), (x0, 90))
        cut.paste(blob, (x0, 90))
    raw.save(tmp / "sheet_raw.png")
    cut.save(tmp / "sheet.png")
    return tmp / "sheet_raw.png", tmp / "sheet.png"


def test_split_sheet_gives_five_square_views_in_order(tmp_path):
    raw, cut = _sheet(tmp_path, 5)
    out = turnaround.split_sheet(raw, cut, tmp_path)
    assert list(out) == ["front", "right", "back", "left", "quarter"]
    for i, (view, files) in enumerate(out.items()):
        with Image.open(files["cutout"]) as im:
            assert im.size == (1024, 1024) and im.mode == "RGBA"
            assert im.getpixel((512, 512))[0] == 40 * i + 50  # the figure of its own column, centred
        with Image.open(files["raw"]) as im:
            assert im.size == (1024, 1024) and im.getpixel((2, 2)) == (255, 255, 255)
        assert files["white"].is_file()
    assert imaging.alpha_bbox(out["back"]["cutout"])["height"] > 600


def test_split_sheet_refuses_a_sheet_without_five_figures(tmp_path):
    raw, cut = _sheet(tmp_path, 4)
    assert turnaround.split_sheet(raw, cut, tmp_path) is None
    assert len(turnaround.sheet_figures(cut)) == 4


def test_sheet_input_is_the_lora_portrait(tmp_path):
    cut = _rgba(tmp_path / "cut.png", w=300, h=300, box=(100, 20, 200, 280))
    out = turnaround.sheet_input(cut, tmp_path / "in.png")
    with Image.open(out) as im:
        assert im.size == (600, 1080) and im.mode == "RGB"
        assert im.getpixel((5, 5)) == (255, 255, 255) and im.getpixel((300, 540))[0] == 200


# --- the stage -------------------------------------------------------------------------------------------------------


def _world(tmp_path, monkeypatch, category: str, parent_params: dict | None = None):
    concept_img = _rgba(tmp_path / "concept.png")
    concept = SimpleNamespace(id="c" * 32, parent_version_id=None, stage="concept", params={}, metrics={}, image=concept_img)
    new = SimpleNamespace(id="n" * 32, parent_version_id=concept.id, stage="turnaround", params={}, metrics={}, image=None)
    sc = FakeSC(tmp_path, new, FakeDB(concept, new))
    sc.asset = SimpleNamespace(category=category, style_preset="", prompt="")
    sc.copy_in = lambda src, name: Path(src)
    calls: dict[str, list[str]] = {"standard": [], "sheet": [], "camera": []}
    monkeypatch.setattr(turnaround.library, "version_file", lambda v, key, settings: v.image if key == "image" else None)

    async def fake_generate(sc_, src_name, view, seed, lora_strength, extra, base, span, name=None):
        calls["standard"].append(view)
        cut = _rgba(tmp_path / f"{view}.png")
        return {"raw": _rgba(tmp_path / f"{view}_raw.png"), "cutout": cut, "white": imaging.white_composite(cut), "stats": None}

    async def fake_sheet(sc_, source, src, seed):
        calls["sheet"].append(str(seed))
        out = {}
        for v in qwen_edit.SHEET_ORDER:
            cut = _rgba(tmp_path / f"sheet_{v}.png")
            out[v] = {"raw": _rgba(tmp_path / f"sheet_{v}_raw.png"), "cutout": cut, "white": imaging.white_composite(cut)}
        return out

    async def fake_camera_source(sc_, source, src):
        cut = _rgba(tmp_path / "front.png")
        return {"raw": _rgba(tmp_path / "front_raw.png"), "cutout": cut, "white": imaging.white_composite(cut)}

    async def fake_camera_view(sc_, src_name, view, seed, extra, base, span):
        calls["camera"].append(f"{view}:{extra}")
        cut = _rgba(tmp_path / f"cam_{view}.png")
        return {"raw": _rgba(tmp_path / f"cam_{view}_raw.png"), "cutout": cut, "white": imaging.white_composite(cut), "stats": None}

    monkeypatch.setattr(turnaround, "_generate_view", fake_generate)
    monkeypatch.setattr(turnaround, "_draw_sheet", fake_sheet)
    monkeypatch.setattr(turnaround, "_camera_source", fake_camera_source)
    monkeypatch.setattr(turnaround, "_camera_view", fake_camera_view)
    return sc, concept, new, calls


def test_the_sheet_draws_every_side_in_one_job_and_standard_draws_the_rest(tmp_path, monkeypatch):
    sc, concept, new, calls = _world(tmp_path, monkeypatch, "humanoid")
    views = ["front", "left", "back", "right", "quarter", "top"]
    asyncio.run(turnaround.adjust(sc, {"views": views, "turn_method": "sheet", "seed": 11}))
    assert calls["sheet"] == ["11"] and calls["standard"] == ["top"] and not calls["camera"]
    got = sc.metrics["views"]
    assert {v: got[v]["method"] for v in views} == {**{v: "sheet" for v in views[:5]}, "top": "standard"}
    assert new.params["turn_method"] == "sheet" and sc.metrics["turn_method"] == "sheet"
    assert [a["id"] for a in sc.metrics["addons"]] == ["qwen_lightning", "qwen_angles", "turnaround_sheet"]
    assert sc.files["left"].name == "sheet_left_raw.png"


def test_the_standard_way_is_unchanged(tmp_path, monkeypatch):
    sc, concept, new, calls = _world(tmp_path, monkeypatch, "humanoid")
    asyncio.run(turnaround.adjust(sc, {"views": ["front", "left", "back"]}))
    assert calls["standard"] == ["front", "left", "back"] and not calls["sheet"] and not calls["camera"]
    assert new.params["turn_method"] == "standard"
    assert [a["id"] for a in sc.metrics["addons"]] == ["qwen_lightning", "qwen_angles"]
    assert sc.metrics["addons"][1]["value"] == 0.9 and sc.metrics["addons"][1]["required"] is True


def test_a_failing_sheet_view_is_redrawn_the_standard_way(tmp_path, monkeypatch):
    sc, concept, new, calls = _world(tmp_path, monkeypatch, "humanoid")
    real = turnaround.view_checks

    def left_fails_once(view, stats, front, hf, hv, mirror):
        out = real(view, stats, front, hf, hv, mirror)
        if view == "left" and "left" not in calls["standard"]:
            out["passed"] = out["ok"] = False
        return out

    monkeypatch.setattr(turnaround, "view_checks", left_fails_once)
    asyncio.run(turnaround.adjust(sc, {"views": ["front", "left", "right"], "turn_method": "sheet"}))
    assert calls["sheet"] and calls["standard"] == ["left"]
    got = sc.metrics["views"]
    assert got["left"]["method"] == "standard" and got["left"]["rerolls"] == 1 and got["right"]["method"] == "sheet"


def test_a_redraw_of_a_sheet_view_uses_the_standard_way(tmp_path, monkeypatch):
    sc, concept, new, calls = _world(tmp_path, monkeypatch, "humanoid")
    ta = SimpleNamespace(id="t" * 32, parent_version_id=concept.id, stage="turnaround",
                         params={"views": ["front", "left"], "turn_method": "sheet"},
                         metrics={"views": {"front": {"passed": True, "method": "sheet"}, "left": {"passed": True, "method": "sheet"}}},
                         image=None)
    new.parent_version_id = ta.id
    sc.db = FakeDB(concept, ta, new)
    asyncio.run(turnaround.refine(sc, "red cape", "left", None, {}))
    assert calls["standard"] == ["left"] and not calls["sheet"]
    assert sc.metrics["views"]["left"]["method"] == "standard" and new.params["turn_method"] == "sheet"
    # The kept front still came from the sheet, so its LoRA stays in the record.
    assert [a["id"] for a in sc.metrics["addons"]] == ["qwen_lightning", "qwen_angles", "turnaround_sheet"]


def test_the_shadow_free_turn_starts_from_the_concept_on_white(tmp_path, monkeypatch):
    sc, concept, new, calls = _world(tmp_path, monkeypatch, "prop_rigid")
    asyncio.run(turnaround.adjust(sc, {"views": ["front", "left", "back", "right", "quarter", "top"], "turn_method": "shadow_free"}))
    assert calls["camera"] == ["left:", "back:", "right:", "quarter:"] and calls["standard"] == ["top"]
    got = sc.metrics["views"]
    assert got["front"]["method"] == "shadow_free" and got["front"]["seed"] is None
    assert [a["id"] for a in sc.metrics["addons"]] == ["qwen_lightning", "qwen_angles", "turnaround_white"]


def test_a_shadow_free_redraw_takes_the_described_change_without_the_look_words(tmp_path, monkeypatch):
    sc, concept, new, calls = _world(tmp_path, monkeypatch, "prop_rigid")
    ta = SimpleNamespace(id="t" * 32, parent_version_id=concept.id, stage="turnaround",
                         params={"views": ["front", "left"], "turn_method": "shadow_free"},
                         metrics={"views": {"front": {"passed": True, "method": "shadow_free"}}}, image=None)
    new.parent_version_id = ta.id
    sc.db = FakeDB(concept, ta, new)
    asyncio.run(turnaround.refine(sc, "brass lock", "left", None, {}))
    assert calls["camera"] == ["left:brass lock"] and not calls["standard"]


def test_a_way_the_category_does_not_have_fails_the_job(tmp_path, monkeypatch):
    sc, concept, new, calls = _world(tmp_path, monkeypatch, "quadruped")
    with pytest.raises(ValueError, match="characters only"):
        asyncio.run(turnaround.adjust(sc, {"turn_method": "sheet"}))
    assert not calls["sheet"] and not calls["standard"]
