#!/usr/bin/env python3
"""cardq: wait for a free ComfyUI card, claim it, run one command, release it.

Usage:
  python tools/cardq.py run --card A|B|any --session NAME --minutes N [--quiet] -- COMMAND ...
  python tools/cardq.py status

run:
  Waits (polling every 15 s) until the chosen card has nothing running or
  pending in its ComfyUI /queue (A is port 8188, B is 8189), no other cardq
  lock holds it, and the app on 8765 has no running or queued job on it.
  "any" takes whichever card frees first; --quiet waits for both cards and
  holds both while the command runs.
  A card the owner switched off in the app (enabled false, or state off) is
  not waited for: "any" uses the other card, and when no card can be used (or
  --card names an off card, or --quiet is given with one off) it prints why
  and exits 75 without running the command. A cleanly stopped engine (state
  stopped) counts as free with an empty queue: its port is not answering,
  and the app starts that engine itself when a job arrives. It then takes a lock file under
  bakeoff/cards/locks/, appends a claim line to bakeoff/cards/claims.jsonl,
  waits while free memory is under 12 GB, runs the command with stdout and
  stderr passed through, and releases the lock. If that card's queue is empty
  afterwards it POSTs /free {"unload_models": true, "free_memory": true} once.
  The exit code is the command's. The command line is recorded in the claim
  note (cardq does not know the prompt id).

Which card flag to use: a job sent to the owner's app on 8765 is routed by the
app itself. --card A locks card A for this session's own runner and scratch
backends, but the app may still place its own jobs on either card; for app
jobs use --card any or --quiet.

Rules it keeps:
  - After 30 minutes of waiting it prints a line and keeps waiting.
  - It never calls /interrupt and never deletes queue items.
  - A lock whose process is gone (checked by pid) is reclaimed with a log line.
  - The app on 8765 is only ever read (GET).
  - A card that is off in the app (GET /api/gpus: enabled false or state off)
    is never waited for. A stopped engine is free, not off; a failed engine
    is still waited for, as before.

status:
  Prints each card's queue counts, the lock holder, and the app's running and
  queued jobs per card. Read-only.

The lock and claim files live in the main checkout's bakeoff/cards/ (found
through git, so worktrees share them); set CARDQ_ROOT to a folder to use its
bakeoff/cards/ instead.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

CARDS = {"A": 8188, "B": 8189}
APP_URL = "http://127.0.0.1:8765"
POLL_SECONDS = 15
LONG_WAIT_SECONDS = 30 * 60
MIN_FREE_GB = 12.0
EXIT_CARD_UNAVAILABLE = 75  # EX_TEMPFAIL: no card could be used; the command did not run


def log(msg):
    print(f"[cardq {datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---- environment seams (replaced in tests) --------------------------------

def http_json(url, method="GET", body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def pid_alive(pid):
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def free_memory_gb():
    if os.name == "nt":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MS()
        ms.dwLength = ctypes.sizeof(ms)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        return ms.ullAvailPhys / 2**30
    return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30


def sleep(seconds):
    time.sleep(seconds)


def now():
    return datetime.now()


# ---- paths ----------------------------------------------------------------

def cards_dir():
    root = os.environ.get("CARDQ_ROOT")
    if root:
        return Path(root) / "bakeoff" / "cards"
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=here,
                             capture_output=True, text=True, check=True).stdout.strip()
        common = Path(out)
        if not common.is_absolute():
            common = (here / common).resolve()
        return common.parent / "bakeoff" / "cards"
    except Exception:
        return here.parent / "bakeoff" / "cards"


def lock_path(card):
    return cards_dir() / "locks" / f"{card}.lock"


def claims_path():
    return cards_dir() / "claims.jsonl"


# ---- locks ----------------------------------------------------------------

def read_lock(card):
    try:
        return json.loads(lock_path(card).read_text())
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        return {"pid": -1, "session": "?"}


def live_lock(card):
    """Return the lock holder if its process is alive; reclaim a stale lock."""
    info = read_lock(card)
    if info is None:
        return None
    if info.get("pid", -1) > 0 and pid_alive(info["pid"]):
        return info
    log(f"reclaiming stale lock on card {card} (session {info.get('session')}, pid {info.get('pid')} gone)")
    try:
        lock_path(card).unlink()
    except FileNotFoundError:
        pass
    return None


def take_lock(card, session, command):
    p = lock_path(card)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": os.getpid(), "session": session, "command": command,
                   "since": now().isoformat(timespec="seconds")}, f)
    return True


def release_lock(card):
    info = read_lock(card)
    if info and info.get("pid") == os.getpid():
        try:
            lock_path(card).unlink()
        except FileNotFoundError:
            pass


# ---- card state -----------------------------------------------------------

def queue_counts(card):
    """(running, pending) from ComfyUI, or None when it cannot be reached."""
    try:
        q = http_json(f"http://127.0.0.1:{CARDS[card]}/queue")
    except Exception:
        return None
    return len(q.get("queue_running", [])), len(q.get("queue_pending", []))


def app_jobs():
    """{card letter: {"running": n, "queued": n}}; zeros when the app is unreachable."""
    out = {c: {"running": 0, "queued": 0} for c in CARDS}
    for status in ("running", "queued"):
        try:
            jobs = http_json(f"{APP_URL}/api/jobs?status={status}") or []
        except Exception:
            return out
        for j in jobs:
            c = str(j.get("instance", "")).upper()
            if c in out:
                out[c][status] += 1
    return out


class CardUnavailable(Exception):
    """A card the owner has switched off or stopped in the app: nothing to wait for."""


def app_gpus():
    """{card letter: GET /api/gpus entry} from the app, or None when the app cannot be read."""
    try:
        data = http_json(f"{APP_URL}/api/gpus") or []
    except Exception:
        return None
    return {str(g.get("instance", "")).upper(): g for g in data}


def card_off_reason(card, gpus):
    """Why the app says the card is switched off, or None. Only enabled false or state off counts: a stopped
    engine is not off (the app starts it when a job comes). None too when the app is unreachable."""
    if not gpus or card not in gpus:
        return None
    g = gpus[card]
    if g.get("enabled") is False or g.get("state") == "off":
        return f"card {card} is switched off in the app"
    return None


def card_stopped(card, gpus):
    """True when the app says the card's engine is cleanly stopped (not started, not failed)."""
    return bool(gpus) and card in gpus and gpus[card].get("state") == "stopped"


