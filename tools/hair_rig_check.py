"""Offline check of "Hair from the library" through the rig step, the hand-off and a Unity sandbox import
(docs/HAIR-LIBRARY-PLAN.md section 8, lane 3).

Run: backend/.venv/Scripts/python.exe tools/hair_rig_check.py [--work runtime/tmp/hairrig/run] [--no-unity] [--fresh-rig]

It drives the real stage code (stages/rig.py run, the donor path, stages/export.py run) with a stand-in stage context
whose versions live in a scratch library under the work folder, never in the app's library or database, and runs no
job through the app (no calls to the backend at all). Inputs:
- the wig trial's bald build of the ponytail character (asset 04090242, mesh 4b3834cd) read from the app's library
  folder (read only);
- lane 1's fixture backend/tests/data/hairlib_fixture (hair_mesh.npz and hair_parts.json of MakeHuman ponytail01 fitted
  to that bald build, in the bald mesh's own model space, and its recoloured texture).
Steps:
1. clean-up: blender/prep.py sizes the bald build to 1.7 m with the pivot at its feet (the clean-up step's own
   script); the hair is carried into the clean-up model's space by the same box fit; the clean-up version records
   hair_library {attached, kind cards, alpha cutout, two_sided} and body_glb_hash as lane 2 does.
2. rig: stages/rig.run. SkinToken runs once on ComfyUI B (127.0.0.1:8189) and its output is cached in the work folder,
   so later runs reuse it. Then a second rig version with only the hair changed takes the donor path (no SkinToken).
3. hand-off: stages/export.run with the Unity sandbox import and snapshots (batch mode), once with the library hair
   and once without it (the same body, today's chain) to compare checks, body measures and sockets.
Writes <work>/summary.json and pictures under <work>; prints a short summary.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from vgac import library, reference  # noqa: E402
from vgac.blender import hairbind  # noqa: E402
from vgac.comfy import ComfyClient  # noqa: E402
from vgac.config import get_settings  # noqa: E402
from vgac.stages import export as export_stage  # noqa: E402
from vgac.stages import rig as rig_stage  # noqa: E402
from vgac.stages.base import StageContext  # noqa: E402
from vgac.tools import run_blender, run_unity  # noqa: E402

BALD_MESH = ROOT / "library/projects/2b9d6010-d0eb-460f-a047-523f41a1811d/assets/04090242-8f24-4e2a-b913-076889030a54/mesh/4b3834cd-3cfa-4345-b2ae-7e942b3d0697"
FIXTURE = ROOT / "backend/tests/data/hairlib_fixture"
MAPS = ("base_color", "normal", "metallic", "roughness", "ao")


class FakeVersion:
    def __init__(self, stage: str, parent: "FakeVersion | None" = None, vid: str | None = None):
        self.id = vid or str(uuid.uuid4())
        self.stage = stage
        self.parent_version_id = parent.id if parent else None
        self._parent = parent
        self.params: dict[str, Any] = {}
        self.files: dict[str, str] = {}
        self.metrics: dict[str, Any] = {}
        self.status = "done"
        self.selected = True
        self.created_at = time.time()


class FakeSC(StageContext):
    """StageContext with the database, the job and the ComfyUI client replaced by stand-ins."""

    def __init__(self, settings, asset, project, version: FakeVersion, skintoken_cache: Path):
        self.ctx = SimpleNamespace(settings=settings, instance="b", check_cancelled=lambda: None,
                                   progress=lambda f, m: None, register_process=lambda p: None)
        self.db = None
        self.version = version
        self.asset = asset
        self.project = project
        self.settings = settings
        self.category = reference.category(asset.category)
        self.style = {"id": asset.style_preset, "model": "zturbo", "prompt_block": ""}
        self.platform = asset.platform_target
        self.budget = reference.budget(asset.category, self.platform)
        self.dir = library.version_dir(asset, version, settings)
        self.models_used = {}
        self.cache = skintoken_cache
        self.log: list[str] = []

    def progress(self, fraction=None, message=None):
        if message:
            self.log.append(f"{time.strftime('%H:%M:%S')} {message}")
            print("   ", message, flush=True)

    def save(self):
        pass

    def iter_lineage(self, include_self=True):
        v = self.version if include_self else self.version._parent
        while v is not None:
            yield v
            v = v._parent

    def selected_version(self, stage):
        return None

    async def blender(self, script, args, log_name=None, timeout=3600):
        log_path = self.dir / (log_name or f"{Path(script).stem}.log")
        return await asyncio.to_thread(run_blender, self.settings, script, args, log_path, timeout, None)

    async def unity(self, method, args, flags, log_name, timeout=3600, project_path=None):
        return await asyncio.to_thread(run_unity, self.settings, method, args, flags, self.dir / log_name, timeout, None, project_path)

    def client(self, instance=None):
        return SimpleNamespace(output_dir=self.cache.parent, name="b")

    async def stage_input(self, path, name, instance=None):
        if self.cache.is_file():
            return name
        inst = self.settings.instances["b"]
        c = ComfyClient(inst.url, name="b", input_dir=self.settings.comfy_input_dir, output_dir=inst.output_dir)
        return await c.stage_input(path, name)

    async def comfy(self, graph, instance=None, poll=1.0, message="", base=0.0, span=1.0):
        if not self.cache.is_file():
            inst = self.settings.instances["b"]
            c = ComfyClient(inst.url, name="b", input_dir=self.settings.comfy_input_dir, output_dir=inst.output_dir)
            print("    SkinToken on ComfyUI B (once; cached afterwards)", flush=True)
            entry = await c.run(graph, poll_seconds=poll, timeout=1800)
            paths = ComfyClient.texts_of(entry, "show")
            out_dir = Path(inst.output_dir).resolve()
            found = None
            for p in paths:
                q = Path(str(p).strip())
                q = q if q.is_absolute() else out_dir / q
                if q.is_file():
                    found = q
            if found is None:
                cands = sorted(out_dir.glob(graph["rig"]["inputs"]["filename_prefix"] + "*.glb"), key=lambda q: q.stat().st_mtime)
                found = cands[-1]
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, self.cache)
            (self.cache.parent / "skintoken_faces.txt").write_text(str((ComfyClient.texts_of(entry, "faces") or [""])[0]))
        faces = (self.cache.parent / "skintoken_faces.txt")
        return {"outputs": {"show": {"text": [str(self.cache)]}, "faces": {"text": [faces.read_text() if faces.is_file() else ""]}},
                "elapsed_seconds": 0.0}


def register(settings, v: FakeVersion, key: str, path: Path) -> None:
    v.files[key] = library.rel_path(path, settings)


def glb_box_blender(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Bounding box of every mesh in a GLB, in Blender axes (z up), from the accessors' positions and node matrices."""
    import trimesh

    s = trimesh.load(str(path))
    lo, hi = s.bounds
    P = hairbind.gltf_to_blender(np.array([lo, hi]))
    return P.min(axis=0), P.max(axis=0)


