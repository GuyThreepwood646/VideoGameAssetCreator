"""Step 3 geometry (vgac/meshgeom.py, blender/mesh_health.py): the limits per route, the check, the autopilot switch, and a
real Blender run on a whole and a shattered model. The measures are the Nutra Pak and WP-40 calibration values."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests import machine_tools
from vgac import autopilot, fixnotes, guidance, meshgeom

HEALTHY = {"triangles": 29998, "edges": 44994, "open_edges": 0, "bad_join_edges": 3, "folded": {"120": 0.0048},
           "sliver_share": 0.0016, "pieces": 1, "largest_piece_area": 1.0}
NUTRA_V2 = {"triangles": 1195, "edges": 1440, "open_edges": 209, "bad_join_edges": 502, "folded": {"120": 0.0694},
            "sliver_share": 0.0142, "pieces": 1, "largest_piece_area": 1.0}
LEAFY_TREE = {"triangles": 7027, "edges": 9937, "open_edges": 46, "bad_join_edges": 628, "folded": {"120": 0.252},
              "sliver_share": 0.0141, "pieces": 2, "largest_piece_area": 0.9996}
SHRUB = {"triangles": 7813, "edges": 11710, "open_edges": 1, "bad_join_edges": 11, "folded": {"120": 0.0686},
         "sliver_share": 0.0023, "pieces": 2, "largest_piece_area": 0.993}

# Measured by blender/mesh_health.py on stored melted and whole buildings: same folded share, only the level share differs.
MELTED_CLUB = {**HEALTHY, "folded": {"120": 0.01317}, "level_share": 0.41568}
MELTED_STORE = {**HEALTHY, "folded": {"120": 0.01402}, "level_share": 0.33268}
FINE_BUILDINGS = {"club_quarter": 0.70777, "store_front": 0.6868, "store_quarter": 0.74188, "club_front": 0.69682,
                  "cottage": 0.55722, "painted_store": 0.69264, "low_poly_cottage": 0.51043}


def _check(category, report, rebuild=None):
    return meshgeom.check(category, meshgeom.summary(category, report), rebuild)


def test_every_category_with_a_model_has_a_route_and_limits():
    for cat, route in meshgeom.ROUTE_OF.items():
        assert route in meshgeom.LIMITS, cat
    assert meshgeom.route_of("material") is None
    assert _check("material", HEALTHY) is None
    assert meshgeom.check("environment", None) is None


def test_a_whole_model_passes_and_the_shattered_building_fails():
    assert _check("environment", HEALTHY)["passed"] is True
    bad = _check("environment", NUTRA_V2)
    assert bad["passed"] is False and bad["severity"] == "warn"
    assert "folded back" in bad["help"] and "torn seams" in bad["help"]


def test_a_melted_building_with_no_folds_fails_and_fine_ones_pass():
    """The outline score and the folded share both missed the melted Pixal3D buildings; the level share catches them."""
    for report in (MELTED_CLUB, MELTED_STORE):
        bad = _check("environment", report)
        assert bad["passed"] is False and "bent out of shape" in bad["help"]
    for name, level in FINE_BUILDINGS.items():
        assert _check("environment", {**HEALTHY, "level_share": level})["passed"] is True, name
    # A report from before the measure existed is not judged on it; other routes are never judged on it.
    assert _check("environment", HEALTHY)["passed"] is True
    assert _check("humanoid", MELTED_CLUB)["passed"] is True
    # Round scenery has almost no level surface and is not judged on it.
    assert _check("environment", {**HEALTHY, "level_share": 0.09})["passed"] is True


def test_plants_may_have_open_edges_and_pieces_but_not_a_shard_cloud():
    assert _check("foliage", SHRUB)["passed"] is True
    assert _check("foliage", LEAFY_TREE)["passed"] is False
    pieces = {**HEALTHY, "pieces": 40, "largest_piece_area": 0.2}
    assert _check("foliage", pieces)["passed"] is True
    assert _check("humanoid", pieces)["passed"] is False


def test_the_fix_for_scenery_with_the_rebuild_off_turns_it_on_otherwise_builds_again():
    off = _check("environment", NUTRA_V2, rebuild=False)
    assert off["fix"]["params"] == {"seed": None, "remesh": True}
    on = _check("environment", NUTRA_V2, rebuild=True)
    assert on["fix"]["params"] == {"seed": None} and on["fix_kind"] == "rerun"
    assert fixnotes.covered("model_whole", guidance.CHECK_GUIDE["model_whole"]) == "extra"


def test_a_failed_check_never_blocks_an_automatic_build():
    """Decision D23: a torn model rebuilds (tests/test_shape_rebuild.py) and then warns; the autopilot never stops on it."""

    class V:
        stage = "mesh"
        metrics: dict = {}

    v = V()
    v.metrics = {"checks": [_check("environment", NUTRA_V2)]}
    assert autopilot.attention_reason(v, "block") is None
    assert autopilot.attention_reason(v, "warn") is None


BUILD = """
import bpy, bmesh, random, sys
random.seed(3)
out_whole, out_shards = sys.argv[sys.argv.index('--') + 1:]
def export(path):
    bpy.ops.export_scene.gltf(filepath=path)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4)
bpy.context.object.name = 'ball'
export(out_whole)
bpy.ops.wm.read_factory_settings(use_empty=True)
bm = bmesh.new()
for _ in range(300):
    c = [random.uniform(-1, 1) for _ in range(3)]
    vs = [bm.verts.new([c[i] + random.uniform(-.3, .3) for i in range(3)]) for _ in range(3)]
    bm.faces.new(vs)
me = bpy.data.meshes.new('shards'); bm.to_mesh(me)
ob = bpy.data.objects.new('shards', me); bpy.context.scene.collection.objects.link(ob)
export(out_shards)
"""


@pytest.mark.skipif(machine_tools.blender() is None, reason="Blender not found")
def test_blender_measures_a_whole_sphere_and_a_shard_cloud(tmp_path):
    blender = str(machine_tools.blender())
    script = tmp_path / "build.py"
    script.write_text(BUILD)
    whole, shards = tmp_path / "whole.glb", tmp_path / "shards.glb"
    subprocess.run([blender, "-b", "--python", str(script), "--", str(whole), str(shards)], check=True, capture_output=True, timeout=300)
    health = Path(__file__).resolve().parents[1] / "vgac" / "blender" / "mesh_health.py"
    reports = {}
    for name, glb in (("whole", whole), ("shards", shards)):
        out = tmp_path / name
        subprocess.run([blender, "-b", "--python", str(health), "--", str(glb), str(out), "96"], check=True, capture_output=True, timeout=300)
        reports[name] = json.loads((out / "health.json").read_text())
        assert (out / "grey_0.png").is_file()
    assert reports["whole"]["open_edges"] == 0 and reports["whole"]["pieces"] == 1
    assert _check("environment", reports["whole"])["passed"] is True
    assert reports["shards"]["pieces"] > 100 and reports["shards"]["largest_piece_area"] < 0.1
    assert _check("environment", reports["shards"])["passed"] is False
