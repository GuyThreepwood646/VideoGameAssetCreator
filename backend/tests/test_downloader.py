"""The shared downloader (vgac/downloader.py): resume, checksums, mirrors, space, licence gate, stop and continue, ledger, the list.
No network: every transfer goes through a fake opener; every file lands in a temp folder."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
from pathlib import Path

import pytest

from vgac import downloader as dl


class Resp:
    def __init__(self, data: bytes, start: int = 0, partial: bool = False, hook=None):
        self.data, self.pos, self.hook = data, start if partial else 0, hook
        self.status = 206 if partial else 200
        self.headers = {"Content-Length": str(len(data) - self.pos)}

    def read(self, n):
        if self.hook:
            self.hook(self.pos)
        chunk = self.data[self.pos:self.pos + min(n, 100)]  # small chunks so a stop can land in the middle
        self.pos += len(chunk)
        return chunk

    def close(self):
        pass


DATA = bytes(range(256)) * 20  # 5120 bytes
SHA = hashlib.sha256(DATA).hexdigest()


def opener_for(files, seen=None, honour_range=True, hook=None):
    def _open(url, start):
        if seen is not None:
            seen.append((url, start))
        v = files[url]
        if isinstance(v, Exception):
            raise v
        return Resp(v, start, partial=bool(start) and honour_range, hook=hook)
    return _open


def spec(tmp_path, **kw):
    d = dict(id="model:x", name="Thing", urls=["http://a/x.bin"], dest=tmp_path / "models" / "x.bin", size=len(DATA), sha256=SHA, page="http://page/x")
    d.update(kw)
    return dl.Spec(**d)


def run(s, files, accepted=None, **kw):
    return dl.fetch(s, threading.Event(), opener=opener_for(files, **kw), accepted=accepted)


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "project_dir", lambda: tmp_path / "proj")
    (tmp_path / "proj" / "setup").mkdir(parents=True)
    return tmp_path / "proj"


def test_downloads_checks_and_swaps_in(tmp_path):
    s = spec(tmp_path)
    assert run(s, {"http://a/x.bin": DATA}) == "downloaded"
    assert s.dest.read_bytes() == DATA and not s.part.exists()


def test_a_finished_file_is_never_touched_or_fetched(tmp_path):
    s = spec(tmp_path)
    s.dest.parent.mkdir(parents=True)
    s.dest.write_bytes(b"mine")
    seen = []
    assert run(s, {"http://a/x.bin": DATA}, seen=seen) == "present"
    assert s.dest.read_bytes() == b"mine" and seen == []


def test_resumes_from_the_part_when_the_site_allows(tmp_path):
    s = spec(tmp_path)
    s.part.parent.mkdir(parents=True)
    s.part.write_bytes(DATA[:1000])
    seen = []
    run(s, {"http://a/x.bin": DATA}, seen=seen)
    assert seen == [("http://a/x.bin", 1000)] and s.dest.read_bytes() == DATA


def test_starts_over_when_the_site_ignores_the_range(tmp_path):
    s = spec(tmp_path)
    s.part.parent.mkdir(parents=True)
    s.part.write_bytes(DATA[:1000])
    run(s, {"http://a/x.bin": DATA}, honour_range=False)
    assert s.dest.read_bytes() == DATA


def test_a_wrong_checksum_deletes_the_file_and_it_is_never_used(tmp_path):
    s = spec(tmp_path, sha256="0" * 64)
    with pytest.raises(dl.DownloadError) as e:
        run(s, {"http://a/x.bin": DATA})
    assert e.value.code == "bad_checksum" and "deleted" in e.value.message
    assert not s.dest.exists() and not s.part.exists() and not list(s.dest.parent.glob("*.bad"))


ALLOWED_HOSTS = {
    "download.blender.org", "www.blender.org",  # Blender Foundation
    "huggingface.co",  # only the repositories in ALLOWED_HF below
    "ai.google.dev", "storage.googleapis.com",  # Google: Gemma terms, MediaPipe's own model files
    "github.com",  # only the project's own repositories in ALLOWED_GH below
    "quaternius.itch.io",  # the author's own page
    "files.makehumancommunity.org", "files2.makehumancommunity.org", "static.makehumancommunity.org",  # MakeHuman's own
    "store.steampowered.com", "apps.microsoft.com",  # Blender's own listings; links the person opens, nothing is fetched from them
}
ALLOWED_HF = {  # owner/repo: each one is the author's, the publisher's, or Comfy-Org's own packaging; see the report for the re-uploads
    "Comfy-Org/BiRefNet", "Comfy-Org/Chroma1-HD", "Comfy-Org/Qwen-Image-Edit_ComfyUI", "Comfy-Org/Qwen-Image_ComfyUI", "Comfy-Org/TRELLIS.2",
    "Comfy-Org/flux2-klein-4b", "Comfy-Org/ltx-2", "Comfy-Org/mediapipe", "Comfy-Org/z_image_turbo", "Comfy-Org/z_image",
    "Phips/4xRealWebPhoto_v4_dat2", "VAST-AI/SkinTokens", "artificialguybr/3DRenderStyle-REDMOND-ZIMAGE",
    "artificialguybr/ClayAnimation-Redmond-ZIMAGE", "artificialguybr/PS1Graphics-Redmond-ZimageTURBO",
    "berkerdooo/qwen-image-edit-2511-camera-angle-lora", "black-forest-labs/FLUX.2-klein-4b-fp8", "circlestone-labs/Anima",
    "comfyanonymous/flux_text_encoders", "fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA", "lightx2v/Qwen-Image-Edit-2511-Lightning",
    "tarn59/character_turnaround_sheet_qwen_edit_2511", "tencent/Hunyuan3D-2mv",
}
ALLOWED_GH = {"xinntao/Real-ESRGAN", "Rizzlord/ComfyUI-SkinToken", "Comfy-Org/ComfyUI",
              "Kim2091/Kim2091-Models"}  # Kim2091: the publisher of 4x-PBRify UpscalerV4 (CC0), its own release page


def test_every_address_in_the_shipped_list_is_on_an_allowed_host():
    """No third-party mirror, proxy or re-upload host: an address added to setup/downloads.json must be added here on purpose."""
    from urllib.parse import urlparse
    real = Path(__file__).resolve().parents[2] / "setup" / "downloads.json"
    found: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            assert "mirrors" not in o, "a mirrors entry is not allowed"
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and o.startswith(("http://", "https://")):
            found.append(o)

    walk(json.loads(real.read_text(encoding="utf-8")))
    assert found
    for u in found:
        p = urlparse(u)
        assert p.scheme == "https" and p.netloc in ALLOWED_HOSTS, f"{u}: not an allowed host"
        parts = p.path.strip("/").split("/")
        if p.netloc == "huggingface.co":
            assert "/".join(parts[:2]) in ALLOWED_HF, f"{u}: a Hugging Face repository that is not on the allowed list"
        if p.netloc == "github.com":
            assert "/".join(parts[:2]) in ALLOWED_GH, f"{u}: a GitHub repository that is not on the allowed list"


def test_a_short_file_keeps_its_part_for_the_next_try(tmp_path):
    s = spec(tmp_path)
    r = Resp(DATA)
    r.headers = {"Content-Length": str(len(DATA) + 50)}  # promised more than it gave
    with pytest.raises(dl.DownloadError) as e:
        dl.fetch(s, threading.Event(), opener=lambda u, st: r)
    assert e.value.code == "short" and s.part.exists() and not s.dest.exists()


def test_second_link_is_tried_when_the_first_is_gone(tmp_path):
    s = spec(tmp_path, urls=["http://a/x.bin", "http://mirror/x.bin"])
    files = {"http://a/x.bin": urllib.error.HTTPError("http://a/x.bin", 404, "nf", {}, None), "http://mirror/x.bin": DATA}
    assert run(s, files) == "downloaded"


def test_every_link_gone_points_to_the_page(tmp_path):
    s = spec(tmp_path)
    with pytest.raises(dl.DownloadError) as e:
        run(s, {"http://a/x.bin": urllib.error.HTTPError("http://a/x.bin", 404, "nf", {}, None)})
    assert e.value.code == "link_gone" and "no longer works" in e.value.message and "http://page/x" in e.value.fix


def test_no_internet_is_plain(tmp_path):
    s = spec(tmp_path)
    with pytest.raises(dl.DownloadError) as e:
        run(s, {"http://a/x.bin": urllib.error.URLError("no route")})
    assert e.value.code == "no_internet" and "could not reach the internet" in e.value.message and "Try again" in e.value.fix


def test_not_enough_room_is_refused_before_anything_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "free_bytes", lambda where: 10)
    seen = []
    s = spec(tmp_path)
    with pytest.raises(dl.DownloadError) as e:
        run(s, {"http://a/x.bin": DATA}, seen=seen)
    assert e.value.code == "no_space" and "not enough free space" in e.value.message and seen == []


def test_a_licence_item_is_refused_until_the_caller_accepts_it(tmp_path):
    s = spec(tmp_path, optin=True, licence_page="http://lic/x")
    with pytest.raises(dl.DownloadError) as e:
        run(s, {"http://a/x.bin": DATA})
    assert e.value.code == "licence" and "http://lic/x" in e.value.fix and not s.dest.exists()
    assert run(s, {"http://a/x.bin": DATA}, accepted={"model:x"}) == "downloaded"


def test_no_link_says_where_to_put_it_by_hand(tmp_path):
    s = spec(tmp_path, urls=[])
    with pytest.raises(dl.DownloadError) as e:
        run(s, {})
    assert e.value.code == "no_link" and str(s.dest) in e.value.fix


def test_stop_keeps_the_part_and_the_next_start_continues(tmp_path):
    s = spec(tmp_path)
    ev = threading.Event()

    def hook(pos):
        if pos >= 1500:
            ev.set()

    with pytest.raises(dl.Stopped):
        dl.fetch(s, ev, opener=opener_for({"http://a/x.bin": DATA}, hook=hook))
    kept = s.part.stat().st_size
    assert 0 < kept < len(DATA) and not s.dest.exists()
    seen = []
    assert run(s, {"http://a/x.bin": DATA}, seen=seen) == "downloaded"
    assert seen == [("http://a/x.bin", kept)] and s.dest.read_bytes() == DATA


# --- jobs and the ledger ------------------------------------------------------------------------------------------------


def test_a_job_fetches_in_order_records_the_ledger_and_reports(tmp_path, project):
    a = spec(tmp_path, id="model:a", name="A", dest=tmp_path / "m" / "a.bin")
    b = spec(tmp_path, id="model:b", name="B", dest=tmp_path / "m" / "b.bin", urls=["http://b/b.bin"])
    files = {"http://a/x.bin": DATA, "http://b/b.bin": DATA}
    job = dl.start_job("t1", [a, b], opener=opener_for(files), wait=True)
    st = dl.job_status("t1")
    assert job.state == "done" and st["results"] == {"model:a": "downloaded", "model:b": "downloaded"} and st["fraction"] == 1.0
    items = json.loads((project / "local" / "installed.json").read_text())["items"]
    assert [i["what"] for i in items] == ["A", "B"] and all(i["kind"] == "added" and i["bytes"] == len(DATA) for i in items)
    dl.start_job("t1", [a, b], opener=opener_for({}), wait=True)  # both are there: nothing fetched, nothing recorded twice
    assert len(json.loads((project / "local" / "installed.json").read_text())["items"]) == 2


def test_a_failed_job_carries_the_plain_error_and_can_be_started_again(tmp_path):
    a = spec(tmp_path, id="model:a", dest=tmp_path / "m" / "a.bin")
    job = dl.start_job("t2", [a], opener=opener_for({"http://a/x.bin": urllib.error.URLError("x")}), wait=True)
    assert job.state == "failed" and job.error["code"] == "no_internet" and job.error["fix"]
    job = dl.start_job("t2", [a], opener=opener_for({"http://a/x.bin": DATA}), wait=True)
    assert job.state == "done"


def test_a_stopped_job_continues_from_the_part(tmp_path):
    a = spec(tmp_path, id="model:a", dest=tmp_path / "m" / "a.bin")
    box = {}

    def hook(pos):
        if pos >= 1500:
            box["job"].cancel.set()

    job = dl.Job("t3", [a], opener=opener_for({"http://a/x.bin": DATA}, hook=hook))
    box["job"] = job
    dl._work(job)
    assert job.state == "stopped" and a.part.exists()
    job2 = dl.start_job("t3", [a], opener=opener_for({"http://a/x.bin": DATA}), wait=True)
    assert job2.state == "done" and a.dest.read_bytes() == DATA


def test_a_job_that_is_working_is_not_started_twice(tmp_path):
    gate = threading.Event()
    a = spec(tmp_path, id="model:a", dest=tmp_path / "m" / "a.bin")
    job = dl.start_job("t4", [a], opener=opener_for({"http://a/x.bin": DATA}, hook=lambda pos: gate.wait(5)))
    with pytest.raises(dl.DownloadError) as e:
        dl.start_job("t4", [a], opener=opener_for({}))
    assert e.value.code == "busy"
    gate.set()
    job.thread.join(5)


# --- the list -----------------------------------------------------------------------------------------------------------


def test_the_list_takes_the_persons_own_additions_over_the_shipped_one(project):
    (project / "setup" / "downloads.json").write_text(json.dumps({"models": {"a": {"url": "http://u/a", "dest": "x", "save_as": "a.bin"}}}))
    (project / "setup" / "downloads.local.json").write_text(json.dumps({"models": {"b": {"url": "http://u/b", "dest": "y", "save_as": "b.bin"}}}))
    assert set(dl.catalogue()["models"]) == {"a", "b"}


def test_files_land_where_the_list_says_and_never_outside(project, tmp_path):
    (project / "setup" / "downloads.json").write_text(json.dumps({
        "models": {"ok": {"url": "http://u/ok", "dest": "checkpoints/Z", "save_as": "ok.safetensors", "size": 5, "sha256": "a" * 64, "optin": True},
                   "evil": {"url": "http://u/e", "dest": "../../escape", "save_as": "e.bin"},
                   "abs": {"url": "http://u/e", "dest": "C:/Windows", "save_as": "e.bin"}},
        "addons": {"lora": {"url": "http://u/l", "dest": "loras/Q/l.safetensors"}},
        "project_files": {"pf": {"url": "http://u/p", "dest": "runtime/models", "save_as": "p.task"}}}))
    root = tmp_path / "ComfyUI" / "models"
    specs = {s.id: s for s in dl.specs_for(root)}
    assert set(specs) == {"model:ok", "addon:lora", "project_file:pf"}
    assert specs["model:ok"].dest == root / "checkpoints" / "Z" / "ok.safetensors" and specs["model:ok"].optin
    assert specs["addon:lora"].dest == root / "loras" / "Q" / "l.safetensors"
    assert specs["project_file:pf"].dest == project / "runtime" / "models" / "p.task"


def test_without_a_comfyui_only_the_project_files_can_be_placed(project, monkeypatch):
    (project / "setup" / "downloads.json").write_text(json.dumps({"models": {"m": {"url": "http://u/m", "dest": "x", "save_as": "m.bin"}},
                                                                  "project_files": {"pf": {"url": "http://u/p", "dest": "runtime/models", "save_as": "p.task"}}}))
    monkeypatch.setattr(dl, "models_root", lambda: None)
    assert [s.id for s in dl.specs_for()] == ["project_file:pf"]


def test_the_shipped_list_is_complete_and_safe(monkeypatch):
    """The real setup/downloads.json: every entry stays inside its folder, and every link has a size and a checksum."""
    real = Path(__file__).resolve().parents[2] / "setup" / "downloads.json"
    d = json.loads(real.read_text(encoding="utf-8"))
    monkeypatch.setattr(dl, "catalogue", lambda: d)
    specs = dl.specs_for(Path("C:/x/models"))
    want = sum(len(d.get(k, {})) for k in ("models", "addons", "project_files"))
    assert len(specs) == want, "an entry of the shipped list would land outside its folder"
    for s in specs:
        if s.urls:
            assert len(s.sha256) == 64 and s.size > 0, f"{s.id}: a link without a size and checksum"


def test_a_file_with_no_pinned_checksum_is_never_downloaded(tmp_path):
    import threading

    spec = dl.Spec(id="x", name="X", urls=["http://fake/x"], dest=tmp_path / "x.bin", size=3, sha256="", page="http://page")
    asked = []
    with pytest.raises(dl.DownloadError) as e:
        dl.fetch(spec, threading.Event(), opener=lambda u, s: asked.append(u))
    assert e.value.code == "no_pin" and asked == []
