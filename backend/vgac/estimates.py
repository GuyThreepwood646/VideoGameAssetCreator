"""Honest time estimates: what a stage took the last times it ran on this machine, else the stated default.

guidance.STAGE_GUIDE keeps the defaults (used when there is no history). `stage_seconds` takes the QUANTILE (three
quarters) of the last HISTORY_RUNS finished runs of the stage (job kind "<stage>.run", "material.<stage>.run" for
materials), from the same category when there are enough of those: a step that took 8 to 34 minutes is shown at the
upper end, so the time left falls and is not later raised (in round 4 the median said 14 minutes for a 24-minute
clean-up). Job time runs from the start of the job to its end, so it includes any wait for a borrowed card, the
sharpening and the face close-ups of the clean-up step; the wait in the queue before the job starts is not in it and is
stated separately (JobQueue.note_waits).
"""
from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Asset, Job

HISTORY_RUNS = 15
MIN_RUNS = 3          # fewer finished runs than this and the default stands
MIN_SECONDS = 2.0     # runs that skipped their work (an instant pass-through) say nothing about the real time
QUANTILE = 0.75       # the share of recent runs that finished within the estimate
OVERRUN_FLOOR = 0.1   # a job past its estimate shows this share of it as time left, not less and not more


def pick(runs: list[float], q: float = QUANTILE) -> float:
    """The q quantile of `runs` (linear between neighbours)."""
    xs = sorted(runs)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def seconds_left(expected: float, elapsed: float) -> float:
    """Time left on a job that has run `elapsed` seconds of an `expected` total: it only ever falls, and past the
    estimate it stays at OVERRUN_FLOOR of it."""
    return max(expected - elapsed, OVERRUN_FLOOR * expected)


def minutes_text(seconds: float) -> str:
    """"less than a minute", "about 1 minute", "about 12 minutes" (rounded up, so a wait is not understated)."""
    m = int(-(-seconds // 60))
    if seconds < 45:
        return "less than a minute"
    return f"about {m} minute" + ("" if m == 1 else "s")


def _durations(db: Session, kind: str, category: str | None) -> list[float]:
    q = select(Job.started_at, Job.finished_at).where(Job.kind == kind, Job.status == "done",
                                                      Job.started_at.is_not(None), Job.finished_at.is_not(None))
    if category:
        q = q.join(Asset, Asset.id == Job.asset_id).where(Asset.category == category)
    rows = db.execute(q.order_by(Job.finished_at.desc()).limit(HISTORY_RUNS * 2)).all()
    out = [(f - s).total_seconds() for s, f in rows]
    return [d for d in out if d >= MIN_SECONDS][:HISTORY_RUNS]


def stage_seconds(db: Session, stage: str, category: str | None = None, material: bool = False,
                  op: str = "run") -> tuple[int, int] | None:
    """(seconds, number of runs it rests on) or None when there is too little history."""
    kind = f"material.{stage}.{op}" if material else f"{stage}.{op}"
    for cat in ((category, None) if category else (None,)):
        runs = _durations(db, kind, cat)
        if len(runs) >= MIN_RUNS:
            return round(pick(runs)), len(runs)
    return None


def expected_seconds(db: Session, kind: str, category: str | None = None) -> float | None:
    """How long a job of `kind` ("prep.run", "prep.adjust", "export.run") is expected to run, from its own history,
    else the stage's stated default; None for a kind that is not a stage's."""
    from . import guidance
    parts = kind.split(".")
    material = parts[0] == "material"
    if material:
        parts = parts[1:]
    if len(parts) != 2:
        return None
    stage, op = parts
    got = stage_seconds(db, stage, category, material, op)
    if got:
        return float(got[0])
    g = guidance.stage_guide(stage)
    return float(g["estimate_seconds"]) if g and g.get("estimate_seconds") else None


def job_times(kind: str, asset_id: str | None) -> tuple[float, float] | None:
    """(seconds a job of `kind` is expected to run, seconds the step's own estimate in /api/stages says) or None when
    there is no basis. The page shows the second times the share left, so a job's progress is set from both."""
    from .db import session_factory
    with session_factory()() as db:
        a = db.get(Asset, asset_id) if asset_id else None
        expected = expected_seconds(db, kind, a.category if a is not None else None)
        stage = kind.split(".")[1] if kind.startswith("material.") else kind.split(".")[0]
        stage_expected = expected_seconds(db, f"{stage}.run", None)
    if not expected or not stage_expected:
        return None
    return expected, max(stage_expected, expected)


def activity(status: dict[str, Any]) -> str:
    """What a card is doing, in plain words, from one worker's queue status: "Drawing: Build the 3D model",
    "Lent to Clean up for the game" or "Working" when the kind is not known."""
    from . import guidance

    def step(kind: str | None) -> str | None:
        if not kind:
            return None
        parts = kind.split(".")
        stage = parts[1] if parts[0] == "material" and len(parts) > 1 else parts[0]
        g = guidance.stage_guide(stage)
        return g.get("plain_name") if g else None

    if status.get("current_job_id"):
        what = step(status.get("current_kind"))
        return f"Drawing: {what}" if what else "Working"
    if status.get("lent_to"):
        what = step(status.get("lent_kind"))
        return f"Lent to {what}" if what else "Lent to another step"
    return "Working" if status.get("in_use") else "Free"


def with_history(db: Session, stages: list[dict[str, Any]], category: str | None = None) -> list[dict[str, Any]]:
    """The /api/stages list with estimate_seconds taken from history where there is some, plus estimate_basis
    ("history" or "default") and estimate_runs."""
    out = []
    for s in stages:
        got = stage_seconds(db, s["id"], category)
        out.append({**s, "estimate_seconds": got[0], "estimate_basis": "history", "estimate_runs": got[1]} if got
                   else {**s, "estimate_basis": "default", "estimate_runs": 0})
    return out


def suggest_with_history(db: Session, result: dict[str, Any], category: str, platform: str) -> dict[str, Any]:
    """guidance.suggest's estimates with history laid over them. The 3D build keeps the slower default for the high
    platform, and a stage the category skips (the rig of a prop, estimated at 5 seconds) is left alone."""
    est = dict(result["estimates"])
    basis = {}
    material = category == "material"
    for stage, default in est.items():
        if default <= 5 and not material:
            basis[stage] = "default"
            continue
        got = stage_seconds(db, stage, None if material else category, material)
        if not got:
            basis[stage] = "default"
            continue
        est[stage] = max(got[0], default) if (stage == "mesh" and platform == "high") else got[0]
        basis[stage] = "history"
    return {**result, "estimates": est, "estimate_basis": basis,
            "total_minutes": max(1, round(sum(est.values()) / 60))}
