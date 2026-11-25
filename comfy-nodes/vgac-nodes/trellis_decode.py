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

"""TRELLIS.2 shape and texture decode nodes that fit the card they run on. UNTESTED ON A CARD.

VGACTrellisShapeDecode and VGACTrellisTextureDecode take the inputs and give the outputs of ComfyUI's
VaeDecodeShapeTrellis and VaeDecodeTextureTrellis. They differ in two ways:

1. Before decoding they clear the card for the decode's own size (trellis_memory.py) instead of one fixed figure.
2. When the decode does not fit the memory then free, it is done in windows of the latent grid and the pieces are
   joined (trellis_tiles.py). With the exact halo the joined result is the one-pass result; a subject that fits is
   decoded in one piece, as before.

The shape decode's size is only known once it runs, so each window is checked at the level before the last, where the
real point count is first known and before the two costly levels run; a window over the budget is dropped and cut
again. The texture decode's size is read from the shape decode's guides and is exact from the start.

Everything around the decode follows ComfyUI's own two nodes (comfy_extras/nodes_trellis2.py, GPL-3.0): latent
scaling, the Z-up to Y-up turn and the output types.
"""

from __future__ import annotations

import logging

import numpy as np
import torch

from . import trellis_internals as comfy_side
from . import trellis_memory as memory
from . import trellis_tiles as tiles

log = logging.getLogger("vgac.trellis_decode")

OOM_RETRIES = 3
OOM_BUDGET_CUT = 0.7  # after an out-of-memory error the point budget drops to this share of the window that failed
TOO_LARGE = "This model is too large to decode on this graphics card, even in the smallest pieces."
NO_ROOM = "There is not enough free memory on this graphics card to decode the model."


class _OverBudget(Exception):
    def __init__(self, points: int):
        super().__init__(points)
        self.points = int(points)


def _np(tensor) -> np.ndarray:
    return tensor.detach().cpu().numpy()


def _rows(rows: np.ndarray, device=None):
    return torch.from_numpy(np.ascontiguousarray(rows, dtype=np.int64)).to(device)


def _peak_start(device) -> None:
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)


def _peak_gb(device) -> float:
    return torch.cuda.max_memory_allocated(device) / memory.GB if device.type == "cuda" else 0.0


def _each_window(what: str, latent_cells: np.ndarray, weights: np.ndarray, budget: int, halos: list[int], decode_one) -> list[tiles.Window]:
    """Plan the windows and decode them in turn with decode_one(window, latent rows, budget, alone). A window that
    turns out over the budget (or runs out of memory) is cut again from its core; the windows already decoded stand."""
    budget = int(budget)
    try:
        pending = tiles.plan(latent_cells, weights, budget, halos)
    except tiles.TooLarge:
        raise RuntimeError(TOO_LARGE) from None
    done: list[tiles.Window] = []
    retries = 0
    while pending:
        window = pending.pop(0)
        held = window.holds(latent_cells)
        estimate = float(weights[held].sum())
        failed = None
        try:
            decode_one(window, np.nonzero(held)[0], budget, not done and not pending)
        except _OverBudget as e:
            failed = e.points
        except Exception as e:
            if not comfy_side.is_out_of_memory(e):
                raise
            failed = -1
        if failed is None:
            done.append(window)
            continue
        # Outside the except block, so the failed window's tensors are no longer held by the error.
        if failed < 0:
            retries += 1
            if retries > OOM_RETRIES:
                raise RuntimeError(TOO_LARGE)
            comfy_side.empty_cache()
            budget = int(min(budget, estimate) * OOM_BUDGET_CUT)
            log.warning("VGAC TRELLIS.2 %s decode: out of memory on a window of about %d points; cutting smaller (budget now %d points)", what, estimate, budget)
        else:
            weights[held] *= failed / max(estimate, 1.0)
            log.info("VGAC TRELLIS.2 %s decode: a window held %d points against an estimate of %d and a budget of %d; cutting it again", what, failed, estimate, budget)
        try:
            pending = tiles.plan(latent_cells, weights, budget, halos, rows=np.nonzero(window.owns(latent_cells))[0], step=window.step) + pending
        except tiles.TooLarge:
            raise RuntimeError(TOO_LARGE) from None
    return done


