from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.dev_source_fingerprint import source_digest, source_files


ROOT = Path(__file__).resolve().parents[1]


class SourceFingerprintTests(unittest.TestCase):
    def test_source_files_are_relative_sorted_and_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cmd").mkdir()
            (root / "internal").mkdir()
            (root / "cmd/z.go").write_text("z")
            (root / "cmd/a.go").write_text("a")
            (root / "internal/x.go").write_text("x")
            (root / "other.go").write_text("ignored")
            (root / "go.mod").write_text("module example")
            (root / ".release-commit").write_text("abc\n")
            self.assertEqual(
                [
                    Path(".release-commit"),
                    Path("cmd/a.go"),
                    Path("cmd/z.go"),
                    Path("go.mod"),
                    Path("internal/x.go"),
                ],
                source_files(root),
            )

    def test_digest_changes_for_content_and_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cmd").mkdir()
            source = root / "cmd/main.go"
            source.write_text("package main")
            first = source_digest(root)
            source.write_text("package main\n")
            self.assertNotEqual(first, source_digest(root))
            source.rename(root / "cmd/renamed.go")
            self.assertNotEqual(first, source_digest(root))

    def test_cli_prints_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cmd").mkdir()
            (root / "cmd/main.go").write_text("package main")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/dev_source_fingerprint.py"),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertRegex(result.stdout, r"^[0-9a-f]{64}\n$")


if __name__ == "__main__":
    unittest.main()
