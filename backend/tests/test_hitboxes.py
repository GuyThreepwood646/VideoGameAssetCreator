"""Hit boxes (plan section 4.2, G34): the fitting maths in blender/hitboxes.py on synthetic skeletons and weights
(plain numpy, no Blender), and the rig stage's knobs, checks and the re-fit path of adjust() with a fake context."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vgac.stages import rig

HB_PATH = Path(__file__).resolve().parent.parent / "vgac" / "blender" / "hitboxes.py"
_spec = importlib.util.spec_from_file_location("vgac_hitboxes_under_test", HB_PATH)
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)


# --- synthetic bodies -------------------------------------------------------------------------------------------


def _tube(a, b, r, n, rng):
    """n points on the surface of a tube of radius r around segment a -> b."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    u = (b - a) / np.linalg.norm(b - a)
    p = np.cross(u, [0.0, 0.0, 1.0]) if abs(u[2]) < 0.9 else np.cross(u, [1.0, 0.0, 0.0])
    p /= np.linalg.norm(p)
    q = np.cross(u, p)
    t = rng.uniform(0, 1, n)
    ang = rng.uniform(0, 2 * np.pi, n)
    return a + np.outer(t, b - a) + r * (np.outer(np.cos(ang), p) + np.outer(np.sin(ang), q))


def _body(bones, radii, per_bone=200, seed=1):
    """Vertices around every bone, each owned by that bone (its strongest weight)."""
    rng = np.random.default_rng(seed)
    V, owner = [], []
    for i, b in enumerate(bones):
        r = radii.get(b["name"], 0.05)
        V.append(_tube(b["head"], b["tail"], r, per_bone, rng))
        owner += [i] * per_bone
    return np.concatenate(V), np.array(owner)


def _humanoid(names=None):
    """A generic A-pose humanoid, 1.8 m, Blender space (Z up, facing -Y). +X is its left."""
    n = names or {}
    nm = lambda k: n.get(k, k)  # noqa: E731
    bones = [
        ("hips", None, (0, 0, 0.95), (0, 0, 1.15)),
        ("spine", "hips", (0, 0, 1.15), (0, 0, 1.3)),
        ("chest", "spine", (0, 0, 1.3), (0, 0, 1.48)),
        ("neck", "chest", (0, 0, 1.48), (0, 0, 1.6)),
        ("head", "neck", (0, 0, 1.6), (0, 0, 1.78)),
        ("head_end", "head", (0, 0, 1.78), (0, 0, 1.85)),
    ]
    for side, x in (("l", 1.0), ("r", -1.0)):
        bones += [
            (f"clav_{side}", "chest", (0.03 * x, 0, 1.45), (0.22 * x, 0, 1.45)),
            (f"upper_{side}", f"clav_{side}", (0.22 * x, 0, 1.45), (0.42 * x, 0, 1.25)),
            (f"fore_{side}", f"upper_{side}", (0.42 * x, 0, 1.25), (0.62 * x, 0, 1.08)),
            (f"hand_{side}", f"fore_{side}", (0.62 * x, 0, 1.08), (0.72 * x, 0, 1.0)),
            (f"thigh_{side}", "hips", (0.1 * x, 0, 0.95), (0.1 * x, 0, 0.5)),
            (f"shin_{side}", f"thigh_{side}", (0.1 * x, 0, 0.5), (0.1 * x, 0, 0.08)),
            (f"toe_{side}", f"shin_{side}", (0.1 * x, 0, 0.08), (0.1 * x, -0.12, 0.03)),
        ]
    return [{"name": nm(a), "parent": nm(p) if p else None, "head": h, "tail": t} for a, p, h, t in bones]


HUMAN_RADII = {"hips": 0.16, "spine": 0.15, "chest": 0.17, "neck": 0.06, "head": 0.1, "head_end": 0.08}


def _human_radii(bones):
    out = {}
    for b in bones:
        base = b["name"].split(":")[-1]
        out[b["name"]] = HUMAN_RADII.get(base, 0.08 if "thigh" in base or "UpLeg" in base else 0.05)
    return out


