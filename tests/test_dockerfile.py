from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DockerfileTests(unittest.TestCase):
    def test_camoufox_fetch_is_required_and_verified(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        camoufox_start = dockerfile.index(
            "python -m camoufox fetch",
            dockerfile.index("RUN --mount=type=secret,id=github_token"),
        )
        patchright_start = dockerfile.index(
            "RUN python -m patchright install chromium || true"
        )
        camoufox_block = dockerfile[camoufox_start:patchright_start]

        self.assertNotIn("|| true", camoufox_block)
        self.assertIn("python -m camoufox active", camoufox_block)
        self.assertIn("not fetched", camoufox_block)

    def test_camoufox_fetch_mounts_github_token_secret(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn(
            "--mount=type=secret,id=github_token,env=GITHUB_TOKEN",
            dockerfile,
        )

    def test_development_target_includes_go_toolchain_and_production_is_default(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn(
            "FROM python:3.12-slim-bookworm AS runtime-base",
            dockerfile,
        )
        self.assertIn("FROM runtime-base AS development", dockerfile)
        self.assertIn(
            "COPY --from=go-builder /usr/local/go /usr/local/go",
            dockerfile,
        )
        self.assertIn("FROM runtime-base AS production\nCMD", dockerfile)


if __name__ == "__main__":
    unittest.main()
