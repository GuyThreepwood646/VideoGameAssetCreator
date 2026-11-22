"""Test fixtures: a temporary library and config so tests never touch library/vgac.sqlite."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
BAKEOFF = BACKEND.parent / "bakeoff"


# The app refuses a library folder path over library.max_library_folder_length() characters (library_path_problem), so a test library cannot sit
# in pytest's own temporary folders (C:\Users\...\pytest-of-...\...), which are longer. The owner's rule is that all
# work stays on F: inside the project, so a test library goes in the project's bakeoff/t folder, in a short folder of
# its own, removed after the test. The project is the main checkout (the git common folder's parent), so a worktree
# uses the same folder. If even that is too deep, the test is skipped with the reason. The folder-length tests
# (test_long_paths.py) still prove the refusal on deep paths.


def _project_root() -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=BACKEND, capture_output=True, text=True,
                             timeout=30, check=True).stdout.strip()
        common = Path(out)
        if not common.is_absolute():
            common = BACKEND / common
        return common.resolve().parent
    except (OSError, subprocess.SubprocessError):
        return BACKEND.parent


LIBRARY_SCRATCH = (_project_root() / "bakeoff" / "t").resolve()


def _short_library_root() -> Path:
    from vgac import library

    root = LIBRARY_SCRATCH / ("vgac-" + uuid.uuid4().hex[:8])
    if library.library_path_problem(str(root / "library")) is not None:
        pytest.skip("the project folder is too deep for a test library (its library folder would be over the library limit); "
                    "move the project to a shorter folder")
    return root


@pytest.fixture
def short_library_root():
    """A short folder under the project's bakeoff/t to hold a test library; removed after the test."""
    root = _short_library_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def settings_path(tmp_path: Path, short_library_root: Path) -> Path:
    cfg = {
        "instances": {
            "a": {"url": "http://127.0.0.1:9", "gpu": "test", "role": "3d"},
            "b": {"url": "http://127.0.0.1:10", "gpu": "test", "role": "images", "output_dir": str(tmp_path / "comfy_out")},
        },
        "library_path": str(short_library_root / "library"),
        "blender_exe": None,
        "unity_exe": None,
        "comfy_input_dir": str(tmp_path / "comfy_in"),
        "min_free_ram_gb": 0,
        "job_timeout_seconds": 60,
        "require_setup": False,  # tests stub the engines; the refusals have their own tests (test_first_run.py)
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
def _no_real_card_figures(monkeypatch: pytest.MonkeyPatch):
    """Tests never read this machine's cards (nvidia-smi): cardmem answers "no figures" unless a test says otherwise."""
    from vgac import cardmem

    cardmem.forget()
    monkeypatch.setattr(cardmem, "runner", lambda: None)
    yield
    cardmem.forget()


@pytest.fixture(autouse=True)
def _no_real_models_folders(monkeypatch: pytest.MonkeyPatch):
    """The models panel looks in every engine's own models folder; a test must not see this machine's."""
    from vgac import models_setup

    monkeypatch.setattr(models_setup, "engine_models_folders", lambda: [])


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
    temporary = [base, LIBRARY_SCRATCH]

    def guarded() -> Path:
        p = real()
        for root in temporary:
            try:
                Path(p).resolve().relative_to(root)
                return p
            except ValueError:
                continue
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

    # The hair builder may have been imported after uploads (at collection), which switches the limit off for the process:
    # put uploads' limit back before each test, so a test never starts with it off.
    if Image.MAX_IMAGE_PIXELS is None or Image.MAX_IMAGE_PIXELS > uploads.MAX_PIXELS:
        Image.MAX_IMAGE_PIXELS = uploads.MAX_PIXELS
    before = Image.MAX_IMAGE_PIXELS
    yield
    Image.MAX_IMAGE_PIXELS = before


@pytest.fixture(autouse=True)
def _no_body_marks_in_step_runs(monkeypatch: pytest.MonkeyPatch):
    """Step 2's tests draw plain fake pictures: finding marks on them would add a body_marks check to every one and load
    the pose finder. tests/test_bodymarks.py turns the marks back on with a fake finder."""
    from vgac.stages import turnaround

    monkeypatch.setattr(turnaround, "BODY_MARKS", False)
