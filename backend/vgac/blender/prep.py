"""Blender headless: game-ready prep of a textured low-poly GLB (the first half of bakeoff/finish.py).

Run: blender -b --python prep.py -- <in.glb> <out.glb> <asset_name> [--size 1.8] [--size-axis height|horizontal|longest]
     [--yaw 0] [--symmetric] [--pivot base|center|corner] [--smooth-angle 60] [--shading smooth|flat] [--budget N]
     [--maps maps.json] [--tex 2048] [--padding 8] [--margin 16] [--rebake auto|always|never]
     [--grid 1] [--grid-footprint auto|1x1|2x1|2x2|4x1|4x4] [--grid-height auto|0.5|1|3|4] [--tiling out.png]
     [--protect-head 0.13 --head-gain 2]
     (--height H is the old form of --size H --size-axis height)

Grid (G15, scenery): with --grid, after the turn and the size, the piece is scaled per side onto the grid by
gridfit.plan_fit (width and depth to whole grid steps, height to half steps, thin sides left alone, at most 15
percent per side), the pivot goes on the minimum corner (glTF and Unity: lowest X, lowest Z, Y = 0; Blender: lowest
x, highest y, lowest z), and after the cleanup the snapped sides are trued up onto their targets (cleanup can move a
bound by a millimetre or two). The report gains "grid" (the plan, the measured fit and the tiling gap). With --tiling,
a Workbench picture of three copies in a row (walls) or a 3 by 3 block (floors) is rendered there after the export.

Re-bake (G05, rebake.py): with --maps (a JSON file {key: png} of the mesh stage's base_color, metallic, roughness,
normal and ao), a copy of the mesh is kept as the bake source after it is turned, scaled, moved and (when asked)
symmetrized. When the surface then changes (symmetrize, or the budget decimation) or --rebake always, every map is
baked again from that copy onto the final mesh and written next to <out> as <key>.png; after symmetrize the texture
layout is first re-packed so the half of the atlas symmetrize leaves empty is used again. The report records
rebaked, rebake_reason and rebake_seconds.

Weighted decimation (vgac/headtris.py, headweight.py): with --protect-head SHARE, the budget decimation keeps the head
(the top SHARE of the height) at about --head-gain times the triangles an even decimation would leave it. If that
fails, or the count is still over the budget, the plain decimation runs. The report always records head_triangles
(the final model's triangles in the top 13 percent of its height) and, when the weighted decimation ran,
head_weighted, head_triangles_even, head_triangles_source, head_weight_factor and head_weight_seconds.

Does, in order: import; join meshes; apply transforms; turn about the up axis by --yaw degrees (counter-clockwise seen
from above, so the concept's front ends up facing Blender -Y, which is +Z in glTF and Unity); scale uniformly so the
bounding-box extent on the size axis equals --size (height: Z; horizontal: the larger of X and Y; longest: the largest
of all three); move the pivot (base centre or bounding-box centre) to the origin; weld vertices split along UV chart
borders; optional symmetrize across X; smooth shading with auto-smooth at the crease angle, or with --shading flat (the low-poly recipe)
flat shading, every face its own normal, which the glTF export writes as split vertices; export GLB; write
<out>_report.json with triangle count, dimensions, the measured size, bone count and a manifold check.
"""
import json
import math
import os
import sys

import bmesh
import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
import gridfit  # noqa: E402  (pure maths next to this script)
import headweight  # noqa: E402
import rebake  # noqa: E402
from mathutils import Matrix, Vector

argv = _common.after_double_dash()
src, dst, name = os.path.abspath(argv[0]), os.path.abspath(argv[1]), argv[2]

legacy_height = _common.opt("--height", None)
size_m = float(_common.opt("--size", legacy_height if legacy_height is not None else 1.8))
size_axis = _common.opt("--size-axis", "height")
if size_axis not in ("height", "horizontal", "longest"):
    size_axis = "height"
yaw = float(_common.opt("--yaw", 0.0)) % 360
symmetric = _common.flag("--symmetric")
pivot = _common.opt("--pivot", "base")
smooth_angle = float(_common.opt("--smooth-angle", 60.0))
shading = _common.opt("--shading", "smooth")
if shading not in ("smooth", "flat"):
    shading = "smooth"
