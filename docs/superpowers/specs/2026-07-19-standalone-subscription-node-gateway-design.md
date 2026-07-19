# Standalone Subscription Node Gateway Design

## Goal

Build a separately deployed LAN subscription-node gateway that converts selected
airport nodes into stable, standard SOCKS5 endpoints. One gateway instance serves
multiple LAN clients, so consumers such as `grokcli-2api` do not need to understand
VMess, VLESS, Trojan, Shadowsocks, or Hysteria2 and do not need to run their own
protocol client.

The project is independent from `grokcli-2api`. The existing
`GeekXtop/multi-browser-manager` implementation is a behavioral reference for
subscription parsing, proxy-pool management, sing-box outbound generation, and
multi-inbound routing, but its Electron-specific runtime is not reused.

## Product Scope

The first release provides:

- native Linux and Windows deployment;
- a Go backend delivered as one application executable;
- an embedded React administration interface;
- a managed sing-box child process;
- HTTP/HTTPS subscription URLs whose bodies contain URI lines or Base64-encoded
  URI lines;
- manual import of HTTP and SOCKS5 proxies;
- manual and configurable scheduled subscription synchronization;
- user-selected node activation;
- one stable, editable LAN SOCKS5 port per activated node;
- node testing, status display, filtering, and batch operations;
- SQLite persistence;
- an optional Linux Docker image.

The first release deliberately does not provide:

- Internet-facing deployment hardening;
- administration login or API authentication;
- SOCKS5 username/password authentication;
- Clash YAML or sing-box JSON subscription parsing;
- automatic selection, load balancing, or failover between nodes;
- one sing-box process per node;
- a requirement for Docker, Node.js, or Python on end-user machines.

## Technology Choice

### Backend

Go owns the control plane:

- REST API and embedded static-file serving;
- subscription download, decoding, parsing, and reconciliation;
- SQLite access;
- stable node identity and port allocation;
- sing-box configuration generation and validation;
- child-process lifecycle, readiness, restart, and rollback;
- scheduled synchronization and serialized configuration application.

Go is selected for native Linux/Windows distribution, low-overhead long-running
service behavior, networking and process-management support, and simple embedding
of frontend assets. Go does not reimplement the airport transport protocols.

### Frontend

The administration interface uses React, TypeScript, and Vite. Development uses
Node.js, but production builds do not require a Node.js runtime. Vite emits static
assets into `web/dist`, and the Go application embeds those assets with `go:embed`
and serves them from the same HTTP origin as the API.

Relevant presentation and interaction ideas may be adapted from
`multi-browser-manager`, while Electron IPC is replaced by REST calls.

### Data Plane

sing-box remains the protocol data plane. A single sing-box process contains one
SOCKS inbound and one protocol outbound for every activated node. Routing binds
each inbound tag to exactly one outbound tag, with the unmatched-route default set
to `block` so configuration mistakes never fall back to the machine's direct
connection.

## Runtime Architecture

```text
LAN browser / API client
        |
        +-- Web administration UI
        +-- REST API
        |
        v
Go gateway process
  - subscriptions and parsers
  - SQLite state
  - node/port reconciliation
  - sing-box config builder
  - sing-box process supervisor
        |
        v
One sing-box child process
  0.0.0.0:22001 -> node A outbound
  0.0.0.0:22002 -> node B outbound
  0.0.0.0:22003 -> node C outbound
```

Both the management HTTP listener and activated SOCKS5 listeners are available to
the LAN. Listen addresses, management port, and proxy-port range are configurable.
The UI shows a visible warning that the application is running in LAN trust mode
without authentication.

## Persistence Model

SQLite is the durable source of truth. The schema is organized around the
following entities.

### Settings

- management listen address and port, defaulting to `0.0.0.0:18080`;
- SOCKS listen address, defaulting to `0.0.0.0`;
- automatic allocation range, defaulting to `22000-22999`;
- advertised host used when presenting copyable proxy URLs;
- sing-box executable path;
- default subscription synchronization interval, defaulting to 30 minutes.

### Subscription Sources

- stable source ID;
- name and URL;
- enabled flag;
- synchronization interval;
- created and last-synchronized timestamps;
- last result counts for imported, updated, removed, skipped, and invalid nodes;
- last error summary.

### Nodes

- stable node ID;
- optional subscription source ID, absent for manual imports;
- display name and protocol;
- normalized protocol configuration;
- source identity metadata used during reconciliation;
- activated flag;
- optional durable local port;
- current runtime status;
- last test time, latency, exit IP, and error summary.

Secrets such as subscription tokens, UUIDs, and proxy passwords remain available
to the process because sing-box needs them, but they are not emitted into ordinary
logs or public list responses unnecessarily.

