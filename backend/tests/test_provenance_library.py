"""write_provenance on the job records of one finished export (PL-51 pass mark).

backend/tests/fixtures/export_job_records.json holds only what provenance reads: the stage kind of each step, in
lineage order (oldest first), and the model ids and versions each step recorded. No names, paths, seeds or prompts.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from vgac.stages import export

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "export_job_records.json"


def test_every_model_in_a_finished_exports_job_records_is_listed(tmp_path):
    chain = json.loads(FIXTURE.read_text(encoding="utf-8"))["chain"]
    versions = [SimpleNamespace(id=s["step"], stage=s["stage"], params={}, files={}, metrics={"models": s["models"]})
                for s in reversed(chain)]  # iter_lineage yields newest first
    sc = SimpleNamespace(iter_lineage=lambda: iter(versions), models_used={},
                         dir=tmp_path, asset=SimpleNamespace(name="fixture asset"),
                         register=lambda key, path: str(path))
    doc = json.loads(export.write_provenance(sc, "fixture").read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in doc["models"]}

    used = {m["id"] for s in chain for m in s["models"]}
    assert used, "the fixture records no models"
    missing = used - set(rows)
    assert not missing, sorted(missing)
    for mid in used:
        assert rows[mid]["licence"] not in ("", "unknown"), mid
        assert rows[mid].get("source"), mid
    if "trellis2" in used:
        assert rows["trellis2_dino"]["role"] == "companion"
