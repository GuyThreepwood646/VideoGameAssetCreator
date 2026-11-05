"""G15 (docs/GREAT-ASSETS-PLAN.md 3.8), pipeline half: scenery rounded to a per-asset grid with its pivot on the
minimum corner, the grid knobs, the grid_fit and tiles_cleanly checks, and the metrics."""

from __future__ import annotations

import json
import subprocess

import pytest

from vgac import guidance, quality, reference
from vgac.blender import gridfit
from vgac.stages import prep as prep_stage

from test_great_assets_laneb import BACKEND, _blender

approx = pytest.approx


# --- the rounding maths ------------------------------------------------------------------


def test_snap_side_rounds_to_whole_steps():
    s = gridfit.snap_side(2.9, 1.0)
    assert s["state"] == "snapped" and s["target"] == 3.0 and s["steps"] == 3 and s["factor"] == approx(3.0 / 2.9)
    assert gridfit.snap_side(2.13, 1.0)["target"] == 2.0
    # Half rounds up (not to even), so 4.5 m becomes 5 m.
    assert gridfit.snap_side(4.5, 1.0)["target"] == 5.0
    # A side under 0.75 of a step is thin and left alone.
    thin = gridfit.snap_side(0.3, 1.0)
    assert thin["state"] == "thin" and thin["target"] == 0.3 and thin["factor"] == 1.0
    assert gridfit.snap_side(0.9, 1.0)["state"] == "snapped" and gridfit.snap_side(0.9, 1.0)["target"] == 1.0
    # From 0.75 up to 0.87 of a step a side is too wide to leave alone and too far from one step to stretch.
    assert gridfit.snap_side(0.8, 1.0)["state"] == "too_far"
    assert gridfit.snap_side(0.74, 1.0)["state"] == "thin"


def test_snap_side_stretches_at_most_15_percent():
    assert gridfit.snap_side(0.87, 1.0)["state"] == "snapped"  # +14.9 percent
    far = gridfit.snap_side(1.45, 1.0)  # nearest is 1 m: -31 percent
    assert far["state"] == "too_far" and far["target"] == 1.45 and far["factor"] == 1.0 and far["wanted"] == 1.0
    assert gridfit.snap_side(1.74, 1.0)["state"] == "snapped" and gridfit.snap_side(1.74, 1.0)["target"] == 2.0
    assert gridfit.snap_side(1.7, 1.0)["state"] == "too_far"
    # A target that asks for more than 15 percent is refused too.
    assert gridfit.snap_side(2.0, 0.5, 3.0)["state"] == "too_far"
    assert gridfit.snap_side(2.8, 0.5, 3.0)["state"] == "snapped"


def test_plan_fit_wall_and_floor():
    wall = gridfit.plan_fit([2.9, 0.3, 3.1], 1)
    assert wall["dims"] == approx([3.0, 0.3, 3.0]) and wall["fit"] and wall["kind"] == "wall"
    assert [s["state"] for s in wall["sides"]] == ["snapped", "thin", "snapped"]
    assert wall["cells"] == [3, 1] and wall["footprint"] == "3x1" and wall["lead"] == 1.0
    assert wall["scale"] == approx([3.0 / 2.9, 1.0, 3.0 / 3.1])
    floor = gridfit.plan_fit([1.9, 2.1, 0.2], 1)
    assert floor["dims"] == approx([2.0, 2.0, 0.2]) and floor["fit"] and floor["kind"] == "floor" and floor["footprint"] == "2x2"
    # Height goes to half steps.
    assert gridfit.plan_fit([2.0, 0.3, 2.7], 1)["dims"][2] == approx(2.5)
    assert gridfit.plan_fit([2.0, 0.3, 2.8], 1)["dims"][2] == approx(3.0)
    # The plan's test piece: 2.13 m comes out at 2.00 m.
    assert gridfit.plan_fit([2.13, 0.25, 3.0], 1)["dims"] == approx([2.0, 0.25, 3.0])


