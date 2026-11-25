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


def test_a_build_whose_input_view_drifted_darker_than_step1_is_corrected_toward_step1(tmp_path):
    # Fails on master: the quarter input came out 8 L darker than step 1 (over DRIFT_MAX), so the old match skipped it
    # and the model kept the dark tone. The target is now step 1's own lightness at the front turn.
    glb, png, tex = _box_glb(tmp_path)
    step1 = _picture_of(glb, tex, tmp_path, l_shift=0.0, name="step1.png")
    build = _picture_of(glb, tex, tmp_path, l_shift=-8.0, name="build.png")
    from PIL import Image

    dark = colourmatch.apply(tex, {"mode": "l_shift", "shift": [-8.0, 0, 0]})  # the model's base colour, 8 L too dark
    Image.fromarray(face.to_uint8(dark)).save(png)
    gap_before, _ = _front_gap(glb, png, step1)
    assert gap_before > 6.0
    rec = colourmatch.match_base_colour(glb, build, png, view="quarter", step1=step1)
    assert rec["lightness"]["drift"] < -colourmatch.DRIFT_MAX and rec["lightness"]["target"] == "step1"
    assert rec["applied"] and rec["lightness"]["mode"] == "l_shift"
    gap_after, _ = _front_gap(glb, png, step1)
    assert abs(gap_after) < 2.0


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


# --- the roof lift (buildings only; bakeoff/pl15r/PASSMARK.md) -----------------------------------------------------


def _house(tmp_path, roof=0.45, wall=0.55, name="house"):
    """A gabled house (convex pentagonal prism, ridge along x, front gable towards +z), one UV strip per plane, a plain
    wall colour and a plain roof colour. -> (glb, png, tex, roof_columns, wall_columns)."""
    import trimesh
    from PIL import Image

    prof = [(-0.8, 0.0), (0.8, 0.0), (0.8, 0.7), (0.0, 1.2), (-0.8, 0.7)]  # (z, y) of the gable outline
    quads = []  # polygons as lists of 3D points
    quads.append([(-1, y, z) for z, y in prof])   # end at -x
    quads.append([(1, y, z) for z, y in prof])    # end at +x
    for i in range(5):
        (z0, y0), (z1, y1) = prof[i], prof[(i + 1) % 5]
        quads.append([(-1, y0, z0), (1, y0, z0), (1, y1, z1), (-1, y1, z1)])
    centre = np.array([0.0, 0.5, 0.0])
    verts, faces_, groups = [], [], []
    for gi, poly in enumerate(quads):
        poly = np.array(poly, float)
        for k in range(1, len(poly) - 1):
            tri = poly[[0, k, k + 1]]
            nrm = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            if np.dot(nrm, tri.mean(0) - centre) < 0:
                tri = tri[[0, 2, 1]]
            faces_.append([len(verts), len(verts) + 1, len(verts) + 2])
            verts.extend(tri)
            groups.append(gi)
    hull = trimesh.Trimesh(np.array(verts), np.array(faces_), process=False)
    group = np.array(groups)
    keys = list(range(len(quads)))
    G = len(keys)
    n = hull.face_normals
    uv = np.zeros((len(hull.vertices), 2))
    for i, f in enumerate(hull.faces):
        u0 = group[i] / G
        uv[f, 0] = u0 + 0.01 + 0.5 / G * (hull.vertices[f, 0] + hull.vertices[f, 2] + 2) / 4
        uv[f, 1] = 0.1 + 0.8 * (hull.vertices[f, 1] + hull.vertices[f, 2] * 0.3 + 0.5) / 2.0
    up = np.array([float(n[group == g][0][1]) for g in keys])
    roof_cols = [g for g in range(G) if 0.3 < up[g] < 0.99]
    wall_cols = [g for g in range(G) if abs(up[g]) < 0.1]
    size = 256
    tex = np.full((size, size, 3), 0.5)
    rng = np.random.default_rng(1)
    for g in range(G):
        v = roof if g in roof_cols else wall
        tex[:, int(g / G * size):int((g + 1) / G * size)] = v
    tex = np.clip(tex + rng.normal(0, 0.005, tex.shape), 0, 1)
    hull.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(face.to_uint8(tex)))
    glb = tmp_path / f"{name}.glb"
    hull.export(glb)
    png = tmp_path / f"{name}.png"
    Image.fromarray(face.to_uint8(tex)).save(png)
    return glb, png, tex, roof_cols, wall_cols, G


