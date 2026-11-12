"""Start from your own images, without a server: safe intake (uploads.py), readiness and photo checks and colours
(readiness.py), the reference-picture graph and the style text clean-up. Pillow and numpy only."""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image

from own_images_helpers import BIG, GREY, b64, cut, data_url, figure
from vgac import guidance, reference, readiness, uploads
from vgac.graphs.qwen_edit import edit_graph
from vgac.stages.style import clean_description

HUMANOID = {"id": "humanoid", "pose_block": "x"}


# --- safe intake ----------------------------------------------------------------------------------------------


def test_bad_base64_and_empty_are_plain_errors():
    for bad in ("", "   ", "not base64 at all!!", "data:image/png,abc", "data:image/png;base64,@@@"):
        with pytest.raises(uploads.UploadError) as e:
            uploads.decode_image(bad)
        assert "picture" in str(e.value) and "Traceback" not in str(e.value)


def test_non_images_and_wrong_formats_are_refused():
    with pytest.raises(uploads.UploadError, match="not a picture"):
        uploads.decode_image(base64.b64encode(b"hello, this is text" * 20).decode())
    with pytest.raises(uploads.UploadError, match="not a picture"):
        uploads.decode_image(base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode())  # a PNG signature, no picture
    for fmt, word in (("GIF", "a GIF"), ("BMP", "a BMP"), ("TIFF", "a TIFF")):
        with pytest.raises(uploads.UploadError, match=word):
            uploads.decode_image(b64(figure(), fmt))


def test_png_jpeg_webp_and_data_urls_are_taken():
    for fmt in ("PNG", "JPEG", "WEBP"):
        im = uploads.decode_image(b64(figure(), fmt))
        assert im.mode == "RGB" and im.size == (512, 768)
    assert uploads.decode_image(data_url(figure(), "JPEG")).size == (512, 768)
    # The data: URL's type is not trusted: a GIF labelled as a PNG is still a GIF.
    with pytest.raises(uploads.UploadError, match="GIF"):
        uploads.decode_image("data:image/png;base64," + b64(figure(), "GIF"))


def test_oversized_files_and_too_many_pixels(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_BYTES", 2000)
    monkeypatch.setattr(uploads, "MAX_B64_CHARS", 1_000_000)
    noisy = Image.fromarray(np.random.default_rng(1).integers(0, 255, (200, 200, 3), dtype=np.uint8))
    with pytest.raises(uploads.UploadError, match="25 MB"):
        uploads.decode_image(b64(noisy))
    monkeypatch.setattr(uploads, "MAX_B64_CHARS", 100)
    with pytest.raises(uploads.UploadError, match="25 MB"):
        uploads.decode_image(b64(noisy))
    monkeypatch.undo()
    # A decompression bomb: a tiny file that claims a huge picture is refused before its pixels are decoded.
    monkeypatch.setattr(uploads, "MAX_PIXELS", 10_000)
    with pytest.raises(uploads.UploadError, match="too large to open safely"):
        uploads.decode_image(b64(Image.new("RGB", (400, 400), (1, 2, 3))))
    # Pillow's own guard is on and turned into an error.
    assert Image.MAX_IMAGE_PIXELS is not None and Image.MAX_IMAGE_PIXELS <= 50_000_000


def test_too_small_is_refused():
    with pytest.raises(uploads.UploadError, match="too small"):
        uploads.decode_image(b64(Image.new("RGB", (16, 16))))


def test_exif_orientation_applied_and_metadata_dropped(tmp_path):
    im = Image.new("RGB", (300, 100), (200, 30, 30))
    im.paste((30, 30, 200), (0, 0, 50, 100))  # blue on the left
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 clockwise to view
    exif[0x8825] = {2: (1.0, 2.0, 3.0)}  # a GPS block that must not survive
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif.tobytes(), quality=95)
    out = uploads.decode_image(base64.b64encode(buf.getvalue()).decode())
    assert out.size == (100, 300)  # turned the right way up
    r, g, b = out.getpixel((50, 10))  # the blue band is now at the top
    assert b > 150 and r < 100
    assert not out.info and not out.getexif()
    p = uploads.save_png(out, tmp_path, "image.png")
    again = Image.open(p)
    assert again.format == "PNG" and not again.getexif() and "icc_profile" not in again.info and "exif" not in again.info


def test_shrink_transparency_and_fixed_names(tmp_path):
    big = uploads.decode_image(b64(Image.new("RGB", (4000, 1000), (9, 9, 9))))
    assert big.size == (2048, 512)
    see_through = uploads.decode_image(b64(cut(figure())))
    assert see_through.mode == "RGBA"
    opaque = Image.new("RGBA", (64, 64), (1, 2, 3, 255))
    assert uploads.decode_image(b64(opaque)).mode == "RGB"
    flat = uploads.flatten(see_through)
    assert flat.mode == "RGB" and flat.getpixel((2, 2)) == GREY
    for bad in ("../x.png", "Image.png", "a/b.png", "x.jpg", "image.png.exe"):
        with pytest.raises(ValueError):
            uploads.save_png(flat, tmp_path, bad)
    assert uploads.save_png(flat, tmp_path, "supplied_back.png").parent == tmp_path.resolve()


