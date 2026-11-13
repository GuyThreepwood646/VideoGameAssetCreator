"""Your own 3D model: read one model file, make it one mesh with one set of texture maps, the shape step 3 hands on.

    blender -b --python modelimport.py -- <job.json>

job.json: {"src": <file>, "out_dir": <folder>, "tex": 2048, "max_triangles": 2000000, "turn": 0}

`turn` (0, 90, 180, 270) turns the model counter-clockwise seen from above after it is read, for a file whose front is
not Blender's front (-Y); many FBX files from Unity face +Y and need 180.

Reads GLB, glTF, FBX or OBJ (an OBJ's .mtl and pictures must sit next to it: the backend unpacks a zip there). Then:

1. Only meshes are kept. A skeleton is set aside (its modifiers removed, the mesh kept in its rest shape) and counted,
   so the report can say so; cameras, lights and empties go. Shape keys are dropped (the base shape stays), other
   modifiers are applied.
2. Every mesh is joined into one object with its transforms applied (so a centimetre FBX with a 0.01 scale comes out
   at its real size). Front stays -Y, up +Z, as the importers give.
3. The colours, in one of three ways (report "texture_mode"):
   - "kept": one material whose base colour comes straight from one picture, on the mesh's own texture layout. The
     picture is copied as base_color.png untouched. Metallic, roughness and normal are baked on the same layout when
     the material has them.
   - "baked": anything else with colour (several materials, colours from vertex paint, plain material colours,
     pictures through mix nodes, no texture layout). A new texture layout is made (smart UV project) and every map is
     baked onto it with Cycles on the CPU: each material's base colour, metallic and roughness are routed through an
     emission shader and baked as EMIT (the same transfer rebake.py uses), the normal as a tangent-space NORMAL bake.
   - "none": no material and no vertex colours. No maps; the model is exported grey.
   The CPU is used on purpose: both graphics cards may be busy with other jobs.
4. One material is put on the mesh with the maps, and <out_dir>/model.glb is written with one texture layout, no
   skin, no animation.

Writes <out_dir>/import_report.json: triangles, vertices, objects, materials, texture_mode, maps, uv_made, bones,
armature, dims, warnings, seconds; or {"error": code, "detail": ...} when the file cannot be used (codes: unreadable,
no_mesh, too_many_triangles, too_few_triangles). The backend turns the code into a plain sentence (vgac/model_import.py).
"""
import json
import math
import os
import sys
import time

import bpy
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

MIN_TRIANGLES = 20
BAKE_MARGIN = 8
COLOUR_SPACE = {"base_color": "sRGB", "metallic": "Non-Color", "roughness": "Non-Color", "normal": "Non-Color"}
SOCKET = {"base_color": "Base Color", "metallic": "Metallic", "roughness": "Roughness"}


