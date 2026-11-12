"""Stage 3, mesh and texture (instance A).

trellis (default): TRELLIS.2 shape + texture + native post chain from one RGBA cutout, output model.glb with
base_color, metallic, roughness, normal and ao PNGs, decimated to the category/platform triangle budget.
hy3d: Hunyuan3D-2mv from the four turnaround views over white (shape only, Tencent licence recorded).
The default triangle target sits 2 percent under the budget so prep need not decimate again (G05 a). UnwrapMesh
padding is max(4, texture / 256) texels and every map is grown max(8, texture / 128) texels into the empty space
after the bake (G03); metrics record the atlas share used, the measured gutter and the UV hash of the baked mesh.
After the job: silhouette audit at 15-degree steps against the turnaround cutouts (Blender on the host).
refine with an instruction edits the picture first (TRELLIS only): the input view's cutout over white goes through the
Qwen instruction edit on the picture card (the concept card, else this job's card) with "keep everything else the same"
words after the person's, BiRefNet cuts it out, and the model is built from that cutout (files edited_picture and
edited_cutout, params picture_edit and edit_seed). A refine of an edited version edits the edited picture again; adjust
(Fine-tune) of one keeps its edited picture. A Resume keeps an edit already made. Hunyuan3D builds from all four views,
so its refine still only re-runs with a new seed and records the instruction. adjust re-runs with merged params.

Recipes (reference.recipe): when the version does not choose them, the triangle target is the recipe's share of the
budget (a quarter for low-poly, 40 percent for Retro 64) and the texture size the recipe's cap (never over the
platform's). A look without surface bumps (mesh.normal_map false) never registers the normal map the builder bakes,
and its model.glb loses its copy. The checks measure against the recipe's targets, and say so.

More triangles on the head (vgac/headtris.py): for humanoids in a look that keeps the full budget with smooth shading,
the TRELLIS target is headtris.HEAD_ROOM times the usual one (params head_room). The model is over the budget on
purpose; its triangle_budget check measures it against its own target and says why. The clean-up step decimates it back
to the budget with the head kept, and the rig step refuses a model that is still over (headtris.handoff_problem).

Colour against the picture (vgac/texcolour.py): TRELLIS sometimes predicts a near-black base colour from a bright
picture. Each build's base colour is measured against the picture; while it is under texcolour.RATIO_MIN the model is
built again with the next seed (texcolour.MAX_TRIES builds in all), the closest is kept, and its seed becomes the
version's seed (first_seed keeps the one it started from). Check texture_matches_picture (block) when even the closest
is too dark.

Hair from the library (vgac/hairlib.py, docs/HAIR-LIBRARY-PLAN.md sections 2 and 5): when the version's hair_source is
"library" (an explicit param, else the asset's setting), the model is built from the bald counterpart of its input
view (concept and front: bald_front_cutout; quarter: bald_quarter_cutout), the silhouette audit compares against the
bald front, left and right (no back) and texture_matches_picture against the picture it was built from; at the end
the library's hairstyles are ranked on the bald model (hairfit.rank) and the hair_fit check reports the best. A
missing bald picture is a plain error, never a quiet fall back to the haired one. An explicit hair_source "model" (the
hair_fit Fix) also sets the asset back to today's hair. Hunyuan3D always builds today's hair.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from .. import colourmatch, guidance, hairlib, headgraft, headtris, imaging, library, normalfix, quality, reference, texcolour, texfidelity
from ..comfy import ComfyClient
from ..graphs.hy3d import HY3D_VIEWS, hy3d_graph
from ..graphs.qwen_edit import edit_graph
from ..graphs.trellis import IMAGE_NODES, UNWRAP_PADDING_MAX, convert_graph
from ..imaging import (VIEW_YAW, atlas_used, best_alignment, contact_sheet, dilate_png, dilation_px, gutter_px, image_size,
                       load_uv_mesh, rasterise_uv, silhouette, unwrap_padding, uv_hash)
from .base import StageContext, random_seed
from .concept import stage_recipe

TEXTURE_KEYS = ("base_color", "metallic", "roughness", "normal", "ao")
# G05 (a): the mesh stage aims this far under the platform budget so prep does not have to decimate again.
BUDGET_HEADROOM = 0.98


def _silhouette_match(views_dir: Path, yaws, refs: dict[str, Path]) -> dict[str, Any]:
    """Rendered silhouettes against the reference cutouts, best yaw alignment. Pure CPU work, run in a thread."""
    rendered = {yaw: silhouette(views_dir / f"shade_{yaw}.png") for yaw in yaws}
    ref_sils = {v: silhouette(p) for v, p in refs.items()}
    return best_alignment(ref_sils, rendered)


def _input_image(sc: StageContext, input_view: str) -> Path:
    edited = _edited_cutout(sc)
    if edited is not None:
        return edited  # built from the picture its instruction edited (made from the bald view when the hair is the library's)
    if hairlib.is_library(sc):
        # Built bald on purpose: the bald counterpart of the view, and never the haired picture.
        key = "bald_front_cutout" if input_view in ("concept", "front") else f"bald_{input_view}_cutout"
        p = sc.lineage_file(key, ("turnaround",))
        if p is None:
            raise FileNotFoundError(f"Hair from the library needs the bald {key[5:-7]} picture from Check all sides, and "
                                    "it is missing. Make the bald picture again in Check all sides, or use the hair built "
                                    "with the 3D model.")
        return p
    if input_view == "concept":
        p = sc.lineage_file("cutout", ("concept",))
        if p is None:
            raise FileNotFoundError("no concept cutout in lineage")
        return p
    p = sc.lineage_file(f"{input_view}_cutout", ("turnaround",))
    if p is None:
        raise FileNotFoundError(f"no turnaround view {input_view!r} in lineage")
    return p


def default_target_faces(budget_triangles: int | None) -> int:
    """The triangle target when the version does not set one: the platform budget less a little headroom, so the weld
    and hole filling in prep do not push the model over the budget and force a second decimation (and a re-bake)."""
    if not budget_triangles:
        return 20000
    return max(500, int(int(budget_triangles) * BUDGET_HEADROOM))


# The fewest triangles a recipe's target may ask the builder for.
RECIPE_FACES_MIN = 200


def recipe_target_faces(rec: dict[str, Any], budget_triangles: int | None) -> int:
    """The triangle target from the recipe: its share of the budget (reference.recipe mesh.triangles) less the same
    headroom as the budget default; the budget default itself when the recipe keeps the full budget."""
    t = (rec.get("mesh") or {}).get("triangles")
    if not t or not budget_triangles or int(t) >= int(budget_triangles):
        return default_target_faces(budget_triangles)
    return max(RECIPE_FACES_MIN, int(int(t) * BUDGET_HEADROOM))


def resolve_targets(rec: dict[str, Any], params: dict[str, Any], budget: dict[str, Any], category_id: str | None = None) -> dict[str, Any]:
    """{target_faces, target_faces_source, texture_size, texture_source}: the version's choice ("chosen"), else the
    recipe's ("recipe"; the platform budget for looks that keep it). With category_id, when the look and category get
    more triangles on the head (headtris.applies) and the target is the recipe's, the target is headtris.HEAD_ROOM
    times larger and the result has head_room: the clean-up step decimates back to the budget with the head kept."""
    m = rec.get("mesh") or {}
    room = None
    if params.get("target_faces"):
        faces, fsrc = int(params["target_faces"]), "chosen"
    else:
        faces, fsrc = recipe_target_faces(rec, budget.get("triangles")), "recipe"
        if category_id is not None and budget.get("triangles") and headtris.applies(rec, category_id):
            faces, room = headtris.room_target(faces), headtris.HEAD_ROOM
    if params.get("texture_size"):
        tex, tsrc = int(params["texture_size"]), "chosen"
    else:
        tex, tsrc = int(m.get("texture") or budget.get("texture") or 2048), "recipe"
    out = {"target_faces": faces, "target_faces_source": fsrc, "texture_size": tex, "texture_source": tsrc}
    if room:
        out["head_room"] = room
    return out


def resolve_padding(params: dict[str, Any], tex: int) -> int:
    """UnwrapMesh padding in texels: the version's padding, else max(4, texture / 256), within the node's 1 to 16."""
    p = params.get("padding")
    # The unwrap node leaves about half its padding as real space between pieces (measured live: 8 asked, 4 found),
    # so ask for double the target; the node's own limit is 16.
    value = int(p) if p not in (None, "") else 2 * unwrap_padding(tex)
    return max(1, min(UNWRAP_PADDING_MAX, value))


def finish_maps(glb: Path, maps: dict[str, Path], tex: int) -> dict[str, Any]:
    """G03 after the bake: grow every map max(8, size / 128) texels into the empty texture space (nearest colour), and
    measure the layout: share of the atlas used, gutter between pieces, and the UV hash of the mesh the maps were
    baked on. Pure CPU work, run in a thread."""
    uvm = load_uv_mesh(glb)
    if uvm is None:
        return {}
    masks: dict[int, Any] = {}
    grown = {}
    for key, path in maps.items():
        size = image_size(path)[0]
        if size not in masks:
            masks[size] = rasterise_uv(uvm, size)["labels"]
        px = dilation_px(size)
        dilate_png(path, masks[size] > 0, px)
        grown[key] = px
    labels = masks.get(tex) if tex in masks else rasterise_uv(uvm, tex)["labels"]
    pad = unwrap_padding(tex)
    return {"atlas_used": round(atlas_used(labels), 4), "texture_padding_px": gutter_px(labels, pad // 2 + 2),
            "dilated_px": grown, "bake_mesh_hash": uv_hash(uvm)}


DEFAULT_TEX_STEPS = 24  # the texture sampler's steps (graphs/trellis.py); 12 on request (Adjust)


async def _trellis(sc: StageContext, params: dict[str, Any]) -> None:
    input_view = params.get("input_view") or sc.category.get("input_view_default") or "concept"
    rec = stage_recipe(sc)
    # The TRELLIS builder only: more triangles than the budget, on purpose, for the clean-up step's weighted decimation
    targets = resolve_targets(rec, params, sc.budget, sc.asset.category)
    target_faces, tex = targets["target_faces"], targets["texture_size"]
    keep_normal = bool(rec["mesh"]["normal_map"])
    crisp = rec["surface"]["texture_filter"] == "crisp"
    upsample = int(params.get("upsample") or rec["mesh"].get("upsample") or 1024)
    # Looks that change colour on purpose (crisp, a palette) keep the colours as built; every other look is matched to the picture
    match_look = rec["surface"]["texture_filter"] != "crisp" and not rec["surface"].get("palette_colors")
    tex_steps = int(params.get("tex_steps") or DEFAULT_TEX_STEPS)
    # The first build's seed: the one given (the person's, or a Resume's), else a new one. Builds again after a too-dark
    # base colour take the seeds that follow from it (texcolour.try_seed); the kept build's seed replaces it at the end.
    first_seed = int(params.get("seed") or random_seed())
    remesh = bool(sc.category.get("remesh_default", True)) if params.get("remesh") is None else bool(params["remesh"])
    padding = resolve_padding(params, tex)
    if params.get("hand_edit") and sc.asset.category == "humanoid" and sc.file(EDITED_KEY) is None:
        # The picture is edited once so the hands are open and apart (phase 0 of docs/HANDS-PLAN.md); a Resume keeps the edit.
        await _edit_picture(sc, HAND_EDIT, _input_image(sc, input_view), None)
        sc.set_params(hand_edit=True)
    src = _input_image(sc, input_view)
    sc.set_params(engine="trellis", input_view=input_view, target_faces=target_faces, texture_size=tex, upsample=upsample, tex_steps=tex_steps, seed=first_seed, remesh=remesh,
                  padding=padding, target_faces_source=targets["target_faces_source"], texture_source=targets["texture_source"],
                  head_room=targets.get("head_room"), first_seed=None)
    sc.use_model("trellis2")
    name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_mesh_in.png")
    sc.register("input", sc.copy_in(src, "input_image.png"))
    picture = None
    if sc.asset.category != "material":
        try:
            picture = await asyncio.to_thread(texcolour.picture_stats, src)
        except Exception as e:  # noqa: BLE001  (no comparison: the model is built once, as before)
            sc.set_metrics(texture_match_error=str(e)[:300])
    tries_dir = sc.dir / TRIES_DIR
    shutil.rmtree(tries_dir, ignore_errors=True)  # a Resume starts the tries again from the first

    async def build_once(seed: int, n: int) -> dict[str, Any]:
        return await _trellis_build(sc, name, n, seed, target_faces, tex, upsample, remesh, padding, keep_normal, crisp, picture,
                                    tex_steps=tex_steps, match_to=src if match_look else None)

    def on_retry(n: int, last: dict[str, Any]) -> None:
        sc.progress(0.05, f"The colours came out too dark against the picture, so the 3D model is being built again "
                          f"(build {n + 1} of {texcolour.MAX_TRIES})")

    best, tries = await texcolour.build_with_rerolls(first_seed, picture is not None, build_once,
                                                     lambda n: _keep_aside(sc.dir, tries_dir / f"try{n + 1}"),
                                                     lambda n: _bring_back(tries_dir / f"try{n + 1}", sc.dir), on_retry)
    shutil.rmtree(tries_dir, ignore_errors=True)
    kept = tries[best]
    for key in ("glb", *kept["maps"].keys(), *kept["other"]):
        p = sc.dir / ("model.glb" if key == "glb" else f"{key}.png")
        if p.is_file():
            sc.register(key, p)
    # The seed that made the kept build, so a re-run or a Fine-tune from it makes the same model; first_seed when it
    # took more than one build.
    sc.set_params(seed=kept["seed"], first_seed=first_seed if kept["seed"] != first_seed else None)
    sc.set_metrics(**kept["metrics"])
    sc.set_metrics(engine="trellis", seed=kept["seed"], elapsed_seconds=kept["elapsed"], target_faces=target_faces, texture_size=tex,
                   unwrap_padding_px=padding, target_faces_source=targets["target_faces_source"], texture_source=targets["texture_source"],
                   normal_map=keep_normal, recipe=reference.recipe_record(rec, "mesh", "surface"),
                   elapsed_seconds_all=round(sum(float(t["elapsed"] or 0) for t in tries), 1))
    if picture is not None:
        sc.set_metrics(picture_colour=picture, **texcolour.record(best, tries))
    if headgraft.wanted(sc.asset.category, params, hairlib.is_library(sc), "trellis"):
        await _head_build(sc, params, src, int(kept["seed"]), target_faces, tex, upsample, remesh, padding, keep_normal, crisp, tex_steps)


# --- The head built on its own (vgac/headgraft.py, docs/HEAD-BUILD-PLAN.md) -------------------------------------
# On the bald route for people, a second build from a head-and-shoulders crop of the bald picture gives the face real
# brows, eyes, nose and lips; it is joined to the body at the neck and the two textures are baked into one
# (blender/headjoin.py), so the model that leaves this step is one mesh with one texture, as before. Any failure keeps
# the body's own head (files as built) and records why; the body build is kept in body/ when the join is used.
HEAD_DIR = "head"
BODY_DIR = "body"


async def _head_build(sc: StageContext, params: dict[str, Any], src: Path, seed: int, target_faces: int, tex: int, upsample: int,
                      remesh: bool, padding: int, keep_normal: bool, crisp: bool, tex_steps: int = DEFAULT_TEX_STEPS) -> None:
    from ..jobs import JobCancelled

    t0 = time.time()
    share = headgraft.crop_share(params)
    sc.set_params(head_build=True, head_crop=share)
    rec: dict[str, Any] = {"wanted": True, "used": False, "reason": None}
    hdir = sc.dir / HEAD_DIR
    shutil.rmtree(hdir, ignore_errors=True)
    hdir.mkdir(parents=True, exist_ok=True)
    try:
        sc.progress(0.76, "Building the head on its own")
        try:
            rec["crop"] = await asyncio.to_thread(headgraft.crop, src, hdir / "head_input.png", share)
        except ValueError:
            rec["reason"] = "no_picture"
            return
        name = await sc.stage_input(hdir / "head_input.png", f"vgac_{sc.version.id[:8]}_head_in.png")
        head_faces = max(2000, int(target_faces * headgraft.HEAD_FACES_SHARE))
        built = None
        tries: list[dict[str, Any]] = []
        for n, s in enumerate((seed, texcolour.try_seed(seed, 1))):
            res = await _trellis_build(sc, name, n, s, head_faces, tex, upsample, remesh, padding, keep_normal, crisp, None,
                                       dest=hdir, tag="head", tex_steps=tex_steps)
            ratio = await asyncio.to_thread(headgraft.depth_ratio, hdir / "model.glb")
            tries.append({"seed": s, "depth_ratio": ratio, "elapsed": res.get("elapsed")})
            if ratio is not None and ratio >= headgraft.FLAT_RATIO:
                built = res
                rec["seed"] = s
                break
        rec["tries"] = tries
        if built is None:
            rec["reason"] = "flat"
            return
        sc.progress(0.79, "Joining the head to the body")
        body_maps = {k: sc.dir / f"{k}.png" for k in TEXTURE_KEYS if (sc.dir / f"{k}.png").is_file()}
        g = await asyncio.to_thread(headgraft.graft, sc.dir / "model.glb", body_maps, hdir / "model.glb", built["maps"], hdir / "graft")
        rec.update({k: v for k, v in g["metrics"].items() if not k.endswith("_rows")})
        if not g["ok"]:
            rec["reason"] = g["reason"]
            return
        mj = hdir / "graft" / "graft_maps.json"
        mj.write_text(json.dumps({k: str(Path(p).resolve()) for k, p in g["maps"].items()}), encoding="utf-8")
        joined = hdir / "joined"
        joined.mkdir(parents=True, exist_ok=True)
        await sc.blender("headjoin.py", [g["glb"], joined / "model.glb", "--maps", mj, "--tex", tex, "--padding", unwrap_padding(tex),
                                         "--margin", dilation_px(tex)], log_name="headjoin.log", timeout=sc.settings.blender_timeout_seconds)
        if not (joined / "model.glb").is_file() or not (joined / "base_color.png").is_file():
            rec["reason"] = "join"
            return
        # The body build stays beside it (body/), and the joined model takes its place under the same names.
        bdir = sc.dir / BODY_DIR
        shutil.rmtree(bdir, ignore_errors=True)
        bdir.mkdir(parents=True, exist_ok=True)
        for fname in BUILD_FILES:
            p = sc.dir / fname
            if p.is_file():
                shutil.copy2(p, bdir / fname)
        shutil.copy2(joined / "model.glb", sc.dir / "model.glb")
        maps_now: dict[str, Path] = {}
        for key in body_maps:
            jp = joined / f"{key}.png"
            if jp.is_file():
                shutil.copy2(jp, sc.dir / f"{key}.png")
                maps_now[key] = sc.dir / f"{key}.png"
        if not keep_normal or crisp:
            imaging.glb_replace_images(sc.dir / "model.glb", drop_normal=not keep_normal, nearest=crisp)
        try:
            sc.set_metrics(**await asyncio.to_thread(finish_maps, sc.dir / "model.glb", maps_now, tex))
        except Exception as e:  # noqa: BLE001  (the maps stay as baked)
            sc.set_metrics(dilate_error=str(e)[:300])
        for key in ("glb", *maps_now):
            sc.register(key, sc.dir / ("model.glb" if key == "glb" else f"{key}.png"))
        sc.register("body_glb", bdir / "model.glb")
        sc.register("head_glb", hdir / "model.glb")
        sc.register("head_input", hdir / "head_input.png")
        rec["used"] = True
    except JobCancelled:
        raise
    except Exception as e:  # noqa: BLE001  (the body's own head stays: the model as built)
        rec["reason"] = "error"
        rec["error"] = str(e)[:300]
    finally:
        rec["seconds"] = round(time.time() - t0, 1)
        sc.set_metrics(head_build=rec)


# Where the 3D step keeps an earlier build while it builds again (texcolour.build_with_rerolls); removed afterwards.
TRIES_DIR = "tries"
# The files one build writes in the version folder, besides the maps.
BUILD_FILES = ("model.glb", *(f"{k}.png" for k in IMAGE_NODES.values()))


def _keep_aside(src_dir: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in BUILD_FILES:
        p = src_dir / name
        if p.is_file():
            shutil.move(str(p), str(dest / name))


def _bring_back(src: Path, dest_dir: Path) -> None:
    for name in BUILD_FILES:
        (dest_dir / name).unlink(missing_ok=True)
        p = src / name
        if p.is_file():
            shutil.copy2(p, dest_dir / name)


async def _trellis_build(sc: StageContext, name: str, n: int, seed: int, target_faces: int, tex: int, upsample: int, remesh: bool,
                         padding: int, keep_normal: bool, crisp: bool, picture: dict[str, Any] | None,
                         dest: Path | None = None, tag: str = "", tex_steps: int = DEFAULT_TEX_STEPS,
                         match_to: Path | None = None) -> dict[str, Any]:
    """One TRELLIS.2 build into the version folder (or `dest`): model.glb and the maps, normal map flipped, maps grown
    into the seams, and the base colour measured against the picture. `tag` names a second build of the same version
    (the head built on its own). `match_to` is the picture the base colour is shifted towards afterwards
    (colourmatch.py; None for the head build, looks that change colour on purpose and anything without a picture). Returns {seed, elapsed, maps, other, metrics, match}."""
    out = Path(dest) if dest is not None else sc.dir
    if tag:
        prefix = sc.prefix(tag if n == 0 else f"{tag}{n + 1}")
        message = "TRELLIS.2 convert, the head on its own" + ("" if n == 0 else f", build {n + 1}")
        base, span = 0.76, 0.02
    else:
        prefix = sc.prefix() if n == 0 else sc.prefix(f"try{n + 1}")
        message = "TRELLIS.2 convert" if n == 0 else f"TRELLIS.2 convert, build {n + 1}"
        base, span = 0.05, 0.7
    entry = await sc.comfy(convert_graph(name, prefix, target_faces, tex, upsample, seed, remesh, padding=padding, tex_steps=tex_steps),
                           poll=5.0, message=message, base=base, span=span)
    glb = await sc.download_mesh(entry, out / "model.glb")
    if not keep_normal or crisp:
        # The recipe's look: no bump map (the GLB's own copy goes too); crisp textures sampled nearest-neighbour.
        imaging.glb_replace_images(glb, drop_normal=not keep_normal, nearest=crisp)
    maps: dict[str, Path] = {}
    other: list[str] = []
    for node, key in IMAGE_NODES.items():
        if key == "normal" and not keep_normal:
            continue  # the builder always bakes one; a look without surface bumps never registers or ships it
        imgs = ComfyClient.images_of(entry, node)
        if imgs:
            p = await sc.client().download_output(imgs[0], out / f"{key}.png")
            if key in TEXTURE_KEYS:
                maps[key] = Path(p)
            else:
                other.append(key)
    metrics: dict[str, Any] = {}
    if keep_normal:
        # The builder writes the normal map's green channel upside down (vgac/normalfix.py): flipped once, here, in
        # normal.png and in the GLB's own copy, and the version marked so the clean-up step never flips it again.
        metrics.update(await asyncio.to_thread(normalfix.fix_download, Path(glb), maps.get("normal")))
    try:
        sc.progress(0.76, "Growing the textures into the seams")
        metrics.update(await asyncio.to_thread(finish_maps, Path(glb), maps, tex))
    except Exception as e:  # the maps stay as baked; the prep checks still measure the layout
        metrics["dilate_error"] = str(e)[:300]
    if match_to is not None and maps.get("base_color") is not None:
        try:
            sc.progress(0.77, "Matching the colours to the picture")
            metrics["colour_match"] = await asyncio.to_thread(colourmatch.match_base_colour, Path(glb), Path(match_to), Path(maps["base_color"]))
        except Exception as e:  # noqa: BLE001  (not matched: the base colour stays as built)
            metrics["colour_match"] = {"applied": False, "reason": "error", "error": str(e)[:300]}
    match = None
    if picture is not None:
        try:
            match = texcolour.compare(picture, await asyncio.to_thread(texcolour.texture_stats, maps.get("base_color"), Path(glb)))
        except Exception as e:  # noqa: BLE001  (not measured: kept as it is, like a build that passes)
            metrics["texture_match_error"] = str(e)[:300]
    return {"seed": seed, "elapsed": entry.get("elapsed_seconds"), "maps": maps, "other": other, "metrics": metrics, "match": match}


async def _hy3d(sc: StageContext, params: dict[str, Any]) -> None:
    seed = int(params.get("seed") or random_seed())
    names = {}
    for v in HY3D_VIEWS:
        p = sc.lineage_file(f"{v}_white", ("turnaround",))
        if p is None:
            raise FileNotFoundError(f"hy3d needs turnaround view {v} over white")
        names[v] = await sc.stage_input(p, f"vgac_{sc.version.id[:8]}_hy_{v}.png")
    sc.set_params(engine="hy3d", seed=seed, steps=int(params.get("steps") or 20), cfg=float(params.get("cfg") or 7.5))
    sc.use_model("hy3d_mv")
    entry = await sc.comfy(hy3d_graph(names, sc.prefix("model"), steps=int(params.get("steps") or 20), cfg=float(params.get("cfg") or 7.5), seed=seed),
                           poll=5.0, message="Hunyuan3D-2mv", base=0.05, span=0.7)
    glb = await sc.download_mesh(entry, sc.dir / "model.glb")
    sc.register("glb", glb)
    sc.set_metrics(engine="hy3d", seed=seed, elapsed_seconds=entry.get("elapsed_seconds"), textured=False)


async def audit(sc: StageContext) -> None:
    """Silhouette IoU against the turnaround cutouts; records metrics.silhouette_iou. Never fails the job."""
    glb = sc.file("glb")
    if glb is None:
        return
    refs = {}
    bald = hairlib.is_library(sc)
    for v in VIEW_YAW:
        if bald and v == "back":
            continue  # no bald back picture is drawn
        p = sc.lineage_file(f"bald_{v}_cutout" if bald else f"{v}_cutout", ("turnaround",))
        if p is not None:
            refs[v] = p
    try:
        sc.progress(0.8, "silhouette audit render")
        views_dir = sc.dir / "views"
        await sc.blender("render_views.py", [glb, views_dir, 256, 15, "shade"], log_name="render_views.log", timeout=sc.settings.blender_timeout_seconds)
        info = json.loads((views_dir / "info.json").read_text())
        sc.set_metrics(triangles=info.get("triangles"), dims=info.get("dims"))
        sc.register("views_dir", views_dir / "info.json")
        if refs:
            best = await asyncio.to_thread(_silhouette_match, views_dir, info.get("yaws", range(0, 360, 15)), refs)
            sc.set_metrics(silhouette_iou=best["mean"], views={v: {"silhouette_iou": x} for v, x in best["views"].items()},
                           silhouette_offset=best["offset"], silhouette_sign=best["sign"])
        front = views_dir / "shade_0.png"
        if front.is_file():
            sc.register("preview", front)
        sheet_paths = [views_dir / f"shade_{y}.png" for y in range(0, 360, 45) if (views_dir / f"shade_{y}.png").is_file()]
        if sheet_paths:
            sheet = await asyncio.to_thread(contact_sheet, sheet_paths, sc.dir / "sheet.png", cell=256, columns=4, labels=[p.stem for p in sheet_paths])
            sc.register("sheet", sheet)
    except Exception as e:  # Blender missing or render failed: keep the mesh, record the problem
        sc.set_metrics(audit_error=str(e)[:500])
    if hairlib.is_library(sc):
        await rank_hair(sc, glb)
    await judge(sc)


HAIR_RANK_DIR = "hair_rank"
HAIR_CANDIDATES_MAX = 4


async def rank_hair(sc: StageContext, glb: Path) -> None:
    """Hair from the library: the hairstyles ranked on the bald model (hairfit.rank, Blender on the host), turned by
    the audit's best turn. Files hair_candidates and hair_cand_1 to 4; metrics.hair_library. Never fails the job."""
    from ..jobs import JobCancelled

    m = sc.version.metrics or {}
    try:
        from .. import hairfit, hairstyles  # lane 1

        family, words = hairstyles.family_of(sc.asset.prompt or "")
        need = {k: sc.lineage_file(k, ("turnaround",)) for k in ("hair_picture", "bald_front_cutout", "hair_outline_front")}
        missing = [k for k, p in need.items() if p is None]
        if missing:
            raise FileNotFoundError("the bald pictures from Check all sides are missing: " + ", ".join(missing))
        best = None
        floor = float(sc.category.get("silhouette_min") or DEFAULT_SILHOUETTE_MIN)
        if m.get("silhouette_offset") is not None and m.get("silhouette_iou") is not None and float(m["silhouette_iou"]) >= floor:
            best = quality.best_turn(m["silhouette_offset"], int(m.get("silhouette_sign") or 1))
        yaw = quality.auto_turn(best)
        sc.progress(0.9, "Trying the hairstyles from the library on the bald model")
        out_dir = sc.dir / HAIR_RANK_DIR
        hl = dict(await hairfit.rank(sc, Path(glb), need["hair_picture"], need["bald_front_cutout"], need["hair_outline_front"],
                                     family, yaw, out_dir) or {})
        hl.setdefault("family", family)
        hl.setdefault("family_words", list(words or []))
        hl["yaw"] = yaw
        cj = out_dir / "hair_candidates.json"
        if cj.is_file():
            sc.register("hair_candidates", cj)
        for i in range(1, HAIR_CANDIDATES_MAX + 1):
            png = out_dir / f"hair_cand_{i}.png"
            if png.is_file():
                sc.register(f"hair_cand_{i}", png)
    except JobCancelled:
        raise
    except Exception as e:  # noqa: BLE001  (the 3D model stands; the pick can still be made by hand later)
        hl = {"error": str(e)[:300], "candidates": [], "best": None, "best_score": None, "nothing_fits": True}
    sc.set_metrics(hair_library=hl)


