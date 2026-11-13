"""The picture step's hard-light warning (stages/concept.light_check, check `soft_light`).

Unit tests on drawn figures, and one over the eleven pictures of the Realistic test (bakeoff/realistic_test/inputs,
their stored cut-outs in library/vgac.sqlite); that one is skipped when the pictures or the library are not there
(neither is in git).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from vgac import guidance, readiness
from vgac.stages import concept

ROOT = Path(__file__).resolve().parents[2]
HUMANOID = {"id": "humanoid"}
SKIN = (205, 150, 120)


def figure(shadow: bool = False, skin: tuple[int, int, int] = SKIN, clothed: bool = False) -> Image.Image:
    """A 512 x 768 cut-out of a person made of skin-coloured shapes; with `shadow` the lower half and the right side of
    the head are in deep shadow (the same hue, much darker), as under a hard light."""
    im = Image.new("RGBA", (512, 768), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    shade = tuple(int(c * 0.25) for c in skin)
    d.ellipse((216, 40, 296, 140), fill=skin + (255,))
    d.rectangle((226, 140, 286, 170), fill=skin + (255,))
    d.rectangle((176, 170, 336, 420), fill=(60, 70, 120, 255) if clothed else skin + (255,))
    d.rectangle((90, 175, 176, 215), fill=skin + (255,))
    d.rectangle((336, 175, 422, 215), fill=skin + (255,))
    d.rectangle((196, 420, 256, 740), fill=skin + (255,))
    d.rectangle((260, 420, 320, 740), fill=skin + (255,))
    if shadow:
        d.rectangle((176, 520, 336, 740), fill=shade + (255,))
        d.rectangle((256, 40, 296, 140), fill=shade + (255,))
    return im


def test_flat_light_passes_whatever_the_skin_tone():
    for skin in (SKIN, (110, 70, 52), (240, 200, 180)):
        c = concept.light_check(figure(skin=skin), HUMANOID, "realistic_pbr")
        assert c is not None and c["passed"] and c["severity"] == "warn", (skin, c)
        assert c["value"] >= concept.LIGHT_RATIO_MIN


def test_hard_light_warns_with_plain_help_and_the_even_light_redraw():
    c = concept.light_check(figure(shadow=True), HUMANOID, "realistic_pbr")
    assert c is not None and not c["passed"] and c["severity"] == "warn"
    assert c["name"] == "soft_light" and c["value"] < concept.LIGHT_RATIO_MIN
    assert "hard, high-contrast light" in c["help"] and "nearly black" in c["help"] and "flat, even light" in c["help"]
    assert "carry on" in c["help"] and "{" not in c["help"]
    assert c["fix_label"] == "Redraw it in even light"
    assert c["fix"]["op"] == "refine" and c["fix"]["instruction"] == readiness.HUMANOID_REDRAW
    assert guidance.CHECK_GUIDE["soft_light"]["severity"] == "warn"


def test_a_warning_never_a_block():
    c = concept.light_check(figure(shadow=True), HUMANOID, "realistic_pbr")
    assert c["severity"] == "warn"


def test_no_verdict_when_it_does_not_apply():
    assert concept.light_check(figure(shadow=True), HUMANOID, "painted") is None  # painted looks shade on purpose
    assert concept.light_check(figure(shadow=True), {"id": "quadruped"}, "realistic_pbr") is None
    assert concept.light_check(figure(shadow=True), HUMANOID, None) is None
    # a person covered from head to foot shows too little skin to tell
    covered = Image.new("RGBA", (512, 768), (0, 0, 0, 0))
    ImageDraw.Draw(covered).rectangle((150, 40, 360, 740), fill=(60, 70, 120, 255))
    assert concept.light_check(covered, HUMANOID, "realistic_pbr") is None
    assert concept.light_check(Image.new("RGBA", (64, 64)), HUMANOID, "realistic_pbr") is None


def test_unreadable_picture_gives_no_check(tmp_path):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"not a picture")
    assert concept.light_check(bad, HUMANOID, "realistic_pbr") is None


# --- the eleven pictures of the Realistic test ----------------------------------------------------------------------

INPUTS = ROOT / "bakeoff" / "realistic_test" / "inputs"
LIBRARY = ROOT / "library"
ELEVEN = ["W1-clothed", "W1-bikini", "M1-clothed", "M1-shorts", "W2-clothed", "W2-underwear", "M1-clothed-hard",
          "W1-bikini-hard", "P1-photo", "W1-clothed-tall", "W1-face"]
HARD = {"M1-clothed-hard", "W1-bikini-hard"}
ASSET_OF = {"W1-face": "W1-clothed-tall-face"}  # the stored run's asset name


def eleven_cutouts() -> dict[str, Path]:
    db = LIBRARY / "vgac.sqlite"
    if not db.is_file() or not all((INPUTS / f"{n}.png").is_file() for n in ELEVEN):
        pytest.skip("the Realistic test pictures and library are not here (not in git)")
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("select a.name, v.files_json from version v join asset a on a.id = v.asset_id "
                           "join project p on p.id = a.project_id where p.name = 'RCT Realistic PC' and v.stage = 'concept' "
                           "and v.selected = 1").fetchall()
    finally:
        con.close()
    stored = {name: LIBRARY / json.loads(files)["cutout"] for name, files in rows}
    out = {n: stored.get(ASSET_OF.get(n, n)) for n in ELEVEN}
    if not all(p and p.is_file() for p in out.values()):
        pytest.skip("a stored cut-out of the Realistic test is missing")
    return out


def test_hard_light_flags_the_two_hard_pictures_and_none_of_the_nine():
    cuts = eleven_cutouts()
    flagged, ratios = set(), {}
    for name, path in cuts.items():
        c = concept.light_check(path, HUMANOID, "realistic_pbr")
        assert c is not None, name  # skin shows on every one of the eleven
        ratios[name] = c["value"]
        if not c["passed"]:
            flagged.add(name)
    assert flagged == HARD, ratios
    # a margin on both sides of the limit
    assert max(v for n, v in ratios.items() if n in HARD) < concept.LIGHT_RATIO_MIN - 0.1
    assert min(v for n, v in ratios.items() if n not in HARD) > concept.LIGHT_RATIO_MIN + 0.1
