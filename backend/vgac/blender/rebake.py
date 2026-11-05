"""Re-bake the texture maps from the mesh the 3D builder painted onto the final prepared mesh (G05).

Loaded by prep.py inside Blender (not a script of its own and not imported by the backend):

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rebake

Why: the mesh stage bakes base colour, metallic, roughness, normal and AO onto its own low-poly mesh. When prep then
symmetrizes or decimates, the maps describe a different surface (wavy or faceted shading), and after symmetrize both
halves share one half of the atlas. This module copies every map from the source (the mesh stage's model, already
turned, scaled and moved like the target) onto the final mesh with Cycles "selected to active" bakes on the CPU:

- base colour, metallic, roughness and AO as emission transfers (the source's own maps, read at the hit point);
- normal as a tangent-space normal bake of the source's shading normal, which includes the source's normal map, so
  the fine detail baked from the dense mesh carries over and the result matches the final triangles and tangents.

When the target was symmetrized, its texture layout is first re-packed with the stacked mirrored halves kept together,
so the space the removed half used is reclaimed and the whole atlas is used (repack_mirrored).
"""
import os
import time

import bpy
from mathutils import Vector

# Map key -> (colour space of the image, bake kind). AO keeps its own resolution (the mesh stage bakes it at half size).
MAPS = {
    "base_color": ("sRGB", "EMIT"),
    "metallic": ("Non-Color", "EMIT"),
    "roughness": ("Non-Color", "EMIT"),
    "ao": ("Non-Color", "EMIT"),
    "normal": ("Non-Color", "NORMAL"),
}


def copy_object(obj, name):
    """A linked-into-the-scene copy of `obj` with its own mesh data (the bake source)."""
    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.name = name
    dup.modifiers.clear()
    bpy.context.scene.collection.objects.link(dup)
    return dup


