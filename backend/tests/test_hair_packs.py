"""The hair library follows the packs the person kept in setup (<library>/hair-packs.json)."""
import json
from types import SimpleNamespace

from vgac import credits, hairstyles


def _use_library(monkeypatch, tmp_path, packs):
    if packs is not None:
        (tmp_path / "hair-packs.json").write_text(json.dumps({"packs": packs}), encoding="utf-8")
    monkeypatch.setattr("vgac.config.get_settings", lambda: SimpleNamespace(library_path=tmp_path))
    hairstyles.use_dir(None)  # the app's own library, cache cleared
    return hairstyles


def test_no_file_keeps_every_style(monkeypatch, tmp_path):
    h = _use_library(monkeypatch, tmp_path, None)
    try:
        assert len(h.catalogue()["styles"]) >= 26
    finally:
        h.use_dir(None)


def test_picks_filter_catalogue_and_credits(monkeypatch, tmp_path):
    h = _use_library(monkeypatch, tmp_path, [{"catalogue_pack": "Universal Base Characters (Standard)", "catalogue_author": "Quaternius"}])
    try:
        styles = h.catalogue()["styles"]
        assert styles and all(s["pack"] == "Universal Base Characters (Standard)" for s in styles.values())
        assert len(credits._hair()) == len(styles)
        assert all(e["author"] == "Quaternius" for e in credits._hair())
    finally:
        monkeypatch.undo()
        h.use_dir(None)


def test_bad_file_means_everything(monkeypatch, tmp_path):
    (tmp_path / "hair-packs.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("vgac.config.get_settings", lambda: SimpleNamespace(library_path=tmp_path))
    hairstyles.use_dir(None)
    try:
        assert len(hairstyles.catalogue()["styles"]) >= 26
    finally:
        monkeypatch.undo()
        hairstyles.use_dir(None)
