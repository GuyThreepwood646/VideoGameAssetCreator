"""The texture seed in the TRELLIS.2 graph (graphs/trellis.py texture_seed) and the retry plan in texcolour.py: texture-only
tries first (same shape seed), then full rebuilds. Trialled on three recorded dark pictures on card B (WP-11).
"""

from __future__ import annotations

import asyncio
import hashlib
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


def test_texture_seed_defaults_to_seed_plus_one_and_changes_only_the_texture_sampler():
    base = convert_graph("in.png", "pfx", seed=42)
    assert base["k4"]["inputs"]["seed"] == 43
    assert _digest(convert_graph("in.png", "pfx", seed=42, texture_seed=43)) == _digest(base)
    other = convert_graph("in.png", "pfx", seed=42, texture_seed=1234)
    assert other["k4"]["inputs"]["seed"] == 1234
    # Every node except the texture sampler is unchanged, so ComfyUI reuses the shape stages' cached outputs.
    for node in base:
        if node != "k4":
            assert base[node] == other[node], node


def test_texture_try_seed_build_zero_matches_today_and_later_builds_step():
    first = 1234
    assert texcolour.texture_try_seed(first, 0) == first + 1
    seeds = [texcolour.texture_try_seed(first, n) for n in range(3)]
    assert len(set(seeds)) == 3 and all(1 <= s <= texcolour.SEED_MAX for s in seeds)


def test_plan_texture_tries_keep_the_shape_then_full_rebuilds_follow_today():
    first = 77
    # Default: build 0 first, build 1 texture-only (shape kept), build 2 the full rebuild today makes (try_seed 1).
    assert texcolour.try_plan(first, 0) == (77, None)
    assert texcolour.try_plan(first, 1) == (77, texcolour.texture_try_seed(first, 1))
    assert texcolour.try_plan(first, 2) == (texcolour.try_seed(first, 1), None)
    assert [texcolour.try_kind(n) for n in range(3)] == ["first", "texture_only", "full"]
    # With no texture tries the plan is the old full-rebuild sequence.
    assert [texcolour.try_plan(first, n, 0)[0] for n in range(3)] == [texcolour.try_seed(first, n) for n in range(3)]
    assert all(texcolour.try_plan(first, n, 0)[1] is None for n in range(3))


def _fake(ratios: list[float], seen: list[tuple[int, int]]):
    async def build(seed: int, n: int) -> dict:
        seen.append((n, seed))
        r = ratios[n]
        return {"seed": seed, "match": {"ratio": r, "ok": r >= texcolour.RATIO_MIN}}
    return build


def test_texture_try_that_passes_stops_before_any_full_rebuild():
    seen: list[tuple[int, int]] = []
    aside: list[int] = []
    best, tries = asyncio.run(texcolour.build_with_rerolls(77, True, _fake([0.2, 0.7, 0.9], seen), aside.append, lambda n: None))
    assert seen == [(0, 77), (1, 77)]  # the second build kept the shape seed
    assert best == 1 and len(tries) == 2
    assert [t["kind"] for t in tries] == ["first", "texture_only"]
    assert aside == [0]
    assert texcolour.record(best, tries)["texture_tries"] == 2


def test_texture_try_that_fails_falls_through_to_a_full_rebuild():
    seen: list[tuple[int, int]] = []
    aside: list[int] = []
    best, tries = asyncio.run(texcolour.build_with_rerolls(77, True, _fake([0.1, 0.2, 0.8], seen), aside.append, lambda n: None))
    assert seen == [(0, 77), (1, 77), (2, texcolour.try_seed(77, 1))]
    assert best == 2 and len(tries) == texcolour.MAX_TRIES
    assert [t["kind"] for t in tries] == ["first", "texture_only", "full"]
    assert aside == [0, 1]


def test_best_try_kept_when_every_try_fails():
    seen: list[tuple[int, int]] = []
    back: list[int] = []
    best, tries = asyncio.run(texcolour.build_with_rerolls(77, True, _fake([0.1, 0.45, 0.3], seen), lambda n: None, back.append))
    assert best == 1  # highest ratio among the failures, the texture-only try
    assert back == [1]
    assert texcolour.check(texcolour.record(best, tries))["ok"] is False


def test_nothing_to_compare_builds_once_even_with_texture_tries():
    seen: list[tuple[int, int]] = []
    best, tries = asyncio.run(texcolour.build_with_rerolls(5, False, _fake([0.1, 0.9, 0.9], seen), lambda n: None, lambda n: None))
    assert best == 0 and len(tries) == 1 and seen == [(0, 5)]
