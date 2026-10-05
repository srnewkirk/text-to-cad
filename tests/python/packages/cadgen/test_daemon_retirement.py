"""Test-owned retirement and idle shutdown release real, lightweight processes."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from cadgen.daemon import client, transport
from tests.python.support.daemon_cleanup import retire_owned_daemon
from tests.python.packages.cadgen.test_warm_output_equivalence import _Daemon, _env, REPO_ROOT
from tests.python.packages.cadgen import test_warm_output_equivalence as equivalence


class RetirementProtocol(unittest.TestCase):
    def test_harness_reports_failed_cleanup(self):
        daemon = _Daemon(Path('owned'))
        error = subprocess.CalledProcessError(1, ['cleanup'])
        with mock.patch.object(equivalence.subprocess, 'run', side_effect=error) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                daemon.__exit__(None, None, None)
        self.assertTrue(run.call_args.kwargs['check'])

    def test_refuses_an_ambient_or_mismatched_address(self):
        with mock.patch.dict(os.environ, {'CADGEN_DAEMON_SOCKET': 'owned'}), mock.patch.object(client, '_connect') as connect:
            for address in ('', 'ambient'):
                with self.assertRaises(ValueError):
                    retire_owned_daemon(address)
            connect.assert_not_called()

    def test_does_not_spawn_when_owned_daemon_is_absent(self):
        with mock.patch.dict(os.environ, {'CADGEN_DAEMON_SOCKET': 'owned'}), mock.patch.object(client, '_connect', side_effect=OSError), mock.patch.object(client, '_connect_or_spawn') as spawn:
            retire_owned_daemon('owned')
            spawn.assert_not_called()

    def test_rejected_retirement_is_loud_and_closes_channel(self):
        channel = mock.Mock()
        with mock.patch.dict(os.environ, {'CADGEN_DAEMON_SOCKET': 'owned'}), mock.patch.object(client, '_connect', return_value=channel), mock.patch.object(client, '_send_json', return_value=True), mock.patch.object(client, '_recv_json', return_value={'exit': 2}):
            with self.assertRaisesRegex(RuntimeError, 'unexpected'):
                retire_owned_daemon('owned')
        channel.close.assert_called_once()

    def test_failed_send_is_loud_and_closes_channel(self):
        channel = mock.Mock()
        with mock.patch.dict(os.environ, {'CADGEN_DAEMON_SOCKET': 'owned'}), mock.patch.object(client, '_connect', return_value=channel), mock.patch.object(client, '_send_json', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'request'):
                retire_owned_daemon('owned')
        channel.close.assert_called_once()


class ManifestIsolation(unittest.TestCase):
    def test_lazy_inspection_uses_private_address_then_restores_environment(self):
        with mock.patch.dict(os.environ, {'CADGEN_DAEMON_SOCKET': 'ambient'}):
            with mock.patch.object(equivalence, '_manifest_in_current_env', side_effect=lambda root: {'address': os.environ['CADGEN_DAEMON_SOCKET']}):
                result = equivalence._manifest(Path('owned'), daemon_env={'CADGEN_DAEMON_SOCKET':'owned'})
            self.assertEqual(result, {'address': 'owned'})
            self.assertEqual(os.environ['CADGEN_DAEMON_SOCKET'], 'ambient')

    def test_cold_manifest_selects_cold_executor(self):
        fixture = equivalence.WarmOutputEquivalence()
        with mock.patch.object(fixture, '_tree', return_value=Path('cold')), mock.patch.object(equivalence, '_run', return_value=(0,'')), mock.patch.object(equivalence, '_manifest_in_current_env', side_effect=lambda root: {'daemon':os.environ['CADGEN_DAEMON']}):
            result, _ = fixture._cold('model.py', '', ['model.py'])
        self.assertEqual(result, {'daemon':'0'})


class ListenerShutdown(unittest.TestCase):
    def test_close_wakes_a_blocked_accept_without_authenticating(self):
        with tempfile.TemporaryDirectory(prefix='cad-close-') as tmp:
            address = rf'\\.\pipe\cad-close-{Path(tmp).name}' if os.name == 'nt' else str(Path(tmp)/'s.sock')
            listener = transport.Server(address, b'key')
            result = []
            thread = threading.Thread(target=lambda: result.append(listener.accept()), daemon=True)
            thread.start()
            deadline = time.monotonic()+3
            while not listener._accepting and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue(listener._accepting)
            listener.close()
            thread.join(3)
            self.assertFalse(thread.is_alive(), 'accept did not wake')
            self.assertEqual(result, [None])
            listener.close()

    def test_failed_native_wakeup_can_be_retried(self):
        listener = transport.Server.__new__(transport.Server)
        listener._guard = threading.Lock()
        listener._closed = False
        listener._wake_failed = False
        listener._accepting = True
        listener.address = 'owned'
        listener._family = 'AF_PIPE'
        with mock.patch.object(transport, '_wake_listener', side_effect=[OSError('native error'), None]) as wake:
            with self.assertRaises(OSError):
                listener.close()
            self.assertTrue(listener.closed)
            self.assertTrue(listener._wake_failed)
            listener.close()
            self.assertFalse(listener._wake_failed)
            self.assertEqual(wake.call_count, 2)


class ProcessRetirement(unittest.TestCase):
    def _start(self, root, *, idle=60):
        daemon = _Daemon(root)
        env = _env(**daemon.env(), CADGEN_DAEMON_SPARES='1', CADGEN_DAEMON_MAX_WORKERS='2', CADGEN_JOBS='1', CADGEN_DAEMON_IDLE_TIMEOUT=str(idle), CADGEN_CACHE_DIR=str(root/'store'), XDG_CACHE_HOME=str(root/'state'), LOCALAPPDATA=str(root/'state'))
        with open(root/'daemon.log', 'w', encoding='utf-8') as log:
            proc = subprocess.Popen([sys.executable, str(REPO_ROOT/'tests/python/support/lifecycle_daemon.py')], env=env, stdout=log, stderr=log)
        worker_identities = []
        def cleanup():
            if proc.poll() is None:
                with mock.patch.dict(os.environ, env):
                    retire_owned_daemon(daemon.address)
                proc.wait(timeout=10)
            self._assert_workers_exited(worker_identities)
        self.addCleanup(cleanup)
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            if proc.poll() is not None:
                self.fail((root/'daemon.log').read_text())
            try:
                with mock.patch.dict(os.environ, env):
                    channel = client._connect(daemon.address)
                    try:
                        client._send_json(channel, {'kind':'status'})
                        status = client._recv_json(channel, 2)['status']
                    finally:
                        channel.close()
                if status.get('workers'):
                    import psutil
                    worker_identities.extend(psutil.Process(w['pid']) for w in status['workers'])
                    return daemon, proc, env, worker_identities
            except (OSError, TypeError, KeyError):
                pass
            time.sleep(.02)
        self.fail('synthetic worker did not start: '+(root/'daemon.log').read_text())

    def _assert_workers_exited(self, workers):
        # Process identities were captured before retirement; is_running checks PID reuse.
        import psutil
        _, alive = psutil.wait_procs(workers, timeout=5)
        self.assertEqual(alive, [], f'workers survived: {[p.pid for p in alive]}')

    def test_success_and_failed_test_retire_only_the_owned_daemon(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='cad-retire-'))
        root=Path(tmp)
        ambient=root/'ambient'; ambient.mkdir()
        _, ambient_proc, _, _ = self._start(ambient)
        for failed in (False, True):
            with self.subTest(failed=failed):
                owned=root/str(failed); owned.mkdir()
                daemon, proc, env, workers = self._start(owned)
                with mock.patch.dict(os.environ, env):
                    if failed:
                        with self.assertRaisesRegex(AssertionError, 'test failure'):
                            with daemon:
                                raise AssertionError('test failure')
                    else:
                        with daemon:
                            pass
                self.assertEqual(proc.wait(timeout=10), 0)
                self._assert_workers_exited(workers)
                self.assertIsNone(ambient_proc.poll(), 'ambient daemon was stopped')

    def test_idle_shutdown_releases_daemon_and_worker(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='cad-idle-'))
        _, proc, _, workers = self._start(Path(tmp), idle=1)
        self.assertEqual(proc.wait(timeout=10), 0)
        self._assert_workers_exited(workers)


if __name__ == '__main__':
    unittest.main()
