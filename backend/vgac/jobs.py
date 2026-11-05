"""In-process job queue: one asyncio worker per instance (a, b, host), job rows as the queue.

A worker loops over queued jobs for its instance, waits while free system RAM is below min_free_ram_gb, runs the
stage operation named by the job kind ("<stage>.<run|refine|adjust>", or "material.<stage>.<op>" for the tileable
material category), and records status, progress, message and timing in the job table. One job per instance at a
time, never two. Job and version changes are pushed to the websocket bus. Nothing is held only in memory, so a
restart resumes queued jobs (running jobs from a crashed server are re-queued at start).

Graphics cards can be turned off in the app, and each card can be limited to some kinds of work (prefs.json, see
prefs.py). route() picks the card for a job: the preferred one from the stage tables unless it is off or not allowed
that kind of work, else the first card that is on, allowed, and capable (Settings.instance_capabilities), else
nothing, in which case the job waits on the preferred card with a "Waiting for ..." message. JobQueue.configure
moves queued jobs (and, when a card is turned off, its running job) to where route() sends them.

On-demand start (instances.py): before a job runs on a card whose instance has a start script, the worker calls
Supervisor.ensure_running. While the instance starts the job stays queued with "Starting the graphics engine on
<gpu> (about a minute)"; if the start fails the card counts as unavailable (like a card that is off) until Try again
or the card is turned off and on, so the job moves to another card that can run it or waits with the reason. A card
that is off is never started, and host jobs never start anything. An instance the backend started is stopped when its
card is turned off, and after stop_when_idle_minutes without work (prefs.json; 0 = never).
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import psutil
from sqlalchemy import func, select

from . import prefs
from .comfy import ComfyClient
from .config import Settings, get_settings
from .db import SET_ASIDE_ERROR, STOPPED_ERROR, Asset, Job, Version, is_stopped, job_to_dict, session_factory, utcnow, version_to_dict
from .events import bus
from .instances import NotOwned, StartAborted, StartFailed, Supervisor
from .reference import STAGE_ORDER

log = logging.getLogger("vgac.jobs")
MESSAGE_THROTTLE_SECONDS = 5.0
IDLE_CHECK_SECONDS = 15.0  # how often the queue checks for idle engines to stop and engines that went away
STARTING_PREFIX = "Starting the graphics engine on "
START_FAILED_PREFIX = "Could not start the graphics engine on "

GB = 1024**3
MB = 1024**2
HOST = "host"
JUDGE = "judge"  # the routing name of the local vision judge (any stage's "<stage>.judge" job)
# The order capabilities are listed in (GET /api/gpus can_run) and their plain names for the per-card note.
CAPABILITY_ORDER = ("concept", "turnaround", "mesh", "rig", "material", JUDGE)
CAPABILITY_PLAIN = {"concept": "making pictures", "turnaround": "drawing all sides", "mesh": "building 3D models",
                    "rig": "rigging", "material": "making materials", JUDGE: "judging pictures"}


def instance_for(stage: str, category: str, settings: Settings | None = None) -> str:
    """Which instance runs `stage` (Settings.stage_instances, or material_stage_instances for the material category)."""
    settings = settings or get_settings()
    table = settings.material_stage_instances if category == "material" else settings.stage_instances
    return table[stage]


def preferred_instance(stage: str, category: str, settings: Settings | None = None) -> str:
    """The instance a stage (or "judge") runs on when every card is on."""
    settings = settings or get_settings()
    if stage == JUDGE:
        return settings.judge_instance
    return instance_for(stage, category, settings)


def capability_for(stage: str, category: str) -> str:
    """The capability a card needs to run `stage`: the stage itself, "material" for every step of the material
    category, "judge" for the local judge."""
    if stage == JUDGE:
        return JUDGE
    if category == "material":
        return "material"
    return stage


def capabilities(instance: str, settings: Settings | None = None) -> list[str]:
    """What a ComfyUI instance can run (Settings.instance_capabilities). An instance missing from that table can run
    what the stage tables and judge_instance give it. The host is not a card and has none."""
    settings = settings or get_settings()
    if instance == HOST or instance not in settings.instances:
        return []
    caps = settings.instance_capabilities.get(instance)
    if caps is None:
        derived = {s for s, i in settings.stage_instances.items() if i == instance}
        if any(i == instance for i in settings.material_stage_instances.values()):
            derived.add("material")
        if settings.judge_instance == instance:
            derived.add(JUDGE)
        caps = list(derived)
    known = [c for c in CAPABILITY_ORDER if c in caps]
    return known + sorted(set(caps) - set(CAPABILITY_ORDER))


# "What this card may do" (Advanced, per card): the four kinds of work the person can allow or forbid, and the
# capabilities each covers. Stored as prefs.json allowed_kinds {instance: [kind, ...]}; every kind is allowed by default.
KIND_GROUPS: dict[str, tuple[str, ...]] = {
    "pictures": ("concept", "turnaround", "material"),
    "model": ("mesh",),
    "rigging": ("rig",),
    "review": (JUDGE,),
}
KIND_PLAIN = {"pictures": "pictures", "model": "3D models", "rigging": "rigging", "review": "reviews"}


def kind_group(capability: str) -> str | None:
    """The allowed-kinds group a capability belongs to (concept -> pictures), or None for one outside them."""
    for group, caps in KIND_GROUPS.items():
        if capability in caps:
            return group
    return None


def kind_allowed(instance: str, capability: str, allowed_kinds: dict[str, Any] | None) -> bool:
    """Whether the person allows `instance` to do work needing `capability`. No table, or no entry for the
    instance, means everything is allowed."""
    if not allowed_kinds or instance not in allowed_kinds:
        return True
    group = kind_group(capability)
    return group is None or group in set(allowed_kinds[instance])


def possible_kinds(instance: str, settings: Settings | None = None) -> list[str]:
    """The kinds the config lets this card do at all (some capability of the group is in instance_capabilities)."""
    caps = set(capabilities(instance, settings))
    return [g for g, members in KIND_GROUPS.items() if caps & set(members)]


def effective_capabilities(instance: str, settings: Settings | None = None, allowed_kinds: dict[str, Any] | None = None) -> list[str]:
    """instance_capabilities intersected with the kinds the person allows on this card."""
    return [c for c in capabilities(instance, settings) if kind_allowed(instance, c, allowed_kinds)]


def route(stage: str, category: str, settings: Settings | None = None, disabled: Any = (),
          allowed_kinds: dict[str, Any] | None = None) -> str | None:
    """Where a job for `stage` (or "judge") goes while the cards in `disabled` are turned off and each card may do
    only its `allowed_kinds`: the preferred instance unless it is off or not allowed that kind of work, else the
    first card that is on, allowed, and can run it (instance_capabilities), else None (nothing can take it now).
    Host stages always run on the host."""
    settings = settings or get_settings()
    preferred = preferred_instance(stage, category, settings)
    if preferred == HOST:
        return HOST
    off = set(disabled or ())
    need = capability_for(stage, category)
    if preferred not in off and kind_allowed(preferred, need, allowed_kinds):
        return preferred
    for name in settings.instances:
        if (name != preferred and name not in off and kind_allowed(name, need, allowed_kinds)
                and need in capabilities(name, settings)):
            return name
    return None


def kind_capability(kind: str) -> tuple[str, str]:
    """(routing stage, capability) of an existing job's kind ("<stage>.<op>" or "material.<stage>.<op>")."""
    stage, op, is_material = parse_kind(kind)
    category = "material" if is_material else ""
    rstage = JUDGE if op == "judge" else stage
    return rstage, capability_for(rstage, category)


