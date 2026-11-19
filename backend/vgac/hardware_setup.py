"""The hardware plan (setup card, phase 6): read the graphics cards and the memory, propose how to use them.

Three parts, kept apart so the middle one can be tested with made-up machines:

1. Reading (`read_hardware`): `nvidia-smi` for the NVIDIA cards (name, memory, place on the board) and the system
   memory. Read-only and quick; nothing is started and no card is used. The card numbers are the ones `nvidia-smi`
   shows, which is the order of the board slots; the engines the backend starts run with CUDA_DEVICE_ORDER=PCI_BUS_ID
   (instances.py), so CUDA numbers the cards the same way.
2. Proposing (`propose`): pure. A reading goes in, a plan comes out: which cards run an engine, which engine takes the
   3D step, which engine takes each step, the `min_free_ram_gb` floor, and plain sentences for the cases that will
   not work well. The numbers are judgements from the owner's machine, not measurements (docs/INSTALLER-PLAN.md B6).
3. Writing (`accept`): the person's accept writes the plan through config.update_machine, after checking the result
   as the app itself would at start. Proposing never writes. The settings are recomputed from a fresh reading when
   the person accepts; the page's copy of the plan is never trusted.

GET  /api/hardware          the reading, the proposal and what is saved now (`use` and `three_d` choose cards).
POST /api/hardware/accept   {use, three_d, min_free_ram_gb}: write the proposal for those choices.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, setup_settings
from .config import ConfigError, Settings

log = logging.getLogger("vgac.hardware_setup")

router = APIRouter(prefix="/api/hardware", tags=["setup"])
_lock = threading.Lock()

# Judgements, not measurements (INSTALLER-PLAN B6): below this a card is not offered; below TESTED_GB the app was not
# tried; under LOW_RAM_GB the big steps are slow and may fail; under VERY_LOW_RAM_GB they probably will.
MIN_CARD_GB = 8.0
TESTED_GB = 10.0
LOW_RAM_GB = 32.0
VERY_LOW_RAM_GB = 16.0
# nvidia-smi reports an "8 GB" card as 8192 or a little less once the driver takes its share.
CARD_TOLERANCE_GB = 0.5

# How the stages go on two engines: `a` the card with more memory (3D), `b` the other one. The same as the defaults of
# backend/config.yaml and config.py, so the owner's machine proposes what it already has.
TWO_STAGES = {"concept": "b", "turnaround": "b", "mesh": "a", "prep": "host", "rig": "b", "export": "host"}
TWO_MATERIAL = {"concept": "b", "turnaround": "b", "mesh": "b", "prep": "host", "rig": "host", "export": "host"}
TWO_CAPS = {"a": ["concept", "turnaround", "mesh", "material", "judge"],
            "b": ["concept", "turnaround", "mesh", "rig", "judge", "material"]}
ONE_STAGES = {"concept": "a", "turnaround": "a", "mesh": "a", "prep": "host", "rig": "a", "export": "host"}
ONE_MATERIAL = {"concept": "a", "turnaround": "a", "mesh": "a", "prep": "host", "rig": "host", "export": "host"}
ONE_CAPS = {"a": ["concept", "turnaround", "mesh", "rig", "judge", "material"]}


# --- reading -------------------------------------------------------------------------------------------------------


@dataclass
class Card:
    index: int  # as nvidia-smi numbers it (and CUDA, with CUDA_DEVICE_ORDER=PCI_BUS_ID)
    name: str  # as the driver says: "NVIDIA GeForce RTX 3060"
    memory_mb: int
    bus: str = ""

    @property
    def gb(self) -> float:
        return self.memory_mb / 1024

    def to_json(self) -> dict[str, Any]:
        return {"index": self.index, "name": self.name, "short": short_name(self.name), "memory_gb": round(self.gb, 1),
                "bus": self.bus}


@dataclass
class Hardware:
    cards: list[Card] = field(default_factory=list)
    ram_total_gb: float = 0.0
    ram_free_gb: float = 0.0
    # Cards of other makers (AMD, Intel), by name, so the message can say what this computer has.
    other_cards: list[str] = field(default_factory=list)
    # Why nvidia-smi could not be read, in plain words ("" when it could).
    smi_problem: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"cards": [c.to_json() for c in self.cards], "ram_total_gb": round(self.ram_total_gb, 1),
                "ram_free_gb": round(self.ram_free_gb, 1), "other_cards": self.other_cards, "smi_problem": self.smi_problem}


def short_name(name: str) -> str:
    """'NVIDIA GeForce RTX 3060' -> 'RTX 3060' (what the settings call a card)."""
    n = re.sub(r"^(NVIDIA\s+)?(GeForce\s+)?", "", name.strip(), flags=re.I)
    return n or name.strip()


def match_word(name: str) -> str:
    """The part of a card's name that tells it apart from the others ('RTX 3060' -> '3060')."""
    nums = re.findall(r"\d{3,5}", short_name(name))
    return nums[-1] if nums else short_name(name)


