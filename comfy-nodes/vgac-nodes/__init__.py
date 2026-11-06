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

"""Video Game Asset Creator helper nodes.

VGACLoadTrimesh: load a GLB/OBJ/PLY from the ComfyUI input directory as a trimesh.Trimesh (type TRIMESH), with an
optional quadric decimation to a target face count. Exists so native 3D outputs (TRELLIS.2, Hunyuan3D) can feed
nodes that expect TRIMESH, such as SkinToken Rig.
"""
import os

import folder_paths
import trimesh


def _single_mesh(obj):
    if isinstance(obj, trimesh.Scene):
        geoms = [g for g in obj.dump(concatenate=False) if isinstance(g, trimesh.Trimesh)]
        if not geoms:
            raise ValueError("scene has no triangle geometry")
        obj = trimesh.util.concatenate(geoms) if len(geoms) > 1 else geoms[0]
    if not isinstance(obj, trimesh.Trimesh):
        raise ValueError("unsupported mesh object %r" % type(obj))
    return obj


class VGACLoadTrimesh:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path": ("STRING", {"default": "model.glb", "tooltip": "Relative to the ComfyUI input directory, or absolute."}),
                "target_faces": ("INT", {"default": 0, "min": 0, "max": 5000000, "tooltip": "0 keeps the mesh as is."}),
                "merge_vertices": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("TRIMESH", "INT", "INT")
    RETURN_NAMES = ("trimesh", "faces_in", "faces_out")
    FUNCTION = "load"
    CATEGORY = "3D/VGAC"

    def load(self, path, target_faces, merge_vertices):
        full = path if os.path.isabs(path) else os.path.join(folder_paths.get_input_directory(), path)
        mesh = _single_mesh(trimesh.load(full, force="mesh"))
        faces_in = int(len(mesh.faces))
        if merge_vertices:
            mesh.merge_vertices()
        if target_faces and faces_in > target_faces:
            mesh = mesh.simplify_quadric_decimation(face_count=target_faces)
        return (mesh, faces_in, int(len(mesh.faces)))


class VGACSaveTrimesh:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"trimesh": ("TRIMESH",), "filename_prefix": ("STRING", {"default": "3D/vgac"})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("path",)
    FUNCTION = "save"
    CATEGORY = "3D/VGAC"
    OUTPUT_NODE = True

    def save(self, trimesh, filename_prefix):
        folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(filename_prefix, folder_paths.get_output_directory())
        name = f"{filename}_{counter:05}_.glb"
        out = os.path.join(folder, name)
        trimesh.export(out, file_type="glb")
        return {"ui": {"3d": [{"filename": name, "subfolder": subfolder, "type": "output"}]}, "result": (out,)}


NODE_CLASS_MAPPINGS = {"VGACLoadTrimesh": VGACLoadTrimesh, "VGACSaveTrimesh": VGACSaveTrimesh}
NODE_DISPLAY_NAME_MAPPINGS = {"VGACLoadTrimesh": "VGAC Load Trimesh", "VGACSaveTrimesh": "VGAC Save Trimesh"}
