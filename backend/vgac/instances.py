"""Start ComfyUI instances on demand and stop the ones the backend started.

A card whose instance has an `engine` (config.EngineConfig) or a `start_script` in the settings is started when a job
needs it and nothing answers its /system_stats. ensure_running(name):

1. it answers: running, nothing to do;
2. the backend already started it and that process is alive, or a process whose command line contains
   `process_match` exists, or something listens on its port: someone is starting it, so wait for it to answer;
3. otherwise start it detached and hidden, output to runtime/logs/comfy-<name>.out.log and .err.log: with an
   `engine`, the ComfyUI command built from the settings (engine_command(): CUDA_DEVICE_ORDER=PCI_BUS_ID and
   SKINTOKEN_BLENDER_BIN set); otherwise the start script (powershell -NoProfile -ExecutionPolicy Bypass -File).
   Record that the backend owns it (the pid, also saved in <library_path>/engines.json so a backend restart still
   knows), and wait until it answers or start_timeout_seconds pass.

stop(name) only ever stops a process tree the backend started itself (taskkill /PID <pid> /T /F on Windows); for
anything else it raises NotOwned. An instance run by ComfyUI Desktop or the desktop launcher is never stopped.

Finding an instance by its card (config.DiscoverConfig): an instance with `discover` counts as answering only when
its /system_stats device name contains `gpu_match`. When the configured url does not answer, or answers from another
card, locate() tries the listed ports on the same host (at most once every PROBE_SECONDS) and points the instance's
client at the first ComfyUI on the right card for the rest of the session, never at one whose device matches another
instance's gpu_match. Never while the instance is in use (in_use(): a job running on it, the card lent, a prompt being
waited on): a slow or missing answer then means the engine is busy (loading a model), not gone, and its address
stays. An engine that really went away is noticed by the job itself (a refused connection ends its wait; the queue
requeues it once), and the search runs again once the instance is no longer in use. A ComfyUI on the instance's own port that runs on another card is a setup problem (conflict()):
the start fails at once with a plain fix instead of sending the work to the wrong card.

State per instance: running | starting | stopped | failed, owned, last_error. Every change calls `on_change` (the job
queue publishes the "gpus" event). The operating-system calls are in ProcessOps, which tests replace with a stub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

from .config import PROJECT_DIR, InstanceConfig, Settings

log = logging.getLogger("vgac.instances")

STATES = ("running", "starting", "stopped", "failed")
OWNED_FILE = "engines.json"
POLL_SECONDS = 2.0
PROBE_SECONDS = 5.0  # the other ports are tried at most this often per instance
PROBE_TIMEOUT = 2.0


def device_names(summary: dict[str, Any] | None) -> str:
    """The device names of a vram_summary() answer, joined ("cuda:0 NVIDIA GeForce RTX 3060 : cudaMallocAsync")."""
    if not summary or not summary.get("reachable"):
        return ""
    return " | ".join(str(d.get("name") or "") for d in summary.get("devices") or [] if isinstance(d, dict))


def card_matches(summary: dict[str, Any] | None, gpu_match: str) -> bool:
    return bool(gpu_match) and gpu_match.lower() in device_names(summary).lower()


def with_port(url: str, port: int) -> str:
    u = urlparse(url)
    host = u.hostname or "127.0.0.1"
    return urlunparse((u.scheme or "http", f"{host}:{port}", "", "", "", "")).rstrip("/")


def port_of(url: str) -> int | None:
    try:
        return urlparse(url).port
    except ValueError:
        return None


def owner_of(settings: Settings, devices: str, name: str) -> tuple[str, InstanceConfig] | None:
    """The other instance whose gpu_match these device names contain, if any."""
    for other, oi in settings.instances.items():
        if other != name and oi.discover is not None and oi.discover.gpu_match.lower() in devices.lower():
            return other, oi
    return None


def conflict_message(settings: Settings, name: str, url: str, devices: str) -> str:
    """Plain words for "the ComfyUI on this instance's port runs on another card", with the fix."""
    inst = settings.instances[name]
    port = port_of(url)
    where = f"port {port}" if port else url
    gpu = inst.gpu or f"card {name}"
    owner = owner_of(settings, devices, name)
    if owner is not None:
        oi = owner[1]
        who = oi.external_owner or "Another copy of ComfyUI"
        return (f"{who} is using {where} on the {oi.gpu or 'other card'}; close it or change its port so the "
                f"{gpu} can use {where}.")
    return (f"The graphics engine on {where} runs on another card ({devices or 'unknown'}), not the {gpu}; close "
            f"it or change its port so the {gpu} can use {where}.")


async def search_ports(settings: Settings, name: str, here: str,
                       probe: Callable[[str], Awaitable[dict[str, Any] | None]]) -> str | None:
    """The first address among the configured url and the `discover` ports (other than `here`) where a ComfyUI runs on
    this instance's card and not on another instance's, or None. Read-only requests, all at once."""
    inst = settings.instances[name]
    d = inst.discover
    if d is None:
        return None
    here = here.rstrip("/")
    candidates: list[str] = []
    for u in [inst.url.rstrip("/")] + [with_port(inst.url, p) for p in d.ports]:
        if u != here and u not in candidates:
            candidates.append(u)
    if not candidates:
        return None
    answers = await asyncio.gather(*(probe(u) for u in candidates))
    for u, ans in zip(candidates, answers):
        if card_matches(ans, d.gpu_match) and owner_of(settings, device_names(ans), name) is None:
            return u
    return None


async def probe_url(url: str) -> dict[str, Any] | None:
    """A vram_summary()-shaped answer from <url>/system_stats (device names only), or None when nothing answers there.
    Read-only."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as http:
            r = await http.get(url.rstrip("/") + "/system_stats")
            r.raise_for_status()
            stats = r.json()
    except Exception:  # noqa: BLE001  (refused, timeout, not ComfyUI)
        return None
    if not isinstance(stats, dict) or not isinstance(stats.get("devices"), list):
        return None
    return {"reachable": True, "devices": [{"name": d.get("name")} for d in stats["devices"] if isinstance(d, dict)]}


