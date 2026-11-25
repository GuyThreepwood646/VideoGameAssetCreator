"""The app's own idle as the default Stand still for humanoids (moves.idle_source, rig._animate, VgacImport Version 20): the
rows the library retarget gets, what an older character keeps, choosing the library's idle or another clip, the
change from the Moves panel, and the importer's own-idle controller data (read from the sidecar the rig stage writes)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import moves
from vgac.stages import rig

from test_moves import HUMANOID, FakeSC, _libs

IMPORTER = (Path(__file__).resolve().parents[2] / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs").read_text(encoding="utf-8")


def names(rows):
    return [r["name"] for r in rows]


def test_idle_source_defaults_to_the_library_for_a_version_that_has_none():
    assert moves.idle_source({}) == "library"
    assert moves.idle_source({"idle_source": ""}) == "library"
    assert moves.idle_source({"idle_source": "builtin"}) == "builtin"
    assert moves.idle_source({"idle_source": "ual1:Idle_Talking_Loop"}) == "ual1:Idle_Talking_Loop"
    assert moves.idle_source({"idle_source": "nonsense"}) == "library"


def test_a_first_rig_gets_the_built_in_idle_and_a_remake_of_an_older_rig_keeps_the_library_one():
    assert moves.resolve_idle_source({}, "mesh") == "builtin"
    assert moves.resolve_idle_source({}, None) == "builtin"
    assert moves.resolve_idle_source({}, "rig") == "library"  # made from an earlier rig: not changed silently
    assert moves.resolve_idle_source({"idle_source": "library"}, "mesh") == "library"  # the person's choice wins
    assert moves.resolve_idle_source({"idle_source": "builtin"}, "rig") == "builtin"


def test_move_rows_built_in_has_no_idle_row_and_every_other_move_stays():
    assert names(moves.move_rows(HUMANOID, {})) == ["idle", "walk", "death", "roll"]  # an older version
    assert names(moves.move_rows(HUMANOID, {"idle_source": "builtin"})) == ["walk", "death", "roll"]
    assert names(moves.move_rows(HUMANOID, {"idle_source": "library"})) == ["idle", "walk", "death", "roll"]


def test_move_rows_can_use_any_other_library_clip_as_stand_still(monkeypatch):
    monkeypatch.setattr(moves, "find_clip", lambda cid, s=None: {"id": cid, "label": "Talking idle", "loop": True})
    rows = moves.move_rows(HUMANOID, {"idle_source": "ual2:Idle_Talking_Loop"})
    idle = rows[0]
    assert idle["name"] == "idle" and idle["from"] == [{"library": "ual2", "action": "Idle_Talking_Loop"}]
    assert idle["label"] == "Talking idle" and idle["loop"] is True and idle["clip"] == "ual2:Idle_Talking_Loop"
    assert names(rows) == ["idle", "walk", "death", "roll"]
    # a clip that is gone leaves the library's own idle rather than no idle
    monkeypatch.setattr(moves, "find_clip", lambda cid, s=None: None)
    assert moves.move_rows(HUMANOID, {"idle_source": "ual2:Gone"})[0]["from"][0]["action"] == "Idle_Loop"


def test_taking_stand_still_off_still_wins_over_the_choice():
    assert "idle" not in names(moves.move_rows(HUMANOID, {"idle_source": "library", "removed_moves": ["idle"]}))


def _run(tmp_path, params, parent_stage):
    sc = FakeSC(tmp_path, _libs(tmp_path))
    sc.parent = SimpleNamespace(stage=parent_stage) if parent_stage else None
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", params))
    args = sc.calls[1][1]
    spec = json.loads(Path(args[args.index("--spec") + 1]).read_text())
    return sc, [r["name"] for r in spec]


def test_a_first_rig_makes_the_moves_without_the_library_idle_and_records_the_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(rig.quality, "rig_checks", lambda *a, **k: [])
    sc, spec = _run(tmp_path, {}, "mesh")
    assert spec == ["walk", "death", "roll"] and sc.version.params["idle_source"] == "builtin"
    args = sc.calls[0][1]  # procanim still makes the built-in idle, and retarget does not replace it
    assert "idle" in args[args.index("--clips") + 1].split(",")


def test_an_older_character_keeps_the_library_idle_when_its_moves_are_made_again(tmp_path, monkeypatch):
    monkeypatch.setattr(rig.quality, "rig_checks", lambda *a, **k: [])
    sc, spec = _run(tmp_path, {"added_moves": []}, "rig")
    assert spec == ["idle", "walk", "death", "roll"] and sc.version.params["idle_source"] == "library"


def test_choosing_the_library_idle_on_a_first_rig_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setattr(rig.quality, "rig_checks", lambda *a, **k: [])
    sc, spec = _run(tmp_path, {"idle_source": "library"}, "mesh")
    assert spec[0] == "idle" and sc.version.params["idle_source"] == "library"


def test_the_idle_choice_is_a_moves_only_change_and_blocks_a_donor_with_another_choice():
    assert rig.reuse_change({"template": "auto"}, {"idle_source": "library", rig.KEEP_MARKER: True}) == {"moves"}
    assert rig._same_moves(HUMANOID, {"idle_source": "builtin"}, {"idle_source": "builtin"})
    assert not rig._same_moves(HUMANOID, {"idle_source": "builtin"}, {})  # a rig without the choice has the library's
    assert rig._same_moves(HUMANOID, {"idle_source": "library"}, {})


def test_change_moves_stores_the_choice_per_character_and_asset_moves_reports_it(settings, monkeypatch):
    from vgac import db as dbm, pipeline

    submitted = []
    monkeypatch.setattr(pipeline, "submit", lambda db, a, v, op, payload: submitted.append((v.id, op, payload)) or SimpleNamespace(id="job"))
    monkeypatch.setattr(dbm, "job_to_dict", lambda j: {"id": j.id})
    monkeypatch.setattr(moves, "find_clip", lambda cid, s=None: None if cid.endswith("Nope") else {"id": cid, "name": "x", "label": "X", "loop": True})
    with dbm.session_factory()() as db:
        p = dbm.Project(name="P")
        db.add(p)
        db.commit()
        a, b = (dbm.Asset(project_id=p.id, name=n, category="humanoid") for n in ("Ranger", "Knight"))
        db.add_all([a, b])
        db.commit()
        for x, params in ((a, {"idle_source": "builtin"}), (b, {})):
            v = dbm.Version(asset_id=x.id, stage="rig", status="done", selected=True)
            v.params = params
            db.add(v)
        db.commit()
        assert moves.asset_moves(db, a)["idle_source"] == "builtin" and moves.asset_moves(db, b)["idle_source"] == "library"
        moves.change_moves(db, a, moves.MovesIn(idle="library"))
        vid, op, payload = submitted[-1]
        assert op == "adjust" and payload["params"]["idle_source"] == "library"
        assert db.get(dbm.Version, vid).params["idle_source"] == "library"
        db.get(dbm.Version, vid).status = "done"
        for old in moves._rig_versions(db, a.id):
            old.selected = old.id == vid  # the finished version is kept, as the keep marker does
        db.commit()
        # the same choice again is nothing to do; a clip that is gone is refused
        for bad in (moves.MovesIn(idle="library"), moves.MovesIn(idle="ual1:Nope")):
            with pytest.raises(moves.MovesError):
                moves.change_moves(db, a, bad)
        moves.change_moves(db, a, moves.MovesIn(idle="ual1:Idle_Talking_Loop", add=["ual2:Yes"]))
        assert submitted[-1][2]["params"]["idle_source"] == "ual1:Idle_Talking_Loop"
        # the other character is untouched
        assert [v.params for v in moves._rig_versions(db, b.id)] == [{}]


# ---- the importer ------------------------------------------------------------------------------------------------------

def test_the_importer_takes_the_own_idle_from_the_sidecar_and_makes_it_the_locomotion_idle():
    assert 'Version = "27"' in IMPORTER
    assert "public string source" in IMPORTER
    # the rule: the sidecar's idle row made by the app ("procedural") is the character's own idle
    assert 'string.Equals(c.source, "procedural", StringComparison.OrdinalIgnoreCase) ? c.name : null' in IMPORTER
    # its clip comes from the model's own FBX into the shared set, with loop settings from the sidecar
    assert "if (ownIdleName != null) fromOwn.Add(ownIdleName);" in IMPORTER
    assert "SetClipLoops(importer, report, side, fromOwn)" in IMPORTER
    assert "clipSet.AddRange(LoadClips(fbxPath).Where(c => fromOwn.Contains(ShortName(c.name))))" in IMPORTER
    # the clip is chosen by its asset path being the character's own model, never by name alone, and the clip object itself goes
    # to the controller, so a library clip with the same short name cannot be taken for it
    assert "ownIdle = clipSet.FirstOrDefault(c => AssetDatabase.GetAssetPath(c) == fbxPath && string.Equals(ShortName(c.name), ownIdleName" in IMPORTER
    assert "AnimationClip ownIdle = null" in IMPORTER and "HashSet<string> added = null, AnimationClip ownIdle = null" in IMPORTER
    # and it is the idle of the Locomotion blend tree (idle at speed 0; walk and run from the library blend from it)
    assert 'var idle = Take(ownIdle != null ? ownIdle : Pick(clips, used, "idle"));' in IMPORTER
    assert "if (idle) cycle.Add((idle, 0f));" in IMPORTER
    # which idle played is in unity_report.json (controller.idleUsed: own, library or none; controller.idleClip)
    assert 'info.idleUsed = idle == null ? "none" : (ownIdle != null && idle == ownIdle ? "own" : "library");' in IMPORTER
    assert "public string idleUsed" in IMPORTER and "public string idleClip" in IMPORTER
    # it is not one of the person's added moves: no state or parameter of its own
    assert "addedClips.Add(ownIdle)" not in IMPORTER


def test_the_sidecar_keeps_the_library_idle_row_for_an_older_character(tmp_path, monkeypatch):
    """The fake retarget writes only the rows it was given, so a built-in idle has no row here (the real sidecar keeps
    procanim's, source procedural); the library rows are the ones the importer must not take as its own idle."""
    monkeypatch.setattr(rig.quality, "rig_checks", lambda *a, **k: [])
    sc, _ = _run(tmp_path, {}, "rig")
    rows = {r["name"]: r for r in json.loads((sc.dir / "anim_clips.json").read_text())["clips"]}
    assert rows["idle"]["source"] == "library"
