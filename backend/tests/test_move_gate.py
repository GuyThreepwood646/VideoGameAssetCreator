"""The start gate for the big-files move (vgac/move_gate.py): the backend refuses to start while a move is unfinished or unreadable."""

from pathlib import Path

import pytest

from vgac import move_gate


def _local(root: Path) -> Path:
    local = root / "local"
    local.mkdir(parents=True, exist_ok=True)
    return local


def test_clear_when_no_move_record(tmp_path):
    assert move_gate.blocking_reason(tmp_path) is None


def test_unfinished_move_record_blocks(tmp_path):
    (_local(tmp_path) / "move.json").write_text('{"state": "copying"}', encoding="utf-8")
    reason = move_gate.blocking_reason(tmp_path)
    assert reason is not None and "not finished" in reason


def test_unjudgeable_move_record_still_blocks(tmp_path):
    # The backend does not parse the record: a file that exists at all means a move is unfinished or cannot be judged.
    (_local(tmp_path) / "move.json").write_text("not json at all", encoding="utf-8")
    assert move_gate.blocking_reason(tmp_path) is not None


def test_switched_move_needing_restart_blocks(tmp_path):
    (_local(tmp_path) / "restart-needed.json").write_text("{}", encoding="utf-8")
    reason = move_gate.blocking_reason(tmp_path)
    assert reason is not None and "restarted" in reason


def test_unreadable_state_refuses_and_says_why(tmp_path, monkeypatch):
    def boom(self):
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "exists", boom)
    reason = move_gate.blocking_reason(tmp_path)
    assert reason is not None and "cannot tell" in reason and "access denied" in reason


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(move_gate, "PROJECT_DIR", tmp_path)
    assert move_gate.main([]) == 0
    (_local(tmp_path) / "move.json").write_text("{}", encoding="utf-8")
    assert move_gate.main([]) == 1
    assert "not finished" in capsys.readouterr().err


def test_restart_marker_takes_precedence_message(tmp_path):
    local = _local(tmp_path)
    (local / "move.json").write_text("{}", encoding="utf-8")
    (local / "restart-needed.json").write_text("{}", encoding="utf-8")
    assert "restarted" in move_gate.blocking_reason(tmp_path)
