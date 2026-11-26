"""WP-83 (decision D23): a torn 3D model never stops a build. On buildings the 3D step builds again with the next seeds (at
most two more), keeps the best (lowest folded share, then lowest seed) and, when the best still fails, shows model_whole as
likely to show."""

import asyncio
from pathlib import Path

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
    # colour: seed -> whether that build's base colour passes (default passes).
    state = {"folded": [0.001], "seeds": [], "measured": [], "colour": {}}

    async def fake_build(sc, name, n, seed, *a, **k):
        state["seeds"].append(seed)
        (sc.dir / "model.glb").write_text(f"glb {seed}")
        (sc.dir / "base_color.png").write_text(f"base {seed}")
        ok = state["colour"].get(seed, True)
        return {"seed": seed, "elapsed": 200.0, "maps": {"base_color": sc.dir / "base_color.png"}, "other": [], "metrics": {},
                "match": {"ratio": 0.9 if ok else 0.2, "ok": ok, "picture_luma": 180, "texture_luma": 162 if ok else 36}}

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
    assert rec["tries"] == [{"seed": 50, "folded": 0.004, "passed": True, "elapsed": 200.0, "colour": {"ratio": 0.9, "ok": True}}]
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


def test_gate_keeps_a_whole_dark_build_over_a_torn_bright_one(gate):
    state, run = gate
    second = texcolour.try_seed(50, 1)
    state["folded"] = [0.12, 0.002]  # the first build is torn (colour bright), the second whole (colour dark)
    state["colour"] = {second: False}
    sc = run({"seed": 50})
    rec = sc.version.metrics["shape_rebuild"]
    assert rec["kept_try"] == 2 and rec["extra_builds"] == 1
    assert rec["tries"][1]["colour"] == {"ratio": 0.2, "ok": False}
    assert rec["tries"][0]["colour"] == {"ratio": 0.9, "ok": True}
    assert (sc.dir / "model.glb").read_text() == f"glb {second}" and sc.version.params["first_seed"] == 50


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


def test_pick_prefers_a_build_whose_colour_passes_over_a_lower_folded_share():
    tries = [{"seed": 50, "folded": 0.12, "colour": {"ok": True}}, {"seed": 60, "folded": 0.02, "colour": {"ok": False}},
             {"seed": 70, "folded": 0.09, "colour": {"ok": True}}]
    assert meshgeom.pick_best(tries) == 2  # lowest folded among the colour passes; the dark 0.02 build loses


def test_pick_among_colour_passes_is_lowest_folded_then_lowest_seed():
    tries = [{"seed": 900, "folded": 0.07, "colour": {"ok": True}}, {"seed": 300, "folded": 0.07, "colour": {"ok": True}},
             {"seed": 100, "folded": 0.07, "colour": {"ok": False}}]
    assert meshgeom.pick_best(tries) == 1  # the lowest seed among the passes, not the dark build with the lowest seed


def test_pick_group_boundaries_whole_dark_beats_torn_bright_and_both_pass_beats_both():
    torn_bright = {"seed": 50, "folded": 0.12, "passed": False, "colour": {"ok": True}}   # group 3: colour only
    whole_dark = {"seed": 60, "folded": 0.04, "passed": True, "colour": {"ok": False}}   # group 2: geometry only
    both = {"seed": 70, "folded": 0.09, "passed": True, "colour": {"ok": True}}          # group 1: both pass
    assert meshgeom.pick_best([torn_bright, whole_dark]) == 1  # a whole dark build beats a torn bright one
    assert meshgeom.pick_best([torn_bright, whole_dark, both]) == 2  # a both-pass build beats both of those


def test_pick_when_no_build_passes_colour_keeps_the_old_order():
    tries = [{"seed": 50, "folded": 0.2, "colour": {"ok": False}}, {"seed": 60, "folded": 0.1, "colour": {"ok": False}}]
    assert meshgeom.pick_best(tries) == 1  # lowest folded, as before
    tries = [{"seed": 50, "folded": 0.2, "colour": None}, {"seed": 60, "folded": 0.1, "colour": {"ok": False}}]
    assert meshgeom.pick_best(tries) == 0  # an unmeasured colour counts as passing, so it is the only pool


def test_a_failed_model_never_stops_the_autopilot():
    from vgac import autopilot

    assert not any(entry[1] == "model_whole" for entry in autopilot.STOP_AND_ASK)


def test_buildings_limit_is_the_six_percent_rerun_2_limit():
    assert meshgeom.LIMITS["buildings"]["folded"] == 0.06
    assert meshgeom.passes("environment", _report(0.0474)) is True  # the highest judged-usable build of rerun 2
    assert meshgeom.passes("environment", _report(0.113)) is False  # the lowest judged-damaged one


def test_soft_limit_builds_a_whole_but_folded_model_once_more_and_keeps_the_lower(gate):
    state, run = gate
    state["folded"] = [0.0106, 0.0063]  # whole (under 0.06) but over the scenery soft limit of 0.009
    sc = run({"seed": 50})
    assert state["seeds"] == [50, texcolour.try_seed(50, 1)]
    rec = sc.version.metrics["shape_rebuild"]
    assert rec["extra_builds"] == 1 and rec["kept_try"] == 2 and rec["soft_rebuild"] is True
    assert (sc.dir / "model.glb").read_text() == f"glb {texcolour.try_seed(50, 1)}"


def test_soft_limit_keeps_the_first_when_the_extra_build_is_worse_and_never_builds_a_third(gate):
    state, run = gate
    state["folded"] = [0.0106, 0.0163, 0.0001]
    sc = run({"seed": 50})
    assert state["seeds"] == [50, texcolour.try_seed(50, 1)]  # one extra build only
    rec = sc.version.metrics["shape_rebuild"]
    assert rec["kept_try"] == 1 and rec["extra_builds"] == 1
    assert (sc.dir / "model.glb").read_text() == "glb 50"


def test_soft_limit_does_not_fire_at_or_under_the_limit(gate):
    state, run = gate
    state["folded"] = [0.009]
    sc = run({"seed": 50})
    assert state["seeds"] == [50]
    assert "soft_rebuild" not in sc.version.metrics["shape_rebuild"]


def test_soft_limit_is_scenery_only():
    assert meshgeom.soft_fold_over("environment", 0.0106) is True
    assert meshgeom.soft_fold_over("environment", 0.0063) is False
    assert meshgeom.soft_fold_over("environment", None) is False
    assert meshgeom.soft_fold_over("humanoid", 0.5) is False
    assert meshgeom.soft_fold_over("foliage", 0.15) is False