def _note_windows(what: str, windows: list[tiles.Window], halos: list[int]) -> None:
    if len(windows) < 2:
        return
    smallest = min(w.halo for w in windows)
    seams = "equal to a one-pass decode" if smallest >= halos[0] else f"approximate at the cuts (halo down to {smallest} of {halos[0]} cells)"
    log.info("VGAC TRELLIS.2 %s decode: joined %d windows, %s", what, len(windows), seams)


def run_shape_decode(dec, feats, coords, resolution: int, budget: int, points_per_token: float = memory.SHAPE_POINTS_PER_TOKEN,
                     halos: list[int] | None = None):
    """Decode one sample's shape latent. `feats` [N, C] are the scaled latent values in the decoder's dtype and
    `coords` [N, 4] their cells with batch column 0, both on the decode device; `budget` is the most points (at the
    level before the last) one window may hold.

    Returns (vertices [V, 3] float32, faces [F, 3] int32, guides [(coords [n, 4], child logits [n, 8]) per level]),
    all on the CPU and in the row order of a one-pass decode."""
    device = feats.device
    ups = len(comfy_side.levels(dec)) - 1
    halos = halos or tiles.halo_steps(tiles.exact_halo(comfy_side.reach(dec), ups))
    latent_cells = _np(coords[:, 1:]).astype(np.int32)
    weights = np.full(latent_cells.shape[0], float(points_per_token))
    whole = []  # the outputs of a decode done in one window, used as they are
    parts, own_vertices, spare_vertices, guide_values = [], [], [], []

    def decode_one(window: tiles.Window, rows: np.ndarray, budget: int, alone: bool) -> None:
        def check(level: int, points: int) -> None:
            if level == ups - 1 and points > budget:
                raise _OverBudget(points)

        at = _rows(rows, device)
        _peak_start(device)
        out, guides = comfy_side.walk(dec, comfy_side.sparse_tensor(feats[at], coords[at]), check=check)
        heavy = int(guides[ups - 1][0].shape[0]) if len(guides) >= ups else 0
        guides = [(c.cpu(), f.cpu()) for c, f in guides]
        if out is None:
            cells, faces, vertices = np.zeros((0, 3), np.int32), torch.zeros((0, 3), dtype=torch.int32), torch.zeros((0, 3))
        else:
            vertices, faces = comfy_side.mesh(dec, out, resolution)
            cells, vertices, faces = _np(out.coords[:, 1:]), vertices.float().cpu(), faces.cpu()
        del out
        log.info("VGAC TRELLIS.2 shape decode: window of %d tokens (halo %d), %d points before the last level, %d vertices, peak %.2f GB",
                 rows.size, window.halo, heavy, cells.shape[0], _peak_gb(device))
        if alone:
            whole.append((vertices, faces.int(), guides))
            return
        try:
            part = tiles.take_shape(window, ups, cells, _np(faces), [_np(c[:, 1:]) for c, _ in guides])
        except ValueError as e:  # the mesher no longer lays its triangles out the way the join reads them
            raise comfy_side.changed(str(e)) from None
        parts.append(part)
        own_vertices.append(vertices[_rows(part.own_rows)])
        spare_vertices.append(vertices[_rows(part.spare_rows)])
        guide_values.append([f[_rows(r)] for (_, f), r in zip(guides, part.guide_rows)])

    windows = _each_window("shape", latent_cells, weights, budget, halos, decode_one)
    if whole:
        return whole[0]
    _note_windows("shape", windows, halos)

    # The guides of each level, put in one-pass row order; their child flags give the cells of the next level.
    values = [torch.cat([v[level] for v in guide_values if level < len(v)]) for level in range(ups)]
    rows, cells = tiles.join_guides(latent_cells, parts, ups, lambda level, at: _np(values[level][_rows(at)] > 0))
    guides = [(_with_batch(cells[level], coords), values[level][_rows(rows[level])]) for level in range(ups)]
    try:
        joined = tiles.join_mesh(cells[-1], parts)
    except ValueError as e:
        raise comfy_side.changed(str(e)) from None
    vertices = torch.cat(own_vertices)[_rows(joined.vertex_source)]
    if joined.spare_source.size:
        log.info("VGAC TRELLIS.2 shape decode: %d vertices taken from a halo because no window owns their cell", joined.spare_source.size)
        vertices = torch.cat([vertices, torch.cat(spare_vertices)[_rows(joined.spare_source)]])
    return vertices, torch.from_numpy(joined.faces), guides


