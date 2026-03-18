# Entra ID Mock - API Usage Guide

This document describes how to programmatically manage the Entra ID mock service via its Admin REST API. All admin endpoints are under `/admin/api` and accept/return JSON.

The service also provides a web UI at `/admin/`.

## Base URL

```
http://localhost:8080
```

## Starting the Service

```bash
pip install -r requirements.txt
python run.py
```

Or with Docker/Podman:

```bash
docker-compose up --build
```

Configuration is loaded from `config.yaml` (or set `ENTRA_MOCK_CONFIG` env var to a custom path). The config seeds initial tenants, users, groups, and clients on startup.

---

## Resource Management Order

Resources have dependencies. Create them in this order:

1. **Tenant** (required by all other resources)
2. **Groups** (belong to a tenant)
3. **Users** (belong to a tenant, optionally assigned to groups)
4. **Clients** (OAuth apps, belong to a tenant)

---

## Tenants

### List tenants

```
GET /admin/api/tenants
```

Response `200`:
```json
[
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "domain": "contoso.onmicrosoft.com",
    "display_name": "Contoso",
    "salt": "..."
  }
]
```

### Create tenant

```
POST /admin/api/tenants
Content-Type: application/json
```

Body:
```json
{
  "domain": "fabrikam.onmicrosoft.com",
  "display_name": "Fabrikam"
}
```

Optional fields:
- `id` - provide a specific UUID; auto-generated if omitted

Response `201`:
```json
{
  "id": "generated-or-provided-uuid",
  "domain": "fabrikam.onmicrosoft.com",
  "display_name": "Fabrikam"
}
```

Errors: `400` if `domain` or `display_name` missing. `409` on duplicate.

### Update tenant

```
PUT /admin/api/tenants/<tenant_id>
Content-Type: application/json
```

Body:
```json
{
  "domain": "fabrikam-new.onmicrosoft.com",
  "display_name": "Fabrikam (Renamed)"
}
```

Response `200`: the updated tenant object. `404` if not found.

### Delete tenant

```
DELETE /admin/api/tenants/<tenant_id>
```

Response `204` (no body).

---

## Groups

### List groups

```
GET /admin/api/groups
```

Response `200`:
```json
[
  {
    "id": "g1g1g1g1-g1g1-g1g1-g1g1-g1g1g1g1g1g1",
    "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "name": "Admins",
    "tenant_name": "Contoso",
    "member_ids": ["00000000-0000-0000-0000-000000000001"]
  }
]
```

### Create group

```
POST /admin/api/groups
Content-Type: application/json
```

Body:
```json
{
  "name": "Developers",
  "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "member_ids": ["user-uuid-1", "user-uuid-2"]
}
```

- `name` and `tenant_id` are **required**
- `id` - optional, auto-generated if omitted
- `member_ids` - optional array of user UUIDs to add as members

Response `201`:
```json
{
  "id": "generated-or-provided-uuid",
  "name": "Developers",
  "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "member_ids": ["user-uuid-1", "user-uuid-2"]
}
```

### Update group

```
PUT /admin/api/groups/<group_id>
Content-Type: application/json
```

Body (all fields optional):
```json
{
  "name": "Senior Developers",
  "member_ids": ["user-uuid-1", "user-uuid-3"]
}
```

If `member_ids` is provided, it **replaces** all current members (delete-and-reinsert).

Response `200`: updated group object. `404` if not found.

### Delete group

```
DELETE /admin/api/groups/<group_id>
```

Response `204` (no body). Also removes all user-group memberships for this group.

---

## Users

### List users

```
GET /admin/api/users
```

Response `200`:
```json
[
  {
    "id": "00000000-0000-0000-0000-000000000001",
    "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "tenant_name": "Contoso",
    "upn": "admin@contoso.onmicrosoft.com",
    "email": "admin@contoso.com",
    "display_name": "Admin User",
    "given_name": "Admin",
    "family_name": "User",
    "groups": [
      {"id": "g1g1g1g1-g1g1-g1g1-g1g1-g1g1g1g1g1g1", "name": "Admins"}
    ]
  }
]
```

Note: `password_hash` is stripped from responses.

### Create user

```
POST /admin/api/users
Content-Type: application/json
```

Body:
```json
{
  "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "upn": "jane@contoso.onmicrosoft.com",
  "email": "jane@contoso.com",
  "display_name": "Jane Doe",
  "given_name": "Jane",
  "family_name": "Doe",
  "password": "s3cret!",
  "group_ids": ["g1g1g1g1-g1g1-g1g1-g1g1-g1g1g1g1g1g1"]
}
```

**Required**: `tenant_id`, `upn`, `email`, `display_name`, `password`

**Optional**: `id` (auto-generated), `given_name`, `family_name`, `group_ids`

Response `201`:
```json
{
  "id": "generated-or-provided-uuid",
  "upn": "jane@contoso.onmicrosoft.com",
  "display_name": "Jane Doe"
}
```

### Update user

```
PUT /admin/api/users/<user_id>
Content-Type: application/json
```

