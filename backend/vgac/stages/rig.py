"""Stage 5, rig (SkinToken on instance B, then Blender on the host).

Template: category rig_engine skintoken_mixamo -> "Mixamo", skintoken -> "Keep model names", none -> skip with
metrics.rigid = true (the prep GLB passes through as files.glb). SkinToken asserting on an empty skinning
prediction is also "rigid, no rig", not an error. After rigging: rig_info (bones, weight coverage, rest and bend
renders at 35 and 90 degrees, skeleton overlay), hit boxes and the body shape for moving (hitboxes.py, plan
section 4.2), then procedural idle and walk clips (procanim) and a rendered strip of them.

An Adjust that changes only the hit-box knobs (hitboxes, hitbox_fit, hitbox_regions) re-fits the hit boxes on the
parent's rigged files instead of running SkinToken again (about 10 seconds). An Adjust that changes only the moves
(added_moves, removed_moves from "Add a move", and the moves knobs) makes only the moves again on the parent's
skeleton the same way (moves.py); both together do both.

Spring bones (vgac/springs.py, blender/springs.py; setting `springs`, humanoids, on by default) run after the moves:
parts that hang clear of the body get a chain of bones and the sway is baked into every clip of anim.glb. Turning the
setting on or off makes only the moves again. Check `springs`; files springs, springs_found, springs_report.

Hair from the library (docs/HAIR-LIBRARY-PLAN.md section 6; only when the clean-up version attached a hairstyle,
metrics.hair_library.attached): after the moves and before the spring bones, hair_bind keeps anim.glb as anim_body and
binds the VGAC_Hair object into anim.glb (blender/hairbind.py; metrics.hair_bind, check hair_bound); the spring bones
then give the hairstyle's hanging groups their chains. When a done rig of the asset was built on the same body
(body_glb_hash), template and weights per vertex, it is the donor: no SkinToken, its rigged body and moves are reused and
only the hair is bound again. With today's hair none of this runs.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .. import guidance, headtris, library, quality
from ..comfy import ComfyClient, ComfyError
from ..graphs.skintoken import TEMPLATES, rig_graph
from ..imaging import contact_sheet
from .base import StageContext

HITBOX_PARAMS = ("hitboxes", "hitbox_fit", "hitbox_regions")
HITBOX_CATEGORIES = ("humanoid", "quadruped", "multileg", "winged", "serpent")
HITBOX_MODES = ("detailed", "simple", "none")
HITBOX_FITS = ("snug", "normal", "loose")
# Each shape costs physics time on small devices, so Phone gets head and body only (plan 4.2, owner decision).
HITBOX_MODE_BY_PLATFORM = {"mobile_low": "simple"}
# Files a re-fit makes itself; every other file of the parent rig version is reused.
HITBOX_FILES = ("hitboxes",)
# Metrics that belong to one run and are not carried over from the parent on a re-fit.
_RUN_METRICS = ("checks", "checks_passed", "checks_failed", "gate_ok", "hitboxes", "hitbox_error", "elapsed_seconds", "refit_of")


def hitbox_params(platform: str | None, params: dict[str, Any]) -> dict[str, Any]:
    """The hit-box knobs with their defaults: detailed on PC and Showcase, simple on Phone; normal snugness; every
    region found. Unknown values fall back to the default."""
    mode = params.get("hitboxes")
    if mode not in HITBOX_MODES:
        mode = HITBOX_MODE_BY_PLATFORM.get(platform or "", "detailed")
    fit = params.get("hitbox_fit")
    if fit not in HITBOX_FITS:
        fit = "normal"
    regions = params.get("hitbox_regions")
    if isinstance(regions, str):
        regions = [r.strip() for r in regions.split(",") if r.strip()]
    regions = [str(r) for r in regions] if regions else None
    return {"hitboxes": mode, "hitbox_fit": fit, "hitbox_regions": regions}


def hitbox_summary(doc: dict[str, Any]) -> dict[str, Any]:
    """metrics.hitboxes: what the rig view line, the finish card and the checks need from hitboxes.json."""
    boxes = doc.get("hitboxes") or []
    return {"count": int(doc.get("count") or 0), "mode": doc.get("mode"), "fit": doc.get("fit"), "factor": doc.get("factor"),
            "fallback": bool(doc.get("fallback")), "skeleton": doc.get("skeleton"),
            "names": [h.get("name") for h in boxes], "regions": [h.get("region") for h in boxes],
            "regions_found": doc.get("regions_found") or [],
            "cover": doc.get("cover"), "overreach": doc.get("overreach"),
            "movement_default": (doc.get("movement") or {}).get("default")}


def _check(id_: str, ok: bool, value: Any, limit: Any, **vars_: Any) -> dict[str, Any]:
    """quality.check plus the optional `vars` the check contract (plan 2.1) fills sentences from."""
    c = quality.check(id_, ok, value, limit)
    if vars_:
        c["vars"] = vars_
        c = guidance.describe_check(c)
    return c


def hitbox_checks(doc: dict[str, Any] | None, error: str | None = None) -> list[dict[str, Any]]:
    """hitbox_cover and hitbox_overreach (warn) from hitboxes.json; hitbox_fit only when fitting failed (it passes
    silently, so it is not listed then). No checks when the user chose no hit boxes."""
    if error:
        return [_check("hitbox_fit", False, 0, ">0")]
    if not doc or doc.get("mode") == "none" or not doc.get("count"):
        return []
    n = int(doc["count"])
    out = []
    cover, cmin = doc.get("cover"), float(doc.get("cover_min") or 0.85)
    if cover is not None:
        out.append(_check("hitbox_cover", float(cover) >= cmin, round(float(cover), 4), cmin, n=n, cover=f"{round(float(cover) * 100)}%"))
    over, omax = doc.get("overreach"), float(doc.get("overreach_max") or 1.6)
    if over is not None:
        out.append(_check("hitbox_overreach", float(over) <= omax, round(float(over), 2), omax, n=n, times=f"{float(over):.1f}"))
    return out


def resolve_template(sc: StageContext, params: dict[str, Any]) -> str | None:
    """-> SkinToken template label, or None for no rig."""
    t = params.get("template") or "auto"
    if t == "none":
        return None
    if t in TEMPLATES:
        return TEMPLATES[t]
    engine = sc.category.get("rig_engine", "none")
    if engine == "skintoken_mixamo":
        return TEMPLATES["mixamo"]
    if engine == "skintoken":
        return TEMPLATES["generic"]
    return None


def overlay_bones(info_dir: Path) -> Path | None:
    """Draw the skeleton from rig_info.json over rest.png (bakeoff/overlay_bones.py)."""
    info = json.loads((info_dir / "rig_info.json").read_text())
    if not info.get("bone_segments") or not (info_dir / "rest.png").is_file():
        return None
    cam = info["camera"]
    cx, cy, cz = cam["centre"]
    S, R = cam["ortho_scale"], cam["res"]

    def proj(p):
        return ((p[0] - cx) / S + 0.5) * R, (0.5 - (p[2] - cz) / S) * R

    im = Image.open(info_dir / "rest.png").convert("RGBA")
    bg = Image.new("RGBA", im.size, (110, 110, 110, 255))
    bg.alpha_composite(im)
    dr = ImageDraw.Draw(bg)
    for b in info["bone_segments"]:
        h, t = proj(b["head"]), proj(b["tail"])
        dr.line([h, t], fill=(255, 200, 0, 255), width=3)
        dr.ellipse([h[0] - 4, h[1] - 4, h[0] + 4, h[1] + 4], fill=(255, 60, 60, 255))
    out = info_dir / "bones.png"
    bg.convert("RGB").save(out)
    return out


async def _skip(sc: StageContext, reason: str, src: Path | None) -> None:
    if src is not None:
        sc.register("glb", sc.copy_in(src, "rig.glb"))
    link_textures(sc)
    sc.set_metrics(rigid=True, bones=0, reason=reason)
    sc.set_metrics(**quality.summarise(quality.rig_checks({"bones": 0}, rig_expected=False)))


def guard_budget(sc: StageContext, src: Path) -> None:
    """Refuse a model over the triangle budget on purpose (the 3D step left room for the head, vgac/headtris.py) when
    no clean-up model under the budget stands between it and the rig: the clean-up step was skipped or did not bring
    it back. Models built at the budget pass as before."""
    mesh_v = sc.stage_version("mesh")
    if mesh_v is None:
        return
    prep_v = sc.stage_version("prep")
    prep_glb = library.version_file(prep_v, "glb", sc.settings) if prep_v is not None else None
    from_prep = prep_glb is not None and Path(prep_glb).resolve() == Path(src).resolve()
    prep_tris = (prep_v.metrics or {}).get("triangles") if from_prep else None
    if from_prep and prep_tris is None:
        return  # an older clean-up version without a count: nothing to judge by
    problem = headtris.handoff_problem(mesh_v.params, (mesh_v.metrics or {}).get("triangles"), prep_tris, sc.budget.get("triangles"))
    if problem:
        raise ValueError(problem)


async def _rig(sc: StageContext, params: dict[str, Any]) -> None:
    if sc.asset.category == "material":
        sc.set_metrics(skipped=True, rigid=True, reason="material category has no mesh")
        return
    src = sc.lineage_file("glb", ("prep", "mesh"))
    if src is None:
        raise FileNotFoundError("no prep or mesh glb in lineage")
    guard_budget(sc, src)
    template = resolve_template(sc, params)
    target_faces = int(params.get("target_faces") or 0)
    gpv = int(params.get("group_per_vertex") or 4)
    bco = bool(params.get("bottom_center_origin", True))
    bend_test = bool(params.get("bend_test", True))
    sc.set_params(template=params.get("template") or "auto", template_label=template, target_faces=target_faces, group_per_vertex=gpv,
                  bottom_center_origin=bco, bend_test=bend_test)
    hp = hitbox_params(sc.platform, params)
    if sc.asset.category in HITBOX_CATEGORIES:
        sc.set_params(**hp)
    if template is None:
        await _skip(sc, f"rig_engine {sc.category.get('rig_engine', 'none')}", src)
        return
    lib_hair = library_hair(sc)
    if lib_hair is not None:
        # Hair from the library: the body is the clean-up GLB without hair; a rig built on the same body is reused.
        sc.set_metrics(body_glb_hash=lib_hair.get("body_glb_hash"))
        donor = find_donor(sc, template, gpv, lib_hair.get("body_glb_hash"))
        if donor is not None:
            sc.progress(0.05, "only the hair changed: reusing the rigged body")
            await rig_from_donor(sc, donor, params, bend_test)
            return

    sc.use_model("skintoken")
    mesh_name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_rig_in.glb")
    graph = rig_graph(mesh_name, sc.prefix("rig"), target_faces, template=template, group_per_vertex=gpv, bottom_center_origin=bco)
    try:
        entry = await sc.comfy(graph, poll=5.0, message="SkinToken rig", base=0.05, span=0.5)
    except ComfyError as e:
        if e.is_assertion() or any("skin" in m.lower() and "empty" in m.lower() for m in e.exception_messages):
            await _skip(sc, "SkinToken returned an empty skinning prediction (rigid object)", src)
            sc.set_metrics(skintoken_error=str(e)[:500])
            return
        raise
    faces_text = ComfyClient.texts_of(entry, "faces")
    paths = ComfyClient.texts_of(entry, "show")
    rigged = _locate_output(sc, paths)
    raw_glb = sc.copy_in(rigged, "rig_raw.glb")
    rig_glb = sc.dir / "rig.glb"
    try:
        # Repair weights: unweighted vertices inherit the nearest weighted vertex, 4 influences max, normalised.
        # It also restores what the rigger drops: texture coordinates, the material and the size cleanup set.
        # The Mixamo template's names are wrong on real rigs; rig_fix renames humanoid bones by geometry.
        fix_report = sc.dir / "rig_fix.json"
        skeleton = "humanoid" if template == TEMPLATES["mixamo"] else "other"
        await sc.blender("rig_fix.py", [raw_glb, rig_glb, "--reference", src, "--report", fix_report, "--skeleton", skeleton],
                         log_name="rig_fix.log", timeout=sc.settings.blender_timeout_seconds)
        if fix_report.is_file():
            rep = json.loads(fix_report.read_text(encoding="utf-8")) or {}
            ref, names = rep.get("reference") or {}, rep.get("bone_names") or {}
            if "stray_weights" in rep:
                sc.set_metrics(weights_fix=WEIGHTS_FIX, stray_weights=int((rep.get("stray_weights") or {}).get("verts") or 0))
            sc.set_metrics(rig_restore={"ok": bool(ref.get("ok")), "uvs": ref.get("uvs"), "scale_factor": ref.get("scale_factor"),
                                        "size_m": ref.get("size_after_m"), "reason": ref.get("reason"),
                                        "bone_names": {"count": int(names.get("renamed") or 0),
                                                       "corrected": bool(names) and not names.get("skipped_reason"),
                                                       "left_is": names.get("left_is"), "reason": names.get("skipped_reason")}})
    except Exception as e:  # noqa: BLE001
        sc.set_metrics(rig_fix_error=str(e)[:500])
    if not rig_glb.is_file():
        rig_glb = raw_glb
    sc.register("glb", rig_glb)
    link_textures(sc)
    sc.set_metrics(rigid=False, elapsed_seconds=entry.get("elapsed_seconds"), faces_rigged=int(faces_text[0]) if faces_text and str(faces_text[0]).isdigit() else None,
                   template=template)
    await _inspect(sc, rig_glb, bend_test)
    await fit_hitboxes(sc, rig_glb, hp)
    await _animate(sc, rig_glb, params)
    await judge(sc)


def _locate_output(sc: StageContext, paths: list[str]) -> Path:
    client = sc.client()
    out_dir = client.output_dir.resolve() if client.output_dir is not None else None
    for p in paths:
        cand = Path(str(p).strip())
        if out_dir is None:
            continue
        # SkinToken reports absolute or output-relative paths; either way the file must sit inside ComfyUI's
        # output folder, never anywhere else on disk.
        full = (cand if cand.is_absolute() else out_dir / cand).resolve()
        try:
            full.relative_to(out_dir)
        except ValueError:
            continue
        if full.is_file():
            return full
    if client.output_dir is not None:
        matches = sorted(client.output_dir.glob(sc.prefix("rig") + "*.glb"), key=lambda q: q.stat().st_mtime)
        if matches:
            return matches[-1]
    raise FileNotFoundError(f"rigged GLB not found; SkinToken reported {paths!r}")


async def _inspect(sc: StageContext, rig_glb: Path, bend_test: bool) -> None:
    info_dir = sc.dir / "rig_info"
    try:
        sc.progress(0.6, "rig inspection render")
        await sc.blender("rig_info.py", [rig_glb, info_dir, "35,90" if bend_test else "35"], log_name="rig_info.log", timeout=sc.settings.blender_timeout_seconds)
        info = json.loads((info_dir / "rig_info.json").read_text())
        sc.register("rig_info", info_dir / "rig_info.json")
        for key, fname in (info.get("renders") or {}).items():
            sc.register(key, info_dir / fname)
        bones_png = overlay_bones(info_dir)
        if bones_png:
            sc.register("bones", bones_png)
        strip_paths = [info_dir / f for f in [bones_png.name if bones_png else "rest.png"] + [fn for k, fn in (info.get("renders") or {}).items() if k != "rest"] if (info_dir / f).is_file()]
        if strip_paths:
            sc.register("bend_strip", contact_sheet(strip_paths, sc.dir / "bend_strip.png", cell=384, columns=len(strip_paths), labels=[p.stem for p in strip_paths]))
        sc.set_metrics(bones=info.get("bones"), bone_names=info.get("bone_names"), roots=info.get("roots"),
                       weight_coverage=round(float(info.get("weight_coverage", 0.0)), 4), max_influences=info.get("max_influences"),
                       unnormalised_verts=info.get("unnormalised_verts"), bent=info.get("bent"))
    except Exception as e:
        sc.set_metrics(rig_info_error=str(e)[:500])


async def fit_hitboxes(sc: StageContext, rig_glb: Path, hp: dict[str, Any]) -> None:
    """Run hitboxes.py on the rigged GLB: hitboxes.json (registered as "hitboxes") and metrics.hitboxes. A failure
    leaves the rig version usable and is reported by the hitbox_fit check."""
    if sc.asset.category not in HITBOX_CATEGORIES:
        return
    out = sc.dir / "hitboxes.json"
    args: list[Any] = [rig_glb, out, "--category", sc.asset.category, "--mode", hp["hitboxes"], "--fit", hp["hitbox_fit"]]
    if hp.get("hitbox_regions"):
        args += ["--regions", ",".join(hp["hitbox_regions"])]
    try:
        sc.progress(0.7, "fitting the hit boxes")
        await sc.blender("hitboxes.py", args, log_name="hitboxes.log", timeout=sc.settings.blender_timeout_seconds)
        doc = json.loads(out.read_text())
        sc.register("hitboxes", out)
        sc.set_metrics(hitboxes=hitbox_summary(doc), hitbox_error=None)
    except Exception as e:  # noqa: BLE001
        sc.set_metrics(hitboxes={"count": 0, "mode": hp["hitboxes"], "fit": hp["hitbox_fit"], "failed": True},
                       hitbox_error=str(e)[:500])


def only_hitbox_change(parent_params: dict[str, Any], params: dict[str, Any]) -> bool:
    """True when the Adjust changes at least one hit-box knob and nothing else. An empty change (a plain "Try
    again") still rigs again."""
    changed = {k for k, v in params.items() if parent_params.get(k) != v}
    return bool(changed) and changed <= set(HITBOX_PARAMS)


# The knobs that only change the moves: with nothing else changed, the skeleton is reused and only the moves are
# made again (procanim, retarget, the strip), as the hit-box re-fit does for hit boxes.
MOVES_PARAMS = ("added_moves", "removed_moves", "moves", "clips_source", "move_size", "move_speed", "motion", "springs")
# anim_body and hair_bind exist only when the hair comes from the library (hair_bind below); with today's hair the
# keys are never present, so listing them changes nothing there.
ANIM_FILES = ("anim_glb", "anim_clips", "anim_report", "anim_retarget", "anim_strip", "springs", "springs_found", "springs_report",
              "anim_body", "hair_bind")
_ANIM_METRICS = ("clips", "missing_clips", "library_clips", "procedural_clips", "clips_source", "library_skipped", "retarget_error",
                 "retarget_seconds", "retarget", "anim_error", "anim_kind", "legs_moved", "legs_total", "wings_found", "walk_probe",
                 "moves", "clips_library", "added_moves_missing", "added_moves_reasons", "springs", "hair_bind")
KEEP_MARKER = "_keep_when_done"


def reuse_change(parent_params: dict[str, Any], params: dict[str, Any]) -> set[str] | None:
    """{"hitboxes", "moves"} parts an Adjust changes when it changes only hit-box and moves knobs, else None (rig
    again). An empty change still rigs again."""
    changed = {k for k, v in params.items() if k != KEEP_MARKER and parent_params.get(k) != v}
    if not changed or not changed <= set(HITBOX_PARAMS) | set(MOVES_PARAMS):
        return None
    return ({"hitboxes"} if changed & set(HITBOX_PARAMS) else set()) | ({"moves"} if changed & set(MOVES_PARAMS) else set())


# The version of the weight clean-up (blender/rig_fix.py). 2 releases influences from a bone far across the body (a
# fingertip tied to a toe bone), which pulled hands into spikes in motion. A rig cleaned by an older version is
# cleaned again the next time it is reused, so characters made before the fix get it without being rigged again.
WEIGHTS_FIX = 2


def needs_weights_fix(metrics: dict[str, Any] | None) -> bool:
    m = metrics or {}
    return not m.get("rigid") and int(m.get("weights_fix") or 0) < WEIGHTS_FIX


async def _fix_weights_again(sc: StageContext, rig_glb: Path) -> None:
    """Run the current weight clean-up on a reused rig, in place. A failure keeps the rig as it was."""
    out, report = sc.dir / "rig_refixed.glb", sc.dir / "rig_fix_again.json"
    try:
        await sc.blender("rig_fix.py", [rig_glb, out, "--report", report, "--weights-only"], log_name="rig_fix_again.log",
                         timeout=sc.settings.blender_timeout_seconds)
        rep = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
        if out.is_file() and out.stat().st_size > 0:
            shutil.move(str(out), str(rig_glb))
            sc.set_metrics(weights_fix=WEIGHTS_FIX, stray_weights=int((rep.get("stray_weights") or {}).get("verts") or 0), weights_fixed_again=True)
    except Exception as e:  # noqa: BLE001
        sc.set_metrics(weights_fix_error=str(e)[:300])


def _reuse_parent(sc: StageContext, parent: Any, skip: tuple[str, ...] = HITBOX_FILES, skip_metrics: tuple[str, ...] = ()) -> Path | None:
    """Copy the parent rig version's files (all but `skip`, by default its hit boxes) into this version at the same
    relative paths and carry its metrics over, so deleting the parent later cannot break this version. -> the copied
    rig GLB."""
    parent_dir = library.version_dir(sc.asset, parent, sc.settings, create=False).resolve()
    for key in list(parent.files):
        if key in skip:
            continue
        src = library.version_file(parent, key, sc.settings)
        if src is None:
            continue
        src = Path(src).resolve()
        try:
            rel = src.relative_to(parent_dir)
        except ValueError:
            rel = Path(src.name)
        dest = sc.dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src != dest.resolve():
            shutil.copy2(src, dest)
        sc.register(key, dest)
    carried = {k: v for k, v in (parent.metrics or {}).items() if k not in _RUN_METRICS and k not in skip_metrics}
    sc.set_metrics(**carried, refit_of=parent.id)
    glb = sc.file("glb")
    return Path(glb) if glb is not None else None


async def reuse(sc: StageContext, parent: Any, params: dict[str, Any], parts: set[str]) -> bool:
    """Re-fit the hit boxes and/or make the moves again on the parent's skeleton (no SkinToken). False when the
    parent has no rig to reuse; the caller then rigs again. With the keep marker (POST /api/assets/{aid}/moves) the
    new version is kept when its moves were made, as the person expects after "Add them"."""
    if parent is None or parent.stage != "rig" or (parent.metrics or {}).get("rigid"):
        return False
    if "hitboxes" in parts and sc.asset.category not in HITBOX_CATEGORIES:
        parts = parts - {"hitboxes"}
        if not parts:
            return False
    if library.version_file(parent, "glb", sc.settings) is None:
        return False
    # A rig cleaned by an older version of the weight clean-up is cleaned again, and its moves are then made again
    # too (the animated file carries the weights).
    refix = needs_weights_fix(parent.metrics)
    if refix:
        parts = parts | {"moves"}
    sc.progress(0.05, "making the moves again" if "moves" in parts else "fitting the hit boxes")
    skip = (HITBOX_FILES if "hitboxes" in parts else ()) + (ANIM_FILES if "moves" in parts else ())
    rig_glb = _reuse_parent(sc, parent, skip, _ANIM_METRICS if "moves" in parts else ())
    if rig_glb is None:
        return False
    if refix:
        sc.progress(0.08, "cleaning up the skin weights")
        await _fix_weights_again(sc, rig_glb)
    if "hitboxes" in parts:
        hp = hitbox_params(sc.platform, params)
        sc.set_params(**hp)
        await fit_hitboxes(sc, rig_glb, hp)
    if "moves" in parts:
        sc.set_metrics(remade_moves_of=parent.id)
        await _animate(sc, rig_glb, params)
    await judge(sc)
    m = sc.version.metrics
    if params.get(KEEP_MARKER) and parent.selected and not m.get("anim_error") and not m.get("retarget_error") and m.get("clips"):
        from .. import pipeline

        pipeline.select(sc.db, sc.version)
    return True


async def refit(sc: StageContext, parent: Any, params: dict[str, Any]) -> bool:
    """Re-fit only the hit boxes on the parent's rigged files. False when the parent has no rig to reuse; the caller
    then rigs again."""
    if parent is None or parent.stage != "rig" or (parent.metrics or {}).get("rigid") or sc.asset.category not in HITBOX_CATEGORIES:
        return False
    if library.version_file(parent, "glb", sc.settings) is None:
        return False
    sc.progress(0.05, "fitting the hit boxes")
    rig_glb = _reuse_parent(sc, parent)
    if rig_glb is None:
        return False
    hp = hitbox_params(sc.platform, params)
    sc.set_params(**hp)
    await fit_hitboxes(sc, rig_glb, hp)
    await judge(sc)
    return True


CLIPS_SOURCES = ("auto", "library", "procedural")


def clips_source(params: dict[str, Any]) -> str:
    """The clips_source knob with its default: auto. Unknown values fall back to auto."""
    v = str(params.get("clips_source") or "auto").strip().lower()
    return v if v in CLIPS_SOURCES else "auto"


def library_file(path: Path | str | None) -> Path | None:
    """The file retarget.py loads for a configured clip library. config.yaml names the Unity FBX; the same download
    carries a glTF copy (the Godot or Unreal-Godot folder) that Blender reads faster and with the bone axes intact,
    so that is preferred. -> None when nothing usable is on disk."""
    if not path:
        return None
    p = Path(path)
    if p.suffix.lower() == ".glb" and p.is_file():
        return p
    root = p.parent
    for anc in list(p.parents)[:4]:
        if (anc / "License.txt").is_file():
            root = anc
            break
    if root.is_dir():
        # The pack's own file, not a spare mannequin (pack 2 also has a Female Mannequin GLB with no moves).
        glbs = sorted(root.rglob("*.glb"), key=lambda q: (0 if "godot" in str(q).lower() else 1, "mannequin" in str(q).lower(), len(str(q))))
        if glbs:
            return glbs[0]
    return p if p.is_file() else None


def library_rows(category: dict[str, Any], moves: Any) -> list[dict[str, Any]]:
    """The category's library_clips, narrowed to the moves knob when it is set (a row is made when its `move` is
    one of the chosen moves)."""
    rows = [r for r in (category.get("library_clips") or []) if isinstance(r, dict) and r.get("name")]
    if moves is None:
        return rows
    chosen = {str(m).strip().lower() for m in (moves if isinstance(moves, (list, tuple)) else str(moves).split(","))}
    return [r for r in rows if str(r.get("move") or r["name"]).lower() in chosen]


def use_library(sc: StageContext, params: dict[str, Any]) -> tuple[bool, str | None]:
    """(retarget library clips?, reason when not). Humanoids only (Mixamo-named skeleton), the knob not set to
    built-in moves, a configured library on disk."""
    if clips_source(params) == "procedural":
        return False, "built-in moves chosen"
    if sc.category.get("rig_engine") != "skintoken_mixamo" or not sc.category.get("library_clips"):
        return False, "library clips are for human-shaped skeletons"
    lib_id = params.get("clips_library") or "ual1"
    if lib_id == "none":
        return False, "no clip library chosen"
    if library_file((sc.settings.clips_libraries or {}).get(lib_id)) is None:
        return False, f"clip library {lib_id} not found on disk"
    return True, None


def chosen_moves_of(category: dict[str, Any], params: dict[str, Any]) -> Any:
    """The moves knob as the person chose it, or None. A stored value equal to the category's own clip list is the
    default the stage wrote back into the params, not a choice: it must not narrow the library moves (an Adjust
    inherits the parent's params, and reading it as a choice dropped jump, crouch, roll and interact)."""
    v = params.get("moves")
    if v is None:
        return None
    default = category.get("clips")
    if isinstance(v, (list, tuple)) and isinstance(default, (list, tuple)) and list(v) == list(default):
        return None
    return v


async def _animate(sc: StageContext, rig_glb: Path, params: dict[str, Any] | None = None) -> None:
    """The moves: procedural clips per category (motion and moves: G30, G31), then for humanoids the hand-made CC0
    library clips retargeted onto the skeleton (retarget.py; clips_source auto or library), which replace the
    procedural clips of the same name. The procedural clips stay as the fallback and for every non-humanoid. Then a
    rendered strip of the final clips. File names: anim.glb, anim_clips.json, anim_anim.json (and
    anim_retarget.json when the library ran)."""
    params = params or {}
    anim_glb = sc.dir / "anim.glb"
    motion = params.get("motion") or sc.category.get("motion")
    chosen_moves = chosen_moves_of(sc.category, params)
    moves = chosen_moves if chosen_moves is not None else sc.category.get("clips")
    args: list[Any] = [rig_glb, anim_glb, 24]
    if motion:
        args += ["--motion", str(motion)]
    if moves is not None:
        args += ["--clips", (",".join(moves) if isinstance(moves, (list, tuple)) else str(moves)) or "none"]
    if params.get("move_size"):
        args += ["--size", str(params["move_size"])]
    if params.get("move_speed"):
        args += ["--speed", str(params["move_speed"])]
    sc.set_params(motion=motion, moves=list(moves) if isinstance(moves, (list, tuple)) else moves, clips_source=clips_source(params))
    try:
        sc.progress(0.75, "making the moves")
        await sc.blender("procanim.py", args, log_name="procanim.log", timeout=sc.settings.blender_timeout_seconds)
        report_path, clips_path = sc.dir / "anim_anim.json", sc.dir / "anim_clips.json"
        sc.register("anim_glb", anim_glb)
        if clips_path.is_file():
            sc.register("anim_clips", clips_path)
        if report_path.is_file():
            sc.register("anim_report", report_path)
            rep = json.loads(report_path.read_text())
            sc.set_metrics(anim_kind=rep.get("kind"), motion=rep.get("motion"), clips=rep.get("clips"),
                           missing_clips=rep.get("missing_clips"), legs_moved=rep.get("legs_moved"),
                           legs_total=rep.get("legs_total"), wings_found=rep.get("wings_found"), walk_probe=rep.get("walk_probe"),
                           clips_source="procedural", library_clips=[], procedural_clips=list(rep.get("clips") or []))
        await _retarget(sc, anim_glb, params, chosen_moves)
        await _after_moves(sc, anim_glb, params)
    except Exception as e:
        sc.set_metrics(anim_error=str(e)[:500])


async def _after_moves(sc: StageContext, anim_glb: Path, params: dict[str, Any]) -> None:
    """After the moves: the library hair joins the skeleton (only when the hair comes from the library), the spring
    bones, then the rendered strip. Raises like the rest of _animate (the caller records anim_error)."""
    await hair_bind(sc, anim_glb)
    await _springs(sc, anim_glb, params)
    await _strip(sc, anim_glb)


async def _strip(sc: StageContext, anim_glb: Path) -> None:
    """The rendered strip of the final clips (anim_strip)."""
    if not (sc.version.metrics.get("clips") or []):
        return
    sc.progress(0.88, "render clip strip")
    frames_dir = sc.dir / "anim_frames"
    await sc.blender("render_anim.py", [anim_glb, frames_dir, 6, 256], log_name="render_anim.log", timeout=sc.settings.blender_timeout_seconds)
    frames = json.loads((frames_dir / "frames.json").read_text())
    paths, labels = [], []
    for clip, files in (frames.get("clips") or {}).items():
        for i, f in enumerate(files):
            paths.append(frames_dir / f)
            labels.append(f"{clip} {i}")
    if paths:
        strip = contact_sheet(paths, sc.dir / "anim_strip.png", cell=256, columns=frames.get("frames", 6), labels=labels)
        sc.register("anim_strip", strip)


# --- Hair from the library (docs/HAIR-LIBRARY-PLAN.md section 6) ----------------------------------------------------
# Everything below runs only when the clean-up version on the lineage attached a library hairstyle
# (metrics.hair_library.attached and its hair_mesh file). With today's hair none of it is reached.

HAIR_OBJECT = "VGAC_Hair"
HAIR_BOUND_MIN = 0.999


def library_hair(sc: Any) -> dict[str, Any] | None:
    """{mesh: hair_mesh.npz, parts: hair_parts.json or None, prep_glb, metrics} when the clean-up version on the
    lineage attached library hair; None otherwise (today's hair)."""
    prep_v = sc.stage_version("prep") if hasattr(sc, "stage_version") else None
    if prep_v is None:
        return None
    hl = (prep_v.metrics or {}).get("hair_library") or {}
    if not hl.get("attached"):
        return None
    mesh = library.version_file(prep_v, "hair_mesh", sc.settings)
    if mesh is None:
        return None
    return {"mesh": Path(mesh), "parts": library.version_file(prep_v, "hair_parts", sc.settings),
            "prep_glb": library.version_file(prep_v, "glb", sc.settings), "metrics": hl,
            "body_glb_hash": (prep_v.metrics or {}).get("body_glb_hash"), "prep_version": prep_v}


def hair_bind_metrics(rep: dict[str, Any] | None, seconds: float | None = None, error: str | None = None) -> dict[str, Any]:
    """metrics.hair_bind from hairbind.py's report."""
    r = rep or {}
    return {"ok": bool(r.get("ok")) and not error, "verts": int(r.get("verts") or 0), "rest_share": r.get("rest_share"),
            "nape_blend_verts": int(r.get("nape_blend_verts") or 0), "groups": len(r.get("groups") or []),
            "bound_share": r.get("bound_share"), "follow_error_m": r.get("follow_error_m"), "seconds": seconds,
            "error": (error or r.get("error") or "")[:300] or None}


async def hair_bind(sc: StageContext, anim_glb: Path) -> None:
    """Join the library hair to the skeleton (blender/hairbind.py), between the moves and the spring bones. anim.glb
    is kept first as the version file anim_body (what the donor path starts from), then written again with the
    VGAC_Hair object in it. Does nothing (no files, no metrics) when the hair is not from the library. A failure keeps
    the body-only anim.glb and records metrics.hair_bind.error."""
    lib = library_hair(sc)
    if lib is None or not anim_glb.is_file():
        return
    body = sc.dir / "anim_body.glb"
    if anim_glb.resolve() != body.resolve():
        shutil.copy2(anim_glb, body)
    sc.register("anim_body", body)
    out, rep_path = sc.dir / "hair_bind_out.glb", sc.dir / "hair_bind.json"
    args: list[Any] = [anim_glb, out, "--hair-mesh", lib["mesh"], "--report", rep_path]
    if lib.get("prep_glb") is not None:
        args += ["--reference", lib["prep_glb"]]
    t0 = time.monotonic()
    sc.progress(0.85, "fitting the hair to the skeleton")
    try:
        await sc.blender("hairbind.py", args, log_name="hairbind.log", timeout=sc.settings.blender_timeout_seconds)
        rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.is_file() else {}
        if rep.get("ok") and out.is_file() and out.stat().st_size > 0:
            shutil.move(str(out), str(anim_glb))
            sc.register("hair_bind", rep_path)
        sc.set_metrics(hair_bind=hair_bind_metrics(rep, round(time.monotonic() - t0, 2)))
    except Exception as e:  # noqa: BLE001
        rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.is_file() else {}
        sc.set_metrics(hair_bind=hair_bind_metrics(rep, round(time.monotonic() - t0, 2), error=str(e)))


def hair_bound_check(m: dict[str, Any]) -> dict[str, Any] | None:
    """hair_bound (warn): the share of the library hair's vertices following the skeleton, against 1.0. vars
    groups_swaying (spring chains made on the hair's hanging groups) and rest_on_head. None without library hair."""
    hb = m.get("hair_bind")
    if not hb:
        return None
    share = float(hb.get("bound_share") or 0.0) if hb.get("ok") else 0.0
    sp = m.get("springs") or {}
    swaying = sum(1 for c in sp.get("chains") or [] if c.get("library"))
    return quality.check("hair_bound", share >= HAIR_BOUND_MIN, round(share, 4), 1.0,
                         vars={"groups_swaying": swaying, "rest_on_head": int(hb.get("groups") or 0) == 0})


# Settings a donor rig must share with this one: the same body (body_glb_hash), template and weights per vertex.
def _same_moves(category: dict[str, Any], a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("added_moves", "removed_moves", "move_size", "move_speed", "clips_library")
    if any((a.get(k) or None) != (b.get(k) or None) for k in keys):
        return False
    if clips_source(a) != clips_source(b):  # the stage writes the default ("auto") back into its params
        return False
    return chosen_moves_of(category, a) == chosen_moves_of(category, b) and (a.get("motion") or category.get("motion")) == (b.get("motion") or category.get("motion"))


def find_donor(sc: Any, template: str, gpv: int, body_hash: str | None) -> Any:
    """A done rig of this asset built on a clean-up body with the same body_glb_hash, template and weights per vertex,
    with its rigged GLB on disk and the current weight clean-up; newest first. None when there is none."""
    if not body_hash or getattr(sc, "db", None) is None:
        return None
    from sqlalchemy import select

    from ..db import Version

    rows = sc.db.scalars(select(Version).where(Version.asset_id == sc.asset.id, Version.stage == "rig", Version.status == "done",
                                               Version.id != sc.version.id).order_by(Version.created_at.desc())).all()
    for v in rows:
        m, p = v.metrics or {}, v.params or {}
        if m.get("rigid") or m.get("body_glb_hash") != body_hash or needs_weights_fix(m):
            continue
        if p.get("template_label") != template or int(p.get("group_per_vertex") or 4) != gpv:
            continue
        if library.version_file(v, "glb", sc.settings) is None:
            continue
        return v
    return None


async def rig_from_donor(sc: StageContext, donor: Any, params: dict[str, Any], bend_test: bool) -> None:
    """Only the hair changed: reuse the donor's rigged body (rig.glb, rig_fix, rig_info, hit boxes; no SkinToken). When
    the moves settings match, start from the donor's anim_body.glb and only bind the hair, add the spring bones and
    render the strip; otherwise make the moves again on the donor's skeleton."""
    same = _same_moves(sc.category, params, donor.params or {}) and library.version_file(donor, "anim_body", sc.settings) is not None
    skip = ("anim_glb", "anim_strip", "springs", "springs_found", "springs_report", "hair_bind") if same else ANIM_FILES
    skip_m = ("springs", "hair_bind") if same else _ANIM_METRICS
    rig_glb = _reuse_parent(sc, donor, skip, skip_m)
    m = sc.version.metrics
    m.pop("refit_of", None)
    sc.version.metrics = m
    sc.set_metrics(rig_donor=donor.id)
    link_textures(sc)
    if rig_glb is None:
        raise FileNotFoundError("the donor rig has no rigged GLB")
    if same:
        anim_glb = sc.dir / "anim.glb"
        shutil.copy2(sc.file("anim_body"), anim_glb)
        sc.register("anim_glb", anim_glb)
        try:
            await _after_moves(sc, anim_glb, params)
        except Exception as e:  # noqa: BLE001
            sc.set_metrics(anim_error=str(e)[:500])
    else:
        await _animate(sc, rig_glb, params)
    await judge(sc)


async def _retarget(sc: StageContext, anim_glb: Path, params: dict[str, Any], chosen_moves: Any) -> None:
    """Retarget the library clips into anim.glb (in place). A failure leaves the procedural clips and records
    retarget_error; metrics say which clips came from where and how long it took."""
    ok, reason = use_library(sc, params)
    if not ok:
        sc.set_metrics(library_skipped=reason)
        return
    from .. import moves as moves_mod

    removed = moves_mod.removed_list(params)
    rows = moves_mod.move_rows(sc.category, params, chosen_moves)
    if not rows:
        sc.set_metrics(library_skipped="no library clips match the chosen moves")
        return
    libs, lib_missing = moves_mod.libraries_for(rows, sc.settings)
    if not libs:
        sc.set_metrics(library_skipped=next(iter(lib_missing.values()), "no clip library found on disk"))
        return
    spec_path = sc.dir / "anim_retarget_spec.json"
    spec_path.write_text(json.dumps(moves_mod.spec_rows(rows), indent=1), encoding="utf-8")
    loops = ",".join(str(r["name"]) for r in rows if r.get("loop"))
    report = sc.dir / "anim_retarget.json"
    args: list[Any] = [anim_glb, anim_glb, "--library", ";".join(f"{lid}={p}" for lid, p in libs), "--spec", spec_path,
                       "--loops", loops or "none", "--fps", 24, "--report", report]
    proc = list(sc.version.metrics.get("procedural_clips") or [])
    if removed:
        # Built-in moves the person took off (and built-in copies of removed library moves) are not kept.
        args += ["--keep", ",".join(c for c in proc if c not in removed) or "none"]
    t0 = time.monotonic()
    sc.set_metrics(library_skipped=None)
    try:
        sc.progress(0.82, "moving the hand-made clips onto the skeleton")
        await sc.blender("retarget.py", args, log_name="retarget.log", timeout=sc.settings.blender_timeout_seconds)
    except Exception as e:  # noqa: BLE001
        rep = json.loads(report.read_text()) if report.is_file() else {}
        sc.set_metrics(retarget_error=str(rep.get("error") or e)[:500], retarget_seconds=round(time.monotonic() - t0, 2))
        return
    rep = json.loads(report.read_text()) if report.is_file() else {}
    sc.register("anim_retarget", report)
    wanted = [str(r["name"]) for r in rows]
    made = list(rep.get("clips") or [])
    side = sc.dir / "anim_clips.json"
    side_doc = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else None
    added_missing = [r["name"] for r in rows if r.get("added") and r["name"] not in made]
    reasons = {k: moves_mod.plain_reason(v) for k, v in (rep.get("clip_reasons") or {}).items()}
    for r in rows:
        lid = r["from"][0]["library"] if r.get("from") else None
        if r["name"] not in made and lid in lib_missing:
            reasons[r["name"]] = lib_missing[lid]
    sc.set_metrics(clips=made, clips_source="library" if rep.get("library_clips") else "procedural",
                   library_clips=rep.get("library_clips") or [], procedural_clips=rep.get("procedural_clips") or [],
                   missing_clips=[c for c in wanted if c not in made], retarget_error=None,
                   retarget_seconds=round(time.monotonic() - t0, 2), clips_library=",".join(lid for lid, _ in libs),
                   moves=moves_mod.moves_metrics(side_doc, rows, removed), added_moves_missing=added_missing,
                   added_moves_reasons={n: reasons.get(n, "") for n in added_missing},
                   retarget={"leg_ratio": rep.get("leg_ratio"), "missing_bones": rep.get("missing_bones") or [],
                             "seconds_in_blender": rep.get("seconds"), "faithful_max_deg": _faithful_max(rep),
                             "libraries": {k: {"leg_ratio": v.get("leg_ratio"), "fps": v.get("fps"), "error": v.get("error")}
                                           for k, v in (rep.get("libraries") or {}).items()}})
    # Where the moves came from, next to the models the export manifest lists (licence, and "Where it came from").
    # The list replaces the one carried over from the parent (a move taken off takes its source with it).
    recs = moves_mod.source_records([r for r in rows if r["name"] in made], sc.settings)
    ids = {r["id"] for r in recs}
    kept_models = [x for x in (sc.version.metrics.get("models") or []) if isinstance(x, dict) and x.get("role") != "moves" and x.get("id") not in ids]
    sc.set_metrics(models=kept_models + recs)


def _hair_points(sc: StageContext, work: Path) -> Path | None:
    """The hair pass's hair triangles (their centres, glTF space) for the spring bones: the clean-up version's
    hair/hair_points.json when the hair pass found the hair; when the hair pass never ran on it (older versions, the
    pass turned off), the hair is found here the same way (hair.find_hair, no pictures). None when there is no hair to
    go by (the pass stood back, or nothing was found)."""
    prep_v = sc.stage_version("prep")
    if prep_v is None:
        return None
    try:
        p = Path(library.version_dir(sc.asset, prep_v, sc.settings, create=False)) / "hair" / "hair_points.json"
    except Exception:  # noqa: BLE001
        return None
    if p.is_file():
        return p
    if (prep_v.metrics or {}).get("hair_detail") is not None or sc.asset.category != "humanoid":
        return None  # the hair pass ran and stood back: the hair could not be told apart
    glb = library.version_file(prep_v, "glb", sc.settings)
    base = sc.lineage_file("base_color", ("prep", "mesh"))
    if glb is None or base is None:
        return None
    from .. import face, hair

    def find() -> Path | None:
        found = hair.find_hair(Path(glb), Path(base), sc.lineage_file("metallic", ("prep", "mesh")))
        if not found.get("found"):
            return None
        out = work / "hair_points.json"
        out.write_text(json.dumps(hair.hair_points(face.load_mesh(Path(glb)), found["tris"])), encoding="utf-8")
        return out

    try:
        return find()
    except Exception:  # noqa: BLE001
        return None


async def _springs(sc: StageContext, anim_glb: Path, params: dict[str, Any]) -> None:
    """Spring bones (vgac/springs.py, blender/springs.py): find the parts that hang free, add a chain of bones to each,
    fade the weights onto it and bake the sway into every clip of anim.glb (in place; the file before is kept as
    springs/anim_plain.glb). Files: springs (springs.json), springs_found (what was taken as a hanging part, with the
    chains drawn on it), springs_report. metrics.springs says what sways or why nothing does. Never raises."""
    from .. import springs as springs_mod

    rep_path = sc.dir / "anim_anim.json"
    try:
        anim_rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.is_file() else {}
    except (OSError, ValueError):
        anim_rep = {}
    on, reason = springs_mod.enabled(sc.asset.category, params, anim_rep)
    if sc.asset.category in springs_mod.CATEGORIES:
        sc.set_params(springs=bool(on or reason != "off"))
    if not on:
        sc.set_metrics(springs=springs_mod.metrics(None, False, reason) if reason else None)
        return
    if not (sc.version.metrics.get("clips") or []) or not anim_glb.is_file():
        sc.set_metrics(springs=springs_mod.metrics(None, True, "no_clips"))
        return
    work = sc.dir / "springs"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    out = work / "anim_springs.glb"
    try:
        sc.progress(0.86, "spring bones: finding what hangs free")
        lib = library_hair(sc) if (sc.version.metrics.get("hair_bind") or {}).get("ok") else None
        # With library hair the body is bald: its hair points mean nothing, and the hair object brings its own groups.
        hair_points = None if lib is not None else await asyncio.to_thread(_hair_points, sc, work)
        hb = sc.file("hitboxes")
        args = springs_mod.blender_args(anim_glb, out, work, sc.dir / "anim_clips.json", Path(hb) if hb else None, hair_points,
                                        sc.lineage_file("metallic", ("prep", "mesh")),
                                        library_hair=(HAIR_OBJECT, lib["mesh"], lib.get("parts")) if lib is not None else None)
        await sc.blender("springs.py", args, log_name="springs.log", timeout=sc.settings.blender_timeout_seconds)
        rep = json.loads((work / "springs_report.json").read_text(encoding="utf-8"))
        family = ((lib or {}).get("metrics") or {}).get("family")  # library hair: the hairstyle's family words
        sheet = springs_mod.found_sheet(rep, work / "springs_pictures", sc.dir / "springs_found.png", family=family)
        if sheet is not None:
            sc.register("springs_found", sheet)
        sc.register("springs_report", work / "springs_report.json")
        if rep.get("chains") and out.is_file() and out.stat().st_size > 0:
            shutil.copy2(anim_glb, work / "anim_plain.glb")
            shutil.move(str(out), str(anim_glb))
            doc = sc.dir / "springs.json"
            shutil.copy2(work / "springs.json", doc)
            sc.register("springs", doc)
        sc.set_metrics(springs=springs_mod.metrics(rep, True, None, round(time.monotonic() - t0, 2), family=family))
    except Exception as e:  # noqa: BLE001
        sc.set_metrics(springs=springs_mod.metrics(None, True, None, round(time.monotonic() - t0, 2), error=str(e)))


def springs_check(m: dict[str, Any] | None) -> dict[str, Any] | None:
    """The rig check `springs` from metrics.springs (vgac/springs.check_values), or None when it does not apply."""
    from .. import springs as springs_mod

    v = springs_mod.check_values(m)
    if v is None:
        return None
    return quality.check("springs", v["ok"], v["value"], v["limit"], vars=v["vars"])


def _faithful_max(rep: dict[str, Any]) -> float | None:
    """Largest angle between a source limb segment and the same target segment over every library clip (the IK
    that plants the feet accounts for most of it)."""
    vals = [v.get("max_deg") for d in (rep.get("detail") or {}).values() for v in (d.get("segment_angle") or {}).values()]
    vals = [float(v) for v in vals if v is not None]
    return round(max(vals), 1) if vals else None


def link_textures(sc: StageContext) -> None:
    """The rig version shows the maps that ship: the clean-up step's (which may have painted the face or corrected the
    colours), else the mesh stage's."""
    from .prep import link_textures as _lt

    _lt(sc, stages=("prep",) if sc.stage_version("prep") is not None else ("mesh",))


async def judge(sc: StageContext) -> None:
    info_path = sc.file("rig_info")
    if sc.version.metrics.get("rigid"):
        checks = quality.rig_checks({"bones": sc.version.metrics.get("bones", 0)}, rig_expected=False)
    elif info_path is not None:
        checks = quality.rig_checks(json.loads(Path(info_path).read_text()), rig_expected=True,
                                    group_per_vertex=sc.version.params.get("group_per_vertex"))
    else:
        checks = quality.rig_checks({"bones": sc.version.metrics.get("bones", 0)}, rig_expected=True)
    if sc.version.metrics.get("anim_glb") or sc.file("anim_glb"):
        m = sc.version.metrics
        if m.get("rig_restore") is not None:
            checks.append(quality.check("rig_restore", bool(m["rig_restore"].get("ok")), bool(m["rig_restore"].get("ok")), True))
        clips, missing = m.get("clips") or [], m.get("missing_clips") or []
        checks.append(quality.check("clips", bool(clips) and not missing, clips, clips + missing, vars={"missing": missing}))
        # The hand-made library was meant to run (humanoid, automatic or library) but gave no clips.
        if m.get("library_skipped") is None and "library_clips" in m and (m.get("library_clips") or m.get("retarget_error")):
            lib = m.get("library_clips") or []
            checks.append(quality.check("library_clips", bool(lib) and not m.get("retarget_error"), len(lib), ">0",
                                        vars={"count": len(lib), "reason": m.get("retarget_error") or ""}))
        # Moves the person added ("Add a move"): every one made, or which were not and why.
        added = [x for x in (m.get("moves") or []) if isinstance(x, dict) and x.get("added")]
        if added:
            missing_added = [x.get("label") or x.get("name") for x in added if not x.get("made", True)]
            made_n = len(added) - len(missing_added)
            why = "; ".join(sorted({str(v) for v in (m.get("added_moves_reasons") or {}).values() if v}))
            checks.append(quality.check("added_moves", not missing_added, made_n, len(added),
                                        vars={"missing": missing_added, "reason": (why[:1].upper() + why[1:] + ".") if why else ""}))
        if sc.asset.category in ("quadruped", "multileg") and m.get("legs_total"):
            checks.append(quality.check("legs_moved", (m.get("legs_moved") or 0) >= m["legs_total"], m.get("legs_moved"), m["legs_total"]))
        if sc.asset.category == "winged":
            checks.append(quality.check("wings_found", bool(m.get("wings_found")), bool(m.get("wings_found")), True))
        sp = springs_check(m.get("springs"))
        if sp is not None:
            checks.append(sp)
        hb = hair_bound_check(m)
        if hb is not None:
            checks.append(hb)
    if not sc.version.metrics.get("rigid") and sc.asset.category in HITBOX_CATEGORIES:
        hb_path = sc.file("hitboxes")
        doc = json.loads(Path(hb_path).read_text()) if hb_path is not None else None
        checks += hitbox_checks(doc, sc.version.metrics.get("hitbox_error"))
    sc.set_metrics(**quality.summarise(checks))


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    await _rig(sc, params)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params, "instruction": instruction}
    await _rig(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    parent_params = parent.params if parent else {}
    merged = {**parent_params, **params}
    if parent is not None and only_hitbox_change(parent_params, params) and await refit(sc, parent, merged):
        return
    parts = reuse_change(parent_params, params) if parent is not None else None
    if parts and await reuse(sc, parent, merged, parts):
        return
    merged.pop(KEEP_MARKER, None)
    await _rig(sc, merged)
