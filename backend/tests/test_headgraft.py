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


# --- the ring across the neck -----------------------------------------------------------------------------------
def _tube(n_around: int, n_up: int, radius, y0: float, y1: float, centre=(0.0, 0.0)):
    """An open tube round the y axis (radius a number or a function of the angle), outward-facing triangles, and
    texture positions (u round, v up)."""
    a = np.linspace(-np.pi, np.pi, n_around, endpoint=False) + 0.01
    ys = np.linspace(y0, y1, n_up + 1)
    rr = radius(a) if callable(radius) else np.full(len(a), radius)
    verts = np.array([[centre[0] + r * np.cos(t), y, centre[1] + r * np.sin(t)] for y in ys for t, r in zip(a, rr)])
    uv = np.array([[i / n_around, j / n_up] for j in range(n_up + 1) for i in range(n_around)])
    faces = []
    for j in range(n_up):
        for i in range(n_around):
            p, q = j * n_around + i, j * n_around + (i + 1) % n_around
            faces += [(p, (j + 1) * n_around + i, q), (q, (j + 1) * n_around + i, (j + 1) * n_around + (i + 1) % n_around)]
    return verts, uv, np.array(faces)


def _area(v, f):
    return float(np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1).sum() / 2)


def test_split_cuts_exactly_at_the_height_and_keeps_the_winding():
    v, uv, f = _tube(24, 6, 1.0, 0.0, 1.0)
    y = 0.43
    cross = (v[f][:, :, 1].min(axis=1) < y) & (v[f][:, :, 1].max(axis=1) > y)
    v2, uv2, below, parent = headgraft._split(v, uv, f[cross], y, keep_above=False)
    _, _, above, _ = headgraft._split(v, uv, f[cross], y, keep_above=True)
    assert len(parent) == len(below) and parent.max() < cross.sum()
    assert (v2[below][:, :, 1] <= y + 1e-12).all()
    assert abs(_area(v2, below) + _area(headgraft._split(v, uv, f[cross], y, True)[0], above) - _area(v, f[cross])) < 1e-9
    new = np.arange(len(v), len(v2))
    assert np.allclose(v2[new, 1], y) and len(new) == 48  # one new vertex per cut edge (24 upright, 24 slanted)
    n_old = np.cross(v[f[cross][parent]][:, 1] - v[f[cross][parent]][:, 0], v[f[cross][parent]][:, 2] - v[f[cross][parent]][:, 0])
    n_new = np.cross(v2[below][:, 1] - v2[below][:, 0], v2[below][:, 2] - v2[below][:, 0])
    assert ((n_old * n_new).sum(axis=1) > 0).all()