Body (all fields optional):
```json
{
  "email": "jane.doe@contoso.com",
  "display_name": "Jane M. Doe",
  "password": "new-password",
  "group_ids": ["group-uuid-1", "group-uuid-2"]
}
```

- If `password` is provided, the password is updated
- If `group_ids` is provided, it **replaces** all group memberships

Response `200`: updated user object (without `password_hash`). `404` if not found.

### Delete user

```
DELETE /admin/api/users/<user_id>
```

Response `204` (no body). Also removes all group memberships for this user.

---

## Clients (OAuth Applications)

### List clients

```
GET /admin/api/clients
```

Response `200`:
```json
[
  {
    "client_id": "11111111-2222-3333-4444-555555555555",
    "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "tenant_name": "Contoso",
    "display_name": "OAuth2 Proxy",
    "client_type": "confidential",
    "redirect_uris": ["http://localhost:4180/oauth2/callback"],
    "allowed_scopes": ["openid", "profile", "email", "offline_access"],
    "application_id_uri": null,
    "front_channel_logout_uri": null
  }
]
```

Note: `client_secret` is stripped from responses.

### Create client

```
POST /admin/api/clients
Content-Type: application/json
```

Body:
```json
{
  "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "display_name": "My App",
  "client_secret": "my-secret-value",
  "client_type": "confidential",
  "redirect_uris": ["http://localhost:3000/callback"],
  "allowed_scopes": ["openid", "profile", "email"],
  "application_id_uri": "api://my-app-id",
  "front_channel_logout_uri": "http://localhost:3000/logout"
}
```

**Required**: `tenant_id`, `display_name`

**Optional**: `client_id` (auto-generated), `client_secret` (null for public clients), `client_type` (default: `"confidential"`), `redirect_uris`, `allowed_scopes`, `application_id_uri`, `front_channel_logout_uri`

Response `201`:
```json
{
  "client_id": "generated-or-provided-uuid",
  "display_name": "My App"
}
```

### Update client

```
PUT /admin/api/clients/<client_id>
Content-Type: application/json
```

Body:
```json
{
  "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "display_name": "My App (Updated)",
  "client_type": "confidential",
  "redirect_uris": ["http://localhost:3000/callback", "http://localhost:3000/callback2"],
  "allowed_scopes": ["openid", "profile", "email", "offline_access"],
  "client_secret": "new-secret"
}
```

- If `client_secret` is provided, it is updated
- `redirect_uris` and `allowed_scopes` are **replaced** entirely

Response `200`: updated client object (without `client_secret`). `404` if not found.

### Delete client

```
DELETE /admin/api/clients/<client_id>
```

Response `204` (no body).

---

## Token Lifetimes

Token lifetimes (access token, ID token, refresh token, and auth code expiry) are configured globally in `config.yaml` and can be overridden per-tenant and per-client via the admin API.

**Resolution order**: client-specific > tenant-specific > global default. Only the keys you provide are overridden; omitted keys fall through to the parent level. Setting `token_lifetimes` to `null` (or omitting it) means "use the parent default."

### Global defaults (config.yaml)

```yaml
token_lifetimes:
  access_token_seconds: 3600
  id_token_seconds: 3600
  refresh_token_days: 90
  auth_code_seconds: 60
```

### Set token lifetimes on a tenant

Include `token_lifetimes` when creating or updating a tenant. Only the keys you specify are overridden; the rest inherit from the global defaults.

```bash
# Create a tenant with shorter access tokens
curl -s -X POST "$BASE/admin/api/tenants" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "shortlived.onmicrosoft.com",
    "display_name": "Short-Lived Corp",
    "token_lifetimes": {
      "access_token_seconds": 300,
      "id_token_seconds": 300
    }
  }'

# Update an existing tenant's lifetimes
curl -s -X PUT "$BASE/admin/api/tenants/$TENANT_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"domain\": \"shortlived.onmicrosoft.com\",
    \"display_name\": \"Short-Lived Corp\",
    \"token_lifetimes\": {
      \"access_token_seconds\": 600,
      \"refresh_token_days\": 7
    }
  }"
```

### Set token lifetimes on a client

Client-level overrides take the highest priority, beating both tenant and global defaults.

```bash
# Create a client with a long-lived access token
curl -s -X POST "$BASE/admin/api/clients" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"display_name\": \"Long Token App\",
    \"client_secret\": \"my-secret\",
    \"redirect_uris\": [\"http://localhost:3000/callback\"],
    \"allowed_scopes\": [\"openid\", \"profile\", \"email\"],
    \"token_lifetimes\": {
      \"access_token_seconds\": 86400
    }
  }"

# Update an existing client's lifetimes
curl -s -X PUT "$BASE/admin/api/clients/$CLIENT_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"display_name\": \"Long Token App\",
    \"redirect_uris\": [\"http://localhost:3000/callback\"],
    \"allowed_scopes\": [\"openid\", \"profile\", \"email\"],
    \"token_lifetimes\": {
      \"access_token_seconds\": 7200,
      \"auth_code_seconds\": 120
    }
  }"
```