def _diag(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (hi - lo).length


def _select_only(obj, active=True):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    if active:
        bpy.context.view_layer.objects.active = obj


def triangulate(obj):
    """Bake on triangles (MikkTSpace tangents then match what the engine computes from the exported triangles)."""
    _select_only(obj)
    mod = obj.modifiers.new("tri", "TRIANGULATE")
    mod.quad_method = "BEAUTY"
    mod.ngon_method = "BEAUTY"
    bpy.ops.object.modifier_apply(modifier=mod.name)


def repack_mirrored(obj, padding_px, tex):
    """Re-pack the texture layout after symmetrize. Symmetrize gives the mirrored half the kept half's UVs, so the two
    halves lie on top of each other and the pieces the removed half used are left empty: about half the atlas is
    never sampled. Packing with overlaps merged keeps each mirrored pair stacked (both halves share texels, as game
    artists mirror UVs) and scales the layout up to fill the square, so the texture is about twice as sharp as before
    the re-pack. (Giving each half its own space was tried: it reached 38 percent of a 1024 atlas on the knight against
    57 for the mesh stage, which is less sharp than the stacked layout.)"""
    _select_only(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.select_all(action="SELECT")
    margin = max(1, int(padding_px)) / float(max(1, tex))
    try:
        bpy.ops.uv.pack_islands(rotate=True, rotate_method="ANY", scale=True, merge_overlap=True,
                                margin_method="FRACTION", margin=margin, shape_method="CONCAVE")
    except (TypeError, RuntimeError) as e:
        print("REBAKE concave packing unavailable:", e)
        bpy.ops.uv.pack_islands(rotate=True, margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    return True


def _load(path, colorspace):
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = colorspace
    return img


def _source_material(images):
    """One material on the source whose output is rewired per bake (emission of one map, or a Principled BSDF with the
    normal map for the normal bake)."""
    mat = bpy.data.materials.new("vgac_bake_source")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    tex_nodes = {}
    for key, img in images.items():
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = img
        n.interpolation = "Linear"
        tex_nodes[key] = n
    if "normal" in tex_nodes:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(tex_nodes["normal"].outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    return mat, out, emit, bsdf, tex_nodes


def _wire(mat, out, emit, bsdf, tex_nodes, key):
    nt = mat.node_tree
    for link in list(out.inputs["Surface"].links):
        nt.links.remove(link)
    for link in list(emit.inputs["Color"].links):
        nt.links.remove(link)
    if key == "normal":
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    else:
        nt.links.new(tex_nodes[key].outputs["Color"], emit.inputs["Color"])
        nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])


def _final_material(images):
    """The material the exported GLB carries: base colour, metallic and roughness, normal map and occlusion through the
    glTF exporter's 'glTF Material Output' group."""
    mat = bpy.data.materials.new("vgac_baked")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF") or nt.nodes.new("ShaderNodeBsdfPrincipled")

    def tex(key):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = images[key]
        return n

    if "base_color" in images:
        nt.links.new(tex("base_color").outputs["Color"], bsdf.inputs["Base Color"])
    if "metallic" in images:
        nt.links.new(tex("metallic").outputs["Color"], bsdf.inputs["Metallic"])
    else:
        bsdf.inputs["Metallic"].default_value = 0.0
    if "roughness" in images:
        nt.links.new(tex("roughness").outputs["Color"], bsdf.inputs["Roughness"])
    if "normal" in images:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(tex("normal").outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if "ao" in images:
        try:
            grp = bpy.data.node_groups.get("glTF Material Output")
            if grp is None:
                grp = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
                grp.interface.new_socket("Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
            g = nt.nodes.new("ShaderNodeGroup")
            g.node_tree = grp
            nt.links.new(tex("ao").outputs["Color"], g.inputs["Occlusion"])
        except Exception as e:  # occlusion still ships as the ORM pack written by the export stage
            print("REBAKE occlusion output skipped:", e)
    return mat


def rebake(source, target, maps, out_dir, tex, padding_px, margin_px, repack=False, samples=4):
    """Bake every map in `maps` ({key: png path}) from `source` onto `target`, write <out_dir>/<key>.png, and give the
    target a material using the new images. Returns a report dict."""
    t0 = time.time()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    try:
        scene.cycles.device = "CPU"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False
    except AttributeError:
        pass
    scene.view_settings.view_transform = "Standard"

    repacked = repack_mirrored(target, padding_px, tex) if repack else False

    src_images = {}
    for key, path in maps.items():
        if key in MAPS and path and os.path.isfile(path):
            src_images[key] = _load(path, MAPS[key][0])
    mat, out, emit, bsdf, tex_nodes = _source_material(src_images)
    source.data.materials.clear()
    source.data.materials.append(mat)

    tmat = bpy.data.materials.new("vgac_bake_target")
    tmat.use_nodes = True
    tnt = tmat.node_tree
    target.data.materials.clear()
    target.data.materials.append(tmat)

    diag = _diag(target)
    bake = scene.render.bake
    bake.use_selected_to_active = True
    bake.use_cage = False
    bake.cage_extrusion = 0.01 * diag
    bake.max_ray_distance = 0.02 * diag
    bake.margin = int(margin_px)
    try:
        bake.margin_type = "EXTEND"
    except Exception:
        pass
    bake.target = "IMAGE_TEXTURES"
    bake.use_clear = True

    written, new_images, timings = {}, {}, {}
    for key in ("base_color", "metallic", "roughness", "ao", "normal"):
        if key not in src_images:
            continue
        colorspace, kind = MAPS[key]
        w, h = src_images[key].size[:]
        size = int(tex) if key != "ao" else int(max(w, h))
        img = bpy.data.images.new(f"baked_{key}", size, size, alpha=False, float_buffer=False)
        img.colorspace_settings.name = colorspace
        if key == "normal":
            img.generated_color = (0.5, 0.5, 1.0, 1.0)
        node = tnt.nodes.new("ShaderNodeTexImage")
        node.image = img
        for n in tnt.nodes:
            n.select = False
        node.select = True
        tnt.nodes.active = node
        _wire(mat, out, emit, bsdf, tex_nodes, key)
        bpy.ops.object.select_all(action="DESELECT")
        source.select_set(True)
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        t = time.time()
        if kind == "NORMAL":
            bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT", use_selected_to_active=True)
        else:
            bpy.ops.object.bake(type="EMIT", use_selected_to_active=True)
        timings[key] = round(time.time() - t, 2)
        path = os.path.join(out_dir, f"{key}.png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        written[key] = path
        new_images[key] = img

    target.data.materials.clear()
    target.data.materials.append(_final_material(new_images))
    bpy.data.objects.remove(source, do_unlink=True)
    return {"rebaked": True, "rebake_seconds": round(time.time() - t0, 2), "rebake_map_seconds": timings,
            "rebake_maps": written, "repacked": bool(repacked),
            "rebake_margin_px": int(margin_px), "rebake_padding_px": int(padding_px),
            "cage_extrusion_m": round(0.01 * diag, 5), "max_ray_distance_m": round(0.02 * diag, 5)}
