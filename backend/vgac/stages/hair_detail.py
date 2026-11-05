"""Hair detail pass (strands and shading painted on), run at the end of the clean-up step (stages/prep.py) after the
face detail pass. docs/HAIR-RESEARCH.md stage 1 (a); the maths is in vgac/hair.py.

Two calls from prep:
1. find (before the face pass): which triangles are hair, by a vote of the model's own colours and every turnaround
   picture whose head outline lines up with the model's; and how many times its texels (texture pixels) across the
   hair should grow. The face pass then lays the texture out once for both (blender/faceuv.py --hair-points), so the
   face is painted on the final layout and nothing is baked twice. The face keeps its texels; the hair gets what still
   fits while the rest of the body loses at most 12 percent of its texels across.
2. run (after the face pass): when the face pass did not lay the texture out, the hair's own relayout; then the paint
   (hair.paint_hair) on the final maps: streaks along the flow in clumps and strands, darker under-layers and roots,
   lighter tips, a strand height in the normal map, roughness in a hair range and no metal, a painted highlight band
   for painted, stylised, cel and clay looks (not Photoreal), a softened hairline on the hair side. The face's own
   texels (eyes, brows, nose, mouth) are never written, and its measures are taken before and after.

Skipped, with the reason recorded and said in plain words, when the option is off, the strength is 0, for categories
other than humanoid, for crisp and flat looks (as the face pass), and when the hair cannot be told from skin or
clothes with confidence (hair.REASONS). Metrics under "hair_detail"; version files "hair_mask" (what was taken as hair)
and "hair_before_after".
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import zlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import face, hair, imaging, quality
from .base import StageContext

CATEGORIES = ("humanoid",)  # creatures: see docs/HAIR-RESEARCH.md; off until fur is judged better than the plain coat
DEFAULT_STRENGTH = 1.0
MAX_LOSS = 0.12  # the rest of the body's texels across may fall by at most this much (face and hair together)
FACE_SHARP_DROP_MAX = 0.03  # the face's sharpness may fall by this share at most
FACE_EYES_MOVE_MAX = 1.5  # pixels on the 1024 front render
PICTURE_KEYS = (("front", "cutout", ("concept",)), ("front_turn", "front_cutout", ("turnaround",)),
                ("quarter", "quarter_cutout", ("turnaround",)), ("left", "left_cutout", ("turnaround",)),
                ("right", "right_cutout", ("turnaround",)), ("back", "back_cutout", ("turnaround",)))
# Not something to warn about: the pass does not apply.
NOT_APPLICABLE = ("off", "not_category", "style", "strength_zero", "no_maps")
METHOD = ("hair found by a vote of the model's colours and the turnaround pictures; strands and clumps painted along "
          "the hair's flow from 3D positions (line integral convolution), shading from the occlusion map and the hair's "
          "edges, a strand height in the normal map")


def skip_reason(category_id: str, rec: dict[str, Any] | None, params: dict[str, Any]) -> str | None:
    """Why the pass does not run for this version, or None when it should run."""
    if params.get("hair_detail") is False:
        return "off"
    if category_id not in CATEGORIES:
        return "not_category"
    srf = (rec or {}).get("surface") or {}
    mesh = (rec or {}).get("mesh") or {}
    if srf.get("texture_filter") == "crisp" or srf.get("shading") == "flat" or (mesh.get("texture") or 2048) < 512:
        return "style"
    if strength_of(params) <= 0.0:
        return "strength_zero"
    return None


def strength_of(params: dict[str, Any]) -> float:
    try:
        s = float(params.get("hair_strength", DEFAULT_STRENGTH))
    except (TypeError, ValueError):
        s = DEFAULT_STRENGTH
    return min(1.0, max(0.0, s))


def on(params: dict[str, Any]) -> bool:
    return params.get("hair_detail") is not False


def look_of(style: dict[str, Any] | None, rec: dict[str, Any] | None) -> dict[str, Any]:
    """{photoreal, highlight, normal_map}: Photoreal (the style removes painted light: delight_default remove) gets no
    painted highlight band; every other look that reaches the pass gets one."""
    st = style or {}
    photoreal = st.get("delight_default") == "remove" or (rec or {}).get("style") == "realistic_pbr"
    mesh = (rec or {}).get("mesh") or {}
    return {"photoreal": bool(photoreal), "highlight": not photoreal, "normal_map": bool(mesh.get("normal_map", True))}


def seed_of(params: dict[str, Any], asset_id: str) -> int:
    """The pattern's seed: the version's hair_seed, else one fixed by the asset (the same asset gets the same strands
    on every run)."""
    s = params.get("hair_seed")
    if s is not None:
        try:
            return int(s)
        except (TypeError, ValueError):
            pass
    return zlib.crc32(str(asset_id).encode()) & 0x7FFFFFFF


def pictures(sc: StageContext) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for name, key, stages in PICTURE_KEYS:
        p = sc.lineage_file(key, stages)
        if p is not None and Path(p).is_file():
            out[name] = Path(p)
    if "front" not in out and "front_turn" in out:
        out["front"] = out["front_turn"]
    out.pop("front_turn", None)
    return out


# --- checks -----------------------------------------------------------------------------------------------------


def hair_checks(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """hair_matches_picture (warn), from the "hair_detail" metrics block. Nothing when the pass does not apply (off,
    strength 0, not a humanoid, a crisp or flat look). When the hair could not be told apart from the skin or the
    clothes, a passing check that says why in plain words (nothing is wrong with the model; the pass stood back).
    A soft failure when something went wrong, or when the face's own measures changed."""
    hd = (metrics or {}).get("hair_detail") or {}
    if not hd:
        return []
    if not hd.get("ran"):
        reason = hd.get("skipped")
        if reason in NOT_APPLICABLE:
            return []
        ok = reason != "error"
        return [quality.check("hair_matches_picture", ok, reason, None,
                              vars={"reason": reason, "skipped": True, "why": hair.reason_words(reason)})]
    fb, fa = hd.get("face_before") or {}, hd.get("face_after") or {}
    face_ok, moved = True, None
    if fb.get("sharpness") and fa.get("sharpness") is not None:
        face_ok = float(fa["sharpness"]) >= (1 - FACE_SHARP_DROP_MAX) * float(fb["sharpness"])
    if fb.get("eyes") and fa.get("eyes"):
        moved = max(float(np.hypot(a[0] - b[0], a[1] - b[1])) for a, b in zip(fa["eyes"], fb["eyes"]))
        face_ok = face_ok and moved <= FACE_EYES_MOVE_MAX
    lay = hd.get("layout") or {}
    return [quality.check("hair_matches_picture", face_ok, hd.get("texels_per_cm_after"), hd.get("texels_per_cm_before"),
                          vars={"reason": None if face_ok else "face_changed", "skipped": False,
                                "before": _one(hd.get("texels_per_cm_before")), "after": _one(hd.get("texels_per_cm_after")),
                                "asked": hd.get("gain_asked"), "gain": lay.get("hair_gain"),
                                "room_note": room_note(hd), "highlight": bool(hd.get("highlight")),
                                "from_picture": round(100 * float(hd.get("flow_from_picture") or 0.0)),
                                "share": round(100 * float(hd.get("mask_share_of_head") or 0.0))})]