def make_hair_into(prep_dir: Path, mesh_glb: Path, prep_glb: Path) -> dict[str, Any]:
    """Carry lane 1's fixture (fitted on the bald mesh in its own model space) into the clean-up model's space, as
    lane 2's attach at the end of Clean up would have it: the same box fit as the clean-up size and pivot."""
    nz = np.load(FIXTURE / "hair_mesh.npz")
    mlo, mhi = glb_box_blender(mesh_glb)
    plo, phi = glb_box_blender(prep_glb)
    s, sh = hairbind.box_fit(mlo, mhi, plo, phi)

    def carry(P):
        return hairbind.blender_to_gltf(hairbind.gltf_to_blender(P) * s + sh).astype(np.float32)

    out = prep_dir / "hair_mesh.npz"
    np.savez_compressed(out, verts=carry(nz["verts"]), tris=nz["tris"], uv=nz["uv"], role=nz["role"],
                        flow=hairbind.blender_to_gltf(hairbind.gltf_to_blender(nz["flow"])).astype(np.float32))
    parts = json.loads((FIXTURE / "hair_parts.json").read_text(encoding="utf-8"))
    for g in parts.get("groups") or []:
        g["path"] = carry(np.asarray(g["path"], float)).round(5).tolist()
        g["length_m"] = round(float(g.get("length_m") or 0) * s, 4)
    parts["extra_height_m"] = round(float(parts.get("extra_height_m") or 0) * s, 4)
    (prep_dir / "hair_parts.json").write_text(json.dumps(parts, indent=1), encoding="utf-8")
    base = prep_dir / "hair_base_color.png"
    shutil.copy2(FIXTURE / "hair_base_color.png", base)
    size = Image.open(base).size
    Image.new("L", size, int(round(0.55 * 255))).save(prep_dir / "hair_roughness.png")
    Image.new("RGBA", size, (128, 0, 255, 128)).save(prep_dir / "hair_flow.png")
    return {"scale": s, "shift": [float(x) for x in sh], "tex": size[0], "triangles": int(len(nz["tris"])),
            "cap_triangles": None, "groups": len(parts.get("groups") or [])}


