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


def test_the_raise_turns_arm_points_by_their_share_and_counts_stretch_near_the_cut():
    P = np.array([[1.0, 0, 0], [1.0, 0, -1.0], [0.5, 0, -1.0]])
    Q = sp.raise_arm(P, np.array([0.0, 1.0, 0.5]), np.zeros(3), (0, -1, 0))
    assert Q[0].tolist() == pytest.approx([1, 0, 0])  # a body point stays
    assert Q[1].tolist() == pytest.approx([1, 0, 1])  # an arm point hanging down swings out and up
    assert Q[2].tolist() == pytest.approx([0.75, 0, -0.25])  # half arm: half way between still and turned
    edges = [[0, 1], [1, 2]]
    share = np.array([0.0, 1.0, 0.0])  # edge 1-2 joins an arm point to a body point 0.5 m away: 4 times longer raised
    assert sp.raise_stretch(P, edges, share, np.zeros(3), (0, -1, 0), [False, False, True]) == (1, 1)
    assert sp.raise_stretch(P, edges, share, np.zeros(3), (0, -1, 0), [True, False, False]) == (1, 0)
    assert sp.raise_stretch(P, edges, np.ones(3), np.zeros(3), (0, -1, 0), [True, True, True]) == (2, 0)  # rigid


def test_messages_say_what_was_separated_what_was_left_and_why():
    sep = {"changed": True, "ok": True, "sides": {
        "L": {"caps": [{"ok": True}], "stretch": {"near_edges_after": 1000, "stretched_after": 69}},
        "R": {"caps": [{"ok": True}], "stretch": {"near_edges_after": 1000, "stretched_after": 10}}}}
    assert wrap.messages(sep) == [
        "The left arm is separate, but parts near it still move with the arm; fixing their weights comes next.",
        "The right arm is separate."]
    left = {"changed": False, "ok": False, "sides": {
        "L": {"caps": [{"ok": False, "reason": "not_upper_arm"}]},
        "R": {"caps": [{"ok": False, "reason": "stretch_rose"}]}}}
    assert wrap.messages(left) == [
        "The left arm was not changed: its skin weights start on the forearm, so it is not an upper arm grown into the "
        "body.", "The right arm was not changed: cutting it made raising the arm stretch the model more, not less."]
    assert wrap.messages({"reason": "worse", "sides": {"L": {"caps": [{"ok": True}]}}}) == [
        "The model was not changed: the cut left it less whole than before."]


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
    assert wrap.summary(rep) == {"ok": True, "changed": False, "cuts": 1, "skipped": ["patch"], "open_edges": 0,
                                 "bad_edges": 0, "pieces": 1, "new_crossings": 0, "caps_placed": 2}


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
    st = first["sides"]["L"]["stretch"]  # raising the arm 90 degrees stretches less near the cut than before
    assert first["changed"] and st["near_edges_after"] > 0 and st["stretched_after"] <= st["stretched_before"]
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
    assert rep["changed"] is False and rep["reason"] == "nothing_cut" and not (tmp_path / "w.glb").exists()


@pytest.mark.skipif(not BLENDER.is_file(), reason="needs Blender (VGAC_TEST_BLENDER)")
def test_arm_weights_starting_on_the_forearm_are_refused_and_the_model_is_left_alone(tmp_path):
    _fused(tmp_path, "--forearm")  # the low-poly dwarf's case: the weight line is at the elbow, not the upper arm
    rep = _separate(tmp_path, "f")
    caps = rep["sides"]["L"]["caps"]
    assert caps and {c["reason"] for c in caps} == {"not_upper_arm"} and min(c["t_min"] for c in caps) > 0.65
    assert rep["changed"] is False and not (tmp_path / "f.glb").exists()
    assert wrap.messages(rep) == ["The left arm was not changed: its skin weights start on the forearm, so it is not "
                                  "an upper arm grown into the body."]


# --- WP-27: labels by face corners, weights derived again, cap colour ----------------------------------------------


def test_corner_map_names_the_rig_point_even_where_a_later_pass_moved_it():
    s_pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0.0]])
    r_pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0.3], [1, 0, 0]])  # point 3 moved; a seam copy of 1
    s_c = [0, 1, 2, 1, 3, 2]
    r_c = [0, 4, 2, 1, 3, 2]
    rig_of, off = sp.corner_map(s_c, s_pos, r_c, r_pos)
    assert rig_of[3] == 3 and off[3].tolist() == pytest.approx([0, 0, 0.3])
    assert (np.abs(off[:3]) < 1e-12).all()
    assert sp.corner_map(s_c[:3], s_pos, r_c, r_pos) is None  # different face corners
    assert sp.corner_map(s_c, s_pos, r_c, r_pos + 1.0) is None  # nothing lines up


