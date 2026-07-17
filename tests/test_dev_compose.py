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
        environment = self.config["services"]["api-dev"]["environment"]
        self.assertEqual("1", environment["GROK2API_RELOAD"])
        self.assertEqual("1", environment["GROK2API_WORKERS"])
        self.assertEqual("0", environment["GROK2API_INLINE_SOLVER"])

    def test_env_example_is_not_ignored(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", ".env.dev.example"],
            cwd=ROOT,
            capture_output=True,
        )
        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
