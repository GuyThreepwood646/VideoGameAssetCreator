"""A graphics card's memory as the driver sees it (nvidia-smi), for the lending guard and the panels.

Why not the engine's figure: on ComfyUI 0.45 with comfy-aimdo (DynamicVRAM) the `vram_free` an engine reports in
/system_stats is that process's own view, the card's size minus what that one process holds. Another program drawing
on the same card does not move it (measured 2026-11-13 21:38: the 3060 at 11.2 of 12 GB used and 99 percent busy
with another ComfyUI, the app's engine on it reporting 10.89 GB free throughout), and the engine's own loaded models
are not in its torch_vram figures either. The driver's free memory counts every process.

read() runs `nvidia-smi --query-gpu` (at most every CACHE_SECONDS; blocking, so call it in a thread), last() returns
the cached answer without running anything. Both return None when nvidia-smi is not on PATH or fails: callers then use
the engine's own figure and say so (source "engine"). card_of() matches an instance to a card by name (the instance's
gpu_match or gpu), using its engine's cuda_device only to tell identical cards apart.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

GB = 1024**3
MB = 1024**2
CACHE_SECONDS = 3.0
QUERY = ["--query-gpu=index,pci.bus_id,name,memory.total,memory.used,memory.free,utilization.gpu",
         "--format=csv,noheader,nounits"]
# The engine's own share of the card (its size minus the engine's own free figure) below which unloading its models
# cannot give back anything worth having: the CUDA context and caches alone are about 1 GB.
OWN_RECLAIMABLE_MIN_GB = 1.5
# Busy outside the app: the card is this busy (percent), or other programs hold this much (GB), while the app uses
# it for nothing.
OUTSIDE_UTIL_PERCENT = 30
OUTSIDE_USED_GB = 1.5


@dataclass(frozen=True)
class Card:
    index: int
    bus_id: str
    name: str
    total_gb: float
    used_gb: float
    free_gb: float
    util: int | None


def parse(text: str) -> list[Card]:
    """Cards from nvidia-smi's CSV (MiB, percent). Lines that do not parse are skipped."""
    cards: list[Card] = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            index = int(parts[0])
            total, used, free = (float(parts[i]) * MB / GB for i in (3, 4, 5))
        except ValueError:
            continue
        try:
            util: int | None = int(parts[6])
        except ValueError:
            util = None
        cards.append(Card(index, parts[1], parts[2], round(total, 2), round(used, 2), round(free, 2), util))
    return cards