def _house_picture(glb, tex, tmp_path):
    return _picture_of(glb, tex, tmp_path, l_shift=0.0, name="house_picture.png")


def _run(glb, png, picture, route, tmp_path, tag):
    import shutil

    g, p = tmp_path / f"{tag}.glb", tmp_path / f"{tag}.png"
    shutil.copy(glb, g)
    shutil.copy(png, p)
    rec = colourmatch.match_base_colour(g, picture, p, view="front", route=route)
    return rec, face.read_rgb(p)


def test_a_planted_dark_roof_island_is_lifted_and_the_walls_are_not(tmp_path):
    good = _house(tmp_path, roof=0.45, name="good")
    picture = _house_picture(good[0], good[2], tmp_path)
    dark = _house(tmp_path, roof=0.06, name="dark")  # same shape, roof baked far too dark
    rec, lifted = _run(dark[0], dark[1], picture, "buildings", tmp_path, "lifted")
    rec0, plain = _run(dark[0], dark[1], picture, None, tmp_path, "plain")
    assert rec["roof_lift"]["mode"] == "roof_lift" and 5.0 < rec["roof_lift"]["shift"] <= colourmatch.ROOF_CAP
    size, G = lifted.shape[0], good[5]
    L_old, L_new = face.to_lab(plain)[..., 0], face.to_lab(lifted)[..., 0]
    roof = np.zeros(lifted.shape[:2], bool)
    walls = np.zeros_like(roof)
    for g in dark[3]:
        roof[:, int(g / G * size):int((g + 1) / G * size)] = True
    for g in dark[4]:
        walls[:, int(g / G * size):int((g + 1) / G * size)] = True
    from vgac import imaging

    covered = imaging.rasterise_uv(imaging.load_uv_mesh(dark[0]), size)["labels"] > 0  # texels some triangle samples
    roof, walls = roof & covered, walls & covered
    assert L_new[roof].mean() > L_old[roof].mean() + 15.0
    assert np.array_equal(lifted[walls], plain[walls])  # walls are not up-facing: not one texel moved


def test_a_correct_roof_and_other_routes_are_left_exactly_as_today(tmp_path):
    good = _house(tmp_path, roof=0.45, name="good")
    picture = _house_picture(good[0], good[2], tmp_path)
    rec, out = _run(good[0], good[1], picture, "buildings", tmp_path, "b")
    assert rec["roof_lift"]["mode"] == "none" and np.array_equal(out, face.read_rgb(good[1]))
    dark = _house(tmp_path, roof=0.06, name="dark")
    _, plain = _run(dark[0], dark[1], picture, None, tmp_path, "plain")
    for route in ("prop_rigid", "humanoid", "environment"):
        rec, out = _run(dark[0], dark[1], picture, route, tmp_path, f"r_{route}")
        assert "roof_lift" not in rec and np.array_equal(out, plain)


def test_only_up_facing_islands_count_as_roof(tmp_path):
    from vgac import imaging

    glb = _house(tmp_path)[0]
    uvm = imaging.load_uv_mesh(glb)
    roofs = colourmatch.roof_islands(uvm)
    assert len(roofs) == 2 and all(uvm["up"][uvm["island"] == i].min() > 0.3 for i in roofs)


def test_only_the_building_route_gets_the_roof_lift():
    import inspect

    from vgac import meshgeom
    from vgac.stages import mesh as mesh_stage

    # the build stage hands the colour match its category's route, and only the buildings route is lifted
    assert "route=meshgeom.route_of(sc.asset.category)" in inspect.getsource(mesh_stage._trellis_build)
    lifted = {c for c in meshgeom.ROUTE_OF if meshgeom.route_of(c) == colourmatch.ROOF_ROUTE}
    assert lifted == {"environment"}
    assert all(meshgeom.route_of(c) != colourmatch.ROOF_ROUTE for c in ("humanoid", "prop_rigid", "vehicle", "foliage", "quadruped"))
    assert meshgeom.route_of(None) != colourmatch.ROOF_ROUTE


