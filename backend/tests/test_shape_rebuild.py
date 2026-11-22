"""WP-83 (decision D23): a torn 3D model never stops a build. On buildings the 3D step builds again with the next seeds (at
most two more), keeps the best (lowest folded share, then lowest seed) and, when the best still fails, shows model_whole as
likely to show. Also the local fold measure on a planted mesh."""

import asyncio
from pathlib import Path

import numpy as np
import pytest

from vgac import check_tiers, meshgeom, steptime, texcolour
from vgac.stages import mesh as mesh_stage

from tests.test_texcolour import _SC, _picture


def _report(folded: float) -> dict:
    """A mesh_health.py report that is whole apart from the folded share."""
    return {"triangles": 1000, "edges": 1500, "open_edges": 0, "bad_join_edges": 0, "folded": {"120": folded}, "sliver_share": 0.0,
            "pieces": 1, "largest_piece_area": 1.0}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """_trellis for a building with the TRELLIS build replaced: build n writes model.glb holding its seed, and the grey-view
    measure comes from `folded` (a list: the folded share of build 1, 2, 3 in the order they are made)."""
    pic = _picture(tmp_path / "cutout.png", (200, 180, 160))
    monkeypatch.setattr(mesh_stage, "stage_recipe", lambda sc: {"mesh": {"normal_map": True}, "surface": {"texture_filter": "smooth"}})
    monkeypatch.setattr(mesh_stage, "_input_image", lambda sc, view: pic)
    monkeypatch.setattr(mesh_stage.reference, "recipe_record", lambda *a, **k: {})
    state = {"folded": [0.001], "seeds": [], "measured": []}

    async def fake_build(sc, name, n, seed, *a, **k):
        state["seeds"].append(seed)
        (sc.dir / "model.glb").write_text(f"glb {seed}")
        (sc.dir / "base_color.png").write_text(f"base {seed}")
        return {"seed": seed, "elapsed": 200.0, "maps": {"base_color": sc.dir / "base_color.png"}, "other": [], "metrics": {},
                "match": {"ratio": 0.9, "ok": True, "picture_luma": 180, "texture_luma": 162}}

    async def fake_health(sc, glb, tag):
        state["measured"].append(Path(glb).read_text())
        i = len(state["measured"]) - 1
        return _report(state["folded"][min(i, len(state["folded"]) - 1)])

    monkeypatch.setattr(mesh_stage, "_trellis_build", fake_build)
    monkeypatch.setattr(mesh_stage, "_health", fake_health)

    def run(params, category="environment"):
        d = tmp_path / f"v{len(list(tmp_path.glob('v*')))}"
        d.mkdir()
        sc = _SC(d, params, category)
        steptime.begin(sc)
        state["seeds"], state["measured"] = [], []
        asyncio.run(mesh_stage._trellis(sc, sc.version.params))
        return sc

    return state, run


def test_gate_passes_first_time_no_rebuild(gate):
    state, run = gate
    state["folded"] = [0.004]
    sc = run({"seed": 50})
    assert state["seeds"] == [50]
    rec = sc.version.metrics["shape_rebuild"]
    assert rec["extra_builds"] == 0 and rec["kept_try"] == 1 and rec["kept_passed"] is True
    assert rec["tries"] == [{"seed": 50, "folded": 0.004, "passed": True, "elapsed": 200.0}]
    assert sc.version.params["seed"] == 50 and sc.version.params["first_seed"] is None
    assert (sc.dir / "model.glb").read_text() == "glb 50"


def test_gate_fails_once_then_passes_keeps_the_second(gate):
    state, run = gate
    state["folded"] = [0.12, 0.002]
    sc = run({"seed": 50})
    second = texcolour.try_seed(50, 1)
    assert state["seeds"] == [50, second]
    rec = sc.version.metrics["shape_rebuild"]
    assert rec["extra_builds"] == 1 and rec["kept_try"] == 2 and rec["kept_passed"] is True
    assert [t["seed"] for t in rec["tries"]] == [50, second]
    assert (sc.dir / "model.glb").read_text() == f"glb {second}" and (sc.dir / "base_color.png").read_text() == f"base {second}"
    assert sc.version.params["seed"] == second and sc.version.params["first_seed"] == 50
    assert sc.version.metrics["seed"] == second
    assert sc.version.metrics["elapsed_seconds_all"] == 400.0
    assert not (sc.dir / mesh_stage.TRIES_DIR).exists()


def test_gate_fails_three_times_keeps_the_best_and_goes_on(gate):
    state, run = gate
    state["folded"] = [0.20, 0.09, 0.13]
    sc = run({"seed": 50})
    assert len(state["seeds"]) == 3 and state["seeds"][1:] == [texcolour.try_seed(50, 1), texcolour.try_seed(50, 2)]
    rec = sc.version.metrics["shape_rebuild"]
    assert rec["extra_builds"] == 2 and rec["kept_try"] == 2 and rec["kept_passed"] is False
    assert (sc.dir / "model.glb").read_text() == f"glb {state['seeds'][1]}"
    assert sc.version.metrics["elapsed_seconds_all"] == 600.0
    geometry = {"route": "buildings", "folded_edge_share": 0.09, "problems": meshgeom.problems("buildings", _report(0.09))}
    c = meshgeom.check("environment", geometry, True, rec["extra_builds"])
    assert c["passed"] is False and c["vars"]["rebuilds"] == 2 and "stop" not in c["vars"]
    assert check_tiers.tier_of(c) == "likely"
    assert any("built again" in m for m in sc.messages)