def parse_smi(text: str) -> list[Card]:
    """Lines of `nvidia-smi --query-gpu=index,name,memory.total,pci.bus_id --format=csv,noheader`. Ordered by place on
    the board, which is the order CUDA uses with CUDA_DEVICE_ORDER=PCI_BUS_ID. A line that cannot be read is skipped."""
    cards: list[Card] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        m = re.match(r"(\d+)", parts[2])
        if not parts[0].isdigit() or not m:
            continue
        cards.append(Card(index=int(parts[0]), name=parts[1], memory_mb=int(m.group(1)), bus=parts[3] if len(parts) > 3 else ""))
    cards.sort(key=lambda c: (c.bus.lower(), c.index))
    return [Card(index=i, name=c.name, memory_mb=c.memory_mb, bus=c.bus) for i, c in enumerate(cards)]


def _run(cmd: list[str], timeout: float) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise OSError((out.stderr or out.stdout or "").strip() or f"exit {out.returncode}")
    return out.stdout


Runner = Callable[[list[str], float], str]
MemoryReader = Callable[[], tuple[float, float]]


def read_memory() -> tuple[float, float]:
    """(total, free) physical memory in GB."""
    try:
        import psutil

        m = psutil.virtual_memory()
        return m.total / 1024 ** 3, m.available / 1024 ** 3
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong), ("pa", ctypes.c_ulonglong),
                        ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("ve", ctypes.c_ulonglong)]

        s = Status()
        s.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))  # type: ignore[attr-defined]
        return s.total / 1024 ** 3, s.avail / 1024 ** 3
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def read_hardware(runner: Runner | None = None, memory: MemoryReader | None = None) -> Hardware:
    """Look at this computer. `runner` runs a command and returns its text (tests give a fake); `memory` returns the
    memory in GB. Never raises: what cannot be read is empty and `smi_problem` says why."""
    run = runner or _run
    hw = Hardware()
    try:
        hw.cards = parse_smi(run(["nvidia-smi", "--query-gpu=index,name,memory.total,pci.bus_id",
                                  "--format=csv,noheader,nounits"], 15))
    except FileNotFoundError:
        hw.smi_problem = "The NVIDIA tools (nvidia-smi) were not found, which usually means there is no NVIDIA driver."
    except subprocess.TimeoutExpired:
        hw.smi_problem = "The NVIDIA tools did not answer in time."
    except OSError as err:
        hw.smi_problem = f"The NVIDIA tools could not be read: {err}"
    try:
        hw.ram_total_gb, hw.ram_free_gb = (memory or read_memory)()
    except Exception:  # noqa: BLE001
        log.exception("reading memory")
    if not hw.cards:
        try:
            names = run(["powershell", "-NoProfile", "-Command",
                         "(Get-CimInstance Win32_VideoController).Name -join \"`n\""], 15)
            hw.other_cards = [n.strip() for n in names.splitlines() if n.strip()]
        except Exception:  # noqa: BLE001
            pass
    return hw


