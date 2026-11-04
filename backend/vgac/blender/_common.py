"""Helpers shared by the headless Blender scripts in this folder.

Each script loads it with:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

Not a Blender add-on and not imported by the backend: it runs inside Blender's Python only.
"""
import sys

import bpy


def import_glb(path):
    """glTF import without the bone display-shape helper object the importer otherwise adds for skins."""
    try:
        bpy.ops.import_scene.gltf(filepath=path, bone_shape_type="NONE")
    except TypeError:
        bpy.ops.import_scene.gltf(filepath=path)
    for o in [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.vertex_groups and o.name.lower().startswith("icosphere")]:
        bpy.data.objects.remove(o, do_unlink=True)


def open_glb(path):
    """Start from an empty factory scene and import `path` into it."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_glb(path)


def after_double_dash():
    """The script's own arguments: everything after `--` on the Blender command line (ValueError when absent)."""
    return sys.argv[sys.argv.index("--") + 1:]


def opt(flag, default):
    """Value following `flag` in the script arguments, else `default`."""
    argv = after_double_dash()
    return argv[argv.index(flag) + 1] if flag in argv else default


def flag(name):
    """True when the bare switch `name` (for example --pixel) is in the script arguments."""
    return name in after_double_dash()

