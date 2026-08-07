from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DevPullScriptTests(unittest.TestCase):
    def test_script_pulls_and_recreates_without_build(self):
        text = (ROOT / "scripts/g2a-dev-pull.sh").read_text()
        self.assertIn("docker compose", text)
        self.assertIn("pull", text)
        self.assertIn("up -d --force-recreate --no-build", text)
        self.assertNotIn("source .env.dev", text)
        self.assertNotIn("docker compose down", text)

    def test_script_has_bounded_health_loop_and_diagnostics(self):
        text = (ROOT / "scripts/g2a-dev-pull.sh").read_text()
        self.assertRegex(text, r"90")
        for endpoint in ("/health", "/ready"):
            self.assertIn(endpoint, text)
        self.assertIn("docker compose ps", text)
        self.assertIn("docker compose logs", text)


if __name__ == "__main__":
    unittest.main()
