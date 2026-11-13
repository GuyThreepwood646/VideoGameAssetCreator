"""Open folder brings Explorer to the front (vgac/folders.py): which Explorer window shows the folder."""

from __future__ import annotations

from pathlib import Path

from vgac import folders

F = Path(r"D:\Games\VGAC\exports\Hollow Keep\Knight")


def test_title_matches_the_folder_name_the_full_path_and_the_explorer_suffix():
    assert folders.title_matches("Knight", F)
    assert folders.title_matches(str(F), F)
    assert folders.title_matches(str(F) + " - File Explorer", F)
    assert folders.title_matches("knight - File Explorer", F)
    assert not folders.title_matches("Knight2", F)
    assert not folders.title_matches("Downloads", F)


def test_pick_window_prefers_the_new_window_then_one_already_open():
    windows = {1: "Knight", 2: "Downloads", 3: "Knight"}
    assert folders.pick_window(windows, F, before={1, 2}) == 3
    assert folders.pick_window(windows, F, before={1, 2, 3}) in (1, 3)
    assert folders.pick_window({2: "Downloads"}, F, before=set()) is None


def test_open_folder_endpoints_pass_the_front_result_on(settings, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from vgac import user_addons
    from vgac.main import app

    monkeypatch.setattr(settings, "loras_dir", tmp_path / "loras")  # never this PC's own ComfyUI folder
    monkeypatch.setattr(folders, "open_in_explorer", lambda p: {"front": False})
    with TestClient(app) as c:
        r = c.post("/api/my-addons/open-folder")
        assert r.status_code == 200 and r.json()["front"] is False and r.json()["opened"] is True
    assert user_addons._launch_explorer(Path(".")) == {"front": False}
