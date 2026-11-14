"""Honest time estimates: what a stage took the last times it ran on this machine, else the stated default.

guidance.STAGE_GUIDE keeps the defaults (used when there is no history). `stage_seconds` takes the median of the last
HISTORY_RUNS finished runs of the stage (job kind "<stage>.run", "material.<stage>.run" for materials), from the same
category when there are enough of those. Job time runs from the start of the job to its end, so it includes any wait
for a borrowed card, the sharpening and the face close-ups of the clean-up step.
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


def _durations(db: Session, kind: str, category: str | None) -> list[float]:
    q = select(Job.started_at, Job.finished_at).where(Job.kind == kind, Job.status == "done",
                                                      Job.started_at.is_not(None), Job.finished_at.is_not(None))
    if category:
        q = q.join(Asset, Asset.id == Job.asset_id).where(Asset.category == category)
    rows = db.execute(q.order_by(Job.finished_at.desc()).limit(HISTORY_RUNS * 2)).all()
    out = [(f - s).total_seconds() for s, f in rows]
    return [d for d in out if d >= MIN_SECONDS][:HISTORY_RUNS]


def stage_seconds(db: Session, stage: str, category: str | None = None, material: bool = False) -> tuple[int, int] | None:
    """(median seconds, number of runs it rests on) or None when there is too little history."""
    kind = f"material.{stage}.run" if material else f"{stage}.run"
    for cat in ((category, None) if category else (None,)):
        runs = _durations(db, kind, cat)
        if len(runs) >= MIN_RUNS:
            return round(statistics.median(runs)), len(runs)
    return None


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
