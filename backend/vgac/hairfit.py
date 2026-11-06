"""Hair from the library: rank the library's hairstyles on a bald 3D model, and attach the chosen one to the final body
(docs/HAIR-LIBRARY-PLAN.md sections 5 and 6). The geometry runs in Blender (blender/hairfit.py, modes rank and attach);
this module writes the job, runs it through the step's StageContext (sc.blender: the configured Blender, the job's
cancel and process registration, a timeout), and turns the results into the step's files and metrics.

    rank(sc, glb, picture_json, bald_cutout, outline, family, yaw, out_dir) -> metrics.hair_library block
        Files in out_dir: hair_candidates.json and hair_cand_1.png .. hair_cand_4.png (front and three-quarter side by
        side). Capped at RANK_SECONDS; never raises (errors go to the block's `error`).
    attach(sc, body_glb, style_id, params, picture_json, bald_cutout, outline, cover, tex, out_dir)
        -> {files: {hair_mesh, hair_parts, hair_glb, with_hair_glb, hair_base_color, hair_normal, hair_roughness,
                    hair_flow, hair_fit_sheet}, metrics: {...}}
    check_values(m) -> {hair_fit: (value, limit, vars), hair_skin_through: ..., hair_triangles: ...}
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import hairstyles

RANK_SECONDS = 150
TIED = ("ponytail", "bun", "braid")      # scored on the head only: the tail or braid hangs behind the head in a front picture
RANK_TOP = 4
FIT_MIN = 0.6
SKIN_MAX = 0.02
ROUGHNESS = 0.55
MODEL_ID = "hair_library"


def _picture(picture_json: Path | str) -> dict[str, Any]:
    return json.loads(Path(picture_json).read_text(encoding="utf-8"))


def _style_job(e: dict[str, Any], platform: str | None) -> dict[str, Any]:
    p = hairstyles.paths(e["id"])
    npz = p["npz"]
    if platform in hairstyles.PHONE_PLATFORMS and p.get("lite") is not None:
        npz = p["lite"]
    return dict(id=e["id"], npz=str(npz), grey=str(p["grey"]), normal=str(p["normal"]) if p.get("normal") else None,
                alpha=e["alpha"], two_sided=bool(e["two_sided"]), hang_groups=e.get("hang_groups") or [], front=30.0)


def _look(sc: Any) -> str | None:
    return str(getattr(getattr(sc, "asset", None), "style_preset", "") or "") or None


def _sheet(pngs: list[Path], dest: Path, size: int = 384) -> Path | None:
    ims = [Image.open(p).convert("RGB").resize((size, size)) for p in pngs if p and Path(p).is_file()]
    if not ims:
        return None
    out = Image.new("RGB", (size * len(ims), size), "white")
    for i, im in enumerate(ims):
        out.paste(im, (i * size, 0))
    out.save(dest)
    return dest


def fit_words(score: float | None) -> str:
    if score is None:
        return "not measured"
    if score >= 0.75:
        return "close to the picture"
    if score >= FIT_MIN:
        return "a fair match"
    return "not like the picture"


async def rank(sc: Any, glb: Path, picture_json: Path, bald_cutout: Path, outline: Path, family: str | None, yaw: int,
               out_dir: Path) -> dict[str, Any]:
    """Every offered style of the family (all styles when the description names none) fitted to the bald model and
    scored on the front outline; the best RANK_TOP are built, recoloured and rendered. Never raises."""
    t0 = time.time()
    out_dir = Path(out_dir)
    block: dict[str, Any] = dict(family=family, family_source="description" if family else "none", candidates=[], best=None,
                                 best_score=None, nothing_fits=True, seconds=None, error=None)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        platform = getattr(sc, "platform", None)
        offered = hairstyles.styles_for(family, platform, _look(sc))
        if family and not offered:
            block["note"] = f"the library has no {family} styles here; every style was tried"
            offered = hairstyles.styles_for(None, platform, _look(sc))
        if not family:
            block["note"] = "the description does not name the hairstyle"
        if not offered:
            raise ValueError("no hairstyle in the library can be offered here")
        pic = _picture(picture_json)
        job = dict(glb=str(Path(glb).resolve()), yaw=int(yaw or 0), picture_json=str(Path(picture_json).resolve()),
                   bald_cutout=str(Path(bald_cutout).resolve()), outline=str(Path(outline).resolve()),
                   out_dir=str(out_dir.resolve()), family=family, seconds=RANK_SECONDS - 10, top=RANK_TOP, render_px=384,
                   colour=pic["colour"], styles=[_style_job(e, platform) for e in offered], head_only=family in TIED)
        if family in TIED:
            block["score_region"] = "head"
        jp = out_dir / "rank_job.json"
        jp.write_text(json.dumps(job, indent=1), encoding="utf-8")
        await sc.blender("hairfit.py", ["rank", jp], log_name="hairfit_rank.log", timeout=RANK_SECONDS)
        rep = json.loads((out_dir / "rank.json").read_text(encoding="utf-8"))
        cands = []
        for c in rep["candidates"]:
            e = hairstyles.style(c["style"])
            cands.append(dict(style=c["style"], name=e["name"], score=c["score"], iou=c["iou"], eye_penalty=c["eye_penalty"],
                              params=c["params"], triangles=c.get("triangles"), kind=e["kind"], fit_words=fit_words(c["score"]),
                              credit=hairstyles.credit(c["style"]), _files=c.get("files", {})))
        # the description's words order the candidates before the score: curly styles go last unless it asks for curls,
        # and a fringe (bangs) puts the fringe styles first
        prompt = str(getattr(getattr(sc, "asset", None), "prompt", "") or "")
        fringe = hairstyles.wants_fringe(prompt) and any(hairstyles.style(c["style"]).get("fringe") for c in cands)
        curls = hairstyles.wants_curls(prompt)
        cands.sort(key=lambda c: (bool(hairstyles.style(c["style"]).get("curly")) and not curls,
                                  fringe and not hairstyles.style(c["style"]).get("fringe"), -c["score"]))
        block["fringe_first"] = fringe
        for i, c in enumerate(cands):
            f = c.pop("_files")
            png = _sheet([Path(f.get("front", "")), Path(f.get("quarter", ""))], out_dir / f"hair_cand_{i + 1}.png")
            c["picture"] = png.name if png else None
        (out_dir / "hair_candidates.json").write_text(json.dumps(dict(candidates=cands, ranked=rep["ranked"], align=rep["align"],
                                                                      timed_out=rep.get("timed_out")), indent=1), encoding="utf-8")
        best = cands[0] if cands else None
        block.update(candidates=[{k: c[k] for k in ("style", "name", "score", "iou", "eye_penalty", "params", "triangles", "kind")} for c in cands],
                     best=best["style"] if best else None, best_score=best["score"] if best else None,
                     nothing_fits=(best is None) or best["score"] < FIT_MIN, searched=rep.get("searched"), offered=rep.get("offered"),
                     timed_out=rep.get("timed_out"))
        try:
            sc.use_model(MODEL_ID)
        except Exception:  # noqa: BLE001  (the models list is a record only)
            pass
    except Exception as e:  # noqa: BLE001  (ranking never fails the 3D step)
        if type(e).__name__ == "JobCancelled":
            raise
        block["error"] = f"{type(e).__name__}: {e}"[:300]
    block["seconds"] = round(time.time() - t0, 1)
    return block


def _flow_png(npz: Path, tex: int, dest: Path) -> Path | None:
    """The strand directions as a flow texture (hairmat.flow_map), from the canonical npz (hair triangles only)."""
    from . import hair, hairmat
    d = np.load(npz)
    role = d["role"]
    tris = d["tris"][role[d["tris"][:, 0]] != 1]
    uvs = d["uv"][role[d["tris"][:, 0]] != 1]
    V = d["verts"].astype(np.float64)[tris].reshape(-1, 3)
    F = np.arange(len(V)).reshape(-1, 3)
    UV = uvs.reshape(-1, 2).astype(np.float64)
    fl = d["flow"].astype(np.float64)[tris].reshape(-1, 3)
    from .face import smooth_normals
    mesh = {"verts": V, "faces": F, "uv": UV, "vnormals": smooth_normals(V, F)}
    reg = hair.texel_region(mesh, tex, np.ones(len(F), dtype=bool))
    flow_t = fl.reshape(-1, 3, 3).mean(1)[reg["tri"]]
    img = hairmat.flow_map(mesh, tex, reg["ys"], reg["xs"], flow_t)
    Image.fromarray(img, "RGBA").save(dest)
    return dest


def _solid(dest: Path, size: int, value: tuple[int, ...], mode: str) -> Path:
    Image.new(mode, (size, size), value if len(value) > 1 else value[0]).save(dest)
    return dest


async def attach(sc: Any, body_glb: Path, style_id: str, params: dict | None, picture_json: Path, bald_cutout: Path,
                 outline: Path, cover: str, tex: int, out_dir: Path) -> dict[str, Any]:
    """Fit `style_id` to the final body and write the hair as its own files (see the module text). `params` are the
    mesh step's numbers for this style; None runs a single-style search first (about 10 s)."""
    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    e = hairstyles.style(style_id)
    platform = getattr(sc, "platform", None)
    pic = _picture(picture_json)
    job = dict(glb=str(Path(body_glb).resolve()), yaw=0, picture_json=str(Path(picture_json).resolve()),
               bald_cutout=str(Path(bald_cutout).resolve()), outline=str(Path(outline).resolve()), out_dir=str(out_dir.resolve()),
               style=_style_job(e, platform), params=params, cover=cover or "normal", tex=int(tex), colour=pic["colour"],
               export=True, measure=True, head_only=e["family"] in TIED)
    jp = out_dir / "attach_job.json"
    jp.write_text(json.dumps(job, indent=1), encoding="utf-8")
    await sc.blender("hairfit.py", ["attach", jp], log_name="hairfit_attach.log", timeout=600)
    rep = json.loads((out_dir / "attach.json").read_text(encoding="utf-8"))
    files: dict[str, Path] = {}
    for key, name in (("hair_mesh", "hair_mesh.npz"), ("hair_parts", "hair_parts.json"), ("hair_glb", "hair.glb"),
                      ("with_hair_glb", "with_hair.glb"), ("hair_base_color", "hair_base_color.png")):
        p = out_dir / name
        if p.is_file():
            files[key] = p
    paths = hairstyles.paths(style_id)
    nsz = int(tex)
    if paths.get("normal") is not None and Path(paths["normal"]).is_file():
        Image.open(paths["normal"]).convert("RGB").resize((nsz, nsz), Image.LANCZOS).save(out_dir / "hair_normal.png")
        files["hair_normal"] = out_dir / "hair_normal.png"
    else:
        files["hair_normal"] = _solid(out_dir / "hair_normal.png", 64, (128, 128, 255), "RGB")
    files["hair_roughness"] = _solid(out_dir / "hair_roughness.png", 64, (int(round(ROUGHNESS * 255)),), "L")
    try:
        files["hair_flow"] = await asyncio.to_thread(_flow_png, files["hair_mesh"], nsz, out_dir / "hair_flow.png")
    except Exception as ex:  # noqa: BLE001  (the flow map only feeds the highlight)
        rep["flow_error"] = str(ex)[:200]
    sheet = _sheet([Path(v) for v in (rep.get("renders") or {}).values()], out_dir / "hair_fit_sheet.png")
    if sheet:
        files["hair_fit_sheet"] = sheet
    try:
        sc.use_model(MODEL_ID)
    except Exception:  # noqa: BLE001
        pass
    sk = rep.get("skin_through") or {}
    metrics = dict(attached=True, style=style_id, name=e["name"], family=e["family"], score=(rep.get("score") or {}).get("score"),
                   iou=(rep.get("score") or {}).get("iou"), eye_penalty=(rep.get("score") or {}).get("eye_penalty"),
                   eye_cover=rep.get("eye_cover"), params=rep.get("params"), triangles=rep.get("triangles"),
                   cap_triangles=rep.get("cap_triangles"), skin_through=dict(all=sk.get("all"), crown=sk.get("crown"), sides=sk.get("sides")),
                   pushed_out=rep.get("pushed_out"), kind=e["kind"], alpha=e["alpha"], two_sided=bool(e["two_sided"]), tex=nsz,
                   colour_delta=rep.get("colour_delta"), render_delta=rep.get("render_delta"),
                   score_region="head" if e["family"] in TIED else "whole", extra_height_m=rep.get("extra_height_m"), groups=rep.get("groups"),
                   credit=hairstyles.credit(style_id), seconds=round(time.time() - t0, 1))
    if rep.get("flow_error"):
        metrics["flow_error"] = rep["flow_error"]
    return dict(files=files, metrics=metrics)


