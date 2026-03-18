# Entra ID Mock Server Specification

A standalone OIDC provider that mimics the Microsoft Entra ID v2.0 endpoints closely enough
to serve as a drop-in replacement for development and testing. The primary client is
`oauth2-proxy` with the Microsoft Entra ID provider, but the server should be usable by
any OIDC-conformant client that targets Entra ID.

## 1. Endpoints

All authentication endpoints are served under `/{tenant}/oauth2/v2.0/` to match
the Entra ID URL structure. The `{tenant}` path segment accepts a tenant GUID,
a domain name (e.g. `contoso.onmicrosoft.com`), or the aliases `common`,
`organizations`, `consumers`.

| Endpoint | Method | Path |
| --- | --- | --- |
| OpenID Configuration | GET | `/{tenant}/v2.0/.well-known/openid-configuration` |
| Authorization | GET | `/{tenant}/oauth2/v2.0/authorize` |
| Token | POST | `/{tenant}/oauth2/v2.0/token` |
| JWKS | GET | `/{tenant}/discovery/v2.0/keys` |
| Logout | GET, POST | `/{tenant}/oauth2/v2.0/logout` |
| UserInfo | GET, POST | `/oidc/userinfo` |

---

## 2. OpenID Configuration (`/.well-known/openid-configuration`)

Returns a JSON document modeled on the live Entra ID response. The `{tenant}` in the
request path determines the issuer and endpoint URLs in the response.

```json
{
  "issuer": "{scheme}://{host}/{tenant}/v2.0",
  "authorization_endpoint": "{scheme}://{host}/{tenant}/oauth2/v2.0/authorize",
  "token_endpoint": "{scheme}://{host}/{tenant}/oauth2/v2.0/token",
  "userinfo_endpoint": "{scheme}://{host}/oidc/userinfo",
  "jwks_uri": "{scheme}://{host}/{tenant}/discovery/v2.0/keys",
  "end_session_endpoint": "{scheme}://{host}/{tenant}/oauth2/v2.0/logout",
  "token_endpoint_auth_methods_supported": [
    "client_secret_post",
    "client_secret_basic",
    "private_key_jwt"
  ],
  "response_types_supported": [
    "code",
    "id_token",
    "code id_token",
    "id_token token"
  ],
  "response_modes_supported": ["query", "fragment", "form_post"],
  "scopes_supported": ["openid", "profile", "email", "offline_access"],
  "subject_types_supported": ["pairwise"],
  "id_token_signing_alg_values_supported": ["RS256"],
  "claims_supported": [
    "sub", "iss", "aud", "exp", "iat", "nbf", "nonce",
    "auth_time", "name", "preferred_username", "email",
    "tid", "oid", "ver", "at_hash", "c_hash"
  ],
  "request_uri_parameter_supported": false,
  "frontchannel_logout_supported": true,
  "http_logout_supported": true
}
```

`{scheme}` is `http` or `https` from the server config. `{host}` is the server's
external hostname and port. All URLs in the discovery document, all `iss` claims in
issued tokens, and all redirect/callback URLs must use the same scheme consistently.
When `scheme=http`, the server listens in plaintext -- no TLS termination, no
certificate needed. This is the default for local development and compose-based testing.

---

## 3. Authorization Endpoint

### Request (GET)

| Parameter | Required | Description |
| --- | --- | --- |
| `client_id` | yes | Must match a registered client. |
| `response_type` | yes | `code`, `id_token`, `code id_token`, or `id_token token`. |
| `redirect_uri` | yes | Must match one of the client's registered redirect URIs. |
| `scope` | yes | Space-separated. Must include `openid` for OIDC. Also `profile`, `email`, `offline_access`. |
| `state` | recommended | Echoed back unchanged. |
| `nonce` | required for `id_token` | Included in the ID token `nonce` claim. |
| `response_mode` | recommended | `query` (default for `code`), `fragment`, or `form_post`. |
| `prompt` | optional | `login`, `none`, `consent`, `select_account`. |
| `login_hint` | optional | Pre-fills username on the login page. |
| `domain_hint` | optional | Accepted but ignored (single-provider). |
| `code_challenge` | recommended | PKCE challenge (base64url-encoded SHA256 hash). |
| `code_challenge_method` | recommended | `S256` or `plain`. |

### Behavior

1. Validate `client_id` exists and `redirect_uri` matches a registered URI.
2. If `prompt=none`, attempt to find an existing session. If no session,
   return `error=login_required` to the redirect URI.