# --- readiness ---------------------------------------------------------------------------------------------------


def _checks(im, category=HUMANOID):
    return {c["name"]: c for c in readiness.concept_checks(cut(im), category)}


def test_a_pose_figure_is_ready():
    c = _checks(figure("a", **BIG))
    assert all(x["passed"] for x in c.values()), c
    assert set(c) == {"one_subject", "in_frame", "big_enough", "detail_size", "arms_away"}
    assert all(x["severity"] == "warn" for x in c.values())


def test_t_pose_passes_and_arms_down_warns_with_the_redraw_fix():
    assert _checks(figure("t"))["arms_away"]["passed"]
    arms = _checks(figure("down"))["arms_away"]
    assert not arms["passed"] and arms["severity"] == "warn"
    assert arms["fix_label"] == "Redraw it ready for 3D"
    assert arms["fix"] == {"stage": "concept", "op": "refine", "instruction": readiness.HUMANOID_REDRAW, "params": {},
                           "label": "Redraw it ready for 3D"}
    assert "arms are close to the body" in arms["help"]


def test_two_subjects_cropped_and_small_pictures_warn():
    two = Image.new("RGB", (900, 768), GREY)
    two.paste(figure("a", size=(450, 768)).crop((0, 0, 450, 768)), (0, 0))
    two.paste(figure("a", size=(450, 768)).crop((0, 0, 450, 768)), (450, 0))
    one = _checks(two)["one_subject"]
    assert not one["passed"] and one["value"] == 2 and "2 separate things" in one["help"]
    cropped = _checks(figure("a", offset=(0, 120)))["in_frame"]
    assert not cropped["passed"] and "bottom" in cropped["help"] and cropped["fix"]["op"] == "refine"
    small = _checks(figure("a", size=(200, 300), scale=0.35))["big_enough"]
    assert not small["passed"] and small["value"] < 400 and "pixels tall" in small["help"]
    empty = readiness.concept_checks(Image.new("RGBA", (64, 64)), HUMANOID)
    assert len(empty) == 1 and not empty[0]["passed"] and "could not find a subject" in empty[0]["help"]


def test_props_measure_across_and_skip_the_arms_check():
    prop = reference.category("prop_rigid")
    c = _checks(figure("down"), prop)
    assert "arms_away" not in c and "pixels across" in c["big_enough"]["help"]


def test_redraw_instruction_uses_each_category_pose_block():
    assert readiness.redraw_instruction(reference.category("humanoid")) == (
        "Redraw this same character with the same design, colours and outfit, standing in a relaxed A-pose with each hand "
        "open and empty, back of the hand toward the camera, fingers straight and slightly spread, nothing held in "
        "either hand and no hand in a pocket or hidden, whole body in frame, plain light grey background, even lighting")
    for cid in ("quadruped", "prop_rigid", "vehicle"):
        cat = reference.category(cid)
        text = readiness.redraw_instruction(cat)
        assert text.startswith("Redraw this same") and cat["pose_block"] in text


def test_material_photo_checks():
    rng = np.random.default_rng(3)
    even = Image.fromarray(rng.integers(90, 160, (256, 256, 3), dtype=np.uint8))
    c = {x["name"]: x for x in readiness.photo_checks(even)}
    assert c["even_lighting"]["passed"] and c["straight_on"]["passed"]
    ramp = np.clip(np.asarray(even, dtype=float) * np.linspace(0.2, 1.3, 256)[None, :, None], 0, 255).astype(np.uint8)
    lit = {x["name"]: x for x in readiness.photo_checks(Image.fromarray(ramp))}
    assert not lit["even_lighting"]["passed"] and "brighter" in lit["even_lighting"]["help"] and "fix" not in lit["even_lighting"]
    # Fine detail at the top, smooth at the bottom: a photo of the ground taken at an angle.
    fine = rng.integers(0, 255, (256, 256), dtype=np.uint8).astype(float)
    blur = np.asarray(Image.fromarray(fine.astype(np.uint8)).resize((16, 16)).resize((256, 256), Image.Resampling.BICUBIC), dtype=float)
    mix = np.concatenate([fine[:128], blur[128:]], axis=0)
    angled = {x["name"]: x for x in readiness.photo_checks(Image.fromarray(mix.astype(np.uint8)).convert("RGB"))}
    assert not angled["straight_on"]["passed"] and "at an angle" in angled["straight_on"]["help"]


# --- colours -----------------------------------------------------------------------------------------------------


