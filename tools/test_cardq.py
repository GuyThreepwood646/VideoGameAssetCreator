"""Unit tests for tools/cardq.py with HTTP, process and memory checks stubbed.

Run: python -m unittest tools.test_cardq  (from the repo root)
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cardq  # noqa: E402


class FakeWorld:
    """Stub for http_json: per-card queues, app jobs, recorded POSTs."""

    def __init__(self):
        self.queue = {8188: ([], []), 8189: ([], [])}
        self.jobs = {"running": [], "queued": []}
        self.posts = []
        self.down = set()

    def __call__(self, url, method="GET", body=None, timeout=10):
        if method == "POST":
            self.posts.append((url, body))
            return None
        if "/api/jobs" in url:
            return list(self.jobs[url.split("status=")[1]])
        port = int(url.split(":")[2].split("/")[0])
        if port in self.down:
            raise OSError("down")
        run, pend = self.queue[port]
        return {"queue_running": run, "queue_pending": pend}


class CardqTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"CARDQ_ROOT": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.world = FakeWorld()
        self.alive = set()
        self.sleeps = []
        self.mem = [64.0]
        patches = [
            mock.patch.object(cardq, "http_json", self.world),
            mock.patch.object(cardq, "pid_alive", lambda pid: pid in self.alive),
            mock.patch.object(cardq, "sleep", self._sleep),
            mock.patch.object(cardq, "free_memory_gb", lambda: self.mem[0]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.on_sleep = None

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        if len(self.sleeps) > 50:
            raise AssertionError("waited too long")
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))

    def run_cli(self, *argv):
        return cardq.main(["run", "--session", "t", "--minutes", "5", *argv])

    def claims(self):
        p = cardq.claims_path()
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_free_card_runs_claims_locks_and_frees(self):
        seen = {}

        def fake_run(cmd):
            seen["lock"] = cardq.read_lock("A")
            return SimpleNamespace(returncode=3)

        with mock.patch.object(cardq.subprocess, "run", fake_run):
            rc = self.run_cli("--card", "A", "--", "echo", "hi")
        self.assertEqual(rc, 3)
        self.assertEqual(seen["lock"]["pid"], os.getpid())
        self.assertIsNone(cardq.read_lock("A"))
        claim = self.claims()[0]
        self.assertEqual((claim["session"], claim["card"], claim["expected_min"]), ("t", 8188, 5.0))
        self.assertIn("echo hi", claim["note"])
        self.assertEqual(self.world.posts,
                         [("http://127.0.0.1:8188/free", {"unload_models": True, "free_memory": True})])

    def test_waits_while_card_queue_busy(self):
        self.world.queue[8188] = ([["x"]], [])

        def clear(n):
            if n == 3:
                self.world.queue[8188] = ([], [])

        self.on_sleep = clear
        with mock.patch.object(cardq.subprocess, "run", lambda c: SimpleNamespace(returncode=0)):
            self.run_cli("--card", "A", "--", "x")
        self.assertEqual(len(self.sleeps), 3)
        self.assertTrue(all(s >= 15 for s in self.sleeps))

    def test_waits_on_app_job_for_that_card_only(self):
        self.world.jobs["running"] = [{"instance": "a"}]
        self.assertIsNotNone(cardq.card_busy_reason("A", cardq.app_jobs()))
        self.assertIsNone(cardq.card_busy_reason("B", cardq.app_jobs()))
        self.world.jobs = {"running": [], "queued": [{"instance": "b"}]}
        self.assertIsNotNone(cardq.card_busy_reason("B", cardq.app_jobs()))

    def test_any_takes_whichever_frees(self):
        self.world.queue[8188] = ([["x"]], [])
        with mock.patch.object(cardq.subprocess, "run", lambda c: SimpleNamespace(returncode=0)):
            self.run_cli("--card", "any", "--", "x")
        self.assertEqual(self.claims()[0]["card"], 8189)
        self.assertEqual(self.sleeps, [])

    def test_quiet_holds_both_and_waits_for_both(self):
        self.world.queue[8189] = ([], [["p"]])

        def clear(n):
            self.world.queue[8189] = ([], [])

        self.on_sleep = clear
        held = {}

        def fake_run(cmd):
            held["A"], held["B"] = cardq.read_lock("A"), cardq.read_lock("B")
            return SimpleNamespace(returncode=0)

        with mock.patch.object(cardq.subprocess, "run", fake_run):
            self.run_cli("--card", "A", "--quiet", "--", "x")
        self.assertTrue(held["A"] and held["B"])
        self.assertEqual(len(self.claims()), 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_live_lock_blocks_stale_lock_is_reclaimed(self):
        cardq.take_lock("A", "other", ["x"])
        info = cardq.read_lock("A")
        self.alive = {info["pid"]}
        self.assertIn("locked by other", cardq.card_busy_reason("A", cardq.app_jobs()))
        self.alive = set()
        self.assertIsNone(cardq.card_busy_reason("A", cardq.app_jobs()))
        self.assertIsNone(cardq.read_lock("A"))

    def test_no_free_when_queue_not_empty_after_run(self):
        def fake_run(cmd):
            self.world.queue[8188] = ([], [["other"]])
            return SimpleNamespace(returncode=0)

        with mock.patch.object(cardq.subprocess, "run", fake_run):
            self.run_cli("--card", "A", "--", "x")
        self.assertEqual(self.world.posts, [])

    def test_waits_for_memory(self):
        self.mem[0] = 8.0

        def more(n):
            self.mem[0] = 20.0

        self.on_sleep = more
        with mock.patch.object(cardq.subprocess, "run", lambda c: SimpleNamespace(returncode=0)):
            self.run_cli("--card", "B", "--", "x")
        self.assertEqual(len(self.sleeps), 1)

    def test_long_wait_prints_and_keeps_waiting(self):
        self.world.queue[8188] = ([["x"]], [])
        clock = [cardq.now()]

        def fake_now():
            return clock[0]

        def tick(n):
            clock[0] = clock[0] + __import__("datetime").timedelta(minutes=20)
            if n == 4:
                self.world.queue[8188] = ([], [])

        self.on_sleep = tick
        with mock.patch.object(cardq, "now", fake_now), \
                mock.patch.object(cardq.subprocess, "run", lambda c: SimpleNamespace(returncode=0)), \
                mock.patch.object(cardq, "log") as lg:
            self.run_cli("--card", "A", "--", "x")
        self.assertTrue(any("still waiting" in c.args[0] for c in lg.call_args_list))
        self.assertEqual(self.world.posts and len(self.world.posts), 1)

    def test_never_interrupts(self):
        self.world.queue[8188] = ([["x"]], [])
        self.on_sleep = lambda n: self.world.queue.__setitem__(8188, ([], [])) if n == 2 else None
        with mock.patch.object(cardq.subprocess, "run", lambda c: SimpleNamespace(returncode=0)):
            self.run_cli("--card", "A", "--", "x")
        self.assertFalse(any("interrupt" in u or "queue" in u for u, _ in self.world.posts))

    def test_status_is_read_only(self):
        with mock.patch("builtins.print") as pr:
            self.assertEqual(cardq.main(["status"]), 0)
        self.assertEqual(pr.call_count, 2)
        self.assertEqual(self.world.posts, [])


if __name__ == "__main__":
    unittest.main()
