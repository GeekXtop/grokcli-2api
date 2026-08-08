from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DevWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        workflow = ROOT / ".github/workflows/build-fork-dev-ghcr.yml"
        self.assertTrue(workflow.is_file(), f"missing workflow: {workflow}")
        self.text = workflow.read_text()

    def test_trigger_permissions_and_concurrency(self) -> None:
        self.assertIn("branches: [local-customizations]", self.text)
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("packages: write", self.text)
        self.assertIn("contents: read", self.text)
        self.assertIn("cancel-in-progress: true", self.text)

    def test_checkout_does_not_persist_token_into_worktree(self) -> None:
        checkout_start = self.text.index("uses: actions/checkout@v4")
        checkout_block = self.text[checkout_start : self.text.find("\n      - name:", checkout_start)]
        self.assertIn("persist-credentials: false", checkout_block)
        self.assertIn("packages: write", self.text)
        self.assertIn("github_token=${{ github.token }}", self.text)

    def test_build_and_promotion_contract(self) -> None:
        for value in (
            "target: development",
            "platforms: linux/amd64",
            "ci-dev-",
            "local-customizations-dev",
            "sha-dev-",
            "buildcache-dev-amd64",
            "github_token=${{ github.token }}",
            "smoke_dev_image.sh",
        ):
            self.assertIn(value, self.text)
        self.assertIn("git/ref/heads/local-customizations", self.text)
        self.assertIn("imagetools create", self.text)


class SmokeDevImageScriptTests(unittest.TestCase):
    def test_smoke_script_checks_development_image_contract(self) -> None:
        script = ROOT / "scripts/ci/smoke_dev_image.sh"
        self.assertTrue(script.is_file(), f"missing smoke script: {script}")
        self.assertTrue(script.stat().st_mode & 0o100, "smoke script is not executable")
        script_text = script.read_text()
        for value in (
            "/opt/grok2api-dev/bin/grok2api",
            "/opt/grok2api-dev/bin/grok2api-migrate",
            "/opt/grok2api-dev/source.digest",
            "/usr/local/go/bin/go",
            "/go/pkg/mod",
            "/go/cache",
            "/app/scripts/dev_source_fingerprint.py /app",
            'test "$baked" = "$actual"',
        ):
            self.assertIn(value, script_text)


if __name__ == "__main__":
    unittest.main()
