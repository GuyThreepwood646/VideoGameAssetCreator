"""WP-95: one backend table says how much each check matters; every check the backend can emit has an entry."""
from __future__ import annotations

import re
from pathlib import Path

from vgac import check_tiers, guidance
from vgac.check_tiers import TIER_IDS, annotate_metrics, explicit_tier, tier_of

from tests.test_fix_notes import STORED_IDS

SRC = Path(guidance.__file__).parent
# Ids written as f-strings or built from a map name: the patterns cover them, these are examples of each.
EXAMPLES = ["lod1_ratio", "lod3_ratio", "texture_base_color_pow2", "texture_normal_square", "texture_roughness_budget",
            "hitbox_head", "hitbox_cover"]


def _emitted_ids() -> set[str]:
    ids: set[str] = set()
    rx = re.compile(r"""(?:\bcheck|\bnot_measured|_check)\(\s*"([a-z][a-z0-9_]+)\"""")
    for p in SRC.rglob("*.py"):
        ids.update(rx.findall(p.read_text(encoding="utf8", errors="ignore")))
    # words the pattern also catches that are not checks (calls such as check("blender") in setup code)
    return ids - {"blender", "comfyui", "unity", "chosen", "colour_overruled", "picture", "data", "models", "tools", "libraries", "settings"}


def test_every_check_the_backend_can_emit_has_a_tier():
    universe = set(guidance.CHECK_GUIDE) | set(STORED_IDS) | _emitted_ids() | set(EXAMPLES)
    missing = sorted(i for i in universe if explicit_tier(i) is None)
    assert missing == [], f"Add these check ids to vgac/check_tiers.py (tier table): {missing}"


def test_table_uses_only_known_tiers_and_no_stale_ids():
    assert set(check_tiers.TABLE.values()) <= set(TIER_IDS)
    assert {t for _p, t in check_tiers.PATTERNS} <= set(TIER_IDS)


def test_lod_and_uv_padding_are_never_advice():
    for i in ("lod_count", "lod1_ratio", "lod2_ratio", "texture_padding"):
        assert explicit_tier(i) in ("likely", "look"), i


def test_near_miss_under_five_percent_is_advice_and_marked():
    c = {"id": "big_enough", "passed": False, "value": 840, "limit": 860}
    assert tier_of(c) == "advice"
    far = {"id": "big_enough", "passed": False, "value": 500, "limit": 860}
    assert tier_of(far) == "look"
    m = annotate_metrics({"checks": [c, far, {"id": "size_m", "passed": True, "value": 1, "limit": 1}]})
    assert m["checks"][0]["tier"] == "advice" and m["checks"][0]["near_miss"] is True
    assert m["checks"][1]["tier"] == "look" and "near_miss" not in m["checks"][1]
    assert "tier" not in m["checks"][2]


def test_unmeasured_and_non_numbers_are_not_near_misses():
    assert not check_tiers.is_near_miss({"id": "x", "passed": False, "value": "not measured", "limit": None, "vars": {"measured": False}})
    assert not check_tiers.is_near_miss({"id": "x", "passed": False, "value": [1, 2], "limit": [1, 2]})
    assert not check_tiers.is_near_miss({"id": "x", "passed": False, "value": 0, "limit": 0})


def test_served_table_and_version_payload():
    from fastapi.testclient import TestClient
    from vgac.main import app
    with TestClient(app) as c:
        r = c.get("/api/check-tiers")
    j = r.json()
    assert [t["id"] for t in j["tiers"]] == ["likely", "look", "advice"]
    assert j["table"]["size_m"] == "likely"
    assert j["table"]["tiles_cleanly"] == "look"


def test_a_served_version_carries_the_tier_on_failed_checks():
    from vgac.db import _with_fix_notes
    m = _with_fix_notes({"checks": [{"name": "size_m", "id": "size_m", "passed": False, "value": 1.2, "limit": 1.8},
                                    {"name": "bones", "id": "bones", "passed": True, "value": 1, "limit": 1}]})
    assert m["checks"][0]["tier"] == "likely"
    assert "tier" not in m["checks"][1]


def test_every_check_has_a_plain_headline_without_numbers():
    from vgac.check_headlines import headline_for
    universe = set(guidance.CHECK_GUIDE) | set(STORED_IDS) | _emitted_ids() | set(EXAMPLES)
    missing = sorted(i for i in universe if headline_for(i) is None)
    assert missing == [], f"Add a headline for these check ids in vgac/check_headlines.py: {missing}"
    for i in universe:
        h = headline_for(i)
        assert 15 <= len(h) <= 85 and (i.startswith("lod") or not any(ch.isdigit() for ch in h.replace("3D", ""))) and not h.endswith("."), (i, h)


def test_headline_is_served_on_failed_checks_but_not_when_unmeasured():
    from vgac.check_headlines import headline_for
    m = annotate_metrics({"checks": [{"id": "neck_join", "passed": False, "value": 0, "limit": 2},
                                     {"id": "uv_set", "passed": False, "value": "not measured", "limit": None, "vars": {"measured": False}}]})
    assert m["checks"][0]["headline"] == "The seam between face and body paint may show at the neck"
    assert "headline" not in m["checks"][1]
    assert headline_for("lod2_ratio") == "Detail level 2 has the wrong number of triangles"
