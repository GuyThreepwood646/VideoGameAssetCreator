"""The item finder's model files through the one downloader (vgac/finder_setup.py): the pinned list, check-only runs that
download nothing, a changed file refused, and a missing file fetched against its pin. No network: every transfer goes
through a fake opener, every file lands in a temp folder."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

import pytest

from vgac import downloader as dl
from vgac import finder_setup

REAL = Path(__file__).resolve().parents[2] / "setup" / "downloads.json"
COMMIT = "a" * 40
DATA = b"finder model bytes" * 50
SHA = hashlib.sha256(DATA).hexdigest()


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "setup").mkdir(parents=True)
    monkeypatch.setattr(dl, "project_dir", lambda: proj)
    return proj


def fake_list(monkeypatch, files: dict):
    """A list with the finder entries given (name -> (bytes, url)), under the real finder layout."""
    entries = {}
    for name, (data, url) in files.items():
        entries[f"finder_{name}"] = {"name": name, "url": url, "page": "https://huggingface.co/x/y",
                                     "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                     "dest": f"local/finder/hf/hub/models--x--y/snapshots/{COMMIT}", "save_as": name,
                                     "publisher": True, "licence": "Apache-2.0"}
    monkeypatch.setattr(dl, "catalogue", lambda: {"project_files": entries})


def opener_for(payloads: dict, calls: list):
    def _open(url, start):
        calls.append(url)
        return _Resp(payloads[url])
    return _open


class _Resp:
    def __init__(self, data: bytes):
        self.data, self.pos, self.status = data, 0, 200
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def close(self):
        pass


def test_the_shipped_list_pins_both_models_to_the_commits_the_finder_loads():
    d = json.loads(REAL.read_text(encoding="utf-8"))
    finder = {k: v for k, v in d["project_files"].items() if k.startswith("finder_")}
    assert len(finder) == 12
    commits = {}
    for k, v in finder.items():
        m = re.fullmatch(r"https://huggingface\.co/([^/]+/[^/]+)/resolve/([0-9a-f]{40})/(.+)", v["url"])
        assert m, k
        assert v["save_as"] == m.group(3), k
        assert re.fullmatch(r"[0-9a-f]{64}", v["sha256"]) and v["size"] > 0, k
        dm = re.fullmatch(r"local/finder/hf/hub/models--([^/]+)/snapshots/([0-9a-f]{40})", v["dest"].replace("\\", "/"))
        assert dm and dm.group(2) == m.group(2), k  # the snapshot folder is the commit in the address
        assert dm.group(1) == m.group(1).replace("/", "--"), k
        commits.setdefault(m.group(1), set()).add(m.group(2))
    assert commits == {"IDEA-Research/grounding-dino-tiny": {"a2bb814dd30d776dcf7e30523b00659f4f141c71"},
                       "facebook/sam2.1-hiera-small": {"ee5bba1d82bb8749febdf90f45e84b687142ba03"}}
    sizes = {k: v["size"] for k, v in finder.items()}
    assert sizes["finder_dino_model"] == 689359096 and sizes["finder_sam_model"] == 184305280


def test_check_only_on_matching_files_downloads_nothing(project, monkeypatch):
    fake_list(monkeypatch, {"model.bin": (DATA, "https://huggingface.co/x/y/resolve/" + COMMIT + "/model.bin")})
    dest = project / "local/finder/hf/hub/models--x--y/snapshots" / COMMIT / "model.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(DATA)
    monkeypatch.setattr(dl, "default_open", lambda *a: pytest.fail("a check must not download"))
    lines = []
    assert finder_setup.run(check_only=True, say=lines.append) == 0
    assert "1 of 1 present and matching; 0 to download" in lines[0]


def test_check_only_reports_a_missing_file_and_still_downloads_nothing(project, monkeypatch):
    fake_list(monkeypatch, {"model.bin": (DATA, "https://huggingface.co/x/y/resolve/" + COMMIT + "/model.bin")})
    monkeypatch.setattr(dl, "default_open", lambda *a: pytest.fail("a check must not download"))
    lines = []
    assert finder_setup.run(check_only=True, say=lines.append) == 1
    assert "0 of 1 present and matching; 1 to download" in lines[0]


def test_a_changed_file_in_place_is_refused_and_nothing_is_fetched(project, monkeypatch):
    fake_list(monkeypatch, {"model.bin": (DATA, "https://huggingface.co/x/y/resolve/" + COMMIT + "/model.bin"),
                            "other.bin": (b"other", "https://huggingface.co/x/y/resolve/" + COMMIT + "/other.bin")})
    snap = project / "local/finder/hf/hub/models--x--y/snapshots" / COMMIT
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"changed")
    monkeypatch.setattr(dl, "default_open", lambda *a: pytest.fail("a changed file must stop the run before any download"))
    lines = []
    assert finder_setup.run(say=lines.append) == 2
    assert (snap / "model.bin").read_bytes() == b"changed"  # nothing deleted or replaced
    assert not (snap / "other.bin").exists()
    assert "Not the pinned file" in lines[0]


def test_a_missing_file_is_fetched_against_its_pin_and_the_refs_file_is_written(project, monkeypatch):
    url = "https://huggingface.co/x/y/resolve/" + COMMIT + "/model.bin"
    fake_list(monkeypatch, {"model.bin": (DATA, url)})
    calls = []
    monkeypatch.setattr(dl, "default_open", opener_for({url: DATA}, calls))
    lines = []
    assert finder_setup.run(say=lines.append) == 0
    snap = project / "local/finder/hf/hub/models--x--y/snapshots" / COMMIT
    assert (snap / "model.bin").read_bytes() == DATA and calls == [url]
    assert (project / "local/finder/hf/hub/models--x--y/refs/main").read_text(encoding="utf-8") == COMMIT
    assert "1 of 1 present and matching" in lines[-1]


def test_a_file_that_does_not_match_its_pin_is_refused_and_removed(project, monkeypatch):
    url = "https://huggingface.co/x/y/resolve/" + COMMIT + "/model.bin"
    fake_list(monkeypatch, {"model.bin": (DATA, url)})
    calls = []
    monkeypatch.setattr(dl, "default_open", opener_for({url: b"x" * len(DATA)}, calls))
    lines = []
    assert finder_setup.run(say=lines.append) == 3
    snap = project / "local/finder/hf/hub/models--x--y/snapshots" / COMMIT
    assert not (snap / "model.bin").exists() and not (snap / "model.bin.part").exists()
    assert "not the original file" in lines[-1]


def test_an_existing_refs_file_is_never_changed(project, monkeypatch):
    fake_list(monkeypatch, {"model.bin": (DATA, "https://huggingface.co/x/y/resolve/" + COMMIT + "/model.bin")})
    refs = project / "local/finder/hf/hub/models--x--y/refs"
    refs.mkdir(parents=True)
    (refs / "main").write_text("b" * 40, encoding="utf-8")
    snap = project / "local/finder/hf/hub/models--x--y/snapshots" / COMMIT
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(DATA)
    assert finder_setup.run(check_only=True, say=lambda s: None) == 0
    finder_setup.write_refs(dl.Spec(id="x", name="x", urls=[], dest=snap / "model.bin"))
    assert (refs / "main").read_text(encoding="utf-8") == "b" * 40


PS1 = Path(__file__).resolve().parents[2] / "setup" / "Setup-ItemFinder.ps1"
PIN_FILE = Path(__file__).resolve().parents[2] / "setup" / "finder-requirements.txt"


def test_the_committed_pin_file_is_the_finder_environment_with_no_local_lines():
    pins = finder_setup.read_pins(PIN_FILE.read_text(encoding="utf-8"))
    assert pins["torch"] == "2.15.0+cpu"
    assert pins["transformers"] == "5.24.1" and pins["timm"] == "1.0.31" and pins["pillow"] == "13.0.0"
    assert pins["numpy"] == "2.5.5"
    for line in PIN_FILE.read_text(encoding="utf-8").splitlines():
        assert not line.startswith("-e") and "@" not in line and "file:" not in line, line


def test_the_install_uses_the_pin_file_and_the_torch_cpu_index_with_the_same_torch_pin():
    text = PS1.read_text(encoding="utf-8")
    assert r"$reqFile = Join-Path $root 'setup\finder-requirements.txt'" in text
    assert "pip install --quiet -r $reqFile" in text
    assert "pip install --quiet torch==2.15.0+cpu --index-url https://download.pytorch.org/whl/cpu" in text
    assert "transformers timm pillow numpy" not in text  # no unpinned install left
    pins = finder_setup.read_pins(PIN_FILE.read_text(encoding="utf-8"))
    assert f"torch=={pins['torch']} --index-url" in text


def test_drift_lists_missing_and_different_pins_and_ignores_extra_packages():
    pins = {"torch": "2.15.0+cpu", "numpy": "2.5.5", "timm": "1.0.31"}
    have = {"torch": "2.15.0+cpu", "numpy": "2.6.0", "extra-thing": "1.0"}
    drift = finder_setup.pin_drift(pins, have)
    assert drift == ["numpy is 2.6.0, pinned 2.5.5", "timm 1.0.31 is not installed"]


def test_pin_names_compare_in_the_canonical_form():
    assert finder_setup.read_pins("Pillow==13.0.0\nhf_xet==1.8.0\n") == {"pillow": "13.0.0", "hf-xet": "1.8.0"}


def test_run_reports_drift_from_the_environments_own_freeze_and_changes_nothing(project, monkeypatch):
    url = "https://huggingface.co/x/y/resolve/" + COMMIT + "/model.bin"
    fake_list(monkeypatch, {"model.bin": (DATA, url)})
    (project / "setup" / "finder-requirements.txt").write_text("numpy==2.5.5\ntimm==1.0.31\n", encoding="utf-8")
    py = project / "local" / "finder" / "venv" / ("Scripts" if finder_setup.os.name == "nt" else "bin") / (
        "python.exe" if finder_setup.os.name == "nt" else "python")
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    monkeypatch.setattr(finder_setup, "installed_versions", lambda p: {"numpy": "2.5.5"})
    monkeypatch.setattr(dl, "default_open", lambda *a: pytest.fail("a check must not download"))
    lines = []
    finder_setup.run(check_only=True, say=lines.append)
    assert any("timm 1.0.31 is not installed" in line for line in lines)
    assert not any("numpy" in line and "differ" in line for line in lines)


def test_run_says_the_packages_match_when_the_freeze_matches(project, monkeypatch):
    url = "https://huggingface.co/x/y/resolve/" + COMMIT + "/model.bin"
    fake_list(monkeypatch, {"model.bin": (DATA, url)})
    (project / "setup" / "finder-requirements.txt").write_text("numpy==2.5.5\n", encoding="utf-8")
    monkeypatch.setattr(finder_setup, "venv_python", lambda: project / "venv-python")
    (project / "venv-python").write_text("", encoding="utf-8")
    monkeypatch.setattr(finder_setup, "installed_versions", lambda p: {"numpy": "2.5.5"})
    lines = []
    finder_setup.run(check_only=True, say=lines.append)
    assert "Item finder packages match setup/finder-requirements.txt." in lines
