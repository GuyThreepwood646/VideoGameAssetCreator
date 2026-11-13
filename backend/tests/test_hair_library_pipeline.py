"""Hair from the library, the pipeline lane (docs/HAIR-LIBRARY-PLAN.md section 8, lane 2).

The three steps this lane changes (Check all sides, Build the 3D model, Clean up) run here through their real code on a
real database and StageContext, with the engines stubbed: ComfyUI answers with small drawn pictures, Blender writes the
files the stage reads, the TRELLIS build and the heavy texture passes are replaced, and the library modules of lane 1
(hairstyles, baldpic, hairfit) are fakes with the plan's signatures. OFF IS UNCHANGED is proven against key sets that
were recorded from this same harness on the code before the feature (GOLDEN_OFF).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from vgac import imaging, library
from vgac.db import Asset, Project, Version, session_factory
from vgac.jobs import JobCancelled, JobContext
from vgac.stages import base as stage_base
from vgac.stages import face_detail as fd_stage
from vgac.stages import hair_detail as hd_stage
from vgac.stages import mesh as mesh_stage
from vgac.stages import prep as prep_stage
from vgac.stages import turnaround as turn_stage

S = 128  # picture size of the drawn test pictures
SKIN, HAIR, CLOTH = (225, 185, 160), (90, 55, 30), (40, 60, 140)


# --- drawn pictures -------------------------------------------------------------------------------------------------


def figure(bald: bool = False, narrow: bool = False) -> np.ndarray:
    """A standing figure on white: body, head, and brown hair on top unless bald."""
    a = np.full((S, S, 3), 255, np.uint8)
    w = 14 if narrow else 22
    a[48:120, 64 - w:64 + w] = CLOTH
    yy, xx = np.mgrid[0:S, 0:S]
    head = (yy - 34) ** 2 + (xx - 64) ** 2 <= 14 ** 2
    a[head] = SKIN
    if not bald:
        hair = head & (yy < 30) | ((yy - 30) ** 2 + (xx - 64) ** 2 <= 16 ** 2) & (yy < 28)
        a[hair] = HAIR
    return a


def save_rgb(arr: np.ndarray, p: Path) -> Path:
    Image.fromarray(arr).save(p)
    return p


def cut(rgb_path: Path, dest: Path) -> Path:
    a = np.asarray(Image.open(rgb_path).convert("RGB"))
    alpha = (np.abs(a.astype(int) - 255).sum(2) > 30).astype(np.uint8) * 255
    Image.fromarray(np.dstack([a, alpha]), "RGBA").save(dest)
    return dest


# --- the engines, stubbed -----------------------------------------------------------------------------------------


class Engines:
    """Records what the stages ask for and answers like the engines would."""

    def __init__(self) -> None:
        self.graphs: list[dict[str, Any]] = []
        self.blender_calls: list[tuple[str, list[Any]]] = []
        self.stop_on_edit = False
        self.hold_edit = False
        self.in_edit = False
        self.staged: list[Path] = []
        self.body_at_budget = False
        self.fail_prep = False
        self.fail_edit = False

    def install(self, mp: pytest.MonkeyPatch) -> None:
        eng = self

        async def stage_input(self, path, name, instance=None):
            eng.staged.append(Path(path))
            return name

        async def comfy(self, graph, instance=None, poll=1.0, message="", base=0.0, span=1.0):
            self.progress(base, message)
            if "um" in graph:  # prep making a picture bigger before painting it on (vgac/srcproject.py): not an edit;
                raise RuntimeError("no upscaler in this harness")  # the picture is then used as it is
            if "l2" not in graph:
                if eng.stop_on_edit:
                    raise JobCancelled()
                if eng.fail_edit:
                    raise RuntimeError("the image engine stopped with an error")
                eng.in_edit = True
                while eng.hold_edit:
                    self.ctx.check_cancelled()
                    await asyncio.sleep(0.02)
            eng.graphs.append(graph)
            return {"graph": graph, "outputs": {}}

        async def download_image(self, entry, node, dest, instance=None):
            g = entry["graph"]
            edit = "l2" not in g
            src = str(g["img"]["inputs"]["image"])
            bald = edit or "bald" in src
            prompt = str(g["pos"]["inputs"]["prompt"])
            side = any(w in prompt.lower() for w in ("left", "right", "side"))
            return save_rgb(figure(bald=bald, narrow=side and not edit), Path(dest))

        async def cutout(self, image_path, name, instance=None, base=0.0, span=0.1):
            dest = self.dir / f"{name}.png"
            cut(Path(image_path), dest)
            white = imaging.white_composite(dest)
            stats = imaging.alpha_bbox(dest)
            self.use_model("birefnet")
            return {"cutout": dest, "white": white, "stats": stats}

        async def blender(self, script, args, log_name=None, timeout=3600):
            eng.blender_calls.append((script, list(args)))
            if script == "render_views.py":
                views = Path(args[1])
                views.mkdir(parents=True, exist_ok=True)
                for y in range(0, 360, 15):
                    save_rgb(figure(bald=True, narrow=y % 180 != 0), views / f"shade_{y}.png")
                (views / "info.json").write_text(json.dumps({"triangles": 19000, "dims": [0.6, 0.3, 1.7], "yaws": list(range(0, 360, 15))}))
            elif script == "prep.py":
                if eng.fail_prep:
                    raise RuntimeError("Blender stopped with an error")
                out = Path(args[1])
                out.write_bytes(b"glTF-prep " + Path(args[0]).read_bytes())
                budget = int(args[args.index("--budget") + 1]) if "--budget" in args else None
                (self.dir / "prep_report.json").write_text(json.dumps({
                    "triangles": (budget or 18000) if eng.body_at_budget else min(18000, budget or 18000), "vertices": 9000, "dims_m": [0.6, 0.3, 1.7], "size_extent_m": 1.7,
                    "non_manifold_edges": 0, "boundary_edges": 0, "degenerate_faces": 0, "uv_layers": 1, "bones": 0,
                    "rebaked": False, "decimated": False, "head_triangles": 1200, "budget_seen": budget}))
            return ""

        mp.setattr(stage_base.StageContext, "stage_input", stage_input)
        mp.setattr(stage_base.StageContext, "comfy", comfy)
        mp.setattr(stage_base.StageContext, "download_image", download_image)
        mp.setattr(stage_base.StageContext, "cutout", cutout)
        mp.setattr(stage_base.StageContext, "blender", blender)

        async def fake_build(sc, name, n, seed, *a, **k):
            (sc.dir / "model.glb").write_bytes(b"glTF-model %d" % seed)
            maps = {}
            for key in ("base_color", "metallic", "roughness", "normal", "ao"):
                p = sc.dir / f"{key}.png"
                Image.new("RGB", (256, 256), (128, 128, 128)).save(p)
                maps[key] = p
            return {"seed": seed, "elapsed": 100.0, "maps": maps, "other": [], "metrics": {"normal_fix": 1, "atlas_used": 0.5},
                    "match": {"ratio": 0.9, "ok": True, "picture_luma": 150.0, "texture_luma": 135.0}}

        mp.setattr(mesh_stage, "_trellis_build", fake_build)

        async def no_upright(sc, stage, glb, normal):
            return None

        mp.setattr(mesh_stage, "upright_check", no_upright)

        def texture_pass(final_glb, bake_glb, maps, out_dir, tex, delight_mode, pbr_fix, fill=False, look=None):
            return {"metrics": {"atlas_used": 0.5, "texture_padding_px": 8, "texture_padding_target_px": 8, "bake_mesh_hash": "h",
                                "export_mesh_hash": "h", "bake_match": 1.0, "light_score": 0.1, "delight_applied": False,
                                "pbr": {"albedo_out": 0.0, "metal_mixed": 0.0}}, "files": {}}

        mp.setattr(prep_stage, "texture_pass", texture_pass)
        mp.setattr(prep_stage.skinmetal, "clear", lambda *a, **k: {"files": {}, "metrics": {"skin_metal": {"ran": True}}})
        mp.setattr(hd_stage.hair, "find_hair", lambda *a, **k: {"found": False, "reason": "too_little"})
        eng.face_sides: list[dict[str, Any]] = []

        async def face_run(sc, glb, maps, rec, params, hair_plan=None, **_kw):
            eng.face_sides.append(fd_stage.side_pictures(sc))
            return {"files": {}, "metrics": {"face_detail": {"ran": True, "skipped": None}}, "relayout": False}

        mp.setattr(fd_stage, "run", face_run)


# --- lane 1's modules, faked with the plan's signatures ------------------------------------------------------------------


FIXTURE = Path(__file__).resolve().parent / "data" / "hairlib_fixture"
CATALOGUE = json.loads((FIXTURE / "lib" / "catalogue.json").read_text(encoding="utf-8"))["styles"]
SHELL, CARDS = "q_buzzed", "mh_ponytail01"  # lane 1's two fixture styles: a shell and a card ponytail


def _fixture_counts() -> tuple[int, int]:
    """(all triangles, cap triangles) of lane 1's fixture attach of the ponytail: the cap is role 1."""
    d = np.load(FIXTURE / "hair_mesh.npz")
    tris, role = d["tris"], d["role"]
    return int(len(tris)), int((role[tris] == 1).all(1).sum())


FIT_TRIS, CAP_TRIS = _fixture_counts()  # rank and attach count hair plus cap; the catalogue counts the hair alone


def fitted(style_id: str) -> int:
    """Hair plus cap of a fitted style: the fixture's own count for the ponytail, else the catalogue's plus that cap."""
    return FIT_TRIS if style_id == CARDS else int(CATALOGUE[style_id]["triangles"]) + CAP_TRIS


