"""Local vision judge op (POST /api/versions/{id}/judge mode=local): runs as a normal instance-b job.

Picks the image to judge: files.sheet for mesh versions when present, else the first image file on the version
(image, preview, front, base_color, tile_preview, rest ...). Writes metrics.judge.
"""

from __future__ import annotations

from pathlib import Path

from ..comfy import ComfyClient
from ..graphs.judge import RUBRIC, RUBRIC_MESH, judge_graph, parse_verdict
from .base import StageContext

IMAGE_KEYS = ("sheet", "image", "preview", "front", "cutout", "base_color", "tile_preview", "rest", "unity_strip", "anim_strip", "bones")


def pick_image(sc: StageContext) -> Path | None:
    files = sc.version.files
    if sc.version.stage == "mesh" and files.get("sheet"):
        p = sc.file("sheet")
        if p is not None:
            return p
    for key in IMAGE_KEYS:
        p = sc.file(key)
        if p is not None and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return p
    for key, rel in files.items():
        p = sc.file(key)
        if p is not None and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return p
    return None


async def local(sc: StageContext) -> None:
    image = pick_image(sc)
    if image is None:
        raise FileNotFoundError("version has no image to judge")
    rubric = RUBRIC_MESH if sc.version.stage in ("mesh", "prep", "rig", "export") else RUBRIC
    name = await sc.stage_input(image, f"vgac_judge_{sc.version.id[:8]}_{image.name}", instance="b")
    entry = await sc.comfy(judge_graph(name, rubric), instance="b", poll=2.0, message="local judge (Gemma 3)", base=0.05, span=0.9)
    text = "\n".join(ComfyClient.texts_of(entry, "show")) or "\n".join(str(t) for t in ComfyClient.files_of(entry, "text"))
    verdict = parse_verdict(text)
    verdict["image"] = sc.version.files.get(next((k for k, v in sc.version.files.items() if sc.file(k) == image), ""), image.name)
    verdict["elapsed_seconds"] = entry.get("elapsed_seconds")
    sc.set_metrics(judge=verdict)
