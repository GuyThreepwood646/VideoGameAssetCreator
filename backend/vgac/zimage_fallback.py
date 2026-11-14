"""Z-Image: use the earlier files when the publisher's are not in the engine's lists.

The graphs name Comfy-Org's split int8 files (graphs/concept.py). A machine that has not got them yet but has the earlier
copies (the all-in-one fp8 or bf16 checkpoint for Turbo, the earlier base diffusion file) keeps working: just before a graph is
queued, comfy.ComfyClient.queue_prompt asks the engine what its loaders list (object_info, which is the truth for what a
graph can load, whatever folder or drive the file is in) and calls adapt().

  - the publisher's file is listed (in any subfolder): the graph names it as listed;
  - Turbo, and only the earlier all-in-one checkpoint is listed: the three split loaders become one CheckpointLoaderSimple
    (the layout of the earlier route), whose model, text encoder and VAE outputs feed the same nodes;
  - Base, and only the earlier diffusion file is listed: the same loader, naming the earlier file;
  - neither is listed: the graph is left alone and the engine says what is missing, as before.

The models panel shows the publisher's files as not there and says the earlier copy is in use (models_setup, `older`).
"""

from __future__ import annotations

import copy
from typing import Any

from .graphs.concept import ZBASE_UNET, ZTURBO_UNET

# The earlier all-in-one Turbo checkpoints, best first, and the earlier base diffusion file.
OLD_TURBO_CHECKPOINTS = ("z-image-turbo-fp8-aio.safetensors", "z-image-turbo-bf16-aio.safetensors")
OLD_BASE_UNET = "z_image_int8_convrot.safetensors"


def _base(name: str) -> str:
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].casefold()


def listed(choices: list[Any] | None, wanted: str) -> str | None:
    """The entry of `choices` (as the engine lists it, subfolder and all) whose file name is `wanted`, in any subfolder."""
    for c in choices or []:
        if isinstance(c, str) and _base(c) == _base(wanted):
            return c
    return None


def wants(graph: dict[str, Any]) -> bool:
    """Whether the graph loads one of the publisher's Z-Image diffusion files (so the lists are worth asking for)."""
    names = {_base(ZTURBO_UNET), _base(ZBASE_UNET)}
    return any(isinstance(n, dict) and n.get("class_type") == "UNETLoader" and _base((n.get("inputs") or {}).get("unet_name", "")) in names
               for n in graph.values())


def _fold_to_checkpoint(graph: dict[str, Any], node_id: str, ckpt: str) -> dict[str, Any] | None:
    """The split layout (nodes node_id, "1c", "1v") as one checkpoint loader; None when the graph is not in that shape."""
    if "1c" not in graph or "1v" not in graph or node_id != "1":
        return None
    out: dict[str, Any] = {}
    for k, node in graph.items():
        if k in ("1c", "1v"):
            continue
        inputs = {}
        for ik, iv in node["inputs"].items():
            if iv == ["1c", 0]:
                iv = ["1", 1]
            elif iv == ["1v", 0]:
                iv = ["1", 2]
            inputs[ik] = iv
        out[k] = {**node, "inputs": inputs}
    out["1"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}}
    return out


def adapt(graph: dict[str, Any], unets: list[Any] | None, checkpoints: list[Any] | None) -> tuple[dict[str, Any], str]:
    """(the graph to queue, a one-line note of what was changed or ''). `unets` and `checkpoints` are what the engine's
    UNETLoader and CheckpointLoaderSimple list (None: could not ask; the graph is then left alone)."""
    if unets is None and checkpoints is None:
        return graph, ""
    out = graph
    notes: list[str] = []
    for node_id, node in list(graph.items()):
        if not isinstance(node, dict) or node.get("class_type") != "UNETLoader":
            continue
        name = (node.get("inputs") or {}).get("unet_name", "")
        is_turbo, is_base = _base(name) == _base(ZTURBO_UNET), _base(name) == _base(ZBASE_UNET)
        if not (is_turbo or is_base):
            continue
        mine = listed(unets, name)
        if mine is not None:
            if mine != name:  # the publisher's file is there, in another folder
                out = copy.deepcopy(out) if out is graph else out
                out[node_id]["inputs"]["unet_name"] = mine
            continue
        if is_base:
            old = listed(unets, OLD_BASE_UNET)
            if old is not None:
                out = copy.deepcopy(out) if out is graph else out
                out[node_id]["inputs"]["unet_name"] = old
                notes.append(f"Z-Image base: using the earlier file {old}")
            continue
        for want in OLD_TURBO_CHECKPOINTS:
            ckpt = listed(checkpoints, want)
            if ckpt is None:
                continue
            folded = _fold_to_checkpoint(out, node_id, ckpt)
            if folded is not None:
                out = folded
                notes.append(f"Z-Image Turbo: using the earlier all-in-one checkpoint {ckpt}")
            break
    return out, "; ".join(notes)
