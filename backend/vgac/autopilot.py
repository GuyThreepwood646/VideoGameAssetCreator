"""Autopilot: run every stage of an asset in order, keep results that pass, pause only when a human is needed.

Rules (decision D11): concept makes two candidates and the automated metrics pick; every other stage makes one.
A version is kept when it finished and has no failed check of severity "block" (stop_on="block"), no failed check at
all (stop_on="warn"), or always (stop_on="never"). A stage that already has finished versions but none kept (the
user paused, fixed, or rejected) is re-evaluated instead of re-run. State lives on Asset.autopilot_json and every
change is pushed on the websocket bus as {"type": "autopilot", "asset_id", "autopilot"}.

A Fix is the owner's decision: its result is what the later steps are built on. The version the Fix adjusts (or
redraws) is set aside, the Fix's result is judged on its own (kept when it passes, paused on as usual when it fails a
check it cannot accept), and it is never re-ranked against the older versions of its step. When the Fix's build fails
(an error, not a failed check), the version kept before it is kept again and the reason says so.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from . import pipeline
from .db import SET_ASIDE_ERROR, Asset, Version, _iso, is_stopped, job_to_dict, session_factory, utcnow, version_to_dict
from .events import bus
from .jobs import active_queue
from .reference import STAGE_ORDER

log = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}
POLL_SECONDS = 3.0
CONCEPT_CANDIDATES = 2


def default_state(until_stage: str = "export", stop_on: str = "block") -> dict[str, Any]:
    return {"status": "idle", "stage": None, "job_id": None, "version_id": None, "reason": None, "stop_on": stop_on,
            "until_stage": until_stage, "started_at": None, "updated_at": _iso(utcnow()), "stages_done": [],
            "stopped_by_user": False}


def get_state(a: Asset) -> dict[str, Any]:
    return a.autopilot or default_state()


def _set(aid: str, **fields: Any) -> dict[str, Any]:
    with session_factory()() as db:
        a = db.get(Asset, aid)
        state = get_state(a)
        state.update(fields)
        state["updated_at"] = _iso(utcnow())
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


def start(aid: str, until_stage: str = "export", stop_on: str = "block", fix: dict[str, Any] | None = None,
          from_stage: str | None = None, stage_params: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """fix = {"stage", "params"}: before continuing, run that stage once more (as an adjust of its kept version when
    there is one) with those params; later stages are unkept so they are rebuilt from the fixed result.
    from_stage: go back to that step. It is made again, built on what is kept in the step before it, and every step
    after it is rebuilt; the results they had are set aside (still in the history).
    stage_params = {stage: params}: the settings the person chose for a step's first build; used whenever this run
    starts that step afresh (never for a fix or a resume)."""
    if from_stage is not None and from_stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage {from_stage!r}")
    if until_stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage {until_stage!r}")
    if stop_on not in ("block", "warn", "never"):
        raise ValueError("stop_on must be block, warn or never")
    if stage_params and any(k not in STAGE_ORDER or not isinstance(v, dict) for k, v in stage_params.items()):
        raise ValueError("stage_params must map stage ids to settings")
    if fix and fix.get("stage") not in STAGE_ORDER:
        raise ValueError("fix.stage must be a stage id")
    if fix and fix.get("op") not in (None, "refine"):
        raise ValueError("fix.op must be refine when given")
    if fix and fix.get("op") == "refine" and not (isinstance(fix.get("instruction"), str) and 0 < len(fix["instruction"]) <= 2000):
        raise ValueError("a refine fix needs an instruction")
    t = _tasks.get(aid)
    if t is not None and not t.done():
        return _set(aid)
    state = _set(aid, status="running", reason=None, stop_on=stop_on, until_stage=until_stage, started_at=_iso(utcnow()),
                 pending_fix=fix or None, pending_from=from_stage, stopped_by_user=False, note=None,
                 stage_params=stage_params or None)
    _tasks[aid] = asyncio.get_running_loop().create_task(_run(aid))
    return state


def resume_all() -> int:
    """After a restart, pick every autopilot up where it was: its state says running but no task exists. _run
    re-evaluates from the database, so nothing is duplicated. Returns the number resumed."""
    with session_factory()() as db:
        ids = [a.id for a in db.scalars(select(Asset)).all() if (a.autopilot or {}).get("status") == "running"]
    n = 0
    for aid in ids:
        t = _tasks.get(aid)
        if t is not None and not t.done():
            continue
        _set(aid, reason=None)
        _tasks[aid] = asyncio.get_running_loop().create_task(_run(aid))
        n += 1
    if n:
        log.info("autopilot resumed for %d asset(s) after restart", n)
    return n


STOPPED_REASON = "You stopped it. Everything made so far is kept. Press Resume to carry on from this step."


def stop(aid: str) -> dict[str, Any]:
    """Stop the autopilot now. The caller also stops the step in progress (JobQueue.stop_asset); Resume starts the
    autopilot again and that step runs once more with the same settings."""
    t = _tasks.get(aid)
    if t is not None and not t.done():
        t.cancel()
    return _set(aid, status="paused", reason=STOPPED_REASON, stopped_by_user=True, job_id=None)


def steer_by_hand(aid: str) -> dict[str, Any]:
    """"Steer by hand instead": the autopilot is put away for this asset (idle), so the page stops offering to resume
    it. A running autopilot is left alone (Stop comes first). Nothing made is touched."""
    t = _tasks.get(aid)
    if t is not None and not t.done():
        return _set(aid)
    return _set(aid, status="idle", reason=None, stopped_by_user=False, job_id=None, pending_fix=None, pending_from=None,
                note=None, by_hand=True)


def _stopped_among(version_ids: list[str]) -> bool:
    """True when the person stopped one of these versions (the job log's Stop while the autopilot was waiting)."""
    with session_factory()() as db:
        return any(v is not None and is_stopped(v) for v in (db.get(Version, vid) for vid in version_ids))


async def _wait(version_ids: list[str]) -> None:
    """Block until every version has finished (done, error or rejected)."""
    while True:
        await asyncio.sleep(POLL_SECONDS)
        with session_factory()() as db:
            vs = [db.get(Version, vid) for vid in version_ids]
            if all(v is None or v.status in ("done", "error", "rejected") for v in vs):
                return


FIX_FAILED_REASON = ("The fix could not be made ({error}), so the result kept before it is used again and the later "
                     "steps are built on that.")


def _settle_fix(aid: str, vid: str, fallback: str | None, stop_on: str) -> bool:
    """After a Fix's version finished: keep it when it passes (it is never re-ranked against older versions); pause on
    it when it fails a check it cannot accept; when it errored, keep the version kept before again (said in the
    reason). Returns False when the autopilot paused or stopped."""
    with session_factory()() as db:
        v = db.get(Version, vid)
        if v is not None and v.status == "done":
            ok, reason = _passes(v, stop_on)
            if not ok:
                _set(aid, status="paused", stage=v.stage, version_id=v.id, job_id=None, reason=reason)
                return False
            pipeline.select(db, v)
            _set(aid, stage=v.stage, version_id=v.id, job_id=None)
            return True
        err = (v.error if v is not None else None) or "the step failed"
        old = db.get(Version, fallback) if fallback else None
        if old is None:
            _set(aid, status="error", stage=v.stage if v is not None else None, job_id=None,
                 reason=f"The step failed: {str(err)[:300]}")
            return False
        old.status = "done"
        db.commit()
        pipeline.select(db, old)
        bus.publish_threadsafe({"type": "version", "version": version_to_dict(old)})
        note = FIX_FAILED_REASON.format(error=str(err)[:200])
        _set(aid, stage=old.stage, version_id=old.id, job_id=None, reason=note, note=note)
        return True


async def _run(aid: str) -> None:
    try:
        while True:
            with session_factory()() as db:
                a = db.get(Asset, aid)
                state = get_state(a)
                until = state.get("until_stage", "export")
                stop_on = state.get("stop_on", "block")
                # Going back to an earlier step: its results and every later step's are set aside, so the normal
                # loop below makes them again from what is kept before that step.
                frm = state.get("pending_from")
                if frm:
                    redo = STAGE_ORDER[STAGE_ORDER.index(frm):]
                    changed: list[Version] = []
                    for v in a.versions:
                        if v.stage not in redo:
                            continue
                        if v.selected or v.status == "done" or is_stopped(v):
                            v.selected = False
                            if v.status == "done":
                                v.status = "rejected"
                            elif is_stopped(v):
                                v.error = SET_ASIDE_ERROR  # an older Stop there is history, not something to resume
                            changed.append(v)
                    db.commit()
                    for v in changed:
                        bus.publish_threadsafe({"type": "version", "version": version_to_dict(v)})
                    _set(aid, pending_from=None, stage=frm, version_id=None, job_id=None,
                         stages_done=[s for s in state.get("stages_done", []) if s not in redo])
                    continue
                # A requested fix: re-run its stage with the params (adjust of the kept version when one exists),
                # unkeep everything after it, then let the normal loop pick the result up.
                fix = state.get("pending_fix")
                if fix:
                    fstage = fix["stage"]
                    kept = pipeline.selected(db, aid, fstage)
                    later = STAGE_ORDER[STAGE_ORDER.index(fstage) + 1:]
                    # Everything built on the old result is superseded: mark it rejected so it is rebuilt, not re-kept.
                    for v in a.versions:
                        if v.stage in later:
                            v.selected = False
                            if v.status == "done":
                                v.status = "rejected"
                    # A redraw fix ("Redraw it ready for 3D") is a refine of the result the autopilot paused on.
                    base = kept if kept is not None and kept.status == "done" else None
                    if base is None and fix.get("op") == "refine" and state.get("version_id"):
                        cand = db.get(Version, state["version_id"])
                        if cand is not None and cand.asset_id == aid and cand.stage == fstage and cand.status == "done":
                            base = cand
                    # What the Fix replaces: set aside below, so the Fix's result is judged on its own; kept again only
                    # when the Fix fails to build.
                    replaced = base if fix.get("op") == "refine" else (kept if kept is not None and kept.status == "done" else None)
                    fallback = kept.id if kept is not None and kept.status == "done" else None
                    if fix.get("op") == "refine" and base is not None:
                        inherited = {k: val for k, val in base.params.items() if k != "reference_experimental"}
                        v = pipeline.new_version(db, a, fstage, base, {**inherited, **(fix.get("params") or {})})
                        job = pipeline.submit(db, a, v, "refine", {"instruction": fix["instruction"], "view": None, "mask_png": None,
                                                                    "params": fix.get("params") or {}})
                        pairs = [{"job": job_to_dict(job), "version": version_to_dict(v)}]
                    elif kept is not None and kept.status == "done":
                        v = pipeline.new_version(db, a, fstage, kept, {**kept.params, **(fix.get("params") or {})})
                        job = pipeline.submit(db, a, v, "adjust", {"params": fix.get("params") or {}})
                        pairs = [{"job": job_to_dict(job), "version": version_to_dict(v)}]
                    else:
                        pairs = pipeline.run_stage(db, a, fstage, count=1, params=fix.get("params") or {})
                    for v in a.versions:
                        if v.stage == fstage:
                            v.selected = False
                    if replaced is not None and replaced.status == "done":
                        replaced.status = "rejected"
                    db.commit()
                    target = fstage
                    version_ids = [pair["version"]["id"] for pair in pairs]
                    _set(aid, status="running", stage=target, job_id=pairs[0]["job"]["id"], version_id=version_ids[0], pending_fix=None)
                    await _wait(version_ids)
                    if _stopped_among(version_ids):
                        _set(aid, status="paused", stage=target, job_id=None, reason=STOPPED_REASON, stopped_by_user=True)
                        return
                    if not _settle_fix(aid, version_ids[0], fallback, stop_on):
                        return
                    continue
                stages_done: list[str] = []
                target: str | None = None
                for s in STAGE_ORDER:
                    sel = pipeline.selected(db, aid, s)
                    if sel is not None and sel.status == "done":
                        stages_done.append(s)
                        if s == until:
                            break
                        continue
                    target = s
                    break
                if target is None:
                    _set(aid, status="done", stage=None, job_id=None, reason=state.get("note"), stages_done=stages_done)
                    # Items set aside from a character are made after it, then put on it (vgac/items.py).
                    try:
                        from . import items

                        items.after_autopilot(aid)
                    except Exception:  # noqa: BLE001  (the character is done either way)
                        log.exception("items after the autopilot of %s", aid)
                    return
                # Versions already queued or running in this stage are waited for, never duplicated.
                inflight = [v for v in a.versions if v.stage == target and v.status in ("queued", "running")]
                if inflight:
                    version_ids = [v.id for v in inflight]
                    _set(aid, status="running", stage=target, job_id=inflight[0].job_id, version_id=inflight[0].id, stages_done=stages_done)
                    await _wait(version_ids)
                    if _stopped_among(version_ids):
                        _set(aid, status="paused", stage=target, job_id=None, reason=STOPPED_REASON, stopped_by_user=True)
                        return
                    continue
                # Existing finished versions in this stage (after a pause) are judged instead of re-run.
                finished = [v for v in a.versions if v.stage == target and v.status == "done"]
                if finished:
                    best = sorted(finished, key=_rank, reverse=True)[0]
                    ok, reason = _passes(best, stop_on)
                    if ok:
                        pipeline.select(db, best)
                        _set(aid, stage=target, version_id=best.id, stages_done=stages_done)
                        continue
                    _set(aid, status="paused", stage=target, version_id=best.id, job_id=None, reason=reason, stages_done=stages_done)
                    return
                # The person's own picture could not be prepared: stop and say so, never paint something else instead.
                if target == "concept":
                    broken = [v for v in a.versions if v.stage == "concept" and v.status == "error" and (v.params or {}).get("source") == "upload"]
                    if broken:
                        _set(aid, status="error", stage=target, version_id=broken[-1].id, job_id=None, stages_done=stages_done,
                             reason="We could not prepare your picture. Add it again, or reject that version to have a picture "
                                    "painted from the description instead.")
                        return
                # A step the person stopped earlier is resumed (same settings) instead of started a second time.
                q = active_queue()
                if q is not None and q.resume_asset(aid, target):
                    continue
                # Run the stage.
                count = CONCEPT_CANDIDATES if target == "concept" else 1
                try:
                    pairs = pipeline.run_stage(db, a, target, count=count, params=dict((state.get("stage_params") or {}).get(target) or {}))
                except Exception as e:  # noqa: BLE001
                    _set(aid, status="error", stage=target, reason=f"Could not start the step: {e}", stages_done=stages_done)
                    return
                version_ids = [p["version"]["id"] for p in pairs]
                _set(aid, status="running", stage=target, job_id=pairs[0]["job"]["id"], version_id=version_ids[0], stages_done=stages_done)
            await _wait(version_ids)
            with session_factory()() as db:
                vs = [db.get(Version, vid) for vid in version_ids]
                done = [v for v in vs if v.status == "done"]
                if not done and any(v is not None and is_stopped(v) for v in vs):
                    _set(aid, status="paused", stage=target, job_id=None, reason=STOPPED_REASON, stopped_by_user=True)
                    return
                if not done:
                    err = next((v.error for v in vs if v.error), None) or "the step failed"
                    _set(aid, status="error", stage=target, reason=f"The step failed: {str(err)[:300]}")
                    return
                best = sorted(done, key=_rank, reverse=True)[0]
                ok, reason = _passes(best, stop_on)
                if not ok:
                    _set(aid, status="paused", stage=target, version_id=best.id, job_id=None, reason=reason)
                    return
                pipeline.select(db, best)
                _set(aid, stage=target, version_id=best.id, job_id=None)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("autopilot %s crashed", aid)
        _set(aid, status="error", reason=f"Autopilot stopped unexpectedly: {e}")
