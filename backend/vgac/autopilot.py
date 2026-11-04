"""Autopilot: run every stage of an asset in order, keep results that pass, pause only when a human is needed.

Rules (decision D11): concept makes two candidates and the automated metrics pick; every other stage makes one.
A version is kept when it finished and has no failed check of severity "block" (stop_on="block"), no failed check at
all (stop_on="warn"), or always (stop_on="never"). A stage that already has finished versions but none kept (the
user paused, fixed, or rejected) is re-evaluated instead of re-run. State lives on Asset.autopilot_json and every
change is pushed on the websocket bus as {"type": "autopilot", "asset_id", "autopilot"}.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from .db import Asset, Version, session_factory
from .events import bus
from .reference import STAGE_ORDER

log = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}
POLL_SECONDS = 3.0
CONCEPT_CANDIDATES = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state(until_stage: str = "export", stop_on: str = "block") -> dict[str, Any]:
    return {"status": "idle", "stage": None, "job_id": None, "version_id": None, "reason": None, "stop_on": stop_on,
            "until_stage": until_stage, "started_at": None, "updated_at": _now(), "stages_done": []}


def get_state(a: Asset) -> dict[str, Any]:
    return a.autopilot or default_state()


def _set(aid: str, **fields: Any) -> dict[str, Any]:
    with session_factory()() as db:
        a = db.get(Asset, aid)
        state = get_state(a)
        state.update(fields)
        state["updated_at"] = _now()
        a.autopilot = state
        db.commit()
    bus.publish_threadsafe({"type": "autopilot", "asset_id": aid, "autopilot": state})
    return state


def _failed_checks(v: Version) -> list[dict[str, Any]]:
    return [c for c in (v.metrics.get("checks") or []) if not c.get("passed")]


def _passes(v: Version, stop_on: str) -> tuple[bool, str | None]:
    failed = _failed_checks(v)
    if stop_on == "never" or not failed:
        return True, None
    blocking = [c for c in failed if c.get("severity") == "block"]
    if stop_on == "warn" or blocking:
        c = (blocking or failed)[0]
        return False, c.get("help") or f"check {c.get('name')} failed: {c.get('message')}"
    return True, None


def _rank(v: Version) -> tuple[int, int, float, float]:
    """Gate passed, then checks passed, then silhouette match, then newest (so a fix beats its predecessor on a tie)."""
    m = v.metrics or {}
    ts = v.created_at.timestamp() if v.created_at else 0.0
    return (1 if m.get("gate_ok", not _failed_checks(v)) else 0, int(m.get("checks_passed") or 0), float(m.get("silhouette_iou") or 0.0), ts)


def start(aid: str, until_stage: str = "export", stop_on: str = "block", fix: dict[str, Any] | None = None) -> dict[str, Any]:
    """fix = {"stage", "params"}: before continuing, run that stage once more (as an adjust of its kept version when
    there is one) with those params; later stages are unkept so they are rebuilt from the fixed result."""
    if until_stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage {until_stage!r}")
    if stop_on not in ("block", "warn", "never"):
        raise ValueError("stop_on must be block, warn or never")
    if fix and fix.get("stage") not in STAGE_ORDER:
        raise ValueError("fix.stage must be a stage id")
    t = _tasks.get(aid)
    if t is not None and not t.done():
        return _set(aid)
    state = _set(aid, status="running", reason=None, stop_on=stop_on, until_stage=until_stage, started_at=_now(), pending_fix=fix or None)
    _tasks[aid] = asyncio.get_running_loop().create_task(_run(aid))
    return state


def stop(aid: str) -> dict[str, Any]:
    t = _tasks.get(aid)
    if t is not None and not t.done():
        t.cancel()
    return _set(aid, status="paused", reason="Stopped by you. Press Make it to continue.")


async def _wait(version_ids: list[str]) -> None:
    """Block until every version has finished (done, error or rejected)."""
    while True:
        await asyncio.sleep(POLL_SECONDS)
        with session_factory()() as db:
            vs = [db.get(Version, vid) for vid in version_ids]
            if all(v is None or v.status in ("done", "error", "rejected") for v in vs):
                return


async def _run(aid: str) -> None:
    from . import main as m  # late import: main imports this module

    try:
        while True:
            with session_factory()() as db:
                a = db.get(Asset, aid)
                state = get_state(a)
                until = state.get("until_stage", "export")
                stop_on = state.get("stop_on", "block")
                # A requested fix: re-run its stage with the params (adjust of the kept version when one exists),
                # unkeep everything after it, then let the normal loop pick the result up.
                fix = state.get("pending_fix")
                if fix:
                    fstage = fix["stage"]
                    kept = m._selected(db, aid, fstage)
                    later = STAGE_ORDER[STAGE_ORDER.index(fstage) + 1:]
                    # Everything built on the old result is superseded: mark it rejected so it is rebuilt, not re-kept.
                    for v in a.versions:
                        if v.stage in later:
                            v.selected = False
                            if v.status == "done":
                                v.status = "rejected"
                    if kept is not None and kept.status == "done":
                        v = m._new_version(db, a, fstage, kept, {**kept.params, **(fix.get("params") or {})})
                        job = m._submit(db, a, v, "adjust", {"params": fix.get("params") or {}})
                        pairs = [{"job": m.job_to_dict(job), "version": m.version_to_dict(v)}]
                    else:
                        pairs = m._run_stage_internal(db, a, fstage, count=1, params=fix.get("params") or {})
                    for v in a.versions:
                        if v.stage == fstage:
                            v.selected = False
                    db.commit()
                    target = fstage
                    version_ids = [pair["version"]["id"] for pair in pairs]
                    _set(aid, status="running", stage=target, job_id=pairs[0]["job"]["id"], version_id=version_ids[0], pending_fix=None)
                    await _wait(version_ids)
                    continue
                stages_done: list[str] = []
                target: str | None = None
                for s in STAGE_ORDER:
                    sel = m._selected(db, aid, s)
                    if sel is not None and sel.status == "done":
                        stages_done.append(s)
                        if s == until:
                            break
                        continue
                    target = s
                    break
                if target is None:
                    _set(aid, status="done", stage=None, job_id=None, reason=None, stages_done=stages_done)
                    return
                # Versions already queued or running in this stage are waited for, never duplicated.
                inflight = [v for v in a.versions if v.stage == target and v.status in ("queued", "running")]
                if inflight:
                    version_ids = [v.id for v in inflight]
                    _set(aid, status="running", stage=target, job_id=inflight[0].job_id, version_id=inflight[0].id, stages_done=stages_done)
                    await _wait(version_ids)
                    continue
                # Existing finished versions in this stage (after a pause) are judged instead of re-run.
                finished = [v for v in a.versions if v.stage == target and v.status == "done"]
                if finished:
                    best = sorted(finished, key=_rank, reverse=True)[0]
                    ok, reason = _passes(best, stop_on)
                    if ok:
                        m._select(db, best)
                        _set(aid, stage=target, version_id=best.id, stages_done=stages_done)
                        continue
                    _set(aid, status="paused", stage=target, version_id=best.id, job_id=None, reason=reason, stages_done=stages_done)
                    return
                # Run the stage.
                count = CONCEPT_CANDIDATES if target == "concept" else 1
                try:
                    pairs = m._run_stage_internal(db, a, target, count=count, params={})
                except Exception as e:  # noqa: BLE001
                    _set(aid, status="error", stage=target, reason=f"Could not start the step: {e}", stages_done=stages_done)
                    return
                version_ids = [p["version"]["id"] for p in pairs]
                _set(aid, status="running", stage=target, job_id=pairs[0]["job"]["id"], version_id=version_ids[0], stages_done=stages_done)
            await _wait(version_ids)
            with session_factory()() as db:
                vs = [db.get(Version, vid) for vid in version_ids]
                done = [v for v in vs if v.status == "done"]
                if not done:
                    err = next((v.error for v in vs if v.error), None) or "the step failed"
                    _set(aid, status="error", stage=target, reason=f"The step failed: {str(err)[:300]}")
                    return
                best = sorted(done, key=_rank, reverse=True)[0]
                ok, reason = _passes(best, stop_on)
                if not ok:
                    _set(aid, status="paused", stage=target, version_id=best.id, job_id=None, reason=reason)
                    return
                m._select(db, best)
                _set(aid, stage=target, version_id=best.id, job_id=None)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("autopilot %s crashed", aid)
        _set(aid, status="error", reason=f"Autopilot stopped unexpectedly: {e}")
