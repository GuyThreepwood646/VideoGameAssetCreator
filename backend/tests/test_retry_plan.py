"""The retry plan at the 3D step (texcolour.py): a base colour that comes out too dark against the picture is built again
with the next full seed, up to MAX_TRIES builds, and the closest is kept. There is no texture-only retry (WP-11 removed
it: its rerun stayed dark where the full rebuild gave a passing colour).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json

from vgac import texcolour
from vgac.graphs.trellis import convert_graph


def _digest(g: dict) -> str:
    return hashlib.sha256(json.dumps(g, sort_keys=True).encode()).hexdigest()


def test_default_graph_is_byte_identical_to_before():
    # Hashes taken from the graph before texture_seed existed (master at WP-11 start).
    assert _digest(convert_graph("in.png", "pfx")) == "dbe6cbb15bb59b3dc716b42e31f9797a1ec5f9c6783f08aeb8da3e76b3c618b2"
    kw = dict(target_faces=30000, tex=2048, upsample=1536, seed=7, padding=16, tex_steps=24)
    assert _digest(convert_graph("in.png", "pfx", **kw)) == "af1971af3956f17b8ef3ca86be2d8d28877a6f43e4bac8dadf9a407d0de9e7b3"
    kw = dict(seed=99, remesh=False, tex_steps=12, padding=0)
    assert _digest(convert_graph("in.png", "pfx", **kw)) == "2e2787ab8fdd882b33d6db61fddef012a91e36a9a53a641bca6a018e1127b61d"


def test_the_texture_only_retry_is_gone():
    # No switch, constant or texture seed is left behind for it.
    for name in ("TEXTURE_TRIES", "texture_try_seed", "try_kind", "try_plan"):
        assert not hasattr(texcolour, name), name
    assert "texture_seed" not in inspect.signature(convert_graph).parameters


def _fake(ratios: list[float], seen: list[tuple[int, int]]):
    async def build(seed: int, n: int) -> dict:
        seen.append((n, seed))
        r = ratios[n]
        return {"seed": seed, "match": {"ratio": r, "ok": r >= texcolour.RATIO_MIN}}
    return build


def test_dark_first_build_gets_a_full_rebuild_with_no_texture_only_try():
    seen: list[tuple[int, int]] = []
    aside: list[int] = []
    best, tries = asyncio.run(texcolour.build_with_rerolls(77, True, _fake([0.2, 0.8, 0.9], seen), aside.append, lambda n: None))
    assert seen == [(0, 77), (1, texcolour.try_seed(77, 1))]  # the retry takes a new shape seed at once
    assert best == 1 and len(tries) == 2
    assert aside == [0]
    assert all("kind" not in t for t in tries)


def test_every_full_rebuild_dark_keeps_the_closest_of_max_tries():
    seen: list[tuple[int, int]] = []
    back: list[int] = []
    best, tries = asyncio.run(texcolour.build_with_rerolls(77, True, _fake([0.1, 0.45, 0.3], seen), lambda n: None, back.append))
    assert seen == [(0, 77), (1, texcolour.try_seed(77, 1)), (2, texcolour.try_seed(77, 2))]
    assert len(tries) == texcolour.MAX_TRIES
    assert best == 1 and back == [1]  # highest ratio among the failures
    assert texcolour.check(texcolour.record(best, tries))["ok"] is False


def test_nothing_to_compare_builds_once():
    seen: list[tuple[int, int]] = []
    best, tries = asyncio.run(texcolour.build_with_rerolls(5, False, _fake([0.1, 0.9, 0.9], seen), lambda n: None, lambda n: None))
    assert best == 0 and len(tries) == 1 and seen == [(0, 5)]