### Clear overrides

To remove per-tenant or per-client overrides and revert to the parent default, omit `token_lifetimes` (or set it to `null`) on a PUT request.

### Available keys

| Key | Type | Description |
|-----|------|-------------|
| `access_token_seconds` | int | Access token lifetime in seconds |
| `id_token_seconds` | int | ID token lifetime in seconds |
| `refresh_token_days` | int | Refresh token lifetime in days |
| `auth_code_seconds` | int | Authorization code lifetime in seconds |

### Seed data

You can also set `token_lifetimes` on tenant and client entries in `config.yaml`. These are persisted into the database on startup:

```yaml
tenants:
  - id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    domain: "contoso.onmicrosoft.com"
    display_name: "Contoso"
    token_lifetimes:
      access_token_seconds: 1800

clients:
  - client_id: "11111111-2222-3333-4444-555555555555"
    tenant_id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    display_name: "OAuth2 Proxy"
    client_secret: "my-client-secret"
    redirect_uris:
      - "http://localhost:4180/oauth2/callback"
    allowed_scopes:
      - "openid"
    token_lifetimes:
      access_token_seconds: 300
      auth_code_seconds: 30
```

---

## Full Setup Example

Complete workflow to set up a tenant with a user, group, and client using `curl`:

```bash
BASE=http://localhost:8080

# 1. Create tenant
TENANT_ID=$(curl -s -X POST "$BASE/admin/api/tenants" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.onmicrosoft.com",
    "display_name": "Example Corp"
  }' | jq -r '.id')

echo "Tenant: $TENANT_ID"

# 2. Create group
GROUP_ID=$(curl -s -X POST "$BASE/admin/api/groups" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"engineers\",
    \"tenant_id\": \"$TENANT_ID\"
  }" | jq -r '.id')

echo "Group: $GROUP_ID"

# 3. Create user in that group
USER_ID=$(curl -s -X POST "$BASE/admin/api/users" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"upn\": \"alice@example.onmicrosoft.com\",
    \"email\": \"alice@example.com\",
    \"display_name\": \"Alice Smith\",
    \"given_name\": \"Alice\",
    \"family_name\": \"Smith\",
    \"password\": \"hunter2\",
    \"group_ids\": [\"$GROUP_ID\"]
  }" | jq -r '.id')

echo "User: $USER_ID"

# 4. Create OAuth client
CLIENT_ID=$(curl -s -X POST "$BASE/admin/api/clients" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"display_name\": \"Test App\",
    \"client_secret\": \"test-secret\",
    \"client_type\": \"confidential\",
    \"redirect_uris\": [\"http://localhost:3000/callback\"],
    \"allowed_scopes\": [\"openid\", \"profile\", \"email\", \"offline_access\"]
  }" | jq -r '.client_id')

echo "Client: $CLIENT_ID"
```

---

## OIDC Endpoints Reference

Once tenants/users/clients are configured, OIDC consumers use these endpoints:

| Endpoint | URL |
|----------|-----|
| Discovery | `GET /{tenant_id}/v2.0/.well-known/openid-configuration` |
| Authorize | `GET/POST /{tenant_id}/oauth2/v2.0/authorize` |
| Token | `POST /{tenant_id}/oauth2/v2.0/token` |
| JWKS | `GET /{tenant_id}/discovery/v2.0/keys` |
| UserInfo | `GET/POST /oidc/userinfo` (Bearer token required) |
| Logout | `GET/POST /{tenant_id}/oauth2/v2.0/logout` |

The `{tenant_id}` can be the tenant UUID or domain name. The aliases `common` and `organizations` resolve to the first tenant.

### Getting a Token (Resource Owner Password)

For scripted/automated use, the ROPC grant skips the browser login flow:

```bash
curl -s -X POST "$BASE/$TENANT_ID/oauth2/v2.0/token" \
  -d "grant_type=password" \
  -d "client_id=$CLIENT_ID" \
  -d "client_secret=test-secret" \
  -d "username=alice@example.onmicrosoft.com" \
  -d "password=hunter2" \
  -d "scope=openid profile email"
```

Response:
```json
{
  "token_type": "Bearer",
  "scope": "openid profile email",
  "expires_in": 3600,
  "access_token": "eyJ...",
  "id_token": "eyJ...",
  "refresh_token": "abc..."
}
```

---

## Notes

- All IDs are UUIDs. You may supply your own or let the service auto-generate them.
- Passwords are hashed with PBKDF2 (Werkzeug) before storage; plaintext is never stored.
- Client secrets are also hashed; the original value cannot be retrieved after creation.
- The `groups` claim in ID tokens contains group UUIDs (not names). Plan group IDs accordingly if downstream consumers match on specific values.
- The SQLite database is stored at `data/entra_mock.db` (override with `ENTRA_MOCK_DB` env var).
- Seed data from `config.yaml` is loaded on startup. Runtime changes via the admin API persist in SQLite.
