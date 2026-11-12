"""Mouth measure (docs/MOUTH-PLAN.md phase 0): how far the model's lips are from the picture's.

For a finished clean-up (prep) version it loads prep.glb and base_color.png, renders the head from the front with
face.head_camera and face.render_view, and finds lip points (vgac/lipmarks.py) in three images:
  a  the asset's original picture (the concept cut-out) framed like the camera frames the head (face.painted_head),
  b  the textured render,
  c  an untextured shaded render of the same mesh from the same camera (if MediaPipe finds no face there, the lip line
     is the dark crease under the nose, lipmarks.valley_row; the sheet says which).
Reported as shares of the face width (face.find_face's width on the render):
  paint against geometry  (b vs c): corner distance, lip-line offset, mouth width ratio, opening
  picture against paint   (a vs b): the same, raw and with the eyes' middles put together
  picture against geometry (a vs c) as well, and the openings of a, b and c.
One sheet per asset: bakeoff/out/mouth/<label>.png; numbers: <label>.json and results.json.

Usage (from the repo root; the backend may run, it is only asked for GET /api/...):
  backend/.venv/Scripts/python.exe bakeoff/mouth_measure.py label=asset_id [label=asset_id ...] [--prep VERSION_ID]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).parent
sys.path.insert(0, str(ROOT / "backend"))
from vgac import face, lipmarks  # noqa: E402

API = "http://127.0.0.1:8765"
OUT = Path(HERE) / "out" / "mouth"
LIBRARY = ROOT / "library"


def get(path: str) -> Any:
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.load(r)


def newest_done(asset: dict[str, Any], stage: str) -> dict[str, Any] | None:
    vs = [v for v in asset["stages"].get(stage, []) if v["status"] == "done"]
    return sorted(vs, key=lambda v: v["created_at"])[-1] if vs else None


def locate(asset_id: str, prep_id: str | None = None) -> dict[str, Any]:
    a = get("/api/assets/" + asset_id)
    prep = next(v for v in a["stages"]["prep"] if v["id"].startswith(prep_id)) if prep_id else newest_done(a, "prep")
    concept = newest_done(a, "concept")
    files = prep["files"]
    cut = (concept or {}).get("files", {}).get("cutout")
    return {"name": a["name"], "prep": prep["id"], "glb": LIBRARY / files["glb"], "base_color": LIBRARY / files["base_color"],
            "cutout": LIBRARY / cut if cut else None}


def shaded(mesh: dict[str, np.ndarray], cam: face.Camera) -> np.ndarray:
    """A skin-coloured, untextured render lit from above and the left of the camera, with a little light from the
    front: the shape alone. (H, W, 3) floats."""
    ras = face.view_raster(mesh, cam)
    m = ras["tri"] >= 0
    n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)
    right, up, fwd = cam.basis
    back = -fwd
    key = -0.45 * right + 0.6 * up + 0.66 * back
    key /= np.linalg.norm(key)
    fill = 0.3 * right + 0.1 * up + 0.95 * back
    fill /= np.linalg.norm(fill)
    lit = 0.18 + 0.62 * np.clip(n @ key, 0, 1) + 0.3 * np.clip(n @ fill, 0, 1)
    skin = np.array([0.80, 0.62, 0.52])
    img = np.full((cam.size, cam.size, 3), 0.25)
    img[m] = np.clip(lit[m][:, None] * skin, 0, 1)
    return img


def valley_points(img: np.ndarray, info: dict[str, Any]) -> dict[str, Any] | None:
    """Fallback lip points from the crease under the nose: only the lip line and the middle (no corners)."""
    lum = face.luminance(img)
    nx, ny = info["nose"]
    y = lipmarks.valley_row(face.blur(lum, max(1.0, info["width_px"] / 250)), nx, ny, info["width_px"])
    if y is None:
        return None
    w = info["width_px"]
    return {"left": (nx - 0.15 * w, y), "right": (nx + 0.15 * w, y), "upper": (nx, y), "lower": (nx, y),
            "inner_upper": (nx, y), "inner_lower": (nx, y), "opening_px": 0.0, "line_y": y, "eye_mid": None,
            "eye_px": None, "face_w": None, "how": "valley"}


def fit_check(pic: np.ndarray, pa: dict[str, Any], pc: dict[str, Any], info: dict[str, Any], w: float) -> dict[str, Any]:
    """Phase 1, offline: the picture fitted to the bare geometry's landmarks (c) by face.fit_landmarks without the mouth
    corners (the old fit) and with them (the new one); the lips found again on each fitted picture and compared with
    c's, as shares of the face width."""
    def lm(p: dict[str, Any], corners: bool) -> dict[str, Any]:
        d = {"eyes": p["eyes"], "mouth": p["mouth"]}
        if corners:
            d["mouth_corners"] = [p["left"], p["right"]]
        return d

    out: dict[str, Any] = {}
    for name, corners in (("old", False), ("new", True)):
        fit = face.fit_landmarks(lm(pa, corners), lm(pc, corners), info["nose"])
        if fit is None:
            out[name] = None
            continue
        moved = lipmarks.find(face.apply_alignment(pic, fit))
        r = {"sx": fit["sx"], "sy": fit["sy"], "eye_residual": round(fit["residual_px"] / w, 4)}
        if moved:
            r.update({k: None if v is None else round(v, 4) for k, v in lipmarks.compare(pc, moved, w).items()
                      if k in ("corner_dist", "line_dy", "mid_dx", "width_ratio")})
        out[name] = r
    return out


def rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.float64) / 255.0


