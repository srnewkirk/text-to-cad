"""The warm worker pool: a worker per model, an extra when it is busy, spares in reserve.

One rule decides routing here: **a busy worker is never shared.** A request
for a model whose worker is idle takes that worker. A request for a model whose
worker is busy gets an *extra* — a spare bound to the same model for the length
of one job — and runs now. A request for a model with no worker binds a spare. A
request with no spare left evicts an idle worker or reserves a spawn, subject
to a daemon-wide resident limit. Busy workers (including waiting parents),
starting workers and retiring workers all count. At capacity admission fails
promptly for direct callers. The server opts into bounded, cancellable waiting
while broker slots, imports or owned teardown may release capacity. Slot-less
parents do not establish productive work; a short dispatch-transition grace
precedes refusal, and all admission waiting has a total startup-time bound.
Outcomes between
concurrent builds of one model are still decided by the publish rule.

Spares: ``CADGEN_DAEMON_SPARES`` (default 2) workers that have finished importing
build123d and are bound to nothing. Binding one starts a replacement in the
background only when capacity and memory headroom permit. An extra returns to
the spare set when its job ends; idle primaries may be evicted under pressure.

Recycle: a worker is dropped after ``CADGEN_DAEMON_RECYCLE`` jobs (default 1000)
as a leak hedge; its model binds a fresh worker on the next request.

Workers read frames on a thread so every read honours a timeout: a worker that
hangs before announcing itself, or mid-job, is reported instead of blocking its
caller forever.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import queue
import subprocess
import sys
import threading
import time

from cadgen.daemon import memory

DEFAULT_SPARES = 2
DEFAULT_RECYCLE_AFTER = 1000
DEFAULT_IDLE_UNBIND_SECONDS = 600.0
DEFAULT_MAX_WORKERS = 4
SPAWN_TIMEOUT_SECONDS = 120.0
ADMISSION_TRANSITION_GRACE_SECONDS = 2.0
ADMISSION_POLL_SECONDS = 0.1
_USE_SEQUENCE = itertools.count()


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def spare_count() -> int:
    value = _env_int("CADGEN_DAEMON_SPARES")
    return max(0, value) if value is not None else DEFAULT_SPARES


def worker_limit() -> int:
    value = _env_int("CADGEN_DAEMON_MAX_WORKERS")
    return max(1, value) if value is not None else DEFAULT_MAX_WORKERS


class WorkerCapacity(RuntimeError):
    """Admission refused before launching another native process."""


class AdmissionCancelled(WorkerCapacity):
    """The requesting client disappeared before admission."""


def recycle_after() -> int:
    value = _env_int("CADGEN_DAEMON_RECYCLE")
    return max(1, value) if value is not None else DEFAULT_RECYCLE_AFTER


def idle_unbind_seconds() -> float:
    raw = os.environ.get("CADGEN_DAEMON_IDLE_UNBIND", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_IDLE_UNBIND_SECONDS


class WorkerGone(RuntimeError):
    """The worker process ended, or its pipe closed, before the job produced its
    terminal frame. Carries the wait status when the process has been reaped."""

    def __init__(self, message: str, *, exit_status: int | None = None) -> None:
        super().__init__(message)
        self.exit_status = exit_status


_NTSTATUS_NAMES = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
    0xC000013A: "STATUS_CONTROL_C_EXIT",
}


def describe_exit(status: int | None) -> str:
    """A worker's death in words: the signal that killed it, or its exit code."""
    if status is None:
        return "closed its output while still running"
    if status < 0:
        import signal

        number = -status
        try:
            name = signal.Signals(number).name
        except ValueError:
            return f"was killed by signal {number}"
        return f"was killed by {name} (signal {number})"
    if os.name == "nt" or status > 255:
        unsigned = status & 0xFFFFFFFF
        name = _NTSTATUS_NAMES.get(unsigned)
        if name:
            return f"exited with 0x{unsigned:08X} ({name})"
        return f"exited with code {status}"
    return f"exited with code {status}"


_TIMED_OUT = object()  # _read_frame: the wait elapsed; distinct from None (pipe closed)


