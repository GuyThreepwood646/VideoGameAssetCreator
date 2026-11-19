"""The pose check before the 3D build and the pose redraw's guard (vgac/buildpose.py), and how step 2 uses them
(stages/turnaround.py _pose_ready_picture, _pose_for_3d)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from own_images_helpers import cut, figure
from vgac import buildpose, guidance
from vgac.stages import turnaround

LIBRARY = Path(__file__).resolve().parents[2] / "library"


@pytest.fixture(autouse=True)
def _no_finders(monkeypatch):
    """No MediaPipe model is asked unless a test gives one: the outline decides, the same on every machine."""
    monkeypatch.setattr(buildpose, "_model", lambda name: None)


def _measure(im):
    return buildpose.measure(cut(im))


# --- the measure on drawn figures --------------------------------------------------------------------------------------


def test_wide_a_pose_with_feet_apart_passes():
    m = _measure(figure("wide", legs="apart"))
    assert m["arms"]["left"]["state"] == m["arms"]["right"]["state"] == "free"
    assert m["legs"]["state"] == "free" and m["legs"]["gap"] >= buildpose.ANKLE_MAY
    c = buildpose.pose_check(m)
    assert c["passed"] and c["severity"] == "warn" and "fix" not in c


def test_arms_down_touch_the_body_and_the_fix_is_the_pose_redraw():
    m = _measure(figure("down", legs="apart"))
    assert {a["state"] for a in m["arms"].values()} == {"touching"}
    c = buildpose.pose_check(m)
    assert not c["passed"] and c["value"] == "touching"
    assert c["fix"] == {"stage": "turnaround", "params": {"pose_ready": True}, "label": "Pose it for 3D, then draw the sides"}
    assert "Both arms hang close to the body" in c["help"] and "web" in c["help"]


def test_narrow_a_pose_joins_and_t_pose_passes():
    narrow = _measure(figure("a", legs="apart"))
    assert {a["state"] for a in narrow["arms"].values()} == {"join"}
    t = _measure(figure("t", legs="apart"))
    assert t["arms_outline"]["tpose"] and {a["state"] for a in t["arms"].values()} == {"free"}


def test_feet_together_will_join_the_thighs():
    m = _measure(figure("wide"))
    assert m["legs"]["state"] == "will_join" and m["legs"]["gap"] < buildpose.ANKLE_WILL
    c = buildpose.pose_check(m)
    assert not c["passed"] and "thighs" in c["help"] and c["fix"]["params"] == {"pose_ready": True}


def _robe():
    """Arms out wide, a long robe to just above the feet, the feet apart below its hem."""
    im = Image.new("RGB", (512, 768), (235, 235, 235))
    d = ImageDraw.Draw(im)
    d.ellipse([216, 70, 296, 150], fill=(230, 190, 150))
    d.polygon([(216, 150), (296, 150), (360, 640), (152, 640)], fill=(90, 40, 140))
    d.line([(212, 165), (60, 330)], fill=(90, 40, 140), width=18)
    d.line([(300, 165), (452, 330)], fill=(90, 40, 140), width=18)
    d.rectangle([185, 640, 225, 705], fill=(40, 30, 20))
    d.rectangle([290, 640, 330, 705], fill=(40, 30, 20))
    return im


def test_a_long_robe_is_clothing_and_offers_no_legs_fix():
    m = _measure(_robe())
    assert m["legs"]["state"] == "clothing"
    c = buildpose.pose_check(m)
    assert c["passed"] and "robe or skirt" in c["help"]


def test_not_measured_is_said_never_a_silent_pass():
    c = buildpose.pose_check(_measure(figure("wide", legs="apart")))
    assert "Not measured: the arms were read from the outline" in c["help"]
    assert "the hands were not checked" in c["help"]
    empty = buildpose.pose_check(buildpose.measure(Image.new("RGBA", (64, 64))))
    assert not empty["passed"] and empty["value"] == "not measured" and "fix" not in empty


def test_words_carry_no_numbers_and_no_placeholders():
    for im in (figure("down"), figure("wide", legs="apart"), _robe(), figure("a")):
        c = buildpose.pose_check(_measure(im), held_item="cane")
        assert not re.search(r"\d", c["help"].replace("3D", "")) and "{" not in c["help"], c["help"]


def test_cautions_are_shown_with_the_fix_only():
    c = buildpose.pose_check(_measure(figure("down", legs="apart")), held_item="cane")
    assert "takes the cane out of the hands" in c["help"]
    ok = buildpose.pose_check(_measure(figure("wide", legs="apart")), held_item="cane")
    assert "cane" not in ok["help"]


def test_after_the_redraw_no_second_redraw_is_offered():
    c = buildpose.pose_check(_measure(figure("down", legs="apart")), after_redraw=True)
    assert not c["passed"] and "fix" not in c and c["help"].startswith("After the pose redraw:")


# --- joints and hands (landmarks given, no model) -------------------------------------------------------------------------


def _pose(arm_deg: float) -> np.ndarray:
    """33 MediaPipe points of a figure 600 px tall facing us, upper arms arm_deg from the body."""
    P = np.zeros((33, 3))
    P[:, 2] = 0.99
    P[buildpose.L_SH, :2], P[buildpose.R_SH, :2] = (330, 200), (270, 200)
    P[buildpose.L_HIP, :2], P[buildpose.R_HIP, :2] = (320, 400), (280, 400)
    r = np.radians(arm_deg)
    P[buildpose.L_EL, :2] = (330 + 110 * np.sin(r), 200 + 110 * np.cos(r))
    P[buildpose.R_EL, :2] = (270 - 110 * np.sin(r), 200 + 110 * np.cos(r))
    return P


def test_parts_beyond_the_body_are_measured_and_cautioned():
    """A plain figure lies inside the body drawn from its joints; a big extra part beside it does not."""
    P = np.zeros((33, 3))
    P[:, 2] = 0.99
    pts = {buildpose.NOSE: (256, 110), 7: (276, 105), 8: (236, 105), buildpose.L_SH: (296, 165), buildpose.R_SH: (216, 165),
           buildpose.L_EL: (376, 248), buildpose.R_EL: (136, 248), buildpose.L_WR: (440, 318), buildpose.R_WR: (72, 318),
           buildpose.L_HIP: (270, 430), buildpose.R_HIP: (242, 430), buildpose.L_KN: (300, 560), buildpose.R_KN: (212, 560),
           buildpose.L_AN: (330, 690), buildpose.R_AN: (182, 690)}
    for i, xy in pts.items():
        P[i, :2] = xy
    for i, j in ((29, buildpose.L_AN), (31, buildpose.L_AN), (30, buildpose.R_AN), (32, buildpose.R_AN)):
        P[i, :2] = P[j, :2]
    plain = Image.new("L", (512, 768), 0)
    d = ImageDraw.Draw(plain)
    d.ellipse([226, 75, 286, 145], fill=255)
    d.polygon([(216, 165), (296, 165), (270, 430), (242, 430)], fill=255)
    for a, b in (((296, 165), (440, 318)), ((216, 165), (72, 318)), ((270, 430), (330, 690)), ((242, 430), (182, 690))):
        d.line([a, b], fill=255, width=24)
    mask = np.asarray(plain) > 127
    h = 690 - 75
    assert buildpose.beyond_body(mask, P, h) < 0.02
    winged = plain.copy()
    ImageDraw.Draw(winged).polygon([(296, 200), (500, 60), (500, 330)], fill=255)  # a wing off the back
    extra = buildpose.beyond_body(np.asarray(winged) > 127, P, h)
    assert extra >= buildpose.BEYOND_BODY_CAUTION
    m = _measure(figure("down", legs="apart"))
    m["beyond_body"] = extra
    c = buildpose.pose_check(m)
    assert "a cape, wide sleeves, wings, a tail or extra arms" in c["help"] and c["vars"]["beyond_body"] == extra


def test_arm_angles_from_the_joints():
    a = buildpose.arm_angles(_pose(45))
    assert abs(a["left"] - 45) < 0.5 and abs(a["right"] - 45) < 0.5
    a = buildpose.arm_angles(_pose(10))
    assert a["left"] < buildpose.ARM_LINE


def _hand(spread: float, curled: bool = False) -> np.ndarray:
    """21 hand points, fingers up from a wrist at (0, 0): straight and spread by `spread` palm lengths, or curled."""
    P = np.zeros((21, 2))
    P[1:5] = [(-30, -20), (-45, -35), (-55, -50), (-62, -62)]
    for i, base in enumerate((5, 9, 13, 17)):
        x = (i - 1.5) * 20
        if curled:
            P[base:base + 4] = [(x, -100), (x, -125), (x, -110), (x, -95)]
        else:
            fan = (i - 1.5) * spread * 100
            P[base:base + 4] = [(x, -100), (x + fan * 0.4, -140), (x + fan * 0.7, -165), (x + fan, -190)]
    return P


def test_hand_states():
    assert buildpose.hand_state(_hand(0.5))["state"] == "open"
    assert buildpose.hand_state(_hand(0.0))["state"] == "open_together"
    assert buildpose.hand_state(_hand(0.5, curled=True))["state"] == "fist"


def test_a_fist_warns_with_no_finger_bones():
    m = _measure(figure("wide", legs="apart"))
    m["hands"] = {"left": {"state": "fist"}, "right": {"state": "open"}}
    c = buildpose.pose_check(m)
    assert not c["passed"] and "left hand is a fist" in c["help"] and "finger bones" in c["help"]


# --- the wording ---------------------------------------------------------------------------------------------------------


def test_the_wording_asks_for_a_wide_a_pose_and_keeps_the_rest():
    text = buildpose.pose_prompt()
    assert "about 45 degrees" in text and "a third of the body's height apart" in text
    assert "spread apart" in text and "Change only the pose" in text
    assert "glove" not in text.lower()  # gloves are never named unless the picture has them
    bare = buildpose.pose_prompt({"hands": {"left": {"skin_gap": 3.0}, "right": {"skin_gap": 4.0}}, "grey": True})
    assert buildpose.BARE_HANDS.strip() in bare and buildpose.KEEP_GREY.strip() in bare
    # hands that were only not found (hidden, in sleeves) say nothing about gloves: neutral wording (WP-09)
    unseen = buildpose.pose_prompt({"hands": {"left": {"state": "not_found"}, "right": {"state": "not_found"}}})
    assert "glove" not in unseen.lower() and "gauntlet" not in unseen.lower() and buildpose.BARE_HANDS.strip() not in unseen
    unseen = buildpose.pose_prompt({"hands": {"left": {"state": "not_found", "skin_gap": None}, "right": {"state": "not_found"}}})
    assert "glove" not in unseen.lower()
    # measured gloved hands (both far from the face's skin) keep the sentence
    gloved = buildpose.pose_prompt({"hands": {"left": {"skin_gap": 40.0}, "right": {"skin_gap": 35.0}}})
    assert buildpose.GLOVED_HANDS.strip() in gloved
    one = buildpose.pose_prompt({"hands": {"left": {"skin_gap": 40.0}, "right": {"state": "not_found"}}})
    assert "glove" not in one.lower()
    # the no-clothing sentence names no garment, and every route carries it
    assert buildpose.KEEP_WORN.strip() in text
    for word in ("glove", "sock", "boot", "shoe", "gauntlet", "sleeve"):
        assert word not in buildpose.KEEP_WORN.lower()
    assert turnaround.pose_ready_prompt(True).count("closed mouth") == 1


# --- the guard -----------------------------------------------------------------------------------------------------------


def _grey(im: Image.Image) -> Image.Image:
    return im.convert("L").convert("RGB")


def test_guard_keeps_the_same_character_and_names_what_it_could_not_compare():
    a = cut(figure("down", legs="apart"))
    g = buildpose.guard(a, cut(figure("wide", legs="apart")))
    assert g["ok"], g
    assert any("garment" in x for x in g["not_compared"]) and any("face" in x for x in g["not_compared"])
    assert "Not compared:" in buildpose.guard_words(g)


def test_guard_refuses_colour_added_to_a_black_and_white_picture():
    g = buildpose.guard(cut(_grey(figure("down", legs="apart"))), cut(figure("wide", legs="apart")))
    assert not g["ok"] and "colour was added to a black-and-white picture" in g["reasons"]


def test_guard_refuses_changed_colours_and_removed_parts():
    a = figure("wide", legs="apart")
    green = Image.fromarray((np.asarray(a).astype(int)[..., [1, 2, 0]]).astype(np.uint8))
    assert "the colours changed" in buildpose.guard(cut(a), cut(green))["reasons"]
    wide = Image.new("RGB", (700, 768), (235, 235, 235))
    wide.paste(a, (94, 0))
    ImageDraw.Draw(wide).rectangle([0, 200, 140, 330], fill=(180, 60, 60))  # a tool arm off the side
    ImageDraw.Draw(wide).rectangle([560, 200, 700, 330], fill=(180, 60, 60))
    g = buildpose.guard(cut(wide, tol=40), cut(a))
    assert not g["ok"] and "parts of the character were removed" in g["reasons"]


def test_guard_part_colours_and_gloves_with_joints(monkeypatch):
    """With joints for both pictures: gloves on bare hands and a changed forearm are refused."""
    base = figure("wide", legs="apart")
    fig = buildpose.load(cut(base))
    h = fig["height"]
    ys, xs = np.nonzero(fig["mask"])
    top = ys.min()
    # joints in the cropped figure's pixels, from the drawing (helpers: torso 216-296, arms to (60, 330) and (452, 330))
    ox = 256 - (xs.min() + xs.max()) / 2  # the drawing's centre is x 256
    def at(x, y):
        return (x - ox, y - 70 + top)
    P = np.zeros((33, 3))
    P[:, 2] = 0.99
    for i, xy in {buildpose.NOSE: (256, 115), buildpose.L_EYE: (270, 105), buildpose.R_EYE: (242, 105),
                  buildpose.L_SH: (296, 165), buildpose.R_SH: (216, 165), buildpose.L_EL: (376, 248), buildpose.R_EL: (136, 248),
                  buildpose.L_WR: (440, 318), buildpose.R_WR: (72, 318), 17: (446, 326), 19: (450, 328), 18: (66, 326), 20: (62, 328),
                  buildpose.L_HIP: (270, 430), buildpose.R_HIP: (242, 430), buildpose.L_KN: (300, 560), buildpose.R_KN: (212, 560),
                  buildpose.L_AN: (330, 690), buildpose.R_AN: (182, 690)}.items():
        P[i, :2] = at(*xy)
    monkeypatch.setattr(buildpose, "pose_points", lambda rgb: P.copy())
    same = buildpose.guard(cut(base), cut(base))
    assert same["ok"] and "parts" in same["measures"], same
    gloved = base.copy()
    d = ImageDraw.Draw(gloved)
    for x, y in ((440, 318), (72, 318)):
        d.ellipse([x - 22, y - 22, x + 22, y + 22], fill=(20, 20, 20))
    g = buildpose.guard(cut(base), cut(gloved))
    assert not g["ok"] and "gloves were added to bare hands" in g["reasons"], g
    bracer = base.copy()
    ImageDraw.Draw(bracer).line([(376, 248), (440, 318)], fill=(40, 120, 40), width=18)
    g = buildpose.guard(cut(base), cut(bracer))
    assert not g["ok"] and any("forearms" in r for r in g["reasons"]), g
    # a colour already on the character (the trousers' blue on the forearm, as a print shows another of its colours
    # from another angle) is not a change
    sleeved = base.copy()
    shown = base.copy()
    for a, b in (((376, 248), (440, 318)), ((136, 248), (72, 318))):  # the part is both forearms
        ImageDraw.Draw(sleeved).line([a, b], fill=(180, 60, 60), width=24)
        ImageDraw.Draw(shown).line([a, b], fill=(40, 40, 120), width=24)
    g = buildpose.guard(cut(sleeved), cut(shown))
    assert g["ok"] and "forearms" in g["measures"]["explained_by_neighbour"], g
    # ... but a bare forearm that turns into another colour is never explained away (WP-09): something was put on it
    g = buildpose.guard(cut(base), cut(shown))
    assert not g["ok"] and any("bare skin of the forearms" in r for r in g["reasons"]), g


# The stored pose redraws (docs: notes of 2026-11-13): the eye's verdict for each. Run only where the library holds
# them. Without the pose finder only the outline tests apply (colour added, parts removed); with it, all.
STORED_PAIRS = [
    ("soldier 009a", "projects/172faef2-4e20-4751-a507-eabd266a2f4e/assets/f561afe4-0ae2-47d2-b40f-939c636a6e6e", "2017441c-a370-4a9e-8fb2-de93a9d46deb", "009a6407-7b1b-4a4d-909a-5d6f8dc4e35d", False, True),
    ("caped hero", "projects/b5393692-52f3-411b-ada1-7c223a8bf9ce/assets/9e42d0c0-382c-4801-bf39-78d4a9077ac8", "c793a0b9-cae2-4bb0-b7a0-f55fad8729c2", "9e6deff5-17dc-4ff9-9895-fb623d4c76b2", False, False),
    ("knight", "projects/45f36361-e495-4959-8280-9fcdcee53509/assets/2b8cae6a-db85-41f6-b9cd-7fd74bac6f24", "5bbcb393-6c35-4bcb-ad5e-29884b6d76b5", "8a58ad38-4267-4942-9475-4587171828f3", True, True),
    ("TheHeir", "projects/ea2b48ed-390a-41d9-9471-8892e8a46748/assets/818aadfa-eb4c-4ab3-a07b-a831bfd87f9b", "09519218-d5d9-4c32-b735-3e9a9804454c", "1e851466-5b0c-4761-88fe-5f3af0c90921", False, True),
    ("mage", "projects/4f79ce38-4a56-4656-92d4-ede5620a13c9/assets/af4e409e-2f2d-4161-902f-cca4f1931c48", "2b8e6e2d-af1f-41ed-867e-6c2b5c94b140", "c5a034db-42d0-4f26-bfbf-42196453ef33", False, False),
    ("viking", "projects/87cafcd1-54d6-40de-adc9-d36943cf10e1/assets/e40fa231-4db0-4e3f-9714-b656a9b406fb", "c1f9cafa-dcac-4e26-b2b6-3144d0903c7f", "9de3c0f3-ec62-4571-98c7-80d4e802d799", True, True),
    ("soldier 5ac1", "projects/23cbb5a3-ca2e-412c-bd99-9e9d1cfdc324/assets/5a6a8525-c908-4128-8a54-96a1b754688b", "0110297f-d456-4202-85bf-06d6ef0f5dfd", "5ac1051d-15ac-4332-9f5a-bb5e0bdc69bf", False, True),
]


@pytest.mark.parametrize("name,asset,concept,turn,keep,outline_tells", STORED_PAIRS, ids=[p[0] for p in STORED_PAIRS])
def test_stored_pose_redraws(monkeypatch, name, asset, concept, turn, keep, outline_tells):
    orig = LIBRARY / asset / "concept" / concept / "cutout.png"
    posed = LIBRARY / asset / "turnaround" / turn / "pose_ready_cutout.png"
    if not (orig.is_file() and posed.is_file()):
        pytest.skip("the stored pictures are not in this library")
    monkeypatch.undo()  # this one may use the real finders when they are installed
    g = buildpose.guard(orig, posed)
    if buildpose.available("pose"):
        assert g["ok"] is keep, (name, g)
    elif outline_tells:
        assert g["ok"] is keep, (name, g)
    else:  # gloves and bracers need the joints: without them it must say what it did not compare
        assert any("garment" in x for x in g["not_compared"]), (name, g)


# --- step 2 ----------------------------------------------------------------------------------------------------------------


def _png(path: Path) -> Path:
    cut(figure("wide", legs="apart")).save(path)
    return path


class _SC:
    def __init__(self, d: Path, person: Path | None, db=None):
        self.dir = d
        self.asset = SimpleNamespace(category="humanoid", id="asset0001", held_item=None)
        self.settings = SimpleNamespace(stage_instances={"concept": "b"})
        self.ctx = SimpleNamespace(instance="a", clients={"a": object(), "b": object()})
        self.version = SimpleNamespace(params={}, metrics={}, id="ver000001", stage="turnaround")
        self.parent = None
        self.registered: dict = {}
        self.graphs: list = []
        self.person = person
        self.db = db

    def lineage_file(self, key, stages=None):
        return self.person if key == "cutout" else None

    def file(self, key):
        return self.registered.get(key)

    def register(self, key, p):
        self.registered[key] = Path(p)

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    def publish(self):
        pass

    def use_model(self, _m):
        pass

    def prefix(self, tag=""):
        return "p/" + tag

    def copy_in(self, src, name):
        dest = self.dir / name
        dest.write_bytes(Path(src).read_bytes())
        return dest

    async def stage_input(self, _p, name, instance=None):
        return name

    async def comfy(self, graph, instance=None, **_k):
        self.graphs.append((instance, graph))
        return {"elapsed_seconds": 2.0}

    async def download_image(self, _entry, _node, dest, instance=None):
        return _png(Path(dest))

    async def cutout(self, image_path, name, instance=None, **_k):
        dest = self.dir / f"{name}.png"
        dest.write_bytes(Path(image_path).read_bytes())
        return {"cutout": dest, "stats": {"empty": False, "coverage": 0.4}}


@pytest.fixture
def step(tmp_path, monkeypatch):
    monkeypatch.setattr(turnaround.library, "version_file", lambda v, key, _s: (getattr(v, "files_on_disk", {}) or {}).get(key))
    d = tmp_path / "v"
    d.mkdir()
    person = _png(tmp_path / "person_cutout.png")
    return SimpleNamespace(dir=d, person=person, src=_png(tmp_path / "concept.png"), tmp=tmp_path)


def _checks(sc):
    return {c["name"]: c for c in sc.version.metrics.get("checks") or []}


def test_a_kept_redraw_records_the_guard(step):
    sc = _SC(step.dir, step.person)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert out == step.dir / "pose_ready_picture.png" and len(sc.graphs) == 1
    g = _checks(sc)["pose_guard"]
    assert g["passed"] and "kept your character" in g["help"] and "fix" not in g
    assert sc.version.metrics["pose_guard"]["tries"] == 1


def test_a_refused_redraw_is_tried_again_then_the_persons_picture_is_used(step, monkeypatch):
    verdicts = iter([{"ok": False, "reasons": ["gloves were added to bare hands"], "not_compared": [], "measures": {}}] * 3)
    monkeypatch.setattr(buildpose, "guard", lambda a, b: next(verdicts))
    sc = _SC(step.dir, step.person)
    assert asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src)) is None
    assert len(sc.graphs) == 3 and set(sc.registered) == {"pose_refused_picture", "pose_refused_cutout"}
    g = _checks(sc)["pose_guard"]
    assert not g["passed"] and "gloves were added to bare hands" in g["help"] and "second try" in g["help"]
    assert g["fix"] == {"stage": "turnaround", "params": {"pose_ready": True, "pose_keep_refused": "ver000001"},
                        "label": "Use the redraw anyway"}
    # Resume after the refusal: no third redraw
    assert asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src)) is None and len(sc.graphs) == 3


def test_refused_once_then_kept(step, monkeypatch):
    verdicts = iter([{"ok": False, "reasons": ["the colours changed"], "not_compared": [], "measures": {}},
                     {"ok": True, "reasons": [], "not_compared": [], "measures": {}}])
    monkeypatch.setattr(buildpose, "guard", lambda a, b: next(verdicts))
    sc = _SC(step.dir, step.person)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert out == step.dir / "pose_ready_picture_2.png" and sc.version.metrics["pose_guard"]["tries"] == 2
    assert _checks(sc)["pose_guard"]["passed"]


def test_use_the_redraw_anyway(step):
    held = step.tmp / "held"
    held.mkdir()
    files = {"pose_refused_picture": _png(held / "pose_refused_picture.png"), "pose_refused_cutout": _png(held / "pose_refused_cutout.png")}
    refused = SimpleNamespace(id="refused01", stage="turnaround", asset_id="asset0001", files_on_disk=files, params={},
                              metrics={"pose_guard": {"tries": 2, "reasons": ["the face changed"]}})
    other = SimpleNamespace(id="other001", stage="turnaround", asset_id="someone_else", files_on_disk=files, metrics={})
    db = SimpleNamespace(get=lambda _cls, vid: {"refused01": refused, "other001": other}.get(vid))
    sc = _SC(step.dir, step.person, db=db)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True, "pose_keep_refused": "refused01"}, step.src))
    assert out == step.dir / "pose_ready_picture.png" and sc.graphs == []
    g = _checks(sc)["pose_guard"]
    assert not g["passed"] and "You chose to keep" in g["help"] and "the face changed" in g["help"] and "fix" not in g
    # another asset's version is never borrowed: a fresh redraw instead
    sc2 = _SC(step.tmp, step.person, db=db)
    asyncio.run(turnaround._pose_ready_picture(sc2, {"pose_ready": True, "pose_keep_refused": "other001"}, step.src))
    assert len(sc2.graphs) == 1


def test_step_two_carries_the_pose_check(step):
    sc = _SC(step.dir, step.person)
    source = SimpleNamespace(files_on_disk={"cutout": step.person})
    turnaround._pose_for_3d(sc, source, None)
    assert "pose_for_3d" not in _checks(sc)  # without a redraw step 1 carries it
    sc.register("pose_ready_cutout", _png(step.dir / "posed.png"))
    turnaround._pose_for_3d(sc, source, step.dir / "posed.png")
    assert _checks(sc)["pose_for_3d"]["help"].startswith("After the pose redraw:")


def test_guide_entries():
    for name in ("pose_for_3d", "pose_guard"):
        assert guidance.CHECK_GUIDE[name]["severity"] == "warn"


# --- choose_try: keep the better try of the pose redraw ---------------------------------------------------------------


def _t(ok, gap):
    return {"guard_ok": ok, "gap": gap}


def test_choose_try_keeps_the_only_good_try_and_stops():
    r = buildpose.choose_try([_t(True, 0.3)], 1)
    assert r["keep"] == 0 and r["another"] is False


def test_choose_try_asks_for_another_while_the_gap_is_under_the_limit():
    r = buildpose.choose_try([_t(True, 0.222)], 1)
    assert r["keep"] == 0 and r["another"] is True and "0.222" in r["why"]
    assert buildpose.choose_try([_t(True, 0.222)], 0) == {"keep": 0, "another": False, "why": r["why"]}


def test_choose_try_prefers_the_guard_then_the_larger_gap():
    # the first was wide but refused, the second kept the character: the second, even with the smaller gap
    r = buildpose.choose_try([_t(False, 0.34), _t(True, 0.222)], 0)
    assert r["keep"] == 1
    # both kept: the wider one, whichever came first
    assert buildpose.choose_try([_t(True, 0.21), _t(True, 0.26)], 0)["keep"] == 1
    assert buildpose.choose_try([_t(True, 0.26), _t(True, 0.21)], 0)["keep"] == 0
    # a tie keeps the earlier
    assert buildpose.choose_try([_t(True, 0.25), _t(True, 0.25)], 0)["keep"] == 0


def test_choose_try_with_no_try_kept_keeps_nothing_and_retries_only_if_it_can():
    assert buildpose.choose_try([_t(False, 0.3)], 1) == {"keep": None, "another": True, "why": "no try kept the character"}
    assert buildpose.choose_try([_t(False, 0.3), _t(False, 0.4)], 0)["keep"] is None
    assert buildpose.choose_try([_t(False, 0.3), _t(False, 0.4)], 0)["another"] is False
    assert buildpose.choose_try([], 2)["keep"] is None


def test_choose_try_an_unread_gap_counts_least_and_asks_for_nothing_more():
    assert buildpose.choose_try([_t(True, None), _t(True, 0.1)], 0)["keep"] == 1
    r = buildpose.choose_try([_t(True, None)], 1)
    assert r["keep"] == 0 and r["another"] is False


def _gaps(monkeypatch, *values):
    it = iter(values)
    monkeypatch.setattr(turnaround, "_feet_gap", lambda _p: next(it))


def test_feet_too_close_on_the_first_try_asks_for_another_and_keeps_the_wider(step, monkeypatch):
    _gaps(monkeypatch, 0.20, 0.30)
    sc = _SC(step.dir, step.person)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert len(sc.graphs) == 2 and out == step.dir / "pose_ready_picture_2.png"
    m = sc.version.metrics["pose_guard"]
    assert m["tries"] == 2 and m["gaps"] == [0.20, 0.30] and m["kept_try"] == 2 and m["ok"] is True
    assert _checks(sc)["pose_guard"]["passed"]


def test_feet_far_enough_on_the_first_try_draws_no_second(step, monkeypatch):
    _gaps(monkeypatch, 0.31)
    sc = _SC(step.dir, step.person)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert len(sc.graphs) == 1 and out == step.dir / "pose_ready_picture.png"
    assert sc.version.metrics["pose_guard"]["kept_try"] == 1


def test_both_tries_too_close_keeps_the_wider_not_the_last(step, monkeypatch):
    _gaps(monkeypatch, 0.22, 0.19, 0.21)
    sc = _SC(step.dir, step.person)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert len(sc.graphs) == 1 + turnaround.POSE_GUARD_RETRIES == 3 and out == step.dir / "pose_ready_picture.png"
    m = sc.version.metrics["pose_guard"]
    assert m["kept_try"] == 1 and "may still join" in m["why"]
    # the card says how many tries were drawn, which was kept and why
    help_ = _checks(sc)["pose_guard"]["help"]
    assert "redrawn 3 times; try 1 was kept" in help_ and "may still join" in help_


def test_an_unread_gap_asks_for_nothing_more(step, monkeypatch):
    _gaps(monkeypatch, None)
    sc = _SC(step.dir, step.person)
    asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert len(sc.graphs) == 1


def test_a_refused_close_first_try_and_a_kept_wide_second_keeps_the_second(step, monkeypatch):
    verdicts = iter([{"ok": False, "reasons": ["the colours changed"], "not_compared": [], "measures": {}},
                     {"ok": True, "reasons": [], "not_compared": [], "measures": {}}])
    monkeypatch.setattr(buildpose, "guard", lambda a, b: next(verdicts))
    _gaps(monkeypatch, 0.35, 0.25)       # the refused one is wider, but a refused redraw never beats a kept one
    sc = _SC(step.dir, step.person)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert out == step.dir / "pose_ready_picture_2.png" and sc.version.metrics["pose_guard"]["kept_try"] == 2


def test_a_third_try_is_drawn_when_two_were_too_close_and_it_is_kept_when_it_clears(step, monkeypatch):
    _gaps(monkeypatch, 0.21, 0.22, 0.28)
    sc = _SC(step.dir, step.person)
    out = asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert len(sc.graphs) == 3 and out == step.dir / "pose_ready_picture_3.png"
    assert sc.version.metrics["pose_guard"]["kept_try"] == 3
    assert "redrawn 3 times; try 3 was kept" in _checks(sc)["pose_guard"]["help"]


def test_one_try_says_nothing_about_tries(step, monkeypatch):
    _gaps(monkeypatch, 0.31)
    sc = _SC(step.dir, step.person)
    asyncio.run(turnaround._pose_ready_picture(sc, {"pose_ready": True}, step.src))
    assert "redrawn" not in _checks(sc)["pose_guard"]["help"]


def test_guard_feet_bare_stay_bare_and_footwear_stays(monkeypatch):
    """WP-09: footwear added to bare feet, and footwear or gloves taken off, are refused; the same feet pass."""
    base = figure("wide", legs="apart")
    fig = buildpose.load(cut(base))
    ys, xs = np.nonzero(fig["mask"])
    top = ys.min()
    ox = 256 - (xs.min() + xs.max()) / 2

    def at(x, y):
        return (x - ox, y - 70 + top)
    P = np.zeros((33, 3))
    P[:, 2] = 0.99
    for i, xy in {buildpose.NOSE: (256, 115), buildpose.L_EYE: (270, 105), buildpose.R_EYE: (242, 105),
                  buildpose.L_SH: (296, 165), buildpose.R_SH: (216, 165), buildpose.L_EL: (376, 248), buildpose.R_EL: (136, 248),
                  buildpose.L_WR: (440, 318), buildpose.R_WR: (72, 318), 17: (446, 326), 19: (450, 328), 18: (66, 326), 20: (62, 328),
                  buildpose.L_HIP: (270, 430), buildpose.R_HIP: (242, 430), buildpose.L_KN: (300, 560), buildpose.R_KN: (212, 560),
                  buildpose.L_AN: (340, 690), buildpose.R_AN: (172, 690),
                  29: (350, 700), 31: (354, 712), 30: (162, 700), 32: (158, 712)}.items():
        P[i, :2] = at(*xy)
    monkeypatch.setattr(buildpose, "pose_points", lambda rgb: P.copy())

    def feet(colour, r):
        im = base.copy()
        for x in (352, 160):
            ImageDraw.Draw(im).ellipse([x - r, 706 - r, x + r, 706 + r], fill=colour)
        return im
    skin, boot = (230, 190, 150), (30, 30, 30)
    bare, booted = feet(skin, 16), feet(boot, 20)
    same = buildpose.guard(cut(bare), cut(bare))
    assert same["ok"] and "feet" in same["measures"]["skin"], same
    assert buildpose.guard(cut(booted), cut(booted))["ok"]
    g = buildpose.guard(cut(bare), cut(booted))
    assert not g["ok"] and "footwear was added to bare feet" in g["reasons"], g
    g = buildpose.guard(cut(booted), cut(bare))
    assert not g["ok"] and "the footwear was taken off" in g["reasons"], g
    # a bare foot turned another colour that the figure already has (the trousers' blue) is no longer forgiven
    g = buildpose.guard(cut(bare), cut(feet((40, 40, 120), 20)))
    assert not g["ok"] and "footwear was added to bare feet" in g["reasons"], g


def test_redraw_guard_words_and_check_are_plain():
    ok = {"ok": True, "reasons": [], "not_compared": []}
    bad = {"ok": False, "reasons": ["footwear was added to bare feet"], "not_compared": ["the feet"]}
    c = buildpose.redraw_guard_check("hand_guard", "The hand redraw", ok, False, "unused")
    assert c["passed"] and "kept what your character wears" in c["help"]
    c = buildpose.redraw_guard_check("redraw_guard", "The redraw", bad, True, "It is kept.")
    assert not c["passed"] and c["severity"] == "warn"
    assert "footwear was added to bare feet" in c["help"] and "second try" in c["help"] and "It is kept." in c["help"]
    assert "Not compared: the feet" in c["help"]