class Refuse(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code, self.detail = code, detail


# ------------------------------------------------------------------------------------------------------- reading


def import_file(path):
    low = path.lower()
    try:
        if low.endswith(".fbx"):
            bpy.ops.import_scene.fbx(filepath=path)
        elif low.endswith(".obj"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            _common.import_glb(path)
    except Exception as e:  # noqa: BLE001
        raise Refuse("unreadable", str(e)[-300:])


def triangle_count(me):
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def flatten_scene(warnings):
    """-> (the one joined mesh object, bones set aside, armature found). Removes everything that is not a mesh."""
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise Refuse("no_mesh")
    arms = [o for o in scene.objects if o.type == "ARMATURE"]
    bones = sum(len(a.data.bones) for a in arms)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for o in meshes:
        for m in list(o.modifiers):
            if m.type == "ARMATURE":
                o.modifiers.remove(m)
        if o.data.shape_keys is not None:
            o.shape_key_clear()
    depsgraph.update()
    for o in meshes:
        # Apply what is left (mirror, subdivision, solidify...) by taking the evaluated mesh; keep the world matrix.
        mw = o.matrix_world.copy()
        if o.modifiers:
            new = bpy.data.meshes.new_from_object(o.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
            o.modifiers.clear()
            o.data = new
        o.parent = None
        o.matrix_world = mw
    for o in [o for o in scene.objects if o.type != "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)
    meshes = [o for o in scene.objects if o.type == "MESH" and triangle_count(o.data) > 0]
    if not meshes:
        raise Refuse("no_mesh")
    for o in scene.objects:
        o.select_set(o in meshes)
    bpy.context.view_layer.objects.active = meshes[0]
    # Different texture layout names would join into several layers; name the first layer of each the same.
    for o in meshes:
        if o.data.uv_layers:
            o.data.uv_layers[0].name = "UVMap"
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = "model"
    me = obj.data
    while len(me.uv_layers) > 1:
        me.uv_layers.remove(me.uv_layers[-1])
    if arms:
        warnings.append("skeleton_set_aside")
    return obj, bones, bool(arms)


# --------------------------------------------------------------------------------------------------- materials


def principled(mat):
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    for n in mat.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    return None


def linked_image(sock):
    """The picture feeding `sock` directly (an Image Texture node), else None."""
    if sock is None or not sock.is_linked:
        return None
    node = sock.links[0].from_node
    if node.type == "TEX_IMAGE" and node.image is not None:
        return node.image
    return None


def has_colour(obj):
    me = obj.data
    return any(m is not None for m in me.materials) or len(me.color_attributes) > 0


def image_pixels_ok(img):
    try:
        return img.size[0] > 0 and img.size[1] > 0  # reading size loads a packed picture
    except Exception:  # noqa: BLE001
        return False


def plan(obj):
    """-> ("kept", base image) | ("baked", None) | ("none", None)."""
    me = obj.data
    mats = [m for m in me.materials if m is not None]
    if not mats and not me.color_attributes:
        return "none", None
    if me.uv_layers and len(mats) >= 1:
        bsdfs = [principled(m) for m in mats]
        imgs = [linked_image(b.inputs.get("Base Color")) if b is not None else None for b in bsdfs]
        if imgs and all(i is not None for i in imgs) and len({i.name for i in imgs}) == 1 and image_pixels_ok(imgs[0]):
            return "kept", imgs[0]
    return "baked", None


def colour_from_attributes(obj):
    """A mesh with vertex colours and no material gets one that shows them, so the bake has something to read."""
    me = obj.data
    if any(m is not None for m in me.materials) or not me.color_attributes:
        return
    mat = bpy.data.materials.new("vertex_colour")
    mat.use_nodes = True
    nt = mat.node_tree
    b = principled(mat)
    attr = nt.nodes.new("ShaderNodeVertexColor")
    attr.layer_name = me.color_attributes[0].name
    nt.links.new(attr.outputs["Color"], b.inputs["Base Color"])
    me.materials.append(mat)


# ------------------------------------------------------------------------------------------------------------ bake


def new_image(key, tex):
    img = bpy.data.images.new(key, tex, tex, alpha=False, float_buffer=False)
    img.colorspace_settings.name = COLOUR_SPACE[key]
    return img


def setup_cycles():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.bake.margin = BAKE_MARGIN
    try:
        scene.render.bake.margin_type = "EXTEND"
    except Exception:  # noqa: BLE001
        pass


def materials_of(obj):
    return [m for m in obj.data.materials if m is not None and m.use_nodes]


def add_target(mats, img):
    """An unlinked, active Image Texture node holding `img` in every material: where the bake writes."""
    nodes = []
    for m in mats:
        n = m.node_tree.nodes.new("ShaderNodeTexImage")
        n.image = img
        n.interpolation = "Linear"
        m.node_tree.nodes.active = n
        nodes.append((m, n))
    return nodes


def remove_nodes(pairs):
    for m, n in pairs:
        m.node_tree.nodes.remove(n)


def route_to_emission(mats, key):
    """Each material's `key` input (its link or its constant) through an emission shader into the output. Returns
    what to undo. A material without a Principled node emits black for metallic, mid for roughness, its viewport
    colour for base colour."""
    undo = []
    for m in mats:
        nt = m.node_tree
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) or \
            next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if out is None:
            out = nt.nodes.new("ShaderNodeOutputMaterial")
        old = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].is_linked else None
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Strength"].default_value = 1.0
        b = principled(m)
        sock = b.inputs.get(SOCKET[key]) if b is not None else None
        added = [em]
        if sock is not None and sock.is_linked:
            nt.links.new(sock.links[0].from_socket, em.inputs["Color"])
        else:
            if sock is not None:
                val = sock.default_value
                col = (val[0], val[1], val[2], 1.0) if key == "base_color" else (float(val),) * 3 + (1.0,)
            else:
                col = tuple(m.diffuse_color) if key == "base_color" else ((0.0,) * 3 + (1.0,) if key == "metallic" else (0.5,) * 3 + (1.0,))
            em.inputs["Color"].default_value = col
        nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
        undo.append((m, out, old, added))
    return undo


def restore(undo):
    for m, out, old, added in undo:
        nt = m.node_tree
        for n in added:
            nt.nodes.remove(n)
        if old is not None:
            nt.links.new(old, out.inputs["Surface"])


def input_varies(mats, key):
    """True when any material feeds `key` from a link or differs from the Principled default."""
    defaults = {"metallic": 0.0, "roughness": 0.5}
    for m in mats:
        b = principled(m)
        if b is None:
            continue
        s = b.inputs.get(SOCKET[key])
        if s is None:
            continue
        if s.is_linked or abs(float(s.default_value) - defaults[key]) > 1e-3:
            return True
    return False


def has_normal_map(mats):
    for m in mats:
        b = principled(m)
        if b is not None and b.inputs.get("Normal") is not None and b.inputs["Normal"].is_linked:
            return True
    return False


def bake_map(obj, key, tex, out_dir):
    mats = materials_of(obj)
    img = new_image(key, tex)
    targets = add_target(mats, img)
    try:
        if key == "normal":
            bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT", margin=BAKE_MARGIN)
        else:
            undo = route_to_emission(mats, key)
            try:
                bpy.ops.object.bake(type="EMIT", margin=BAKE_MARGIN)
            finally:
                restore(undo)
    finally:
        remove_nodes(targets)
    path = os.path.join(out_dir, key + ".png")
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    return path, img


def make_atlas(obj):
    """A new texture layout for the bake; the old one (if any) stays the one pictures are read through."""
    me = obj.data
    old = me.uv_layers[0] if me.uv_layers else None
    atlas = me.uv_layers.new(name="atlas")
    if old is not None:
        old.active_render = True
    me.uv_layers.active = atlas
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.003, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    return old


def finish_layout(obj):
    """Only the bake's layout remains, under the usual name."""
    me = obj.data
    atlas = me.uv_layers.get("atlas")
    for layer in [x for x in me.uv_layers if x.name != "atlas"]:
        me.uv_layers.remove(layer)
    if atlas is not None:
        atlas.name = "UVMap"
        atlas.active_render = True


def final_material(obj, images):
    mat = bpy.data.materials.new("model")
    mat.use_nodes = True
    nt = mat.node_tree
    b = principled(mat)
    x = -600
    for key in ("base_color", "metallic", "roughness"):
        img = images.get(key)
        if img is None:
            continue
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = img
        n.location = (x, 300 - 300 * ("base_color", "metallic", "roughness").index(key))
        nt.links.new(n.outputs["Color"], b.inputs[SOCKET[key]])
    if images.get("normal") is not None:
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = images["normal"]
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(n.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], b.inputs["Normal"])
    me = obj.data
    me.materials.clear()
    me.materials.append(mat)


def save_copy(img, key, out_dir):
    """The picture as <key>.png (packed or on disk, any format), colour space kept."""
    path = os.path.join(out_dir, key + ".png")
    copy = img.copy()
    copy.filepath_raw = path
    copy.file_format = "PNG"
    copy.save()
    return path, copy


# ------------------------------------------------------------------------------------------------------------ main


def run(job):
    t0 = time.time()
    out_dir = job["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    tex = int(job.get("tex") or 2048)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_file(job["src"])
    warnings = []
    obj, bones, armature = flatten_scene(warnings)
    me = obj.data
    turn = int(job.get("turn") or 0) % 360
    if turn:
        me.transform(Matrix.Rotation(math.radians(turn), 4, "Z"))
        me.update()
    tris = triangle_count(me)
    if tris > int(job.get("max_triangles") or 2_000_000):
        raise Refuse("too_many_triangles", str(tris))
    if tris < MIN_TRIANGLES:
        raise Refuse("too_few_triangles", str(tris))
    materials = len([m for m in me.materials if m is not None])
    colour_from_attributes(obj)
    mode, base_img = plan(obj)
    maps, images, uv_made = {}, {}, False
    if mode != "none":
        setup_cycles()
        mats = materials_of(obj)
        if mode == "kept":
            maps["base_color"], images["base_color"] = save_copy(base_img, "base_color", out_dir)
            tex = max(base_img.size[0], base_img.size[1]) or tex
        else:
            make_atlas(obj)
            uv_made = True
            maps["base_color"], images["base_color"] = bake_map(obj, "base_color", tex, out_dir)
        for key in ("metallic", "roughness"):
            if input_varies(mats, key):
                maps[key], images[key] = bake_map(obj, key, tex, out_dir)
        if has_normal_map(mats):
            maps["normal"], images["normal"] = bake_map(obj, "normal", tex, out_dir)
        if uv_made:
            finish_layout(obj)
        final_material(obj, images)
    else:
        if not me.uv_layers:
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            make_atlas(obj)
            finish_layout(obj)
            uv_made = True
        warnings.append("no_colour")
    if not me.uv_layers:
        warnings.append("no_texture_layout")
    lo = [min(v.co[i] for v in me.vertices) for i in range(3)]
    hi = [max(v.co[i] for v in me.vertices) for i in range(3)]
    dims = [hi[i] - lo[i] for i in range(3)]
    if max(dims) > 50:
        warnings.append("very_large")
    elif max(dims) < 0.05:
        warnings.append("very_small")
    out = os.path.join(out_dir, "model.glb")
    for o in bpy.context.scene.objects:
        o.select_set(o == obj)
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True, export_apply=True, export_animations=False,
                              export_skins=False, export_morph=False)
    report = {"triangles": triangle_count(me), "vertices": len(me.vertices), "materials": materials, "texture_mode": mode,
              "maps": maps, "texture_size": tex if maps else None, "uv_made": uv_made, "bones": bones, "armature": armature, "turn": turn,
              "dims": [round(d, 4) for d in dims], "warnings": warnings, "seconds": round(time.time() - t0, 1)}
    return report


def main():
    job_path = _common.after_double_dash()[0]
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    report_path = os.path.join(job["out_dir"], "import_report.json")
    os.makedirs(job["out_dir"], exist_ok=True)
    try:
        report = run(job)
    except Refuse as e:
        report = {"error": e.code, "detail": e.detail}
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f)
    print("IMPORT_DONE", json.dumps(report)[:400])


main()
