"""Which card draws the pictures that are sharpened before painting: a card whose engine is stopped or unreachable is
passed over for the next card that may draw pictures, a card that fails part way hands the picture to the other card
once, and a single-card setup keeps working."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from vgac import prefs, srcproject
from vgac.stages import face_detail


class _Client:
    def __init__(self, up: bool = True, fail_after: int | None = None):
        self.up = up
        self.fail_after = fail_after
        self.staged = 0

    async def vram_summary(self):
        return {"reachable": self.up}


class _SC:
    def __init__(self, settings, tmp: Path, clients: dict[str, _Client]):
        self.settings = settings
        self.asset = SimpleNamespace(category="environment", style_preset="lowpoly_flat")
        self.version = SimpleNamespace(id="v" * 36)
        self.ctx = SimpleNamespace(clients=clients)
        self.clients = clients
        self.tmp = tmp
        self.used: list[str] = []

    def client(self, inst):
        return self.clients[inst]

    def stage_version(self, stage):
        return None

    def prefix(self, name):
        return name

    def use_model(self, m):
        pass

    async def stage_input(self, p, name, instance=None):
        c = self.clients[instance]
        if not c.up or (c.fail_after is not None and c.staged >= c.fail_after):
            raise ConnectionError("All connection attempts failed")
        c.staged += 1
        self.used.append(instance)
        return name

    async def comfy(self, graph, instance=None, message=""):
        return {"card": instance}

    async def download_image(self, entry, key, dest, instance=None):
        Image.new("RGB", (64, 64), (10, 20, 30)).save(dest)
        return dest


def _pictures(tmp: Path, n: int = 3) -> dict[str, Path]:
    out = {}
    for i in range(n):
        p = tmp / f"pic{i}.png"
        Image.new("RGB", (32 + i, 32 + i), (200, 100, 50)).save(p)
        out[f"v{i}"] = p
    return out


def test_the_preferred_card_comes_first_and_the_other_follows(settings, tmp_path):
    sc = _SC(settings, tmp_path, {"a": _Client(), "b": _Client()})
    prefs.update(settings, default_instance="a")
    assert face_detail.image_instances(sc)[:2] == ["a", "b"]
    assert face_detail.image_instance(sc) == "a"


def test_a_card_that_is_turned_off_is_not_offered(settings, tmp_path):
    prefs.set_disabled_instances(["a"], settings)
    sc = _SC(settings, tmp_path, {"a": _Client(), "b": _Client()})
    assert face_detail.image_instances(sc) == ["b"]


def test_a_stopped_card_is_passed_over(settings, tmp_path):
    prefs.update(settings, default_instance="a")
    sc = _SC(settings, tmp_path, {"a": _Client(up=False), "b": _Client()})
    assert asyncio.run(face_detail.live_image_instance(sc)) == "b"


def test_when_no_card_answers_the_preferred_one_is_kept_so_the_failure_is_reported(settings, tmp_path):
    prefs.update(settings, default_instance="a")
    sc = _SC(settings, tmp_path, {"a": _Client(up=False), "b": _Client(up=False)})
    assert asyncio.run(face_detail.live_image_instance(sc)) == "a"


def test_a_single_card_setup_still_works(settings, tmp_path):
    sc = _SC(settings, tmp_path, {"a": _Client()})
    assert asyncio.run(face_detail.live_image_instance(sc)) == "a"
    files = _pictures(tmp_path)
    out, info = asyncio.run(srcproject.upscale_sources(sc, files, tmp_path))
    assert info["upscaled"] == list(files) and not info.get("failed")
    assert set(sc.used) == {"a"}


def test_sharpening_goes_to_the_card_that_answers(settings, tmp_path):
    prefs.update(settings, default_instance="a")
    sc = _SC(settings, tmp_path, {"a": _Client(up=False), "b": _Client()})
    out, info = asyncio.run(srcproject.upscale_sources(sc, _pictures(tmp_path), tmp_path))
    assert not info.get("failed") and len(info["upscaled"]) == 3
    assert set(sc.used) == {"b"}


def test_a_card_that_fails_part_way_hands_the_picture_to_the_other_card_once(settings, tmp_path):
    prefs.update(settings, default_instance="a")
    sc = _SC(settings, tmp_path, {"a": _Client(fail_after=1), "b": _Client()})
    out, info = asyncio.run(srcproject.upscale_sources(sc, _pictures(tmp_path), tmp_path))
    assert not info.get("failed") and len(info["upscaled"]) == 3
    assert sc.used == ["a", "b", "b"]


def test_when_both_cards_fail_each_picture_is_reported_and_used_as_it_is(settings, tmp_path):
    prefs.update(settings, default_instance="a")
    sc = _SC(settings, tmp_path, {"a": _Client(up=False), "b": _Client(up=False)})
    files = _pictures(tmp_path)
    out, info = asyncio.run(srcproject.upscale_sources(sc, files, tmp_path))
    assert set(info["failed"]) == set(files) and info["upscaled"] == []
    assert out == files
