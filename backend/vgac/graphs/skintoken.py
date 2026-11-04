"""SkinToken auto-rig graph through VGACLoadTrimesh. From bakeoff/rig.py.

skeleton_template is "Mixamo" for humanoids (Unity Humanoid mapping) or "Keep model names" for the rest.
The node writes the rigged GLB to the instance's output directory and reports its path through the
PreviewAny node "show" (outputs["show"]["text"][0]).
"""

from __future__ import annotations

from typing import Any

CKPT = "experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt"
TEMPLATES = {"mixamo": "Mixamo", "generic": "Keep model names"}


def rig_graph(mesh_name: str, prefix: str, target_faces: int, ckpt: str = CKPT, template: str = "Keep model names",
              group_per_vertex: int = 4, bottom_center_origin: bool = True, smooth_angle: float = 55.0) -> dict[str, Any]:
    return {
        "load": {"class_type": "VGACLoadTrimesh", "inputs": {"path": mesh_name, "target_faces": target_faces, "merge_vertices": True}},
        "rig": {"class_type": "SkinTokenRigTrimesh", "inputs": {
            "trimesh": ["load", 0], "ckpt_name": ckpt, "device": "auto", "save_file": True,
            "filename_prefix": prefix, "file_format": "glb", "use_transfer": False,
            "use_postprocess": False, "group_per_vertex": group_per_vertex, "bottom_center_origin": bottom_center_origin,
            "smooth_angle": smooth_angle, "skeleton_template": template, "top_k": 5, "top_p": 0.95, "temperature": 1.0,
            "repetition_penalty": 2.0, "num_beams": 10}},
        "show": {"class_type": "PreviewAny", "inputs": {"source": ["rig", 1]}},
        "faces": {"class_type": "PreviewAny", "inputs": {"source": ["load", 2]}},
    }
