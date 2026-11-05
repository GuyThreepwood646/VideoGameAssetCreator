"""Blender headless: a "beauty" picture of the finished asset in realistic light (PHOTOREAL-PLAN phase 1).

Run:  blender -b --python beauty.py -- <in.glb> <out.png> <hdri.hdr> [--size 1024] [--samples 48] [--sun-u 0.6]
                                                            [--strength 1.0] [--no-ground] [--pose rest|first]

Eevee render under the same CC0 light picture the app viewer uses for "Outdoors" (frontend/public/hdri). The light
picture lights the model and is what metal reflects; the camera sees a plain soft backdrop instead, so the picture
is about the asset. Its sun is turned to come from the front left of the camera (--sun-u is where the sun sits
across the picture, 0 to 1, measured once per light picture). The view transform is Khronos PBR Neutral, the same
tone curve as the app viewer, so painted colours stay as painted. A soft ground disc catches the shadow.
Writes <out.png> and <out>.json (seconds, size, engine, view transform, triangles).
"""
import json
import math
import os
import re
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
from mathutils import Vector  # noqa: E402

t0 = time.time()
argv = _common.after_double_dash()
src, out, hdri = os.path.abspath(argv[0]), os.path.abspath(argv[1]), os.path.abspath(argv[2])
size = int(_common.opt("--size", 1024))
samples = int(_common.opt("--samples", 32))
sun_u = float(_common.opt("--sun-u", 0.6))
strength = float(_common.opt("--strength", 1.0))
pose = _common.opt("--pose", "rest")
ground = not _common.flag("--no-ground")
os.makedirs(os.path.dirname(out), exist_ok=True)

_common.open_glb(src)
scene = bpy.context.scene

# Characters stand in their rest pose (the pose they were modelled in), not the first frame of whichever clip
# happens to be first.
for o in scene.objects:
    if o.type == "ARMATURE" and pose == "rest":
        o.data.pose_position = "REST"
    if o.animation_data:
        o.animation_data.action = None
bpy.context.view_layer.update()

meshes = [o for o in scene.objects if o.type == "MESH" and o.visible_get()]
# Colliders and helper shapes the export may carry are not part of the picture.
meshes = [o for o in meshes if not any(k in o.name.lower() for k in ("ucx_", "collider", "collision", "socket_"))]
# Older exports carry every detail level in one file: keep the full-detail one.
if any(re.search(r"_lod0$", o.name, re.I) for o in meshes):
    meshes = [o for o in meshes if not re.search(r"_lod[1-9]\d*$", o.name, re.I)]
if not meshes:
    raise SystemExit("no mesh in " + src)
for o in scene.objects:
    if o.type == "MESH" and o not in meshes:
        o.hide_render = True

deps = bpy.context.evaluated_depsgraph_get()
pts = []
tris = 0
for o in meshes:
    ev = o.evaluated_get(deps)
    m = ev.to_mesh()
    mw = o.matrix_world
    pts.extend(mw @ v.co for v in m.vertices)
    m.calc_loop_triangles()
    tris += len(m.loop_triangles)
    ev.to_mesh_clear()
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
centre = (lo + hi) / 2
dims = hi - lo
radius = max(dims.length / 2, 1e-3)

# ---- Light: the light picture for lighting and reflections, a plain backdrop for the camera ----
world = bpy.data.worlds.new("VGAC beauty")
scene.world = world
world.use_nodes = True
nt = world.node_tree
nt.nodes.clear()
coord = nt.nodes.new("ShaderNodeTexCoord")
mapping = nt.nodes.new("ShaderNodeMapping")
env = nt.nodes.new("ShaderNodeTexEnvironment")
env.image = bpy.data.images.load(hdri, check_existing=True)
bg_light = nt.nodes.new("ShaderNodeBackground")
bg_light.inputs["Strength"].default_value = strength
bg_cam = nt.nodes.new("ShaderNodeBackground")
bg_cam.inputs["Color"].default_value = (0.105, 0.112, 0.125, 1.0)
bg_cam.inputs["Strength"].default_value = 1.0
path = nt.nodes.new("ShaderNodeLightPath")
mix = nt.nodes.new("ShaderNodeMixShader")
outn = nt.nodes.new("ShaderNodeOutputWorld")
nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
nt.links.new(env.outputs["Color"], bg_light.inputs["Color"])
nt.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
nt.links.new(bg_light.outputs["Background"], mix.inputs[1])
nt.links.new(bg_cam.outputs["Background"], mix.inputs[2])
nt.links.new(mix.outputs["Shader"], outn.inputs["Surface"])