# --- proposing (pure) ----------------------------------------------------------------------------------------------


def _gb(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}"


def usable(card: Card) -> bool:
    return card.gb >= MIN_CARD_GB - CARD_TOLERANCE_GB


def ram_floor(total_gb: float) -> float:
    """What must stay free before a job starts: a quarter of the memory, at least 8 GB, never more than half of it."""
    if total_gb <= 0:
        return 24.0  # unknown: the app's own default
    return float(min(max(8, round(total_gb / 4)), max(1, int(total_gb / 2))))


@dataclass
class Plan:
    status: str  # ok, warn, cannot
    engines: list[dict[str, Any]]  # [{name, card, gpu, role, steps}]
    min_free_ram_gb: float
    messages: list[dict[str, str]]  # [{level: info|warn|bad, text}]
    explain: list[str]
    patch: dict[str, Any]  # laid over the "backend" section of the machine file
    ignored: list[int] = field(default_factory=list)  # cards the person did not choose or that are too small

    def to_json(self) -> dict[str, Any]:
        return {"status": self.status, "engines": self.engines, "min_free_ram_gb": self.min_free_ram_gb,
                "messages": self.messages, "explain": self.explain, "ignored": self.ignored}


STEP_WORDS = {"two": {"a": "the 3D model", "b": "the pictures, the side views and the rig"},
              "one": {"a": "every step"}}


def _engine_patch(name: str, card: Card, role: str, twin: bool) -> dict[str, Any]:
    p: dict[str, Any] = {"gpu": short_name(card.name), "role": role, "engine": {"cuda_device": card.index}}
    # Two cards with the same name cannot be told apart by name; then the card number alone says which is which.
    p["discover"] = None if twin else {"gpu_match": match_word(card.name)}
    return p


