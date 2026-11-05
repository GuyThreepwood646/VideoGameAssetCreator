"""Face detail pass, run at the end of the clean-up step (stages/prep.py) for characters with a face.

The 3D builder invents the face from one picture and gives it few, blurry texels: the eyes come out as smudges and the
mouth as a smear. This pass makes the face match the painted picture (vgac/face.py has the maths):

1. Render the model's head from the front with a camera we know (base colour only) and find the face on it: the nose
   tip from the shape, the skin around it by colour, eyes and mouth filled in.
2. Cut the painted picture's head out with the same framing (the concept's cut-out), and let Qwen-Image-Edit redraw
   it sharper with nothing moved (the same model and helpers the side-views step uses; one picture, no new download).
   When the redraw moved things (its features no longer sit on the crop's), the crop is used as it is.
3. Fit that picture onto the render: one scale and shift that put the picture's eye line and mouth on the model's
   (found as the darkest spots and line around the nose tip), or, when they cannot be found, the scale and shift that
   best correlate the two faces' features. A fit that does not find the face is rejected and nothing is painted.
4. When the face has too few texels to hold the detail (under an eighth of the texture's side across the face), the
   face's texture pieces are made bigger and every map is baked again onto the new layout (blender/faceuv.py).
5. Paint the fitted picture back through the model's UVs from the same camera, only on the face, weighted by how
   squarely the surface faces the camera and feathered at the edges, with its broad tone matched to the old texture.

Skipped, with the reason recorded, when the option is off, for categories other than humanoid, for crisp or flat
looks (pixel and low-poly recipes), without a painted picture, when no face is found or it is covered (helmet, hood,
mask). Metrics under "face_detail"; the before and after picture is the version file "face_before_after".
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .. import face, imaging, quality
from ..comfy import ComfyClient, ComfyError
from ..graphs.qwen_edit import edit_graph
from ..jobs import JobCancelled
from .base import StageContext, random_seed

CATEGORIES = ("humanoid",)
TARGET_FRACTION = 0.125  # texels wanted across the face: an eighth of the texture's side (256 at 2048)
GAIN_MIN = 1.15  # below this the layout is left alone (the repack and the re-bake take about 30 s)
SHARP_MIN = 1.0  # face sharpness after the pass (face.sharpness of the front render); smeared faces score 0.6 to 0.8
MATCH_MIN = 0.6  # the model's face against the fitted picture (face.feature_match)
ALIGN_MIN = 0.15  # the fit's correlation; under it the picture did not find the face
RIM_FRAC = 0.02  # how much of the painted face's edge is dropped outside the inner face, as a share of the picture
SOFTEN_M = 0.025  # the paint fades over about this much surface wherever it stops short (metres)
FACING = (0.15, 0.6)  # surface-to-view cosine where the paint fades in: from edge-on (old texture) to fully painted
LANDMARK_RESIDUAL_MAX = 0.12  # the eye-and-mouth fit's eye error, as a share of the face's width
REDRAW_KEEP_MIN = 0.5  # the sharpened picture against the crop: under it the redraw moved things
COVERED_WIDTH_MIN = 0.35
DEFAULT_STRENGTH = 1.0
# Reasons the person can do something about: the check fails softly with a Fix (run the clean-up step again).
ACTIONABLE = ("no_card", "not_lined_up", "error")

REDRAW_PROMPT = ("Redraw this picture at high quality with crisp, sharp detail: clear eyes with irises, eyelids and lashes, "
                 "defined eyebrows, nose and lips, fine hair strands. Keep everything else exactly the same: the same "
                 "character, the same face, the same position, size, angle and framing, the same hair, clothing and grey "
                 "background, the same colours and the same painting style. Soft, even lighting.")

METHOD = "painted picture, sharpened by Qwen-Image-Edit, fitted to the 3D face and painted on through the texture layout"
METHOD_UNSHARPENED = "painted picture fitted to the 3D face and painted on through the texture layout (not sharpened)"


def skip_reason(category_id: str, rec: dict[str, Any] | None, params: dict[str, Any], has_picture: bool) -> str | None:
    """Why the pass does not run for this version, or None when it should run. In the order a person would ask."""
    if params.get("face_detail") is False:
        return "off"
    if category_id not in CATEGORIES:
        return "not_humanoid"
    srf = (rec or {}).get("surface") or {}
    mesh = (rec or {}).get("mesh") or {}
    if srf.get("texture_filter") == "crisp" or srf.get("shading") == "flat" or (mesh.get("texture") or 2048) < 512:
        return "style"
    if not has_picture:
        return "no_picture"
    return None


def strength_of(params: dict[str, Any]) -> float:
    try:
        s = float(params.get("face_strength", DEFAULT_STRENGTH))
    except (TypeError, ValueError):
        s = DEFAULT_STRENGTH
    return min(1.0, max(0.0, s))


def target_texels(tex: int) -> int:
    return max(64, int(round(TARGET_FRACTION * tex)))


def face_checks(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """face_matches_picture (warn), from the "face_detail" metrics block. Nothing when the pass does not apply (off,
    not a humanoid, a crisp or flat look, no picture, no face or a covered one): there is nothing to measure. A soft
    failure when it was skipped for a reason the person can act on, or when the face is still blurry or does not
    follow the fitted picture by the measured numbers. It measures sharpness and how well the model's face carries
    the fitted picture's features, not whether it is the same person."""
    fd = (metrics or {}).get("face_detail") or {}
    if not fd:
        return []
    if not fd.get("ran"):
        reason = fd.get("skipped")
        if reason not in ACTIONABLE:
            return []
        return [quality.check("face_matches_picture", False, reason, SHARP_MIN, vars={"reason": reason})]
    sharp = float(fd.get("sharpness_after") or 0.0)
    match = float(fd.get("feature_match_after") or 0.0)
    ok = sharp >= SHARP_MIN and match >= MATCH_MIN
    return [quality.check("face_matches_picture", ok, round(sharp, 3), SHARP_MIN,
                          vars={"reason": None if ok else ("blurry" if sharp < SHARP_MIN else "off_picture"),
                                "before": fd.get("sharpness_before"), "match": round(match, 3), "texels": fd.get("face_texels_after")})]


