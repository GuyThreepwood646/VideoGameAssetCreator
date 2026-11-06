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

    def install(self, mp: pytest.MonkeyPatch) -> None:
        eng = self

        async def stage_input(self, path, name, instance=None):
            eng.staged.append(Path(path))
            return name

        async def comfy(self, graph, instance=None, poll=1.0, message="", base=0.0, span=1.0):
            self.progress(base, message)
            if "l2" not in graph:
                if eng.stop_on_edit:
                    raise JobCancelled()
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

        async def face_run(sc, glb, maps, rec, params, hair_plan=None):
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
            return {"files": files, "metrics": {"attached": True, "style": style_id, "family": st["family"], "score": me.score,
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
                         "picture_colour", "recipe", "seed", "silhouette_iou", "silhouette_offset", "silhouette_sign",
                         "target_faces", "target_faces_source", "texture_match", "texture_size", "texture_source",
                         "texture_tries", "texture_try_kept", "texture_try_ratios", "texture_try_seeds", "triangles",
                         "unwrap_padding_px", "views"],
             "params": ["engine", "first_seed", "head_room", "input_view", "padding", "remesh", "seed", "target_faces",
                        "target_faces_source", "texture_size", "texture_source", "upsample"]},
    "prep": {"checks": ["albedo_range", "atlas_used", "bake_matches", "baked_light", "degenerate_faces",
                        "face_matches_picture", "hair_matches_picture", "manifold", "metal_mixed", "size_m",
                        "texture_padding", "triangle_budget", "uv_set", "watertight"],
             "files": ["ao", "base_color", "glb", "metallic", "normal", "report", "roughness"],
             "metrics": ["atlas_used", "bake_match", "bake_mesh_hash", "bones", "boundary_edges", "checks",
                         "checks_failed", "checks_passed", "decimated", "degenerate_faces", "delight_applied",
                         "delight_mode", "delight_source", "dims", "export_mesh_hash", "face_best_turn", "face_detail",
                         "face_yaw_applied", "facing_source", "gate_ok", "hair_detail", "head_triangles",
                         "head_weight_reason", "head_weighted", "light_score", "models", "non_manifold_edges", "pbr",
                         "picture_colour", "rebake_reason", "rebake_seconds", "rebaked", "recipe", "shading_applied",
                         "shading_source", "size_extent_m", "size_source", "skin_metal", "texture_match",
                         "texture_padding_px", "texture_padding_target_px", "texture_size", "triangle_limit",
                         "triangle_limit_source", "triangles", "triangles_before_budget", "uv_layers", "vertices"],
             "params": ["delight", "face_closeup", "face_density", "face_detail", "face_strength", "face_yaw",
                        "hair_detail", "hair_material", "hair_strength", "head_triangles", "pbr_fix", "pivot", "rebake",
                        "shading", "size_axis", "size_m", "size_word", "smooth_angle", "symmetric"]},
    "turnaround": {"checks": [],
                   "files": ["back", "back_cutout", "back_white", "front", "front_cutout", "front_white", "left",
                             "left_cutout", "left_white", "quarter", "quarter_cutout", "quarter_white", "right",
                             "right_cutout", "right_white"],
                   "metrics": ["addons", "consistency_ok", "flagged_views", "models", "recipe", "supplied_views",
                               "views", "views_passed", "views_total"],
                   "params": ["auto_reroll", "lora_strength", "lora_strength_source", "seed", "supplied_views",
                              "views"]}}

# What library hair adds, step by step (docs/HAIR-LIBRARY-PLAN.md sections 1, 2, 5, 6 and 7).
BALD_FILES = {f"bald_{v}{s}" for v in ("front", "quarter", "left", "right") for s in ("", "_cutout", "_white")}
ADDED_ON = {
    "turnaround": {"params": {"bald_views", "bald_seed"}, "files": BALD_FILES | {"hair_outline_front", "hair_picture", "bald_sheet"},
                   "metrics": {"bald", "checks", "checks_passed", "checks_failed", "gate_ok"}, "checks": {"bald_picture"}},
    "mesh": {"params": {"hair_source"}, "files": {"hair_candidates", "hair_cand_1", "hair_cand_2"}, "metrics": {"hair_library"},
             "checks": {"hair_fit"}},
    "prep": {"params": {"hair_source", "hair_style", "hair_style_source", "hair_allowance", "hair_cover"},
             "files": {"hair_mesh", "hair_parts", "hair_glb", "with_hair_glb", "hair_base_color", "hair_fit_sheet"},
             "metrics": {"hair_library", "body_glb_hash"}, "checks": {"hair_fit", "hair_skin_through", "hair_triangles"}},
}
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
    assert all(set(s) == {"quarter", "left", "right"} and all("bald" not in str(p) for p in s.values()) for s in engines.face_sides)
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
    assert "completely bald" in edits[0]["pos"]["inputs"]["prompt"] and "beard or moustache" in edits[0]["pos"]["inputs"]["prompt"]
    bald_views = [g for g in engines.graphs if "l2" in g and "bald" in g["img"]["inputs"]["image"]]
    assert len(bald_views) == 3
    seeds = {v: turn.metrics["views"][v]["seed"] for v in ("quarter", "left", "right")}
    assert sorted(g["ks"]["inputs"]["seed"] for g in bald_views) == sorted(seeds.values())  # each with its haired view's seed
    for key in BALD_FILES | {"hair_outline_front", "hair_picture", "bald_sheet"}:
        assert path_of(turn, key, settings).is_file(), key
    assert "bald" not in turn.metrics["views"] and set(turn.metrics["views"]) == {"front", "left", "back", "right", "quarter"}
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
    assert set(sides) == {"quarter", "left", "right"} and all(p.name.startswith("bald_") for p in sides.values())


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
    assert len(out["started"]) == 1 and out["rebuild_from"] == "mesh"
    assert out["panel"]["allowance"] is None  # the kept 3D model still has today's hair
    vid = out["started"][0]["version"]["id"]
    _until(lambda: c.get(f"/api/versions/{vid}").json()["status"] == "done")
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
    out = c.post(f"/api/assets/{aid}/hair/pick", json={"style": SHELL, "rebuild": True}).json()
    assert out["panel"]["pick"] == SHELL and out["autopilot"]["status"] == "running"
    assert _autopilot_done(c, aid)["status"] == "done"
    full = c.get(f"/api/assets/{aid}").json()
    prep = next(v for v in full["stages"]["prep"] if v["id"] == full["selected"]["prep"])
    assert prep["metrics"]["hair_library"]["style"] == SHELL and prep["metrics"]["hair_library"]["reused_body_of"]
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
