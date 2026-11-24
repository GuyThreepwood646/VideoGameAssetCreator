"""Moving parts through the pack (WP-29, blender/pack.py) and what an asset without them keeps.

Pass mark 9: a rigid asset with no moving parts packs to the same file names and the same report fields as before
WP-29 (the lists below are master's pack.py output on the same box, 2026-11-20; the GLBs were byte-identical too:
bakeoff/wp29/pm9). With a piece under a pivot, the piece stays its own object with its own levels and collider, and
its clip is in the files.

Blender runs for real (tests/machine_tools.py); those tests skip when there is no Blender.
"""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.machine_tools import blender

PACK = Path(__file__).resolve().parents[1] / "vgac" / "blender" / "pack.py"

# A subdivided box with texture coordinates (a crate). PARTS adds what blender/moving_parts.py makes: a pivot node,
# its moving node with a 90 degree open clip in an NLA track, and a lid mesh under it.
MAKE = r'''
import bpy, bmesh, sys, math
argv = sys.argv[sys.argv.index("--") + 1:]
out, parts = argv[0], argv[1] == "parts"
bpy.ops.wm.read_factory_settings(use_empty=True)
def box(name, size, z):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=6, use_grid_fill=True)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bmesh.ops.scale(bm, vec=size, verts=bm.verts[:])
    bmesh.ops.translate(bm, vec=(0, 0, z), verts=bm.verts[:])
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    me.uv_layers.new(name="UVMap")
    o = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(o)
    return o
objs = [box("Crate", (1, 1, 1), 0.0)]
if parts:
    piv = bpy.data.objects.new("lid_1_Pivot", None); bpy.context.scene.collection.objects.link(piv)
    piv.location = (0, 0.5, 0.6)
    mov = bpy.data.objects.new("lid_1", None); bpy.context.scene.collection.objects.link(mov)
    mov.parent = piv
    bpy.context.view_layer.update()
    lid = box("lid_1_Mesh", (1, 1, 0.2), 0.6)
    lid.parent = mov
    lid.matrix_parent_inverse = mov.matrix_world.inverted()
    mov.animation_data_create()
    act = bpy.data.actions.new("lid_open|lid_1")
    mov.animation_data.action = act
    for f, v in ((0, 0.0), (30, math.radians(90))):
        mov.rotation_euler = (0, 0, v)
        mov.keyframe_insert(data_path="rotation_euler", index=2, frame=f)
    mov.animation_data.action = None
    tr = mov.animation_data.nla_tracks.new(); tr.name = "lid_open"
    st = tr.strips.new("lid_open", 0, act)
    if hasattr(st, "action_slot") and len(getattr(act, "slots", [])):
        st.action_slot = act.slots[0]
    mov.rotation_euler = (0, 0, 0)
    objs += [piv, mov, lid]
for o in objs:
    o.select_set(True)
bpy.context.view_layer.objects.active = objs[0]
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True, export_animations=parts,
                          export_animation_mode="NLA_TRACKS", export_force_sampling=True)
'''

# master's pack.py on the plain box (2026-11-20).
MASTER_FILES = ["crate.fbx", "crate.glb", "crate_godot.glb", "crate_unreal.fbx", "lods/crate_LOD1.glb", "lods/crate_LOD2.glb", "report.json"]
MASTER_FIELDS = ["asset", "bones", "boundary_edges", "clips", "collider_count", "collider_mode", "collider_parts",
                 "collider_triangles_max", "collider_triangles_total", "collider_verts", "dims_m", "fbx", "fbx_unreal", "glb",
                 "glb_godot", "glb_lods", "hair_material", "lod_outline", "lod_ratios", "lods", "non_manifold_edges", "normal_map", "shading",
                 "sockets", "unreal_collision", "uv_transfer", "uvs"]
MASTER_LODS = {"LOD0": 588, "LOD1": 294, "LOD2": 146}


def _blender_or_skip():
    exe = blender()
    if exe is None:
        pytest.skip("Blender is not set up on this machine (VGAC_TEST_BLENDER)")
    return exe


def _pack(tmp_path: Path, parts: bool) -> tuple[Path, dict]:
    exe = _blender_or_skip()
    src, out, make = tmp_path / "in.glb", tmp_path / "out", tmp_path / "make.py"
    make.write_text(MAKE, encoding="utf-8")
    subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(make), "--", str(src), "parts" if parts else "plain"],
                   check=True, capture_output=True, timeout=300)
    subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(PACK), "--", str(src), str(out), "crate",
                    "--lods", "0.5,0.25", "--collider", "hull", "--formats", "fbx,glb"], check=True, capture_output=True, timeout=600)
    return out, json.loads((out / "report.json").read_text(encoding="utf-8"))


def _files(out: Path) -> list[str]:
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def _gltf(path: Path) -> dict:
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + n])


def test_no_moving_parts_packs_as_before(tmp_path):
    """Pass mark 9: same file names, same report fields, same triangle counts as master; no parts key, no clips."""
    out, rep = _pack(tmp_path, parts=False)
    assert _files(out) == MASTER_FILES
    assert sorted(rep) == MASTER_FIELDS
    assert rep["lods"] == MASTER_LODS
    assert rep["clips"] == [] and "parts" not in rep and "default_pose" not in rep and "fbx_clip_frames" not in rep


