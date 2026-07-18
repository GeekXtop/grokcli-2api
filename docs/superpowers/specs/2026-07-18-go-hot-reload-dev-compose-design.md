# Go Hot Reload Development Compose Design

## Goal

Restore automatic API hot reload in `compose.dev.yml` after the v2.0.1 switch
from a Python main process to a Go main process.

## Scope

The change covers the root multi-stage `Dockerfile`, `compose.dev.yml`,
`scripts/dev_watch.py`, and focused tests. It preserves the existing solver and
asset watchers, GitHub build secret, host networking, source bind mount, and
registration/SSO sidecar behavior.

## Architecture

The existing Python-based runtime stage becomes a reusable `runtime-base`.
A `development` target derives from it and copies the Go toolchain from the
existing `go-builder` stage. A final `production` target derives from
`runtime-base`, remains the Dockerfile default, and therefore does not contain
the Go compiler.

`api-dev` builds the `development` target and starts
`python /app/scripts/dev_watch.py api`. The watcher compiles
`./cmd/grok2api` to `/tmp/grok2api-dev`, outside the `/app` bind mount, then
starts it through `/app/entrypoint.sh`. This keeps the Python registration and
SSO sidecars managed by the existing entrypoint while avoiding the bind mount
that hides image-baked files under `/app/bin`.

## Watch and Restart Behavior

The API watcher observes:

- `cmd/**/*.go`
- `internal/**/*.go`
- `go.mod`
- `go.sum`
- `.release-commit`

On startup and after a debounced change, it runs:

```bash
go build -o /tmp/grok2api-dev ./cmd/grok2api
```

If compilation succeeds, the watcher stops the prior process group and starts
`/app/entrypoint.sh /tmp/grok2api-dev`. If compilation fails, it reports the
failure, leaves the watcher alive, and waits for another source change instead
of entering a rapid restart loop.

SIGTERM and SIGINT stop the current API process group before the watcher exits.

## Compose Changes

`api-dev` will:

- build Dockerfile target `development`;
- set `GROK2API_RUNTIME=go`;
- keep one worker and disable the inline solver;
- run `python /app/scripts/dev_watch.py api`;
- keep its existing health check and GitHub build secret.

The common `.:/app` source mount remains in place for all development services.

## Testing

Tests will verify:

- the Dockerfile has separate `development` and default `production` targets;
- only the development target copies the Go toolchain;
- Compose selects `development`, Go runtime, and API watcher mode;
- the watcher produces the expected Go build and entrypoint commands;
- failed builds do not start a replacement child;
- existing solver, asset, Compose, and Camoufox tests still pass.

After unit tests, rebuild the development image using the GitHub BuildKit
secret, recreate the development containers, verify API and solver health, and
modify a harmless Go source timestamp to confirm that the API watcher rebuilds
and restarts the process.

## Success Criteria

- `docker compose -f compose.dev.yml up` no longer references deleted
  `/app/app.py`.
- The API container runs the v2.0.1 Go main process.
- Go source changes trigger automatic compile and restart.
- Compile failures do not terminate the watcher or replace the last successful
  binary.
- The production image default does not contain the Go toolchain.
- Solver and asset hot reload continue to work unchanged.
