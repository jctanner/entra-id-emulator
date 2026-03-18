# Implementation Plan

How to build, deploy, and test the Entra ID mock server defined in `server-spec.md`.

## Development environment

- Podman + podman-compose for containerized testing
- Python 3.11+ on the host for direct development and unit testing
- VM with `/etc/hosts` overrides for full-fidelity integration testing

## Phase 1: Core server (standalone, no containers)

Build the Flask app on the host, test with `curl` and a browser.

### Steps

1. **Project scaffolding**
   - Create `entra_mock/` package with `app.py`, `config.py`, `db.py`, `run.py`.
   - Create `config.yaml` with one tenant, one user, one client.
   - Implement config loading and SQLite schema initialization + seeding.

2. **Key management (`keys.py`)**
   - Generate RSA 2048-bit key pair on first run.
   - Store in SQLite `signing_keys` table.
   - Build JWKS from stored keys.
   - JWT signing helper using PyJWT.

3. **Discovery endpoint**
   - `GET /{tenant}/v2.0/.well-known/openid-configuration`
   - Return JSON with all endpoint URLs derived from `external_hostname` config.

4. **JWKS endpoint**
   - `GET /{tenant}/discovery/v2.0/keys`
   - Return JWK Set from stored keys.

5. **Authorization endpoint**
   - `GET /{tenant}/oauth2/v2.0/authorize`
   - Parameter validation, tenant resolution.
   - Render login page (minimal HTML form).
   - On POST with credentials: validate user, create auth code, redirect.
   - Support `response_mode=query`, `fragment`, `form_post`.
   - PKCE: store `code_challenge` and `code_challenge_method` with the auth code.

6. **Token endpoint**
   - `POST /{tenant}/oauth2/v2.0/token`
   - `grant_type=authorization_code`: validate code, PKCE, client auth. Issue tokens.
   - `grant_type=refresh_token`: validate refresh token, issue new tokens.
   - `grant_type=password` (ROPC): validate username/password, issue tokens.
   - Token generation: ID token (JWT), access token (JWT), refresh token (opaque).

7. **UserInfo endpoint**
   - `GET/POST /oidc/userinfo`
   - Validate Bearer token, return user claims as JSON.

8. **Logout endpoint**
   - `GET/POST /{tenant}/oauth2/v2.0/logout`
   - Clear session, redirect to `post_logout_redirect_uri`.

### Verification

- `curl` the discovery endpoint, verify JSON structure.
- `curl` the JWKS endpoint, verify key format.
- Browser: walk through authorize -> login -> redirect -> token exchange manually.
- Decode issued JWTs with `python3 -c` or `jwt.io`, verify claims.
- Unit tests for token generation, PKCE validation, claim structure.

## Phase 2: Containerize

### Dockerfile

- Based on `python:3.11-slim`.
- Install dependencies from `requirements.txt`.
- Copy `entra_mock/` and `config.yaml`.
- Expose port 8080 (plaintext by default).
- Entrypoint: `python run.py`.

The Dockerfile installs dependencies into the image but does NOT copy source code.
Source is mounted in at runtime so edits on the host are reflected immediately.

### podman-compose.yml (dev/test)

```yaml
version: "3"
services:
  entra-mock:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./entra_mock:/app/entra_mock:Z       # live reload: source code
      - ./templates:/app/templates:Z         # live reload: Jinja2 templates
      - ./static:/app/static:Z              # live reload: CSS/assets
      - ./config.yaml:/app/config.yaml:ro,Z  # config (read-only)
      - ./data:/app/data:Z                   # SQLite DB persisted
      - ./run.py:/app/run.py:ro,Z            # entrypoint
    environment:
      - FLASK_DEBUG=1                        # enables auto-reloader + debugger
      - FLASK_ENV=development
```

Notes:
- `:Z` suffix is required for podman with SELinux (relabels for container access).
- `FLASK_DEBUG=1` enables the Werkzeug auto-reloader. Flask watches mounted files
  for changes and restarts the server process automatically when source is modified.
- The Dockerfile only needs to provide the Python runtime and installed packages.
  All application code comes from the volume mounts.
- For templates/static, Flask picks up changes on each request without a restart
  when `FLASK_DEBUG=1` (Jinja2 auto-reload is enabled in debug mode).

