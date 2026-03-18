"""ID token, access token, and refresh token generation."""

import hashlib
import secrets
import time

from entra_mock.db import get_db, get_effective_lifetimes, get_tenant_salt, get_user_groups
from entra_mock.keys import sign_jwt, _base64url_encode


def _pairwise_sub(user_id, client_id, tenant_salt):
    """Generate a pairwise subject identifier.

    SHA256(user_id + client_id + tenant_salt), hex-encoded.
    """
    data = f"{user_id}{client_id}{tenant_salt}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _half_hash(value):
    """Compute left half of SHA256, base64url-encoded.

    Used for at_hash and c_hash claims.
    """
    digest = hashlib.sha256(value.encode("ascii")).digest()
    left_half = digest[:16]  # left 128 bits
    return _base64url_encode(left_half)


def generate_id_token(
    app,
    user,
    client_id,
    tenant_id,
    scope,
    nonce=None,
    code=None,
    access_token=None,
):
    """Generate an ID token JWT.

    Args:
        app: Flask app.
        user: User DB row.
        client_id: The client_id (audience).
        tenant_id: Tenant GUID.
        scope: Space-separated scope string.
        nonce: Nonce from the authorization request.
        code: Authorization code (for c_hash in hybrid flow).
        access_token: Access token string (for at_hash in implicit flow).

    Returns:
        Encoded JWT string.
    """
    config = app.config["ENTRA_CONFIG"]
    server = config["server"]
    scheme = server["scheme"]
    host = server["external_hostname"]
    lifetimes = get_effective_lifetimes(app, tenant_id, client_id)

    now = int(time.time())

    conn = get_db(app)
    tenant_salt = get_tenant_salt(conn, tenant_id)
    groups = get_user_groups(conn, user["id"])
    conn.close()

    sub = _pairwise_sub(user["id"], client_id, tenant_salt)

    scopes = scope.split() if scope else []

    payload = {
        "ver": "2.0",
        "iss": f"{scheme}://{host}/{tenant_id}/v2.0",
        "aud": client_id,
        "sub": sub,
        "oid": user["id"],
        "tid": tenant_id,
        "iat": now,
        "nbf": now,
        "exp": now + lifetimes["id_token_seconds"],
        "aio": secrets.token_urlsafe(16),
        "rh": secrets.token_urlsafe(8),
        "uti": secrets.token_urlsafe(8),
    }

    if nonce:
        payload["nonce"] = nonce

    if "profile" in scopes:
        payload["name"] = user["display_name"]
        payload["preferred_username"] = user["upn"]

    if "email" in scopes:
        payload["email"] = user["email"]

    # Groups
    group_list = [g["group_id"] for g in groups]
    if len(group_list) <= 200:
        if group_list:
            payload["groups"] = group_list
    else:
        # Groups overage
        payload["_claim_names"] = {"groups": "src1"}
        payload["_claim_sources"] = {
            "src1": {
                "endpoint": f"{scheme}://{host}/v1.0/users/{user['id']}/getMemberObjects"
            }
        }

    # Hybrid flow hashes
    if code:
        payload["c_hash"] = _half_hash(code)
    if access_token:
        payload["at_hash"] = _half_hash(access_token)

    return sign_jwt(app, payload)


OIDC_SCOPES = {"openid", "profile", "email", "offline_access"}

# Microsoft Graph API resource ID
MS_GRAPH_RESOURCE_ID = "00000003-0000-0000-c000-000000000000"


def _has_resource_scope(scopes, application_id_uri):
    """Check if any requested scope targets the client's own API.

    A resource scope is any scope that is not a standard OIDC scope AND
    starts with the client's application_id_uri (e.g. api://client-id/.default).
    """
    if not application_id_uri:
        return False
    for s in scopes:
        if s not in OIDC_SCOPES and s.startswith(application_id_uri):
            return True
    return False


def generate_access_token(app, user, client_id, tenant_id, scope,
                          application_id_uri=None):
    """Generate an access token JWT.

    When only OIDC scopes are requested (or the client has no
    application_id_uri), the access token mimics a Microsoft Graph v1.0
    token. When a resource scope matching the client's application_id_uri
    is present, the token is issued as a v2.0 app-owned token.

    Args:
        app: Flask app.
        user: User DB row.
        client_id: The requesting client_id.
        tenant_id: Tenant GUID.
        scope: Space-separated scope string.
        application_id_uri: The client's application ID URI (optional).

    Returns:
        Encoded JWT string.
    """
    config = app.config["ENTRA_CONFIG"]
    server = config["server"]
    scheme = server["scheme"]
    host = server["external_hostname"]
    lifetimes = get_effective_lifetimes(app, tenant_id, client_id)

    now = int(time.time())

    conn = get_db(app)
    tenant_salt = get_tenant_salt(conn, tenant_id)
    conn.close()

    sub = _pairwise_sub(user["id"], client_id, tenant_salt)

    # Determine client auth method
    conn = get_db(app)
    client = conn.execute(
        "SELECT client_type FROM clients WHERE client_id = ?", (client_id,)
    ).fetchone()
    conn.close()

    azpacr = "0"  # public
    if client and client["client_type"] == "confidential":
        azpacr = "1"  # client secret

    scopes = scope.split() if scope else []

    # Determine token style based on requested scopes
    app_owned = _has_resource_scope(scopes, application_id_uri)

    if app_owned:
        # v2.0 app-owned access token (current behavior)
        aud = client_id
        iss = f"{scheme}://{host}/{tenant_id}/v2.0"
        ver = "2.0"
        scp = " ".join(s for s in scopes if s != "offline_access")
    else:
        # v1.0 Graph-like access token
        aud = MS_GRAPH_RESOURCE_ID
        iss = f"{scheme}://{host}/{tenant_id}"
        ver = "1.0"
        scp = " ".join(s for s in scopes if s not in {"offline_access", "openid"})

    payload = {
        "ver": ver,
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "oid": user["id"],
        "tid": tenant_id,
        "iat": now,
        "nbf": now,
        "exp": now + lifetimes["access_token_seconds"],
        "azp": client_id,
        "azpacr": azpacr,
        "scp": scp,
        "aio": secrets.token_urlsafe(16),
        "rh": secrets.token_urlsafe(8),
        "uti": secrets.token_urlsafe(8),
    }

    if "profile" in scopes:
        payload["name"] = user["display_name"]
        payload["preferred_username"] = user["upn"]

    if "email" in scopes:
        payload["email"] = user["email"]

    return sign_jwt(app, payload)


def generate_refresh_token(app, user, client_id, tenant_id, scope):
    """Generate and store an opaque refresh token.

    Returns:
        The refresh token string.
    """
    lifetimes = get_effective_lifetimes(app, tenant_id, client_id)
    now = time.time()
    expires_at = now + (lifetimes["refresh_token_days"] * 86400)

    token = secrets.token_hex(64)

    conn = get_db(app)
    conn.execute(
        """INSERT INTO refresh_tokens (token, client_id, user_id, tenant_id,
                                       scope, created_at, expires_at, revoked)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (token, client_id, user["id"], tenant_id, scope, now, expires_at),
    )
    conn.commit()
    conn.close()

    return token
