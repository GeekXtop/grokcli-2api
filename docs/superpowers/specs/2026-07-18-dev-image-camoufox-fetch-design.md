# Development Image Camoufox Fetch Design

## Goal

Ensure the root development image cannot build successfully unless the
Camoufox browser required by the local Turnstile solver is actually installed.

## Scope

This change covers the root `Dockerfile` used by `compose.dev.yml`, the
development Compose build secret declaration, and focused regression tests. It
does not change the standalone
`turnstile-solver/Dockerfile`, host startup scripts, solver health semantics, or
frontend error formatting.

## Root Cause

The current browser installation layer combines the required Camoufox fetch
and optional Patchright installation in one shell expression ending in
`|| true`. A Camoufox fetch failure can therefore be ignored, producing an
image whose HTTP solver process starts normally but whose browser pool fails on
the first captcha request.

## Design

Split the browser setup into two build steps:

1. Run `python -m camoufox fetch` as a required command.
2. Verify that `python -m camoufox active` no longer reports `not fetched`.
3. Keep `python -m patchright install chromium` optional with its own
   `|| true`, preserving the existing fallback behavior without weakening the
   Camoufox requirement.

The validation remains inside the image build so the failure is detected before
any development containers start.

When `GITHUB_TOKEN` is available in the host environment, Compose provides it
to the build through a BuildKit secret named `github_token`. The Dockerfile
mounts that secret only for the Camoufox fetch command, so the token is not
copied into the image filesystem or runtime environment. The mount is optional;
anonymous fetching remains possible when no token is configured.

## Testing

Add a focused static Dockerfile regression test that verifies:

- Camoufox fetch is not in the same failure-masked command as Patchright.
- The Dockerfile contains a post-fetch check rejecting `not fetched`.
- Patchright remains explicitly optional.
- Compose exposes `GITHUB_TOKEN` only as a build secret.

Run the test before implementation to confirm it fails for the current
Dockerfile, then run it again after the change. Finally, rebuild the development
image and execute `python -m camoufox version` inside a fresh container to
confirm the browser reports as installed.

## Success Criteria

- A missing or failed Camoufox browser download makes the image build fail.
- The resulting development image reports a Camoufox browser as installed.
- Existing development compose and watcher tests continue to pass.
