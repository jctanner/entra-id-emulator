"""OpenID Connect discovery endpoint."""

from flask import Blueprint, current_app, jsonify, request

from entra_mock.db import get_db, get_tenant

bp = Blueprint("discovery", __name__)


@bp.route("/<tenant>/v2.0/.well-known/openid-configuration")
def openid_configuration(tenant):
    """Return the OpenID Connect discovery document."""
    conn = get_db(current_app)
    tenant_row = get_tenant(conn, tenant)
    conn.close()

    if tenant_row is None:
        return jsonify({"error": "tenant_not_found", "error_description": "Tenant not found."}), 400

    config = current_app.config["ENTRA_CONFIG"]
    server = config["server"]
    scheme = server["scheme"]
    host = server["external_hostname"]
    tenant_id = tenant_row["id"]

    base = f"{scheme}://{host}/{tenant_id}"

    doc = {
        "issuer": f"{base}/v2.0",
        "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
        "token_endpoint": f"{base}/oauth2/v2.0/token",
        "userinfo_endpoint": f"{scheme}://{host}/oidc/userinfo",
        "jwks_uri": f"{scheme}://{host}/{tenant_id}/discovery/v2.0/keys",
        "end_session_endpoint": f"{base}/oauth2/v2.0/logout",
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
            "private_key_jwt",
        ],
        "response_types_supported": [
            "code",
            "id_token",
            "code id_token",
            "id_token token",
        ],
        "response_modes_supported": ["query", "fragment", "form_post"],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
        "subject_types_supported": ["pairwise"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "claims_supported": [
            "sub", "iss", "aud", "exp", "iat", "nbf", "nonce",
            "auth_time", "name", "preferred_username", "email",
            "tid", "oid", "ver", "at_hash", "c_hash",
        ],
        "request_uri_parameter_supported": False,
        "frontchannel_logout_supported": True,
        "http_logout_supported": True,
    }

    return jsonify(doc)
