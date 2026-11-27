"""Separate eyes (vgac/eyes.py, blender/eyes.py, blender/eye_skin.py)."""
import json
import math
import subprocess

import numpy as np
import pytest
from PIL import Image

import machine_tools
from vgac import eyes, guidance
from vgac.blender import eye_skin
from vgac.blender import eyes as mesh_work


def test_wanted_only_for_a_character_in_a_painted_or_photographic_look():
    assert eyes.wanted("humanoid", "painted", {}, True)
    assert eyes.wanted("humanoid", "realistic_pbr", {}, True)
    assert not eyes.wanted("humanoid", "cel", {}, True)
    assert not eyes.wanted("quadruped", "painted", {}, True)
    assert not eyes.wanted("humanoid", "painted", {}, False)
    assert not eyes.wanted("humanoid", "painted", {"face_detail": False}, True)


def _ring(cx, cy, half_w, half_h):
    # the order of a MediaPipe lid ring: from one corner round the lower lid to the other, back along the upper
    angles = [math.pi + math.pi * i / 8 for i in range(9)] + [math.pi * i / 8 for i in range(1, 8)]
    return np.array([complex(cx + half_w * math.cos(a), cy - half_h * math.sin(a)) for a in angles])


def test_a_closed_or_odd_eye_is_refused():
    px_m, head = 0.0003, 0.19  # a 100 px eye is 3 cm
    good = [_ring(300, 500, 50, 18), _ring(500, 500, 50, 18)]
    irises = [(complex(300, 500), 20.0), (complex(500, 500), 20.0)]
    assert eyes.shape_fault(good, irises, px_m, head) is None
    assert eyes.shape_fault([_ring(300, 500, 50, 4), good[1]], irises, px_m, head) == "closed"
    assert eyes.shape_fault([_ring(300, 500, 15, 6), good[1]], irises, px_m, head) == "odd_shape"
    assert eyes.shape_fault(good, [(complex(900, 500), 20.0), irises[1]], px_m, head) == "odd_shape"


def test_the_corners_are_set_onto_the_models():
    s, t = eyes.onto_corners(0j, 10 + 0j, 100 + 100j, 100 + 120j)
    assert abs(s * 0j + t - (100 + 100j)) < 1e-9
    assert abs(s * (10 + 0j) + t - (100 + 120j)) < 1e-9


def test_the_outline_is_rounded_through_its_points():
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    out = eyes.rounded(square, 16)
    assert len(out) == 16
    for i, corner in enumerate(square):
        assert np.allclose(out[4 * i], corner)


def test_squares_are_placed_in_free_texture_and_none_when_full():
    one = np.array([[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]])
    rects = eyes.place({"uv": one, "faces": np.array([[0, 1, 2], [0, 2, 3]])}, 1024)
    assert rects is not None and len(rects) == 4
    for u0, v0, side in rects:
        assert not (u0 < 0.5 and v0 < 0.5), "on top of the used part"
        assert 0 <= u0 <= 1 - side and 0 <= v0 <= 1 - side
    full = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    assert eyes.place({"uv": full, "faces": np.array([[0, 1, 2], [0, 2, 3]])}, 1024) is None


def test_writing_a_square_changes_only_that_part(tmp_path):
    path = tmp_path / "map.png"
    Image.fromarray(np.zeros((64, 64, 3), np.uint8), "RGB").save(path)
    eyes.write_square(path, [0.5, 0.5, 0.25], np.array([1.0, 0.0, 0.0]))
    out = np.array(Image.open(path))
    assert tuple(out[20, 36]) == (255, 0, 0)  # u 0.5..0.75, v 0.5..0.75 is rows 16..32
    assert tuple(out[50, 10]) == (0, 0, 0)


def test_every_skip_reason_has_plain_words():
    for reason in ("no_face", "no_eyes", "closed", "covered", "odd_shape", "no_room"):
        assert guidance.eyes_skipped(reason) != guidance.EYES_SKIPPED_OTHER
    assert guidance.eyes_skipped("not_closed") == guidance.EYES_SKIPPED_OTHER