def card_busy_reason(card, jobs, gpus=None):
    """Why the card is not free, or None when it is. A cleanly stopped engine has an empty queue: its port is not
    answering, so there is nothing to wait for."""
    counts = queue_counts(card)
    if counts is None and card_stopped(card, gpus):
        counts = (0, 0)
    if counts is None:
        return f"card {card} ComfyUI not reachable"
    if counts != (0, 0):
        return f"card {card} queue has {counts[0]} running, {counts[1]} pending"
    holder = live_lock(card)
    if holder:
        return f"card {card} locked by {holder.get('session')} (pid {holder.get('pid')})"
    if jobs[card]["running"] or jobs[card]["queued"]:
        return (f"app has {jobs[card]['running']} running, {jobs[card]['queued']} queued "
                f"job(s) on card {card}")
    return None


# ---- run ------------------------------------------------------------------

def acquire(wanted, mode, session, command):
    """Wait until the cards are free and locked; return the cards held. Raises CardUnavailable when a card that
    must be used (all of them, or every card in "any") is off or stopped in the app."""
    start = now()
    next_notice = LONG_WAIT_SECONDS
    last = None
    while True:
        gpus = app_gpus()
        offs = {c: card_off_reason(c, gpus) for c in wanted}
        off = [offs[c] for c in wanted if offs[c]]
        usable = [c for c in wanted if not offs[c]]
        if mode == "all" and off:
            raise CardUnavailable("; ".join(off))
        if mode == "any" and not usable:
            raise CardUnavailable("; ".join(off))
        jobs = app_jobs()
        reasons = {c: card_busy_reason(c, jobs, gpus) for c in usable}
        free = [c for c in usable if reasons[c] is None]
        target = None
        if mode == "all" and len(free) == len(wanted):
            target = list(wanted)
        elif mode == "any" and free:
            target = [free[0]]
        if target:
            held = []
            for c in target:
                if take_lock(c, session, command):
                    held.append(c)
            if len(held) == len(target):
                return held
            for c in held:
                release_lock(c)
        text = "; ".join(r for r in reasons.values() if r) or "lock race, retrying"
        waited = (now() - start).total_seconds()
        if text != last:
            log(f"waiting: {text}")
            last = text
        if waited >= next_notice:
            log(f"still waiting after {int(waited // 60)} minutes ({text}); not interrupting anything")
            next_notice += LONG_WAIT_SECONDS
        sleep(POLL_SECONDS)


