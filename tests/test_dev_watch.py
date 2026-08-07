from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.dev_source_fingerprint import source_digest
from scripts.dev_watch import (
    api_build_commands,
    api_command,
    api_child_env,
    build_api_binaries,
    prebuilt_api_paths,
    prebuilt_source_matches,
    promote_api_binaries,
    run_api,
    select_api_startup,
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
    def test_prebuilt_paths_are_stable(self) -> None:
        self.assertEqual(
            (
                Path("/opt/grok2api-dev/bin/grok2api"),
                Path("/opt/grok2api-dev/bin/grok2api-migrate"),
            ),
            prebuilt_api_paths(Path("/opt/grok2api-dev")),
        )

    def test_prebuilt_source_matches_only_when_digest_and_exec_files_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cmd").mkdir()
            (root / "cmd/main.go").write_text("package main")
            artifact = root / "artifacts"
            (artifact / "bin").mkdir(parents=True)
            digest_file = artifact / "source.digest"
            digest_file.write_text(source_digest(root))
            main = artifact / "bin/grok2api"
            migrate = artifact / "bin/grok2api-migrate"
            main.touch(mode=0o755)
            migrate.touch(mode=0o755)

            self.assertTrue(prebuilt_source_matches(root, digest_file))

            digest_file.write_text("0" * 64)
            self.assertFalse(prebuilt_source_matches(root, digest_file))

    def test_prebuilt_source_matches_requires_executable_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cmd").mkdir()
            (root / "cmd/main.go").write_text("package main")
            artifact = root / "artifacts"
            (artifact / "bin").mkdir(parents=True)
            (artifact / "source.digest").write_text(source_digest(root))
            (artifact / "bin/grok2api").touch(mode=0o644)
            (artifact / "bin/grok2api-migrate").touch(mode=0o755)

            self.assertFalse(
                prebuilt_source_matches(root, artifact / "source.digest")
            )

    def test_prebuilt_source_matches_rejects_unreadable_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cmd").mkdir()
            (root / "cmd/main.go").write_text("package main")
            artifact = root / "artifacts"
            (artifact / "bin").mkdir(parents=True)
            digest_file = artifact / "source.digest"
            digest_file.write_text(source_digest(root))
            (artifact / "bin/grok2api").touch(mode=0o755)
            (artifact / "bin/grok2api-migrate").touch(mode=0o755)
            digest_file.chmod(0)

            self.assertFalse(prebuilt_source_matches(root, digest_file))

    def test_prebuilt_source_matches_rejects_binary_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cmd").mkdir()
            (root / "cmd/main.go").write_text("package main")
            artifact = root / "artifacts"
            bin_root = artifact / "bin"
            bin_root.mkdir(parents=True)
            digest_file = artifact / "source.digest"
            digest_file.write_text(source_digest(root))
            (bin_root / "grok2api").mkdir()
            (bin_root / "grok2api-migrate").touch(mode=0o755)

            self.assertFalse(prebuilt_source_matches(root, digest_file))

    def test_api_command_accepts_custom_main_binary(self) -> None:
        self.assertEqual(
            ["/app/entrypoint.sh", "/tmp/custom"],
            api_command(Path("/tmp/custom")),
        )

    def test_api_child_env_injects_migrate_binary_without_mutating_process_env(self) -> None:
        with patch.dict(os.environ, {"EXISTING": "value"}, clear=True):
            result = api_child_env(Path("/tmp/custom-migrate"))

            self.assertEqual("value", result["EXISTING"])
            self.assertEqual("/tmp/custom-migrate", result["GROK2API_MIGRATE_BIN"])
            self.assertNotIn("GROK2API_MIGRATE_BIN", os.environ)

    def test_select_api_startup_returns_matching_prebuilt_without_building(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cmd").mkdir()
            (root / "cmd/main.go").write_text("package main")
            artifact = root / "artifacts"
            (artifact / "bin").mkdir(parents=True)
            (artifact / "source.digest").write_text(source_digest(root))
            (artifact / "bin/grok2api").touch(mode=0o755)
            (artifact / "bin/grok2api-migrate").touch(mode=0o755)

            with patch("scripts.dev_watch.build_api_binaries") as build:
                selected = select_api_startup(root, artifact)

            self.assertEqual(
                (
                    artifact / "bin/grok2api",
                    artifact / "bin/grok2api-migrate",
                ),
                selected,
            )
            build.assert_not_called()

    def test_matching_prebuilt_skips_go_build_and_starts_with_migrate_env(self) -> None:
        class FakeChild:
            pid = 0

            def __init__(self) -> None:
                self.poll_values = iter((None, 0))

            def poll(self) -> int | None:
                return next(self.poll_values, 0)

        handlers: dict[int, object] = {}

        def register(signum: int, handler: object) -> object:
            handlers[signum] = handler
            return object()

        def stop_run(_seconds: float) -> None:
            handler = handlers.get(signal.SIGTERM)
            if handler is not None:
                handler(signal.SIGTERM, None)  # type: ignore[operator]

        child = FakeChild()
        selected = (
            Path("/opt/grok2api-dev/bin/grok2api"),
            Path("/opt/grok2api-dev/bin/grok2api-migrate"),
        )
        start = MagicMock(return_value=child)
        build = MagicMock()
        output = StringIO()
        with patch("scripts.dev_watch.snapshot", return_value={}), patch(
            "scripts.dev_watch.select_api_startup", return_value=selected
        ), patch("scripts.dev_watch.build_api_binaries", build), patch(
            "scripts.dev_watch.start_child", start
        ), patch("scripts.dev_watch.signal.signal", side_effect=register), patch(
            "scripts.dev_watch.time.sleep", side_effect=stop_run
        ), redirect_stdout(output):
            self.assertEqual(0, run_api())

        build.assert_not_called()
        start.assert_called_once_with(
            ["/app/entrypoint.sh", str(selected[0])],
            cwd=Path(__file__).resolve().parents[1],
            env=api_child_env(selected[1]),
        )
        self.assertIn("[dev-watch] using prebuilt Go API", output.getvalue())

    def test_dirty_initial_build_failure_does_not_start_child(self) -> None:
        handlers: dict[int, object] = {}

        def register(signum: int, handler: object) -> object:
            handlers[signum] = handler
            return object()

        def wait_once(
            _paths: object,
            _suffixes: object,
            previous: dict[str, tuple[int, int]],
        ) -> dict[str, tuple[int, int]]:
            handler = handlers[signal.SIGTERM]
            handler(signal.SIGTERM, None)  # type: ignore[operator]
            return previous

        start = MagicMock()
        build = MagicMock(return_value=False)
        output = StringIO()
        with patch("scripts.dev_watch.snapshot", return_value={}), patch(
            "scripts.dev_watch.select_api_startup", return_value=None
        ), patch("scripts.dev_watch.build_api_binaries", build), patch(
            "scripts.dev_watch.start_child", start
        ), patch("scripts.dev_watch.wait_for_change", side_effect=wait_once), patch(
            "scripts.dev_watch.signal.signal", side_effect=register
        ), redirect_stdout(output):
            self.assertEqual(0, run_api())

        start.assert_not_called()
        build.assert_called_once_with()
        self.assertIn("[dev-watch] source differs; building Go API", output.getvalue())
        self.assertIn(
            "[dev-watch] Go build failed; waiting for source change",
            output.getvalue(),
        )

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

    def test_running_build_failure_keeps_active_binary_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main"
            migrate = root / "migrate"
            main_next = root / "main.next"
            migrate_next = root / "migrate.next"
            main.write_text("old-main")
            migrate.write_text("old-migrate")

            def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                if command[-1] == "./cmd/grok2api":
                    main_next.write_text("new-main")
                    return SimpleNamespace(returncode=0)
                return SimpleNamespace(returncode=1)

            with patch("scripts.dev_watch.API_BINARY", main), patch(
                "scripts.dev_watch.API_MIGRATE_BINARY", migrate
            ), patch("scripts.dev_watch.API_BINARY_NEXT", main_next), patch(
                "scripts.dev_watch.API_MIGRATE_BINARY_NEXT", migrate_next
            ), patch("scripts.dev_watch.subprocess.run", side_effect=run):
                self.assertFalse(
                    build_api_binaries(
                        commands=[
                            ["go", "build", "-o", str(main_next), "./cmd/grok2api"],
                            [
                                "go",
                                "build",
                                "-o",
                                str(migrate_next),
                                "./cmd/grok2api-migrate",
                            ],
                        ],
                        cwd=root,
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
