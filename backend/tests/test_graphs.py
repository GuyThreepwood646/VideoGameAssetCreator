"""Graph builders must produce the exact node structures of the bakeoff scripts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from vgac.graphs import birefnet, concept, hy3d, judge, material, qwen_edit, skintoken, trellis


def load_bakeoff(bakeoff_dir: Path, name: str):
    # The bakeoff scripts import their own comfy.py helper; put the folder first on sys.path for the import.
    sys.path.insert(0, str(bakeoff_dir))
    try:
        spec = importlib.util.spec_from_file_location(f"bakeoff_{name}", bakeoff_dir / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(bakeoff_dir))


def strip_prefix(g: dict) -> dict:
    out = {}
    for k, node in g.items():
        inputs = {ik: iv for ik, iv in node["inputs"].items() if ik != "filename_prefix"}
        out[k] = {"class_type": node["class_type"], "inputs": inputs}
    return out


def test_zimage_matches_gen_concepts(bakeoff_dir):
    bk = load_bakeoff(bakeoff_dir, "gen_concepts")
    ours = concept.zimage_graph("a knight", 123, "bakeoff/concepts/x")
    theirs = bk.zimage_graph("a knight", 123, "x")
    assert ours == theirs


def test_concept_dispatch_and_save_nodes():
    from vgac import reference

    assert reference.concept_models() == ("zturbo", "zbase", "klein", "anima", "pixel", "chroma")
    for model in reference.concept_models():
        g = concept.concept_graph(model, "p", 1, "pre", width=1024, height=1024)
        node = g[concept.save_node(model)]
        assert node["class_type"] == "SaveImage" and node["inputs"]["filename_prefix"] == "pre"
    g = concept.concept_graph("zbase", "p", 1, "pre", negative="bad")
    assert g["5"]["inputs"]["text"] == "bad"
    with pytest.raises(ValueError):
        concept.concept_graph("nope", "p", 1, "pre")


def test_angle_and_cutout_match_turnaround(bakeoff_dir):
    bk = load_bakeoff(bakeoff_dir, "turnaround")
    assert qwen_edit.VIEWS["left"] == bk.VIEWS["left"] == "<sks> right side view eye-level shot medium shot"
    for view in bk.VIEWS:
        assert qwen_edit.VIEWS[view] == bk.VIEWS[view]
    ours = qwen_edit.angle_graph("img.png", qwen_edit.VIEWS["front"], 5, "bakeoff/turnaround/k/front_5", lora_strength=0.9)
    theirs = bk.angle_graph("img.png", bk.VIEWS["front"], 5, "k/front_5")
    assert ours == theirs
    assert birefnet.cutout_graph("img.png", "bakeoff/cutout/k/front") == bk.cutout_graph("img.png", "k/front")


def test_edit_graph_drops_angle_lora():
    g = qwen_edit.edit_graph("img.png", "make the cloak red", 1, "p")
    assert "l2" not in g
    assert g["ms"]["inputs"]["model"] == ["l1", 0]
    assert g["pos"]["inputs"]["prompt"] == "make the cloak red"


def test_trellis_matches_convert(bakeoff_dir):
    bk = load_bakeoff(bakeoff_dir, "convert")
    for remesh in (True, False):
        ours = trellis.convert_graph("in.png", "bakeoff/convert/knight_20000", 20000, 2048, 1024, seed=42, remesh=remesh)
        theirs = bk.convert_graph("in.png", "knight_20000", 20000, 2048, 1024, seed=42, remesh=remesh)
        assert ours == theirs
    g = trellis.convert_graph("in.png", "x")
    assert g["remesh"]["inputs"]["sign_mode.fill"] == 20 and g["remesh"]["inputs"]["sign_mode.qef"] is False
    assert set(trellis.IMAGE_NODES) <= set(g)


def test_hy3d_matches_mesh(bakeoff_dir):
    bk = load_bakeoff(bakeoff_dir, "mesh")
    names = {v: f"{v}.png" for v in ("front", "left", "back", "right")}
    assert hy3d.hy3d_graph(names, "bakeoff/mesh/k_hy3d", 20, 7.5) == bk.hy3d_graph(names, "k_hy3d", 20, 7.5)


def test_skintoken_matches_rig(bakeoff_dir):
    bk = load_bakeoff(bakeoff_dir, "rig")
    ours = skintoken.rig_graph("k.glb", "bakeoff/rig/k", 150000, template="Mixamo")
    theirs = bk.rig_graph("k.glb", "k", 150000, template="Mixamo")
    assert ours == theirs
    assert skintoken.TEMPLATES == {"mixamo": "Mixamo", "generic": "Keep model names"}


def test_material_matches_bakeoff(bakeoff_dir):
    bk = load_bakeoff(bakeoff_dir, "material")
    assert material.t2i_graph("stone", 3, 1024, "bakeoff/material/s_raw") == bk.t2i_graph("stone", 3, 1024, "s_raw")


def test_judge_matches_bakeoff_and_parses(bakeoff_dir):
    bk = load_bakeoff(bakeoff_dir, "judge")
    assert judge.judge_graph("img.png", bk.RUBRIC) == bk.judge_graph("img.png", bk.RUBRIC)
    v = judge.parse_verdict('Here you go:\n```json\n{"scores": {"pose": 4}, "defects": ["crop tighter"], "summary": "fine"}\n```')
    assert v["scores"] == {"pose": 4} and v["defects"] == ["crop tighter"] and v["model"] == "gemma-3-12b-it"
    assert "raw" in judge.parse_verdict("no json here")


def test_workflow_json_files_match_builders():
    import json

    wf = Path(__file__).resolve().parent.parent.parent / "workflows"
    if not (wf / "trellis_convert.json").is_file():
        pytest.skip("workflows not generated")
    from vgac.workflows import DEFAULTS

    for name, builder in DEFAULTS.items():
        saved = json.loads((wf / f"{name}.json").read_text(encoding="utf-8"))
        assert saved == builder(), name