def engine_command(settings: Settings, name: str) -> tuple[list[str], Path, dict[str, str]]:
    """The command that starts instance `name`'s ComfyUI from its `engine` settings: (argv, working folder,
    environment variables to add). ValueError when the instance has no engine. Paths are already absolute
    (config.resolve_paths)."""
    inst = settings.instances[name]
    e = inst.engine
    if e is None:
        raise ValueError(f"instance {name} has no engine settings")
    port = e.port if e.port is not None else port_of(inst.url)
    main = e.main.replace("/", os.sep)
    argv = [str(e.python), "-s", main]
    if port is not None:
        argv += ["--port", str(port)]
    if e.listen:
        argv += ["--listen", e.listen]
    if e.cuda_device is not None:
        argv += ["--cuda-device", str(e.cuda_device)]
    input_dir = e.input_dir if e.input_dir is not None else settings.comfy_input_dir
    output_dir = e.output_dir if e.output_dir is not None else inst.output_dir
    for flag, value in (("--extra-model-paths-config", e.extra_model_paths), ("--input-directory", input_dir),
                        ("--user-directory", e.user_dir), ("--output-directory", output_dir)):
        if value is not None:
            argv += [flag, str(value)]
    argv += [str(a) for a in e.extra_args]
    env = {"CUDA_DEVICE_ORDER": "PCI_BUS_ID"}
    if settings.blender_exe is not None:
        env["SKINTOKEN_BLENDER_BIN"] = str(settings.blender_exe)
    return argv, Path(e.comfy_dir), env


class StartFailed(Exception):
    """The instance could not be started; the message is a plain reason ("it did not answer within 180 seconds")."""


class StartAborted(StartFailed):
    """The start was given up because the card was turned off or the engine was stopped."""


class NotOwned(Exception):
    """stop() for an instance the backend did not start."""


