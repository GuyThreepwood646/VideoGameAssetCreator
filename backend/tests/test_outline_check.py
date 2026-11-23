"""WP-16 follow-up: the outline check (vgac/blender/outline.py). The pure helpers run on stand-in meshes made of boxes; the
planted-defect proof runs the same measures inside Blender on small models (skipped when Blender is missing)."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from vgac.blender import outline, shapeloss
from vgac.stages import prep

from test_great_assets_laneb import BACKEND, _blender


class Boxes:
    """A stand-in for a BVH tree: axis-aligned boxes (lo, hi) that a ray can hit (slab test)."""

    def __init__(self, boxes):
        self.boxes = [(np.array(lo, float), np.array(hi, float)) for lo, hi in boxes]

    def vertices(self):
        return np.array([c for lo, hi in self.boxes for c in (lo, hi)])

    def ray_cast(self, origin, direction):
        o, d = np.array(origin), np.array(direction)
        for lo, hi in self.boxes:
            t0, t1 = -1e18, 1e18
            for k in range(3):
                if abs(d[k]) < 1e-12:
                    if o[k] < lo[k] or o[k] > hi[k]:
                        t0, t1 = 1, 0
                        break
                else:
                    a, b = (lo[k] - o[k]) / d[k], (hi[k] - o[k]) / d[k]
                    t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
            if t0 <= t1 and t1 > 0:
                return (tuple(o + d * max(t0, 0)), None, 0, max(t0, 0))
        return (None, None, None, None)


def _measure(pre, fin):
    return outline.measure(pre, fin, pre.vertices(), fin.vertices(), resolution=96)


SWORD = [((-0.15, -0.03, 0.0), (0.15, 0.03, 0.04)),  # guard, as wide as the blade is not
         ((-0.06, -0.015, 0.04), (0.06, 0.015, 1.0))]  # blade


def test_the_same_model_has_a_perfect_outline():
    m = _measure(Boxes(SWORD), Boxes(SWORD))
    assert m["iou_min"] == 1.0 and m["region_max"] == 0.0
    assert set(m["views"]) == set(outline.VIEW_NAMES) and len(m["views"]) == 7


def test_a_blade_squeezed_to_a_sliver_is_a_lost_region():
    thin = [SWORD[0], ((-0.006, -0.006, 0.04), (0.006, 0.006, 1.0))]
    m = _measure(Boxes(SWORD), Boxes(thin))
    assert m["iou_min"] < 0.5
    assert m["region_max"] > 0.2 and m["region_max_kind"] == "lost"


def test_a_closed_hole_is_an_added_region():
    ring = [((0, -0.05, 0), (1, 0.05, 0.2)), ((0, -0.05, 0.8), (1, 0.05, 1.0)),
            ((0, -0.05, 0.2), (0.2, 0.05, 0.8)), ((0.8, -0.05, 0.2), (1, 0.05, 0.8))]
    plugged = ring + [((0.2, -0.05, 0.2), (0.8, 0.05, 0.8))]
    m = _measure(Boxes(ring), Boxes(plugged))
    assert m["region_max_kind"] == "added" and m["region_max"] > 0.2
    assert m["views"]["side 90"]["iou"] < 0.7  # looking along the plate's thin side: the plate is a solid wall either way


def test_a_small_shift_of_the_edge_is_not_a_region():
    wide = [((-0.5, -0.5, 0), (0.5, 0.5, 1.0))]
    tweaked = [((-0.495, -0.495, 0), (0.495, 0.495, 1.0))]  # under a pixel at this resolution
    m = _measure(Boxes(wide), Boxes(tweaked))
    assert m["region_max"] == 0.0 and m["iou_min"] > 0.97


def test_dilate_and_largest_region():
    m = np.zeros((9, 9), bool)
    m[4, 4] = True
    assert outline.dilate(m, 1).sum() == 9 and outline.dilate(m, 2).sum() == 25
    two = np.zeros((9, 9), bool)
    two[0, 0:2] = True
    two[4:7, 4:7] = True
    assert outline.largest_region(two) == 9
    assert outline.largest_region(np.zeros((3, 3), bool)) == 0


def test_views_cover_half_a_turn_and_the_top():
    names = [v[0] for v in outline.view_axes()]
    assert names == list(outline.VIEW_NAMES)
    for _, d, u, v in outline.view_axes():
        assert abs(np.dot(d, u)) < 1e-9 and abs(np.dot(d, v)) < 1e-9 and abs(np.dot(u, v)) < 1e-9


def test_the_outline_check_passes_a_clean_run_fails_a_lost_part_and_says_when_it_could_not_measure():
    ok = {"outline": {"iou_min": 0.99, "iou_min_view": "side 0", "region_max": 0.0, "region_max_kind": "lost", "region_max_view": "side 0"}}
    c = prep.outline_check(ok)
    assert c["id"] == "outline_kept" and c["passed"] is True
    bad = {"outline": {"iou_min": 0.6, "iou_min_view": "side 90", "region_max": 0.3, "region_max_kind": "lost", "region_max_view": "side 60"}}
    c = prep.outline_check(bad)
    assert c["passed"] is False and c["vars"]["view"] == "side 60"
    assert prep.outline_check({"outline_error": "boom"})["vars"]["measured"] is False
    assert prep.outline_check({}) is None


# --- Blender: the planted-defect proof ----------------------------------------------------------------


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    exe = _blender()
    if not exe:
        pytest.skip("Blender not installed")
    out = tmp_path_factory.mktemp("planted") / "planted.json"
    script = BACKEND / "tests" / "blender_planted_shapes.py"
    subprocess.run([exe, "-b", "--factory-startup", "--python", str(script), "--", str(out)], check=True,
                   capture_output=True, timeout=600)
    return {r["case"]: r for r in json.loads(out.read_text())}


def _passes(case):
    o = case["outline"]
    return o["iou_min"] >= prep.OUTLINE_IOU_MIN and o["region_max"] <= prep.OUTLINE_REGION_MAX


@pytest.mark.parametrize("name", ["sword_clean", "sword_clean_hard", "mug_clean", "mug_clean_hard", "figure_clean",
                                  "figure_clean_hard", "box_clean", "box_clean_hard"])
def test_blender_clean_reductions_pass_the_outline_check(planted, name):
    assert _passes(planted[name]), planted[name]["outline"]


@pytest.mark.parametrize("name", ["sword_blade_sliver", "sword_blade_flat", "sword_thin_blade_flat", "mug_handle_hole_closed"])
def test_blender_the_outline_check_catches_a_collapsed_blade_and_a_closed_hole(planted, name):
    assert not _passes(planted[name]), planted[name]["outline"]


def test_blender_shape_loss_misses_a_thin_blade_pressed_flat_and_the_outline_does_not(planted):
    # Every point of the pressed blade stays within 0.5 percent of the height of its old surface, under shape_loss's own
    # 1 percent mark: no sample is over it, so today's block looks clean. Seen edge-on the blade is gone from the outline.
    thin = planted["sword_thin_blade_flat"]
    assert thin["shape_loss"]["lost"]["over_threshold"] == 0.0 and thin["shape_loss"]["added"]["over_threshold"] == 0.0
    assert thin["shape_loss"]["lost"]["max"] < shapeloss.THRESHOLD_SHARE
    assert not _passes(thin)


def test_blender_the_outline_misses_a_finger_and_a_small_dent_and_says_so(planted):
    # Not caught, on purpose recorded: a finger beside its neighbours is inside the 2 pixel tolerance, and a dent a tenth of the
    # box wide is a 0.8 percent patch, under the limit that real reductions need (real runs reach 0.7 percent).
    assert _passes(planted["figure_missing_finger"]) and _passes(planted["box_dented_corner"])


def test_blender_prep_writes_the_outline_block(tmp_path):
    from test_shape_loss import _prep_sphere

    report, _ = _prep_sphere(tmp_path, budget=100000)
    assert "outline_error" not in report
    block = report["outline"]
    assert block["iou_min"] > 0.99 and block["region_max"] == 0.0 and len(block["views"]) == 7
    assert prep.outline_check(report)["passed"] is True
