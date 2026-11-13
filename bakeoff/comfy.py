"""Minimal synchronous ComfyUI client for the phase 0 bake-off scripts.

Instance A (3D, RTX 3060) is http://127.0.0.1:8188.
Instance B (images, RTX 3080) is http://127.0.0.1:8189.
"""
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

A = "http://127.0.0.1:8188"
B = "http://127.0.0.1:8189"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from local_paths import comfy_input  # noqa: E402

INPUT_DIR = comfy_input() or ""  # VGAC_COMFY_INPUT, else comfy_input_dir in the app's settings
BS = chr(92)


def post(base, path, data):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def get(base, path):
    return json.load(urllib.request.urlopen(base + path))


def run(base, graph, poll=1.0):
    """Queue a graph and block until it finishes. Returns (outputs dict, seconds)."""
    resp = post(base, "/prompt", {"prompt": graph})
    if resp.get("node_errors"):
        raise RuntimeError("node_errors: " + json.dumps(resp["node_errors"], indent=1)[:4000])
    pid = resp["prompt_id"]
    t0 = time.time()
    while True:
        hist = get(base, "/history/" + pid)
        if pid in hist:
            h = hist[pid]
            if h.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(h["status"], indent=1)[:3000])
            return h["outputs"], round(time.time() - t0, 1)
        time.sleep(poll)


def download(base, item, dest):
    q = "filename=%s&subfolder=%s&type=%s" % (
        urllib.parse.quote(item["filename"]), urllib.parse.quote(item.get("subfolder", "")), item.get("type", "output"))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    urllib.request.urlretrieve(base + "/view?" + q, dest)
    return dest


def images_of(outputs):
    return [i for o in outputs.values() for i in o.get("images", [])]


def files_of(outputs, key):
    """Collect output entries under an arbitrary key such as '3d' or 'result'."""
    return [i for o in outputs.values() for i in o.get(key, [])]


def stage_input(src, name=None):
    """Copy a local file into the shared ComfyUI input directory and return the name LoadImage expects."""
    name = name or ("bakeoff_" + os.path.basename(src))
    shutil.copy(src, os.path.join(INPUT_DIR, name))
    return name