# --- shape maths ------------------------------------------------------------------------------------------------


def test_capsule_on_a_tube_and_snugness():
    rng = np.random.default_rng(0)
    P = _tube((0, 0, 0), (0, 0, 1), 0.1, 4000, rng)
    c = hb.fit_capsule(P, (0, 0, 0), (0, 0, 1), 1.0)
    assert abs(abs(c["axis"][2]) - 1) < 1e-6
    assert c["radius"] == pytest.approx(0.1, rel=0.02)
    assert c["height"] == pytest.approx(1.0, abs=0.01)  # bone + radius at both ends, clipped to the points
    assert np.allclose(c["center"], (0, 0, 0.5), atol=0.01)
    snug = hb.fit_capsule(P, (0, 0, 0), (0, 0, 1), hb.FIT_FACTORS["snug"])
    loose = hb.fit_capsule(P, (0, 0, 0), (0, 0, 1), hb.FIT_FACTORS["loose"])
    assert snug["radius"] == pytest.approx(0.09, rel=0.02) and loose["radius"] == pytest.approx(0.115, rel=0.02)


def test_capsule_radius_is_the_80th_percentile_and_short_bone_extends():
    rng = np.random.default_rng(3)
    P = np.concatenate([_tube((0, 0, 0), (0, 0, 1), 0.1, 800, rng), _tube((0, 0, 0), (0, 0, 1), 0.3, 200, rng)])
    c = hb.fit_capsule(P, (0, 0, 0.4), (0, 0, 0.6), 1.0)
    assert 0.1 <= c["radius"] < 0.3  # 80 percent of the points are on the thin tube
    assert c["height"] == pytest.approx(0.2 + 2 * c["radius"], abs=0.01)  # a short bone pushed out by the radius
    assert hb.fit_capsule(P, (0, 0, 0.4), (0, 0, 0.6), ends="points")["height"] > 0.95


def test_box_keeps_a_minimum_thickness():
    rng = np.random.default_rng(1)
    P = np.c_[rng.uniform(0, 1.0, 2000), rng.uniform(0, 0.4, 2000), np.zeros(2000)]  # a flat wing
    b = hb.fit_box(P)
    assert b["kind"] == "box" and min(b["size"]) == pytest.approx(hb.MIN_BOX_THICKNESS)
    assert b["size"][1] == pytest.approx(0.96, abs=0.03)  # Y runs along the longest spread
    assert abs(abs(b["forward"][2]) - 1) < 1e-6  # Z is the thin direction


def test_inside_volume_and_mesh_volume():
    sphere = {"kind": "sphere", "center": np.zeros(3), "axis": hb.UP, "radius": 0.5, "height": 1.0}
    assert hb.union_volume([sphere], res=64) == pytest.approx(4 / 3 * np.pi * 0.125, rel=0.03)
    cap = {"kind": "capsule", "center": np.zeros(3), "axis": np.array([1.0, 0, 0]), "radius": 0.1, "height": 1.0}
    assert hb.shape_volume(cap) == pytest.approx(np.pi * 0.01 * 0.8 + 4 / 3 * np.pi * 0.001)
    assert hb.union_volume([cap, cap]) == pytest.approx(hb.shape_volume(cap), rel=0.05)  # overlap counted once
    pts = np.array([[0.45, 0, 0], [0.505, 0, 0], [0, 0.105, 0], [0, 0.2, 0]])
    assert hb.inside(cap, pts).tolist() == [True, False, False, False]
    assert hb.inside(cap, pts, tol=0.1).tolist() == [True, True, True, False]
    V = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
    T = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    assert hb.mesh_volume(V, T) == pytest.approx(1.0)


def test_gltf_axes_match_rig_info():
    assert hb.to_gltf((1, 2, 3)) == [1.0, 3.0, -2.0]


# --- regions ----------------------------------------------------------------------------------------------------


