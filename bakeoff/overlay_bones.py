"""Draw the skeleton from rig_info.json over rest.png (front orthographic view). Usage: python overlay_bones.py <info_dir>"""
import json, os, sys
from PIL import Image, ImageDraw
d = sys.argv[1]
info = json.load(open(os.path.join(d, "rig_info.json")))
cam = info["camera"]; cx, cy, cz = cam["centre"]; S = cam["ortho_scale"]; R = cam["res"]
def proj(p):  # front view: image x from world x, image y from world z
    return ((p[0] - cx) / S + 0.5) * R, (0.5 - (p[2] - cz) / S) * R
im = Image.open(os.path.join(d, "rest.png")).convert("RGBA")
bg = Image.new("RGBA", im.size, (110, 110, 110, 255)); bg.alpha_composite(im); dr = ImageDraw.Draw(bg)
for b in info["bone_segments"]:
    h, t = proj(b["head"]), proj(b["tail"])
    dr.line([h, t], fill=(255, 200, 0, 255), width=3); dr.ellipse([h[0]-4, h[1]-4, h[0]+4, h[1]+4], fill=(255, 60, 60, 255))
bg.convert("RGB").save(os.path.join(d, "bones.png")); print("bones.png", len(info["bone_segments"]), "bones")
