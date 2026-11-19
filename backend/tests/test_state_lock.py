"""The shared lock for local/machine.json and local/installed.json (vgac/state_lock.py) and the unique temporary names."""

import os
import time
from pathlib import Path

import pytest

from vgac import state_lock
from vgac.state_lock import LOCK_NAME, StateLockBusy, state_lock as hold, unique_tmp


def test_lock_is_taken_and_released(tmp_path):
    with hold(tmp_path):
        assert (tmp_path / LOCK_NAME).exists()
    assert not (tmp_path / LOCK_NAME).exists()


def test_lock_released_even_when_the_body_fails(tmp_path):
    with pytest.raises(ValueError):
        with hold(tmp_path):
            raise ValueError("boom")
    assert not (tmp_path / LOCK_NAME).exists()


def test_held_lock_refuses_after_the_wait_and_keeps_the_other_holders_lock(tmp_path):
    (tmp_path / LOCK_NAME).write_text("someone else", encoding="utf-8")
    with pytest.raises(StateLockBusy):
        with hold(tmp_path, wait_seconds=0.2):
            pytest.fail("the body must not run while the lock is held elsewhere")
    assert (tmp_path / LOCK_NAME).read_text(encoding="utf-8") == "someone else"


def test_stale_lock_from_a_dead_process_is_broken(tmp_path):
    lock = tmp_path / LOCK_NAME
    lock.write_text("dead", encoding="utf-8")
    old = time.time() - (state_lock.STALE_SECONDS + 60)
    os.utime(lock, (old, old))
    ran = []
    with hold(tmp_path, wait_seconds=1):
        ran.append(True)
    assert ran and not lock.exists()


def test_unique_tmp_names_differ_and_are_beside_the_file(tmp_path):
    target = tmp_path / "installed.json"
    a, b = unique_tmp(target), unique_tmp(target)
    assert a != b and a.parent == tmp_path and a.name.startswith("installed.json.") and a.name.endswith(".tmp")
    assert a.name != "installed.json.tmp"


def test_record_in_downloader_takes_the_lock(tmp_path, monkeypatch):
    # downloader.record writes installed.json under the lock: while it is held elsewhere, nothing is written.
    from vgac import downloader

    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(downloader, "ledger_path", lambda: local / "installed.json")
    (local / LOCK_NAME).write_text("other", encoding="utf-8")
    orig = state_lock.state_lock

    def short_wait(d, wait_seconds=0.2):
        return orig(d, wait_seconds=0.2)

    monkeypatch.setattr(downloader, "state_lock", short_wait)
    with pytest.raises(StateLockBusy):
        downloader.record("test", tmp_path / "x", "added", 0)
    assert not (local / "installed.json").exists()
