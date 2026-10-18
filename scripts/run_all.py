"""Start every Rooted agent as its own process on its own port.

Usage: .venv/bin/python scripts/run_all.py [key ...]   (keys: orchestrator risk visualizer funding juniper mesa arbor)
Ctrl+C stops them all.
"""
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.runtime import AGENTS as _SPECS  # noqa: E402

AGENTS = [{"key": k, **v} for k, v in _SPECS.items()]


def _pipe(proc: subprocess.Popen, name: str) -> None:
    for line in proc.stdout:
        sys.stdout.write(f"{name:20s} | {line}")
        sys.stdout.flush()


def main() -> None:
    wanted = set(sys.argv[1:]) or {a["key"] for a in AGENTS}
    unknown = wanted - {a["key"] for a in AGENTS}
    if unknown:
        sys.exit(f"unknown agent keys: {', '.join(sorted(unknown))}")
    procs = []
    for spec in AGENTS:
        if spec["key"] not in wanted:
            continue
        env = {**os.environ, **spec.get("env", {}), "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(ROOT)}
        proc = subprocess.Popen(
            [sys.executable, "-m", spec["module"]],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(target=_pipe, args=(proc, spec["name"]), daemon=True).start()
        procs.append((spec["name"], proc))
        print(f"started {spec['name']} on port {spec['port']} (pid {proc.pid})")

    def stop(*_):
        for _, p in procs:
            p.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    for name, p in procs:
        code = p.wait()
        print(f"{name} exited with code {code}")


if __name__ == "__main__":
    main()