def route_kind(kind: str, settings: Settings | None = None, disabled: Any = (),
               allowed_kinds: dict[str, Any] | None = None) -> str | None:
    """route() for an existing job, from its kind."""
    stage, op, is_material = parse_kind(kind)
    return route(JUDGE if op == "judge" else stage, "material" if is_material else "", settings, disabled, allowed_kinds)


def gpu_name(instance: str, settings: Settings | None = None) -> str:
    """The card's name for messages ("RTX 3080"), from the instance's gpu in the config."""
    settings = settings or get_settings()
    inst = settings.instances.get(instance)
    return (inst.gpu if inst is not None and inst.gpu else "") or f"graphics card {instance}"


def waiting_message(instance: str, settings: Settings | None = None) -> str:
    return f"Waiting for {gpu_name(instance, settings)} to be turned back on"


def not_allowed_message(capability: str) -> str:
    group = kind_group(capability)
    return f"Waiting for a graphics card that is allowed to do {KIND_PLAIN.get(group or '', capability)}"


def moved_message(source: str, target: str, settings: Settings | None = None) -> str:
    return f"Moved to {gpu_name(target, settings)} while {gpu_name(source, settings)} is off"


def starting_message(instance: str, settings: Settings | None = None) -> str:
    return f"{STARTING_PREFIX}{gpu_name(instance, settings)} (about a minute)"


def start_failed_message(instance: str, reason: str, settings: Settings | None = None) -> str:
    return (f"{START_FAILED_PREFIX}{gpu_name(instance, settings)}: {reason}. It will try again when you press Try again "
            f"or turn the card off and on.")


def start_moved_message(source: str, target: str, settings: Settings | None = None) -> str:
    return f"Moved to {gpu_name(target, settings)}: could not start the graphics engine on {gpu_name(source, settings)}"


def owner_note(instance: str, settings: Settings | None = None) -> str:
    """The sentence for an engine the backend did not start: it is never stopped by the app."""
    settings = settings or get_settings()
    inst = settings.instances.get(instance)
    who = inst.external_owner if inst is not None and inst.external_owner else ""
    return f"Started by {who}; the app leaves it running." if who else "Started outside the app; the app leaves it running."


def kind_notes(instance: str, settings: Settings | None = None) -> dict[str, str]:
    """For each kind this card's config cannot do, a plain sentence naming where it runs, such as
    {"rigging": "Rigging only runs on the RTX 3080's instance."}"""
    settings = settings or get_settings()
    mine = set(possible_kinds(instance, settings))
    out = {}
    for group in KIND_GROUPS:
        if group in mine:
            continue
        where = [gpu_name(n, settings) for n in settings.instances if n != instance and group in possible_kinds(n, settings)]
        plain = KIND_PLAIN[group]
        if where:
            verb = "runs" if group == "rigging" else "run"
            out[group] = f"{plain[0].upper()}{plain[1:]} only {verb} on the {' or the '.join(where)}'s instance."
        else:
            out[group] = f"No graphics card here can do {plain}."
    return out


def gpu_note(instance: str, settings: Settings | None = None) -> str:
    """A plain sentence on what only this card can do, such as "Rigging only runs on this card." """
    settings = settings or get_settings()
    others = [n for n in settings.instances if n != instance]
    other_caps: set[str] = set()
    for n in others:
        other_caps.update(capabilities(n, settings))
    only = [c for c in capabilities(instance, settings) if c not in other_caps]
    if not others:
        return "This is the only graphics card, so turning it off pauses every picture and 3D step."
    if not only:
        return ("The other card can take over everything this card does." if len(others) == 1
                else "The other cards can take over everything this card does.")
    words = [CAPABILITY_PLAIN.get(c, c) for c in only]
    text = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return f"{text[0].upper()}{text[1:]} only {'runs' if len(only) == 1 else 'run'} on this card."


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


ENGINE_RETRY_KEY = "engine_retries"  # in the job payload: times the job went back to queued after an engine crash
MAX_ENGINE_RETRIES = 1
ENGINE_RETRY_MESSAGE = "The graphics engine stopped; starting it again and retrying once"


def _connection_classes() -> tuple[type[BaseException], ...]:
    import httpx

    return (httpx.NetworkError, httpx.RemoteProtocolError, httpx.TimeoutException, ConnectionError)


