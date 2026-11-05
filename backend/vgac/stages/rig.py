"""Stage 5, rig (SkinToken on instance B, then Blender on the host).

Template: category rig_engine skintoken_mixamo -> "Mixamo", skintoken -> "Keep model names", none -> skip with
metrics.rigid = true (the prep GLB passes through as files.glb). SkinToken asserting on an empty skinning
prediction is also "rigid, no rig", not an error. After rigging: rig_info (bones, weight coverage, rest and bend
renders at 35 and 90 degrees, skeleton overlay), hit boxes and the body shape for moving (hitboxes.py, plan
section 4.2), then procedural idle and walk clips (procanim) and a rendered strip of them.

An Adjust that changes only the hit-box knobs (hitboxes, hitbox_fit, hitbox_regions) re-fits the hit boxes on the
parent's rigged files instead of running SkinToken again (about 10 seconds).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .. import guidance, library, quality
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


async def _rig(sc: StageContext, params: dict[str, Any]) -> None:
    if sc.asset.category == "material":
        sc.set_metrics(skipped=True, rigid=True, reason="material category has no mesh")
        return
    src = sc.lineage_file("glb", ("prep", "mesh"))
    if src is None:
        raise FileNotFoundError("no prep or mesh glb in lineage")
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


def _reuse_parent(sc: StageContext, parent: Any) -> Path | None:
    """Copy the parent rig version's files (all but its hit boxes) into this version at the same relative paths and
    carry its metrics over, so deleting the parent later cannot break this version. -> the copied rig GLB."""
    parent_dir = library.version_dir(sc.asset, parent, sc.settings, create=False).resolve()
    for key in list(parent.files):
        if key in HITBOX_FILES:
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
    carried = {k: v for k, v in (parent.metrics or {}).items() if k not in _RUN_METRICS}
    sc.set_metrics(**carried, refit_of=parent.id)
    glb = sc.file("glb")
    return Path(glb) if glb is not None else None


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


async def _animate(sc: StageContext, rig_glb: Path, params: dict[str, Any] | None = None) -> None:
    """Procedural clips per category (motion and moves: G30, G31), plus a rendered strip of them."""
    params = params or {}
    anim_glb = sc.dir / "anim.glb"
    motion = params.get("motion") or sc.category.get("motion")
    moves = params.get("moves")
    if moves is None:
        moves = sc.category.get("clips")
    args: list[Any] = [rig_glb, anim_glb, 24]
    if motion:
        args += ["--motion", str(motion)]
    if moves is not None:
        args += ["--clips", (",".join(moves) if isinstance(moves, (list, tuple)) else str(moves)) or "none"]
    if params.get("move_size"):
        args += ["--size", str(params["move_size"])]
    if params.get("move_speed"):
        args += ["--speed", str(params["move_speed"])]
    sc.set_params(motion=motion, moves=list(moves) if isinstance(moves, (list, tuple)) else moves)
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
                           legs_total=rep.get("legs_total"), wings_found=rep.get("wings_found"), walk_probe=rep.get("walk_probe"))
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
    except Exception as e:
        sc.set_metrics(anim_error=str(e)[:500])


def link_textures(sc: StageContext) -> None:
    from .prep import link_textures as _lt

    _lt(sc)


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
        if sc.asset.category in ("quadruped", "multileg") and m.get("legs_total"):
            checks.append(quality.check("legs_moved", (m.get("legs_moved") or 0) >= m["legs_total"], m.get("legs_moved"), m["legs_total"]))
        if sc.asset.category == "winged":
            checks.append(quality.check("wings_found", bool(m.get("wings_found")), bool(m.get("wings_found")), True))
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
    await _rig(sc, merged)