def test_generic_humanoid_detailed_eleven_parts_left_is_plus_x():
    bones = _humanoid()
    V, owner = _body(bones, _human_radii(bones))
    res = hb.fit(bones, V, owner, None, "humanoid", "detailed")
    assert not res["fallback"] and res["skeleton"] == "generic"
    regions = {s["region"] for s in res["shapes"]}
    assert regions == {"head", "chest", "hips", "upper_arm_l", "forearm_l", "upper_arm_r", "forearm_r", "thigh_l", "shin_l", "thigh_r", "shin_r"}
    by = {s["region"]: s for s in res["shapes"]}
    assert by["upper_arm_l"]["center"][0] > 0 and by["upper_arm_r"]["center"][0] < 0
    assert by["upper_arm_l"]["bone"] == "upper_l"  # the collarbone went to the chest
    assert by["head"]["center"][2] > 1.5
    assert res["cover"] > 0.85


def test_humanoid_simple_is_head_and_body_spanning_the_legs():
    bones = _humanoid()
    V, owner = _body(bones, _human_radii(bones))
    res = hb.fit(bones, V, owner, None, "humanoid", "simple")
    by = {s["region"]: s for s in res["shapes"]}
    assert set(by) == {"head", "body"}
    body = by["body"]
    bottom = body["center"][2] - body["height"] / 2
    assert bottom < 0.15  # the body capsule reaches the feet, not only the torso bones


def test_mixamo_names_used_when_sane_and_ignored_when_shifted():
    good = {"hips": "mixamorig:Hips", "spine": "mixamorig:Spine", "chest": "mixamorig:Spine1", "neck": "mixamorig:Neck",
            "head": "mixamorig:Head", "head_end": "mixamorig:HeadTop_End", "clav_l": "mixamorig:LeftShoulder",
            "upper_l": "mixamorig:LeftArm", "fore_l": "mixamorig:LeftForeArm", "hand_l": "mixamorig:LeftHand",
            "clav_r": "mixamorig:RightShoulder", "upper_r": "mixamorig:RightArm", "fore_r": "mixamorig:RightForeArm",
            "hand_r": "mixamorig:RightHand", "thigh_l": "mixamorig:LeftUpLeg", "shin_l": "mixamorig:LeftLeg",
            "toe_l": "mixamorig:LeftFoot", "thigh_r": "mixamorig:RightUpLeg", "shin_r": "mixamorig:RightLeg", "toe_r": "mixamorig:RightFoot"}
    bones = _humanoid(good)
    V, owner = _body(bones, _human_radii(bones))
    res = hb.fit(bones, V, owner, None, "humanoid", "detailed")
    assert res["skeleton"] == "mixamo" and len(res["shapes"]) == 11
    assert {s["region"]: s["bone"] for s in res["shapes"]}["forearm_l"] in ("mixamorig:LeftForeArm", "mixamorig:LeftHand")
    # SkinToken sometimes shifts the names along one arm: "Head" sits on the arm. The names are then not trusted.
    shifted = dict(good, neck="mixamorig:Extra_04", head="mixamorig:Extra_05", head_end="mixamorig:Extra_06",
                   clav_l="mixamorig:Neck", upper_l="mixamorig:Head", fore_l="mixamorig:LeftShoulder", hand_l="mixamorig:LeftArm")
    bones = _humanoid(shifted)
    V, owner = _body(bones, _human_radii(bones))
    res = hb.fit(bones, V, owner, None, "humanoid", "detailed")
    assert res["skeleton"] == "generic"
    assert {s["region"]: s["bone"] for s in res["shapes"]}["head"] in ("mixamorig:Extra_05", "mixamorig:Extra_04", "mixamorig:Extra_06")


def _quadruped():
    """A 1.6 m long four-legged animal facing -Y, back at z 0.8, with a tail and a raised head."""
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


