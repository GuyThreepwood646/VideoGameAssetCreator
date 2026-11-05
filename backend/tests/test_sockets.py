"""Attachment sockets (vgac/sockets.py): the placement maths on synthetic skeletons and closed tube meshes (plain
numpy, no Blender), the sockets.json contract and its GLB read-back, and the export stage's pack and Unity arguments."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vgac import sockets
from vgac.stages import export

REPO = Path(__file__).resolve().parents[2]
IMPORTER = REPO / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs"


# --- synthetic bodies: closed tubes around bones ---------------------------------------------------------------


def _tube_mesh(a, b, r, sides=16, rings=4):
    """A closed tube of radius r around segment a -> b: (vertices, triangles)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    u = (b - a) / np.linalg.norm(b - a)
    p = np.cross(u, [0.0, 0.0, 1.0]) if abs(u[2]) < 0.9 else np.cross(u, [1.0, 0.0, 0.0])
    p /= np.linalg.norm(p)
    q = np.cross(u, p)
    V, T = [], []
    for i in range(rings + 1):
        c = a + (b - a) * i / rings
        for k in range(sides):
            ang = 2 * np.pi * k / sides
            V.append(c + r * (np.cos(ang) * p + np.sin(ang) * q))
    for i in range(rings):
        for k in range(sides):
            a0, a1 = i * sides + k, i * sides + (k + 1) % sides
            b0, b1 = a0 + sides, a1 + sides
            T += [[a0, a1, b1], [a0, b1, b0]]
    V += [a, b]
    ca, cb = len(V) - 2, len(V) - 1
    for k in range(sides):
        T.append([ca, (k + 1) % sides, k])
        T.append([cb, rings * sides + k, rings * sides + (k + 1) % sides])
    return np.array(V), np.array(T)


def _mesh(bones, radii):
    V, T, owner = [], [], []
    for i, b in enumerate(bones):
        v, t = _tube_mesh(b["head"], b["tail"], radii.get(b["name"], 0.04))
        T.append(t + sum(len(x) for x in V))
        V.append(v)
        owner += [i] * len(v)
    return np.concatenate(V), np.concatenate(T), np.array(owner)


MIXAMO = {"hips": "Hips", "spine": "Spine", "chest": "Spine1", "neck": "Neck", "head": "Head", "head_end": "HeadTop_End",
          "clav_l": "LeftShoulder", "upper_l": "LeftArm", "fore_l": "LeftForeArm", "hand_l": "LeftHand",
          "clav_r": "RightShoulder", "upper_r": "RightArm", "fore_r": "RightForeArm", "hand_r": "RightHand",
          "thigh_l": "LeftUpLeg", "shin_l": "LeftLeg", "toe_l": "LeftFoot", "thigh_r": "RightUpLeg", "shin_r": "RightLeg",
          "toe_r": "RightFoot"}


def _humanoid():
    """A 1.85 m A-pose person with Mixamo names and fingers, Blender space (Z up, facing -Y; +X is its left)."""
    b = [("hips", None, (0, 0, 0.95), (0, 0, 1.15)), ("spine", "hips", (0, 0, 1.15), (0, 0, 1.3)),
         ("chest", "spine", (0, 0, 1.3), (0, 0, 1.48)), ("neck", "chest", (0, 0, 1.48), (0, 0, 1.6)),
         ("head", "neck", (0, 0, 1.6), (0, 0, 1.75)), ("head_end", "head", (0, 0, 1.75), (0, 0, 1.8))]
    for side, x in (("l", 1.0), ("r", -1.0)):
        S = "Left" if side == "l" else "Right"
        b += [(f"clav_{side}", "chest", (0.03 * x, 0, 1.45), (0.22 * x, 0, 1.45)),
              (f"upper_{side}", f"clav_{side}", (0.22 * x, 0, 1.45), (0.42 * x, 0, 1.25)),
              (f"fore_{side}", f"upper_{side}", (0.42 * x, 0, 1.25), (0.62 * x, 0, 1.08)),
              (f"hand_{side}", f"fore_{side}", (0.62 * x, 0, 1.08), (0.70 * x, 0, 1.0)),
              (f"{S}HandIndex1", f"hand_{side}", (0.70 * x, -0.03, 1.0), (0.74 * x, -0.03, 0.96)),
              (f"{S}HandMiddle1", f"hand_{side}", (0.71 * x, 0.0, 0.99), (0.75 * x, 0.0, 0.95)),
              (f"{S}HandPinky1", f"hand_{side}", (0.70 * x, 0.03, 1.0), (0.73 * x, 0.03, 0.97)),
              (f"{S}HandThumb1", f"hand_{side}", (0.64 * x, -0.04, 1.05), (0.67 * x, -0.07, 1.02)),
              (f"thigh_{side}", "hips", (0.1 * x, 0, 0.95), (0.1 * x, 0, 0.5)),
              (f"shin_{side}", f"thigh_{side}", (0.1 * x, 0, 0.5), (0.1 * x, 0, 0.08)),
              (f"toe_{side}", f"shin_{side}", (0.1 * x, 0, 0.08), (0.1 * x, -0.12, 0.03))]
    nm = lambda k: "mixamorig:" + MIXAMO.get(k, k)  # noqa: E731
    return [{"name": nm(n), "parent": nm(p) if p else None, "head": h, "tail": t} for n, p, h, t in b]


