# ADR-006: Expose OCI Authentication Backend Selection

| Status | Date | Implementation |
| --- | --- | --- |
| Accepted | 2026-07-27 | Implemented by OCI push and pull in the Python API and CLI. |

## Context

`stacpkg` uses `oras-py` for OCI registry push and pull. `oras-py` defaults to
its `token` authentication backend: it starts with configured credentials and
requests a bearer token from the registry.

Some OCI Distribution registries use HTTP Basic authentication without a token
service. They need the `oras-py` `basic` backend to send configured credentials
directly. Selecting that backend currently requires replacing the private
`stacpkg.oci._oras_client` helper.

Authentication protocol and transport security are separate choices. Basic
authentication does not imply plain HTTP or disabled certificate verification.
Credentials must remain in runtime configuration rather than package files or
command-line arguments.

## Decision

Add a keyword-only `auth_backend` argument to OCI client creation,
`push_package`, and `pull_package`. Pass it directly to
`oras.client.OrasClient`. Keep `token` as the default for compatibility.

Expose the same choice as `--auth-backend` on `stacpkg push` and `stacpkg pull`,
also defaulting to `token`.

Load oras-py credential configuration after parsing the target container for
both push and pull. Credentials continue to come from oras-py mechanisms such
as `ORAS_USER`, `ORAS_PASS`, and Docker authentication configuration.

Keep authentication and transport options independent:

- `auth_backend="basic"` selects HTTP Basic authentication.
- `plain_http=True` selects HTTP instead of HTTPS.
- `insecure=True` disables TLS certificate verification.

## Alternatives Considered

- **Keep the backend private:** Avoids a new public argument, but downstream
  users must monkeypatch a private helper for Basic-only registries.
- **Infer Basic authentication from transport flags:** Reduces the number of
  options, but incorrectly couples credentials to HTTP or certificate policy.
- **Add username and password arguments:** Makes one credential source obvious,
  but exposes secrets in process arguments and duplicates oras-py credential
  loading.
- **Change the default to Basic:** Helps Basic-only registries, but changes
  behavior for existing token-based registries.

## Consequences

Basic-only registries work through the public Python API and CLI without a
private monkeypatch. Existing callers retain token authentication unless they
opt into another backend.

Backend names are passed through to oras-py, so oras-py remains responsible for
validating and implementing supported authentication backends. Basic
authentication should normally run over HTTPS; selecting it does not weaken
transport security.
