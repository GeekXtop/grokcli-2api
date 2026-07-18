# Development Image Camoufox Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the root development image build fail unless the Camoufox browser required by the local Turnstile solver is installed.

**Architecture:** Keep the change at the image-build boundary. Compose exposes an optional `GITHUB_TOKEN` as a BuildKit secret, and the Dockerfile mounts it only while fetching Camoufox. Focused static regression tests lock down the shell-command and Compose structure, while the Dockerfile treats Camoufox as required, validates its active version, and keeps Patchright as an independently optional fallback.

**Tech Stack:** Dockerfile, Python `unittest`, Docker Compose

## Global Constraints

- Modify the root `Dockerfile`, `compose.dev.yml`, and focused regression tests.
- Do not change `turnstile-solver/Dockerfile`, host startup scripts, health checks, or frontend error formatting.
- Camoufox fetch and validation must fail the image build on error.
- Patchright installation remains optional.
- Never persist `GITHUB_TOKEN` in an image layer or runtime environment.

---

### Task 1: Require a fetched Camoufox browser in the development image

**Files:**
- Create: `tests/test_dockerfile.py`
- Modify: `Dockerfile:1,92-101`
- Modify: `compose.dev.yml:18-23,60-63`
- Modify: `tests/test_dev_compose.py:65-78`

**Interfaces:**
- Consumes: the root `Dockerfile` text, the Compose JSON model, and the `python -m camoufox` CLI already installed from `turnstile-solver/requirements.txt`.
- Produces: a development image in which `python -m camoufox active` cannot report `not fetched` after a successful build.

- [ ] **Step 1: Write the failing regression test**

Create `tests/test_dockerfile.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DockerfileTests(unittest.TestCase):
    def test_camoufox_fetch_is_required_and_verified(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        camoufox_start = dockerfile.index("RUN python -m camoufox fetch")
        patchright_start = dockerfile.index(
            "RUN python -m patchright install chromium || true"
        )
        camoufox_block = dockerfile[camoufox_start:patchright_start]

        self.assertNotIn("|| true", camoufox_block)
        self.assertIn("python -m camoufox active", camoufox_block)
        self.assertIn("not fetched", camoufox_block)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python -m unittest tests.test_dockerfile -v
```

Expected: `ERROR` or `FAIL` because the current Dockerfile does not contain a separate optional Patchright `RUN` command and has no Camoufox active-version validation.

- [ ] **Step 3: Implement the minimal Dockerfile fix**

Replace the existing browser-prefetch command with:

```dockerfile
# syntax=docker/dockerfile:1.10

# Camoufox is required by the local Turnstile solver. Fail the image build if
# its repository sync/download silently leaves the active channel unfetched.
RUN --mount=type=secret,id=github_token,env=GITHUB_TOKEN,required=false \
    python -m camoufox fetch \
    && active="$(python -m camoufox active)" \
    && case "$active" in \
         *"not fetched"*) echo "Camoufox browser was not installed: $active" >&2; exit 1 ;; \
       esac

# Chromium is only an optional fallback path.
RUN python -m patchright install chromium || true
```

Add this under `api-dev.build` in `compose.dev.yml`:

```yaml
secrets:
  - github_token
```

and add this top-level declaration:

```yaml
secrets:
  github_token:
    environment: GITHUB_TOKEN
```

- [ ] **Step 4: Run targeted and related tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_dockerfile tests.test_dev_compose tests.test_dev_watch -v
```

Expected: all tests pass with exit code 0.

- [ ] **Step 5: Build and inspect a fresh development image**

Run:

```bash
GROK2API_ENV_FILE=.env.dev.example docker compose -f compose.dev.yml build api-dev
docker run --rm --entrypoint python grokcli-2api:dev -m camoufox active
docker run --rm --entrypoint python grokcli-2api:dev -m camoufox version
```

Expected: the build exits 0, `camoufox active` does not contain `not fetched`, and `camoufox version` reports `Installed Yes` with a concrete browser path.

- [ ] **Step 6: Check the final diff and commit**

Run:

```bash
git diff --check
git status --short
git diff -- Dockerfile tests/test_dockerfile.py
git add Dockerfile tests/test_dockerfile.py
git commit -m "fix(dev): require Camoufox browser in image"
```

Expected: only the scoped Dockerfile and test changes are committed; the design and implementation-plan commits remain separate.
