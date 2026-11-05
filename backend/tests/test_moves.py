"""Add a move (moves.py): skeleton recognition for other tools' names, the library catalogue (both packs, plain names
and groups), a character's move rows, the moves-only re-run of the rig step, the added_moves check, your own
animation files (upload checks, registry) and the Unity how-to line. Blender runs are covered in
test_animation_quality.py and by the live check."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from vgac import moves
from vgac.blender import gait
from vgac.stages import export, rig

ROOT = Path(__file__).resolve().parents[2]
UAL1 = ROOT / "data/animations/ual1/Animation Library[Standard]/Godot/AnimationLibrary_Godot_Standard.glb"
UAL2 = ROOT / "data/animations/ual2/Universal Animation Library 2 [Standard]/Unreal-Godot/UAL2_Standard.glb"

HUMANOID = {"id": "humanoid", "rig_engine": "skintoken_mixamo", "motion": "legs", "clips": ["idle", "walk", "turn_left", "death"],
            "library_clips": [{"name": "idle", "from": ["Idle_Loop"], "move": "idle", "loop": True},
                              {"name": "walk", "from": ["Walk_Loop"], "move": "walk", "loop": True},
                              {"name": "death", "from": ["Death01"], "move": "death", "loop": False},
                              {"name": "roll", "from": ["Roll"], "move": "roll", "loop": False}]}


# ---- skeleton recognition ---------------------------------------------------------------------------------------

UNITY = ["Hips", "Spine", "Chest", "UpperChest", "Neck", "Head"] + [s + b for s in ("Left", "Right") for b in (
    "Shoulder", "UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg", "Foot", "Toes")]
BVH_JOINTS = ["Hips", "Chest", "Chest2", "Neck", "Head"] + [s + b for s in ("Left", "Right") for b in (
    "Collar", "Shoulder", "Elbow", "Wrist", "Hip", "Knee", "Ankle", "Toe")]


@pytest.mark.parametrize("names,count", [
    (["mixamorig:" + b for b in gait.BODY_BONES], 22),
    (UNITY, 22),
    (BVH_JOINTS, 21),
    (["Bip01 Pelvis", "Bip01 Spine", "Bip01 Spine1", "Bip01 Neck", "Bip01 Head"] + [f"Bip01 {s} {b}" for s in "LR" for b in (
        "Clavicle", "UpperArm", "Forearm", "Hand", "Thigh", "Calf", "Foot", "Toe0")], 21),
    (["pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head"] + [f"{b}_{s}" for s in "lr" for b in (
        "clavicle", "upperarm", "lowerarm", "hand", "thigh", "calf", "foot", "ball")], 22),
])
def test_human_skeletons_from_other_tools_are_recognised(names, count):
    r = gait.recognise(names)
    assert r["usable"] and r["recognised"] == count and r["total"] == 22
    assert r["sentence"] == f"We recognised {count} of the 22 main bones."


def test_joint_named_files_use_shoulder_as_the_upper_arm():
    m = gait.source_shorts(BVH_JOINTS)
    assert m["LeftShoulder"] == "LeftArm" and m["LeftCollar"] == "LeftShoulder" and m["LeftKnee"] == "LeftLeg"
    assert m["Chest"] == "Spine" and m["Chest2"] == "Spine1"


def test_a_creature_skeleton_is_refused_in_one_sentence():
    r = gait.recognise(["root", "body", "neck", "head", "leg_fl", "leg_fr", "leg_bl", "leg_br", "tail_01"])
    assert not r["usable"] and r["sentence"] == "This file's skeleton is not human-shaped, so we cannot use it."
    assert gait.body_plain("LeftToeBase") == "the left toes" and gait.body_plain("Spine2") == "the chest"


def test_helper_and_end_bones_are_not_mapped():
    m = gait.source_shorts(["mixamorig:HeadTop_End", "mixamorig:LeftToe_End", "LeftArmTwist", "upper_arm.L.001", "mixamorig:LeftHandIndex4"])
    assert m == {}


# ---- catalogue ------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(not (UAL1.is_file() and UAL2.is_file()), reason="needs both clip packs")
def test_both_packs_have_a_plain_name_and_group_for_every_clip(tmp_path):
    s = SimpleNamespace(clips_libraries={"ual1": UAL1, "ual2": UAL2}, library_path=tmp_path)
    groups = {g for g, _ in moves.GROUPS}
    for pack, count in (("ual1", 43), ("ual2", 41)):
        rows = moves.pack_clips(s, pack)
        assert len(rows) == count
        for r in rows:
            assert r["action"] in moves.CLIP_TABLE[pack], r["action"]  # never the fallback name
            assert r["group"] in groups and r["label"] and "_" not in r["label"]
    cat = moves.catalogue(s, make_previews=False)
    assert [p["id"] for p in cat["packs"]] == ["ual1", "ual2", "mine"]
    by = {c["id"]: c for c in cat["clips"]}
    assert by["ual2:Sword_Block"]["label"] == "Block with a sword" and not by["ual2:Sword_Block"]["loop"]
    assert by["ual1:Sitting_Idle_Loop"]["loop"] and by["ual1:Sitting_Idle_Loop"]["name"] == "sit_chair"
    assert by["ual2:ClimbUp_1m_RM"]["moves_character"] and "ual1:Roll_RM" not in by and "ual1:A_TPose" not in by


def test_clip_names_and_readable_labels():
    assert moves.clip_name("Sitting_Idle_Loop") == "sitting_idle"
    assert moves.clip_name("ClimbUp_1m_RM") == "climb_up_1m"
    assert moves.humanise("Armature|mixamo.com|Layer0") == "" and moves.humanise("Armature|Scene") == ""
    assert moves.humanise("standing_taunt") == "Standing taunt"


# ---- a character's moves -----------------------------------------------------------------------------------------------

def test_move_rows_are_the_defaults_minus_removed_plus_added():
    params = {"removed_moves": ["roll"], "added_moves": [{"clip": "ual2:Sword_Block", "name": "sword_block", "loop": False},
                                                         {"clip": "ual1:Sitting_Idle_Loop", "name": "sitting_idle", "loop": True},
                                                         {"clip": "ual1:Walk_Loop", "name": "walk", "loop": True}]}
    rows = moves.move_rows(HUMANOID, params)
    assert [r["name"] for r in rows] == ["idle", "walk", "death", "sword_block", "sitting_idle"]
    sb = rows[3]
    assert sb["added"] and sb["from"] == [{"library": "ual2", "action": "Sword_Block"}] and sb["label"] == "Block with a sword"
    assert rows[0]["from"][0]["library"] == "ual1" and rows[0]["label"] == "Stand still" and not rows[0]["added"]
    spec = moves.spec_rows(rows)
    assert spec[4]["loop"] is True and spec[3]["added"] is True and "move" not in spec[0]


def test_the_stored_default_moves_knob_does_not_narrow_the_library():
    assert rig.chosen_moves_of(HUMANOID, {"moves": ["idle", "walk", "turn_left", "death"]}) is None
    assert rig.chosen_moves_of(HUMANOID, {"moves": ["walk"]}) == ["walk"]
    assert rig.chosen_moves_of(HUMANOID, {}) is None


def test_only_moves_changes_reuse_the_skeleton():
    parent = {"hitboxes": "detailed", "added_moves": [], "template": "auto"}
    assert rig.reuse_change(parent, {"added_moves": [{"clip": "ual2:Yes", "name": "yes"}], rig.KEEP_MARKER: True}) == {"moves"}
    assert rig.reuse_change(parent, {"removed_moves": ["roll"], "hitbox_fit": "snug"}) == {"moves", "hitboxes"}
    assert rig.reuse_change(parent, {"template": "generic", "removed_moves": ["roll"]}) is None
    assert rig.reuse_change(parent, {rig.KEEP_MARKER: True}) is None


def test_moves_metrics_list_what_was_made_and_what_was_not():
    rows = moves.move_rows(HUMANOID, {"added_moves": [{"clip": "ual2:Yes", "name": "yes", "loop": False},
                                                      {"clip": "ual2:Nope", "name": "nope", "loop": False}]})
    side = {"clips": [{"name": "idle", "source": "library", "library": "ual1", "from": "Idle_Loop", "loop": True},
                      {"name": "turn_left", "source": "procedural", "loop": False},
                      {"name": "yes", "source": "library", "library": "ual2", "from": "Yes", "loop": False, "added": True, "label": "Nod (yes)"}]}
    out = {m["name"]: m for m in moves.moves_metrics(side, rows, [])}
    assert out["idle"]["source"] == "library" and out["idle"]["clip"] == "ual1:Idle_Loop" and out["idle"]["default"]
    assert out["turn_left"]["source"] == "built-in" and out["turn_left"]["label"] == "Turn left"
    assert out["yes"]["added"] and out["yes"]["made"] and out["yes"]["label"] == "Nod (yes)"
    assert out["nope"]["made"] is False and out["walk"]["made"] is False


# ---- the rig stage with added moves ---------------------------------------------------------------------------------------

class FakeSC:
    def __init__(self, tmp_path, libs):
        self.dir = tmp_path / "ver"
        self.dir.mkdir()
        self.category = HUMANOID
        self.asset = SimpleNamespace(category="humanoid")
        self.settings = SimpleNamespace(clips_libraries=libs, blender_timeout_seconds=60)
        self.version = SimpleNamespace(metrics={}, params={})
        self.files, self.calls = {}, []

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    def register(self, key, path):
        self.files[key] = Path(path)

    def file(self, key):
        return self.files.get(key)

    def progress(self, *a, **k):
        pass

    async def blender(self, script, args, log_name=None, timeout=None):
        args = [str(a) for a in args]
        self.calls.append((script, args))
        if script == "procanim.py":
            Path(args[1]).write_bytes(b"glb")
            (self.dir / "anim_anim.json").write_text(json.dumps({"clips": ["idle", "walk", "turn_left", "death"], "missing_clips": []}))
        elif script == "retarget.py":
            spec = json.loads(Path(args[args.index("--spec") + 1]).read_text())
            made = [r for r in spec if r["name"] != "nope"]
            rows = [{"name": r["name"], "source": "library", "library": r["from"][0]["library"], "from": r["from"][0]["action"],
                     "loop": r["loop"], **({"added": True, "label": r["label"]} if r.get("added") else {})} for r in made]
            rows.append({"name": "turn_left", "source": "procedural", "loop": False})
            (self.dir / "anim_clips.json").write_text(json.dumps({"clips": rows}))
            Path(args[args.index("--report") + 1]).write_text(json.dumps({
                "ok": True, "clips": [r["name"] for r in rows], "library_clips": [r["name"] for r in made], "procedural_clips": ["turn_left"],
                "clip_reasons": {"nope": "not in the library"}, "libraries": {"ual1": {"leg_ratio": 1.0}, "ual2": {"leg_ratio": 1.0}}}))
        elif script == "render_anim.py":
            d = Path(args[1])
            d.mkdir(parents=True, exist_ok=True)
            (d / "frames.json").write_text(json.dumps({"clips": {}, "frames": 6}))
        return ""


def _libs(tmp_path):
    out = {}
    for pack in ("ual1", "ual2"):
        root = tmp_path / pack
        (root / "Godot").mkdir(parents=True)
        (root / "License.txt").write_text("CC0")
        (root / "Godot" / f"{pack}.glb").write_bytes(b"glb")
        out[pack] = root / "Godot" / f"{pack}.glb"
    return out


def test_animate_moves_clips_from_both_packs_and_reports_the_added_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(rig.quality, "rig_checks", lambda *a, **k: [])
    sc = FakeSC(tmp_path, _libs(tmp_path))
    # Carried over from the parent version: the rigger, and the source of a move since taken off.
    sc.version.metrics["models"] = [{"id": "skintoken", "licence": "MIT"}, {"id": "mine_old", "role": "moves"}]
    params = {"removed_moves": ["roll", "turn_left"], "added_moves": [{"clip": "ual2:Yes", "name": "yes", "loop": False},
                                                                     {"clip": "ual2:Nope", "name": "nope", "loop": False}]}
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", params))
    args = dict(zip(sc.calls[1][1][::1], sc.calls[1][1][1:]))
    libs = args["--library"].split(";")
    assert libs[0].startswith("ual1=") and libs[1].startswith("ual2=") and len(libs) == 2
    assert args["--keep"] == "idle,walk,death"  # the built-in turn_left was taken off
    m = sc.version.metrics
    assert m["added_moves_missing"] == ["nope"] and m["added_moves_reasons"] == {"nope": "it is no longer in the library"}
    assert {x["name"]: x["made"] for x in m["moves"] if x["added"]} == {"yes": True, "nope": False}
    assert [r["id"] for r in m["models"]] == ["skintoken", "ual1", "ual2"]
    assert all(r.get("role") == "moves" for r in m["models"][1:])
    sc.version.metrics.update(anim_glb=True)
    asyncio.run(rig.judge(sc))
    chk = {c["id"]: c for c in sc.version.metrics["checks"]}["added_moves"]
    assert chk["ok"] is False and chk["value"] == 1 and chk["limit"] == 2
    assert "Nope" in json.dumps(chk) or "nope" in json.dumps(chk)


# ---- your own animation files ----------------------------------------------------------------------------------------------

def test_upload_names_and_first_bytes_are_checked():
    assert moves.safe_upload_name("My Wave.FBX") == "My Wave.fbx"
    for bad in ("../x.fbx", "a/b.bvh", "x.exe", "", "notes.txt"):
        with pytest.raises(moves.MovesError):
            moves.safe_upload_name(bad)
    assert moves.sniff_kind(b"Kaydara FBX Binary  \x00\x1a\x00", ".fbx") == "fbx"
    assert moves.sniff_kind(b"glTF\x02\x00\x00\x00", ".glb") == "glb"
    assert moves.sniff_kind(b"HIERARCHY\nROOT Hips", ".bvh") == "bvh"
    with pytest.raises(moves.MovesError) as e:
        moves.sniff_kind(b"MZ\x90\x00", ".fbx")
    assert "not a readable FBX file" in str(e.value)


@pytest.fixture
def client(settings):
    from vgac.main import app

    return TestClient(app)


def test_my_moves_api_refuses_a_file_that_is_not_animation(client, settings):
    r = client.post("/api/my-moves/upload?name=wave.fbx", content=b"MZ not an fbx at all")
    assert r.status_code == 400 and "not a readable FBX" in r.json()["detail"]
    r = client.post("/api/my-moves/upload?name=wave.txt", content=b"HIERARCHY")
    assert r.status_code == 400 and r.json()["detail"] == "Use an FBX, GLB or BVH animation file."
    assert not any((Path(settings.library_path) / moves.MINE_DIR).glob("*/*"))  # nothing left behind
    got = client.get("/api/my-moves").json()
    assert got["files"] == [] and got["responsibility"].startswith("You are responsible")


def test_my_moves_registry_update_and_remove(settings):
    entry = {"id": "mine_wave", "label": "Wave", "file_name": "wave.fbx", "kind": "fbx", "source": "",
             "skeleton": {"recognised": 22, "total": 22, "missing": [], "sentence": "We recognised 22 of the 22 main bones.", "usable": True},
             "clips": [{"action": "Armature|Scene", "label": "Wave", "loop": False, "seconds": 2.0}]}
    moves._write([entry], settings)
    (moves.mine_root(settings) / "mine_wave").mkdir(parents=True)
    out = moves.update_mine("mine_wave", None, "Made it myself in Blender", [{"action": "Armature|Scene", "label": "Big wave", "loop": True}], settings)
    assert out["source"] == "Made it myself in Blender" and out["clips"][0]["label"] == "Big wave" and out["clips"][0]["loop"]
    assert out["clips"][0]["id"] == "mine_wave:Armature|Scene"
    info = moves.find_clip("mine_wave:Armature|Scene", settings)
    assert info["origin"] == "Made it myself in Blender" and info["licence"] == moves.LICENCE_MINE
    rows = moves.move_rows(HUMANOID, {"added_moves": [{"clip": "mine_wave:Armature|Scene", "name": "big_wave", "loop": True}]}, None, settings)
    assert rows[-1]["origin"] == "Made it myself in Blender" and rows[-1]["label"] == "Big wave"
    with pytest.raises(moves.MovesError):
        moves.update_mine("mine_wave", None, None, [{"action": "Other", "label": "x"}], settings)
    assert moves.remove_mine("mine_wave", settings) == {"removed": "mine_wave"}
    assert not (moves.mine_root(settings) / "mine_wave").exists() and moves.mine_entries(settings) == []


# ---- Unity --------------------------------------------------------------------------------------------------------------

def test_unity_howto_names_the_added_moves_once():
    ctrl = {"states": ["Locomotion", "Attack", "SwordBlock", "SittingIdle"],
            "parameters": ["Speed:Float", "Attack:Trigger", "SwordBlock:Trigger", "SittingIdle:Bool", "Yes:Trigger"],
            "added": ["SwordBlock:Trigger", "SittingIdle:Bool", "Yes:Trigger"]}
    lines = export.unity_howto("Assets/VGAC/Ranger/Ranger.prefab", ctrl)
    assert 'animator.SetTrigger("Attack");.' in lines[2] and "SwordBlock" not in lines[2]
    assert lines[-1] == ('For the moves you added, animator.SetTrigger("SwordBlock") plays a move you added once and '
                         'animator.SetBool("SittingIdle", true) plays a looping one until you set it to false; the other 1 work the same way, named after the move.')


def test_creatures_hear_what_they_get_instead():
    assert moves.creature_moves("winged") == ["stand still", "walk", "fly", "glide", "take off", "land", "attack", "get hit", "die"]
    assert moves.creature_moves("serpent") == ["stand still", "slither", "turn", "attack", "get hit", "die"]
    assert moves.creature_moves("prop_rigid") == []
    asset = SimpleNamespace(category="quadruped")
    can, reason = moves._can_add(asset)
    assert not can and reason.startswith("The hand-made moves only fit human-shaped characters")
    assert moves._can_add(SimpleNamespace(category="prop_rigid")) == (False, "This model has no skeleton, so it does not move.")
    assert moves._can_add(SimpleNamespace(category="humanoid")) == (True, "")


# ---- POST /api/assets/{aid}/moves --------------------------------------------------------------------------------------

def test_change_moves_adds_removes_copies_and_refuses(settings, monkeypatch):
    from vgac import db as dbm, pipeline

    submitted = []
    monkeypatch.setattr(pipeline, "submit", lambda db, a, v, op, payload: submitted.append((v.id, op, payload)) or SimpleNamespace(id="job"))
    monkeypatch.setattr(dbm, "job_to_dict", lambda j: {"id": j.id})
    monkeypatch.setattr(moves, "find_clip", lambda cid, s=None: {"name": moves.name_from_label(moves.label_for(cid.partition(":")[2]) or "x", "x"),
                                                                   "loop": cid.endswith("_Loop")} if not cid.endswith("Nope") else None)
    with dbm.session_factory()() as db:
        p = dbm.Project(name="P")
        db.add(p)
        db.commit()
        ranger, knight, wolf = (dbm.Asset(project_id=p.id, name=n, category=c) for n, c in (("Ranger", "humanoid"), ("Knight", "humanoid"), ("Wolf", "quadruped")))
        db.add_all([ranger, knight, wolf])
        db.commit()
        for a, params in ((ranger, {"moves": ["idle"]}), (knight, {"added_moves": [{"clip": "ual2:Yes", "name": "nod_yes", "loop": False}], "removed_moves": ["roll"]})):
            v = dbm.Version(asset_id=a.id, stage="rig", status="done", selected=True)
            v.params = params
            db.add(v)
        db.commit()
        out = moves.change_moves(db, ranger, moves.MovesIn(add=["ual1:Sitting_Idle_Loop", "ual2:Sword_Block"], remove=["roll"]))
        vid, op, payload = submitted[-1]
        assert op == "adjust" and payload["params"][rig.KEEP_MARKER] is True and out["job"] == {"id": "job"}
        assert payload["params"]["added_moves"] == [{"clip": "ual1:Sitting_Idle_Loop", "name": "sit_chair", "loop": True},
                                                    {"clip": "ual2:Sword_Block", "name": "block_sword", "loop": False}]
        assert payload["params"]["removed_moves"] == ["roll"]
        new = db.get(dbm.Version, vid)
        assert new.params["moves"] == ["idle"] and new.params["removed_moves"] == ["roll"] and new.parent_version_id
        # A queued rig version: nothing more until it finishes.
        with pytest.raises(moves.MovesError) as e:
            moves.change_moves(db, ranger, moves.MovesIn(add=["ual2:Yes"]))
        assert e.value.status == 409
        new.status = "done"
        db.commit()
        # "The same moves as Knight", and the panel's others list.
        listing = moves.asset_moves(db, ranger)
        assert [o["name"] for o in listing["others"]] == ["Knight"] and listing["busy"] is False
        moves.change_moves(db, ranger, moves.MovesIn(copy_from=knight.id))
        assert submitted[-1][2]["params"]["added_moves"] == [{"clip": "ual2:Yes", "name": "nod_yes", "loop": False}]
        db.get(dbm.Version, submitted[-1][0]).status = "done"
        db.commit()
        for bad in (moves.MovesIn(add=["ual2:Nope"]), moves.MovesIn(remove=["fly"]), moves.MovesIn()):
            with pytest.raises(moves.MovesError):
                moves.change_moves(db, ranger, bad)
        with pytest.raises(moves.MovesError) as e:
            moves.change_moves(db, wolf, moves.MovesIn(add=["ual2:Yes"]))
        assert "human-shaped" in str(e.value)