def test_quadruped_detailed_eight_and_simple_two():
    bones = _quadruped()
    radii = {"root": 0.2, "mid": 0.2, "chest": 0.22, "neck": 0.09, "skull": 0.1, "jaw": 0.05, "tail1": 0.04, "tail2": 0.03}
    V, owner = _body(bones, radii)
    res = hb.fit(bones, V, owner, None, "quadruped", "detailed")
    assert not res["fallback"]
    assert {s["region"] for s in res["shapes"]} == {"head", "neck", "body", "tail", "leg_fl", "leg_fr", "leg_bl", "leg_br"}
    by = {s["region"]: s for s in res["shapes"]}
    assert by["leg_fl"]["center"][0] > 0 and by["leg_fl"]["center"][1] < 0  # left (+X), front (-Y)
    assert by["leg_fl"]["radius"] < 0.1  # the shoulder bone's vertices went to the body
    assert res["movement"]["default"] == "along"
    simple = hb.fit(bones, V, owner, None, "quadruped", "simple")
    assert {s["region"] for s in simple["shapes"]} == {"head", "body"}


def test_fallback_when_no_limbs_can_be_found():
    bones = [{"name": "a", "parent": None, "head": (0, 0, 0.1), "tail": (0, 0, 0.9)},
             {"name": "b", "parent": "a", "head": (0, 0, 0.9), "tail": (0, 0, 1.7)}]
    V, owner = _body(bones, {"a": 0.2, "b": 0.15})
    res = hb.fit(bones, V, owner, None, "humanoid", "detailed")
    assert res["fallback"] and [s["kind"] for s in res["shapes"]] == ["sphere", "capsule"]
    head, body = res["shapes"]
    assert head["region"] == "head" and head["center"][2] > 1.4 and head["bone"] == "b"
    assert body["bone"] == "a"
    doc = hb.to_json(res, bones)
    assert doc["fallback"] is True and doc["count"] == 2 and doc["cover_min"] == hb.LIMITS["simple"][0]


def test_serpent_chain_counts():
    bones = [{"name": f"s{i}", "parent": f"s{i - 1}" if i else None, "head": (0, 0.1 * i, 0.1), "tail": (0, 0.1 * (i + 1), 0.1)} for i in range(30)]
    bones[-1]["tail"] = (0, 3.1, 0.3)
    for b in bones[-3:]:
        b["head"] = (b["head"][0], b["head"][1], 0.25)
    V, owner = _body(bones, {b["name"]: 0.05 for b in bones}, per_bone=40)
    det = hb.fit(bones, V, owner, None, "serpent", "detailed")
    assert sum(1 for s in det["shapes"] if s["region"].startswith("spine_")) == 10 and any(s["region"] == "head" for s in det["shapes"])
    head = next(s for s in det["shapes"] if s["region"] == "head")
    assert head["center"][1] > 2.5  # the raised end is the head
    simple = hb.fit(bones, V, owner, None, "serpent", "simple")
    assert len(simple["shapes"]) == 4


def test_regions_filter_and_none_mode_keep_the_movement_shape():
    bones = _humanoid()
    V, owner = _body(bones, _human_radii(bones))
    res = hb.fit(bones, V, owner, None, "humanoid", "detailed", keep_regions=["head", "chest"])
    assert {s["region"] for s in res["shapes"]} == {"head", "chest"} and len(res["regions_found"]) == 11
    none = hb.fit(bones, V, owner, None, "humanoid", "none")
    doc = hb.to_json(none, bones)
    assert doc["count"] == 0 and doc["mode"] == "none" and doc["movement"]["default"] == "upright"


def test_json_contract_keys():
    bones = _humanoid()
    V, owner = _body(bones, _human_radii(bones))
    doc = hb.to_json(hb.fit(bones, V, owner, None, "humanoid", "detailed"), bones)
    json.dumps(doc)
    assert {"version", "space", "units", "category", "mode", "fit", "factor", "fallback", "skeleton", "count", "regions_found",
            "skipped", "hitboxes", "movement", "cover", "overreach", "cover_min", "overreach_max"} <= set(doc)
    box = doc["hitboxes"][0]
    assert set(box) == {"name", "region", "label", "bone", "kind", "center", "axis", "forward", "radius", "height", "size",
                        "bone_head", "bone_tail", "offset", "vertices"}
    assert all(b["name"].startswith("Hit_") for b in doc["hitboxes"])
    up = doc["movement"]["upright"]
    assert up["direction"] == 1 and up["height"] == pytest.approx(V[:, 2].max() - V[:, 2].min(), abs=1e-3)
    assert set(doc["movement"]) == {"default", "upright", "along", "box"}


