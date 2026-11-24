"""The per-asset provenance file (provenance.json), written beside manifest.json at export.

It lists every model the asset's steps used, with its version, licence and where it comes from. The models come from
the job records: each stage's metrics.models, walked along the lineage (stages/base.py manifest_provenance). A companion
file (a text encoder, VAE or image encoder that a model loads) is listed when a model it is used_by was used. Finders
(the suggested item finder's own models) are not listed: they run outside the asset's steps.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable

import yaml

from .config import DATA_DIR

PROVENANCE_FILE = "provenance.json"
FIELDS = ("name", "file", "version", "licence", "source", "note")


@lru_cache(maxsize=1)
def _registry() -> dict[str, Any]:
    with open(DATA_DIR / "models.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def companions_for(model_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The companion files (models.yaml `companions`) that a used model loads, by companion id."""
    used = set(model_ids)
    return {cid: c for cid, c in (_registry().get("companions") or {}).items() if used & set(c.get("used_by") or [])}


def _row(mid: str, role: str, job: dict[str, Any], known: dict[str, Any], used_by: list[str] | None = None) -> dict[str, Any]:
    """One entry: what the job record says, with anything it lacks filled from models.yaml."""
    row: dict[str, Any] = {"id": mid, "role": role, "name": job.get("name") or known.get("name") or mid}
    for key in FIELDS:
        if key == "name":
            continue
        value = job.get(key) if job.get(key) not in (None, "") else known.get(key)
        if value not in (None, ""):
            row[key] = value
    row.setdefault("licence", "unknown")
    if used_by:
        row["used_by"] = sorted(used_by)
    return row


def provenance_doc(models: dict[str, dict[str, Any]], asset_name: str, export_name: str) -> dict[str, Any]:
    """The provenance answer for one asset: `models` is manifest_provenance's models (id to job record)."""
    reg = _registry()
    known_models = reg.get("models") or {}
    rows = [_row(mid, "model", job or {}, known_models.get(mid) or {}) for mid, job in sorted(models.items())]
    for cid, c in sorted(companions_for(models).items()):
        rows.append(_row(cid, "companion", {}, c, used_by=[u for u in c.get("used_by") or [] if u in models]))
    licences = sorted({r["licence"] for r in rows})
    return {"asset": asset_name, "export_name": export_name, "models": rows, "licences": licences}