def _run_nvidia_smi() -> str | None:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run([exe, *QUERY], capture_output=True, text=True, timeout=5, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


# Replaced in tests (conftest sets it to a function returning None: no machine figures leak into tests).
runner: Callable[[], str | None] = _run_nvidia_smi
_lock = threading.Lock()
_cache: tuple[float, list[Card] | None] | None = None


def read(max_age: float = CACHE_SECONDS) -> list[Card] | None:
    """The cards now (cached for `max_age` seconds; 0 asks again). None: nvidia-smi missing or failed. Blocking."""
    global _cache
    with _lock:
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < max_age:
            return _cache[1]
        text = runner()
        cards = parse(text) if text is not None else None
        _cache = (time.monotonic(), cards or None)
        return _cache[1]


def last() -> list[Card] | None:
    """The last answer read() got, however old, without running anything."""
    return _cache[1] if _cache is not None else None


def forget() -> None:
    global _cache
    with _lock:
        _cache = None


def _engine_device_name(summary: dict[str, Any] | None) -> str:
    devs = (summary or {}).get("devices") or []
    name = str((devs[0] or {}).get("name") or "") if devs else ""
    # "cuda:0 NVIDIA GeForce RTX 3060 : cudaMallocAsync" -> "NVIDIA GeForce RTX 3060"
    if name.startswith("cuda:"):
        name = name.split(" ", 1)[1] if " " in name else ""
    return name.split(" : ")[0].strip()


def card_of(settings: Any, name: str, summary: dict[str, Any] | None, cards: list[Card] | None) -> Card | None:
    """The card instance `name` draws on, or None when it cannot be told for certain (no cards, no match, or several
    identical cards and no cuda_device to choose by)."""
    if not cards:
        return None
    inst = settings.instances.get(name)
    if inst is None:
        return None
    keys = [k for k in (getattr(getattr(inst, "discover", None), "gpu_match", None), _engine_device_name(summary),
                        getattr(inst, "gpu", None)) if k]
    for key in keys:
        hits = [c for c in cards if key.lower() in c.name.lower()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            dev = getattr(getattr(inst, "engine", None), "cuda_device", None)
            by_index = [c for c in hits if c.index == dev]
            return by_index[0] if len(by_index) == 1 else None
    if len(cards) == 1 and len(settings.instances) == 1:
        return cards[0]   # one card, one instance, names that do not match ("GPU 0"): it can only be this one
    return None


def engine_free_gb(summary: dict[str, Any] | None) -> float | None:
    devs = (summary or {}).get("devices") or []
    free = (devs[0] or {}).get("vram_free") if devs else None
    return free / GB if isinstance(free, (int, float)) else None


def engine_total_gb(summary: dict[str, Any] | None) -> float | None:
    devs = (summary or {}).get("devices") or []
    total = (devs[0] or {}).get("vram_total") if devs else None
    return total / GB if isinstance(total, (int, float)) else None


def torch_held_gb(summary: dict[str, Any] | None) -> float:
    devs = (summary or {}).get("devices") or []
    d = (devs[0] or {}) if devs else {}
    t, f = d.get("torch_vram_total"), d.get("torch_vram_free")
    return max(0.0, (t - f) / GB) if isinstance(t, (int, float)) and isinstance(f, (int, float)) else 0.0


def own_gb(summary: dict[str, Any] | None) -> float | None:
    """What the engine itself holds on its card, as far as its own figure tells: its size minus its own free. On an
    engine whose figure counts every process this is everything in use, so it only ever overstates."""
    total, free = engine_total_gb(summary), engine_free_gb(summary)
    return max(0.0, total - free) if total is not None and free is not None else None


def others_gb(summary: dict[str, Any] | None, card: Card) -> float:
    """What other programs hold on the card: the driver's used minus the engine's own share (never below 0)."""
    own = own_gb(summary)
    return max(0.0, card.used_gb - (own or 0.0))


def room(summary: dict[str, Any] | None, card: Card | None) -> tuple[float | None, str]:
    """(GB the engine could use now, "card" or "engine"). With the driver's figure: the card's free memory plus what
    torch counts as held by the engine's own models (exact where torch tracks them, 0 under DynamicVRAM). Without it:
    the engine's own free figure plus the same, as before (source "engine")."""
    if card is not None:
        return card.free_gb + torch_held_gb(summary), "card"
    free = engine_free_gb(summary)
    if free is None:
        return None, "engine"
    return free + torch_held_gb(summary), "engine"


def outside_use(summary: dict[str, Any] | None, card: Card | None, app_using: bool) -> bool:
    """Whether something other than the app is using the card now (only said when the app itself is not)."""
    if card is None or app_using:
        return False
    return (card.util or 0) >= OUTSIDE_UTIL_PERCENT or others_gb(summary, card) >= OUTSIDE_USED_GB


def describe(card: Card | None, summary: dict[str, Any] | None) -> dict[str, Any]:
    """The figures for the API: the driver's when known, else the engine's own, labelled."""
    if card is not None:
        return {"source": "card", "total_gb": card.total_gb, "used_gb": card.used_gb, "free_gb": card.free_gb,
                "util": card.util, "others_gb": round(others_gb(summary, card), 2)}
    total, free = engine_total_gb(summary), engine_free_gb(summary)
    return {"source": "engine", "total_gb": round(total, 2) if total is not None else None,
            "used_gb": round(total - free, 2) if total is not None and free is not None else None,
            "free_gb": round(free, 2) if free is not None else None, "util": None, "others_gb": None}