def _one(v: Any) -> Any:
    return None if v is None else round(float(v), 1)


def room_note(hd: dict[str, Any]) -> str:
    """The plain sentence when the hair got fewer texels than it asked for."""
    asked = float(hd.get("gain_asked") or 1.0)
    lay = hd.get("layout") or {}
    got = float(lay.get("hair_gain") or 1.0)
    if asked <= 1.0 or got >= 0.92 * asked:
        return ""
    budget = hd.get("texture_size") or 2048
    if got <= 1.05:
        return (f"There was no room in the {budget} px texture to give the hair more texels without the rest of the body "
                f"losing more than {round(100 * MAX_LOSS)} percent of its sharpness, so the strands are painted at the "
                "texels it had.")
    return (f"The hair asked for {asked:.1f} times its texels across and got {got:.1f} times: more would have cost the "
            f"rest of the body more than {round(100 * MAX_LOSS)} percent of its sharpness.")


# --- the pass ------------------------------------------------------------------------------------------------------


async def find(sc: StageContext, glb: Path, maps: dict[str, Path], rec: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Step 1, before the face pass. Returns {plan: {points (Path), gain} or None, record (metrics so far), found}."""
    t0 = time.time()
    reason = skip_reason(sc.asset.category, rec, params)
    if reason:
        return {"plan": None, "record": {"ran": False, "skipped": reason}, "t0": t0}
    base = maps.get("base_color")
    if base is None:
        return {"plan": None, "record": {"ran": False, "skipped": "no_maps"}, "t0": t0}
    out_dir = sc.dir / "hair"
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = seed_of(params, sc.asset.id)
    sc.progress(0.72, "Finding the hair")
    try:
        found = await asyncio.to_thread(hair.find_hair, glb, Path(base), maps.get("metallic"), pictures(sc), seed)
    except Exception as e:  # noqa: BLE001  (never fails the clean-up step)
        return {"plan": None, "record": {"ran": False, "skipped": "error", "error": str(e)[:300]}, "t0": t0}
    measures = {k: v for k, v in found.items() if k not in ("tris", "centres", "tri_label", "found", "reason", "hair_i", "skin_i")}
    if not found.get("found"):
        files: dict[str, Path] = {}
        if found.get("tris") is not None and np.asarray(found["tris"]).any():
            # the picture of what would have been taken as hair, so the reason can be seen
            try:
                files["hair_mask"] = await asyncio.to_thread(skipped_mask, glb, Path(base), found["tris"], out_dir / "hair_mask.png")
            except Exception:  # noqa: BLE001
                pass
        return {"plan": None, "files": files, "record": {"ran": False, "skipped": found.get("reason") or "error", "find": measures,
                                                         "find_seconds": round(time.time() - t0, 1)}, "t0": t0}
    mesh = face.load_mesh(glb)
    T = int(imaging.image_size(base)[0])
    tpc = hair.texels_per_cm(mesh, found["tris"], T)
    gain = hair.hair_gain(tpc, T)
    pts = out_dir / "hair_points.json"
    pts.write_text(json.dumps(hair.hair_points(mesh, found["tris"])), encoding="utf-8")
    (out_dir / "hair_find.json").write_text(json.dumps({"centres": np.asarray(found["centres"]).round(3).tolist(),
                                                         "hair_i": found["hair_i"], "skin_i": found["skin_i"]}), encoding="utf-8")
    record = {"ran": False, "find": measures, "confidence": found.get("confidence"), "texels_per_cm_before": tpc,
              "atlas_share_before": hair.atlas_share(mesh, found["tris"]), "gain_asked": gain, "texture_size": T,
              "body_texels_per_cm_before": hair.texels_per_cm(mesh, ~found["tris"], T), "seed": seed,
              "find_seconds": round(time.time() - t0, 1)}
    return {"plan": {"points": pts, "gain": gain}, "record": record, "found": found, "t0": t0}


def skipped_mask(glb: Path, base: Path, tris: np.ndarray, dest: Path) -> Path:
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base)
    return hair.mask_sheet(mesh, tex, hair.tri_mask(mesh, np.asarray(tris), tex.shape[0]), dest)


async def relayout(sc: StageContext, glb: Path, maps: dict[str, Path], plan: dict[str, Any], tex: int) -> dict[str, Any] | None:
    """The hair's own relayout, when the face pass did not lay the texture out: blender/faceuv.py with no face points.
    Returns the report (rebake_maps, hair_gain, rest_scale, ...) or None when nothing changed."""
    out_dir = sc.dir / "hair"
    (out_dir / "no_face_points.json").write_text("[]", encoding="utf-8")
    (out_dir / "hair_maps.json").write_text(json.dumps({k: str(p) for k, p in maps.items()}), encoding="utf-8")
    new_glb = out_dir / "hair.glb"
    await sc.blender("faceuv.py", [glb, new_glb, "--points", out_dir / "no_face_points.json", "--maps", out_dir / "hair_maps.json",
                                   "--gain", 1.0, "--tex", tex, "--padding", imaging.unwrap_padding(tex),
                                   "--margin", imaging.dilation_px(tex), "--max-loss", MAX_LOSS,
                                   "--hair-points", plan["points"], "--hair-gain", plan["gain"]], log_name="hairuv.log")
    rep_path = out_dir / "hair_report.json"
    rep = json.loads(rep_path.read_text()) if rep_path.is_file() else None
    if not rep or rep.get("hair_mode") != "repack" or not new_glb.is_file():
        return {**(rep or {}), "applied": False}
    return {**rep, "applied": True, "glb_path": str(new_glb)}


def _read_map(path: Path | None, grey: bool = False) -> np.ndarray | None:
    if path is None or not Path(path).is_file():
        return None
    im = Image.open(path)
    if grey:
        return np.asarray(im.convert("L"), dtype=np.float64) / 255.0
    return np.asarray(im.convert("RGB"), dtype=np.float64) / 255.0


def _write(arr: np.ndarray, dest: Path) -> Path:
    if arr.ndim == 2:
        Image.fromarray((np.clip(arr, 0, 1) * 255).round().astype(np.uint8), "L").save(dest)
    else:
        face.save_rgb(arr, dest)
    return dest


def paint(glb: Path, maps: dict[str, Path], points: Path, find_json: Path, out_dir: Path, strength: float, seed: int,
          look: dict[str, Any], flow_files: list[Path], cutout: Path | None) -> dict[str, Any]:
    """The CPU part of step 2 on the final layout: paint, measure, draw the pictures. Writes the changed maps and the
    two pictures into out_dir. Returns {files: {key: path}, measures}."""
    mesh = face.load_mesh(glb)
    pts = json.loads(Path(points).read_text())
    fj = json.loads(Path(find_json).read_text())
    centres = np.asarray(fj["centres"], dtype=np.float64)
    tris = hair.match_tris(mesh, pts)
    base = _read_map(maps.get("base_color"))
    arrays: dict[str, np.ndarray | None] = {"base_color": base, "normal": _read_map(maps.get("normal")) if look.get("normal_map", True) else None,
                                            "roughness": _read_map(maps.get("roughness"), True), "metallic": _read_map(maps.get("metallic"), True),
                                            "ao": _read_map(maps.get("ao"), True)}
    fp = hair.face_protect(mesh, base)
    flow_pics: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    head = face.find_head(mesh["verts"])
    if head is not None:
        cam = face.head_camera(head, 0, 1024)
        srcs: list[tuple[str, np.ndarray]] = []
        for f in flow_files:
            if Path(f).is_file():
                srcs.append((Path(f).stem, face.read_rgb(f, 1024)))
        if cutout is not None and Path(cutout).is_file():
            rgba = np.asarray(Image.open(cutout).convert("RGBA"), dtype=np.float64) / 255.0
            crop = face.painted_head(rgba, head, cam)
            if crop is not None:
                srcs.append(("painting", crop["image"]))
        for name, pic in srcs:
            lbl, _ = hair.nearest_centre(face.to_lab(np.clip(pic, 0, 1)), centres)
            flow_pics.append({"name": name, "picture": pic, "hair_px": lbl == int(fj["hair_i"]), "cam": cam})
        if srcs:
            last = flow_pics[-1]
            stats = hair.picture_stats(last["picture"], last["hair_px"])
    T = base.shape[0]
    same = {k: v for k, v in arrays.items() if v is not None and v.shape[0] == T}
    res = hair.paint_hair(mesh, same, tris, centres, int(fj["hair_i"]), int(fj["skin_i"]), strength=strength, seed=seed,
                          highlight=bool(look.get("highlight")), photoreal=bool(look.get("photoreal")), protect=fp["protect"],
                          flow_pictures=flow_pics, stats=stats)
    changed = dict(res["maps"])
    # maps at another size (a 4096 base colour with 2048 data maps): painted again at their own size
    others = {k: v for k, v in arrays.items() if v is not None and v.shape[0] != T and k in ("normal", "roughness", "metallic")}
    if others:
        T2 = next(iter(others.values())).shape[0]
        small = {**others, "base_color": face.resize(base, T2, Image.BOX), "ao": arrays.get("ao")}
        fp2 = hair.face_protect(mesh, small["base_color"])
        r2 = hair.paint_hair(mesh, small, tris, centres, int(fj["hair_i"]), int(fj["skin_i"]), strength=strength, seed=seed,
                             highlight=False, photoreal=bool(look.get("photoreal")), protect=fp2["protect"], flow_pictures=flow_pics, stats=stats)
        changed.update({k: v for k, v in r2["maps"].items() if k != "base_color"})
    files: dict[str, Path] = {}
    for key, arr in changed.items():
        files[key] = _write(arr, out_dir / f"{key}.png")
    after_base = changed.get("base_color", base)
    after_view = face.render_view(mesh, after_base, fp["cam"]) if fp.get("cam") is not None else None
    face_after = hair.face_measures(after_view, fp["info"]) if after_view is not None else {}
    mask = res["mask"]
    files["hair_mask"] = hair.mask_sheet(mesh, base, mask, out_dir / "hair_mask.png")
    before_maps = {k: v for k, v in arrays.items() if v is not None}
    after_maps = {**before_maps, **changed}
    files["hair_before_after"] = hair.before_after_sheet(mesh, before_maps, after_maps, out_dir / "hair_before_after.png")
    measures = {**res["measures"], "face_before": fp.get("measures") or {}, "face_after": face_after,
                "triangles_matched": int(tris.sum()), "texels_per_cm_after": hair.texels_per_cm(mesh, tris, T),
                "atlas_share_after": hair.atlas_share(mesh, tris), "picture_stats": stats}
    head_tris = mesh["verts"][mesh["faces"]].mean(axis=1)[:, 1] >= (head.top - 0.13 * head.height if head else -1e9)
    measures["mask_share_of_head"] = round(float(tris[head_tris].mean()), 3) if head_tris.any() else None
    return {"files": files, "measures": measures}


async def run(sc: StageContext, glb: Path, maps: dict[str, Path], rec: dict[str, Any], params: dict[str, Any],
              found: dict[str, Any], face_layout: dict[str, Any] | None) -> dict[str, Any]:
    """Step 2 on the prepared model `glb` (changed in place) with the maps after the face pass. found: find()'s result;
    face_layout: the face pass's relayout report when it laid the texture out (with the hair in it). Returns {files,
    metrics: {"hair_detail": {...}}, relayout: bool}. Never raises for a reason that should not fail the clean-up."""
    t0 = found.get("t0") or time.time()
    t1 = time.time()  # the hair's own time: find_seconds plus this step's (the face pass runs in between)
    record = dict(found.get("record") or {})
    plan = found.get("plan")
    if plan is None:
        record.setdefault("ran", False)
        record["seconds"] = round(float(record.get("find_seconds") or 0.0), 1)
        return {"files": dict(found.get("files") or {}), "metrics": {"hair_detail": record}, "relayout": False}
    out_dir = sc.dir / "hair"
    strength = strength_of(params)
    look = look_of(sc.style, rec)
    record.update(strength=strength, highlight=look["highlight"], photoreal=look["photoreal"], normal_map=look["normal_map"])
    work_glb, work_maps, did_relayout = Path(glb), dict(maps), False
    layout: dict[str, Any] | None = None
    try:
        if face_layout and face_layout.get("hair_mode"):
            layout = {k: face_layout.get(k) for k in ("hair_mode", "hair_gain", "hair_island_scale", "rest_scale", "gain",
                                                       "hair_atlas_share_before", "hair_atlas_share_after", "hair_tries")}
            layout["shared_with_face"] = True
        elif float(plan["gain"]) > 1.0:
            sc.progress(0.9, "Giving the hair more of the texture")
            tex = int(record.get("texture_size") or imaging.image_size(maps["base_color"])[0])
            rep = await relayout(sc, Path(glb), {k: v for k, v in maps.items() if k in ("base_color", "normal", "metallic", "roughness", "ao")}, plan, tex)
            layout = {k: (rep or {}).get(k) for k in ("hair_mode", "hair_gain", "hair_island_scale", "rest_scale",
                                                       "hair_atlas_share_before", "hair_atlas_share_after", "hair_tries", "rebake_seconds")}
            layout["shared_with_face"] = False
            if rep and rep.get("applied"):
                work_glb = Path(rep["glb_path"])
                work_maps = {k: Path(p) for k, p in (rep.get("rebake_maps") or {}).items() if Path(p).is_file()}
                did_relayout = True
        sc.progress(0.93, "Painting the hair")
        flow_files = [sc.dir / "face" / "painted_head_closeup_merged.png"]  # only when the face pass checked and used it
        cutout = sc.lineage_file("cutout", ("concept",))
        done = await asyncio.to_thread(paint, work_glb, work_maps, plan["points"], out_dir / "hair_find.json", out_dir, strength,
                                       int(record.get("seed") or 0), look, flow_files, cutout)
    except Exception as e:  # noqa: BLE001
        record.update(ran=False, skipped="error", error=str(e)[:300], seconds=round(time.time() - t0, 1))
        return {"files": {}, "metrics": {"hair_detail": record}, "relayout": False}
    files: dict[str, Path] = {}
    if did_relayout:
        files.update({k: p for k, p in work_maps.items()})
        shutil.copy2(work_glb, glb)
    files.update(done["files"])
    swap = {k: done["files"].get(k) for k in ("base_color", "metallic", "roughness")}
    if did_relayout and swap["base_color"] is None:
        swap["base_color"] = work_maps.get("base_color")
    await asyncio.to_thread(imaging.glb_replace_images, glb, base_color=swap["base_color"], metallic=swap["metallic"],
                            roughness=swap["roughness"])
    if done["files"].get("normal") is not None:
        await asyncio.to_thread(hair.glb_replace_normal, glb, done["files"]["normal"])
    record.update(ran=True, method=METHOD, layout=layout, **done["measures"], paint_seconds=round(time.time() - t1, 1),
                  seconds=round(float(record.get("find_seconds") or 0.0) + time.time() - t1, 1))
    return {"files": files, "metrics": {"hair_detail": record}, "relayout": did_relayout}
