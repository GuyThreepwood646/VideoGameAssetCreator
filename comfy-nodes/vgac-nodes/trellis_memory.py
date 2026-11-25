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

"""Card memory a TRELLIS.2 sparse decode needs, and how many points fit the memory that is free. Standard library only.

The decode's peak follows the number of points at the level before the last (the 512 grid of a 1024 build), where the
decoder is widest for its point count. The figures are ComfyUI's own estimate for that stage
(`comfy_extras/nodes_trellis2.py`, `_sparse_vae_decode_memory`): 896 values and 27 neighbour numbers per point, plus a
fixed 2 GB of working room. ComfyUI's shape decode feeds that estimate one fixed point count for every subject; here it
is fed the subject's own. UNTESTED ON A CARD: the figures are ComfyUI's, not yet checked against a measured peak.
"""

from __future__ import annotations

GB = 1024 ** 3
FIXED_BYTES = 2 * GB
VALUES_PER_POINT = 896
NEIGHBOURS_PER_POINT = 27  # int32 each
SAFETY_BYTES = GB // 2  # left free on top of ComfyUI's own reserve when sizing windows
# Points at the level before the last for each latent token, used for the shape decode before any of it has run. A
# surface grows four times per doubling and there are three doublings to that level. The decode replaces this guess
# with the real count as soon as it has one.
SHAPE_POINTS_PER_TOKEN = 64


def bytes_per_point(value_bytes: int) -> int:
    return VALUES_PER_POINT * int(value_bytes) + NEIGHBOURS_PER_POINT * 4


def decode_bytes(points: int, value_bytes: int) -> int:
    """Card memory for decoding `points` points in one piece. `value_bytes` is the size of one decoder value (2 for
    the half-precision VAE files)."""
    return FIXED_BYTES + int(points) * bytes_per_point(value_bytes)


def point_budget(free_bytes: int, reserved_bytes: int, value_bytes: int) -> int:
    """Most points one window may hold, from the card memory free now. `reserved_bytes` is what ComfyUI keeps back for
    other programs. 0 when not even an empty decode fits."""
    room = int(free_bytes) - int(reserved_bytes) - SAFETY_BYTES - FIXED_BYTES
    return max(0, room // bytes_per_point(value_bytes))