HUMAN_RADII = {"mixamorig:Hips": 0.16, "mixamorig:Spine": 0.15, "mixamorig:Spine1": 0.17, "mixamorig:Neck": 0.06,
               "mixamorig:Head": 0.1, "mixamorig:HeadTop_End": 0.08, "mixamorig:LeftHand": 0.035, "mixamorig:RightHand": 0.035,
               "mixamorig:LeftUpLeg": 0.08, "mixamorig:RightUpLeg": 0.08}


def _quadruped():
    """A 1.6 m long four-legged animal facing -Y, back at z 0.8, with a tail, a raised head and a jaw bone."""
    b = [("root", None, (0, 0.5, 0.8), (0, 0.2, 0.8)), ("mid", "root", (0, 0.2, 0.8), (0, -0.2, 0.8)),
         ("chest", "mid", (0, -0.2, 0.8), (0, -0.5, 0.85)), ("neck", "chest", (0, -0.5, 0.85), (0, -0.65, 1.05)),
         ("skull", "neck", (0, -0.65, 1.05), (0, -0.85, 1.1)), ("jaw", "neck", (0, -0.66, 1.0), (0, -0.82, 0.98)),
         ("tail1", "root", (0, 0.5, 0.8), (0, 0.7, 0.7)), ("tail2", "tail1", (0, 0.7, 0.7), (0, 0.85, 0.55))]
    for name, par, y in (("f", "chest", -0.45), ("b", "root", 0.45)):
        for side, x in (("l", 1), ("r", -1)):
            k = f"{name}{side}"
            b += [(f"{k}_top", par, (0.12 * x, y, 0.75), (0.15 * x, y, 0.55)),
                  (f"{k}_up", f"{k}_top", (0.15 * x, y, 0.55), (0.15 * x, y, 0.3)),
                  (f"{k}_low", f"{k}_up", (0.15 * x, y, 0.3), (0.15 * x, y, 0.05)),
                  (f"{k}_foot", f"{k}_low", (0.15 * x, y, 0.05), (0.15 * x, y - 0.08, 0.0))]
    return [{"name": n, "parent": p, "head": h, "tail": t} for n, p, h, t in b]


QUAD_RADII = {"root": 0.2, "mid": 0.2, "chest": 0.22, "neck": 0.09, "skull": 0.1, "jaw": 0.05, "tail1": 0.04, "tail2": 0.03}


def _frame_ok(s):
    r, u, f = (np.asarray(s[k], float) for k in ("right", "up", "forward"))
    assert np.linalg.norm(r) == pytest.approx(1, abs=1e-6) and np.linalg.norm(u) == pytest.approx(1, abs=1e-6)
    assert abs(float(r @ u)) < 1e-6 and abs(float(u @ f)) < 1e-6
    assert np.allclose(np.cross(u, f), r, atol=1e-6)  # X = Y x Z, a proper rotation


# --- maths ------------------------------------------------------------------------------------------------------


