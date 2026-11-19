"""Hunyuan3D (hy3d) at step 3: the raw count is a note, not a block; prep judges the reduced mesh.

TRELLIS.2 keeps its step 3 triangle_budget check unchanged (its graph decimates to the target).
"""

from vgac import quality
from vgac.stages import mesh, prep

BUDGET = {"triangles": 20000, "texture": 2048}
RETRO = {"mesh": {"triangles": 3000, "texture": 256}}
HUMANOID_TRIS_LIMIT = 3000


def test_hy3d_raw_count_is_a_passing_note_with_prep_limit_recorded():
    params = {"engine": "hy3d", "seed": 733377461, "steps": 20, "cfg": 7.5}
    note, metrics = mesh.raw_triangle_note(RETRO, params, BUDGET, 308546)
    assert note is not None
    assert note["name"] == mesh.HY3D_RAW_NOTE
    assert note["passed"] is True
    assert note["severity"] == "warn"
    assert "Hunyuan3D made" in str(note)
    assert metrics == {"hy3d_raw_triangles": 308546, "hy3d_prep_limit": 3000, "hy3d_prep_limit_source": "recipe"}


def test_hy3d_on_platform_budget_records_platform_limit():
    params = {"engine": "hy3d"}
    note, metrics = mesh.raw_triangle_note({}, params, BUDGET, 699574)
    assert note["passed"] is True
    assert metrics["hy3d_prep_limit"] == 20000
    assert metrics["hy3d_prep_limit_source"] == "platform"


def test_hy3d_without_a_triangle_count_gives_no_note():
    note, metrics = mesh.raw_triangle_note(RETRO, {"engine": "hy3d"}, BUDGET, None)
    assert note is None
    assert metrics["hy3d_raw_triangles"] is None
    assert metrics["hy3d_prep_limit"] == 3000


def test_hy3d_note_is_never_a_failed_block():
    note, _ = mesh.raw_triangle_note(RETRO, {"engine": "hy3d"}, BUDGET, 700000)
    assert note["passed"] is True
    assert note.get("severity") != "block"


def test_trellis_step3_limit_is_unchanged():
    # TRELLIS.2 decimates in its graph to target_faces, so its own target is still the limit judge uses.
    params = {"engine": "trellis", "target_faces": 3000, "target_faces_source": "recipe"}
    limit, vars_ = mesh.triangle_target(RETRO, params, BUDGET)
    assert limit == 3000
    assert vars_["recipe"] is True
    assert vars_["platform_triangles"] == 20000


def test_prep_judges_the_reduced_count_and_blocks_when_over():
    report_ok = {"triangles": 2900, "edges": 4000}
    checks = prep.prep_checks(report_ok, {}, {}, "humanoid", HUMANOID_TRIS_LIMIT)
    tb = [c for c in checks if c["name"] == "triangle_budget"]
    assert tb and tb[0]["passed"] is True

    report_over = {"triangles": 308546, "edges": 400000}
    checks = prep.prep_checks(report_over, {}, {}, "humanoid", HUMANOID_TRIS_LIMIT)
    tb = [c for c in checks if c["name"] == "triangle_budget"]
    assert tb and tb[0]["passed"] is False
    assert tb[0]["severity"] == "block"


def test_hy3d_note_check_uses_guide_sentence():
    c = quality.check(mesh.HY3D_RAW_NOTE, True, 308546, 3000)
    assert c["severity"] == "warn"
    assert "Hunyuan3D made" in str(c)
