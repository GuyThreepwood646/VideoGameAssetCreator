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
