"""Blender headless (WP-69 suggested worn items): render the rig's body from six views (a colour picture each, with the
body's own texture and flat light) and map every pixel to a polygon by ray casting (the id maps are exact), and write
the body's rest mesh (vertices in Blender's frame, polygons, bones) for the vote and the placement.

Run: blender -b --factory-startup --python suggest_render.py -- <rig.glb> <out_dir> [--size 512]
Outputs in out_dir: <view>_color.png and <view>_ids.npy (int64 polygon index per pixel, -1 for none), mesh_polys.json
(vertex count and the polygons), body.npz (verts, faces, bone_names, bone_heads), views.json.
Front of the character is -Y, back is +Y, up is +Z (the rig's frame after the glTF import).
"""
import sys, os, json
import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, outd = os.path.abspath(argv[0]), os.path.abspath(argv[1])
size = int(argv[argv.index("--size") + 1]) if "--size" in argv else 512
os.makedirs(outd, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
scene = bpy.context.scene

# the body: the skinned mesh with the most polygons; other meshes and the armature are hidden from the render
meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
body = max(meshes, key=lambda o: len(o.data.polygons))
for o in scene.objects:
    if o is not body:
        o.hide_render = True
        if o.type == "ARMATURE":
            o.hide_viewport = True

# the body's rest mesh and bones, for the vote and the placement (same order as mesh_polys.json)
import numpy as np
_verts = np.array([tuple(v.co) for v in body.data.vertices], dtype=float).reshape(-1, 3)
_faces = np.array([tuple(p.vertices) for p in body.data.polygons if len(p.vertices) == 3], dtype=np.int64).reshape(-1, 3)
_arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
_bones = [b.name for b in _arm.data.bones] if _arm else []
_heads = [list(_arm.matrix_world @ b.head_local) for b in _arm.data.bones] if _arm else []

# bind pose: drop the animation so the picture is the rest pose the rig was made in
arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
if arm and arm.animation_data:
    arm.animation_data.action = None
    for t in arm.animation_data.nla_tracks:
        t.mute = True
bpy.context.view_layer.update()

# world-space bounds of the body
corners = [body.matrix_world @ Vector(c) for c in body.bound_box]
lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
centre = (lo + hi) * 0.5
height = hi.z - lo.z
dist = max(height * 2.2, 3.0)

scene.render.resolution_x = size
scene.render.resolution_y = size
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.film_transparent = False
scene.display.render_aa = "OFF"
sh = scene.display.shading
sh.light = "FLAT"
sh.color_type = "TEXTURE"
sh.show_shadows = False
sh.show_cavity = False
sh.background_type = "WORLD"
scene.world = scene.world or bpy.data.worlds.new("w")
scene.world.color = (0.5, 0.5, 0.5)
vs = scene.view_settings
vs.view_transform = "Standard"
vs.look = "None"
vs.exposure = 0.0
vs.gamma = 1.0

cam_d = bpy.data.cameras.new("cam")
cam_d.type = "ORTHO"
cam_d.ortho_scale = max(height, lo.x - hi.x) * 1.25
cam = bpy.data.objects.new("cam", cam_d)
scene.collection.objects.link(cam)
scene.camera = cam

# the six views: direction from the body centre to the camera (x, y, z), front -Y, back +Y
s = 0.7071
views = {
    "back": Vector((0, 1, 0.12)),
    "left": Vector((-1, 0, 0.12)),
    "right": Vector((1, 0, 0.12)),
    "back_left": Vector((-s, s, 0.12)),
    "back_right": Vector((s, s, 0.12)),
    "top_back": Vector((0, 0.6, 1.0)),
}

# face ids by ray casting: for each pixel centre, the first polygon hit by the orthographic ray (exact, no colour
# management involved). The body is in world space, so the BVH is built from the world-space vertices.
from mathutils.bvhtree import BVHTree
dg = bpy.context.evaluated_depsgraph_get()
ev = body.evaluated_get(dg)
emesh = ev.to_mesh()
n_poly = len(emesh.polygons)
verts_w = [body.matrix_world @ v.co for v in emesh.vertices]
polys = [tuple(p.vertices) for p in emesh.polygons]
bvh = BVHTree.FromPolygons(verts_w, polys)
ev.to_mesh_clear()

json.dump({"vertices": len(body.data.vertices), "polygons": [list(p) for p in polys]}, open(os.path.join(outd, "mesh_polys.json"), "w"))
np.savez(os.path.join(outd, "body.npz"), verts=_verts, faces=_faces, bone_names=np.array(_bones, dtype=object), bone_heads=np.array(_heads, dtype=float))
meta = {"size": size, "polygons": n_poly, "ids": "<view>_ids.npy: int64 polygon index per pixel (row j, column i), -1 where nothing is hit", "views": {}}
orig_mat_colour = None
for name, direction in views.items():
    d = direction.normalized()
    cam.location = centre + d * dist
    cam.rotation_mode = "XYZ"
    look = (centre - cam.location).normalized()
    cam.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    # colour: the body's own look
    sh.color_type = "TEXTURE"
    scene.render.filepath = os.path.join(outd, f"{name}_color.png")
    bpy.ops.render.render(write_still=True)
    # ids: ray cast through every pixel centre of the orthographic camera
    import numpy as np
    right = cam.matrix_world.col[0].xyz.normalized()
    up = cam.matrix_world.col[1].xyz.normalized()
    fwd = -cam.matrix_world.col[2].xyz.normalized()
    half = cam_d.ortho_scale * 0.5
    ids = np.full((size, size), -1, dtype=np.int64)
    origin0 = cam.location - fwd * (dist * 2.0)
    for j in range(size):
        y = half - (j + 0.5) * (2 * half / size)
        for i in range(size):
            x = -half + (i + 0.5) * (2 * half / size)
            o = origin0 + right * x + up * y
            hit = bvh.ray_cast(o, fwd)
            if hit[0] is not None and hit[2] is not None:
                ids[j, i] = hit[2]
    np.save(os.path.join(outd, f"{name}_ids.npy"), ids)
    meta.setdefault("hit_pixels", {})[name] = int((ids >= 0).sum())
    meta["views"][name] = {"camera": list(cam.location), "direction": list(d)}
sh.color_type = "TEXTURE"
json.dump(meta, open(os.path.join(outd, "views.json"), "w"), indent=1)
print("VIEWS_OK", outd, n_poly)
