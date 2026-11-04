"""Stage helpers that run image work off the event loop, the decoded-alpha cache, and turnaround's explicit source.

The stages run against a small fake StageContext: no database, no ComfyUI. The fakes record what the stage sees, so
the tests can check that refine and adjust never rewrite parent_version_id (audit B5) and that the threaded image
work (audit P2) still feeds the same checks and metrics.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from vgac import imaging
from vgac.stages import material, turnaround


def _rgba(path: Path, w=64, h=64, box=(16, 8, 48, 56), colour=(200, 50, 50)) -> Path:
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), colour + (255,))
    im.paste(px, box[:2])
    im.save(path)
    return path


# --- imaging cache (P6) -------------------------------------------------------------


def test_silhouette_and_bbox_decode_once_and_follow_rewrites(tmp_path):
    imaging.clear_cache()
    p = _rgba(tmp_path / "a.png")
    s1 = imaging.silhouette(p, 64)
    stats = imaging.alpha_bbox(p)
    s2 = imaging.silhouette(p, 64)
    assert imaging._alpha_cached.cache_info().misses == 1  # one decode for silhouette, bbox and the repeat
    assert imaging._silhouette_cached.cache_info().hits == 1
    assert np.array_equal(s1, s2) and s1 is not s2
    s1[:] = False  # callers get their own copy; the cached array is untouched
    assert imaging.silhouette(p, 64).any()
    assert stats["width"] == 32 and stats["height"] == 48 and stats["image_size"] == [64, 64]
    assert stats["alpha_corner"] == 0 and stats["alpha_centre"] == 255

    # Rewriting the file (new size and mtime) is a new cache entry, not a stale hit.
    _rgba(p, w=80, h=80, box=(0, 0, 80, 40))
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    stats2 = imaging.alpha_bbox(p)
    assert stats2["width"] == 80 and stats2["height"] == 40 and stats2["image_size"] == [80, 80]
    imaging.clear_cache()


def test_alpha_bbox_of_rgb_and_empty_images(tmp_path):
    rgb = tmp_path / "rgb.png"
    Image.new("RGB", (20, 10), (1, 2, 3)).save(rgb)
    assert imaging.alpha_bbox(rgb)["coverage"] == 1.0
    empty = tmp_path / "empty.png"
    Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(empty)
    assert imaging.alpha_bbox(empty)["empty"] is True
    assert not imaging.silhouette(empty, 32).any()


# --- turnaround source version (B5) and threaded checks (P2) --------------------------


class FakeDB:
    def __init__(self, *versions):
        self.rows = {v.id: v for v in versions}

    def get(self, _cls, vid):
        return self.rows.get(vid)


class FakeSC:
    def __init__(self, tmp: Path, version, db: FakeDB):
        self.dir = tmp
        self.settings = None
        self.version = version
        self.db = db
        self.files: dict[str, Path] = {}
        self.staged: list[Path] = []
        self.parent_seen: set[str | None] = set()
        self.metrics: dict = {}

    @property
    def parent(self):
        return self.db.get(None, self.version.parent_version_id) if self.version.parent_version_id else None

    async def stage_input(self, src, name):
        self.staged.append(Path(src))
        return name

    def use_model(self, _m):
        pass

    def progress(self, *_a):
        self.parent_seen.add(self.version.parent_version_id)

    def register(self, key, path):
        self.parent_seen.add(self.version.parent_version_id)
        self.files[key] = Path(path)

    def file(self, key):
        return self.files.get(key)

    def copy_in(self, src, name):
        raise AssertionError("no files to copy in these tests")

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.parent_seen.add(self.version.parent_version_id)
        self.metrics.update(kw)


def _lineage(tmp_path):
    concept_img = _rgba(tmp_path / "concept.png")
    concept = SimpleNamespace(id="c" * 32, parent_version_id=None, stage="concept", params={}, metrics={}, image=concept_img)
    ta = SimpleNamespace(id="t" * 32, parent_version_id=concept.id, stage="turnaround",
                         params={"views": ["front", "left", "back", "right"], "lora_strength": 0.8},
                         metrics={"views": {"front": {"passed": True}, "back": {"passed": True}}}, image=None)
    new = SimpleNamespace(id="n" * 32, parent_version_id=ta.id, stage="turnaround", params={}, metrics={}, image=None)
    return concept, ta, new


def _patch_turnaround(monkeypatch):
    monkeypatch.setattr(turnaround.library, "version_file", lambda v, key, settings: v.image if key == "image" else None)

    async def fake_generate(sc, src_name, view, seed, lora_strength, extra, base, span):
        raw = _rgba(sc.dir / f"{view}_raw.png")
        cut = _rgba(sc.dir / f"{view}.png")
        white = imaging.white_composite(cut)
        return {"raw": raw, "cutout": cut, "white": white, "stats": None}

    monkeypatch.setattr(turnaround, "_generate_view", fake_generate)


def test_turnaround_refine_uses_concept_without_touching_parent(tmp_path, monkeypatch):
    _patch_turnaround(monkeypatch)
    concept, ta, new = _lineage(tmp_path)
    sc = FakeSC(tmp_path, new, FakeDB(concept, ta, new))
    asyncio.run(turnaround.refine(sc, "red cape", "left", None, {}))
    assert sc.staged == [concept.image]
    assert sc.parent_seen == {ta.id} and new.parent_version_id == ta.id
    views = sc.metrics["views"]
    assert set(views) == {"front", "back", "left"} and views["left"]["passed"]
    assert new.params["refine_of"] == ta.id and new.params["refined_view"] == "left"


def test_turnaround_adjust_uses_concept_and_mirror_checks(tmp_path, monkeypatch):
    _patch_turnaround(monkeypatch)
    concept, ta, new = _lineage(tmp_path)
    sc = FakeSC(tmp_path, new, FakeDB(concept, ta, new))
    asyncio.run(turnaround.adjust(sc, {"views": ["front", "left", "right"], "seed": 7}))
    assert sc.staged == [concept.image]
    assert sc.parent_seen == {ta.id} and new.parent_version_id == ta.id
    views = sc.metrics["views"]
    assert views["right"]["mirror_iou"] > 0.99 and views["left"]["mirror_iou"] > 0.99
    assert views["left"]["hist_dist"] == 0.0 and views["left"]["height_ratio"] == 1.0
    assert sc.metrics["consistency_ok"] is True


def test_turnaround_adjust_without_concept_parent_still_errors(tmp_path, monkeypatch):
    _patch_turnaround(monkeypatch)
    concept, ta, new = _lineage(tmp_path)
    ta.parent_version_id = None
    sc = FakeSC(tmp_path, new, FakeDB(ta, new))
    try:
        asyncio.run(turnaround.adjust(sc, {}))
    except ValueError as e:
        assert "concept parent" in str(e)
    else:
        raise AssertionError("expected ValueError")
    assert new.parent_version_id == ta.id


# --- material mesh stage off the loop (P2) ----------------------------------------------


def test_material_mesh_run_in_thread(tmp_path, monkeypatch):
    src = tmp_path / "surface.png"
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).save(src)
    version = SimpleNamespace(id="m" * 32, parent_version_id=None, stage="mesh", params={}, metrics={})
    sc = FakeSC(tmp_path, version, FakeDB(version))
    sc.budget = {"texture": 64}
    sc.lineage_file = lambda key, stages=None: src if key == "image" else None

    async def no_judge(_sc):
        pass

    monkeypatch.setattr(material, "judge", no_judge)
    asyncio.run(material.mesh_run(sc, {}))
    assert {"base_color", "normal", "roughness", "height", "metallic"} <= set(sc.files)
    assert sc.metrics["size"] == [64, 64] and sc.metrics["textured"] is True
    assert isinstance(sc.metrics["seam_error"], float)