3. Otherwise, render a login page. The login page shows a username/password
   form. If `login_hint` is provided, pre-fill the username field.
   If `prompt=select_account`, show an account picker (list known sessions).
   If `prompt=consent`, show a consent screen after login.
4. On successful authentication, generate an authorization code and redirect:
   - `response_mode=query`: append `code` and `state` as query parameters.
   - `response_mode=fragment`: append as fragment.
   - `response_mode=form_post`: return an auto-submitting HTML form that POSTs
     `code` and `state` to the redirect URI.
5. If `response_type` includes `id_token` (hybrid flow), also include `id_token`
   in the response. The ID token must include a `c_hash` claim (left half of
   SHA256 of the authorization code).

### Authorization code properties

- Stored in SQLite with: `code`, `client_id`, `redirect_uri`, `user_id`,
  `tenant_id`, `scope`, `nonce`, `code_challenge`, `code_challenge_method`,
  `created_at`, `expires_at`.
- Expires after 60 seconds.
- Single-use: deleted after redemption.

### Error responses

Errors are returned to the `redirect_uri` using the selected `response_mode`:

| Error code | Condition |
| --- | --- |
| `invalid_request` | Missing required parameter. |
| `unauthorized_client` | Client not registered. |
| `access_denied` | User denied consent. |
| `unsupported_response_type` | Unsupported `response_type`. |
| `login_required` | `prompt=none` but no active session. |
| `interaction_required` | `prompt=none` but consent or MFA needed. |
| `server_error` | Internal error. |

If `redirect_uri` is invalid or not registered, display an error page instead
of redirecting.

---

## 4. Token Endpoint

### Request (POST, `application/x-www-form-urlencoded`)

#### Grant type: `authorization_code`

| Parameter | Required | Description |
| --- | --- | --- |
| `grant_type` | yes | `authorization_code` |
| `client_id` | yes | Must match the code's client. |
| `code` | yes | The authorization code. |
| `redirect_uri` | yes | Must match the value used in the authorize request. |
| `code_verifier` | if PKCE | The PKCE code verifier. Validated against the stored `code_challenge`. |
| `client_secret` | confidential clients | The client's secret. |
| `client_assertion_type` | alternative to secret | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` |
| `client_assertion` | alternative to secret | A signed JWT assertion. |

#### Grant type: `refresh_token`

| Parameter | Required | Description |
| --- | --- | --- |
| `grant_type` | yes | `refresh_token` |
| `client_id` | yes | Must match the original client. |
| `refresh_token` | yes | The refresh token. |
| `scope` | optional | Must be a subset of the original scope. |
| `client_secret` | confidential clients | The client's secret. |
| `client_assertion_type` | alternative to secret | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` |
| `client_assertion` | alternative to secret | A signed JWT assertion. |

#### Grant type: `password` (ROPC)

| Parameter | Required | Description |
| --- | --- | --- |
| `grant_type` | yes | `password` |
| `client_id` | yes | Application client ID. |
| `username` | yes | User's email/UPN. |
| `password` | yes | User's password. |
| `scope` | recommended | Space-separated scopes. |
| `client_secret` | confidential clients | The client's secret. |

ROPC constraints:
- Not supported with `tenant` set to `common` or `consumers` -- return `invalid_request`.
- Only supported for tenant-specific or `organizations` endpoints.

### Client authentication

Three methods supported, checked in order:

1. **`client_secret_basic`**: `Authorization: Basic base64(client_id:client_secret)` header.
2. **`client_secret_post`**: `client_secret` in the POST body.
3. **`private_key_jwt`**: `client_assertion_type` + `client_assertion` in the POST body.
   For the mock server, validate that the assertion is a parseable JWT with `sub` and
   `iss` matching `client_id` and `aud` matching the token endpoint URL. Full signature
   validation of the assertion is optional for dev/test purposes.

Public clients (no secret configured) skip client authentication.

### PKCE validation

If the authorization code was issued with a `code_challenge`:
- `code_challenge_method=S256`: verify `base64url(sha256(code_verifier)) == code_challenge`.
- `code_challenge_method=plain`: verify `code_verifier == code_challenge`.
- If no `code_verifier` is provided, reject with `invalid_grant`.

### Successful response

```json
{
  "token_type": "Bearer",
  "scope": "openid profile email",
  "expires_in": 3599,
  "access_token": "<jwt>",
  "id_token": "<jwt>",
  "refresh_token": "<opaque-string>"
}
```

- `access_token`: always returned.
- `id_token`: returned if `openid` was in the requested scope.
- `refresh_token`: returned if `offline_access` was in the requested scope.
  Also returned on `grant_type=refresh_token` (token rotation).
