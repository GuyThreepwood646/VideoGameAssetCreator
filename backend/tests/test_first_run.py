"""A machine with nothing set up: the backend starts, says what is missing, refuses steps, and takes settings one at a
time (firstrun.py, setup_settings.py). Everything runs in temporary folders; no ComfyUI, no Blender, no real library or
machine file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from vgac import config, db, firstrun, setup_settings
from vgac.setup_settings import SettingError, set_setting

PORT_A, PORT_B = 59481, 59482


@pytest.fixture
def bare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The tracked defaults with nothing laid over them (no machine settings yet), unused ports, a temporary library."""
    cfg = {
        "instances": {"a": {"url": f"http://127.0.0.1:{PORT_A}", "gpu": "RTX 3060", "role": "3d"},
                      "b": {"url": f"http://127.0.0.1:{PORT_B}", "gpu": "RTX 3080", "role": "images"}},
        "library_path": str(tmp_path / "library"),
        "min_free_ram_gb": 0,
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    machine = tmp_path / "local" / "machine.json"
    monkeypatch.setenv("VGAC_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("VGAC_MACHINE", str(machine))
    monkeypatch.setattr(setup_settings, "blender_runner", lambda p: "Blender 5.2.3\nbuild date")
    monkeypatch.setattr(firstrun, "_port_open", lambda url: False)  # nothing answers, and no socket is opened
    config.reset_settings()
    db.reset_engine()
    yield tmp_path, machine
    db.reset_engine()
    config.reset_settings()


def _fake_blender(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "blender.exe"
    exe.write_bytes(b"x")
    return exe


def _fake_comfy(folder: Path) -> Path:
    (folder / "ComfyUI" / ".venv" / "Scripts").mkdir(parents=True)
    (folder / "ComfyUI" / "main.py").write_text("", encoding="utf-8")
    (folder / "ComfyUI" / ".venv" / "Scripts" / "python.exe").write_bytes(b"x")
    return folder


def _saved(machine: Path) -> dict:
    return json.loads(machine.read_text(encoding="utf-8"))


# --- the bare start -------------------------------------------------------------------------------------------------


def test_bare_start_says_what_is_missing(bare):
    from vgac import main

    with TestClient(main.app) as c:
        h = c.get("/api/health").json()
        assert h["ok"] is True and h["instances"]["host"]["blender"] is None and h["instances"]["host"]["unity"] is None
        r = c.get("/api/setup?refresh=1").json()
        fr = r["first_run"]
        assert fr["state"] == "not_set_up" and fr["fresh"] is True and fr["settings_problem"] == ""
        missing = {m["id"]: m for m in fr["missing"]}
        assert {"blender", "engine.a", "engine.b"} <= set(missing)
        assert missing["blender"]["setting"] == "blender_exe" and "setup card" in missing["blender"]["fix"]
        assert missing["engine.b"]["setting"] == "instances.b.engine.comfy_dir"
        assert "RTX 3080" in missing["engine.b"]["detail"] and f"{PORT_B}" in missing["engine.b"]["detail"]
        assert "unity" in {o["id"] for o in fr["optional"]}  # Unity is an extra, never a fault
        steps = {s["stage"]: s for s in fr["steps"]}
        assert not any(s["can_run"] for s in steps.values())
        assert steps["concept"]["missing"] == ["engine.b"] and steps["mesh"]["missing"] == ["engine.a", "blender"]
        assert steps["prep"]["missing"] == ["blender"] and "blender.exe" in steps["prep"]["message"]
        assert fr["restart_needed"] == []


def test_a_step_is_refused_with_the_missing_thing_and_queues_nothing(bare):
    from vgac import main

    with TestClient(main.app) as c:
        pid = c.post("/api/projects", json={"name": "p", "platform_target": "pc_mid"}).json()["id"]
        a = c.post(f"/api/projects/{pid}/assets", json={"name": "k", "category": "humanoid", "style_preset": "painted",
                                                         "prompt": "a knight"}).json()
        r = c.post(f"/api/assets/{a['id']}/stages/concept/run", json={})
        assert r.status_code == 409
        text = r.json()["detail"]
        assert "does not know where ComfyUI for RTX 3080 is" in text and "setup card" in text
        assert "instances." not in text and "blender_exe" not in text  # plain words, not setting names
        assert c.get("/api/jobs").json() == [] and c.get(f"/api/assets/{a['id']}").json()["stages"]["concept"] == []
        # Starting the card by hand says the same.
        assert "does not know where ComfyUI" in c.post("/api/gpus/b/start").json()["detail"]


def test_a_card_that_works_is_used_and_one_that_cannot_is_left_out(bare):
    tmp, _ = bare
    s = config.get_settings()
    up_b = lambda url: url.endswith(str(PORT_B))  # noqa: E731  (only the images card answers)
    assert firstrun.blockers(s, "concept", "humanoid", port_open=up_b) == []
    # The 3D step falls to the card that answers (the images card can build 3D models too); only Blender is lacking.
    assert firstrun.pick_instance(s, "mesh", "humanoid", port_open=up_b) == "b"
    assert [b["id"] for b in firstrun.blockers(s, "mesh", "humanoid", port_open=up_b)] == ["blender"]
    # Where only the 3D card may build 3D models and it cannot work, the step names that card.
    only_a = s.model_copy(update={"instance_capabilities": {"a": ["mesh"], "b": ["concept"]}})
    assert [b["id"] for b in firstrun.blockers(only_a, "mesh", "humanoid", port_open=up_b)] == ["engine.a", "blender"]


def test_a_comfyui_folder_that_is_wrong_is_not_called_ready(bare):
    tmp, machine = bare
    config.update_machine({"backend": {"instances": {"b": {"engine": {"comfy_dir": str(tmp / "nowhere")}}}}}, machine)
    config.reset_settings()
    s = config.get_settings()
    found = firstrun.blockers(s, "concept", "humanoid")
    assert found and "does not exist" in found[0]["message"] and "correct the ComfyUI folder" in found[0]["message"]
    _fake_comfy(tmp / "nowhere")
    assert firstrun.blockers(config.get_settings(), "concept", "humanoid") == []


def test_unreadable_machine_file_starts_on_the_defaults_and_refuses_steps(bare):
    from vgac import main

    tmp, machine = bare
    machine.parent.mkdir(parents=True)
    machine.write_text("{ not json", encoding="utf-8")
    with TestClient(main.app) as c:
        fr = c.get("/api/setup?refresh=1").json()["first_run"]
        assert fr["state"] == "settings_problem" and "not valid JSON" in fr["settings_problem"]
        assert fr["missing"][0]["id"] == "settings"
        assert all(not s["can_run"] and "settings could not be used" in s["message"] for s in fr["steps"])
        r = c.put("/api/setup/settings", json={"key": "blender_exe", "value": str(_fake_blender(tmp / "bl"))})
        assert r.status_code == 400 and "nothing can be saved" in r.json()["detail"]
    assert machine.read_text(encoding="utf-8") == "{ not json"  # left for the person to fix


# --- one setting at a time -----------------------------------------------------------------------------------------


def test_blender_is_checked_then_used_from_now_on(bare):
    tmp, machine = bare
    s = config.get_settings()
    assert firstrun.blockers(s, "prep", "humanoid") != []
    exe = _fake_blender(tmp / "Blender 5.2")
    out = set_setting("blender_exe", str(exe))
    assert out["applies"] == "now" and out["changed"] is True and out["from"] is None
    assert out["to"] == str(exe) and "used from now on" in out["message"] and out["restart_needed"] == []
    assert _saved(machine)["backend"]["blender_exe"] == str(exe) and _saved(machine)["schema"] == 1
    # The running app's settings changed at once, and the step no longer lacks Blender.
    assert str(config.get_settings().blender_exe) == str(exe)
    assert firstrun.blockers(config.get_settings(), "prep", "humanoid") == []
    assert "blender" not in {m["id"] for m in out["missing"]} and {"engine.a", "engine.b"} <= {m["id"] for m in out["missing"]}
    again = set_setting("blender_exe", str(exe))
    assert again["changed"] is False and "already set" in again["message"]


@pytest.mark.parametrize("value,fragment", [
    ("", "Give the full path of blender.exe"),
    (None, None),  # None clears: handled below
    ("blender.exe", "full path"),
    ("%USERPROFILE%\\blender.exe", "not expanded"),
    ("~/blender.exe", "not expanded"),
    ("C:\\Tools\\..\\Windows\\blender.exe", "'..'"),
    ("\\\\?\\C:\\blender.exe", "device path"),
    (5, "Give the full path"),
])
def test_blender_refuses_what_is_not_a_path(bare, value, fragment):
    tmp, machine = bare
    if value is None:
        assert set_setting("blender_exe", None)["changed"] is False
        return
    with pytest.raises(SettingError, match=fragment):
        set_setting("blender_exe", value)
    assert not machine.exists()  # nothing was written


def test_blender_refuses_the_wrong_thing(bare, monkeypatch):
    tmp, machine = bare
    exe = _fake_blender(tmp / "bl")
    with pytest.raises(SettingError, match="Blender's folder"):
        set_setting("blender_exe", str(tmp / "bl"))
    with pytest.raises(SettingError, match="is a folder"):
        set_setting("blender_exe", str(tmp))
    with pytest.raises(SettingError, match="no file at"):
        set_setting("blender_exe", str(tmp / "missing" / "blender.exe"))
    other = tmp / "bl" / "notepad.exe"
    other.write_bytes(b"x")
    with pytest.raises(SettingError, match="is not Blender"):
        set_setting("blender_exe", str(other))
    monkeypatch.setattr(setup_settings, "blender_runner", lambda p: "Blender 4.2.1 LTS")
    old = tmp / "old" / "blender.exe"
    old.parent.mkdir()
    old.write_bytes(b"y")
    with pytest.raises(SettingError, match="needs 5.0 or newer"):
        set_setting("blender_exe", str(old))
    monkeypatch.setattr(setup_settings, "blender_runner", lambda p: "garbage")
    dud = tmp / "dud" / "blender.exe"
    dud.parent.mkdir()
    dud.write_bytes(b"z")
    with pytest.raises(SettingError, match="did not say which version"):
        set_setting("blender_exe", str(dud))
    assert not machine.exists() and exe.is_file()


def test_the_path_is_kept_as_the_person_named_it(bare):
    tmp, machine = bare
    exe = _fake_blender(tmp / "My Tools" / "Blender")
    out = set_setting("blender_exe", f'  "{exe}"  ')  # pasted with quotes
    assert out["to"] == str(exe)
    link_target = tmp / "real"
    link_target.mkdir()
    try:
        (tmp / "link").symlink_to(link_target, target_is_directory=True)
    except OSError:
        return  # no permission to make a link on this machine
    set_setting("export_path", str(tmp / "link"))
    assert _saved(machine)["backend"]["export_path"] == str(tmp / "link")  # not resolved to ...\real


def test_folders_must_exist_and_be_writable(bare):
    tmp, machine = bare
    with pytest.raises(SettingError, match="does not exist"):
        set_setting("export_path", str(tmp / "nope"))
    f = tmp / "a.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SettingError, match="is a file"):
        set_setting("loras_dir", str(f))
    ok = tmp / "loras"
    ok.mkdir()
    assert set_setting("loras_dir", str(ok))["applies"] == "now"
    assert str(config.get_settings().loras_dir) == str(ok)
    proj = tmp / "unity"
    proj.mkdir()
    with pytest.raises(SettingError, match="not a Unity project"):
        set_setting("unity_project", str(proj))
    (proj / "Assets").mkdir()
    (proj / "ProjectSettings").mkdir()
    assert set_setting("unity_project", str(proj))["to"] == str(proj)
    with pytest.raises(SettingError, match="not the Unity editor"):
        set_setting("unity_exe", str(_fake_blender(tmp / "u")))


def test_library_and_input_folder_wait_for_a_restart(bare):
    tmp, machine = bare
    lib = tmp / "other-library"
    lib.mkdir()
    out = set_setting("library_path", str(lib))
    assert out["applies"] == "next_start" and "restart" in out["message"] and out["restart_needed"] == ["library_path"]
    assert _saved(machine)["backend"]["library_path"] == str(lib)
    assert config.get_settings().library_path == tmp / "library"  # the running app still uses the old one
    listing = setup_settings.describe()["settings"]
    row = next(r for r in listing if r["key"] == "library_path")
    assert row["pending_restart"] is True and row["value"] == str(lib) and row["in_use"] == str(tmp / "library")
    # Giving it back clears the wait.
    assert set_setting("library_path", str(tmp / "library"))["restart_needed"] == []


def test_engine_address_must_be_local_and_its_own(bare):
    tmp, machine = bare
    for bad, fragment in [("ftp://127.0.0.1:8188", "http://"), ("http://example.com:8188", "on this computer"),
                          ("http://127.0.0.1", "needs a port"), ("http://127.0.0.1:8188/x", "only the address"),
                          (f"http://127.0.0.1:{PORT_A}", "other graphics card already uses port"), ("", "Give the address")]:
        with pytest.raises(SettingError, match=fragment):
            set_setting("instances.b.url", bad)
    out = set_setting("instances.b.url", "http://localhost:59555/")
    assert out["to"] == "http://localhost:59555" and out["applies"] == "next_start"
    assert out["restart_needed"] == ["instances.b.url"]


def test_comfyui_folder_is_checked_and_makes_the_card_startable(bare):
    tmp, machine = bare
    with pytest.raises(SettingError, match="does not hold ComfyUI"):
        set_setting("instances.b.engine.comfy_dir", str(tmp))
    with pytest.raises(SettingError, match="not a setting"):
        set_setting("instances.c.engine.comfy_dir", str(tmp))
    with pytest.raises(SettingError, match="Give the ComfyUI folder first"):
        set_setting("instances.b.engine.main", "ComfyUI/main.py")
    comfy = _fake_comfy(tmp / "comfy")
    out = set_setting("instances.b.engine.comfy_dir", str(comfy))
    assert out["applies"] == "now" and "engine.b" not in {m["id"] for m in out["missing"]}
    assert _saved(machine)["backend"]["instances"]["b"]["engine"] == {"comfy_dir": str(comfy)}
    live = config.get_settings()
    assert str(live.instances["b"].engine.comfy_dir) == str(comfy)
    assert firstrun.blockers(live, "concept", "humanoid") == []  # the app can start it
    # Its other settings.
    with pytest.raises(SettingError, match="no file at"):
        set_setting("instances.b.engine.python", str(tmp / "py.exe"))
    with pytest.raises(SettingError, match="not a full path"):
        set_setting("instances.b.engine.main", str(tmp / "main.py"))
    with pytest.raises(SettingError, match="not a full path"):
        set_setting("instances.b.engine.main", "../main.py")
    with pytest.raises(SettingError, match="There is no"):
        set_setting("instances.b.engine.main", "Elsewhere/main.py")
    with pytest.raises(SettingError, match="between 0 and 15"):
        set_setting("instances.b.engine.cuda_device", 99)
    with pytest.raises(SettingError, match="number of the graphics card"):
        set_setting("instances.b.engine.cuda_device", "one")
    assert set_setting("instances.b.engine.cuda_device", "1")["to"] == 1
    yml = tmp / "extra_model_paths.yaml"
    yml.write_text("x: 1", encoding="utf-8")
    assert set_setting("instances.b.engine.extra_model_paths", str(yml))["changed"] is True
    with pytest.raises(SettingError, match="ends in .yaml"):
        set_setting("instances.b.engine.extra_model_paths", str(_fake_blender(tmp / "q")))
    # Clearing the folder removes the engine altogether (its other settings go with it).
    cleared = set_setting("instances.b.engine.comfy_dir", None)
    assert "engine" not in _saved(machine)["backend"]["instances"]["b"] and "engine.b" in {m["id"] for m in cleared["missing"]}


def test_a_setting_that_is_not_on_the_list_is_refused(bare):
    for key in ("min_free_ram_gb", "stage_instances", "instances.b.engine.extra_args", "instances", "../x", "",
                "instances.a.output_dir"):
        with pytest.raises(SettingError, match="not a setting that can be given here"):
            set_setting(key, "1")


def test_nothing_that_would_stop_the_app_starting_is_saved(bare, monkeypatch):
    tmp, machine = bare
    exe = _fake_blender(tmp / "bl")
    set_setting("blender_exe", str(exe))
    before = machine.read_text(encoding="utf-8")
    # The library folder must take files.
    f = tmp / "file"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SettingError, match="is a file"):
        set_setting("library_path", str(f))

    def refuse(*a, **k):
        raise config.ConfigError("the settings are not valid: library_path: bad")

    monkeypatch.setattr(config, "update_machine", refuse)
    with pytest.raises(SettingError, match="unable to start"):
        set_setting("export_path", str(tmp))
    assert machine.read_text(encoding="utf-8") == before


def test_the_http_door(bare):
    from vgac import main

    tmp, machine = bare
    exe = _fake_blender(tmp / "bl")
    with TestClient(main.app) as c:
        listing = c.get("/api/setup/settings").json()
        keys = [r["key"] for r in listing["settings"]]
        assert "blender_exe" in keys and "instances.a.engine.comfy_dir" in keys and "instances.b.url" in keys
        assert next(r for r in listing["settings"] if r["key"] == "blender_exe")["value"] is None
        bad = c.put("/api/setup/settings", json={"key": "blender_exe", "value": "nope"})
        assert bad.status_code == 400 and "full path" in bad.json()["detail"]
        assert c.put("/api/setup/settings", json={"key": "nonsense", "value": "x"}).status_code == 400
        # The setup report is asked afresh after a change, not kept for a minute.
        before = c.get("/api/setup").json()["first_run"]
        assert "blender" in {m["id"] for m in before["missing"]}
        ok = c.put("/api/setup/settings", json={"key": "blender_exe", "value": str(exe)})
        assert ok.status_code == 200 and ok.json()["applies"] == "now"
        after = c.get("/api/setup").json()["first_run"]
        assert "blender" not in {m["id"] for m in after["missing"]}
        assert after["fresh"] is False
        assert next(s for s in after["steps"] if s["stage"] == "prep")["can_run"] is True


def test_autopilot_on_a_bare_machine_stops_with_the_same_words(bare):
    import time

    from vgac import main

    with TestClient(main.app) as c:
        pid = c.post("/api/projects", json={"name": "p", "platform_target": "pc_mid"}).json()["id"]
        a = c.post(f"/api/projects/{pid}/assets", json={"name": "k", "category": "humanoid", "style_preset": "painted",
                                                         "prompt": "a knight"}).json()
        c.post(f"/api/assets/{a['id']}/autopilot", json={})
        state = {}
        for _ in range(100):
            state = c.get(f"/api/assets/{a['id']}").json()["autopilot"] or {}
            if state.get("status") == "error":
                break
            time.sleep(0.1)
        assert state.get("status") == "error"
        assert "Could not start the step" in state["reason"] and "does not know where ComfyUI" in state["reason"]
        assert c.get("/api/jobs").json() == []