def test_a_picture_of_the_side_is_found_when_the_front_turn_does_not_match(tmp_path):
    # Fails on master: the turn search covered only the view's turn +/- 30 degrees and its mirror, so a picture of
    # turn 90 was never tried for the front and the lightness was taken from the wrong side.
    from vgac import texfidelity

    glb, png, tex = _box_glb(tmp_path)
    mesh = face.load_mesh(glb)
    cam = texfidelity._frame_camera(mesh, 90.0, 300.0, 512)
    r = face.render_view(mesh, tex, cam, background=0.0)
    from PIL import Image

    picture = tmp_path / "side.png"
    Image.fromarray(np.dstack([face.to_uint8(r["image"]), (r["mask"] * 255).astype(np.uint8)])).save(picture)
    pic, pm = texfidelity.load_picture(picture)
    lp = colourmatch.lightness_plan(mesh, tex, pic, pm, "front")
    assert lp["yaw"] in (90.0, 270.0)


# --- the chroma and hue stage (four-view humanoid builds only) -----------------------------------------------------


def _tinted(tex, a, b):
    """tex with a and b added to a* and b* (a colour cast), L* kept."""
    lab = face.to_lab(tex)
    lab[..., 1] += a
    lab[..., 2] += b
    return colourmatch.from_lab(lab)


def test_apply_chroma_keeps_lightness_and_neutral_texels():
    rgb = np.array([[0.5, 0.5, 0.5], [0.6, 0.3, 0.2]])
    out = colourmatch.apply_chroma(rgb, {"mode": "chroma", "scale": 1.3, "rotate": 10.0})
    assert np.abs(out[0] - rgb[0]).max() < 0.01  # a neutral texel stays neutral
    assert abs(face.to_lab(out)[1, 0] - face.to_lab(rgb)[1, 0]) < 1.0
    assert colourmatch.apply_chroma(rgb, {"mode": "none"}) is rgb


def test_the_chroma_stage_pulls_a_colour_cast_towards_the_picture(tmp_path):
    from vgac import texfidelity

    glb, png, tex = _box_glb(tmp_path, level=0.45)
    from PIL import Image

    picture = _picture_of(glb, _tinted(tex, 8.0, -4.0), tmp_path, l_shift=0.0)  # the picture's own, milder tint
    Image.fromarray(face.to_uint8(_tinted(tex, 18.0, 6.0))).save(png)  # the build has a red cast the picture does not
    pic, pm = texfidelity.load_picture(picture)
    mesh = face.load_mesh(glb)

    def cast():
        lp = colourmatch.lightness_plan(mesh, face.read_rgb(png), pic, pm, "front")
        return colourmatch.chroma_plan(mesh, face.read_rgb(png), pic, pm, lp["yaw"], lp["iou"])

    before = cast()
    assert before["mode"] == "chroma" and before["scale"] < 1.0 and abs(before["rotate"]) > 2.0
    rec = colourmatch.match_base_colour(glb, picture, png, chroma=True)
    assert rec["chroma"]["mode"] == "chroma" and rec["applied"]
    after = cast()
    assert after["render_chroma"] < before["render_chroma"]


def test_the_chroma_stage_is_off_unless_asked_for(tmp_path):
    glb, png, tex = _box_glb(tmp_path, level=0.45)
    from PIL import Image

    picture = _picture_of(glb, tex, tmp_path, l_shift=0.0)
    Image.fromarray(face.to_uint8(_tinted(tex, 18.0, 6.0))).save(png)
    before = png.read_bytes()
    rec = colourmatch.match_base_colour(glb, picture, png)
    assert "chroma" not in rec and not rec["applied"] and png.read_bytes() == before
