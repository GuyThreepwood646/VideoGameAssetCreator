"""WP-92 and WP-86: each new check is shown able to fail (a planted defect trips it) and able to pass (the same without it)."""
from __future__ import annotations

import numpy as np

from vgac import meshchecks, quality
from vgac.check_headlines import headline_for
from vgac.check_tiers import explicit_tier


def _grid(n=8, w=1.0):
    """A flat n x n quad grid in the xy plane (two triangles a square) and UVs filling 0..1."""
    xs, ys = np.meshgrid(np.linspace(0, w, n + 1), np.linspace(0, w, n + 1))
    v = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1)
    f = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            f += [[a, a + 1, a + n + 2], [a, a + n + 2, a + n + 1]]
    return v, np.array(f), v[:, :2] / w


def test_slivers_trip_on_needle_triangles_and_not_on_a_clean_grid():
    v, f, _ = _grid()
    clean = meshchecks.sliver_share(v, f)
    assert clean["share"] == 0.0
    bad = v.copy()
    bad[:, 1] = np.where(bad[:, 0] > 0.5, bad[:, 1] * 0.01, bad[:, 1])  # the right half squashed to needles
    s = meshchecks.sliver_share(bad, f)
    assert s["share"] > 0.3
    assert not quality.mesh_measure_checks({"slivers": s})[0]["passed"]
    assert quality.mesh_measure_checks({"slivers": clean})[0]["passed"]


def test_texel_density_spread_trips_when_one_piece_is_far_denser_and_not_when_even():
    v, f, uv = _grid(n=4)
    v2, f2 = np.vstack([v, v + [2, 0, 0]]), np.vstack([f, f + len(v)])  # two separate pieces

    def uvs(second_scale):
        return np.vstack([uv * 0.45, uv * 0.45 * second_scale + [0.5, 0.0]])

    even = meshchecks.texel_density_spread(v2, f2, uvs(1.0))
    assert even["spread"] < 1.1
    planted = meshchecks.texel_density_spread(v2, f2, uvs(0.1))  # the second piece gets a tenth of the texture length
    assert planted["spread"] > 5
    assert not quality.mesh_measure_checks({"texel_density": planted})[0]["passed"]
    assert quality.mesh_measure_checks({"texel_density": even})[0]["passed"]


def _octahedron(inside_out=False):
    v = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=float)
    f = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]])
    return v, (f[:, ::-1] if inside_out else f)


def test_a_closed_shell_turned_inside_out_is_flipped_and_the_right_way_is_not():
    assert meshchecks.flipped_shells(*_octahedron())["flipped"] == 0
    bad = meshchecks.flipped_shells(*_octahedron(inside_out=True))
    assert bad["flipped"] == 1 and bad["kinds"]["closed"] == 1 and bad["flipped_share"] > 0.999
    assert not quality.mesh_measure_checks({"flipped_shells": bad})[0]["passed"]


def _body_and_cloth(cloth_inward: bool):
    """A closed body (octahedron, the middle of the model) and an open cloth sheet: a ring of 60 squares around it, one piece
    that is not joined to the body. Its triangles face outward, or inward when planted."""
    bv, bf = _octahedron()
    n, r = 30, 3.0
    ang = np.linspace(0, 2 * np.pi, n + 1)
    ring = [(r * np.cos(a), r * np.sin(a)) for a in ang]
    v, f = [], []
    for i in range(n):
        (x0, y0), (x1, y1) = ring[i], ring[i + 1]
        k = len(v)
        v += [[x0, y0, -0.5], [x1, y1, -0.5], [x1, y1, 0.5], [x0, y0, 0.5]]
        f += [[k, k + 1, k + 2], [k, k + 2, k + 3]]  # counter-clockwise seen from outside: faces outward
    v, f = np.array(v, dtype=float), np.array(f)
    if cloth_inward:
        f = f[:, ::-1]
    return np.vstack([bv, v]), np.vstack([bf, f + len(bv)])


def test_an_open_cloth_facing_inward_is_flipped_and_facing_outward_is_not():
    ok = meshchecks.flipped_shells(*_body_and_cloth(False))
    assert ok["flipped"] == 0, ok
    bad = meshchecks.flipped_shells(*_body_and_cloth(True))
    assert bad["flipped"] == 1 and bad["kinds"]["open"] == 1, bad
    assert 0.05 < bad["flipped_share"] < 1.0


def test_a_shell_with_part_of_it_turned_over_is_flipped():
    """What a mistaken consistent-normals pass leaves on a leaf: one half of the sheet walked the other way."""
    v, f, _ = _grid(n=8)
    assert meshchecks.flipped_shells(v, f)["flipped"] == 0
    g = f.copy()
    half = v[g].mean(axis=1)[:, 0] > 0.5
    g[half] = g[half][:, ::-1]
    bad = meshchecks.flipped_shells(v, g)
    assert bad["kinds"]["conflict"] == 1 and bad["flipped"] == 1
    assert not quality.mesh_measure_checks({"flipped_shells": bad})[0]["passed"]


def test_painted_share_and_uv_transfer_checks_fail_on_the_planted_value_and_pass_without_it():
    assert not quality.painted_share_check(0.05)["passed"] and quality.painted_share_check(0.69)["passed"]
    assert quality.painted_share_check(None) is None and quality.painted_share_check("x") is None
    assert not quality.uv_transfer_check({"max_face_offset": 0.2})["passed"]
    assert quality.uv_transfer_check({"max_face_offset": 0.0})["passed"]
    assert quality.uv_transfer_check({"skipped": "target already has UVs"}) is None


def test_unreached_check_fails_above_two_percent():
    assert not quality.unreached_check({"share": 0.17, "texels": 1, "covered": 2})["passed"]
    assert quality.unreached_check({"share": 0.01, "texels": 1, "covered": 2})["passed"]
    assert quality.unreached_check({"share": None}) is None


def test_the_new_checks_are_look_or_advice_with_plain_headlines_and_a_sentence():
    for i in ("painted_share", "uv_transfer_offset", "slivers", "texel_density_spread", "flipped_shells", "unreached_texels"):
        assert explicit_tier(i) in ("look", "advice"), i
        assert headline_for(i), i
    c = quality.painted_share_check(0.05)
    assert "5%" in c["help"] or "5 %" in c["help"] or "5" in c["help"]
