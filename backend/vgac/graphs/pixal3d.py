"""Pixal3D four-view build: front, left, back and right RGBA cut-outs -> textured low-poly GLB with PBR maps.

The TRELLIS.2 graph (graphs/trellis.py convert_graph) with the conditioning swapped: Pixal3D's multi-view model
(TRELLIS.2 backbone, Tencent ARC, MIT) is given all four views through Pixal3DMultiViewConditioning, with the DINOv3
encoder that carries NAF's weights. Samplers, stages, VAEs and the post chain (remesh, decimate, unwrap, bakes, GLB) are
the TRELLIS.2 graph's own, so the output contract is the same: SaveGLB `glb` and the SaveImage nodes of IMAGE_NODES.

The camera angle: the multi-view node takes the horizontal field of view of the views as framed. `fov` None wires in
the front picture's own angle, estimated by MoGe-2 on the framed front crop (MoGeGeometryToFOV, horizontal, degrees),
and PreviewAny `fovshow` returns the number so the step can record it; a number is given to the node as it is, with no
MoGe nodes. Neither is a clear win (trial, docs/research/tuning/pixal3d-trial.md): at the node's default 20 degrees the
model grew a closed-eyelid shelf over each eye in 4 of 4 builds and the own estimate removed it, but on M1 the own
estimate painted a wider face with a cheek edge (eye or mouth up to 1.3 to 1.45 cm from the picture against about 0.5 at
20), while on the Boy it removed pointed ears and cheek creases.

Every view is framed like the front (ImageCropToMask of the picture's alpha, 1024 square, pad 1.1, black), the same
scale in every view. The first connected view (front) is the front the mesh is posed to.
"""

from __future__ import annotations

from typing import Any

from .trellis import convert_graph

PIXAL3D_VIEWS = ("front", "left", "back", "right")
PIXAL3D_MODEL = "pixal3d_multiview_int8_convrot.safetensors"
PIXAL3D_ENCODER = "dino_v3_L_naf_fp32.safetensors"
MOGE_MODEL = "moge_2_vitl_normal_fp16.safetensors"
FOV_NODE = "fovshow"


def _crop(image_name: str, key: str) -> dict[str, Any]:
    return {
        f"img_{key}": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        f"inv_{key}": {"class_type": "InvertMask", "inputs": {"mask": [f"img_{key}", 1]}},
        f"crop_{key}": {"class_type": "ImageCropToMask", "inputs": {"images": [f"img_{key}", 0], "masks": [f"inv_{key}", 0], "width": 1024, "height": 1024,
                                                                    "pad_factor": 1.1, "grow_mask": 0, "background": "#000000"}},
    }


def pixal3d_graph(names: dict[str, str], prefix: str, target_faces: int = 20000, tex: int = 2048, upsample: int = 1024, seed: int = 42,
                  remesh: bool = True, padding: int = 1, tex_steps: int = 24, fov: float | None = None) -> dict[str, Any]:
    """names: view -> uploaded image name for each of PIXAL3D_VIEWS (all four required). fov: the camera angle in
    degrees, None for the front picture's own estimate."""
    missing = [v for v in PIXAL3D_VIEWS if not names.get(v)]
    if missing:
        raise ValueError("the four-view build needs the front, left, back and right pictures; missing: " + ", ".join(missing))
    g = convert_graph(names["front"], prefix, target_faces, tex, upsample, seed, remesh, padding=padding, tex_steps=tex_steps)
    g["cv"]["inputs"]["clip_name"] = PIXAL3D_ENCODER
    g["unet"]["inputs"]["unet_name"] = PIXAL3D_MODEL
    if fov is None:
        g["moge"] = {"class_type": "LoadMoGeModel", "inputs": {"model_name": MOGE_MODEL}}
        g["mogei"] = {"class_type": "MoGeInference", "inputs": {"moge_model": ["moge", 0], "image": ["crop", 0], "resolution_level": 9, "fov_x_degrees": 0.0,
                                                                "batch_size": 4, "force_projection": True, "apply_mask": True, "refine_steps": 3}}
        g["fov"] = {"class_type": "MoGeGeometryToFOV", "inputs": {"moge_geometry": ["mogei", 0], "axis": "horizontal", "unit": "degrees"}}
        g[FOV_NODE] = {"class_type": "PreviewAny", "inputs": {"source": ["fov", 0]}}
        fov_in: Any = ["fov", 0]
    else:
        fov_in = float(fov)
    cond: dict[str, Any] = {"clip_vision_model": ["cv", 0], "fov": fov_in, "front": ["crop", 0]}
    for v in PIXAL3D_VIEWS[1:]:
        g.update(_crop(names[v], v))
        cond[v] = [f"crop_{v}", 0]
    g["cond"] = {"class_type": "Pixal3DMultiViewConditioning", "inputs": cond}
    return g