def measure(label: str, asset_id: str, prep_id: str | None = None) -> dict[str, Any]:
    loc = locate(asset_id, prep_id)
    mesh = face.load_mesh(loc["glb"])
    tex = face.read_rgb(loc["base_color"])
    head = face.find_head(mesh["verts"])
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
    res: dict[str, Any] = {"label": label, "asset": asset_id, "name": loc["name"], "prep": loc["prep"]}
    if not info["found"]:
        res["error"] = "no face found on the render: " + str(info["reason"])
        return res
    w = float(info["width_px"])
    res["face_width_px"] = round(w, 1)
    imgs: dict[str, np.ndarray | None] = {"b": view["image"], "c": shaded(mesh, cam), "a": None}
    if loc["cutout"] is not None:
        ph = face.painted_head(rgba(loc["cutout"]), head, cam, 1024)
        imgs["a"] = ph["image"] if ph else None
    pts: dict[str, dict[str, Any] | None] = {}
    for k in "abc":
        p = lipmarks.find(imgs[k]) if imgs[k] is not None else None
        if p is None and k == "c":
            p = valley_points(imgs[k], info)
        pts[k] = p
    res["method"] = {k: (pts[k] or {}).get("how") for k in "abc"}
    res["points"] = {k: pts[k] for k in "abc"}
    if pts["a"]:
        res["opening_a"] = round(pts["a"]["opening_px"] / w, 4)
    res["opening_b"] = round(pts["b"]["opening_px"] / w, 4) if pts["b"] else None
    res["opening_c"] = round(pts["c"]["opening_px"] / w, 4) if pts["c"] else None
    cmp_: dict[str, Any] = {}
    for name, x, y, eye in (("paint_vs_geometry", "c", "b", False), ("picture_vs_paint", "b", "a", False),
                            ("picture_vs_paint_eyes", "b", "a", True), ("picture_vs_geometry", "c", "a", False)):
        if pts[x] and pts[y]:
            cmp_[name] = {k: (None if v is None else round(v, 4)) for k, v in lipmarks.compare(pts[x], pts[y], w, eye).items()}
    res["compare"] = cmp_
    if pts["a"] and pts["c"] and pts["c"].get("how") == "mediapipe" and imgs["a"] is not None:
        res["fit"] = fit_check(imgs["a"], pts["a"], pts["c"], info, w)
    # the sheet
    OUT.mkdir(parents=True, exist_ok=True)
    nx, ny = info["nose"]
    box = (nx - 0.75 * w, ny - 0.55 * w, nx + 0.75 * w, ny + 0.75 * w)
    cell = 520
    ch = int(cell * (box[3] - box[1]) / (box[2] - box[0]))
    sheet = Image.new("RGB", (cell * 3, ch + 90), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    titles = {"a": "a  picture", "b": "b  textured render", "c": "c  bare shaded render"}
    for i, k in enumerate("abc"):
        x0 = i * cell
        d.text((x0 + 6, 4), f"{titles[k]}   [{(pts[k] or {}).get('how', 'no points')}]", fill=(255, 255, 255))
        if imgs[k] is None:
            continue
        im = Image.fromarray(face.to_uint8(imgs[k])).crop(tuple(int(v) for v in box)).resize((cell, int(cell * (box[3] - box[1]) / (box[2] - box[0]))), Image.LANCZOS)
        sheet.paste(im, (x0, 20))
        sc = cell / (box[2] - box[0])
        p = pts[k]
        if not p:
            continue
        dd = ImageDraw.Draw(sheet)

        def px(q: tuple[float, float]) -> tuple[float, float]:
            return x0 + (q[0] - box[0]) * sc, 20 + (q[1] - box[1]) * sc

        for key, col in (("left", (255, 60, 60)), ("right", (255, 60, 60)), ("upper", (60, 255, 60)), ("lower", (80, 140, 255)),
                         ("inner_upper", (255, 230, 60)), ("inner_lower", (255, 230, 60))):
            cx, cy = px(p[key])
            dd.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), outline=col, width=2)
        ly = px((0, p["line_y"]))[1]
        dd.line((x0 + 8, ly, x0 + 40, ly), fill=(255, 255, 255), width=1)
    lines = []
    pct = lambda v, f="{:.1f}%": "n/a" if v is None else f.format(v * 100)  # noqa: E731
    for name, c in cmp_.items():
        lines.append(f"{name}: corners {pct(c['corner_dist'])}  line dy {pct(c['line_dy'], '{:+.1f}%')}  mid dx {pct(c['mid_dx'], '{:+.1f}%')}  "
                     f"width x{'n/a' if c['width_ratio'] is None else format(c['width_ratio'], '.2f')}  open {pct(c['open_a'])} -> {pct(c['open_b'])}")
    d.text((6, ch + 28), f"{label} {loc['name']}  prep {loc['prep'][:8]}  face width {w:.0f}px.  Pairs read (first -> second): "
           "paint_vs_geometry c->b, picture_vs_paint b->a, picture_vs_geometry c->a", fill=(200, 200, 200))
    for j, t in enumerate(lines[:3]):
        d.text((6, ch + 42 + 12 * j), t, fill=(255, 255, 255))
    sheet.save(OUT / f"{label}.png")
    (OUT / f"{label}.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def main(argv: list[str]) -> int:
    prep = None
    if "--prep" in argv:
        i = argv.index("--prep")
        prep = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    jobs = [a.split("=", 1) for a in argv if "=" in a]
    results = []
    for label, aid in jobs:
        try:
            r = measure(label, aid, prep)
        except Exception as e:  # noqa: BLE001
            r = {"label": label, "asset": aid, "error": repr(e)}
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "points"}, default=float))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
