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
        memory = mock.patch.object(runtime_limits, "_windows_available_memory", return_value=256 * 1024**3)
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
        memory = mock.patch.object(runtime_limits, "_windows_available_memory", return_value=256 * 1024**3)
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


class AdmissionWaiting(_PoolFixture):
    def setUp(self):
        super().setUp()
        self.pool._limit = 1
        self.enterContext(self._spares(0))
        self.enterContext(mock.patch.object(pool_mod, "ADMISSION_TRANSITION_GRACE_SECONDS", 0.03))
        self.enterContext(mock.patch.object(pool_mod, "ADMISSION_POLL_SECONDS", 0.005))
        self.enterContext(mock.patch.object(pool_mod, "SPAWN_TIMEOUT_SECONDS", 0.5))

    def _pending(self, **kwargs):
        waiting = threading.Event()
        executor = self.enterContext(concurrent.futures.ThreadPoolExecutor(max_workers=1))
        future = executor.submit(self.pool.acquire, "/m/child.py",
                                 on_wait=lambda reason: waiting.set(), **kwargs)
        self.assertTrue(waiting.wait(1), "request did not report admission waiting")
        return future

    def test_root_waits_for_active_release_without_overbooking(self):
        parent = self.pool.acquire("/m/parent.py")
        future = self._pending(productive=lambda: True)
        self.assertFalse(future.done())
        self.assertEqual(_StubWorker.spawned, 1)
        self.pool.release(parent)
        child = future.result(timeout=1)
        self.assertTrue(parent.killed)
        self.assertEqual(self.pool.snapshot()["workersStarting"], 0)
        self.pool.release(child)

    def test_two_waiters_cannot_claim_the_same_released_capacity(self):
        parent = self.pool.acquire("/m/parent.py")
        waiting = threading.Event()
        seen = [0]
        def report(reason):
            seen[0] += 1
            if seen[0] == 2:
                waiting.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.pool.acquire, f"/m/{i}.py",
                                      productive=lambda: True, on_wait=report) for i in range(2)]
            self.assertTrue(waiting.wait(1))
            self.pool.release(parent)
            done, pending = concurrent.futures.wait(futures, timeout=1,
                                                    return_when=concurrent.futures.FIRST_COMPLETED)
            self.assertEqual(len(done), 1)
            first = next(iter(done)).result()
            self.assertEqual(len(self.pool.snapshot()["workers"]), 1)
            self.pool.release(first)
            second = next(iter(pending)).result(timeout=1)
            self.assertEqual(len(self.pool.snapshot()["workers"]), 1)
            self.pool.release(second)

    def test_nested_waits_behind_productive_leaf(self):
        self.pool._limit = 2
        parent = self.pool.acquire("/m/parent.py")
        leaf = self.pool.acquire("/m/leaf.py")
        future = self._pending(productive=lambda: True)
        self.pool.release(leaf)
        child = future.result(timeout=1)
        self.assertFalse(parent.killed)
        self.pool.release(child)
        self.pool.release(parent)

    def test_yielded_parents_are_not_productive(self):
        parent = self.pool.acquire("/m/parent.py")
        future = self._pending(productive=lambda: False)
        with self.assertRaisesRegex(pool_mod.WorkerCapacity, "no active work"):
            future.result(timeout=1)
        self.assertEqual(_StubWorker.spawned, 1)
        self.assertFalse(parent.killed)
        self.pool.release(parent)

    def test_cancellation_waiting_does_not_take_later_capacity(self):
        parent = self.pool.acquire("/m/parent.py")
        cancelled = threading.Event()
        future = self._pending(productive=lambda: True, cancelled=cancelled.is_set)
        cancelled.set()
        with self.assertRaises(pool_mod.AdmissionCancelled):
            future.result(timeout=1)
        self.assertEqual(self.pool._demand_waiters, 0)
        self.pool.release(parent)
        self.assertEqual(_StubWorker.spawned, 1)

    def test_shutdown_wakes_contention_waiter(self):
        self.pool.acquire("/m/parent.py")
        future = self._pending(productive=lambda: True)
        self.pool.shutdown()
        with self.assertRaisesRegex(pool_mod.WorkerCapacity, "closed"):
            future.result(timeout=1)

    def test_productive_signal_does_not_allow_indefinite_wait(self):
        parent = self.pool.acquire("/m/parent.py")
        with mock.patch.object(pool_mod, "SPAWN_TIMEOUT_SECONDS", 0.03):
            future = self._pending(productive=lambda: True)
            with self.assertRaisesRegex(pool_mod.WorkerCapacity, "admission timed out"):
                future.result(timeout=1)
        self.pool.release(parent)

    def test_stuck_retirement_remains_counted_and_bounded(self):
        parent = self.pool.acquire("/m/parent.py")
        self.pool.release(parent)
        with mock.patch.object(parent, "kill", return_value=None), mock.patch.object(pool_mod, "SPAWN_TIMEOUT_SECONDS", 0.03):
            future = self._pending(productive=lambda: False)
            with self.assertRaisesRegex(pool_mod.WorkerCapacity, "timed out"):
                future.result(timeout=1)
        self.assertEqual(self.pool.snapshot()["workersRetiring"], 1)
        self.assertEqual(_StubWorker.spawned, 1)
        parent.kill()

    def test_windows_headroom_reclaims_idle_below_resident_limit(self):
        self.pool._limit = 3
        parent = self.pool.acquire("/m/parent.py")
        self.pool.release(parent)
        available = lambda: 10 * 1024**3 if parent.killed else 1
        with mock.patch.object(runtime_limits, "_windows_available_memory", side_effect=available):
            child = self.pool.acquire("/m/child.py", productive=lambda: False)
        self.assertTrue(parent.killed)
        self.pool.release(child)

    def test_waiting_demand_prevents_speculative_spare_refill(self):
        parent = self.pool.acquire("/m/parent.py")
        future = self._pending(productive=lambda: True)
        with self._spares(1):
            self.pool.release(parent)
            child = future.result(timeout=1)
            self.assertEqual(_StubWorker.spawned, 2)
            self.assertEqual(self.pool.snapshot()["sparesPending"], 0)
        self.pool.release(child)

    def test_pre_cancelled_request_does_not_spawn(self):
        with self.assertRaises(pool_mod.AdmissionCancelled):
            self.pool.acquire("/m/child.py", cancelled=lambda: True, productive=lambda: True)
        self.assertEqual(_StubWorker.spawned, 0)

    def test_cancellation_after_reservation_returns_capacity_without_spawn(self):
        cancelled = threading.Event()
        reserve = self.pool._reserve_locked
        def reservation(*args, **kwargs):
            reserve(*args, **kwargs)
            cancelled.set()
        with mock.patch.object(self.pool, "_reserve_locked", reservation):
            with self.assertRaises(pool_mod.AdmissionCancelled):
                self.pool.acquire("/m/child.py", productive=lambda: True, cancelled=cancelled.is_set)
        self.assertEqual(_StubWorker.spawned, 0)
        self.assertEqual(self.pool.snapshot()["workersStarting"], 0)

    def test_slow_reporting_does_not_hold_pool_lock(self):
        parent = self.pool.acquire("/m/parent.py")
        reporting, resume = threading.Event(), threading.Event()
        def report(reason):
            reporting.set()
            self.assertTrue(resume.wait(1))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(self.pool.acquire, "/m/child.py",
                                     productive=lambda: True, on_wait=report)
            self.assertTrue(reporting.wait(1))
            try:
                release = executor.submit(self.pool.release, parent)
                release.result(timeout=0.5)
            finally:
                resume.set()
            child = future.result(timeout=1)
            self.pool.release(child)

    def test_cancellation_during_spare_import_does_not_claim_spare(self):
        entered, resume, cancelled = threading.Event(), threading.Event(), threading.Event()
        original = self.pool._spawn
        def warming():
            entered.set()
            self.assertTrue(resume.wait(1))
            return original()
        with self._spares(1), mock.patch.object(self.pool, "_spawn", warming):
            self.pool.ensure_spares()
            self.assertTrue(entered.wait(1))
            try:
                future = self._pending(productive=lambda: False, cancelled=cancelled.is_set)
                cancelled.set()
                with self.assertRaises(pool_mod.AdmissionCancelled):
                    future.result(timeout=1)
            finally:
                resume.set()
            _settle(self.pool)
            self.assertFalse(self.pool.snapshot()["workers"][0]["busy"])

    def test_cancelled_after_spawn_quarantines_new_worker(self):
        cancelled = threading.Event()
        original = self.pool._spawn
        def spawning():
            worker = original()
            cancelled.set()
            return worker
        with mock.patch.object(self.pool, "_spawn", spawning):
            with self.assertRaises(pool_mod.AdmissionCancelled):
                self.pool.acquire("/m/child.py", productive=lambda: False, cancelled=cancelled.is_set)
        self.assertEqual(self.pool.snapshot()["workersStarting"], 0)
        self.assertFalse(any(w["busy"] for w in self.pool.snapshot()["workers"]))


