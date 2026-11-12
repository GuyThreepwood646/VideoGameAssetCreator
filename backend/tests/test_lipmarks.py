"""lipmarks: the pure functions (lip points from a mesh and from a mask, comparisons, the crease finder)."""
from __future__ import annotations

import numpy as np

from vgac import lipmarks


def mesh_points() -> np.ndarray:
    P = np.zeros((478, 2))
    P[lipmarks.CORNER_A] = (300, 500)  # the picture's left (smaller x)
    P[lipmarks.CORNER_B] = (400, 504)
    P[lipmarks.UPPER_OUTER] = (350, 485)
    P[lipmarks.LOWER_OUTER] = (350, 525)
    P[lipmarks.UPPER_INNER] = (350, 498)
    P[lipmarks.LOWER_INNER] = (350, 510)
    P[33], P[133], P[362], P[263] = (250, 300), (290, 302), (410, 302), (450, 300)
    P[lipmarks.FACE_A], P[lipmarks.FACE_B] = (200, 400), (500, 400)
    return P


def test_from_mesh_points_orders_corners_and_measures_the_opening():
    lp = lipmarks.from_mesh_points(mesh_points())
    assert lp["left"] == (300.0, 500.0) and lp["right"] == (400.0, 504.0)
    assert lp["opening_px"] == 12.0 and lp["line_y"] == 504.0
    assert lipmarks.width_px(lp) == 100.0 and lp["face_w"] == 300.0
    assert abs(lp["eye_mid"][0] - 350.0) < 1e-9
    P = mesh_points()
    P[lipmarks.CORNER_A], P[lipmarks.CORNER_B] = P[lipmarks.CORNER_B].copy(), P[lipmarks.CORNER_A].copy()
    assert lipmarks.from_mesh_points(P)["left"] == (300.0, 500.0)  # whichever index is which, left is left
    P[lipmarks.LOWER_INNER] = (350, 490)  # lips pressed past each other: no negative opening
    assert lipmarks.from_mesh_points(P)["opening_px"] == 0.0


def test_from_mask_finds_extremes_and_a_split_mouth():
    m = np.zeros((100, 200), dtype=bool)
    m[40:44, 50:150] = True  # upper lip
    m[52:58, 60:140] = True  # lower lip, a gap of 8 rows between
    lp = lipmarks.from_mask(m)
    assert lp["left"][0] == 50.0 and lp["right"][0] == 149.0
    assert lp["upper"][1] == 40.0 and lp["lower"][1] == 57.0
    assert lp["opening_px"] == 8.0
    closed = np.zeros((100, 200), dtype=bool)
    closed[40:50, 50:150] = True
    assert lipmarks.from_mask(closed)["opening_px"] == 0.0
    assert lipmarks.from_mask(np.zeros((10, 10), dtype=bool)) is None


def test_compare_gives_shares_of_the_face_width():
    a = lipmarks.from_mesh_points(mesh_points())
    b = dict(a)
    b["left"], b["right"] = (300.0, 520.0), (400.0, 524.0)  # 20 px lower
    b["line_y"] = a["line_y"] + 20
    c = lipmarks.compare(a, b, 200.0)
    assert abs(c["line_dy"] - 0.1) < 1e-9 and abs(c["corner_dist"] - 0.1) < 1e-9
    assert c["mid_dx"] == 0.0 and c["width_ratio"] == 1.0 and abs(c["open_a"] - 0.06) < 1e-9
    wide = dict(a, left=(280.0, 500.0), right=(420.0, 504.0))
    assert abs(lipmarks.compare(a, wide, 200.0)["width_ratio"] - 1.4) < 1e-9
    shifted = dict(a, eye_mid=(a["eye_mid"][0] + 30, a["eye_mid"][1] + 10), left=(330.0, 510.0), right=(430.0, 514.0),
                   line_y=a["line_y"] + 10)
    plain = lipmarks.compare(a, shifted, 200.0)
    eyes = lipmarks.compare(a, shifted, 200.0, eye_align=True)
    assert plain["corner_dist"] > 0.1 and eyes["corner_dist"] < 1e-9 and abs(eyes["line_dy"]) < 1e-9


def test_compare_with_a_crease_only_reports_the_line():
    a = lipmarks.from_mesh_points(mesh_points())
    v = {"how": "valley", "line_y": 520.0, "left": (0, 520.0), "right": (1, 520.0), "opening_px": 0.0}
    c = lipmarks.compare(a, v, 100.0)
    assert c["corner_dist"] is None and c["width_ratio"] is None and abs(c["line_dy"] - 0.16) < 1e-9


def test_opening_state():
    assert lipmarks.opening_state(2.0, 500.0) == "closed"
    assert lipmarks.opening_state(40.0, 500.0) == "open"


def test_valley_row_finds_the_dark_crease_under_the_nose():
    lum = np.full((400, 400), 0.7)
    lum[260:264, :] = 0.2  # the crease
    lum[330:334, :] = 0.4  # a weaker one lower down (the chin fold)
    y = lipmarks.valley_row(lum, 200.0, 180.0, 300.0)
    assert y is not None and 258 <= y <= 266
    assert lipmarks.valley_row(lum, 200.0, 390.0, 300.0) is None  # the band falls off the picture
    assert lipmarks.valley_row(np.full((400, 400), 0.5), 200.0, 180.0, 300.0) is None  # flat: no crease
