"""'Try a different face' on step 4: a clean-up again with the parent's settings but new face pass seeds
(stages/prep.py FACE_FRESH)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from vgac.stages import prep

SEEDS = {"face_detail_seed": 11, "face_seed": 11, "face_closeup_seed": 11, "face_closeup_seed_2": 12}


class FakeSc:
    def __init__(self, parent_params: dict[str, Any], own: dict[str, Any]):
        self.parent = SimpleNamespace(params=parent_params, stage="prep")
        self.version = SimpleNamespace(params=own)
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


def run_adjust(monkeypatch, params: dict[str, Any]) -> tuple[FakeSc, dict[str, Any]]:
    parent = {"size_m": 1.8, "face_strength": "strong", "face_closeup_own": True, **SEEDS}
    sc = FakeSc(parent, {**parent, **params})
    seen: dict[str, Any] = {}

    async def fake_prep(sc_: Any, p: dict[str, Any]) -> None:
        seen.update(p)

    monkeypatch.setattr(prep, "_prep", fake_prep)
    monkeypatch.setattr(prep.hairlib, "is_library", lambda sc_: False)
    asyncio.run(prep.adjust(sc, params))
    return sc, seen


def test_fresh_face_drops_the_face_seeds_and_keeps_every_other_setting(monkeypatch):
    sc, seen = run_adjust(monkeypatch, {"face_fresh": True})
    assert not set(SEEDS) & set(seen) and "face_fresh" not in seen
    assert seen["size_m"] == 1.8 and seen["face_strength"] == "strong" and seen["face_closeup_own"] is True
    # The new version's own record loses them too, so the pass writes the new seeds it picks.
    assert not set(SEEDS) & set(sc.version.params) and "face_fresh" not in sc.version.params and sc.saved


def test_a_plain_adjust_keeps_the_face_seeds(monkeypatch):
    _, seen = run_adjust(monkeypatch, {"face_closeup_own": True})
    assert {k: seen[k] for k in SEEDS} == SEEDS