## Supported Inputs

Subscription synchronization accepts an HTTP or HTTPS URL. The response is
interpreted as either:

1. newline-separated proxy URIs; or
2. a Base64/Base64URL-encoded newline-separated proxy URI list.

Initial node protocols are:

- `vmess://`;
- `vless://`;
- `trojan://`;
- `ss://`;
- `hy2://` and `hysteria2://`;
- `http://` and `https://` upstream proxies;
- `socks5://` and `socks5h://` upstream proxies.

If a URL returns Clash YAML, sing-box JSON, HTML, or another unsupported format,
the synchronization result explicitly reports the unsupported format. It must not
silently report success with zero nodes.

Manual import accepts multi-line HTTP/HTTPS and SOCKS5/SOCKS5H proxy URLs and the
credential shorthand already supported by the reference project's proxy import
workflow where practical.

## Subscription Reconciliation

Synchronization is serialized so manual triggers and scheduled jobs cannot
overwrite each other.

The reconciliation flow is:

1. Download with timeout, body-size, and redirect limits.
2. Decode and parse all supported lines.
3. Validate protocol-specific required fields.
4. Match incoming nodes against existing nodes from the same subscription.
5. Preserve stable node IDs, activation state, and local ports for matches.
6. Insert unmatched incoming nodes as disabled nodes without allocated ports.
7. Remove nodes absent from a successful, non-empty synchronization and release
   their ports.
8. Apply the new runtime configuration once for the complete synchronization.

Matching first prefers an exact normalized configuration fingerprint. If the
provider changes credentials or server details, a unique normalized node name
within the same source may retain the existing node ID. Duplicate names are
disambiguated with protocol, host, and port. Ambiguous matches become new nodes
rather than risking transfer of an existing port to the wrong node.

An empty response, download failure, unsupported document format, or a response
from which no valid node can be parsed preserves the existing nodes.

## Port Lifecycle

- New nodes are disabled and have no port.
- First activation allocates the first usable port from the configured range.
- The allocation is persisted before being presented as available.
- Deactivation stops listening but retains the assigned port.
- Reactivation reuses the same port.
- Users may edit a node's assigned port.
- A requested port must be in range, unique in the database, and available to the
  operating system.
- Deleting a node releases its port.
- Restarting the gateway never changes persisted port assignments.
- A port conflict discovered later does not silently change a stable endpoint.
  The node is marked with a port-conflict error and excluded from the active
  sing-box candidate configuration so unaffected nodes can continue to run.

The copy action presents an address such as
`socks5://192.168.1.10:22001`. By default the host is derived from the browser's
current gateway hostname; an advertised-host setting handles machines with
multiple network interfaces or DNS names.

## sing-box Configuration

Only activated, valid, conflict-free nodes are included in the generated
configuration.

For every included node, the gateway creates:

- a SOCKS inbound listening on the configured LAN address and assigned port;
- an outbound derived from the node protocol configuration;
- a route from the inbound tag to the outbound tag.

The generated configuration also contains a block outbound and uses it as the
final route. No runtime error may cause transparent direct connection.

The outbound builder supports protocol-specific requirements including UUIDs,
passwords, Shadowsocks methods, TLS/SNI, Reality fields, uTLS fingerprint,
WebSocket, HTTP/2, gRPC, HTTP Upgrade, QUIC transports, and Hysteria2 obfuscation
fields when present in the input URI.

## Configuration Application and Recovery

All state-changing operations use a serialized application queue. A batch action
or one subscription synchronization results in at most one sing-box reload.

The application sequence is:

1. Construct the candidate database state and sing-box configuration.
2. Write the candidate configuration to a permission-restricted temporary file.
3. Run `sing-box check -c <candidate>`.
4. If validation fails, keep the current database state and running process.
5. Stop the current process only after successful validation.
6. Start the candidate process and verify all expected listener ports.
7. Commit the related database transaction after readiness succeeds.
8. If startup fails, restart the previous configuration and report the failed
   operation to the UI.
9. If the database commit fails after candidate readiness, stop the candidate,
   restart the previous configuration, and return an error so runtime and durable
   state cannot diverge.

On an unexpected sing-box exit, the supervisor restarts it with increasing
backoff. Repeated failures stop automatic retries and leave a persistent degraded
status visible in the UI. Normal gateway shutdown terminates sing-box and removes
temporary configuration files.

## Administration Interface

### Overview

Shows:

- gateway listen address;
- sing-box installed/running/degraded status;
- subscription count;
- total node count;
- activated and listening node counts;
- configured proxy-port range;
- LAN trust mode warning.

### Subscriptions

