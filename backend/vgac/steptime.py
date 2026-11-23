"""Per-step time log (WP-91 part 1).

Each stage run records how long its main parts took in `metrics.timing`: `{"<part>": seconds, ..., "total": seconds}`,
rounded to 0.1 s, parts first. A part is one `with steptime.part(sc, "name"):` (or `await steptime.run(sc, "name", coro)`)
around work the stage already does; the same name used twice adds up. Nothing here may fail a job: every error is logged
and ignored. Seconds are wall time from `time.monotonic()`, so a part that waits for a card (a ComfyUI queue) or for the
host's one Blender at a time includes the wait, and parts drawn on two cards at once overlap (their sum can exceed the
total). `tools/timing_report.py` (backend/tools) reads the numbers back across assets.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, Awaitable, Iterator

log = logging.getLogger("vgac.steptime")

KEY = "timing"
TOTAL = "total"
_ATTR = "_steptime"


class Timer:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.parts: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, name: str, seconds: float) -> None:
        with self._lock:
            self.parts[name] = self.parts.get(name, 0.0) + max(0.0, float(seconds))

    def snapshot(self, total: bool = False) -> dict[str, float]:
        with self._lock:
            out = {k: round(v, 1) for k, v in self.parts.items()}
        if total:
            out[TOTAL] = round(time.monotonic() - self.t0, 1)
        return out


def _timer(sc: Any) -> Timer | None:
    try:
        t = getattr(sc, _ATTR, None)
        if t is None:
            t = Timer()
            setattr(sc, _ATTR, t)
        return t
    except Exception:  # noqa: BLE001  (timing never fails a job)
        log.exception("step timing could not start")
        return None


def begin(sc: Any) -> None:
    """A fresh timer for this stage run (the dispatcher calls it before the stage starts)."""
    try:
        setattr(sc, _ATTR, Timer())
    except Exception:  # noqa: BLE001
        log.exception("step timing could not start")


def _publish(sc: Any, total: bool = False) -> None:
    t = _timer(sc)
    if t is None:
        return
    try:
        sc.set_metrics(**{KEY: t.snapshot(total)})
    except Exception:  # noqa: BLE001
        log.exception("step timing could not be saved")


def add(sc: Any, name: str, seconds: float | None) -> None:
    """A part measured elsewhere (for example seconds a Blender script reported)."""
    try:
        if seconds is None:
            return
        t = _timer(sc)
        if t is not None:
            t.add(name, float(seconds))
            _publish(sc)
    except Exception:  # noqa: BLE001
        log.exception("step timing %s could not be added", name)


@contextlib.contextmanager
def part(sc: Any, name: str) -> Iterator[None]:
    """Time the body (also when it raises) as part `name`."""
    t0 = time.monotonic()
    try:
        yield
    finally:
        add(sc, name, time.monotonic() - t0)


async def run(sc: Any, name: str, coro: Awaitable[Any]) -> Any:
    """await `coro`, timed as part `name`."""
    with part(sc, name):
        return await coro


def finish(sc: Any) -> None:
    """Write the parts and the total (the dispatcher calls it when the stage returns or fails)."""
    _publish(sc, total=True)
