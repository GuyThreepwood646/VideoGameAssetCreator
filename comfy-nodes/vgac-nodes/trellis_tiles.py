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

"""Window planning and joining for the tiled TRELLIS.2 decode. numpy only: it runs without ComfyUI, torch or a card.

The decoder works on a sparse grid of cells that doubles in resolution at each level: level 0 is the latent grid (64
cells a side for a 1024 build), the last level is the mesh grid. Every cell at level s descends from the latent cell
`cell >> s`, its root.

A window is a box of latent cells decoded on its own. It owns the cells of its core and also decodes a halo around the
core, so that owned cells see the same neighbours as in a one-pass decode. Cores never overlap and together hold every
latent cell, so every cell at every level has exactly one owner: the window whose core holds its root. Joining keeps
each window's owned rows and puts them in the row order a one-pass decode produces, which makes the joined result
interchangeable with it.

The decoder is local (3x3x3 sparse convolutions and per-point layers), so a halo at least as wide as the decoder's
reach gives owned cells the values of a one-pass decode. `exact_halo` turns the reach into a halo width.

The windowing idea (a core plus a halo, one owner per cell, the owning window's copy of a shared vertex first) follows
Aero-Ex/ComfyUI-Trellis2-TiledDecode (commit e068dbe; stated MIT in its README and pyproject.toml, the repository
ships no notice text). No code from it is used here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

AXIS_BITS = 21  # bits per axis in a packed cell key; the mesh grid is far below 2**21 cells a side
FIND_CHUNK = 1 << 22  # keys looked up at a time, so the temporary arrays stay small next to a mesh of many millions of faces
SMALLER_HALOS = (12, 8)  # tried in turn when windows with the exact halo cannot fit the budget; seams are then approximate
SPLIT_GAIN = 0.9  # a cut that does not yet fit must bring the larger half under this share of the window, or it is not made


class TooLarge(ValueError):
    """No set of windows fits the point budget."""


def pack(cells: np.ndarray) -> np.ndarray:
    """One int64 key per cell from its three coordinates."""
    c = np.asarray(cells).astype(np.int64, copy=False)
    return (c[..., 0] << (2 * AXIS_BITS)) | (c[..., 1] << AXIS_BITS) | c[..., 2]


def unpack(keys: np.ndarray) -> np.ndarray:
    k = np.asarray(keys, dtype=np.int64)
    mask = (1 << AXIS_BITS) - 1
    return np.stack([k >> (2 * AXIS_BITS), (k >> AXIS_BITS) & mask, k & mask], axis=-1).astype(np.int32)


class Lookup:
    """Row number by cell key."""

    def __init__(self, keys: np.ndarray):
        keys = np.asarray(keys, dtype=np.int64)
        self.order = np.argsort(keys, kind="stable")
        self.sorted = keys[self.order]

    def find(self, keys: np.ndarray, dtype=np.int64) -> np.ndarray:
        """Row of each key, -1 where the key is absent. With repeated keys the first row is returned."""
        keys = np.asarray(keys, dtype=np.int64)
        out = np.full(keys.shape, -1, dtype=dtype)
        if self.sorted.size == 0 or keys.size == 0:
            return out
        flat_keys, flat_out = keys.reshape(-1), out.reshape(-1)
        for start in range(0, flat_keys.size, FIND_CHUNK):
            chunk = flat_keys[start:start + FIND_CHUNK]
            at = np.minimum(np.searchsorted(self.sorted, chunk), self.sorted.size - 1)
            flat_out[start:start + FIND_CHUNK] = np.where(self.sorted[at] == chunk, self.order[at], -1)
        return out


def in_box(cells: np.ndarray, lo, hi) -> np.ndarray:
    c = np.asarray(cells)
    return np.all((c >= np.asarray(lo)) & (c < np.asarray(hi)), axis=-1)


@dataclass(frozen=True)
class Window:
    lo: tuple[int, int, int]  # latent cells decoded, half-open box
    hi: tuple[int, int, int]
    core_lo: tuple[int, int, int]  # latent cells owned, half-open box
    core_hi: tuple[int, int, int]
    halo: int
    step: int  # index of `halo` in the halo list the plan was made with

    def holds(self, latent_cells: np.ndarray) -> np.ndarray:
        return in_box(latent_cells, self.lo, self.hi)

    def owns(self, cells: np.ndarray, level: int = 0) -> np.ndarray:
        """True for cells at `level` whose root lies in the core."""
        return in_box(np.asarray(cells) >> level, self.core_lo, self.core_hi)


def exact_halo(reach: int, levels: int) -> int:
    """Halo width in latent cells that gives owned cells one-pass values. `reach` is the decoder's reach in cells of
    the last level, `levels` the number of doublings. One more last-level cell is needed on the high side because a
    face also reads the three neighbours above its own cell."""
    return reach // (1 << levels) + 1


def halo_steps(exact: int) -> list[int]:
    return [exact] + [h for h in SMALLER_HALOS if h < exact]


def plan(cells: np.ndarray, weights: np.ndarray, budget: float, halos: list[int], rows: np.ndarray | None = None,
         step: int = 0) -> list[Window]:
    """Windows whose cores hold `rows` of the latent `cells` (all rows by default) and whose estimated size, the sum of
    `weights` over core and halo, is within `budget`.

    A core over budget is cut in two across the axis that leaves the smaller larger half, at the weighted middle, and
    each half is shrunk to the box of its own cells. A cut is made when both halves then fit, or when it gains enough
    to be worth cutting further (SPLIT_GAIN). When no cut helps, the next smaller halo is used for that window and the
    ones cut from it. Raises TooLarge when the smallest halo still does not fit."""
    cells = np.asarray(cells)
    weights = np.asarray(weights, dtype=np.float64)
    if cells.shape[0] == 0:
        return []
    bound_lo, bound_hi = cells.min(axis=0), cells.max(axis=0) + 1
    rows = np.arange(cells.shape[0]) if rows is None else np.asarray(rows)

    def window(own: np.ndarray, step: int) -> tuple[Window, float]:
        core_lo, core_hi = cells[own].min(axis=0), cells[own].max(axis=0) + 1
        lo, hi = np.maximum(core_lo - halos[step], bound_lo), np.minimum(core_hi + halos[step], bound_hi)
        size = float(weights[in_box(cells, lo, hi)].sum())
        as_tuple = lambda a: tuple(int(v) for v in a)
        return Window(as_tuple(lo), as_tuple(hi), as_tuple(core_lo), as_tuple(core_hi), int(halos[step]), step), size

    def fit(own: np.ndarray, step: int) -> list[Window]:
        win, size = window(own, step)
        if size <= budget:
            return [win]
        best = None
        for axis in range(3):
            along = cells[own, axis]
            if along.min() == along.max():
                continue
            # Weight below each candidate cut; the cut nearest to half the weight.
            values, where = np.unique(along, return_inverse=True)
            per_value = np.bincount(where, weights=weights[own])
            below = np.cumsum(per_value)[:-1]
            cut = values[1:][int(np.argmin(np.abs(below - per_value.sum() / 2)))]
            low, high = own[along < cut], own[along >= cut]
            larger = max(window(low, step)[1], window(high, step)[1])
            if best is None or larger < best[0]:
                best = (larger, low, high)
        if best is not None and (best[0] <= budget or best[0] <= SPLIT_GAIN * size):
            return fit(best[1], step) + fit(best[2], step)
        if step + 1 < len(halos):
            return fit(own, step + 1)
        raise TooLarge(f"a window of about {size:.0f} points is over the budget of {budget:.0f} and cannot be cut smaller")

    return fit(rows, step)


def expand(cells: np.ndarray, flags: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cells of the next level from each cell's eight child flags, in the decoder's own row order: parents in row
    order, children of one parent by child number. Child number bit i moves the child along axis i. Returns the child
    cells and the row of each child's parent."""
    parent, child = np.nonzero(np.asarray(flags))
    out = np.asarray(cells)[parent].astype(np.int32) * 2
    for axis in range(3):
        out[:, axis] += (child >> axis) & 1
    return out, parent


