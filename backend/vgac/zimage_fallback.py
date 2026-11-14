"""Z-Image Turbo: the all-in-one checkpoint when the engine does not list the publisher's split file.

The graphs name Comfy-Org's split int8 files (graphs/concept.py). A machine that has not got the Turbo diffusion file but has the
earlier all-in-one fp8 or bf16 checkpoint keeps working: just before a graph is queued, comfy.ComfyClient.queue_prompt asks the
engine what its loaders list (object_info, the truth for what a graph can load, whatever folder or drive the file is in) and calls
adapt(), which turns the three split loaders into one CheckpointLoaderSimple (the layout of the earlier route), whose model, text
encoder and VAE outputs feed the same nodes.

Swapping one file name for another that the same loader can load (the earlier Z-Image base file, or a file the person pointed
the app at) is not special to Z-Image: that is model_choices.apply_aliases. Only the change of layout lives here.
"""

from __future__ import annotations

import copy
from typing import Any

from .graphs.concept import ZTURBO_UNET

# The earlier all-in-one Turbo checkpoints, best first.
OLD_TURBO_CHECKPOINTS = ("z-image-turbo-fp8-aio.safetensors", "z-image-turbo-bf16-aio.safetensors")


def _base(name: str) -> str:
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].casefold()


def listed(choices: list[Any] | None, wanted: str) -> str | None:
    """The entry of `choices` (as the engine lists it, subfolder and all) whose file name is `wanted`, in any subfolder."""
    for c in choices or []:
        if isinstance(c, str) and _base(c) == _base(wanted):
            return c
    return None


def wants(graph: dict[str, Any]) -> bool:
    """Whether the graph loads the publisher's Z-Image Turbo diffusion file (so the lists are worth asking for)."""
    return any(isinstance(n, dict) and n.get("class_type") == "UNETLoader" and _base((n.get("inputs") or {}).get("unet_name", "")) == _base(ZTURBO_UNET)
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
        if _base(name) != _base(ZTURBO_UNET):
            continue
        mine = listed(unets, name)
        if mine is not None:
            if mine != name:  # the publisher's file is there, in another folder
                out = copy.deepcopy(out) if out is graph else out
                out[node_id]["inputs"]["unet_name"] = mine
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
