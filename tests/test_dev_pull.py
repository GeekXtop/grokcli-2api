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

    def test_script_has_transactional_rollback_and_sha_image_mode(self):
        text = (ROOT / "scripts/g2a-dev-pull.sh").read_text()
        for value in (
            "docker rename",
            "docker stop",
            "docker start",
            "docker rm -f",
            "compose.dev.pinned.yml",
            "compose.dev.backup.yml",
            "volumes_from",
            "sha-dev-",
            "rollback",
        ):
            self.assertIn(value, text)
        for forbidden in ("docker compose down", "docker volume prune", "docker volume rm"):
            self.assertNotIn(forbidden, text)

    def _run_fake_transaction(
        self,
        *,
        up_fails: bool,
        health_success: bool = False,
        image: str = "ghcr.io/geekxtop/grokcli-2api:sha-dev-0123456789ab",
    ) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            docker_log = temp_root / "docker.log"
            state_file = temp_root / "state"
            state_file.write_text("old")

            self._write_executable(
                fake_bin / "docker",
                """#!/usr/bin/env bash
set -euo pipefail
log="$FAKE_DOCKER_LOG"
state_file="$FAKE_DOCKER_STATE"
printf '%s\n' "$*" >> "$log"

service_from_args() {
  case "$*" in
    *api-dev) printf 'api-dev' ;;
    *solver-dev) printf 'solver-dev' ;;
    *assets-dev) printf 'assets-dev' ;;
  esac
}

if [[ "$1" == compose ]]; then
  joined="$*"
  service="$(service_from_args "$joined")"
  if [[ "$joined" == *" pull" ]]; then exit 0; fi
  if [[ "$joined" == *" create "* ]]; then exit 0; fi
  if [[ "$joined" == *"-p g2a-dev-backup-"* && "$joined" == *" ps -aq "* ]]; then
    case "$service" in
      api-dev) printf 'backup-api\n' ;;
      solver-dev) printf 'backup-solver\n' ;;
      assets-dev) printf 'backup-assets\n' ;;
    esac
    exit 0
  fi
  if [[ "$joined" == *" ps -aq "* ]]; then
    case "$service" in
      api-dev)
        if [[ $(<"$state_file") == old ]]; then printf 'old-api\n'; else printf 'candidate-api\n'; fi
        ;;
      solver-dev)
        if [[ $(<"$state_file") == old ]]; then printf 'old-solver\n'; else printf 'candidate-solver\n'; fi
        ;;
      assets-dev)
        if [[ $(<"$state_file") == old ]]; then printf 'old-assets\n'; else printf 'candidate-assets\n'; fi
        ;;
    esac
    exit 0
  fi
  if [[ "$joined" == *" up -d --force-recreate --no-build"* ]]; then
    printf 'candidate\n' >"$state_file"
    if [[ "$FAKE_UP_FAIL" == 1 ]]; then exit 1; fi
    exit 0
  fi
  exit 0
fi

case "$1" in
  ps)
    exit 0
    ;;
  inspect)
    if [[ "$2" == --format ]]; then
      id="$4"
      case "$3" in
        "{{.Name}}")
          case "$id" in
            old-api|grokcli-2api-api-dev|g2a-dev-old-api-*) printf '/grokcli-2api-api-dev\n' ;;
            old-solver|grokcli-2api-solver-dev|g2a-dev-old-solver-*) printf '/grokcli-2api-solver-dev\n' ;;
            old-assets|grokcli-2api-assets-dev|g2a-dev-old-assets-*) printf '/grokcli-2api-assets-dev\n' ;;
            backup-api|backup-solver|backup-assets) printf '/backup-%s\n' "$id" ;;
          esac
          ;;
        "{{.Image}}") printf 'sha256:old-image\n' ;;
      esac
    fi
    exit 0
    ;;
  rename)
    if [[ "$2" == g2a-dev-backup-* ]]; then printf 'restored\n' >"$state_file"; fi
    ;;
  start|stop|rm)
    ;;
esac""",
            )
            self._write_executable(
                fake_bin / "curl",
                """#!/usr/bin/env bash
if [[ "$FAKE_CURL_SUCCESS" == 1 ]]; then exit 0; fi
exit 22
""",
            )

            env = dict(os.environ)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "FAKE_DOCKER_LOG": str(docker_log),
                    "FAKE_DOCKER_STATE": str(state_file),
                    "FAKE_UP_FAIL": "1" if up_fails else "0",
                    "FAKE_CURL_SUCCESS": "1" if health_success else "0",
                    "GROK2API_DEV_HEALTH_TIMEOUT_SECONDS": "1",
                    "GROK2API_DEV_PROBE_TIMEOUT_SECONDS": "1",
                    "GROK2API_DEV_IMAGE": image,
                }
            )
            process = subprocess.run(
                [str(ROOT / "scripts/g2a-dev-pull.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=4,
            )
            calls = docker_log.read_text().splitlines()
            return process, calls, state_file.read_text()

    def test_failed_health_restores_previous_containers(self):
        process, calls, state = self._run_fake_transaction(up_fails=False)
        self.assertNotEqual(0, process.returncode)
        self.assertEqual("restored\n", state)
        self.assertTrue(any("rename old-api g2a-dev-old-api-" in call for call in calls))
        self.assertTrue(any("stop g2a-dev-old-api-" in call for call in calls))
        self.assertTrue(any("rm -f candidate-api" in call for call in calls))
        self.assertTrue(any("start grokcli-2api-api-dev" in call for call in calls))
        self.assertTrue(any("compose.dev.pinned.yml up -d" in call for call in calls))

    def test_failed_compose_start_restores_previous_containers(self):
        process, calls, state = self._run_fake_transaction(up_fails=True)
        self.assertNotEqual(0, process.returncode)
        self.assertEqual("restored\n", state)
        self.assertTrue(any("rm -f candidate-api" in call for call in calls))
        self.assertTrue(any("start grokcli-2api-api-dev" in call for call in calls))
        self.assertTrue(any("compose.dev.pinned.yml up -d" in call for call in calls))

    def test_successful_default_dev_pull_keeps_source_mount_path(self):
        process, calls, state = self._run_fake_transaction(
            up_fails=False,
            health_success=True,
            image="ghcr.io/geekxtop/grokcli-2api:dev",
        )
        self.assertEqual(0, process.returncode)
        self.assertEqual("candidate\n", state)
        self.assertTrue(any("up -d --force-recreate --no-build" in call for call in calls))
        self.assertTrue(any("rm -f g2a-dev-backup-api-dev-" in call for call in calls))
        self.assertFalse(any("compose.dev.pinned.yml" in call for call in calls))

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