def _with_batch(cells: np.ndarray, like) -> torch.Tensor:
    """[n, 4] coordinates with batch column 0, in the dtype of `like`."""
    spatial = torch.from_numpy(np.ascontiguousarray(cells)).to(like.dtype)
    return torch.cat([torch.zeros((spatial.shape[0], 1), dtype=like.dtype), spatial], dim=1)


def run_texture_decode(dec, feats, coords, guides, budget: int, halos: list[int] | None = None):
    """Decode one sample's texture latent along the shape decode's guides. `guides` are (coords [n, 4], child logits
    [n, 8]) per level for this sample; rows are matched to the decoder's cells by coordinate, so their order is free.

    Returns (values [V, C] in the decoder's dtype, coords [V, 4]) on the CPU, in the row order of a one-pass decode."""
    device = feats.device
    ups = len(comfy_side.levels(dec)) - 1
    if len(guides) != ups:
        raise ValueError(f"the texture decode needs {ups} guide levels, got {len(guides)}")
    halos = halos or tiles.halo_steps(tiles.exact_halo(comfy_side.reach(dec), ups))
    latent_cells = _np(coords[:, 1:]).astype(np.int32)
    lookups = [tiles.Lookup(tiles.pack(_np(c[:, 1:]))) for c, _ in guides]
    flags = [_np(f > 0) for _, f in guides]
    values_on_device = [f.to(device) for _, f in guides]
    whole = tiles.follow_guides(latent_cells, lookups, flags)
    final_cells = whole.cells[-1]
    final = tiles.Lookup(tiles.pack(final_cells))
    # Exact size of each latent cell: how many cells of the level before the last descend from it.
    weights = np.bincount(whole.roots[ups - 1], minlength=latent_cells.shape[0]).astype(np.float64)
    result: list[torch.Tensor] = []
    filled = np.zeros(final_cells.shape[0], dtype=bool)

    def decode_one(window: tiles.Window, rows: np.ndarray, budget: int, alone: bool) -> None:
        local = tiles.follow_guides(latent_cells[rows], lookups, flags)

        def guide(level: int, cells) -> torch.Tensor:
            expected = torch.from_numpy(local.cells[level]).to(device)
            if cells.shape[0] != expected.shape[0] or not torch.equal(cells[:, 1:].to(expected.dtype), expected):
                raise comfy_side.changed("the decoder's row order has changed")
            at = _rows(local.rows[level], device)
            picked = values_on_device[level][at.clamp(min=0)]
            picked[at < 0] = 0
            return picked

        at = _rows(rows, device)
        _peak_start(device)
        out, _ = comfy_side.walk(dec, comfy_side.sparse_tensor(feats[at], coords[at]), guide=guide)
        cells = local.cells[-1]
        log.info("VGAC TRELLIS.2 texture decode: window of %d tokens (halo %d), %d points before the last level, %d at the last, peak %.2f GB",
                 rows.size, window.halo, local.cells[ups - 1].shape[0], cells.shape[0], _peak_gb(device))
        if out is None:
            return
        if out.feats.shape[0] != cells.shape[0]:
            raise RuntimeError("VGAC TRELLIS.2 texture decode: the decoder returned a different number of rows than its guides give")
        owned = window.owns(cells, ups)
        target = final.find(tiles.pack(cells[owned]))
        if (target < 0).any():
            raise RuntimeError("VGAC TRELLIS.2 texture decode: a window produced a cell the whole model does not have")
        values = (out.feats * 0.5 + 0.5)[_rows(np.nonzero(owned)[0], device)].cpu()
        if not result:
            result.append(torch.zeros((final_cells.shape[0], values.shape[1]), dtype=values.dtype))
        result[0][_rows(target)] = values
        filled[target] = True

    windows = _each_window("texture", latent_cells, weights, budget, halos, decode_one)
    _note_windows("texture", windows, halos)
    if not result or not filled.all():
        raise RuntimeError("VGAC TRELLIS.2 texture decode: some cells were not decoded by any window")
    return result[0], _with_batch(final_cells, coords)


