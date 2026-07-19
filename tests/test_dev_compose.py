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
        build = service["build"]
        environment = self.config["services"]["api-dev"]["environment"]
        self.assertEqual("development", build["target"])
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

    def test_only_api_service_builds_the_shared_image(self) -> None:
        services_with_build = {
            name
            for name, service in self.config["services"].items()
            if "build" in service
        }
        self.assertEqual({"api-dev"}, services_with_build)

    def test_api_build_reads_github_token_as_a_secret(self) -> None:
        build = self.config["services"]["api-dev"]["build"]
        self.assertEqual([{"source": "github_token"}], build["secrets"])
        self.assertEqual(
            "GITHUB_TOKEN",
            self.config["secrets"]["github_token"]["environment"],
        )

    def test_env_example_is_not_ignored(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", ".env.dev.example"],
            cwd=ROOT,
            capture_output=True,
        )
        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