def test_ray_hits_and_surface_point_skip_masked_triangles():
    inner_v, inner_t = _tube_mesh((0, 0, -1), (0, 0, 1), 0.2)
    outer_v, outer_t = _tube_mesh((0, 0, -1), (0, 0, 1), 0.5)
    V = np.concatenate([inner_v, outer_v])
    T = np.concatenate([inner_t, outer_t + len(inner_v)])
    d = np.array([np.cos(0.1), np.sin(0.1), 0.0])  # off the corners and ring edges, so each shell is crossed once
    t = sockets.ray_hits((0, 0, 0.13), d, V, T)
    assert len(t) == 2 and t[0] == pytest.approx(0.2, abs=0.01) and t[1] == pytest.approx(0.5, abs=0.01)
    p, dist, how = sockets.surface_point((0, 0, 0), (1, 0, 0), V, T)
    assert how == "ray" and p[0] == pytest.approx(0.5 + sockets.SURFACE_OFFSET, abs=0.01)
    mask = np.r_[np.ones(len(inner_v), bool), np.zeros(len(outer_v), bool)]
    p, dist, how = sockets.surface_point((0, 0, 0), (1, 0, 0), V, T, mask)
    assert how == "ray" and p[0] == pytest.approx(0.2 + sockets.SURFACE_OFFSET, abs=0.01)  # the outer shell is not body


def test_humanoid_sockets_sit_where_they_should():
    bones = _humanoid()
    V, T, owner = _mesh(bones, HUMAN_RADII)
    res = sockets.compute(bones, V, owner, T, "humanoid")
    assert res["skeleton"] == "mixamo" and not res["skipped"]
    by = {s["name"]: s for s in res["sockets"]}
    assert list(by) == list(sockets.HUMANOID)
    assert by["Socket_Hand_R"]["bone"] == "mixamorig:RightHand" and by["Socket_Hand_L"]["bone"] == "mixamorig:LeftHand"
    assert by["Socket_Back"]["bone"] == by["Socket_Chest"]["bone"] == "mixamorig:Spine1"
    assert by["Socket_Hip_L"]["bone"] == by["Socket_Hip_R"]["bone"] == "mixamorig:Hips"
    assert by["Socket_Head"]["bone"] == "mixamorig:Head"
    for s in res["sockets"]:
        _frame_ok(s)
    # Hands: in the palm, between the wrist and the knuckles; the grip points forward (-Y), up toward the thumb.
    for name, x in (("Socket_Hand_R", -1), ("Socket_Hand_L", 1)):
        p = by[name]["position"]
        assert 0.62 < x * p[0] < 0.72 and 0.98 < p[2] < 1.08
        assert by[name]["forward"][1] < -0.9
    # Back and chest: just outside the torso (radius 0.17), at shoulder-blade height.
    back, chest = by["Socket_Back"]["position"], by["Socket_Chest"]["position"]
    assert 0.15 + sockets.SURFACE_OFFSET < back[1] < 0.17 + sockets.SURFACE_OFFSET + 1e-3 and 1.35 < back[2] < 1.45
    assert by["Socket_Back"]["forward"][1] > 0.99  # straight out of the back
    assert chest[1] < -0.15 and by["Socket_Chest"]["forward"][1] < -0.99
    # Hips: outside the outermost body surface on each side at the hip joint's height (there the thigh tops, out to
    # 0.18, stick out past the hips' 0.16), 2 cm out; left is +X.
    hl, hr = by["Socket_Hip_L"]["position"], by["Socket_Hip_R"]["position"]
    assert hl[0] == pytest.approx(0.18 + sockets.SURFACE_OFFSET, abs=0.005) and hr[0] == pytest.approx(-hl[0], abs=0.005)
    assert hl[2] == pytest.approx(0.95)
    # Head: on top of the head (the head-end tube reaches 1.8), on the centre line.
    top = by["Socket_Head"]["position"]
    assert 1.78 < top[2] < 1.81 and abs(top[0]) < 0.01


def test_quadruped_sockets_head_back_mouth():
    bones = _quadruped()
    V, T, owner = _mesh(bones, QUAD_RADII)
    res = sockets.compute(bones, V, owner, T, "quadruped")
    by = {s["name"]: s for s in res["sockets"]}
    assert list(by) == list(sockets.CREATURE)
    for s in res["sockets"]:
        _frame_ok(s)
        assert s["forward"][1] < -0.99  # the creature faces -Y
    head, back, mouth = by["Socket_Head"]["position"], by["Socket_Back"]["position"], by["Socket_Mouth"]["position"]
    assert head[2] > 1.12 and -0.9 < head[1] < -0.6
    assert by["Socket_Mouth"]["bone"] == "jaw" and mouth[1] < -0.75 and mouth[2] < head[2]
    # Saddle: on top of the back (body radius 0.2 around z 0.8), between the legs, behind the front ones.
    assert 1.0 < back[2] < 1.05 and -0.45 < back[1] < 0.45 and abs(back[0]) < 0.01


