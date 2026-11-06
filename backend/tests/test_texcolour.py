"""Base colour against the picture (vgac/texcolour.py): the measure, the 3D step's builds again with a new seed when the
colour texture comes out too dark (stages/mesh.py), the texture_matches_picture check and the clean-up step's
albedo_range for looks that keep painted darks (stages/prep.py)."""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
from PIL import Image

from vgac import guidance, jobs, texcolour
from vgac.stages import mesh as mesh_stage
from vgac.stages import prep as prep_stage


def _picture(path: Path, colour: tuple[int, int, int], size: int = 64, background: tuple[int, int, int] = (0, 0, 0)) -> Path:
    """A cut-out: an opaque square of `colour` in the middle, the rest see-through over `background`."""
    a = np.zeros((size, size, 4), np.uint8)
    a[..., :3] = background
    a[size // 4: 3 * size // 4, size // 4: 3 * size // 4] = (*colour, 255)
    Image.fromarray(a, "RGBA").save(path)
    return path


def _quad_glb(path: Path) -> Path:
    """A square whose texture layout covers only the left half of the texture."""
    verts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    uv = np.array([[0.02, 0.02], [0.48, 0.02], [0.48, 0.98], [0.02, 0.98]])
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.new("RGB", (8, 8), (128, 128, 128)))
    trimesh.Trimesh(verts, faces, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False).export(path)
    return path