def propose(hw: Hardware, use: list[int] | None = None, three_d: int | None = None,
            min_free_ram_gb: float | None = None) -> Plan:
    """The plan for this machine. `use`: the card numbers the person wants an engine on (default: every usable card, the
    two largest at most); `three_d`: the card that takes the 3D step when two are used (default: the one with more
    memory); `min_free_ram_gb`: the person's own floor. Pure: reads nothing, writes nothing."""
    msgs: list[dict[str, str]] = []
    explain: list[str] = []

    def say(level: str, text: str) -> None:
        msgs.append({"level": level, "text": text})

    floor = ram_floor(hw.ram_total_gb) if min_free_ram_gb is None else float(min_free_ram_gb)
    if hw.ram_total_gb > 0:
        explain.append(f"This computer has {_gb(hw.ram_total_gb)} GB of memory. A job waits until {_gb(floor)} GB of it is free, "
                       "so the computer is never starved. The default is a quarter of the memory, at least 8 GB.")

    # --- no card at all, or none big enough -----------------------------------------------------------------------
    cards = hw.cards
    if not cards:
        have = f" This computer has {', '.join(hw.other_cards)}." if hw.other_cards else ""
        say("bad", "VGAC's picture and 3D steps need an NVIDIA graphics card with at least 8 GB of memory." + have
            + " The app can be installed to look at it, but it cannot make characters here.")
        if hw.smi_problem:
            say("info", hw.smi_problem)
        _ram_messages(hw, say)
        return Plan("cannot", [], floor, msgs, explain, {"min_free_ram_gb": floor})
    good = [c for c in cards if usable(c)]
    if not good:
        c = max(cards, key=lambda x: x.gb)
        say("bad", f"VGAC's picture and 3D steps need an NVIDIA graphics card with at least 8 GB of memory. This computer has "
            f"{short_name(c.name)} with {_gb(c.gb)} GB. The app can be installed to look at it, but it cannot make characters here.")
        _ram_messages(hw, say)
        return Plan("cannot", [], floor, msgs, explain, {"min_free_ram_gb": floor}, ignored=[x.index for x in cards])

    # --- which cards ----------------------------------------------------------------------------------------------
    by_size = sorted(good, key=lambda c: (-c.memory_mb, c.index))
    chosen = by_size[:2]
    if use is not None:
        picked = [c for c in good if c.index in use]
        if picked:
            chosen = sorted(picked, key=lambda c: (-c.memory_mb, c.index))[:2]
        else:
            say("warn", "None of the cards you chose can be used, so the plan below uses the largest one.")
            chosen = by_size[:1]
    for c in cards:
        if not usable(c):
            say("warn", f"{short_name(c.name)} has {_gb(c.gb)} GB, which is too little to use. It is left out.")
    if len(good) > 2 and use is None:
        left = ", ".join(short_name(c.name) for c in by_size[2:])
        say("info", f"VGAC uses two graphics cards at most. {left} is not used.")

    # A second card that is much smaller than the first is still a second engine; only a card below the minimum is left out.
    if len(chosen) == 2:
        first, second = chosen  # largest first
        if three_d is not None and three_d == second.index:
            first, second = second, first
        engines = [("a", first, "3d"), ("b", second, "images")]
        twin = short_name(first.name) == short_name(second.name)
        stages, material, caps = TWO_STAGES, TWO_MATERIAL, TWO_CAPS
        say("info", f"Two graphics cards: VGAC will use both. The {_gb(first.gb)} GB card ({short_name(first.name)}) makes the "
            f"3D models; the {_gb(second.gb)} GB card ({short_name(second.name)}) makes the pictures, the side views and does the rig.")
        explain.append("The 3D step is the one that needs the most graphics memory, so it goes on the card with more. "
                       "The other card takes everything else, so a picture and a 3D model can be made at the same time.")
        if three_d is not None and three_d == chosen[1].index and first.memory_mb < second.memory_mb:
            say("warn", f"You gave the 3D step to the smaller card ({short_name(first.name)}). It is the step most likely to run out of memory.")
        for c in (first, second):
            if c.gb < TESTED_GB - CARD_TOLERANCE_GB:
                say("warn", f"{short_name(c.name)} has {_gb(c.gb)} GB, which is below what VGAC was tried on (10 GB and 12 GB cards). "
                    "The largest steps may stop with an out-of-memory error. Nothing is harmed if one does.")
    else:
        only = chosen[0]
        engines = [("a", only, "3d")]
        twin = False
        stages, material, caps = ONE_STAGES, ONE_MATERIAL, ONE_CAPS
        say("info", f"One graphics card ({short_name(only.name)}, {_gb(only.gb)} GB): every step runs on it, one at a time. "
            "Slower than two cards, otherwise the same.")
        explain.append("With one card there is one engine, and jobs wait in line for it.")
        if only.gb < TESTED_GB - CARD_TOLERANCE_GB:
            say("warn", f"{_gb(only.gb)} GB of graphics memory is below what VGAC was tested with. The side views and the 3D model "
                "are the largest steps and may stop with an out-of-memory error. You can try it; nothing is harmed if it fails.")
        else:
            say("info", "VGAC was built and tested on 10 GB and 12 GB cards, and not yet on a single card, so a step on one card "
                "has not been tried here. If one stops with an out-of-memory error, tell the app which step.")
    _ram_messages(hw, say)

    # --- the settings -----------------------------------------------------------------------------------------------
    inst: dict[str, Any] = {}
    out_engines = []
    names = [n for n, _, _ in engines]
    for name, card, role in engines:
        inst[name] = _engine_patch(name, card, role, twin)
        steps = STEP_WORDS["two" if len(engines) == 2 else "one"][name]
        out_engines.append({"name": name, "card": card.index, "gpu": short_name(card.name), "memory_gb": round(card.gb, 1),
                            "role": role, "steps": steps})
    if len(engines) == 1:
        inst["b"] = None  # one card, one engine: the second entry is taken away
    patch = {"instances": inst, "stage_instances": dict(stages), "material_stage_instances": dict(material),
             "judge_instance": "b" if len(engines) == 2 else "a", "instance_capabilities": {n: list(caps[n]) for n in names},
             "min_free_ram_gb": floor}
    # With one engine the table keeps its entry for "b" (the defaults hold it): nothing asks for an instance that is not there.
    status = "warn" if any(m["level"] == "warn" for m in msgs) else "ok"
    ignored = [c.index for c in cards if c.index not in {x[1].index for x in engines}]
    return Plan(status, out_engines, floor, msgs, explain, patch, ignored)


