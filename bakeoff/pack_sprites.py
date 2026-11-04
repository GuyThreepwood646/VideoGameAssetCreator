"""Pack the frames rendered by sprites.py into one sheet per clip plus atlas.json. Usage: python pack_sprites.py <out_dir>"""
import json
import os
import sys

from PIL import Image

d = sys.argv[1]
atlas = json.load(open(os.path.join(d, "atlas_frames.json")))
size = atlas["size"]
out = {"size": size, "directions": atlas["directions"], "clips": {}}
for name, clip in atlas["clips"].items():
    sheet = Image.new("RGBA", (size * clip["cols"], size * clip["rows"]), (0, 0, 0, 0))
    for e, f in zip(clip["entries"], clip["files"]):
        sheet.paste(Image.open(f).convert("RGBA"), (e["x"], e["y"]))
    sheet_path = os.path.join(d, f"{name}.png")
    sheet.save(sheet_path)
    out["clips"][name] = {"sheet": os.path.basename(sheet_path), "rows": clip["rows"], "cols": clip["cols"], "frames": clip["entries"]}
json.dump(out, open(os.path.join(d, "atlas.json"), "w"), indent=1)
print("PACK_OK", list(out["clips"]))
