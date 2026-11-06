"""Hair as its own material (docs/HAIR-RESEARCH.md 3.2 (b), stage 2): the maths and the files. The pass itself runs
inside the hair detail pass (stages/hair_detail.py) at the end of the clean-up step; blender/hairmat.py lays the hair
out on its own texture and bakes it; blender/pack.py gives the hair triangles the second material in the files that
ship; the Unity importer (VgacImport.cs) builds a second material, with the "VGAC/Hair" shader on the built-in
pipeline.

What it does, in order:
1. choose(): which hair triangles get their own material. Only triangles that are hair all through: a triangle with
   skin in it (the hairline runs across it) or any of the face's own texels stays with the body material, so the
   hairline stays in the face's texture, where the face pass painted it. A confidence from the hair pass's own colour
   separation and from how much of the hair triangles' texels really are hair colour; under CONFIDENCE_MIN, or when
   too little would move, everything stays one material (the reason is kept and said in plain words).
2. applies(): which assets get it: humanoids whose hair pass ran, on PC and Showcase budgets (not Phone: one material
   and one draw per character matter more there), when the option hair_material is auto or on.
3. The layout and the bake (blender/hairmat.py), then paint_atlas(): the hair pass paints its strands again on the
   hair's own texture, at that texture's density; the flow map (the strand direction per texel, in the texture's own
   directions) is written from the same flow.
4. glb_add_anisotropy(): KHR_materials_anisotropy on the hair material of a GLB (the app's three.js viewer reads it;
   other GLB readers ignore it).

The prep GLB keeps one material and one layout (the rig step and every preview read it as before); the hair texture,
the flow map and hair_layout.json are version files of the clean-up step that the hand-off step picks up.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import face, hair

PARAM = "hair_material"
OPTIONS = ("auto", "on", "off")
PHONE_PLATFORMS = ("mobile_low",)
CONFIDENCE_MIN = 0.7  # under it the split is doubtful and the hair stays in the body material
PURE_MIN = 0.9  # a triangle moves only when at least this share of its texels is hair colour
SKIN_MAX = 0.02  # ... and at most this share is skin colour (else the hairline runs across it)
FACE_MAX = 0.05  # ... and none of the face pass's own texels (eyes, brows, nose, mouth and the face region) are in it
MIN_TRIS = 60  # fewer triangles than this: not worth a second material
MIN_SHARE = 0.5  # the triangles that would move cover less than this share of the hair's surface: not worth it
MIN_PIECE = 8  # loose groups of fewer triangles stay with the body (single triangles between straddling ones)
HAIR_TEX_MIN, HAIR_TEX_MAX = 1024, 2048
ANISOTROPY_STRENGTH = 0.6
DRAWS_ADDED = 1
REASONS = {
    "doubtful": "the hair could not be told from the skin or the clothes with enough confidence to give it a material of its own",
    "too_little": "too little of the hair is hair all through (most of it touches the face or the skin), so it was not worth a second material",
    "error": "something went wrong while laying the hair out on its own texture",
    "no_gain": "its own texture would not have given the hair more texels than the body texture already does",
}
NOT_APPLICABLE = ("off", "phone", "not_category", "no_hair_pass")


def option(params: dict[str, Any]) -> str:
    v = params.get(PARAM)
    if v is True:
        return "on"
    if v is False:
        return "off"
    return v if v in OPTIONS else "auto"


def applies(category_id: str, platform: str | None, budget: dict[str, Any] | None, params: dict[str, Any]) -> str | None:
    """Why the hair does not get its own material here (a NOT_APPLICABLE key), or None when it should be tried. The
    hair pass's own reasons (look, no hair found) come first and are not repeated here."""
    opt = option(params)
    if opt == "off":
        return "off"
    if category_id != "humanoid":
        return "not_category"
    if opt == "auto" and (platform in PHONE_PLATFORMS or int((budget or {}).get("texture") or 2048) < 2048):
        return "phone"
    return None