def test_palette_from_a_synthetic_picture():
    im = Image.new("RGB", (300, 300), GREY)
    im.paste((200, 20, 20), (30, 30, 180, 270))  # red, the largest
    im.paste((20, 160, 40), (180, 30, 270, 150))  # green
    im.paste((20, 40, 200), (180, 150, 270, 270))  # blue
    out = readiness.palette(im, 8)
    assert out["background"] == "#ebebeb"
    assert out["palette"][:1] == ["#c81414"]
    assert {"#c81414", "#14a028", "#1428c8"} <= set(out["palette"])
    shares = [s["share"] for s in out["swatches"]]
    assert shares == sorted(shares, reverse=True) and abs(sum(shares) - 1) < 0.01
    assert "#ebebeb" not in out["palette"]
    keep_bg = readiness.palette(im, 8, ignore_background=False)
    assert keep_bg["palette"][:2] == ["#c81414", "#ebebeb"] and keep_bg["background"] is None
    # See-through pixels never count.
    rgba = im.convert("RGBA")
    rgba.paste((0, 255, 255, 0), (0, 0, 300, 30))
    assert "#00ffff" not in readiness.palette(rgba, 16)["palette"]
    with pytest.raises(ValueError):
        readiness.palette(im, 5)
    assert len(readiness.palette(Image.fromarray(np.random.default_rng(2).integers(0, 255, (64, 64, 3), dtype=np.uint8)), 32)["palette"]) <= 32


# --- reference picture graph and style text ------------------------------------------------------------------------


def test_edit_graph_wires_a_reference_as_picture_two():
    plain = edit_graph("src.png", "red cloak", 1, "p")
    assert "img2" not in plain and "image2" not in plain["pos"]["inputs"] and plain["pos"]["inputs"]["prompt"] == "red cloak"
    g = edit_graph("src.png", "make the cloak like this", 1, "p", reference_name="ref.png")
    assert g["img2"]["inputs"]["image"] == "ref.png"
    assert g["pos"]["inputs"]["image2"] == ["sc2", 0] and g["neg"]["inputs"]["image2"] == ["sc2", 0]
    assert g["pos"]["inputs"]["image1"] == ["sc", 0]
    assert "Picture 2" in g["pos"]["inputs"]["prompt"] and "make the cloak like this" in g["pos"]["inputs"]["prompt"]


def test_style_description_is_one_short_paragraph():
    reply = ('```\nHere is the style description:\n"Gouache on rough paper, **soft** shading in two tones, thin dark '
             'outlines, chunky rounded shapes with big heads, warm muted colours."\n```')
    assert clean_description(reply) == ("Gouache on rough paper, soft shading in two tones, thin dark outlines, chunky "
                                         "rounded shapes with big heads, warm muted colours.")
    long = ("Ink on paper with flat colour. " * 30).strip()
    out = clean_description(long)
    assert len(out.split()) <= 80 and out.endswith(".")


def test_guide_sentences_for_the_new_checks_have_no_placeholders_left():
    for name in ("one_subject", "in_frame", "big_enough", "detail_size", "arms_away", "even_lighting", "straight_on"):
        g = guidance.CHECK_GUIDE[name]
        assert g["severity"] == "warn"


def test_a_small_picture_offers_upscale_it():
    """Under readiness.DETAIL_SUBJECT_PX the detail_size check warns and offers "Upscale it" (op upscale)."""
    small = _checks(figure("a"))["detail_size"]  # 512 x 768: the figure is well under 860 px tall
    assert not small["passed"] and small["severity"] == "warn"
    assert small["fix"] == {"stage": "concept", "op": "upscale", "params": {}, "label": "Upscale it"}
    assert "1 megapixel" in small["help"] and "{" not in small["help"]
    assert _checks(figure("a", **BIG))["detail_size"]["passed"]


def test_upscale_plan_reaches_the_target_within_limits():
    from vgac.readiness import UPSCALE_TARGET_PX
    from vgac.stages.concept import UPSCALE_ART_MODEL, UPSCALE_MAX_SIDE, UPSCALE_PHOTO_MODEL, upscale_plan

    p = upscale_plan((768, 1344), 782, photo=True)
    assert p["model"] == UPSCALE_PHOTO_MODEL and 782 * p["factor"] >= UPSCALE_TARGET_PX
    assert upscale_plan((768, 1344), 500, photo=True)["factor"] == round(UPSCALE_TARGET_PX / 500, 3)
    assert (p["width"], p["height"]) == (round(768 * p["factor"]), round(1344 * p["factor"]))
    assert upscale_plan((300, 400), 200, photo=False) == {"factor": 4.0, "width": 1200, "height": 1600, "model": UPSCALE_ART_MODEL}
    assert upscale_plan((1000, 1000), 900, photo=True)["factor"] == 1.25  # always a real step up
    big = upscale_plan((3000, 2000), 100, photo=True)
    assert max(big["width"], big["height"]) <= UPSCALE_MAX_SIDE
