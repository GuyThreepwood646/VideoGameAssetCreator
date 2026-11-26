"""PL-131: the triangle floor read from one descent of the reduction, the limit it gives, the recipe keys and the check."""
from vgac import guidance, reference, trifloor
from vgac.stages import prep

STEPS = [{"tris": 10000, "p95": 0.0002}, {"tris": 5000, "p95": 0.0006}, {"tris": 2500, "p95": 0.0015}, {"tris": 1250, "p95": 0.006}, {"tris": 600, "p95": 0.03}]


def test_floor_is_read_between_two_steps_on_a_log_line():
    floor = trifloor.floor_from_steps(STEPS, 1024, 1.5)  # allowed 1.5 / 1024 = 0.001465
    assert 2500 < floor < 2700  # just under the 2,500 step, whose error (0.0015) is a touch over the allowance
    assert trifloor.floor_from_steps(STEPS, 1024, 1.0) > floor  # a stricter allowance needs more triangles
    assert trifloor.floor_from_steps(STEPS, 2048, 1.5) > floor  # a bigger picture shows finer detail


def test_floor_edges():
    assert trifloor.floor_from_steps([], 1024, 1.5) is None
    assert trifloor.floor_from_steps(STEPS, 0, 1.5) is None
    assert trifloor.floor_from_steps([{"tris": 800, "p95": 0.0001}], 1024, 1.5) == trifloor.MIN_TRIANGLES  # holds its shape to the bottom
    assert trifloor.floor_from_steps([{"tris": 800, "p95": 0.5}], 1024, 1.5) == 800  # over even at the first step


def test_limit_is_the_larger_of_target_and_floor_capped_at_the_platform():
    assert trifloor.limit_with_floor(3000, None, 15000) == {"limit": 3000, "floor": None, "state": "none"}
    assert trifloor.limit_with_floor(3000, 2250, 15000)["state"] == "ok"
    assert trifloor.limit_with_floor(3000, 2250, 15000)["limit"] == 3000
    raised = trifloor.limit_with_floor(3000, 3700, 15000)
    assert (raised["limit"], raised["state"]) == (3700, "raised")
    over = trifloor.limit_with_floor(3000, 3700, 3000)
    assert (over["limit"], over["state"]) == (3000, "over_max")


def test_low_poly_scenery_asks_for_a_floor_and_other_looks_do_not():
    r = reference.recipe("lowpoly_flat", "environment", "pc_mid")
    assert r["mesh"]["floor_error_px"] == 1.5 and r["mesh"]["floor_picture_px"] == 1024
    assert "floor_error_px" not in reference.recipe("lowpoly_flat", "humanoid", "pc_mid")["mesh"]
    assert "floor_error_px" not in reference.recipe("realistic_pbr", "environment", "pc_mid")["mesh"]


def test_floor_check_is_quiet_unless_the_floor_is_over_the_target():
    assert prep.floor_check(None, None) is None
    assert prep.floor_check({"state": "ok", "floor": 2000, "target": 3000}, None) is None
    raised = prep.floor_check({"state": "raised", "limit": 3700, "floor": 3700, "target": 3000, "platform_max": 15000}, {"look": "Low-poly"})
    assert raised["id"] == "triangle_floor" and not raised["ok"]
    assert "3,700" in raised["help"] and "3,000" in raised["help"] and "Low-poly" in raised["help"]
    over = prep.floor_check({"state": "over_max", "limit": 3000, "floor": 3700, "target": 3000, "platform_max": 3000}, {"look": "Low-poly"})
    assert "at most" in over["help"] and "3,700" in over["help"]
    assert "triangle_floor" in guidance.CHECK_GUIDE
