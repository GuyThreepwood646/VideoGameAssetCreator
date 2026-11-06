"""Moves facing forward in Unity (VgacImport Version 16, docs/KNOWN-FAULTS-RESEARCH.md fault 5): the report's
clipFacing, its parsing and the clips_face_forward check with its sentences."""

from __future__ import annotations

from pathlib import Path

from vgac.stages import export

SANDBOX_IMPORTER = Path(__file__).resolve().parents[2] / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs"


def _report(rows, measured=True, roles=None):
    return {"ok": True, "facingMeasured": measured, "bindFacing": 0.0, "clipFacing": rows,
            "texturesAssigned": ["base_color", "normal", "ao", "metallic_smoothness"],
            "controller": {"roles": roles or ["Idle=Rig|Idle_Loop", "Walk=Rig|Walk_Loop", "Run=Rig|Jog_Fwd_Loop"],
                           "states": ["Locomotion"], "parameters": ["Speed:Float"]}}


def _row(name, deg, source="shared", in_ctrl=True, measured=True, based="original"):
    return {"name": name, "source": source, "inController": in_ctrl, "measured": measured, "degrees": deg, "swing": 4.0,
            "basedOn": based}


# Measured before the fix on the caped hero (runtime/tmp/facing): baked loops 125 to 180 degrees, one-off moves near 0.
BEFORE = [_row("Rig|Idle_Loop", -162.9), _row("Rig|Walk_Loop", -179.3), _row("Rig|Jog_Fwd_Loop", -180.0),
          _row("Rig|Spell_Simple_Idle_Loop", -125.6), _row("Rig|Sword_Attack", 0.7, based=""),
          _row("idle", 0.1, source="own", in_ctrl=False, based="")]
AFTER = [_row("Rig|Idle_Loop", -0.2, based="body"), _row("Rig|Walk_Loop", 0.1), _row("Rig|Jog_Fwd_Loop", -0.2),
         _row("Rig|Sword_Idle", 1.2, based="body"), _row("Rig|Sword_Attack", -1.9, based=""),
         _row("Rig|Pistol_Aim_Down", -8.2, based="")]


def _check(u):
    return next((c for c in export.unity_checks(u, humanoid=True, rigged=True) if c["name"] == "clips_face_forward"), None)


def test_plain_clip_name():
    assert export.plain_clip_name("Rig|Idle_Loop") == "idle loop"
    assert export.plain_clip_name("walk") == "walk"
    assert export.plain_clip_name("Rig|Spell_Simple_Idle_Loop") == "spell simple idle loop"


def test_clip_facing_worst_and_over():
    f = export.clip_facing(_report(BEFORE))
    assert f["count"] == 5  # the own idle is not played by the controller
    assert f["worst"]["name"] == "Rig|Jog_Fwd_Loop" and f["worst"]["role"] == "run" and f["worst"]["side"] == "left"
    assert [e["name"] for e in f["over"]] == ["Rig|Jog_Fwd_Loop", "Rig|Walk_Loop", "Rig|Idle_Loop", "Rig|Spell_Simple_Idle_Loop"]
    assert not export.clip_facing(_report(AFTER))["over"]


def test_clip_facing_none_without_measurements():
    assert export.clip_facing(_report(BEFORE, measured=False)) is None
    assert export.clip_facing({"ok": True}) is None  # an importer before Version 16
    assert export.clip_facing(_report([_row("Rig|Idle_Loop", 170, measured=False)])) is None
    assert export.clip_facing(_report([_row("Rig|Idle_Loop", 170, in_ctrl=False)])) is None


def test_check_fails_on_backwards_moves_with_a_plain_sentence():
    c = _check(_report(BEFORE))
    assert c is not None and not c["passed"] and c["severity"] == "warn"
    assert c["value"] == 180 and c["limit"] == export.FACING_MAX_DEG
    assert c["help"] == ("The run move (jog fwd loop) plays with the body turned 180 degrees away from the character's forward "
                         "(to its left), and 3 more moves are turned too, so in a game the character would face the wrong way "
                         "while it plays.")
    assert c["fix"] == {"stage": "export", "params": {}, "label": "Rebuild for Unity"}


def test_check_passes_after_the_fix():
    c = _check(_report(AFTER))
    assert c["passed"] and c["value"] == 8 and "fix" not in c
    assert c["help"] == "Every move faces the character's forward in Unity; the furthest any turns is 8 degrees (pistol aim down)."


def test_check_names_a_move_without_a_role():
    c = _check(_report([_row("Rig|Dance_Loop", 95.0), _row("Rig|Idle_Loop", 2.0)]))
    assert not c["passed"]
    assert c["help"].startswith("The dance loop move plays with the body turned 95 degrees away from the character's forward (to its right), so")


def test_no_check_for_generic_rigs_or_old_reports():
    assert _check({**_report(BEFORE), "facingMeasured": False}) is None
    u = _report(BEFORE)
    del u["clipFacing"]
    assert _check(u) is None


def test_unity_metrics_carries_the_facing():
    m = export.unity_metrics(_report(AFTER))
    assert m["facingMeasured"] is True and m["bindFacing"] == 0.0 and len(m["clipFacing"]) == len(AFTER)


def test_sandbox_importer_is_version_16_or_later_and_converts_the_library_axes():
    text = SANDBOX_IMPORTER.read_text(encoding="utf-8")
    assert export.importer_version(SANDBOX_IMPORTER) >= 16
    assert "ci.bakeAxisConversion = true;" in text and "RefreshClipFacing" in text
    assert "AnimatorCullingMode.AlwaysAnimate" in text
