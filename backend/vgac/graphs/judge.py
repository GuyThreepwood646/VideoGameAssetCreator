"""Local vision judge graph (bakeoff/judge.py): Gemma 3 12B through the native TextGenerate node on instance B.

The Qwen2.5-VL encoder fails in TextGenerate (missing stop_tokens), so Gemma is the model. The reply is read from
the PreviewAny node "show" (outputs["show"]["text"]).
"""

from __future__ import annotations

import json
import re
from typing import Any

MODEL_NAME = "gemma-3-12b-it"

RUBRIC = ("You are a strict game art director reviewing a concept image for a 3D character pipeline. "
          "Score each criterion from 1 to 5 and explain briefly: (1) pose is a neutral A-pose or standing pose suitable for rigging, "
          "(2) the whole subject is visible and uncropped, (3) background is plain and separable, (4) lighting is even with no hard shadows, "
          "(5) anatomy and proportions are clean with no extra or fused limbs, (6) overall appeal. "
          "Then list the defects as short imperative fix instructions an image editor could follow. "
          "Answer as JSON with keys scores (object), defects (array of strings), summary (string).")

RUBRIC_MESH = ("You are a strict technical artist reviewing rendered views of a generated 3D game asset. "
               "Score each criterion from 1 to 5 and explain briefly: (1) silhouette matches a clean readable shape with no floating or "
               "merged pieces, (2) surface detail is coherent with no noise or holes, (3) texture colours are plausible with no baked-in "
               "shadows, (4) proportions are consistent from every angle, (5) overall game-readiness. "
               "Then list the defects as short imperative fix instructions. "
               "Answer as JSON with keys scores (object), defects (array of strings), summary (string).")


def judge_graph(image_name: str, prompt: str = RUBRIC, max_length: int = 400) -> dict[str, Any]:
    return {
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "gemma_3_12B_it.safetensors", "type": "ltxv", "device": "default"}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "gen": {"class_type": "TextGenerate", "inputs": {"clip": ["clip", 0], "prompt": prompt, "max_length": max_length, "sampling_mode": "off",
                                                        "image": ["img", 0], "thinking": False, "use_default_template": True}},
        "show": {"class_type": "PreviewAny", "inputs": {"source": ["gen", 0]}},
    }


def parse_verdict(text: str) -> dict[str, Any]:
    """Pull the JSON block (fenced in ```json or bare) out of the model reply. Falls back to {"raw": text}."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [m.group(1)] if m else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return {"scores": data.get("scores", {}), "defects": data.get("defects", []), "summary": data.get("summary", ""), "model": MODEL_NAME}
    return {"raw": text, "model": MODEL_NAME}