# --- CPU parts (run in a worker thread) ---------------------------------------------------------------------


def inspect(glb: Path, base_color: Path, cutout: Path, out_dir: Path) -> dict[str, Any]:
    """Find the face on the model and cut the painted head out to match. Writes painted_head.png. Returns
    {found, reason, ...measures}."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {"found": False, "reason": "no_face", "detail": "the model has no texture layout"}
    tex = face.read_rgb(base_color)
    head = face.find_head(mesh["verts"])
    if head is None:
        return {"found": False, "reason": "no_face"}
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
    if not info.get("found"):
        return {"found": False, "reason": "no_face", "detail": info.get("reason")}
    if face.covered_head(info, head, cam, COVERED_WIDTH_MIN):
        return {"found": False, "reason": "covered", "face_width_px": info.get("width_px")}
    rgba = np.asarray(Image.open(cutout).convert("RGBA"), dtype=np.float64) / 255.0
    crop = face.painted_head(rgba, head, cam)
    if crop is None:
        return {"found": False, "reason": "no_picture"}
    face.save_rgb(crop["image"], out_dir / "painted_head.png")
    dens = face.face_texels(mesh, cam, info["region"], tex.shape[0])
    tris = face.face_triangles(view, info["region"])
    return {"found": True, "reason": None, "texture_size": int(tex.shape[0]), "face_width_px": info["width_px"],
            "painted_head_px": crop["head_px"], "face_triangles": face.triangle_centres(mesh, tris).round(6).tolist(), **dens}


def redraw_kept(sharp: Path, crop: Path) -> float:
    """How well the redrawn picture keeps the crop's features in place (whole picture)."""
    a = face.read_rgb(sharp, 1024)
    b = face.read_rgb(crop, 1024)
    return face.feature_match(a, b, np.ones(a.shape[:2], dtype=bool), lo=2.0, hi=10.0)


