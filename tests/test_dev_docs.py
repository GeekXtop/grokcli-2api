import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DevDocumentationTests(unittest.TestCase):
    def test_readme_has_pull_hot_reload_and_rescue_commands(self):
        text = (ROOT / "README.md").read_text()
        for value in (
            "./scripts/g2a-dev-pull.sh",
            "GROK2API_DEV_IMAGE",
            "sha-dev-<commit>",
            "linux/amd64",
            "compose.dev.local.yml",
            "compose.dev.pinned.yml",
            "image-only",
            "--no-build",
        ):
            self.assertIn(value, text)

    def test_upgrade_doc_warns_about_volumes_and_has_rollback(self):
        text = (ROOT / "docs/UPGRADE.md").read_text()
        self.assertIn("不删除", text)
        self.assertIn("sha-dev-", text)
        self.assertIn("g2a-dev-pull.sh", text)
        self.assertIn("compose.dev.pinned.yml", text)
        self.assertIn("image-only", text)

    def test_env_example_declares_dev_image_override(self):
        text = (ROOT / ".env.dev.example").read_text()
        self.assertIn("GROK2API_DEV_IMAGE=", text)
        self.assertIn("GROK2API_RELOAD=1", text)


if __name__ == "__main__":
    unittest.main()