def _split_latent(samples, coords, coord_counts) -> list[tuple[torch.Tensor, torch.Tensor]]:
    """(values [n, C], coords [n, 4]) per sample of a sparse latent, as ComfyUI's own nodes read it."""
    values = samples.squeeze(-1).transpose(1, 2)  # [B, T, C]
    if coord_counts is None:
        values = values.reshape(-1, values.shape[-1])
        ids = torch.unique(coords[:, 0]).tolist()
        if len(ids) <= 1:
            return [(values, coords)]
        return [(values[(coords[:, 0] == i).to(values.device)], coords[coords[:, 0] == i]) for i in ids]
    counts = [int(c) for c in coord_counts.tolist()]
    if sum(counts) != coords.shape[0]:
        raise ValueError(f"the latent's token counts add up to {sum(counts)} but it has {coords.shape[0]} cells")
    ordered = coords.index_select(0, torch.argsort(coords[:, 0].to(torch.int64), stable=True))
    out, start = [], 0
    for i, count in enumerate(counts):
        out.append((values[i, :count], ordered[start:start + count]))
        start += count
    return out


def _clear_card(vae, sample_shape, points: int, forced_budget: int, device) -> int:
    """Free the card for a decode of `points` points, load the VAE, and return the point budget for one window."""
    value_bytes = comfy_side.value_bytes(vae.vae_dtype)
    need = memory.decode_bytes(points, value_bytes)
    reserved = comfy_side.reserved_bytes()
    comfy_side.clear_card(need + comfy_side.model_bytes(vae) + reserved, device)
    comfy_side.load_for_decode(vae, sample_shape, memory.FIXED_BYTES)
    free = comfy_side.free_bytes(device)
    budget = int(forced_budget) if forced_budget > 0 else memory.point_budget(free, reserved, value_bytes)
    log.info("VGAC TRELLIS.2 decode: about %d points, %.2f GB if decoded in one piece; %.2f GB free on the card; at most %d points per window",
             points, need / memory.GB, free / memory.GB, budget)
    if budget <= 0:
        raise RuntimeError(NO_ROOM)
    return budget


_BUDGET_INPUT = ("INT", {"default": 0, "min": 0, "max": 2 ** 31 - 1,
                         "tooltip": "Most points decoded in one piece. 0 works it out from the card's free memory."})


class VGACTrellisShapeDecode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"samples": ("LATENT",), "vae": ("VAE",)}, "optional": {"point_budget": _BUDGET_INPUT}}

    RETURN_TYPES = ("MESH", "SHAPE_SUBDIVIDES")
    RETURN_NAMES = ("mesh", "shape_subdivides")
    FUNCTION = "decode"
    CATEGORY = "3D/VGAC"

    def decode(self, samples, vae, point_budget=0):
        # The mesh grid follows the resolution the latent was sampled at (64 cells for 1024), when the latent says so.
        coord_resolution = samples.get("coord_resolution")
        resolution = int(coord_resolution) * 16 if coord_resolution is not None else int(vae.first_stage_model.resolution.item())
        model_frame = samples.get("model_frame", "y_up")
        device = comfy_side.torch_device()
        dec = comfy_side.shape_decoder(vae)
        groups = _split_latent(samples["samples"], samples["coords"], samples.get("coord_counts"))
        tokens = max(values.shape[0] for values, _ in groups)
        budget = _clear_card(vae, samples["samples"].shape, tokens * memory.SHAPE_POINTS_PER_TOKEN, point_budget, device)
        scale = comfy_side.shape_format()
        vertex_list, face_list, guides_per_sample = [], [], []
        for values, coords in groups:
            coords = coords.to(device).clone()
            coords[:, 0] = 0
            values = scale.process_out(values.to(device)).to(vae.vae_dtype)
            vertices, faces, guides = run_shape_decode(dec, values, coords, resolution, budget)
            if model_frame == "z_up":  # the TRELLIS.2 training frame; glTF is Y-up
                vertices = torch.stack([vertices[..., 0], vertices[..., 2], -vertices[..., 1]], dim=-1)
            vertex_list.append(vertices.float().cpu())
            face_list.append(faces.int().cpu())
            guides_per_sample.append(guides)

        out_device = comfy_side.output_device()
        subdivides = []
        for level in range(len(guides_per_sample[0])):
            coords = torch.cat([_batch(g[level][0], i) for i, g in enumerate(guides_per_sample)])
            values = torch.cat([g[level][1] for g in guides_per_sample])
            subdivides.append(comfy_side.sparse_tensor(values.to(out_device), coords.to(out_device), level))
        if all(v.shape == vertex_list[0].shape for v in vertex_list) and all(f.shape == face_list[0].shape for f in face_list):
            mesh = comfy_side.mesh_type()(vertices=torch.stack(vertex_list), faces=torch.stack(face_list))
        else:
            mesh = comfy_side.pack_meshes(vertex_list, face_list)
        return (mesh, subdivides)


