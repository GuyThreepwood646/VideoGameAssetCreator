"""vgac/colourmatch.py: the base colour's lightness moved towards the picture, in the PNG and in the GLB."""
import numpy as np

from vgac import colourmatch, face


def test_lab_round_trip():
    rgb = np.random.default_rng(0).uniform(0.02, 0.98, (50, 3))
    assert np.abs(colourmatch.from_lab(face.to_lab(rgb)) - rgb).max() < 0.01


def test_a_shift_does_not_flatten_detail_at_the_ends_of_the_range():
    rng = np.random.default_rng(5)
    tex = np.clip(0.9 + rng.normal(0, 0.04, (64, 64, 3)), 0, 1)  # bright and detailed: a plain clip would flatten it
    out = colourmatch.apply(tex, {"mode": "l_shift", "shift": [6.0, 0.0, 0.0]})
    assert out.std() > 0.6 * tex.std()
    dark = np.clip(0.06 + rng.normal(0, 0.03, (64, 64, 3)), 0, 1)
    out = colourmatch.apply(dark, {"mode": "l_shift", "shift": [-6.0, 0.0, 0.0]})
    assert out.std() > 0.6 * dark.std() and out.min() >= 0.0 and out.max() <= 1.0


# --- the lightness stage (bakeoff/render-tone/PLAN.md) -----------------------------------------------------------


def _box_glb(tmp_path, level=0.30):
    """A textured box GLB (each face its own strip of the atlas) and its base colour PNG, plain grey with fine noise."""
    import trimesh
    from PIL import Image

    box = trimesh.creation.box(extents=(1.0, 1.4, 0.6))
    box.unmerge_vertices()  # one UV per corner of each face
    uv = np.zeros((len(box.vertices), 2))
    for i, f in enumerate(box.faces):
        k = i // 2
        u0 = k / 6.0
        uv[f, 0] = u0 + 0.02 + 0.12 * (box.vertices[f, 0] + box.vertices[f, 2] + 1.2) / 2.4
        uv[f, 1] = 0.1 + 0.8 * (box.vertices[f, 1] + 0.7) / 1.4
    rng = np.random.default_rng(3)
    tex = np.clip(level + rng.normal(0, 0.01, (256, 256, 3)), 0, 1)
    png = tmp_path / "base_color.png"
    Image.fromarray(face.to_uint8(tex)).save(png)
    box.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(face.to_uint8(tex)))
    glb = tmp_path / "model.glb"
    box.export(glb)
    return glb, png, tex


def _picture_of(glb, tex, tmp_path, l_shift, extra_share=0.0, shadow=0.0, name="picture.png"):
    """The box seen from the front with its lightness moved by l_shift, a slab added beside it (extra_share of its
    width, so the outline fit drops), and the right half darkened by `shadow` L (a painted shadow side)."""
    from PIL import Image

    from vgac import texfidelity

    mesh = face.load_mesh(glb)
    cam = texfidelity._frame_camera(mesh, 0.0, 300.0, 512)
    r = face.render_view(mesh, colourmatch.apply(tex, {"mode": "l_shift", "shift": [l_shift, 0, 0]}), cam, background=0.0)
    mask = r["mask"].copy()
    img = r["image"].copy()
    ys, xs = np.nonzero(mask)
    if extra_share:
        w = int((xs.max() - xs.min()) * extra_share)
        slab = np.zeros_like(mask)
        slab[ys.min():ys.max(), xs.max():xs.max() + w] = True
        img[slab] = img[mask].mean(axis=0)
        mask |= slab
    if shadow:
        right = mask & (np.arange(512)[None, :] > (xs.min() + xs.max()) / 2)
        img[right] = colourmatch.apply(img[right], {"mode": "l_shift", "shift": [-shadow, 0, 0]})
    rgba = np.dstack([face.to_uint8(img), (mask * 255).astype(np.uint8)])
    p = tmp_path / name
    Image.fromarray(rgba).save(p)
    return p


def _front_gap(glb, png, picture):
    from vgac import texfidelity

    mesh = face.load_mesh(glb)
    pic, pm = texfidelity.load_picture(picture)
    lp = colourmatch.lightness_plan(mesh, face.read_rgb(png), pic, pm, "front")
    return lp["picture_L"] - lp["render_L"], lp