def test_plan_fit_grid_sizes():
    assert gridfit.plan_fit([1.45, 0.3, 3.0], 0.5)["dims"] == approx([1.5, 0.3, 3.0])
    assert gridfit.plan_fit([1.45, 0.3, 3.0], 0.5)["cells"] == [3, 1]
    assert gridfit.plan_fit([3.7, 3.9, 0.3], 4)["dims"] == approx([4.0, 4.0, 0.3])
    # An unknown grid falls back to 1 m.
    assert gridfit.plan_fit([2.9, 0.3, 3.1], 3)["grid_m"] == 1.0 and gridfit.grid_value("0.5") == 0.5 and gridfit.grid_value(None) == 1.0


def test_plan_fit_miss_keeps_the_size():
    miss = gridfit.plan_fit([1.45, 0.3, 3.0], 1)
    assert not miss["fit"] and miss["off_side"] == 0 and miss["dims"] == approx([1.45, 0.3, 3.0]) and miss["scale"][0] == 1.0


def test_plan_fit_chosen_footprint_and_height_scale_evenly_first():
    # 2 x 1 squares on a wall 2.9 wide: the whole piece shrinks evenly to 2 m wide, then the height rounds.
    fp = gridfit.plan_fit([2.9, 0.3, 3.1], 1, "2x1")
    lead = 2.0 / 2.9
    assert fp["lead"] == approx(lead) and fp["dims"][0] == approx(2.0)
    assert fp["dims"][1] == approx(0.3 * lead)  # thin: follows the even scale only
    assert fp["dims"][2] == approx(2.0) and fp["fit"]  # 3.1 * 0.69 = 2.14 -> 2.0
    # The longer count goes on the longer ground side, even when that is the depth.
    deep = gridfit.plan_fit([0.3, 2.9, 3.1], 1, "2x1")
    assert deep["dims"][1] == approx(2.0) and deep["cells"] == [1, 2]
    # A chosen height alone scales evenly to it.
    tall = gridfit.plan_fit([2.2, 0.3, 2.4], 1, "auto", 3)
    assert tall["dims"][2] == approx(3.0) and tall["lead"] == approx(1.25) and tall["dims"][0] == approx(3.0)
    # Footprint and height chosen together: the height is a stretch like any other side, so a far one misses.
    both = gridfit.plan_fit([2.0, 0.3, 2.0], 1, "2x1", 4)
    assert not both["fit"] and both["off_side"] == 2 and both["dims"][2] == approx(2.0)
    # A 2 x 2 footprint on a floor sets both sides.
    fl = gridfit.plan_fit([1.9, 2.1, 0.2], 1, "2x2")
    assert fl["dims"][:2] == approx([2.0, 2.0]) and fl["fit"]
    assert gridfit.height_choice("auto") is None and gridfit.height_choice("0.5") == 0.5


def test_piece_kind_and_judge_fit():
    assert gridfit.piece_kind([2.0, 2.0, 0.2]) == "floor" and gridfit.piece_kind([2.0, 2.0, 0.6]) == "wall"
    assert gridfit.piece_kind([3.0, 0.3, 3.0]) == "wall"
    ok = gridfit.judge_fit([3.0004, 0.3, 2.9995], ["snapped", "thin", "snapped"], 1)
    assert ok["fit"] and ok["off_side"] is None
    off = gridfit.judge_fit([3.003, 0.3, 3.0], ["snapped", "thin", "snapped"], 1)
    assert not off["fit"] and off["off_side"] == 0 and off["off_size"] == approx(3.003)
    far = gridfit.judge_fit([1.45, 0.3, 3.0], ["too_far", "thin", "snapped"], 1)
    assert not far["fit"] and far["off_side"] == 0 and far["errors"][0] == approx(0.45)
    # A chosen height off the half-step grid is judged against its target.
    assert gridfit.judge_fit([4.0, 0.3, 3.0], ["snapped", "thin", "snapped"], 4, [4.0, 0.3, 3.0])["fit"]
    assert gridfit.is_multiple(3.0, 1.0) and gridfit.is_multiple(2.5, 0.5) and not gridfit.is_multiple(2.4, 1.0)
    assert not gridfit.is_multiple(0.0, 1.0)