def _batch(coords: torch.Tensor, index: int) -> torch.Tensor:
    coords = coords.clone()
    coords[:, 0] = index
    return coords


class VGACTrellisTextureDecode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"samples": ("LATENT",), "vae": ("VAE",), "shape_subdivides": ("SHAPE_SUBDIVIDES",)},
                "optional": {"point_budget": _BUDGET_INPUT}}

    RETURN_TYPES = ("VOXEL",)
    RETURN_NAMES = ("voxel_colors",)
    FUNCTION = "decode"
    CATEGORY = "3D/VGAC"

    def decode(self, samples, vae, shape_subdivides, point_budget=0):
        device = comfy_side.torch_device()
        dec = comfy_side.texture_decoder(vae)
        budget = _clear_card(vae, samples["samples"].shape, int(shape_subdivides[-1].feats.shape[0]), point_budget, device)
        model_frame = samples.get("model_frame", "y_up")
        coord_resolution = samples.get("coord_resolution")
        groups = _split_latent(samples["samples"], samples["coords"], samples.get("coord_counts"))
        scale = comfy_side.texture_format()
        value_list, coord_list = [], []
        for index, (values, coords) in enumerate(groups):
            sample_id = int(coords[0, 0]) if coords.shape[0] else index
            guides = []
            for sub in shape_subdivides:
                mine = sub.coords[:, 0] == sample_id
                guides.append((sub.coords[mine], sub.feats[mine]))
            coords = coords.to(device).clone()
            coords[:, 0] = 0
            values = scale.process_out(values.to(device)).to(vae.vae_dtype)
            colours, cells = run_texture_decode(dec, values, coords, guides, budget)
            value_list.append(colours)
            coord_list.append(_batch(cells, sample_id))
        # All decoded channels are kept: base colour (0:3), metallic (3), roughness (4), alpha (5), each in 0 to 1.
        colours, voxel_coords = torch.cat(value_list), torch.cat(coord_list)

        if coord_resolution is not None:
            tex_resolution = int(coord_resolution) * 16
        elif voxel_coords.numel() > 0:
            highest = int(voxel_coords[:, 1:].max().item()) + 1
            tex_resolution = next((r for r in (256, 512, 1024, 1536, 2048) if r >= highest), highest)
        else:
            tex_resolution = 1024
        # Z-up cells to Y-up, (x, y, z) -> (x, z, R-1-y), the same turn the shape decode gives the mesh.
        if model_frame == "z_up" and voxel_coords.numel() > 0:
            spatial = voxel_coords[:, 1:]
            turned = torch.stack([spatial[:, 0], spatial[:, 2], (tex_resolution - 1) - spatial[:, 1]], dim=-1)
            voxel_coords = torch.cat([voxel_coords[:, :1], turned], dim=-1)
        out_device = comfy_side.output_device()
        return (comfy_side.voxel_type()(voxel_coords.to(out_device), colours.to(out_device), tex_resolution),)


NODE_CLASS_MAPPINGS = {"VGACTrellisShapeDecode": VGACTrellisShapeDecode, "VGACTrellisTextureDecode": VGACTrellisTextureDecode}
NODE_DISPLAY_NAME_MAPPINGS = {"VGACTrellisShapeDecode": "VGAC TRELLIS.2 Shape Decode", "VGACTrellisTextureDecode": "VGAC TRELLIS.2 Texture Decode"}
