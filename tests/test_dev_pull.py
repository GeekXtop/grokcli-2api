from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DevPullScriptTests(unittest.TestCase):
    @staticmethod
    def _write_executable(path: Path, text: str) -> None:
        path.write_text(text)
        path.chmod(0o755)

    def test_script_pulls_and_recreates_without_build(self):
        text = (ROOT / "scripts/g2a-dev-pull.sh").read_text()
        self.assertIn("docker compose", text)
        self.assertIn("pull", text)
        self.assertIn("up -d --force-recreate --no-build", text)
        self.assertNotIn("source .env.dev", text)
        self.assertNotIn("docker compose down", text)

    def test_script_has_bounded_health_loop_and_diagnostics(self):
        text = (ROOT / "scripts/g2a-dev-pull.sh").read_text()
        self.assertIn("GROK2API_DEV_HEALTH_TIMEOUT_SECONDS:-90", text)
        self.assertIn("--connect-timeout", text)
        self.assertIn("--max-time", text)
        for endpoint in ("/health", "/ready"):
            self.assertIn(endpoint, text)
        self.assertIn("docker compose ps", text)
        self.assertIn("docker compose logs", text)

    def test_hanging_probe_respects_total_deadline_and_prints_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            docker_log = temp_root / "docker.log"
            curl_log = temp_root / "curl.log"

            self._write_executable(
                fake_bin / "docker",
                """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
""",
            )
            self._write_executable(
                fake_bin / "curl",
                """#!/usr/bin/env bash
connect_timeout=""
max_time=""
url=""
while (($#)); do
  case "$1" in
    --connect-timeout)
      connect_timeout="$2"
      shift 2
      ;;
    --max-time)
      max_time="$2"
      shift 2
      ;;
    -*)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
printf 'connect=%s max=%s url=%s\\n' "$connect_timeout" "$max_time" "$url" >> "$FAKE_CURL_LOG"

case "$url" in
  http://127.0.0.1:40081/health|http://127.0.0.1:40081/ready)
    exit 0
    ;;
esac

if [[ -z "$connect_timeout" || -z "$max_time" ]]; then
  sleep 10
  exit 28
fi
sleep "$max_time"
exit 28
""",
            )

            env = dict(os.environ)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "FAKE_DOCKER_LOG": str(docker_log),
                    "FAKE_CURL_LOG": str(curl_log),
                    "GROK2API_DEV_API_PORT": "40081",
                    "GROK2API_DEV_HEALTH_TIMEOUT_SECONDS": "1",
                    "GROK2API_DEV_PROBE_TIMEOUT_SECONDS": "3",
                    "GROK2API_DEV_SOLVER_PORT": "5072",
                }
            )

            started = time.monotonic()
            process = subprocess.Popen(
                [str(ROOT / "scripts/g2a-dev-pull.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
                self.fail(
                    "hanging health probe exceeded the controlled deadline; "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )

            elapsed = time.monotonic() - started
            self.assertNotEqual(0, process.returncode)
            self.assertLess(elapsed, 2.5)
            self.assertIn("health check timed out after 1s", stderr)

            curl_calls = curl_log.read_text().splitlines()
            self.assertEqual(3, len(curl_calls))
            for call in curl_calls:
                self.assertRegex(call, r"connect=[1-9][0-9]*")
                self.assertRegex(call, r"max=[1-9][0-9]*")
            self.assertIn("url=http://127.0.0.1:40081/health", curl_calls[0])
            self.assertIn("url=http://127.0.0.1:40081/ready", curl_calls[1])
            self.assertIn("url=http://127.0.0.1:5072/health", curl_calls[2])

            compose_calls = docker_log.read_text().splitlines()
            self.assertTrue(any(call.endswith(" ps") for call in compose_calls))
            self.assertTrue(any(" logs " in call for call in compose_calls))
            self.assertFalse(any(" down" in call or " rm" in call for call in compose_calls))


if __name__ == "__main__":
    unittest.main()