def is_engine_lost(err: BaseException) -> bool:
    """Whether a job failed because its ComfyUI went away (connection refused or reset, the instance stopped
    answering), looking through the exception's causes. A workflow error ComfyUI reported (ComfyError, including its
    own "timed out after" job time limit) is not, and neither is anything else."""
    from .comfy import ComfyError

    classes = _connection_classes()
    seen: set[int] = set()
    e: BaseException | None = err
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, ComfyError):
            return False
        if isinstance(e, classes):
            return True
        e = e.__cause__ or e.__context__
    return False


def engine_lost_message(instance: str, settings: Settings | None = None) -> str:
    return (f"The graphics engine on {gpu_name(instance, settings)} stopped twice while working on this step. Check that "
            f"it starts (the Health page shows it), then run the step again.")


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
    # Set when the card this job runs on is turned off: the job goes back to queued on `reroute_to` (or stays parked
    # on this instance when it is None) with `reroute_message`, instead of ending cancelled.
    rerouted: bool = False
    reroute_to: str | None = None
    reroute_message: str = ""

    @property
    def client(self) -> ComfyClient | None:
        return self.clients.get(self.instance)

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        self._progress_cb(fraction if fraction is not None else -1.0, message or "")

    def register_process(self, proc: subprocess.Popen) -> None:
        self.processes.append(proc)

    def check_cancelled(self) -> None:
        if self.cancelled or self.rerouted:
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
    # Turned off in the app (Graphics cards): picks no job. Separate from `paused` (POST /api/queue/{i}/pause).
    disabled: bool = False
    current_job_id: str | None = None
    current_ctx: JobContext | None = None
    # Called with the target instance after a running job was put back to queued because this card was turned off.
    on_rerouted: Callable[[str], None] | None = None
    # Whether the person allows this card the work a job kind needs (Advanced, "What this card may do"). A job of a
    # kind it may not do stays queued here, parked, and the worker passes over it. None: everything is allowed.
    allows: Callable[[str], bool] | None = None
    # Advanced, "Free memory after each step": ComfyClient.free() after every finished job.
    free_after_job: bool = False
    # On-demand start: awaited with the job id before a job runs; False means "not now" (the instance is not up, the
    # job was parked or moved). None: nothing to prepare (the host).
    prepare: Callable[[str], Awaitable[bool]] | None = None
    # True while this card's engine could not be started: the worker picks nothing until Try again.
    blocked: Callable[[], bool] | None = None
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

    def _allowed(self, kind: str) -> bool:
        try:
            return self.allows is None or self.allows(kind)
        except ValueError:
            return True  # a bad kind fails in _execute with its own error

    def _next_queued(self) -> str | None:
        with session_factory()() as db:
            if self.allows is None:
                job = db.scalar(select(Job).where(Job.instance == self.name, Job.status == "queued").order_by(Job.created_at, Job.id).limit(1))
                return job.id if job else None
            rows = db.execute(select(Job.id, Job.kind).where(Job.instance == self.name, Job.status == "queued")
                              .order_by(Job.created_at, Job.id).limit(500)).all()
            return next((r.id for r in rows if self._allowed(r.kind)), None)

    async def _run(self) -> None:
        log.info("worker %s started", self.name)
        while True:
            try:
                if self.paused or self.disabled or (self.blocked is not None and self.blocked()):
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
                if self.prepare is not None and not await self.prepare(job_id):
                    await self._sleep(1.0)
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
        def mark(ctx: JobContext) -> None:
            ctx.cancelled = True

        return self._stop_current(job_id, mark)

    def reroute_current(self, job_id: str, target: str | None, message: str) -> bool:
        """Stop the running job because this card was turned off, and put it back to queued on `target` (None: it
        stays parked here) instead of cancelling it. Same mechanics as cancel_current."""
        def mark(ctx: JobContext) -> None:
            ctx.rerouted = True
            ctx.reroute_to = target
            ctx.reroute_message = message

        return self._stop_current(job_id, mark)

    def _stop_current(self, job_id: str, mark: Callable[[JobContext], None]) -> bool:
        if self.current_job_id != job_id:
            return False
        ctx = self.current_ctx
        if ctx is not None:
            mark(ctx)
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
            # The job may have moved to another card, or this card been turned off, since the worker picked it.
            if job is None or job.status != "queued" or job.instance != self.name or self.disabled or not self._allowed(job.kind):
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
        rerouted = False
        engine_retry = False  # the engine went away mid-job: back to queued once, the start-on-demand path restarts it
        try:
            await stages.execute(ctx, kind, payload)
        except JobCancelled:
            if ctx.rerouted and not ctx.cancelled:
                rerouted = True
                error = "rerouted"
            else:
                cancelled = True
                error = "cancelled"
        except asyncio.CancelledError:
            if ctx.cancelled:
                cancelled = True
                error = "cancelled"
            elif ctx.rerouted:
                # The card was turned off in the app: back to queued, on the card that takes over.
                rerouted = True
                error = "rerouted"
            else:
                # Not the user: the server is shutting down. Put the job back so a restart resumes it.
                requeued = True
                error = "re-queued after restart"
        except Exception as e:
            if ctx.rerouted and not ctx.cancelled:
                # Killing its child processes or interrupting ComfyUI made the stage fail first; it still moves.
                rerouted = True
                error = "rerouted"
            elif self.name in self.settings.instances and is_engine_lost(e) and not ctx.cancelled:
                technical = f"{type(e).__name__}: {e}"
                if int(payload.get(ENGINE_RETRY_KEY) or 0) < MAX_ENGINE_RETRIES:
                    engine_retry = True
                    error = ENGINE_RETRY_MESSAGE
                    log.warning("job %s: the graphics engine on %s went away (%s); re-queued once", job_id[:8], self.name,
                                technical)
                else:
                    error = f"{engine_lost_message(self.name, self.settings)}\n{technical}"
                    log.error("job %s failed: the graphics engine on %s went away again (%s)", job_id, self.name, technical)
            else:
                error = f"{type(e).__name__}: {e}"
                log.error("job %s failed: %s\n%s", job_id, e, traceback.format_exc()[-4000:])
        finally:
            self.current_job_id = None
            self.current_ctx = None
            target = self.name
            with Session() as db:
                job = db.get(Job, job_id)
                if job is not None:
                    job.finished_at = utcnow()
                    if engine_retry:
                        job.status = "queued"
                        job.started_at = None
                        job.finished_at = None
                        job.progress = 0.0
                        job.message = ENGINE_RETRY_MESSAGE
                        job.payload = {**job.payload, ENGINE_RETRY_KEY: int(job.payload.get(ENGINE_RETRY_KEY) or 0) + 1}
                    elif requeued or rerouted:
                        job.status = "queued"
                        job.started_at = None
                        job.finished_at = None
                        job.progress = 0.0
                        job.message = error
                        if rerouted:
                            target = ctx.reroute_to or self.name
                            job.instance = target
                            job.message = ctx.reroute_message
                            log.info("job %s re-queued on %s: %s", job_id[:8], target, job.message)
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
                                if error is not None and not rerouted:
                                    m = v.metrics
                                    m["judge_error"] = error
                                    v.metrics = m
                            elif v.status == "rejected":
                                pass  # the person rejected it while it ran; the files are kept, the verdict stands
                            elif requeued or rerouted or engine_retry:
                                v.status = "queued"
                            elif error is None:
                                v.status = "done"
                            elif cancelled:
                                v.status = "rejected"
                                v.error = STOPPED_ERROR
                            else:
                                v.status = "error"
                                v.error = error
                            db.commit()
                            _publish_version(v)
        if rerouted and self.on_rerouted is not None:
            try:
                self.on_rerouted(target)
            except Exception:  # noqa: BLE001
                log.exception("worker %s: after re-routing job %s", self.name, job_id)
        if requeued:
            raise asyncio.CancelledError()
        if engine_retry:
            # The next pick asks the engine afresh (prepare -> Supervisor.ensure_running starts it when it is down).
            client = self.clients.get(self.name)
            if client is not None and hasattr(client, "forget_cache"):
                client.forget_cache()
            return
        if self.free_after_job and not rerouted:
            client = self.clients.get(self.name)
            if client is not None:
                log.info("worker %s: freeing the card's memory after job %s", self.name, job_id[:8])
                await client.free()  # never raises; the card may be down


