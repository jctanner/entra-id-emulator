"""Logout endpoint."""

import json
import time

from flask import (
    Blueprint, current_app, jsonify, make_response, redirect,
    render_template, request,
)

from entra_mock.db import get_db, get_tenant, get_client

bp = Blueprint("logout", __name__)


@bp.route("/<tenant>/oauth2/v2.0/logout", methods=["GET", "POST"])
def logout(tenant):
    """Handle logout requests."""
    conn = get_db(current_app)
    tenant_row = get_tenant(conn, tenant)

    if tenant_row is None:
        conn.close()
        return render_template("error.html", error="Tenant not found",
                               description=f"The tenant '{tenant}' was not found."), 400

    post_logout_redirect_uri = (
        request.args.get("post_logout_redirect_uri")
        or request.form.get("post_logout_redirect_uri")
    )

    # Clear session
    session_id = request.cookies.get("entra_mock_session")
    front_channel_uris = []

    if session_id:
        # Get session info before deleting
        session = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

        if session:
            # Collect front-channel logout URIs from other clients in the tenant
            clients = conn.execute(
                "SELECT front_channel_logout_uri FROM clients WHERE tenant_id = ? AND front_channel_logout_uri IS NOT NULL",
                (session["tenant_id"],),
            ).fetchall()
            front_channel_uris = [c["front_channel_logout_uri"] for c in clients]

        # Delete the session
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()

    conn.close()

    # Validate post_logout_redirect_uri if provided
    if post_logout_redirect_uri:
        resp = redirect(post_logout_redirect_uri)
    else:
        resp = make_response(render_template(
            "logout.html",
            front_channel_uris=front_channel_uris,
        ))

    # Delete session cookie
    resp.delete_cookie("entra_mock_session")

    return resp
