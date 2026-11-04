"""Write every graph builder's output for default params to workflows/*.json for reference.

Run from backend: .venv\\Scripts\\python -m vgac.workflows
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import PROJECT_DIR
from .graphs import birefnet, concept, hy3d, judge, material, qwen_edit, skintoken, trellis

WORKFLOWS_DIR = PROJECT_DIR / "workflows"

DEFAULTS = {
    "zimage_concept": lambda: concept.zimage_graph("PROMPT", 42, "vgac/concept/image"),
    "zbase_concept": lambda: concept.zbase_graph("PROMPT", 42, "vgac/concept/image"),
    "klein_concept": lambda: concept.klein_graph("PROMPT", 42, "vgac/concept/image"),
    "anima_concept": lambda: concept.anima_graph("PROMPT", 42, "vgac/concept/image"),
    "pixel_concept": lambda: concept.pixel_graph("PROMPT", 42, "vgac/concept/image"),
    "chroma_concept": lambda: concept.chroma_graph("PROMPT", 42, "vgac/concept/image"),
    "qwen_angles": lambda: qwen_edit.angle_graph("concept.png", qwen_edit.VIEWS["front"], 42, "vgac/turnaround/front"),
    "qwen_edit": lambda: qwen_edit.edit_graph("image.png", "INSTRUCTION", 42, "vgac/concept/edit"),
    "birefnet_cutout": lambda: birefnet.cutout_graph("image.png", "vgac/cutout"),
    "trellis_convert": lambda: trellis.convert_graph("cutout.png", "vgac/mesh", 20000, 2048, 1024, 42, True),
    "hy3d_mv": lambda: hy3d.hy3d_graph({v: f"{v}_white.png" for v in hy3d.HY3D_VIEWS}, "vgac/mesh/model"),
    "skintoken_rig": lambda: skintoken.rig_graph("prep.glb", "vgac/rig", 0, template="Mixamo"),
    "material_surface": lambda: material.t2i_graph("PROMPT", 42, 1024, "vgac/material/raw"),
    "judge_local": lambda: judge.judge_graph("image.png"),
}


def write_all(out_dir: Path = WORKFLOWS_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, builder in DEFAULTS.items():
        p = out_dir / f"{name}.json"
        p.write_text(json.dumps(builder(), indent=1), encoding="utf-8")
        written.append(p)
    return written


if __name__ == "__main__":
    for p in write_all():
        print(p)
