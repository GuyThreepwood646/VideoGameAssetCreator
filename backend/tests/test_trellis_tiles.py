"""The tiled TRELLIS.2 decode's planning, joining and memory arithmetic (comfy-nodes/vgac-nodes), without ComfyUI or a card.

The decoder is stood in for by a small integer one with the same structure: 3x3x3 sums over the cells present at each
level, child flags and face choices that depend on those sums, and the dual-grid mesher's quad layout. Integers make
"equal to the one-pass result" an exact comparison.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

PACK = Path(__file__).resolve().parents[2] / "comfy-nodes" / "vgac-nodes"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"vgac_nodes_{name}", PACK / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tiles = load("trellis_tiles")
memory = load("trellis_memory")

LEVELS = 2
REACH = 4 + 2 + 1  # one 3x3x3 sum at each of the three levels, in last-level cells
MOD = 1_000_003
NEIGHBOURS = [np.array((x, y, z)) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)]
# The dual-grid mesher's quad: the emitting cell and three neighbours, per crossed axis (comfy/ldm/trellis2/vae.py).
QUAD = np.array([
    [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]],
    [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
    [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]],
])
SPLITS = (np.array([0, 1, 2, 0, 2, 3]), np.array([0, 1, 3, 3, 1, 2]))


def smooth(cells: np.ndarray, values: np.ndarray) -> np.ndarray:
    look = tiles.Lookup(tiles.pack(cells + 1))  # +1 keeps a neighbour at -1 a valid key
    out = np.zeros_like(values)
    for k, step in enumerate(NEIGHBOURS):
        rows = look.find(tiles.pack(cells + 1 + step))
        out += np.where(rows >= 0, values[np.maximum(rows, 0)] * (k + 2), 0)
    return out % MOD


def child_logits(values: np.ndarray) -> np.ndarray:
    return (values[:, None] * np.array([3, 5, 8, 11, 13, 17, 19, 23])[None, :]) % 7 - 1  # positive for 5 residues of 7


def mesh(cells: np.ndarray, values: np.ndarray) -> np.ndarray:
    look = tiles.Lookup(tiles.pack(cells))
    crossed = (values[:, None] * np.array([5, 6, 7])[None, :]) % 3 == 0
    row, axis = np.nonzero(crossed)
    quad = look.find(tiles.pack(cells[row][:, None, :] + QUAD[axis]))
    quad = quad[(quad >= 0).all(axis=1)]
    first = (values[quad[:, 0]] + values[quad[:, 2]]) % 2 == 0
    return np.where(first[:, None], quad[:, SPLITS[0]], quad[:, SPLITS[1]]).reshape(-1, 3)


def decode(cells: np.ndarray, values: np.ndarray, flags: list[np.ndarray] | None = None):
    """The stand-in decoder. With `flags` (one array per level, row for row) it is the guided decode."""
    guide_cells, guide_logits = [], []
    for level in range(LEVELS):
        values = smooth(cells, values)
        logits = child_logits(values)
        guide_cells.append(cells)
        guide_logits.append(logits)
        children, parent = tiles.expand(cells, logits > 0 if flags is None else flags[level])
        number = (children[:, 0] & 1) + 2 * (children[:, 1] & 1) + 4 * (children[:, 2] & 1)
        cells, values = children, (values[parent] * 3 + number + 1) % MOD
    values = smooth(cells, values)
    return cells, values, mesh(cells, values), guide_cells, guide_logits


def subject(seed: int = 3, size=(12, 10, 8), keep: float = 0.7):
    rng = np.random.default_rng(seed)
    grid = np.stack(np.meshgrid(*[np.arange(n) for n in size], indexing="ij"), axis=-1).reshape(-1, 3)
    cells = grid[rng.random(grid.shape[0]) < keep].astype(np.int32) + 2
    rng.shuffle(cells)
    return cells, rng.integers(1, MOD, size=cells.shape[0])


def tiled_shape(cells, values, windows):
    parts, own, spare, logits = [], [], [], [[] for _ in range(LEVELS)]
    for window in windows:
        held = window.holds(cells)
        final, vertex, faces, guide_cells, guide_logits = decode(cells[held], values[held])
        part = tiles.take_shape(window, LEVELS, final, faces, guide_cells)
        parts.append(part)
        own.append(vertex[part.own_rows])
        spare.append(vertex[part.spare_rows])
        for level in range(LEVELS):
            logits[level].append(guide_logits[level][part.guide_rows[level]])
    logits = [np.concatenate(v) for v in logits]
    rows, level_cells = tiles.join_guides(cells, parts, LEVELS, lambda level, at: logits[level][at] > 0)
    joined = tiles.join_mesh(level_cells[-1], parts)
    vertex = np.concatenate([np.concatenate(own)[joined.vertex_source], np.concatenate(spare)[joined.spare_source]])
    return level_cells, [logits[level][rows[level]] for level in range(LEVELS)], vertex, joined


def cut_windows(cells, halo: int, share: float = 0.6):
    windows = tiles.plan(cells, np.ones(cells.shape[0]), share * cells.shape[0], [halo])
    assert len(windows) > 1
    return windows


# ---- planning ----

def test_exact_halo_and_steps():
    assert tiles.exact_halo(277, 4) == 18  # the TRELLIS.2 decoder: 5, 18, 10, 6 and 1 convolutions at its five levels
    assert tiles.exact_halo(REACH, LEVELS) == 2
    assert tiles.exact_halo(16, 4) == 2  # a reach of exactly one latent cell still needs the face neighbour's cell
    assert tiles.halo_steps(18) == [18, 12, 8]
    assert tiles.halo_steps(10) == [10, 8]


def test_one_window_when_the_subject_fits():
    cells, _ = subject()
    (window,) = tiles.plan(cells, np.ones(cells.shape[0]), cells.shape[0], [2])
    assert window.holds(cells).all() and window.owns(cells).all()
    assert window.lo == tuple(cells.min(axis=0)) and window.hi == tuple(cells.max(axis=0) + 1)


@pytest.mark.parametrize("share", [0.75, 0.6, 0.45])
def test_cores_hold_every_cell_once_and_windows_fit(share):
    cells, _ = subject()
    weights = np.random.default_rng(1).integers(1, 9, size=cells.shape[0]).astype(float)
    budget = share * weights.sum()
    windows = tiles.plan(cells, weights, budget, [2])
    assert len(windows) > 1
    owners = np.sum([w.owns(cells) for w in windows], axis=0)
    assert (owners == 1).all()
    low, high = cells.min(axis=0), cells.max(axis=0) + 1
    for w in windows:
        assert weights[w.holds(cells)].sum() <= budget
        assert (np.array(w.lo) >= low).all() and (np.array(w.hi) <= high).all()  # the halo stops at the subject's box
        assert (np.array(w.lo) == np.maximum(np.array(w.core_lo) - w.halo, low)).all()
        assert (np.array(w.hi) == np.minimum(np.array(w.core_hi) + w.halo, high)).all()


def test_halo_steps_down_when_no_cut_helps_then_gives_up():
    cells, _ = subject(size=(12, 12, 12), keep=1.0)
    weights = np.ones(cells.shape[0])
    # Halo 6 on a 12-cell box: every window still holds the whole box, so only the smaller halo can fit.
    windows = tiles.plan(cells, weights, 0.6 * cells.shape[0], [6, 1])
    assert {w.halo for w in windows} == {1} and all(w.step == 1 for w in windows)
    assert (np.sum([w.owns(cells) for w in windows], axis=0) == 1).all()
    with pytest.raises(tiles.TooLarge):
        tiles.plan(cells, weights, 0.6 * cells.shape[0], [6])
    with pytest.raises(tiles.TooLarge):
        tiles.plan(cells, weights, 0.5, [1])  # under one cell


def test_a_small_gain_is_taken_at_the_same_halo_when_it_is_enough():
    # A 20-cell row with halo 8: either half with its halo holds 18 of the 20 cells, a gain of only a tenth.
    cells = np.stack([np.arange(20), np.zeros(20, int), np.zeros(20, int)], axis=1).astype(np.int32)
    weights = np.ones(20)
    fits = tiles.plan(cells, weights, 18, [8, 2])
    assert [w.halo for w in fits] == [8, 8]  # enough: stays at the exact halo
    tighter = tiles.plan(cells, weights, 17, [8, 2])
    assert {w.halo for w in tighter} == {2}  # not enough and too small a gain to keep cutting: the smaller halo


def test_a_window_found_too_large_is_cut_again_from_its_core():
    cells, _ = subject()
    weights = np.ones(cells.shape[0])
    budget = 0.75 * cells.shape[0]
    first = tiles.plan(cells, weights, budget, [2])[0]
    weights[first.holds(cells)] *= 2.0  # the decode found twice the points it was planned for
    again = tiles.plan(cells, weights, budget, [2], rows=np.nonzero(first.owns(cells))[0], step=first.step)
    assert len(again) > 1
    assert (np.sum([w.owns(cells) for w in again], axis=0) == first.owns(cells)).all()
    assert all(weights[w.holds(cells)].sum() <= budget for w in again)


# ---- row order and ownership ----

def test_expand_gives_children_in_decoder_row_order():
    cells = np.array([[1, 2, 3], [0, 0, 0]], dtype=np.int32)
    flags = np.zeros((2, 8), dtype=bool)
    flags[0, [1, 6]] = True  # child 1 steps along the first axis, child 6 along the second and third
    flags[1, 0] = True
    children, parent = tiles.expand(cells, flags)
    assert children.tolist() == [[3, 4, 6], [2, 5, 7], [0, 0, 0]]
    assert parent.tolist() == [0, 0, 1]


def test_pack_round_trip_and_lookup():
    cells = np.array([[0, 0, 0], [5, 1, 1535], [1535, 1535, 1535], [5, 1, 1534]])
    assert (tiles.unpack(tiles.pack(cells)) == cells).all()
    look = tiles.Lookup(tiles.pack(cells))
    assert look.find(tiles.pack(np.array([[5, 1, 1534], [9, 9, 9], [0, 0, 0]]))).tolist() == [3, -1, 0]
    assert tiles.Lookup(np.zeros(0, np.int64)).find(tiles.pack(cells)).tolist() == [-1] * 4


@pytest.mark.parametrize("axis", [0, 1, 2])
@pytest.mark.parametrize("split", [0, 1])
def test_a_face_belongs_to_the_cell_that_emitted_it(axis, split):
    emitter = np.array([7, 4, 9])
    cells = emitter + QUAD[axis]
    order = np.random.default_rng(axis).permutation(4)  # vertex rows in any order
    rows = np.argsort(order)
    faces = rows[SPLITS[split]].reshape(2, 3)
    assert (tiles.unpack(tiles.face_owner_keys(cells[order], faces)) == emitter).all()


# ---- joining ----

def test_tiled_decode_equals_one_pass_with_the_exact_halo():
    cells, values = subject()
    final, vertex, faces, guide_cells, guide_logits = decode(cells, values)
    assert faces.shape[0] > 200
    halo = tiles.exact_halo(REACH, LEVELS)
    level_cells, logits, tiled_vertex, joined = tiled_shape(cells, values, cut_windows(cells, halo))
    assert joined.spare_source.size == 0
    assert np.array_equal(level_cells[-1], final)
    assert np.array_equal(tiled_vertex, vertex)
    assert np.array_equal(joined.faces, faces)
    for level in range(LEVELS):
        assert np.array_equal(level_cells[level], guide_cells[level])
        assert np.array_equal(logits[level], guide_logits[level])


def test_a_halo_below_the_exact_one_does_not_equal_one_pass():
    cells, values = subject()
    final, vertex, faces, _, _ = decode(cells, values)
    level_cells, _, tiled_vertex, joined = tiled_shape(cells, values, cut_windows(cells, tiles.exact_halo(REACH, LEVELS) - 1))
    same = (np.array_equal(level_cells[-1], final) and np.array_equal(tiled_vertex[:final.shape[0]], vertex)
            and np.array_equal(joined.faces, faces))
    assert not same


@pytest.mark.parametrize("halo", [2, 1])
def test_vertices_on_a_cut_are_joined_once_and_faces_cross_it(halo):
    cells, values = subject()
    windows = cut_windows(cells, halo)
    level_cells, _, vertex, joined = tiled_shape(cells, values, windows)
    final = level_cells[-1]
    count = final.shape[0] + joined.spare_source.size
    assert np.unique(tiles.pack(final)).size == final.shape[0]  # one vertex per cell, however many windows decoded it
    assert vertex.shape[0] == count and joined.faces.min() >= 0 and joined.faces.max() < count
    assert np.unique(np.sort(joined.faces, axis=1), axis=0).shape[0] == joined.faces.shape[0]  # no face twice
    # Faces whose corners are owned by different windows exist and point at the one joined vertex of each cell.
    owner = np.argmax([w.owns(final, LEVELS) for w in windows], axis=0)
    inside = joined.faces[(joined.faces < final.shape[0]).all(axis=1)]
    crossing = (owner[inside].min(axis=1) != owner[inside].max(axis=1)).sum()
    borrowed = joined.faces.shape[0] - inside.shape[0]  # faces that reach a halo vertex no window owns
    assert crossing > 0
    assert (borrowed == 0) == (halo == tiles.exact_halo(REACH, LEVELS))
    # Each face's emitting cell is its smallest corner, and the faces come sorted by that cell's row.
    emitted_by = tiles.Lookup(tiles.pack(final)).find(tiles.face_owner_keys(final, inside))
    assert (emitted_by >= 0).all()
    if joined.spare_source.size == 0:
        assert (np.diff(emitted_by) >= 0).all()


def test_guided_decode_in_windows_equals_one_pass():
    cells, values = subject()
    _, _, _, guide_cells, guide_logits = decode(cells, values)
    shuffle = [np.random.default_rng(level).permutation(g.shape[0]) for level, g in enumerate(guide_cells)]
    lookups = [tiles.Lookup(tiles.pack(g[s])) for g, s in zip(guide_cells, shuffle)]  # guide rows in any order
    flags = [(g > 0)[s] for g, s in zip(guide_logits, shuffle)]
    other = np.random.default_rng(9).integers(1, MOD, size=cells.shape[0])  # the texture latent's own values

    whole = tiles.follow_guides(cells, lookups, flags)
    final, colour, _, _, _ = decode(cells, other, [f[r] for f, r in zip(flags, whole.rows)])
    assert np.array_equal(whole.cells[-1], final)
    assert np.array_equal(np.bincount(whole.roots[-1], minlength=cells.shape[0]).sum(), final.shape[0])

    look = tiles.Lookup(tiles.pack(final))
    out = np.full(final.shape[0], -1, dtype=np.int64)
    for window in cut_windows(cells, tiles.exact_halo(REACH, LEVELS)):
        held = window.holds(cells)
        local = tiles.follow_guides(cells[held], lookups, flags)
        local_final, local_colour, _, level_cells, _ = decode(cells[held], other[held], [f[np.maximum(r, 0)] & (r >= 0)[:, None] for f, r in zip(flags, local.rows)])
        for level in range(LEVELS):
            assert np.array_equal(level_cells[level], local.cells[level])
        owned = window.owns(local_final, LEVELS)
        out[look.find(tiles.pack(local_final[owned]))] = local_colour[owned]
    assert np.array_equal(out, colour)


def test_a_cell_missing_from_the_guide_has_no_children():
    cells = np.array([[0, 0, 0], [1, 0, 0]], dtype=np.int32)
    flags = [np.ones((1, 8), dtype=bool)]
    walked = tiles.follow_guides(cells, [tiles.Lookup(tiles.pack(cells[1:]))], flags)
    assert walked.rows[0].tolist() == [-1, 0]
    assert walked.cells[1].shape[0] == 8 and (walked.cells[1] >> 1 == cells[1]).all()
    assert walked.roots[1].tolist() == [1] * 8
    empty = tiles.follow_guides(cells, [tiles.Lookup(np.zeros(0, np.int64))], [np.zeros((0, 8), dtype=bool)])
    assert empty.cells[1].shape[0] == 0


# ---- memory ----

def test_decode_memory_follows_the_point_count():
    assert memory.bytes_per_point(2) == 1900
    # ComfyUI's shape decode asks for the same figure for every subject: 1.25 x 1024 x 1024 points, about 4.3 GB.
    fixed = memory.decode_bytes(1024 * 1024 * 5 // 4, 2)
    assert 4.2 * memory.GB < fixed < 4.4 * memory.GB
    # A decode of 12.95 million vertices has a quarter as many points before the last level and needs about 7.7 GB.
    large = memory.decode_bytes(12_950_000 // 4, 2)
    assert 7.6 * memory.GB < large < 7.8 * memory.GB
    assert memory.decode_bytes(0, 2) == memory.FIXED_BYTES
    assert memory.decode_bytes(1000, 4) - memory.decode_bytes(1000, 2) == 1000 * 896 * 2


def test_point_budget_is_what_fits_the_free_memory():
    free, reserved = 9 * memory.GB, int(1.5 * memory.GB)
    budget = memory.point_budget(free, reserved, 2)
    assert budget > 0
    assert memory.decode_bytes(budget, 2) <= free - reserved - memory.SAFETY_BYTES < memory.decode_bytes(budget + 1, 2)
    assert memory.point_budget(memory.FIXED_BYTES + reserved, reserved, 2) == 0
    assert memory.point_budget(0, reserved, 2) == 0