def test_a_dark_build_with_a_loose_outline_is_brought_to_the_picture(tmp_path):
    # Fails on master: the outline fit is under 0.80, so the old match gave up ("fit_too_loose") and left it 12 L dark.
    glb, png, tex = _box_glb(tmp_path)
    picture = _picture_of(glb, tex, tmp_path, l_shift=12.0, extra_share=0.7)
    gap_before, lp = _front_gap(glb, png, picture)
    assert gap_before > 10.0 and 0.5 <= lp["iou"] < 0.8
    rec = colourmatch.match_base_colour(glb, picture, png)
    assert rec["applied"] and rec["lightness"]["mode"] == "l_shift"
    gap_after, _ = _front_gap(glb, png, picture)
    assert abs(gap_after) < 2.0


def test_a_painted_shadow_side_cannot_pull_past_the_cap(tmp_path):
    # Guard: half the picture is painted 40 L darker (its shadow side); the texture matches the lit half. The mean
    # gap is about -20, but the lightness stage never moves more than L_CAP.
    glb, png, tex = _box_glb(tmp_path, level=0.55)
    picture = _picture_of(glb, tex, tmp_path, l_shift=0.0, shadow=40.0)
    mesh = face.load_mesh(glb)
    from vgac import texfidelity

    pic, pm = texfidelity.load_picture(picture)
    lp = colourmatch.lightness_plan(mesh, tex, pic, pm, "front")
    assert lp["gap"] < -colourmatch.L_CAP and lp["capped"]
    assert lp["shift"][0] == -colourmatch.L_CAP


def test_a_matching_build_is_left_alone_by_the_lightness_stage(tmp_path):
    glb, png, tex = _box_glb(tmp_path)
    picture = _picture_of(glb, tex, tmp_path, l_shift=0.0)
    mesh = face.load_mesh(glb)
    from vgac import texfidelity

    pic, pm = texfidelity.load_picture(picture)
    assert colourmatch.lightness_plan(mesh, tex, pic, pm, "front")["mode"] == "none"


def test_a_step2_picture_whose_tone_drifted_from_step1_leaves_the_texture_alone(tmp_path):
    # The build view came out 10 L brighter than the step-1 picture, which the texture already matches: matching the
    # build view would carry the redraw's drift into the model, so the lightness stage is skipped.
    glb, png, tex = _box_glb(tmp_path)
    step1 = _picture_of(glb, tex, tmp_path, l_shift=0.0, name="step1.png")
    build = _picture_of(glb, tex, tmp_path, l_shift=10.0, name="build.png")
    before = face.read_rgb(png)
    rec = colourmatch.match_base_colour(glb, build, png, view="front", step1=step1)
    assert rec["lightness"]["reason"] == "step2_tone_drift" and rec["lightness"]["drift"] > colourmatch.DRIFT_MAX
    assert np.array_equal(face.read_rgb(png), before)


def test_a_step2_picture_that_kept_the_tone_is_still_matched(tmp_path):
    glb, png, tex = _box_glb(tmp_path)
    step1 = _picture_of(glb, tex, tmp_path, l_shift=12.0, name="step1.png")
    build = _picture_of(glb, tex, tmp_path, l_shift=12.0, extra_share=0.7, name="build.png")
    rec = colourmatch.match_base_colour(glb, build, png, view="front", step1=step1)
    assert rec["lightness"]["mode"] == "l_shift" and abs(rec["lightness"]["drift"]) <= colourmatch.DRIFT_MAX


def test_a_neutral_grey_stays_neutral_under_the_lightness_move():
    grey = np.array([[g, g, g] for g in (0.08, 0.3, 0.48, 0.7, 0.92)])
    for dl in (-15.0, -6.0, 6.0, 15.0):
        lab = face.to_lab(colourmatch.apply(grey, {"mode": "l_shift", "shift": [dl, 0.0, 0.0]}))
        assert np.abs(lab[:, 1:]).max() < 1.0


def test_the_corrected_base_colour_is_written_into_the_glb(tmp_path):
    # Fails on master: the correction went to base_color.png only, and the clean-up re-bakes from the GLB's own image.
    from vgac import texfidelity

    glb, png, tex = _box_glb(tmp_path)
    picture = _picture_of(glb, tex, tmp_path, l_shift=12.0, extra_share=0.7)
    rec = colourmatch.match_base_colour(glb, picture, png)
    assert rec["applied"]
    embedded = texfidelity.load_texture(glb, None)
    assert embedded.shape == face.read_rgb(png).shape
    assert np.abs(embedded - face.read_rgb(png)).max() < 1.5 / 255
