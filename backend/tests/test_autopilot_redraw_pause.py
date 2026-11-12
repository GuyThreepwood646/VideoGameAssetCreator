"""Autopilot stops once after step 2 when Pose for 3D first redrew the picture (UI-47)."""

from __future__ import annotations

from types import SimpleNamespace

from vgac.autopilot import REDRAW_PAUSE_REASON, redraw_pause_reason


def _v(stage="turnaround", params=None, files=None):
    return SimpleNamespace(stage=stage, params=params or {}, files=files or {})


def test_pauses_after_a_redrawn_step_2():
    v = _v(params={"pose_ready": True}, files={"pose_ready_picture": "x.png"})
    assert redraw_pause_reason(v, "block") == REDRAW_PAUSE_REASON
    assert redraw_pause_reason(v, "warn") == REDRAW_PAUSE_REASON


def test_never_stop_setting_skips_it():
    v = _v(params={"pose_ready": True}, files={"pose_ready_picture": "x.png"})
    assert redraw_pause_reason(v, "never") is None


def test_no_pause_without_a_redraw_or_on_other_steps():
    assert redraw_pause_reason(_v(), "block") is None
    assert redraw_pause_reason(_v(params={"pose_ready": True}), "block") is None
    assert redraw_pause_reason(_v(stage="mesh", params={"pose_ready": True}, files={"pose_ready_picture": "x"}), "block") is None
