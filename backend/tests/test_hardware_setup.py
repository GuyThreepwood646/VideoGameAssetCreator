"""The hardware plan (hardware_setup.py): the proposal for made-up machines, the reading from fake tool output, and
the accept that writes through the validated writer. Every machine file is a temporary one."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from vgac import config, hardware_setup as hs, setup_settings

# The owner's real file, read only: in this project folder, or (a worktree) in the main checkout above it.
REAL_MACHINE = next((p / "local" / "machine.json" for p in Path(__file__).resolve().parents
                     if (p / "local" / "machine.json").is_file()), Path("missing"))


def card(i: int, name: str, gb: float, bus: str | None = None) -> hs.Card:
    return hs.Card(i, f"NVIDIA GeForce {name}", int(gb * 1024), bus or f"00000000:{i + 1:02X}:00.0")


def machine(cards: list[hs.Card], ram: float = 64, free: float = 40, other: list[str] | None = None) -> hs.Hardware:
    return hs.Hardware(cards=cards, ram_total_gb=ram, ram_free_gb=free, other_cards=other or [])


def texts(plan: hs.Plan, level: str | None = None) -> str:
    return " ".join(m["text"] for m in plan.messages if level in (None, m["level"]))


# --- reading ---------------------------------------------------------------------------------------------------------


def test_parse_smi_orders_by_board_slot_and_numbers_from_zero():
    text = ("0, NVIDIA GeForce RTX 3080, 10240, 00000000:01:00.0\n"
            "1, NVIDIA GeForce RTX 3060, 12288, 00000000:08:00.0\n")
    cards = hs.parse_smi(text)
    assert [(c.index, hs.short_name(c.name), c.memory_mb) for c in cards] == [(0, "RTX 3080", 10240), (1, "RTX 3060", 12288)]
    # Given in another order, the card on the lower slot is still number 0 (CUDA_DEVICE_ORDER=PCI_BUS_ID).
    swapped = hs.parse_smi("5, NVIDIA GeForce RTX 3060, 12288 MiB, 00000000:08:00.0\n"
                           "7, NVIDIA GeForce RTX 3080, 10240 MiB, 00000000:01:00.0")
    assert [hs.short_name(c.name) for c in swapped] == ["RTX 3080", "RTX 3060"]
    assert hs.parse_smi("garbage\n,,\n") == []


def test_names():
    assert hs.short_name("NVIDIA GeForce RTX 3060") == "RTX 3060"
    assert hs.match_word("NVIDIA GeForce RTX 3060") == "3060"
    assert hs.match_word("NVIDIA RTX A4000") == "4000"


def test_read_hardware_with_fakes_and_without_nvidia_tools():
    def run(cmd, timeout):
        if cmd[0] == "nvidia-smi":
            return "0, NVIDIA GeForce RTX 3090, 24576, 00000000:01:00.0\n"
        raise AssertionError(cmd)

    hw = hs.read_hardware(run, lambda: (64.0, 30.0))
    assert len(hw.cards) == 1 and hw.ram_total_gb == 64 and hw.smi_problem == ""

    def none(cmd, timeout):
        if cmd[0] == "nvidia-smi":
            raise FileNotFoundError(cmd[0])
        return "Intel(R) UHD Graphics\nAMD Radeon RX 6600\n"

    hw = hs.read_hardware(none, lambda: (16.0, 8.0))
    assert hw.cards == [] and "NVIDIA" in hw.smi_problem and hw.other_cards == ["Intel(R) UHD Graphics", "AMD Radeon RX 6600"]


# --- the proposal ----------------------------------------------------------------------------------------------------


def test_owner_two_cards_12_and_10():
    hw = machine([card(0, "RTX 3080", 10), card(1, "RTX 3060", 12)], ram=95.8)
    p = hs.propose(hw)
    assert p.status == "ok"
    by = {e["name"]: e for e in p.engines}
    assert by["a"]["gpu"] == "RTX 3060" and by["a"]["card"] == 1 and by["a"]["role"] == "3d"
    assert by["b"]["gpu"] == "RTX 3080" and by["b"]["card"] == 0
    assert p.patch["stage_instances"]["mesh"] == "a" and p.patch["stage_instances"]["rig"] == "b"
    assert p.patch["instances"]["a"]["discover"] == {"gpu_match": "3060"}
    assert p.patch["instances"]["a"]["engine"] == {"cuda_device": 1} and p.patch["instances"]["b"]["engine"] == {"cuda_device": 0}
    assert p.min_free_ram_gb == 24
    assert "Two graphics cards" in texts(p) and "12 GB card" in texts(p)


def test_user_can_give_the_3d_step_to_the_smaller_card_and_is_warned():
    hw = machine([card(0, "RTX 3080", 10), card(1, "RTX 3060", 12)])
    p = hs.propose(hw, three_d=0)
    by = {e["name"]: e for e in p.engines}
    assert by["a"]["card"] == 0 and by["b"]["card"] == 1
    assert "smaller card" in texts(p, "warn")


def test_one_card_12gb():
    p = hs.propose(machine([card(0, "RTX 3060", 12)]))
    assert p.status == "ok" and len(p.engines) == 1
    assert set(p.patch["stage_instances"].values()) == {"a", "host"}
    assert p.patch["instances"]["b"] is None and list(p.patch["instance_capabilities"]) == ["a"]
    assert p.patch["judge_instance"] == "a"
    assert "One graphics card" in texts(p) and "one at a time" in texts(p)
    assert "not yet on a single card" in texts(p, "info")


def test_one_card_8gb_warns_plainly():
    p = hs.propose(machine([card(0, "RTX 3070", 8)]))
    assert p.status == "warn" and p.engines
    assert "8 GB of graphics memory is below what VGAC was tested with" in texts(p, "warn")
    assert "nothing is harmed" in texts(p, "warn")
    # A card that reports a little under 8 GB (8188 MiB is common) still counts as an 8 GB card.
    q = hs.propose(machine([hs.Card(0, "NVIDIA GeForce RTX 3070", 8188, "b")]))
    assert q.engines and q.status == "warn"


def test_one_card_10gb_has_the_tested_wording():
    p = hs.propose(machine([card(0, "RTX 3080", 10)]))
    assert p.status == "ok" and "10 GB and 12 GB cards" in texts(p)


def test_one_card_24gb():
    p = hs.propose(machine([card(0, "RTX 4090", 24)], ram=64))
    assert p.status == "ok" and p.engines == [{"name": "a", "card": 0, "gpu": "RTX 4090", "memory_gb": 24.0, "role": "3d",
                                               "steps": "every step"}]
    assert p.min_free_ram_gb == 16


def test_three_cards_use_the_two_largest():
    p = hs.propose(machine([card(0, "RTX 3060", 12), card(1, "RTX 4090", 24), card(2, "RTX 3070", 8)]))
    by = {e["name"]: e for e in p.engines}
    assert by["a"]["card"] == 1 and by["b"]["card"] == 0 and p.ignored == [2]
    assert "two graphics cards at most" in texts(p)


def test_second_card_too_small_is_left_out():
    p = hs.propose(machine([card(0, "RTX 3060", 12), card(1, "GTX 1650", 4)]))
    assert len(p.engines) == 1 and p.engines[0]["card"] == 0
    assert "GTX 1650" in texts(p, "warn") and "too little" in texts(p, "warn")


def test_person_can_choose_one_card_of_two():
    p = hs.propose(machine([card(0, "RTX 3080", 10), card(1, "RTX 3060", 12)]), use=[0])
    assert [e["card"] for e in p.engines] == [0] and p.patch["instances"]["b"] is None


def test_two_identical_cards_have_no_name_match():
    p = hs.propose(machine([card(0, "RTX 3060", 12), card(1, "RTX 3060", 12)]))
    assert p.patch["instances"]["a"]["discover"] is None and p.patch["instances"]["b"]["discover"] is None
    assert {e["card"] for e in p.engines} == {0, 1}


def test_low_system_memory():
    p = hs.propose(machine([card(0, "RTX 3060", 12)], ram=24))
    assert "less than 32 GB of memory, large steps will be slow and may fail" in texts(p, "warn")
    assert p.min_free_ram_gb == 8 and p.status == "warn"
    q = hs.propose(machine([card(0, "RTX 3060", 12)], ram=12))
    assert "probably" in texts(q, "warn") and q.min_free_ram_gb == 6  # never more than half of what there is
    z = hs.propose(machine([card(0, "RTX 3060", 12)], ram=16))
    assert z.min_free_ram_gb == 8
    assert hs.propose(machine([card(0, "RTX 3060", 12)], ram=0)).min_free_ram_gb == 24
    assert hs.propose(machine([card(0, "RTX 3060", 12)], ram=64), min_free_ram_gb=20).min_free_ram_gb == 20


def test_no_nvidia_card():
    p = hs.propose(machine([], other=["AMD Radeon RX 6600"]))
    assert p.status == "cannot" and p.engines == []
    assert "NVIDIA graphics card with at least 8 GB" in texts(p, "bad") and "AMD Radeon RX 6600" in texts(p, "bad")
    assert "look at it" in texts(p, "bad")
    assert hs.propose(machine([])).status == "cannot"


def test_only_a_small_card():
    p = hs.propose(machine([card(0, "GTX 1650", 4)]))
    assert p.status == "cannot" and "GTX 1650 with 4 GB" in texts(p, "bad")


def test_no_jargon_in_messages():
    cases = [machine([card(0, "RTX 3060", 12)], ram=16), machine([card(0, "RTX 3070", 8)]), machine([]),
             machine([card(0, "RTX 3080", 10), card(1, "RTX 3060", 12)])]
    for hw in cases:
        p = hs.propose(hw)
        t = texts(p) + " ".join(p.explain)
        for bad in ("VRAM", "CUDA", "OOM", "PCI", "nvidia-smi", "cuda_device"):
            assert bad not in t


# --- the settings file -----------------------------------------------------------------------------------------------


OWNER = {
    "schema": 1,
    "backend": {
        "instances": {
            "a": {"gpu": "RTX 3060", "process_match": "--cuda-device 1", "external_owner": "ComfyUI Desktop",
                  "discover": {"gpu_match": "3060", "ports": [8188, 8189, 8190, 8191, 8000]},
                  "engine": {"comfy_dir": "{c}", "cuda_device": 1, "user_dir": "runtime/comfy-a-user",
                             "output_dir": "runtime/comfy-a-output"}},
            "b": {"gpu": "RTX 3080", "process_match": "ComfyUI\\main.py --port 8189",
                  "discover": {"gpu_match": "3080", "ports": []},
                  "engine": {"comfy_dir": "{c}", "cuda_device": 0, "user_dir": "runtime/comfy-b-user"}},
        },
        "blender_exe": "F:/Tools/blender/blender.exe",
    },
    "desktop": {"comfyA": {"name": "ComfyUI A"}},
}


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = {"instances": {"a": {"url": "http://127.0.0.1:59981", "role": "3d"},
                         "b": {"url": "http://127.0.0.1:59982", "role": "images", "output_dir": "runtime/comfy-b-output"}},
           "library_path": str(tmp_path / "library"), "min_free_ram_gb": 24}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    machine_file = tmp_path / "local" / "machine.json"
    monkeypatch.setenv("VGAC_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("VGAC_MACHINE", str(machine_file))
    config.reset_settings()
    yield tmp_path, machine_file
    config.reset_settings()


def write_owner(box_) -> Path:
    tmp, mf = box_
    text = json.dumps(OWNER).replace("{c}", str(tmp / "comfy").replace("\\", "/"))
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text(text, encoding="utf-8")
    config.reset_settings()
    return mf


OWNER_HW = machine([card(0, "RTX 3080", 10), card(1, "RTX 3060", 12)], ram=95.8)


def layout() -> dict:
    return hs.current_layout(config.load_settings(create_library=False))


def test_propose_writes_nothing(box):
    mf = write_owner(box)
    before = mf.read_bytes()
    out = hs.describe(hw=OWNER_HW)
    assert mf.read_bytes() == before and not mf.with_name("machine.json.bak").exists()
    assert out["plan"]["status"] == "ok" and out["can_accept"]


def test_owner_accept_reproduces_the_current_layout(box):
    mf = write_owner(box)
    before = layout()
    raw_before = json.loads(mf.read_text(encoding="utf-8"))
    assert hs.describe(hw=OWNER_HW)["already"] is True
    out = hs.accept(hw=OWNER_HW)
    assert out["changed"] is False
    assert layout() == before
    raw = json.loads(mf.read_text(encoding="utf-8"))
    # The engine, the card numbers and every other setting of the owner's file are untouched.
    for name in ("a", "b"):
        for key in ("engine", "gpu", "discover", "process_match"):
            assert raw["backend"]["instances"][name][key] == raw_before["backend"]["instances"][name][key]
    assert raw["backend"]["blender_exe"] == raw_before["backend"]["blender_exe"] and raw["desktop"] == raw_before["desktop"]
    assert raw["backend"]["stage_instances"]["mesh"] == "a" and raw["backend"]["min_free_ram_gb"] == 24


@pytest.mark.skipif(not REAL_MACHINE.is_file(), reason="no local/machine.json on this machine")
def test_owner_real_file_copy_reproduces(box):
    """A temporary copy of the real file; the real one is never written."""
    tmp, mf = box
    mf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REAL_MACHINE, mf)
    config.reset_settings()
    before = layout()
    real_bytes = REAL_MACHINE.read_bytes()
    if [e["name"] for e in before["engines"]] != ["a", "b"] or before["engines"][0]["card"] != 1:
        pytest.skip("the real file is not the two-card layout of the owner's machine")
    out = hs.accept(hw=OWNER_HW)
    assert out["changed"] is False and layout() == before
    assert REAL_MACHINE.read_bytes() == real_bytes


def test_accepting_one_card_removes_the_second_engine_and_validates(box):
    mf = write_owner(box)
    out = hs.accept(hw=machine([card(0, "RTX 3060", 12)]))
    assert out["changed"] and "second card" in out["note"]
    s = config.load_settings(create_library=False)
    assert list(s.instances) == ["a"] and s.judge_instance == "a" and s.stage_instances["rig"] == "a"
    assert s.instances["a"].engine.cuda_device == 0 and s.instances["a"].gpu == "RTX 3060"
    assert mf.with_name("machine.json.bak").is_file()


def test_accept_on_a_blank_machine_names_the_cards_without_inventing_engines(box):
    out = hs.accept(hw=OWNER_HW)
    s = config.load_settings(create_library=False)
    assert s.instances["a"].gpu == "RTX 3060" and s.instances["a"].discover.gpu_match == "3060"
    assert s.instances["a"].engine is None and s.instances["b"].engine is None
    assert out["changed"]


def test_accept_refuses_when_nothing_can_run_and_writes_nothing(box):
    mf = write_owner(box)
    before = mf.read_bytes()
    with pytest.raises(setup_settings.SettingError, match="no graphics card"):
        hs.accept(hw=machine([]))
    with pytest.raises(setup_settings.SettingError, match="between 0"):
        hs.accept(min_free_ram_gb=500, hw=OWNER_HW)
    assert mf.read_bytes() == before


def test_accept_refused_while_the_settings_file_is_broken(box):
    tmp, mf = box
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text("{ not json", encoding="utf-8")
    config.reset_settings()
    with pytest.raises(setup_settings.SettingError):
        hs.accept(hw=OWNER_HW)
    assert mf.read_text(encoding="utf-8") == "{ not json"


def test_http_routes(box, monkeypatch):
    from fastapi.testclient import TestClient
    from vgac.main import app

    write_owner(box)
    monkeypatch.setattr(hs, "read_hardware", lambda *a, **k: OWNER_HW)
    c = TestClient(app)
    r = c.get("/api/hardware", params={"use": "0,1", "three_d": 1})
    assert r.status_code == 200 and r.json()["plan"]["engines"][0]["gpu"] == "RTX 3060"
    assert c.get("/api/hardware", params={"use": "x"}).status_code == 400
    r = c.post("/api/hardware/accept", json={})
    assert r.status_code == 200 and r.json()["changed"] is False
    monkeypatch.setattr(hs, "read_hardware", lambda *a, **k: machine([]))
    assert c.post("/api/hardware/accept", json={}).status_code == 400
