"""In-process job queue: one asyncio worker per instance (a, b, host), job rows as the queue.

A worker loops over queued jobs for its instance, waits while free system RAM is below min_free_ram_gb, runs the
stage operation named by the job kind ("<stage>.<run|refine|adjust>", or "material.<stage>.<op>" for the tileable
material category), and records status, progress, message and timing in the job table. One job per instance at a
time, never two. Job and version changes are pushed to the websocket bus. Nothing is held only in memory, so a
restart resumes queued jobs (running jobs from a crashed server are re-queued at start).
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

import psutil
from sqlalchemy import func, select

from .comfy import ComfyClient
from .config import Settings, get_settings
from .db import Job, Version, job_to_dict, session_factory, utcnow, version_to_dict
from .events import bus
from .reference import STAGE_ORDER

log = logging.getLogger("vgac.jobs")
MESSAGE_THROTTLE_SECONDS = 5.0

GB = 1024**3
HOST = "host"



def instance_for(stage: str, category: str, settings: Settings | None = None) -> str:
    """Which instance runs `stage` (Settings.stage_instances, or material_stage_instances for the material category)."""
    settings = settings or get_settings()
    table = settings.material_stage_instances if category == "material" else settings.stage_instances
    return table[stage]


def check_stage_instances(settings: Settings) -> None:
    """Every stage maps to a configured instance (or the host), for both tables and the judge. Raises ValueError."""
    known = set(settings.instances) | {HOST}
    for label, table in (("stage_instances", settings.stage_instances), ("material_stage_instances", settings.material_stage_instances)):
        missing = [s for s in STAGE_ORDER if s not in table]
        if missing:
            raise ValueError(f"config {label} has no instance for stage(s) {missing}")
        bad = {s: i for s, i in table.items() if i not in known}
        if bad:
            raise ValueError(f"config {label} names unknown instance(s) {bad}; known: {sorted(known)}")
    if settings.judge_instance not in settings.instances:
        raise ValueError(f"config judge_instance {settings.judge_instance!r} is not a ComfyUI instance; known: {sorted(settings.instances)}")


def job_kind(stage: str, op: str, category: str) -> str:
    if category == "material":
        return f"material.{stage}.{op}"
    return f"{stage}.{op}"


def parse_kind(kind: str) -> tuple[str, str, bool]:
    """-> (stage, op, is_material)"""
    parts = kind.split(".")
    if parts[0] == "material" and len(parts) == 3:
        return parts[1], parts[2], True
    if len(parts) == 2:
        return parts[0], parts[1], False
    raise ValueError(f"bad job kind {kind!r}")


def free_ram_gb() -> float:
    return psutil.virtual_memory().available / GB


class JobCancelled(Exception):
    pass


@dataclass
class JobContext:
    job_id: str
    instance: str
    payload: dict[str, Any]
    settings: Settings
    clients: dict[str, ComfyClient]
    _progress_cb: Callable[[float, str], None]
    processes: list[subprocess.Popen] = field(default_factory=list)
    cancelled: bool = False

    @property
    def client(self) -> ComfyClient | None:
        return self.clients.get(self.instance)

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        self._progress_cb(fraction if fraction is not None else -1.0, message or "")

    def register_process(self, proc: subprocess.Popen) -> None:
        self.processes.append(proc)

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled()


def _publish_job(job: Job) -> None:
    bus.publish_threadsafe({"type": "job", "job": job_to_dict(job)})


def _publish_version(version: Version) -> None:
    bus.publish_threadsafe({"type": "version", "version": version_to_dict(version)})


@dataclass
class InstanceWorker:
    name: str
    settings: Settings
    clients: dict[str, ComfyClient]
    paused: bool = False
    current_job_id: str | None = None
    current_ctx: JobContext | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task | None = None
    _job_task: asyncio.Task | None = None
    _loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name=f"vgac-worker-{self.name}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _next_queued(self) -> str | None:
        with session_factory()() as db:
            job = db.scalar(select(Job).where(Job.instance == self.name, Job.status == "queued").order_by(Job.created_at, Job.id).limit(1))
            return job.id if job else None

    async def _run(self) -> None:
        log.info("worker %s started", self.name)
        while True:
            try:
                if self.paused:
                    await self._sleep(2.0)
                    continue
                job_id = self._next_queued()
                if job_id is None:
                    await self._sleep(2.0)
                    continue
                free = free_ram_gb()
                if free < self.settings.min_free_ram_gb:
                    log.info("worker %s holding job %s: free RAM %.1f GB < %.1f GB", self.name, job_id, free, self.settings.min_free_ram_gb)
                    await self._sleep(5.0)
                    continue
                self._job_task = asyncio.create_task(self._execute(job_id), name=f"vgac-job-{job_id[:8]}")
                try:
                    await self._job_task
                except asyncio.CancelledError:
                    me = asyncio.current_task()
                    if me is not None and me.cancelling():
                        raise
                finally:
                    self._job_task = None
                me = asyncio.current_task()
                if me is not None and me.cancelling():
                    raise asyncio.CancelledError()  # the stop() cancel arrived while the job was swallowing its own
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker %s loop error", self.name)
                await self._sleep(5.0)

    async def _sleep(self, seconds: float) -> None:
        self.wake.clear()
        try:
            await asyncio.wait_for(self.wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def cancel_current(self, job_id: str) -> bool:
        """Cancel the running job. Safe to call from a request thread: the task cancel and the ComfyUI
        interrupt are scheduled on the worker's loop."""
        if self.current_job_id != job_id:
            return False
        ctx = self.current_ctx
        if ctx is not None:
            ctx.cancelled = True
            for p in ctx.processes:
                try:
                    p.kill()
                except Exception as e:  # noqa: BLE001
                    log.debug("job %s: could not kill child process: %s", job_id[:8], e)
        task = self._job_task
        client = self.clients.get(self.name)

        def _do() -> None:
            if task is not None and not task.done():
                task.cancel()
            if client is not None:
                asyncio.ensure_future(client.interrupt())

        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _do()
        else:
            loop.call_soon_threadsafe(_do)
        return True

    async def _execute(self, job_id: str) -> None:
        from . import stages  # late import: stages import this module for JobContext

        Session = session_factory()
        with Session() as db:
            job = db.get(Job, job_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            job.started_at = utcnow()
            job.message = "starting"
            db.commit()
            kind = job.kind
            payload = job.payload
            version_id = job.version_id
            _publish_job(job)
            is_judge = parse_kind(kind)[1] == "judge"
            if version_id and not is_judge:
                v = db.get(Version, version_id)
                if v is not None and v.status == "rejected":
                    # Rejected while queued: nothing to build.
                    job.status = "cancelled"
                    job.error = job.message = "cancelled"
                    job.finished_at = utcnow()
                    db.commit()
                    _publish_job(job)
                    return
                if v is not None:
                    v.status = "running"
                    db.commit()
                    _publish_version(v)

        self.current_job_id = job_id
        log.info("worker %s running job %s (%s)", self.name, job_id, kind)
        last: dict[str, Any] = {"fraction": -1.0, "message": "", "at": 0.0}

        def progress(fraction: float, message: str) -> None:
            # A fraction change always goes out; a message-only change (the elapsed-seconds counter) at most every
            # MESSAGE_THROTTLE_SECONDS, so a long job does not commit, log and broadcast once per poll.
            now = time.monotonic()
            fraction_changed = fraction >= 0 and abs(fraction - last["fraction"]) >= 0.005
            message_changed = bool(message) and message != last["message"]
            if not fraction_changed and (not message_changed or now - last["at"] < MESSAGE_THROTTLE_SECONDS):
                return
            with Session() as db2:
                j = db2.get(Job, job_id)
                if j is None:
                    return
                if fraction_changed:
                    j.progress = max(0.0, min(1.0, fraction))
                    last["fraction"] = fraction
                if message_changed:
                    j.message = message
                    last["message"] = message
                last["at"] = now
                db2.commit()
                log.log(logging.INFO if fraction_changed else logging.DEBUG, "job %s: %.0f%% %s", job_id[:8], j.progress * 100, j.message)
                _publish_job(j)

        ctx = JobContext(job_id=job_id, instance=self.name, payload=payload, settings=self.settings, clients=self.clients,
                         _progress_cb=progress)
        self.current_ctx = ctx
        error: str | None = None
        cancelled = False
        requeued = False
        try:
            await stages.execute(ctx, kind, payload)
        except JobCancelled:
            cancelled = True
            error = "cancelled"
        except asyncio.CancelledError:
            if ctx.cancelled:
                cancelled = True
                error = "cancelled"
            else:
                # Not the user: the server is shutting down. Put the job back so a restart resumes it.
                requeued = True
                error = "re-queued after restart"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            log.error("job %s failed: %s\n%s", job_id, e, traceback.format_exc()[-4000:])
        finally:
            self.current_job_id = None
            self.current_ctx = None
            with Session() as db:
                job = db.get(Job, job_id)
                if job is not None:
                    job.finished_at = utcnow()
                    if requeued:
                        job.status = "queued"
                        job.started_at = None
                        job.finished_at = None
                        job.progress = 0.0
                        job.message = error
                    elif error is None:
                        job.status = "done"
                        job.progress = 1.0
                        job.message = "done"
                    elif cancelled:
                        job.status = "cancelled"
                        job.error = error
                        job.message = "cancelled"
                    else:
                        job.status = "error"
                        job.error = error
                        job.message = error.splitlines()[0][:200]
                    db.commit()
                    _publish_job(job)
                    if job.version_id:
                        v = db.get(Version, job.version_id)
                        if v is not None:
                            if is_judge:
                                if error is not None:
                                    m = v.metrics
                                    m["judge_error"] = error
                                    v.metrics = m
                            elif v.status == "rejected":
                                pass  # the person rejected it while it ran; the files are kept, the verdict stands
                            elif requeued:
                                v.status = "queued"
                            elif error is None:
                                v.status = "done"
                            elif cancelled:
                                v.status = "rejected"
                                v.error = error
                            else:
                                v.status = "error"
                                v.error = error
                            db.commit()
                            _publish_version(v)
        if requeued:
            raise asyncio.CancelledError()


_active: "JobQueue | None" = None


def active_queue() -> "JobQueue | None":
    """The running queue (set by JobQueue.start, cleared by stop), so the API and autopilot share one."""
    return _active


class JobQueue:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.workers: dict[str, InstanceWorker] = {}
        self.clients: dict[str, ComfyClient] = {}

    def _requeue_orphans(self) -> None:
        """Jobs left 'running' by a crashed server go back to queued."""
        with session_factory()() as db:
            for j in db.scalars(select(Job).where(Job.status == "running")).all():
                j.status = "queued"
                j.message = "re-queued after restart"
            for v in db.scalars(select(Version).where(Version.status == "running")).all():
                v.status = "queued"
            db.commit()

    async def start(self) -> None:
        global _active
        try:
            check_stage_instances(self.settings)
        except ValueError as e:
            # Not fatal: jobs for the stages it names fail at submit with "unknown instance", as before.
            log.warning("%s", e)
        bus.bind()
        self._requeue_orphans()
        for name, inst in self.settings.instances.items():
            self.clients[name] = ComfyClient(inst.url, name=name, input_dir=self.settings.comfy_input_dir, output_dir=inst.output_dir)
        for name in list(self.settings.instances) + [HOST]:
            w = InstanceWorker(name=name, settings=self.settings, clients=self.clients)
            w.start()
            self.workers[name] = w
        _active = self

    async def stop(self) -> None:
        global _active
        for w in self.workers.values():
            await w.stop()
        self.workers.clear()
        for c in self.clients.values():
            await c.aclose()
        if _active is self:
            _active = None

    def instances(self) -> list[str]:
        return list(self.settings.instances) + [HOST]

    def submit(self, instance: str, kind: str, asset_id: str | None, version_id: str | None, payload: dict[str, Any]) -> Job:
        if instance not in self.instances():
            raise ValueError(f"unknown instance {instance!r}; known: {self.instances()}")
        parse_kind(kind)
        with session_factory()() as db:
            job = Job(instance=instance, kind=kind, asset_id=asset_id, version_id=version_id, status="queued",
                      payload_json=json.dumps(payload or {}))
            db.add(job)
            db.flush()  # the id comes from the column default, so it exists only after the flush
            if version_id:
                v = db.get(Version, version_id)
                if v is not None:
                    v.job_id = job.id
            db.commit()
            db.refresh(job)
        _publish_job(job)
        self.wake(instance)
        return job

    def wake(self, instance: str) -> None:
        w = self.workers.get(instance)
        if w:
            w.wake.set()

    def cancel(self, job_id: str) -> Job | None:
        with session_factory()() as db:
            job = db.get(Job, job_id)
            if job is None:
                return None
            if job.status == "queued":
                job.status = "cancelled"
                job.error = "cancelled"
                job.message = "cancelled"
                job.finished_at = utcnow()
                if job.version_id:
                    v = db.get(Version, job.version_id)
                    if v is not None and v.status != "rejected":
                        v.status = "rejected"
                        v.error = "cancelled"
                        db.commit()
                        _publish_version(v)
                db.commit()
                _publish_job(job)
                return job
            if job.status == "running":
                w = self.workers.get(job.instance)
                if w is not None:
                    w.cancel_current(job.id)
                job.message = "cancelling"
                db.commit()
                _publish_job(job)
                return job
            return job

    def set_paused(self, instance: str, paused: bool) -> None:
        w = self.workers[instance]
        w.paused = paused
        w.wake.set()

    def status(self) -> dict[str, Any]:
        """Per worker: paused, current job, queued count and the next job in the order the worker picks (one query)."""
        ranked = select(
            Job.instance, Job.id,
            func.row_number().over(partition_by=Job.instance, order_by=(Job.created_at, Job.id)).label("rn"),
            func.count().over(partition_by=Job.instance).label("n"),
        ).where(Job.status == "queued").subquery()
        with session_factory()() as db:
            heads = {row.instance: (row.id, int(row.n)) for row in db.execute(select(ranked.c.instance, ranked.c.id, ranked.c.n).where(ranked.c.rn == 1))}
        out = {}
        for name, w in self.workers.items():
            next_id, n = heads.get(name, (None, 0))
            out[name] = {"paused": w.paused, "current_job_id": w.current_job_id, "queued": n, "next_job_id": next_id}
        return out