def _ram_messages(hw: Hardware, say: Callable[[str, str], None]) -> None:
    if hw.ram_total_gb <= 0:
        say("info", "The amount of memory in this computer could not be read, so the default floor of 24 GB free is kept.")
    elif hw.ram_total_gb < VERY_LOW_RAM_GB:
        say("warn", f"This computer has {_gb(hw.ram_total_gb)} GB of memory. With less than 16 GB the large steps will probably "
            "fail: one of the models alone is about 30 GB with its helper, and ComfyUI moves it between the card and the memory.")
    elif hw.ram_total_gb < LOW_RAM_GB - 0.5:
        say("warn", f"This computer has {_gb(hw.ram_total_gb)} GB of memory. With less than 32 GB of memory, large steps will be "
            "slow and may fail.")


# --- what is saved now, and writing ---------------------------------------------------------------------------------


def current_layout(settings: Settings) -> dict[str, Any]:
    """The engines and the step layout the app has now, for the comparison on the page."""
    return {"engines": [{"name": n, "gpu": i.gpu, "card": i.engine.cuda_device if i.engine else None, "role": i.role}
                        for n, i in settings.instances.items()],
            "stage_instances": dict(settings.stage_instances), "material_stage_instances": dict(settings.material_stage_instances),
            "judge_instance": settings.judge_instance, "instance_capabilities": {k: list(v) for k, v in settings.instance_capabilities.items()},
            "min_free_ram_gb": settings.min_free_ram_gb}


def fit_patch(patch: dict[str, Any], saved: Settings) -> dict[str, Any]:
    """The plan's patch made safe to write: a card number goes into an engine only where the instance has one (an engine
    needs its ComfyUI folder, which the ComfyUI panel sets); the card is still named in `gpu` and `discover`."""
    out = config.merge({}, patch)
    for name, p in (out.get("instances") or {}).items():
        if isinstance(p, dict) and (name not in saved.instances or saved.instances[name].engine is None):
            p.pop("engine", None)
    return out


def effective(patch: dict[str, Any]) -> Settings:
    """The settings the app would have with `patch` laid over what is saved; ConfigError when they would not be valid.
    Nothing is written."""
    raw, sources = config.layered_raw()
    merged = config.merge(raw, patch)
    s = config.resolve_paths(config._validate(merged, sources + " with the hardware plan"))
    from .jobs import check_stage_instances

    try:
        check_stage_instances(s)
    except ValueError as err:
        raise ConfigError(str(err)) from None
    return s