def _texture(path: Path, used: int, padding: int, size: int = 128) -> Path:
    """Left half (the texels the quad uses) one grey, right half (atlas padding and grown seams) another."""
    a = np.zeros((size, size, 3), np.uint8)
    a[:, : size // 2] = used
    a[:, size // 2:] = padding
    Image.fromarray(a).save(path)
    return path


# --- the measure ------------------------------------------------------------------------------------------------------


def test_bright_picture_and_dark_texture_fails(tmp_path):
    pic = _picture(tmp_path / "pic.png", (200, 180, 160))
    m = texcolour.measure(_texture(tmp_path / "base.png", 20, 20), _quad_glb(tmp_path / "m.glb"), pic)
    assert m["ok"] is False and m["ratio"] < 0.2
    assert m["picture_luma"] == pytest.approx(183.4, abs=1.0)  # the opaque square only, never the black background


def test_dark_picture_and_dark_texture_passes(tmp_path):
    pic = _picture(tmp_path / "pic.png", (24, 22, 20))
    glb = _quad_glb(tmp_path / "m.glb")
    m = texcolour.measure(_texture(tmp_path / "base.png", 22, 22), glb, pic)
    assert m["ok"] and m["ratio"] > 0.8
    # Even under half the brightness, a texture this close to a dark picture's passes (NEAR_LUMA), so it is never rebuilt.
    m = texcolour.measure(_texture(tmp_path / "base2.png", 11, 11), glb, pic)
    assert m["ratio"] < texcolour.RATIO_MIN and m["ok"]


def test_atlas_padding_and_background_are_ignored(tmp_path):
    glb = _quad_glb(tmp_path / "m.glb")
    # Bright where the model's layout is, black in the padding: the measure sees only the bright texels.
    m = texcolour.measure(_texture(tmp_path / "a.png", 180, 0), glb, _picture(tmp_path / "p.png", (180, 180, 180)))
    assert m["texture_luma"] == pytest.approx(180, abs=1) and m["ok"]
    # Black where the layout is, white padding all round: still too dark.
    m = texcolour.measure(_texture(tmp_path / "b.png", 10, 255), glb, _picture(tmp_path / "p2.png", (180, 180, 180), background=(255, 255, 255)))
    assert m["texture_luma"] == pytest.approx(10, abs=1) and m["picture_luma"] == pytest.approx(180, abs=1) and not m["ok"]


def test_no_cut_out_means_no_measure(tmp_path):
    rgb = tmp_path / "rgb.png"
    Image.new("RGB", (32, 32), (200, 200, 200)).save(rgb)
    opaque = tmp_path / "opaque.png"
    Image.new("RGBA", (32, 32), (200, 200, 200, 255)).save(opaque)
    assert texcolour.picture_stats(rgb) is None and texcolour.picture_stats(opaque) is None
    assert texcolour.picture_stats(tmp_path / "missing.png") is None
    glb = _quad_glb(tmp_path / "m.glb")
    assert texcolour.measure(_texture(tmp_path / "b.png", 10, 10), glb, rgb) is None
    assert texcolour.texture_stats(None, glb) is None


# --- builds again ------------------------------------------------------------------------------------------------------


def _fake_builds(ratios: list[float]):
    calls = {"seeds": [], "aside": [], "back": []}

    async def build_once(seed, n):
        calls["seeds"].append(seed)
        r = ratios[n]
        return {"seed": seed, "match": {"ratio": r, "ok": r >= texcolour.RATIO_MIN}}

    return calls, build_once


def test_first_dark_then_good_keeps_the_second():
    calls, build = _fake_builds([0.16, 0.67, 0.9])
    best, tries = asyncio.run(texcolour.build_with_rerolls(1234, True, build, calls["aside"].append, calls["back"].append))
    assert best == 1 and len(tries) == 2
    assert calls["seeds"] == [1234, texcolour.try_seed(1234, 1)] and calls["seeds"][1] != 1234
    assert calls["aside"] == [0] and calls["back"] == []
    rec = texcolour.record(best, tries)
    assert rec["texture_tries"] == 2 and rec["texture_try_kept"] == 2 and rec["texture_try_ratios"] == [0.16, 0.67]


def test_all_three_dark_keeps_the_closest_and_fails():
    calls, build = _fake_builds([0.16, 0.48, 0.29])
    best, tries = asyncio.run(texcolour.build_with_rerolls(99, True, build, calls["aside"].append, calls["back"].append))
    assert len(tries) == texcolour.MAX_TRIES == 3
    assert best == 1 and calls["aside"] == [0, 1] and calls["back"] == [1]
    c = texcolour.check(texcolour.record(best, tries))
    assert c["ok"] is False and c["severity"] == "block" and c["value"] == 0.48
    assert "built 3 times" in c["help"] and "Build it again" in c["help"]
    assert c["fix"] == {"stage": "mesh", "params": {"seed": None}, "label": "Build it again"}


def test_nothing_to_compare_builds_once():
    calls, build = _fake_builds([0.1, 0.9, 0.9])
    best, tries = asyncio.run(texcolour.build_with_rerolls(5, False, build, calls["aside"].append, calls["back"].append))
    assert best == 0 and len(tries) == 1 and calls["aside"] == []


def test_try_seeds_follow_from_the_first_and_stay_in_range():
    assert texcolour.try_seed(7, 0) == 7
    assert texcolour.try_seed(7, 1) == texcolour.try_seed(7, 1) != texcolour.try_seed(7, 2)
    big = 2**31 - 2
    assert all(1 <= texcolour.try_seed(big, n) < 2**31 for n in range(3))
    assert "seed" in jobs.RESUME_SEEDS  # a Resume carries the first seed, so the same builds follow


def test_check_wording_after_a_rebuild():
    c = texcolour.check({"texture_match": {"ratio": 0.67, "ok": True, "picture_luma": 98, "texture_luma": 66},
                         "texture_tries": 2, "texture_try_kept": 2})
    assert c["ok"] and "came out too dark, so the 3D model was built again" in c["help"] and "67%" in c["help"]
    assert "fix" not in c
    assert texcolour.check({}) is None


# --- the 3D step with the build mocked -------------------------------------------------------------------------------


class _SC:
    def __init__(self, d: Path, params: dict, category: str = "prop_rigid"):
        self.dir = d
        self.asset = SimpleNamespace(category=category, id="asset0001")
        self.category = {"input_view_default": "concept"}
        self.budget = {"triangles": 20000, "texture": 2048}
        self.version = SimpleNamespace(params=dict(params), metrics={}, id="ver000001")
        self.registered: dict = {}
        self.messages: list = []

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    def use_model(self, _m):
        pass

    async def stage_input(self, _p, name):
        return name

    def register(self, key, p):
        self.registered[key] = Path(p)

    def copy_in(self, src, name):
        shutil.copy2(src, self.dir / name)
        return self.dir / name

    def progress(self, _f=None, msg=None):
        if msg:
            self.messages.append(msg)

    def prefix(self, tag=""):
        return "p/" + tag


@pytest.fixture
def stage(tmp_path, monkeypatch):
    """The 3D step's _trellis with the TRELLIS build replaced: build n writes model.glb and base_color.png holding its
    seed, and its ratio comes from `ratios` (a dark picture's builds come out dark too)."""
    pic = _picture(tmp_path / "cutout.png", (200, 180, 160))
    monkeypatch.setattr(mesh_stage, "stage_recipe", lambda sc: {"mesh": {"normal_map": True}, "surface": {"texture_filter": "smooth"}})
    monkeypatch.setattr(mesh_stage, "_input_image", lambda sc, view: pic)
    monkeypatch.setattr(mesh_stage.reference, "recipe_record", lambda *a, **k: {})
    state = {"ratios": [0.16, 0.67, 0.9], "seeds": [], "stop_at": None}

    async def fake_build(sc, name, n, seed, *a, **k):
        if state["stop_at"] == n:
            raise jobs.JobCancelled()
        state["seeds"].append(seed)
        (sc.dir / "model.glb").write_text(f"glb {seed}")
        (sc.dir / "base_color.png").write_text(f"base {seed}")
        r = state["ratios"][n]
        return {"seed": seed, "elapsed": 100.0, "maps": {"base_color": sc.dir / "base_color.png"}, "other": [],
                "metrics": {"atlas_used": 0.5 + n / 100}, "match": {"ratio": r, "ok": r >= texcolour.RATIO_MIN, "picture_luma": 180, "texture_luma": 180 * r}}

    monkeypatch.setattr(mesh_stage, "_trellis_build", fake_build)

    def run(params, category="prop_rigid"):
        d = tmp_path / f"v{len(list(tmp_path.glob('v*')))}"
        d.mkdir()
        sc = _SC(d, params, category)
        state["seeds"] = []
        asyncio.run(mesh_stage._trellis(sc, sc.version.params))
        return sc

    return state, run


def test_stage_keeps_the_second_build_and_its_seed(stage):
    state, run = stage
    sc = run({"seed": 4242})
    assert state["seeds"][0] == 4242  # the person's seed is honoured for the first build
    second = texcolour.try_seed(4242, 1)
    assert sc.version.params["seed"] == second and sc.version.params["first_seed"] == 4242
    assert sc.version.metrics["seed"] == second and sc.version.metrics["texture_tries"] == 2
    assert (sc.dir / "base_color.png").read_text() == f"base {second}"
    assert not (sc.dir / mesh_stage.TRIES_DIR).exists()
    assert sc.registered["glb"] == sc.dir / "model.glb"
    assert any("built again" in m for m in sc.messages)


def test_stage_all_dark_keeps_the_closest_files(stage):
    state, run = stage
    state["ratios"] = [0.16, 0.48, 0.29]
    sc = run({"seed": 10})
    s2 = texcolour.try_seed(10, 1)
    assert sc.version.params["seed"] == s2 and sc.version.metrics["texture_try_kept"] == 2
    assert (sc.dir / "model.glb").read_text() == f"glb {s2}" and (sc.dir / "base_color.png").read_text() == f"base {s2}"
    assert sc.version.metrics["atlas_used"] == 0.51  # the kept build's own measures
    assert sc.version.metrics["elapsed_seconds_all"] == 300.0


def test_stage_good_first_build_stays_as_it_was(stage):
    state, run = stage
    state["ratios"] = [0.9]
    sc = run({"seed": 77})
    assert state["seeds"] == [77] and sc.version.params["seed"] == 77 and sc.version.params["first_seed"] is None
    assert sc.version.metrics["texture_tries"] == 1


def test_stage_dark_picture_is_not_rebuilt(stage, tmp_path, monkeypatch):
    """The ratio is relative: a dark picture and a dark texture pass, so the model is built once."""
    state, run = stage
    dark = _picture(tmp_path / "dark.png", (22, 20, 18))
    monkeypatch.setattr(mesh_stage, "_input_image", lambda sc, view: dark)
    pic = texcolour.picture_stats(dark)

    async def dark_build(sc, name, n, seed, *a, **k):
        state["seeds"].append(seed)
        (sc.dir / "model.glb").write_text("glb")
        return {"seed": seed, "elapsed": 1.0, "maps": {}, "other": [], "metrics": {},
                "match": texcolour.compare(pic, {"luma": pic["luma"] * 0.6, "dark": 1.0})}

    monkeypatch.setattr(mesh_stage, "_trellis_build", dark_build)
    sc = run({"seed": 3})
    assert state["seeds"] == [3] and sc.version.metrics["texture_match"]["ok"]


def test_stop_mid_rebuild_and_resume_builds_the_same_tries(stage):
    state, run = stage
    state["stop_at"] = 1
    with pytest.raises(jobs.JobCancelled):
        run({"seed": None})
    first = state["seeds"][0]
    # Stopped during the second build: the version still has the first seed, which a Resume carries.
    state["stop_at"] = None
    sc = run({"seed": first})
    assert state["seeds"] == [first, texcolour.try_seed(first, 1)]
    assert sc.version.params["seed"] == texcolour.try_seed(first, 1)


def test_stopped_version_keeps_the_first_seed(stage, tmp_path):
    state, run = stage
    state["stop_at"] = 1
    d = tmp_path / "stopped"
    d.mkdir()
    sc = _SC(d, {"seed": 555})
    with pytest.raises(jobs.JobCancelled):
        asyncio.run(mesh_stage._trellis(sc, sc.version.params))
    assert sc.version.params["seed"] == 555


# --- the clean-up step's albedo_range --------------------------------------------------------------------------------


def _albedo(metrics):
    return next(c for c in prep_stage.surface_checks({"export_mesh_hash": "x", **metrics}) if c["name"] == "albedo_range")


def test_albedo_range_kept_look_excused_only_while_as_bright_as_the_picture():
    base = {"pbr": {"albedo_out": 0.6, "albedo_black": 0.0, "albedo_white": 0.0}, "delight_mode": "keep"}
    # A Painted character 30 percent darker than its picture (Caped hero): excused, as before.
    assert _albedo({**base, "texture_match": {"ratio": 0.715, "ok": True}})["ok"]
    # A near-black texture from a bright picture: fails in a kept look too.
    c = _albedo({**base, "texture_match": {"ratio": 0.14, "ok": False}})
    assert not c["ok"] and not c["vars"]["kept"]
    # Nothing to compare (an older version, no cut-out): excused, as before.
    assert _albedo(base)["ok"] and _albedo({**base, "texture_match": None})["ok"]
    # A look that removes painted light is never excused.
    assert not _albedo({**base, "delight_mode": "remove", "texture_match": {"ratio": 1.0, "ok": True}})["ok"]


def test_check_guide_entry_reads_plainly():
    g = guidance.CHECK_GUIDE[texcolour.CHECK_ID]
    assert g["severity"] == "block" and g["fix_label"] == "Build it again"
