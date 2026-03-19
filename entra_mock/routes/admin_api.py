"""Admin API and UI for managing tenants, users, groups, and clients."""

import json
import secrets
import uuid

from flask import Blueprint, Response, current_app, jsonify, render_template, request
from werkzeug.security import generate_password_hash

from entra_mock.db import get_db

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ── Admin Auth ────────────────────────────────────────────────────────────────

def _check_admin_auth():
    """Return 401 if an admin_password is configured and the request lacks
    valid HTTP Basic Auth credentials (any username, matching password)."""
    cfg = current_app.config.get("ENTRA_CONFIG", {})
    admin_pw = cfg.get("server", {}).get("admin_password")
    if not admin_pw:
        return  # no password configured – allow all

    auth = request.authorization
    if auth and auth.password == admin_pw:
        return  # credentials match

    return Response(
        "Authentication required.\n",
        401,
        {"WWW-Authenticate": 'Basic realm="Entra ID Admin"'},
    )


@bp.before_request
def require_admin_auth():
    return _check_admin_auth()


# ── Admin UI ──────────────────────────────────────────────────────────────────

@bp.route("/")
def admin_ui():
    return render_template("admin.html")


# ── Tenants ───────────────────────────────────────────────────────────────────

@bp.route("/api/tenants", methods=["GET"])
def list_tenants():
    conn = get_db(current_app)
    rows = conn.execute("SELECT * FROM tenants").fetchall()
    conn.close()
    result = []
    for r in rows:
        t = dict(r)
        t["token_lifetimes"] = json.loads(t["token_lifetimes"]) if t.get("token_lifetimes") else None
        result.append(t)
    return jsonify(result)