def wait_for_memory():
    announced = False
    while True:
        gb = free_memory_gb()
        if gb >= MIN_FREE_GB:
            return
        if not announced:
            log(f"free memory {gb:.1f} GB is under {MIN_FREE_GB:.0f} GB; waiting")
            announced = True
        sleep(POLL_SECONDS)


def append_claim(card, session, minutes, command):
    line = {"time": now().isoformat(timespec="seconds"), "session": session,
            "card": CARDS[card], "asset": "-", "prompt_id": "-",
            "expected_min": minutes, "note": "cardq run: " + " ".join(command)}
    p = claims_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def free_if_empty(card):
    counts = queue_counts(card)
    if counts is None:
        return  # engine not answering (stopped): nothing loaded to free
    if counts != (0, 0):
        log(f"card {card} queue not empty; leaving models loaded")
        return
    try:
        http_json(f"http://127.0.0.1:{CARDS[card]}/free", "POST",
                  {"unload_models": True, "free_memory": True})
        log(f"freed card {card} models and memory")
    except Exception as e:
        log(f"/free on card {card} failed: {e}")


def cmd_run(args):
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        log("no command given after --")
        return 2
    if args.quiet:
        wanted, mode = list(CARDS), "all"
    elif args.card.upper() == "ANY":
        wanted, mode = list(CARDS), "any"
    else:
        wanted, mode = [args.card.upper()], "all"
    try:
        held = acquire(wanted, mode, args.session, command)
    except CardUnavailable as e:
        log(f"not run: {e}. Switch the card on in the app (or start its engine), or use --card any "
            "to take the other card.")
        return EXIT_CARD_UNAVAILABLE
    try:
        for c in held:
            append_claim(c, args.session, args.minutes, command)
            log(f"holding card {c} for {args.session}")
        wait_for_memory()
        rc = subprocess.run(command).returncode
    finally:
        for c in held:
            release_lock(c)
    for c in held:
        free_if_empty(c)
    return rc


def cmd_status(_args):
    jobs = app_jobs()
    gpus = app_gpus()
    for c, port in CARDS.items():
        counts = queue_counts(c)
        q = "not reachable" if counts is None else f"{counts[0]} running, {counts[1]} pending"
        info = read_lock(c)
        if info is None:
            lock = "none"
        else:
            alive = info.get("pid", -1) > 0 and pid_alive(info["pid"])
            lock = f"{info.get('session')} pid {info.get('pid')} ({'alive' if alive else 'stale'})"
        j = jobs[c]
        g = (gpus or {}).get(c)
        app_state = "app unreachable" if gpus is None else (
            f"app state {g.get('state', '?')}, enabled {g.get('enabled', '?')}" if g else "not in the app")
        print(f"card {c} (port {port}): queue {q}; lock {lock}; "
              f"app jobs {j['running']} running, {j['queued']} queued; {app_state}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cardq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--card", required=True, choices=["A", "B", "any", "a", "b", "ANY"])
    r.add_argument("--session", required=True)
    r.add_argument("--minutes", type=float, required=True)
    r.add_argument("--quiet", action="store_true")
    r.add_argument("command", nargs=argparse.REMAINDER)
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