def test_flood_keeps_to_allowed_points():
    nbrs = [[1], [0, 2], [1, 3], [2]]
    assert sp.flood([0], nbrs, np.array([True, True, False, True])).tolist() == [True, True, False, False]
    assert not sp.flood([2], nbrs, np.array([True, True, False, True])).any()


def test_keep_side_drops_the_other_sides_bones_past_the_ramp():
    is_arm = np.array([False, True])  # group 0 the spine, group 1 the arm
    W = [{0: 0.5, 1: 0.5}, {0: 0.5, 1: 0.5}, {0: 0.5, 1: 0.5}, {1: 1.0}, {0: 0.5, 1: 0.5}]
    in_arm = np.array([True, False, True, False, False])
    t = np.array([1.0, 1.0, 0.3 + sp.RELEASE_RAMP / 2, 1.0, 0.2])
    out, changed, need = sp.keep_side(W, is_arm, in_arm, t, 0.3)
    assert out[0] == {1: pytest.approx(1.0)}  # on the arm: only the arm
    assert out[1] == {0: pytest.approx(1.0)}  # off the arm (hair, robe): no arm weight
    assert out[2] == {0: pytest.approx(0.25), 1: pytest.approx(0.75)}  # half way up the ramp: half the change
    assert need == {3} and 3 not in changed  # nothing of its side left: filled from its neighbours
    assert out[4] == W[4] and 4 not in changed  # the shoulder (before `start`) keeps its blend


def test_fill_and_smooth_never_cross_the_cut():
    nbrs = [[1], [0, 2], [1, 3], [2]]
    cls = np.array([True, True, False, False])  # points 0-1 the arm, 2-3 the body
    W, left = sp.fill_from_neighbours([{1: 1.0}, {}, {}, {0: 1.0}], {1, 2}, nbrs, cls)
    assert left == 0 and W[1] == {1: pytest.approx(1.0)} and W[2] == {0: pytest.approx(1.0)}
    S, held = sp.smooth_weights([{1: 1.0}, {1: 0.6, 2: 0.4}, {0: 1.0}, {0: 1.0}], [0, 1, 2], nbrs, cls, passes=3)
    assert set(S[0]) | set(S[1]) == {1, 2} and set(S[2]) == {0}  # no body bone on the arm side, nor arm on the body
    assert held == 0
    # the volume limit: a point that would move further than the limit keeps its weights
    S2, held2 = sp.smooth_weights([{1: 1.0}, {2: 1.0}, {0: 1.0}, {0: 1.0}], [1], nbrs, cls, passes=4, limit=0.1)
    assert held2 == 1 and S2[1] == {2: 1.0}


def test_top_influences_keep_the_largest_four_summing_to_one():
    d = sp.top_influences({0: 0.4, 1: 0.3, 2: 0.2, 3: 0.08, 4: 0.02})
    assert set(d) == {0, 1, 2, 3} and sum(d.values()) == pytest.approx(1.0)
    assert sp.top_influences({}) == {}


def test_barycentric_and_closest_on_segments():
    a, b, c = np.array([0, 0, 0.0]), np.array([1, 0, 0.0]), np.array([0, 1, 0.0])
    assert sp.barycentric([0.25, 0.25, 0.5], a, b, c).tolist() == pytest.approx([0.5, 0.25, 0.25])
    P = np.array([[0.5, 1.0, 0], [2.0, 0.0, 0]])
    C = sp.closest_on_segments(P, np.array([[0, 0, 0.0]]), np.array([[1, 0, 0.0]]))
    assert np.allclose(C, [[0.5, 0, 0], [1, 0, 0]])


def test_cap_colour_reads_only_covered_texels_and_follows_the_ring():
    px = np.zeros((8, 8, 3), np.float32)
    px[:, :4] = (1.0, 0.0, 0.0)  # a red island on the left half; the right half is empty atlas (black)
    covered = np.zeros((8, 8), bool)
    covered[:, :4] = True
    assert sp.sample_covered(px, covered, (0.49, 0.5)).tolist() == pytest.approx([1, 0, 0])  # at the island's border
    got = sp.sample_many(px, covered, np.array([[0.1, 0.1], [0.55, 0.5]]))
    assert np.allclose(got, [[1, 0, 0], [1, 0, 0]])
    ring = np.array([[0, 0, 0.0], [1, 0, 0.0]])
    col = np.array([[1.0, 0, 0], [0, 0, 1.0]])
    out = sp.idw_colour(np.array([[0, 0, 0.0], [0.5, 0, 0], [1, 0, 0]]), ring, col)
    assert out[0].tolist() == pytest.approx([1, 0, 0], abs=1e-6) and out[2].tolist() == pytest.approx([0, 0, 1], abs=1e-6)
    assert out[1].tolist() == pytest.approx([0.5, 0, 0.5])  # half way: half of each, not one flat average of a patch