def credit() -> dict[str, Any]:
    cat = json.loads((FIXTURE / "lib" / "catalogue.json").read_text(encoding="utf-8"))
    styles = cat.get("styles") if isinstance(cat, dict) else cat
    styles = styles.values() if isinstance(styles, dict) else (styles or [])
    e = next((x for x in styles if isinstance(x, dict) and "ponytail" in str(x.get("id"))), {})
    return {"id": e.get("id", "mh_ponytail01"), "name": e.get("name", "ponytail01"), "author": e.get("author", "MakeHuman"),
            "licence": e.get("licence", "CC0-1.0"), "source_url": e.get("source_url")}


def unity_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Unity.exe"], capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return False
    return "Unity.exe" in out


def checks_of(v: FakeVersion) -> list[tuple[str, bool]]:
    return [(c.get("id") or c.get("name"), bool(c.get("ok", c.get("passed")))) for c in v.metrics.get("checks") or []]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(ROOT / "runtime/tmp/hairrig/run"))
    ap.add_argument("--no-unity", action="store_true")
    ap.add_argument("--fresh-rig", action="store_true", help="run SkinToken again instead of the cached output")
    ap.add_argument("--style", default="",
                    help="run lane 1's real hairfit.attach with this library style on the clean-up body (else the fixture)")
    ap.add_argument("--budget", type=int, default=0,
                    help="body triangles: the pc_mid budget 20000 less the hair allowance (5051 rounded up to 250), as lane 2's prep does")
    a = ap.parse_args()
    if not a.budget:
        # the pc_mid budget less the hair allowance: the style's triangles plus about 300 for the cap, rounded up to
        # 250, at most 25 percent of the budget (plan section 3)
        tris = 5051
        if a.style:
            from vgac import hairstyles
            tris = int(hairstyles.style(a.style)["triangles"]) + 300
        a.budget = 20000 - min(5000, -(-tris // 250) * 250)
    work = Path(a.work).resolve()
    (work / "lib").mkdir(parents=True, exist_ok=True)
    settings = get_settings().model_copy(update={"library_path": work / "lib", "export_path": work / "exports"})
    summary: dict[str, Any] = {"work": str(work)}
    t_all = time.monotonic()

    def asset_ns(aid: str, name: str):
        return SimpleNamespace(id=aid, project_id="hairrig", name=name, category="humanoid", platform_target="pc_mid",
                               style_preset="cel", prompt="dark brown hair in a long high ponytail", parts=None,
                               held_item=None, equipment=None, base_asset_id=None, variant_change=None)

    project = SimpleNamespace(id="hairrig", name="Hair rig check", platform_target="pc_mid", style_lock=None, unity_project_path=None)
    asset = asset_ns("hairrig-ponytail-library", "Ponytail library hair")

    # 1. mesh and clean-up
    mesh_v = FakeVersion("mesh", vid="mesh-4b3834cd")
    mdir = library.version_dir(asset, mesh_v, settings)
    for f in ["model.glb"] + [f"{k}.png" for k in MAPS]:
        if (BALD_MESH / f).is_file():
            shutil.copy2(BALD_MESH / f, mdir / f)
    register(settings, mesh_v, "glb", mdir / "model.glb")
    for k in MAPS:
        if (mdir / f"{k}.png").is_file():
            register(settings, mesh_v, k, mdir / f"{k}.png")
    prep_v = FakeVersion("prep", mesh_v, vid="prep-hairrig")
    pdir = library.version_dir(asset, prep_v, settings)
    prep_glb = pdir / "prep.glb"
    if (pdir / "budget.txt").is_file() and (pdir / "budget.txt").read_text() != str(a.budget) and prep_glb.is_file():
        prep_glb.unlink()  # a clean-up made for another budget
    (pdir / "budget.txt").write_text(str(a.budget))
    print("1. clean-up (prep.py) and the hair carried into its space", flush=True)
    if not prep_glb.is_file():
        run_blender(settings, "prep.py", [mdir / "model.glb", prep_glb, "ponytail", "--size", 1.7, "--pivot", "base",
                                          "--budget", a.budget], pdir / "prep.log", 1800)
    register(settings, prep_v, "glb", prep_glb)
    for k in MAPS:
        if k in mesh_v.files:
            prep_v.files[k] = mesh_v.files[k]
    rep = json.loads((pdir / "prep_report.json").read_text()) if (pdir / "prep_report.json").is_file() else {}
    body_hash = hashlib.sha1(prep_glb.read_bytes()).hexdigest()
    if a.style:
        # lane 1's real attach on the clean-up body, with the trial's bald picture measures (tools/hairlib_check.py)
        print(f"   hairfit.attach {a.style}", flush=True)
        sys.path.insert(0, str(ROOT / "tools"))
        import hairlib_check
        from vgac import hairfit
        adir = pdir / "attach_in"
        adir.mkdir(exist_ok=True)
        pic = hairlib_check.pictures("ponytail", adir)
        sc_p = FakeSC(settings, asset, project, prep_v, work / "unused.glb")
        at = await hairfit.attach(sc_p, prep_glb, a.style, None, pic["picture"], pic["cutout"], pic["outline"], "normal", 1024,
                                  pdir / "hair")
        for k, pth in at["files"].items():
            register(settings, prep_v, k, Path(pth))
        hl = dict(at["metrics"])
        hair = {k: hl.get(k) for k in ("style", "kind", "alpha", "two_sided", "tex", "triangles", "cap_triangles", "groups", "score",
                                         "skin_through", "colour_delta", "seconds")}
    else:
        hair = make_hair_into(pdir, mdir / "model.glb", prep_glb)
        for k in ("hair_mesh", "hair_parts", "hair_base_color", "hair_roughness", "hair_flow"):
            ext = ".npz" if k == "hair_mesh" else (".json" if k == "hair_parts" else ".png")
            register(settings, prep_v, k, pdir / f"{k}{ext}")
        hl = {"attached": True, "style": "mh_ponytail01", "family": "ponytail", "kind": "cards", "alpha": "cutout",
              "two_sided": True, "tex": hair["tex"], "triangles": hair["triangles"], "credit": credit()}
    cache = work / "skintoken" / body_hash[:10] / "rig_raw.glb"  # SkinToken's output for this body
    if a.fresh_rig and cache.is_file():
        cache.unlink()
    prep_v.metrics = {"triangles": rep.get("triangles"), "body_glb_hash": body_hash, "hair_library": hl}
    summary["prep"] = {"triangles": rep.get("triangles"), "dims_m": rep.get("dims_m"), "hair_carry": hair}

    # 2. rig (SkinToken once, cached), then the donor path
    rig_v = FakeVersion("rig", prep_v, vid=f"r{body_hash[:7]}-rig-1")
    sc = FakeSC(settings, asset, project, rig_v, cache)
    print("2. rig step (stages/rig.run)", flush=True)
    t0 = time.monotonic()
    await rig_stage.run(sc, {})
    summary["rig"] = {"seconds": round(time.monotonic() - t0, 1), "hair_bind": rig_v.metrics.get("hair_bind"),
                      "springs": {k: (rig_v.metrics.get("springs") or {}).get(k) for k in ("made", "reason", "chains", "bones_added", "what", "error", "seam_max_m", "into_body_max_m", "swing_deg")},
                      "checks": checks_of(rig_v), "clips": rig_v.metrics.get("clips"), "anim_error": rig_v.metrics.get("anim_error"),
                      "files": sorted(rig_v.files)}
    rig_v.status = "done"
    print("   donor path: only the hair changed", flush=True)
    rig2 = FakeVersion("rig", prep_v, vid=f"r{body_hash[:7]}-rig-2")
    sc2 = FakeSC(settings, asset, project, rig2, cache)
    orig = rig_stage.find_donor
    rig_stage.find_donor = lambda s, template, gpv, h: rig_v if (h == body_hash and rig_v.params.get("template_label") == template) else None
    t0 = time.monotonic()
    try:
        await rig_stage.run(sc2, {})
    finally:
        rig_stage.find_donor = orig
    summary["rig_donor"] = {"seconds": round(time.monotonic() - t0, 1), "rig_donor": rig2.metrics.get("rig_donor"),
                            "hair_bind": rig2.metrics.get("hair_bind"), "checks": checks_of(rig2),
                            "springs_bones": (rig2.metrics.get("springs") or {}).get("bones_added"),
                            "skintoken_log": (rig2.metrics.get("models") or [])}

    # 3. hand-off with the library hair, then the same body without it (today's chain)
    do_unity = not a.no_unity
    if do_unity and unity_running():
        print("   another Unity is running: the Unity import is left out", flush=True)
        do_unity = False
    exp_v = FakeVersion("export", rig_v, vid="export-hairrig-lib")
    sc3 = FakeSC(settings, asset, project, exp_v, cache)
    print("3. hand-off (stages/export.run) with library hair", flush=True)
    t0 = time.monotonic()
    await export_stage.run(sc3, {"unity": do_unity})
    pr = json.loads(Path(sc3.file("pack_report")).read_text())
    summary["export_library"] = {"seconds": round(time.monotonic() - t0, 1), "checks": checks_of(exp_v),
                                 "hair_material": exp_v.metrics.get("hair_material"), "dims_m": pr.get("dims_m"),
                                 "non_manifold_edges": pr.get("non_manifold_edges"), "boundary_edges": pr.get("boundary_edges"),
                                 "lods": pr.get("lods"), "lods_body": pr.get("lods_body"), "sockets": pr.get("sockets"),
                                 "unity": {k: (exp_v.metrics.get("unity_report") or {}).get(k) for k in
                                           ("ok", "hairMaterial", "hairShader", "hairSubmeshes", "hairTriangles", "hairCutout",
                                            "hairTwoSided", "springChains", "springBones", "springWarning", "lodTriangles", "warnings")},
                                 "dir": str(sc3.dir)}

    # today's chain on the same body: a clean-up without hair_library and a rig whose moves file is the body alone
    asset_off = asset_ns("hairrig-ponytail-off", "Ponytail no library hair")
    prep_off = FakeVersion("prep", mesh_v, vid="prep-hairrig-off")
    prep_off.files = {k: v for k, v in prep_v.files.items() if not k.startswith("hair_")}
    prep_off.metrics = {"triangles": rep.get("triangles")}
    rig_off = FakeVersion("rig", prep_off, vid="rig-hairrig-off")
    rig_off.params = dict(rig_v.params)
    rig_off.files = {k: v for k, v in rig_v.files.items() if k not in ("springs", "springs_found", "springs_report", "hair_bind", "anim_body")}
    rig_off.files["anim_glb"] = rig_v.files["anim_body"]
    rig_off.metrics = {k: v for k, v in rig_v.metrics.items() if k not in ("hair_bind", "body_glb_hash")}
    rig_off.metrics["springs"] = None
    exp_off = FakeVersion("export", rig_off, vid="export-hairrig-off")
    sc4 = FakeSC(settings, asset_off, project, exp_off, cache)
    print("   hand-off of the same body with today's chain (no library hair)", flush=True)
    t0 = time.monotonic()
    await export_stage.run(sc4, {"unity": do_unity})
    pr_off = json.loads(Path(sc4.file("pack_report")).read_text())
    summary["export_off"] = {"seconds": round(time.monotonic() - t0, 1), "checks": checks_of(exp_off), "dims_m": pr_off.get("dims_m"),
                             "non_manifold_edges": pr_off.get("non_manifold_edges"), "boundary_edges": pr_off.get("boundary_edges"),
                             "lods": pr_off.get("lods"), "sockets": pr_off.get("sockets"), "hair_material": exp_off.metrics.get("hair_material"),
                             "unity": {k: (exp_off.metrics.get("unity_report") or {}).get(k) for k in ("ok", "hairMaterial", "hairShader", "lodTriangles")},
                             "dir": str(sc4.dir)}
    # sockets: same places on the body with and without the hair
    sj_l, sj_o = sc3.dir / "sockets.json", sc4.dir / "sockets.json"
    if sj_l.is_file() and sj_o.is_file():
        a_ = {s["name"]: s for s in json.loads(sj_l.read_text()).get("sockets") or []}
        b_ = {s["name"]: s for s in json.loads(sj_o.read_text()).get("sockets") or []}
        diff = [float(np.linalg.norm(np.asarray(a_[n]["position"]) - np.asarray(b_[n]["position"]))) for n in a_ if n in b_ and "position" in a_[n]]
        summary["sockets_max_diff_m"] = round(max(diff), 6) if diff else None
    lib_ids = [c for c, _ in summary["export_library"]["checks"]]
    off_ids = [c for c, _ in summary["export_off"]["checks"]]
    summary["export_check_count"] = {"library": len(lib_ids), "off": len(off_ids),
                                     "only_library": sorted(set(lib_ids) - set(off_ids)), "only_off": sorted(set(off_ids) - set(lib_ids)),
                                     "failed_library": [c for c, ok in summary["export_library"]["checks"] if not ok],
                                     "failed_off": [c for c, ok in summary["export_off"]["checks"] if not ok]}
    summary["seconds"] = round(time.monotonic() - t_all, 1)
    (work / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("rig", "rig_donor", "export_check_count", "sockets_max_diff_m", "seconds") if k in summary},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