def test_the_ball_fitted_to_a_lid_edge_follows_it():
    centre_u, centre_v, centre_w, radius = 0.0, 0.0, 0.03, 0.025
    points = [(u, v, centre_w - mesh_work.dome(u, v, centre_u, centre_v, radius))
              for u, v in mesh_work.resample([(-0.015, 0.0), (0.0, -0.006), (0.015, 0.0), (0.0, 0.006)], 32)]
    cu, cv, cw, r = mesh_work.fit_ball(points, 0.0, 0.0, 0.03, share=0.5)
    miss = [abs(cw - mesh_work.dome(u, v, cu, cv, r) - w) for u, v, w in points]
    assert max(miss) < 0.0006


def test_eye_vertices_and_their_neighbours_in_place_go_to_the_head():
    rects = [[0.1, 0.1, 0.05]]
    uv = [(0.12, 0.12), (0.5, 0.5), (0.151, 0.1)]
    assert eye_skin.in_squares(uv, rects).tolist() == [True, False, True]
    positions = [(0, 0, 0), (1, 0, 0), (1, 0, 0)]
    assert eye_skin.with_same_place(positions, np.array([False, True, False])).tolist() == [False, True, True]
    assert eye_skin.head_name(["mixamorig:Hips", "mixamorig:Head"]) == "mixamorig:Head"
    assert eye_skin.head_name(["Extra_01"]) is None


def _face_like_surface(path):
    """A curved front of a 20 cm head with edges of about 8 mm, as a GLB with texture coordinates."""
    import trimesh

    n = 21
    xs, ys = np.linspace(-0.08, 0.08, n), np.linspace(-0.08, 0.08, n)
    verts = np.array([[x, y, math.sqrt(0.1 ** 2 - x * x * 0.5 - y * y * 0.5)] for y in ys for x in xs])
    faces = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            faces += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    uv = np.array([[0.05 + 0.6 * i / (n - 1), 0.05 + 0.6 * j / (n - 1)] for j in range(n) for i in range(n)])
    material = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.new("RGB", (64, 64), (180, 140, 120)))
    mesh = trimesh.Trimesh(verts, np.array(faces), process=False,
                           visual=trimesh.visual.TextureVisuals(uv=uv, material=material))
    mesh.export(path)
    return verts


def test_blender_cuts_the_openings_and_leaves_closed_surfaces(tmp_path):
    exe = machine_tools.blender()
    if exe is None:
        pytest.skip("Blender not found")
    src, dst = tmp_path / "head.glb", tmp_path / "out.glb"
    _face_like_surface(src)
    target = [0.0, 0.0, 0.1]
    job = {"camera": {"target": target, "right": [1, 0, 0], "up": [0, 1, 0], "forward": [0, 0, -1]}, "eyes": []}
    for i, u in enumerate((-0.032, 0.032)):
        outline = [[u + 0.015 * math.cos(a), 0.006 * math.sin(a)] for a in np.linspace(0, 2 * math.pi, 48, endpoint=False)]
        job["eyes"].append({"outline": outline, "centre": [u, 0.0], "width": 0.03, "half": 0.018,
                            "lid": 0.1 - math.sqrt(0.01 - u * u * 0.5), "eye_rect": [0.7 + 0.1 * i, 0.7, 0.08],
                            "rim_rect": [0.7 + 0.1 * i, 0.85, 0.02]})
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    script = mesh_work.__file__
    run = subprocess.run([str(exe), "-b", "--python-exit-code", "1", "--python", script, "--", str(src), str(dst), "--job",
                          str(tmp_path / "job.json"), "--report", str(tmp_path / "report.json")],
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["ok"], report
    assert report["open_edges_after"] == report["open_edges_before"]
    assert report["over_shared_after"] == 0
    assert 0 < report["triangles_added"] <= eyes.TRIANGLE_RESERVE
    assert dst.is_file()