def same_layout(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("stage_instances", "material_stage_instances", "judge_instance", "instance_capabilities", "min_free_ram_gb")
    if any(a[k] != b[k] for k in keys):
        return False
    return [(e["name"], e["gpu"], e["card"]) for e in a["engines"]] == [(e["name"], e["gpu"], e["card"]) for e in b["engines"]]


def describe(use: list[int] | None = None, three_d: int | None = None, min_free_ram_gb: float | None = None,
             hw: Hardware | None = None) -> dict[str, Any]:
    """The answer of GET /api/hardware. Writes nothing."""
    hw = hw or read_hardware()
    plan = propose(hw, use, three_d, min_free_ram_gb)
    out: dict[str, Any] = {"hardware": hw.to_json(), "plan": plan.to_json(), "current": None, "already": False,
                           "can_accept": plan.status != "cannot", "problem": config.settings_problem()}
    try:
        saved = config.load_settings(create_library=False)
        cur = current_layout(saved)
        out["current"] = cur
        if plan.status != "cannot":
            out["already"] = same_layout(cur, current_layout(effective(fit_patch(plan.patch, saved))))
    except ConfigError as err:
        out["problem"] = out["problem"] or str(err)
    return out


def accept(use: list[int] | None = None, three_d: int | None = None, min_free_ram_gb: float | None = None,
           hw: Hardware | None = None) -> dict[str, Any]:
    """Write the plan for these choices, from a fresh reading. setup_settings.SettingError for a refusal."""
    hw = hw or read_hardware()
    plan = propose(hw, use, three_d, min_free_ram_gb)
    if plan.status == "cannot":
        raise setup_settings.SettingError("This computer has no graphics card VGAC can use, so there is no plan to save.")
    if min_free_ram_gb is not None and not 0 <= float(min_free_ram_gb) <= max(hw.ram_total_gb, 1):
        raise setup_settings.SettingError("The memory to keep free must be between 0 and the memory this computer has.")
    with setup_settings._lock:
        problem = config.settings_problem()
        if problem:
            raise setup_settings.SettingError(f"This machine's settings file could not be used ({problem}) and nothing can be "
                                              "saved until it is fixed or removed.")
        mpath = config.machine_path()
        if mpath is None:
            raise setup_settings.SettingError("This copy of the app has no machine settings file to write to.")
        saved_before = config.load_settings(create_library=False)
        before = current_layout(saved_before)
        patch = fit_patch(plan.patch, saved_before)
        try:
            effective(patch)
            from .state_lock import state_lock   # the starter writes this file too (vgac/state_lock.py)
            with state_lock(mpath.parent):
                data = config.merge(config.read_machine(mpath), {"backend": patch})
                # update_machine would drop a null; the file must keep it, or the defaults bring the instance back.
                for name, p in (patch.get("instances") or {}).items():
                    if p is None:
                        data.setdefault("backend", {}).setdefault("instances", {})[name] = None
                config.write_machine(data, mpath)
        except ConfigError as err:
            raise setup_settings.SettingError(f"That would leave the app unable to start, so it was not saved: {err}") from None
        saved = config.load_settings(create_library=False)
        for cb in list(setup_settings.after_change):
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("after the hardware plan was saved")
    after = current_layout(saved)
    changed = not same_layout(before, after)
    gone = [n["name"] for n in before["engines"] if n["name"] not in {e["name"] for e in after["engines"]}]
    note = (f"The engine for the second card was taken away ({', '.join(gone)}); the plan uses one card."
            if gone else "")
    return {"changed": changed, "message": ("The hardware plan is saved. It is used the next time the app starts; restart it to apply it."
                                            if changed else "The hardware plan was already what is saved; nothing changed."),
            "restart_needed": changed, "current": after,
            "plan": plan.to_json(), "note": note}


# --- HTTP ----------------------------------------------------------------------------------------------------------


class AcceptIn(BaseModel):
    use: list[int] | None = None
    three_d: int | None = None
    min_free_ram_gb: float | None = None


def _use(text: str | None) -> list[int] | None:
    if text is None or not text.strip():
        return None
    try:
        return [int(x) for x in text.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "use must be card numbers separated by commas.") from None


@router.get("")
def get_hardware(use: str | None = None, three_d: int | None = None, min_free_ram_gb: float | None = None) -> dict[str, Any]:
    return describe(_use(use), three_d, min_free_ram_gb)


@router.post("/accept")
def post_accept(body: AcceptIn) -> dict[str, Any]:
    try:
        return accept(body.use, body.three_d, body.min_free_ram_gb)
    except setup_settings.SettingError as err:
        raise HTTPException(400, str(err)) from None
