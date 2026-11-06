"""Offline check of the hair library and the fit engine on the five saved wig-trial bald builds (lane 1 of
docs/HAIR-LIBRARY-PLAN.md). Runs the app's own code: baldpic on the trial pictures, hairfit.rank and hairfit.attach
with a stand-in for the step context (Blender run directly, absolute paths). No app job, no POST call.

    backend/.venv/Scripts/python.exe tools/hairlib_check.py [char ...]

Needs runtime/tmp/hairlib/paths.json and runtime/tmp/wigtrial (the trials' saved bald builds and pictures; not in
git). Writes runtime/tmp/hairlib/check/<char>/ and runtime/tmp/hairlib/check/check.json, and prints the table against
round two's choices and scores (runtime/tmp/hairlib/r2/<char>/match2.json)."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "runtime" / "tmp" / "wigtrial"))
from vgac import baldpic, hairfit, hairstyles  # noqa: E402

BLENDER = r"F:/Tools/blender-5.2.3-windows-x64/blender.exe"
HL = ROOT / "runtime" / "tmp" / "hairlib"
OUT = HL / "check"
SAME = {"toigo_blunt_bob_with_bangs": "mh_toigo_blunt_bob_bangs", "bob02": "mh_bob02", "ponytail01": "mh_ponytail01",
        "Hair_BuzzedFemale": "q_buzzed_female", "culturalibre_hair_01": None}
CHARS = ("knight1", "knight2", "ponytail", "man", "mage")


class FakeAsset:
    def __init__(self, prompt: str, look: str):
        self.prompt, self.style_preset = prompt, look


class FakeSC:
    """The parts of StageContext hairfit uses."""

    def __init__(self, d: Path, prompt: str, look: str, platform: str = "pc_mid"):
        self.dir, self.asset, self.platform, self.models = d, FakeAsset(prompt, look), platform, []

    async def blender(self, script, args, log_name=None, timeout=3600):
        cmd = [BLENDER, "-b", "--python", str(ROOT / "backend" / "vgac" / "blender" / script), "--", *[str(a) for a in args]]
        log = self.dir / (log_name or "blender.log")

        def run():
            with open(log, "w", encoding="utf-8") as f:
                r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
            if r.returncode:
                raise RuntimeError(f"blender failed, see {log}")
        await asyncio.to_thread(run)

    def use_model(self, mid):
        self.models.append(mid)


def pictures(ch: str, d: Path) -> dict[str, Path]:
    from chars import CHARS as TRIAL
    orig = Image.open(TRIAL[ch]["img"]).convert("RGB")
    cut = Image.open(ROOT / "runtime" / "tmp" / "wigtrial" / ch / "bald_full_s1101_cut.png").convert("RGBA").resize(orig.size)
    mask, rec = baldpic.picture_record(orig, cut)
    outline = d / "hair_outline_front.png"
    Image.fromarray((mask * 255).astype(np.uint8), "L").save(outline)
    cutp = d / "bald_front_cutout.png"
    cut.save(cutp)
    pj = d / "hair_picture.json"
    pj.write_text(json.dumps(rec, indent=1, default=float), encoding="utf-8")
    return dict(picture=pj, cutout=cutp, outline=outline, prompt=TRIAL[ch]["hairdesc"], look=TRIAL[ch]["look"])


async def one(ch: str) -> dict:
    d = OUT / ch
    d.mkdir(parents=True, exist_ok=True)
    paths = json.loads((HL / "paths.json").read_text(encoding="utf-8"))[ch]
    glb = Path(paths["bald"]["glb"])
    pic = pictures(ch, d)
    sc = FakeSC(d, pic["prompt"], pic["look"])
    fam, words = hairstyles.family_of(pic["prompt"])
    t0 = time.time()
    hl = await hairfit.rank(sc, glb, pic["picture"], pic["cutout"], pic["outline"], fam, 0, d / "rank")
    t_rank = time.time() - t0
    res = dict(char=ch, family=fam, words=words, rank=hl, seconds_rank=round(t_rank, 1))
    if hl.get("best"):
        best = hl["candidates"][0]
        t1 = time.time()
        at = await hairfit.attach(sc, glb, best["style"], best["params"], pic["picture"], pic["cutout"], pic["outline"], "normal", 1024, d / "attach")
        res["attach"] = dict(metrics=at["metrics"], files={k: str(v) for k, v in at["files"].items()})
        res["seconds_attach"] = round(time.time() - t1, 1)
    # outline-only ranking over every style (no family), for the record
    r2 = HL / "r2" / ch / "match2.json"
    if r2.is_file():
        m = json.loads(r2.read_text(encoding="utf-8"))
        res["round_two"] = dict(choice=m["choice"], score=m["styles"][m["choice"]]["score"] if m["choice"] else None)
    return res


def table(rows: list[dict]) -> str:
    out = ["| Character | Family | Choice now | Score | Round two | Score | Same style | Diff | Skin through | Colour delta (texture) | Render delta | Tris | Rank s | Attach s |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        hl, at = r["rank"], (r.get("attach") or {}).get("metrics", {})
        r2 = r.get("round_two") or {}
        r2s = (r2.get("choice") or "").split(":")[-1]
        same = None
        if hl.get("best") and r2s:
            same = SAME.get(r2s) == hl["best"]
        diff = None if hl.get("best_score") is None or r2.get("score") is None else round(hl["best_score"] - r2["score"], 3)
        out.append(f"| {r['char']} | {r['family']} | {hl.get('best')} | {hl.get('best_score')} | {r2s} | {r2.get('score')} | {same} | {diff} | "
                   f"{(at.get('skin_through') or {}).get('all')} | {at.get('colour_delta')} | {at.get('render_delta')} | {at.get('triangles')} | {r['seconds_rank']} | {r.get('seconds_attach')} |")
    return "\n".join(out)


async def main(chars):
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for ch in chars:
        r = await one(ch)
        rows.append(r)
        print(ch, json.dumps({k: r["rank"].get(k) for k in ("best", "best_score", "error", "seconds")}), flush=True)
    (OUT / "check.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    t = table(rows)
    (OUT / "check_table.md").write_text(t + "\n", encoding="utf-8")
    print(t)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or list(CHARS)))
