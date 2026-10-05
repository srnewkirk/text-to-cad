"""The warm worker pool: a worker per model, an extra when it is busy, spares in reserve.

One rule decides everything here: **worker admission never waits on a build.** A request
for a model whose worker is idle takes that worker. A request for a model whose
worker is busy gets an *extra* — a spare bound to the same model for the length
of one job — and runs now. A request for a model with no worker binds a spare. A
request with no spare left evicts an idle worker or reserves a spawn, subject
to a daemon-wide resident limit. Busy workers (including waiting parents),
starting workers and retiring workers all count. At capacity admission fails
promptly: queuing parents behind their children would deadlock. Outcomes between
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

DEFAULT_SPARES = 2
DEFAULT_RECYCLE_AFTER = 1000
DEFAULT_IDLE_UNBIND_SECONDS = 600.0
DEFAULT_MAX_WORKERS = 4
# The incident's largest workers used 1.3–1.7 GiB of private memory. This is
# admission headroom, not a promise to constrain a model's later allocations.
SPAWN_MEMORY_BYTES = 2 * 1024**3
SPAWN_MEMORY_RESERVE_BYTES = 2 * 1024**3
SPAWN_TIMEOUT_SECONDS = 120.0
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
        for line in stream:
            try:
                self._frames.put(json.loads(line))
            except ValueError:
                self._frames.put({"stream": "stderr", "data": line})
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
                        proc.wait(timeout=2)
        except OSError:
            pass
        finally:
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()


class Pool:
    """See the module docstring."""

    def __init__(self, clock=time.monotonic) -> None:
        self._cv = threading.Condition()
        self._clock = clock
        self._workers: list[Worker] = []
        self._spares_pending = 0
        self._starting = 0
        self._retired_workers: list[Worker] = []
        self._limit = worker_limit()
        self._stats = {"jobsServed": 0, "imports": 0, "concurrent": 0, "crashes": 0, "recycles": 0, "unbinds": 0, "rejected": 0, "evictions": 0}
        self._closed = False

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

    def _reserve_locked(self) -> None:
        if self._closed:
            raise WorkerCapacity("worker pool is closed")
        if len(self._workers) + self._starting + self._retiring >= self._limit:
            raise WorkerCapacity(
                f"resident worker limit {self._limit} reached (including waiting parents); "
                "build dependencies separately, then retry the parent; "
                "CADGEN_JOBS does not bound resident workers"
            )
        from cadgen._internal.runtime_limits import _windows_available_memory

        available = _windows_available_memory()
        required = SPAWN_MEMORY_RESERVE_BYTES + (self._starting + 1) * SPAWN_MEMORY_BYTES
        if available is not None and available < required:
            raise WorkerCapacity("insufficient Windows RAM/commit headroom to start a CAD worker; retry after memory is available")
        self._starting += 1

    def ensure_spares(self) -> None:
        """Top the spare set up to ``spare_count()`` in the background."""
        with self._cv:
            if self._closed:
                return
            want = 0
            desired = spare_count() - len(self._spares_locked()) - self._spares_pending
            for _ in range(max(0, desired)):
                try:
                    self._reserve_locked()
                except WorkerCapacity:
                    break
                want += 1
            if not want:
                return
            self._spares_pending += want

        def fill(count: int) -> None:
            for _ in range(count):
                try:
                    worker = self._spawn()
                except (WorkerGone, OSError):
                    worker = None
                with self._cv:
                    self._spares_pending -= 1
                    self._starting -= 1
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

    def acquire(self, model: str = "") -> Worker:
        """A worker for ``model`` or a capacity error; never wait on active builds.

        Pending spare imports may be awaited up to the spawn timeout.

        ``model`` is the script path (the routing key); "" means a request with
        no model subject, which borrows a spare without binding it.
        """
        with self._cv:
            if self._closed:
                raise WorkerCapacity("worker pool is closed")
            self._reap_dead_locked()
            # A pending spare holds capacity but is not running a model. Wait
            # for its bounded import rather than reject demand or queue behind
            # active parents. Recheck under the lock because other callers can
            # claim the spare first, and failed imports release reservations.
            deadline = time.monotonic() + SPAWN_TIMEOUT_SECONDS
            while self._spares_pending and self._take_spare_locked() is None:
                if any(w.model == model and model and not w.busy and not w.extra for w in self._workers):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stats["rejected"] += 1
                    raise WorkerCapacity("spare worker initialization timed out; retry after imports finish")
                self._cv.wait(timeout=remaining)
                if self._closed:
                    raise WorkerCapacity("worker pool closed while waiting for a spare")
                self._reap_dead_locked()
            if model:
                bound = [w for w in self._workers if w.model == model and not w.extra]
                idle = [w for w in bound if not w.busy]
                if idle:
                    worker = idle[0]
                    worker.busy = True
                    return self._used_locked(worker)
                spare = self._take_spare_locked()
                if spare is not None:
                    spare.busy = True
            else:
                spare = self._take_spare_locked()
                if spare is not None:
                    spare.busy = True
                bound = []
            if spare is None:
                idle = [w for w in self._workers if not w.busy]
                if idle and len(self._workers) + self._starting + self._retiring >= self._limit:
                    # Do not rebind a model worker: its RAM memo belongs to its
                    # previous model. Reap before reserving its replacement.
                    victim = min(idle, key=lambda w: w.last_used)
                    self._retire_locked(victim)
                    self._stop_retired_worker(victim)
                    self._stats["evictions"] += 1
                try:
                    self._reserve_locked()
                except WorkerCapacity:
                    self._stats["rejected"] += 1
                    raise
        if spare is None:
            try:
                spare = self._spawn()
            except BaseException:
                with self._cv:
                    self._starting -= 1
                    self._cv.notify_all()
                raise
            with self._cv:
                self._starting -= 1
                if self._closed:
                    spare.kill()
                    raise WorkerCapacity("worker pool closed while starting a worker")
                # Publish before releasing the lock: another admission must
                # count this worker even before it is assigned below.
                spare.busy = True
                self._workers.append(spare)
        with self._cv:
            if self._closed:
                spare.kill()
                raise WorkerCapacity("worker pool closed during admission")
            spare.busy = True
            if model:
                spare.model = model
                # An extra when a primary already exists; a primary otherwise.
                spare.extra = bool(bound)
                if spare.extra:
                    self._stats["concurrent"] += 1
            else:
                spare.extra = True  # borrowed; returns to the spare set on release
            if spare not in self._workers:
                self._workers.append(spare)
            self._used_locked(spare)
        self.ensure_spares()
        return spare

    def _used_locked(self, worker: Worker) -> Worker:
        worker.last_used = self._clock()
        worker.use_seq = next(_USE_SEQUENCE)
        return worker

    def unbind_idle(self) -> None:
        """A bound worker idle for ``idle_unbind_seconds()`` returns to the spare set
        (spares beyond K exit). Its model's next build rebinds a spare -- no import
        repaid, a cold RAM op-memo tier. Purely RAM: idle workers hold no slot and
        block nothing, so this is the only reason to touch them at all."""
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
