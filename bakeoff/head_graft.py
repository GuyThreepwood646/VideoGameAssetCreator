"""Head graft prototype: put a head built on its own (head_build.py) onto the full-body build (bald_knight.py).

1. Keystones from each model's side outline (face_reshape.profile_keys on a side render): the top of the head, the nose
   tip, the chin bottom and the neck (the deepest point between the chin and a collar).
2. The head model is scaled so its top-to-chin height equals the body's, moved so the tops meet, centred on the body's
   head across, and moved forward or back so the nose tips meet.
3. Cut at the body's neck row: the body loses every face above it within the head's reach (the collar, wider, stays);
   the head model keeps its head and a short stub of neck that runs down inside the body's collar (its own collar and
   shoulders, wider than the neck, go). Each part keeps its own texture: the GLB has two meshes.
4. Renders front, three-quarter and side of: the body as built, the grafted model, the head model alone; outline match
   of the grafted head against the body's own outline in the side view; the cut, the scale and the moves.

Usage: backend/.venv/Scripts/python.exe bakeoff/head_graft.py
Output: bakeoff/out/texture_trials/graft/{model.glb, report.json, sheet.png}. Bake-off files only.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import trimesh
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from vgac import face  # noqa: E402

from face_reshape import profile_keys  # noqa: E402

ROOT = os.path.join(HERE, "out", "texture_trials")
# python head_graft.py [body_build head_build out_name]: default the bald knight
BODY = os.path.join(ROOT, sys.argv[1] if len(sys.argv) > 3 else "knightbald_u1024_t4096")
HEAD = os.path.join(ROOT, sys.argv[2] if len(sys.argv) > 3 else "knightbaldhead_u1024_t4096")
OUT = os.path.join(ROOT, sys.argv[3] if len(sys.argv) > 3 else "graft")
STUB = 0.18  # how far the head's neck runs below the cut, as a share of the top-to-chin height
NECK_R = 0.3
HAIR_TOL = 18.0  # Lab distance from the crown's colour that still counts as hair
SKIN_TOL = 22.0  # Lab distance (lightness at a third) from the cheek colour that still counts as skin


def face_lab(mesh: dict, tex: np.ndarray) -> np.ndarray:
    """Each triangle's texture colour at its middle (Lab)."""
    uv = mesh["uv"][mesh["faces"]].mean(axis=1)
    T = tex.shape[0]
    x = np.clip((uv[:, 0] * T).astype(int), 0, T - 1)
    y = np.clip(((1 - uv[:, 1]) * T).astype(int), 0, T - 1)
    return face.to_lab(tex[y, x][:, None, :])[:, 0, :]