# --- rig stage: knobs, checks, re-fit -------------------------------------------------------------------------


def test_hitbox_params_defaults_by_platform():
    assert rig.hitbox_params("mobile_low", {})["hitboxes"] == "simple"
    assert rig.hitbox_params("pc_mid", {})["hitboxes"] == "detailed"
    assert rig.hitbox_params("high", {"hitbox_fit": "bogus"}) == {"hitboxes": "detailed", "hitbox_fit": "normal", "hitbox_regions": None}
    assert rig.hitbox_params("pc_mid", {"hitbox_regions": "head, tail"})["hitbox_regions"] == ["head", "tail"]


def test_hitbox_checks():
    doc = {"mode": "detailed", "count": 11, "cover": 0.9, "overreach": 1.8, "cover_min": 0.85, "overreach_max": 2.75}
    checks = {c["name"]: c for c in rig.hitbox_checks(doc)}
    assert set(checks) == {"hitbox_cover", "hitbox_overreach"} and all(c["passed"] for c in checks.values())
    assert checks["hitbox_cover"]["vars"] == {"n": 11, "cover": "90%"}
    bad = {c["name"]: c for c in rig.hitbox_checks(dict(doc, cover=0.6, overreach=3.2))}
    assert not bad["hitbox_cover"]["passed"] and not bad["hitbox_overreach"]["passed"]
    assert bad["hitbox_overreach"]["vars"]["times"] == "3.2"
    failed = rig.hitbox_checks(None, "blender exploded")
    assert [c["name"] for c in failed] == ["hitbox_fit"] and not failed[0]["passed"]
    assert rig.hitbox_checks({"mode": "none", "count": 0}) == []


def test_only_hitbox_change():
    parent = {"template": "auto", "hitboxes": "detailed", "hitbox_fit": "normal"}
    assert rig.only_hitbox_change(parent, {"hitbox_fit": "loose"})
    assert rig.only_hitbox_change(parent, {"hitbox_fit": "loose", "template": "auto"})  # unchanged extras are fine
    assert not rig.only_hitbox_change(parent, {"hitbox_fit": "loose", "group_per_vertex": 3})
    assert not rig.only_hitbox_change(parent, {})  # "Try again" rigs again
    assert not rig.only_hitbox_change(parent, {"hitbox_fit": "normal"})


class _RefitSC:
    def __init__(self, tmp: Path, parent, version):
        self.dir = tmp / "new"
        self.dir.mkdir()
        self.settings = SimpleNamespace(blender_timeout_seconds=60)
        self.asset = SimpleNamespace(category="humanoid", id="a" * 32)
        self.platform = "pc_mid"
        self.version = version
        self._parent = parent
        self.files: dict[str, Path] = {}
        self.blender_calls: list[tuple[str, list]] = []

    @property
    def parent(self):
        return self._parent

    def progress(self, *_a):
        pass

    def register(self, key, path):
        self.files[key] = Path(path)
        self.version.files[key] = str(path)

    def file(self, key):
        p = self.files.get(key)
        return p if p is not None and p.is_file() else None

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    async def blender(self, script, args, log_name=None, timeout=0):
        self.blender_calls.append((script, [str(a) for a in args]))
        out = Path(args[1])
        out.write_text(json.dumps({"mode": "detailed", "fit": args[args.index("--fit") + 1], "factor": 1.15, "count": 11,
                                   "fallback": False, "hitboxes": [{"name": "Hit_Head", "region": "head"}],
                                   "movement": {"default": "upright"}, "cover": 0.95, "overreach": 2.0,
                                   "cover_min": 0.85, "overreach_max": 2.75}))
        return "HITBOXES_OK"


