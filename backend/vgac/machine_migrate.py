"""One-time move of this machine's values out of the tracked files into local/machine.json.

Before installer phase 0a, this machine's values sat in tracked files: backend/config.yaml, desktop/config.json and
the two engine start scripts runtime/start-comfy-a.ps1 and -b.ps1. This tool reads those older files and writes
local/machine.json so that the settings the backend loads (the new defaults in backend/config.yaml with the machine
file laid over them) are the same as before, value for value. The one intended difference: an instance that was
started with a start script now gets an `engine` entry with the same command, and no start script.

    cd backend
    .venv\\Scripts\\python -m vgac.machine_migrate --old-config OLD.yaml --old-desktop OLD.json [--scripts DIR]
        [--defaults backend/config.yaml] [--write] [--force]

Without --write it only prints what it would write and the comparison. It never overwrites an existing machine file
without --force (the previous one is kept as machine.json.bak either way). Finishes with compare(): every setting
before and after, and the engine command next to the script's.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from . import config
from .config import DEFAULT_CONFIG_PATH, MACHINE_PATH, PROJECT_DIR

DESKTOP_CONFIG_PATH = PROJECT_DIR / "desktop" / "config.json"
FLAG_KEYS = {"--port": "port", "--listen": "listen", "--cuda-device": "cuda_device",
             "--extra-model-paths-config": "extra_model_paths", "--input-directory": "input_dir",
             "--user-directory": "user_dir", "--output-directory": "output_dir"}


# --- reading a start script ---------------------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    """PowerShell-ish argument split: double or single quoted words, or bare words."""
    return [a or b or c for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', text)]


def parse_start_script(text: str) -> dict[str, Any]:
    """What a start script of the old kind does: {"env": {...}, "cwd": str, "python": str, "main": str,
    "flags": {flag: value}, "extra": [...]}. Only the shape the old scripts had: `$env:X = "..."` lines,
    `Set-Location "..."`, then `& "<python>" -s <main> --flag value ...` with backtick line continuations."""
    joined = re.sub(r"`\s*\r?\n", " ", text)
    out: dict[str, Any] = {"env": {}, "cwd": None, "python": None, "main": None, "flags": {}, "extra": []}
    for line in joined.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"""\$env:(\w+)\s*=\s*["']([^"']*)["']""", s, re.IGNORECASE)
        if m:
            out["env"][m.group(1)] = m.group(2)
            continue
        m = re.match(r"""Set-Location\s+["']?([^"']+)["']?""", s, re.IGNORECASE)
        if m:
            out["cwd"] = m.group(1).strip()
            continue
        if s.startswith("&"):
            toks = _tokens(s[1:])
            out["python"] = toks[0]
            rest = toks[1:]
            if rest and rest[0] == "-s":
                rest = rest[1:]
            out["main"] = rest[0] if rest else None
            rest = rest[1:]
            i = 0
            while i < len(rest):
                t = rest[i]
                if t in FLAG_KEYS and i + 1 < len(rest):
                    out["flags"][t] = rest[i + 1]
                    i += 2
                else:
                    out["extra"].append(t)
                    i += 1
    return out


def script_argv(parsed: dict[str, Any]) -> list[str]:
    """The command line a parsed script runs, with the python made absolute against its Set-Location folder."""
    py = PureWindowsPath(parsed["python"])
    if not py.is_absolute() and parsed["cwd"]:
        py = PureWindowsPath(parsed["cwd"]) / py
    argv = [str(py), "-s", parsed["main"]]
    for flag in FLAG_KEYS:
        if flag in parsed["flags"]:
            argv += [flag, parsed["flags"][flag]]
    return argv + parsed["extra"]


# --- portable values ----------------------------------------------------------------------------------------------


def portable(value: Any, project_dir: Path = PROJECT_DIR) -> Any:
    """A path string under the project folder made relative to it (forward slashes); another absolute Windows path
    with forward slashes; anything else unchanged."""
    if not isinstance(value, str) or not re.match(r"^[A-Za-z]:[\\/]", value):
        return value
    parts = PureWindowsPath(value).parts
    root = PureWindowsPath(str(project_dir)).parts
    if len(parts) >= len(root) and [x.lower() for x in parts[:len(root)]] == [x.lower() for x in root]:
        rest = parts[len(root):]
        return "/".join(rest) if rest else "."
    return value.replace("\\", "/")


def portable_tree(value: Any, project_dir: Path = PROJECT_DIR) -> Any:
    if isinstance(value, dict):
        return {k: portable_tree(v, project_dir) for k, v in value.items()}
    if isinstance(value, list):
        return [portable_tree(v, project_dir) for v in value]
    return portable(value, project_dir)


def overlay(old: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """The smallest table that, laid over `defaults` with config.merge, gives `old` (a None removes a default)."""
    out: dict[str, Any] = {}
    for k, v in old.items():
        if k not in defaults:
            out[k] = v
        elif isinstance(v, dict) and isinstance(defaults[k], dict):
            sub = overlay(v, defaults[k])
            if sub:
                out[k] = sub
        elif v != defaults[k]:
            out[k] = v
    for k in defaults:
        if k not in old:
            out[k] = None
    return out


def _absolute(value: str, project_dir: Path = PROJECT_DIR) -> PureWindowsPath:
    p = PureWindowsPath(value)
    return p if p.is_absolute() else PureWindowsPath(str(project_dir)) / p


def _same_path(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return str(PureWindowsPath(a)).lower() == str(PureWindowsPath(b)).lower()


# --- the migration ------------------------------------------------------------------------------------------------


def engine_from_script(parsed: dict[str, Any], inst: dict[str, Any], comfy_input_dir: str | None,
                       project_dir: Path = PROJECT_DIR) -> dict[str, Any]:
    """An `engine` entry that runs what the script runs. Values equal to their defaults are left out."""
    if not parsed.get("cwd") or not parsed.get("python") or not parsed.get("main"):
        raise ValueError("the start script does not have the expected Set-Location and & python ... lines")
    eng: dict[str, Any] = {"comfy_dir": portable(parsed["cwd"], project_dir)}
    py = PureWindowsPath(parsed["python"])
    if py.parts and py.parts[0] == ".":
        py = PureWindowsPath(*py.parts[1:])
    py_text = py.as_posix() if not py.is_absolute() else str(py)
    if py_text != config.EngineConfig.model_fields["python"].default.as_posix():
        eng["python"] = py_text
    main = parsed["main"].replace("\\", "/")
    if main != config.EngineConfig.model_fields["main"].default:
        eng["main"] = main
    flags = parsed["flags"]
    url_port = None
    m = re.search(r":(\d+)", str(inst.get("url", "")).split("//")[-1])
    if m:
        url_port = int(m.group(1))
    if "--port" in flags and int(flags["--port"]) != url_port:
        eng["port"] = int(flags["--port"])
    if "--listen" in flags and flags["--listen"] != "127.0.0.1":
        eng["listen"] = flags["--listen"]
    elif "--listen" not in flags:
        eng["listen"] = ""
    if "--cuda-device" in flags:
        eng["cuda_device"] = int(flags["--cuda-device"])
    if "--extra-model-paths-config" in flags:
        eng["extra_model_paths"] = portable(flags["--extra-model-paths-config"], project_dir)
    if "--input-directory" in flags and not _same_path(flags["--input-directory"], comfy_input_dir):
        eng["input_dir"] = portable(flags["--input-directory"], project_dir)
    if "--user-directory" in flags:
        eng["user_dir"] = portable(flags["--user-directory"], project_dir)
    out_dir = inst.get("output_dir")
    if "--output-directory" in flags:
        here = None if out_dir is None else str(_absolute(str(out_dir), project_dir))
        if not _same_path(here, flags["--output-directory"]):
            eng["output_dir"] = portable(flags["--output-directory"], project_dir)
    if parsed["extra"]:
        eng["extra_args"] = list(parsed["extra"])
    return eng


def migrate(old_config: Path, old_desktop: Path | None, scripts_dir: Path | None, defaults_path: Path,
            new_desktop: Path = DESKTOP_CONFIG_PATH, project_dir: Path = PROJECT_DIR) -> tuple[dict[str, Any], list[str]]:
    """The machine file to write, and notes (plain lines) on anything that needs a person's eye."""
    notes: list[str] = []
    old = yaml.safe_load(old_config.read_text(encoding="utf-8")) or {}
    defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}
    old = portable_tree(old, project_dir)
    blender = old.get("blender_exe")
    for name, inst in (old.get("instances") or {}).items():
        script_rel = inst.get("start_script")
        if not script_rel:
            continue
        script = Path(script_rel)
        if not script.is_absolute():
            script = (scripts_dir / script.name) if scripts_dir else project_dir / script
        try:
            parsed = parse_start_script(script.read_text(encoding="utf-8-sig"))
            inst["engine"] = engine_from_script(parsed, inst, old.get("comfy_input_dir"), project_dir)
        except (OSError, ValueError) as err:
            notes.append(f"instance {name}: its start script {script} was kept, because it could not be read as an "
                         f"engine command ({err}).")
            continue
        del inst["start_script"]
        sk = parsed["env"].get("SKINTOKEN_BLENDER_BIN")
        if sk and not _same_path(sk, blender):
            notes.append(f"instance {name}: its script set SKINTOKEN_BLENDER_BIN to {sk}, but blender_exe is "
                         f"{blender}; the engine now gets blender_exe.")
        other_env = {k: v for k, v in parsed["env"].items() if k != "SKINTOKEN_BLENDER_BIN"}
        if other_env:
            notes.append(f"instance {name}: its script also set {', '.join(other_env)}, which the engine settings do "
                         "not carry.")
    backend = overlay(old, portable_tree(defaults, project_dir))
    data: dict[str, Any] = {"schema": config.MACHINE_SCHEMA, "backend": backend}
    if old_desktop is not None and old_desktop.is_file():
        od = json.loads(old_desktop.read_text(encoding="utf-8"))
        nd = json.loads(new_desktop.read_text(encoding="utf-8")) if new_desktop.is_file() else {}
        od.pop("_comment", None)
        nd.pop("_comment", None)
        od_root = od.pop("projectRoot", None)
        nd.pop("projectRoot", None)
        if od_root and not _same_path(od_root, str(project_dir)):
            notes.append(f"desktop/config.json had projectRoot {od_root}, not this project folder; not carried over.")
        desk = overlay(od, nd)
        if desk:
            data["desktop"] = desk
    return data, notes


