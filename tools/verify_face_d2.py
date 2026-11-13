"""Offline, read-only check of docs/AFTER-TEST-CHANGES.md D2 items 1 to 4 against the stored runs of the realistic test
(projects "RCT Realistic PC" and "RCT Realistic Showcase" in library/vgac.sqlite). Nothing is written or run again: it
reads the stored models, views and metrics and prints what the new code decides.

    cd backend && .venv/Scripts/python.exe ../tools/verify_face_d2.py [a|b|c|d ...]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from vgac import face, facing, imaging, quality, srcproject  # noqa: E402
from vgac.imaging import VIEW_YAW, silhouette  # noqa: E402
from vgac.stages import face_detail, prep, turnaround  # noqa: E402

LIB = ROOT / "library"


def runs(project: str = "RCT Realistic PC") -> dict[str, dict]:
    con = sqlite3.connect(f"file:{LIB / 'vgac.sqlite'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    vers = {r["id"]: r for r in con.execute("select * from version")}
    out = {}
    for a in con.execute("select a.id, a.name from asset a join project p on p.id=a.project_id where p.name=?", (project,)):
        mine = [v for v in vers.values() if v["asset_id"] == a["id"] and v["status"] == "done" and v["selected"]]
        st = {}
        for v in mine:
            st[v["stage"]] = {"files": json.loads(v["files_json"] or "{}"), "metrics": json.loads(v["metrics_json"] or "{}")}
        out[a["name"]] = st
    return out


def a_facing(r: dict) -> None:
    print("(a) facing decision: old turn applied -> new turn applied; both tests")
    for name, st in r.items():
        m, pm = st["mesh"]["metrics"], st["prep"]["metrics"]
        files = st["mesh"]["files"]
        vd = (LIB / files["views_dir"]).parent
        info = json.loads((vd / "info.json").read_text())
        rendered = {y: silhouette(vd / f"shade_{y}.png") for y in info.get("yaws", range(0, 360, 15))}
        tf = st["turnaround"]["files"]
        refs = {v: silhouette(LIB / tf[f"{v}_cutout"]) for v in VIEW_YAW}
        scores = facing.turn_scores(refs, rendered)
        res = prep.resolve_facing({"id": "humanoid"}, "auto", {**m, "silhouette_turn_scores": scores},
                                  facing.toes_of_file(LIB / files["glb"], "humanoid"))
        checks = prep.prep_checks({}, {}, res, "humanoid", None)
        bad = [c["name"] for c in checks if not c["passed"]]
        print(f"  {name:22s} old {pm['face_yaw_applied']:3d} new {res['face_yaw_applied']:3d} | outline {res['face_outline_turn']:3d} margin "
              f"{res['face_outline_margin']} | toes {res['face_toes_turn']} | differ {res['face_tests_differ_deg']} by {res['face_decided_by']} | "
              f"remainder {res['face_remainder_deg']} | failed: {bad}")


def b_yaw(r: dict) -> None:
    print("(b) face pass yaw (camera yaw its face is seen from) against the fitted picture's own yaw in the source projection")
    for name, st in r.items():
        m, pm = st["mesh"]["metrics"], st["prep"]["metrics"]
        files = st["mesh"]["files"]
        res = prep.resolve_facing({"id": "humanoid"}, "auto", m, facing.toes_of_file(LIB / files["glb"], "humanoid"))
        y = face_detail.head_yaw_from_remainder(res["face_remainder_deg"])
        views = pm.get("srcproject_views") or {}
        fits = {k: (v.get("yaw"), v.get("iou")) for k, v in views.items() if k in ("source", "front", "picture") and "refused" not in v
                and v.get("yaw") is not None}
        s = next((v["yaw"] for k, v in views.items() if k == "source" and "refused" not in v and v.get("yaw") is not None), None)
        print(f"  {name:22s} applied {pm['face_yaw_applied']:3d} head yaw {y:6.1f} | source/front fit yaw "
              f"{(facing.signed(s) if s is not None else None)} | fits {fits}")


def c_same_side(r: dict) -> None:
    print("(c) same-side test on the stored views (flagged = as drawn above mirrored + margin)")
    for name, st in r.items():
        f = st["turnaround"]["files"]
        row = []
        for a, b in (("left", "right"), ("quarter", "quarter2")):
            if f.get(f"{a}_cutout") and f.get(f"{b}_cutout"):
                d, mi = imaging.pair_overlap(LIB / f[f"{a}_cutout"], LIB / f[f"{b}_cutout"])
                chk = turnaround.same_side_check(b, d, mi)
                row.append(f"{a}/{b} {d:.3f} vs {mi:.3f} {'FLAGGED' if not chk['ok'] else 'ok'}")
        print(f"  {name:22s} " + " | ".join(row))


def d_padding(r: dict) -> None:
    print("(d) outline match of the original picture in the source projection: squashed (old) against padded (new)")
    for name in ("W1-clothed-tall", "P1-photo", "W1-clothed-tall-face", "W1-clothed"):
        st = r[name]
        mesh = face.load_mesh(LIB / st["prep"]["files"]["glb"])
        centre, scale = srcproject.model_frame(mesh)
        im = Image.open(LIB / st["concept"]["files"]["cutout"]).convert("RGBA")
        w, h = im.size
        rms = {yaw: srcproject.outline(mesh, face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=256))
               for yaw in (-20, -10, -5, 0, 5, 10, 20, 340, 350)}
        for label, padded in (("squashed", False), ("padded", True)):
            if padded:
                _, alpha = srcproject.prepare_picture(im, 1024)
            else:  # the old behaviour: straight to a square
                arr = np.asarray(im, dtype=np.float64) / 255.0
                alpha = face.resize(arr[..., 3], 1024, Image.BILINEAR)
                alpha = np.clip(alpha, 0, 1)
            best = max(srcproject.fit_similarity(alpha > 0.5, rm, n=256)["iou"] for rm in rms.values())
            print(f"  {name:22s} {w}x{h} {label:9s} best outline match {best:.3f} (needs {srcproject.SOURCE_MIN_IOU})")


if __name__ == "__main__":
    which = sys.argv[1:] or ["a", "b", "c", "d"]
    data = runs()
    data.update({f"{k} (showcase)": v for k, v in runs("RCT Realistic Showcase").items()} if "a" in which else {})
    for k in which:
        {"a": a_facing, "b": b_yaw, "c": c_same_side, "d": d_padding}[k](data if k != "d" else runs())
