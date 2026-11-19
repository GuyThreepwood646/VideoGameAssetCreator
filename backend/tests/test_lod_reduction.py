"""LOD reduction on meshes a collapse decimation cannot shrink by itself: several pieces, and edges that three or more
faces share (a wall meeting a roof, open shells). Before WP-63 the LODs of such a mesh kept their full triangle count.

Blender runs for real (tests/machine_tools.py); the test skips when there is no Blender.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.machine_tools import blender

PACK = Path(__file__).resolve().parents[1] / "vgac" / "blender" / "pack.py"

# Builds, in Blender, a 4 by 4 by 2 lattice of cubes that touch face to face (their coincident faces stay, so every shared
# edge has four faces after the weld), plus a separate icosphere (a second, manifold piece). Exported as a GLB for pack.py.
MAKE_MESH = r'''
import bpy, bmesh, sys
from mathutils import Matrix
out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bm = bmesh.new()
for x in range(4):
    for y in range(4):
        for z in range(2):
            bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((x, y, z)))
bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.4, matrix=Matrix.Translation((6.0, 0.0, 0.0)))
mesh = bpy.data.meshes.new("Multi")
bm.to_mesh(mesh)
bm.free()
obj = bpy.data.objects.new("Multi", mesh)
bpy.context.scene.collection.objects.link(obj)
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
'''


def _blender_or_skip():
    exe = blender()
    if exe is None:
        pytest.skip("Blender is not set up on this machine (VGAC_TEST_BLENDER)")
    return exe


def _tris(report: dict, key: str) -> int:
    return report["lods"][key]


def test_lods_of_a_multi_piece_non_manifold_mesh_are_reduced(tmp_path):
    exe = _blender_or_skip()
    script = tmp_path / "make_mesh.py"
    script.write_text(MAKE_MESH, encoding="utf-8")
    src = tmp_path / "multi.glb"
    made = subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(script), "--", str(src)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert src.is_file(), made.stdout[-800:] + made.stderr[-800:]

    out = tmp_path / "out"
    run = subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(PACK), "--", str(src), str(out), "multi",
                          "--lods", "0.5,0.25", "--formats", "glb", "--collider", "none"],
                         capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    report_path = out / "report.json"
    assert report_path.is_file(), run.stdout[-800:] + run.stderr[-800:]
    report = json.loads(report_path.read_text(encoding="utf-8"))

    lod0 = _tris(report, "LOD0")
    assert lod0 > 300  # the cubes and the sphere came through the weld
    # each level lands near its ratio (the collapse stops a little short of the exact count on a mesh this small)
    assert 0.40 * lod0 <= _tris(report, "LOD1") <= 0.60 * lod0, report["lods"]
    assert 0.18 * lod0 <= _tris(report, "LOD2") <= 0.32 * lod0, report["lods"]
    # the lower level is smaller than the one above it, not the same mesh again
    assert _tris(report, "LOD2") < _tris(report, "LOD1")
