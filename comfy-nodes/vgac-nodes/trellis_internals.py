# Copyright (C) 2026 Runtime Forge LLC
#
# This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program. If not, see
# <https://www.gnu.org/licenses/>.

"""The one place VGAC's TRELLIS.2 decode nodes reach into ComfyUI.

Nothing in ComfyUI is replaced or changed: this file only reads attributes and calls functions, and every one of them is
looked up through `_need`, which raises ComfyChanged with a plain message naming what is missing. Written against
ComfyUI commit 03d670be (2026-11-17).

`walk` runs a TRELLIS.2 sparse decoder one block at a time, calling ComfyUI's own blocks. It follows the loop of
`SparseUnetVaeDecoder.forward` (comfy/ldm/trellis2/vae.py, GPL-3.0) and adds what a windowed decode needs: the caller
sees the point count at each level before that level runs, supplies the texture decode's child flags row by row, and a
window in which nothing subdivides ends cleanly.
"""

from __future__ import annotations

import functools
import importlib
from fractions import Fraction

import torch
import torch.nn.functional as F


class ComfyChanged(RuntimeError):
    """ComfyUI no longer has something the decode nodes need."""


def changed(detail: str) -> ComfyChanged:
    return ComfyChanged("VGAC's 3D decode does not match this version of ComfyUI (" + detail + "). "
                        "Update VGAC, or use the ComfyUI version VGAC installs.")


def _need(obj, name: str, where: str):
    try:
        return getattr(obj, name)
    except AttributeError:
        raise changed(f"{where} has no '{name}'") from None


@functools.lru_cache(maxsize=None)
def _module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as e:
        raise changed(f"cannot load {name}: {e}") from None


def _sparse():
    return _module("comfy.ldm.trellis2.vae")


def _memory():
    return _module("comfy.model_management")


# ---- sparse decoder ----

def sparse_tensor(feats, coords, level: int = 0):
    """A ComfyUI SparseTensor for cells at `level` (its scale is one over two to the level, as the decoder sets it)."""
    scale = (Fraction(1, 1 << level),) * 3
    return _need(_sparse(), "SparseTensor", "ComfyUI's TRELLIS.2 code")(feats=feats, coords=coords, scale=scale)


def shape_decoder(vae):
    return _need(_need(vae, "first_stage_model", "the shape VAE"), "shape_dec", "the shape VAE")


def texture_decoder(vae):
    return _need(_need(vae, "first_stage_model", "the texture VAE"), "txt_dec", "the texture VAE")


def levels(dec) -> list[tuple[list, object]]:
    """[(plain blocks, upsampling block or None)] per level; the last level has no upsampling block."""
    blocks = list(_need(dec, "blocks", "the decoder"))
    for name in ("from_latent", "output_layer", "pred_subdiv"):
        _need(dec, name, "the decoder")
    out = []
    for i, level in enumerate(blocks):
        level = list(level)
        up = None if i == len(blocks) - 1 else level.pop()
        for block in level:
            _need(block, "conv", "a decoder block")
        if up is not None:
            for name in ("conv1", "conv2", "updown", "pred_subdiv"):
                _need(up, name, "an upsampling block")
            if dec.pred_subdiv:
                _need(up, "to_subdiv", "an upsampling block")
        out.append((level, up))
    return out