def test_a_piece_under_a_pivot_stays_its_own_object_with_its_clip(tmp_path):
    out, rep = _pack(tmp_path, parts=True)
    assert _files(out) == MASTER_FILES  # the same files: the piece rides inside them
    assert rep["clips"] == ["lid_open"] and rep["default_pose"] == "rest"
    assert rep["fbx_clip_frames"] == {"lid_open": [0, 30]}  # the FBX's one take, cut into clips by the Unity importer
    (part,) = rep["parts"]
    assert part["node"] == "lid_1" and part["pivot"] == "lid_1_Pivot"
    assert part["lods"]["LOD1"] < part["lods"]["LOD0"] and part["boundary_edges"] == 0
    assert rep["lods"] == MASTER_LODS  # the body's own levels are as without the lid
    names = {c["name"]: c["kind"] for c in rep["collider_parts"]}
    assert names.get("crate_lid_1_collider") == "part"
    assert rep["unreal_collision"] == ["UCX_crate_00"]  # a moving hull is not static collision
    doc = _gltf(out / "crate.glb")
    nodes = {n["name"]: n for n in doc["nodes"]}
    assert {"lid_1_Pivot", "lid_1", "crate_lid_1", "crate"} <= set(nodes)
    lid = [i for i, n in enumerate(doc["nodes"]) if n["name"] == "crate_lid_1"][0]
    mover = [i for i, n in enumerate(doc["nodes"]) if n["name"] == "lid_1"][0]
    assert lid in nodes["lid_1"].get("children", []) and mover in nodes["lid_1_Pivot"].get("children", [])
    assert [a["name"] for a in doc.get("animations", [])] == ["lid_open"]
    assert "rotation" not in nodes["lid_1"] or nodes["lid_1"]["rotation"] in ([0, 0, 0, 1], [0.0, 0.0, 0.0, 1.0])  # rests shut
    lod1 = {n["name"] for n in _gltf(out / "lods" / "crate_LOD1.glb")["nodes"]}
    assert {"crate_LOD1", "crate_lid_1_LOD1", "lid_1", "lid_1_Pivot"} <= lod1


def test_the_manifest_gets_moving_parts_only_with_pieces(tmp_path):
    from vgac.stages import export

    sc = SimpleNamespace(file=lambda key: None)
    assert export._moving_manifest(sc, {"lods": {}}) is None
    assert export._moving_manifest(sc, None) is None
    clips = tmp_path / "anim_clips.json"
    clips.write_text(json.dumps({"parts": [{"name": "lid", "kind": "hinge", "node": "lid_1", "pivot": [0, 1, 0], "axis": [1, 0, 0],
                                            "amount": 90.0, "unit": "degrees", "sign": -1.0, "clips": ["lid_open", "lid_close"]}]}))
    sc = SimpleNamespace(file=lambda key: clips if key == "anim_clips" else None)
    (row,) = export._moving_manifest(sc, {"parts": [{"node": "lid_1", "lods": {"LOD0": 10}}]})
    assert row["pivot_node"] == "lid_1_Pivot" and row["clips"] == ["lid_open", "lid_close"] and row["triangles_per_lod"] == {"LOD0": 10}


def test_the_rig_step_leaves_an_asset_without_moving_parts_alone(monkeypatch):
    """No hinge, spin or slide part declared or placed: no param, no metric, no file, no check."""
    import asyncio

    from vgac.stages import rig

    calls = []
    sc = SimpleNamespace(asset=SimpleNamespace(parts=[{"name": "body", "kind": "separate"}]), file=lambda key: None, parent=None,
                         set_params=lambda **kw: calls.append(kw), set_metrics=lambda **kw: calls.append(kw),
                         register=lambda *a: calls.append(a))
    monkeypatch.setattr(rig, "stiff_parts_path", lambda sc_: None)
    assert asyncio.run(rig._cut_moving_parts(sc, Path("x.glb"))) is None
    assert calls == []


def test_a_moving_parts_own_hull_does_not_count_against_the_platform_cap():
    from vgac.stages import export

    parts = [{"name": f"c{i}", "kind": "hull", "triangles": 40, "vertices": 20} for i in range(6)]
    parts += [{"name": f"w{i}", "kind": "part", "triangles": 40, "vertices": 20} for i in range(4)]
    checks = {c["id"]: c for c in export.collider_checks({"collider_parts": parts}, {"cap": 6}, "vehicle")}
    c = checks["collider_count"]
    assert c["ok"] and c["value"] == 6 and c["vars"]["moving"] == 4 and c["vars"]["vertices"] == 200
    assert "its own shape that moves with it (4 in all)" in c["help"]
    plain = {c["id"]: c for c in export.collider_checks({"collider_parts": parts[:6]}, {"cap": 6}, "vehicle")}["collider_count"]
    assert "moving" not in plain["vars"] and "moves with it" not in plain["help"]
