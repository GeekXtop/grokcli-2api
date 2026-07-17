#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLL_SECONDS = 0.5
DEBOUNCE_SECONDS = 0.25


def snapshot(
    paths: Sequence[Path], suffixes: tuple[str, ...]
) -> dict[str, tuple[int, int]]:
    state: dict[str, tuple[int, int]] = {}
    for root in paths:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file() or path.suffix not in suffixes:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            state[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return state


def solver_command(env: Mapping[str, str]) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "turnstile-solver" / "api_solver.py"),
        "--browser_type",
        env.get("TURNSTILE_BROWSER_TYPE", "camoufox"),
        "--thread",
        env.get("TURNSTILE_THREAD", "1"),
        "--host",
        env.get("TURNSTILE_HOST", "127.0.0.1"),
        "--port",
        env.get("TURNSTILE_PORT", "5072"),
        "--debug",
    ]


def stop_child(child: subprocess.Popen[bytes] | None) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3)


def wait_for_change(
    paths: Sequence[Path],
    suffixes: tuple[str, ...],
    previous: dict[str, tuple[int, int]],
) -> dict[str, tuple[int, int]]:
    while True:
        time.sleep(POLL_SECONDS)
        current = snapshot(paths, suffixes)
        if current != previous:
            time.sleep(DEBOUNCE_SECONDS)
            return snapshot(paths, suffixes)


def run_solver() -> int:
    paths = [ROOT / "turnstile-solver"]
    state = snapshot(paths, (".py",))
    child: subprocess.Popen[bytes] | None = None
    stopping = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        stop_child(child)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while not stopping:
        print("[dev-watch] starting Turnstile solver", flush=True)
        child = subprocess.Popen(
            solver_command(os.environ), cwd=ROOT / "turnstile-solver"
        )
        while child.poll() is None and not stopping:
            time.sleep(POLL_SECONDS)
            current = snapshot(paths, (".py",))
            if current != state:
                state = current
                time.sleep(DEBOUNCE_SECONDS)
                print("[dev-watch] solver source changed; restarting", flush=True)
                stop_child(child)
                break
        if stopping:
            break
        if child.poll() is not None:
            time.sleep(1)
    return 0


def run_assets() -> int:
    paths = [ROOT / "static" / "js", ROOT / "static" / "css"]
    command = [sys.executable, str(ROOT / "scripts" / "build_admin_assets.py")]
    while True:
        print("[dev-watch] building admin assets", flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode != 0:
            print(
                f"asset build failed with exit code {result.returncode}",
                file=sys.stderr,
            )
        state = snapshot(paths, (".js", ".css"))
        wait_for_change(paths, (".js", ".css"), state)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"solver", "assets"}:
        print("usage: dev_watch.py {solver|assets}", file=sys.stderr)
        return 2
    return run_solver() if sys.argv[1] == "solver" else run_assets()


if __name__ == "__main__":
    raise SystemExit(main())