def test_tile_layout():
    wall = gridfit.tile_layout("wall", [3.0, 0.3, 3.0], ["snapped", "thin", "snapped"], 1)
    assert wall["offsets"] == [[0.0, 0.0], [3.0, 0.0], [6.0, 0.0]] and wall["gap_cm"] == 0.0 and wall["axes"] == [0]
    floor = gridfit.tile_layout("floor", [2.0, 2.0, 0.2], ["snapped", "snapped", "thin"], 1)
    assert len(floor["offsets"]) == 9 and [4.0, -4.0] in floor["offsets"] and floor["gap_cm"] == 0.0
    miss = gridfit.tile_layout("wall", [1.45, 0.3, 3.0], ["too_far", "thin", "snapped"], 1)
    assert miss["pitch"][0] == 1.0 and miss["gap_cm"] == 45.0


# --- checks, metrics and copy ------------------------------------------------------------


def _grid_report(dims, states, grid=1, before=None, targets=None, gap=0.0, kind="wall", footprint="3x1"):
    return {"grid_m": grid, "dims_m": dims, "states": states, "targets_m": targets or dims, "dims_before_m": before or dims,
            "tile_gap_cm": gap, "kind": kind, "footprint": footprint, "cells": [3, 1], "height_m": dims[2], "fit": True,
            "scale": [1, 1, 1]}


def test_grid_checks_pass_with_the_plan_sentences():
    checks = quality.grid_checks(_grid_report([3.0, 0.3, 3.0], ["snapped", "thin", "snapped"]), {})
    by = {c["name"]: c for c in checks}
    assert by["grid_fit"]["passed"] and by["grid_fit"]["help"] == "The piece is 3 x 0.3 x 3 m and lines up with the 1 m grid."
    assert by["tiles_cleanly"]["passed"] and by["tiles_cleanly"]["help"] == "Copies placed side by side meet without gaps."
    assert by["grid_fit"]["severity"] == "warn" and by["tiles_cleanly"]["severity"] == "warn"
    small = quality.grid_checks(_grid_report([0.4, 0.4, 0.5], ["thin", "thin", "snapped"]), {})[0]
    assert small["passed"] and small["help"] == "The piece is 0.4 x 0.4 x 0.5 m, smaller than one 1 m square, so it sits inside one square."


def test_grid_checks_fail_with_a_fix():
    rep = _grid_report([1.45, 0.3, 3.0], ["too_far", "thin", "snapped"], gap=45.0)
    by = {c["name"]: c for c in quality.grid_checks(rep, {"grid_footprint": "auto"})}
    fit = by["grid_fit"]
    assert not fit["passed"] and fit["help"] == "The piece is 1.45 m wide, between grid steps, so it leaves a gap next to other pieces."
    # Everything automatic already: the fix is the next finer grid on which it fits.
    assert fit["fix"] == {"stage": "prep", "params": {"grid_m": 0.5}, "label": "Snap to the grid"}
    assert not by["tiles_cleanly"]["passed"] and by["tiles_cleanly"]["help"] == "Copies placed side by side leave a gap or overlap of 45 cm."
    assert "fix" not in by["tiles_cleanly"]
    # A chosen footprint goes back to automatic (the plan's Fix).
    chosen = quality.grid_checks(rep, {"grid_footprint": "1x1"})[0]
    assert chosen["fix"]["params"] == {"grid_footprint": "auto"}
    # A chosen height that missed goes back to automatic.
    tall = _grid_report([2.0, 0.2, 2.0], ["snapped", "thin", "too_far"])
    hc = quality.grid_checks(tall, {"grid_footprint": "2x1", "grid_height": 4})[0]
    assert hc["help"] == "The piece is 2 m tall, between grid steps, so it leaves a gap next to other pieces."
    assert hc["fix"]["params"] == {"grid_height": "auto"}
    # Nothing finer fits: no fix, "Change something" applies.
    stuck = _grid_report([1.3, 0.3, 3.0], ["too_far", "thin", "snapped"], grid=0.5, before=[1.3, 0.3, 3.0])
    assert "fix" not in quality.grid_checks(stuck, {})[0]