@bp.route("/api/tenants", methods=["POST"])
def create_tenant():
    data = request.get_json()
    if not data or not data.get("domain") or not data.get("display_name"):
        return jsonify({"error": "domain and display_name are required"}), 400

    tenant_id = data.get("id", str(uuid.uuid4()))
    salt = secrets.token_hex(32)
    token_lifetimes = json.dumps(data["token_lifetimes"]) if data.get("token_lifetimes") else None

    conn = get_db(current_app)
    try:
        conn.execute(
            "INSERT INTO tenants (id, domain, display_name, salt, token_lifetimes) VALUES (?, ?, ?, ?, ?)",
            (tenant_id, data["domain"], data["display_name"], salt, token_lifetimes),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 409
    conn.close()
    return jsonify({"id": tenant_id, "domain": data["domain"],
                    "display_name": data["display_name"],
                    "token_lifetimes": data.get("token_lifetimes")}), 201


@bp.route("/api/tenants/<tenant_id>", methods=["PUT"])
def update_tenant(tenant_id):
    data = request.get_json()
    token_lifetimes = json.dumps(data["token_lifetimes"]) if data.get("token_lifetimes") else None
    conn = get_db(current_app)
    conn.execute(
        "UPDATE tenants SET domain = ?, display_name = ?, token_lifetimes = ? WHERE id = ?",
        (data.get("domain"), data.get("display_name"), token_lifetimes, tenant_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    result = dict(row)
    result["token_lifetimes"] = json.loads(result["token_lifetimes"]) if result.get("token_lifetimes") else None
    return jsonify(result)


@bp.route("/api/tenants/<tenant_id>", methods=["DELETE"])
def delete_tenant(tenant_id):
    conn = get_db(current_app)
    conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
    conn.commit()
    conn.close()
    return "", 204


# ── Users ─────────────────────────────────────────────────────────────────────

@bp.route("/api/users", methods=["GET"])
def list_users():
    conn = get_db(current_app)
    rows = conn.execute(
        """SELECT u.*, t.display_name as tenant_name
           FROM users u LEFT JOIN tenants t ON u.tenant_id = t.id"""
    ).fetchall()
    users = []
    for r in rows:
        u = dict(r)
        groups = conn.execute(
            """SELECT g.id as group_id, g.name as group_name
               FROM user_groups ug JOIN groups g ON ug.group_id = g.id
               WHERE ug.user_id = ?""",
            (u["id"],),
        ).fetchall()
        u["groups"] = [{"id": g["group_id"], "name": g["group_name"]} for g in groups]
        u.pop("password_hash", None)
        users.append(u)
    conn.close()
    return jsonify(users)


@bp.route("/api/users", methods=["POST"])
def create_user():
    data = request.get_json()
    required = ["tenant_id", "upn", "email", "display_name", "password"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    user_id = data.get("id", str(uuid.uuid4()))
    password_hash = generate_password_hash(data["password"])

    conn = get_db(current_app)
    try:
        conn.execute(
            """INSERT INTO users (id, tenant_id, upn, email, display_name,
                                  given_name, family_name, password_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, data["tenant_id"], data["upn"], data["email"],
             data["display_name"], data.get("given_name", ""),
             data.get("family_name", ""), password_hash),
        )
        for group_id in data.get("group_ids", []):
            conn.execute(
                "INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)",
                (user_id, group_id),
            )
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 409
    conn.close()
    return jsonify({"id": user_id, "upn": data["upn"],
                    "display_name": data["display_name"]}), 201


@bp.route("/api/users/<user_id>", methods=["PUT"])
def update_user(user_id):
    data = request.get_json()
    conn = get_db(current_app)

    # Build update fields (skip password if not provided)
    fields = {
        "tenant_id": data.get("tenant_id"),
        "upn": data.get("upn"),
        "email": data.get("email"),
        "display_name": data.get("display_name"),
        "given_name": data.get("given_name", ""),
        "family_name": data.get("family_name", ""),
    }
    if data.get("password"):
        fields["password_hash"] = generate_password_hash(data["password"])

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)

    # Update group memberships: delete and re-insert
    if "group_ids" in data:
        conn.execute("DELETE FROM user_groups WHERE user_id = ?", (user_id,))
        for group_id in data.get("group_ids", []):
            conn.execute(
                "INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)",
                (user_id, group_id),
            )

    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    result = dict(row)
    result.pop("password_hash", None)
    return jsonify(result)


@bp.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    conn = get_db(current_app)
    conn.execute("DELETE FROM user_groups WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return "", 204


# ── Groups ────────────────────────────────────────────────────────────────────

@bp.route("/api/groups", methods=["GET"])
def list_groups():
    """List all groups with tenant info and members."""
    conn = get_db(current_app)
    rows = conn.execute(
        """SELECT g.*, t.display_name as tenant_name
           FROM groups g LEFT JOIN tenants t ON g.tenant_id = t.id"""
    ).fetchall()
    groups = []
    for r in rows:
        g = dict(r)
        members = conn.execute(
            "SELECT user_id FROM user_groups WHERE group_id = ?", (g["id"],)
        ).fetchall()
        g["member_ids"] = [m["user_id"] for m in members]
        groups.append(g)
    conn.close()
    return jsonify(groups)


@bp.route("/api/groups", methods=["POST"])
def create_group():
    """Create a group in a tenant and optionally assign members."""
    data = request.get_json()
    if not data or not data.get("name") or not data.get("tenant_id"):
        return jsonify({"error": "name and tenant_id are required"}), 400

    group_id = data.get("id", str(uuid.uuid4()))
    member_ids = data.get("member_ids", [])

    conn = get_db(current_app)
    try:
        conn.execute(
            "INSERT INTO groups (id, tenant_id, name) VALUES (?, ?, ?)",
            (group_id, data["tenant_id"], data["name"]),
        )
        for uid in member_ids:
            conn.execute(
                "INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)",
                (uid, group_id),
            )
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 409
    conn.close()
    return jsonify({"id": group_id, "name": data["name"],
                    "tenant_id": data["tenant_id"],
                    "member_ids": member_ids}), 201


@bp.route("/api/groups/<group_id>", methods=["PUT"])
def update_group(group_id):
    """Update group name, tenant, and membership."""
    data = request.get_json()
    conn = get_db(current_app)

    updates = {}
    if data.get("name"):
        updates["name"] = data["name"]
    if data.get("tenant_id"):
        updates["tenant_id"] = data["tenant_id"]

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [group_id]
        conn.execute(f"UPDATE groups SET {set_clause} WHERE id = ?", values)

    if "member_ids" in data:
        conn.execute("DELETE FROM user_groups WHERE group_id = ?", (group_id,))
        for uid in data["member_ids"]:
            conn.execute(
                "INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)",
                (uid, group_id),
            )

    conn.commit()
    row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    result = dict(row)
    result["member_ids"] = data.get("member_ids", [])
    return jsonify(result)


@bp.route("/api/groups/<group_id>", methods=["DELETE"])
def delete_group(group_id):
    conn = get_db(current_app)
    conn.execute("DELETE FROM user_groups WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()
    return "", 204


# ── Clients ───────────────────────────────────────────────────────────────────

@bp.route("/api/clients", methods=["GET"])
def list_clients():
    conn = get_db(current_app)
    rows = conn.execute(
        """SELECT c.*, t.display_name as tenant_name
           FROM clients c LEFT JOIN tenants t ON c.tenant_id = t.id"""
    ).fetchall()
    conn.close()
    clients = []
    for r in rows:
        c = dict(r)
        c["redirect_uris"] = json.loads(c.get("redirect_uris") or "[]")
        c["allowed_scopes"] = json.loads(c.get("allowed_scopes") or "[]")
        c["token_lifetimes"] = json.loads(c["token_lifetimes"]) if c.get("token_lifetimes") else None
        c.pop("client_secret", None)
        clients.append(c)
    return jsonify(clients)


@bp.route("/api/clients", methods=["POST"])
def create_client():
    data = request.get_json()
    required = ["tenant_id", "display_name"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    client_id = data.get("client_id", str(uuid.uuid4()))
    client_secret = data.get("client_secret")
    secret_hash = generate_password_hash(client_secret) if client_secret else None

    client_token_lifetimes = json.dumps(data["token_lifetimes"]) if data.get("token_lifetimes") else None

    conn = get_db(current_app)
    try:
        conn.execute(
            """INSERT INTO clients (client_id, tenant_id, display_name, client_secret,
                                    client_type, redirect_uris, front_channel_logout_uri,
                                    allowed_scopes, application_id_uri, token_lifetimes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client_id, data["tenant_id"], data["display_name"], secret_hash,
             data.get("client_type", "confidential"),
             json.dumps(data.get("redirect_uris", [])),
             data.get("front_channel_logout_uri"),
             json.dumps(data.get("allowed_scopes", [])),
             data.get("application_id_uri"),
             client_token_lifetimes),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 409
    conn.close()
    return jsonify({"client_id": client_id, "display_name": data["display_name"],
                    "token_lifetimes": data.get("token_lifetimes")}), 201


@bp.route("/api/clients/<client_id>", methods=["PUT"])
def update_client(client_id):
    data = request.get_json()
    conn = get_db(current_app)

    updates = {
        "tenant_id": data.get("tenant_id"),
        "display_name": data.get("display_name"),
        "client_type": data.get("client_type", "confidential"),
        "redirect_uris": json.dumps(data.get("redirect_uris", [])),
        "front_channel_logout_uri": data.get("front_channel_logout_uri"),
        "allowed_scopes": json.dumps(data.get("allowed_scopes", [])),
        "application_id_uri": data.get("application_id_uri"),
        "token_lifetimes": json.dumps(data["token_lifetimes"]) if data.get("token_lifetimes") else None,
    }
    if data.get("client_secret"):
        updates["client_secret"] = generate_password_hash(data["client_secret"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [client_id]
    conn.execute(f"UPDATE clients SET {set_clause} WHERE client_id = ?", values)
    conn.commit()

    row = conn.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    result = dict(row)
    result["redirect_uris"] = json.loads(result.get("redirect_uris") or "[]")
    result["allowed_scopes"] = json.loads(result.get("allowed_scopes") or "[]")
    result["token_lifetimes"] = json.loads(result["token_lifetimes"]) if result.get("token_lifetimes") else None
    result.pop("client_secret", None)
    return jsonify(result)


@bp.route("/api/clients/<client_id>", methods=["DELETE"])
def delete_client(client_id):
    conn = get_db(current_app)
    conn.execute("DELETE FROM clients WHERE client_id = ?", (client_id,))
    conn.commit()
    conn.close()
    return "", 204
