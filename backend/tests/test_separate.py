"""Separating a limb the 3D build grew into the body (WP-25): blender/separate.py's plain-numpy parts, the backend
wrapper's parameter checks, and (with Blender, VGAC_TEST_BLENDER) a real cut on a box torso with a fused arm."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vgac" / "blender"))
import separate as sp  # noqa: E402

from machine_tools import blender as machine_blender  # noqa: E402
from vgac import separate as wrap  # noqa: E402
from vgac.tools import ToolError  # noqa: E402

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "vgac" / "blender" / "separate.py"
FIXTURE = HERE / "blender_fused_arm.py"
BLENDER = machine_blender() or Path("blender-not-found")
SQUARE = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)


def test_inside_polygon_and_winding():
    pts = np.array([[0.5, 0.5], [1.5, 0.5], [-0.1, 0.2], [0.99, 0.01]])
    assert sp.inside_polygon(pts, SQUARE).tolist() == [True, False, False, True]
    assert sp.signed_area(SQUARE) == pytest.approx(1.0)
    assert sp.signed_area(SQUARE[::-1]) == pytest.approx(-1.0)


def test_segment_distance():
    d = sp.seg_distance(np.array([[0.5, 0.2], [2.0, 0.0]]), SQUARE, np.roll(SQUARE, -1, axis=0))
    assert d.tolist() == pytest.approx([0.2, 1.0])


def test_cap_grid_stays_inside_off_the_edges_and_out_of_holes():
    hole = np.array([[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]])
    G = sp.cap_grid([SQUARE, hole], 0.1)
    assert len(G) > 30
    assert sp.inside_polygon(G, SQUARE).all() and not sp.inside_polygon(G, hole).any()
    edges = np.vstack([SQUARE, hole]), np.vstack([np.roll(SQUARE, -1, axis=0), np.roll(hole, -1, axis=0)])
    assert sp.seg_distance(G, *edges).min() > 0.05
    assert len(sp.cap_grid([SQUARE], 5.0)) == 0  # spacing larger than the outline: no inner points


def test_chain_parameter_runs_shoulder_to_hand():
    chain = [np.array([0, 0, 2.0]), np.array([0, 0, 1.0]), np.array([0, 0, 0.5]), np.array([0, 0, 0.4])]
    t, d = sp.chain_param(np.array([[0.1, 0, 2.0], [0, 0, 1.5], [0.2, 0, 0.75], [0, 0, 0.3], [0, 0, 2.5]]), chain)
    assert t.tolist() == pytest.approx([0.0, 0.5, 1.5, 4.0, -0.5])  # past the hand: in hand lengths
    assert d[2] == pytest.approx(0.2)


def test_arm_share_counts_every_bone_below_the_upper_arm_and_smooths():
    groups = ["mixamorig:Spine", "mixamorig:LeftArm", "mixamorig:Extra_01"]  # an added finger bone under the hand
    arm = {"mixamorig:LeftArm", "mixamorig:Extra_01"}
    a = sp.arm_share([{0: 1.0}, {0: 0.5, 1: 0.5}, {2: 2.0}, {}], groups, arm)
    assert a.tolist() == pytest.approx([0.0, 0.5, 1.0, 0.0])
    nbrs = [np.array([1]), np.array([0, 2]), np.array([1]), np.array([], dtype=int)]
    s = sp.smooth_field(a, nbrs, passes=1)
    assert s[0] == pytest.approx(0.25) and s[3] == 0.0


def test_texel_density_and_raster_cover():
    uv = np.array([[[0, 0], [0.1, 0], [0, 0.1]]])
    xyz = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]])
    assert sp.texel_density(uv, xyz) == pytest.approx(0.1)
    rows, cols, tid, bary = sp.raster_cover(np.array([[[0, 0], [4, 0], [0, 4]]]), 8, 8)
    assert len(rows) == 10 and (tid == 0).all() and np.allclose(bary.sum(axis=1), 1)
    assert rows.max() < 4 and cols.max() < 4


def test_wrapper_checks_parameters_and_inputs(tmp_path):
    assert wrap.check_params("L", 0.004, 0.25) == {"side": "L", "gap": 0.004, "start": 0.25}
    for bad in (("up", 0.006, 0.3), ("both", 0.05, 0.3), ("both", 0.006, 0.9)):
        with pytest.raises(ValueError):
            wrap.check_params(*bad)
    with pytest.raises(ToolError):
        wrap.separate(None, tmp_path / "none.glb", tmp_path / "none_rig.glb", tmp_path / "out.glb")  # type: ignore[arg-type]


def test_summary_names_skipped_cuts():
    rep = {"ok": True, "check": {"open_edges": 0, "bad_edges": 0, "shells": 1, "new_crossings": 0},
           "sides": {"L": {"caps": [{"ok": True}, {"ok": False, "reason": "patch"}]}}, "uv": {"placed": 2}}
    assert wrap.summary(rep) == {"ok": True, "cuts": 1, "skipped": ["patch"], "open_edges": 0, "bad_edges": 0,
                                 "pieces": 1, "new_crossings": 0, "caps_placed": 2}


def _fused(tmp: Path, *extra: str) -> Path:
    subprocess.run([str(BLENDER), "-b", "-t", "2", "--python", str(FIXTURE), "--", str(tmp), *extra], check=True,
                   capture_output=True, timeout=300)
    return tmp


def _separate(tmp: Path, name: str) -> dict:
    report = tmp / f"{name}.json"
    subprocess.run([str(BLENDER), "-b", "-t", "2", "--python", str(SCRIPT), "--", str(tmp / "step4.glb"),
                    str(tmp / "rig.glb"), str(tmp / f"{name}.glb"), "--report", str(report), "--side", "L"],
                   check=True, capture_output=True, timeout=600)
    return json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.skipif(not BLENDER.is_file(), reason="needs Blender (VGAC_TEST_BLENDER)")
def test_a_fused_arm_comes_apart_closed_and_the_same_every_run(tmp_path):
    _fused(tmp_path)
    first = _separate(tmp_path, "a")
    assert first["input"]["check"]["open_edges"] == 0
    ck = first["check"]
    assert first["ok"] and ck["open_edges"] == 0 and ck["bad_edges"] == 0 and ck["shells"] == 1
    assert ck["new_crossings"] == 0
    caps = first["sides"]["L"]["caps"]
    assert len(caps) == 1 and caps[0]["ok"] and caps[0]["cap_triangles"] > 50 and not caps[0]["closed"]
    assert caps[0]["released"]["arm"] > 0 and caps[0]["released"]["body"] > 0
    assert first["uv"]["placed"] == 2  # the arm's cap and the body's cap
    assert (tmp_path / "a.glb").stat().st_size > 10_000
    second = _separate(tmp_path, "b")
    assert second["check"] == ck and second["sides"] == first["sides"]


@pytest.mark.skipif(not BLENDER.is_file(), reason="needs Blender (VGAC_TEST_BLENDER)")
def test_a_cut_plane_lying_in_a_flat_wall_is_refused_and_nothing_breaks(tmp_path):
    _fused(tmp_path, "--wall")
    rep = _separate(tmp_path, "w")
    caps = rep["sides"]["L"]["caps"]
    assert caps and not any(c["ok"] for c in caps)
    assert {c["reason"] for c in caps} <= {"inner_cut_not_closed", "plane_missed", "branching_cut"}
    ck = rep["check"]
    assert rep["ok"] and ck["open_edges"] == 0 and ck["bad_edges"] == 0 and ck["new_crossings"] == 0
