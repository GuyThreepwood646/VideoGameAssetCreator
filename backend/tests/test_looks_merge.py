"""One list of looks (UI-01): every look carries a painting part and a pipeline part, and old ids migrate."""

from __future__ import annotations

import json

from sqlalchemy import text

from vgac import db, reference

OLD_LOOK_IDS = {"painted_fantasy": "painted", "clean_cartoon": "cel", "pixel_retro": "pixel", "realistic": "realistic_pbr", "clay_toy": "clay"}
OLD_STYLE_IDS = {"painted", "cel", "pixel", "retro64", "lowpoly_flat", "realistic_pbr", "clay"}


def test_every_style_is_one_look_with_both_parts():
    looks = {p["id"]: p for p in reference.style_presets()}
    assert set(looks) == OLD_STYLE_IDS
    for lid, look in looks.items():
        lock = look["style_lock"]
        assert look["label"] and look["description"] and look["example_prompt"], lid
        assert lock["preset"] == lid and lock["model"] and lock["prompt_block"] and lock["palette"], lid
        style = reference.style(lid)  # the pipeline part: model, prompt block and recipe still load as before
        assert style["model"] and style["prompt_block"], lid
        assert look["style_preset"] == lid


def test_the_two_new_looks_exist_with_samples():
    from pathlib import Path

    public = Path(__file__).resolve().parents[2] / "frontend" / "public" / "looks"
    for lid in ("retro64", "lowpoly_flat"):
        assert (public / f"{lid}.webp").is_file()
    for lid in OLD_STYLE_IDS:
        assert (public / f"{lid}.webp").is_file(), lid


def test_every_old_look_id_maps_to_a_new_one():
    assert db.LOOK_ID_MIGRATION == OLD_LOOK_IDS
    assert set(db.LOOK_ID_MIGRATION.values()) <= OLD_STYLE_IDS


def test_migration_maps_ids_falls_back_and_backs_up(settings):
    from vgac.db import Asset, Project, session_factory

    engine = db.get_engine()
    with session_factory()() as s:
        for old in OLD_LOOK_IDS:
            s.add(Project(id=f"p-{old}", name="p", style_lock={"preset": old, "model": "zturbo"}))
        s.add(Project(id="p-gone", name="p", style_lock={"preset": "no_such_look", "model": "zturbo"}))
        s.add(Project(id="p-none", name="p", style_lock={"model": "zturbo"}))
        s.flush()
        s.add(Asset(id="a-odd", project_id="p-gone", name="x", category="humanoid", style_preset="mystery", prompt=""))
        s.commit()
    assert db.migrate_looks(engine) > 0
    with engine.begin() as conn:
        rows = {r[0]: json.loads(r[1]) for r in conn.execute(text("SELECT id, style_lock_json FROM project")).fetchall()}
        asset_style = conn.execute(text("SELECT style_preset FROM asset WHERE id = 'a-odd'")).scalar()
    for old, new in OLD_LOOK_IDS.items():
        assert rows[f"p-{old}"]["preset"] == new
    assert rows["p-gone"]["preset"] is None and rows["p-gone"]["based_on"] == "painted"
    assert asset_style == "painted"  # the project's look, because the asset's own value is unknown
    assert settings.db_path.with_name(settings.db_path.name + ".before-looks-merge").is_file()
    assert db.migrate_looks(engine) == 0  # idempotent
