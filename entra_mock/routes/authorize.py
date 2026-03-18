"""Authorization endpoint."""

import json
import secrets
import time
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from flask import (
    Blueprint, current_app, jsonify, redirect, render_template, request,
)
from werkzeug.security import check_password_hash

from entra_mock.db import (
    get_db, get_effective_lifetimes, get_tenant, get_client,
    get_user_by_upn, get_user_by_email,
)
from entra_mock.tokens import generate_id_token

bp = Blueprint("authorize", __name__)

VALID_RESPONSE_TYPES = {"code", "id_token", "code id_token", "id_token token"}


def _error_redirect(redirect_uri, response_mode, error, error_description, state=None):
    """Build an error redirect response."""
    params = {"error": error, "error_description": error_description}
    if state:
        params["state"] = state
    return _build_redirect(redirect_uri, response_mode, params)


def _build_redirect(redirect_uri, response_mode, params):
    """Build a redirect response based on response_mode."""
    if response_mode == "fragment":
        parsed = urlparse(redirect_uri)
        fragment = urlencode(params)
        url = urlunparse(parsed._replace(fragment=fragment))
        return redirect(url)
    elif response_mode == "form_post":
        return render_template(
            "form_post.html",
            redirect_uri=redirect_uri,
            params=params,
        )
    else:
        # Default: query
        parsed = urlparse(redirect_uri)
        existing = parse_qs(parsed.query)
        for k, v in params.items():
            existing[k] = [v]
        new_query = urlencode({k: v[0] for k, v in existing.items()})
        url = urlunparse(parsed._replace(query=new_query))
        return redirect(url)


@bp.route("/<tenant>/oauth2/v2.0/authorize", methods=["GET", "POST"])
def authorize(tenant):
    """Handle authorization requests."""
    conn = get_db(current_app)
    tenant_row = get_tenant(conn, tenant)

    if tenant_row is None:
        conn.close()
        return render_template("error.html", error="Tenant not found",
                               description=f"The tenant '{tenant}' was not found."), 400

    # GET shows login form or validates params; POST processes login
    if request.method == "POST":
        return _handle_login(conn, tenant_row, tenant)

    return _handle_authorize_get(conn, tenant_row, tenant)


def _handle_authorize_get(conn, tenant_row, tenant):
    """Validate authorization parameters and show login page."""
    client_id = request.args.get("client_id")
    response_type = request.args.get("response_type")
    redirect_uri = request.args.get("redirect_uri")
    scope = request.args.get("scope", "")
    state = request.args.get("state")
    nonce = request.args.get("nonce")
    response_mode = request.args.get("response_mode")
    prompt = request.args.get("prompt")
    login_hint = request.args.get("login_hint")
    code_challenge = request.args.get("code_challenge")
    code_challenge_method = request.args.get("code_challenge_method")

    # Validate client_id
    if not client_id:
        conn.close()
        return render_template("error.html", error="invalid_request",
                               description="Missing required parameter: client_id"), 400

    client = get_client(conn, client_id)
    if client is None:
        conn.close()
        return render_template("error.html", error="unauthorized_client",
                               description="Client not registered."), 400

    # Validate redirect_uri
    if not redirect_uri:
        conn.close()
        return render_template("error.html", error="invalid_request",
                               description="Missing required parameter: redirect_uri"), 400

    registered_uris = json.loads(client["redirect_uris"])
    if redirect_uri not in registered_uris:
        conn.close()
        return render_template("error.html", error="invalid_request",
                               description="redirect_uri does not match any registered URI."), 400

    # Set default response_mode based on response_type
    if not response_mode:
        if response_type and "token" in response_type and "code" not in response_type:
            response_mode = "fragment"
        else:
            response_mode = "query"

    # Validate response_type
    if not response_type:
        conn.close()
        return _error_redirect(redirect_uri, response_mode, "invalid_request",
                               "Missing required parameter: response_type", state)

    if response_type not in VALID_RESPONSE_TYPES:
        conn.close()
        return _error_redirect(redirect_uri, response_mode, "unsupported_response_type",
                               f"Unsupported response_type: {response_type}", state)

    # Nonce is required for id_token response types
    if "id_token" in response_type and not nonce:
        conn.close()
        return _error_redirect(redirect_uri, response_mode, "invalid_request",
                               "nonce is required when response_type includes id_token", state)

    # Handle prompt=none (check for existing session)
    if prompt == "none":
        session_id = request.cookies.get("entra_mock_session")
        if session_id:
            session = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ? AND expires_at > ?",
                (session_id, time.time()),
            ).fetchone()
            if session:
                # Existing session found - issue code directly
                return _issue_auth_response(
                    conn, session["user_id"], tenant_row, client,
                    redirect_uri, response_type, response_mode, scope,
                    state, nonce, code_challenge, code_challenge_method,
                )
        conn.close()
        return _error_redirect(redirect_uri, response_mode, "login_required",
                               "No active session found.", state)

    conn.close()

    # Render login page
    return render_template(
        "login.html",
        tenant=tenant,
        client_name=client["display_name"],
        client_id=client_id,
        response_type=response_type,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state or "",
        nonce=nonce or "",
        response_mode=response_mode,
        login_hint=login_hint or "",
        code_challenge=code_challenge or "",
        code_challenge_method=code_challenge_method or "",
    )


