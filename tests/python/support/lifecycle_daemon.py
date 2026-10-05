"""Real daemon/pool lifecycle with a tiny protocol worker, without a CAD kernel."""

import subprocess
import sys

from cadgen.daemon import pool, server
from cadgen._internal import runtime_limits


WORKER = """import json, os, sys
print(json.dumps({'ready': os.getpid()}), flush=True)
for line in sys.stdin:
    print(json.dumps({'exit': 0}), flush=True)
"""
original_popen = subprocess.Popen


def tiny_worker(args, **kwargs):
    assert args == [sys.executable, '-m', 'cadgen.daemon.worker']
    return original_popen([sys.executable, '-c', WORKER], **kwargs)


# Only synthetic worker imports are admitted here. Production admission is unchanged.
runtime_limits._windows_available_memory = lambda: None
pool.subprocess.Popen = tiny_worker
raise SystemExit(server.serve())
