"""setup/Check-Setup.ps1 and the app must reach the same verdict for every model on the same machine.

Two ways the script can answer, both pinned here against a stand-in ComfyUI that lists only earlier copies of the Z-Image files:
1. the app answers /api/setup: the script shows the app's own verdict and words;
2. the app does not answer: the script looks itself, with the alias data the app reads (the "older" lists of setup/downloads.json),
   and must say the same as preflight.check_instance for the same lists.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from test_setup_check import DEV_3080, NodeClient
from vgac import preflight

ROOT = Path(__file__).resolve().parents[2]
ROW = re.compile(r"^(Model: .+?)\s+(Ready|Missing|Needs you)\s+(.*)$")
STATE = {"ok": "Ready", "fail": "Missing", "warn": "Missing", "unknown": "Needs you"}


def files_listed() -> list[str]:
    models = preflight.load_models()
    cat = json.loads((ROOT / "setup" / "downloads.json").read_text(encoding="utf-8-sig"))
    zbase_old = Path(cat["models"]["zbase"]["older"][0]).name
    turbo_old = Path(cat["models"]["zturbo"]["older"][0]).name
    return [f"Z-Image/{zbase_old}", f"Z-Image/{turbo_old}", str(models["qwen_edit"]["file"])]


class Fake(BaseHTTPRequestHandler):
    files: list[str] = []

    def do_GET(self):  # noqa: N802
        if self.path == "/system_stats":
            body = {"devices": [{"name": DEV_3080}], "system": {"comfyui_version": "0.45.3"}}
        elif self.path.startswith("/object_info/"):
            cls = self.path.rsplit("/", 1)[1]
            spec = [list(Fake.files), {}]
            body = {cls: {"input": {"required": {k: spec for k in ("ckpt_name", "unet_name", "lora_name", "bg_removal_name", "clip_name", "vae_name", "model_name")}}}}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # quiet
        pass


def run_script(tmp: Path, port: int, backend_url: str) -> dict[str, tuple[str, str]]:
    ps = shutil.which("powershell")
    if ps is None:
        pytest.skip("Windows PowerShell is not available")
    for rel in ("setup/Check-Setup.ps1", "setup/downloads.json", "backend/vgac/data/models.yaml", "backend/vgac/data/addons.yaml",
                "backend/vgac/data/minimum_versions.json"):
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dst)
    (tmp / "backend" / "config.yaml").write_text(
        f"instances:\n  a:\n    url: http://127.0.0.1:{port}\n    role: 3d\n  b:\n    url: http://127.0.0.1:{port}\n    role: images\n", encoding="utf-8")
    env = {**__import__("os").environ, "VGAC_BACKEND_URL": backend_url}
    out = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp / "setup" / "Check-Setup.ps1")],
                         capture_output=True, text=True, timeout=300, env=env, check=False).stdout
    rows = {}
    for line in out.splitlines():
        m = ROW.match(line)
        if m:
            rows[re.sub(r" \(optional\)$", "", m.group(1))] = (m.group(2), m.group(3))
    return rows


@pytest.fixture
def fake_engine():
    Fake.files = files_listed()
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def app_verdicts(settings) -> dict[str, str]:
    client = NodeClient("http://127.0.0.1:10", DEV_3080, files=files_listed())
    items = asyncio.run(preflight.check_instance(settings, "b", client, set()))
    return {f"Model: {i['label']}": STATE[i["state"]] for i in items if i["id"].startswith("model.b.")}


def test_script_without_the_app_agrees_with_the_app(settings, fake_engine, tmp_path):
    app = app_verdicts(settings)
    # earlier copies stand in for both Z-Image files, as the app says
    assert app["Model: Z-Image Turbo"] == "Ready" and app["Model: Z-Image base"] == "Ready"
    rows = run_script(tmp_path, fake_engine, "http://127.0.0.1:1")
    assert rows, "the script printed no model rows"
    for label, (state, detail) in rows.items():
        if label in app:
            assert state == app[label], (label, state, detail, app[label])
    assert rows["Model: Z-Image Turbo"][0] == "Ready" and "earlier copy" in rows["Model: Z-Image Turbo"][1]
    assert rows["Model: Z-Image base"][0] == "Ready" and "earlier copy" in rows["Model: Z-Image base"][1]
    assert rows["Model: Qwen-Image-Edit-2511"][0] == "Ready"
    assert rows["Model: FLUX.2 Klein 4B"][0] == "Missing"


def test_script_shows_the_apps_own_verdict_when_it_answers(fake_engine, tmp_path):
    items = [
        {"id": "model.b.zturbo", "group": "models", "label": "Z-Image Turbo", "state": "ok", "fix": "",
         "detail": "The app's own file is not installed; a file you chose or an earlier copy is used instead.",
         "technical": "instance b: x; using Z-Image/old.safetensors"},
        {"id": "model.b.klein", "group": "models", "label": "FLUX.2 Klein 4B", "state": "warn", "detail": "The model file is not installed.", "fix": "Download it.", "technical": ""},
    ]

    class App(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            data = json.dumps({"items": items}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rows = run_script(tmp_path, fake_engine, f"http://127.0.0.1:{srv.server_address[1]}")
    finally:
        srv.shutdown()
    assert rows["Model: Z-Image Turbo"][0] == "Ready" and "earlier copy" in rows["Model: Z-Image Turbo"][1] and "old.safetensors" in rows["Model: Z-Image Turbo"][1]
    assert rows["Model: FLUX.2 Klein 4B"][0] == "Missing"
    assert set(rows) == {"Model: Z-Image Turbo", "Model: FLUX.2 Klein 4B"}