### Dockerfile (dev-oriented)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code is NOT copied -- mounted at runtime
EXPOSE 8080
CMD ["python", "run.py"]
```

### Verification

- `podman-compose up --build`, verify endpoints respond.
- Edit a route handler on the host, verify the server auto-restarts.
- Edit a template on the host, refresh the browser, verify the change appears.
- Same curl/browser tests as Phase 1 but against the container.

## Phase 3: Integration test with oauth2-proxy

### Setup (Scenario A: mock hostname)

The mock server uses its own hostname. No DNS tricks needed.
oauth2-proxy is configured with `--oidc-issuer-url` pointing at the mock.

```yaml
version: "3"
services:
  entra-mock:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./entra_mock:/app/entra_mock:Z
      - ./templates:/app/templates:Z
      - ./static:/app/static:Z
      - ./config.yaml:/app/config.yaml:ro,Z
      - ./data:/app/data:Z
      - ./run.py:/app/run.py:ro,Z
    environment:
      - FLASK_DEBUG=1

  oauth2-proxy:
    image: quay.io/oauth2-proxy/oauth2-proxy:latest
    ports:
      - "4180:4180"
    command:
      - --http-address=0.0.0.0:4180
      - --provider=oidc
      - --oidc-issuer-url=http://entra-mock:8080/{tenant-id}/v2.0
      - --client-id=<client-id>
      - --client-secret=<client-secret>
      - --redirect-url=http://localhost:4180/oauth2/callback
      - --upstream=http://httpbin:80
      - --cookie-secret=<random-32-bytes>
      - --email-domain=*
      - --insecure-oidc-skip-issuer-verification=true
      - --skip-provider-button=true
    depends_on:
      - entra-mock

  httpbin:
    image: kennethreitz/httpbin
    ports:
      - "8080:80"
```

Notes:
- `--insecure-oidc-skip-issuer-verification` may be needed if the issuer URL
  seen by oauth2-proxy (via container name) differs from what the browser sees.
- The mock's `external_hostname` config should match what the browser uses
  (e.g. `localhost:8443`) for redirect URLs to work.

### Verification

1. Open `http://localhost:4180/` in browser.
2. Get redirected to mock's login page.
3. Enter test credentials.
4. Get redirected back through oauth2-proxy.
5. See httpbin response (authenticated).

### Setup (Scenario B: full Entra fidelity)

For testing with the mock pretending to be `login.microsoftonline.com`.
Requires a VM or host-level `/etc/hosts` changes.

1. Set up a VM (Fedora/RHEL).
2. Add to `/etc/hosts`:
   ```
   127.0.0.1  login.microsoftonline.com
   127.0.0.1  graph.microsoft.com
   ```
3. Configure the mock with:
   ```yaml
   server:
     scheme: "https"
     external_hostname: "login.microsoftonline.com"
     port: 443
   ```
4. Generate a self-signed CA and TLS cert for `login.microsoftonline.com`.
   Install the CA cert in the VM's trust store.
5. Run the mock on port 443 (or use a reverse proxy).
6. Run oauth2-proxy with standard Entra ID provider config (no `--oidc-issuer-url`
   override needed since discovery URL matches real Entra).
7. The UserInfo endpoint needs to be served from `graph.microsoft.com` --
   either run a second mock instance or add a route that handles the Graph
   hostname.

### Verification

Same browser test as Scenario A, but:
- oauth2-proxy uses the standard `--provider=microsoft-entra-id` provider.
- No `--insecure-oidc-skip-issuer-verification` needed.
- Tokens have `iss: https://login.microsoftonline.com/{tenant}/v2.0`.
- The multi-tenant `getTenantFromToken` regex passes.

## Phase 4: Refinements (as needed)

- **Session management**: cookie-based sessions for `prompt=none` support.
- **Consent screen**: render consent page, store consent records.
- **Groups overage**: emit `_claim_names`/`_claim_sources` when group count > 200.
- **Graph API mock**: `GET /v1.0/me/transitiveMemberOf` for group overage resolution.
- **Key rotation**: CLI command or admin endpoint to rotate signing keys.
- **TLS**: built-in TLS support or document reverse proxy setup.
- **Admin API**: REST endpoints for managing tenants/users/clients at runtime.

## Dependencies

```
flask>=3.0
pyjwt>=2.8
cryptography>=42.0
jwcrypto>=1.5
pyyaml>=6.0
werkzeug>=3.0    # included with flask, used for password hashing
```

## Build order summary

| Phase | Delivers | Test method |
| --- | --- | --- |
| 1 | Working server on host | curl, browser, unit tests |
| 2 | Container image | podman, same tests |
| 3 | oauth2-proxy integration | podman-compose, browser |
| 4 | Production-like features | Scenario B in VM |
