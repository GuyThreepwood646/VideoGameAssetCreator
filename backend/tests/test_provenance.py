"""The per-asset provenance file (PL-51): every model the asset's steps used is listed with its version, licence and
source, checked against the job records; every models.yaml entry has a licence and a source; the DINOv3 entry carries
Meta's licence, not MIT."""
from __future__ import annotations

import json
from types import SimpleNamespace

import yaml

from vgac import provenance
from vgac.config import DATA_DIR
from vgac.stages import export


def _version(vid: str, stage: str, models: list[dict], params: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=vid, stage=stage, params=params or {}, files={}, metrics={"models": models})


def _stub_context(tmp_path, chain_newest_first: list, models_used: dict | None = None) -> SimpleNamespace:
    """A stand-in for StageContext: manifest_provenance reads iter_lineage, models_used and the version metrics."""
    registered: dict[str, str] = {}

    def register(key: str, path) -> str:
        registered[key] = str(path)
        return str(path)

    return SimpleNamespace(iter_lineage=lambda: iter(chain_newest_first), models_used=models_used or {},
                           dir=tmp_path, asset=SimpleNamespace(name="Test Knight"), register=register)


def _job_records() -> list:
    """Job records for a humanoid: a concept painted with zturbo, a 3D model from trellis2 (which loads the DINOv3
    encoder), a rig from skintoken, a library move pack, and the export's own Blender and Unity runs."""
    concept = _version("c", "concept", [{"id": "zturbo", "name": "Z-Image Turbo", "file": "z.safetensors", "licence": "Apache-2.0", "version": "1.0"}])
    mesh = _version("m", "mesh", [{"id": "trellis2", "name": "TRELLIS.2", "file": "t.safetensors", "licence": "MIT", "version": "2"}])
    rig = _version("r", "rig", [{"id": "skintoken", "name": "SkinToken", "file": "grpo_1400.ckpt", "licence": "MIT", "version": "grpo_1400"},
                                {"id": "ual1", "name": "Universal Animation Library Standard (CC0)", "licence": "CC0", "version": "3.0", "role": "moves"}])
    return [rig, mesh, concept]


def test_provenance_lists_every_model_the_job_records_used(tmp_path):
    chain = _job_records()
    sc = _stub_context(tmp_path, chain, models_used={"blender": {"id": "blender", "name": "Blender", "licence": "GPL (tool)", "version": "5.2.3"},
                                                     "unity": {"id": "unity", "name": "Unity Editor (sandbox import)", "licence": "Unity Personal/Pro (tool)", "version": "6000.4.3f1"}})
    path = export.write_provenance(sc, "test_knight")
    assert path.name == provenance.PROVENANCE_FILE and path.parent == tmp_path
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["asset"] == "Test Knight" and doc["export_name"] == "test_knight"

    rows = {r["id"]: r for r in doc["models"]}
    job_ids = {m["id"] for v in chain for m in v.metrics["models"]} | {"blender", "unity"}
    assert job_ids <= set(rows), job_ids - set(rows)
    assert rows["trellis2"]["version"] == "2" and rows["zturbo"]["licence"] == "Apache-2.0"
    assert rows["ual1"]["role"] == "model" and rows["ual1"]["licence"] == "CC0"
    assert rows["blender"]["version"] == "5.2.3"


def test_provenance_lists_the_companion_a_used_model_loads(tmp_path):
    sc = _stub_context(tmp_path, _job_records())
    doc = json.loads(export.write_provenance(sc, "k").read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in doc["models"]}
    dino = rows["trellis2_dino"]
    assert dino["role"] == "companion" and dino["used_by"] == ["trellis2"]
    assert dino["licence"] == "DINOv3 License (Meta)" and dino["source"]
    # a companion of a model this asset did not use is not listed
    assert "pixal3d_dino" not in rows and "qwen_te" not in rows


def test_every_row_has_a_licence_and_a_source(tmp_path):
    sc = _stub_context(tmp_path, _job_records())
    doc = json.loads(export.write_provenance(sc, "k").read_text(encoding="utf-8"))
    for row in doc["models"]:
        assert row["licence"] not in ("", "unknown"), row["id"]
        assert row.get("source"), row["id"]
    assert set(doc["licences"]) == {r["licence"] for r in doc["models"]}


def test_a_job_model_missing_from_models_yaml_is_shown_as_unknown_not_dropped(tmp_path):
    chain = [_version("x", "mesh", [{"id": "mystery_model", "name": "Mystery"}])]
    doc = provenance.provenance_doc({"mystery_model": chain[0].metrics["models"][0]}, "A", "a")
    row = doc["models"][0]
    assert row["id"] == "mystery_model" and row["licence"] == "unknown"


def test_every_models_yaml_entry_has_a_licence_and_a_source():
    reg = yaml.safe_load((DATA_DIR / "models.yaml").read_text(encoding="utf-8"))
    missing = []
    for section in ("models", "companions", "finders"):
        for mid, entry in (reg.get(section) or {}).items():
            if not str(entry.get("licence", "")).strip():
                missing.append((section, mid, "licence"))
            if not str(entry.get("source", "")).strip():
                missing.append((section, mid, "source"))
    assert not missing, missing


def test_dinov3_entry_carries_meta_licence_not_mit():
    reg = yaml.safe_load((DATA_DIR / "models.yaml").read_text(encoding="utf-8"))
    dino = reg["companions"]["trellis2_dino"]
    assert dino["licence"] == "DINOv3 License (Meta)"
    assert reg["companions"]["pixal3d_dino"]["licence"].startswith("DINOv3 License")
    downloads = json.loads((DATA_DIR.parents[2] / "setup" / "downloads.json").read_text(encoding="utf-8"))
    assert "DINOv3" in downloads["models"]["trellis2_dino"]["licence"]


def test_skintoken_entry_records_the_accepted_weights_risk():
    reg = yaml.safe_load((DATA_DIR / "models.yaml").read_text(encoding="utf-8"))
    note = reg["models"]["skintoken"]["note"]
    assert "issue 9" in note and "2026-11-23" in note