def test_unrigged_and_missing_skeleton():
    assert sockets.catalogue("prop_rigid") == {} and sockets.catalogue("environment") == {}
    assert sockets.compute([], np.zeros((3, 3)), np.zeros(3, int), None, "prop_rigid")["sockets"] == []
    res = sockets.compute([], np.zeros((3, 3)), np.zeros(3, int), None, "quadruped")
    assert res["sockets"] == [] and {s["name"] for s in res["skipped"]} == set(sockets.CREATURE)


def test_quaternion_round_trip():
    r, u, f = sockets.frame((0.3, 0.1, 1.0), (0, 1, 0))
    q = sockets.quat_from_frame(r, u, f)
    assert np.allclose(sockets.quat_to_matrix(q), np.column_stack([r, u, f]), atol=1e-9)
    r2, u2, f2 = sockets.frame((0, 0, 1), (0, 1, 0.3), keep_up=True)
    assert np.allclose(u2, np.array([0, 1, 0.3]) / np.linalg.norm([0, 1, 0.3])) and abs(float(u2 @ f2)) < 1e-9


# --- sockets.json and the GLB read-back ----------------------------------------------------------------------------


def test_json_contract_is_gltf_space_with_every_key():
    bones = _humanoid()
    V, T, owner = _mesh(bones, HUMAN_RADII)
    doc = sockets.to_json(sockets.compute(bones, V, owner, T, "humanoid"), bones)
    assert doc["version"] == 1 and doc["space"] == "gltf" and doc["units"] == "m"
    assert doc["count"] == 7 and doc["names"][0] == "Socket_Hand_R" and doc["glb_checked"] is False
    keys = {"name", "label", "purpose", "where", "bone", "position", "rotation", "forward", "up", "right", "bone_head",
            "offset", "local_position", "local_rotation", "local_space", "method", "sentence"}
    for s in doc["sockets"]:
        assert keys <= set(s) and all(v is not None for v in s.values())
        assert len(s["position"]) == 3 and len(s["rotation"]) == 4
    back = next(s for s in doc["sockets"] if s["name"] == "Socket_Back")
    assert back["forward"][2] < -0.99  # glTF: the front is +Z, so out of the back is -Z
    assert back["label"] == "Back" and back["sentence"].startswith("Back (Socket_Back): between the shoulder blades")
    hand = next(s for s in doc["sockets"] if s["name"] == "Socket_Hand_R")
    assert hand["position"][0] < 0 and hand["forward"][2] > 0.9  # right is -X; the grip points forward
    json.dumps(doc)


def _write_glb(path: Path, gltf: dict) -> None:
    js = json.dumps(gltf).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js)) + struct.pack("<II", len(js), 0x4E4F534A) + js)


def test_attach_glb_reads_joint_local_and_checks_position(tmp_path):
    doc = {"sockets": [{"name": "Socket_Head", "position": [0.0, 1.8, 0.1], "forward": [0, 0, 1], "up": [0, 1, 0],
                        "local_position": [0, 0, 0], "local_rotation": [0, 0, 0, 1]},
                       {"name": "Socket_Gone", "position": [0, 0, 0], "forward": [0, 0, 1], "up": [0, 1, 0]}]}
    # Armature root at the origin, a joint at y 1.6 turned 90 degrees about Y, the socket under it.
    s = np.sqrt(0.5)
    gltf = {"nodes": [{"name": "Armature", "children": [1]},
                      {"name": "Head", "translation": [0, 1.6, 0], "rotation": [0, s, 0, s], "children": [2]},
                      {"name": "Socket_Head", "translation": [-0.1, 0.2, 0.0], "rotation": [0, -s, 0, s]}]}
    glb = tmp_path / "x.glb"
    _write_glb(glb, gltf)
    out = sockets.attach_glb(doc, glb)
    head = out["sockets"][0]
    assert head["glb_parent"] == "Head" and head["local_space"] == "joint"
    assert head["local_position"] == pytest.approx([-0.1, 0.2, 0.0])
    assert out["glb_checked"] and out["glb_max_error"] < 1e-6 and out["glb_angle_error"] < 1e-3
    assert out["glb_missing"] == ["Socket_Gone"]


