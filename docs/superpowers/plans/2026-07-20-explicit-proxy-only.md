# Explicit Proxy Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make empty proxy configuration mean direct connectivity for registration and account-pool traffic while preserving every explicitly configured proxy source.

**Architecture:** Centralize effective outbound selection in `proxy_pool.py`: durable outbound settings win, followed by project proxy environment variables, the stored registration fallback, and explicit generic proxy environment variables. Registration parses only a request-specific proxy itself, then reuses that same effective outbound source; neither path performs network discovery or probes hard-coded local endpoints.

**Tech Stack:** Python 3.12, standard-library `unittest`/`unittest.mock`, existing proxy parsing helpers, Go regression suite, Docker Compose development stack.

## Global Constraints

- Do not add a configuration field or discovery feature flag.
- Continue supporting `GROK2API_XAI_PROXY_POOL`, `GROK2API_PROXY_POOL`, `GROK2API_XAI_PROXY`, `GROK2API_PROXY`, and `GROK_CLI_PROXY`.
- Treat `GROK2API_AUTO_PROXY`, `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and lowercase variants as explicit proxy URLs.
- Never probe `privoxy`, `warp-proxy`, `host.docker.internal`, or common local proxy ports.
- Empty settings and environment must return direct connectivity with `enabled=false`, `source=none`, and an empty pool.
- Preserve request-specific registration proxy precedence over the global outbound source, and global outbound settings precedence over environment fallbacks.

---

### Task 1: Lock account-pool proxy semantics with regression tests

**Files:**
- Create: `tests/test_proxy_selection.py`
- Test: `grok2api/upstream/proxy_pool.py`

**Interfaces:**
- Consumes: `get_outbound_proxy_source() -> dict[str, Any]`, `invalidate_outbound_proxy_cache() -> None`.
- Produces: regression coverage for empty, project-specific, and generic explicit environment proxy selection.

- [ ] **Step 1: Write the failing account-pool tests**

```python
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from grok2api.admin import settings_store
from grok2api.upstream import proxy_pool


class ProxySelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.outbound = patch.object(
            settings_store,
            "get_outbound_proxy_config",
            return_value={
                "enabled": True,
                "proxy": "",
                "proxy_username": "",
                "proxy_password": "",
                "proxy_strategy": "round_robin",
            },
        )
        self.registration = patch.object(
            settings_store,
            "get_registration_config",
            return_value={"proxy": ""},
        )
        self.outbound.start()
        self.registration.start()
        proxy_pool.invalidate_outbound_proxy_cache()

    def tearDown(self) -> None:
        proxy_pool.invalidate_outbound_proxy_cache()
        self.registration.stop()
        self.outbound.stop()
        self.env.stop()

    def test_empty_configuration_is_direct_and_does_not_probe_network(self) -> None:
        with patch("socket.create_connection") as connect:
            source = proxy_pool.get_outbound_proxy_source()

        self.assertEqual([], source["pool"])
        self.assertFalse(source["enabled"])
        self.assertEqual("none", source["source"])
        connect.assert_not_called()

    def test_project_proxy_environment_is_explicit(self) -> None:
        for key in (
            "GROK2API_XAI_PROXY_POOL",
            "GROK2API_PROXY_POOL",
            "GROK2API_XAI_PROXY",
            "GROK2API_PROXY",
            "GROK_CLI_PROXY",
        ):
            with self.subTest(key=key), patch.dict(
                os.environ, {key: "http://project-proxy:8080"}, clear=True
            ):
                proxy_pool.invalidate_outbound_proxy_cache()
                source = proxy_pool.get_outbound_proxy_source()
                self.assertEqual(["http://project-proxy:8080"], source["pool"])
                self.assertEqual("env", source["source"])

    def test_generic_proxy_environment_is_explicit_without_discovery(self) -> None:
        for key in (
            "GROK2API_AUTO_PROXY",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "https_proxy",
            "http_proxy",
            "ALL_PROXY",
            "all_proxy",
        ):
            with self.subTest(key=key), patch.dict(
                os.environ, {key: "http://generic-proxy:8080"}, clear=True
            ), patch("socket.create_connection") as connect:
                proxy_pool.invalidate_outbound_proxy_cache()
                source = proxy_pool.get_outbound_proxy_source()
                self.assertEqual(["http://generic-proxy:8080"], source["pool"])
                self.assertEqual("env", source["source"])
                connect.assert_not_called()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_proxy_selection.ProxySelectionTests -v`

Expected: FAIL because empty configuration becomes `source=auto`, network probing occurs, and generic environment proxies are reported as automatically discovered.

- [ ] **Step 3: Do not change production code yet**

Confirm the failures correspond to implicit discovery rather than import, fixture, or proxy-normalization errors.

---

### Task 2: Lock registration precedence and direct fallback with regression tests

**Files:**
- Modify: `tests/test_proxy_selection.py`
- Test: `grok2api/upstream/grok_build_adapter.py`

**Interfaces:**
- Consumes: `_proxy_pool(proxy_text, username=None, password=None) -> list[str]`.
- Produces: regression coverage for request proxy precedence, shared global source reuse, and direct registration when all sources are empty.

- [ ] **Step 1: Add failing registration tests**

```python
from grok2api.upstream import grok_build_adapter


    def test_registration_without_proxy_uses_direct_connection(self) -> None:
        with patch("socket.create_connection") as connect:
            pool = grok_build_adapter._proxy_pool(None)

        self.assertEqual([], pool)
        connect.assert_not_called()

    def test_registration_request_proxy_has_highest_precedence(self) -> None:
        self.outbound.return_value = {
            "enabled": True,
            "proxy": "http://global-proxy:8080",
            "proxy_username": "",
            "proxy_password": "",
            "proxy_strategy": "round_robin",
        }

        pool = grok_build_adapter._proxy_pool("http://form-proxy:8080")

        self.assertEqual(["http://form-proxy:8080"], pool)

    def test_registration_prefers_global_settings_over_environment(self) -> None:
        self.outbound.return_value = {
            "enabled": True,
            "proxy": "http://global-proxy:8080",
            "proxy_username": "",
            "proxy_password": "",
            "proxy_strategy": "round_robin",
        }
        with patch.dict(
            os.environ,
            {"GROK2API_XAI_PROXY": "http://environment-proxy:8080"},
            clear=True,
        ):
            proxy_pool.invalidate_outbound_proxy_cache()
            pool = grok_build_adapter._proxy_pool(None)

        self.assertEqual(["http://global-proxy:8080"], pool)
