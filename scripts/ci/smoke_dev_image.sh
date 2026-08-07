#!/usr/bin/env bash
set -euo pipefail

test -x /opt/grok2api-dev/bin/grok2api
test -x /opt/grok2api-dev/bin/grok2api-migrate
test -s /opt/grok2api-dev/source.digest
test -x /usr/local/go/bin/go
test -d /go/pkg/mod
test -d /go/cache
baked="$(tr -d '[:space:]' </opt/grok2api-dev/source.digest)"
actual="$(python /app/scripts/dev_source_fingerprint.py /app)"
test "$baked" = "$actual"
echo "development image smoke test passed: $baked"