def _conv_reach(conv) -> int:
    kernel, dilation = _need(conv, "kernel_size", "a sparse convolution"), _need(conv, "dilation", "a sparse convolution")
    return max((int(k) // 2) * int(d) for k, d in zip(kernel, dilation))


def reach(dec) -> int:
    """How far the decoder's output at one cell of the last level depends on its input, in cells of the last level.
    Each convolution reaches its own radius at its own level; an upsampling block's first convolution works at the
    level it starts from and its second at the next."""
    lv = levels(dec)
    ups = len(lv) - 1
    total = 0
    for i, (plain, up) in enumerate(lv):
        cell = 1 << (ups - i)
        total += sum(_conv_reach(b.conv) for b in plain) * cell
        if up is not None:
            total += _conv_reach(up.conv1) * cell + _conv_reach(up.conv2) * (cell >> 1)
    return total


def walk(dec, x, guide=None, check=None):
    """Decode sparse tensor `x` block by block.

    check(level, points) is called before each level runs and may raise to stop the decode. With a decoder that
    predicts its own subdivisions (shape), returns (output, [(cells, child logits) per level]). With a guided decoder
    (texture), guide(level, cells) must return the child logits or flags for exactly those rows.

    The output is None when no cell of some level has a child; the guides gathered up to that level are still
    returned."""
    lv = levels(dec)
    predicts = bool(dec.pred_subdiv)
    if not predicts and guide is None:
        raise ValueError("a guided decoder needs a guide")
    h = dec.from_latent(x)
    guides = []
    for i, (plain, up) in enumerate(lv):
        if check is not None:
            check(i, int(h.feats.shape[0]))
        for block in plain:
            h = block(h)
        if up is None:
            break
        if predicts:
            logits = up.to_subdiv(h).feats
            if not bool((logits > 0).any()):
                guides.append((h.coords, logits))
                return None, guides
            h, sub = up(h)
            guides.append((sub.coords, sub.feats))
        else:
            flags = guide(i, h.coords)
            if not bool((flags > 0).any()):
                return None, guides
            h = up(h, subdiv=sparse_tensor(flags, h.coords, i))
    h = _need(_sparse(), "SparseTensor", "ComfyUI's TRELLIS.2 code")(
        feats=F.layer_norm(h.feats, h.feats.shape[-1:]), coords=h.coords, shape=h.shape, scale=h._scale)
    return dec.output_layer(h), guides


def mesh(dec, out, resolution: int):
    """(vertices, triangles) from the shape decoder's output, as `FlexiDualGridVaeDecoder.forward` builds them."""
    margin = _need(dec, "voxel_margin", "the shape decoder")
    feats = out.feats
    if feats.shape[-1] != 7:
        raise changed("the shape decoder's output is not 7 values wide")
    vertices = (1 + 2 * margin) * torch.sigmoid(feats[..., 0:3]) - margin
    crossed = feats[..., 3:6] > 0
    split = F.softplus(feats[..., 6:7])
    to_mesh = _need(_sparse(), "flexible_dual_grid_to_mesh", "ComfyUI's TRELLIS.2 code")
    return to_mesh(out.coords[:, 1:], vertices, crossed, split, aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]], grid_size=resolution)


# ---- latent scaling ----

def shape_format():
    return _need(_module("comfy.latent_formats"), "Trellis2ShapeSLAT", "ComfyUI's latent formats")()


def texture_format():
    return _need(_module("comfy.latent_formats"), "Trellis2TexSLAT", "ComfyUI's latent formats")()


# ---- card memory ----

def torch_device():
    return _need(_memory(), "get_torch_device", "ComfyUI's memory code")()


def output_device():
    return _need(_memory(), "intermediate_device", "ComfyUI's memory code")()


def value_bytes(dtype) -> int:
    return int(_need(_memory(), "dtype_size", "ComfyUI's memory code")(dtype))


def reserved_bytes() -> int:
    return int(_need(_memory(), "extra_reserved_memory", "ComfyUI's memory code")())


def free_bytes(device) -> int:
    return int(_need(_memory(), "get_free_memory", "ComfyUI's memory code")(device))


def clear_card(wanted_bytes: int, device) -> None:
    """Unload other models until `wanted_bytes` are free on the card, or nothing is left to unload. This is the call
    that really unloads: with ComfyUI's dynamic memory handling on, the figure a VAE passes when it loads leaves other
    dynamic models where they are."""
    _need(_memory(), "free_memory", "ComfyUI's memory code")(int(wanted_bytes), device)


def empty_cache() -> None:
    _need(_memory(), "soft_empty_cache", "ComfyUI's memory code")()


def is_out_of_memory(error: BaseException) -> bool:
    mm = _memory()
    if _need(mm, "OOM_EXCEPTION", "ComfyUI's memory code") is Exception:
        return False  # this torch has no out-of-memory error of its own, so no error can be told to be one
    return bool(_need(mm, "is_oom", "ComfyUI's memory code")(error))


def model_bytes(vae) -> int:
    return int(_need(_need(vae, "patcher", "the VAE"), "model_size", "the VAE's loader")())


def load_for_decode(vae, sample_shape, working_bytes: int) -> None:
    _need(vae, "prepare_decode", "the VAE")(sample_shape, memory_required=int(working_bytes))


# ---- node output types ----

def mesh_type():
    return _need(_need(_module("comfy_api.latest"), "Types", "ComfyUI's node API"), "MESH", "ComfyUI's node API")


def voxel_type():
    return _need(_need(_module("comfy_api.latest"), "Types", "ComfyUI's node API"), "VOXEL", "ComfyUI's node API")


def pack_meshes(vertices: list, faces: list):
    return _need(_module("comfy_extras.nodes_mesh_postprocess"), "pack_variable_mesh_batch", "ComfyUI's mesh nodes")(vertices, faces)