def test_prep_checks_use_the_grid_for_scenery():
    report = {"dims_m": [3.0, 0.3, 3.0], "triangles": 1200, "non_manifold_edges": 0, "boundary_edges": 0, "degenerate_faces": 0,
              "uv_layers": 1, "grid": _grid_report([3.0, 0.3, 3.0], ["snapped", "thin", "snapped"])}
    names = [c["name"] for c in prep_stage.prep_checks(report, {"size_m": 3.0, "grid_m": 1}, {}, "environment", 15000)]
    assert "size_m" not in names and "grid_fit" in names and "tiles_cleanly" in names
    # Without a grid block (a version made before G15) the size check still runs.
    old = {k: v for k, v in report.items() if k != "grid"}
    assert "size_m" in [c["name"] for c in prep_stage.prep_checks(old, {"size_m": 3.0}, {}, "environment", 15000)]
    # Other categories never get the grid checks.
    names = [c["name"] for c in prep_stage.prep_checks(report, {"size_m": 0.6}, {}, "prop_rigid", 5000)]
    assert "grid_fit" not in names and "size_m" in names


def test_grid_metrics():
    rep = _grid_report([3.0, 0.3, 3.0], ["snapped", "thin", "snapped"], before=[2.81, 0.29, 3.0])
    m = prep_stage.grid_metrics(rep)
    assert m["grid_m"] == 1 and m["grid_size_m"] == [3.0, 0.3, 3.0] and m["grid_footprint_cells"] == "3x1"
    assert m["grid_size_before_m"] == [2.81, 0.29, 3.0] and m["tile_gap_cm"] == 0.0 and m["pivot_corner"] and m["grid_kind"] == "wall"


def test_resolve_grid():
    env = reference.category("environment")
    assert prep_stage.resolve_grid(env, {}) == {"grid_m": 1, "grid_footprint": "auto", "grid_height": "auto"}
    assert prep_stage.resolve_grid(env, {"grid_m": 0.5, "grid_footprint": "2x1", "grid_height": 3.0}) == {
        "grid_m": 0.5, "grid_footprint": "2x1", "grid_height": 3}
    assert prep_stage.resolve_grid(env, {"grid_footprint": "9x9", "grid_height": "tall"})["grid_footprint"] == "auto"
    assert prep_stage.resolve_grid(reference.category("prop_rigid"), {"grid_m": 1}) is None


