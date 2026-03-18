# ODH Auth Stack: Token Flow Notes

## Components

- **oauth2-proxy** — sits in front of the user-facing web UI, handles browser login via OIDC
- **kube-rbac-proxy** — sits in front of backend services, authenticates API requests via Bearer JWT
- **entra-mock** — mock Entra ID identity provider for testing

## Which token goes where?

### oauth2-proxy

- Validates the **ID token** via OIDC (signature, iss, aud=client_id, expiry, nonce)
- Does NOT validate the access token at all — stores it as-is in the session
- When `--pass-access-token` is enabled, forwards the access token to the upstream
  via the `X-Forwarded-Access-Token` header
- The access token format/content is irrelevant to oauth2-proxy's auth decisions

### kube-rbac-proxy

- Expects a JWT in the `Authorization: Bearer <token>` header
- Uses the Kubernetes OIDC authenticator (`k8s.io/apiserver/plugin/pkg/authenticator/token/oidc`)
- Performs full OIDC discovery and JWT signature verification
- Validates:
  - `iss` must match `--oidc-issuer-url`
  - `aud` must match `--oidc-client-id` (the app's client_id)
  - JWT signature via JWKS fetched from the issuer
- Extracts claims for RBAC:
  - `email` claim (default, configurable via `--oidc-username-claim`) for user identity
  - `groups` claim (default, configurable via `--oidc-groups-claim`) for group membership
- After auth, optionally forwards `x-remote-user` and `x-remote-groups` headers upstream
- Ignores `X-Forwarded-Access-Token` entirely

### Conclusion: kube-rbac-proxy needs the ID token

The ID token is the only token with:
- `aud` = client_id (required by kube-rbac-proxy's audience check)
- `iss` = `{issuer}/v2.0` (required by kube-rbac-proxy's issuer check)
- `email`, `groups` claims (required for RBAC decisions)

The access token (after our v1.0 Graph-like changes) has:
- `aud` = `00000003-0000-0000-c000-000000000000` (Microsoft Graph)
- `iss` = `{issuer}` (no `/v2.0` suffix)
- No `groups` claim

Sending the access token to kube-rbac-proxy would fail both audience and issuer validation.

## Real Entra ID access token behavior

When only OIDC scopes (`openid profile email`) are requested:
- The access token is "owned" by Microsoft Graph
- `aud`: `00000003-0000-0000-c000-000000000000`
- `iss`: `https://sts.windows.net/{tenant_id}/` (v1.0 style, no `/v2.0`)
- `ver`: `1.0`
- May be opaque (not a parseable JWT) in some configurations

To get an access token addressed to your own API:
- Register an `application_id_uri` (e.g., `api://{client-id}`)
- Request a custom scope (e.g., `api://{client-id}/.default`)
- The access token will then have `aud` = client_id, `iss` with `/v2.0`, `ver` = `2.0`

The entra-mock now reproduces this behavior.
