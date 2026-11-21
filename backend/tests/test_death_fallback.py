"""WP-78 death fallback rule (gait.death_fallback_used): the new death is kept unless it sinks more than 1 cm deeper than
Today's death over the fall. Pure Python: no Blender needed."""
import pytest

from vgac.blender import gait


def test_new_death_kept_when_it_sinks_no_deeper():
    assert gait.death_fallback_used(-0.0208, -0.0208) is False  # boy: the same depth as Today
    assert gait.death_fallback_used(-0.0044, -0.0044) is False  # blacksmith


def test_new_death_kept_when_it_is_shallower():
    assert gait.death_fallback_used(-0.0068, -0.0208) is False  # boy: better than Today
    assert gait.death_fallback_used(0.0, -0.0385) is False


def test_new_death_kept_within_one_centimetre_deeper():
    assert gait.death_fallback_used(-0.0485, -0.0385) is False  # 1.0 cm deeper is not more than 1 cm


def test_today_death_used_when_new_sinks_more_than_one_centimetre_deeper():
    assert gait.death_fallback_used(-0.1387, -0.0381) is True  # goblin: 10 cm deeper


def test_margin_is_one_centimetre():
    assert gait.DEATH_FALLBACK_MARGIN_M == pytest.approx(0.01)
    assert gait.death_fallback_used(-0.0401, -0.0300, margin=0.01) is True