# --- the comparison -----------------------------------------------------------------------------------------------


def _flat(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else str(k)))
        return out or {prefix: {}}
    return {prefix: value}


def _norm(v: Any) -> Any:
    if isinstance(v, Path):
        return str(v).lower()
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def compare(before: config.Settings, after: config.Settings) -> tuple[list[tuple[str, Any, Any, str]], bool]:
    """Every setting before and after: (key, before, after, verdict) with verdict "same", "engine" (the intended
    start-script-to-engine change) or "DIFFERENT"; and whether nothing else differs."""
    a = _flat(before.model_dump())
    b = _flat(after.model_dump())
    rows = []
    ok = True
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if _norm(va) == _norm(vb):
            verdict = "same"
        elif re.match(r"instances\.\w+\.(start_script|engine)(\.|$)", k):
            verdict = "engine"
        else:
            verdict = "DIFFERENT"
            ok = False
        rows.append((k, va, vb, verdict))
    return rows, ok


def compare_commands(after: config.Settings, scripts: dict[str, Path]) -> tuple[list[str], bool]:
    """Each engine command next to the command its old script ran."""
    from .instances import engine_command

    lines, ok = [], True
    for name, script in scripts.items():
        if after.instances.get(name) is None or after.instances[name].engine is None:
            continue
        parsed = parse_start_script(script.read_text(encoding="utf-8-sig"))
        want = script_argv(parsed)
        argv, cwd, env = engine_command(after, name)
        same_argv = [x.lower() for x in argv] == [x.lower() for x in want]
        same_cwd = _same_path(str(cwd), parsed["cwd"])
        sk = parsed["env"].get("SKINTOKEN_BLENDER_BIN")
        same_env = sk is None or _same_path(env.get("SKINTOKEN_BLENDER_BIN"), sk)
        good = same_argv and same_cwd and same_env
        ok = ok and good
        lines.append(f"instance {name}: {'same command' if good else 'DIFFERENT command'}")
        lines.append(f"  script : cwd {parsed['cwd']} | {' '.join(want)} | SKINTOKEN_BLENDER_BIN={sk}")
        lines.append(f"  engine : cwd {cwd} | {' '.join(argv)} | "
                     + " ".join(f"{k}={v}" for k, v in env.items()))
    return lines, ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--old-config", type=Path, required=True, help="the backend/config.yaml that held this machine's values")
    ap.add_argument("--old-desktop", type=Path, help="the desktop/config.json that held this machine's values")
    ap.add_argument("--scripts", type=Path, help="folder holding the old start scripts (default: as named in the config)")
    ap.add_argument("--defaults", type=Path, default=DEFAULT_CONFIG_PATH, help="the new defaults file")
    ap.add_argument("--machine", type=Path, default=MACHINE_PATH, help="the machine file to write")
    ap.add_argument("--write", action="store_true", help="write the machine file (otherwise only show it)")
    ap.add_argument("--force", action="store_true", help="replace an existing machine file")
    args = ap.parse_args(argv)

    data, notes = migrate(args.old_config, args.old_desktop, args.scripts, args.defaults)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    for n in notes:
        print("NOTE:", n)
    if args.write:
        if args.machine.exists() and not args.force:
            print(f"{args.machine} already exists; not replaced (use --force).", file=sys.stderr)
            return 2
        config.write_machine(data, args.machine, args.defaults)
        print(f"written: {args.machine}")
        after = config.load_settings(args.defaults, machine=args.machine, create_library=False)
    else:
        raw = config.merge(yaml.safe_load(args.defaults.read_text(encoding="utf-8")) or {}, data["backend"])
        after = config.resolve_paths(config.Settings.model_validate(raw))
    before = config.load_settings(args.old_config, machine=False, create_library=False)
    rows, ok = compare(before, after)
    width = max(len(r[0]) for r in rows)
    print(f"\n{'setting':<{width}}  verdict    before -> after")
    for k, va, vb, verdict in rows:
        shown = f"{va}" if verdict == "same" else f"{va} -> {vb}"
        print(f"{k:<{width}}  {verdict:<9}  {shown}")
    scripts = {}
    old_raw = yaml.safe_load(args.old_config.read_text(encoding="utf-8")) or {}
    for name, inst in (old_raw.get("instances") or {}).items():
        if inst.get("start_script"):
            s = Path(inst["start_script"])
            scripts[name] = (args.scripts / s.name) if args.scripts else (s if s.is_absolute() else PROJECT_DIR / s)
    cmd_lines, cmd_ok = compare_commands(after, scripts)
    print()
    print("\n".join(cmd_lines))
    print(f"\nsettings: {'all equal' if ok else 'SOME DIFFER'} (apart from start script -> engine); "
          f"engine commands: {'equal to the scripts' if cmd_ok else 'DIFFER'}")
    return 0 if ok and cmd_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
