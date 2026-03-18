"""JWKS (JSON Web Key Set) endpoint."""

from flask import Blueprint, current_app, jsonify

from entra_mock.db import get_db, get_tenant
from entra_mock.keys import build_jwks

bp = Blueprint("jwks", __name__)


@bp.route("/<tenant>/discovery/v2.0/keys")
def jwks_keys(tenant):
    """Return the JSON Web Key Set for token verification."""
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

    issuer = f"{scheme}://{host}/{tenant_id}/v2.0"
    jwks = build_jwks(current_app, issuer=issuer)

    return jsonify(jwks)