class Worker:
    """One warm subprocess. Owned by the pool; never shared between concurrent jobs."""

    def __init__(self) -> None:
        from cadgen.daemon.client import daemon_address
        from cadgen.daemon.executors import worker_env

        env = worker_env()
        # The broker a worker's jobs take slots from is the daemon itself; name the
        # address explicitly so a worker never guesses it from its identity.
        env["CADGEN_DAEMON_SOCKET"] = daemon_address()
        # A daemon's worker submits its children to the daemon, whatever the process
        # that started the daemon had in its environment.
        env.pop("CADGEN_DAEMON", None)
        # Guards against a worker's own top-level call routing back into the daemon
        # as a fresh request; nested SUBMITS ignore this on purpose (client.run_nested).
        env["CADGEN_DAEMON_CHILD"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "cadgen.daemon.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
            # utf-8 EXPLICITLY: this pipe carries JSON frames and the worker encodes
            # utf-8 (worker.serve), so neither end infers the platform code page.
            env=env, text=True, encoding="utf-8", errors="backslashreplace", bufsize=1,
        )
        self.jobs_served = 0
        self.busy = False
        self.extra = False
        self.last_used = time.monotonic()
        self.use_seq = next(_USE_SEQUENCE)
        # The MODEL this worker is bound to (its script path), "" while a spare.
        self.model = ""
        self._frames: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._pump, name="cadgen-worker-frames", daemon=True)
        self._reader.start()
        ready = self._read_frame(timeout=SPAWN_TIMEOUT_SECONDS)
        if ready is _TIMED_OUT:
            ready = None
        if not ready or "ready" not in ready:
            self.kill()
            raise WorkerGone(
                "worker did not announce itself" + ("" if ready else f" within {SPAWN_TIMEOUT_SECONDS:.0f}s")
            )
        self.pid = int(ready["ready"])

    def _pump(self) -> None:
        stream = self.proc.stdout
        if stream is None:
            self._frames.put(None)
            return
        try:
            for line in stream:
                try:
                    self._frames.put(json.loads(line))
                except ValueError:
                    self._frames.put({"stream": "stderr", "data": line})
        finally:
            # This reader owns stdout. Cross-thread close can block forever on
            # its buffered IO lock if a descendant still holds the pipe open.
            with contextlib.suppress(OSError, ValueError):
                stream.close()
            self._frames.put(None)

    def _read_frame(self, timeout: float | None = None) -> dict | None | object:
        """The next frame; None when the pipe closed; ``_TIMED_OUT`` when ``timeout`` elapsed.

        The two are told apart on purpose: a worker that died is reported by its
        exit status, a worker that is merely quiet by the silence timeout. Folding
        them into one None let a SIGKILLed worker read as "went silent" whenever
        its pipe's EOF arrived a beat before the kernel let it be reaped.
        """
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return _TIMED_OUT

    def send(self, request: dict) -> None:
        if self.proc.poll() is not None or self.proc.stdin is None:
            raise WorkerGone("worker is not running")
        try:
            self.proc.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise WorkerGone(f"worker stdin closed: {exc}") from exc

    def frames(self, *, silence_timeout: float | None = None):
        """Yield frames until the terminating one, which is yielded last.

        ``silence_timeout`` bounds the wait for ANY frame; a worker silent that
        long is reported as gone (its process is killed) rather than waited for.
        """
        while True:
            frame = self._read_frame(timeout=silence_timeout)
            if frame is _TIMED_OUT:
                self.kill()
                raise WorkerGone(
                    f"worker {getattr(self, 'pid', self.proc.pid)} went silent for "
                    f"{silence_timeout:.0f}s and was killed"
                )
            if frame is None:
                status = self._exit_status()
                raise WorkerGone(
                    f"worker {getattr(self, 'pid', self.proc.pid)} {describe_exit(status)}",
                    exit_status=status,
                )
            yield frame
            if "exit" in frame or "pong" in frame:
                return

    def _exit_status(self) -> int | None:
        try:
            return self.proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return None

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self) -> None:
        proc = self.proc
        try:
            if proc.poll() is None:
                if proc.stdin is not None:
                    with contextlib.suppress(OSError):
                        proc.stdin.close()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        with contextlib.suppress(subprocess.TimeoutExpired):
                            proc.wait(timeout=2)
        except OSError:
            pass
        finally:
            for stream in (proc.stdin,):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()