# Camera: three-quarter front (the asset faces -Y in Blender after the glTF import), a little above.
cam_yaw = math.radians(30)
cam_dir = Vector((math.sin(cam_yaw), -math.cos(cam_yaw), 0.0))
# The sun sits at azimuth atan2(y, -x) = (u - 0.5) * 2 pi in Blender's equirect lookup; turn it to 50 degrees left of
# the camera, seen from above, so the front is lit and the form still shows.
sun_now = (sun_u - 0.5) * 2 * math.pi + math.pi  # angle in Blender's lookup frame (checked by the shadow direction)
cam_az = math.atan2(cam_dir.y, -cam_dir.x)
sun_want = cam_az - math.radians(50)
mapping.inputs["Rotation"].default_value = (0.0, 0.0, sun_now - sun_want)

# ---- Ground disc: neutral and rough, so it catches the shadow and a little bounce ----
if ground:
    # It fades out toward its edge (blended), so there is no horizon line against the backdrop.
    disc_r = radius * 2.6
    bpy.ops.mesh.primitive_circle_add(vertices=96, radius=disc_r, fill_type="NGON", location=(centre.x, centre.y, lo.z))
    disc = bpy.context.active_object
    disc.name = "VGAC ground"
    mat = bpy.data.materials.new("VGAC ground")
    mat.use_nodes = True
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    mn = mat.node_tree
    bsdf = mn.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.11, 0.115, 0.125, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.95
    tc = mn.nodes.new("ShaderNodeTexCoord")
    sep = mn.nodes.new("ShaderNodeVectorMath")
    sep.operation = "LENGTH"
    # Object coordinates: distance from the disc centre in metres, mapped to 1 at the centre and 0 at the edge.
    ramp = mn.nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = disc_r * 0.35
    ramp.inputs["From Max"].default_value = disc_r
    ramp.inputs["To Min"].default_value = 1.0
    ramp.inputs["To Max"].default_value = 0.0
    mn.links.new(tc.outputs["Object"], sep.inputs[0])
    mn.links.new(sep.outputs["Value"], ramp.inputs["Value"])
    mn.links.new(ramp.outputs["Result"], bsdf.inputs["Alpha"])
    disc.data.materials.append(mat)

cam_data = bpy.data.cameras.new("VGAC camera")
cam_data.lens = 70
cam = bpy.data.objects.new("VGAC camera", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
fov = 2 * math.atan(cam_data.sensor_width / (2 * cam_data.lens))
dist = radius / math.sin(fov / 2) * 1.08
look = centre + Vector((0, 0, -dims.z * 0.03))
cam.location = look + cam_dir * dist + Vector((0, 0, dist * 0.22))
direction = look - cam.location
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
cam_data.clip_end = dist * 20
cam_data.clip_start = dist / 1000

# ---- Render settings ----
engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
ee = scene.eevee
for attr, val in (("taa_render_samples", samples), ("use_shadows", True), ("use_raytracing", True), ("use_gtao", True)):
    if hasattr(ee, attr):
        try:
            setattr(ee, attr, val)
        except Exception:
            pass
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
for vt in ("Khronos PBR Neutral", "AgX", "Filmic", "Standard"):
    try:
        scene.view_settings.view_transform = vt
        break
    except TypeError:
        continue
try:
    scene.view_settings.look = "None"
except TypeError:
    pass
scene.view_settings.exposure = 0.0
scene.render.filepath = out
bpy.ops.render.render(write_still=True)

info = {"seconds": round(time.time() - t0, 2), "size": size, "samples": samples, "engine": scene.render.engine,
        "view_transform": scene.view_settings.view_transform, "triangles": tris, "hdri": os.path.basename(hdri),
        "dims": [dims.x, dims.y, dims.z]}
with open(os.path.splitext(out)[0] + ".json", "w") as f:
    json.dump(info, f)
print("BEAUTY_OK", json.dumps(info))