maps_json = _common.opt("--maps", None)
tex_size = int(_common.opt("--tex", 2048))
padding_px = int(_common.opt("--padding", max(4, tex_size // 256)))
margin_px = int(_common.opt("--margin", max(8, tex_size // 128)))
rebake_mode = _common.opt("--rebake", "auto")
maps = {}
if maps_json and os.path.isfile(maps_json):
    with open(maps_json) as f:
        maps = {k: v for k, v in json.load(f).items() if v and os.path.isfile(v)}
grid_arg = _common.opt("--grid", None)
grid_m = gridfit.grid_value(grid_arg) if grid_arg is not None else None
grid_footprint = _common.opt("--grid-footprint", "auto")
grid_height = _common.opt("--grid-height", "auto")
tiling_png = _common.opt("--tiling", None)
protect_head = float(_common.opt("--protect-head", 0) or 0)
head_gain = float(_common.opt("--head-gain", 2.0))
if grid_m is not None:
    pivot = "corner"
os.makedirs(os.path.dirname(dst), exist_ok=True)

_common.open_glb(src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
if not meshes:
    raise SystemExit("no mesh")
mesh = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.join()
arm = arms[0] if arms else None
root = arm if arm else mesh

bpy.ops.object.select_all(action="DESELECT")
root.select_set(True)
mesh.select_set(True)
bpy.context.view_layer.objects.active = root
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def bounds():
    pts = [mesh.matrix_world @ Vector(c) for c in mesh.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def extent(lo, hi):
    d = hi - lo
    if size_axis == "horizontal":
        return max(d.x, d.y)
    if size_axis == "longest":
        return max(d.x, d.y, d.z)
    return d.z


if yaw:
    root.rotation_mode = "XYZ"  # glTF imports use quaternions, which ignore rotation_euler
    root.rotation_euler = (0.0, 0.0, math.radians(yaw))
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

lo, hi = bounds()
s = size_m / max(1e-6, extent(lo, hi))
root.scale = (s, s, s)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
grid_plan = None
if grid_m is not None:
    lo, hi = bounds()
    grid_dims_before = [hi.x - lo.x, hi.y - lo.y, hi.z - lo.z]
    grid_plan = gridfit.plan_fit(grid_dims_before, grid_m, grid_footprint, grid_height)
    root.scale = tuple(grid_plan["scale"])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


def corner(lo, hi):
    """The minimum corner in glTF and Unity terms (lowest X, lowest Z, ground): Blender lowest x, highest y, lowest z."""
    return Vector((lo.x, hi.y, lo.z))


lo, hi = bounds()
if pivot == "center":
    base = (lo + hi) / 2
elif pivot == "corner":
    base = corner(lo, hi)
else:
    base = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
root.location -= base
if arm is not None and mesh.parent is None:
    mesh.location -= base
bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
lo, hi = bounds()

mesh.name = name
mesh.data.name = name

# Weld vertices split along UV chart borders by the unwrapper (UVs are per-loop, so they survive the merge).
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=0.00001)
bpy.ops.object.mode_set(mode="OBJECT")

if symmetric:
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.symmetrize(direction="POSITIVE_X", threshold=0.001)
    # Symmetrize leaves a few non-manifold edges and tiny gaps on the mirror plane: weld them and fill small holes.
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.0005)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.fill_holes(sides=8)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")

# The bake source: the mesh as the 3D builder painted it, turned, scaled and moved like the final mesh and, when
# symmetrized, already mirrored (its mirrored half samples the kept half's texels, so the colours and the bumps match
# the symmetric shape exactly instead of being fetched from the original, lopsided surface).
bake_source = rebake.copy_object(mesh, "vgac_bake_source") if maps and rebake_mode != "never" else None

# Triangle budget: symmetrize and welding can push the count over the platform budget; decimate back to it.
budget = int(_common.opt("--budget", 0))
mesh.data.calc_loop_triangles()
triangles_before_budget = len(mesh.data.loop_triangles)
decimated = bool(budget and len(mesh.data.loop_triangles) > budget)
head_report = {}


def plain_decimate():
    mod = mesh.modifiers.new("budget", "DECIMATE")
    mesh.data.calc_loop_triangles()
    mod.ratio = budget / len(mesh.data.loop_triangles) * 0.995
    mod.use_collapse_triangulate = True
    if symmetric:
        # Keep the mirror plane: collapses across it leave faces straddling x = 0 whose UVs fold over themselves,
        # and the re-bake then shows a bright or dark patch along the centre line (seen on the knight's nose).
        mod.use_symmetry = True
        mod.symmetry_axis = "X"
    bpy.ops.object.modifier_apply(modifier="budget")


if decimated:
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    weighted = False
    if protect_head > 0:
        try:
            head_report = headweight.decimate(mesh, budget / len(mesh.data.loop_triangles) * 0.995, protect_head, head_gain, symmetric)
            weighted = True
        except Exception as exc:  # the plain decimation below still brings the model to the budget
            for m in [m for m in mesh.modifiers if m.name == "budget"]:
                mesh.modifiers.remove(m)
            g = mesh.vertex_groups.get(headweight.GROUP)
            if g is not None:
                mesh.vertex_groups.remove(g)
            head_report = {"head_weight_error": str(exc)[:300]}
    if not weighted:
        plain_decimate()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.00001)
    bpy.ops.object.mode_set(mode="OBJECT")
    mesh.data.calc_loop_triangles()
    if len(mesh.data.loop_triangles) > budget:
        # Never hand on a model over the budget: decimate again, plainly.
        plain_decimate()
        head_report["head_weight_second_pass"] = True
    head_report["head_weighted"] = weighted

# Final manifold cleanup: weld the remaining non-manifold vertices a little more aggressively, fill small holes,
# drop loose geometry. Done last so it also covers artefacts from the budget decimation.
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="DESELECT")
bpy.ops.mesh.select_mode(type="VERT")
bpy.ops.mesh.select_non_manifold()
bpy.ops.mesh.remove_doubles(threshold=0.002, use_unselected=False)
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.fill_holes(sides=12)
bpy.ops.mesh.delete_loose()
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode="OBJECT")

reasons = [r for r, on in (("symmetrize", symmetric), ("decimate", decimated), ("asked", rebake_mode == "always")) if on]
if bake_source is not None and reasons:
    rebake.triangulate(mesh)  # before the auto-smooth modifier, so the bake and the export see the same triangles

# Smooth shading, split only at hard edges above the crease angle; or flat (every face its own normal).
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
if shading == "flat":
    for mod in [m for m in mesh.modifiers if m.type == "NODES" and "Smooth" in m.name]:
        mesh.modifiers.remove(mod)
    if mesh.data.has_custom_normals:
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    bpy.ops.object.shade_flat()
else:
    bpy.ops.object.shade_smooth()
    try:
        bpy.ops.object.shade_auto_smooth(angle=math.radians(smooth_angle))
    except Exception:
        pass

# Re-bake (G05) when the surface the maps were baked on changed, or when asked.
bake_report = {"rebaked": False, "rebake_reason": None, "rebake_seconds": 0.0}
if bake_source is not None and reasons:
    bake_report = rebake.rebake(bake_source, mesh, maps, os.path.dirname(dst), tex_size, padding_px, margin_px,
                                repack=symmetric)
    bake_report["rebake_reason"] = ", ".join(reasons)
elif bake_source is not None:
    bpy.data.objects.remove(bake_source, do_unlink=True)

grid_report = None
if grid_plan is not None:
    # True-up: welding, hole filling and decimation can move a bound by a millimetre or two; scale the snapped sides
    # back onto their targets (never more than gridfit.TRUE_UP_MAX) and put the corner back on the origin.
    bpy.context.view_layer.update()
    lo2, hi2 = bounds()
    d = hi2 - lo2
    states = [sd["state"] for sd in grid_plan["sides"]]
    f = [1.0, 1.0, 1.0]
    for i in range(3):
        t = grid_plan["dims"][i]
        if states[i] == "snapped" and d[i] > 0 and abs(t / d[i] - 1.0) <= gridfit.TRUE_UP_MAX:
            f[i] = t / d[i]
    mesh.data.transform(Matrix.Diagonal((f[0], f[1], f[2], 1.0)))
    mesh.data.update()
    bpy.context.view_layer.update()
    lo2, hi2 = bounds()
    mesh.data.transform(Matrix.Translation(-corner(lo2, hi2)))
    mesh.data.update()
    bpy.context.view_layer.update()
    lo, hi = bounds()
    final = [hi.x - lo.x, hi.y - lo.y, hi.z - lo.z]
    kind = gridfit.piece_kind(final)
    judged = gridfit.judge_fit(final, states, grid_m, grid_plan["dims"])
    tiles = gridfit.tile_layout(kind, final, states, grid_m)
    grid_report = {
        "grid_m": grid_m, "footprint_choice": grid_footprint, "height_choice": grid_height,
        "dims_before_m": grid_dims_before,
        "lead_scale": grid_plan["lead"], "scale": grid_plan["scale"], "true_up": f,
        "targets_m": grid_plan["dims"], "states": states, "cells": grid_plan["cells"], "footprint": grid_plan["footprint"],
        "height_m": final[2], "kind": kind, "dims_m": final, "fit": judged["fit"], "off_side": judged["off_side"],
        "off_size_m": judged["off_size"], "errors_m": judged["errors"], "tile_offsets": tiles["offsets"],
        "tile_axes": tiles["axes"], "tile_pitch_m": tiles["pitch"], "tile_gap_cm": tiles["gap_cm"],
    }

mesh.data.calc_loop_triangles()
bm = bmesh.new()
bm.from_mesh(mesh.data)
non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
boundary = sum(1 for e in bm.edges if e.is_boundary)
degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-12)
bm.free()
report = {
    "asset": name,
    "size_m": size_m,
    "size_axis": size_axis,
    "size_extent_m": extent(lo, hi),
    "yaw_deg": yaw,
    "height_m": hi.z - lo.z,
    "symmetric": symmetric,
    "pivot": pivot,
    "smooth_angle": smooth_angle,
    "shading": shading,
    "triangles": len(mesh.data.loop_triangles),
    "vertices": len(mesh.data.vertices),
    "uv_layers": len(mesh.data.uv_layers),
    "materials": len(mesh.data.materials),
    "bones": len(arm.data.bones) if arm else 0,
    "non_manifold_edges": non_manifold,
    "boundary_edges": boundary,
    "degenerate_faces": degenerate,
    "dims_m": [hi.x - lo.x, hi.y - lo.y, hi.z - lo.z],
    "min_m": [lo.x, lo.y, lo.z],
    "max_m": [hi.x, hi.y, hi.z],
    "decimated": decimated,
    "triangles_before_budget": triangles_before_budget,
    **head_report,
    # after the clean-up below the decimation: the final model's head (top 13 percent of the height, face.find_head)
    "head_triangles": headweight.head_count(mesh.data, headweight.head_cut(mesh, 0.13)[0]),
    "texture_size": tex_size,
    **bake_report,
}
if grid_report is not None:
    report["grid"] = grid_report

bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=True)
report["glb"] = dst


def render_tiling(path, offsets, res=768):
    """"How it tiles" (plan 3.8 A4): linked copies of the piece at the grid pitch, Workbench, three-quarter view from
    the front-left and above, with object outlines so the joins show. Runs after the export, so the copies never
    reach the GLB."""
    scene = bpy.context.scene
    objs = [mesh]
    for dx, dy in offsets[1:]:
        o = mesh.copy()
        o.location = (dx, dy, 0.0)
        scene.collection.objects.link(o)
        objs.append(o)
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo_t = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi_t = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    centre = (lo_t + hi_t) / 2
    radius = max(1e-3, (hi_t - lo_t).length / 2)
    cam_data = bpy.data.cameras.new("vgac_tiling_cam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("vgac_tiling_cam", cam_data)
    scene.collection.objects.link(cam)
    direction = Vector((-0.55, -0.8, 0.65)).normalized()  # front (-y) left (-x) and above
    dist = radius / math.sin(cam_data.angle / 2) * 1.05
    cam.location = centre + direction * dist
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_end = dist * 4
    scene.camera = cam
    world = bpy.data.worlds.new("vgac_tiling_world")
    world.color = (0.82, 0.82, 0.82)
    scene.world = world
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.light = "STUDIO"
    # Light fixed to the world and turned so the front and the top are lit from the camera side.
    shading.use_world_space_lighting = True
    shading.studiolight_rotate_z = math.radians(-35)
    # Textured pieces show their textures (seams in the pattern show at the joins); plain ones alternate two greys in
    # a checker, so each copy's edges and any overlap show.
    textured = any(n.type == "TEX_IMAGE" for m in mesh.data.materials if m and getattr(m, "node_tree", None) for n in m.node_tree.nodes)
    shading.color_type = "TEXTURE" if textured else "OBJECT"
    if not textured:
        # objs[k] sits at offsets[k]: a row of three, or a 3 by 3 block in rows of three.
        for k, o in enumerate(objs):
            cell = (k % 3 + k // 3) if len(offsets) > 3 else k
            o.color = (0.75, 0.72, 0.66, 1.0) if cell % 2 == 0 else (0.45, 0.47, 0.52, 1.0)
    shading.show_object_outline = True
    shading.object_outline_color = (0.1, 0.1, 0.1)
    shading.show_cavity = True
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    for o in objs[1:]:
        bpy.data.objects.remove(o, do_unlink=True)


if tiling_png and grid_report is not None:
    try:
        render_tiling(os.path.abspath(tiling_png), grid_report["tile_offsets"])
        report["tiling_png"] = os.path.abspath(tiling_png) if os.path.isfile(os.path.abspath(tiling_png)) else None
    except Exception as exc:  # the picture is a help, never a reason to fail prep
        report["tiling_png"] = None
        report["tiling_error"] = str(exc)
with open(os.path.splitext(dst)[0] + "_report.json", "w") as f:
    json.dump(report, f, indent=1)
print("PREP_OK", json.dumps(report))
