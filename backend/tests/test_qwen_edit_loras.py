"""Personal LoRAs in the Qwen-Image-Edit graphs (graphs/qwen_edit.py add_personal_loras).

With no LoRA chosen every builder must give exactly the graph it gave before the feature existed: tests/data/
qwen_edit_graphs.json was written from that code, and the snapshot cases below must still match it node for node.
"""

from __future__ import annotations

import json
from pathlib import Path

from vgac.graphs import qwen_edit as q
from vgac.graphs.concept import lora_name

SNAPSHOT = Path(__file__).parent / "data" / "qwen_edit_graphs.json"

CASES = {
    "angle_front": lambda **kw: q.angle_graph("concept.png", q.VIEWS["front"], 42, "vgac/turnaround/front", **kw),
    "angle_refs_zero_neg": lambda **kw: q.angle_graph("concept.png", q.VIEWS["back"], 7, "vgac/t/back", lora_strength=0.75,
                                                       zero_negative=True, references=["side_l.png", "side_r.png"], **kw),
    "angle_cutout_left": lambda **kw: q.angle_cutout_graph("pic.png", q.VIEWS["left"], 7, "p", "cut", lora_strength=0.9, **kw),
    "sheet": lambda **kw: q.sheet_graph("sheet_in.png", 3, "s", strength=1.0, **kw),
    "camera_left": lambda **kw: q.camera_graph("src.png", "left", 5, "c", strength=1.0, extra="Keep it sharp.", **kw),
    "edit_plain": lambda **kw: q.edit_graph("img.png", "make the coat red", 9, "e", **kw),
    "edit_ref_size": lambda **kw: q.edit_graph("img.png", "make the coat red", 9, "e", width=768, height=512,
                                               reference_name="ref.png", **kw),
    "compose_three": lambda **kw: q.compose_graph(["a.png", "b.png", "c.png"], "Picture 1 wears the coat of Picture 2", 11, "k", **kw),
}

TWO = [{"file": "VGAC/mine/a.safetensors", "value": 0.6, "trigger": "mystyle"},
       {"file": "VGAC/mine/b.safetensors", "value": 1.0, "trigger": ""}]


def _loras(g: dict) -> list[str]:
    return sorted(k for k, n in g.items() if n["class_type"] == "LoraLoaderModelOnly" and k.startswith("my"))


def test_no_loras_graphs_match_the_snapshot_node_for_node():
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert sorted(saved) == sorted(CASES)
    for name, build in CASES.items():
        assert build() == saved[name], name
        assert build(loras=[]) == saved[name], name  # an empty choice is no choice


def test_add_personal_loras_with_none_returns_the_same_graph():
    g = q.edit_graph("img.png", "x", 1, "e")
    before = json.dumps(g, sort_keys=True)
    assert q.add_personal_loras(g, None) is g and json.dumps(g, sort_keys=True) == before


def test_edit_chain_goes_after_lightning_with_strengths_and_triggers_on_positive_only():
    g = q.edit_graph("img.png", "make the coat red", 9, "e", loras=TWO)
    assert "l2" not in g  # edits never load the views LoRA
    assert _loras(g) == ["my1", "my2"]
    assert g["my1"]["inputs"] == {"model": ["l1", 0], "lora_name": lora_name("VGAC/mine/a.safetensors"), "strength_model": 0.6}
    assert g["my2"]["inputs"] == {"model": ["my1", 0], "lora_name": lora_name("VGAC/mine/b.safetensors"), "strength_model": 1.0}
    assert g["ms"]["inputs"]["model"] == ["my2", 0]
    assert g["pos"]["inputs"]["prompt"] == "make the coat red mystyle"
    assert g["neg"]["inputs"]["prompt"] == ""  # the negative keeps no trigger words


def test_each_view_route_chains_after_the_views_lora_and_keeps_it():
    one = [TWO[0]]
    g = q.angle_graph("concept.png", q.VIEWS["front"], 42, "p", loras=one)
    assert g["l2"]["class_type"] == "LoraLoaderModelOnly" and g["l2"]["inputs"]["model"] == ["l1", 0]
    assert g["my1"]["inputs"]["model"] == ["l2", 0] and g["ms"]["inputs"]["model"] == ["my1", 0]
    assert g["pos"]["inputs"]["prompt"].endswith("mystyle")
    assert len(_loras(g)) == 1  # applied once, not once per wrapper

    cut = q.angle_cutout_graph("pic.png", q.VIEWS["left"], 7, "p", "cut", loras=one)
    assert _loras(cut) == ["my1"]

    sheet = q.sheet_graph("sheet_in.png", 3, "s", loras=one)
    assert sheet["l2"]["inputs"]["lora_name"] == q.SHEET_LORA and sheet["my1"]["inputs"]["model"] == ["l2", 0]

    cam = q.camera_graph("src.png", "left", 5, "c", loras=one)
    assert cam["l2"]["inputs"]["lora_name"] == q.CAMERA_LORA and cam["my1"]["inputs"]["model"] == ["l2", 0]


def test_compose_and_face_edits_get_the_chain_once():
    g = q.compose_graph(["a.png", "b.png", "c.png"], "Picture 1 wears the coat of Picture 2", 11, "k", loras=TWO)
    assert _loras(g) == ["my1", "my2"] and g["ms"]["inputs"]["model"] == ["my2", 0]
    # the picture nodes of compose are still there and wired as before
    assert g["pos"]["inputs"]["image2"] == ["sc2", 0] and g["pos"]["inputs"]["image3"] == ["sc3", 0]
    ref = q.edit_graph("img.png", "x", 9, "e", reference_name="ref.png", loras=[TWO[0]])
    assert _loras(ref) == ["my1"] and ref["pos"]["inputs"]["image2"] == ["sc2", 0]
