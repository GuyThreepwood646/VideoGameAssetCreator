"""Test fixtures: a temporary library and config so tests never touch library/vgac.sqlite."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
BAKEOFF = BACKEND.parent / "bakeoff"


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    cfg = {
        "instances": {
            "a": {"url": "http://127.0.0.1:9", "gpu": "test", "role": "3d"},
            "b": {"url": "http://127.0.0.1:10", "gpu": "test", "role": "images", "output_dir": str(tmp_path / "comfy_out")},
        },
        "library_path": str(tmp_path / "library"),
        "blender_exe": None,
        "unity_exe": None,
        "comfy_input_dir": str(tmp_path / "comfy_in"),
        "min_free_ram_gb": 0,
        "job_timeout_seconds": 60,
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


@pytest.fixture
def settings(settings_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vgac import config, db

    monkeypatch.setenv("VGAC_CONFIG", str(settings_path))
    config.reset_settings()
    db.reset_engine()
    s = config.get_settings()
    db.init_db()
    yield s
    db.reset_engine()
    config.reset_settings()


@pytest.fixture
def bakeoff_dir() -> Path:
    if not BAKEOFF.is_dir():
        pytest.skip("bakeoff scripts not present")
    return BAKEOFF


@pytest.fixture(autouse=True)
def _never_touch_live_comfy(monkeypatch: pytest.MonkeyPatch):
    """Tests must never reach a real ComfyUI: the config above uses unroutable ports, and interrupt is a no-op."""
    from vgac import comfy

    async def _noop(self):
        return None

    async def _free(self):
        return False

    monkeypatch.setattr(comfy.ComfyClient, "interrupt", _noop)
    monkeypatch.setattr(comfy.ComfyClient, "free", _free)


@pytest.fixture(autouse=True)
def _own_hair_stays_out_of_tests_without_a_temporary_library(request, monkeypatch: pytest.MonkeyPatch):
    """A test that does not use the temporary-library fixture would read the person's real my_hair/ folder, and the
    built-in library tests (all CC0, all with files in the app's data folder) would see their own hairstyles."""
    if "settings" in request.fixturenames:
        return
    from vgac import my_hair

    monkeypatch.setattr(my_hair, "entries", lambda settings=None: {})


@pytest.fixture(autouse=True)
def _own_addons_stay_out_of_tests_without_a_temporary_library(request, tmp_path, tmp_path_factory, monkeypatch: pytest.MonkeyPatch):
    """The person's own add-ons live in <library_path>/my_addons.json. A test whose library is not a temporary folder
    would read the real library's file (and see an add-on the person registered), so it reads an empty one instead.
    A library inside pytest's temporary folders (the settings fixture, a test's own) is used as it is."""
    from vgac import user_addons

    real = user_addons.registry_path
    base = Path(tmp_path_factory.getbasetemp()).resolve()

    def guarded() -> Path:
        p = real()
        try:
            Path(p).resolve().relative_to(base)
            return p
        except ValueError:
            return tmp_path / "no_library" / user_addons.REGISTRY_FILE

    monkeypatch.setattr(user_addons, "registry_path", guarded)


@pytest.fixture(autouse=True)
def _pillows_pixel_limit_does_not_leak_between_tests():
    """vgac/hairlib_builder.py turns Pillow's decompression-bomb limit off (Image.MAX_IMAGE_PIXELS = None) when it is
    imported, for the whole process; vgac/uploads.py sets the limit once, when it is imported. Whichever test imports
    the hair builder first would switch the limit off for every test after it (test_own_images_units checks it is on),
    so each test starts with the limit it found."""
    from PIL import Image

    from vgac import uploads  # noqa: F401  (sets the limit when first imported, before any test can turn it off)

    before = Image.MAX_IMAGE_PIXELS
    yield
    Image.MAX_IMAGE_PIXELS = before