DEFAULT_SILHOUETTE_MIN = 0.6


def look_vars(rec: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    """Placeholder values for checks measured against the recipe's own target instead of the platform budget."""
    known = {s["id"]: s for s in reference.styles()}
    look = (known.get(rec.get("style")) or {}).get("look") or rec.get("style")
    return {"look": look, "platform_triangles": budget.get("triangles"), "platform_texture": budget.get("texture")}


def triangle_target(rec: dict[str, Any], params: dict[str, Any], budget: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    """The triangle limit a check measures against, and the check's vars: the version's own target when it was built
    over the budget on purpose for the head (headtris; vars head_room, platform_triangles); the recipe's target when
    the look asks for fewer triangles than the platform allows (and the version did not choose more), else the
    platform budget and no vars."""
    platform = budget.get("triangles")
    room = headtris.mesh_limit(params, platform)
    if room:
        return room, {"head_room": True, "platform_triangles": platform}
    target = (rec.get("mesh") or {}).get("triangles")
    chosen = params.get("target_faces_source") == "chosen" or (params.get("target_faces") and "target_faces_source" not in params)
    if chosen and int(params.get("target_faces") or 0) > int(target or 0):
        return platform, None
    if target and platform and int(target) < int(platform):
        return int(target), {**look_vars(rec, budget), "recipe": True}
    return platform, None


def texture_target(rec: dict[str, Any], budget: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    """The texture size limit a check measures against: the recipe's when smaller than the platform's."""
    platform = budget.get("texture")
    tex = (rec.get("mesh") or {}).get("texture")
    if tex and platform and int(tex) < int(platform):
        return int(tex), {**look_vars(rec, budget), "recipe": True}
    return platform, None


def with_vars(checks: list[dict[str, Any]], vars: dict[str, Any] | None, suffix: str) -> list[dict[str, Any]]:
    """Add vars to the checks whose name ends with suffix."""
    if vars:
        for c in checks:
            if str(c.get("name") or c.get("id") or "").endswith(suffix):
                c["vars"] = {**(c.get("vars") or {}), **vars}
                c.update(guidance.describe_check(c))  # the sentence again, with the new values
    return checks


async def judge(sc: StageContext) -> None:
    rec = stage_recipe(sc)
    tex_limit, tex_vars = texture_target(rec, sc.budget)
    checks = with_vars(quality.texture_checks({k: library.version_file(sc.version, k, sc.settings) for k in TEXTURE_KEYS}, tex_limit),
                       tex_vars, "_budget")
    tris = sc.version.metrics.get("triangles")
    limit, tvars = triangle_target(rec, sc.version.params, sc.budget)
    if tris is not None and limit:
        checks.append(quality.check("triangle_budget", tris <= limit * (1 + quality.TRIANGLE_BUDGET_TOLERANCE), tris, limit, vars=tvars))
    iou = sc.version.metrics.get("silhouette_iou")
    if iou is not None:
        # Long-bodied categories (quadruped, serpent, winged) never match a flat concept as closely as a biped does,
        # so each category may lower the floor (silhouette_min in its yaml); 0.6 is the biped default.
        floor = float(sc.category.get("silhouette_min") or DEFAULT_SILHOUETTE_MIN)
        bald = {"bald": True} if hairlib.is_library(sc) else None
        checks.append(quality.check("silhouette_iou", iou >= floor, iou, floor, vars=bald))
    nc = await upright_check(sc, "mesh", sc.file("glb"), sc.file("normal"))
    if nc:
        checks.append(nc)
    tc = await colour_check(sc)
    if tc:
        checks.append(tc)
    checks += await fidelity_checks(sc, rec, (sc.version.params or {}).get("input_view"))
    if hairlib.is_library(sc):
        hc = hairlib.fit_check(sc.version.metrics, "mesh")
        if hc:
            checks.append(hc)
    hb = head_build_check(sc.version.metrics)
    if hb:
        checks.append(hb)
    sc.set_metrics(**quality.summarise(checks))


def head_build_check(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """The head_build check (warn): passes when the head built on its own was joined; fails with the reason the body's
    own head was kept. None when the head was not built on its own."""
    hb = (metrics or {}).get("head_build")
    if not hb or not hb.get("wanted"):
        return None
    used = bool(hb.get("used"))
    reason = hb.get("reason")
    return quality.check("head_build", used, "joined" if used else (reason or "error"), "joined",
                         vars={"reason": reason, "reason_words": headgraft.REASONS.get(reason or "error", headgraft.REASONS["error"])})


async def fidelity_checks(sc: StageContext, rec: dict[str, Any], input_view: str | None) -> list[dict[str, Any]]:
    """texture_sharp and texture_colour_matches_picture (vgac/texfidelity.py), report-only: the model's flat colour from
    the picture's side against the picture, measured once and kept in the metrics (texture_fidelity). Nothing for
    materials, models without a base colour, and looks that change the colours or their sharpness on purpose (crisp,
    limited palette). Used by the 3D step and by prep. Never fails the job."""
    srf = rec.get("surface") or {}
    if sc.asset.category == "material" or srf.get("texture_filter") == "crisp" or srf.get("palette_colors"):
        return []
    glb, base = sc.file("glb"), sc.file("base_color")
    if glb is None or base is None:
        return []
    if "texture_fidelity" not in (sc.version.metrics or {}):
        try:
            # Against the original picture (what the texture should look like), not the view drawn from it that the
            # model was built from; the built-from picture when the original has hair the bald model lacks, or shows
            # what an edit removed.
            mv = sc.version if sc.version.stage == "mesh" else sc.stage_version("mesh")
            orig = None
            if not hairlib.is_library(sc) and not (mv is not None and (mv.params or {}).get("picture_edit")):
                orig = sc.lineage_file("cutout", ("concept",))
            original = orig is not None and Path(orig).is_file()
            pic = Path(orig) if original else _input_image(sc, input_view or sc.category.get("input_view_default") or "concept")
            fid = await asyncio.to_thread(texfidelity.measure_for_stage, glb, pic, base)
            fid["against"] = "original" if original else "built_from"
        except Exception as e:  # noqa: BLE001  (no picture to compare with)
            fid = {"error": str(e)[:200]}
        sc.set_metrics(texture_fidelity=fid)
    return texfidelity.fidelity_checks(sc.version.metrics.get("texture_fidelity") or {})


async def colour_check(sc: StageContext) -> dict[str, Any] | None:
    """The texture_matches_picture check (vgac/texcolour.py). A TRELLIS build measures itself while it is made; an older
    version is measured once here, from its kept files and the picture it was built from, and the result kept in the
    metrics. None for Hunyuan3D (no base colour), tileable materials and pictures with no cut-out. Never fails the job."""
    m = sc.version.metrics or {}
    if sc.asset.category == "material" or (sc.version.params or {}).get("engine", "trellis") != "trellis":
        return None
    if not m.get("texture_match") and "texture_match_measured" not in m:
        try:
            view = (sc.version.params or {}).get("input_view") or sc.category.get("input_view_default") or "concept"
            pic = _input_image(sc, view)
            match = await asyncio.to_thread(texcolour.measure, sc.file("base_color"), sc.file("glb"), pic)
        except Exception as e:  # noqa: BLE001  (no picture or no texture layout: nothing to compare)
            sc.set_metrics(texture_match_measured=False, texture_match_error=str(e)[:300])
            return None
        if match is None:
            sc.set_metrics(texture_match_measured=False)
            return None
        sc.set_metrics(texture_match=match, texture_tries=1, texture_try_kept=1)
    return texcolour.check(sc.version.metrics)


async def upright_check(sc: StageContext, stage: str, glb: Path | None, normal: Path | None) -> dict[str, Any] | None:
    """The normal_map_upright check (vgac/normalfix.py) on a model and its normal map, measured once and kept in the
    metrics (normal_upright); None without a normal map. Never fails the job."""
    m = sc.version.metrics.get("normal_upright")
    if not m and glb is not None and normal is not None and Path(glb).is_file() and Path(normal).is_file():
        try:
            m = await asyncio.to_thread(normalfix.upright, Path(glb), Path(normal))
        except Exception as e:  # noqa: BLE001  (a measure that fails leaves the model as it is)
            sc.set_metrics(normal_upright_error=str(e)[:300])
            return None
        if m:
            sc.set_metrics(normal_upright=m)
    return normalfix.check(m, stage)


def hair_source(sc: StageContext, params: dict[str, Any], engine: str) -> None:
    """Record the version's hair_source (only when the library is on or a param names it, so today's builds keep
    today's params). An explicit "model" (the hair_fit Fix) sets the asset back to today's hair."""
    explicit = params.get("hair_source")
    src, note = hairlib.mesh_source(sc.asset, explicit, engine)
    if src is None:
        return
    sc.set_params(hair_source=src)
    if note:
        sc.set_metrics(hair_library_note=note)
    if explicit == hairlib.MODEL and hairlib.asset_source(sc.asset) == hairlib.LIBRARY:
        hairlib.set_choice(sc.db, sc.asset, hairlib.MODEL, changed_by="fix")


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    engine = params.get("engine") or "trellis"
    hair_source(sc, params, engine)
    if engine == "hy3d":
        await _hy3d(sc, params)
    elif engine == "trellis":
        await _trellis(sc, params)
    else:
        raise ValueError(f"unknown mesh engine {engine!r}")
    await audit(sc)


def inherit(parent_params: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Parent params under the new ones, without the triangle target and texture size the parent took from the recipe
    (only a chosen value carries over, so a new run follows the recipe as it is now)."""
    base = dict(parent_params or {})
    if "hair_source" not in params:
        base.pop("hair_source", None)  # upstream follows the asset's hair setting unless the new params name one
    for key, src in (("target_faces", "target_faces_source"), ("texture_size", "texture_source")):
        if base.get(src) == "recipe" and key not in params:
            base.pop(key, None)
        base.pop(src, None)
    return {**base, **params}


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**inherit(parent.params if parent else {}, params), "seed": params.get("seed") or random_seed(), "instruction": instruction}
    for key in EDIT_PARAMS:
        merged.pop(key, None)  # the parent's edit is used below as the picture to edit, never as this version's own
    sc.set_params(instruction=instruction, refine_of=parent.id if parent else None)
    if (merged.get("engine") or "trellis") == "trellis" and instruction.strip() and merged.get("edit_picture") is not False:
        input_view = merged.get("input_view") or sc.category.get("input_view_default") or "concept"
        merged["input_view"] = input_view
        if sc.file(EDITED_KEY) is None:  # a Resume keeps the edit it already made
            from_parent = _parent_edit(sc, input_view)
            await _edit_picture(sc, instruction, from_parent or _input_image(sc, input_view), parent.id if from_parent and parent else None)
        else:
            sc.set_params(picture_edit=True)
    await _rerun_from_mesh_parent(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = inherit(parent.params if parent else {}, params)
    for key in EDIT_PARAMS:
        merged.pop(key, None)
    # Fine-tune of a version built from an edited picture keeps that picture (same engine and view only).
    input_view = merged.get("input_view") or sc.category.get("input_view_default") or "concept"
    kept = _parent_edit(sc, input_view) if (merged.get("engine") or "trellis") == "trellis" else None
    if kept is not None and sc.file(EDITED_KEY) is None:
        sc.register(EDITED_KEY, sc.copy_in(kept, "edited_cutout.png"))
        pic = library.version_file(parent, "edited_picture", sc.settings) if parent else None
        if pic is not None and Path(pic).is_file():
            sc.register("edited_picture", sc.copy_in(pic, "edited_picture.png"))
    if kept is not None or (sc.file(EDITED_KEY) is not None and (sc.version.params or {}).get("picture_edit")):
        sc.set_params(picture_edit=True, edit_seed=(parent.params or {}).get("edit_seed") if parent else None,
                      edit_from=parent.id if parent else None)
    await _rerun_from_mesh_parent(sc, merged)


async def _rerun_from_mesh_parent(sc: StageContext, merged: dict[str, Any]) -> None:
    # Inputs come from the lineage above the mesh parent; lineage_file walks through it, so nothing to swap.
    await run(sc, merged)


# --- the picture edit behind a refine instruction ---------------------------------------------------------------------

EDITED_KEY = "edited_cutout"
# The hand redraw (the mesh param hand_edit): what the phase 0 trials used. Fingers resolve in the build only when they
# are separate in the picture, and the three-quarter view shows a hand edge-on unless its back faces the camera.
HAND_EDIT = ("Redraw only the hands: each hand open with the back of the hand toward the camera, all five fingers straight, long and "
             "slightly spread apart with clear gaps between them, thumb separate.")
# Params that belong to one version's own picture edit: never inherited as they are.
EDIT_PARAMS = ("picture_edit", "edit_seed", "edit_from")
# After the person's words, so the edit changes only what they asked for and the cutout stays usable for 3D.
EDIT_SUFFIX = ("Change only what is asked. Keep everything else exactly the same: the same character, design, colours, "
               "materials, outfit, every tool, weapon and attachment, pose, camera angle, size and framing, the whole body in "
               "frame, on a plain white background.")
EDIT_MEGAPIXELS = 1.0


def edit_prompt(instruction: str) -> str:
    text = " ".join(instruction.split())
    if text and text[-1] not in ".!?":
        text += "."
    return f"{text} {EDIT_SUFFIX}"


def edit_size(w: int, h: int, megapixels: float = EDIT_MEGAPIXELS) -> tuple[int, int]:
    """The edit's output size: the picture's shape at about a megapixel, each side a multiple of 16."""
    scale = (megapixels * 1024 * 1024 / max(1, w * h)) ** 0.5
    return max(256, int(round(w * scale / 16)) * 16), max(256, int(round(h * scale / 16)) * 16)


def _edited_cutout(sc: StageContext) -> Path | None:
    if not (sc.version.params or {}).get("picture_edit"):
        return None
    p = sc.file(EDITED_KEY)
    return Path(p) if p is not None and Path(p).is_file() else None


def _parent_edit(sc: StageContext, input_view: str) -> Path | None:
    """The parent mesh version's edited cutout, when it made one from the same view."""
    parent = sc.parent
    if parent is None or parent.stage != "mesh" or not (parent.params or {}).get("picture_edit"):
        return None
    if (parent.params.get("input_view") or sc.category.get("input_view_default") or "concept") != input_view:
        return None
    p = library.version_file(parent, EDITED_KEY, sc.settings)
    return Path(p) if p is not None and Path(p).is_file() else None


def _picture_cards(sc: StageContext) -> list[str]:
    """Where the edit runs: the card that makes pictures, then this job's own card if that one cannot be reached."""
    out: list[str] = []
    for name in (sc.settings.stage_instances.get("concept"), sc.ctx.instance):
        if name and name in sc.ctx.clients and name not in out:
            out.append(name)
    return out


async def _edit_picture(sc: StageContext, instruction: str, src: Path, edit_from: str | None) -> Path:
    """Edit the input picture with the instruction and cut it out; registers edited_picture and edited_cutout."""
    seed = random_seed()
    white = await asyncio.to_thread(imaging.white_composite, src, sc.dir / "edit_source.png")
    w, h = edit_size(*image_size(white))
    cards = _picture_cards(sc)
    if not cards:
        raise RuntimeError("no graphics card can edit the picture")
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")
    last: Exception | None = None
    for card in cards:
        try:
            name = await sc.stage_input(white, f"vgac_{sc.version.id[:8]}_mesh_edit_src.png", card)
            entry = await sc.comfy(edit_graph(name, edit_prompt(instruction), seed, sc.prefix("edit"), w, h), instance=card, poll=1.0,
                                   message="Editing the picture as described", base=0.0, span=0.04)
            edited = await sc.download_image(entry, "save", sc.dir / "edited_picture.png", card)
            cut = await sc.cutout(Path(edited), "edited_cutout", instance=card, base=0.04, span=0.01)
            break
        except Exception as e:  # noqa: BLE001  (try the next card; a cancelled job stops at once)
            if type(e).__name__ == "JobCancelled":
                raise
            last = e
    else:
        raise RuntimeError(f"the picture could not be edited: {last}")
    stats = cut.get("stats") or {}
    if stats.get("empty") or float(stats.get("coverage") or 0) < 0.02:
        raise RuntimeError("the edited picture came out without the character; try the change again or word it differently")
    sc.register("edited_picture", edited)
    sc.register(EDITED_KEY, cut["cutout"])
    sc.set_params(picture_edit=True, edit_seed=seed, edit_from=edit_from)
    sc.set_metrics(picture_edit={"seed": seed, "card": card, "size": [w, h], "elapsed_seconds": entry.get("elapsed_seconds"),
                                 "prompt": edit_prompt(instruction)})
    return Path(cut["cutout"])
