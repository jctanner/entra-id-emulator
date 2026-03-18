"""Token endpoint."""

import base64
import hashlib
import json
import time
import uuid

from flask import Blueprint, current_app, jsonify, request
from werkzeug.security import check_password_hash

from entra_mock.db import (
    get_db, get_effective_lifetimes, get_tenant, get_client, get_user_by_id,
    get_user_by_upn, get_user_by_email,
)
from entra_mock.tokens import (
    generate_id_token, generate_access_token, generate_refresh_token,
)

bp = Blueprint("token", __name__)


def _token_error(error, description, status=400):
    """Return a standard OAuth2 token error response."""
    return jsonify({
        "error": error,
        "error_description": description,
        "error_codes": [],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "trace_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
    }), status


def _authenticate_client(conn, form_client_id):
    """Authenticate the client using one of the supported methods.

    Returns (client_row, error_response). error_response is None on success.
    """
    client_id = form_client_id
    client_secret = None

    # Method 1: client_secret_basic (Authorization header)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            client_id, client_secret = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return None, _token_error("invalid_client", "Malformed Authorization header.")

    # Method 2: client_secret_post
    if client_secret is None:
        client_secret = request.form.get("client_secret")

    # Method 3: private_key_jwt
    assertion_type = request.form.get("client_assertion_type")
    client_assertion = request.form.get("client_assertion")

    if not client_id:
        return None, _token_error("invalid_request", "Missing client_id.")

    client = get_client(conn, client_id)
    if client is None:
        return None, _token_error("invalid_client", "Client not registered.")

    # Public clients skip authentication
    if client["client_type"] == "public":
        return client, None

    # Confidential client: must authenticate
    if assertion_type and client_assertion:
        # private_key_jwt: minimal validation for mock server
        if assertion_type != "urn:ietf:params:oauth:client-assertion-type:jwt-bearer":
            return None, _token_error("invalid_client", "Unsupported client_assertion_type.")
        # Parse the JWT to verify sub/iss match client_id
        import jwt as pyjwt
        try:
            # Decode without verification for mock purposes
            claims = pyjwt.decode(client_assertion, options={"verify_signature": False})
            if claims.get("sub") != client_id or claims.get("iss") != client_id:
                return None, _token_error("invalid_client", "Client assertion sub/iss mismatch.")
        except pyjwt.DecodeError:
            return None, _token_error("invalid_client", "Invalid client assertion JWT.")
        return client, None

    if client_secret:
        if client["client_secret"] and check_password_hash(client["client_secret"], client_secret):
            return client, None
        return None, _token_error("invalid_client", "Client authentication failed.")

    return None, _token_error("invalid_client", "Client authentication required.")


def _verify_pkce(code_challenge, code_challenge_method, code_verifier):
    """Verify PKCE code_verifier against stored code_challenge.

    Returns True if valid, False otherwise.
    """
    if not code_challenge:
        # No PKCE was used
        return True
    if not code_verifier:
        return False

    if code_challenge_method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return computed == code_challenge
    elif code_challenge_method == "plain":
        return code_verifier == code_challenge
    else:
        return False


@bp.route("/<tenant>/oauth2/v2.0/token", methods=["POST"])
def token(tenant):
    """Handle token requests."""
    conn = get_db(current_app)
    tenant_row = get_tenant(conn, tenant)

    if tenant_row is None:
        conn.close()
        return _token_error("invalid_request", "Tenant not found.")

    grant_type = request.form.get("grant_type")

    if grant_type == "authorization_code":
        return _handle_auth_code(conn, tenant_row)
    elif grant_type == "refresh_token":
        return _handle_refresh_token(conn, tenant_row)
    elif grant_type == "password":
        return _handle_ropc(conn, tenant_row, tenant)
    else:
        conn.close()
        return _token_error("unsupported_grant_type",
                            f"Unsupported grant_type: {grant_type}")


def _handle_auth_code(conn, tenant_row):
    """Handle authorization_code grant."""
    code = request.form.get("code")
    redirect_uri = request.form.get("redirect_uri")
    client_id = request.form.get("client_id")
    code_verifier = request.form.get("code_verifier")

    # Authenticate client
    client, err = _authenticate_client(conn, client_id)
    if err:
        conn.close()
        return err

    if not code:
        conn.close()
        return _token_error("invalid_request", "Missing required parameter: code.")

    # Look up authorization code
    auth_code = conn.execute(
        "SELECT * FROM auth_codes WHERE code = ?", (code,)
    ).fetchone()

    if auth_code is None:
        conn.close()
        return _token_error("invalid_grant", "Authorization code not found or already used.")

    # Delete the code (single-use)
    conn.execute("DELETE FROM auth_codes WHERE code = ?", (code,))
    conn.commit()

    # Validate code
    if auth_code["client_id"] != client["client_id"]:
        conn.close()
        return _token_error("invalid_grant", "Authorization code was issued to a different client.")

    if auth_code["expires_at"] < time.time():
        conn.close()
        return _token_error("invalid_grant", "The authorization code has expired.")

    if redirect_uri and auth_code["redirect_uri"] != redirect_uri:
        conn.close()
        return _token_error("invalid_grant", "redirect_uri mismatch.")

    # PKCE validation
    if not _verify_pkce(auth_code["code_challenge"], auth_code["code_challenge_method"], code_verifier):
        conn.close()
        return _token_error("invalid_grant", "PKCE verification failed.")

    # Get user
    user = get_user_by_id(conn, auth_code["user_id"])
    if user is None:
        conn.close()
        return _token_error("invalid_grant", "User not found.")

    tenant_id = auth_code["tenant_id"]
    scope = auth_code["scope"]
    nonce = auth_code["nonce"]
    scopes = scope.split() if scope else []

    lifetimes = get_effective_lifetimes(current_app, tenant_id, client["client_id"])

    # Generate tokens
    access_token = generate_access_token(
        current_app, user, client["client_id"], tenant_id, scope,
        application_id_uri=client["application_id_uri"],
    )

    response = {
        "token_type": "Bearer",
        "scope": scope,
        "expires_in": lifetimes["access_token_seconds"],
        "access_token": access_token,
    }

    # ID token if openid scope was requested
    if "openid" in scopes:
        id_token = generate_id_token(
            current_app, user, client["client_id"], tenant_id, scope,
            nonce=nonce,
        )
        response["id_token"] = id_token

    # Refresh token if offline_access scope was requested
    if "offline_access" in scopes:
        refresh_token = generate_refresh_token(
            current_app, user, client["client_id"], tenant_id, scope
        )
        response["refresh_token"] = refresh_token

    conn.close()
    return jsonify(response)


