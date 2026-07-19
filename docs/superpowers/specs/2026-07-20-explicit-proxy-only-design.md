# Explicit Proxy Only Design

## Goal

Ensure registration and account-pool outbound traffic use a proxy only when the
operator explicitly configures one. Empty proxy configuration must mean direct
network access.

## Current Problem

Both registration and account-pool proxy selection eventually call automatic
discovery when explicit configuration is empty. Automatic discovery scans
hard-coded peer and host ports and treats a successful TCP connection as proof
that the endpoint is a proxy.

In the observed failure, `host.docker.internal:40080` resolved to a fake-IP
address that accepted TCP connections but closed HTTP CONNECT requests. The
endpoint was selected as `source=auto`, causing registration to fail before the
xAI flow began.

## Required Semantics

Proxy selection uses only explicit sources:

1. A proxy supplied by the registration request.
2. The configured global outbound proxy pool.
3. Project-specific proxy environment variables:
   `GROK2API_XAI_PROXY_POOL`, `GROK2API_PROXY_POOL`,
   `GROK2API_XAI_PROXY`, `GROK2API_PROXY`, or `GROK_CLI_PROXY`.
4. Explicit generic proxy environment variables:
   `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, including lowercase variants, or
   `GROK2API_AUTO_PROXY`.

If none of these sources contains a proxy URL, selection returns no proxy and
the request connects directly.

## Removed Behavior

The application will no longer probe these implicit candidates:

- `privoxy:8118`
- `warp-proxy:1080`
- `host.docker.internal:40080`
- `host.docker.internal:7890`
- `host.docker.internal:8118`

No new configuration field or feature flag is introduced.

## Affected Paths

### Registration

Registration continues to prefer a request-specific proxy and can reuse the
explicit global outbound pool. If both are empty, `_proxy_pool()` returns an
empty list and registration uses direct connectivity.

### Account-pool outbound traffic

`get_outbound_proxy_source()` continues to resolve admin settings, project
environment variables, and registration configuration fallbacks. If the
resulting pool is empty, it reports `source=none`, `enabled=false`, and an empty
pool instead of creating `source=auto`.

Account requests, model probes, token refresh, and OIDC operations therefore
remain direct unless a proxy is explicitly configured.

## Testing

Add focused proxy selection tests that verify:

- empty settings and empty environment produce no proxy and never
  `source=auto`;
- hard-coded peer/host candidates are absent;
- explicit project proxy environment variables are honored;
- explicit `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and
  `GROK2API_AUTO_PROXY` values are honored;
- registration with no request proxy and no global proxy returns an empty pool;
- explicit registration and global proxies preserve their existing precedence.

Run the focused proxy tests plus registration, Compose, and Go development
regressions. Finally, reload the Python registration sidecar and verify a new
empty-proxy registration session no longer records an automatically discovered
proxy.

## Success Criteria

- Empty proxy configuration always means direct connection.
- No effective configuration reports `source=auto`.
- No hard-coded local proxy hostname or port is probed.
- Explicit proxy configuration continues to work.
- Registration and account-pool outbound selection follow the same semantics.