def fit_and_paint(glb: Path, base_color: Path, picture: Path, before_glb: Path, before_tex: Path, crop: Path, out_dir: Path,
                  strength: float) -> dict[str, Any]:
    """Steps 3 and 5 on the final layout: fit the picture to the render, paint it back, measure, and draw the before
    and after sheet. Writes base_color_face.png and face_before_after.png into out_dir."""
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base_color)
    T = tex.shape[0]
    head = face.find_head(mesh["verts"])
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
    if not info.get("found"):
        return {"ok": False, "reason": "no_face"}
    region = info["region"]
    src = face.read_rgb(picture, 1024)
    inner = face.inner_face(info)
    # The eye-and-mouth fit is used when it explains the picture: its eyes land within LANDMARK_RESIDUAL_MAX of the face's
    # width of the model's and the broad features agree; it keeps the eye line and the mouth where the model has them.
    # Otherwise the whole-face correlation (bounded stretch). Scores are on broad features: the model's face is
    # blurred, so its fine detail matches nothing.
    lm_src, lm_dst = face.landmarks(src, info), face.landmarks(view["image"], info)
    a = face.fit_landmarks(lm_src, lm_dst, info["nose"]) if lm_src and lm_dst else None
    if a is not None:
        a["how"] = "eyes and mouth"
        a["score"] = face.feature_match(face.apply_alignment(src, a), view["image"], inner, 6.0, 24.0)
        if a["residual_px"] > LANDMARK_RESIDUAL_MAX * float(info["width_px"]) or a["score"] < ALIGN_MIN:
            a = None
    if a is None:
        a = face.align(src, view["image"], inner)
        a["how"] = "whole face"
        a["score"] = face.feature_match(face.apply_alignment(src, a), view["image"], inner, 6.0, 24.0)
    if a["score"] < ALIGN_MIN:
        return {"ok": False, "reason": "not_lined_up", "alignment": a}
    # No local warping after this: block matching against the blurred model face moved the brows into a frown on the
    # knight (tried and dropped); one scale and shift keeps the painted face as it was drawn.
    fitted = face.apply_alignment(src, a)
    match_fit = face.feature_match(fitted, view["image"], region)
    # Hair stays hair and skin stays skin: never paint onto the model's hair (a flap over an eye, the fringe), and
    # where the picture has hair or background over the model's skin (its fringe and jaw are not where the model's
    # are), fill that spot from the painted skin around it instead.
    seg = face.hair_and_skin(view["image"], fitted, info, view["mask"])
    paint = region & ~face.dilate_mask(seg["old_hair"], 2)
    core = seg["pic_hair"] | (seg["pic_background"] & ~inner)
    # Outside the eyes-nose-mouth area the painted face's own rim goes too (the shadow along its jaw and cheek, which
    # lands inside the model's wider cheek and shows as a line from the side).
    rim = face.dilate_mask(core, max(2, int(RIM_FRAC * view["image"].shape[0]))) & ~face.features_box(info)
    bad = (core | rim) & paint
    if bad.any():
        filled = face.inpaint(fitted, paint & ~bad)
        fitted = np.where(bad[..., None], filled, fitted)
    weight = face.screen_weight(paint)
    # where the paint will fade (the region's edge, and surface turning away from the camera) the tone is matched
    # locally, so the cheeks do not show a step where the painted face hands over to the old texture
    fade = weight * face.smoothstep(FACING[0], FACING[1] + 0.25, view["facing"])
    toned = face.tone_match_local(fitted, view["image"], paint, fade)
    res = face.project_back(mesh, tex, cam, toned, weight, facing=FACING, strength=strength, soften_m=SOFTEN_M)
    spread, spread_n = face.spread_tone(mesh, tex, res["texture"], res["texel_weight"], info["skin"])
    uvm = imaging.load_uv_mesh(glb)
    coverage = imaging.rasterise_uv(uvm, T)["labels"] > 0
    new_tex = face.grow_into_gutter(spread, coverage, res["bbox"], imaging.dilation_px(T))
    out = face.save_rgb(new_tex, out_dir / "base_color_face.png")
    after = face.render_view(mesh, new_tex, cam)
    old_mesh = face.load_mesh(before_glb)
    old_tex = face.read_rgb(before_tex)
    old_head = face.find_head(old_mesh["verts"])
    before = face.render_view(old_mesh, old_tex, face.head_camera(old_head, 0, 1024))
    metrics = {
        "sharpness_before": face.sharpness(before["image"], region), "sharpness_after": face.sharpness(after["image"], region),
        "feature_match_before": face.feature_match(before["image"], toned, region),
        "feature_match_after": face.feature_match(after["image"], toned, region),
        "fit_match": match_fit,
        "alignment": {k: a.get(k) for k in ("how", "sx", "sy", "tx", "ty", "score", "residual_px")},
        "texels_painted": res["changed"], "hair_kept_px": int((region & seg["old_hair"]).sum()), "hair_filled_px": int(bad.sum()), "tone_spread_texels": spread_n,
        "face_texels_after": int(face.face_texels(mesh, cam, region, T)["face_width_texels"]),
        "eyes_moved": eyes_moved(view["image"], after["image"], info),
    }
    sheet = before_after_sheet(crop, (old_mesh, old_tex, old_head), (mesh, new_tex, head), out_dir / "face_before_after.png")
    return {"ok": True, "base_color": out, "sheet": sheet, "metrics": metrics}


