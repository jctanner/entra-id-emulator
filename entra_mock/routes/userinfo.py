"""UserInfo endpoint."""

import jwt as pyjwt
from flask import Blueprint, current_app, jsonify, request

from entra_mock.db import get_db, get_user_by_id

bp = Blueprint("userinfo", __name__)


@bp.route("/oidc/userinfo", methods=["GET", "POST"])
def userinfo():
    """Return user claims based on the Bearer access token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "invalid_token", "error_description": "Missing or invalid Authorization header."}), 401

    token = auth_header[7:]

    # Decode the access token (without signature verification since we issued it)
    try:
        claims = pyjwt.decode(token, options={"verify_signature": False})
    except pyjwt.DecodeError:
        return jsonify({"error": "invalid_token", "error_description": "Token is not a valid JWT."}), 401

    # Check expiration
    import time
    if claims.get("exp", 0) < time.time():
        return jsonify({"error": "invalid_token", "error_description": "Token has expired."}), 401

    # Get user from the oid claim
    user_id = claims.get("oid")
    if not user_id:
        return jsonify({"error": "invalid_token", "error_description": "Token missing oid claim."}), 401

    conn = get_db(current_app)
    user = get_user_by_id(conn, user_id)
    conn.close()

    if user is None:
        return jsonify({"error": "invalid_token", "error_description": "User not found."}), 401

    # Build response based on scopes in the token
    scp = claims.get("scp", "")
    scopes = scp.split() if scp else []

    config = current_app.config["ENTRA_CONFIG"]
    scheme = config["server"]["scheme"]
    host = config["server"]["external_hostname"]

    response = {
        "sub": claims.get("sub"),
    }

    if "profile" in scopes:
        response["name"] = user["display_name"]
        response["family_name"] = user["family_name"]
        response["given_name"] = user["given_name"]
        response["picture"] = f"{scheme}://{host}/v1.0/me/photo/$value"

    if "email" in scopes:
        response["email"] = user["email"]

    return jsonify(response)
