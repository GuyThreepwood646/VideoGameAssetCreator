"""texfidelity (bakeoff/facing, fix B): whether step 2 redrew the step-1 picture, told by the two outlines."""

from __future__ import annotations


def test_redrawn_picture_is_told_by_its_outline(tmp_path):
    """texfidelity.outline_overlap: the same figure at another size and place overlaps fully; arms down against a T-pose
    (step 2 redrew the pose) falls under REDRAWN_IOU."""
    import numpy as np
    from PIL import Image

    from vgac import texfidelity

    def figure(path, w, h, arms_out, at=(10, 10)):
        a = np.zeros((h + 40, w + 40, 4), np.uint8)
        x0, y0 = at
        cx = x0 + w // 2
        a[y0:y0 + h, cx - w // 8:cx + w // 8, 3] = 255                      # body
        if arms_out:
            a[y0 + h // 5:y0 + h // 5 + h // 12, x0:x0 + w, 3] = 255         # arms straight out
        else:
            a[y0 + h // 5:y0 + h // 2, cx - w // 5:cx - w // 8, 3] = 255    # arms down by the sides
            a[y0 + h // 5:y0 + h // 2, cx + w // 8:cx + w // 5, 3] = 255
        Image.fromarray(a, "RGBA").save(path)
        return path

    t1 = figure(tmp_path / "t1.png", 200, 300, True)
    t2 = figure(tmp_path / "t2.png", 100, 150, True, at=(30, 5))
    down = figure(tmp_path / "down.png", 200, 300, False)
    assert texfidelity.outline_overlap(t1, t2) > 0.9
    assert texfidelity.outline_overlap(t1, down) < texfidelity.REDRAWN_IOU
