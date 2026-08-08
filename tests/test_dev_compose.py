from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DevComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        env = dict(os.environ)
        env["GROK2API_ENV_FILE"] = ".env.dev.example"
        # Exercise the documented default image even if a developer has a
        # local override in their shell environment.
        env.pop("GROK2API_DEV_IMAGE", None)
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.dev.yml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        cls.config = json.loads(result.stdout)

        overlay_env = dict(env)
        overlay_env["GITHUB_TOKEN"] = "test-token"
        overlay_result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.dev.yml",
                "-f",
                "compose.dev.local.yml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=overlay_env,
            text=True,
            capture_output=True,
            check=True,
        )
        cls.overlay_config = json.loads(overlay_result.stdout)

    def test_default_uses_ghcr_without_build_or_secret(self):
        for service in self.config["services"].values():
            self.assertEqual("ghcr.io/geekxtop/grokcli-2api:dev", service["image"])
            self.assertEqual("always", service["pull_policy"])
            self.assertNotIn("build", service)
        self.assertNotIn("secrets", self.config)

    def test_source_mount_and_watchers_remain_enabled(self):
        for service in self.config["services"].values():
            self.assertTrue(
                any(
                    volume == ".:/app"
                    or (
                        isinstance(volume, dict)
                        and volume.get("target") == "/app"
                        and volume.get("source")
                        and Path(volume.get("source", "")).resolve() == ROOT.resolve()
                    )
                    for volume in service["volumes"]
                )
            )
        self.assertEqual(
            ["python", "/app/scripts/dev_watch.py", "api"],
            self.config["services"]["api-dev"]["entrypoint"],
        )

    def test_pinned_sha_overlay_disables_host_source_mount(self):
        env = dict(os.environ)
        env["GROK2API_ENV_FILE"] = ".env.dev.example"
        env["GROK2API_DEV_IMAGE"] = (
            "ghcr.io/geekxtop/grokcli-2api:sha-dev-0123456789ab"
        )
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.dev.yml",
                "-f",
                "compose.dev.pinned.yml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        config = json.loads(result.stdout)
        for service in config["services"].values():
            self.assertFalse(
                any(
                    (volume == ".:/app")
                    or (
                        isinstance(volume, dict)
                        and volume.get("target") == "/app"
                        and Path(volume.get("source", "")).resolve() == ROOT.resolve()
                    )
                    for volume in service.get("volumes", [])
                )
            )

    def test_backup_overlay_uses_old_images_and_volume_sources(self):
        env = dict(os.environ)
        env.update(
            {
                "GROK2API_ENV_FILE": ".env.dev.example",
                "GROK2API_DEV_BACKUP_API_IMAGE": "sha256:api-old",
                "GROK2API_DEV_BACKUP_SOLVER_IMAGE": "sha256:solver-old",
                "GROK2API_DEV_BACKUP_ASSETS_IMAGE": "sha256:assets-old",
                "GROK2API_DEV_OLD_API_NAME": "grokcli-2api-api-dev",
                "GROK2API_DEV_OLD_SOLVER_NAME": "grokcli-2api-solver-dev",
                "GROK2API_DEV_OLD_ASSETS_NAME": "grokcli-2api-assets-dev",
                "GROK2API_DEV_BACKUP_API_NAME": "g2a-dev-backup-api",
                "GROK2API_DEV_BACKUP_SOLVER_NAME": "g2a-dev-backup-solver",
                "GROK2API_DEV_BACKUP_ASSETS_NAME": "g2a-dev-backup-assets",
            }
        )
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                "g2a-dev-backup-test",
                "-f",
                "compose.dev.yml",
                "-f",
                "compose.dev.backup.yml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        config = json.loads(result.stdout)
        for service in config["services"].values():
            self.assertEqual("never", service["pull_policy"])
            self.assertEqual([], service.get("volumes", []))
            self.assertEqual(1, len(service["volumes_from"]))
            self.assertTrue(service["volumes_from"][0].startswith("container:"))

    def test_local_overlay_uses_prebuilt_image_and_only_api_builds(self):
        services = self.overlay_config["services"]
        for service in services.values():
            self.assertEqual("grokcli-2api:dev", service["image"])
            self.assertEqual("never", service["pull_policy"])

        self.assertEqual(
            {"api-dev"},
            {name for name, service in services.items() if "build" in service},
        )
        self.assertEqual(
            ROOT.resolve(), Path(services["api-dev"]["build"]["context"]).resolve()
        )
        self.assertEqual("Dockerfile", services["api-dev"]["build"]["dockerfile"])
        self.assertEqual("development", services["api-dev"]["build"]["target"])
        self.assertEqual([{"source": "github_token"}], services["api-dev"]["build"]["secrets"])
        self.assertEqual(
            "GITHUB_TOKEN",
            self.overlay_config["secrets"]["github_token"]["environment"],
        )

    def test_only_development_services_are_defined(self) -> None:
        self.assertEqual(
            {"api-dev", "solver-dev", "assets-dev"},
            set(self.config["services"]),
        )

    def test_all_services_use_host_networking(self) -> None:
        for service in self.config["services"].values():
            self.assertEqual("host", service["network_mode"])

    def test_api_has_reload_and_single_worker(self) -> None:
        service = self.config["services"]["api-dev"]
        environment = self.config["services"]["api-dev"]["environment"]
        self.assertEqual(["python", "/app/scripts/dev_watch.py", "api"], service["entrypoint"])
        self.assertEqual("go", environment["GROK2API_RUNTIME"])
        self.assertEqual("/tmp/grok2api-migrate-dev", environment["GROK2API_MIGRATE_BIN"])
        self.assertEqual("0.0.0.0", environment["GROK2API_HOST"])
        self.assertEqual("40081", environment["GROK2API_PORT"])
        self.assertEqual(
            "http://192.168.100.105:40081",
            environment.get("GROK2API_PUBLIC_BASE_URL"),
        )
        self.assertEqual("1", environment["GROK2API_RELOAD"])
        self.assertEqual("1", environment["GROK2API_WORKERS"])
        self.assertEqual("0", environment["GROK2API_INLINE_SOLVER"])

    def test_solver_stays_bound_to_loopback(self) -> None:
        environment = self.config["services"]["solver-dev"]["environment"]
        self.assertEqual("127.0.0.1", environment["TURNSTILE_HOST"])
        self.assertEqual("5072", environment["TURNSTILE_PORT"])

    def test_env_example_is_not_ignored(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", ".env.dev.example"],
            cwd=ROOT,
            capture_output=True,
        )
        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
