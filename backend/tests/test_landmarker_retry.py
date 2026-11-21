"""A failed MediaPipe landmarker load is logged and tried again, never kept for the life of the backend.

Found 2026-11-21: a backend process whose first face-landmarker load failed kept the processor face finder off for
good (lipmarks._landmarker_failed), with nothing in the log, so every later paint fell back to the card finder.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from vgac import buildpose, facemarks, lipmarks

MAIN_MODELS = Path(r"F:\Projects\VideoGameAssetCreator\runtime\models")


def _real(name: str) -> Path:
    for root in (Path(__file__).resolve().parents[2] / "runtime" / "models", MAIN_MODELS):
        if (root / name).is_file():
            return root / name
    pytest.skip(f"{name} is not on this machine")


@pytest.fixture
def fresh(monkeypatch):
    pytest.importorskip("mediapipe")
    clock = {"t": 1000.0}
    monkeypatch.setattr(lipmarks.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(buildpose.time, "monotonic", lambda: clock["t"])
    for mod, names in ((lipmarks, ("_landmarker", "_failed_at", "_last_error")),):
        for n in names:
            monkeypatch.setattr(mod, n, None, raising=False)
    monkeypatch.setattr(buildpose, "_models", {})
    monkeypatch.setattr(buildpose, "_failed", {})
    return clock


def test_face_landmarker_loads_again_after_a_failed_load(fresh, monkeypatch, tmp_path, caplog):
    real = _real(lipmarks.MODEL_NAME)
    monkeypatch.setenv("VGAC_FACE_LANDMARKER", str(tmp_path / "not_there.task"))
    with caplog.at_level(logging.WARNING, logger="vgac.lipmarks"):
        assert lipmarks._get_landmarker() is None
    assert lipmarks.last_error() and "FileNotFoundError" in lipmarks.last_error()
    assert any("did not load" in r.getMessage() for r in caplog.records)

    monkeypatch.setenv("VGAC_FACE_LANDMARKER", str(real))
    assert lipmarks._get_landmarker() is None  # within the retry wait: not tried again yet
    fresh["t"] += lipmarks.RETRY_SECONDS + 1
    assert lipmarks._get_landmarker() is not None  # tried again, and it loads
    assert lipmarks.last_error() is None


def test_pose_landmarker_loads_again_after_a_failed_load(fresh, monkeypatch, tmp_path):
    real = _real(buildpose.POSE_MODEL)
    monkeypatch.setenv("VGAC_POSE_LANDMARKER", str(tmp_path / "not_there.task"))
    assert buildpose._model("pose") is None
    monkeypatch.setenv("VGAC_POSE_LANDMARKER", str(real))
    fresh["t"] += lipmarks.RETRY_SECONDS + 1
    assert buildpose._model("pose") is not None


def test_finder_names_a_failed_load_when_the_model_file_is_there(monkeypatch, tmp_path):
    model = tmp_path / "face_landmarker.task"
    model.write_bytes(b"not a model")
    monkeypatch.setenv("VGAC_FACE_LANDMARKER", str(model))
    monkeypatch.setattr(lipmarks, "available", lambda: False)
    monkeypatch.setattr(lipmarks, "last_error", lambda: "RuntimeError: bad model")
    token = facemarks._local.set(True)
    try:
        out = facemarks.finder()
    finally:
        facemarks._local.reset(token)
    pytest.importorskip("mediapipe")
    assert out["missing"] == "the face landmarker did not load (RuntimeError: bad model)"