def hair_texture_size(budget: dict[str, Any] | None) -> int:
    """Half the platform's texture budget, 1024 to 2048: 1024 on PC (2048 budget), 2048 on Showcase (4096)."""
    t = int((budget or {}).get("texture") or 2048)
    return int(min(HAIR_TEX_MAX, max(HAIR_TEX_MIN, t // 2)))


def reason_words(reason: str | None) -> str:
    return REASONS.get(reason or "", "the hair stayed in the body material")


# --- 1. which triangles --------------------------------------------------------------------------------------------


def _components(sel: np.ndarray, nb: list[np.ndarray]) -> list[np.ndarray]:
    seen = np.zeros(len(sel), dtype=bool)
    out = []
    for s in np.nonzero(sel)[0]:
        if seen[s]:
            continue
        stack, comp = [s], []
        seen[s] = True
        while stack:
            t = stack.pop()
            comp.append(t)
            for n in nb[t]:
                if sel[n] and not seen[n]:
                    seen[n] = True
                    stack.append(n)
        out.append(np.asarray(comp))
    return out


def choose(mesh: dict[str, np.ndarray], tris: np.ndarray, base: np.ndarray, centres: np.ndarray, hair_i: int, skin_i: int,
           protect: np.ndarray | None = None, find_confidence: float | None = None,
           tri_label: np.ndarray | None = None) -> dict[str, Any]:
    """Which of the hair triangles `tris` get the hair material. -> {tris (m,) bool, use, reason, confidence,
    measures}. use is False with a REASONS key when the split is doubtful or not worth it."""
    tris = np.asarray(tris, dtype=bool)
    T = base.shape[0]
    reg = hair.texel_region(mesh, T, tris)
    tm = hair.texel_mask(reg, base, tris, centres, hair_i, skin_i)
    n_t = len(tris)
    cnt = np.bincount(reg["tri"], minlength=n_t).astype(np.float64)
    hair_n = np.bincount(reg["tri"], weights=tm["hair"].astype(np.float64), minlength=n_t)
    colour_n = np.bincount(reg["tri"], weights=(tm["label"] == hair_i).astype(np.float64), minlength=n_t)
    skin_n = np.bincount(reg["tri"], weights=tm["skin"].astype(np.float64), minlength=n_t)
    pure = np.where(cnt > 0, hair_n / np.maximum(cnt, 1), 1.0)
    skin = np.where(cnt > 0, skin_n / np.maximum(cnt, 1), 0.0)
    prot = np.zeros(n_t)
    if protect is not None:
        np.maximum.at(prot, reg["tri"], np.clip(protect[reg["ys"], reg["xs"]], 0.0, 1.0))
    straddle = tris & ((pure < PURE_MIN) | (skin > SKIN_MAX))
    at_face = tris & (prot > FACE_MAX)
    sel = tris & ~straddle & ~at_face
    nb = hair.triangle_neighbours(mesh["faces"], mesh["verts"])
    # holes: a hair triangle inside the hair (every neighbour is hair, most of them moving) that a few light texels
    # labelled it skin moves too, so no speck of the body material is left in the middle of the hair
    filled = 0
    for _ in range(3):
        add = np.zeros_like(sel)
        for t in np.nonzero(~sel & ~at_face)[0]:
            n = nb[t]
            if not len(n):
                continue
            # a hair triangle among hair; or any triangle the moving hair closes round (a hole in the found mask)
            if (tris[t] and tris[n].all() and sel[n].mean() >= 0.6) or (not tris[t] and sel[n].all()):
                add[t] = True
            # a hair-coloured flake the hair pass left out of its mask, lying on the moving hair (tri_label: the
            # hair pass's colour group per triangle)
            elif (not tris[t] and tri_label is not None and int(tri_label[t]) == hair_i and sel[n].sum() >= 2
                  and not (tri_label[n] == skin_i).any()):
                add[t] = True
        sel |= add
        filled += int(add.sum())
    small = 0
    for comp in _components(sel, nb):
        if len(comp) < MIN_PIECE:
            sel[comp] = False
            small += len(comp)
    v, f = mesh["verts"], mesh["faces"]
    area = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    hair_area = float(area[tris].sum())
    share = float(area[sel].sum() / max(hair_area, 1e-12))
    # how much of the hair triangles' surface really is the hair's colour: a scarf or collar grown into the hair lowers it
    colour = np.where(cnt > 0, colour_n / np.maximum(cnt, 1), 1.0)
    # (the triangles the hairline runs across have skin in them by definition: they are left out of this measure)
    inner = tris & (skin <= SKIN_MAX)
    clean = float(np.average(colour[inner], weights=area[inner])) if inner.any() else 0.0
    conf = (1.0 if find_confidence is None else float(find_confidence)) * min(1.0, clean / 0.95)
    measures = {"hair_triangles": int(tris.sum()), "material_triangles": int(sel.sum()), "straddling": int(straddle.sum()),
                "at_face": int((at_face & ~straddle).sum()), "small_pieces": small, "holes_filled": filled, "material_share": round(share, 3),
                "hair_colour_share": round(clean, 3), "find_confidence": find_confidence, "confidence": round(conf, 3)}
    reason = None
    if int(sel.sum()) < MIN_TRIS or share < MIN_SHARE:
        reason = "too_little"
    elif conf < CONFIDENCE_MIN:
        reason = "doubtful"
    return {"tris": sel, "use": reason is None, "reason": reason, "confidence": round(conf, 3), "measures": measures,
            "ring": tris & ~sel}


# --- 3. the hair's own texture ----------------------------------------------------------------------------------------


def read_layout(path: Path | str) -> dict[str, Any]:
    """hair_layout.json (blender/hairmat.py) -> {tex, centres (k,3), corners (k,3,3), uv (k,3,2)} in glTF coordinates
    (Y up)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = np.asarray(doc["faces"], dtype=np.float64).reshape(-1, 18)

    def gl(a: np.ndarray) -> np.ndarray:  # Blender (x, y, z), Z up -> glTF (x, z, -y)
        return np.stack([a[..., 0], a[..., 2], -a[..., 1]], axis=-1)

    return {"tex": int(doc.get("tex") or 1024), "centres": gl(rows[:, 0:3]), "corners": gl(rows[:, 3:12].reshape(-1, 3, 3)),
            "uv": rows[:, 12:18].reshape(-1, 3, 2)}


def submesh(layout: dict[str, Any]) -> dict[str, np.ndarray]:
    """The hair triangles as a mesh of their own with the hair texture's coordinates (corners not shared between
    triangles, as the texture pieces are not; hair.triangle_neighbours welds by position)."""
    verts = layout["corners"].reshape(-1, 3)
    faces = np.arange(len(verts), dtype=np.int64).reshape(-1, 3)
    return {"verts": verts, "faces": faces, "uv": layout["uv"].reshape(-1, 2), "vnormals": face.smooth_normals(verts, faces)}


def texels_per_cm(mesh: dict[str, np.ndarray], T: int) -> float:
    return hair.texels_per_cm(mesh, np.ones(len(mesh["faces"]), dtype=bool), T)


def flow_map(mesh: dict[str, np.ndarray], T: int, ys: np.ndarray, xs: np.ndarray, flow: np.ndarray,
             shift: np.ndarray | None = None) -> np.ndarray:
    """(T,T,4) uint8: the strand direction at each hair texel in the texture's own directions. R: along increasing u
    (the texture's columns), G: along increasing v (up the image, as OpenGL, Blender and Unity normal maps), each
    0.5 + 0.5 x; B: 1 (full strength; the anisotropy strength of KHR_materials_anisotropy and Godot); A: a strand
    noise for the highlight's shift (0.5 none). Texels next to the hair take the nearest hair texel's value, so
    filtering at the pieces' edges reads a direction, not grey."""
    reg = hair.texel_region(mesh, T, np.ones(len(mesh["faces"]), dtype=bool))
    idx = -np.ones((T, T), dtype=np.int64)
    idx[reg["ys"], reg["xs"]] = np.arange(len(reg["ys"]))
    k = idx[ys, xs]
    ok = k >= 0
    n = reg["nrm"][k[ok]]
    tu = hair.tangent(reg["tu"][k[ok]], n)
    tv = reg["tv"][k[ok]] - (reg["tv"][k[ok]] * n).sum(axis=1, keepdims=True) * n - (reg["tv"][k[ok]] * tu).sum(axis=1, keepdims=True) * tu
    tv /= np.maximum(np.linalg.norm(tv, axis=1, keepdims=True), 1e-12)
    fl = flow[ok]
    d = np.stack([(fl * tu).sum(axis=1), (fl * tv).sum(axis=1)], axis=1)
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
    img = np.zeros((T, T, 4), dtype=np.float64)
    img[..., 0:2] = 0.5
    img[..., 2] = 1.0
    img[..., 3] = 0.5
    yy, xx = ys[ok], xs[ok]
    img[yy, xx, 0] = 0.5 + 0.5 * d[:, 0]
    img[yy, xx, 1] = 0.5 + 0.5 * d[:, 1]
    if shift is not None:
        img[yy, xx, 3] = np.clip(0.5 + 0.5 * np.asarray(shift)[ok], 0.0, 1.0)
    written = np.zeros((T, T), dtype=bool)
    written[yy, xx] = True
    img = hair.spill(img, written, np.zeros((T, T), dtype=bool), px=6)
    return (np.clip(img, 0, 1) * 255).round().astype(np.uint8)


def gl_flow(rgba: np.ndarray) -> np.ndarray:
    """The flow map for KHR_materials_anisotropy (glTF): its texture's v runs down the image, so the second
    direction component is the other way round. RGB only (strength in blue)."""
    out = rgba[..., :3].copy()
    out[..., 1] = 255 - out[..., 1]
    return out


def rotate_flow(rgba: np.ndarray) -> np.ndarray:
    """The direction turned a quarter turn in the texture plane (x, y) -> (-y, x)."""
    out = rgba.copy()
    x = rgba[..., 0].astype(np.int32)
    y = rgba[..., 1].astype(np.int32)
    out[..., 0] = np.clip(255 - y, 0, 255).astype(np.uint8)
    out[..., 1] = x.astype(np.uint8)
    return out


def paint_atlas(layout_json: Path, baked: dict[str, Path], out_dir: Path, centres: np.ndarray, hair_i: int, skin_i: int, *,
                strength: float, seed: int, look: dict[str, Any], flow_pictures: list[dict[str, Any]] | None = None,
                stats: dict[str, Any] | None = None, view_mesh: dict[str, np.ndarray] | None = None,
                fields: dict[str, Any] | None = None, strand_cell: float | None = None) -> dict[str, Any]:
    """The hair pass's paint again, on the hair's own texture (hair.paint_hair on the hair triangles alone, with the
    texture's coordinates), and the flow map from the same flow. Writes hair_<key>.png and hair_flow.png into out_dir.
    -> {files: {hair_base_color, hair_normal, hair_roughness, hair_ao, hair_flow}, measures, sub (the mesh),
    before: {maps as baked}, after: {maps as painted}, layout}. fields: the body texture's paint's 3D fields (flow,
    under-layers, roots, tips over the whole hair), so this texture is as deep as that one; strand_cell: the strands'
    width, the same on both textures."""
    out_dir = Path(out_dir)
    layout = read_layout(layout_json)
    T = layout["tex"]
    sub = submesh(layout)
    files: dict[str, Path] = {}
    before: dict[str, np.ndarray] = {}
    for key, grey in (("base_color", False), ("normal", False), ("roughness", True), ("ao", True)):
        p = resize_map(baked.get(key), T, out_dir / f"hair_{key}.png", grey)
        if p is None:
            continue
        files[f"hair_{key}"] = p
        im = Image.open(p)
        before[key] = np.asarray(im, dtype=np.float64) / 255.0
    if not look.get("normal_map", True):
        before.pop("normal", None)
        files.pop("hair_normal", None)
    res = hair.paint_hair(sub, {**before, "metallic": None}, np.ones(len(sub["faces"]), dtype=bool), centres, hair_i, skin_i,
                          strength=strength, seed=seed, highlight=bool(look.get("highlight")), photoreal=bool(look.get("photoreal")),
                          protect=None, flow_pictures=flow_pictures, stats=stats, view_mesh=view_mesh, fields=fields,
                          strand_cell=strand_cell)
    after = {**before, **res["maps"]}
    for key, arr in res["maps"].items():
        dest = out_dir / f"hair_{key}.png"
        if arr.ndim == 2:
            Image.fromarray((np.clip(arr, 0, 1) * 255).round().astype(np.uint8), "L").save(dest)
        else:
            face.save_rgb(arr, dest)
        files[f"hair_{key}"] = dest
    if "flow" in res:
        # the strand noise for the highlight's shift: the painted lightness swing, so the shine breaks up along strands
        lum = face.luminance(after["base_color"][res["ys"], res["xs"]])
        shift = np.clip((lum - lum.mean()) / max(float(lum.std()), 1e-6) * 0.4, -1.0, 1.0)
        fm = flow_map(sub, T, res["ys"], res["xs"], res["flow"], shift)
        Image.fromarray(fm, "RGBA").save(out_dir / "hair_flow.png")
        files["hair_flow"] = out_dir / "hair_flow.png"
    measures = {**res["measures"], "tex": T, "texels_per_cm": texels_per_cm(sub, T), "triangles": int(len(sub["faces"]))}
    return {"files": files, "measures": measures, "sub": sub, "before": before, "after": after, "layout": layout}


def _layout_index(mesh: dict[str, np.ndarray], tris: np.ndarray, layout: dict[str, Any], tol: float = 2e-4) -> np.ndarray:
    """For each triangle index in `tris`, the layout row with the same centre (-1 when none)."""
    c = mesh["verts"][mesh["faces"][tris]].mean(axis=1)
    lc = layout["centres"]
    cell = 4 * tol
    keys: dict[tuple[int, int, int], list[int]] = {}
    for i, k in enumerate(map(tuple, np.floor(lc / cell).astype(np.int64).tolist())):
        keys.setdefault(k, []).append(i)
    out = -np.ones(len(tris), dtype=np.int64)
    offs = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for j, k in enumerate(np.floor(c / cell).astype(np.int64).tolist()):
        best, bd = -1, tol
        for dx, dy, dz in offs:
            for i in keys.get((k[0] + dx, k[1] + dy, k[2] + dz), ()):
                d = float(np.linalg.norm(lc[i] - c[j]))
                if d < bd:
                    best, bd = i, d
        out[j] = best
    return out


def into_body(mesh: dict[str, np.ndarray], body: dict[str, np.ndarray], material_tris: np.ndarray, layout: dict[str, Any],
              hair_maps: dict[str, np.ndarray], hair_tpc: float, body_tpc: float) -> dict[str, np.ndarray]:
    """The hair texture's paint carried into the body texture on the triangles that have the hair material, so the
    model with one material (the clean-up and rig previews, and any reader that ignores the second material) shows
    the same hair: each body texel reads the hair texture at the same surface point, from a copy shrunk to the body
    texture's density first (no strand flicker). Normals are carried through 3D from the hair texture's own tangent
    frame to the body texture's. Metal is 0 there. -> the changed body maps (base_color, roughness, metallic, normal)."""
    T = body["base_color"].shape[0]
    idx = np.nonzero(np.asarray(material_tris, dtype=bool))[0]
    rows = _layout_index(mesh, idx, layout)
    ok = rows >= 0
    idx, rows = idx[ok], rows[ok]
    if not len(idx):
        return {}
    Th0 = layout["tex"]
    Th = int(max(16, min(Th0, round(Th0 * min(1.0, body_tpc / max(hair_tpc, 1e-6))))))
    small = {k: (face.resize(v, Th, Image.BOX) if v.shape[0] != Th else v) for k, v in hair_maps.items()
             if k in ("base_color", "roughness", "normal") and v is not None}
    ras = face.rasterise(face.uv_pixels(mesh["uv"], T), np.zeros(len(mesh["verts"])), mesh["faces"], T, idx)
    cov = ras["tri"] >= 0
    ys, xs = np.nonzero(cov)
    t = ras["tri"][cov]
    b = ras["bary"][cov]
    # each body triangle's corners in the layout's corner order (matched by position)
    pos_of = {int(tt): k for k, tt in enumerate(idx)}
    kk = np.array([pos_of[int(x)] for x in t])
    lr = rows[kk]
    fv = mesh["verts"][mesh["faces"][t]]  # (n,3,3)
    lv = layout["corners"][lr]  # (n,3,3)
    d = np.linalg.norm(fv[:, :, None, :] - lv[:, None, :, :], axis=-1)  # body corner a vs layout corner c
    perm = d.argmin(axis=2)  # (n,3)
    luv = layout["uv"][lr]  # (n,3,2)
    huv = (b[:, :, None] * np.take_along_axis(luv, perm[:, :, None], axis=1)).sum(axis=1)
    hp = face.uv_pixels(huv, Th)
    out: dict[str, np.ndarray] = {}
    if "base_color" in small:
        nb = body["base_color"].copy()
        nb[ys, xs] = face.sample(small["base_color"], hp[:, 0], hp[:, 1])
        out["base_color"] = nb
    if "roughness" in small and body.get("roughness") is not None:
        r = body["roughness"] if body["roughness"].ndim == 2 else body["roughness"][..., 0]
        rn = r.copy()
        rr = small["roughness"] if small["roughness"].ndim == 2 else small["roughness"][..., 0]
        rn[ys, xs] = face.sample(rr[..., None], hp[:, 0], hp[:, 1])[:, 0]
        out["roughness"] = rn
    if body.get("metallic") is not None:
        mm = body["metallic"] if body["metallic"].ndim == 2 else body["metallic"][..., 0]
        mn = mm.copy()
        mn[ys, xs] = 0.0
        # the gutter round these pieces kept the 3D step's metal (it marked the hair as metal): hair-coloured texels
        # next to them lose it too, so no metal line shows along the pieces' edges
        written = np.zeros((T, T), dtype=bool)
        written[ys, xs] = True
        band = face.dilate_mask(written, 4) & ~written
        by, bx = np.nonzero(band)
        if len(by):
            ref = np.median(face.to_lab(np.clip(body["base_color"][ys, xs], 0, 1)), axis=0)
            lab = face.to_lab(np.clip(body["base_color"][by, bx], 0, 1))
            near = hair.lab_dist(lab, ref) < 20.0
            mn[by[near], bx[near]] = 0.0
        out["metallic"] = mn
    if "normal" in small and body.get("normal") is not None and body["normal"].shape[0] == T:
        sub = submesh(layout)
        tu_h, tv_h = hair.uv_jacobian(sub, Th)
        tu_b, tv_b = hair.uv_jacobian(mesh, T)
        n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)[cov]
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)

        def frame(tu: np.ndarray, tv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            a = hair.tangent(tu, n)
            c = tv - (tv * n).sum(axis=1, keepdims=True) * n - (tv * a).sum(axis=1, keepdims=True) * a
            return a, c / np.maximum(np.linalg.norm(c, axis=1, keepdims=True), 1e-12)

        ah, bh = frame(tu_h[lr], tv_h[lr])
        ab, bb = frame(tu_b[t], tv_b[t])
        ts = hair.decode_normal(face.sample(small["normal"], hp[:, 0], hp[:, 1]))
        world = ah * ts[:, :1] + bh * ts[:, 1:2] + n * ts[:, 2:3]
        loc = np.stack([(world * ab).sum(axis=1), (world * bb).sum(axis=1), (world * n).sum(axis=1)], axis=1)
        loc /= np.maximum(np.linalg.norm(loc, axis=1, keepdims=True), 1e-9)
        nn = body["normal"].copy()
        nn[ys, xs] = hair.encode_normal(loc)
        out["normal"] = nn
    # the gutter round these pieces keeps the baked hair colour (no strands): it is only ever read by filtering
    return out


def resize_map(path: Path | str | None, T: int, dest: Path, grey: bool = False) -> Path | None:
    """A baked map at the hair texture's size (the occlusion map bakes at its source's size)."""
    if path is None or not Path(path).is_file():
        return None
    im = Image.open(path).convert("L" if grey else "RGB")
    if im.size != (T, T):
        im = im.resize((T, T), Image.BOX if im.size[0] > T else Image.BILINEAR)
    im.save(dest)
    return Path(dest)


# --- pictures ----------------------------------------------------------------------------------------------------------


def render_split(mesh: dict[str, np.ndarray], tex: np.ndarray, sub: dict[str, np.ndarray] | None, hair_tex: np.ndarray | None,
                 cam: Any, background: float = 0.25) -> np.ndarray:
    """The model with the hair drawn from its own texture: the whole model with the body texture, and the hair
    triangles over it from the hair texture wherever they are the nearest surface."""
    body = face.render_view(mesh, tex, cam, background)
    img = body["image"].copy()
    if sub is None or hair_tex is None:
        return img
    zb = body["raster"]["z"]
    hv = face.render_view(sub, hair_tex, cam, background)
    zh = hv["raster"]["z"]
    tol = 2.0 * cam.scale / cam.size
    on = hv["mask"] & (zh <= zb + tol)
    img[on] = hv["image"][on]
    return img


def split_sheet(mesh: dict[str, np.ndarray], base: np.ndarray, hair_mask: np.ndarray, split_mask: np.ndarray | None,
                dest: Path, cell: int = 300) -> Path:
    """'What was taken as hair', with the split: from five sides, the hair that has its own material tinted blue and
    the hair that stays in the body material (the hairline, and the hair at the face) tinted green. Without a split,
    as hair.mask_sheet (all green)."""
    from PIL import ImageDraw  # noqa: PLC0415

    if split_mask is None:
        return hair.mask_sheet(mesh, base, hair_mask, dest, cell)
    head = face.find_head(mesh["verts"])
    tint = base.copy()
    green = hair_mask & ~split_mask
    tint[green] = tint[green] * 0.45 + np.array([0.15, 0.85, 0.2]) * 0.55
    tint[split_mask] = tint[split_mask] * 0.45 + np.array([0.2, 0.45, 1.0]) * 0.55
    sheet = Image.new("RGB", (cell * len(hair.SHEET_VIEWS), cell + 48), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for i, (label, yaw, pitch) in enumerate(hair.SHEET_VIEWS):
        img = face.render_view(mesh, tint, hair.head_view_cam(head, yaw, pitch, cell))["image"]
        sheet.paste(Image.fromarray(face.to_uint8(img)).resize((cell, cell), Image.LANCZOS), (i * cell, 24))
        d.text((i * cell + 8, 6), label, fill=(230, 230, 230))
    d.text((8, cell + 30), "Blue: hair with its own material and texture.  Green: hair kept in the body material "
                           "(the hairline and the hair at the face).", fill=(230, 230, 230))
    sheet.save(dest)
    return Path(dest)


def before_after_sheet(mesh: dict[str, np.ndarray], before: dict[str, Any], body_after: dict[str, Any], sub: dict[str, np.ndarray],
                       hair_after: dict[str, Any], dest: Path, cell: int = 300) -> Path:
    """'Hair: before and after' when the hair has its own material: colour from five sides before (the body texture
    as the 3D step and the face pass left it) and after (the hair from its own texture), then lit with the normal
    map from the front-left, from the right and from behind (before, after)."""
    from PIL import ImageDraw  # noqa: PLC0415

    head = face.find_head(mesh["verts"])
    rows: list[tuple[str, list[np.ndarray]]] = []
    rows.append(("Colour before", [face.render_view(mesh, before["base_color"], hair.head_view_cam(head, y, p, cell))["image"]
                                   for _, y, p in hair.SHEET_VIEWS]))
    rows.append(("Colour after (hair from its own texture)",
                 [render_split(mesh, body_after["base_color"], sub, hair_after["base_color"], hair.head_view_cam(head, y, p, cell))
                  for _, y, p in hair.SHEET_VIEWS]))
    lights = (np.array([0.6, 0.8, 0.9]), np.array([-0.8, 0.3, 0.4]))
    views = ((25.0, 10.0, lights[0]), (-35.0, 5.0, lights[1]), (180.0, 10.0, lights[0]))
    rows.append(("Lit before", [hair.render_lit(mesh, before["base_color"], before.get("normal"), before.get("roughness"),
                                                hair.head_view_cam(head, y, p, cell), L) for y, p, L in views]))
    lit_after = []
    for y, p, L in views:
        cam = hair.head_view_cam(head, y, p, cell)
        img = hair.render_lit(mesh, body_after["base_color"], body_after.get("normal"), body_after.get("roughness"), cam, L)
        hi = hair.render_lit(sub, hair_after["base_color"], hair_after.get("normal"), hair_after.get("roughness"), cam, L)
        zb = face.view_raster(mesh, cam)["z"]
        hr = face.view_raster(sub, cam)
        on = (hr["tri"] >= 0) & (hr["z"] <= zb + 2.0 * cam.scale / cam.size)
        img[on] = hi[on]
        lit_after.append(img)
    rows.append(("Lit after", lit_after))
    pad = 22
    sheet = Image.new("RGB", (cell * len(hair.SHEET_VIEWS), (cell + pad) * len(rows)), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for r, (tag, imgs) in enumerate(rows):
        y0 = r * (cell + pad)
        d.text((8, y0 + 4), tag, fill=(230, 230, 230))
        for c, img in enumerate(imgs):
            sheet.paste(Image.fromarray(face.to_uint8(img)).resize((cell, cell), Image.LANCZOS), (c * cell, y0 + pad))
    sheet.save(dest)
    return Path(dest)


# --- 4. the GLB ----------------------------------------------------------------------------------------------------------


def _read_glb(p: Path) -> tuple[dict[str, Any], bytes]:
    data = p.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"not a GLB: {p}")
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    blen = struct.unpack_from("<I", data, bin_off)[0] if len(data) > bin_off else 0
    return gltf, data[bin_off + 8: bin_off + 8 + blen]


def _write_glb(p: Path, gltf: dict[str, Any], bin_data: bytes) -> None:
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    b = bytes(bin_data) + b"\x00" * ((4 - len(bin_data) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(b)
    p.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I", len(js)) + b"JSON" + js
                  + struct.pack("<I", len(b)) + b"BIN\x00" + b)


def glb_materials(glb: Path | str) -> list[str]:
    gltf, _ = _read_glb(Path(glb))
    return [m.get("name", "") for m in gltf.get("materials") or []]


def glb_add_anisotropy(glb: Path | str, flow_png: Path | str, material_suffix: str = "_Hair",
                       strength: float = ANISOTROPY_STRENGTH, rotation: float = 0.0) -> bool:
    """Add KHR_materials_anisotropy to the material whose name ends with material_suffix: anisotropyStrength,
    anisotropyRotation and the flow map (glTF direction convention, gl_flow) as anisotropyTexture on the same
    texture coordinates as its base colour. The image is appended to the binary chunk. False when the GLB has no
    such material. Idempotent: a second call replaces the extension."""
    p = Path(glb)
    gltf, bin_data = _read_glb(p)
    mats = gltf.get("materials") or []
    target = next((m for m in mats if str(m.get("name", "")).endswith(material_suffix)), None)
    if target is None:
        return False
    png = Path(flow_png).read_bytes()
    buf = bytearray(bin_data)
    while len(buf) % 4:
        buf.append(0)
    views = gltf.setdefault("bufferViews", [])
    views.append({"buffer": 0, "byteOffset": len(buf), "byteLength": len(png)})
    buf += png
    images = gltf.setdefault("images", [])
    images.append({"bufferView": len(views) - 1, "mimeType": "image/png", "name": "hair_anisotropy"})
    textures = gltf.setdefault("textures", [])
    tex_entry: dict[str, Any] = {"source": len(images) - 1}
    base_ref = (target.get("pbrMetallicRoughness") or {}).get("baseColorTexture") or {}
    if base_ref.get("index") is not None and textures[base_ref["index"]].get("sampler") is not None:
        tex_entry["sampler"] = textures[base_ref["index"]]["sampler"]
    textures.append(tex_entry)
    ext = {"anisotropyStrength": float(strength), "anisotropyRotation": float(rotation),
           "anisotropyTexture": {"index": len(textures) - 1, **({"texCoord": base_ref["texCoord"]} if base_ref.get("texCoord") else {})}}
    target.setdefault("extensions", {})["KHR_materials_anisotropy"] = ext
    used = gltf.setdefault("extensionsUsed", [])
    if "KHR_materials_anisotropy" not in used:
        used.append("KHR_materials_anisotropy")
    gltf["buffers"][0]["byteLength"] = len(buf)
    _write_glb(p, gltf, bytes(buf))
    return True


# --- the README lines ------------------------------------------------------------------------------------------------------


def readme_lines(name: str, hair_tex: int, flow_file: str, files: dict[str, str]) -> list[str]:
    """Plain lines for the hand-off README: what the hair material is and how to switch it to each engine's hair or
    anisotropy settings by hand. Only what docs/HAIR-RESEARCH.md says those engines do."""
    return [
        "Hair material",
        f"The hair has its own material ({name}_Hair) and its own {hair_tex} px textures: {', '.join(files.values())}.",
        "The rest of the character uses the body material and the usual textures. Two materials cost one more draw call per character.",
        f"{flow_file} is the flow map: the strand direction at each texel of the hair texture. Red is the direction along the texture's "
        "width, green along its height (up, as in OpenGL normal maps), each stored as 0.5 + 0.5 times the direction; blue is 1.",
        "Unity: the importer builds the hair material for you (the VGAC/Hair shader with a stretched highlight on the built-in render "
        "pipeline; URP and HDRP get their own Lit shader with the hair textures).",
        "Unreal: open the hair material, set its Shading Model to Hair, and connect the hair base colour. The FBX carries only the "
        "material slot names, so this is done by hand. Not tried here.",
        "Godot: on the hair material (StandardMaterial3D), turn on Anisotropy and set Flowmap to the flow map. Godot does not read "
        "glTF anisotropy, so this is done by hand. Not tried here.",
        "Other GLB readers: the GLB's hair material carries KHR_materials_anisotropy (the app's own preview uses it); readers that "
        "do not know it show the hair with the ordinary material.",
    ]


def unit_sentence(m: dict[str, Any]) -> str:
    """One sentence for the finish card: the hair's own material in plain words."""
    return (f"The hair has its own material and a {m.get('tex')} px texture: {m.get('texels_per_cm')} texels per cm on the hair "
            f"({m.get('texels_per_cm_body_texture')} in the body texture). It costs one more draw call per character.")


def anisotropy_rotation() -> float:
    """The quarter turn KHR_materials_anisotropy needs: its direction is where the highlight stretches, which for hair
    runs across the strands."""
    return math.pi / 2
