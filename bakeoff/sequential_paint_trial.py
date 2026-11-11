"""Round 2 part 2: paint the sides the picture cannot see with views that are locked to the mesh (trial only).

After the picture is projected (source_project_trial.py), render the next camera (the two sides, then the back) showing
the painted texture where it exists, plus a mask of what is unpainted or weakly painted from that camera. Qwen-Image-
Edit-2511 on instance B redraws that render with the source picture as a second picture and the mask as a third,
asked to finish the unpainted areas in the same materials and detail as the painted ones and to keep everything else.
Only the masked region of the answer is kept (feathered), guarded by a drift check and the structure agreement gate,
projected through the UVs, and the next camera is rendered from the result. The render is the model, so no fit and no
warp is needed (only a drift guard).

Compares, on the same fitted picture view and the same gates: before, front picture only, front picture plus the AI
turnaround views as detail (V2a), and this sequential repaint. Needs the fitted views cached by
`source_project_trial.py <subject> --cache`, and instance B (http://127.0.0.1:8189) with an empty queue.

Usage: backend/.venv/Scripts/python.exe bakeoff/sequential_paint_trial.py knight [wolf] [--rel 90,270,180] [--seed 1000]
Output: bakeoff/out/sequential_paint/<subject>/ (step renders, redraws, atlases, sheets, report.json), results.md
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)
import source_project_trial as sp  # noqa: E402
from comfy import B, download, get, images_of, run, stage_input  # noqa: E402

from vgac import face  # noqa: E402
from vgac.graphs.qwen_edit import compose_graph  # noqa: E402

OUT = os.path.join(HERE, "out", "sequential_paint")
S = sp.S

PROMPT = (
    "Picture 1 is a render of a 3D model seen from one side, with a flat grey background. Parts of it are sharp and "
    "detailed (already painted from the original artwork, Picture 2) and parts are soft and blurry. Picture 3 shows in "
    "white exactly the soft, blurry parts. Redraw Picture 1 so that the soft parts get the same crisp materials, "
    "textures, colours and fine detail as the sharp parts, continuing the sharp parts' materials naturally around the "
    "form; use Picture 2 as the reference for the colours and materials of this subject. Keep everything else exactly "
    "the same: the same shape, pose, position, size, framing and grey background, and do not change any of the sharp "
    "parts. Add nothing new.")


def queue_empty() -> bool:
    q = get(B, "/queue")
    return not q.get("queue_running") and not q.get("queue_pending")


def feather(mask: np.ndarray, px: float) -> np.ndarray:
    return np.clip(face.blur(mask.astype(np.float64), px), 0.0, 1.0)


def make_seq_view(r: "sp.Raster", composite: np.ndarray, render_in: np.ndarray, to_finish: np.ndarray, name: str,
                  weight: float = 0.5) -> "sp.View":
    v = sp.View(name, weight)
    v.cam, v.raster = r.cam, r
    v.pic_aligned = v.pic_affine = composite
    v.render = render_in
    v.valid = to_finish & r.mask
    v.alpha_img = np.ones((S, S))
    v.pic_px_m = r.cam.scale / 1024.0
    v.flow_u, v.flow_cap = np.zeros((sp.FIT_S, sp.FIT_S, 2)), 1.0
    v.disc = sp.depth_discontinuities(r.z, r.cam.scale / S)
    v.info = {"name": name}
    sp.prepare_gates(v)       # agreement gate between the redraw and the model's own render, depth-edge erosion
    mf = feather(to_finish, 4.0) * r.mask
    fe = v.feather
    v.feather0 = mf
    v.feather = mf * fe
    v.ok = True
    return v


def run_subject(key: str, rels: list[float], seed: int, log=print) -> dict:
    build, tname = sp.SUBJECTS[key]
    b = os.path.join(sp.CONVERT, build)
    dest = os.path.join(OUT, key)
    os.makedirs(dest, exist_ok=True)
    cache = os.path.join(sp.CACHE_DIR, f"{key}_s{S}.pkl")
    if not os.path.isfile(cache):
        raise SystemExit(f"run source_project_trial.py {key} --cache first ({cache})")
    t_all = time.time()
    mesh = face.load_mesh(os.path.join(b, "model.glb"))
    tex = face.read_rgb(os.path.join(b, "base.png"))
    T = tex.shape[0]
    views = sp.load_views(cache)
    for v in views:
        sp.prepare_gates(v)
    front = views[0]
    fy = float(front.info["yaw"])
    centre, scale = sp.model_frame(mesh)
    atlas = sp.Atlas(mesh, T)
    pitch_px = atlas.pitch_m * S / scale
    pic, alpha = sp.load_picture(os.path.join(b, "input.png"), None)
    grey = pic * alpha[..., None] + 0.5 * (1 - alpha[..., None])
    face.save_rgb(grey, os.path.join(dest, "picture_grey.png"))
    pic_name = stage_input(os.path.join(dest, "picture_grey.png"), f"seq_{key}_picture.png")
    meas = sp.Measurer(mesh, atlas, front)
    results: dict[str, dict] = {}
    rows: dict[str, dict] = {}

    def record(name: str, new: np.ndarray, extra: dict | None = None) -> None:
        m, imgs, rend = meas.measure(new)
        m.update(extra or {})
        results[name] = m
        rows[name] = {"imgs": imgs, "front": rend}
        face.save_rgb(new, os.path.join(dest, f"{name}_base.png"))
        log(f"[{key}] {name}: {json.dumps({k: v for k, v in m.items() if k != 'sharp'})}")

    record("before", tex)
    projs = [sp.project_view(atlas, v) for v in views]
    front_paint, front_alpha, _ = sp.blend(atlas, projs[:1], [front.pic_aligned])
    cur = sp.finish(atlas, tex, front_paint, front_alpha)
    record("front_only", cur, {"painted_share": round(float((front_alpha > 0.5)[~atlas.is_gut].mean()), 4)})
    imgs = [front.pic_aligned] + [sp.detail_only(v, 2.0 * pitch_px) for v in views[1:]]
    tp, ta, _ = sp.blend(atlas, projs, imgs)
    record("turnaround_v2a", sp.finish(atlas, tex, tp, ta),
           {"painted_share": round(float((ta > 0.5)[~atlas.is_gut].mean()), 4), "views": [v.name for v in views]})

    # ---- sequential
    if not queue_empty():
        raise SystemExit("instance B has jobs queued; try again when it is idle")
    seq_views, seq_imgs, seq_projs = [front], [front.pic_aligned], [projs[0]]
    paint, a_cur = front_paint, front_alpha
    steps = []
    qwen_seconds = 0.0
    t_seq = time.time()
    for rel in rels:
        yaw = (fy + rel) % 360
        step: dict = {"rel": rel, "yaw": round(yaw, 1)}
        cam = sp.face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=S)
        r = sp.Raster(mesh, cam)
        alpha_atlas = np.zeros(T * T)
        alpha_atlas[atlas.idx] = a_cur
        pa = face.sample(alpha_atlas.reshape(T, T, 1), r.uv[..., 0] * T, r.uv[..., 1] * T)[..., 0]
        to_finish = r.mask & (pa < 0.6)
        share = float(to_finish.sum() / max(r.mask.sum(), 1))
        step["unpainted_share_of_view"] = round(share, 4)
        render_in = sp.render_colour(mesh, r, cur)
        tag = f"{int(round(rel)):03d}"
        face.save_rgb(sp.small(render_in, 1024), os.path.join(dest, f"step{tag}_render.png"))
        if share < 0.04:
            step["skipped"] = "almost everything in this view is painted already"
            steps.append(step)
            continue
        m1024 = sp.small(to_finish.astype(np.float64), 1024) > 0.3
        mask_img = np.repeat(face.dilate_mask(m1024, 2)[..., None].astype(np.float64), 3, axis=2)
        face.save_rgb(mask_img, os.path.join(dest, f"step{tag}_mask.png"))
        names = [stage_input(os.path.join(dest, f"step{tag}_render.png"), f"seq_{key}_{tag}_render.png"), pic_name,
                 stage_input(os.path.join(dest, f"step{tag}_mask.png"), f"seq_{key}_{tag}_mask.png")]
        outputs, secs = run(B, compose_graph(names, PROMPT, seed, f"bakeoff/seq/{key}/{tag}", 1024, 1024), poll=1.0)
        qwen_seconds += secs
        item = images_of(outputs)[0]
        redraw_path = os.path.join(dest, f"step{tag}_redraw.png")
        download(B, item, redraw_path)
        step["qwen_seconds"] = secs
        out1024 = face.read_rgb(redraw_path, 1024)
        render1024 = sp.small(render_in, 1024)
        rmask1024 = sp.small(r.mask.astype(np.float64), 1024) > 0.5
        untouched = rmask1024 & ~face.dilate_mask(m1024, 10)
        step["untouched_mean_abs_diff_255"] = round(float(np.abs(out1024 - render1024)[untouched].mean() * 255), 2) if untouched.any() else None
        step["untouched_structure_match"] = face.feature_match(out1024, render1024, untouched) if untouched.sum() > 500 else None
        fg = np.abs(out1024 - 0.5).max(2) > 0.04
        step["silhouette_iou_redraw_vs_render"] = round(sp.iou(fg & face.dilate_mask(rmask1024, 6), rmask1024), 4)
        a = face.align(out1024, render1024, rmask1024)
        step["align"] = {k: (round(float(a[k]), 4) if isinstance(a.get(k), (int, float)) else None) for k in ("sx", "sy", "tx", "ty", "score")}
        shift = max(abs(a["tx"]), abs(a["ty"])) / 1024
        if a["score"] < 0.35 or shift > 0.012 or max(abs(a["sx"] - 1), abs(a["sy"] - 1)) > 0.03:
            step["skipped"] = "the redraw moved or changed too much"
            steps.append(step)
            continue
        out1024 = face.apply_alignment(out1024, a)
        out_s = face.resize(out1024, S, Image.LANCZOS)
        mf = feather(to_finish, 4.0)
        composite = render_in * (1 - mf[..., None]) + out_s * mf[..., None]
        face.save_rgb(sp.small(composite, 1024), os.path.join(dest, f"step{tag}_composite.png"))
        v2 = make_seq_view(r, composite, render_in, to_finish, f"seq{tag}")
        p2 = sp.project_view(atlas, v2)
        step["gates"] = {**v2.stats, **v2.info.get("gate_pixels", {})}
        step["texels_painted"] = int((p2["w"] > 0.02).sum())
        seq_views.append(v2)
        seq_imgs.append(composite)
        seq_projs.append(p2)
        paint, a_cur, dom = sp.blend(atlas, seq_projs, seq_imgs)
        cur = sp.finish(atlas, tex, paint, a_cur)
        face.save_rgb(cur, os.path.join(dest, f"after{tag}_base.png"))
        steps.append(step)
        log(f"[{key}] step {rel}: {json.dumps(step)}")
    seq_seconds = time.time() - t_seq
    record("sequential", cur, {"painted_share": round(float((a_cur > 0.5)[~atlas.is_gut].mean()), 4), "qwen_seconds": round(qwen_seconds, 1),
                               "seconds": round(seq_seconds, 1)})
    sp.sheets(dest, key, rows, front, front.pic_affine)
    rep = {"subject": key, "picture_yaw": fy, "steps": steps, "results": results,
           "seconds": {"sequential": round(seq_seconds, 1), "qwen": round(qwen_seconds, 1), "total": round(time.time() - t_all, 1)}}
    with open(os.path.join(dest, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=float)
    return rep


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rels = [90.0, 270.0, 180.0]
    seed = 1000
    for i, a in enumerate(sys.argv):
        if a == "--rel":
            rels = [float(x) for x in sys.argv[i + 1].split(",")]
            args = [x for x in args if x != sys.argv[i + 1]]
        if a == "--seed":
            seed = int(sys.argv[i + 1])
            args = [x for x in args if x != sys.argv[i + 1]]
    os.makedirs(OUT, exist_ok=True)
    reps = [run_subject(k, rels, seed) for k in (args or ["knight", "wolf"])]
    cols = ["mean_sharp8", "real_detail_db", "front_lab_diff", "front_sharp_ratio", "seam_step", "seam_ratio", "painted_share", "seconds"]
    lines = ["| subject | variant | " + " | ".join(cols) + " | sharpness at 0/90/180/270 |", "|" + "---|" * (len(cols) + 3)]
    for r in reps:
        for var, m in r["results"].items():
            sh = "/".join(str(m["sharp"][str(y)]) for y in (0, 90, 180, 270))
            lines.append(f"| {r['subject']} | {var} | " + " | ".join(str(m.get(c, "")) for c in cols) + f" | {sh} |")
    with open(os.path.join(OUT, "results.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
