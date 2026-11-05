"""Blender helper: bring a file of moves into the open scene and find its skeleton and clips.

Used by retarget.py (the hand-made library packs and a person's own files), moves_inspect.py and moves_preview.py.
Reads .glb/.gltf (the Quaternius packs), .fbx (Mixamo "without skin", Unity, Unreal) and .bvh (motion capture).

load(path, keep_meshes=False) -> {"arm": armature object, "actions": {name: action} (in file order),
    "fps": frames per second the actions' frame numbers count in, "ground": lowest skinned vertex z or None,
    "meshes": skinned meshes kept (keep_meshes) , "armatures": how many armatures the file had}
The scene's own frame rate is put back after an import that changed it (the FBX and BVH importers set it to the
file's), so the caller's scene keeps its rate and the returned fps says how to read this file's frames.
"""
import os
import re

import bpy

import gait


def _bvh_frame_time(path):
    try:
        with open(path, "r", errors="replace") as f:
            for _ in range(20000):
                line = f.readline()
                if not line:
                    break
                m = re.match(r"\s*Frame Time:\s*([0-9.eE+-]+)", line)
                if m:
                    return float(m.group(1))
    except OSError:
        pass
    return None


def canon(name):
    """An action name without the ".001" Blender adds to clashing names: 'Armature.001|Scene.002' -> 'Armature|Scene'."""
    return re.sub(r"\.\d{3}(?=$|\|)", "", name)


def _spine_up(arm):
    """True when the skeleton stands up in the scene: hips to head points mostly along +Z at rest."""
    shorts = gait.source_shorts([b.name for b in arm.data.bones])
    by = {s: n for n, s in shorts.items()}
    if "Hips" not in by or "Head" not in by:
        return True
    W = arm.matrix_world
    d = (W @ arm.data.bones[by["Head"]].head_local) - (W @ arm.data.bones[by["Hips"]].head_local)
    return d.length < 1e-9 or d.normalized().z > 0.7


def _skeleton_score(arm):
    return gait.recognise([b.name for b in arm.data.bones])["recognised"]


def load(path, keep_meshes=False):
    scene = bpy.context.scene
    fps0, base0 = scene.render.fps, scene.render.fps_base
    before = set(bpy.data.objects)
    acts_before = set(bpy.data.actions)
    ext = os.path.splitext(path)[1].lower()
    file_fps = None
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=False)
    elif ext == ".bvh":
        ft = _bvh_frame_time(path)
        file_fps = (1.0 / ft) if ft and ft > 1e-6 else 30.0
        # BVH is Y-up by convention; a file written Z-up (Blender's own BVH export) would lie on its back, so the
        # skeleton's spine decides and the file is read again the other way when it does not point up.
        for fwd, up in (("-Z", "Y"), ("Y", "Z")):
            objs0, acts0 = set(bpy.data.objects), set(bpy.data.actions)
            bpy.ops.import_anim.bvh(filepath=path, rotate_mode="NATIVE", axis_forward=fwd, axis_up=up, update_scene_fps=False,
                                    update_scene_duration=False, use_fps_scale=False, frame_start=1)
            got = [o for o in bpy.data.objects if o not in objs0 and o.type == "ARMATURE"]
            if not got or _spine_up(got[0]) or up == "Z":
                break
            for o in [o for o in bpy.data.objects if o not in objs0]:
                bpy.data.objects.remove(o, do_unlink=True)
            for a in [a for a in bpy.data.actions if a not in acts0]:
                bpy.data.actions.remove(a)
    else:
        try:
            bpy.ops.import_scene.gltf(filepath=path, bone_shape_type="NONE")
        except TypeError:
            bpy.ops.import_scene.gltf(filepath=path)
    after_fps = scene.render.fps / (scene.render.fps_base or 1.0)
    if file_fps is None:
        # glTF keys are converted with the scene rate; the FBX importer may set the scene to the file's rate first.
        file_fps = after_fps
    scene.render.fps, scene.render.fps_base = fps0, base0
    new = [o for o in bpy.data.objects if o not in before]
    arms = [o for o in new if o.type == "ARMATURE"]
    arm = max(arms, key=_skeleton_score) if arms else None
    ground, meshes = None, []
    for o in new:
        if o.type == "MESH" and o.vertex_groups and (arm is None or o.parent == arm or any(
                m.type == "ARMATURE" and m.object == arm for m in o.modifiers)):
            z = min(((o.matrix_world @ v.co).z for v in o.data.vertices), default=None)
            if z is not None:
                ground = z if ground is None else min(ground, z)
            if keep_meshes:
                meshes.append(o)
    for o in new:
        if o is arm or o in meshes:
            continue
        if o.type == "MESH" and o.name.lower().startswith("icosphere"):
            bpy.data.objects.remove(o, do_unlink=True)
            continue
        if o.type in ("MESH", "EMPTY", "CAMERA", "LIGHT", "CURVE") or (o.type == "ARMATURE" and o is not arm):
            if o.type == "EMPTY" and arm is not None and arm.parent is o:
                continue  # an FBX root empty that carries the armature's scale
            bpy.data.objects.remove(o, do_unlink=True)
    actions = {}
    for a in bpy.data.actions:
        if a not in acts_before and a.name not in actions:
            # Blender adds ".001" when the open scene already has an action of that name: key it by the file's own
            # name, so the name read in an empty scene (moves_preview) finds it again here.
            # The FBX importer also names an action after its object ("Armature.001|Take"), renamed the same way.
            base = canon(a.name)
            key = base if base != a.name and base not in actions and not any(x.name == base for x in bpy.data.actions if x not in acts_before) else a.name
            actions[key] = a
    if arm is not None and arm.animation_data:
        arm.animation_data.action = None
        for tr in list(arm.animation_data.nla_tracks):
            arm.animation_data.nla_tracks.remove(tr)
    return {"arm": arm, "actions": actions, "fps": float(file_fps), "ground": ground, "meshes": meshes,
            "armatures": len(arms)}


def use_action(arm, act):
    """Make `act` the armature's action (and its first slot on Blender 4.4 and later)."""
    ad = arm.animation_data or arm.animation_data_create()
    if ad.action is not act:
        ad.action = act
        if hasattr(ad, "action_slot") and len(getattr(act, "slots", [])) > 0:
            ad.action_slot = act.slots[0]
