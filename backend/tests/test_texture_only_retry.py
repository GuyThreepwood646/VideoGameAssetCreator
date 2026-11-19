"""The texture seed in the TRELLIS.2 graph (graphs/trellis.py texture_seed) and the texture-only retry route in
texcolour.py. UNTESTED ON A CARD: the WP-11 trial has not run, so the route is off by default and stays so until it passes.
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
    explicit = convert_graph("in.png", "pfx", seed=42, texture_seed=43)
    assert _digest(explicit) == _digest(base)
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


def test_texture_only_keeps_the_shape_seed_on_every_build():
    seen: list[int] = []

    async def build(seed: int, n: int) -> dict:
        seen.append(seed)
        return {"seed": seed, "match": {"ratio": 0.2 + 0.1 * n, "ok": n >= 2}}

    best, tries = asyncio.run(texcolour.build_with_rerolls(77, True, build, lambda n: None, lambda n: None,
                                                           texture_only=True))
    assert seen == [77, 77, 77]
    assert best == 2 and len(tries) == 3