```

- [ ] **Step 2: Run the registration tests and verify RED**

Run: `python3 -m unittest tests.test_proxy_selection.ProxySelectionTests.test_registration_without_proxy_uses_direct_connection tests.test_proxy_selection.ProxySelectionTests.test_registration_prefers_global_settings_over_environment -v`

Expected: FAIL because registration invokes automatic discovery when empty and currently parses project environment variables before consulting global outbound settings.

- [ ] **Step 3: Keep explicit request precedence test as a passing guard**

Run: `python3 -m unittest tests.test_proxy_selection.ProxySelectionTests.test_registration_request_proxy_has_highest_precedence -v`

Expected: PASS, documenting existing behavior that must remain unchanged.

---

### Task 3: Remove implicit discovery and unify effective proxy selection

**Files:**
- Modify: `grok2api/upstream/proxy_pool.py`
- Modify: `grok2api/upstream/grok_build_adapter.py`
- Test: `tests/test_proxy_selection.py`

**Interfaces:**
- Consumes: `_env_proxy_text() -> str`, `parse_proxy_pool(..., fallback_env=False) -> list[str]`.
- Produces: `_generic_env_proxy_text() -> str`; `get_outbound_proxy_source()` with only explicit sources; `_proxy_pool()` that delegates non-form selection to the outbound source.

- [ ] **Step 1: Add an explicit generic environment helper**

Add beside `_env_proxy_text()` in `grok2api/upstream/proxy_pool.py`:

```python
def _generic_env_proxy_text() -> str:
    """Return the first explicitly configured generic proxy URL."""
    for key in (
        "GROK2API_AUTO_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return ""
```

- [ ] **Step 2: Delete network discovery helpers**

Remove `_auto_proxy_candidates()` and `first_working_proxy()` from `grok2api/upstream/proxy_pool.py`, including all hard-coded peer/host endpoints and TCP-connect probing.

- [ ] **Step 3: Resolve generic environment proxies as explicit input**

In `get_outbound_proxy_source()`, after the stored registration fallback and before constructing the cache key, add:

```python
    if not text:
        generic_env_text = _generic_env_proxy_text()
        if generic_env_text:
            text = generic_env_text
            source = "env"
```

Update its preference docstring to list explicit generic proxy environment variables last. Delete the final `if not out.get("pool"):` automatic-discovery block entirely.

- [ ] **Step 4: Make registration parse only the request-specific proxy locally**

Change `grok2api/upstream/grok_build_adapter.py::_proxy_pool()` to import only `parse_proxy_pool` and `get_outbound_proxy_source`, parse `proxy_text` with `fallback_env=False`, then return the effective outbound source pool when the form value is empty:

```python
        pool = parse_proxy_pool(
            proxy_text,
            username=username,
            password=password,
            fallback_env=False,
        )
        if pool:
            return pool
        src = get_outbound_proxy_source() or {}
        if src.get("enabled", True):
            pool = list(src.get("pool") or [])
            if pool:
                return pool
```

Delete the `first_working_proxy` import and fallback. Update the preference comment to state: request proxy, effective global/explicit environment source, then direct connection.

- [ ] **Step 5: Run the focused suite and verify GREEN**

Run: `python3 -m unittest tests.test_proxy_selection -v`

Expected: all proxy-selection tests PASS with no socket probes.

- [ ] **Step 6: Review the diff for forbidden discovery behavior**

Run: `rg -n "privoxy|warp-proxy|host\\.docker\\.internal|first_working_proxy|source.*auto" grok2api/upstream tests/test_proxy_selection.py`

Expected: no matches related to proxy discovery or `source=auto`.

---

### Task 4: Verify repository and development runtime behavior

**Files:**
- Verify: `tests/test_proxy_selection.py`
- Verify: `grok2api/upstream/proxy_pool.py`
- Verify: `grok2api/upstream/grok_build_adapter.py`
- Verify: `compose.dev.yml`

**Interfaces:**
- Consumes: completed proxy selection implementation and development hot reload stack.
- Produces: fresh test/build/runtime evidence that empty configuration stays direct.

- [ ] **Step 1: Run all Python regression tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests PASS, including Compose, Dockerfile, hot-reload, and proxy-selection coverage.

- [ ] **Step 2: Run all Go tests**

Run: `go test ./...`

Expected: all packages PASS.

- [ ] **Step 3: Validate Compose rendering**

Run: `GROK2API_ENV_FILE=.env.dev.example docker compose -f compose.dev.yml config --quiet`

Expected: exit status 0.

- [ ] **Step 4: Reload the development registration process**

Run: `docker compose -f compose.dev.yml ps`

Expected: identify the running `api-dev` service; source bind mounts and `dev_watch.py` reload the Python registration sidecar after the edited files change. If it is not running, start only the existing development services with `docker compose -f compose.dev.yml up -d`.

- [ ] **Step 5: Verify effective empty configuration inside the development service**

Run:

```bash
docker compose -f compose.dev.yml exec -T api-dev python -c 'from grok2api.upstream.proxy_pool import invalidate_outbound_proxy_cache, get_outbound_proxy_source; invalidate_outbound_proxy_cache(); s=get_outbound_proxy_source(); print({"enabled": s.get("enabled"), "source": s.get("source"), "pool": s.get("pool")})'
```

Expected with empty proxy settings/environment: `{'enabled': False, 'source': 'none', 'pool': []}`. If the development environment contains an explicit proxy variable, report that explicit source instead of clearing user configuration.

- [ ] **Step 6: Inspect the final diff and commit**

Run: `git diff --check && git status --short && git diff -- grok2api/upstream/proxy_pool.py grok2api/upstream/grok_build_adapter.py tests/test_proxy_selection.py`

Expected: no whitespace errors and only the scoped proxy-selection changes.

Commit:

```bash
git add grok2api/upstream/proxy_pool.py grok2api/upstream/grok_build_adapter.py tests/test_proxy_selection.py
git commit -m "fix(proxy): require explicit outbound configuration"
```