def _parent_rig(tmp: Path):
    pdir = tmp / "parent"
    (pdir / "rig_info").mkdir(parents=True)
    (pdir / "rig.glb").write_bytes(b"glb")
    (pdir / "anim.glb").write_bytes(b"anim")
    (pdir / "rig_info" / "rig_info.json").write_text(json.dumps({"bones": 30, "verts": 10, "weight_coverage": 1.0, "roots": ["Hips"],
                                                                 "max_influences": 4, "unnormalised_verts": 0}))
    (pdir / "hitboxes.json").write_text("{}")
    files = {"glb": pdir / "rig.glb", "anim_glb": pdir / "anim.glb", "rig_info": pdir / "rig_info" / "rig_info.json", "hitboxes": pdir / "hitboxes.json"}
    parent = SimpleNamespace(id="p" * 32, stage="rig", files={k: str(v) for k, v in files.items()},
                             params={"template": "auto", "hitboxes": "detailed", "hitbox_fit": "normal"},
                             metrics={"rigid": False, "bones": 30, "clips": ["idle", "walk"], "checks": ["old"], "hitboxes": {"count": 3}})
    return parent, pdir


def _patch_library(monkeypatch, pdir):
    monkeypatch.setattr(rig.library, "version_dir", lambda asset, v, settings=None, create=True: pdir)
    monkeypatch.setattr(rig.library, "version_file", lambda v, key, settings=None: Path(v.files[key]) if key in v.files and Path(v.files[key]).is_file() else None)


def test_adjust_hitbox_knob_refits_without_skintoken(tmp_path, monkeypatch):
    parent, pdir = _parent_rig(tmp_path)
    _patch_library(monkeypatch, pdir)
    version = SimpleNamespace(id="n" * 32, stage="rig", files={}, params={**parent.params, "hitbox_fit": "loose"}, metrics={})
    sc = _RefitSC(tmp_path, parent, version)

    async def no_rig(*_a, **_k):
        raise AssertionError("SkinToken must not run for a hit-box-only change")

    monkeypatch.setattr(rig, "_rig", no_rig)
    asyncio.run(rig.adjust(sc, {"hitbox_fit": "loose"}))
    assert [c[0] for c in sc.blender_calls] == ["hitboxes.py"]
    args = sc.blender_calls[0][1]
    assert args[0] == str(sc.dir / "rig.glb") and args[args.index("--fit") + 1] == "loose"
    assert (sc.dir / "rig.glb").read_bytes() == b"glb" and (sc.dir / "rig_info" / "rig_info.json").is_file()
    assert sc.files["hitboxes"] == sc.dir / "hitboxes.json"
    m = version.metrics
    assert m["refit_of"] == parent.id and m["bones"] == 30 and m["clips"] == ["idle", "walk"]
    assert m["hitboxes"]["count"] == 11 and m["hitboxes"]["fit"] == "loose"
    names = {c["name"] for c in m["checks"]}
    assert {"hitbox_cover", "hitbox_overreach", "bones", "clips"} <= names
    assert version.params["hitbox_fit"] == "loose"


def test_adjust_other_knob_rigs_again(tmp_path, monkeypatch):
    parent, pdir = _parent_rig(tmp_path)
    _patch_library(monkeypatch, pdir)
    version = SimpleNamespace(id="n" * 32, stage="rig", files={}, params=dict(parent.params), metrics={})
    sc = _RefitSC(tmp_path, parent, version)
    seen = {}

    async def fake_rig(_sc, merged):
        seen.update(merged)

    monkeypatch.setattr(rig, "_rig", fake_rig)
    asyncio.run(rig.adjust(sc, {"group_per_vertex": 3, "hitbox_fit": "snug"}))
    assert seen["group_per_vertex"] == 3 and seen["hitbox_fit"] == "snug" and not sc.blender_calls
    parent.metrics["rigid"] = True
    seen.clear()
    asyncio.run(rig.adjust(sc, {"hitbox_fit": "snug"}))  # nothing rigged to reuse: rig again
    assert seen["hitbox_fit"] == "snug"