class ProcessOps:
    """The operating-system calls the supervisor makes. Tests pass a stub with the same methods."""

    def __init__(self) -> None:
        self._popen: dict[int, subprocess.Popen] = {}

    def spawn(self, script: Path, cwd: Path, out_log: Path, err_log: Path) -> int:
        """Run a PowerShell script detached and hidden, output to the two logs. Returns the pid."""
        out_log.parent.mkdir(parents=True, exist_ok=True)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        with open(out_log, "ab") as out, open(err_log, "ab") as err:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            out.write(f"\n[{stamp}] VGAC backend: starting {script}\n".encode())
            out.flush()
            proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                 "-File", str(script)],
                cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=flags, close_fds=True)
        self._popen[proc.pid] = proc  # kept so Python does not warn about a child it forgot
        return proc.pid

    def launch(self, argv: list[str], cwd: Path, env: dict[str, str], out_log: Path, err_log: Path) -> int:
        """Run a command detached and hidden with `env` added to this process's environment, output to the two logs.
        Returns the pid."""
        out_log.parent.mkdir(parents=True, exist_ok=True)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        with open(out_log, "ab") as out, open(err_log, "ab") as err:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            shown = " ".join(f"{k}={v}" for k, v in env.items())
            out.write(f"\n[{stamp}] VGAC backend: starting {subprocess.list2cmdline(argv)} in {cwd} ({shown})\n".encode())
            out.flush()
            proc = subprocess.Popen(argv, cwd=str(cwd), env={**os.environ, **env}, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=err, creationflags=flags, close_fds=True)
        self._popen[proc.pid] = proc  # kept so Python does not warn about a child it forgot
        return proc.pid

    def alive(self, pid: int) -> bool:
        import psutil

        try:
            p = psutil.Process(pid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    def create_time(self, pid: int) -> float | None:
        import psutil

        try:
            return psutil.Process(pid).create_time()
        except psutil.Error:
            return None

    def kill_tree(self, pid: int) -> None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        else:
            import psutil

            try:
                parent = psutil.Process(pid)
                for c in parent.children(recursive=True):
                    c.kill()
                parent.kill()
            except psutil.Error:
                pass
        self._popen.pop(pid, None)

    def find(self, match: str) -> list[int]:
        """Pids of processes whose command line contains `match` (case-insensitive)."""
        import psutil

        needle = match.lower()
        out = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
            except psutil.Error:
                continue
            if needle in cmd.lower() and p.info["pid"] != os.getpid():
                out.append(p.info["pid"])
        return out

    def port_open(self, url: str) -> bool:
        u = urlparse(url)
        host, port = u.hostname or "127.0.0.1", u.port or (443 if u.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False


@dataclass
class EngineState:
    state: str = "stopped"
    owned_pid: int | None = None
    owned_created: float | None = None
    last_error: str = ""
    abort: bool = False
    # Monotonic time of the last moment the card had work (or was started); the idle stop counts from here.
    last_busy: float = 0.0


class Supervisor:
    def __init__(self, settings: Settings, clients: dict[str, Any], ops: ProcessOps | None = None,
                 on_change: Callable[[str], None] | None = None, poll_seconds: float | None = None,
                 clock: Callable[[], float] = time.monotonic, project_dir: Path = PROJECT_DIR,
                 probe: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None) -> None:
        self.settings = settings
        self.clients = clients
        self.ops = ops or ProcessOps()
        self.on_change = on_change
        self.poll_seconds = POLL_SECONDS if poll_seconds is None else poll_seconds
        self.clock = clock
        self.project_dir = project_dir
        self.engines: dict[str, EngineState] = {n: EngineState(last_busy=clock()) for n in settings.instances}
        self._tasks: dict[str, asyncio.Task] = {}
        self.probe = probe or probe_url
        # Instances found at another address than the configured one (url by name), and the setup problem of an
        # instance whose port answers from another card (plain message by name).
        self.found: dict[str, str] = {}
        self.conflicts: dict[str, str] = {}
        self._probed_at: dict[str, float] = {}
        # Set by the job queue: whether a card has a job running or is lent (jobs.JobQueue.card_in_use).
        self.busy: Callable[[str], bool] | None = None
        self._busy_noted: set[str] = set()
        self._load_owned()

    # --- facts -------------------------------------------------------------------------------------------------

    def can_start(self, name: str) -> bool:
        inst = self.settings.instances.get(name)
        return inst is not None and (inst.engine is not None or inst.start_script is not None)

    def script_path(self, name: str) -> Path | None:
        inst = self.settings.instances.get(name)
        if inst is None or inst.start_script is None:
            return None
        p = Path(inst.start_script)
        return p if p.is_absolute() else self.project_dir / p

    def log_paths(self, name: str) -> tuple[Path, Path]:
        logs = self.project_dir / "runtime" / "logs"
        return logs / f"comfy-{name}.out.log", logs / f"comfy-{name}.err.log"

    def state(self, name: str) -> str:
        return self.engines[name].state if name in self.engines else "stopped"

    def last_error(self, name: str) -> str:
        return self.engines[name].last_error if name in self.engines else ""

    def failed(self) -> set[str]:
        return {n for n, e in self.engines.items() if e.state == "failed"}

    def owned(self, name: str) -> bool:
        e = self.engines.get(name)
        if e is None or e.owned_pid is None:
            return False
        if self.ops.alive(e.owned_pid):
            return True
        self._forget_owned(name)
        return False

    def starting(self, name: str) -> bool:
        t = self._tasks.get(name)
        return t is not None and not t.done()

    async def answers(self, name: str) -> bool:
        """Whether the instance answers /system_stats now (a fresh request; the answer is cached for GET /api/gpus).
        For an instance with `discover`, only an answer from its own card counts (locate())."""
        inst = self.settings.instances.get(name)
        if inst is not None and inst.discover is not None:
            return await self.locate(name)
        client = self.clients.get(name)
        if client is None:
            return False
        try:
            summary = await client.cached_vram_summary(0)
        except Exception:  # noqa: BLE001
            return False
        return bool(summary and summary.get("reachable"))

    # --- finding an instance by its card --------------------------------------------------------------------------

    def current_url(self, name: str) -> str:
        client = self.clients.get(name)
        url = getattr(client, "base_url", None)
        return str(url).rstrip("/") if url else self.settings.instances[name].url.rstrip("/")

    def conflict(self, name: str) -> str:
        return self.conflicts.get(name, "")

    def in_use(self, name: str) -> bool:
        """Whether the instance is working for the app now: a prompt being waited on (ComfyClient.waiting), or a job
        running on its card or the card lent (the queue's `busy`). Its address is then never changed."""
        if int(getattr(self.clients.get(name), "waiting", 0) or 0) > 0:
            return True
        try:
            return bool(self.busy is not None and self.busy(name))
        except Exception:  # noqa: BLE001
            return False

    def identity_ok(self, name: str, summary: dict[str, Any] | None) -> bool:
        """Whether a vram_summary() answer comes from this instance's card (any answer, without `discover`)."""
        inst = self.settings.instances.get(name)
        if inst is None or inst.discover is None:
            return bool(summary and summary.get("reachable"))
        return card_matches(summary, inst.discover.gpu_match)

    def _switch(self, name: str, url: str) -> None:
        client = self.clients.get(name)
        if client is not None and hasattr(client, "set_base_url"):
            client.set_base_url(url)
        configured = self.settings.instances[name].url.rstrip("/")
        if url.rstrip("/") == configured:
            self.found.pop(name, None)
        else:
            self.found[name] = url.rstrip("/")
        log.info("graphics engine %s: using the ComfyUI on the %s at %s", name, self.settings.instances[name].gpu, url)

    async def locate(self, name: str, force: bool = False) -> bool:
        """True when the instance answers from its own card, at its current address or, after trying the configured
        url and the `discover` ports, at a new one (which the instance then keeps for the session). Read-only."""
        inst = self.settings.instances[name]
        d = inst.discover
        client = self.clients.get(name)
        summary: dict[str, Any] | None = None
        if client is not None:
            try:
                summary = await client.cached_vram_summary(0)
            except Exception:  # noqa: BLE001
                summary = None
        reachable = bool(summary and summary.get("reachable"))
        if d is None:
            return reachable
        if reachable and card_matches(summary, d.gpu_match):
            self.conflicts.pop(name, None)
            self._busy_noted.discard(name)
            return True
        here = self.current_url(name)
        busy = self.in_use(name)
        if busy and not reachable:
            # Working for us and slow to answer (a model loading holds the engine's server): busy, not gone.
            if name not in self._busy_noted:
                self._busy_noted.add(name)
                log.info("graphics engine %s: no answer from %s while it works for a step; taken as busy, its "
                         "address is kept", name, here)
            return True
        now = self.clock()
        if not busy and (force or now - self._probed_at.get(name, -1e18) >= PROBE_SECONDS):
            self._probed_at[name] = now
            url = await search_ports(self.settings, name, here, self.probe)
            if url is not None:
                self._switch(name, url)
                self.conflicts.pop(name, None)
                return True
        if reachable:
            msg = conflict_message(self.settings, name, here, device_names(summary))
            if self.conflicts.get(name) != msg:
                log.warning("graphics engine %s: %s", name, msg)
            self.conflicts[name] = msg
        else:
            self.conflicts.pop(name, None)
        return False

    # --- changes -----------------------------------------------------------------------------------------------

    def _set(self, name: str, state: str | None = None, error: str | None = None) -> None:
        e = self.engines[name]
        changed = False
        if state is not None and e.state != state:
            e.state = state
            changed = True
        if error is not None and e.last_error != error:
            e.last_error = error
            changed = True
        if changed:
            log.info("graphics engine %s: %s%s", name, e.state, f" ({e.last_error})" if e.last_error else "")
            if self.on_change is not None:
                try:
                    self.on_change(name)
                except Exception:  # noqa: BLE001
                    log.exception("graphics engine %s: state change callback", name)

    def mark_running(self, name: str) -> None:
        if name in self.engines and not self.starting(name):
            self._set(name, "running", "")

    def mark_stopped(self, name: str) -> None:
        """The card was found not answering (or turned off): stopped, unless a start is under way."""
        if name in self.engines and not self.starting(name):
            self._set(name, "stopped")

    def clear_failed(self, name: str) -> None:
        """Forget a failed start (Try again, or the card turned off and on), so the next job tries once more."""
        e = self.engines.get(name)
        if e is not None and e.state == "failed":
            self._set(name, "stopped", "")

    def touch(self, name: str) -> None:
        """The card has work now; the idle stop counts from here."""
        if name in self.engines:
            self.engines[name].last_busy = self.clock()

    def idle_seconds(self, name: str) -> float:
        return self.clock() - self.engines[name].last_busy

    async def ensure_running(self, name: str) -> None:
        """Make sure the instance answers, starting it if needed (see the module text). Raises StartFailed with a
        plain reason, StartAborted when the start was given up (card off, engine stopped). Two callers at once share
        one start."""
        if name not in self.settings.instances:
            raise StartFailed(f"there is no graphics card called {name!r}")
        task = self._tasks.get(name)
        if task is None or task.done():
            self.engines[name].abort = False
            task = self._tasks[name] = asyncio.ensure_future(self._start(name))
        await asyncio.shield(task)

    def abort_start(self, name: str) -> None:
        if name in self.engines and self.starting(name):
            self.engines[name].abort = True

    async def _start(self, name: str) -> None:
        e = self.engines[name]
        inst = self.settings.instances[name]
        try:
            if await self.answers(name):
                self._set(name, "running", "")
                return
            if self.conflict(name):
                raise StartFailed(self.conflict(name))
            self._set(name, "starting", "")
            spawned = False
            if self.owned(name):
                log.info("graphics engine %s: the copy the app started (pid %s) is still loading; waiting", name, e.owned_pid)
            else:
                others = self.ops.find(inst.process_match) if inst.process_match else []
                if others or self.ops.port_open(self.current_url(name)):
                    log.info("graphics engine %s: already being started outside the app (%s); waiting for it", name,
                             f"pid {others[0]}" if others else "its port is open")
                else:
                    out_log, err_log = self.log_paths(name)
                    if inst.engine is not None:
                        argv, cwd, env = engine_command(self.settings, name)
                        if not cwd.is_dir():
                            raise StartFailed(f"its ComfyUI folder {cwd} was not found (instances.{name}.engine."
                                              "comfy_dir in local/machine.json)")
                        if not Path(argv[0]).is_file():
                            raise StartFailed(f"its ComfyUI Python {argv[0]} was not found (instances.{name}.engine."
                                              "python in local/machine.json)")
                        try:
                            pid = self.ops.launch(argv, cwd, env, out_log, err_log)
                        except OSError as err:
                            raise StartFailed(f"Windows could not start its ComfyUI ({err})") from err
                        what = f"{argv[0]} in {cwd}"
                    else:
                        script = self.script_path(name)
                        if script is None:
                            raise StartFailed("no way to start it is set up (no engine settings and no start script)")
                        if not script.is_file():
                            raise StartFailed(f"the start script {script} was not found")
                        try:
                            pid = self.ops.spawn(script, script.parent, out_log, err_log)
                        except OSError as err:
                            raise StartFailed(f"Windows could not run the start script ({err})") from err
                        what = str(script)
                    e.owned_pid = pid
                    e.owned_created = self.ops.create_time(pid)
                    e.last_busy = self.clock()
                    self._save_owned()
                    spawned = True
                    log.info("graphics engine %s: started %s as pid %s (logs %s, %s)", name, what, pid, out_log, err_log)
            deadline = self.clock() + inst.start_timeout_seconds
            _, err_log = self.log_paths(name)
            while True:
                if await self.answers(name):
                    e.last_busy = self.clock()
                    self._set(name, "running", "")
                    return
                if self.conflict(name):
                    raise StartFailed(self.conflict(name))
                if e.abort:
                    raise StartAborted("the start was stopped")
                if spawned and e.owned_pid is not None and not self.ops.alive(e.owned_pid):
                    self._forget_owned(name)
                    raise StartFailed(f"it closed while starting (see {err_log.name} in runtime/logs)")
                if self.clock() >= deadline:
                    raise StartFailed(f"it did not answer within {inst.start_timeout_seconds:g} seconds "
                                      f"(see {err_log.name} in runtime/logs)")
                await asyncio.sleep(self.poll_seconds)
        except StartAborted:
            self._set(name, "stopped", "")
            raise
        except StartFailed as err:
            self._set(name, "failed", str(err))
            raise
        except asyncio.CancelledError:
            if e.state == "starting":
                self._set(name, "stopped")
            raise

    def stop(self, name: str) -> None:
        """Stop the instance's process tree, only if the backend started it. Raises NotOwned otherwise."""
        if name not in self.engines:
            raise KeyError(name)
        if not self.owned(name):
            raise NotOwned(name)
        e = self.engines[name]
        pid = e.owned_pid
        self.abort_start(name)
        log.info("graphics engine %s: stopping the copy the app started (pid %s)", name, pid)
        try:
            self.ops.kill_tree(pid)  # type: ignore[arg-type]
        finally:
            self._forget_owned(name)
            client = self.clients.get(name)
            if client is not None and hasattr(client, "forget_cache"):
                client.forget_cache()
            self._set(name, "stopped", "")

    async def close(self) -> None:
        """Give up any start under way (the server is shutting down). Started processes keep running and stay owned
        (engines.json), so the next run can still stop them."""
        for t in list(self._tasks.values()):
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._tasks.clear()

    def stop_if_owned(self, name: str) -> bool:
        try:
            self.stop(name)
            return True
        except (NotOwned, KeyError):
            self.abort_start(name)
            return False

    # --- ownership kept across a backend restart -----------------------------------------------------------------

    def _owned_path(self) -> Path:
        return self.settings.library_path / OWNED_FILE

    def _forget_owned(self, name: str) -> None:
        e = self.engines[name]
        if e.owned_pid is not None:
            e.owned_pid = None
            e.owned_created = None
            self._save_owned()

    def _save_owned(self) -> None:
        data = {n: {"pid": e.owned_pid, "created": e.owned_created} for n, e in self.engines.items() if e.owned_pid is not None}
        p = self._owned_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, p)
        except OSError as err:
            log.warning("could not save %s: %s", p, err)

    def _load_owned(self) -> None:
        """Adopt the processes a previous run of the backend started, if they are still the same processes (same pid
        and start time, so a reused pid is never taken for ours)."""
        try:
            data = json.loads(self._owned_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for name, rec in data.items():
            if name not in self.engines or not isinstance(rec, dict) or not isinstance(rec.get("pid"), int):
                continue
            pid, created = rec["pid"], rec.get("created")
            now_created = self.ops.create_time(pid)
            if (self.ops.alive(pid) and created is not None and now_created is not None
                    and abs(now_created - float(created)) < 2.0):
                self.engines[name].owned_pid = pid
                self.engines[name].owned_created = created
                log.info("graphics engine %s: adopting pid %s started by an earlier run of the app", name, pid)
        self._save_owned()
