"""This machine's settings (config.py): local/machine.json laid over the tracked defaults, the atomic writer, the
one-time migration from the old tracked files (machine_migrate.py), and the engine command built from settings
(instances.engine_command)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from vgac import config, machine_migrate
from vgac.config import ConfigError, load_settings, merge, read_machine, update_machine, write_machine
from vgac.instances import engine_command

DEFAULTS = {
    "instances": {
        "a": {"url": "http://127.0.0.1:8188", "role": "3d"},
        "b": {"url": "http://127.0.0.1:8189", "role": "images", "output_dir": "runtime/comfy-b-output"},
    },
    "library_path": "library",
    "unity_project": "unity-sandbox",
    "clips_libraries": {"ual1": "data/animations/ual1/x.fbx"},
    "min_free_ram_gb": 24,
}


@pytest.fixture
def defaults(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(DEFAULTS), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VGAC_CONFIG", raising=False)
    monkeypatch.delenv("VGAC_MACHINE", raising=False)


def _machine(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "local" / "machine.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# --- layering -------------------------------------------------------------------------------------------------------


def test_merge_tables_merge_none_removes_and_inputs_are_kept():
    base = {"a": {"x": 1, "y": 2}, "b": [1, 2], "c": 3}
    over = {"a": {"y": 5, "z": 6}, "b": [9], "c": None}
    assert merge(base, over) == {"a": {"x": 1, "y": 5, "z": 6}, "b": [9]}
    assert base == {"a": {"x": 1, "y": 2}, "b": [1, 2], "c": 3}


def test_defaults_alone_relative_paths_are_project_relative(defaults: Path, tmp_path: Path):
    s = load_settings(defaults, machine=tmp_path / "missing.json", create_library=False)
    assert s.library_path == config.PROJECT_DIR / "library"
    assert s.unity_project == config.PROJECT_DIR / "unity-sandbox"
    assert s.instances["b"].output_dir == config.PROJECT_DIR / "runtime" / "comfy-b-output"
    assert s.clips_libraries["ual1"] == config.PROJECT_DIR / "data/animations/ual1/x.fbx"
    assert s.blender_exe is None and s.loras_dir is None and s.instances["a"].engine is None


def test_machine_file_is_laid_over_the_defaults(defaults: Path, tmp_path: Path):
    lib = tmp_path / "lib"
    m = _machine(tmp_path, {"schema": 1, "backend": {
        "library_path": str(lib), "blender_exe": "C:/Tools/blender.exe", "min_free_ram_gb": 8,
        "instances": {"a": {"gpu": "Card A", "discover": {"gpu_match": "A"}}, "b": None}},
        "desktop": {"comfyA": {"desktopExe": "x"}}})
    s = load_settings(defaults, machine=m)
    assert s.library_path == lib and lib.is_dir()  # the library folder is made
    assert s.blender_exe == Path("C:/Tools/blender.exe")
    assert s.min_free_ram_gb == 8
    assert s.instances["a"].gpu == "Card A" and s.instances["a"].url == "http://127.0.0.1:8188"  # merged, not replaced
    assert s.instances["a"].discover.gpu_match == "A"
    assert "b" not in s.instances  # a null removes an instance (a one-card machine)


def test_which_machine_file_is_used(defaults: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    m = _machine(tmp_path, {"backend": {"min_free_ram_gb": 3}})
    assert config.machine_path() == config.MACHINE_PATH
    # VGAC_CONFIG alone (the tests' way): this machine's file is never laid over a test's settings.
    monkeypatch.setenv("VGAC_CONFIG", str(defaults))
    assert config.machine_path() is None
    assert load_settings(create_library=False).min_free_ram_gb == 24
    # Named explicitly: used.
    monkeypatch.setenv("VGAC_MACHINE", str(m))
    assert load_settings(create_library=False).min_free_ram_gb == 3


def test_missing_machine_file_is_fine_and_broken_ones_are_plain_errors(defaults: Path, tmp_path: Path):
    assert read_machine(tmp_path / "none.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{ nope", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"not valid JSON \(line 1"):
        load_settings(defaults, machine=bad, create_library=False)
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError, match="must hold a JSON object"):
        load_settings(defaults, machine=bad, create_library=False)
    bad.write_text(json.dumps({"backend": {"min_free_ram_gb": "lots", "instances": {"c": {"role": "x"}}}}),
                   encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_settings(defaults, machine=bad, create_library=False)
    assert "min_free_ram_gb" in str(err.value) and "instances.c.url: is missing" in str(err.value)
    with pytest.raises(ConfigError, match="was not found"):
        load_settings(tmp_path / "no-defaults.yaml", machine=False)


def test_paths_that_do_not_exist_are_reported_not_fatal(defaults: Path, tmp_path: Path):
    m = _machine(tmp_path, {"backend": {"blender_exe": str(tmp_path / "nope.exe"), "loras_dir": str(tmp_path / "nl"),
                                        "instances": {"b": {"engine": {"comfy_dir": str(tmp_path / "nc")}}}}})
    s = load_settings(defaults, machine=m, create_library=False)
    problems = config.path_problems(s)
    assert any("blender_exe" in p and "does not exist" in p for p in problems)
    assert any("loras_dir" in p for p in problems)
    assert any("instances.b.engine.comfy_dir" in p for p in problems)


# --- the writer -----------------------------------------------------------------------------------------------------


def test_write_machine_is_atomic_keeps_a_backup_and_refuses_bad_settings(defaults: Path, tmp_path: Path):
    m = tmp_path / "local" / "machine.json"
    write_machine({"backend": {"min_free_ram_gb": 10}}, m, defaults)
    first = json.loads(m.read_text(encoding="utf-8"))
    assert first == {"backend": {"min_free_ram_gb": 10}, "schema": config.MACHINE_SCHEMA}
    update_machine({"backend": {"blender_exe": "C:/b.exe"}}, m, defaults)
    assert json.loads(m.read_text(encoding="utf-8"))["backend"] == {"min_free_ram_gb": 10, "blender_exe": "C:/b.exe"}
    assert json.loads((m.parent / "machine.json.bak").read_text(encoding="utf-8")) == first
    before = m.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="min_free_ram_gb"):
        write_machine({"backend": {"min_free_ram_gb": -1}}, m, defaults)
    with pytest.raises(ConfigError):
        write_machine({"backend": "no"}, m, defaults)
    assert m.read_text(encoding="utf-8") == before  # untouched
    assert [p.name for p in m.parent.iterdir() if p.suffix == ".tmp"] == []  # no temporary file left behind


def test_write_machine_leaves_the_old_file_when_the_swap_fails(defaults: Path, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch):
    m = tmp_path / "local" / "machine.json"
    write_machine({"backend": {"min_free_ram_gb": 10}}, m, defaults)
    before = m.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk said no")

    monkeypatch.setattr(config.os, "replace", boom)
    with pytest.raises(ConfigError, match="could not be written: disk said no"):
        write_machine({"backend": {"min_free_ram_gb": 11}}, m, defaults)
    assert m.read_text(encoding="utf-8") == before
    assert [p.name for p in m.parent.iterdir() if p.suffix == ".tmp"] == []


# --- the engine command ---------------------------------------------------------------------------------------------


def test_engine_command_from_settings(defaults: Path, tmp_path: Path):
    comfy = tmp_path / "Comfy Install"
    m = _machine(tmp_path, {"backend": {
        "blender_exe": "D:/Blender/blender.exe", "comfy_input_dir": "D:/in",
        "instances": {"b": {"engine": {"comfy_dir": str(comfy), "cuda_device": 0, "extra_model_paths": "D:/paths.yaml",
                                       "user_dir": "runtime/comfy-b-user", "extra_args": ["--lowvram"]}}}}})
    s = load_settings(defaults, machine=m, create_library=False)
    argv, cwd, env = engine_command(s, "b")
    assert cwd == comfy
    assert argv == [str(comfy / "ComfyUI/.venv/Scripts/python.exe"), "-s", os.path.join("ComfyUI", "main.py"),
                    "--port", "8189", "--listen", "127.0.0.1", "--cuda-device", "0",
                    "--extra-model-paths-config", str(Path("D:/paths.yaml")), "--input-directory", str(Path("D:/in")),
                    "--user-directory", str(config.PROJECT_DIR / "runtime/comfy-b-user"),
                    "--output-directory", str(config.PROJECT_DIR / "runtime/comfy-b-output"), "--lowvram"]
    # The launcher and ComfyUI Desktop recognise a running B by this piece of its command line.
    assert f"{os.path.join('ComfyUI', 'main.py')} --port 8189" in " ".join(argv)
    assert env == {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "SKINTOKEN_BLENDER_BIN": str(Path("D:/Blender/blender.exe"))}
    with pytest.raises(ValueError):
        engine_command(s, "a")


# --- the migration --------------------------------------------------------------------------------------------------

OLD_SCRIPT = """# Start ComfyUI instance B.
$env:SKINTOKEN_BLENDER_BIN = "D:\\Tools\\blender\\blender.exe"
Set-Location "D:\\Comfy\\Install (1)"
& ".\\ComfyUI\\.venv\\Scripts\\python.exe" -s ComfyUI\\main.py `
    --port 8189 --listen 127.0.0.1 --cuda-device 0 `
    --extra-model-paths-config "D:\\Comfy\\paths.yaml" `
    --input-directory "D:\\Comfy\\input" `
    --user-directory "{project}\\runtime\\comfy-b-user" `
    --output-directory "{project}\\runtime\\comfy-b-output"
"""


def _old_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = str(config.PROJECT_DIR)
    scripts = tmp_path / "old"
    scripts.mkdir()
    (scripts / "start-comfy-b.ps1").write_text(OLD_SCRIPT.replace("{project}", project), encoding="utf-8")
    old = {
        "instances": {
            "a": {"url": "http://127.0.0.1:8188", "gpu": "Card A", "role": "3d", "process_match": "--cuda-device 1",
                  "discover": {"gpu_match": "A", "ports": [8188, 8190]}},
            "b": {"url": "http://127.0.0.1:8189", "gpu": "Card B", "role": "images",
                  "output_dir": project.replace("\\", "/") + "/runtime/comfy-b-output",
                  "start_script": "runtime/start-comfy-b.ps1", "start_timeout_seconds": 180},
        },
        "library_path": project + "\\library",
        "blender_exe": "D:/Tools/blender/blender.exe",
        "unity_project": project.replace("\\", "/") + "/unity-sandbox",
        "comfy_input_dir": "D:/Comfy/input",
        "loras_dir": "D:/Comfy/models/loras",
        "clips_libraries": {"ual1": project.replace("\\", "/") + "/data/animations/ual1/x.fbx"},
        "min_free_ram_gb": 24,
    }
    old_cfg = tmp_path / "old-config.yaml"
    old_cfg.write_text(yaml.safe_dump(old), encoding="utf-8")
    old_desk = tmp_path / "old-desktop.json"
    old_desk.write_text(json.dumps({"projectRoot": project, "comfyA": {"name": "A (Card A)", "url": "u",
                                                                      "desktopExe": "D:/Comfy/Comfy.exe"},
                                    "comfyB": {"name": "B", "startScript": "runtime/start-comfy-b.ps1"}}),
                        encoding="utf-8")
    return old_cfg, old_desk, scripts


def test_parse_start_script_reads_the_old_shape():
    p = machine_migrate.parse_start_script(OLD_SCRIPT.replace("{project}", "P:\\proj"))
    assert p["env"] == {"SKINTOKEN_BLENDER_BIN": "D:\\Tools\\blender\\blender.exe"}
    assert p["cwd"] == "D:\\Comfy\\Install (1)"
    assert p["python"] == ".\\ComfyUI\\.venv\\Scripts\\python.exe" and p["main"] == "ComfyUI\\main.py"
    assert p["flags"]["--cuda-device"] == "0" and p["flags"]["--user-directory"] == "P:\\proj\\runtime\\comfy-b-user"
    assert p["extra"] == []


def test_portable_makes_project_paths_relative():
    root = str(config.PROJECT_DIR)
    assert machine_migrate.portable(root + "\\library") == "library"
    assert machine_migrate.portable(root.upper() + "/runtime/x") == "runtime/x"
    assert machine_migrate.portable(root) == "."
    assert machine_migrate.portable("D:\\Other\\x") == "D:/Other/x"
    assert machine_migrate.portable("http://127.0.0.1:8188") == "http://127.0.0.1:8188"
    assert machine_migrate.overlay({"a": 1, "t": {"x": 1, "y": 2}}, {"a": 1, "t": {"x": 1}, "gone": 3}) == \
        {"t": {"y": 2}, "gone": None}


def test_migration_keeps_every_value_and_turns_the_script_into_an_engine(defaults: Path, tmp_path: Path):
    old_cfg, old_desk, scripts = _old_files(tmp_path)
    new_desk = tmp_path / "new-desktop.json"
    new_desk.write_text(json.dumps({"projectRoot": "..", "comfyA": {"name": "ComfyUI A", "url": "u"},
                                    "comfyB": {"name": "B"}}), encoding="utf-8")
    data, notes = machine_migrate.migrate(old_cfg, old_desk, scripts, defaults, new_desktop=new_desk)
    assert notes == []
    back = data["backend"]
    # Only this machine's values; project paths that equal the defaults are not repeated.
    assert "library_path" not in back and "unity_project" not in back and "clips_libraries" not in back
    assert back["blender_exe"] == "D:/Tools/blender/blender.exe"
    eng = back["instances"]["b"]["engine"]
    assert eng == {"comfy_dir": "D:/Comfy/Install (1)", "cuda_device": 0, "extra_model_paths": "D:/Comfy/paths.yaml",
                   "user_dir": "runtime/comfy-b-user"}
    assert "start_script" not in back["instances"]["b"]
    assert data["desktop"] == {"comfyA": {"name": "A (Card A)", "desktopExe": "D:/Comfy/Comfy.exe"},
                               "comfyB": {"startScript": "runtime/start-comfy-b.ps1"}}
    m = tmp_path / "local" / "machine.json"
    write_machine(data, m, defaults)

    before = load_settings(old_cfg, machine=False, create_library=False)
    after = load_settings(defaults, machine=m, create_library=False)
    rows, ok = machine_migrate.compare(before, after)
    assert ok, [r for r in rows if r[3] == "DIFFERENT"]
    assert {r[0].split(".")[2] for r in rows if r[3] == "engine"} == {"engine", "start_script"}
    lines, same = machine_migrate.compare_commands(after, {"b": scripts / "start-comfy-b.ps1"})
    assert same, lines


def test_migration_tool_writes_once_and_reports(defaults: Path, tmp_path: Path, capsys):
    old_cfg, old_desk, scripts = _old_files(tmp_path)
    m = tmp_path / "local" / "machine.json"
    args = ["--old-config", str(old_cfg), "--scripts", str(scripts), "--defaults", str(defaults), "--machine", str(m)]
    assert machine_migrate.main(args) == 0 and not m.exists()  # a dry run writes nothing
    assert machine_migrate.main(args + ["--write"]) == 0 and m.is_file()
    assert "settings: all equal" in capsys.readouterr().out
    assert machine_migrate.main(args + ["--write"]) == 2  # never replaces an existing file without --force
    assert machine_migrate.main(args + ["--write", "--force"]) == 0
    assert (m.parent / "machine.json.bak").is_file()


def test_migration_notes_a_script_it_cannot_read(defaults: Path, tmp_path: Path):
    old_cfg, _, scripts = _old_files(tmp_path)
    (scripts / "start-comfy-b.ps1").write_text("Write-Host hello\n", encoding="utf-8")
    data, notes = machine_migrate.migrate(old_cfg, None, scripts, defaults)
    assert data["backend"]["instances"]["b"]["start_script"] == "runtime/start-comfy-b.ps1"
    assert notes and "was kept" in notes[0]


def test_tracked_defaults_hold_no_machine_values():
    """backend/config.yaml must stay true on any machine: no drive paths, no card names, no tool paths."""
    text = config.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    for key in ("blender_exe", "unity_exe", "comfy_input_dir", "loras_dir"):
        assert key not in raw
    for inst in raw["instances"].values():
        assert not {"gpu", "discover", "engine", "start_script", "process_match"} & set(inst)
    import re
    assert not re.search(r"\b[A-Za-z]:[\\/]", text)