class Pool:
    """See the module docstring."""

    def __init__(self, clock=time.monotonic, *, headroom_reader=None,
                 baseline_reader=None, memory_policy=None) -> None:
        self._cv = threading.Condition()
        self._clock = clock
        self._workers: list[Worker] = []
        self._spares_pending = 0
        self._starting = 0
        self._retired_workers: list[Worker] = []
        self._limit = worker_limit()
        self._stats = {"jobsServed": 0, "imports": 0, "concurrent": 0, "crashes": 0, "recycles": 0, "unbinds": 0, "rejected": 0, "evictions": 0}
        self._closed = False
        self._demand_waiters = 0
        self._headroom_reader = headroom_reader or memory.observe_headroom
        self._baseline_reader = baseline_reader or memory.fresh_worker_bytes
        self._memory_policy = memory_policy or memory.MemoryPolicy.for_platform()
        self._startup_bytes = self._memory_policy.startup_bytes
        self._memory_epoch = 0

    def _observe_headroom(self):
        # OS reads never hold the admission lock. Only never-used idle workers
        # can calibrate interpreter/import cost; retained geometry is not baseline.
        with self._cv:
            fresh = [w for w in self._workers if not w.busy and not w.jobs_served]
            epoch = self._memory_epoch
        try:
            observation = self._headroom_reader()
        except (OSError, ValueError) as exc:
            observation = memory.Headroom(None, "memory reader",
                                         f"memory headroom observation unavailable: {exc}")
        measured = []
        for worker in fresh:
            try:
                amount = self._baseline_reader(worker.pid)
            except (OSError, ValueError):
                amount = None
            if amount is not None and amount > 0:
                measured.append((worker, amount))
        with self._cv:
            for worker, amount in measured:
                if worker in self._workers and not worker.busy and not worker.jobs_served:
                    self._startup_bytes = max(self._startup_bytes, amount)
        return observation, epoch

    def _check_headroom_locked(self, observation, *, spawning, job):
        if not observation.enforced:
            return
        if observation.available_bytes is None:
            raise WorkerCapacity(observation.error or "memory headroom observation unavailable")
        # Resident consumption is ALREADY excluded from available headroom.
        # Starts not yet represented there require atomic reservations instead.
        demand_pending = self._starting - self._spares_pending
        required = (self._memory_policy.reserve_bytes
                    + self._starting * self._startup_bytes
                    + demand_pending * self._memory_policy.spawn_job_increment_bytes
                    + (self._startup_bytes if spawning else 0)
                    + ((self._memory_policy.spawn_job_increment_bytes if spawning
                        else self._memory_policy.job_bytes) if job else 0))
        if observation.available_bytes < required:
            raise WorkerCapacity(
                f"insufficient memory headroom ({observation.source}): "
                f"{observation.available_bytes // memory.MIB} MiB available, "
                f"{required // memory.MIB} MiB estimated required; "
                "reservations do not constrain subsequent native allocations")

    # --- spares -------------------------------------------------------------------

    @property
    def _retiring(self) -> int:
        return len(self._retired_workers)

    def _spawn(self) -> Worker:
        worker = Worker()
        with self._cv:
            self._stats["imports"] += 1
        return worker

    def _spares_locked(self) -> list[Worker]:
        return [w for w in self._workers if not w.model and not w.busy]

    def _reserve_locked(self, observation, *, spare=False) -> None:
        if self._closed:
            raise WorkerCapacity("worker pool is closed")
        if len(self._workers) + self._starting + self._retiring >= self._limit:
            raise WorkerCapacity(
                f"resident worker limit {self._limit} reached (including waiting parents); "
                "build dependencies separately, then retry the parent; "
                "CADGEN_JOBS does not bound resident workers"
            )
        self._check_headroom_locked(observation, spawning=True, job=not spare)
        self._starting += 1
        self._memory_epoch += 1
        if spare:
            self._spares_pending += 1

    def ensure_spares(self) -> None:
        """Top the spare set up to ``spare_count()`` in the background."""
        with self._cv:
            if self._closed or self._demand_waiters or spare_count() <= 0:
                return
        observation, epoch = self._observe_headroom()
        with self._cv:
            if self._closed or self._demand_waiters:
                return
            if epoch != self._memory_epoch or time.monotonic() - observation.observed_at > memory.OBSERVATION_MAX_AGE_SECONDS:
                return
            want = 0
            desired = spare_count() - len(self._spares_locked()) - self._spares_pending
            for _ in range(max(0, desired)):
                try:
                    self._reserve_locked(observation, spare=True)
                except WorkerCapacity:
                    break
                want += 1
            if not want:
                return

        def fill(count: int) -> None:
            for _ in range(count):
                try:
                    worker = self._spawn()
                except (WorkerGone, OSError):
                    worker = None
                with self._cv:
                    self._spares_pending -= 1
                    self._starting -= 1
                    self._memory_epoch += 1
                    if worker is not None:
                        if self._closed:
                            worker.kill()
                        else:
                            self._workers.append(worker)
                    self._cv.notify_all()

        threading.Thread(target=fill, args=(want,), name="cadgen-spares", daemon=True).start()

    def _take_spare_locked(self) -> Worker | None:
        spares = self._spares_locked()
        return spares[0] if spares else None

    # --- acquire / release -------------------------------------------------------

    def acquire(self, model: str = "", *, cancelled=None, on_wait=None,
                productive=None) -> Worker:
        """Acquire a worker; server callers may opt into bounded contention waiting.

        ``productive`` observes active broker slots, not busy resident parents.
        The no-progress grace tolerates dispatch transitions, not dependency graphs.
        Direct callers retain fail-fast capacity refusal and bounded spare waiting.
        """
        deadline = time.monotonic() + SPAWN_TIMEOUT_SECONDS
        stalled_since = None
        reported = None
        waiting = False
        spare = None
        try:
            with self._cv:
                while True:
                    if cancelled is not None and cancelled():
                        raise AdmissionCancelled("client disconnected while waiting for worker admission")
                    if self._closed:
                        raise WorkerCapacity("worker pool is closed")
                    self._cv.release()
                    try:
                        observation, epoch = self._observe_headroom()
                    finally:
                        self._cv.acquire()
                    if cancelled is not None and cancelled():
                        raise AdmissionCancelled("client disconnected while observing memory headroom")
                    if self._closed:
                        raise WorkerCapacity("worker pool is closed")
                    if epoch != self._memory_epoch or time.monotonic() - observation.observed_at > memory.OBSERVATION_MAX_AGE_SECONDS:
                        if time.monotonic() >= deadline:
                            raise WorkerCapacity("memory headroom observation could not stabilize before admission timeout")
                        continue
                    if observation.enforced and observation.available_bytes is None:
                        self._stats["rejected"] += 1
                        raise WorkerCapacity(observation.error or "memory headroom observation unavailable")
                    self._reap_dead_locked()
                    bound = [w for w in self._workers if model and w.model == model and not w.extra]
                    idle_bound = [w for w in bound if not w.busy]
                    spare = idle_bound[0] if idle_bound else self._take_spare_locked()
                    try:
                        if spare is not None:
                            self._check_headroom_locked(observation, spawning=False, job=True)
                            spare.busy = True
                            break
                        self._reserve_locked(observation)
                        break
                    except WorkerCapacity as exc:
                        reason = str(exc)
                    idle = [w for w in self._workers if not w.busy]
                    if idle:
                        victim = min(idle, key=lambda w: w.last_used)
                        if productive is None:
                            # Preserve direct callers' synchronous eviction contract.
                            self._retire_locked(victim)
                            self._stop_retired_worker(victim)
                        else:
                            self._drop_locked(victim)
                        self._stats["evictions"] += 1
                        continue
                    now = time.monotonic()
                    if now >= deadline:
                        self._stats["rejected"] += 1
                        raise WorkerCapacity(("spare worker initialization timed out: " if self._spares_pending
                                              else "worker admission timed out: ") + reason)
                    if productive is None and not self._spares_pending:
                        self._stats["rejected"] += 1
                        raise WorkerCapacity(reason)
                    progress = self._starting or self._retiring or (productive is not None and productive())
                    if progress:
                        stalled_since = None
                    elif stalled_since is None:
                        stalled_since = now
                    elif now - stalled_since >= ADMISSION_TRANSITION_GRACE_SECONDS:
                        self._stats["rejected"] += 1
                        raise WorkerCapacity(reason + "; no active work can release admission capacity")
                    if not waiting:
                        waiting = True
                        self._demand_waiters += 1
                    report_key = "memory headroom" if reason.startswith("insufficient memory headroom") else reason
                    if on_wait is not None and reported != report_key:
                        reported = report_key
                        # Reporting may perform socket I/O. Release the global
                        # admission lock and retry routing/cancellation afterward.
                        self._cv.release()
                        try:
                            on_wait(reason)
                        finally:
                            self._cv.acquire()
                        continue
                    self._cv.wait(timeout=min(ADMISSION_POLL_SECONDS, deadline - now))
                if waiting:
                    self._demand_waiters -= 1
                    waiting = False
            if spare is None:
                with self._cv:
                    if self._closed or (cancelled is not None and cancelled()):
                        self._starting -= 1
                        self._memory_epoch += 1
                        self._cv.notify_all()
                        if self._closed:
                            raise WorkerCapacity("worker pool closed before worker startup")
                        raise AdmissionCancelled("client disconnected before worker startup")
                try:
                    spare = self._spawn()
                except BaseException:
                    with self._cv:
                        self._starting -= 1
                        self._memory_epoch += 1
                        self._cv.notify_all()
                    raise
                with self._cv:
                    self._starting -= 1
                    self._memory_epoch += 1
                    spare.busy = True
                    self._workers.append(spare)
                    self._cv.notify_all()
            with self._cv:
                if self._closed or (cancelled is not None and cancelled()):
                    spare.busy = False
                    self._drop_locked(spare)
                    if self._closed:
                        raise WorkerCapacity("worker pool closed during admission")
                    raise AdmissionCancelled("client disconnected during worker admission")
                if model:
                    was_bound = spare in bound
                    spare.model = model
                    spare.extra = not was_bound and any(
                        w is not spare and w.model == model and not w.extra for w in self._workers)
                    if spare.extra:
                        self._stats["concurrent"] += 1
                else:
                    spare.extra = True
                self._used_locked(spare)
            self.ensure_spares()
            return spare
        finally:
            if waiting:
                with self._cv:
                    self._demand_waiters -= 1
                    self._cv.notify_all()

    def _used_locked(self, worker: Worker) -> Worker:
        worker.last_used = self._clock()
        worker.use_seq = next(_USE_SEQUENCE)
        return worker

    def unbind_idle(self) -> None:
        """Return aged bound workers to spares, retiring those beyond K.

        Admission also reclaims owned idle workers under resident/headroom pressure.
        Persistent derived artifacts are untouched by either lifecycle operation.
        """
        limit = idle_unbind_seconds()
        with self._cv:
            now = self._clock()
            for worker in list(self._workers):
                if not worker.model or worker.busy or worker.extra:
                    continue
                if now - worker.last_used < limit:
                    continue
                self._stats["unbinds"] += 1
                if len(self._spares_locked()) + self._spares_pending >= spare_count():
                    self._drop_locked(worker)
                else:
                    worker.model = ""

    def release(self, worker: Worker, *, healthy: bool = True) -> None:
        with self._cv:
            worker.busy = False
            worker.last_used = self._clock()
            worker.jobs_served += 1
            self._stats["jobsServed"] += 1
            if not healthy or not worker.alive():
                if not healthy:
                    self._stats["crashes"] += 1
                self._drop_locked(worker)
            elif worker.jobs_served >= recycle_after():
                self._stats["recycles"] += 1
                self._drop_locked(worker)
            elif worker.extra:
                if len(self._spares_locked()) + self._spares_pending >= spare_count():
                    # The spare set is already full (a replacement was started when this
                    # one was taken); keeping it too would grow the set by one per extra.
                    self._drop_locked(worker)
                else:
                    # Back to the spare set: unbound, idle, warm.
                    worker.model = ""
                    worker.extra = False
            self._cv.notify_all()
        self.ensure_spares()

    def _drop_locked(self, worker: Worker) -> None:
        if worker in self._retired_workers:
            return
        self._retire_locked(worker)
        threading.Thread(target=self._stop_retired_worker, args=(worker,), daemon=True).start()

    def _retire_locked(self, worker: Worker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        self._retired_workers.append(worker)

    def _stop_retired_worker(self, worker: Worker) -> None:
        try:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                worker.kill()
        finally:
            with self._cv:
                self._reap_retired_locked()
                self._cv.notify_all()

    def _reap_retired_locked(self) -> None:
        for worker in list(self._retired_workers):
            # Termination returning (or throwing) is not proof of exit.
            # Keep survivors quarantined and counted; they cannot be reused.
            if not worker.alive():
                self._retired_workers.remove(worker)

    def _reap_dead_locked(self) -> None:
        self._reap_retired_locked()
        for worker in list(self._workers):
            if not worker.alive() and not worker.busy:
                self._drop_locked(worker)

    def reap_dead(self) -> None:
        with self._cv:
            self._reap_dead_locked()

    def shutdown(self) -> None:
        with self._cv:
            self._closed = True
            workers = list(self._workers) + list(self._retired_workers)
            self._workers = []
            self._cv.notify_all()
        for worker in workers:
            worker.kill()

    def snapshot(self) -> dict:
        with self._cv:
            workers = [
                {
                    "pid": getattr(w, "pid", None),
                    "model": w.model,
                    "busy": w.busy,
                    "extra": w.extra,
                    "jobs": w.jobs_served,
                }
                for w in self._workers
            ]
            return {
                "workers": workers,
                "spares": len(self._spares_locked()),
                "sparesPending": self._spares_pending,
                "sparesWanted": spare_count(),
                "workerLimit": self._limit,
                "workersStarting": self._starting,
                "workersRetiring": self._retiring,
                **self._stats,
            }
