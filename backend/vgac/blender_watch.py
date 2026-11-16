"""Notice when the Blender behind `blender_exe` is no longer the one that was chosen.

Steam and the Microsoft Store update Blender by themselves, so the saved path can hold another version tomorrow. This
remembers the version seen at the path (`local/blender_seen.json`, next to machine.json) and compares at every
setup check and before every Blender job. It never raises and never blocks a job that can still run: a usable
different version is used and only mentioned, in plain words; one that is too old or gone is named, with what to do.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from . import config, minimums

log = logging.getLogger("vgac.blender_watch")

TESTED = minimums.tested("blender")  # the version VGAC was tested with (data/minimum_versions.json)


def _file() -> Path | None:
    mp = config.machine_path()
    return mp.with_name("blender_seen.json") if mp is not None else None


def _load() -> dict[str, Any]:
    f = _file()
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f and f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save(d: dict[str, Any]) -> None:
    f = _file()
    if f is None:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d, indent=1), encoding="utf-8")
    except OSError as e:
        log.info("could not save %s: %s", f, e)


def _vtext(v: tuple[int, ...] | None) -> str:
    return ".".join(str(x) for x in v) if v else ""


def check(exe: Path | str | None, runner: Callable[[Path], str] | None = None) -> dict[str, Any] | None:
    """None when nothing changed (or nothing is known); otherwise
    {kind: changed|old|unreadable|missing, from, to, tested, usable, message}. The first sight of a path is remembered silently."""
    try:
        return _check(exe, runner)
    except Exception as e:  # noqa: BLE001 - this must never be the reason a job or a check fails
        log.info("blender watch failed: %s", e)
        return None


def _check(exe: Path | str | None, runner: Callable[[Path], str] | None) -> dict[str, Any] | None:
    from . import preflight

    if not exe:
        return None
    p = Path(exe)
    rec = _load()
    known = rec.get("path") == str(p) and rec.get("version")
    was = str(rec.get("version") or "")
    if not p.is_file():
        if not known:
            return None
        return {"kind": "missing", "from": was, "to": "", "tested": False, "usable": False,
                "message": f"Blender {was}, which was at {p}, is no longer there (it may have been updated, moved or uninstalled)."}
    ver = preflight.blender_version(p, runner or (lambda x: preflight._run_version(x)))
    now = _vtext(ver)
    if not now:
        if not known:
            return None
        return {"kind": "unreadable", "from": was, "to": "", "tested": False, "usable": False,
                "message": f"The Blender at {p} was {was}, but it no longer says which version it is. Check that it starts."}
    if not known:
        _save({"path": str(p), "version": now})
        return None
    if now == was:
        return None
    assert ver is not None
    tested = now == TESTED
    if ver[:2] < preflight.BLENDER_MIN:
        return {"kind": "old", "from": was, "to": now, "tested": False, "usable": False,
                "message": f"Blender changed from {was} to {now}, which is too old: the app needs {minimums.phrase('blender')}. Choose a newer Blender in the setup card."}
    tail = (f"{now} is the version VGAC was tested with." if tested else
            f"VGAC was tested with {TESTED}, so {now} is untested. It will be used; if a Blender step misbehaves, say so in the setup card.")
    return {"kind": "changed", "from": was, "to": now, "tested": tested, "usable": True,
            "message": f"Blender changed from {was} to {now} (Steam and the Microsoft Store update it by themselves). {tail}"}


def accept(exe: Path | str | None, runner: Callable[[Path], str] | None = None) -> None:
    """The person has seen the notice: remember the version now at the path."""
    from . import preflight

    if exe and Path(exe).is_file():
        ver = preflight.blender_version(Path(exe), runner or (lambda x: preflight._run_version(x)))
        if ver:
            _save({"path": str(Path(exe)), "version": _vtext(ver)})
