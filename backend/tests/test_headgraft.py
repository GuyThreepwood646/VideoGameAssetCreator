"""The head built on its own (vgac/headgraft.py, stages/mesh.py _head_build, docs/HEAD-BUILD-PLAN.md)."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
from PIL import Image

from vgac import guidance, headgraft, jobs
from vgac.stages import mesh as mesh_stage

KNIGHT = Path(__file__).resolve().parents[2] / "bakeoff" / "out" / "texture_trials"


# --- when it runs -----------------------------------------------------------------------------------------------
def test_wanted_only_for_people_on_the_bald_route_with_trellis():
    assert headgraft.wanted("humanoid", {}, True, "trellis")
    assert not headgraft.wanted("humanoid", {}, False, "trellis")  # hair built with the model: tight crops came out flat
    assert not headgraft.wanted("quadruped", {}, True, "trellis")
    assert not headgraft.wanted("humanoid", {}, True, "hy3d")
    assert not headgraft.wanted("humanoid", {"head_build": False}, True, "trellis")


def test_crop_share_defaults_and_bounds():
    assert headgraft.crop_share({}) == headgraft.CROP_SHARE
    assert headgraft.crop_share({"head_crop": 0.05}) == headgraft.CROP_RANGE[0]
    assert headgraft.crop_share({"head_crop": "0.9"}) == headgraft.CROP_RANGE[1]
    assert headgraft.crop_share({"head_crop": "abc"}) == headgraft.CROP_SHARE


# --- the crop ---------------------------------------------------------------------------------------------------
def _figure(path: Path, w: int = 400, h: int = 800) -> Path:
    """A standing figure on a clear background: a round head at the top, a wide body below."""
    a = np.zeros((h, w, 4), dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    head = (xx - 240) ** 2 + (yy - 120) ** 2 < 60 ** 2  # off centre on purpose: the crop follows the head
    body = (np.abs(xx - 240) < 120) & (yy > 170) & (yy < 780)
    a[head | body] = (200, 170, 150, 255)
    Image.fromarray(a, "RGBA").save(path)
    return path


def test_crop_frames_the_head_with_a_clear_background(tmp_path):
    rec = headgraft.crop(_figure(tmp_path / "bald.png"), tmp_path / "head.png", 0.26)
    out = np.asarray(Image.open(tmp_path / "head.png"))
    assert out.shape == (1024, 1024, 4)
    alpha = out[..., 3] > 127
    assert alpha.mean() > 0.2 and not alpha[:20].any()  # the figure fills it, a little room above the head
    assert (out[~alpha][:, :3] == 0).all()  # fully clear: the builder builds whatever is not
    cols = np.where(alpha[:300].any(axis=0))[0]
    assert abs((cols[0] + cols[-1]) / 2 - 512) < 40  # centred on the head, not on the picture
    assert rec["share"] == 0.26


def test_crop_refuses_a_picture_without_a_figure(tmp_path):
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(tmp_path / "empty.png")
    with pytest.raises(ValueError):
        headgraft.crop(tmp_path / "empty.png", tmp_path / "out.png")


# --- the flat-board check ---------------------------------------------------------------------------------------
def _box_glb(path: Path, extents: tuple[float, float, float]) -> Path:
    m = trimesh.creation.box(extents=extents)
    m.visual = trimesh.visual.TextureVisuals(uv=np.random.default_rng(0).random((len(m.vertices), 2)))
    m.export(str(path))
    return path


def test_depth_ratio_tells_a_flat_board_from_a_bust(tmp_path):
    assert headgraft.depth_ratio(_box_glb(tmp_path / "board.glb", (1.0, 1.0, 0.024))) < headgraft.FLAT_RATIO
    assert headgraft.depth_ratio(_box_glb(tmp_path / "bust.glb", (0.9, 1.0, 0.44))) > headgraft.FLAT_RATIO


# --- the side outline -------------------------------------------------------------------------------------------
def _profile_mask() -> np.ndarray:
    """A side silhouette facing left: skull, nose bump, lips, chin, a neck recess and a collar that comes forward."""
    S = 512
    m = np.zeros((S, S), dtype=bool)
    reach = np.zeros(S)  # how far left of x=400 the outline reaches per row
    for y in range(S):
        if y < 40:
            r = 0.0
        elif y < 200:
            r = 150 * np.sqrt(max(0.0, 1 - ((y - 200) / 160) ** 2))
        elif y < 400:
            r = 150.0
        else:
            r = 0.0
        if 200 <= y < 230:
            r += 30 * np.sin(np.pi * (y - 200) / 30)  # the nose
        if 240 <= y < 270:
            r += 8  # the lips
        if 280 <= y < 320:
            r += 12  # the chin
        if 330 <= y < 370:
            r -= 60  # the neck
        if 370 <= y < 400:
            r += 10  # a collar, forward of the neck
        reach[y] = r
        if r > 0:
            m[y, int(400 - r): 420] = True
    return m


def test_profile_keys_find_the_keystones_in_order_and_stop_before_the_collar():
    k = headgraft.profile_keys(_profile_mask(), faces_left=True)
    assert 40 <= k["top"] <= 42
    assert 205 <= k["tip"] <= 225
    assert k["chin_front"] is not None and 280 <= k["chin_front"] < 325
    assert k["chin_bottom"] is not None and k["chin_front"] < k["chin_bottom"] <= 345
    assert k["neck"] is not None and 330 <= k["neck"] < 372  # the neck, not the collar below it


def test_head_colours_count_hair_only_when_the_crown_is_not_skin():
    skin = np.array([60.0, 18.0, 22.0])
    grey = np.array([[55.0, 1.0, 4.0]])  # armour
    # bald: the crown is skin, so a pale grey near it is not taken for hair
    assert not headgraft.is_head_colour(grey, np.stack([skin, np.array([70.0, 7.0, 16.0])]))[0]
    # dark hair: hair counts, grey armour still does not
    hair = np.array([14.0, 2.0, 2.0])
    refs = np.stack([skin, hair])
    assert headgraft.is_head_colour(np.array([[16.0, 2.0, 3.0]]), refs)[0]
    assert not headgraft.is_head_colour(grey, refs)[0]
    assert headgraft.is_head_colour(np.array([[55.0, 17.0, 21.0]]), refs)[0]  # shaded skin


# --- the check --------------------------------------------------------------------------------------------------
def _grid(n: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """A flat n x n grid of squares, two triangles each."""
    xs, ys = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
    verts = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1).astype(float)
    faces = []
    for j in range(n):
        for i in range(n):
            a_, b_, c_, d_ = j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i, (j + 1) * (n + 1) + i + 1
            faces += [(a_, b_, d_), (a_, d_, c_)]
    return verts, np.array(faces)


def test_tidy_drops_loose_fragments_and_fills_small_holes():
    verts, faces = _grid(20)
    cx = verts[faces].mean(axis=1)
    keep = cx[:, 1] < 12  # the bottom part kept
    hole = (np.abs(cx[:, 0] - 5) < 1.5) & (np.abs(cx[:, 1] - 5) < 1.5)  # a small hole inside it
    shard = (np.abs(cx[:, 0] - 15) < 1) & (np.abs(cx[:, 1] - 17) < 1)  # a loose piece above the cut
    out = headgraft.tidy(verts, faces, (keep & ~hole) | shard, only_largest=False)
    assert out[hole].all()
    assert not out[shard].any()
    assert (out == keep).all()
    # a big cut-away part is not a hole: it stays dropped
    assert not out[cx[:, 1] > 13].any()


def test_tidy_keeps_only_the_largest_piece_of_the_head():
    verts, faces = _grid(20)
    cx = verts[faces].mean(axis=1)
    keep = (cx[:, 1] < 12) | (cx[:, 1] > 15)
    out = headgraft.tidy(verts, faces, keep, only_largest=True)
    assert out[cx[:, 1] < 12].all() and not out[cx[:, 1] > 15].any()


def test_collar_edge_found_round_a_neck_and_not_on_an_open_neck():
    rng = np.random.default_rng(0)
    a = rng.uniform(-np.pi, np.pi, 4000)
    r = rng.uniform(0.05, 0.08, 4000)
    y = rng.uniform(0.0, 0.1, 4000) + 0.03 * (np.sin(a) > 0)  # higher at the back (z positive)
    pts = np.stack([r * np.cos(a), y, r * np.sin(a)], axis=1)
    grey = np.ones(len(pts), dtype=bool)
    edge = headgraft.collar_edge(pts, grey, (0.0, 0.0), chin=0.12, tip=0.2, H=0.2, head_width=0.15)
    assert edge is not None and len(edge["top"]) == headgraft.COLLAR_BINS
    assert edge["top"].max() > 0.12 and edge["top"].min() < 0.11  # the back rides higher
    assert np.all((edge["inner"] > 0.045) & (edge["inner"] < 0.07))
    assert headgraft.collar_edge(pts, ~grey, (0.0, 0.0), chin=0.12, tip=0.2, H=0.2, head_width=0.15) is None


def test_head_build_check_wording():
    assert mesh_stage.head_build_check({}) is None
    ok = mesh_stage.head_build_check({"head_build": {"wanted": True, "used": True}})
    assert ok["ok"] and "joined to the body at the neck" in ok["help"]
    bad = mesh_stage.head_build_check({"head_build": {"wanted": True, "used": False, "reason": "flat"}})
    assert not bad["ok"] and "the body's own head is used: it came out flat" in bad["help"]
    assert guidance.CHECK_GUIDE["head_build"]["severity"] == "warn"


# --- the 3D step with the builds mocked -------------------------------------------------------------------------
class _SC:
    def __init__(self, d: Path, params: dict):
        self.dir = d
        self.asset = SimpleNamespace(category="humanoid", id="asset0001")
        self.category = {"input_view_default": "concept"}
        self.budget = {"triangles": 20000, "texture": 2048}
        self.version = SimpleNamespace(params=dict(params), metrics={}, id="ver000001")
        self.registered: dict = {}
        self.messages: list = []
        self.blender_calls: list = []
        self.settings = SimpleNamespace(blender_timeout_seconds=60)

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    async def stage_input(self, _p, name):
        return name

    def register(self, key, p):
        self.registered[key] = Path(p)

    def progress(self, _f=None, msg=None):
        if msg:
            self.messages.append(msg)

    async def blender(self, script, args, log_name=None, timeout=0):
        self.blender_calls.append(script)
        out = Path(args[1])
        out.write_text("joined glb")
        Image.new("RGB", (8, 8), (1, 2, 3)).save(out.parent / "base_color.png")
        return ""


@pytest.fixture
def head_stage(tmp_path, monkeypatch):
    state = {"depth": [0.4], "graft": {"ok": True, "reason": None, "metrics": {"width_ratio": 0.97}}}

    async def fake_build(sc, name, n, seed, *a, dest=None, tag="", **k):
        out = Path(dest)
        (out / "model.glb").write_text(f"head {seed}")
        Image.new("RGB", (8, 8)).save(out / "base_color.png")
        return {"seed": seed, "elapsed": 1.0, "maps": {"base_color": out / "base_color.png"}, "other": [], "metrics": {}, "match": None}

    depths = iter(())

    def fake_depth(_glb):
        return next(depths)

    def fake_graft(body_glb, body_maps, head_glb, head_maps, out_dir):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        g = dict(state["graft"])
        if g["ok"]:
            (Path(out_dir) / "graft.glb").write_text("graft")
            g["glb"] = Path(out_dir) / "graft.glb"
            g["maps"] = {"base_color": Path(out_dir) / "graft_base_color.png"}
        return g

    monkeypatch.setattr(mesh_stage, "_trellis_build", fake_build)
    monkeypatch.setattr(mesh_stage.headgraft, "graft", fake_graft)
    monkeypatch.setattr(mesh_stage.headgraft, "depth_ratio", fake_depth)
    monkeypatch.setattr(mesh_stage, "finish_maps", lambda glb, maps, tex: {"atlas_used": 0.37})

    def run():
        nonlocal depths
        depths = iter(state["depth"])
        d = tmp_path / f"v{len(list(tmp_path.glob('v*')))}"
        d.mkdir()
        (d / "model.glb").write_text("body glb")
        Image.new("RGB", (8, 8), (9, 9, 9)).save(d / "base_color.png")
        src = _figure(d / "bald.png")
        sc = _SC(d, {})
        asyncio.run(mesh_stage._head_build(sc, sc.version.params, src, 7, 20000, 2048, 1024, True, 8, True, False))
        return sc

    return state, run


def test_joined_model_takes_the_body_builds_place(head_stage):
    state, run = head_stage
    sc = run()
    hb = sc.version.metrics["head_build"]
    assert hb["used"] and hb["reason"] is None and hb["seed"] == 7
    assert (sc.dir / "model.glb").read_text() == "joined glb"
    assert (sc.dir / mesh_stage.BODY_DIR / "model.glb").read_text() == "body glb"  # the body build kept beside it
    assert sc.blender_calls == ["headjoin.py"]
    assert {"glb", "base_color", "body_glb", "head_glb", "head_input"} <= set(sc.registered)
    assert sc.version.params["head_build"] is True and sc.version.params["head_crop"] == headgraft.CROP_SHARE


def test_a_flat_head_is_built_once_more_then_the_body_head_stays(head_stage):
    state, run = head_stage
    state["depth"] = [0.02, 0.01]
    sc = run()
    hb = sc.version.metrics["head_build"]
    assert not hb["used"] and hb["reason"] == "flat" and len(hb["tries"]) == 2
    assert hb["tries"][0]["seed"] != hb["tries"][1]["seed"]
    assert (sc.dir / "model.glb").read_text() == "body glb" and not sc.blender_calls


def test_a_flat_first_try_and_a_good_second_try_is_used(head_stage):
    state, run = head_stage
    state["depth"] = [0.02, 0.4]
    sc = run()
    hb = sc.version.metrics["head_build"]
    assert hb["used"] and hb["seed"] == hb["tries"][1]["seed"]


def test_a_head_that_does_not_fit_keeps_the_body_head(head_stage):
    state, run = head_stage
    state["graft"] = {"ok": False, "reason": "size", "metrics": {"width_ratio": 1.49}}
    sc = run()
    hb = sc.version.metrics["head_build"]
    assert not hb["used"] and hb["reason"] == "size", hb
    assert hb["width_ratio"] == 1.49
    assert (sc.dir / "model.glb").read_text() == "body glb" and "glb" not in sc.registered


def test_stop_still_stops(head_stage, monkeypatch):
    state, run = head_stage

    async def stopped(*a, **k):
        raise jobs.JobCancelled()

    monkeypatch.setattr(mesh_stage, "_trellis_build", stopped)
    with pytest.raises(jobs.JobCancelled):
        run()


# --- the real graft on the bake-off knight (slow; only where the bake-off builds exist) --------------------------
@pytest.mark.skipif(not (KNIGHT / "knightbald_u1024_t4096" / "model.glb").is_file()
                    or not (KNIGHT / "knightbaldhead_u1024_t4096" / "model.glb").is_file(), reason="bake-off builds not here")
def test_graft_on_the_bald_knight(tmp_path):
    body, head = KNIGHT / "knightbald_u1024_t4096", KNIGHT / "knightbaldhead_u1024_t4096"
    maps = lambda d: {k: d / f"{k}.png" for k in headgraft.NEUTRAL if (d / f"{k}.png").is_file()}  # noqa: E731
    g = headgraft.graft(body / "model.glb", maps(body), head / "model.glb", maps(head), tmp_path)
    assert g["ok"], g
    m = g["metrics"]
    assert 0.9 < m["width_ratio"] < 1.05
    assert m["head_faces_kept"] > 1000 and m["body_faces_dropped"] > 300
    assert m["head_texture_share"] <= headgraft.HEAD_SHARE_MAX + 1e-6
    joined = trimesh.load(str(g["glb"]), force="mesh", process=False)
    assert len(joined.faces) == m["triangles"]
    sheet = Image.open(g["maps"]["base_color"])
    assert sheet.size[0] == sheet.size[1] == 2 * Image.open(body / "base_color.png").size[0]


@pytest.mark.skipif(not (KNIGHT / "knightbald_u1024_t4096" / "model.glb").is_file()
                    or not (KNIGHT / "knightbaldhead_u1024_t4096" / "model.glb").is_file(), reason="bake-off builds not here")
def test_graft_on_turned_builds_as_from_a_three_quarter_picture(tmp_path):
    """Step 3 builds people from the three-quarter bald view: both models come out turned. The graft turns them to
    face the front, joins them, and turns the result back by the body's own turn."""
    from vgac import face

    body, head = KNIGHT / "knightbald_u1024_t4096", KNIGHT / "knightbaldhead_u1024_t4096"
    maps = lambda d: {k: d / f"{k}.png" for k in headgraft.NEUTRAL if (d / f"{k}.png").is_file()}  # noqa: E731
    for d, turn, share, name in ((body, -38, 0.13, "body"), (head, -44, 0.45, "head")):
        tm = trimesh.load(str(d / "model.glb"), force="mesh", process=False)
        c = face.find_head(np.asarray(tm.vertices), share).centre
        tm.vertices = headgraft._turned(np.asarray(tm.vertices), turn, c)
        tm.export(str(tmp_path / f"{name}.glb"))
    g = headgraft.graft(tmp_path / "body.glb", maps(body), tmp_path / "head.glb", maps(head), tmp_path / "out")
    assert g["ok"], g
    assert abs(g["metrics"]["body_yaw"] - 38) <= 7 and abs(g["metrics"]["head_yaw"] - 44) <= 7
    assert 0.9 < g["metrics"]["width_ratio"] < 1.05
    joined = face.load_mesh(g["glb"])
    assert abs(headgraft.facing_yaw(joined, 0.13) - g["metrics"]["body_yaw"]) <= 4  # still turned like the body