def _handle_login(conn, tenant_row, tenant):
    """Process login form submission."""
    # Get form values
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    client_id = request.form.get("client_id")
    response_type = request.form.get("response_type")
    redirect_uri = request.form.get("redirect_uri")
    scope = request.form.get("scope", "")
    state = request.form.get("state")
    nonce = request.form.get("nonce")
    response_mode = request.form.get("response_mode", "query")
    code_challenge = request.form.get("code_challenge")
    code_challenge_method = request.form.get("code_challenge_method")

    # Re-validate client and redirect_uri
    client = get_client(conn, client_id)
    if client is None:
        conn.close()
        return render_template("error.html", error="unauthorized_client",
                               description="Client not registered."), 400

    registered_uris = json.loads(client["redirect_uris"])
    if redirect_uri not in registered_uris:
        conn.close()
        return render_template("error.html", error="invalid_request",
                               description="redirect_uri does not match any registered URI."), 400

    # Authenticate user
    tenant_id = tenant_row["id"]
    user = get_user_by_upn(conn, username, tenant_id)
    if user is None:
        user = get_user_by_email(conn, username, tenant_id)

    if user is None or not check_password_hash(user["password_hash"], password):
        conn.close()
        return render_template(
            "login.html",
            tenant=tenant,
            client_name=client["display_name"],
            client_id=client_id,
            response_type=response_type,
            redirect_uri=redirect_uri,
            scope=scope,
            state=state or "",
            nonce=nonce or "",
            response_mode=response_mode,
            login_hint=username,
            code_challenge=code_challenge or "",
            code_challenge_method=code_challenge_method or "",
            error_message="Invalid username or password.",
        )

    # Create session
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        """INSERT INTO sessions (session_id, user_id, tenant_id, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, user["id"], tenant_id, now, now + 86400),
    )
    conn.commit()

    # Issue authorization response
    response = _issue_auth_response(
        conn, user["id"], tenant_row, client,
        redirect_uri, response_type, response_mode, scope,
        state, nonce, code_challenge, code_challenge_method,
    )

    # Set session cookie
    if isinstance(response, str):
        # form_post returns HTML string
        from flask import make_response as mk
        resp = mk(response)
    else:
        resp = response

    config = current_app.config["ENTRA_CONFIG"]
    secure = config["server"]["scheme"] == "https"
    resp.set_cookie(
        "entra_mock_session",
        session_id,
        httponly=True,
        samesite="Lax",
        secure=secure,
        max_age=86400,
    )

    return resp


def _issue_auth_response(conn, user_id, tenant_row, client,
                         redirect_uri, response_type, response_mode, scope,
                         state, nonce, code_challenge, code_challenge_method):
    """Generate authorization code and/or tokens, return redirect."""
    tenant_id = tenant_row["id"]
    client_id = client["client_id"]
    lifetimes = get_effective_lifetimes(current_app, tenant_id, client_id)
    now = time.time()

    params = {}
    if state:
        params["state"] = state

    # Generate authorization code if needed
    code = None
    if "code" in response_type:
        code = secrets.token_urlsafe(32)
        conn.execute(
            """INSERT INTO auth_codes (code, client_id, user_id, tenant_id,
                                       redirect_uri, scope, nonce,
                                       code_challenge, code_challenge_method,
                                       created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code, client_id, user_id, tenant_id,
                redirect_uri, scope, nonce,
                code_challenge or None, code_challenge_method or None,
                now, now + lifetimes["auth_code_seconds"],
            ),
        )
        conn.commit()
        params["code"] = code

    # Generate ID token for hybrid flow (response_type includes id_token)
    if "id_token" in response_type:
        from entra_mock.db import get_user_by_id
        user = get_user_by_id(conn, user_id)
        id_token = generate_id_token(
            current_app, user, client_id, tenant_id, scope,
            nonce=nonce, code=code,
        )
        params["id_token"] = id_token

    conn.close()

    return _build_redirect(redirect_uri, response_mode, params)
