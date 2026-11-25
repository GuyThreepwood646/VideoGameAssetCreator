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

"""VGACThinMesh: a smaller copy of a decoded mesh, for the texture bake's surface search.

BakeTextureFromVoxel back-projects each texel onto a reference mesh, and the memory it needs follows that mesh's size. The
decoded mesh of a large subject is tens of millions of faces; this node thins it by vertex clustering (the function
RemeshMesh itself uses on a mesh above its vertex cap, which is cheap in memory where the quadric decimation of
DecimateMesh is not). A mesh already under the target passes through unchanged.
"""

from __future__ import annotations

import logging

import torch

from . import trellis_internals as comfy_side


class VGACThinMesh:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"mesh": ("MESH",),
                             "max_vertices": ("INT", {"default": 4_000_000, "min": 100_000, "max": 100_000_000,
                                                      "tooltip": "Cluster the mesh to about this many vertices; a smaller mesh passes through."})}}

    RETURN_TYPES = ("MESH",)
    RETURN_NAMES = ("mesh",)
    FUNCTION = "thin"
    CATEGORY = "3D/VGAC"

    def thin(self, mesh, max_vertices):
        count = len(mesh.vertices) if isinstance(mesh.vertices, list) else int(mesh.vertices.shape[0])
        fetch, cluster, device = comfy_side.mesh_item(), comfy_side.cluster_decimate(), comfy_side.torch_device()
        vertex_list, face_list = [], []
        for i in range(count):
            vertices, faces = fetch(mesh, i)[:2]
            if vertices.shape[0] > max_vertices:
                before = int(faces.shape[0])
                new_v, new_f, _ = cluster(vertices.to(device).float(), faces.to(device).to(torch.int64), target_verts=int(max_vertices))
                vertices, faces = new_v.cpu(), new_f.int().cpu()
                logging.info("VGAC thin mesh: %d faces to %d", before, int(faces.shape[0]))
                comfy_side.empty_cache()
            vertex_list.append(vertices)
            face_list.append(faces)
        if all(v.shape == vertex_list[0].shape for v in vertex_list) and all(f.shape == face_list[0].shape for f in face_list):
            return (comfy_side.mesh_type()(vertices=torch.stack(vertex_list), faces=torch.stack(face_list)),)
        return (comfy_side.pack_meshes(vertex_list, face_list),)


NODE_CLASS_MAPPINGS = {"VGACThinMesh": VGACThinMesh}
NODE_DISPLAY_NAME_MAPPINGS = {"VGACThinMesh": "VGAC Thin Mesh"}
