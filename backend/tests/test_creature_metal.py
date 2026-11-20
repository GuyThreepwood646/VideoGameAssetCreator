"""Creatures are not metal unless the description says so (vgac/creaturemetal.py, WP-65 step 3)."""
import numpy as np

from vgac import creaturemetal, imaging, skinmetal


def test_only_the_creature_categories_take_part():
    for category in ("humanoid", "vehicle", "prop_rigid", "prop_articulated", "environment", "foliage", "material"):
        assert creaturemetal.mode(category, "a brown dog") is None
    for category in creaturemetal.CREATURE_CATEGORIES:
        assert creaturemetal.mode(category, "a brown dog") == "clear"


def test_a_metal_look_word_keeps_the_builders_metal():
    assert creaturemetal.mode("multileg", "iridescent beetle") == "keep"
    assert creaturemetal.mode("winged", "a clockwork hawk") == "keep"
    assert creaturemetal.mode("serpent", "a chrome lizard") == "keep"
    assert creaturemetal.mode("quadruped", "a mechanical spider") == "keep"
    assert creaturemetal.mode("multileg", "a steel-plated crab") == "keep"
    assert creaturemetal.mode("multileg", "an armoured crab") == "keep"
    assert creaturemetal.mode("serpent", "a golden serpent") == "keep"
    assert creaturemetal.mode("winged", "a bronze eagle") == "keep"
    assert creaturemetal.mode("quadruped", "a robot wolf") == "keep"


def test_colour_words_for_fur_feathers_scales_and_breeds_are_not_metal():
    for category, description in (("quadruped", "golden retriever"), ("quadruped", "silver fox"),
                                  ("quadruped", "silverback gorilla"), ("quadruped", "a wolf with copper-coloured fur"),
                                  ("winged", "a hawk with golden feathers"), ("quadruped", "a cat, silver tabby"),
                                  ("serpent", "a snake with silver scales"), ("winged", "a bird with bronze plumage"),
                                  ("quadruped", "a dog with a golden coat")):
        assert creaturemetal.mode(category, description) == "clear", description


def test_colour_words_that_are_a_metal_stay_metal():
    assert creaturemetal.mode("winged", "a golden dragon") == "keep"
    assert creaturemetal.mode("winged", "a bronze dragon") == "keep"
    assert creaturemetal.mode("multileg", "a silver beetle") == "keep"
    assert creaturemetal.mode("winged", "a copper-plated dragon") == "keep"  # plated always counts


def test_words_that_always_count_keep_metal_whatever_the_colour_context():
    assert creaturemetal.mode("winged", "gilded dragon") == "keep"
    assert creaturemetal.mode("multileg", "iridescent beetle") == "keep"
    assert creaturemetal.mode("winged", "a metal-feathered hawk") == "keep"
    assert creaturemetal.mode("serpent", "a dragon with iron scales") == "keep"


def test_a_shine_word_clears_metal_and_no_metal_word_clears_it():
    assert creaturemetal.mode("multileg", "glossy black beetle") == "shine"
    assert creaturemetal.mode("multileg", "a wet frog") == "shine"
    assert creaturemetal.mode("serpent", "a polished viper") == "shine"
    assert creaturemetal.mode("winged", "a lacquered parrot") == "shine"
    assert creaturemetal.mode("quadruped", "a glassy wolf") == "shine"
    assert creaturemetal.mode("multileg", "a brown beetle") == "clear"


def test_a_metal_word_wins_over_a_shine_word():
    assert creaturemetal.mode("multileg", "glossy metallic beetle") == "keep"
    assert creaturemetal.mode("multileg", "polished iridescent beetle") == "keep"


def test_a_cardboard_or_costume_robot_is_not_metal_but_a_real_one_is():
    assert not skinmetal.metal_body("a cardboard robot costume")
    assert not skinmetal.metal_body("a boy in a robot costume")
    assert not skinmetal.metal_body("a cardboard robot dog")
    assert skinmetal.metal_body("a rusty robot knight")
    assert skinmetal.metal_body("a robot")
    assert skinmetal.metal_body("a golem made of iron")
    assert creaturemetal.mode("quadruped", "a cardboard robot dog") == "clear"
    assert creaturemetal.mode("quadruped", "a robot dog") == "keep"


def test_clear_zeroes_the_covered_metal_and_nothing_else():
    metal = np.array([[0.6, 0.9], [0.4, 0.0]])
    covered = np.array([[True, True], [False, True]])
    new_m, new_r = creaturemetal.apply_maps(metal, None, covered, "clear")
    assert new_m.tolist() == [[0.0, 0.0], [0.4, 0.0]]
    assert new_r is None
    assert metal[0, 0] == 0.6  # the input is not changed


def test_keep_changes_nothing():
    metal = np.array([[0.6, 0.9]])
    rough = np.array([[0.8, 0.7]])
    covered = np.array([[True, True]])
    new_m, new_r = creaturemetal.apply_maps(metal, rough, covered, "keep")
    assert new_m.tolist() == metal.tolist() and new_r.tolist() == rough.tolist()


def test_shine_clears_metal_and_lowers_only_the_roughness_that_is_too_high():
    metal = np.array([[0.6, 0.4, 0.5]])
    rough = np.array([[0.85, 0.2, 0.9]])
    covered = np.array([[True, True, False]])
    new_m, new_r = creaturemetal.apply_maps(metal, rough, covered, "shine")
    assert new_m.tolist() == [[0.0, 0.0, 0.5]]  # the uncovered texel keeps its metal
    assert new_r[0, 0] == creaturemetal.SHINE_ROUGH_CAP  # too high: lowered
    assert new_r[0, 1] == 0.2  # already low: left alone
    assert new_r[0, 2] == 0.9  # not covered: left alone


def test_prep_sharpening_cannot_raise_cleared_metal():
    cleared = np.zeros((4, 4))
    assert not imaging.sharpen_metallic(cleared).any()


def test_clear_skips_a_category_that_is_not_a_creature_and_records_it(tmp_path):
    r = creaturemetal.clear(tmp_path / "none.glb", {"metallic": tmp_path / "m.png"}, "vehicle", "a car", tmp_path)
    assert r["files"] == {}
    assert r["metrics"]["creature_metal"]["skipped"] == "not a creature"


def test_clear_on_a_missing_model_changes_nothing_and_does_not_raise(tmp_path):
    r = creaturemetal.clear(tmp_path / "none.glb", {"metallic": tmp_path / "m.png"}, "quadruped", "a brown dog", tmp_path)
    assert r["files"] == {}
    assert r["metrics"]["creature_metal"]["ran"] is False