def face_owner_keys(cells: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Key of the cell that emitted each triangle. The dual-grid mesher builds a quad from a cell and three neighbours
    one step up along two axes, so the emitting cell is the smallest corner of the triangle's three cells."""
    cells, faces = np.asarray(cells), np.asarray(faces)
    key = np.zeros(faces.shape[0], dtype=np.int64)
    for axis in range(3):
        along = cells[:, axis]
        low = np.minimum(np.minimum(along[faces[:, 0]], along[faces[:, 1]]), along[faces[:, 2]])
        key = (key << AXIS_BITS) | low.astype(np.int64)
    return key


@dataclass
class ShapePart:
    """What one window contributes to the joined mesh. Row arrays index the window's own outputs; the caller keeps
    those rows of its vertex positions and guide values, in this order."""
    own_rows: np.ndarray  # vertices this window owns
    own_keys: np.ndarray
    spare_rows: np.ndarray  # halo vertices its faces use, wanted only if no window owns that cell
    spare_keys: np.ndarray
    face_keys: np.ndarray  # [F, 3] cell keys of the faces it owns
    quad_keys: np.ndarray  # [F / 2] emitting cell of each owned quad (two triangles)
    guide_rows: list[np.ndarray]  # per level: guide rows it owns
    guide_keys: list[np.ndarray]


def take_shape(window: Window, levels: int, cells: np.ndarray, faces: np.ndarray, guide_cells: list[np.ndarray]) -> ShapePart:
    """Pick out of one window's decode what the window owns. `cells` are the last-level cells (one per vertex, in row
    order), `faces` index those rows, `guide_cells[s]` are the level-s cells the window produced guide rows for."""
    cells, faces = np.asarray(cells), np.asarray(faces)
    keys = pack(cells)
    owned = window.owns(cells, levels)
    if faces.shape[0] % 2:
        raise ValueError("faces must come two to a quad")
    owner = face_owner_keys(cells, faces)
    # Both triangles of a quad come from the same cell, so they are kept or dropped together.
    quad_owner = owner[0::2]
    if not np.array_equal(quad_owner, owner[1::2]):
        raise ValueError("the two triangles of a quad disagree on the cell that emitted them")
    keep_quad = window.owns(unpack(quad_owner), levels)
    kept_faces = faces.reshape(-1, 2, 3)[keep_quad].reshape(-1, 3)
    used = np.zeros(cells.shape[0], dtype=bool)
    used[kept_faces.reshape(-1)] = True
    spare = used & ~owned
    guide_rows = [np.nonzero(window.owns(gc, s))[0] for s, gc in enumerate(guide_cells)]
    return ShapePart(
        own_rows=np.nonzero(owned)[0], own_keys=keys[owned],
        spare_rows=np.nonzero(spare)[0], spare_keys=keys[spare],
        face_keys=keys[kept_faces], quad_keys=quad_owner[keep_quad],
        guide_rows=guide_rows, guide_keys=[pack(np.asarray(gc)[r]) for gc, r in zip(guide_cells, guide_rows)],
    )


def join_guides(latent_cells: np.ndarray, parts: list[ShapePart], levels: int, flags_of) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """The windows' guide rows put in one-pass row order. Returns (rows, cells): rows[s] gives, for each level-s cell in
    one-pass order, its row among the windows' owned guide rows of that level laid end to end; cells[s] are those
    cells, with one more entry for the last level. flags_of(level, rows) must return the child flags of those rows;
    they decide the cells of the next level."""
    cells = np.asarray(latent_cells).astype(np.int32)
    rows_out, cells_out = [], [cells]
    for level in range(levels):
        keys = [p.guide_keys[level] for p in parts if level < len(p.guide_keys)]
        rows = Lookup(np.concatenate(keys) if keys else np.zeros(0, np.int64)).find(pack(cells))
        if (rows < 0).any():
            raise ValueError("a cell has no window that owns it")
        cells, _ = expand(cells, flags_of(level, rows))
        rows_out.append(rows)
        cells_out.append(cells)
    return rows_out, cells_out


@dataclass
class JoinedMesh:
    vertex_source: np.ndarray  # per joined vertex: row in the windows' owned vertices, laid end to end
    spare_source: np.ndarray  # further vertices, appended after those: rows in the windows' spare vertices, end to end
    faces: np.ndarray  # [F, 3] int32, joined vertex numbers


def join_mesh(final_cells: np.ndarray, parts: list[ShapePart]) -> JoinedMesh:
    """One mesh from the windows' parts. `final_cells` are the last-level cells in one-pass row order (from `expand`
    over the joined guides); vertex i of the result is the vertex of final_cells[i], and faces come in one-pass order:
    by emitting cell, each cell's quads in the order its window emitted them.

    A face may use a halo vertex whose cell no window owns. That happens only when windows disagree near a cut (a
    halo smaller than the exact one); such vertices are appended after the owned ones."""
    final_keys = pack(final_cells)
    own_keys = np.concatenate([p.own_keys for p in parts]) if parts else np.zeros(0, np.int64)
    vertex_source = Lookup(own_keys).find(final_keys)
    if (vertex_source < 0).any() or own_keys.size != final_keys.size:
        raise ValueError("the windows' owned vertices do not match the joined guides")
    final = Lookup(final_keys)

    # Part by part, so the int64 keys of all faces are never held twice.
    found = [final.find(p.face_keys, np.int32) for p in parts]
    missing_keys = [p.face_keys[f < 0] for p, f in zip(parts, found)]
    faces = np.concatenate(found) if found else np.zeros((0, 3), np.int32)
    del found
    spare_source = np.zeros(0, dtype=np.int64)
    if any(m.size for m in missing_keys):
        wanted, where = np.unique(np.concatenate(missing_keys), return_inverse=True)
        spare_source = Lookup(np.concatenate([p.spare_keys for p in parts])).find(wanted)
        if (spare_source < 0).any():
            raise ValueError("a face uses a vertex that no window produced")
        faces[faces < 0] = final_keys.size + where
    quad_rank = final.find(np.concatenate([p.quad_keys for p in parts]) if parts else np.zeros(0, np.int64))
    if (quad_rank < 0).any():
        raise ValueError("a face was emitted by a cell that no window owns")
    order = np.argsort(quad_rank, kind="stable")
    faces = faces.reshape(-1, 2, 3)[order].reshape(-1, 3)
    return JoinedMesh(vertex_source=vertex_source, spare_source=spare_source, faces=faces)


@dataclass
class GuidedLevels:
    """The cells a guided decode visits. rows[s] gives, for each level-s cell, its row in the guide of that level (-1
    where the guide has none, which means no children); cells[s] are the level-s cells in decoder row order, with one
    more entry for the last level; roots[s] is the latent row each of them descends from."""
    rows: list[np.ndarray]
    cells: list[np.ndarray]
    roots: list[np.ndarray]


def follow_guides(latent_cells: np.ndarray, guides: list[Lookup], guide_flags: list[np.ndarray]) -> GuidedLevels:
    """Walk the levels as the guided (texture) decoder does: at each level every cell takes its child flags from the
    guide row of the same cell."""
    cells = np.asarray(latent_cells).astype(np.int32)
    root = np.arange(cells.shape[0])
    out = GuidedLevels(rows=[], cells=[cells], roots=[root])
    for lookup, flags in zip(guides, guide_flags):
        rows = lookup.find(pack(cells))
        flags = np.asarray(flags, dtype=bool)
        picked = flags[np.maximum(rows, 0)] & (rows >= 0)[:, None] if flags.shape[0] else np.zeros((rows.size, 8), dtype=bool)
        cells, parent = expand(cells, picked)
        root = root[parent]
        out.rows.append(rows)
        out.cells.append(cells)
        out.roots.append(root)
    return out