def test_best_of_three_can_be_the_last_or_the_first(gate):
    state, run = gate
    state["folded"] = [0.08, 0.10, 0.07]
    sc = run({"seed": 50})
    assert sc.version.metrics["shape_rebuild"]["kept_try"] == 3
    assert (sc.dir / "model.glb").read_text() == f"glb {state['seeds'][2]}"
    state["folded"] = [0.07, 0.10, 0.09]
    sc = run({"seed": 50})
    assert sc.version.metrics["shape_rebuild"]["kept_try"] == 1
    assert (sc.dir / "model.glb").read_text() == "glb 50" and sc.version.params["first_seed"] is None


def test_tie_break_is_lowest_folded_then_lowest_seed():
    tries = [{"seed": 900, "folded": 0.07}, {"seed": 300, "folded": 0.07}, {"seed": 100, "folded": 0.07}]
    assert meshgeom.pick_best(tries) == 2
    tries = [{"seed": 900, "folded": 0.07}, {"seed": 300, "folded": 0.06999}, {"seed": 100, "folded": 0.2}]
    assert meshgeom.pick_best(tries) == 1
    assert meshgeom.pick_best([{"seed": 5, "folded": None}, {"seed": 9, "folded": 0.3}]) == 1  # unmeasured is worst
    assert meshgeom.pick_best([{"seed": 5, "folded": 0.1}, {"seed": 5, "folded": 0.1}]) == 0


def test_other_routes_and_unmeasured_builds_are_not_rebuilt(gate, monkeypatch):
    state, run = gate
    state["folded"] = [0.5]
    sc = run({"seed": 50}, category="humanoid")  # characters: no rebuild route
    assert len(state["seeds"]) == 1 and "shape_rebuild" not in sc.version.metrics

    async def no_report(sc, glb, tag):
        return None

    monkeypatch.setattr(mesh_stage, "_health", no_report)
    sc = run({"seed": 50})
    assert len(state["seeds"]) == 1 and sc.version.metrics["shape_rebuild"]["kept_passed"] is None


def test_a_failed_model_never_stops_the_autopilot():
    from vgac import autopilot

    assert not any(entry[1] == "model_whole" for entry in autopilot.STOP_AND_ASK)


def test_buildings_limit_is_the_six_percent_rerun_2_limit():
    assert meshgeom.LIMITS["buildings"]["folded"] == 0.06
    assert meshgeom.passes("environment", _report(0.0474)) is True  # the highest judged-usable build of rerun 2
    assert meshgeom.passes("environment", _report(0.113)) is False  # the lowest judged-damaged one


# --- the local fold measure on planted meshes -----------------------------------------------------------------------

def _surface_points() -> np.ndarray:
    """Edge midpoints spread evenly over the surface of a unit cube (150000 points: about 200 edges within the 0.05 radius, like a real building)."""
    rng = np.random.default_rng(1)
    p = rng.random((150000, 3))
    face = rng.integers(0, 3, 150000)
    p[np.arange(150000), face] = rng.integers(0, 2, 150000)
    return p - 0.5


def test_planted_cluster_trips_and_scattered_folds_do_not():
    mid = _surface_points()
    rng = np.random.default_rng(7)
    scattered = rng.random(len(mid)) < 0.02          # 2 percent folded everywhere, like a mildly noisy model
    spot = meshgeom.local_fold(mid, scattered)
    assert spot is None or spot["share"] <= meshgeom.LOCAL_LIMIT
    near = np.flatnonzero(((mid - mid[0]) ** 2).sum(axis=1) <= 0.03 ** 2)
    clustered = scattered.copy()
    clustered[near] = True                            # every edge within 0.03 of one spot is folded
    spot = meshgeom.local_fold(mid, clustered)
    assert spot is not None and spot["share"] > 0.3 and spot["folded"] >= meshgeom.LOCAL_MIN_FOLDED
    assert clustered.mean() < 0.06                    # the whole-model share stays small, which is why the whole check misses it
    assert meshgeom.local_fold_check("environment", {"route": "buildings", "local_fold": spot})["passed"] is False
    assert meshgeom.local_fold_check("environment", {"route": "buildings", "local_fold": {}})["passed"] is True
    assert meshgeom.local_fold_check("environment", {"route": "buildings"}) is None  # not measured


def test_tiny_spots_and_nothing_folded_give_no_spot():
    mid = _surface_points()
    assert meshgeom.local_fold(mid, np.zeros(len(mid), bool)) is None
    few = np.zeros(len(mid), bool)
    few[:5] = True
    assert meshgeom.local_fold(mid, few) is None  # fewer than LOCAL_MIN_FOLDED in any ball


def test_local_fold_reads_the_blender_file(tmp_path):
    mid = _surface_points()
    folded = np.zeros(len(mid), bool)
    folded[np.flatnonzero(((mid - mid[0]) ** 2).sum(axis=1) <= 0.03 ** 2)] = True
    np.savez_compressed(tmp_path / "edge_folds.npz", mid=mid.astype(np.float32), folded=folded)
    assert meshgeom.local_fold_of(tmp_path / "edge_folds.npz")["share"] > 0.3
    assert meshgeom.local_fold_of(tmp_path / "missing.npz") is None