_active: "JobQueue | None" = None


def active_queue() -> "JobQueue | None":
    """The running queue (set by JobQueue.start, cleared by stop), so the API and autopilot share one."""
    return _active


class JobQueue:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.workers: dict[str, InstanceWorker] = {}
        self.clients: dict[str, ComfyClient] = {}
        # Graphics cards turned off in the app (prefs.json disabled_instances). Never contains the host.
        self.disabled: set[str] = set()
        # Per card, the kinds of work the person allows (prefs.json allowed_kinds; every kind by default).
        self.allowed_kinds: dict[str, set[str]] = {n: set(KIND_GROUPS) for n in self.settings.instances}
        # Per card, free its memory after every finished job (prefs.json free_after_job; off by default).
        self.free_after_job: dict[str, bool] = {n: False for n in self.settings.instances}
        # Per card, minutes without work before an engine the app started is shut down (prefs.json; 0 = never).
        self.stop_when_idle_minutes: dict[str, int] = {n: 0 for n in self.settings.instances}
        self.supervisor: Supervisor | None = None
        self._idle_task: asyncio.Task | None = None
        self._locate_task: asyncio.Task | None = None

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
        self.supervisor = Supervisor(self.settings, self.clients, on_change=lambda _n: self.publish_gpus())
        # Cards turned off (and kinds of work forbidden) before the restart stay so, applied before any worker can
        # pick a job; queued jobs parked on them move now where another card can take them.
        self._load_prefs()
        for name in list(self.settings.instances) + [HOST]:
            gpu = name in self.settings.instances
            self.workers[name] = InstanceWorker(
                name=name, settings=self.settings, clients=self.clients, disabled=name in self.disabled,
                on_rerouted=self._after_reroute, allows=(lambda kind, n=name: self.kind_allowed_here(n, kind)) if gpu else None,
                free_after_job=self.free_after_job.get(name, False),
                prepare=(lambda job_id, n=name: self._prepare(n, job_id)) if gpu else None,
                blocked=(lambda n=name: self._start_failed(n)) if gpu else None)
        if self.disabled or any(k != set(KIND_GROUPS) for k in self.allowed_kinds.values()):
            log.info("graphics cards turned off in the app: %s; allowed kinds: %s", sorted(self.disabled),
                     {n: sorted(k) for n, k in self.allowed_kinds.items()})
        self._rebalance()
        for w in self.workers.values():
            w.start()
        self._idle_task = asyncio.create_task(self._idle_loop(), name="vgac-engine-idle")
        if any(i.discover is not None for i in self.settings.instances.values()):
            self._locate_task = asyncio.create_task(self._locate_all(), name="vgac-engine-locate")
        _active = self

    async def _locate_all(self) -> None:
        """At start: find each card that has `discover` (its ComfyUI may be on another port), read-only."""
        sup = self.supervisor
        if sup is None:
            return
        for name, inst in self.settings.instances.items():
            if inst.discover is None or name in self.disabled:
                continue
            try:
                await sup.locate(name, force=True)
            except Exception:  # noqa: BLE001
                log.exception("graphics card %s: could not look for its engine", name)
        self.publish_gpus()

    async def stop(self) -> None:
        global _active
        if self._locate_task is not None and not self._locate_task.done():
            self._locate_task.cancel()
            try:
                await self._locate_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._locate_task = None
        if self._idle_task is not None:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
            self._idle_task = None
        for w in self.workers.values():
            await w.stop()
        if self.supervisor is not None:
            await self.supervisor.close()
        self.workers.clear()
        for c in self.clients.values():
            await c.aclose()
        if _active is self:
            _active = None

    def instances(self) -> list[str]:
        return list(self.settings.instances) + [HOST]

    def place(self, stage: str, category: str) -> tuple[str, str | None]:
        """Where a new job for `stage` (or "judge") goes now: (instance, None) from route(), or, when no card that is
        on can run it, (the preferred instance, the waiting message): the job waits there until that card is back."""
        target = route(stage, category, self.settings, self.unavailable(), self.allowed_kinds)
        if target is not None:
            return target, None
        preferred = preferred_instance(stage, category, self.settings)
        return preferred, self._park_message(preferred, capability_for(stage, category))

    def _park_message(self, instance: str, capability: str) -> str:
        if instance in self.disabled:
            return waiting_message(instance, self.settings)
        if self._start_failed(instance):
            return start_failed_message(instance, self.supervisor.last_error(instance), self.settings)  # type: ignore[union-attr]
        return not_allowed_message(capability)

    def _start_failed(self, instance: str) -> bool:
        return self.supervisor is not None and self.supervisor.state(instance) == "failed"

    def unavailable(self) -> set[str]:
        """Cards no job may be routed to now: turned off, or their engine could not be started."""
        return set(self.disabled) | (self.supervisor.failed() if self.supervisor is not None else set())

    def submit(self, instance: str, kind: str, asset_id: str | None, version_id: str | None, payload: dict[str, Any],
               message: str | None = None) -> Job:
        if instance not in self.instances():
            raise ValueError(f"unknown instance {instance!r}; known: {self.instances()}")
        parse_kind(kind)
        with session_factory()() as db:
            job = Job(instance=instance, kind=kind, asset_id=asset_id, version_id=version_id, status="queued",
                      payload_json=json.dumps(payload or {}), message=message or "")
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
                if job.version_id and parse_kind(job.kind)[1] != "judge":
                    v = db.get(Version, job.version_id)
                    if v is not None and v.status in ("queued", "running"):
                        v.status = "rejected"
                        v.error = STOPPED_ERROR
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

    # ---- Stop and Resume (the buttons on every step) ----

    def stop_asset(self, asset_id: str, stage: str | None = None) -> list[str]:
        """Stop every queued or running job of the asset now (only one stage's when `stage` is given). Returns the
        ids of the versions that were stopped; a running one becomes "stopped" a moment later, when its job unwinds."""
        with session_factory()() as db:
            jobs = db.scalars(select(Job).where(Job.asset_id == asset_id, Job.status.in_(("queued", "running")))).all()
            picked: list[tuple[str, str | None]] = []
            for j in jobs:
                v = db.get(Version, j.version_id) if j.version_id else None
                if stage and (v is None or v.stage != stage):
                    continue
                picked.append((j.id, v.id if v is not None and parse_kind(j.kind)[1] != "judge" else None))
        for job_id, _ in picked:
            self.cancel(job_id)
        return [vid for _, vid in picked if vid]

    @staticmethod
    def stopped_versions(db: Any, asset_id: str, stage: str) -> list[Version]:
        """The versions a Resume would bring back: the stopped ones at the end of the stage's list, so a stop that
        was followed by a fresh run is history, not something to resume."""
        vs = db.scalars(select(Version).where(Version.asset_id == asset_id, Version.stage == stage)
                        .order_by(Version.created_at, Version.id)).all()
        out: list[Version] = []
        for v in reversed(vs):
            if not is_stopped(v):
                break
            out.append(v)
        return list(reversed(out))

    def resume_asset(self, asset_id: str, stage: str | None = None) -> list[dict[str, Any]]:
        """Put the stopped versions back in the queue: the same job, the same settings, from the start of the step.
        Returns the versions as dicts. Routed like a new job, so a card turned off since then is respected."""
        from .reference import STAGE_ORDER  # late: reference loads the data files

        resumed: list[dict[str, Any]] = []
        woken: set[str] = set()
        with session_factory()() as db:
            a = db.get(Asset, asset_id)
            if a is None:
                return []
            for s in ([stage] if stage else list(STAGE_ORDER)):
                for v in self.stopped_versions(db, asset_id, s):
                    job = db.get(Job, v.job_id) if v.job_id else None
                    if job is None or job.status != "cancelled" or parse_kind(job.kind)[1] == "judge":
                        continue
                    instance, message = self.place(v.stage, a.category)
                    # The same settings includes the random seed the stopped run had already picked.
                    seed = (v.params or {}).get("seed")
                    payload = dict(job.payload or {})
                    if seed is not None and isinstance(payload.get("params"), dict) and payload["params"].get("seed") is None:
                        payload["params"] = {**payload["params"], "seed": seed}
                        job.payload = payload
                    job.status = "queued"
                    job.instance = instance
                    job.error = None
                    job.message = message or "back in the queue after a stop"
                    job.progress = 0.0
                    job.started_at = None
                    job.finished_at = None
                    v.status = "queued"
                    v.error = None
                    db.commit()
                    _publish_job(job)
                    _publish_version(v)
                    resumed.append(version_to_dict(v))
                    woken.add(instance)
        for instance in woken:
            self.wake(instance)
        return resumed

    def dismiss_stopped(self, asset_id: str, stage: str) -> int:
        """Set the stopped versions of a stage aside: they stay in the history but are no longer offered for Resume."""
        with session_factory()() as db:
            vs = self.stopped_versions(db, asset_id, stage)
            for v in vs:
                v.error = SET_ASIDE_ERROR
            db.commit()
            for v in vs:
                _publish_version(v)
            return len(vs)

    def set_paused(self, instance: str, paused: bool) -> None:
        w = self.workers[instance]
        w.paused = paused
        w.wake.set()

    # --- graphics cards on and off ------------------------------------------------------------------------------

    def is_enabled(self, instance: str) -> bool:
        return instance not in self.disabled

    def kind_allowed_here(self, instance: str, kind: str) -> bool:
        """Whether a job of `kind` may run on `instance` now, as far as the person's allowed kinds go."""
        return kind_allowed(instance, kind_capability(kind)[1], self.allowed_kinds)

    def _runnable_here(self, instance: str, kind: str) -> bool:
        if instance == HOST or instance not in self.settings.instances:
            return True
        return instance not in self.disabled and not self._start_failed(instance) and self.kind_allowed_here(instance, kind)

    def set_enabled(self, instance: str, enabled: bool) -> None:
        """Turn a graphics card on or off in the app. Call it on the queue's event loop (the API endpoint is async).

        Off: the worker picks nothing more; every queued job on it moves to the card route() gives, or stays parked
        with the waiting message when no card that is on can run it; the running job is stopped and put back to
        queued the same way (never cancelled); then ComfyUI is asked to unload its models and free the card's memory.
        On: the worker picks again, and jobs parked on other cards that this one can run move here. Jobs that moved
        away while it was off stay where they are. Every change is saved to prefs.json and published as a "gpus"
        event."""
        self.configure(instance, enabled=enabled)

    def configure(self, instance: str, enabled: bool | None = None, allowed_kinds: list[str] | set[str] | None = None,
                  free_after_job: bool | None = None, stop_when_idle_minutes: int | None = None) -> None:
        """Change a card's settings (PUT /api/gpus/{instance}); fields left None stay. Unticking a kind moves the
        queued jobs of that kind as turning the card off does (a running job of that kind finishes first). Call it
        on the queue's loop. Raises KeyError for an unknown card, ValueError for an unknown kind."""
        if instance not in self.settings.instances:
            raise KeyError(instance)
        if allowed_kinds is not None:
            unknown = set(allowed_kinds) - set(KIND_GROUPS)
            if unknown:
                raise ValueError(f"unknown kind(s) {sorted(unknown)}; kinds: {list(KIND_GROUPS)}")
        if stop_when_idle_minutes is not None and not 0 <= int(stop_when_idle_minutes) <= 24 * 60:
            raise ValueError("stop_when_idle_minutes must be between 0 (never) and 1440 (a day)")
        w = self.workers.get(instance)
        turned_off = enabled is False and instance not in self.disabled
        if enabled is not None:
            if enabled:
                self.disabled.discard(instance)
            else:
                self.disabled.add(instance)
            if w is not None:
                w.disabled = not enabled
            if self.supervisor is not None:
                # Turning the card off and on is one of the two ways to try a failed start again.
                self.supervisor.clear_failed(instance)
                if not enabled:
                    self.supervisor.abort_start(instance)
        if stop_when_idle_minutes is not None:
            self.stop_when_idle_minutes[instance] = int(stop_when_idle_minutes)
            if self.supervisor is not None:
                self.supervisor.touch(instance)  # the countdown starts now
        if allowed_kinds is not None:
            self.allowed_kinds[instance] = set(allowed_kinds)
        if free_after_job is not None:
            self.free_after_job[instance] = bool(free_after_job)
            if w is not None:
                w.free_after_job = bool(free_after_job)
        self._save_prefs()
        self._rebalance()
        if turned_off and w is not None and w.current_job_id is not None:
            running = w.current_job_id
            with session_factory()() as db:
                job = db.get(Job, running)
                kind = job.kind if job is not None else None
            try:
                target = route_kind(kind, self.settings, self.unavailable(), self.allowed_kinds) if kind else None
            except (KeyError, ValueError):
                target = None
            message = moved_message(instance, target, self.settings) if target else waiting_message(instance, self.settings)
            w.reroute_current(running, target, message)
        if turned_off:
            self._release(instance)
        self.wake(instance)
        if enabled is not None:
            log.info("graphics card %s (%s) turned %s", instance, gpu_name(instance, self.settings), "on" if enabled else "off")
        if allowed_kinds is not None or free_after_job is not None or stop_when_idle_minutes is not None:
            log.info("graphics card %s: allowed kinds %s, free after each job %s, stop when idle after %s min", instance,
                     sorted(self.allowed_kinds[instance]), self.free_after_job[instance],
                     self.stop_when_idle_minutes[instance])
        self.publish_gpus()

    def _load_prefs(self) -> None:
        p = prefs.load(self.settings)
        self.disabled = {n for n in p["disabled_instances"] if n in self.settings.instances}
        for n, kinds in p["allowed_kinds"].items():
            if n in self.settings.instances:
                self.allowed_kinds[n] = {k for k in kinds if k in KIND_GROUPS}
        for n, flag in p["free_after_job"].items():
            if n in self.settings.instances:
                self.free_after_job[n] = bool(flag)
        for n, minutes in p["stop_when_idle_minutes"].items():
            if n in self.settings.instances:
                self.stop_when_idle_minutes[n] = int(minutes)

    def _save_prefs(self) -> None:
        try:
            prefs.update(self.settings, disabled_instances=sorted(self.disabled),
                         allowed_kinds={n: [k for k in KIND_GROUPS if k in ks] for n, ks in self.allowed_kinds.items()},
                         free_after_job=dict(self.free_after_job),
                         stop_when_idle_minutes=dict(self.stop_when_idle_minutes))
        except OSError as e:
            log.error("could not save the graphics card settings to %s: %s", prefs.prefs_path(self.settings), e)

    def _rebalance(self) -> None:
        """Every queued job on a card that may not run it now (turned off, or that kind of work not allowed there)
        moves to the card route() gives; the rest stay parked with the waiting message. Parked jobs that can run
        where they are again lose that message. Publishes the changed jobs and wakes the cards concerned."""
        changed: list[Job] = []
        wake: set[str] = set()
        gpus = sorted(self.settings.instances)
        unavailable = self.unavailable()
        with session_factory()() as db:
            rows = db.scalars(select(Job).where(Job.status == "queued", Job.instance.in_(gpus))
                              .order_by(Job.created_at, Job.id)).all()
            for j in rows:
                try:
                    runnable = self._runnable_here(j.instance, j.kind)
                    _, cap = kind_capability(j.kind)
                    target = None if runnable else route_kind(j.kind, self.settings, unavailable, self.allowed_kinds)
                except (KeyError, ValueError):
                    continue  # a bad kind fails in the worker with its own error
                if runnable:
                    if (j.message or "").startswith(("Waiting for ", START_FAILED_PREFIX)):
                        j.message = "queued"
                        changed.append(j)
                        wake.add(j.instance)
                elif target is not None and target != j.instance:
                    if j.instance in self.disabled:
                        message = moved_message(j.instance, target, self.settings)
                    elif self._start_failed(j.instance):
                        message = start_moved_message(j.instance, target, self.settings)
                    else:
                        message = (f"Moved to {gpu_name(target, self.settings)}: {gpu_name(j.instance, self.settings)} "
                                   f"may not do {KIND_PLAIN.get(kind_group(cap) or '', cap)}")
                    j.instance = target
                    j.message = message
                    changed.append(j)
                    wake.add(target)
                else:
                    message = self._park_message(j.instance, cap)
                    if j.message != message:
                        j.message = message
                        changed.append(j)
            db.commit()
            for j in changed:
                db.refresh(j)
                _publish_job(j)
        for t in wake:
            self.wake(t)

    def _after_reroute(self, target: str) -> None:
        self.wake(target)
        self.publish_gpus()

    # --- starting and stopping the graphics engines (instances.py) ------------------------------------------------

    def _set_job_message(self, job_id: str, instance: str, message: str) -> None:
        with session_factory()() as db:
            job = db.get(Job, job_id)
            if job is None or job.status != "queued" or job.instance != instance or job.message == message:
                return
            job.message = message
            db.commit()
            db.refresh(job)
            _publish_job(job)

    async def _prepare(self, instance: str, job_id: str) -> bool:
        """Before a job runs on `instance`: make sure its ComfyUI answers, starting it when it has a start script.
        True: run the job now. False: not now (the start failed and the job was moved or parked, or the card was
        turned off while it started)."""
        sup = self.supervisor
        if sup is None or instance == HOST or not sup.can_start(instance):
            return True
        if instance in self.disabled:
            return False  # a card that is off is never started
        sup.touch(instance)
        if not sup.starting(instance) and await sup.answers(instance):
            if sup.state(instance) != "running":
                sup.mark_running(instance)
            return True
        self._set_job_message(job_id, instance, starting_message(instance, self.settings))
        try:
            await sup.ensure_running(instance)
        except StartAborted:
            return False
        except StartFailed as e:
            log.warning("graphics card %s: could not start its engine: %s", instance, e)
            self._rebalance()  # moves its queued jobs to another card that can run them, or parks them with the reason
            self.publish_gpus()
            return False
        if instance in self.disabled:
            return False
        sup.touch(instance)
        return True

    def start_engine(self, instance: str) -> None:
        """The card's Start / Try again button: forget a failed start and start the engine now, in the background.
        Raises KeyError for an unknown card, ValueError (with a plain message) when it cannot be started."""
        if instance not in self.settings.instances:
            raise KeyError(instance)
        sup = self.supervisor
        gpu = gpu_name(instance, self.settings)
        if sup is None:
            raise ValueError("The app is not ready yet; try again in a moment.")
        if instance in self.disabled:
            raise ValueError(f"{gpu} is turned off. Turn it on first; the app never starts a card that is off.")
        if not sup.can_start(instance):
            raise ValueError(f"The app has no start script for the graphics engine on {gpu}, so it cannot start it.")
        sup.clear_failed(instance)
        sup.touch(instance)
        self._rebalance()  # jobs parked by the failed start may run here again
        self.wake(instance)

        async def _go() -> None:
            try:
                await sup.ensure_running(instance)
            except StartAborted:
                pass
            except StartFailed as e:
                log.warning("graphics card %s: could not start its engine: %s", instance, e)
                self._rebalance()
            self.publish_gpus()

        asyncio.ensure_future(_go())
        self.publish_gpus()

    def stop_engine(self, instance: str) -> None:
        """The card's Stop button: stop the engine the backend started. Raises KeyError for an unknown card,
        ValueError (plain message) for an engine the backend did not start or one busy with a step."""
        if instance not in self.settings.instances:
            raise KeyError(instance)
        sup = self.supervisor
        gpu = gpu_name(instance, self.settings)
        if sup is None or not sup.owned(instance):
            raise ValueError(f"The graphics engine on {gpu} was not started by the app, so the app will not stop it. "
                             f"{owner_note(instance, self.settings)}")
        w = self.workers.get(instance)
        if w is not None and w.current_job_id is not None:
            raise ValueError(f"{gpu} is busy with a step. Wait for it to finish, or turn the card off to hand the step "
                             f"to another card.")
        try:
            sup.stop(instance)
        except NotOwned:
            raise ValueError(f"The graphics engine on {gpu} was not started by the app, so the app will not stop it.")
        self.publish_gpus()

    def idle_check(self) -> list[str]:
        """Stop every engine the backend started whose card has had no running or queued job for its
        stop_when_idle_minutes (0 = never). Returns the cards stopped."""
        sup = self.supervisor
        if sup is None:
            return []
        st = self.status()
        stopped = []
        for name in self.settings.instances:
            s = st.get(name, {})
            if s.get("current_job_id") or int(s.get("queued", 0)) > 0 or sup.starting(name):
                sup.touch(name)
                continue
            minutes = self.stop_when_idle_minutes.get(name, 0)
            if minutes <= 0 or not sup.owned(name):
                continue
            if sup.idle_seconds(name) >= minutes * 60:
                log.info("graphics card %s: no work for %s minutes; stopping the engine the app started", name, minutes)
                try:
                    sup.stop(name)
                    stopped.append(name)
                except Exception:  # noqa: BLE001
                    log.exception("graphics card %s: could not stop its idle engine", name)
        return stopped

    async def refresh_engines(self) -> None:
        """Notice an engine that came up or went away on its own (one cached /system_stats per card), so the card's
        state, and the "gpus" event, follow it. An engine that comes up after a failed start clears the failure."""
        sup = self.supervisor
        if sup is None:
            return
        for name, client in self.clients.items():
            if sup.starting(name) or name in self.disabled:
                continue
            try:
                if self.settings.instances[name].discover is not None:
                    up = await sup.answers(name)  # only its own card counts; finds it on another port (locate)
                else:
                    summary = await client.cached_vram_summary(5.0)
                    up = bool(summary and summary.get("reachable"))
            except Exception:  # noqa: BLE001
                continue
            was = sup.state(name)
            if up and was != "running":
                sup.mark_running(name)
                if was == "failed":
                    self._rebalance()
                    self.wake(name)
            elif not up and was == "running":
                sup.mark_stopped(name)

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(IDLE_CHECK_SECONDS)
            try:
                await self.refresh_engines()
                self.idle_check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("engine idle check failed")

    def engine_state(self, instance: str, reachable: bool) -> str:
        """running | starting | stopped | failed | off, for GET /api/gpus."""
        if instance in self.disabled:
            return "off"
        sup = self.supervisor
        if sup is None:
            return "running" if reachable else "stopped"
        if sup.starting(instance) or sup.state(instance) == "starting":
            return "starting"
        if reachable:
            return "running"
        if sup.state(instance) == "failed":
            return "failed"
        return "stopped"

    def _release(self, instance: str) -> None:
        """Ask the card's ComfyUI to unload its models and free its memory, then stop its engine if the backend
        started it (never one it did not start), on the queue's loop. Errors are ignored."""
        client = self.clients.get(instance)
        w = self.workers.get(instance)
        loop = w._loop if w is not None else None
        if client is None or loop is None or loop.is_closed():
            return

        async def _free_and_stop() -> None:
            await client.free()
            sup = self.supervisor
            if sup is not None and instance in self.disabled and sup.owned(instance):
                log.info("graphics card %s turned off: stopping the engine the app started", instance)
                try:
                    sup.stop(instance)
                except Exception:  # noqa: BLE001
                    log.exception("graphics card %s: could not stop its engine", instance)

        def _do() -> None:
            asyncio.ensure_future(_free_and_stop())

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _do()
        else:
            loop.call_soon_threadsafe(_do)

    def gpus(self, summaries: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """One entry per graphics card for GET /api/gpus and the "gpus" event. `summaries` are vram_summary() answers
        by instance; without them the last cached answer is used (no request)."""
        st = self.status()
        current_ids = [st[n]["current_job_id"] for n in self.settings.instances if n in st and st[n]["current_job_id"]]
        current: dict[str, dict[str, Any]] = {}
        if current_ids:
            from .db import Asset

            with session_factory()() as db:
                for j in db.scalars(select(Job).where(Job.id.in_(current_ids))).all():
                    a = db.get(Asset, j.asset_id) if j.asset_id else None
                    stage, op, is_material = parse_kind(j.kind)
                    current[j.id] = {"id": j.id, "kind": j.kind, "stage": stage, "op": op, "material": is_material,
                                     "asset_id": j.asset_id, "asset_name": a.name if a is not None else None,
                                     "progress": float(j.progress or 0.0), "message": j.message or ""}
        out = []
        for name, inst in self.settings.instances.items():
            client = self.clients.get(name)
            summary = (summaries or {}).get(name) if summaries is not None else (client.last_vram_summary() if client else None)
            reachable = bool(summary and summary.get("reachable"))
            sup = self.supervisor
            # A ComfyUI that answers from another card is not this card's engine (instances.Supervisor.locate).
            setup_problem = sup.conflict(name) if sup is not None else ""
            if reachable and inst.discover is not None and (setup_problem or (sup is not None and not sup.identity_ok(name, summary))):
                reachable = False
            s = st.get(name, {})
            cur = s.get("current_job_id")
            allowed = self.allowed_kinds.get(name, set(KIND_GROUPS))
            possible = possible_kinds(name, self.settings)
            entry: dict[str, Any] = {
                "instance": name, "gpu": inst.gpu, "enabled": self.is_enabled(name),
                # The address in use: the configured one, or where the card was found (found_by_card).
                "url": sup.current_url(name) if sup is not None else inst.url,
                "found_by_card": bool(sup is not None and name in sup.found),
                "setup_problem": setup_problem,
                "reachable": reachable, "current_job_id": cur, "current_job": current.get(cur) if cur else None,
                "queued": int(s.get("queued", 0)),
                "can_run": effective_capabilities(name, self.settings, self.allowed_kinds),
                "note": gpu_note(name, self.settings),
                "allowed_kinds": [k for k in KIND_GROUPS if k in allowed],
                "possible_kinds": possible,
                "effective_kinds": [k for k in possible if k in allowed],
                "kind_notes": kind_notes(name, self.settings),
                "free_after_job": self.free_after_job.get(name, False),
            }
            state = self.engine_state(name, reachable)
            owned = bool(sup is not None and sup.owned(name))
            can_start = bool(sup is not None and sup.can_start(name))
            entry.update({
                "state": state, "owned": owned, "can_start": can_start,
                "stop_when_idle_minutes": self.stop_when_idle_minutes.get(name, 0),
                "last_error": (sup.last_error(name) if sup is not None and state == "failed" else "") or "",
            })
            if reachable and not owned and can_start:
                entry["note"] = f"{entry['note']} {owner_note(name, self.settings)}"
            if reachable:
                dev = (summary.get("devices") or [{}])[0] or {}
                total, free = dev.get("vram_total"), dev.get("vram_free")
                entry["vram_total_mb"] = round(total / MB) if isinstance(total, (int, float)) else None
                entry["vram_free_mb"] = round(free / MB) if isinstance(free, (int, float)) else None
            out.append(entry)
        return out

    def publish_gpus(self) -> None:
        try:
            bus.publish_threadsafe({"type": "gpus", "gpus": self.gpus()})
        except Exception:  # noqa: BLE001
            log.exception("could not publish the graphics card state")

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
            out[name] = {"paused": w.paused, "enabled": not w.disabled, "current_job_id": w.current_job_id, "queued": n,
                         "next_job_id": next_id}
        return out
