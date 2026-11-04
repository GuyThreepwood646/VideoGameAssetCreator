"""Stage 6, export (host): Blender pack (LODs, collider, FBX with clips, GLB), Unity sandbox import with clips and
snapshot strip, optional sprite sheets, manifest.json, quality gate.

Input mesh: the rig stage's animated GLB when present, else its (rigged or pass-through) GLB, else the prep GLB.
Textures come from the mesh stage in the lineage.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import library, quality
from ..imaging import contact_sheet
from ..sprites import pack_sprites
from .base import StageContext, slug

TEXTURE_KEYS = ("base_color", "normal", "metallic", "roughness", "ao")


LOD_SCREEN_HEIGHTS = [0.6, 0.3, 0.12, 0.03]


def parse_lods(value: Any) -> list[float] | None:
    """Accept a list of numbers or a string like "1.0, 0.5, 0.25, 0.1"; a leading 1.0 (LOD0) is dropped."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        items = [x.strip() for x in value.replace(";", ",").split(",") if x.strip()]
    else:
        items = list(value)
    out = []
    for x in items:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            raise ValueError(f"bad LOD ratio {x!r}")
    if out and abs(out[0] - 1.0) < 1e-9:
        out = out[1:]
    return out


def lod_list(lods: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for i in range(len(lods)):
        key = f"LOD{i}"
        if key not in lods:
            continue
        out.append({"level": i, "triangles": lods[key], "screen_height": LOD_SCREEN_HEIGHTS[i] if i < len(LOD_SCREEN_HEIGHTS) else 0.01})
    return out


def asset_export_name(sc: StageContext) -> str:
    return f"{slug(sc.asset.name)}_{sc.asset.id[:8]}"


def _input_glb(sc: StageContext) -> tuple[Path, str]:
    for key, stages in (("anim_glb", ("rig",)), ("glb", ("rig",)), ("glb", ("prep",)), ("glb", ("mesh",))):
        p = sc.lineage_file(key, stages)
        if p is not None:
            return p, f"{stages[0]}.{key}"
    raise FileNotFoundError("no mesh to export in lineage")


def _is_humanoid(sc: StageContext) -> bool:
    rig_v = sc.stage_version("rig")
    bones = (rig_v.metrics.get("bones") if rig_v else 0) or 0
    return sc.category.get("rig_engine") == "skintoken_mixamo" and bones > 0


async def _pack(sc: StageContext, params: dict[str, Any], name: str) -> dict[str, Any]:
    src, src_key = _input_glb(sc)
    lods = parse_lods(params.get("lods"))
    if lods is None:
        lods = list(sc.category.get("lod_ratios") or [0.5, 0.25])
    collider = params.get("collider") or "hull"
    formats = list(params.get("formats") or ["fbx", "glb"])
    sc.set_params(lods=lods, collider=collider, formats=formats, input=src_key)
    sc.use_model("blender")
    sc.progress(0.05, "Blender pack")
    # "none" asks pack.py for LOD0 only; leaving --lods out would fall back to pack.py's own default chain.
    args: list[Any] = [src, sc.dir, name, "--lods", ",".join(str(x) for x in lods) if lods else "none", "--collider", collider, "--formats", ",".join(formats)]
    await sc.blender("pack.py", args, log_name="pack.log")
    report = json.loads((sc.dir / "report.json").read_text())
    sc.register("pack_report", sc.dir / "report.json")
    if (sc.dir / f"{name}.fbx").is_file():
        sc.register("fbx", sc.dir / f"{name}.fbx")
    if (sc.dir / f"{name}.glb").is_file():
        sc.register("glb", sc.dir / f"{name}.glb")
    for key in TEXTURE_KEYS:
        p = sc.lineage_file(key, ("mesh",))
        if p is not None:
            sc.register(key, sc.copy_in(p, f"{key}.png"))
    sc.set_metrics(lods=lod_list(report.get("lods") or {}), collider_verts=report.get("collider_verts"), bones=report.get("bones"), clips=report.get("clips") or [],
                   non_manifold_edges=report.get("non_manifold_edges"), boundary_edges=report.get("boundary_edges"), dims=report.get("dims_m"))
    return report


async def _unity(sc: StageContext, params: dict[str, Any], name: str) -> dict[str, Any] | None:
    if not params.get("unity", True):
        return None
    if not sc.settings.unity_exe or not Path(sc.settings.unity_exe).is_file():
        sc.set_metrics(unity_error="unity_exe not configured")
        return None
    if sc.file("fbx") is None:
        sc.set_metrics(unity_error="no FBX to import")
        return None
    humanoid = _is_humanoid(sc)
    flags = ["-nographics"] + (["-vgacHumanoid"] if humanoid else [])
    args: dict[str, Any] = {"-vgacInput": sc.dir, "-vgacName": name}
    clips_lib = params.get("clips_library", "ual1")
    if humanoid and clips_lib and clips_lib != "none":
        lib = sc.settings.clips_libraries.get(clips_lib)
        if lib and Path(lib).is_file():
            args["-vgacClips"] = lib
            sc.use_model(clips_lib)
        else:
            sc.set_metrics(clips_warning=f"clips library {clips_lib!r} not found")
    sc.use_model("unity")
    sc.progress(0.5, "Unity sandbox import")
    report: dict[str, Any] | None = None
    try:
        await sc.unity("VgacImport.Run", args, flags, "unity_import.log")
    except Exception as e:
        sc.set_metrics(unity_error=str(e)[-800:])
    rp = sc.dir / "unity_report.json"
    if rp.is_file():
        report = json.loads(rp.read_text(encoding="utf-8"))
        sc.register("unity_report", rp)
        sc.set_metrics(unity_report={"ok": bool(report.get("ok")), "prefab": report.get("prefab", ""), "avatar": report.get("avatar", ""),
                                     "warnings": list(report.get("warnings") or []), "errors": [report["error"]] if report.get("error") else [],
                                     "humanoidValid": report.get("humanoidValid"), "lodCount": report.get("lodCount"), "lodTriangles": report.get("lodTriangles"),
                                     "bones": report.get("bones"), "colliderVertices": report.get("colliderVertices"),
                                     "texturesAssigned": report.get("texturesAssigned"), "clipCount": report.get("clipCount"), "clips": report.get("clips")})
    if report and report.get("ok"):
        await _unity_snapshot(sc, params, name)
    return report


async def _unity_snapshot(sc: StageContext, params: dict[str, Any], name: str) -> None:
    snap_dir = sc.dir / "unity_snapshots"
    try:
        sc.progress(0.7, "Unity clip snapshots")
        await sc.unity("VgacImport.Snapshot", {"-vgacName": name, "-vgacOut": snap_dir, "-vgacClipFilter": "walk,idle", "-vgacFrames": 4, "-vgacSize": 384},
                       [], "unity_snapshot.log", timeout=1800)
    except Exception as e:
        sc.set_metrics(unity_snapshot_error=str(e)[-600:])
    rp = snap_dir / "snapshot_report.json"
    if rp.is_file():
        rep = json.loads(rp.read_text(encoding="utf-8"))
        files = [Path(f) for f in rep.get("files") or [] if Path(f).is_file()]
        if files:
            strip = contact_sheet(files, sc.dir / "unity_strip.png", cell=256, columns=4, labels=[f.stem for f in files])
            sc.register("unity_strip", strip)
            sc.set_metrics(unity_snapshot_frames=len(files))


async def _sprites(sc: StageContext, params: dict[str, Any]) -> None:
    if not params.get("sprites", False):
        return
    src = sc.lineage_file("anim_glb", ("rig",)) or sc.lineage_file("glb", ("prep",)) or sc.file("glb")
    if src is None:
        return
    directions = int(params.get("sprite_directions") or 8)
    frames = int(params.get("sprite_frames") or 8)
    size = int(params.get("sprite_size") or 128)
    out = sc.dir / "sprites"
    args: list[Any] = [src, out, directions, frames, size]
    if sc.asset.style_preset == "pixel":
        args.append("--pixel")
    sc.progress(0.8, "sprite sheets")
    await sc.blender("sprites.py", args, log_name="sprites.log", timeout=3600)
    atlas = pack_sprites(out)
    sc.register("sprites_dir", out / "atlas.json")
    sc.register("sprite_atlas", out / "atlas.json")
    for i, (clip, info) in enumerate(atlas.get("clips", {}).items()):
        sc.register(f"sprite_{clip}", out / info["sheet"])
        if i == 0:
            sc.register("sprite_sheet", out / info["sheet"])
    shutil.rmtree(out / "_frames", ignore_errors=True)
    sc.set_metrics(sprites={"directions": directions, "frames": frames, "size": size, "clips": list(atlas.get("clips", {}))})


def build_manifest(sc: StageContext, name: str, pack_report: dict[str, Any] | None, unity_report: dict[str, Any] | None) -> dict[str, Any]:
    models: dict[str, dict[str, Any]] = {}
    seeds: dict[str, Any] = {}
    lineage = sc.lineage()
    for entry in lineage:
        v = sc.db.get(type(sc.version), entry["version_id"])
        if v is None:
            continue
        for m in v.metrics.get("models") or []:
            models[m.get("id", m.get("name"))] = m
        if v.params.get("seed") is not None:
            seeds[v.stage] = v.params.get("seed")
        if v.stage == "turnaround":
            seeds["turnaround_views"] = {k: info.get("seed") for k, info in (v.metrics.get("views") or {}).items()}
    for m in sc.models_used.values():
        models[m.get("id", m.get("name"))] = m
    rig_v = sc.stage_version("rig")
    textures = {}
    for key in TEXTURE_KEYS:
        p = sc.file(key)
        if p is not None:
            from ..imaging import image_size

            textures[key] = {"file": p.name, "size": list(image_size(p))}
    return {
        "name": sc.asset.name,
        "export_name": name,
        "asset_id": sc.asset.id,
        "project_id": sc.project.id,
        "version_id": sc.version.id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "category": sc.asset.category,
        "style_preset": sc.asset.style_preset,
        "style_lock": sc.project.style_lock,
        "platform_target": sc.platform,
        "budget": sc.budget,
        "prompt": sc.asset.prompt,
        "parts": sc.asset.parts,
        "triangles_per_lod": (pack_report or {}).get("lods"),
        "lod_ratios": sc.version.params.get("lods"),
        "collider_vertices": (pack_report or {}).get("collider_verts"),
        "lods": lod_list((pack_report or {}).get("lods") or {}),
        "textures": textures,
        "bones": (pack_report or {}).get("bones"),
        "bone_names": (rig_v.metrics.get("bone_names") if rig_v else None),
        "rigid": bool(rig_v.metrics.get("rigid")) if rig_v else True,
        "rig_template": (rig_v.params.get("template_label") if rig_v else None),
        "clips": (pack_report or {}).get("clips"),
        "unity": unity_report,
        "sprites": sc.version.metrics.get("sprites"),
        "files": sc.version.files,
        "models": list(models.values()),
        "licences": sorted({m.get("licence", "unknown") for m in models.values()}),
        "seeds": seeds,
        "lineage": lineage,
        "checks": sc.version.metrics.get("checks"),
    }


async def judge(sc: StageContext) -> None:
    rp = sc.file("pack_report")
    report = json.loads(Path(rp).read_text()) if rp else {}
    checks: list[dict[str, Any]] = []
    checks += quality.geometry_checks(report, sc.budget["triangles"] or None)
    checks += quality.lod_checks(report, sc.version.params.get("lods"))
    checks += quality.collider_checks(report, expected=(sc.version.params.get("collider", "hull") == "hull"))
    checks += quality.texture_checks({k: library.version_file(sc.version, k, sc.settings) for k in TEXTURE_KEYS}, sc.budget["texture"])
    rig_v = sc.stage_version("rig")
    if rig_v is not None:
        info_path = library.version_file(rig_v, "rig_info", sc.settings)
        if rig_v.metrics.get("rigid"):
            checks += quality.rig_checks({"bones": report.get("bones", 0)}, rig_expected=False)
        elif info_path is not None:
            checks += quality.rig_checks(json.loads(Path(info_path).read_text()), rig_expected=True)
    u = sc.version.metrics.get("unity_report")
    if u:
        checks.append(quality.check("unity_import", bool(u.get("ok")), u.get("ok"), True))
        if _is_humanoid(sc):
            checks.append(quality.check("unity_humanoid_avatar", bool(u.get("humanoidValid")), u.get("humanoidValid"), True))
    sc.set_metrics(**quality.summarise(checks))


async def _export(sc: StageContext, params: dict[str, Any]) -> None:
    # The material category never reaches here: stages.module_for routes its export to stages/material.py.
    name = asset_export_name(sc)
    sc.set_params(export_name=name, unity=bool(params.get("unity", True)), clips_library=params.get("clips_library", "ual1"),
                  sprites=bool(params.get("sprites", False)), sprite_directions=int(params.get("sprite_directions") or 8),
                  sprite_size=int(params.get("sprite_size") or 128))
    pack_report = await _pack(sc, params, name)
    unity_report = await _unity(sc, params, name)
    await _sprites(sc, params)
    await judge(sc)
    manifest = build_manifest(sc, name, pack_report, unity_report)
    if getattr(sc.asset, "base_asset_id", None):
        manifest["variant_of"] = {"asset_id": sc.asset.base_asset_id, "change": sc.asset.variant_change}
    mp = sc.dir / "manifest.json"
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    sc.register("manifest", mp)
    await _deliver(sc, name, params)
    sc.progress(1.0, "export done")


def delivery_dir(export_root: Path, project_name: str, asset_name: str) -> Path:
    """exports/<project>/<asset>, with both names reduced to [A-Za-z0-9_] so no name can leave the export root."""
    dest = (export_root / slug(project_name) / slug(asset_name)).resolve()
    dest.relative_to(export_root.resolve())  # ValueError if a name ever escaped
    return dest


async def _deliver(sc: StageContext, name: str, params: dict[str, Any]) -> None:
    """Decision D12: always copy the deliverables to exports/<project>/<asset>/; also import into the project's own
    Unity project when one is set (never the sandbox's prefab, a fresh import there)."""
    export_root = (Path(sc.settings.export_path) if sc.settings.export_path else Path(sc.settings.library_path).parent / "exports").resolve()
    dest = delivery_dir(export_root, sc.project.name, sc.asset.name)
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for key in ("fbx", "glb", "manifest", "base_color", "normal", "metallic", "roughness", "ao", "height", "tile_preview",
                "sprite_atlas", "unity_strip", "zip"):
        src = sc.file(key)
        if src is not None and Path(src).is_file():
            shutil.copy2(src, dest / Path(src).name)
            copied.append(Path(src).name)
    for key, rel in sc.version.files.items():
        if key.startswith("sprite_") and key != "sprite_atlas":
            src = sc.file(key)
            if src is not None and Path(src).is_file():
                shutil.copy2(src, dest / Path(src).name)
                copied.append(Path(src).name)
    sc.set_metrics(delivered_to=str(dest), delivered_files=copied)
    sc.progress(0.95, f"delivered to {dest}")
    proj_path = getattr(sc.project, "unity_project_path", None)
    if proj_path and params.get("unity", True) and sc.file("fbx") is not None:
        proj = Path(proj_path)
        if not (proj / "Assets").is_dir():
            sc.set_metrics(unity_project_import={"ok": False, "error": f"not a Unity project: {proj}"})
            return
        editor_dir = proj / "Assets" / "Editor"
        editor_dir.mkdir(parents=True, exist_ok=True)
        script_src = Path(sc.settings.unity_project) / "Assets" / "Editor" / "VgacImport.cs" if sc.settings.unity_project else None
        if script_src and script_src.is_file() and not (editor_dir / "VgacImport.cs").is_file():
            shutil.copy2(script_src, editor_dir / "VgacImport.cs")
        humanoid = _is_humanoid(sc)
        flags = ["-nographics"] + (["-vgacHumanoid"] if humanoid else [])
        args: dict[str, Any] = {"-vgacInput": sc.dir, "-vgacName": name}
        clips_lib = params.get("clips_library", "ual1")
        lib = sc.settings.clips_libraries.get(clips_lib) if humanoid and clips_lib and clips_lib != "none" else None
        if lib and Path(lib).is_file():
            args["-vgacClips"] = lib
        sc.progress(0.97, f"Unity import into {proj.name}")
        # The importer always writes <input>/unity_report.json; the sandbox step already produced one, so move it
        # aside and read only a report this run wrote (B4 in docs/CODE-AUDIT.md).
        rp = sc.dir / "unity_report.json"
        sandbox_rp = sc.dir / "unity_sandbox_report.json"
        if rp.is_file():
            rp.replace(sandbox_rp)
            if sc.version.files.get("unity_report"):
                sc.register("unity_report", sandbox_rp)
        try:
            await sc.unity("VgacImport.Run", args, flags, "unity_project_import.log", project_path=proj)
            if rp.is_file():
                report = json.loads(rp.read_text(encoding="utf-8"))
                rp.replace(sc.dir / "unity_project_report.json")
                sc.register("unity_project_report", sc.dir / "unity_project_report.json")
            else:
                report = {"ok": False, "error": "Unity wrote no report; the project may not compile or the importer script is missing"}
            sc.set_metrics(unity_project_import={"ok": bool(report.get("ok")), "project": str(proj), "prefab": report.get("prefab", ""), "error": (report.get("error") or "")[:300]})
        except Exception as e:  # noqa: BLE001
            sc.set_metrics(unity_project_import={"ok": False, "project": str(proj), "error": str(e)[-500:]})


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    await _export(sc, params)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params, "instruction": instruction}
    await _export(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params}
    await _export(sc, merged)
