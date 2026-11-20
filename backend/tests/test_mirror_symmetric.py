"""WP-47 item 1: the left and right mirror_iou limit (turnaround.view_checks) applies only to categories that are meant
to be mirror-symmetric. same_side stays for every category: it protects the facing of the 3D model."""

from __future__ import annotations

from vgac import reference
from vgac.stages import turnaround

STATS = {"coverage": 0.3, "height": 100, "aspect": 0.4}


def _ids(info):
    return {c["id"] for c in info["checks"]}


def test_mirror_limit_is_on_for_symmetric_views_by_default():
    info = turnaround.view_checks("left", STATS, None, None, None, mirror=0.2, as_drawn=0.1)
    assert "mirror_iou" in _ids(info)


def test_mirror_limit_is_off_for_asymmetric_categories():
    info = turnaround.view_checks("left", STATS, None, None, None, mirror=0.2, as_drawn=0.1, symmetric=False)
    assert "mirror_iou" not in _ids(info) and "mirror_iou" not in info
    # the same_side check still runs: it is about the facing, not the mirror limit
    assert "same_side" in _ids(info)


def test_category_flags_match_the_yaml_intent():
    assert reference.category("humanoid")["symmetric"] is True
    assert reference.category("prop_rigid")["symmetric"] is False
    assert reference.category("environment")["symmetric"] is False
    assert reference.category("foliage")["symmetric"] is False


def test_symmetric_on_reads_the_asset_category():
    class _Asset:
        def __init__(self, c):
            self.category = c

    class _SC:
        def __init__(self, c):
            self.asset = _Asset(c)

    assert turnaround.symmetric_on(_SC("humanoid")) is True
    assert turnaround.symmetric_on(_SC("prop_rigid")) is False
    assert turnaround.symmetric_on(_SC(None)) is True
    assert turnaround.symmetric_on(_SC("not_a_category")) is True
