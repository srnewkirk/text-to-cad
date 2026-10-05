"""The pool's dispatch rule: a worker per model, an extra when it is busy, spares in reserve.

Worker admission is bounded and never waits on a build: the rule is bookkeeping, so it is
asserted against stub workers on identity and state, never on timing.
"""

from __future__ import annotations

import concurrent.futures
import os
import pathlib
import sys
import time
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from cadgen.daemon import pool as pool_mod  # noqa: E402
from cadgen._internal import runtime_limits  # noqa: E402


class _StubWorker:
    """Stands in for a subprocess: the dispatch rule is about bookkeeping, not OCP."""

    _next_pid = 1000
    spawned = 0

    def __init__(self) -> None:
        _StubWorker._next_pid += 1
        _StubWorker.spawned += 1
        self.pid = _StubWorker._next_pid
        self.busy = False
        self.extra = False
        self.model = ""
        self.jobs_served = 0
        self.last_used = 0.0
        self.use_seq = next(pool_mod._USE_SEQUENCE)
        self.killed = False
        self._alive = True

    def alive(self) -> bool:
        return self._alive

    def kill(self) -> None:
        self.killed = True
        self._alive = False


def _settle(pool: pool_mod.Pool, timeout: float = 5.0) -> None:
    """Wait for the background spare refill to land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pool.snapshot()["sparesPending"] == 0:
            return
        time.sleep(0.01)
    raise AssertionError("spare refill never settled")


class _PoolFixture(unittest.TestCase):
    def setUp(self) -> None:
        memory = mock.patch.object(runtime_limits, "_windows_available_memory", return_value=None)
        memory.start()
        self.addCleanup(memory.stop)
        limit = mock.patch.dict(os.environ, {"CADGEN_DAEMON_MAX_WORKERS": "128"})
        limit.start()
        self.addCleanup(limit.stop)
        patcher = mock.patch.object(pool_mod, "Worker", _StubWorker)
        patcher.start()
        self.addCleanup(patcher.stop)
        _StubWorker.spawned = 0
        self.pool = pool_mod.Pool()
        self.addCleanup(self.pool.shutdown)

    def _spares(self, count: int):
        return mock.patch.dict(os.environ, {"CADGEN_DAEMON_SPARES": str(count)})


class Binding(_PoolFixture):
    def test_a_model_binds_a_worker_and_keeps_it(self):
        with self._spares(0):
            first = self.pool.acquire("/m/a.py")
            self.pool.release(first)
            again = self.pool.acquire("/m/a.py")
        self.assertIs(first, again, "sequential builds of one model must reuse its worker")
        self.assertEqual(first.model, "/m/a.py")
        self.assertFalse(first.extra)
        self.pool.release(again)
        self.assertEqual(again.jobs_served, 2)

    def test_two_models_never_share_a_worker(self):
        with self._spares(0):
            a = self.pool.acquire("/m/a.py")
            self.pool.release(a)
            b = self.pool.acquire("/m/b.py")
        self.assertIsNot(a, b)
        self.assertEqual({a.model, b.model}, {"/m/a.py", "/m/b.py"})
        self.pool.release(b)

    def test_a_busy_model_gets_an_extra_and_nobody_waits(self):
        with self._spares(0):
            primary = self.pool.acquire("/m/a.py")
            extra = self.pool.acquire("/m/a.py")
        self.assertIsNot(primary, extra)
        self.assertTrue(extra.extra)
        self.assertEqual(extra.model, "/m/a.py")
        self.assertEqual(self.pool.snapshot()["concurrent"], 1)
        self.pool.release(extra)
        self.pool.release(primary)

    def test_an_extra_returns_to_the_spare_set_when_its_job_ends(self):
        with self._spares(1):
            primary = self.pool.acquire("/m/a.py")
            _settle(self.pool)
            extra = self.pool.acquire("/m/a.py")
            _settle(self.pool)
            self.pool.release(extra)
            _settle(self.pool)
            snapshot = self.pool.snapshot()
        spares = [w for w in snapshot["workers"] if not w["model"]]
        self.assertEqual(len(spares), 1, snapshot)
        self.assertTrue(extra.killed or extra.model == "", "the extra neither returned nor left")
        self.pool.release(primary)

    def test_a_request_with_no_model_borrows_a_spare_without_binding_it(self):
        with self._spares(0):
            worker = self.pool.acquire("")
            self.assertEqual(worker.model, "")
            self.pool.release(worker)
            _settle(self.pool)
        bound = [w for w in self.pool.snapshot()["workers"] if w["model"]]
        self.assertEqual(bound, [], "a subject-less job bound a worker")

    def test_explicit_large_limit_allows_many_stub_workers(self):
        with self._spares(0):
            held = [self.pool.acquire(f"/m/{i}.py") for i in range(40)]
        self.assertEqual(len({w.pid for w in held}), 40)
        for worker in held:
            self.pool.release(worker)

    def test_concurrent_acquire_never_hands_one_worker_to_two_callers(self):
        with self._spares(0):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                got = list(executor.map(lambda i: self.pool.acquire(f"/m/{i % 3}.py"), range(24)))
        self.assertEqual(len({w.pid for w in got}), len(got), "a worker was handed out twice")
        for worker in got:
            self.pool.release(worker)


class Spares(_PoolFixture):
    def test_ensure_spares_fills_to_k_in_the_background(self):
        with self._spares(2):
            self.pool.ensure_spares()
            _settle(self.pool)
            self.assertEqual(self.pool.snapshot()["spares"], 2)
            self.assertEqual(self.pool.snapshot()["imports"], 2)

    def test_binding_a_spare_starts_a_replacement(self):
        with self._spares(2):
            self.pool.ensure_spares()
            _settle(self.pool)
            before = _StubWorker.spawned
            worker = self.pool.acquire("/m/a.py")
            _settle(self.pool)
            snapshot = self.pool.snapshot()
        self.assertEqual(worker.model, "/m/a.py")
        self.assertEqual(snapshot["spares"], 2, "the spare set was not refilled")
        self.assertEqual(_StubWorker.spawned, before + 1, "exactly one replacement")
        self.pool.release(worker)

    def test_a_model_with_no_worker_takes_a_spare_not_a_spawn(self):
        with self._spares(1):
            self.pool.ensure_spares()
            _settle(self.pool)
            spare_pid = next(w["pid"] for w in self.pool.snapshot()["workers"] if not w["model"])
            worker = self.pool.acquire("/m/a.py")
        self.assertEqual(worker.pid, spare_pid, "a warm spare was available and not used")
        self.pool.release(worker)

    def test_the_spare_set_never_exceeds_k(self):
        with self._spares(1):
            self.pool.ensure_spares()
            _settle(self.pool)
            primary = self.pool.acquire("/m/a.py")
            _settle(self.pool)
            extras = [self.pool.acquire("/m/a.py") for _ in range(3)]
            _settle(self.pool)
            for extra in extras:
                self.pool.release(extra)
            _settle(self.pool)
            self.assertLessEqual(self.pool.snapshot()["spares"], 1)
        self.pool.release(primary)


class Lifecycle(_PoolFixture):
    def test_a_crashed_worker_is_dropped_and_its_model_rebinds_fresh(self):
        with self._spares(0):
            worker = self.pool.acquire("/m/a.py")
            worker._alive = False
            self.pool.release(worker, healthy=False)
            replacement = self.pool.acquire("/m/a.py")
        self.assertIsNot(worker, replacement)
        self.assertEqual(self.pool.snapshot()["crashes"], 1)
        self.pool.release(replacement)

    def test_a_worker_is_recycled_after_n_jobs(self):
        with self._spares(0), mock.patch.dict(os.environ, {"CADGEN_DAEMON_RECYCLE": "2"}):
            first = self.pool.acquire("/m/a.py")
            self.pool.release(first)
            same = self.pool.acquire("/m/a.py")
            self.assertIs(first, same)
            self.pool.release(same)  # second job: recycled
            fresh = self.pool.acquire("/m/a.py")
        self.assertIsNot(first, fresh)
        self.assertTrue(first.killed)
        self.assertEqual(self.pool.snapshot()["recycles"], 1)
        self.pool.release(fresh)

    def test_bound_workers_are_never_idle_reaped(self):
        with self._spares(0):
            worker = self.pool.acquire("/m/a.py")
            self.pool.release(worker)
            worker.last_used = 0.0  # ages ago
            self.pool.reap_dead()
        self.assertFalse(worker.killed)
        self.assertEqual(len(self.pool.snapshot()["workers"]), 1)

    def test_shutdown_kills_everything(self):
        with self._spares(0):
            held = [self.pool.acquire(f"/m/{i}.py") for i in range(3)]
            for worker in held:
                self.pool.release(worker)
        self.pool.shutdown()
        self.assertTrue(all(w.killed for w in held))
        self.assertEqual(self.pool.snapshot()["workers"], [])


class IdleUnbind(unittest.TestCase):
    """A bound worker idle for ten minutes returns to spare; nothing else is ever unbound."""

    def setUp(self) -> None:
        memory = mock.patch.object(runtime_limits, "_windows_available_memory", return_value=None)
        memory.start()
        self.addCleanup(memory.stop)
        patcher = mock.patch.object(pool_mod, "Worker", _StubWorker)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = [1000.0]
        self.pool = pool_mod.Pool(clock=lambda: self.now[0])
        self.addCleanup(self.pool.shutdown)

    def test_a_bound_worker_idle_past_the_timer_becomes_a_spare(self):
        with mock.patch.dict(os.environ, {"CADGEN_DAEMON_SPARES": "1", "CADGEN_DAEMON_IDLE_UNBIND": "600"}):
            worker = self.pool.acquire("/m/a.py")
            self.pool.release(worker)
            _settle(self.pool)
            self.now[0] += 599.0
            self.pool.unbind_idle()
            self.assertEqual(worker.model, "/m/a.py", "unbound before the timer")
            self.now[0] += 2.0
            self.pool.unbind_idle()
        # The spare set already held K=1, so this one exits rather than growing it.
        self.assertTrue(worker.killed or worker.model == "")
        self.assertEqual(self.pool.snapshot()["unbinds"], 1)

    def test_an_unbound_worker_is_rebound_without_a_spawn(self):
        with mock.patch.dict(os.environ, {"CADGEN_DAEMON_SPARES": "1", "CADGEN_DAEMON_IDLE_UNBIND": "600"}):
            worker = self.pool.acquire("/m/a.py")
            self.pool.release(worker)
            _settle(self.pool)
            self.now[0] += 601.0
            self.pool.unbind_idle()
            snapshot = self.pool.snapshot()
            # Unbound: either it is the spare now, or the spare set was already full and
            # it left. Either way there is exactly K warm and nothing bound.
            self.assertNotIn("/m/a.py", [w["model"] for w in snapshot["workers"]])
            self.assertEqual(snapshot["spares"], 1, snapshot)
            spare_pid = next(w["pid"] for w in snapshot["workers"] if not w["model"])
            again = self.pool.acquire("/m/b.py")
            self.assertEqual(again.pid, spare_pid, "a warm spare was available and a fresh worker was spawned instead")
            self.assertEqual(again.model, "/m/b.py")
            self.pool.release(again)

    def test_busy_and_recently_used_workers_are_left_alone(self):
        with mock.patch.dict(os.environ, {"CADGEN_DAEMON_SPARES": "0", "CADGEN_DAEMON_IDLE_UNBIND": "600"}):
            busy = self.pool.acquire("/m/a.py")
            idle = self.pool.acquire("/m/b.py")
            self.pool.release(idle)
            self.now[0] += 100.0
            self.pool.unbind_idle()
            self.assertEqual((busy.model, idle.model), ("/m/a.py", "/m/b.py"))
            self.now[0] += 600.0
            self.pool.unbind_idle()
            self.assertEqual(busy.model, "/m/a.py", "a busy worker was unbound")
            self.assertTrue(idle.model == "" or idle.killed, "the idle worker stayed bound")
        self.pool.release(busy)


class Capacity(_PoolFixture):
    def setUp(self):
        super().setUp()
        self.pool._limit = 4

    def test_default_resident_limit_is_four(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pool_mod.worker_limit(), 4)

    def test_concurrent_requests_cannot_overbook_resident_capacity(self):
        def acquire(i):
            try:
                return self.pool.acquire(f"/m/{i}.py")
            except pool_mod.WorkerCapacity:
                return None

        with self._spares(0), concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            workers = [w for w in executor.map(acquire, range(40)) if w is not None]
        self.assertEqual(len(workers), 4)
        self.assertEqual(_StubWorker.spawned, 4)
        self.assertEqual(self.pool.snapshot()["rejected"], 36)
        for worker in workers:
            self.pool.release(worker)

    def test_fanout_and_waiting_parents_count_toward_resident_limit(self):
        with self._spares(0):
            held = [self.pool.acquire(f"/m/{i}.py") for i in range(4)]
            with self.assertRaisesRegex(pool_mod.WorkerCapacity, "waiting parents"):
                self.pool.acquire("/m/child.py")
            self.assertEqual(_StubWorker.spawned, 4)
            self.assertEqual(self.pool.snapshot()["rejected"], 1)
            for worker in held:
                self.pool.release(worker)

    def test_many_sequential_models_evict_idle_workers(self):
        with self._spares(0):
            for i in range(40):
                worker = self.pool.acquire(f"/m/{i}.py")
                self.pool.release(worker)
                self.assertLessEqual(len(self.pool.snapshot()["workers"]), 4)
        self.assertGreater(self.pool.snapshot()["evictions"], 0)

    def test_starting_workers_reserve_capacity_before_imports(self):
        entered, resume = threading.Event(), threading.Event()
        original = self.pool._spawn

        def delayed_spawn():
            entered.set()
            self.assertTrue(resume.wait(3))
            return original()

        self.pool._limit = 1
        with self._spares(0), mock.patch.object(self.pool, "_spawn", delayed_spawn):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                first = executor.submit(self.pool.acquire, "/m/parent.py")
                try:
                    self.assertTrue(entered.wait(3))
                    with self.assertRaises(pool_mod.WorkerCapacity):
                        self.pool.acquire("/m/child.py")
                    self.assertEqual(self.pool.snapshot()["workersStarting"], 1)
                finally:
                    resume.set()
                worker = first.result(timeout=3)
        self.pool.release(worker)

    def test_spares_share_the_resident_budget(self):
        self.pool._limit = 2
        with self._spares(10):
            self.pool.ensure_spares()
            _settle(self.pool)
            workers = [self.pool.acquire(f"/m/{i}.py") for i in range(2)]
            _settle(self.pool)
            with self.assertRaises(pool_mod.WorkerCapacity):
                self.pool.acquire("/m/third.py")
            self.assertEqual(_StubWorker.spawned, 2)
            for worker in workers:
                self.pool.release(worker)

    def test_low_memory_rejects_before_spawn_and_skips_spare_warming(self):
        with self._spares(2), mock.patch.object(runtime_limits, "_windows_available_memory", return_value=1):
            self.pool.ensure_spares()
            with self.assertRaisesRegex(pool_mod.WorkerCapacity, "headroom"):
                self.pool.acquire("/m/a.py")
        self.assertEqual(_StubWorker.spawned, 0)
        self.assertEqual(self.pool.snapshot()["workersStarting"], 0)

    def test_failed_spawn_returns_its_reservation(self):
        with self._spares(0), mock.patch.object(self.pool, "_spawn", side_effect=OSError("spawn failed")):
            with self.assertRaises(OSError):
                self.pool.acquire("/m/a.py")
        self.assertEqual(self.pool.snapshot()["workersStarting"], 0)

    def test_retiring_worker_counts_until_process_exits(self):
        entered, resume = threading.Event(), threading.Event()
        self.pool._limit = 1
        with self._spares(0):
            worker = self.pool.acquire("/m/a.py")
            original = worker.kill

            def delayed_kill():
                entered.set()
                resume.wait(3)
                original()

            with mock.patch.object(worker, "kill", delayed_kill):
                self.pool.release(worker, healthy=False)
                try:
                    self.assertTrue(entered.wait(3))
                    with self.assertRaises(pool_mod.WorkerCapacity):
                        self.pool.acquire("/m/b.py")
                finally:
                    resume.set()
            with self.pool._cv:
                self.pool._cv.wait_for(lambda: self.pool._retiring == 0, timeout=3)
            self.assertEqual(self.pool.snapshot()["workersRetiring"], 0)

    def test_failed_idle_eviction_keeps_survivor_counted_and_quarantined(self):
        self.pool._limit = 1
        with self._spares(0):
            worker = self.pool.acquire("/m/a.py")
            self.pool.release(worker)
            with mock.patch.object(worker, "kill", return_value=None):
                with self.assertRaises(pool_mod.WorkerCapacity):
                    self.pool.acquire("/m/b.py")
                with self.assertRaises(pool_mod.WorkerCapacity):
                    self.pool.acquire("/m/a.py")
            self.assertTrue(worker.alive())
            self.assertEqual(_StubWorker.spawned, 1)
            self.assertEqual(self.pool.snapshot()["workersRetiring"], 1)
            worker.kill()  # later observed exit makes capacity available
            replacement = self.pool.acquire("/m/b.py")
            self.assertEqual(self.pool.snapshot()["workersRetiring"], 0)
            self.pool.release(replacement)

    def test_failed_async_retirement_keeps_capacity_until_observed_exit(self):
        self.pool._limit = 1
        for failure in (None, OSError("termination denied")):
            with self.subTest(failure=failure), self._spares(0):
                worker = self.pool.acquire("/m/a.py")
                finished = threading.Event()
                original = self.pool._stop_retired_worker

                def retire(candidate):
                    try:
                        original(candidate)
                    finally:
                        finished.set()

                with mock.patch.object(worker, "kill", side_effect=failure), mock.patch.object(self.pool, "_stop_retired_worker", retire):
                    self.pool.release(worker, healthy=False)
                    self.assertTrue(finished.wait(3))
                with self.assertRaises(pool_mod.WorkerCapacity):
                    self.pool.acquire("/m/b.py")
                self.assertEqual(self.pool.snapshot()["workersRetiring"], 1)
                worker.kill()
                self.pool.reap_dead()
                self.assertEqual(self.pool.snapshot()["workersRetiring"], 0)

    def test_first_requests_wait_for_warming_spares_without_overbooking(self):
        self.pool._limit = 2
        entered, resume, waiting = threading.Event(), threading.Event(), threading.Event()
        original = self.pool._spawn
        original_wait = self.pool._cv.wait
        waiters = [0]

        def wait(timeout=None):
            waiters[0] += 1
            if waiters[0] == 2:
                waiting.set()
            return original_wait(timeout)

        def warming():
            entered.set()
            self.assertTrue(resume.wait(3))
            return original()

        with self._spares(2), mock.patch.object(self.pool, "_spawn", warming), mock.patch.object(self.pool._cv, "wait", wait):
            self.pool.ensure_spares()
            self.assertTrue(entered.wait(3))
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                requests = [executor.submit(self.pool.acquire, f"/m/{i}.py") for i in range(2)]
                try:
                    self.assertTrue(waiting.wait(3))
                finally:
                    resume.set()
                workers = [request.result(timeout=3) for request in requests]
            _settle(self.pool)
            self.assertEqual(_StubWorker.spawned, 2)
            self.assertEqual(self.pool.snapshot()["rejected"], 0)
            self.assertEqual(len({w.pid for w in workers}), 2)
            for worker in workers:
                self.pool.release(worker)

    def test_waiting_for_a_stuck_spare_import_is_bounded(self):
        self.pool._limit = 1
        entered, resume = threading.Event(), threading.Event()
        original = self.pool._spawn

        def warming():
            entered.set()
            self.assertTrue(resume.wait(3))
            return original()

        with self._spares(1), mock.patch.object(self.pool, "_spawn", warming), mock.patch.object(pool_mod, "SPAWN_TIMEOUT_SECONDS", 0.01):
            self.pool.ensure_spares()
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaisesRegex(pool_mod.WorkerCapacity, "initialization timed out"):
                    self.pool.acquire("/m/a.py")
                self.assertEqual(self.pool.snapshot()["workersStarting"], 1)
                self.assertEqual(_StubWorker.spawned, 0)
            finally:
                resume.set()
            _settle(self.pool)

    def test_demand_recovers_when_warming_import_fails(self):
        self.pool._limit = 1
        entered, resume = threading.Event(), threading.Event()
        original = self.pool._spawn
        calls = [0]

        def warming():
            calls[0] += 1
            if calls[0] == 1:
                entered.set()
                self.assertTrue(resume.wait(3))
                raise OSError("import failed")
            return original()

        with self._spares(1), mock.patch.object(self.pool, "_spawn", warming):
            self.pool.ensure_spares()
            self.assertTrue(entered.wait(3))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                request = executor.submit(self.pool.acquire, "/m/a.py")
                resume.set()
                worker = request.result(timeout=3)
            self.assertEqual(self.pool.snapshot()["workersStarting"], 0)
            self.pool.release(worker)

    def test_shutdown_wakes_demand_waiting_for_spare_import(self):
        self.pool._limit = 1
        entered, resume, waiting = threading.Event(), threading.Event(), threading.Event()
        original_spawn = self.pool._spawn
        original_wait = self.pool._cv.wait

        def warming():
            entered.set()
            self.assertTrue(resume.wait(3))
            return original_spawn()

        def wait(timeout=None):
            waiting.set()
            return original_wait(timeout)

        with self._spares(1), mock.patch.object(self.pool, "_spawn", warming), mock.patch.object(self.pool._cv, "wait", wait):
            self.pool.ensure_spares()
            self.assertTrue(entered.wait(3))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                request = executor.submit(self.pool.acquire, "/m/a.py")
                try:
                    self.assertTrue(waiting.wait(3))
                    self.pool.shutdown()
                    with self.assertRaisesRegex(pool_mod.WorkerCapacity, "closed"):
                        request.result(timeout=3)
                finally:
                    resume.set()
            _settle(self.pool)


class Status(_PoolFixture):
    def test_snapshot_reports_per_worker_model_busy_jobs_extra(self):
        with self._spares(0):
            primary = self.pool.acquire("/m/a.py")
            extra = self.pool.acquire("/m/a.py")
            self.pool.release(extra)
            snapshot = self.pool.snapshot()
        rows = {w["pid"]: w for w in snapshot["workers"]}
        self.assertEqual(rows[primary.pid], {"pid": primary.pid, "model": "/m/a.py", "busy": True, "extra": False, "jobs": 0})
        for key in ("spares", "imports", "concurrent", "jobsServed", "recycles", "crashes"):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["concurrent"], 1)
        self.assertEqual(snapshot["jobsServed"], 1)
        self.pool.release(primary)


if __name__ == "__main__":
    unittest.main()