def expected_allowance(budget: int = 20000) -> int:
    """The plan's rule on the fake rank's two candidates: the largest hair plus cap, up to 250, at most a quarter."""
    import math

    return min(int(math.ceil(max(fitted(SHELL), fitted(CARDS)) / 250) * 250), budget // 4)


class Lane1:
    """hairstyles on lane 1's fixture catalogue; baldpic, hairfit.rank and hairfit.attach with the plan's signatures."""

    def __init__(self, score: float = 0.77, best: str = CARDS) -> None:
        self.score = score
        self.best = best
        self.rank_calls: list[dict[str, Any]] = []
        self.attach_calls: list[dict[str, Any]] = []

    def install(self, mp: pytest.MonkeyPatch) -> None:
        from vgac import baldpic, hairfit, hairstyles

        me = self
        me.kept: list[float] = []
        me.attach_scores: dict[str, float] = {}
        # Lane 1's real loader on its fixture library, and its real picture maths; only the Blender parts are faked.
        hairstyles.use_dir(FIXTURE / "lib")
        mp.setattr(hairstyles, "use_dir", hairstyles.use_dir)  # restored below
        real_kept = baldpic.kept_measure

        def kept_measure(orig, bald, mask):
            out = real_kept(orig, bald, mask)
            return {**out, "kept_share": me.kept.pop(0)} if me.kept else out

        mp.setattr(baldpic, "kept_measure", kept_measure)
        hs = hairstyles
        self._restore = lambda: hairstyles.use_dir(None)

        async def rank(sc, glb, picture_json, bald_cutout, outline, family, yaw, out_dir):
            me.rank_calls.append({"glb": Path(glb), "picture_json": Path(picture_json), "bald_cutout": Path(bald_cutout),
                                  "outline": Path(outline), "family": family, "yaw": yaw})
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            other = SHELL if me.best == CARDS else CARDS
            cands = [{"style": me.best, "name": CATALOGUE[me.best]["name"], "score": me.score, "iou": 0.8, "eye_penalty": 0.0,
                      "params": {"volume": 1.5}, "triangles": fitted(me.best), "kind": CATALOGUE[me.best]["kind"]},
                     {"style": other, "name": CATALOGUE[other]["name"], "score": me.score - 0.1, "iou": 0.7, "eye_penalty": 0.0,
                      "params": {"volume": 1.0}, "triangles": fitted(other), "kind": CATALOGUE[other]["kind"]}]
            (out_dir / "hair_candidates.json").write_text(json.dumps(cands))
            for i in (1, 2):
                save_rgb(figure(), out_dir / f"hair_cand_{i}.png")
            return {"family": family, "family_source": "description" if family else "none", "candidates": cands,
                    "best": me.best, "best_score": me.score, "nothing_fits": me.score < 0.6, "seconds": 12.0, "error": None}

        async def attach(sc, body_glb, style_id, params, picture_json, bald_cutout, outline, cover, tex, out_dir):
            me.attach_calls.append({"body_glb": Path(body_glb), "body_bytes": Path(body_glb).read_bytes(), "style": style_id,
                                    "params": params, "cover": cover, "tex": tex, "bald_cutout": Path(bald_cutout)})
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            files = {"hair_mesh": FIXTURE / "hair_mesh.npz", "hair_parts": FIXTURE / "hair_parts.json",
                     "hair_base_color": FIXTURE / "hair_base_color.png"}
            files = {k: Path(shutil.copy2(p, out_dir / p.name)) for k, p in files.items()}
            for key, name in (("hair_glb", "hair.glb"), ("with_hair_glb", "with_hair.glb"), ("hair_fit_sheet", "hair_fit_sheet.png")):
                (out_dir / name).write_bytes(f"{style_id} {key}".encode())
                files[key] = out_dir / name
            st = CATALOGUE[style_id]
            return {"files": files, "metrics": {"attached": True, "style": style_id, "family": st["family"], "score": me.attach_scores.get(style_id, me.score),
                                                "params": params or {}, "triangles": fitted(style_id), "cap_triangles": CAP_TRIS,
                                                "skin_through": {"all": 0.01, "crown": 0.01, "sides": 0.0}, "kind": st["kind"],
                                                "alpha": st["alpha"], "two_sided": st["two_sided"], "tex": tex, "seconds": 3.0,
                                                "colour_delta": 6.0, "eye_cover": 0.0, "credit": hs.credit(style_id)}}

        mp.setattr(hairfit, "rank", rank)
        mp.setattr(hairfit, "attach", attach)


# --- a real asset and its versions ----------------------------------------------------------------------------------------


class World:
    def __init__(self, settings, prompt: str = "a knight with a short brown bob", hair: dict | None = None,
                 platform: str = "pc_mid") -> None:
        self.settings = settings
        with session_factory()() as db:
            p = Project(name="P")
            db.add(p)
            db.commit()
            a = Asset(project_id=p.id, name="knight", category="humanoid", style_preset="painted", platform_target=platform, prompt=prompt)
            if hair is not None:
                a.hair_json = json.dumps(hair)
            db.add(a)
            db.commit()
            self.aid = a.id
            v = Version(asset_id=a.id, stage="concept", status="done", selected=True)
            db.add(v)
            db.commit()
            d = library.version_dir(a, v, settings)
            img = save_rgb(figure(), d / "image.png")
            library.register_files(v, {"image": img, "cutout": cut(img, d / "cutout.png")}, settings)
            db.commit()
            self.concept = v.id

    def new(self, stage: str, parent: str | None, params: dict | None = None) -> str:
        with session_factory()() as db:
            v = Version(asset_id=self.aid, stage=stage, parent_version_id=parent, status="running")
            v.params = dict(params or {})
            db.add(v)
            db.commit()
            return v.id

    def run(self, stage: str, op: str, vid: str, params: dict | None = None) -> Version:
        mod = {"turnaround": turn_stage, "mesh": mesh_stage, "prep": prep_stage}[stage]
        ctx = JobContext(job_id="j", instance="b", payload={}, settings=self.settings, clients={}, _progress_cb=lambda f, m: None)
        with session_factory()() as db:
            v = db.get(Version, vid)
            sc = stage_base.StageContext(ctx, db, v)
            fn = getattr(mod, op)
            asyncio.run(fn(sc, dict(params or {})))
            v.status = "done"
            db.commit()
            db.refresh(v)
            return v

    def chain(self, params: dict[str, dict] | None = None) -> dict[str, Version]:
        """Turnaround, mesh and prep, each run as the autopilot runs it (op run, the payload's params only)."""
        params = params or {}
        out = {}
        parent = self.concept
        for stage in ("turnaround", "mesh", "prep"):
            vid = self.new(stage, parent, params.get(stage))
            out[stage] = self.run(stage, "run", vid, params.get(stage))
            with session_factory()() as db:
                vv = db.get(Version, vid)
                vv.selected = True
                db.commit()
            parent = vid
        return out

    def asset(self) -> Asset:
        with session_factory()() as db:
            return db.get(Asset, self.aid)


def keys(v: Version) -> dict[str, list[str]]:
    m = v.metrics
    return {"params": sorted(v.params), "files": sorted(v.files), "metrics": sorted(m),
            "checks": sorted(c.get("name") for c in m.get("checks") or [])}


# Recorded from this harness on the code before the feature (commit b71f7cd), with the asset's hair never set.
GOLDEN_OFF = {
    "mesh": {"checks": ["silhouette_iou", "texture_ao_budget", "texture_ao_pow2", "texture_ao_square",
                        "texture_base_color_budget", "texture_base_color_pow2", "texture_base_color_square",
                        "texture_matches_picture", "texture_metallic_budget", "texture_metallic_pow2",
                        "texture_metallic_square", "texture_normal_budget", "texture_normal_pow2",
                        "texture_normal_square", "texture_roughness_budget", "texture_roughness_pow2",
                        "texture_roughness_square", "triangle_budget"],
             "files": ["ao", "base_color", "glb", "input", "metallic", "normal", "preview", "roughness", "sheet",
                       "views_dir"],
             "metrics": ["atlas_used", "checks", "checks_failed", "checks_passed", "dims", "elapsed_seconds",
                         "elapsed_seconds_all", "engine", "gate_ok", "models", "normal_fix", "normal_map",
                         "picture_colour", "recipe", "seed", "silhouette_iou", "silhouette_offset", "silhouette_sign", "silhouette_turn_scores",
                         "target_faces", "target_faces_source", "texture_match", "texture_size", "texture_source",
                         "texture_tries", "texture_try_kept", "texture_try_ratios", "texture_try_seeds", "triangles",
                         "unwrap_padding_px", "views"],
             "params": ["engine", "first_seed", "head_room", "input_view", "padding", "remesh", "seed", "target_faces",
                        "target_faces_source", "tex_steps", "texture_size", "texture_source", "upsample"]},
    "prep": {"checks": ["albedo_range", "atlas_used", "bake_matches", "baked_light", "degenerate_faces",
                        "face_matches_picture", "hair_matches_picture", "manifold", "metal_mixed", "size_m",
                        "texture_padding", "triangle_budget", "uv_set", "watertight"],
             "files": ["ao", "base_color", "glb", "metallic", "normal", "report", "roughness"],
             "metrics": ["atlas_used", "bake_match", "bake_mesh_hash", "bones", "boundary_edges", "checks",
                         "checks_failed", "checks_passed", "decimated", "degenerate_faces", "delight_applied",
                         "delight_mode", "delight_source", "dims", "export_mesh_hash", "face_best_turn", "face_decided_by", "face_decision_note", "face_detail",
                         "face_outline_margin", "face_outline_turn", "face_remainder_deg", "face_tests_agree", "face_tests_differ_deg",
                         "face_toes_found", "face_toes_reason", "face_toes_turn",
                         "face_yaw_applied", "facing_source", "gate_ok", "hair_detail", "head_triangles",
                         "head_weight_reason", "head_weighted", "light_score", "models", "non_manifold_edges", "pbr",
                         "picture_colour", "rebake_reason", "rebake_seconds", "rebaked", "recipe", "shading_applied",
                         "shading_source", "size_extent_m", "size_source", "skin_metal", "texture_match",
                         "texture_padding_px", "texture_padding_target_px", "texture_size", "triangle_limit",
                         "triangle_limit_source", "triangles", "triangles_before_budget", "uv_layers", "vertices"],
             # face_closeup_guided and face_sides (the face pass's optional steps) were added later, on 2026-11-10.
             "params": ["delight", "face_closeup", "face_closeup_guided", "face_density", "face_detail", "face_sides",
                        "face_strength", "face_yaw",
                        "hair_detail", "hair_material", "hair_strength", "hand_triangles", "head_triangles", "pbr_fix", "pivot", "rebake",
                        "shading", "size_axis", "size_m", "size_word", "smooth_angle", "symmetric"]},
    "turnaround": {"checks": [],
                   "files": ["back", "back_cutout", "back_white", "front", "front_cutout", "front_white", "left",
                             "left_cutout", "left_white", "quarter", "quarter_cutout", "quarter_white", "right",
                             "right_cutout", "right_white"],
                   # turn_method (how the views were turned) was added later, on 2026-11-06, for every turnaround;
                   # cards (which cards drew the views, and why one or two) on 2026-11-13.
                   "metrics": ["addons", "cards", "consistency_ok", "cutout_in_view_graph", "flagged_views", "models", "recipe", "supplied_views",
                               "turn_method", "views", "views_passed", "views_total", "zero_negative"],
                   "params": ["auto_reroll", "lora_strength", "lora_strength_source", "seed", "supplied_views",
                              "turn_method", "views"]}}

# What library hair adds, step by step (docs/HAIR-LIBRARY-PLAN.md sections 1, 2, 5, 6 and 7).
BALD_FILES = {f"bald_{v}{s}" for v in ("front", "quarter", "quarter2", "left", "right") for s in ("", "_cutout", "_white")}
ADDED_ON = {
    "turnaround": {"params": {"bald_views", "bald_seed"}, "files": BALD_FILES | {"hair_outline_front", "hair_picture", "bald_sheet"},
                   "metrics": {"bald", "checks", "checks_passed", "checks_failed", "gate_ok"}, "checks": {"bald_picture"}},
    "mesh": {"params": {"hair_source", "head_build", "head_crop"}, "files": {"hair_candidates", "hair_cand_1", "hair_cand_2"},
             "metrics": {"hair_library", "head_build"}, "checks": {"hair_fit", "head_build"}},
    "prep": {"params": {"hair_source", "hair_style", "hair_style_source", "hair_allowance", "hair_cover"},
             "files": {"hair_mesh", "hair_parts", "hair_glb", "with_hair_glb", "hair_base_color", "hair_fit_sheet"},
             "metrics": {"hair_library", "body_glb_hash"}, "checks": {"hair_fit", "hair_skin_through", "hair_triangles"}},
}
# Texture fidelity (2026-11): the report-only measure in both steps, and the picture painted onto a character by itself.
for _stage, _part, _keys in (("turnaround", "files", ["quarter2", "quarter2_cutout", "quarter2_white"]),  # the other three-quarter view
                             ("mesh", "metrics", ["texture_fidelity"]), ("prep", "params", ["source_project_mode"]),
                             ("prep", "metrics", ["srcproject_applied", "srcproject_reason", "srcproject_upscale", "texture_fidelity"])):
    GOLDEN_OFF[_stage][_part] = sorted(GOLDEN_OFF[_stage][_part] + _keys)
# Today's checks that stand back when the model is bald on purpose.
GONE_ON = {"prep": {"checks": {"hair_matches_picture"}}}


@pytest.fixture
def engines(settings, monkeypatch):
    e = Engines()
    e.install(monkeypatch)
    return e


@pytest.fixture
def lane1(monkeypatch):
    lib = Lane1()
    lib.install(monkeypatch)
    yield lib
    lib._restore()


def check(v: Version, name: str) -> dict[str, Any]:
    return next(c for c in v.metrics.get("checks") or [] if c.get("name") == name)


def path_of(v: Version, key: str, settings) -> Path:
    return library.version_file(v, key, settings)


# --- the asset setting ----------------------------------------------------------------------------------------------------


def test_column_migration_adds_hair_json(settings, tmp_path):
    from sqlalchemy import create_engine, text

    from vgac import db

    eng = create_engine("sqlite:///" + (tmp_path / "old.sqlite").as_posix())
    db.Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE asset DROP COLUMN hair_json"))  # a database from before the feature
        assert "hair_json" not in {r[1] for r in conn.execute(text("PRAGMA table_info(asset)")).fetchall()}
    db._ensure_columns(eng)
    with eng.begin() as conn:
        assert "hair_json" in {r[1] for r in conn.execute(text("PRAGMA table_info(asset)")).fetchall()}
    assert db._ADDED_COLUMNS["asset"]["hair_json"] == "TEXT"
    eng.dispose()
    # The property round trip, and asset_to_dict.
    w = World(settings, hair={"source": "library", "pick": None})
    a = w.asset()
    assert a.hair == {"source": "library", "pick": None} and db.asset_to_dict(a, with_stages=False)["hair"]["source"] == "library"
    with session_factory()() as s:
        b = s.get(Asset, w.aid)
        b.hair = None
        s.commit()
        assert s.get(Asset, w.aid).hair_json is None


# --- OFF IS UNCHANGED -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("hair", [None, {"source": "model", "pick": None, "changed_by": "form"}])
def test_off_is_unchanged(settings, engines, lane1, hair):
    """No hair setting (or today's hair chosen): the params, files, metrics and check keys of the three steps this lane
    changes are exactly today's, nothing is drawn bald, nothing is ranked or attached."""
    w = World(settings, hair=hair)
    chain = w.chain()
    assert {s: keys(v) for s, v in chain.items()} == GOLDEN_OFF
    assert all("l2" in g for g in engines.graphs)  # no bald edit was sent
    assert not lane1.rank_calls and not lane1.attach_calls
    assert chain["prep"].metrics["hair_detail"]["skipped"] == "too_little"  # the hair pass ran as today
    assert all(set(s) == {"quarter", "quarter2", "left", "right"} and all("bald" not in str(p) for p in s.values()) for s in engines.face_sides)
    # Today's 3D input: the haired quarter view, as the autopilot runs it (no input_view in the payload).
    assert chain["mesh"].params["input_view"] == "quarter"
    assert path_of(chain["mesh"], "input", settings).read_bytes() == path_of(chain["turnaround"], "quarter_cutout", settings).read_bytes()


def test_library_adds_exactly_the_planned_keys(settings, engines, lane1):
    w = World(settings, prompt="a woman with dark brown hair in a long high ponytail", hair={"source": "library", "pick": None})
    chain = w.chain()
    for stage, v in chain.items():
        got = keys(v)
        for part in ("params", "files", "metrics", "checks"):
            expected = set(GOLDEN_OFF[stage][part]) | ADDED_ON[stage][part]
            expected -= GONE_ON.get(stage, {}).get(part, set())
            assert set(got[part]) == expected, (stage, part, set(got[part]) ^ expected)


# --- on: the bald pictures, the 3D input, the clean-up ----------------------------------------------------------------------


def test_library_chain_bald_pictures_mesh_and_prep(settings, engines, lane1):
    w = World(settings, prompt="a woman with dark brown hair in a long high ponytail", hair={"source": "library", "pick": None})
    chain = w.chain()
    turn, mesh, prep = chain["turnaround"], chain["mesh"], chain["prep"]

    # Check all sides: one bald edit of the concept with the trial's prompt plus the beard, sides from the bald front.
    edits = [g for g in engines.graphs if "l2" not in g]
    assert len(edits) == 1 and edits[0]["ks"]["inputs"]["seed"] == turn.params["bald_seed"]
    assert edits[0]["pos"]["inputs"]["prompt"] == turn_stage.BALD_PROMPT  # the trial's words; no beard is named
    bald_views = [g for g in engines.graphs if "l2" in g and "bald" in g["img"]["inputs"]["image"]]
    assert len(bald_views) == 4  # both three-quarter views, left and right
    seeds = {v: turn.metrics["views"][v]["seed"] for v in ("quarter", "quarter2", "left", "right")}
    assert sorted(g["ks"]["inputs"]["seed"] for g in bald_views) == sorted(seeds.values())  # each with its haired view's seed
    for key in BALD_FILES | {"hair_outline_front", "hair_picture", "bald_sheet"}:
        assert path_of(turn, key, settings).is_file(), key
    assert "bald" not in turn.metrics["views"] and set(turn.metrics["views"]) == {"front", "left", "back", "right", "quarter", "quarter2"}
    with Image.open(path_of(turn, "hair_outline_front", settings)) as im:
        assert im.mode == "L" and im.size == (S, S)
    pic = json.loads(path_of(turn, "hair_picture", settings).read_text())
    assert {"target", "eye_boxes", "pic_eye_cover", "colour", "outline_px"} <= set(pic)
    assert {"H_px", "top_px", "neck_px", "head_cx_px", "head_w", "head_h"} <= set(pic["target"]) and pic["outline_px"] > 0
    assert set(pic["colour"]) == {"dark", "median", "light"}
    bc = check(turn, "bald_picture")
    assert bc["passed"] and bc["value"] >= 0.97 and bc["limit"] == 0.9 and bc["severity"] == "warn"

    # Build the 3D model: from the bald quarter (the humanoid default view), audited against the bald pictures.
    assert mesh.params["hair_source"] == "library" and mesh.params["input_view"] == "quarter"
    assert path_of(mesh, "input", settings).read_bytes() == path_of(turn, "bald_quarter_cutout", settings).read_bytes()
    assert check(mesh, "silhouette_iou")["vars"]["bald"] is True
    assert set(mesh.metrics["views"]) == {"front", "left", "right"}  # no bald back
    assert len(lane1.rank_calls) == 1
    rc = lane1.rank_calls[0]
    assert rc["bald_cutout"] == path_of(turn, "bald_front_cutout", settings) and rc["family"] == "ponytail"
    assert rc["picture_json"] == path_of(turn, "hair_picture", settings) and rc["outline"] == path_of(turn, "hair_outline_front", settings)
    fit = check(mesh, "hair_fit")
    assert fit["passed"] and fit["value"] == 0.77 and fit["vars"]["step"] == "mesh" and fit["vars"]["candidates"] == 2

    # Clean up: the best style, the body decimated to the budget less the allowance, hair attached after skin metal.
    assert prep.params["hair_style"] == CARDS and prep.params["hair_style_source"] == "best"
    allow = expected_allowance()
    assert prep.params["hair_allowance"] == allow and prep.params["hair_cover"] == "normal"
    prep_call = next(a for s, a in engines.blender_calls if s == "prep.py")
    assert prep_call[prep_call.index("--budget") + 1] == 20000 - allow
    assert prep.metrics["triangle_limit"] == 20000 - allow
    assert prep.metrics["hair_detail"]["skipped"] == "library" and "hair_material" not in prep.metrics
    at = lane1.attach_calls[0]
    assert at["style"] == CARDS and at["params"] == {"volume": 1.5} and at["tex"] == 256  # min(library 256, hair texture)
    assert at["bald_cutout"] == path_of(turn, "bald_front_cutout", settings)
    hl = prep.metrics["hair_library"]
    assert hl["attached"] and hl["allowance"] == allow and hl["credit"]["licence"] == "CC0-1.0" and hl["reused_body_of"] is None
    assert prep.metrics["body_glb_hash"] == hashlib.sha1(path_of(prep, "glb", settings).read_bytes()).hexdigest()
    assert path_of(prep, "glb", settings).read_bytes() == at["body_bytes"]  # prep.glb stays the bald body
    tri = check(prep, "hair_triangles")
    assert tri["value"] == prep.metrics["triangles"] + FIT_TRIS and tri["limit"] == 20000  # attach's count has the cap in it
    assert tri["vars"] == {"hair": FIT_TRIS, "body": prep.metrics["triangles"], "allowance": allow}
    assert check(prep, "hair_skin_through")["passed"] and check(prep, "hair_fit")["vars"]["step"] == "prep"
    # The face pass paints from the concept and reads only bald side pictures.
    sides = engines.face_sides[-1]
    assert set(sides) == {"quarter", "quarter2", "left", "right"} and all(p.name.startswith("bald_") for p in sides.values())


def test_face_pass_reads_no_side_picture_when_the_bald_ones_are_missing(settings, engines, lane1):
    w = World(settings, prompt="a knight with a ponytail", hair={"source": "library", "pick": None})
    chain = w.chain()
    with session_factory()() as db:  # the bald side pictures are gone from the turnaround
        t = db.get(Version, chain["turnaround"].id)
        t.files = {k: v for k, v in t.files.items() if not (k.startswith("bald_") and k != "bald_front_cutout")}
        db.commit()
        v = db.get(Version, chain["prep"].id)
        ctx = JobContext(job_id="j", instance="host", payload={}, settings=settings, clients={}, _progress_cb=lambda f, m: None)
        sc = stage_base.StageContext(ctx, db, v)
        assert fd_stage.side_pictures(sc) == {}  # never the haired side pictures


def test_missing_bald_picture_is_a_plain_error_and_hunyuan_builds_todays_hair(settings, engines, lane1):
    w = World(settings, hair={"source": "library", "pick": None})
    # A turnaround made before the setting was turned on: no bald pictures.
    t = w.new("turnaround", w.concept, {"bald_views": False})
    w.run("turnaround", "run", t, {"bald_views": False})
    m = w.new("mesh", t)
    with pytest.raises(FileNotFoundError, match="bald quarter picture"):
        w.run("mesh", "run", m)
    # Hunyuan3D: hair_source forced to model, with a note.
    with session_factory()() as db:
        v = db.get(Version, m)
        ctx = JobContext(job_id="j", instance="a", payload={}, settings=settings, clients={}, _progress_cb=lambda f, m: None)
        sc = stage_base.StageContext(ctx, db, v)
        mesh_stage.hair_source(sc, {}, "hy3d")
        assert v.params["hair_source"] == "model" and "Hunyuan3D" in v.metrics["hair_library_note"]


def test_bald_redraw_once_keeps_the_better(settings, engines, lane1):
    w = World(settings, hair={"source": "library", "pick": None})
    lane1.kept = [0.7, 0.8]  # both tries change too much
    t = w.new("turnaround", w.concept)
    v = w.run("turnaround", "run", t)
    edits = [g["ks"]["inputs"]["seed"] for g in engines.graphs if "l2" not in g]
    assert edits == [v.params["bald_seed"], v.params["bald_seed"] + 1]
    b = v.metrics["bald"]
    assert b["rerolled"] and b["kept_share"] == 0.8 and b["kept_seed"] == v.params["bald_seed"] + 1
    c = check(v, "bald_picture")
    assert not c["passed"] and c["severity"] == "warn" and c["vars"]["reason"] == "clothes_changed" and c["vars"]["rerolled"]
    assert c["fix"]["stage"] == "turnaround" and c["fix"]["params"] == {"bald_only": True, "bald_seed": None}


def test_bald_only_adjust_keeps_every_view_and_refine_copies_bald_forward(settings, engines, lane1):
    w = World(settings, hair={"source": "library", "pick": None})
    t = w.new("turnaround", w.concept)
    first = w.run("turnaround", "run", t)
    n_views = len([g for g in engines.graphs if "l2" in g and "bald" not in g["img"]["inputs"]["image"]])
    a = w.new("turnaround", t, {**first.params, "bald_only": True, "bald_seed": None})
    adj = w.run("turnaround", "adjust", a, {"bald_only": True, "bald_seed": None})
    assert len([g for g in engines.graphs if "l2" in g and "bald" not in g["img"]["inputs"]["image"]]) == n_views  # no view redrawn
    assert adj.params["bald_seed"] != first.params["bald_seed"] and adj.params["bald_only"] is True
    assert adj.metrics["views"] == first.metrics["views"]
    for key in ("front_cutout", "quarter_cutout", "back"):
        assert path_of(adj, key, settings).read_bytes() == path_of(first, key, settings).read_bytes()
    assert path_of(adj, "bald_quarter_cutout", settings).is_file()
    # A refine of one view copies every bald file forward unchanged.
    r = w.new("turnaround", t)
    ctx = JobContext(job_id="j", instance="b", payload={}, settings=settings, clients={}, _progress_cb=lambda f, m: None)
    with session_factory()() as db:
        v = db.get(Version, r)
        asyncio.run(turn_stage.refine(stage_base.StageContext(ctx, db, v), "make the cloak red", "left", None, {}))
        db.refresh(v)
        for key in BALD_FILES:
            assert library.version_file(v, key, settings).read_bytes() == path_of(first, key, settings).read_bytes()
        assert v.metrics["bald"] == first.metrics["bald"] and v.params["bald_seed"] == first.params["bald_seed"]


def test_stop_during_the_bald_edit_keeps_the_bald_seed(settings, engines, lane1):
    w = World(settings, hair={"source": "library", "pick": None})
    t = w.new("turnaround", w.concept)
    engines.stop_on_edit = True
    with pytest.raises(JobCancelled):
        w.run("turnaround", "run", t)
    with session_factory()() as db:
        seed = db.get(Version, t).params["bald_seed"]
    assert seed
    # Resume: the job's params gain the seed the stopped run picked (jobs.RESUME_SEEDS), so the same picture is drawn.
    from vgac import jobs

    assert "bald_seed" in jobs.RESUME_SEEDS
    engines.stop_on_edit = False
    v = w.run("turnaround", "run", t, {"bald_seed": seed})
    assert v.params["bald_seed"] == seed and v.metrics["bald"]["seed"] == seed
    assert [g["ks"]["inputs"]["seed"] for g in engines.graphs if "l2" not in g] == [seed]


# --- the style Clean up uses --------------------------------------------------------------------------------------------


def test_prep_resolves_explicit_then_pick_then_best(settings, engines, lane1):
    w = World(settings, prompt="a woman with a ponytail", hair={"source": "library", "pick": None})
    chain = w.chain()
    assert chain["prep"].params["hair_style"] == CARDS and chain["prep"].params["hair_style_source"] == "best"
    with session_factory()() as db:
        from vgac import hairlib

        hairlib.set_choice(db, db.get(Asset, w.aid), pick=SHELL, changed_by="page")
    p2 = w.run("prep", "run", w.new("prep", chain["mesh"].id))
    assert p2.params["hair_style"] == SHELL and p2.params["hair_style_source"] == "picked"
    p3 = w.run("prep", "run", w.new("prep", chain["mesh"].id, {"hair_style": CARDS}), {"hair_style": CARDS})
    assert p3.params["hair_style"] == CARDS and p3.params["hair_style_source"] == "chosen"
    assert [c["style"] for c in lane1.attach_calls] == [CARDS, SHELL, CARDS]


def test_hair_only_adjust_copies_the_body_and_keeps_its_hash(settings, engines, lane1):
    w = World(settings, prompt="a woman with a ponytail", hair={"source": "library", "pick": None})
    chain = w.chain()
    parent = chain["prep"]
    n_prep = sum(1 for s, _ in engines.blender_calls if s == "prep.py")
    a = w.new("prep", parent.id, {**parent.params, "hair_style": SHELL})
    v = w.run("prep", "adjust", a, {"hair_style": SHELL})
    assert sum(1 for s, _ in engines.blender_calls if s == "prep.py") == n_prep  # the body was not made again
    assert v.metrics["body_glb_hash"] == parent.metrics["body_glb_hash"]
    assert path_of(v, "glb", settings).read_bytes() == path_of(parent, "glb", settings).read_bytes()
    assert path_of(v, "glb", settings) != path_of(parent, "glb", settings)  # a copy in its own folder
    assert lane1.attach_calls[-1]["style"] == SHELL and lane1.attach_calls[-1]["body_bytes"] == path_of(parent, "glb", settings).read_bytes()
    hl = v.metrics["hair_library"]
    assert hl["reused_body_of"] == parent.id and hl["style"] == SHELL
    assert v.params["hair_style_source"] == "chosen" and v.params["hair_allowance"] == parent.params["hair_allowance"]
    assert {"hair_fit", "hair_skin_through", "hair_triangles", "triangle_budget"} <= {c["name"] for c in v.metrics["checks"]}
    # A change beyond the hair (here the size) makes the whole Clean up again.
    b = w.new("prep", parent.id, {**parent.params, "size_m": 1.6})
    w.run("prep", "adjust", b, {"size_m": 1.6})
    assert sum(1 for s, _ in engines.blender_calls if s == "prep.py") == n_prep + 1


def test_only_hair_change_rules():
    from vgac import hairlib

    pp = {"hair_source": "library", "hair_style": "a", "hair_allowance": 2000, "size_m": 1.7}
    assert hairlib.only_hair_change(pp, {"hair_style": "b"}, 1800)
    assert hairlib.only_hair_change(pp, {"hair_cover": "more"}, 1800)
    assert not hairlib.only_hair_change(pp, {"hair_style": "b"}, 2400)  # heavier than the allowance
    assert not hairlib.only_hair_change(pp, {"hair_style": "b", "size_m": 1.6}, 1800)
    assert not hairlib.only_hair_change(pp, {}, 1800)
    assert not hairlib.only_hair_change({**pp, "hair_source": None}, {"hair_style": "b"}, 1800)
    assert hairlib.allowance(20000, {"hair_library": {"candidates": [{"triangles": 830}, {"triangles": 3100}]}}, None) == 3250
    assert hairlib.allowance(8000, {"hair_library": {"candidates": [{"triangles": 4799}]}}, None) == 2000  # capped at a quarter
    # A style not yet fitted counts its catalogue hair plus the cap margin.
    assert hairlib.catalogue_total({"triangles": 4500}) == 4500 + hairlib.CAP_MARGIN


def test_availability_reasons(settings, lane1):
    from vgac import hairlib

    def asset(**kw):
        base = {"category": "humanoid", "style_preset": "painted", "prompt": "a knight with a bob", "platform_target": "pc_mid"}
        return types.SimpleNamespace(**{**base, **kw})

    assert hairlib.available(asset()) is None
    assert "humanoid" in hairlib.available(asset(category="quadruped"))
    assert "Pixel-art" in hairlib.available(asset(style_preset="pixel"))
    assert "bald" in hairlib.available(asset(prompt="a bald monk"))
    assert "helmet" in hairlib.available(asset(prompt="a knight in a full helmet"))
    assert hairlib.available(asset(platform_target="mobile_low")) is None  # q_buzzed is a phone shell
    assert [s["id"] for s in CATALOGUE.values() if hairlib.phone_ok(s)] == [SHELL]


# --- the API and the Autopilot -------------------------------------------------------------------------------------------


REAL_EXECUTE = __import__("vgac.stages", fromlist=["execute"]).execute


@pytest.fixture
def app_client(settings, engines, lane1, monkeypatch):
    """The real app with the real Check all sides, 3D and Clean up steps (engines stubbed); concept, rig and export
    write small stand-ins, like test_api's fake_execute."""
    from fastapi.testclient import TestClient

    from vgac import stages
    from vgac.jobs import parse_kind

    async def execute(ctx, kind, payload):
        stage, op, _ = parse_kind(kind)
        if stage in ("turnaround", "mesh", "prep") and op != "judge":
            return await REAL_EXECUTE(ctx, kind, payload)
        with session_factory()() as db:
            v = db.get(Version, payload["version_id"])
            a = db.get(Asset, v.asset_id)
            d = library.version_dir(a, v, ctx.settings)
            if op == "judge":
                return
            if v.stage == "concept":
                img = save_rgb(figure(), d / "image.png")
                library.register_files(v, {"image": img, "cutout": cut(img, d / "cutout.png")}, ctx.settings)
                v.metrics = {"seed": 1}
            else:
                (d / "model.glb").write_bytes(b"glTF")
                library.register_files(v, {"glb": d / "model.glb"}, ctx.settings)
                v.metrics = {"checks": [{"name": "bones", "passed": True, "message": "ok"}]}
            db.commit()

    monkeypatch.setattr(stages, "execute", execute)
    from vgac import autopilot
    from vgac.main import app

    monkeypatch.setattr(autopilot, "POLL_SECONDS", 0.05)

    with TestClient(app) as c:
        yield c


def _until(fn, timeout: float = 30.0):
    import time

    t0 = time.time()
    while time.time() - t0 < timeout:
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def _autopilot_done(c, aid, timeout=60.0):
    return _until(lambda: (s := c.get(f"/api/assets/{aid}/autopilot").json())["status"] in ("done", "error", "paused") and s, timeout)


def _new_asset(c, prompt="a woman with dark brown hair in a long high ponytail", hair="library"):
    p = c.post("/api/projects", json={"name": "Hair"}).json()
    body = {"name": "pony", "category": "humanoid", "style_preset": "painted", "prompt": prompt}
    if hair:
        body["hair"] = hair
    return c.post(f"/api/projects/{p['id']}/assets", json=body).json()


def test_autopilot_end_to_end_with_a_poor_fit_warns_and_does_not_pause_then_the_fix_brings_todays_hair(app_client, lane1):
    c = app_client
    lane1.score = 0.55
    a = _new_asset(c)
    assert a["hair"]["source"] == "library" and a["hair"]["changed_by"] == "form"
    aid = a["id"]
    c.post(f"/api/assets/{aid}/autopilot", json={})
    st = _autopilot_done(c, aid)
    assert st["status"] == "done", st
    full = c.get(f"/api/assets/{aid}").json()
    sel = {s: next(v for v in full["stages"][s] if v["id"] == full["selected"][s]) for s in full["selected"]}
    assert set(sel) == {"concept", "turnaround", "mesh", "prep", "rig", "export"}
    fit = next(x for x in sel["mesh"]["metrics"]["checks"] if x["name"] == "hair_fit")
    assert not fit["passed"] and fit["severity"] == "warn"
    assert sel["prep"]["metrics"]["hair_library"]["attached"] and sel["prep"]["params"]["hair_style_source"] == "best"
    # The panel says nothing fits well and offers the picks.
    panel = c.get(f"/api/assets/{aid}/hair").json()
    assert panel["source"] == "library" and panel["available"] and panel["nothing_fits"] and panel["best"] == CARDS
    assert [x["style"] for x in panel["candidates"]] == [CARDS, SHELL] and panel["candidates"][0]["picture_url"].startswith("/api/library/")
    assert panel["applied"]["style"] == CARDS and panel["family"] == "ponytail"
    assert panel["allowance"] == sel["prep"]["params"]["hair_allowance"] == expected_allowance()
    assert f'"{CATALOGUE[CARDS]["name"]}"' in panel["sentence"] and CARDS not in panel["sentence"]
    # The Fix on hair_fit: today's hair comes back and the asset setting flips to model.
    fix = {k: v for k, v in fit["fix"].items() if k != "label"}
    assert fix == {"stage": "mesh", "params": {"hair_source": "model"}}
    c.post(f"/api/assets/{aid}/autopilot", json={"fix": fix})
    st = _autopilot_done(c, aid)
    assert st["status"] == "done", st
    full = c.get(f"/api/assets/{aid}").json()
    assert full["hair"]["source"] == "model" and full["hair"]["changed_by"] == "fix"
    mesh = next(v for v in full["stages"]["mesh"] if v["id"] == full["selected"]["mesh"])
    prep = next(v for v in full["stages"]["prep"] if v["id"] == full["selected"]["prep"])
    assert mesh["params"]["hair_source"] == "model" and "hair_library" not in mesh["metrics"]
    assert "hair_library" not in prep["metrics"] and "hair_style" not in prep["params"]
    assert prep["metrics"]["hair_detail"]["skipped"] != "library"
    assert c.get(f"/api/assets/{aid}/hair").json()["rebuild"] is None


def test_off_through_the_app_all_six_steps_carry_no_hair_keys(app_client, lane1, engines):
    """The autopilot over all six steps for an asset with no hair setting: no job is given a hair param, no version
    gains a hair or bald param, file or metric, and the three real steps' keys are today's."""
    c = app_client
    a = _new_asset(c, prompt="a knight with a short brown bob", hair=None)
    aid = a["id"]
    assert a["hair"] is None
    c.post(f"/api/assets/{aid}/autopilot", json={})
    assert _autopilot_done(c, aid)["status"] == "done"
    full = c.get(f"/api/assets/{aid}").json()
    new_params = set().union(*(ADDED_ON[s]["params"] for s in ADDED_ON)) | {"bald_only"}
    new_files = set().union(*(ADDED_ON[s]["files"] for s in ADDED_ON))
    for stage, versions in full["stages"].items():
        for v in versions:
            assert not new_params & set(v["params"]), stage
            assert not new_files & set(v["files"]), stage
            assert not {"hair_library", "bald", "body_glb_hash", "hair_library_note"} & set(v["metrics"]), stage
            job = c.get(f"/api/jobs/{v['job_id']}").json() if v.get("job_id") else None
            assert job is None or job["status"] == "done"
    for stage in ("turnaround", "mesh", "prep"):
        v = next(x for x in full["stages"][stage] if x["id"] == full["selected"][stage])
        assert set(v["files"]) == set(GOLDEN_OFF[stage]["files"]), stage
        assert set(v["metrics"]) == set(GOLDEN_OFF[stage]["metrics"]), stage
        assert sorted(x["name"] for x in v["metrics"].get("checks") or []) == GOLDEN_OFF[stage]["checks"], stage
    assert not lane1.rank_calls and not lane1.attach_calls and all("l2" in g for g in engines.graphs)


def test_stop_and_resume_through_the_app_keeps_the_bald_seed(app_client, engines):
    c = app_client
    a = _new_asset(c)
    aid = a["id"]
    concept = c.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    _until(lambda: c.get(f"/api/jobs/{concept['job']['id']}").json()["status"] == "done")
    engines.hold_edit = True
    out = c.post(f"/api/assets/{aid}/stages/turnaround/run", json={}).json()
    vid = out["version"]["id"]
    _until(lambda: c.get(f"/api/versions/{vid}").json()["params"].get("bald_seed") and engines.in_edit)
    seed = c.get(f"/api/versions/{vid}").json()["params"]["bald_seed"]
    c.post(f"/api/assets/{aid}/stop", json={"stage": "turnaround"})
    _until(lambda: c.get(f"/api/versions/{vid}").json()["stopped"])
    engines.hold_edit = False
    c.post(f"/api/assets/{aid}/resume", json={"stage": "turnaround"})
    _until(lambda: c.get(f"/api/versions/{vid}").json()["status"] == "done")
    v = c.get(f"/api/versions/{vid}").json()
    assert v["params"]["bald_seed"] == seed and v["metrics"]["bald"]["seed"] == seed
    assert c.get(f"/api/jobs/{out['job']['id']}").json()["status"] == "done"


def test_turning_it_on_later_queues_the_bald_pictures_and_the_pick(app_client, lane1, monkeypatch):
    c = app_client
    a = _new_asset(c, hair=None)
    aid = a["id"]
    assert a["hair"] is None
    c.post(f"/api/assets/{aid}/autopilot", json={"until_stage": "mesh"})
    assert _autopilot_done(c, aid)["status"] == "done"
    assert c.get(f"/api/assets/{aid}/hair").json()["source"] == "model"
    out = c.put(f"/api/assets/{aid}/hair", json={"source": "library"}).json()
    assert len(out["started"]) == 1
    assert out["panel"]["allowance"] is None  # the kept 3D model still has today's hair
    vid = out["started"][0]["version"]["id"]
    _until(lambda: c.get(f"/api/versions/{vid}").json()["status"] == "done")
    panel = c.get(f"/api/assets/{aid}/hair").json()
    assert panel["bald_state"] == "ready" and panel["rebuild"]["from_stage"] == "mesh"
    v = c.get(f"/api/versions/{vid}").json()
    assert v["params"]["bald_only"] is True and v["selected"] and "bald_quarter_cutout" in v["files"]
    # Rebuild from the 3D model through the autopilot's from_stage, then pick the other style and rebuild.
    c.post(f"/api/assets/{aid}/autopilot", json={"from_stage": "mesh", "until_stage": "mesh"})
    assert _autopilot_done(c, aid)["status"] == "done"
    # A bald 3D model and no Clean up yet: the allowance Clean up would keep, from the candidates' hair plus cap.
    assert c.get(f"/api/assets/{aid}/hair").json()["allowance"] == expected_allowance()
    c.post(f"/api/assets/{aid}/autopilot", json={})
    assert _autopilot_done(c, aid)["status"] == "done"
    assert c.get(f"/api/assets/{aid}/hair").json()["applied"]["style"] == CARDS
    assert c.post(f"/api/assets/{aid}/hair/pick", json={"style": "nope"}).status_code == 404
    # The new style fits worse than the old one (its hair_fit fails, as on the live bob knight 11-06): the person's
    # pick is kept all the same, and the moves and the hand-off are built on it.
    lane1.attach_scores[SHELL] = 0.5
    old_prep = c.get(f"/api/assets/{aid}").json()["selected"]["prep"]
    out = c.post(f"/api/assets/{aid}/hair/pick", json={"style": SHELL, "rebuild": True}).json()
    assert out["panel"]["pick"] == SHELL and out["autopilot"]["status"] == "running"
    assert _autopilot_done(c, aid)["status"] == "done"
    full = c.get(f"/api/assets/{aid}").json()
    prep = next(v for v in full["stages"]["prep"] if v["id"] == full["selected"]["prep"])
    assert prep["id"] != old_prep and prep["parent_version_id"] == old_prep
    assert prep["metrics"]["hair_library"]["style"] == SHELL and prep["metrics"]["hair_library"]["reused_body_of"] == old_prep
    assert "hair_fit" in prep["metrics"]["checks_failed"]
    rig = next(v for v in full["stages"]["rig"] if v["id"] == full["selected"]["rig"])
    export = next(v for v in full["stages"]["export"] if v["id"] == full["selected"]["export"])
    assert rig["parent_version_id"] == prep["id"] and export["parent_version_id"] == rig["id"]
    assert c.get(f"/api/assets/{aid}/hair").json()["applied"]["style"] == SHELL
    by_id = {v["id"]: v for v in full["stages"]["prep"]}
    assert by_id[old_prep]["status"] == "done" and not by_id[old_prep]["selected"]  # still in the history
    # Off again: nothing is rejected, and the panel offers the rebuild.
    out = c.put(f"/api/assets/{aid}/hair", json={"source": "model"}).json()
    assert out["started"] == [] and out["rebuild_from"] == "mesh"
    bald = _new_asset(c, prompt="a bald monk", hair=None)
    r = c.put(f"/api/assets/{bald['id']}/hair", json={"source": "library"})
    assert r.status_code == 409 and "bald" in r.json()["detail"]


def test_hair_library_routes(app_client, monkeypatch):
    from vgac import hairlib

    c = app_client
    monkeypatch.setattr(hairlib, "DATA_DIR", FIXTURE / "lib")
    lib = c.get("/api/hair-library").json()
    assert lib["installed"] and {s["id"] for s in lib["styles"]} == {SHELL, CARDS} and "ponytail" in lib["families"]
    shell = next(s for s in lib["styles"] if s["id"] == SHELL)
    r = c.get(shell["thumbs"]["front"])
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert c.get("/api/hair-library/files/catalogue.json").status_code == 200
    for bad in ("../README.txt", "..%2FREADME.txt", "styles/../../hair_parts.json", "nope.png"):
        assert c.get(f"/api/hair-library/files/{bad}").status_code == 404, bad


def test_variant_copies_the_hair_setting(app_client):
    c = app_client
    a = _new_asset(c)
    out = c.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    _until(lambda: c.get(f"/api/jobs/{out['job']['id']}").json()["status"] == "done")
    v = c.post(f"/api/assets/{a['id']}/variants", json={"change": "a red cloak"}).json()
    assert v["asset"]["hair"] == a["hair"] and v["asset"]["hair"]["source"] == "library"


def _refine(w, settings, parent_id, view="left"):
    r = w.new("turnaround", parent_id)
    ctx = JobContext(job_id="j", instance="b", payload={}, settings=settings, clients={}, _progress_cb=lambda f, m: None)
    with session_factory()() as db:
        v = db.get(Version, r)
        asyncio.run(turn_stage.refine(stage_base.StageContext(ctx, db, v), "make the cloak red", view, None, {}))
        v.status = "done"
        db.commit()
        db.refresh(v)
        return v


@pytest.mark.parametrize("plain", [False, True])
def test_refine_after_an_adjust_finds_the_concept_and_copies_the_bald_files(settings, engines, lane1, plain):
    """Turning the library on later makes an adjusted turnaround (bald_only); a refine of a view after it, or after a
    plain adjust, and a refine of that refine, draw from the concept picture and carry every bald file forward."""
    w = World(settings, hair={"source": "library", "pick": None})
    t = w.new("turnaround", w.concept)
    first = w.run("turnaround", "run", t)
    extra = {"seed": 7} if plain else {"bald_only": True, "bald_seed": None}
    adj = w.run("turnaround", "adjust", w.new("turnaround", t, {**first.params, **extra}), extra)
    with session_factory()() as db:
        concept_img = library.version_file(db.get(Version, w.concept), "image", settings)
    parent = adj
    for _ in range(2):  # a refine of the adjust, then a refine of that refine
        engines.staged.clear()
        ref = _refine(w, settings, parent.id)
        assert engines.staged[0] == concept_img  # the views are drawn from the concept picture
        assert ref.params["refined_view"] == "left" and ref.params["refine_of"] == parent.id and "bald_only" not in ref.params
        for key in BALD_FILES | {"hair_outline_front", "hair_picture"}:
            assert library.version_file(ref, key, settings).read_bytes() == path_of(adj, key, settings).read_bytes(), key
        assert ref.metrics["bald"] == adj.metrics["bald"]
        parent = ref


@pytest.fixture
def real_fit(settings, engines, monkeypatch):
    """Lane 1's real hairstyles (fixture library), baldpic, hairfit.rank and hairfit.attach; only Blender is faked: it
    reads the job file the real functions write and answers with rank.json and attach.json in their out_dir."""
    from vgac import hairstyles

    hairstyles.use_dir(FIXTURE / "lib")
    jobs_seen: dict[str, dict] = {}
    inner = stage_base.StageContext.blender

    async def blender(self, script, args, log_name=None, timeout=3600):
        if script != "hairfit.py":
            return await inner(self, script, args, log_name, timeout)
        mode, jp = args[0], Path(args[1])
        job = json.loads(jp.read_text(encoding="utf-8"))
        jobs_seen[mode] = job
        out = Path(job["out_dir"])
        if mode == "rank":
            cands = [{"style": st["id"], "score": 0.8 - 0.1 * i, "iou": 0.8, "eye_penalty": 0.0, "params": {"volume": 1.0},
                      "triangles": fitted(st["id"]), "files": {}} for i, st in enumerate(job["styles"])]
            (out / "rank.json").write_text(json.dumps({"candidates": cands, "ranked": len(cands), "align": {}, "timed_out": False}))
        else:
            for name in ("hair_mesh.npz", "hair_parts.json", "hair_base_color.png"):
                shutil.copy2(FIXTURE / name, out / name)
            (out / "hair.glb").write_bytes(b"glTF-hair")
            (out / "with_hair.glb").write_bytes(b"glTF-with-hair")
            (out / "attach.json").write_text(json.dumps({
                "score": {"score": 0.78, "iou": 0.8, "eye_penalty": 0.0}, "eye_cover": 0.0, "params": job.get("params") or {},
                "triangles": FIT_TRIS, "cap_triangles": CAP_TRIS, "skin_through": {"all": 0.004, "crown": 0.004, "sides": 0.0},
                "pushed_out": 0.1, "colour_delta": 7.0, "extra_height_m": 0.03, "groups": 3, "renders": {}}))
        return ""

    monkeypatch.setattr(stage_base.StageContext, "blender", blender)
    yield jobs_seen
    hairstyles.use_dir(None)


def test_real_rank_and_attach_signatures_through_the_steps(settings, engines, real_fit):
    """The 3D step and Clean up call Lane 1's real rank and attach with their real keyword names and read their real
    blocks: the files land under the plan's keys and the prep metrics keep name and extra_height_m."""
    import inspect

    from vgac import hairfit

    assert list(inspect.signature(hairfit.rank).parameters) == ["sc", "glb", "picture_json", "bald_cutout", "outline", "family", "yaw", "out_dir"]
    assert list(inspect.signature(hairfit.attach).parameters) == ["sc", "body_glb", "style_id", "params", "picture_json", "bald_cutout",
                                                                  "outline", "cover", "tex", "out_dir"]
    w = World(settings, prompt="a woman with dark brown hair in a long high ponytail", hair={"source": "library", "pick": None})
    chain = w.chain()
    mesh, prep, turn = chain["mesh"], chain["prep"], chain["turnaround"]
    hl = mesh.metrics["hair_library"]
    assert hl["error"] is None and hl["family"] == "ponytail" and hl["best"] == CARDS and hl["candidates"][0]["style"] == CARDS
    assert {"searched", "offered", "timed_out"} <= set(hl)
    cands = json.loads(path_of(mesh, "hair_candidates", settings).read_text())["candidates"]
    assert [c["style"] for c in cands] == [c["style"] for c in hl["candidates"]]
    rank_job = real_fit["rank"]
    assert rank_job["bald_cutout"] == str(path_of(turn, "bald_front_cutout", settings).resolve())
    assert rank_job["picture_json"] == str(path_of(turn, "hair_picture", settings).resolve())
    att = real_fit["attach"]
    assert att["glb"] == str(path_of(prep, "glb", settings).resolve()) and att["style"]["id"] == CARDS and att["cover"] == "normal"
    assert att["tex"] == 256 and att["params"] == {"volume": 1.0}
    m = prep.metrics["hair_library"]
    assert m["attached"] and m["name"] == CATALOGUE[CARDS]["name"] and m["extra_height_m"] == 0.03
    assert m["allowance"] == expected_allowance() and m["reused_body_of"] is None and m["credit"]["licence"] == "CC0-1.0"
    for key in ("hair_mesh", "hair_parts", "hair_glb", "with_hair_glb", "hair_base_color", "hair_normal", "hair_roughness"):
        assert path_of(prep, key, settings).is_file(), key
    sk = next(c for c in prep.metrics["checks"] if c["name"] == "hair_skin_through")
    assert sk["passed"] and sk["value"] == 0.004 and sk["vars"] == {"crown": 0.004, "sides": 0.0}
    tri = next(c for c in prep.metrics["checks"] if c["name"] == "hair_triangles")
    assert tri["vars"]["hair"] == FIT_TRIS and m["cap_triangles"] == CAP_TRIS


def test_allowance_covers_hair_and_cap_for_every_style_in_the_real_library():
    """On pc_mid (20,000 triangles) every style of the app's own library is offered and gets an allowance of at most
    5,000 and at least its hair plus the largest cap lane 1 measured (264), both before it is fitted (catalogue count
    plus the cap margin) and after (rank's fitted count)."""
    from vgac import hairlib, hairstyles, reference

    hairstyles.use_dir(None)
    styles = list(hairstyles.catalogue()["styles"].values())
    if not styles:
        pytest.skip("the app's hair library is not built in this checkout")
    budget = reference.budget("humanoid", "pc_mid")["triangles"]
    assert budget == 20000
    for st in styles:
        need = int(st["triangles"]) + 264
        sid = st["id"]
        unfitted = hairlib.allowance(budget, {}, sid)  # not ranked: catalogue count plus the cap margin
        fitted_ = hairlib.allowance(budget, {"hair_library": {"candidates": [{"style": sid, "triangles": need}]}}, sid)
        assert need <= unfitted <= 5000, sid
        assert need <= fitted_ <= 5000, sid
        assert hairlib.fits_budget(st, budget), sid


def test_fixture_ponytail_body_plus_hair_and_cap_is_within_the_budget(settings, engines, lane1):
    """The library's own best ponytail on pc_mid: the body decimated to the budget less the allowance, plus the fitted
    hair with its cap, stays within 20,000, and hair_triangles passes."""
    engines.body_at_budget = True  # the Blender stub reports the body at exactly the budget it was given
    w = World(settings, prompt="a woman with a long high ponytail", hair={"source": "library", "pick": None})
    prep = w.chain()["prep"]
    hl = prep.metrics["hair_library"]
    assert hl["style"] == CARDS and hl["triangles"] == FIT_TRIS and hl["cap_triangles"] == CAP_TRIS
    assert prep.metrics["triangles"] == 20000 - prep.params["hair_allowance"]
    assert prep.metrics["triangles"] + FIT_TRIS <= 20000
    tri = next(c for c in prep.metrics["checks"] if c["name"] == "hair_triangles")
    assert tri["passed"] and tri["value"] == prep.metrics["triangles"] + FIT_TRIS


def test_a_style_over_the_hair_share_is_not_offered_or_pickable(app_client, monkeypatch):
    from vgac import hairlib

    c = app_client
    a = _new_asset(c)
    heavy = {**CATALOGUE[CARDS], "triangles": 4700}  # 4,700 plus the 400 cap margin is over 5,000
    assert not hairlib.fits_budget(heavy, 20000) and hairlib.fits_budget(CATALOGUE[CARDS], 20000)
    from vgac import hairstyles

    real = hairstyles.style
    monkeypatch.setattr(hairstyles, "style", lambda sid: heavy if sid == CARDS else real(sid))
    r = c.post(f"/api/assets/{a['id']}/hair/pick", json={"style": CARDS})
    assert r.status_code == 409 and "lighter" in r.json()["detail"]
    assert c.post(f"/api/assets/{a['id']}/hair/pick", json={"style": SHELL}).status_code == 200


def test_the_bald_prompt_names_a_beard_only_when_the_description_has_one():
    trial = ("Make this character completely bald: remove all of the hair, including the fringe, any ponytail and any "
             "loose locks, and show a smooth, clean, hairless bald scalp with a natural rounded skull shape and both ears "
             "visible. Keep the face, eyes, expression, ears, skin tone, neck, outfit, pose, proportions, framing, art "
             "style, lighting and background exactly the same.")
    assert turn_stage.BALD_PROMPT == trial  # runtime/tmp/wigtrial/edit.py PROMPTS["bald"], word for word
    for d in ("a young woman with dark brown hair in a long high ponytail", "a knight with a red bob", None):
        p = turn_stage.bald_prompt(d)
        assert p == trial and "beard" not in p.lower() and "moustache" not in p.lower()
    for d in ("a dwarf with a long braided beard", "an old man with a grey moustache", "a bearded pirate", "a rogue with stubble"):
        assert turn_stage.bald_prompt(d) == trial + turn_stage.BALD_BEARD_SENTENCE


# --- the face inside the bald edit (live run 11-06: two women came back as bald bearded men) -------------------------

FACES = Path(__file__).resolve().parent / "data" / "hairpipe_faces"


def _face_case(name: str):
    """A head-sized crop cut from real pictures: the concept, the bald edit, the hair outline and the head box."""
    orig = np.asarray(Image.open(FACES / f"{name}_orig.png").convert("RGBA"))
    bald = np.asarray(Image.open(FACES / f"{name}_bald.png").convert("RGBA"))
    outline = np.asarray(Image.open(FACES / f"{name}_outline.png")) > 127
    return orig, bald, outline, json.loads((FACES / f"{name}.json").read_text())["target"]


@pytest.mark.parametrize("name", ["good_knight", "good_ponytail"])
def test_face_measure_passes_a_clean_bald_edit(name):
    fm = turn_stage.face_measure(*_face_case(name))
    assert fm["face_dark"] <= 0.01 and fm["face_change"] <= 7.0 and fm["lower_px"] > 500
    assert turn_stage.face_kept(fm)


@pytest.mark.parametrize("name", ["beard_bob", "beard_ponytail"])
def test_face_measure_catches_a_drawn_beard(name):
    """kept_share passed these at 0.968 and 0.986; the lower face shows the beard."""
    fm = turn_stage.face_measure(*_face_case(name))
    assert fm["face_dark"] >= 0.2 and not turn_stage.face_kept(fm)


def test_a_bearded_bald_picture_warns_as_face_changed_and_the_redraw_keeps_the_clean_one():
    bearded = {"ok": False, "reason": "face_changed", "kept_share": 0.986, "face_dark": 0.22, "face_change": 12.0}
    clean = {"ok": True, "reason": None, "kept_share": 0.95, "face_dark": 0.0, "face_change": 4.9}
    also_bearded = {"ok": False, "reason": "face_changed", "kept_share": 0.99, "face_dark": 0.29, "face_change": 10.9}
    assert max([bearded, clean], key=turn_stage._try_rank) is clean
    assert max([also_bearded, bearded], key=turn_stage._try_rank) is bearded  # the smaller beard, not the larger kept share
    c = turn_stage.bald_check({"kept_share": 0.986, "reason": "face_changed", "face_delta": 23.5, "face_dark": 0.22,
                               "face_change": 12.0, "rerolled": True})
    assert not c["passed"] and c["severity"] == "warn" and c["vars"]["reason"] == "face_changed" and c["vars"]["face_dark"] == 0.22
    assert c["fix"]["params"] == {"bald_only": True, "bald_seed": None}



# --- a Fix is the owner's decision: its result is kept, never re-ranked against older versions ----------------------------


def _kept(c, aid, stage):
    full = c.get(f"/api/assets/{aid}").json()
    return next(v for v in full["stages"][stage] if v["id"] == full["selected"][stage]), full


def _fix(c, aid, fix):
    c.post(f"/api/assets/{aid}/autopilot", json={"fix": fix})
    return _autopilot_done(c, aid)


def _library_asset_done(c):
    a = _new_asset(c)
    c.post(f"/api/assets/{a['id']}/autopilot", json={})
    assert _autopilot_done(c, a["id"])["status"] == "done"
    return a["id"]


def test_use_the_hair_built_with_the_3d_model_keeps_todays_chain_even_when_the_bald_model_passed_more(app_client, lane1):
    """The bald 3D model passes hair_fit (21 checks); the Fix's model has no hair_fit (20). Re-ranking kept the bald one
    again while the asset said "model"; now the Fix's result is kept and the setting matches the kept chain."""
    c = app_client
    lane1.score = 0.77
    aid = _library_asset_done(c)
    old_mesh, _ = _kept(c, aid, "mesh")
    assert old_mesh["params"]["hair_source"] == "library" and "hair_fit" not in old_mesh["metrics"]["checks_failed"]
    st = _fix(c, aid, {"stage": "mesh", "params": {"hair_source": "model"}})
    assert st["status"] == "done" and st["reason"] is None, st
    mesh, full = _kept(c, aid, "mesh")
    assert mesh["id"] != old_mesh["id"] and mesh["params"]["hair_source"] == "model"
    assert mesh["metrics"]["checks_passed"] < old_mesh["metrics"]["checks_passed"]
    assert {v["id"]: v for v in full["stages"]["mesh"]}[old_mesh["id"]]["status"] == "rejected"
    assert full["hair"]["source"] == "model"
    prep, _ = _kept(c, aid, "prep")
    rig, _ = _kept(c, aid, "rig")
    assert prep["parent_version_id"] == mesh["id"] and "hair_library" not in prep["metrics"] and rig["parent_version_id"] == prep["id"]


def test_fit_the_hair_again_with_more_cover_is_kept_though_it_passes_fewer_checks(app_client, lane1):
    c = app_client
    aid = _library_asset_done(c)
    old_prep, _ = _kept(c, aid, "prep")
    lane1.attach_scores[CARDS] = 0.5  # the new fit scores under 0.6: hair_fit warns on it
    st = _fix(c, aid, {"stage": "prep", "params": {"hair_cover": "more"}})
    assert st["status"] == "done", st
    prep, full = _kept(c, aid, "prep")
    assert prep["id"] != old_prep["id"] and prep["params"]["hair_cover"] == "more"
    assert prep["metrics"]["checks_passed"] < old_prep["metrics"]["checks_passed"] and "hair_fit" in prep["metrics"]["checks_failed"]
    assert prep["metrics"]["hair_library"]["reused_body_of"] == old_prep["id"]  # only the hair was fitted again
    rig, _ = _kept(c, aid, "rig")
    export, _ = _kept(c, aid, "export")
    assert rig["parent_version_id"] == prep["id"] and export["parent_version_id"] == rig["id"]
    assert c.get(f"/api/assets/{aid}/hair").json()["applied"]["prep_version_id"] == prep["id"]
    assert {v["id"]: v for v in full["stages"]["prep"]}[old_prep["id"]]["status"] == "rejected"


def test_make_the_bald_picture_again_is_kept_and_the_3d_model_is_built_from_it(app_client, lane1):
    c = app_client
    aid = _library_asset_done(c)
    old_turn, _ = _kept(c, aid, "turnaround")
    lane1.kept = [0.7, 0.75]  # both new tries change too much: bald_picture warns on the Fix's result
    st = _fix(c, aid, {"stage": "turnaround", "params": {"bald_only": True, "bald_seed": None}})
    assert st["status"] == "done", st
    turn, full = _kept(c, aid, "turnaround")
    assert turn["id"] != old_turn["id"] and turn["params"]["bald_seed"] != old_turn["params"]["bald_seed"]
    assert "bald_picture" in turn["metrics"]["checks_failed"] and not old_turn["metrics"]["checks_failed"]
    mesh, _ = _kept(c, aid, "mesh")
    assert mesh["parent_version_id"] == turn["id"] and mesh["params"]["hair_source"] == "library"
    from vgac.config import get_settings

    lib = get_settings().library_path
    assert (lib / mesh["files"]["input"]).read_bytes() == (lib / turn["files"]["bald_quarter_cutout"]).read_bytes()
    assert full["hair"]["source"] == "library"
    prep, _ = _kept(c, aid, "prep")
    assert prep["parent_version_id"] == mesh["id"] and prep["metrics"]["hair_library"]["attached"]


def test_a_fix_that_fails_to_build_falls_back_to_the_version_kept_before(app_client, engines):
    c = app_client
    aid = _library_asset_done(c)
    old_prep, _ = _kept(c, aid, "prep")
    old_rig, _ = _kept(c, aid, "rig")
    engines.fail_prep = True
    st = _fix(c, aid, {"stage": "prep", "params": {"size_m": 1.6}})
    assert st["status"] == "done", st
    assert "could not be made" in st["reason"] and "Blender stopped with an error" in st["reason"]
    prep, full = _kept(c, aid, "prep")
    assert prep["id"] == old_prep["id"] and prep["status"] == "done"
    new = [v for v in full["stages"]["prep"] if v["id"] != old_prep["id"]]
    assert len(new) == 1 and new[0]["status"] == "error" and not new[0]["selected"]
    rig, _ = _kept(c, aid, "rig")
    assert rig["parent_version_id"] == old_prep["id"] and rig["id"] != old_rig["id"]  # rebuilt on the old Clean up
    engines.fail_prep = False
    c.post(f"/api/assets/{aid}/autopilot", json={})  # a later run says nothing about the old failure
    assert _autopilot_done(c, aid)["reason"] is None


def test_a_fix_whose_result_fails_a_blocking_check_is_paused_on(app_client, lane1, monkeypatch):
    """Kept as the step's result to judge, not swapped for the older version: the autopilot pauses on it as usual."""
    from vgac import quality

    c = app_client
    aid = _library_asset_done(c)
    old_prep, _ = _kept(c, aid, "prep")
    real = quality.check

    def blocking(id_, ok, value, limit, vars=None):
        out = real(id_, ok, value, limit, vars=vars)
        if id_ == "hair_skin_through":
            out = {**out, "passed": False, "ok": False, "severity": "block"}
        return out

    monkeypatch.setattr(quality, "check", blocking)
    st = _fix(c, aid, {"stage": "prep", "params": {"hair_cover": "more"}})
    assert st["status"] == "paused" and st["stage"] == "prep" and st["version_id"] != old_prep["id"], st
    full = c.get(f"/api/assets/{aid}").json()
    assert "prep" not in full["selected"]
    assert {v["id"]: v for v in full["stages"]["prep"]}[old_prep["id"]]["status"] == "rejected"



# --- the panel's bald_state: a rebuild only once the bald pictures are ready --------------------------------------------


def test_panel_bald_state_drawing_stopped_ready_failed_and_missing(app_client, engines):
    """Turning the library on after the 3D model: while the bald pictures are being drawn, stopped or failed, the panel
    says so and offers no rebuild (the 3D step would end in the missing-file error); once they are ready it does."""
    c = app_client
    a = _new_asset(c, hair=None)
    aid = a["id"]
    c.post(f"/api/assets/{aid}/autopilot", json={"until_stage": "mesh"})
    assert _autopilot_done(c, aid)["status"] == "done"
    assert c.get(f"/api/assets/{aid}/hair").json()["bald_state"] == "none"

    # missing: the setting is on, but the kept turnaround was made without the bald pictures (nothing queued yet).
    from vgac import hairlib

    with session_factory()() as db:
        hairlib.set_choice(db, db.get(Asset, aid), "library", changed_by="page")
    p = c.get(f"/api/assets/{aid}/hair").json()
    assert p["bald_state"] == "missing" and p["rebuild"] is None and "has not been drawn" in p["sentence"]

    # drawing: PUT queues the bald pictures; held in the edit.
    engines.hold_edit = True
    out = c.put(f"/api/assets/{aid}/hair", json={"source": "library"}).json()
    vid = out["started"][0]["version"]["id"]
    assert out["rebuild_from"] is None and out["panel"]["bald_state"] == "drawing" and out["panel"]["rebuild"] is None
    _until(lambda: engines.in_edit)
    p = c.get(f"/api/assets/{aid}/hair").json()
    assert p["bald_state"] == "drawing" and p["rebuild"] is None and "is being drawn" in p["sentence"]

    # stopped: waiting for Resume.
    c.post(f"/api/assets/{aid}/stop", json={"stage": "turnaround"})
    _until(lambda: c.get(f"/api/versions/{vid}").json()["stopped"])
    p = c.get(f"/api/assets/{aid}/hair").json()
    assert p["bald_state"] == "stopped" and p["rebuild"] is None and "Resume" in p["sentence"]

    # ready: resumed and finished with the bald pictures; now the rebuild is offered.
    engines.hold_edit = False
    c.post(f"/api/assets/{aid}/resume", json={"stage": "turnaround"})
    _until(lambda: c.get(f"/api/versions/{vid}").json()["status"] == "done")
    p = c.get(f"/api/assets/{aid}/hair").json()
    assert p["bald_state"] == "ready" and p["rebuild"]["from_stage"] == "mesh"
    assert "bald picture" not in p["sentence"]

    # failed: a bald_only turnaround whose edit errors.
    engines.fail_edit = True
    with session_factory()() as db:
        aa = db.get(Asset, aid)
        kept = hairlib._selected(db, aid, "turnaround")
        files = dict(kept.files)
        for k in [k for k in files if k.startswith("bald_")]:
            files.pop(k)
        kept.files = files  # as if the kept turnaround had none, so PUT draws them again
        db.commit()
        hairlib.set_choice(db, aa, "model", changed_by="page")
    out = c.put(f"/api/assets/{aid}/hair", json={"source": "library"}).json()
    vid2 = out["started"][0]["version"]["id"]
    _until(lambda: c.get(f"/api/versions/{vid2}").json()["status"] == "error")
    p = c.get(f"/api/assets/{aid}/hair").json()
    assert p["bald_state"] == "failed" and p["rebuild"] is None and "could not be drawn" in p["sentence"]
    assert set(hairlib.BALD_STATES) == {"none", "missing", "drawing", "stopped", "failed", "ready"}


def test_triangle_budget_names_the_allowance_and_the_whole_budget_only_for_library_hair(settings, engines, lane1):
    on = World(settings, hair={"source": "library", "pick": None}).chain()["prep"]
    tb = check(on, "triangle_budget")
    assert tb["limit"] == 20000 - on.params["hair_allowance"]  # still the body against the body's limit
    assert tb["vars"]["hair_allowance"] == on.params["hair_allowance"] and tb["vars"]["platform_triangles"] == 20000
    off = World(settings).chain()["prep"]
    assert not {"hair_allowance", "platform_triangles"} & set(check(off, "triangle_budget").get("vars") or {})


def test_hair_library_route_per_platform(app_client, monkeypatch):
    from vgac import hairlib, hairstyles

    c = app_client
    monkeypatch.setattr(hairlib, "DATA_DIR", FIXTURE / "lib")
    whole = c.get("/api/hair-library").json()
    assert {s["id"] for s in whole["styles"]} == {SHELL, CARDS} and whole["platform"] is None
    assert all(s["triangles_used"] == s["triangles"] and s["lite_used"] is False for s in whole["styles"])
    phone = c.get("/api/hair-library", params={"platform": "mobile_low"}).json()
    assert [s["id"] for s in phone["styles"]] == [SHELL]  # cards are not offered on Phone
    s = phone["styles"][0]
    assert s["lite_used"] is True and s["triangles_used"] == CATALOGUE[SHELL]["lite_triangles"] and s["triangles"] == CATALOGUE[SHELL]["triangles"]
    pc = c.get("/api/hair-library", params={"platform": "pc_mid"}).json()
    assert {s["id"] for s in pc["styles"]} == {SHELL, CARDS} and not any(s["lite_used"] for s in pc["styles"])
    p = c.post("/api/projects", json={"name": "Phone"}).json()
    a = c.post(f"/api/projects/{p['id']}/assets", json={"name": "x", "category": "humanoid", "style_preset": "painted",
                                                      "platform_target": "mobile_low", "prompt": "a woman with a ponytail"}).json()
    by_asset = c.get("/api/hair-library", params={"asset_id": a["id"]}).json()
    assert by_asset["platform"] == "mobile_low" and [s["id"] for s in by_asset["styles"]] == [SHELL]
    assert c.get("/api/hair-library", params={"asset_id": "nope"}).status_code == 404
    assert hairstyles.library_dir() == FIXTURE / "lib"


# --- a bearded character: the beard is not face skin, and losing it is a change (live viking 11-06) --------------------


@pytest.mark.parametrize("name", ["beardkept_viking", "beardkept_smith"])
def test_bearded_face_measure_passes_a_kept_beard(name):
    """The live viking warned face_changed (face_dark 0.0325) although his beard was kept: measured as a bearded face
    his upper face and beard are unchanged."""
    fm = turn_stage.face_measure(*_face_case(name), bearded=True)
    assert fm["bearded"] and fm["face_dark"] is None
    assert fm["beard_change"] <= 0.25 and fm["face_change"] <= 9.0 and turn_stage.face_kept(fm)


@pytest.mark.parametrize("name", ["beardlost_ranger", "beardlost_elder"])
def test_bearded_face_measure_catches_a_lost_beard(name):
    fm = turn_stage.face_measure(*_face_case(name), bearded=True)
    assert fm["beard_change"] >= 0.45 and not turn_stage.face_kept(fm)


def test_unbearded_faces_measure_exactly_as_before():
    """The beard words switch the bearded measure on; without them the earlier measure is untouched."""
    # The crops' values from the previous round's measure (runtime/tmp/hairpipe/face_add.py, before the bearded branch).
    before = {"good_knight": (0.0, 4.53), "good_ponytail": (0.0, 4.3), "beard_bob": (0.3034, 10.62), "beard_ponytail": (0.2241, 11.46)}
    for name, (dark, change) in before.items():
        a = turn_stage.face_measure(*_face_case(name))
        b = turn_stage.face_measure(*_face_case(name), bearded=False)
        assert a == b and "beard_change" not in a
        assert a["face_dark"] == dark and a["face_change"] == change
    assert turn_stage.has_facial_hair("a broad man with a thick blond beard and long blond hair")
    assert not turn_stage.has_facial_hair("a young woman with dark brown hair in a long high ponytail")


def test_the_bald_step_measures_a_bearded_description_as_bearded(settings, engines, lane1):
    w = World(settings, prompt="a broad man with a thick blond beard and long blond hair", hair={"source": "library", "pick": None})
    t = w.run("turnaround", "run", w.new("turnaround", w.concept))
    b = t.metrics["bald"]
    assert b["bearded"] is True and b["face_dark"] is None and b["beard_change"] is not None
    edits = [g for g in engines.graphs if "l2" not in g]
    assert edits[0]["pos"]["inputs"]["prompt"] == turn_stage.BALD_PROMPT + turn_stage.BALD_BEARD_SENTENCE
    c = check(t, "bald_picture")
    assert "beard_change" in c["vars"]
    plain = World(settings, hair={"source": "library", "pick": None})
    p = plain.run("turnaround", "run", plain.new("turnaround", plain.concept))
    assert p.metrics["bald"]["bearded"] is False and p.metrics["bald"]["beard_change"] is None
