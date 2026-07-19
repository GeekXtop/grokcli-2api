from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts.dev_watch import (
    api_build_commands,
    api_command,
    promote_api_binaries,
    snapshot,
    solver_command,
    start_child,
    stop_child,
)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_tracks_only_requested_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("one")
            (root / "ignored.log").write_text("log")

            result = snapshot([root], (".py",))

            self.assertEqual([str(root / "a.py")], sorted(result))

    def test_snapshot_changes_after_file_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.py"
            source.write_text("one")
            before = snapshot([root], (".py",))
            source.write_text("a longer value")
            os.utime(source, None)

            self.assertNotEqual(before, snapshot([root], (".py",)))


class SolverCommandTests(unittest.TestCase):
    def test_solver_command_uses_environment(self) -> None:
        command = solver_command(
            {
                "TURNSTILE_BROWSER_TYPE": "camoufox",
                "TURNSTILE_THREAD": "2",
                "TURNSTILE_HOST": "127.0.0.1",
                "TURNSTILE_PORT": "5073",
            }
        )

        self.assertIn("camoufox", command)
        self.assertIn("2", command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("5073", command)
        self.assertEqual("api_solver.py", Path(command[1]).name)


class ApiWatcherTests(unittest.TestCase):
    def test_api_build_commands_compile_main_and_migrations_to_next_paths(self) -> None:
        self.assertEqual(
            [
                ["go", "build", "-o", "/tmp/grok2api-dev.next", "./cmd/grok2api"],
                [
                    "go",
                    "build",
                    "-o",
                    "/tmp/grok2api-migrate-dev.next",
                    "./cmd/grok2api-migrate",
                ],
            ],
            api_build_commands(),
        )

    def test_api_command_runs_through_entrypoint(self) -> None:
        self.assertEqual(
            ["/app/entrypoint.sh", "/tmp/grok2api-dev"],
            api_command(),
        )

    def test_failed_api_build_does_not_promote_next_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main"
            migrate = root / "migrate"
            main_next = root / "main.next"
            migrate_next = root / "migrate.next"
            main.write_text("old-main")
            migrate.write_text("old-migrate")
            main_next.write_text("new-main")
            migrate_next.write_text("new-migrate")

            self.assertFalse(
                promote_api_binaries(
                    main_next,
                    migrate_next,
                    main,
                    migrate,
                    build_succeeded=False,
                )
            )
            self.assertEqual("old-main", main.read_text())
            self.assertEqual("old-migrate", migrate.read_text())


class ChildProcessTests(unittest.TestCase):
    def test_child_starts_in_its_own_process_group(self) -> None:
        child = start_child(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=Path.cwd(),
        )
        try:
            self.assertEqual(child.pid, os.getpgid(child.pid))
        finally:
            stop_child(child, terminate_timeout=0.2, kill_timeout=1.0)

    def test_stop_child_quickly_kills_sigterm_ignoring_process(self) -> None:
        child = start_child(
            [
                sys.executable,
                "-c",
                (
                    "import signal,time; "
                    "signal.signal(signal.SIGTERM, lambda *_: None); "
                    "time.sleep(30)"
                ),
            ],
            cwd=Path.cwd(),
        )
        time.sleep(0.1)
        started = time.monotonic()

        stop_child(child, terminate_timeout=0.2, kill_timeout=1.0)

        self.assertIsNotNone(child.poll())
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