def skin_lab(mesh: dict, tex: np.ndarray, share: float) -> np.ndarray:
    h = face.find_head(mesh["verts"], share)
    cam = face.head_camera(h, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    pos = face.interpolate(mesh["verts"], mesh["faces"], view["raster"])
    z = np.where(view["mask"], view["raster"]["z"], np.inf)
    nz = face.find_nose(view["raster"]["z"], view["mask"], pos, cam, h)
    if nz is None:
        sub = z[300:750, 350:674]
        iy, ix = np.unravel_index(int(np.argmin(sub)), sub.shape)
        nz = (ix + 350, iy + 300)
    nx, ny = nz
    lab = face.to_lab(view["image"])
    # the nose, from a little above its tip to the tip: always skin (a cheek can sit in a fringe's shadow)
    win = lab[ny - 30: ny + 4, nx - 8: nx + 9].reshape(-1, 3)
    skin = np.median(win, axis=0)
    # the crown: hair, or skin on a bald head (then the two references are alike)
    rows = np.where(view["mask"][:, nx])[0]
    crown = np.median(lab[rows[0] + 8: rows[0] + 40, nx - 30: nx + 30][view["mask"][rows[0] + 8: rows[0] + 40, nx - 30: nx + 30]], axis=0)
    return np.stack([skin, crown])

def is_skin(lab: np.ndarray, refs: np.ndarray) -> np.ndarray:
    """Skin, or hair: near the skin colour (lightness counted at a third: skin is lit and shaded), or near the
    crown's colour when the crown is clearly not skin (hair; lightness counted in full, since dark hair and grey
    armour differ mostly in lightness)."""
    skin, crown = refs
    out = np.linalg.norm((lab - skin) * np.array([0.35, 1.0, 1.0]), axis=1) < SKIN_TOL
    if np.linalg.norm(crown - skin) > 15.0:
        out |= np.linalg.norm(lab - crown, axis=1) < HAIR_TOL
    return out



def side_keys(mesh: dict, tex: np.ndarray, share: float) -> dict:
    """Keystones on the side outline. The face's own outline is read from skin-coloured pixels only (a fringe or a
    bob reaching further forward than the nose would otherwise be read as the face); the top is the whole outline's."""
    head = face.find_head(mesh["verts"], 0.13 if share < 0.5 else 0.45)
    span = share * head.height * 1.1
    tgt = np.array([head.centre[0], head.top - 0.5 * span + 0.04 * span, head.centre[2]])
    cam = face.Camera(target=tgt, yaw_deg=90, scale=span, size=1024)
    view = face.render_view(mesh, tex, cam)
    p, _ = cam.project(np.array([[head.centre[0], head.centre[1], head.centre[2] + 0.1]]))
    refs = skin_lab(mesh, tex, 0.13 if share < 0.5 else 0.45)
    lab = face.to_lab(view["image"]).reshape(-1, 3)
    skin_px = (np.linalg.norm((lab - refs[0]) * np.array([0.35, 1.0, 1.0]), axis=1) < SKIN_TOL).reshape(view["mask"].shape)
    skin_px = face.fill_holes(skin_px & view["mask"])
    top = int(np.where(view["mask"].any(axis=1))[0][0])
    k = profile_keys(skin_px, bool(p[0, 0] < 512), top_row=top)
    y = lambda r: float(cam.target[1] + (512 - r) * span / 1024)  # noqa: E731
    out = {n: y(k[n]) for n in ("top", "tip", "chin_bottom", "neck") if k.get(n) is not None}
    # the nose tip's forward position: the most forward vertex near the tip's height and the head's middle
    v = mesh["verts"]
    near = (np.abs(v[:, 1] - out["tip"]) < 0.02 * span) & (np.abs(v[:, 0] - head.centre[0]) < 0.25 * head.width)
    out["tip_z"] = float(v[near, 2].max())
    out["centre_x"] = float(head.centre[0])
    out["head_width"] = float(head.width)
    out["rows"] = {n: k.get(n) for n in ("top", "tip", "subnasale", "chin_front", "chin_bottom", "neck")}
    return out


def composite(parts: list[tuple[dict, np.ndarray]], cam: face.Camera, background: float = 0.25) -> np.ndarray:
    img = np.full((cam.size, cam.size, 3), background)
    zbuf = np.full((cam.size, cam.size), np.inf)
    for mesh, tex in parts:
        r = face.render_view(mesh, tex, cam, background=background)
        z = np.where(r["mask"], r["raster"]["z"], np.inf)
        take = z < zbuf
        img[take] = r["image"][take]
        zbuf = np.minimum(zbuf, z)
    return img


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    body = face.load_mesh(os.path.join(BODY, "model.glb"))
    btex = face.read_rgb(os.path.join(BODY, "base_color.png"))
    hm = face.load_mesh(os.path.join(HEAD, "model.glb"))
    htex = face.read_rgb(os.path.join(HEAD, "base_color.png"))
    kb = side_keys(body, btex, 0.2)
    kh = side_keys(hm, htex, 0.7)
    s = (kb["top"] - kb["chin_bottom"]) / (kh["top"] - kh["chin_bottom"])
    hv = hm["verts"] * s
    t = np.array([kb["centre_x"] - kh["centre_x"] * s, kb["top"] - kh["top"] * s, kb["tip_z"] - kh["tip_z"] * s])
    hv = hv + t
    width_ratio = (kh["head_width"] * s) / kb["head_width"]
    print("scale", round(s, 4), "move", t.round(4), "head width after / body head width", round(width_ratio, 3))

    cut = kb["neck"]
    stub = STUB * (kb["top"] - kb["chin_bottom"])
    bhead = face.find_head(body["verts"])
    axis = np.array([kb["centre_x"], float(bhead.centre[2])])  # the head's middle, front to back (median of its vertices)

    H = kb["top"] - kb["chin_bottom"]

    # head model: the head, and below it only skin and hair (the neck, hair hanging down), down to the stub's end
    hc = hv[hm["faces"]].mean(axis=1)
    h_refs = skin_lab(hm, htex, 0.45)
    print("head model skin and crown references", h_refs.round(1).tolist())
    h_skin = is_skin(face_lab(hm, htex), h_refs)
    # (its collar rises to about the chin at the sides: non-skin is kept only from a little above the chin, where the
    # lips, eyes and brows are)
    keep_h = (hc[:, 1] > kb["chin_bottom"] + 0.15 * H) | ((hc[:, 1] > cut - stub) & h_skin)
    # body: drop its old head, hair and neck (skin or hair above the stub's end) and anything inside the new head
    hk = hm["faces"][keep_h]
    hvn = face.smooth_normals(hv, hk)
    used = np.unique(hk)
    P, N = hv[used], hvn[used]
    bc = body["verts"][body["faces"]].mean(axis=1)
    b_skin = is_skin(face_lab(body, btex), skin_lab(body, btex, 0.13))
    cand = np.where(bc[:, 1] > cut - stub)[0]
    d2 = ((bc[cand, None, :] - P[None, :, :]) ** 2).sum(axis=2)
    j = np.argmin(d2, axis=1)
    off = ((bc[cand] - P[j]) * N[j]).sum(axis=1)
    # strictly inside the new head, and only above the chin (the collar below hugs the new neck from outside)
    inside = (off < 0.0) & (np.sqrt(d2[np.arange(len(cand)), j]) < 0.6 * kb["head_width"]) & (bc[cand, 1] > kb["chin_bottom"])
    drop_b = np.zeros(len(bc), dtype=bool)
    drop_b[cand] = inside | b_skin[cand]
    print("body drops: inside the new head", int(inside.sum()), "; skin", int(b_skin[cand].sum()), "; both", int((inside & b_skin[cand]).sum()),
          "; skin and crown references", skin_lab(body, btex, 0.13).round(1).tolist())
    print("body faces dropped", int(drop_b.sum()), "of", len(drop_b), "; head faces kept", int(keep_h.sum()), "of", len(keep_h))

    body_kept = {"verts": body["verts"], "faces": body["faces"][~drop_b], "uv": body["uv"]}
    head_kept = {"verts": hv, "faces": hm["faces"][keep_h], "uv": hm["uv"]}
    for m in (body_kept, head_kept):
        m["vnormals"] = face.smooth_normals(m["verts"], m["faces"])

    # GLB: two meshes, each with its own texture (trimesh keeps each mesh's material)
    tb = trimesh.load(os.path.join(BODY, "model.glb"), force="mesh", process=False)
    th = trimesh.load(os.path.join(HEAD, "model.glb"), force="mesh", process=False)
    tb.update_faces(~drop_b)
    th.vertices = hv
    th.update_faces(keep_h)
    scene = trimesh.Scene()
    scene.add_geometry(tb, node_name="body")
    scene.add_geometry(th, node_name="head")
    scene.export(os.path.join(OUT, "model.glb"))

    # renders: body as built | grafted | head model alone (aligned), front / three-quarter / side, framed on the head
    head = face.find_head(body["verts"])
    cams = [face.head_camera(head, yaw, 1024, margin=2.0) for yaw in (0, 35, 90)]
    rows = [("bald body as built", [(body, btex)]), ("grafted", [(body_kept, btex), (head_kept, htex)]),
            ("head model alone", [(head_kept, htex)])]
    cell = 400
    sheet = Image.new("RGB", (cell * 3, cell * len(rows)), (40, 40, 40))
    for i, (label, parts) in enumerate(rows):
        for j, cam in enumerate(cams):
            img = composite(parts, cam)
            sheet.paste(Image.fromarray(face.to_uint8(img)).resize((cell, cell), Image.LANCZOS), (j * cell, i * cell))
        ImageDraw.Draw(sheet).text((8, i * cell + 8), label, fill=(255, 255, 255))
    sheet.save(os.path.join(OUT, "sheet.png"))
    # the whole figure, front and three-quarter, so the head's size reads against the body
    span = 1.08 * (body["verts"][:, 1].max() - body["verts"][:, 1].min())
    c = (body["verts"].min(0) + body["verts"].max(0)) / 2
    full = Image.new("RGB", (cell * 4, cell * 2), (40, 40, 40))
    for j, yaw in enumerate((0, 35)):
        cam = face.Camera(target=c, yaw_deg=yaw, scale=span, size=1024)
        for i, (label, parts) in enumerate(rows[:2]):
            full.paste(Image.fromarray(face.to_uint8(composite(parts, cam))).resize((cell * 2, cell * 2), Image.LANCZOS).crop((cell // 2, 0, cell * 3 // 2, cell * 2)),
                       (j * cell * 2 + i * cell, 0))
    full.save(os.path.join(OUT, "full.png"))
    rep = {"scale": s, "move": t.tolist(), "width_ratio": width_ratio, "cut_y": cut, "stub_m": stub, "body_keys": kb, "head_keys": kh,
           "body_faces_dropped": int(drop_b.sum()), "head_faces_kept": int(keep_h.sum())}
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=float)


if __name__ == "__main__":
    main()
