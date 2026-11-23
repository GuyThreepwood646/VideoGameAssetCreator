"""LOD outline check (step 6, warning only): per-category marks, the pass and fail rule, the sentence a failed level shows,
and the tier that keeps it out of the advice list. The measure itself runs inside Blender (blender/pack.py); these tests
feed it the numbers a pack report would carry."""
from vgac import check_tiers, guidance, quality


def _outline(iou_min, region_max=0.0, view="side 60", kind="lost"):
    return {"iou_min": iou_min, "iou_min_view": view, "region_max": region_max, "region_max_kind": kind,
            "region_max_view": view}


def test_no_mark_or_no_levels_gives_no_check():
    assert quality.lod_outline_checks({}, "humanoid") == []
    assert quality.lod_outline_checks({"lod_outline": {"LOD1": _outline(0.5)}}, "material") == []
    assert quality.lod_outline_checks({"lod_outline": {"LOD1": _outline(0.5)}}, None) == []


def test_clean_level_passes_and_failing_level_is_named_with_its_view():
    report = {"lod_outline": {"LOD1": _outline(0.99), "LOD2": _outline(0.85, 0.0, "side 120")}}
    checks = {c["id"]: c for c in quality.lod_outline_checks(report, "foliage")}
    assert checks["lod_outline_kept_LOD1"]["passed"] is True
    bad = checks["lod_outline_kept_LOD2"]
    assert bad["passed"] is False and bad["limit"] == 0.90
    assert bad["vars"]["view"] == "side 120" and bad["vars"]["level"] == "2"


def test_region_over_the_limit_fails_even_when_iou_passes():
    report = {"lod_outline": {"LOD1": _outline(0.99, region_max=0.05, view="top", kind="added")}}
    (c,) = quality.lod_outline_checks(report, "prop_rigid")
    assert c["passed"] is False
    assert c["vars"]["view"] == "top" and c["vars"]["kind"] == "added"


def test_each_category_has_its_own_mark_and_foliage_levels_split_at_the_mark():
    # Reference numbers: a foliage LOD1 at 0.914 passes the mark, a foliage LOD2 at 0.850 is flagged.
    assert quality.LOD_OUTLINE_IOU_MIN["foliage"] == 0.90
    report = {"lod_outline": {"LOD1": _outline(0.914), "LOD2": _outline(0.850)}}
    got = {c["id"]: c["passed"] for c in quality.lod_outline_checks(report, "foliage")}
    assert got == {"lod_outline_kept_LOD1": True, "lod_outline_kept_LOD2": False}


def test_environment_mark_flags_the_shredded_arch_and_not_the_clean_levels():
    # Reference numbers: the stone arch LOD2 (0.8969, cornice shredded in review) is flagged at 0.90; the cottage LOD2
    # (0.9389) and the arch LOD1 (0.9636) pass.
    assert quality.LOD_OUTLINE_IOU_MIN["environment"] == 0.90
    report = {"lod_outline": {"LOD1": _outline(0.9636), "LOD2": _outline(0.8969)}}
    got = {c["id"]: c["passed"] for c in quality.lod_outline_checks(report, "environment")}
    assert got == {"lod_outline_kept_LOD1": True, "lod_outline_kept_LOD2": False}
    cottage = {"lod_outline": {"LOD2": _outline(0.9389)}}
    assert quality.lod_outline_checks(cottage, "environment")[0]["passed"] is True


def test_failed_level_sentence_names_the_level_and_the_limit():
    g, implied = guidance.check_guide("lod_outline_kept_LOD2")
    assert implied == {"level": "2"}
    assert g["severity"] == "warn" and "{level}" in g["fail"] and "{region_limit:pct}" in g["fail"]


def test_outline_level_check_is_look_tier_not_advice():
    assert check_tiers.tier_for("lod_outline_kept_LOD1") == "look"