def test_strip_closes_the_gap_between_two_rings_of_different_counts():
    bv, buv, bf = _tube(40, 4, 1.0, 0.0, 1.0)
    hv, huv, hf = _tube(23, 4, 0.9, 0.9, 2.0, centre=(0.05, 0.0))
    cb = (bv[bf][:, :, 1].min(axis=1) < 0.6) & (bv[bf][:, :, 1].max(axis=1) > 0.6)
    ch = (hv[hf][:, :, 1].min(axis=1) < 1.3) & (hv[hf][:, :, 1].max(axis=1) > 1.3)
    bv2, buv2, b_new, b_par = headgraft._split(bv, buv, bf[cb], 0.6, keep_above=False)
    hv2, _, h_new, _ = headgraft._split(hv, huv, hf[ch], 1.3, keep_above=True)
    sv, suv, sf = headgraft._strip(bv2, buv2, b_new, bf[cb][b_par], len(bv), hv2, h_new, len(hv), (0.0, 0.0))
    assert len(sf) == 2 * 40 + 2 * 23  # one triangle per ring edge (two cut edges per square of each tube)
    # joined by position, the strip and both rings' cut edges make a closed band: every edge used twice
    allv = np.concatenate([bv2, hv2, sv])
    faces = np.concatenate([b_new, h_new + len(bv2), sf + len(bv2) + len(hv2)])
    _, w = np.unique(np.round(allv * 1e9), axis=0, return_inverse=True)
    w = w.reshape(-1)[faces]
    e = np.sort(np.concatenate([w[:, [0, 1]], w[:, [1, 2]], w[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    ring_lo, ring_hi = bv2[:, 1].min(), hv2[:, 1].max()
    assert (counts <= 2).all()
    # outward-facing, and reading the body's texture inside its own triangles
    tri = sv[sf]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    c = tri.mean(axis=1)
    assert ((n[:, 0] * c[:, 0] + n[:, 2] * c[:, 2]) > 0).all()
    assert (suv >= -1e-9).all() and (suv[:, 1] <= 1.0 + 1e-9).all()
    assert ring_lo < ring_hi


def test_ring_ratio_matches_each_vertex_to_the_body_surface_at_its_angle():
    a = np.linspace(-np.pi, np.pi, 30, endpoint=False)
    ring = np.stack([0.8 * np.cos(a), np.zeros_like(a), 0.8 * np.sin(a)], axis=1)
    b = np.linspace(-np.pi, np.pi, 200, endpoint=False)
    inner = np.stack([1.0 * np.cos(b), np.zeros_like(b), 1.0 * np.sin(b)], axis=1)
    outer = np.stack([1.3 * np.cos(b), np.zeros_like(b), 1.3 * np.sin(b)], axis=1)  # a shirt collar further out
    ang, q = headgraft._ring_ratio(ring, np.concatenate([inner, outer]), (0.0, 0.0))
    assert np.all(np.diff(ang) > 0)
    assert np.allclose(q, 1.0 / 0.8, atol=1e-3)


def test_neck_level_finds_the_narrow_round_part():
    # a neck (radius 0.5) between shoulders (wide, open at the front) and a head (radius 0.8)
    nv, nuv, nf = _tube(36, 6, 0.5, 0.0, 1.0)
    sv, suv, sf = _tube(36, 2, lambda t: 1.6 + 0.0 * t, -1.0, 0.0)
    sf = sf[np.sin(sv[sf][:, :, 2].mean(axis=1) / 1.6 * np.pi) < 0.9]  # a gap at the front
    hv, huv, hf = _tube(36, 4, 0.8, 1.0, 2.0)
    v = np.concatenate([sv, nv, hv])
    f = np.concatenate([sf, nf + len(sv), hf + len(sv) + len(nv)])
    level = headgraft._neck_level(v, f, -0.8, 1.8, (0.0, 0.0), head_width=1.8)
    assert level is not None
    _, y, axis, r = level
    assert 0.0 < y < 1.0 and np.allclose(r, 0.5, atol=0.02) and np.hypot(*axis) < 0.02


def test_facing_out_tells_the_skin_from_a_thin_shells_inner_wall():
    v, _, f = _tube(24, 4, 1.0, 0.0, 1.0)
    assert headgraft._facing_out(v, f, (0.0, 0.0)).all()
    assert not headgraft._facing_out(v, f[:, ::-1], (0.0, 0.0)).any()


def test_ring_is_the_outer_wall_of_a_thin_shell():
    # a neck built as a thin shell: an inner wall facing the axis just inside the skin
    vo, _, fo = _tube(36, 4, 1.0, 0.0, 1.0)
    vi, _, fi = _tube(36, 4, 0.9, 0.0, 1.0)
    v = np.concatenate([vo, vi])
    f = np.concatenate([fo, fi[:, ::-1] + len(vo)])
    lo, hi = v[f][:, :, 1].min(axis=1), v[f][:, :, 1].max(axis=1)
    cross = (lo < 0.6) & (hi > 0.6)
    pairs = headgraft._face_pairs(v, f)
    out = headgraft._facing_out(v, f, (0.0, 0.0))
    ring = headgraft._ring_faces(v, f, cross & out, (0.0, 0.0), pairs)
    assert ring.any() and (ring <= (np.arange(len(f)) < len(fo))).all()
    # without the facing rule the innermost wall wins, which nobody sees
    inner = headgraft._ring_faces(v, f, cross, (0.0, 0.0), pairs)
    assert (inner <= (np.arange(len(f)) >= len(fo))).all()