def test_grid_knobs_and_size_knob_by_category():
    prep = {p["name"]: p for p in next(s for s in reference.stages() if s["id"] == "prep")["adjust_schema"]}
    for name in ("grid_footprint", "grid_height", "grid_m"):
        assert prep[name]["categories"] == ["environment"], name
        assert set(prep[name]["option_labels"]) == {str(o) for o in prep[name]["options"]}, name
    assert prep["grid_footprint"]["simple"] and prep["grid_height"]["simple"] and not prep["grid_m"]["simple"]
    assert prep["grid_m"]["default"] == 1 and prep["grid_footprint"]["default"] == "auto" and prep["grid_height"]["default"] == "auto"
    assert prep["grid_footprint"]["label"] == "Size on the grid" and prep["grid_height"]["label"] == "How tall is it?"
    assert prep["grid_m"]["label"] == "Grid size" and prep["grid_m"]["option_labels"]["1"] == "1 m (standard)"
    assert prep["grid_footprint"]["option_labels"]["auto"] == "Automatic (nearest fit)"
    assert prep["grid_height"]["option_labels"]["0.5"] == "Floor (0.5 m)"
    # The grid replaces the size knob and the pivot knob for scenery; the surface knobs stay.
    assert "environment" not in prep["size_m"]["categories"] and "environment" not in prep["size_m"]["by_category"]
    assert "environment" not in prep["pivot"]["categories"]
    for name in ("face_yaw", "symmetric", "delight", "pbr_fix", "rebake"):
        assert "environment" in prep[name]["categories"], name
    v = reference.validate_params("prep", {"grid_m": "1", "grid_footprint": "2x1", "grid_height": "0.5", "size_m": 2.0, "pivot": "base"},
                                  category="environment")
    assert v == {"grid_m": 1, "grid_footprint": "2x1", "grid_height": 0.5}
    assert reference.validate_params("prep", {"grid_m": 1, "size_m": 2.0}, category="humanoid") == {"size_m": 2.0}
    with pytest.raises(ValueError):
        reference.validate_params("prep", {"grid_m": 3}, category="environment")


def test_environment_yaml_and_finish_line():
    env = reference.category("environment")
    assert env["grid_m"] == 1 and env["pivot"] == "corner" and env["size_label"] == "Size on the grid"
    line = guidance.grid_finish_line({"grid_m": 1, "grid_fit": True})
    assert line == {"id": "grid", "text": "Snaps to a 1 m grid, pivot on the corner, ready for baked lighting.", "state": "ok", "term": "grid"}
    assert guidance.grid_finish_line({"grid_m": 0.5, "grid_fit": False})["state"] == "warn"
    assert guidance.grid_finish_line({}) is None


# --- Blender ----------------------------------------------------------------------------


def test_blender_prep_snaps_a_wall_and_puts_the_pivot_on_the_corner(tmp_path):
    exe = _blender()
    if not exe:
        pytest.skip("Blender not installed")
    trimesh = pytest.importorskip("trimesh")
    src = tmp_path / "wall.glb"
    box = trimesh.creation.box(extents=[2.13, 3.0, 0.25])  # glTF: 2.13 wide, 3 tall, 0.25 deep
    box.apply_translation([0.4, 1.0, -0.6])
    box.export(src)
    out = tmp_path / "out.glb"
    tiling = tmp_path / "tiling.png"
    script = BACKEND / "vgac" / "blender" / "prep.py"
    subprocess.run([exe, "-b", "--factory-startup", "--python", str(script), "--", str(src), str(out), "wall",
                    "--size", "3.0", "--size-axis", "height", "--yaw", "0", "--grid", "1", "--tiling", str(tiling)],
                   check=True, capture_output=True, timeout=300)
    report = json.loads((tmp_path / "out_report.json").read_text())
    assert report["pivot"] == "corner"
    x, y, z = report["dims_m"]
    assert x == approx(2.0, abs=0.001) and y == approx(0.25, abs=0.001) and z == approx(3.0, abs=0.001)
    g = report["grid"]
    assert g["fit"] and g["footprint"] == "2x1" and g["kind"] == "wall" and g["tile_gap_cm"] == 0.0
    # glTF bounds: the minimum corner is the origin, within 1 mm.
    lo, hi = trimesh.load(out, force="scene").bounds
    assert list(lo) == approx([0.0, 0.0, 0.0], abs=0.001) and list(hi) == approx([2.0, 3.0, 0.25], abs=0.001)
    assert report.get("tiling_png") and tiling.is_file() and tiling.stat().st_size > 1000
    checks = {c["name"]: c for c in quality.grid_checks(g, {})}
    assert checks["grid_fit"]["passed"] and checks["tiles_cleanly"]["passed"]