Supports creating, editing, deleting, enabling, disabling, and immediately
synchronizing sources. Each source displays its schedule, last synchronization
time, result counts, and last error.

### Nodes

The primary table contains:

- node name, protocol, and source;
- upstream server address;
- availability, latency, and optional exit IP;
- activated state;
- assigned local SOCKS5 address;
- actions for testing, copying, editing the port, and deleting.

It supports source, protocol, and status filters, text search, and batch activate,
deactivate, and test operations. New synchronized nodes remain disabled until the
user selects them.

Single-node changes show an applying state while the configuration is reloaded.
Batch changes submit one API operation and cause one reload. The first release
uses short-interval status polling rather than WebSocket or SSE.

### Manual Import

Provides a multi-line HTTP/SOCKS5 input, parsed preview, invalid-line reporting,
and a confirmation step before insertion.

### Settings

Provides management and SOCKS listen addresses, management port, proxy-port
range, advertised host, sing-box executable path, and default synchronization
interval.

## API Boundaries

The same-origin REST API is grouped by responsibility:

- `/api/status` for application and sing-box status;
- `/api/settings` for runtime settings;
- `/api/subscriptions` for source CRUD and synchronization;
- `/api/nodes` for listing, filtering, testing, activation, port changes, and
  deletion;
- `/api/import` for manual proxy preview and commit.

Bulk activation, deactivation, and testing are explicit batch endpoints so the
backend can serialize changes and avoid one restart per selected row. The first
release does not enable cross-origin requests.

## Security Boundary

The first release intentionally trusts the LAN and provides neither UI login nor
SOCKS authentication. The interface must clearly communicate this behavior.

Even in LAN trust mode:

- subscription requests accept only HTTP/HTTPS;
- request timeout, redirect count, and response size are bounded;
- loopback, link-local, and private destination blocking is applied to remote
  subscription URLs to reduce SSRF exposure;
- API responses and logs avoid unnecessary secret disclosure;
- generated configs and SQLite files use restrictive permissions where the host
  OS supports them;
- CORS is disabled and browser operations use the same origin;
- unmatched sing-box traffic is blocked rather than sent directly.

Authentication is a future extension and must be possible without changing node
or port identities.

## Deployment

Native artifacts are the primary distribution:

- Linux executable, optionally installed as a systemd service;
- Windows executable, optionally installed as a Windows service;
- frontend assets embedded in the executable;
- sing-box located beside the application, in a configured data directory, or at
  an explicitly configured path.

Runtime data lives in a predictable application data directory containing SQLite,
generated configs, and logs. Development may use a project-local data directory.

An optional Linux Docker image packages the gateway and sing-box. Docker is not
required. When used, the management port and selected SOCKS port range must be
reachable from the LAN; native deployment avoids this port-publication complexity.

## Testing Strategy

### Go Unit Tests

- URI and Base64 decoding fixtures for every supported protocol;
- invalid and unsupported subscription handling;
- node reconciliation and duplicate-name behavior;
- stable ID, activation, and port preservation;
- port allocation, manual override, collision, and release;
- sing-box outbound and route generation;
- secret redaction and request-limit behavior.

### Go Integration Tests

- SQLite transactions and migrations;
- subscription synchronization through an HTTP fixture server;
- candidate validation and rollback through a fake sing-box executable;
- listener readiness, child exit, restart backoff, and shutdown;
- Linux and Windows path/process abstractions where CI supports them.

### Frontend Tests

- subscription CRUD and synchronization results;
- node filters and search;
- single and batch activation;
- port editing and conflict errors;
- manual import preview and commit;
- degraded sing-box and LAN trust warnings.

### End-to-End Tests

- start the gateway with a fake or real sing-box test fixture;
- import nodes, activate selected nodes, and verify stable addresses;
- restart the gateway and verify that ports are unchanged;
- update a subscription and verify preserved mappings;
- force a failed configuration and verify rollback;
- validate the core flow on Linux and Windows CI runners.

## Acceptance Criteria

- A user can run the application natively on Linux or Windows and open the React
  administration interface from another LAN device.
- A user can add a URI/Base64 subscription and manually or automatically sync it.
- A user can manually import HTTP/SOCKS5 proxies.
- Newly imported nodes remain disabled.
- Activating selected nodes creates one reachable LAN SOCKS5 endpoint per node.
- Each node's endpoint remains stable across deactivation, reactivation,
  subscription updates, and application restarts.
- Users can change a node's port with clear validation feedback.
- A malformed subscription or failed sing-box configuration does not delete the
  previous working state.
- No configuration error falls back to direct outbound traffic.
- The production application requires neither Node.js nor Python.