class MemoryAdmission(_PoolFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(self._spares(0))
        self.pool._limit = 4
        self.pool._memory_policy = pool_mod.memory.MemoryPolicy.for_platform("linux")
        self.pool._startup_bytes = self.pool._memory_policy.startup_bytes
        self.available = 16 * 1024**3
        self.pool._headroom_reader = lambda: pool_mod.memory.Headroom(self.available, "test headroom")
        self.pool._baseline_reader = lambda pid: None

    def test_linux_demand_spawn_checks_startup_increment_and_reserve(self):
        self.available = (1024 + 512 + 450) * 1024**2 - 1
        with self.assertRaisesRegex(pool_mod.WorkerCapacity, "headroom"):
            self.pool.acquire("/m/a.py")
        self.assertEqual(_StubWorker.spawned, 0)
        self.available += 1
        worker = self.pool.acquire("/m/a.py")
        self.pool.release(worker)

    def test_windows_demand_preserves_four_gib_first_spawn_threshold(self):
        self.pool._memory_policy = pool_mod.memory.MemoryPolicy.for_platform("win32")
        self.pool._startup_bytes = self.pool._memory_policy.startup_bytes
        self.available = 4 * 1024**3
        worker = self.pool.acquire("/m/a.py")
        self.pool.release(worker)

    def test_warm_reuse_checks_increment_without_new_startup_cost(self):
        worker = self.pool.acquire("/m/a.py")
        self.pool.release(worker)
        self.available = (1024 + 450) * 1024**2
        again = self.pool.acquire("/m/a.py")
        self.assertIs(again, worker)
        self.assertEqual(_StubWorker.spawned, 1)
        self.pool.release(again)

    def test_pressure_reclaims_selected_warm_worker_before_refusal(self):
        worker = self.pool.acquire("/m/a.py")
        self.pool.release(worker)
        self.available = 1
        with self.assertRaisesRegex(pool_mod.WorkerCapacity, "headroom"):
            self.pool.acquire("/m/a.py")
        self.assertTrue(worker.killed)
        self.assertEqual(_StubWorker.spawned, 1)

    def test_unknown_headroom_refuses_without_destroying_idle_worker(self):
        worker = self.pool.acquire("/m/a.py")
        self.pool.release(worker)
        self.available = None
        with self.assertRaisesRegex(pool_mod.WorkerCapacity, "observation unavailable"):
            self.pool.acquire("/m/a.py")
        self.assertFalse(worker.killed)
        with self._spares(2):
            self.pool.ensure_spares()
        self.assertEqual(self.pool.snapshot()["sparesPending"], 0)
        self.assertEqual(_StubWorker.spawned, 1)

    def test_failed_headroom_reader_skips_speculative_spares(self):
        self.pool._headroom_reader = mock.Mock(side_effect=OSError("denied"))
        with self._spares(2):
            self.pool.ensure_spares()
        with self.assertRaisesRegex(pool_mod.WorkerCapacity, "denied"):
            self.pool.acquire("/m/a.py")
        self.assertEqual(_StubWorker.spawned, 0)

    def test_pending_spares_and_demands_have_distinct_reservations(self):
        observation = pool_mod.memory.Headroom((1024 + 2 * 512 + 450 + 450) * 1024**2,
                                               "test headroom")
        with self.pool._cv:
            self.pool._starting = 2
            self.pool._spares_pending = 1
            try:
                self.pool._check_headroom_locked(observation, spawning=False, job=True)
                with self.assertRaises(pool_mod.WorkerCapacity):
                    self.pool._check_headroom_locked(observation, spawning=True, job=True)
            finally:
                self.pool._starting = self.pool._spares_pending = 0

    def test_pending_demand_prevents_second_start_overbooking(self):
        self.available = 2500 * 1024**2
        entered, resume = threading.Event(), threading.Event()
        spawn = self.pool._spawn
        def delayed():
            entered.set()
            self.assertTrue(resume.wait(1))
            return spawn()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor, mock.patch.object(self.pool, "_spawn", delayed):
            future = executor.submit(self.pool.acquire, "/m/a.py")
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaisesRegex(pool_mod.WorkerCapacity, "headroom"):
                    self.pool.acquire("/m/b.py")
                self.assertEqual(self.pool.snapshot()["workersStarting"], 1)
            finally:
                resume.set()
            self.pool.release(future.result(timeout=1))

    def test_resident_parent_is_not_added_again_to_available_accounting(self):
        parent = self.pool.acquire("/m/parent.py")
        self.available = (1024 + 512 + 450) * 1024**2
        self.pool._baseline_reader = mock.Mock(side_effect=AssertionError("busy parent is not baseline"))
        child = self.pool.acquire("/m/child.py")
        self.assertFalse(parent.killed)
        self.pool.release(child)
        self.pool.release(parent)

    def test_fresh_measurement_increases_startup_floor_but_not_job_cost(self):
        spare = _StubWorker()
        self.pool._workers.append(spare)
        self.pool._baseline_reader = lambda pid: 768 * 1024**2
        worker = self.pool.acquire("/m/a.py")
        self.assertEqual(self.pool._startup_bytes, 768 * 1024**2)
        self.assertEqual(self.pool._memory_policy.job_bytes, 450 * 1024**2)
        self.pool.release(worker)

    def test_failed_baseline_measurement_retains_seed(self):
        spare = _StubWorker()
        self.pool._workers.append(spare)
        self.pool._baseline_reader = mock.Mock(side_effect=OSError("gone"))
        worker = self.pool.acquire("/m/a.py")
        self.assertEqual(self.pool._startup_bytes, 512 * 1024**2)
        self.pool.release(worker)

    def test_memory_observation_does_not_hold_global_admission_lock(self):
        parent = self.pool.acquire("/m/parent.py")
        entered, resume = threading.Event(), threading.Event()
        def observe():
            entered.set()
            self.assertTrue(resume.wait(1))
            return pool_mod.memory.Headroom(self.available, "test headroom")
        self.pool._headroom_reader = observe
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(self.pool.acquire, "/m/child.py")
            self.assertTrue(entered.wait(1))
            try:
                executor.submit(self.pool.release, parent).result(timeout=0.5)
            finally:
                resume.set()
            self.pool.release(future.result(timeout=1))

    def test_other_platform_preserves_resident_only_admission(self):
        self.pool._headroom_reader = lambda: pool_mod.memory.Headroom(None, "resident only", enforced=False)
        worker = self.pool.acquire("/m/a.py")
        self.pool.release(worker)

    def test_stale_headroom_is_resampled_before_admission(self):
        stale = pool_mod.memory.Headroom(self.available, "stale", observed_at=time.monotonic() - 1)
        self.pool._headroom_reader = mock.Mock(side_effect=[stale, pool_mod.memory.Headroom(self.available, "fresh")])
        worker = self.pool.acquire("/m/a.py")
        self.assertEqual(self.pool._headroom_reader.call_count, 2)
        self.pool.release(worker)


class WorkerPipeOwnership(unittest.TestCase):
    def test_retirement_does_not_close_frame_readers_stdout(self):
        worker = pool_mod.Worker.__new__(pool_mod.Worker)
        worker.proc = mock.Mock()
        worker.proc.poll.return_value = 0
        worker.kill()
        worker.proc.stdout.close.assert_not_called()
        worker.proc.stdin.close.assert_called_once()

    def test_frame_reader_closes_stdout_and_reports_eof(self):
        import io
        import queue
        worker = pool_mod.Worker.__new__(pool_mod.Worker)
        stream = io.StringIO('{"exit":0}\n')
        worker.proc = mock.Mock(stdout=stream)
        worker._frames = queue.Queue()
        worker._pump()
        self.assertTrue(stream.closed)
        self.assertEqual(worker._frames.get_nowait(), {"exit": 0})
        self.assertIsNone(worker._frames.get_nowait())


if __name__ == "__main__":
    unittest.main()
