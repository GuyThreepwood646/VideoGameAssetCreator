"""Style from a picture (POST /api/projects/{pid}/style-from-image): the review engine, the same local vision model
the judge uses (graphs.judge.judge_graph, Gemma through TextGenerate), writes a short style description a painter
model can follow. The job makes no version; its result ({description, palette, words, model}) is read from
GET /api/jobs/{id} and is never applied by itself: the person reads it, edits it and applies it through
PUT /api/projects/{id} style_lock.
"""

from __future__ import annotations

import re
from typing import Any

from ..comfy import ComfyClient
from ..db import Job, session_factory
from ..graphs.judge import MODEL_NAME, judge_graph
from ..jobs import JobContext

MAX_WORDS = 80

STYLE_PROMPT = (
    "You describe the visual style of a picture so another artist can paint new game assets in the same style. "
    "Write one short paragraph, under 80 words, covering: the medium stated as a physical thing (for example gouache "
    "on paper, cel paint, carved wood, pixel grid, clay); how shading is done; the line work; shapes and proportions; "
    "the colour mood. Describe only the style, not what the picture shows. Do not name any brand, game, studio, film "
    "or artist. Do not use lists, headings or quotation marks. Answer with the paragraph only."
)


def clean_description(text: str, max_words: int = MAX_WORDS) -> str:
    """The model's reply as one plain paragraph under max_words: no fences, quotes, headings or list marks, cut at the
    last full sentence that fits."""
    t = re.sub(r"```[a-z]*", " ", text or "")
    t = re.sub(r"^\s*(#+|[-*]|\d+[.)])\s*", "", t, flags=re.M)
    t = re.sub(r"(?i)^\s*(here is|here's|style description)[^:\n]*:\s*", "", t.strip())
    t = t.replace('"', "").replace("“", "").replace("”", "").replace("**", "")
    t = re.sub(r"\s+", " ", t).strip()
    words = t.split(" ")
    if len(words) <= max_words:
        return t
    cut = " ".join(words[:max_words])
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if cut.endswith((".", "!", "?")):
        return cut
    return cut[:end + 1] if end > 40 else cut.rstrip(",;: ") + "."


async def describe(ctx: JobContext, payload: dict[str, Any]) -> None:
    from .. import library

    path = library.abs_path(payload["image"], ctx.settings)
    if not path.is_file():
        raise FileNotFoundError("the picture to describe is missing")
    client: ComfyClient | None = ctx.clients.get(ctx.instance)
    if client is None:
        raise RuntimeError(f"no review engine on instance {ctx.instance!r}")
    ctx.progress(0.05, "reading the picture")
    name = await client.stage_input(path, f"vgac_style_{ctx.job_id[:8]}.png")

    def on_poll(elapsed: float) -> None:
        ctx.progress(None, f"describing the style: {int(elapsed)} s")
        ctx.check_cancelled()

    entry = await client.run(judge_graph(name, STYLE_PROMPT, max_length=220), poll_seconds=2.0,
                             timeout=ctx.settings.job_timeout_seconds, on_poll=on_poll)
    text = "\n".join(ComfyClient.texts_of(entry, "show")) or "\n".join(str(t) for t in ComfyClient.files_of(entry, "text"))
    description = clean_description(text)
    if not description:
        raise ValueError("the review engine gave no description")
    result = {"description": description, "words": len(description.split()), "palette": payload.get("palette") or [],
              "swatches": payload.get("swatches") or [], "model": MODEL_NAME, "applied": False}
    with session_factory()() as db:
        job = db.get(Job, ctx.job_id)
        if job is not None:
            job.result = result
            db.commit()
    ctx.progress(1.0, "described")