- `expires_in`: access token lifetime in seconds.

### Error response

```json
{
  "error": "invalid_grant",
  "error_description": "The authorization code has expired.",
  "error_codes": [70008],
  "timestamp": "2024-01-09 02:02:12Z",
  "trace_id": "<uuid>",
  "correlation_id": "<uuid>"
}
```

| Error code | Condition |
| --- | --- |
| `invalid_request` | Missing required parameter or unsupported grant type on wrong tenant type. |
| `invalid_grant` | Code expired, already used, PKCE mismatch, or bad credentials (ROPC). |
| `invalid_client` | Client authentication failed. |
| `unauthorized_client` | Client not allowed this grant type. |
| `unsupported_grant_type` | Unrecognized `grant_type`. |
| `invalid_scope` | Requested scope not valid. |
| `consent_required` | User hasn't consented to requested scopes. |
| `interaction_required` | Silent request failed. |

---

## 5. JWKS Endpoint

Returns the public keys used to sign tokens, in JWK Set format.

```json
{
  "keys": [
    {
      "kty": "RSA",
      "use": "sig",
      "kid": "<key-id>",
      "n": "<modulus-base64url>",
      "e": "AQAB",
      "issuer": "{scheme}://{host}/{tenantid}/v2.0"
    }
  ]
}
```

- Key type is RSA, algorithm is RS256.
- `kid` is a stable identifier for the key. Must match the `kid` in JWT headers.
- `issuer` field per key follows Entra's pattern: `{scheme}://{host}/{tenantid}/v2.0`
  for tenant-specific keys.
- Support multiple keys to enable key rotation. Only one key is active for signing
  at a time; previous keys remain in the JWKS for verification of recently issued tokens.

### Key management

- On first startup, generate an RSA 2048-bit key pair and store it in SQLite with a
  `kid`, creation timestamp, and active flag.
- A key rotation endpoint or CLI command can generate a new key and mark the old one
  as inactive (but still published in JWKS).

---

## 6. Token Formats

### ID Token (JWT, signed with RS256)

#### Header

```json
{
  "typ": "JWT",
  "alg": "RS256",
  "kid": "<key-id>"
}
```

#### Payload (v2.0)

| Claim | Type | Description |
| --- | --- | --- |
| `ver` | string | Always `"2.0"`. |
| `iss` | string | `{scheme}://{host}/{tenant_id}/v2.0` |
| `aud` | string | The `client_id` of the requesting application. |
| `sub` | string | Pairwise subject identifier. Unique per user+client combination. |
| `oid` | string (GUID) | Immutable user object ID. Same across all apps in a tenant. |
| `tid` | string (GUID) | Tenant ID. |
| `iat` | int | Issued-at (Unix timestamp). |
| `nbf` | int | Not-before (Unix timestamp). Same as `iat`. |
| `exp` | int | Expiration (Unix timestamp). Default: `iat` + 3600 (1 hour). |
| `nonce` | string | Echoed from the authorization request. |
| `name` | string | User's display name. Requires `profile` scope. |
| `preferred_username` | string | User's UPN or email. Requires `profile` scope. |
| `email` | string | User's email. Requires `email` scope. |
| `roles` | array of strings | Roles assigned to the user for this app. Optional. |
| `groups` | array of strings | Group IDs. Included if configured and under overage limit. |
| `aio` | string | Opaque. Generate a random string. |
| `rh` | string | Opaque. Generate a random string. |
| `uti` | string | Token identifier (random, unique). |
| `c_hash` | string | Code hash. Only in hybrid flow responses from `/authorize`. Left 128 bits of SHA256 of the `code`, base64url-encoded. |
| `at_hash` | string | Access token hash. Only in implicit flow responses from `/authorize`. Left 128 bits of SHA256 of the `access_token`, base64url-encoded. |

#### Groups overage

If the user is a member of more than 200 groups, omit the `groups` claim and instead include:

```json
{
  "_claim_names": { "groups": "src1" },
  "_claim_sources": {
    "src1": {
      "endpoint": "{scheme}://{graph_host}/v1.0/users/{oid}/getMemberObjects"
    }
  }
}
```

### Access Token (JWT, signed with RS256)

The access token is a JWT with similar structure to the ID token but with these differences:

| Claim | Description |
| --- | --- |
| `aud` | The resource (API) identifier, not the client_id. For Graph API access, use `https://graph.microsoft.com`. For first-party resources, use the client_id. |
| `scp` | Space-separated string of granted scopes (instead of `roles`). |
| `azp` | The client_id of the requesting application. |
| `azpacr` | Authentication method of the client. `0` = public client, `1` = client secret, `2` = certificate. |

For the mock server, the access token `aud` can default to the `client_id` unless
a specific resource is requested.

### Refresh Token

- Opaque string (not a JWT). Generate a cryptographically random token (e.g. 64 hex chars).
- Stored in SQLite with: `token`, `client_id`, `user_id`, `tenant_id`, `scope`,
  `created_at`, `expires_at`.
- Default lifetime: 90 days (configurable).
- On refresh, issue a new refresh token and keep the old one valid (Entra behavior).
  The old token can be cleaned up after a grace period.

---

## 7. UserInfo Endpoint

### Request

```
GET /oidc/userinfo HTTP/1.1
Host: {host}
Authorization: Bearer <access_token>
```

Supports both GET and POST.

### Response

```json
{
  "sub": "<pairwise-subject-id>",
  "name": "Display Name",
  "family_name": "Last",
  "given_name": "First",
  "picture": "https://{host}/v1.0/me/photo/$value",
  "email": "user@example.com"
}
```

- Validate the Bearer token. Extract user identity from the access token claims.
- `name`, `family_name`, `given_name`, `picture` require the `profile` scope.
- `email` requires the `email` scope.
- Return 401 if the token is invalid or expired.

---

## 8. Logout Endpoint

### Request (GET or POST)

| Parameter | Required | Description |
| --- | --- | --- |
| `post_logout_redirect_uri` | recommended | Where to redirect after logout. Must be a registered redirect URI. |
| `logout_hint` | optional | Identifies which user to sign out without prompting. |

### Behavior

1. Clear the user's server-side session.
2. If `post_logout_redirect_uri` is valid, redirect there.
3. Otherwise, display a generic "signed out" page.
4. If front-channel logout URLs are configured for other clients in the session,
   render hidden iframes pointing to each front-channel logout URL.

---

## 9. Data Model (SQLite)

### `tenants`

| Column | Type | Description |
| --- | --- | --- |
| `id` | TEXT (GUID) | Primary key. Tenant GUID. |
| `domain` | TEXT | e.g. `contoso.onmicrosoft.com`. Unique. |
| `display_name` | TEXT | Human-readable name. |

### `users`

| Column | Type | Description |
| --- | --- | --- |
| `id` | TEXT (GUID) | Primary key. User object ID (`oid`). |
| `tenant_id` | TEXT (GUID) | FK to `tenants.id`. |
| `upn` | TEXT | User principal name (email-like). Unique within tenant. |
| `email` | TEXT | Email address. |
| `display_name` | TEXT | Full display name. |
| `given_name` | TEXT | First name. |
| `family_name` | TEXT | Last name. |
| `password_hash` | TEXT | Werkzeug-generated password hash. |

### `user_groups`

| Column | Type | Description |
| --- | --- | --- |
| `user_id` | TEXT (GUID) | FK to `users.id`. |
| `group_id` | TEXT (GUID) | Group object ID. |
| `group_name` | TEXT | Display name of the group. |

### `clients` (app registrations)

| Column | Type | Description |
| --- | --- | --- |
| `client_id` | TEXT (GUID) | Primary key. Application (client) ID. |
| `tenant_id` | TEXT (GUID) | FK to `tenants.id`. |
| `display_name` | TEXT | Application name. |
| `client_secret` | TEXT | Hashed client secret. NULL for public clients. |
| `client_type` | TEXT | `confidential` or `public`. |
| `redirect_uris` | TEXT (JSON array) | Registered redirect URIs. |
| `front_channel_logout_uri` | TEXT | Front-channel logout URL. NULL if not set. |
| `allowed_scopes` | TEXT (JSON array) | Scopes this app can request. |

### `auth_codes`

| Column | Type | Description |
| --- | --- | --- |
| `code` | TEXT | Primary key. The authorization code. |
| `client_id` | TEXT (GUID) | FK to `clients.client_id`. |
| `user_id` | TEXT (GUID) | FK to `users.id`. |
| `tenant_id` | TEXT (GUID) | FK to `tenants.id`. |
| `redirect_uri` | TEXT | The redirect URI used in the authorize request. |
| `scope` | TEXT | Space-separated scopes. |
| `nonce` | TEXT | The nonce from the authorize request. |
| `code_challenge` | TEXT | PKCE code challenge. NULL if not used. |
| `code_challenge_method` | TEXT | `S256` or `plain`. NULL if not used. |
| `created_at` | REAL | Unix timestamp. |
| `expires_at` | REAL | Unix timestamp. `created_at` + 60. |

