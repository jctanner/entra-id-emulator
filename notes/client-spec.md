# Client Spec: oauth2-proxy Microsoft Entra ID Provider

Analysis of `reference.src/oauth2-proxy/providers/ms_entra_id.go` compared against
the Microsoft Entra ID OIDC and ROPC protocol documentation.

## OAuth Flow Used

The provider implements the **OIDC Authorization Code flow** -- not ROPC. It extends
a base `OIDCProvider` with Entra ID-specific logic for federated token authentication,
multi-tenant validation, and group overage handling via Microsoft Graph.

Grant types used:
- `authorization_code` -- initial sign-in (code-for-token exchange)
- `refresh_token` -- session refresh

Grant types NOT used:
- `password` (ROPC) -- not implemented anywhere in this provider

## Endpoints the Client Expects

Based on the code and the OIDC configuration document spec:

| Endpoint | URL Pattern | Used For |
| --- | --- | --- |
| Authorization | `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize` | Redirect user for sign-in (handled by base OIDCProvider/oauth2 lib) |
| Token | `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` | Code redemption and token refresh (`p.RedeemURL`) |
| JWKS | `https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys` | ID token signature validation (handled by `go-oidc` library) |
| OpenID Config | `https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration` | Discovery (handled by `go-oidc` library) |
| Microsoft Graph | `https://graph.microsoft.com/v1.0/me/transitiveMemberOf` | Group overage resolution |

## Token Endpoint Requests

### Authorization Code Exchange (standard path)

Delegated to `OIDCProvider.Redeem`, which uses the `golang.org/x/oauth2` library.
Sends a standard `grant_type=authorization_code` POST with `client_id`, `client_secret`,
`code`, and `redirect_uri`.

### Authorization Code Exchange (federated token path)

`redeemWithFederatedToken` builds the request manually. Parameters sent:

| Parameter | Value |
| --- | --- |
| `grant_type` | `authorization_code` |
| `client_id` | Application (client) ID |
| `code` | Authorization code from the authorize redirect |
| `redirect_uri` | The app's redirect URI |
| `client_assertion` | Contents of `AZURE_FEDERATED_TOKEN_FILE` |
| `client_assertion_type` | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` |
| `code_verifier` | PKCE code verifier (if present) |

This matches the `private_key_jwt` token endpoint auth method listed in the OIDC
configuration document's `token_endpoint_auth_methods_supported`.

### Refresh Token (federated token path)

`redeemRefreshTokenWithFederatedToken` sends:

| Parameter | Value |
| --- | --- |
| `grant_type` | `refresh_token` |
| `client_id` | Application (client) ID |
| `refresh_token` | The stored refresh token |
| `client_assertion` | Contents of `AZURE_FEDERATED_TOKEN_FILE` |
| `client_assertion_type` | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` |
| `expiry` | `time.Now().Add(-time.Hour)` in RFC3339 format |

### Refresh Token (standard path)

Delegated to `OIDCProvider.redeemRefreshToken`, which uses the `golang.org/x/oauth2`
library with the standard `client_secret` credential.

## Expected Token Response

The `fetchToken` method (line 302) expects a JSON response that deserializes into
`oauth2.Token`. Based on the Entra ID docs, this means:

```json
{
    "token_type": "Bearer",
    "scope": "...",
    "expires_in": 3599,
    "access_token": "eyJ...",
    "refresh_token": "AwAB...",
    "id_token": "eyJ..."
}
```

The code unmarshals both into a typed `oauth2.Token` and a raw `interface{}`, then
merges them with `token.WithExtra(rawResponse)` so that extra fields like `id_token`
are accessible via `token.Extra("id_token")`.

## ID Token Claims Used

The provider extracts the following claims from the ID token:

| Claim | Purpose | Code Location |
| --- | --- | --- |
| `iss` | Tenant extraction and validation | `getTenantFromToken` (line 269) |
| `_claim_names` | Detect group overage | `checkGroupOverage` (line 207) |

The issuer claim is validated against the pattern:
```
^https://login\.microsoftonline\.com/([a-zA-Z0-9-]+)/v2\.0$
```

Additional claims (email, preferred_username, groups, etc.) are extracted by the
base `OIDCProvider` during `EnrichSession` and `createSession`.

## Multi-Tenant Support

When `multiTenantAllowedTenants` is configured, `ValidateSession` extracts the tenant
ID from the `iss` claim and checks it against the allowlist. This is relevant when the
app registration uses the `common` or `organizations` authority URL, allowing sign-in
from multiple tenants but restricting to a specific set.

## Group Overage Handling

When the ID token contains a `_claim_names` field with a `groups` key, this indicates
group overage (too many groups to include in the token). The provider then:

1. Calls `https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=id&$top=100`
2. Uses the access token as a Bearer token
3. Adds `ConsistencyLevel: eventual` header (required by Graph API for advanced queries)
4. Follows `@odata.nextLink` pagination
5. Collects all group IDs and appends them to the session (deduplicated)

## Quirks and Non-Standard Behavior

### `expiry` parameter in refresh token request

In `redeemRefreshTokenWithFederatedToken` (line 176):
```go
params.Add("expiry", time.Now().Add(-time.Hour).Format(time.RFC3339))
```

This parameter is **not** part of the OAuth 2.0 or OIDC spec. The Entra ID token
endpoint does not document an `expiry` parameter. It is likely ignored by Entra ID
and may be an artifact of oauth2-proxy internal logic or a workaround. A mock server
can safely ignore this parameter.

### `AZURE_FEDERATED_TOKEN_FILE` environment variable

The federated token is read from a file path specified by this env var. This is the
standard Kubernetes workload identity mechanism for Azure. The file contains a
short-lived JWT issued by the Kubernetes OIDC provider, which Entra ID accepts as
a client assertion in place of a client secret.

## Implications for a Mock Entra ID Server

To support this client, a mock server needs to implement:

1. **`/.well-known/openid-configuration`** -- Return a JSON document with valid
   `authorization_endpoint`, `token_endpoint`, `jwks_uri`, `issuer`, and
   `token_endpoint_auth_methods_supported` (including `client_secret_post` and
   `private_key_jwt`).

2. **`/oauth2/v2.0/authorize`** -- Accept GET requests with `client_id`, `response_type`,
   `redirect_uri`, `scope` (must include `openid`), `state`, `nonce`, and optional
   `prompt`, `login_hint`, `domain_hint`. Redirect back with an authorization `code`.

3. **`/oauth2/v2.0/token`** -- Accept POST requests with:
   - `grant_type=authorization_code` + `code` + `redirect_uri` + client credentials
   - `grant_type=refresh_token` + `refresh_token` + client credentials
   - Client credentials can be either `client_secret` or `client_assertion` +
     `client_assertion_type`
   - Return JSON with `access_token`, `token_type`, `expires_in`, `scope`,
     `id_token` (as a valid JWT), and optionally `refresh_token`.

4. **`/discovery/v2.0/keys`** (JWKS) -- Return the public keys used to sign ID tokens,
   in JWK Set format. The `go-oidc` library fetches this to verify ID token signatures.

5. **Microsoft Graph mock** (optional) -- If testing group overage scenarios, mock
   `https://graph.microsoft.com/v1.0/me/transitiveMemberOf` with paginated responses.

The ID tokens issued must be valid JWTs with at minimum:
- `iss` matching `https://login.microsoftonline.com/{tenant}/v2.0`
- `aud` matching the `client_id`
- `sub` (subject identifier)
- `nonce` (echoed from the authorize request)
- `exp`, `iat`, `nbf` (standard timing claims)
- Signed with a key listed in the JWKS endpoint