def check_values(m: dict[str, Any] | None) -> dict[str, tuple[Any, Any, dict[str, Any]]]:
    """(value, limit, vars) for the checks hair_fit, hair_skin_through and hair_triangles from a hair_library metrics
    block (the mesh step's, with candidates, or the Clean up's, with the attached style). Keys whose numbers are
    missing are left out. hair_triangles needs `body_triangles` and `budget` in the block."""
    m = m or {}
    out: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    score = m.get("score", m.get("best_score"))
    if score is not None:
        name = m.get("name")
        if name is None and m.get("candidates"):
            name = m["candidates"][0].get("name")
        out["hair_fit"] = (round(float(score), 4), FIT_MIN, dict(style_name=name, family=m.get("family"), family_source=m.get("family_source"),
                                                              picked=m.get("style_source") in ("picked", "chosen"), best_name=m.get("best_name") or name,
                                                              candidates=len(m.get("candidates") or []) or m.get("candidates_n"),
                                                              eye_cover=m.get("eye_cover"), colour_delta=m.get("colour_delta")))
    sk = m.get("skin_through")
    if isinstance(sk, dict) and sk.get("all") is not None:
        out["hair_skin_through"] = (round(float(sk["all"]), 4), SKIN_MAX, dict(crown=sk.get("crown"), sides=sk.get("sides")))
    if m.get("triangles") is not None and m.get("body_triangles") is not None and m.get("budget") is not None:
        hair_t, body_t = int(m["triangles"]), int(m["body_triangles"])
        out["hair_triangles"] = (hair_t + body_t, int(m["budget"]), dict(hair=hair_t, body=body_t, allowance=m.get("allowance")))
    return out
