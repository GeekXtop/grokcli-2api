from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EntrypointTests(unittest.TestCase):
    def test_custom_command_is_selected_before_default_binary_validation(self) -> None:
        entrypoint = (ROOT / "entrypoint.sh").read_text()
        custom_args = entrypoint.index('if [[ "$#" -gt 0 ]]')
        binary_validation = entrypoint.index('if [[ ! -x "${APP_CMD[0]}" ]]')
        self.assertLess(custom_args, binary_validation)


if __name__ == "__main__":
    unittest.main()
