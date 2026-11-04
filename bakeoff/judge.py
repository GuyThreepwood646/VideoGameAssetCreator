"""Local vision judge on instance B: Qwen2.5-VL-7B (the Qwen-Image-Edit text encoder) through the native TextGenerate node.

Usage: python judge.py <image.png> ["<rubric text>"]
Prints the model's JSON-ish verdict. Free and offline; used as the default judge in the app.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from comfy import B, BS, run, stage_input

RUBRIC = ("You are a strict game art director reviewing a concept image for a 3D character pipeline. "
          "Score each criterion from 1 to 5 and explain briefly: (1) pose is a neutral A-pose or standing pose suitable for rigging, "
          "(2) the whole subject is visible and uncropped, (3) background is plain and separable, (4) lighting is even with no hard shadows, "
          "(5) anatomy and proportions are clean with no extra or fused limbs, (6) overall appeal. "
          "Then list the defects as short imperative fix instructions an image editor could follow. "
          "Answer as JSON with keys scores (object), defects (array of strings), summary (string).")


def judge_graph(image_name, prompt, max_length=400):
    return {
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "gemma_3_12B_it.safetensors", "type": "ltxv", "device": "default"}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "gen": {"class_type": "TextGenerate", "inputs": {"clip": ["clip", 0], "prompt": prompt, "max_length": max_length, "sampling_mode": "off",
                                                        "image": ["img", 0], "thinking": False, "use_default_template": True}},
        "show": {"class_type": "PreviewAny", "inputs": {"source": ["gen", 0]}},
    }


if __name__ == "__main__":
    image = sys.argv[1]
    rubric = sys.argv[2] if len(sys.argv) > 2 else RUBRIC
    name = stage_input(image, "bakeoff_judge_" + os.path.basename(image))
    outputs, secs = run(B, judge_graph(name, rubric), poll=2.0)
    text = ""
    for o in outputs.values():
        if "text" in o:
            text = "\n".join(o["text"])
    print(f"[{secs}s]")
    print(text)