def test_dilate_fill_grows_the_cap_colour_by_its_padding_only():
    px = np.zeros((9, 9, 3), np.float32)
    mask = np.zeros((9, 9), bool)
    mask[4, 4] = True
    px[4, 4] = (0.2, 0.4, 0.6)
    grown = sp.dilate_fill(px, mask, 2)
    assert grown.sum() == 25 and (px[2:7, 2:7] == px[4, 4]).all()
    assert (px[0] == 0).all() and (px[:, 8] == 0).all()


def test_wrapper_writes_the_cap_list_and_drops_it_with_a_refused_model(tmp_path, monkeypatch):
    for f in ("step4.glb", "rig.glb"):
        (tmp_path / f).write_bytes(b"x")
    island = {"side": "L", "of": "arm", "triangles": 1, "uv_tris": [[[0, 0], [1, 0], [0, 1]]], "ring_colour_diff": 0.5}
    state = {"ok": True}

    def fake_blender(settings, script, args, log_path=None, timeout=None):
        Path(args[2]).write_bytes(b"glb")
        report = {"changed": True, "ok": state["ok"], "sides": {"L": {"caps": [{"ok": True}]}},
                  "uv": {"placed": 1, "cap_islands": [dict(island)]}}
        Path(args[args.index("--report") + 1]).write_text(json.dumps(report), encoding="utf-8")

    monkeypatch.setattr(wrap, "run_blender", fake_blender)
    out = tmp_path / "o" / "out.glb"
    rep = wrap.separate(None, tmp_path / "step4.glb", tmp_path / "rig.glb", out)  # type: ignore[arg-type]
    caps = json.loads((out.parent / wrap.CAPS_FILE).read_text(encoding="utf-8"))
    assert caps["format"] == wrap.CAPS_FORMAT and caps["caps"][0]["uv_tris"] == island["uv_tris"]
    assert "uv_tris" not in rep["uv"]["cap_islands"][0] and rep["uv"]["cap_islands"][0]["ring_colour_diff"] == 0.5
    state["ok"] = False
    rep = wrap.separate(None, tmp_path / "step4.glb", tmp_path / "rig.glb", out)  # type: ignore[arg-type]
    assert rep["changed"] is False and not (out.parent / wrap.CAPS_FILE).exists() and not out.exists()


@pytest.mark.skipif(not BLENDER.is_file(), reason="needs Blender (VGAC_TEST_BLENDER)")
def test_weights_off_the_arm_are_taken_off_and_the_rigged_copy_sits_in_the_rig_rest_pose(tmp_path):
    _fused(tmp_path, "--palm", "--stray")
    report = tmp_path / "s.json"
    subprocess.run([str(BLENDER), "-b", "-t", "2", "--python", str(SCRIPT), "--", str(tmp_path / "step4.glb"),
                    str(tmp_path / "rig.glb"), str(tmp_path / "s.glb"), "--report", str(report), "--side", "L",
                    "--rigged-out", str(tmp_path / "s_rig.glb")], check=True, capture_output=True, timeout=600)
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["changed"] and rep["ok"]
    assert rep["input"]["labels"] == "corners" and rep["input"]["rest_moved_points"] > 0
    assert rep["rest_offset_points"] > 0  # the rigged copy's hand points moved into the rig's rest pose
    w = rep["sides"]["L"]["weights"]
    assert w["arm_surface_points"] > 0 and "reason" not in w
    assert w["arm_weight_off_arm_before"] > 0 and w["arm_weight_off_arm_after"] == 0
    st = rep["sides"]["L"]["stretch"]
    assert st["stretched_all_after"] <= st["stretched_all_before"]
    isl = rep["uv"]["cap_islands"]
    assert len(isl) == 2 and {i["of"] for i in isl} == {"arm", "body"}
    assert all(i["ring_colour_diff"] < 2.0 for i in isl)  # the fixture's surface is one colour
    import trimesh

    rig_pts = trimesh.load(tmp_path / "rig.glb", force="scene").dump()[0].vertices
    out_pts = trimesh.load(tmp_path / "s_rig.glb", force="scene").dump()[0].vertices

    def hand(P):  # well below the cut, at the hand
        return P[(P[:, 0] > 0.15) & (P[:, 1] < 1.06)]  # glTF is y up: y is height

    assert len(hand(out_pts)) and hand(out_pts)[:, 2].mean() == pytest.approx(hand(rig_pts)[:, 2].mean(), abs=0.005)
