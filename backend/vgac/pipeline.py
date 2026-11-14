"""Pipeline rules shared by the HTTP API and the autopilot (docs/CODE-AUDIT.md H1).

Plain functions over a Session: which version is kept, creating a version (the first of a stage is kept
automatically), submitting its job to the instance that runs the stage, and starting a stage run with the right
parent. They raise PipelineError subclasses; main.py maps them to HTTP responses with the same status codes and
messages the endpoints always used.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select as sql_select
from sqlalchemy.orm import Session

from . import firstrun, reference
from .db import Asset, Job, Version, job_to_dict, version_to_dict
from .jobs import JobQueue, active_queue, job_kind
from .reference import STAGE_ORDER


class PipelineError(Exception):
    """A request the pipeline cannot carry out. `status` is the HTTP status main.py answers with."""

    status = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class BadRequest(PipelineError):
    status = 400


class NotFound(PipelineError):
    status = 404


class Conflict(PipelineError):
    status = 409


class Unavailable(PipelineError):
    status = 503


def queue() -> JobQueue:
    q = active_queue()
    if q is None:
        raise Unavailable("job queue not running")
    return q


def validate_params(stage: str, params: dict[str, Any] | None, category: str | None = None) -> dict[str, Any]:
    """Adjust knobs checked against the stage schema (BadRequest on a bad value). With a category, knobs that do
    not apply to it are dropped."""
    try:
        return reference.validate_params(stage, params, category)
    except ValueError as e:
        raise BadRequest(str(e)) from None


def get_version(db: Session, vid: str) -> Version:
    v = db.get(Version, vid)
    if v is None:
        raise NotFound("version not found")
    return v


def selected(db: Session, asset_id: str, stage: str) -> Version | None:
    """The kept version of a stage, if any."""
    return db.scalar(sql_select(Version).where(Version.asset_id == asset_id, Version.stage == stage, Version.selected == True))  # noqa: E712


def select(db: Session, v: Version) -> None:
    """Keep `v`; its siblings in the stage are unkept."""
    for sib in db.scalars(sql_select(Version).where(Version.asset_id == v.asset_id, Version.stage == v.stage)).all():
        sib.selected = sib.id == v.id
    db.commit()


def new_version(db: Session, asset: Asset, stage: str, parent: Version | None, params: dict[str, Any]) -> Version:
    """A queued version with the stage defaults under `params`. The first version of a stage is kept automatically so
    the next stage has a parent."""
    merged = {k: v for k, v in reference.stage_defaults(stage).items() if v is not None}
    merged.update(params or {})
    v = Version(asset_id=asset.id, stage=stage, parent_version_id=parent.id if parent else None, status="queued")
    v.params = merged
    db.add(v)
    db.commit()
    db.refresh(v)
    if selected(db, asset.id, stage) is None:
        v.selected = True
        db.commit()
    return v


def submit(db: Session, asset: Asset, v: Version, op: str, payload: dict[str, Any]) -> Job:
    """Queue the job for `v` on the instance that runs its stage, or on the card that takes over while that one is
    turned off (jobs.route). When no card that is on can run it, the job waits on the preferred card with a
    "Waiting for ... to be turned back on" message and the version stays queued."""
    q = queue()
    kind = job_kind(v.stage, op, asset.category)
    instance, message = q.place(v.stage, asset.category)
    found = firstrun.blockers(q.settings, v.stage, asset.category, instance, q.supervisor, disabled=q.disabled)
    if found:
        text = firstrun.sentence(found)
        v.status, v.error = "error", text
        db.commit()
        raise Conflict(text)
    body = {"version_id": v.id, "op": op, **payload}
    job = q.submit(instance, kind, asset.id, v.id, body, message=message)
    db.refresh(v)
    return job


def previous_stage(category: str, stage: str) -> str:
    """The stage a run of `stage` builds on: the one before it, walking back only over stages the category lists in
    `skips` (material skips turnaround). Never called for the first stage."""
    skips = set(reference.category(category).get("skips") or [])
    idx = STAGE_ORDER.index(stage) - 1
    while idx > 0 and STAGE_ORDER[idx] in skips:
        idx -= 1
    return STAGE_ORDER[idx]


def run_stage(db: Session, a: Asset, stage: str, count: int = 1, params: dict[str, Any] | None = None,
              parent_version_id: str | None = None, prepare: Any = None) -> list[dict[str, Any]]:
    """Start `count` versions of a stage (more than one only for concept). Returns [{job, version}] as dicts.
    `prepare(version)`, when given, runs after each version exists and before its job is queued (the API writes the
    person's own view pictures into the version folder there)."""
    if stage not in STAGE_ORDER:
        raise BadRequest(f"unknown stage {stage!r}; stages: {list(STAGE_ORDER)}")
    count = count if stage == "concept" else 1
    parent: Version | None = None
    if stage != "concept":
        if parent_version_id:
            parent = get_version(db, parent_version_id)
            if parent.asset_id != a.id:
                raise BadRequest("parent version belongs to another asset")
        else:
            # The parent is the kept version of the previous stage, walking back only over stages this category
            # skips, never over a stage the person simply has not finished.
            prev = previous_stage(a.category, stage)
            parent = selected(db, a.id, prev)
            if parent is None:
                raise Conflict(f"no selected {prev} version to build on")
        if parent.status != "done":
            raise Conflict(f"parent version is {parent.status}, not done")
    q = queue()
    found = firstrun.blockers(q.settings, stage, a.category, None, q.supervisor, disabled=q.disabled)
    if found:
        raise Conflict(firstrun.sentence(found))
    base_params = validate_params(stage, params, a.category)
    if stage == "concept" and not base_params.get("model") and getattr(a, "concept_model", None):
        base_params["model"] = a.concept_model
    versions, jobs = [], []
    for i in range(count):
        p = dict(base_params)
        if count > 1 and p.get("seed") is not None:
            p["seed"] = int(p["seed"]) + i
        v = new_version(db, a, stage, parent, p)
        if prepare is not None:
            prepare(v)
        job = submit(db, a, v, "run", {"params": p})
        versions.append(v)
        jobs.append(job)
    return [{"job": job_to_dict(j), "version": version_to_dict(v)} for j, v in zip(jobs, versions)]
