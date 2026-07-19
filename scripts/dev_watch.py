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
API_BINARY = Path("/tmp/grok2api-dev")
API_MIGRATE_BINARY = Path("/tmp/grok2api-migrate-dev")
API_BINARY_NEXT = Path("/tmp/grok2api-dev.next")
API_MIGRATE_BINARY_NEXT = Path("/tmp/grok2api-migrate-dev.next")


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


def api_build_commands(go: str = "go") -> list[list[str]]:
    return [
        [go, "build", "-o", str(API_BINARY_NEXT), "./cmd/grok2api"],
        [go, "build", "-o", str(API_MIGRATE_BINARY_NEXT), "./cmd/grok2api-migrate"],
    ]


def api_command() -> list[str]:
    return ["/app/entrypoint.sh", str(API_BINARY)]


def promote_api_binaries(
    main_next: Path,
    migrate_next: Path,
    main: Path,
    migrate: Path,
    *,
    build_succeeded: bool,
) -> bool:
    if not build_succeeded or not main_next.is_file() or not migrate_next.is_file():
        return False
    os.replace(main_next, main)
    os.replace(migrate_next, migrate)
    return True


def build_api_binaries(
    commands: Sequence[Sequence[str]] | None = None,
    *,
    cwd: Path = ROOT,
) -> bool:
    for path in (API_BINARY_NEXT, API_MIGRATE_BINARY_NEXT):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    succeeded = True
    for command in commands or api_build_commands():
        result = subprocess.run(command, cwd=cwd, check=False)
        if result.returncode != 0:
            succeeded = False
            break
    return promote_api_binaries(
        API_BINARY_NEXT,
        API_MIGRATE_BINARY_NEXT,
        API_BINARY,
        API_MIGRATE_BINARY,
        build_succeeded=succeeded,
    )


def start_child(command: Sequence[str], *, cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(command, cwd=cwd, start_new_session=True)


def stop_child(
    child: subprocess.Popen[bytes] | None,
    *,
    terminate_timeout: float = 2.0,
    kill_timeout: float = 2.0,
) -> None:
    if child is None or child.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(child.pid, signal.SIGTERM)
    else:
        child.terminate()
    try:
        child.wait(timeout=terminate_timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(child.pid, signal.SIGKILL)
        else:
            child.kill()
        child.wait(timeout=kill_timeout)


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
        child = start_child(
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


def run_api() -> int:
    paths = [
        ROOT / "cmd",
        ROOT / "internal",
        ROOT / "go.mod",
        ROOT / "go.sum",
        ROOT / ".release-commit",
    ]
    suffixes = (".go", ".mod", ".sum", ".commit")
    state = snapshot(paths, suffixes)
    child: subprocess.Popen[bytes] | None = None
    stopping = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        stop_child(child)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while not stopping:
        if child is None:
            print("[dev-watch] building Go API", flush=True)
            if build_api_binaries():
                print("[dev-watch] starting Go API", flush=True)
                child = start_child(api_command(), cwd=ROOT)
            else:
                print("[dev-watch] Go build failed; waiting for source change", flush=True)
                state = wait_for_change(paths, suffixes, state)
                continue

        while child.poll() is None and not stopping:
            time.sleep(POLL_SECONDS)
            current = snapshot(paths, suffixes)
            if current == state:
                continue
            time.sleep(DEBOUNCE_SECONDS)
            state = snapshot(paths, suffixes)
            print("[dev-watch] Go source changed; rebuilding", flush=True)
            if build_api_binaries():
                stop_child(child)
                print("[dev-watch] restarting Go API", flush=True)
                child = start_child(api_command(), cwd=ROOT)
            else:
                print("[dev-watch] Go build failed; keeping current API", flush=True)

        if stopping:
            break
        if child.poll() is not None:
            print("[dev-watch] Go API exited; restarting", flush=True)
            time.sleep(1)
            child = None
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
    if len(sys.argv) != 2 or sys.argv[1] not in {"api", "solver", "assets"}:
        print("usage: dev_watch.py {api|solver|assets}", file=sys.stderr)
        return 2
    if sys.argv[1] == "api":
        return run_api()
    return run_solver() if sys.argv[1] == "solver" else run_assets()


if __name__ == "__main__":
    raise SystemExit(main())
