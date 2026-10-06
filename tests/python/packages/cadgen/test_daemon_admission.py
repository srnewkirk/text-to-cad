"""Request cancellation through admission and coalescing, without CAD imports."""

import json
import threading
import unittest
from unittest import mock

from cadgen.daemon import pool, server
from cadgen.daemon.jobs import JobLedger


class _Channel:
    def __init__(self):
        self.disconnected = threading.Event()
        self.frames = []

    def send(self, raw):
        if self.disconnected.is_set():
            raise OSError("client gone")
        self.frames.append(json.loads(raw.decode("utf-8")))


class AdmissionCancellation(unittest.TestCase):
    def setUp(self):
        self.channel = _Channel()
        self.request = {"tool": "run", "argv": ["child.py"], "cwd": "/w",
                        "closure": "hash", "coalesce": True}
        self.pool = mock.Mock()
        self.broker = mock.Mock()
        self.broker.claim.return_value = None
        self.broker.snapshot.return_value = {"running": 1}
        self.ledger = mock.Mock()
        for target, value in (("_POOL", self.pool), ("_BROKER", self.broker),
                              ("_JOBS", self.ledger), ("CLIENT_LIVENESS_INTERVAL_SECONDS", 0.005)):
            self.enterContext(mock.patch.object(server, target, value))
        self.enterContext(mock.patch.object(server, "_log"))

    def _run(self):
        errors = []
        def request():
            try:
                server._handle_request(self.channel, self.request)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        return thread, errors

    def test_disconnect_cancels_admission_and_finishes_owned_claim(self):
        entered = threading.Event()
        def acquire(model, *, cancelled, on_wait, productive):
            self.assertTrue(productive())
            on_wait("resident worker limit")
            entered.set()
            deadline = threading.Event()
            for _ in range(100):
                if cancelled():
                    raise pool.AdmissionCancelled("client disconnected")
                deadline.wait(0.005)
            self.fail("admission cancellation was not observed")
        self.pool.acquire.side_effect = acquire
        thread, errors = self._run()
        self.assertTrue(entered.wait(1))
        self.channel.disconnected.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.pool.release.assert_not_called()
        self.broker.finish.assert_called_once()
        self.ledger.finish.assert_called_once()
        self.assertTrue(any("waiting for worker admission" in f.get("data", "") for f in self.channel.frames))

    def test_disconnect_before_attachment_releases_unstarted_worker(self):
        worker = mock.Mock(pid=77)
        def acquire(model, *, cancelled, **kwargs):
            self.channel.disconnected.set()
            for _ in range(100):
                if cancelled():
                    return worker
                threading.Event().wait(0.005)
            self.fail("disconnect was not observed")
        self.pool.acquire.side_effect = acquire
        thread, errors = self._run()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        worker.send.assert_not_called()
        worker.kill.assert_not_called()
        self.pool.release.assert_called_once_with(worker, healthy=True)
        self.broker.finish.assert_called_once()

    def test_coalesced_follower_cancellation_does_not_finish_producer(self):
        claimed = threading.Event()
        entry = {"done": threading.Event(), "exit": None}
        def claim(*args):
            claimed.set()
            return entry
        self.broker.claim.side_effect = claim
        thread, errors = self._run()
        self.assertTrue(claimed.wait(1))
        self.channel.disconnected.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.pool.acquire.assert_not_called()
        self.pool.release.assert_not_called()
        self.broker.finish.assert_not_called()
        self.assertFalse(entry["done"].is_set())
        self.ledger.start.assert_not_called()
        self.ledger.finish.assert_not_called()

    def test_cancelled_follower_preserves_real_producer_ledger(self):
        ledger = JobLedger()
        subject = server._script_path(self.request["argv"], self.request["cwd"])
        producer = ledger.start(tool="run", subject=subject, argv=self.request["argv"])
        ledger.observe({"event": {"model": subject, "state": "building"}})
        claimed = threading.Event()
        entry = {"done": threading.Event(), "exit": None}
        def claim(*args):
            claimed.set()
            return entry
        self.broker.claim.side_effect = claim
        with mock.patch.object(server, "_JOBS", ledger):
            thread, errors = self._run()
            self.assertTrue(claimed.wait(1))
            self.channel.disconnected.set()
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        rows = ledger.snapshot()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], producer["id"])
        self.assertEqual(rows[0]["state"], "building")
        self.assertIsNone(rows[0]["exit"])
        self.assertIsNone(rows[0]["finishedAt"])
        self.broker.finish.assert_not_called()

    def test_finished_job_stops_watchdog_before_worker_release(self):
        worker = mock.Mock(pid=77)
        worker.frames.return_value = iter([{"exit": 0}])
        worker.alive.return_value = True
        self.pool.acquire.return_value = worker
        def release(*args, **kwargs):
            self.channel.disconnected.set()
        self.pool.release.side_effect = release
        server._handle_request(self.channel, self.request)
        worker.kill.assert_not_called()
        self.broker.finish.assert_called_once()
        self.ledger.finish.assert_called_once()

    def test_probe_finishing_after_stop_cannot_kill_detached_worker(self):
        entered, resume = threading.Event(), threading.Event()
        channel = mock.Mock()
        def send(raw):
            entered.set()
            self.assertTrue(resume.wait(1))
            raise OSError("late disconnect")
        channel.send.side_effect = send
        worker = mock.Mock(pid=77)
        liveness = server._RequestLiveness(channel, threading.Lock(), "run")
        self.assertTrue(liveness.attach(worker))
        liveness.thread.start()
        self.assertTrue(entered.wait(1))
        try:
            # Model join timing out while a transport probe is still finishing.
            with mock.patch.object(liveness.thread, "join", return_value=None):
                liveness.stop()
            self.assertIsNone(liveness.worker)
        finally:
            resume.set()
            liveness.thread.join(1)
        self.assertFalse(liveness.thread.is_alive())
        worker.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
