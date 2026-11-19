"""One list of looks (UI-01): every look carries a painting part and a pipeline part, and old ids migrate."""

from __future__ import annotations

import json

from sqlalchemy import text

from vgac import db, reference
from test_api import client, fake_execute  # noqa: F401  (fixtures)

OLD_LOOK_IDS = {"painted_fantasy": "painted", "clean_cartoon": "cel", "realistic": "realistic_pbr", "clay_toy": "clay"}
OLD_STYLE_IDS = {"painted", "cel", "retro64", "lowpoly_flat", "realistic_pbr", "clay"}
NO_PALETTE = {"retro64"}


def test_every_style_is_one_look_with_both_parts():
    looks = {p["id"]: p for p in reference.style_presets()}
    assert set(looks) == OLD_STYLE_IDS
    for lid, look in looks.items():
        lock = look["style_lock"]
        assert look["label"] and look["description"] and look["example_prompt"], lid
        assert lock["preset"] == lid and lock["model"] and lock["prompt_block"], lid
        # Retro 64's palette is empty on purpose: with its late-90s add-on the palette sentence painted colours the
        # description never asked for (look research 2026-11-16, row J).
        assert lock["palette"] or lid in NO_PALETTE, lid
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


def test_a_removed_look_opens_as_a_custom_look_and_an_earlier_backup_is_kept(settings):
    """Pixel art was removed (2026-11-16): a stored pixel project keeps its own lock fields as a custom look based on
    Painted, its assets take that look, and the copy made before an earlier migration is never written over."""
    from vgac.db import Asset, Project, session_factory

    engine = db.get_engine()
    earlier = settings.db_path.with_name(settings.db_path.name + ".before-looks-merge")
    earlier.write_bytes(b"an earlier copy")
    with session_factory()() as s:
        s.add(Project(id="p-pixel", name="p", style_lock={"preset": "pixel", "model": "pixel", "prompt_block": "Pixel art style."}))
        s.flush()
        s.add(Asset(id="a-pixel", project_id="p-pixel", name="x", category="humanoid", style_preset="pixel", prompt=""))
        s.commit()
    assert db.migrate_looks(engine) == 2
    with engine.begin() as conn:
        lock = json.loads(conn.execute(text("SELECT style_lock_json FROM project WHERE id = 'p-pixel'")).scalar())
        style = conn.execute(text("SELECT style_preset FROM asset WHERE id = 'a-pixel'")).scalar()
    assert lock["preset"] is None and lock["based_on"] == "painted" and lock["model"] == "pixel"
    assert lock["prompt_block"] == "Pixel art style." and style == "painted"
    assert earlier.read_bytes() == b"an earlier copy"
    assert len(list(settings.db_path.parent.glob(settings.db_path.name + ".before-looks-merge-*"))) == 1


def test_a_project_whose_lock_names_the_removed_pixel_painter_still_opens(client):
    """The two migrated ex-pixel projects keep the pixel painter in their lock (preset None, based on Painted). The
    painter is gone from models.yaml: the project, its assets and the suggestion still answer; only a write that sends
    that lock again is refused with a plain reason, as for any unknown painter."""
    from vgac.db import Asset, Project, session_factory

    with session_factory()() as s:
        s.add(Project(id="p-px", name="old pixel", style_lock={"preset": None, "based_on": "painted", "model": "pixel",
                                                              "prompt_block": "Pixel art style.", "loras": [], "palette": [], "negative": ""}))
        s.flush()
        s.add(Asset(id="a-px", project_id="p-px", name="guard", category="humanoid", style_preset="painted", prompt="a guard"))
        s.commit()
    assert client.get("/api/projects/p-px").json()["style_lock"]["model"] == "pixel"
    assert [a["id"] for a in client.get("/api/projects/p-px/assets").json()] == ["a-px"]
    assert client.get("/api/assets/a-px").status_code == 200
    assert client.get("/api/suggest", params={"category": "humanoid", "style": "painted", "platform": "pc_mid"}).status_code == 200
    assert client.put("/api/projects/p-px", json={"name": "renamed"}).status_code == 200
    r = client.put("/api/projects/p-px", json={"style_lock": client.get("/api/projects/p-px").json()["style_lock"]})
    assert r.status_code == 400 and "style_lock.model must be one of" in r.json()["detail"]

