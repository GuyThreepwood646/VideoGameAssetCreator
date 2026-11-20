"""WP-75 part B: blender/cut_check.py's plain-numpy parts (does a cut make raising the arm worse?)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vgac" / "blender"))
import cut_check as cc  # noqa: E402


def test_stretch_share_counts_only_edges_inside_the_region_and_over_the_ratio():
    rest = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0.0]])
    posed = np.array([[0, 0, 0], [1.5, 0, 0], [2.5, 0, 0], [4, 0, 0.0]])  # edge 0-1 x1.5, 1-2 x1.0, 2-3 x1.5
    E = [[0, 1], [1, 2], [2, 3]]
    assert cc.stretch_share(rest, posed, E, [True, True, True, False]) == pytest.approx(0.5)
    assert cc.stretch_share(rest, posed, E, [True, True, True, True]) == pytest.approx(2 / 3)
    assert cc.stretch_share(rest, posed, E, [False] * 4) == 0.0


def test_regions_keep_to_the_own_side_and_their_bands():
    W = 0.4
    joint, mid = np.array([0.2, 0, 1.4]), np.array([0, 0, 1.4])
    lat, fwd = np.array([1.0, 0, 0]), np.array([0, -1.0, 0])
    co = np.array([[0.25, 0, 1.45], [0.15, 0, 1.0], [-0.25, 0, 1.45], [0.2, 0, 1.1], [0.2, 0, 0.8]])
    r = cc.regions(co, joint, mid, lat, fwd, W, 1)
    assert r["shoulder"].tolist() == [True, False, False, False, False]
    assert r["side"].tolist() == [False, True, False, True, False]  # the last is 1.5 widths down
    assert r["pit"].tolist() == [True, False, False, False, False]  # the second is 0.76 widths from the armpit
    assert r["under"].tolist() == [False, True, False, True, True]  # 1.5 widths down is still under the arm


def test_torn_area_is_the_posed_area_of_faces_pulled_past_the_ratio_in_shoulder_widths():
    rest = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 0, 0], [6, 0, 0], [5, 1, 0.0]])
    posed = rest.copy()
    posed[2] = [0, 2, 0]  # the first triangle pulled to twice its height: area 1
    T = [[0, 1, 2], [3, 4, 5]]
    assert cc.torn_area(rest, posed, T, [True] * 6, W=1.0) == pytest.approx(1.0)
    assert cc.torn_area(rest, posed, T, [True] * 6, W=2.0) == pytest.approx(0.25)
    assert cc.torn_area(rest, posed, T, [False, True, True, True, True, True], W=1.0) == 0.0
    assert cc.torn_area(rest, rest, T, [True] * 6, W=1.0) == 0.0


def test_worse_sides_names_what_the_cut_made_worse_within_the_tolerances():
    before = {"Left": {"shoulder": 0.05, "side": 0.30, "crossings": 2}, "Right": {"shoulder": 0.05, "side": 0.30, "crossings": 2}}
    after = {"Left": {"shoulder": 0.06, "side": 0.04, "crossings": 10}, "Right": {"shoulder": 0.051, "side": 0.04, "crossings": 7}}
    assert cc.worse_sides(before, after) == {"Left": ["shoulder", "crossings"]}
    assert cc.worse_sides(before, {"Left": after["Left"]}) == {"Left": ["shoulder", "crossings"]}
    assert cc.worse_sides(before, before) == {}
    flap = {"Left": {"shoulder": 0.05, "side": 0.04, "crossings": 2, "flap": 0.44}}
    assert cc.worse_sides({"Left": dict(before["Left"], flap=0.20)}, flap) == {"Left": ["flap"]}
    assert cc.worse_sides({"Left": dict(before["Left"], flap=0.40)}, flap) == {}


HERE = Path(__file__).resolve().parent


def _blender():
    from machine_tools import blender as machine_blender

    return machine_blender() or Path("blender-not-found")


@pytest.mark.skipif(not _blender().is_file(), reason="needs Blender (VGAC_TEST_BLENDER)")
def test_the_check_measures_both_rigs_and_judges_them_by_worse_sides(tmp_path):
    import json
    import subprocess

    B = str(_blender())
    subprocess.run([B, "-b", "-t", "2", "--python", str(HERE / "blender_fused_arm.py"), "--", str(tmp_path)], check=True,
                   capture_output=True, timeout=300)
    subprocess.run([B, "-b", "-t", "2", "--python", str(HERE.parent / "vgac" / "blender" / "separate.py"), "--",
                    str(tmp_path / "step4.glb"), str(tmp_path / "rig.glb"), str(tmp_path / "s.glb"), "--report",
                    str(tmp_path / "s.json"), "--side", "L", "--rigged-out", str(tmp_path / "s_rig.glb")],
                   check=True, capture_output=True, timeout=600)
    report = tmp_path / "check.json"
    subprocess.run([B, "-b", "-t", "2", "--python", str(HERE.parent / "vgac" / "blender" / "cut_check.py"), "--",
                    str(tmp_path / "rig.glb"), str(tmp_path / "s_rig.glb"), "--report", str(report)],
                   check=True, capture_output=True, timeout=600)
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["ok"] and rep["raise_deg"] == [90.0, 120.0]
    for k in ("before", "after"):
        assert set(rep[k]["sides"]) == {"Left", "Right"}
        assert all(set(v) == {"shoulder", "side", "crossings", "flap"} for v in rep[k]["sides"].values())
    assert rep["before"]["sides"]["Left"]["side"] > 0  # the fused arm stretches the side when raised
    assert rep["worse"] == cc.worse_sides(rep["before"]["sides"], rep["after"]["sides"])