def eyes_moved(before: np.ndarray, after: np.ndarray, info: dict[str, Any]) -> float | None:
    """How far the darkest spot of each eye moved between the old and the new front render, as a share of the face's
    width (the larger of the two eyes). A registration measure: the old texture's eye smudges sit where the 3D builder
    put the eyes, so a large move means the painted eyes are off the model's eye places. None when not measurable."""
    eb = face.eye_points(before, info)
    ea = face.eye_points(after, info)
    if not eb or not ea:
        return None
    d = max(float(np.hypot(a[0] - b[0], a[1] - b[1])) for a, b in zip(ea, eb))
    return round(d / max(float(info["width_px"]), 1.0), 4)


def before_after_sheet(crop: Path, before: tuple, after: tuple, dest: Path, cell: int = 384) -> Path:
    """Painted head | 3D face before | 3D face after, from the front, then from the left and the right three-quarter;
    a last row of close-ups of the new face: the brow and the bridge of the nose from the front, and the cheek where
    the painted face meets the old texture from each side."""
    rows = []
    for yaw in (0, 35, -35):
        imgs = []
        for m, t, h in (before, after):
            imgs.append(face.render_view(m, t, face.head_camera(h, yaw, 768))["image"])
        rows.append((yaw, imgs))
    pad = 28
    sheet = Image.new("RGB", (cell * 3, pad + cell * 4 + pad), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for i, label in enumerate(("Painted picture", "3D face before", "3D face after")):
        d.text((i * cell + 8, 8), label, fill=(230, 230, 230))
    painted = Image.open(crop).convert("RGB").resize((cell, cell), Image.LANCZOS)
    for r, (yaw, imgs) in enumerate(rows):
        y = pad + r * cell
        if r == 0:
            sheet.paste(painted, (0, y))
        else:
            d.text((8, y + 8), "From the left" if yaw > 0 else "From the right", fill=(230, 230, 230))
        for c, img in enumerate(imgs):
            sheet.paste(Image.fromarray(face.to_uint8(img)).resize((cell, cell), Image.LANCZOS), ((c + 1) * cell, y))
    # close-ups of the new face (fractions of the 768 px renders: the framing of head_camera)
    y = pad + 3 * cell
    d.text((8, y + 4), "Close-ups after: brow, left cheek, right cheek", fill=(230, 230, 230))
    boxes = ((0, (0.25, 0.3, 0.75, 0.62)), (1, (0.2, 0.35, 0.6, 0.85)), (2, (0.4, 0.35, 0.8, 0.85)))
    for c, (row, (fx0, fy0, fx1, fy1)) in enumerate(boxes):
        img = Image.fromarray(face.to_uint8(rows[row][1][1]))
        W = img.size[0]
        part = img.crop((int(fx0 * W), int(fy0 * W), int(fx1 * W), int(fy1 * W)))
        part.thumbnail((cell, cell), Image.LANCZOS)
        sheet.paste(part, (c * cell + (cell - part.size[0]) // 2, y + pad))
    sheet.save(dest)
    return dest


# --- the pass ------------------------------------------------------------------------------------------------


def image_instance(sc: StageContext) -> str | None:
    """The card that draws pictures now (the side-views step's, or the one that took over while it is off)."""
    from .. import jobs, prefs

    try:
        p = prefs.load(sc.settings)
        inst = jobs.route("turnaround", sc.asset.category, sc.settings, prefs.disabled_instances(sc.settings), p.get("allowed_kinds"))
    except Exception:  # noqa: BLE001
        inst = sc.settings.stage_instances.get("turnaround")
    if not inst or inst == jobs.HOST or inst not in sc.ctx.clients:
        return None
    return inst


async def redraw(sc: StageContext, instance: str, crop: Path, seed: int, dest: Path) -> Path:
    """Qwen-Image-Edit redraw of the crop. Stop works: a cancelled job takes its own prompt off the card (removes it
    from the queue, or interrupts it when it is the one running), never someone else's."""
    client: ComfyClient = sc.client(instance)
    name = await client.stage_input(crop, f"vgac_{sc.version.id[:8]}_face_head.png")
    graph = edit_graph(name, REDRAW_PROMPT, seed, sc.prefix("face_redraw"), 1024, 1024)
    prompt_id = await client.queue_prompt(graph)

    def on_poll(elapsed: float) -> None:
        sc.ctx.progress(None, f"Sharpening the painted face on {instance}: {int(elapsed)} s")
        sc.ctx.check_cancelled()

    try:
        entry = await client.wait(prompt_id, poll_seconds=1.0, timeout=sc.settings.job_timeout_seconds, on_poll=on_poll)
    except (JobCancelled, asyncio.CancelledError):
        await _withdraw(client, prompt_id)
        raise
    imgs = ComfyClient.images_of(entry, "save")
    if not imgs:
        raise ComfyError("the redraw produced no picture")
    return await client.download_output(imgs[0], dest)


async def _withdraw(client: ComfyClient, prompt_id: str) -> None:
    try:
        q = await client.queue_state()
        running = [item[1] for item in q.get("queue_running", []) if len(item) > 1]
        if prompt_id in running:
            await client.interrupt()
        else:
            await client._client().post("/queue", json={"delete": [prompt_id]}, timeout=5.0)
    except Exception:  # noqa: BLE001  (the card may be gone; nothing more to do)
        pass


async def run(sc: StageContext, glb: Path, maps: dict[str, Path], rec: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """The pass on the prepared model `glb` (changed in place) with the current maps. Returns {files: {key: path},
    metrics: {"face_detail": {...}}, relayout: bool}. Never raises for a reason that should not fail the clean-up
    step (those are recorded as skipped); Stop still stops it."""
    t0 = time.time()
    out_dir = sc.dir / "face"
    cutout = sc.lineage_file("cutout", ("concept",))
    reason = skip_reason(sc.asset.category, rec, params, cutout is not None and Path(cutout).is_file())
    strength = strength_of(params)
    if reason:
        return _skipped(reason, t0)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = maps.get("base_color")
    if base is None:
        return _skipped("no_face", t0)
    sc.progress(0.75, "Finding the face")
    found = await asyncio.to_thread(inspect, glb, Path(base), Path(cutout), out_dir)
    if not found.get("found"):
        return _skipped(found.get("reason") or "no_face", t0, {k: v for k, v in found.items() if k not in ("found", "reason")})
    crop = out_dir / "painted_head.png"
    record: dict[str, Any] = {"ran": False, "face_texels_before": found["face_width_texels"], "texture_size": found["texture_size"],
                              "painted_head_px": found["painted_head_px"], "strength": strength}

    # 2. Sharpen the painted head (on the picture card). Without a card it is used as it is.
    picture, method, redraw_seconds = crop, METHOD_UNSHARPENED, 0.0
    instance = image_instance(sc)
    reachable = False
    if instance is not None:
        reachable = bool((await sc.client(instance).vram_summary()).get("reachable"))
    if not reachable:
        return _skipped("no_card", t0, record)
    seed = params.get("face_seed")
    if seed is None:
        seed = random_seed()
        sc.set_params(face_seed=seed)
    sc.progress(0.78, "Sharpening the painted face")
    t1 = time.time()
    try:
        sharp = await redraw(sc, instance, crop, int(seed), out_dir / "painted_head_sharp.png")
        kept = await asyncio.to_thread(redraw_kept, sharp, crop)
        record["redraw_kept"] = kept
        if kept >= REDRAW_KEEP_MIN:
            picture, method = sharp, METHOD
        sc.use_model("qwen_edit")
        sc.use_model("qwen_lightning_lora")
    except (JobCancelled, asyncio.CancelledError):
        raise
    except Exception as e:  # noqa: BLE001  (a failed redraw: the crop still carries the painted face)
        record["redraw_error"] = str(e)[:300]
    redraw_seconds = round(time.time() - t1, 1)

    # 4. More texels for the face when it has too few.
    target = target_texels(found["texture_size"])
    gain = face.gain_needed(found["face_width_texels"], target)
    work_glb, work_maps, relayout = Path(glb), dict(maps), None
    if gain >= GAIN_MIN:
        sc.progress(0.82, "Giving the face more of the texture")
        (out_dir / "face_points.json").write_text(json.dumps(found["face_triangles"]), encoding="utf-8")
        (out_dir / "face_maps.json").write_text(json.dumps({k: str(p) for k, p in maps.items()}), encoding="utf-8")
        tex = found["texture_size"]
        new_glb = out_dir / "face.glb"
        await sc.blender("faceuv.py", [glb, new_glb, "--points", out_dir / "face_points.json", "--maps", out_dir / "face_maps.json",
                                       "--gain", round(gain, 3), "--tex", tex, "--padding", imaging.unwrap_padding(tex),
                                       "--margin", imaging.dilation_px(tex)], log_name="faceuv.log")
        rep_path = out_dir / "face_report.json"
        relayout = json.loads(rep_path.read_text()) if rep_path.is_file() else None
        if relayout and relayout.get("layout_mode") != "unchanged" and new_glb.is_file():
            work_glb = new_glb
            work_maps = {k: Path(p) for k, p in (relayout.get("rebake_maps") or {}).items() if Path(p).is_file()}
        else:
            relayout = None

    # 3 and 5. Fit and paint.
    sc.progress(0.88, "Painting the face")
    done = await asyncio.to_thread(fit_and_paint, work_glb, work_maps["base_color"], picture, Path(glb), Path(maps["base_color"]),
                                   crop, out_dir, strength)
    if not done.get("ok"):
        return _skipped(done.get("reason") or "error", t0, {**record, "alignment": done.get("alignment")})
    files: dict[str, Path] = {k: p for k, p in work_maps.items() if k != "base_color"} if relayout else {}
    # The painted base colour becomes base_color.png in the face folder; the GLB carries it.
    final_base = out_dir / "base_color.png"
    if relayout:
        shutil.copy2(work_maps["base_color"], out_dir / "base_color_before_face.png")
    shutil.copy2(done["base_color"], final_base)
    files["base_color"] = final_base
    files["face_before_after"] = done["sheet"]
    if relayout:
        shutil.copy2(work_glb, glb)
    await asyncio.to_thread(imaging.glb_replace_images, glb, base_color=final_base)
    record.update(ran=True, method=method, redraw_seconds=redraw_seconds, **done["metrics"],
                  layout=None if not relayout else {k: relayout.get(k) for k in ("layout_mode", "gain", "rest_scale", "island_scale", "islands", "rebake_seconds")},
                  seconds=round(time.time() - t0, 1))
    return {"files": files, "metrics": {"face_detail": record}, "relayout": bool(relayout)}


def _skipped(reason: str, t0: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"files": {}, "metrics": {"face_detail": {**(extra or {}), "ran": False, "skipped": reason,
                                                     "seconds": round(time.time() - t0, 1)}}, "relayout": False}
