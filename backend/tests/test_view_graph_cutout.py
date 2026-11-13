"""UNTESTED ON A CARD: the cut-out in each view's own graph (graphs/qwen_edit.angle_cutout_graph, turnaround
_generate_view; docs/SPEED-AUDIT.md O2 a). What is proven here, with no card: every node of today's view graph is
unchanged, every node of today's cut-out graph is there with only its picture input moved to the decoder's output, the
save prefixes are today's, the old two-job path is still selectable, and the stage downloads the right nodes.

What a live check must compare (same seed, same start picture, same engine): the raw picture of the two paths must be
byte-identical; the cut-outs may differ in a few edge pixels (the cut-out model sees the decoder's floating point
picture, not its 8-bit PNG read back from disk), so compare the alpha channels and require a maximum difference of
a few levels on a handful of pixels, then the stage's own numbers (silhouette overlap with the other path's cut-out
at least 0.999, same checks pass). Also read the engine's log: the loader lines for the drawing model must appear once
for a whole step, not once per view."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from vgac import imaging
from vgac.graphs import birefnet, qwen_edit
from vgac.stages import turnaround

PROMPT = qwen_edit.view_prompt("left", "same face")


def test_the_view_graph_nodes_are_todays_unchanged():
    old = qwen_edit.angle_graph("in.png", PROMPT, 7, "vgac/a/turnaround/v/left_7", lora_strength=0.8)
    new = qwen_edit.angle_cutout_graph("in.png", PROMPT, 7, "vgac/a/turnaround/v/left_7", "vgac/a/turnaround/v/left", lora_strength=0.8)
    assert {k: new[k] for k in old} == old


def test_the_cutout_nodes_are_todays_with_only_the_picture_moved():
    new = qwen_edit.angle_cutout_graph("in.png", PROMPT, 7, "p_7", "vgac/a/turnaround/v/left")
    today = birefnet.cutout_graph("whatever.png", "vgac/a/turnaround/v/left")
    rename = {"bgm": "co_bgm", "mask": "co_mask", "inv": "co_inv", "rgba": "co_rgba", "save": "cut_save"}
    for key, node in today.items():
        if key == "img":
            continue
        got = new[rename[key]]
        assert got["class_type"] == node["class_type"]
        want = {}
        for name, v in node["inputs"].items():
            if v == ["img", 0]:
                v = ["dec", 0]                                   # the only difference: the decoded picture
            elif isinstance(v, list):
                v = [rename[v[0]], v[1]]
            want[name] = v
        assert got["inputs"] == want
    assert set(new) - set(qwen_edit.angle_graph("in.png", PROMPT, 7, "p_7")) == set(rename.values())
    assert new["cut_save"]["inputs"]["filename_prefix"] == "vgac/a/turnaround/v/left"
    assert new["save"]["inputs"]["filename_prefix"] == "p_7"


def test_the_graph_is_valid_json_with_no_dangling_references():
    import json
    g = json.loads(json.dumps(qwen_edit.angle_cutout_graph("in.png", PROMPT, 7, "p", "q")))
    for key, node in g.items():
        for v in node["inputs"].values():
            if isinstance(v, list):
                assert v[0] in g, (key, v)


class RecordingSC:
    def __init__(self, tmp: Path, params: dict):
        self.dir = tmp
        self.version = SimpleNamespace(id="v" * 32, params=params)
        self.graphs: list[dict] = []
        self.downloads: list[str] = []
        self.cutouts: list[str] = []

    def prefix(self, tag=""):
        return f"vgac/a/turnaround/v/{tag}" if tag else "vgac/a/turnaround/v"

    async def comfy(self, graph, instance=None, poll=1.0, message="", base=0.0, span=1.0):
        self.graphs.append(graph)
        return {"outputs": {}}

    async def download_image(self, entry, node, dest, instance=None):
        self.downloads.append(node)
        im = Image.new("RGBA", (16, 16), (200, 50, 50, 255))
        im.save(dest)
        return Path(dest)

    async def finish_cutout(self, dest):
        return {"cutout": dest, "white": imaging.white_composite(dest), "stats": None}

    async def cutout(self, raw, name, instance=None, base=0.0, span=0.1):
        self.cutouts.append(name)
        Image.new("RGBA", (16, 16), (200, 50, 50, 255)).save(self.dir / f"{name}.png")
        return await self.finish_cutout(self.dir / f"{name}.png")


def draw(tmp_path, params):
    sc = RecordingSC(tmp_path, params)
    files = asyncio.run(turnaround._generate_view(sc, "in.png", "left", 7, 0.9, "", 0.0, 0.1))
    return sc, files


def test_one_graph_one_job_per_view_by_default(tmp_path):
    sc, files = draw(tmp_path, {})
    assert len(sc.graphs) == 1 and "cut_save" in sc.graphs[0] and sc.cutouts == []
    assert sc.downloads == ["save", "cut_save"]
    assert files["raw"].name == "left_raw.png" and files["cutout"].name == "left.png" and files["white"].is_file()


def test_the_old_two_job_path_is_one_setting_away(tmp_path):
    sc, files = draw(tmp_path, {"cutout_in_graph": False, "zero_negative": False})
    assert "cut_save" not in sc.graphs[0] and sc.cutouts == ["left"] and sc.downloads == ["save"]
    assert sc.graphs[0] == qwen_edit.angle_graph("in.png", qwen_edit.view_prompt("left", ""), 7, "vgac/a/turnaround/v/left_7", lora_strength=0.9)
    assert files["cutout"].name == "left.png"


def test_the_switch_can_also_be_flipped_for_every_version(tmp_path, monkeypatch):
    monkeypatch.setattr(turnaround, "CUTOUT_IN_VIEW_GRAPH", False)
    sc, _ = draw(tmp_path, {})
    assert sc.cutouts == ["left"]
    sc, _ = draw(tmp_path, {"cutout_in_graph": True})      # a version's own setting wins
    assert "cut_save" in sc.graphs[0]


# --- the negative at cfg 1.0 (UNTESTED ON A CARD) ----------------------------------------------------------------------


def test_zero_negative_changes_exactly_the_negative_nodes():
    old = qwen_edit.angle_graph("in.png", PROMPT, 7, "p", lora_strength=0.8)
    new = qwen_edit.angle_graph("in.png", PROMPT, 7, "p", lora_strength=0.8, zero_negative=True)
    changed = {k for k in old if k not in new or new[k] != old[k]}
    assert changed == {"neg", "negr"} and set(new) == set(old) - {"neg"}
    assert new["negr"] == {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["posr", 0]}}
    assert new["ks"] == old["ks"] and new["ks"]["inputs"]["negative"] == ["negr", 0] and old["ks"]["inputs"]["cfg"] == 1.0
    assert new["pos"] == old["pos"] and new["posr"] == old["posr"]


def test_zero_negative_is_a_setting_and_the_default_graph_is_untouched(tmp_path):
    sc, _ = draw(tmp_path, {})
    assert sc.graphs[0]["negr"]["class_type"] == "ConditioningZeroOut" and "neg" not in sc.graphs[0]
    sc, _ = draw(tmp_path, {"zero_negative": False, "cutout_in_graph": False})
    assert sc.graphs[0] == qwen_edit.angle_graph("in.png", qwen_edit.view_prompt("left", ""), 7, "vgac/a/turnaround/v/left_7", lora_strength=0.9)
    # the bake-off's graph builder is still compared with the unchanged default (tests/test_graphs.py)
    assert qwen_edit.angle_graph("i", "p", 1, "x")["neg"]["class_type"] == "TextEncodeQwenImageEditPlus"
