"""Preferences the person sets in the app that must survive a restart, kept as JSON at <library_path>/prefs.json.

Today these are the graphics card settings (Health page, "Graphics cards"):

    {"disabled_instances": ["b"],                                  cards turned off (default: none)
     "allowed_kinds": {"a": ["pictures", "model", "review"]},       what each card may do (default: every kind)
     "free_after_job": {"a": false, "b": true}}                     free the card's memory after each job (default: off)

The file is read when the job queue starts and written on every change. A missing or unreadable file means the
defaults. Writes go to a temporary file first and replace the old one, so a crash mid-write never leaves half a file.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger("vgac.prefs")

PREFS_FILE = "prefs.json"
_lock = threading.Lock()


def defaults() -> dict[str, Any]:
    return {"disabled_instances": [], "allowed_kinds": {}, "free_after_job": {}}


def prefs_path(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).library_path / PREFS_FILE


def _normalise(out: dict[str, Any]) -> dict[str, Any]:
    names = out.get("disabled_instances")
    out["disabled_instances"] = sorted({str(n) for n in names}) if isinstance(names, list) else []
    kinds = out.get("allowed_kinds")
    out["allowed_kinds"] = ({str(k): [str(x) for x in v] for k, v in kinds.items() if isinstance(v, list)}
                            if isinstance(kinds, dict) else {})
    free = out.get("free_after_job")
    out["free_after_job"] = {str(k): bool(v) for k, v in free.items()} if isinstance(free, dict) else {}
    return out


def load(settings: Settings | None = None) -> dict[str, Any]:
    """The stored preferences over the defaults. Never raises: a bad file is logged and ignored."""
    out = defaults()
    p = prefs_path(settings)
    try:
        with _lock:
            raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return out
    except (OSError, ValueError) as e:
        log.warning("could not read %s (%s); using the defaults", p, e)
        return out
    if isinstance(raw, dict):
        out.update(raw)
    return _normalise(out)


def save(prefs: dict[str, Any], settings: Settings | None = None) -> None:
    p = prefs_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with _lock:
        tmp.write_text(json.dumps(prefs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, p)


def update(settings: Settings | None = None, **fields: Any) -> dict[str, Any]:
    """Set the given top-level fields, keep the rest, save. Returns the saved preferences."""
    prefs = load(settings)
    prefs.update(fields)
    prefs = _normalise(prefs)
    save(prefs, settings)
    return prefs


def disabled_instances(settings: Settings | None = None) -> list[str]:
    return list(load(settings)["disabled_instances"])


def set_disabled_instances(names: list[str] | set[str], settings: Settings | None = None) -> None:
    update(settings, disabled_instances=sorted(set(names)))