def test_manifest_block_and_metrics():
    assert sockets.manifest_block(None) is None and sockets.manifest_block({"sockets": []}) is None
    doc = {"space": "gltf", "sockets": [{"name": "Socket_Head", "label": "Head", "where": "on top of the head",
                                          "purpose": "a hat, helmet or crown", "bone": "bone_4"}]}
    m = sockets.manifest_block(doc, {"sockets": ["Socket_Head"]})
    assert m["count"] == 1 and m["names"] == ["Socket_Head"] and m["unity"] == ["Socket_Head"]
    assert m["sentences"] == ["Head (Socket_Head): on top of the head, for a hat, helmet or crown. Follows bone_4."]
    assert sockets.metrics(doc) == {"count": 1, "names": ["Socket_Head"]} and sockets.metrics(None) == {"count": 0, "names": []}


# --- export wiring --------------------------------------------------------------------------------------------------


def _sc(tmp_path, category, bones=40, rigid=False):
    rig = SimpleNamespace(metrics={"bones": bones, "rigid": rigid}, params={})
    return SimpleNamespace(dir=tmp_path, asset=SimpleNamespace(category=category), category={"rig_engine": "skintoken"},
                           stage_version=lambda s: rig, settings=SimpleNamespace(clips_libraries={}))


def test_socket_category_only_for_rigged_characters(tmp_path):
    assert export.socket_category(_sc(tmp_path, "humanoid")) == "humanoid"
    assert export.socket_category(_sc(tmp_path, "quadruped")) == "quadruped"
    assert export.socket_category(_sc(tmp_path, "quadruped", bones=0)) is None
    assert export.socket_category(_sc(tmp_path, "humanoid", rigid=True)) is None
    assert export.socket_category(_sc(tmp_path, "prop_rigid")) is None


def test_pack_args_pass_sockets_only_when_asked():
    look = {"shading": "smooth", "texture_filter": "smooth"}
    a = export.pack_args("in.glb", "out", "n", [0.5], "hull", ["fbx", "glb"], look, socket_cat="humanoid")
    assert a[a.index("--sockets") + 1] == "humanoid" and a[a.index("--lods") + 1] == "0.5"
    b = export.pack_args("in.glb", "out", "n", [], "parts", ["fbx"], look, collider_obj="c.obj", base_color="b.png")
    assert "--sockets" not in b and b[b.index("--lods") + 1] == "none"
    assert b[b.index("--collider-obj") + 1] == "c.obj" and b[b.index("--base-color") + 1] == "b.png"


def test_unity_args_pass_sockets(tmp_path):
    sc = _sc(tmp_path, "quadruped")
    assert "-vgacSockets" not in export._unity_args(sc, {}, "wolf")[0]
    (tmp_path / "sockets.json").write_text("{}")
    assert export._unity_args(sc, {}, "wolf")[0]["-vgacSockets"] == tmp_path / "sockets.json"
    m = export.unity_metrics({"ok": True, "sockets": ["Socket_Back"], "socketBones": ["Socket_Back>bone_1"], "socketMaxError": 0.0})
    assert m["sockets"] == ["Socket_Back"] and m["socketBones"] == ["Socket_Back>bone_1"]


def test_socket_howto_line():
    assert export.socket_howto([], True) is None
    h = export.socket_howto(["Socket_Hand_R", "Socket_Back"], True)
    assert 'GetBoneTransform(HumanBodyBones.RightHand).Find("Socket_Hand_R")' in h
    c = export.socket_howto(["Socket_Head", "Socket_Back"], False)
    assert 't.name == "Socket_Back"' in c


def test_importer_handles_sockets():
    src = IMPORTER.read_text(encoding="utf-8")
    assert export.importer_version(IMPORTER) >= 8
    assert '"-vgacSockets"' in src and "PlaceSockets" in src
    assert "bones.RemoveWhere(t => IsSocketName(t.name))" in src  # a socket is never mapped as a human bone
