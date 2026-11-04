"""BiRefNet background removal graph: image -> RGBA cutout. From bakeoff/turnaround.py.

JoinImageWithAlpha treats 1 as transparent, so the mask is inverted first.
"""

from __future__ import annotations

from typing import Any


def cutout_graph(image_name: str, prefix: str) -> dict[str, Any]:
    return {
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "bgm": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"}},
        "mask": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["bgm", 0], "image": ["img", 0]}},
        "inv": {"class_type": "InvertMask", "inputs": {"mask": ["mask", 0]}},
        "rgba": {"class_type": "JoinImageWithAlpha", "inputs": {"image": ["img", 0], "alpha": ["inv", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["rgba", 0], "filename_prefix": prefix}},
    }
