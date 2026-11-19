"""WP-16 shape loss (metrics only): the pure helpers in vgac/blender/shapeloss.py, and prep.py's shape_loss block on a
real Blender run (a hard budget reduction, and none)."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from vgac.blender import shapeloss

from test_great_assets_laneb import BACKEND, _blender

approx = pytest.approx

UNIT_TRIANGLE = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
ONE_TRI = np.array([[0, 1, 2]])


# --- the pure helpers -------------------------------------------------------------------


def test_sample_surface_stays_on_the_triangle_and_repeats_with_the_seed():
    pts = shapeloss.sample_surface(UNIT_TRIANGLE, ONE_TRI, 500, seed=7)
    assert pts.shape == (500, 3)
    assert np.allclose(pts[:, 2], 0.0)
    assert (pts[:, 0] >= -1e-12).all() and (pts[:, 1] >= -1e-12).all() and (pts[:, 0] + pts[:, 1] <= 1 + 1e-12).all()
    again = shapeloss.sample_surface(UNIT_TRIANGLE, ONE_TRI, 500, seed=7)
    assert np.array_equal(pts, again)
    assert not np.array_equal(pts, shapeloss.sample_surface(UNIT_TRIANGLE, ONE_TRI, 500, seed=8))


def test_sample_surface_is_weighted_by_area():
    # Triangle A has area 0.5, triangle B has area 4.5 (nine times as much): about 90 percent of the points land on B.
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [10, 0, 0], [13, 0, 0], [10, 3, 0]], dtype=float)
    tris = np.array([[0, 1, 2], [3, 4, 5]])
    pts = shapeloss.sample_surface(verts, tris, 4000, seed=16)
    on_b = (pts[:, 0] >= 9.5).mean()
    assert on_b == approx(0.9, abs=0.03)


def test_sample_surface_empty_when_there_is_no_area():
    assert shapeloss.sample_surface(UNIT_TRIANGLE, np.zeros((0, 3), dtype=int), 10).shape == (0, 3)
    flat = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)
    assert shapeloss.sample_surface(flat, np.array([[0, 1, 2]]), 10).shape == (0, 3)
    assert shapeloss.sample_surface(UNIT_TRIANGLE, ONE_TRI, 0).shape == (0, 3)


def test_summarize_gives_shares_of_the_height():
    s = shapeloss.summarize([0.0, 0.01, 0.02, 0.5], height=1.0)
    assert s["mean"] == approx(0.1325, abs=1e-6)
    assert s["p95"] == approx(0.428, abs=1e-6)
    assert s["max"] == approx(0.5)
    assert s["over_threshold"] == approx(0.5)  # 0.02 and 0.5 are beyond 1 percent; 0.01 is not
    assert s["samples"] == 4


def test_summarize_scales_by_height_and_handles_no_samples():
    s = shapeloss.summarize([0.2, 0.2], height=2.0)
    assert s["mean"] == approx(0.1) and s["over_threshold"] == approx(1.0)
    empty = shapeloss.summarize([], height=2.0)
    assert empty["mean"] is None and empty["samples"] == 0
    assert shapeloss.summarize([0.1], height=0.0)["max"] is None


def test_region_index_thirds_of_height_and_halves_of_x():
    lo, hi = (0.0, 0.0, 0.0), (2.0, 1.0, 3.0)
    pts = [(0.5, 0.5, 0.5), (1.5, 0.5, 2.5), (1.0, 0.5, 1.5)]
    names = [shapeloss.region_name(i) for i in shapeloss.region_index(pts, lo, hi)]
    assert names == ["bottom x_low", "top x_high", "middle x_high"]


def test_worst_regions_ranks_named_regions_and_skips_thin_ones():
    rng = np.random.default_rng(1)
    bottom_low = np.column_stack([rng.uniform(0.0, 0.9, 25), rng.uniform(0, 1, 25), rng.uniform(0.0, 0.3, 25)])
    top_high = np.column_stack([rng.uniform(1.1, 2.0, 25), rng.uniform(0, 1, 25), rng.uniform(2.7, 3.0, 25)])
    thin = np.column_stack([rng.uniform(1.1, 2.0, 5), rng.uniform(0, 1, 5), rng.uniform(1.4, 1.6, 5)])
    pts = np.vstack([bottom_low, top_high, thin])
    dists = np.concatenate([np.full(25, 0.1), np.full(25, 0.3), np.full(5, 0.9)])
    worst = shapeloss.worst_regions(pts, dists, (0, 0, 0), (2, 1, 3), height=3.0)
    assert [w["region"] for w in worst] == ["top x_high", "bottom x_low"]
    assert worst[0]["mean"] == approx(0.1) and worst[0]["samples"] == 25


def test_measure_with_no_distance_is_zero_and_with_a_fixed_distance_is_over_threshold():
    cube = np.array([[x, y, z] for x in (0, 2) for y in (0, 1) for z in (0, 3)], dtype=float)
    tris = np.array([[0, 1, 2], [1, 3, 2]])  # any surface with area will do for the sampler
    zero = lambda p: np.zeros(len(p))  # noqa: E731
    block = shapeloss.measure(cube, tris, cube, tris, zero, zero, samples=600)
    assert block["height_m"] == approx(3.0)
    assert block["lost"]["mean"] == 0.0 and block["added"]["max"] == 0.0
    assert block["samples"] == 600 and block["seed"] == shapeloss.SEED
    far = lambda p: np.full(len(p), 0.06)  # noqa: E731  (2 percent of the 3 m height)
    block = shapeloss.measure(cube, tris, cube, tris, far, far, samples=600)
    assert block["lost"]["over_threshold"] == 1.0
    assert block["lost"]["mean"] == approx(0.02, abs=1e-6)


def test_measure_caps_the_samples():
    cube = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    tris = np.array([[0, 1, 2], [0, 1, 3]])
    zero = lambda p: np.zeros(len(p))  # noqa: E731
    block = shapeloss.measure(cube, tris, cube, tris, zero, zero, samples=10**6)
    assert block["samples"] == shapeloss.SAMPLES_CAP


# --- Blender ----------------------------------------------------------------------------


def _prep_sphere(tmp_path, budget: int):
    """prep.py on a 5120-triangle sphere with the given budget: (the report, the GLB it wrote)."""
    exe = _blender()
    if not exe:
        pytest.skip("Blender not installed")
    trimesh = pytest.importorskip("trimesh")
    src = tmp_path / f"sphere_{budget}.glb"
    trimesh.creation.icosphere(subdivisions=4, radius=1.0).export(src)  # 5120 triangles
    out = tmp_path / f"out_{budget}.glb"
    script = BACKEND / "vgac" / "blender" / "prep.py"
    subprocess.run([exe, "-b", "--factory-startup", "--python", str(script), "--", str(src), str(out), "ball",
                    "--budget", str(budget)], check=True, capture_output=True, timeout=300)
    return json.loads((tmp_path / f"out_{budget}_report.json").read_text()), out


def test_blender_prep_measures_shape_loss_after_a_hard_reduction(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    report, out = _prep_sphere(tmp_path, budget=60)
    assert report["decimated"] and report["triangles"] <= 60
    assert "shape_loss_error" not in report
    block = report["shape_loss"]
    scene = trimesh.load(out, force="scene")
    lo, hi = scene.bounds
    # The height is the final mesh's Z extent in Blender, which the glTF file holds as Y (Y up). (The report's own
    # height_m and dims_m are measured before the budget decimation, so they still show the pre-reduction 1.8 m.)
    assert block["height_m"] == approx(float(hi[1] - lo[1]), rel=1e-3)
    assert block["lost"]["p95"] > 0.005  # a 60-triangle sphere is far from a 5120-triangle one
    assert block["lost"]["over_threshold"] > 0.0
    assert block["added"]["mean"] > 0.0
    assert block["worst_regions"] and all(w["samples"] >= shapeloss.MIN_REGION_SAMPLES for w in block["worst_regions"])
    assert block["seconds"] < 30
    assert sum(len(g.faces) for g in scene.geometry.values()) <= 60  # the helper copy never reaches the export


def test_blender_prep_shape_loss_is_near_zero_without_a_reduction(tmp_path):
    report, _ = _prep_sphere(tmp_path, budget=100000)
    assert not report["decimated"]
    block = report["shape_loss"]
    assert block["lost"]["max"] < 1e-3 and block["added"]["max"] < 1e-3
    assert block["lost"]["over_threshold"] == 0.0
