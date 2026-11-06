"""The hair detail pass: the mask vote and skip rules, the flow and its fallback, the paint (strength, seed, the face's
texels), the shared relayout's plan, and the check (vgac/hair.py, vgac/stages/hair_detail.py)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from vgac import face, guidance, hair
from vgac.stages import hair_detail as hd

HAIR_RGB = (0.55, 0.12, 0.05)
SKIN_RGB = (0.9, 0.7, 0.6)


def cap(n: int = 24, r: float = 0.1, top_deg: float = 80.0) -> dict[str, np.ndarray]:
    """A head-sized dome (Y up), its texture laid out from above: u from x, v from z."""
    verts, uv = [], []
    for i in range(n + 1):
        th = math.radians(top_deg) * i / n
        for j in range(n):
            ph = 2 * math.pi * j / n
            x, y, z = r * math.sin(th) * math.cos(ph), r * math.cos(th), r * math.sin(th) * math.sin(ph)
            verts.append((x, y, z))
            uv.append((0.5 + 0.45 * x / r, 0.5 + 0.45 * z / r))
    faces = []
    for i in range(n):
        for j in range(n):
            a, b = i * n + j, i * n + (j + 1) % n
            c, d = (i + 1) * n + j, (i + 1) * n + (j + 1) % n
            faces += [(a, c, b), (b, c, d)]
    v = np.array(verts, dtype=np.float64)
    f = np.array(faces, dtype=np.int64)
    return {"verts": v, "faces": f, "uv": np.array(uv), "vnormals": face.smooth_normals(v, f)}


def centres() -> np.ndarray:
    lab = face.to_lab(np.array([HAIR_RGB, SKIN_RGB, (0.3, 0.3, 0.32)]))
    return lab


def maps(T: int = 128) -> dict[str, np.ndarray]:
    base = np.zeros((T, T, 3))
    base[:] = HAIR_RGB
    return {"base_color": base, "normal": np.tile(np.array([0.5, 0.5, 1.0]), (T, T, 1)),
            "roughness": np.full((T, T), 0.3), "metallic": np.full((T, T), 0.8), "ao": np.ones((T, T))}


# --- the mask -------------------------------------------------------------------------------------------------------


def test_verdict_says_why_hair_cannot_be_found():
    ok = {"crown_share": 0.95, "separation_skin": 27.0, "triangles": 800, "reach": 0.12, "body_share": 0.0,
          "separation_touching": 40.0, "agreement": 0.82}
    assert hair.verdict(ok) is None
    assert hair.verdict({**ok, "crown_share": 0.3}) == "crown_mixed"  # a hat or a hood
    assert hair.verdict({**ok, "separation_skin": 9.0}) == "hair_like_skin"  # dark hair on dark skin
    assert hair.verdict({**ok, "triangles": 10}) == "bald"
    assert hair.verdict({**ok, "reach": 0.7}) == "joined_to_clothing"  # a red cloak next to red hair
    assert hair.verdict({**ok, "body_share": 2.5}) == "joined_to_clothing"
    assert hair.verdict({**ok, "separation_touching": 8.0}) == "hair_like_clothes"
    assert hair.verdict({**ok, "agreement": 0.4}) == "pictures_disagree"
    for r in ("crown_mixed", "hair_like_skin", "joined_to_clothing", "helmet", "pictures_disagree"):
        assert hair.reason_words(r) and "_" not in hair.reason_words(r)


def test_a_green_or_blue_crown_is_cloth_not_hair():
    for rgb in ((0.55, 0.12, 0.05), (0.35, 0.22, 0.12), (0.85, 0.72, 0.45), (0.08, 0.07, 0.07), (0.8, 0.8, 0.8)):
        assert hair.natural_hair(face.to_lab(np.array([rgb]))[0]), rgb  # red, brown, blond, black, grey
    for rgb in ((0.2, 0.3, 0.15), (0.15, 0.2, 0.45), (0.4, 0.2, 0.5)):
        assert not hair.natural_hair(face.to_lab(np.array([rgb]))[0]), rgb  # a green hood, a blue hat, violet
    assert hair.verdict({"crown_share": 0.98, "hair_lab": [28.9, -18.0, 14.9]}) == "not_hair_colour"  # the Ranger's hood


def test_the_vote_keeps_hair_joined_to_the_crown_and_drops_specks():
    m = cap(16)
    nb = hair.triangle_neighbours(m["faces"], m["verts"])
    c = m["verts"][m["faces"]].mean(axis=1)
    crown = c[:, 1] > 0.095
    candidate = c[:, 1] > 0.06
    speck = np.zeros(len(c), dtype=bool)
    speck[np.argmin(c[:, 1])] = True  # a hair-coloured triangle on its own, far below
    hair_t = hair.grow_tris(crown & (candidate | speck), candidate | speck, nb)
    assert hair_t[candidate].all() and not hair_t[speck].any()


def test_picture_votes_outweigh_a_single_model_vote():
    model = np.array([1.0, 1.0, 0.0, 0.0])
    pic_hair = np.array([0.0, 2.0, 2.0, 0.0])
    seen = np.array([2.0, 2.0, 2.0, 0.0])
    score = (model + pic_hair) / (1.0 + seen)
    assert list(score >= 0.5) == [False, True, True, False]


def test_kmeans_keeps_hair_and_skin_first_and_labels_by_nearest():
    rng = np.random.default_rng(0)
    lab = face.to_lab(np.array([HAIR_RGB, SKIN_RGB, (0.2, 0.25, 0.6)]))
    pts = np.concatenate([l + rng.normal(0, 1.5, (200, 3)) for l in lab])
    C = hair.kmeans(pts, lab[:2], 3, seed=1)
    lbl, _ = hair.nearest_centre(lab, C)
    assert list(lbl) == [0, 1, 2]


def test_match_tris_finds_the_same_triangles_on_another_export():
    m = cap(10)
    sel = np.zeros(len(m["faces"]), dtype=bool)
    sel[::7] = True
    pts = hair.hair_points(m, sel)
    assert (hair.match_tris(m, pts) == sel).all()
    assert not hair.match_tris(m, []).any()


def test_texel_density_and_gain():
    m = cap(12)
    allt = np.ones(len(m["faces"]), dtype=bool)
    tpc = hair.texels_per_cm(m, allt, 2048)
    assert tpc > 0
    assert hair.hair_gain(hair.TARGET_TPC_2048, 2048) == 1.0
    assert hair.hair_gain(6.6, 2048) == pytest.approx(14 / 6.6, rel=1e-2)
    assert hair.hair_gain(2.0, 2048) == hair.HAIR_GAIN_MOST
    assert hair.hair_gain(6.6, 1024) == 1.0  # a 1024 atlas asks for 7 per cm: 6.6 is close enough


# --- the flow ------------------------------------------------------------------------------------------------------


def test_the_plain_flow_runs_down_from_the_crown_along_the_surface():
    m = cap(20)
    reg = hair.texel_region(m, 96, np.ones(len(m["faces"]), dtype=bool))
    pos, nrm = reg["pos"], reg["nrm"]
    f = hair.flow_down_from_crown(pos, nrm)
    assert np.allclose(np.linalg.norm(f, axis=1), 1.0, atol=1e-6)
    assert np.abs((f * nrm).sum(axis=1)).max() < 1e-6  # on the surface
    side = pos[:, 1] < 0.05
    assert (f[side, 1] < -0.5).mean() > 0.95  # downward on the sides
    out = (f[:, [0, 2]] * pos[:, [0, 2]]).sum(axis=1)
    assert (out[pos[:, 1] > 0.08] > 0).mean() > 0.9  # outward on top


def test_line_filters_find_the_direction_of_painted_strands_and_trust_only_lines():
    S = 96
    Y, X = np.mgrid[0:S, 0:S]
    stripes = (0.5 + 0.4 * np.sin(2 * math.pi * X / 6.0))[..., None].repeat(3, axis=2)  # vertical strands
    o = hair.line_orientation(stripes)
    inner = (slice(20, 76), slice(20, 76))
    ang = o["angle"][inner]
    assert np.median(np.abs(np.cos(ang))) < 0.1  # lines run up and down
    assert np.median(o["confidence"][inner]) > 0.6
    flat = np.full((S, S, 3), 0.5) + np.random.default_rng(3).normal(0, 0.01, (S, S, 3))
    assert np.median(hair.line_orientation(flat)["confidence"][inner]) < 0.5


# --- the paint -----------------------------------------------------------------------------------------------------


def paint(strength: float = 1.0, seed: int = 5, protect: np.ndarray | None = None, highlight: bool = True,
          photoreal: bool = False, mp: dict[str, np.ndarray] | None = None) -> dict:
    m = cap(20)
    return hair.paint_hair(m, mp or maps(), np.ones(len(m["faces"]), dtype=bool), centres(), 0, 1, strength=strength,
                           seed=seed, highlight=highlight, photoreal=photoreal, protect=protect)


def test_strength_zero_changes_nothing():
    r = paint(0.0)
    assert r["maps"] == {} and r["measures"]["painted"] is False
    assert hd.skip_reason("humanoid", {}, {"hair_strength": 0}) == "strength_zero"


def test_paint_adds_streaks_shading_bump_and_hair_roughness():
    mp = maps()
    r = paint(1.0, mp=mp)
    mask = r["mask"]
    assert mask.sum() > 1000
    b0, b1 = mp["base_color"][mask], r["maps"]["base_color"][mask]
    assert face.luminance(b1).std() > face.luminance(b0).std() + 0.005  # streaks where it was flat
    assert np.abs(r["maps"]["normal"][mask] - mp["normal"][mask]).max() > 0.02  # strand relief
    assert abs(r["maps"]["roughness"][mask].mean() - hair.ROUGH["painted"]) < 0.06
    assert r["maps"]["metallic"][mask].max() < 1e-9  # hair is not metal
    assert not np.any(r["maps"]["base_color"][~face.dilate_mask(mask, 4)] != mp["base_color"][~face.dilate_mask(mask, 4)])


def test_the_same_seed_gives_the_same_hair_and_another_seed_another():
    a, b, c = paint(seed=11), paint(seed=11), paint(seed=12)
    assert np.array_equal(a["maps"]["base_color"], b["maps"]["base_color"])
    assert np.array_equal(a["maps"]["normal"], b["maps"]["normal"])
    assert not np.array_equal(a["maps"]["base_color"], c["maps"]["base_color"])


def test_the_face_texels_are_never_written():
    T = 128
    protect = np.zeros((T, T))
    protect[40:70, 40:70] = 1.0
    mp = maps(T)
    r = paint(protect=protect, mp=mp)
    for key in ("base_color", "normal"):
        assert np.array_equal(r["maps"][key][40:70, 40:70], mp[key][40:70, 40:70])
    # metal and shine are a hair's everywhere, the face's region included: a fringe lock over the brow that kept the
    # 3D step's metal rendered as a dark blotch in Unity
    painted = r["mask"][40:70, 40:70]
    assert painted.any()
    assert (r["maps"]["metallic"][40:70, 40:70][painted] < 0.01).all()
    assert (r["maps"]["roughness"][40:70, 40:70][painted] > 0.3).all()


def test_photoreal_gets_no_painted_highlight_and_a_look_without_bumps_no_normal_map():
    r = paint(photoreal=True)
    assert r["measures"]["highlight_share"] == 0.0
    mp = maps()
    mp.pop("normal")
    r2 = paint(mp=mp)
    assert "normal" not in r2["maps"]
    assert hd.look_of({"delight_default": "remove"}, {"style": "realistic_pbr"})["highlight"] is False
    assert hd.look_of({"delight_default": "keep"}, {"style": "painted", "mesh": {"normal_map": True}})["highlight"] is True


def test_the_hairline_leans_toward_the_skin_only_on_the_hair_side():
    mp = maps()
    T = 128
    Y, X = np.mgrid[0:T, 0:T]
    skin = X < 40  # one side of the dome is skin
    mp["base_color"][skin] = SKIN_RGB
    r = paint(mp=mp)
    out = r["maps"]["base_color"]
    assert np.allclose(out[skin & (X < 36)], np.array(SKIN_RGB))  # the skin side is not written
    assert r["measures"]["hairline_texels"] > 0


def test_normal_map_round_trip():
    v = np.array([[0.3, -0.2, 0.93]])
    v /= np.linalg.norm(v)
    assert np.allclose(hair.decode_normal(hair.encode_normal(v)), v, atol=1e-6)


def test_value_noise_is_smooth_and_repeatable():
    p = np.random.default_rng(0).uniform(-0.1, 0.1, (500, 3))
    a = hair.value_noise(p, 0.01, 3)
    assert np.array_equal(a, hair.value_noise(p, 0.01, 3))
    assert a.min() >= -1 and a.max() <= 1
    near = hair.value_noise(p + 1e-5, 0.01, 3)
    assert np.abs(near - a).max() < 0.02


# --- skip rules, settings and the check --------------------------------------------------------------------------------

REC = {"surface": {"shading": "smooth", "texture_filter": "smooth"}, "mesh": {"texture": 2048}}


def test_skip_rules():
    assert hd.skip_reason("humanoid", REC, {}) is None
    assert hd.skip_reason("humanoid", REC, {"hair_detail": False}) == "off"
    assert hd.skip_reason("quadruped", REC, {}) == "not_category"
    assert hd.skip_reason("humanoid", {"surface": {"texture_filter": "crisp"}, "mesh": {"texture": 256}}, {}) == "style"
    assert hd.skip_reason("humanoid", {"surface": {"shading": "flat"}, "mesh": {"texture": 512}}, {}) == "style"
    assert hd.strength_of({"hair_strength": 4}) == 1.0 and hd.strength_of({"hair_strength": "x"}) == 1.0
    assert hd.on({}) and not hd.on({"hair_detail": False})
    assert hd.seed_of({}, "abc") == hd.seed_of({}, "abc") and hd.seed_of({"hair_seed": 7}, "abc") == 7


def test_hair_check_sentences():
    for reason in hd.NOT_APPLICABLE:
        assert hd.hair_checks({"hair_detail": {"ran": False, "skipped": reason}}) == []
    stood_back = hd.hair_checks({"hair_detail": {"ran": False, "skipped": "joined_to_clothing"}})[0]
    assert stood_back["ok"]
    text = guidance.describe_check(stood_back)["help"]
    assert text.startswith("Hair detail (strands and shading painted on) was not added: the hair colour runs on into the clothing")
    err = hd.hair_checks({"hair_detail": {"ran": False, "skipped": "error"}})[0]
    assert not err["ok"]
    face_b = {"sharpness": 1.6, "eyes": [[398.0, 518.0], [625.0, 519.0]]}
    good = hd.hair_checks({"hair_detail": {"ran": True, "texels_per_cm_before": 6.6, "texels_per_cm_after": 15.6,
                                           "face_before": face_b, "face_after": face_b, "gain_asked": 2.1,
                                           "layout": {"hair_gain": 2.1}, "highlight": True}})[0]
    assert good["ok"] and good["vars"]["room_note"] == ""
    moved = hd.hair_checks({"hair_detail": {"ran": True, "face_before": face_b,
                                            "face_after": {"sharpness": 1.6, "eyes": [[403.0, 518.0], [625.0, 519.0]]}}})[0]
    assert not moved["ok"] and moved["vars"]["reason"] == "face_changed"
    blurred = hd.hair_checks({"hair_detail": {"ran": True, "face_before": face_b, "face_after": {**face_b, "sharpness": 1.4}}})[0]
    assert not blurred["ok"]
    short = hd.room_note({"gain_asked": 2.1, "layout": {"hair_gain": 1.0}, "texture_size": 1024})
    assert "no room" in short and "1024" in short
    part = hd.room_note({"gain_asked": 2.1, "layout": {"hair_gain": 1.5}})
    assert "1.5 times" in part
    assert guidance.describe_check(err)["fix"]["params"] == {"hair_detail": True}


def test_check_words_render_without_placeholders_left():
    g = guidance.CHECK_GUIDE["hair_matches_picture"]
    c_skip = {"name": "hair_matches_picture", "ok": True, "value": "hair_like_skin", "limit": None,
              "vars": {"skipped": True, "why": hair.reason_words("hair_like_skin")}}
    s = guidance.format_check_text(g["ok"](c_skip), {**c_skip, **c_skip["vars"]})
    assert "too close in colour to the skin" in s and "{" not in s
    c_ran = {"name": "hair_matches_picture", "ok": True, "value": 15.6, "limit": 6.6,
             "vars": {"skipped": False, "before": 6.6, "after": 15.6, "highlight": True, "room_note": ""}}
    s = guidance.format_check_text(g["ok"](c_ran), {**c_ran, **c_ran["vars"]})
    assert "6.6 per cm before, 15.6 now" in s and "highlight band" in s
    c_fail = {"name": "hair_matches_picture", "ok": False, "vars": {"reason": "face_changed"}}
    assert "face changed" in g["fail"](c_fail)
    assert g["fix"](c_fail)["params"]["hair_strength"] == 0.5


def test_spill_fills_edge_texels_from_the_hair_and_leaves_other_texels_alone():
    img = np.zeros((12, 12))
    written = np.zeros((12, 12), dtype=bool)
    written[4:8, 4:8] = True
    img[written] = 1.0
    covered = np.zeros((12, 12), dtype=bool)
    covered[4:8, 8] = True  # another texel of the region (skin): keeps its value
    out = hair.spill(img, written, covered, px=2)
    assert out[3, 5] == 1.0 and out[2, 5] == 1.0 and out[5, 9] == 1.0  # edge and gutter texels take the hair's value
    assert out[5, 8] == 0.0 and out[0, 0] == 0.0