def _handle_refresh_token(conn, tenant_row):
    """Handle refresh_token grant."""
    refresh_token_value = request.form.get("refresh_token")
    client_id = request.form.get("client_id")
    new_scope = request.form.get("scope")

    # Authenticate client
    client, err = _authenticate_client(conn, client_id)
    if err:
        conn.close()
        return err

    if not refresh_token_value:
        conn.close()
        return _token_error("invalid_request", "Missing required parameter: refresh_token.")

    # Look up refresh token
    rt = conn.execute(
        "SELECT * FROM refresh_tokens WHERE token = ? AND revoked = 0",
        (refresh_token_value,),
    ).fetchone()

    if rt is None:
        conn.close()
        return _token_error("invalid_grant", "Refresh token not found or revoked.")

    if rt["expires_at"] < time.time():
        conn.close()
        return _token_error("invalid_grant", "Refresh token has expired.")

    if rt["client_id"] != client["client_id"]:
        conn.close()
        return _token_error("invalid_grant", "Refresh token was issued to a different client.")

    # Use original scope if new scope not provided
    scope = new_scope if new_scope else rt["scope"]

    # Validate new scope is subset of original
    if new_scope:
        original_scopes = set(rt["scope"].split())
        requested_scopes = set(new_scope.split())
        if not requested_scopes.issubset(original_scopes):
            conn.close()
            return _token_error("invalid_scope",
                                "Requested scope exceeds the original grant.")

    # Get user
    user = get_user_by_id(conn, rt["user_id"])
    if user is None:
        conn.close()
        return _token_error("invalid_grant", "User not found.")

    tenant_id = rt["tenant_id"]
    scopes = scope.split() if scope else []

    lifetimes = get_effective_lifetimes(current_app, tenant_id, client["client_id"])

    # Generate new tokens
    access_token = generate_access_token(
        current_app, user, client["client_id"], tenant_id, scope,
        application_id_uri=client["application_id_uri"],
    )

    response = {
        "token_type": "Bearer",
        "scope": scope,
        "expires_in": lifetimes["access_token_seconds"],
        "access_token": access_token,
    }

    if "openid" in scopes:
        id_token = generate_id_token(
            current_app, user, client["client_id"], tenant_id, scope
        )
        response["id_token"] = id_token

    # Token rotation: issue new refresh token
    new_refresh_token = generate_refresh_token(
        current_app, user, client["client_id"], tenant_id, scope
    )
    response["refresh_token"] = new_refresh_token

    conn.close()
    return jsonify(response)


def _handle_ropc(conn, tenant_row, tenant):
    """Handle password (ROPC) grant."""
    # ROPC not supported for common or consumers
    if tenant in ("common", "consumers"):
        conn.close()
        return _token_error("invalid_request",
                            "ROPC is not supported for this tenant type.")

    username = request.form.get("username")
    password = request.form.get("password")
    client_id = request.form.get("client_id")
    scope = request.form.get("scope", "openid")

    # Authenticate client
    client, err = _authenticate_client(conn, client_id)
    if err:
        conn.close()
        return err

    if not username or not password:
        conn.close()
        return _token_error("invalid_request",
                            "Missing required parameters: username and password.")

    # Look up user by UPN or email
    tenant_id = tenant_row["id"]
    user = get_user_by_upn(conn, username, tenant_id)
    if user is None:
        user = get_user_by_email(conn, username, tenant_id)

    if user is None or not check_password_hash(user["password_hash"], password):
        conn.close()
        return _token_error("invalid_grant", "Invalid username or password.")

    scopes = scope.split() if scope else []

    lifetimes = get_effective_lifetimes(current_app, tenant_id, client["client_id"])

    # Generate tokens
    access_token = generate_access_token(
        current_app, user, client["client_id"], tenant_id, scope,
        application_id_uri=client["application_id_uri"],
    )

    response = {
        "token_type": "Bearer",
        "scope": scope,
        "expires_in": lifetimes["access_token_seconds"],
        "access_token": access_token,
    }

    if "openid" in scopes:
        id_token = generate_id_token(
            current_app, user, client["client_id"], tenant_id, scope
        )
        response["id_token"] = id_token

    if "offline_access" in scopes:
        refresh_token = generate_refresh_token(
            current_app, user, client["client_id"], tenant_id, scope
        )
        response["refresh_token"] = refresh_token

    conn.close()
    return jsonify(response)
