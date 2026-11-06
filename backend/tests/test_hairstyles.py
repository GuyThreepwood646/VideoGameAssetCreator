"""Hair library as app data (docs/HAIR-LIBRARY-PLAN.md section 4): the built library in backend/vgac/data/hairlib and
the loader vgac/hairstyles.py. No Blender needed."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from vgac import hairstyles

LIB = Path(hairstyles.DATA_DIR)
FIX = Path(__file__).resolve().parent / "data" / "hairlib_fixture"
FIELDS = {"id", "name", "pack", "source_url", "author", "licence", "family", "fringe", "parting", "length_rel_head", "kind",
          "alpha", "two_sided", "triangles", "vertices", "hang_groups", "textures", "phone_ok", "lite", "default_params",
          "thumbs", "sha256"}


@pytest.fixture(autouse=True)
def _app_library():
    hairstyles.use_dir(None)
    yield
    hairstyles.use_dir(None)


def test_catalogue_entries_complete_and_files_present():
    cat = hairstyles.catalogue()
    assert 20 <= len(cat["styles"]) <= 40
    for sid, e in cat["styles"].items():
        assert FIELDS <= set(e), (sid, FIELDS - set(e))
        assert e["id"] == sid
        assert e["family"] in hairstyles.FAMILIES
        assert e["kind"] in ("shell", "cards") and e["alpha"] == ("cutout" if e["kind"] == "cards" else "opaque")
        assert e["parting"] in ("left", "right", "centre", "none")
        assert len(e["hang_groups"]) <= 3
        for g in e["hang_groups"]:
            assert g["kind"] == "Hair" and len(g["path_q"]) >= 2
        p = hairstyles.paths(sid)
        for key in ("npz", "grey", "thumb_front", "thumb_quarter"):
            assert p[key] is not None and p[key].is_file(), (sid, key)
        if e["lite"]:
            assert p["lite"].is_file() and e["lite_triangles"] <= 750 and e["kind"] == "shell"
        assert e["phone_ok"] == (e["kind"] == "shell" and bool(e["lite"]) and e["lite_triangles"] <= 750)
        if e["kind"] == "cards":
            assert e["two_sided"] and e["textures"]["normal"] is None


def test_every_style_is_cc0_with_a_credit():
    cat = hairstyles.catalogue()
    credits = (LIB / "CREDITS.txt").read_text(encoding="utf-8")
    for sid, e in cat["styles"].items():
        assert e["licence"] == "CC0-1.0"
        assert "CC0" in e["licence_line"] or "CC-0" in e["licence_line"] or "CC0 1.0" in e["licence_line"], sid
        assert "AGPL" not in e["licence_line"] and "BY" not in e["licence_line"].upper().replace("BY VGAC", "")
        assert f"{sid}:" in credits
        c = hairstyles.credit(sid)
        assert c["licence"] == "CC0-1.0" and c["name"] and c["author"]
    assert "Left out" in credits
    for r in cat["refused"]:
        assert r["style"] in credits and r["licence_line"]


def test_refused_styles_are_not_built():
    cat = hairstyles.catalogue()
    refused = {r["style"].split(":", 1)[1] for r in cat["refused"]}
    assert {"culturalibre_hair_01", "littleright_bobcut_hair", "rehmanpolanski_hair_bun_brown", "elvs_french_braid_variation"} <= refused
    built_sources = {Path(e["source_file"]).parent.name for e in cat["styles"].values() if e.get("source_file")}
    assert not (built_sources & refused)


def test_size_cap_and_families():
    total = sum(p.stat().st_size for p in LIB.rglob("*") if p.is_file())
    assert total <= 40 * 1024 * 1024
    fams: dict[str, int] = {}
    for e in hairstyles.catalogue()["styles"].values():
        fams[e["family"]] = fams.get(e["family"], 0) + 1
    for f in hairstyles.FAMILIES:
        assert fams.get(f, 0) >= (1 if f in ("afro", "braid") else 2), (f, fams)


def test_style_arrays_match_the_plan():
    for sid in hairstyles.catalogue()["styles"]:
        d = np.load(hairstyles.paths(sid)["npz"])
        n, t = len(d["q"]), len(d["tris"])
        assert d["q"].dtype == np.float32 and d["q"].shape == (n, 3)
        assert d["rho_r"].shape == (n,) and d["rho_r"].dtype == np.float32
        assert d["tris"].dtype == np.int32 and d["tris"].max() < n
        assert d["uv"].shape == (t, 3, 2) and d["role"].dtype == np.int8 and d["flow"].shape == (n, 3)
        assert set(np.unique(d["role"])) <= {0, 2, 3, 4}
        assert np.allclose(np.linalg.norm(d["flow"], axis=1), 1, atol=1e-3)
        # a style rests on its skull: most vertices sit near the reference scalp radius
        r = np.linalg.norm(d["q"], axis=1)
        top = d["q"][:, 2] > 0.3
        assert np.median(np.abs(r - d["rho_r"])[top]) < 0.7          # the afro and the spiky crop stand far off


def test_self_check_on_the_reference_skull():
    """Placing every style on its own reference skull gives back its positions (recorded by the builder)."""
    for e in hairstyles.catalogue()["styles"].values():
        assert e["self_check_mm"] <= 1.0, e["id"]


def test_textures_grey_la_and_shared():
    from PIL import Image
    seen = {}
    for e in hairstyles.catalogue()["styles"].values():
        g = hairstyles.paths(e["id"])["grey"]
        with Image.open(g) as im:
            assert im.mode == "LA" and im.size[0] <= 1024
            a = np.asarray(im)
        m = a[..., 1] > 127
        assert abs(float(np.median(a[..., 0][m])) / 255 - 0.5) < 0.06
        seen.setdefault(g.name, []).append(e["id"])
    toigo = [k for k, v in seen.items() if any(s.startswith("mh_toigo") for s in v)]
    assert len(toigo) == 1                    # the six toigo bobs share one texture


WORDS = [
    ("a knight with a short red bob with a fringe", "bob"),
    ("dark brown hair in a long high ponytail", "ponytail"),
    ("long loose straight silver hair", "long"),
    ("short neat dark brown hair", "short"),
    ("a ranger with a buzz cut", "short"),
    ("hair tied in a bun", "bun"),
    ("two long braids", "braid"),
    ("a big afro", "afro"),
    ("hair falling to her waist", "long"),
    ("a smiling baker in an apron", None),
]


@pytest.mark.parametrize("text,fam", WORDS)
def test_family_of_word_table(text, fam):
    got, words = hairstyles.family_of(text)
    assert got == fam
    assert bool(words) == (fam is not None)


def test_styles_for_platform_and_look():
    assert hairstyles.styles_for(None, "pc_mid", "pixel") == []
    phone = hairstyles.styles_for(None, "mobile_low", "painted")
    assert phone and all(e["kind"] == "shell" and e["phone_ok"] for e in phone)
    bobs = hairstyles.styles_for("bob", "pc_mid", "painted")
    assert bobs and all(e["family"] == "bob" for e in bobs)
    assert hairstyles.wants_fringe("red bob with a fringe") and not hairstyles.wants_fringe("red bob")


def test_file_path_contained():
    sid = next(iter(hairstyles.catalogue()["styles"]))
    assert hairstyles.file_path(hairstyles.style(sid)["thumbs"]["front"]).is_file()
    with pytest.raises(ValueError):
        hairstyles.file_path("../hairstyles.py")


def test_fixture_library_loads():
    hairstyles.use_dir(FIX / "lib")
    cat = hairstyles.catalogue()
    assert set(cat["styles"]) == {"q_buzzed", "mh_ponytail01"}
    assert hairstyles.style("q_buzzed")["kind"] == "shell" and hairstyles.style("mh_ponytail01")["kind"] == "cards"
    assert hairstyles.style("mh_ponytail01")["hang_groups"]
    assert yaml.safe_load((FIX / "lib" / "curation.yaml").read_text(encoding="utf-8"))["styles"]


def test_every_style_fits_the_pc_allowance_with_its_cap():
    """Hair plus scalp cap must fit 25 percent of the pc_mid budget (5,000 triangles); the cap measured at most about
    270 triangles on the five test characters, so 400 are allowed for it."""
    for e in hairstyles.catalogue()["styles"].values():
        assert e["triangles"] + 400 <= 5000, (e["id"], e["triangles"])
