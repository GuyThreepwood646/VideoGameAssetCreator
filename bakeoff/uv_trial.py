"""Runs bakeoff/unwrap_trial.py on the knight for several modes and measures each (bakeoff/bake_chain.py measures).
Usage: backend/.venv/Scripts/python.exe bakeoff/uv_trial.py mode [mode ...]"""
import json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend")); sys.path.insert(0, HERE)
from vgac import face  # noqa: E402
import bake_chain as bc  # noqa: E402

src = os.path.join(HERE, "out", "convert", "humanoid_20000")
for mode in sys.argv[1:]:
    dest = os.path.join(bc.OUT, f"knight_uv__{mode}")
    os.makedirs(dest, exist_ok=True)
    maps = {k: os.path.abspath(os.path.join(src, v)) for k, v in bc.KEYMAP.items()}
    mj = os.path.join(dest, "bake_maps.json"); json.dump(maps, open(mj, "w"))
    subprocess.run([bc.BLENDER, "-b", "--python", os.path.join(HERE, "unwrap_trial.py"), "--", os.path.join(src, "model.glb"), dest, mode, mj, "2048"],
                   stdout=open(os.path.join(dest, "blender.log"), "w"), stderr=subprocess.STDOUT, check=True)
    rep = json.load(open(os.path.join(dest, "uv_report.json")))
    m = bc.measure(dest, {})
    mesh = face.load_mesh(os.path.join(dest, "model.glb")); tx = face.read_rgb(os.path.join(dest, "base_color.png"))
    row = {"mode": mode, "islands": rep["after"]["islands"], "atlas_used": m["atlas_used"], "texel_density_mean": rep["after"]["texel_density_mean"],
           "texel_density_p10": rep["after"]["texel_density_p10"], "real_detail_db": m["real_detail_db"], "mean_sharpness": m["mean_sharpness"],
           "front_sharpness": m["view_sharpness"]["0"], "seam_step": bc.seam_step(mesh, tx), "seconds": rep["seconds"]}
    json.dump(row, open(os.path.join(dest, "row.json"), "w"), indent=1); print(json.dumps(row), flush=True)