### `refresh_tokens`

| Column | Type | Description |
| --- | --- | --- |
| `token` | TEXT | Primary key. The opaque refresh token string. |
| `client_id` | TEXT (GUID) | FK to `clients.client_id`. |
| `user_id` | TEXT (GUID) | FK to `users.id`. |
| `tenant_id` | TEXT (GUID) | FK to `tenants.id`. |
| `scope` | TEXT | Space-separated scopes. |
| `created_at` | REAL | Unix timestamp. |
| `expires_at` | REAL | Unix timestamp. `created_at` + 7776000 (90 days). |
| `revoked` | INTEGER | 0 or 1. |

### `sessions`

| Column | Type | Description |
| --- | --- | --- |
| `session_id` | TEXT | Primary key. Random session identifier (stored in cookie). |
| `user_id` | TEXT (GUID) | FK to `users.id`. |
| `tenant_id` | TEXT (GUID) | FK to `tenants.id`. |
| `created_at` | REAL | Unix timestamp. |
| `expires_at` | REAL | Unix timestamp. |

### `signing_keys`

| Column | Type | Description |
| --- | --- | --- |
| `kid` | TEXT | Primary key. Key ID. |
| `private_key_pem` | TEXT | PEM-encoded RSA private key. |
| `public_key_pem` | TEXT | PEM-encoded RSA public key. |
| `created_at` | REAL | Unix timestamp. |
| `active` | INTEGER | 1 = current signing key, 0 = rotated (still in JWKS). |

---

## 10. Configuration

Configuration is loaded from a YAML file at startup. Example:

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  scheme: "http"            # "http" for dev/testing, "https" for VM/production fidelity
  external_hostname: "localhost:8080"

tenants:
  - id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    domain: "contoso.onmicrosoft.com"
    display_name: "Contoso"

users:
  - id: "00000000-0000-0000-0000-000000000001"
    tenant_id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    upn: "admin@contoso.onmicrosoft.com"
    email: "admin@contoso.com"
    display_name: "Admin User"
    given_name: "Admin"
    family_name: "User"
    password: "changeme"
    groups:
      - id: "g1g1g1g1-g1g1-g1g1-g1g1-g1g1g1g1g1g1"
        name: "Admins"

clients:
  - client_id: "11111111-2222-3333-4444-555555555555"
    tenant_id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    display_name: "OAuth2 Proxy"
    client_secret: "my-client-secret"
    client_type: "confidential"
    redirect_uris:
      - "http://localhost:4180/oauth2/callback"
    allowed_scopes:
      - "openid"
      - "profile"
      - "email"
      - "offline_access"

token_lifetimes:
  access_token_seconds: 3600       # default: 3600 (1 hour)
  id_token_seconds: 3600           # default: 3600 (1 hour)
  refresh_token_days: 90           # default: 90
  auth_code_seconds: 60            # default: 60
```

On startup, the server seeds the database from this config. Existing records are
updated, new ones are inserted, but records not in the config are left in place
(to support runtime additions via a future admin API).

---

## 11. Pairwise Subject Identifiers

The `sub` claim must be pairwise per the Entra spec: unique per user+client combination.

Generate as: `SHA256(user_id + client_id + tenant_salt)`, hex-encoded.

The tenant salt is a random value generated once per tenant and stored in the
`tenants` table. This ensures `sub` values can't be correlated across clients.

---

## 12. Session Management

- On successful login at the authorize endpoint, set a session cookie
  (`entra_mock_session`) containing the `session_id`.
- The session cookie is `HttpOnly`, `SameSite=Lax`. The `Secure` flag is set only
  when `scheme=https`. In plaintext mode (`scheme=http`), `Secure` is omitted so
  the cookie works over plain HTTP.
- `prompt=none` uses the session cookie to skip login.
- `prompt=login` ignores any existing session and forces re-authentication.
- Logout clears the session from the database and deletes the cookie.

---

## 13. Tenant Resolution

The `{tenant}` path segment is resolved as follows:

1. If it's a GUID, look up `tenants.id`.
2. If it's a domain string, look up `tenants.domain`.
3. If it's `common` or `organizations`, accept any registered tenant. The actual
   tenant is determined by the user who authenticates (from their `tenant_id`).
4. If it's `consumers`, return `invalid_request` (not supported by the mock).
5. If no tenant is found, return a 400 error page.

