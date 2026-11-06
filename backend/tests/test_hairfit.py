"""Hair from the library, fit engine and picture maths (docs/HAIR-LIBRARY-PLAN.md lane 1): the numpy parts of
blender/hairfit.py, vgac/baldpic.py on the five trial pictures (256 px fixtures), the attach-format fixture, recolour,
check_values, and rank never raising. Blender itself is exercised by tools/hairlib_check.py, not here."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vgac import baldpic, hairfit, hairstyles

HERE = Path(__file__).resolve().parent
FIX = HERE / "data" / "hairlib_fixture"
PICS = FIX / "pictures"
CHARS = ("knight1", "knight2", "man", "ponytail", "mage")

_spec = importlib.util.spec_from_file_location("bl_hairfit", HERE.parent / "vgac" / "blender" / "hairfit.py")
hf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hf)


# --------------------------------------------------------------------------------------------- baldpic

def _pic(ch):
    return Image.open(PICS / ch / "concept.png"), Image.open(PICS / ch / "bald_cutout.png"), np.asarray(Image.open(PICS / ch / "trial_mask.png")) > 127


@pytest.mark.parametrize("ch", CHARS)
def test_outline_mask_matches_the_trial(ch):
    orig, cut, trial = _pic(ch)
    m = baldpic.outline_mask(orig, cut)
    assert m.shape == trial.shape and m.dtype == bool
    iou = (m & trial).sum() / (m | trial).sum()
    if ch == "mage":
        # the trial's mask missed the mage's left silver strand; the port finds it, so IoU is lower but the trial's
        # hair is all found (recall)
        assert (m & trial).sum() / trial.sum() >= 0.9
        assert iou >= 0.6
    else:
        assert iou >= 0.8, iou


def test_target_and_eyes_and_colour():
    orig, cut, trial = _pic("knight1")
    t = baldpic.measure_target(cut)
    assert set(t) >= {"H_px", "top_px", "neck_px", "head_cx_px", "head_w", "head_h"}
    assert 0.07 < t["head_h"] < 0.2 and 0.07 < t["head_w"] < 0.2
    assert baldpic.target(cut) == t
    boxes = baldpic.eye_boxes(cut, t)
    assert len(boxes) == 2 and boxes[0][0] < boxes[1][0]
    for b in boxes:
        assert t["top_px"] < b[1] < t["neck_px"]
    col = baldpic.hair_colour(orig, trial)
    assert col["median"][0] > col["median"][2] + 40            # red hair
    assert sum(col["dark"]) < sum(col["median"]) < sum(col["light"])
    assert baldpic.eye_cover(trial, boxes) == pytest.approx(baldpic.eye_cover(trial, boxes))


def test_kept_measure():
    orig, cut, trial = _pic("knight2")
    k = baldpic.kept_measure(orig, cut, trial)
    assert k["kept_share"] > 0.85 and k["face_delta"] < 15
    # a picture that changed everywhere keeps little
    noisy = Image.fromarray(np.clip(np.asarray(cut.convert("RGBA")).astype(int) + np.array([90, -90, 90, 0]), 0, 255).astype(np.uint8), "RGBA")
    assert baldpic.kept_measure(orig, noisy, trial)["kept_share"] < 0.3


def test_picture_record_shape():
    orig, cut, _ = _pic("ponytail")
    mask, rec = baldpic.picture_record(orig, cut)
    assert set(rec) == {"target", "eye_boxes", "pic_eye_cover", "colour", "outline_px"}
    assert rec["outline_px"] == int(mask.sum()) > 0
    json.dumps(rec)


# --------------------------------------------------------------------------------------------- geometry

def _sphere(n=40, r=0.1, c=(0, 0, 1.6)):
    th, ph = np.meshgrid(np.linspace(-np.pi, np.pi, n), np.linspace(-np.pi / 2, np.pi / 2, n))
    P = np.stack([np.sin(th) * np.cos(ph), -np.cos(th) * np.cos(ph), np.sin(ph)], -1).reshape(-1, 3)
    return np.asarray(c) + P * r


def test_skull_fit_on_an_ellipsoid_head():
    R = np.array([0.078, 0.098, 0.100])
    th, ph = np.meshgrid(np.linspace(-np.pi, np.pi, 90), np.linspace(-1.2, np.pi / 2, 60))
    U = np.stack([np.sin(th) * np.cos(ph), -np.cos(th) * np.cos(ph), np.sin(ph)], -1).reshape(-1, 3)
    head = np.array([0, 0, 1.6]) + U * R
    body = np.vstack([head, np.random.default_rng(0).uniform([-0.2, -0.1, 0], [0.2, 0.1, 1.45], (3000, 3))])
    S = hf.find_skull(body, head_h=0.12)
    assert np.allclose(S["R"], R, atol=0.006) and np.allclose(S["c"], [0, 0, 1.6], atol=0.006)
    assert abs(S["pitch"]) < 0.05 and S["resid_mm"] < 3


def test_place_on_its_own_skull_is_identity_and_mirror_flips():
    S = dict(c=[0.0, 0.01, 1.6], R=[0.08, 0.1, 0.1], pitch=0.05, HH=0.2)
    G = np.full((len(hf.GRID_PH), len(hf.GRID_TH)), 1.0)
    rng = np.random.default_rng(1)
    q = hf.nrm(rng.normal(size=(500, 3))) * rng.uniform(1.0, 1.4, (500, 1))
    q[:, 2] = np.abs(q[:, 2])
    rho = hf.sample_grid(G, q)
    W = hf.place(q, rho, G, S)
    assert np.abs(W - hf.to_world(S, q)).max() < 1e-9
    Wm = hf.place(q, rho, G, S, mirror=True)
    assert np.allclose(hf.to_local(S, Wm)[:, 0], -q[:, 0], atol=1e-9)
    # volume only moves what stands off the scalp, length only what hangs below the skull centre
    Wv = hf.place(q, rho, G, S, volume=1.5)
    assert np.all(np.linalg.norm(hf.to_local(S, Wv), axis=1) >= np.linalg.norm(q, axis=1) - 1e-9)
    tris = np.array([[0, 1, 2]])
    assert hf.mirror_tris(tris, True).tolist() == [[2, 1, 0]]


def test_radial_correction_follows_a_wider_head():
    S = dict(c=[0.0, 0.0, 1.6], R=[0.08, 0.1, 0.1], pitch=0.0, HH=0.2)
    G_r = np.full((len(hf.GRID_PH), len(hf.GRID_TH)), 1.0)
    G_b = np.full_like(G_r, 1.1)                       # our scalp 10 percent further out everywhere
    q = hf.dirv(np.array([0.0, 90.0, 180.0]), np.array([30.0, 20.0, 10.0])) * 1.05
    W = hf.place(q, hf.sample_grid(G_r, q), G_b, S)
    assert np.allclose(np.linalg.norm(hf.to_local(S, W), axis=1), 1.15, atol=1e-6)


def test_raster_and_score():
    xy = np.array([[10.0, 10.0], [40.0, 10.0], [10.0, 40.0]])
    buf = hf.raster_depth(xy, np.zeros(3), np.array([[0, 1, 2]]), 64, 64)
    vis = np.isfinite(buf)
    assert 400 < vis.sum() < 520
    pic = vis.copy()
    sc, iou, pen = hf.score_vis(vis, pic, np.ones_like(vis), [[10, 10, 20, 20]], [0.0])
    assert iou == 1.0 and pen > 0 and sc < 1.0
    assert hf.score_vis(vis, pic, np.ones_like(vis), [[10, 10, 20, 20]], [1.0])[2] == 0


# --------------------------------------------------------------------------------------------- recolour

def _hue(rgb):
    lab = hf.to_lab(np.asarray(rgb, float))
    return np.degrees(np.arctan2(lab[..., 2], lab[..., 1]))


@pytest.mark.parametrize("ch", CHARS)
def test_recolour_keeps_the_pictures_hue(ch):
    colour = baldpic.hair_colour(*[_pic(ch)[i] for i in (0, 2)])
    for tex in (FIX / "lib" / "tex").glob("*_grey.png"):
        a = np.asarray(Image.open(tex).convert("LA")).astype(float) / 255
        rgb = hf.recolour(a[..., 0], a[..., 1], colour)
        m = a[..., 1] > 0.5
        mean = hf.lin_to_srgb(hf.srgb_to_lin(rgb[m]).mean(0)) * 255
        lab_got, lab_want = hf.to_lab(mean), hf.to_lab(np.asarray(colour["median"], float))
        chroma = float(np.hypot(lab_want[1], lab_want[2]))
        if chroma > 8:                                  # hue is only meaningful for coloured hair (not silver)
            d = abs((_hue(mean) - _hue(colour["median"]) + 180) % 360 - 180)
            assert d <= 8, (ch, tex.name, d)
        assert float(np.linalg.norm(lab_got - lab_want)) < 12


# --------------------------------------------------------------------------------------------- the attach format

def test_attach_fixture_follows_the_plan():
    d = np.load(FIX / "hair_mesh.npz")
    assert set(d.files) == {"verts", "tris", "uv", "role", "flow"}
    n, t = len(d["verts"]), len(d["tris"])
    assert d["verts"].dtype == np.float32 and d["tris"].dtype == np.int32 and d["role"].dtype == np.int8
    assert d["uv"].shape == (t, 3, 2) and d["flow"].shape == (n, 3) and d["tris"].max() < n
    roles = set(np.unique(d["role"]).tolist())
    assert {0, 1, 2} <= roles
    parts = json.loads((FIX / "hair_parts.json").read_text(encoding="utf-8"))
    assert parts["cap_first"] is False and parts["style"] == "mh_ponytail01" and parts["extra_height_m"] >= 0
    lo, hi = d["verts"].min(0), d["verts"].max(0)
    for g in parts["groups"]:
        assert g["kind"] == "Hair" and len(g["path"]) >= 2 and g["length_m"] > 0
        P = np.asarray(g["path"])
        assert np.all(P >= lo - 0.01) and np.all(P <= hi + 0.01)          # same frame as the verts
        assert P[0, 1] > P[-1, 1]                                           # root first: the head end is higher (Y up)
    # hair first, the cap after it
    first_cap = int(np.argmax(d["role"] == 1))
    assert np.all(d["role"][:first_cap] != 1) and np.all(d["role"][first_cap:] == 1)


def test_check_values():
    m = dict(score=0.55, name="Bob", family="bob", family_source="description", style_source="best",
             skin_through=dict(all=0.03, crown=0.0, sides=0.05), triangles=3000, body_triangles=17500, budget=20000, allowance=3000)
    cv = hairfit.check_values(m)
    assert cv["hair_fit"][0] == 0.55 and cv["hair_fit"][1] == 0.6 and cv["hair_fit"][2]["style_name"] == "Bob"
    assert cv["hair_skin_through"][:2] == (0.03, 0.02) and cv["hair_skin_through"][2] == dict(crown=0.0, sides=0.05)
    assert cv["hair_triangles"][:2] == (20500, 20000) and cv["hair_triangles"][2]["hair"] == 3000
    mesh = dict(best_score=0.77, candidates=[dict(name="bob02", style="mh_bob02")], family="bob")
    assert hairfit.check_values(mesh)["hair_fit"][0] == 0.77 and "hair_triangles" not in hairfit.check_values(mesh)
    assert hairfit.check_values({}) == {}


class _BrokenSC:
    platform = "pc_mid"

    class asset:
        prompt = "a short red bob"
        style_preset = "painted"

    async def blender(self, *a, **k):
        raise RuntimeError("no blender here")

    def use_model(self, mid):
        pass


def test_rank_never_raises(tmp_path):
    hairstyles.use_dir(FIX / "lib")
    try:
        pj = tmp_path / "p.json"
        pj.write_text(json.dumps(dict(colour=dict(dark=[1, 1, 1], median=[2, 2, 2], light=[3, 3, 3]))), encoding="utf-8")
        out = asyncio.run(hairfit.rank(_BrokenSC(), tmp_path / "x.glb", pj, tmp_path / "c.png", tmp_path / "o.png", "short", 0, tmp_path / "r"))
    finally:
        hairstyles.use_dir(None)
    assert out["error"] and out["candidates"] == [] and out["nothing_fits"] is True and out["family_source"] == "description"
    assert set(out) >= {"family", "family_source", "candidates", "best", "best_score", "nothing_fits", "seconds", "error"}


def test_fit_words():
    assert hairfit.fit_words(0.8) == "close to the picture" and hairfit.fit_words(0.5) == "not like the picture"


def test_tied_families_are_scored_on_the_head_only(tmp_path):
    """Ponytail, bun and braid candidates are scored on the outline above the neck (the tail hangs behind the head in
    a front picture); other families on the whole outline."""
    hairstyles.use_dir(FIX / "lib")
    try:
        pj = tmp_path / "p.json"
        pj.write_text(json.dumps(dict(colour=dict(dark=[1, 1, 1], median=[2, 2, 2], light=[3, 3, 3]))), encoding="utf-8")
        for fam, head in (("ponytail", True), ("short", False)):
            out = asyncio.run(hairfit.rank(_BrokenSC(), tmp_path / "x.glb", pj, tmp_path / "c.png", tmp_path / "o.png", fam, 0, tmp_path / fam))
            job = json.loads((tmp_path / fam / "rank_job.json").read_text(encoding="utf-8"))
            assert job["head_only"] is head
            assert (out.get("score_region") == "head") is head
    finally:
        hairstyles.use_dir(None)


def test_recolour_matches_a_dark_reddish_brown_without_a_render_push():
    """The live ponytail's picture tones: the texture's average must land on the picture's median (the old render
    correction pushed it to a strong red)."""
    colour = dict(median=[66.0, 27.0, 18.0], dark=[43.0, 6.0, 3.0], light=[107.0, 66.0, 46.0])
    for tex in (FIX / "lib" / "tex").glob("*_grey.png"):
        a = np.asarray(Image.open(tex).convert("LA")).astype(float) / 255
        rgb = hf.recolour(a[..., 0], a[..., 1], colour)
        assert hf.colour_delta(rgb, a[..., 1] * 255, colour) < 3
