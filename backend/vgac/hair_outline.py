"""The hair's outline on each picture from the item finder (Grounding DINO tiny and SAM 2.1 small, both Apache-2.0, in their
own Python: item_suggest.installed). finder_worker.py is asked for the phrase "hair" on the pictures hair.find_hair votes with.
-> (outlines {picture name: bool array in the picture's own size}, record). Nothing found, the finder missing or failing:
({}, record) and the hair is found by its colours alone, as before."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import item_suggest

PHRASE = "hair"
SIDE = 1024  # the longest side the finder sees
BACKGROUND = 128  # cut-outs are shown to the finder on mid grey
MIN_SHARE = 0.002  # an outline smaller than this share of the picture is a speck, not hair


def find(settings: Any, pictures: dict[str, Path], work: Path) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    if not item_suggest.installed(settings):
        return {}, {"finder": "not installed"}
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, tuple[int, int]] = {}
    for name, path in pictures.items():
        im = Image.open(path).convert("RGBA")
        sizes[name] = (im.width, im.height)
        bg = Image.new("RGBA", im.size, (BACKGROUND, BACKGROUND, BACKGROUND, 255))
        shown = Image.alpha_composite(bg, im).convert("RGB")
        shown.thumbnail((SIDE, SIDE), Image.LANCZOS)
        shown.save(work / f"{name}_color.png")
    if not sizes:
        return {}, {"finder": "no pictures"}
    env = {**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}  # two cores, not all
    t0 = time.time()
    try:
        r = subprocess.run([str(item_suggest.finder_python(settings)), str(item_suggest.WORKER), "hair", str(work), PHRASE,
                            str(item_suggest.finder_models(settings)), ",".join(sizes)],
                           capture_output=True, text=True, timeout=1800, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return {}, {"finder": "failed: " + str(e)[:160]}
    if r.returncode != 0 or "FINDER_OK" not in r.stdout:
        return {}, {"finder": "failed: " + (r.stderr or r.stdout)[-160:].strip()}
    found = json.loads((work / "detection.json").read_text(encoding="utf-8")).get("views", {})
    out: dict[str, np.ndarray] = {}
    for name, (w, h) in sizes.items():
        m = np.load(work / f"{name}_mask.npy")
        if m.shape != (h, w):
            m = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 127
        if m.mean() >= MIN_SHARE:
            out[name] = m.astype(bool)
    return out, {"finder": "ok", "finder_seconds": round(time.time() - t0, 1), "views": {k: v for k, v in found.items()}}
