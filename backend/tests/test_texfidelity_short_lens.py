"""texfidelity camera framing for a short lens: the pinhole eye stands outside the model. A long, thin model seen end
on at 24 mm was framed with the eye inside its bounding sphere (about 0.3 of its radius from the centre)."""
from __future__ import annotations

import numpy as np
import pytest

from vgac import texfidelity


def _long_post() -> dict[str, np.ndarray]:
    """A 0.2 x 0.2 x 2.0 post along the depth axis (the axis the camera looks along at yaw 0)."""
    verts = np.array([[x, y, z] for x in (-0.1, 0.1) for y in (-0.1, 0.1) for z in (-1.0, 1.0)])
    return {"verts": verts}


@pytest.mark.parametrize("lens", [24.0, 35.0])
def test_short_lens_eye_stays_outside_the_model(lens):
    mesh = _long_post()
    lo, hi = mesh["verts"].min(0), mesh["verts"].max(0)
    centre = (lo + hi) / 2
    radius = float(np.linalg.norm(hi - lo)) / 2
    cam = texfidelity._frame_camera(mesh, 0.0, 0.9 * 1024, 1024, 0.0, lens)
    assert float(np.linalg.norm(cam.eye() - centre)) > radius


def test_short_lens_keeps_the_figure_in_frame():
    mesh = _long_post()
    cam = texfidelity._frame_camera(mesh, 0.0, 0.9 * 1024, 1024, 0.0, 24.0)
    p, depth = cam.project(mesh["verts"])
    assert (depth > 0).all()
    assert p.min() >= 0 and p.max() <= 1024
