"""SQLite schema creation, seeding from config, and helper queries."""

import json
import os
import secrets
import sqlite3

from werkzeug.security import generate_password_hash


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    domain TEXT UNIQUE,
    display_name TEXT,
    salt TEXT,
    token_lifetimes TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT REFERENCES tenants(id),
    upn TEXT,
    email TEXT,
    display_name TEXT,
    given_name TEXT,
    family_name TEXT,
    password_hash TEXT
);

CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    tenant_id TEXT REFERENCES tenants(id),
    name TEXT
);

CREATE TABLE IF NOT EXISTS user_groups (
    user_id TEXT REFERENCES users(id),
    group_id TEXT REFERENCES groups(id),
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    tenant_id TEXT REFERENCES tenants(id),
    display_name TEXT,
    client_secret TEXT,
    client_type TEXT DEFAULT 'confidential',
    redirect_uris TEXT DEFAULT '[]',
    front_channel_logout_uri TEXT,
    allowed_scopes TEXT DEFAULT '[]',
    application_id_uri TEXT,
    token_lifetimes TEXT
);

CREATE TABLE IF NOT EXISTS auth_codes (
    code TEXT PRIMARY KEY,
    client_id TEXT REFERENCES clients(client_id),
    user_id TEXT REFERENCES users(id),
    tenant_id TEXT REFERENCES tenants(id),
    redirect_uri TEXT,
    scope TEXT,
    nonce TEXT,
    code_challenge TEXT,
    code_challenge_method TEXT,
    created_at REAL,
    expires_at REAL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token TEXT PRIMARY KEY,
    client_id TEXT REFERENCES clients(client_id),
    user_id TEXT REFERENCES users(id),
    tenant_id TEXT REFERENCES tenants(id),
    scope TEXT,
    created_at REAL,
    expires_at REAL,
    revoked INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    tenant_id TEXT REFERENCES tenants(id),
    created_at REAL,
    expires_at REAL
);

CREATE TABLE IF NOT EXISTS signing_keys (
    kid TEXT PRIMARY KEY,
    private_key_pem TEXT,
    public_key_pem TEXT,
    created_at REAL,
    active INTEGER DEFAULT 1
);
"""


def get_db(app=None):
    """Get a database connection. Uses app config for path if available."""
    db_path = ":memory:"
    if app:
        db_path = app.config.get("DATABASE", "data/entra_mock.db")
    elif os.environ.get("ENTRA_MOCK_DB"):
        db_path = os.environ["ENTRA_MOCK_DB"]

    # Ensure directory exists for file-based databases
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(app=None):
    """Create tables if they don't exist, and migrate old schema."""
    conn = get_db(app)

    # Check if old user_groups schema exists (has group_name column)
    old_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_groups)").fetchall()}
    if "group_name" in old_cols:
        # Migrate: create groups table from old user_groups data, then rebuild user_groups
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS groups (
                id TEXT PRIMARY KEY,
                tenant_id TEXT REFERENCES tenants(id),
                name TEXT
            );
            INSERT OR IGNORE INTO groups (id, tenant_id, name)
                SELECT ug.group_id, u.tenant_id, ug.group_name
                FROM user_groups ug JOIN users u ON ug.user_id = u.id;
            CREATE TABLE user_groups_new (
                user_id TEXT REFERENCES users(id),
                group_id TEXT REFERENCES groups(id),
                PRIMARY KEY (user_id, group_id)
            );
            INSERT OR IGNORE INTO user_groups_new (user_id, group_id)
                SELECT user_id, group_id FROM user_groups;
            DROP TABLE user_groups;
            ALTER TABLE user_groups_new RENAME TO user_groups;
        """)
        conn.commit()

    conn.executescript(SCHEMA_SQL)
    conn.commit()

    # Migrate: add token_lifetimes column to tenants and clients if missing
    tenant_cols = {r[1] for r in conn.execute("PRAGMA table_info(tenants)").fetchall()}
    if "token_lifetimes" not in tenant_cols:
        conn.execute("ALTER TABLE tenants ADD COLUMN token_lifetimes TEXT")
        conn.commit()

    client_cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)").fetchall()}
    if "token_lifetimes" not in client_cols:
        conn.execute("ALTER TABLE clients ADD COLUMN token_lifetimes TEXT")
        conn.commit()

    conn.close()


def seed_db(config, app=None):
    """Seed database from config. Updates existing records, inserts new ones."""
    conn = get_db(app)

    # Seed tenants
    for tenant in config.get("tenants", []):
        # Generate a salt for pairwise subject identifiers
        existing = conn.execute(
            "SELECT salt FROM tenants WHERE id = ?", (tenant["id"],)
        ).fetchone()
        salt = existing["salt"] if existing else secrets.token_hex(32)

        token_lifetimes = json.dumps(tenant["token_lifetimes"]) if tenant.get("token_lifetimes") else None
        conn.execute(
            """INSERT INTO tenants (id, domain, display_name, salt, token_lifetimes)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 domain = excluded.domain,
                 display_name = excluded.display_name,
                 token_lifetimes = excluded.token_lifetimes""",
            (tenant["id"], tenant["domain"], tenant["display_name"], salt, token_lifetimes),
        )

    # Seed users
    for user in config.get("users", []):
        password_hash = generate_password_hash(user["password"])
        conn.execute(
            """INSERT INTO users (id, tenant_id, upn, email, display_name,
                                  given_name, family_name, password_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 tenant_id = excluded.tenant_id,
                 upn = excluded.upn,
                 email = excluded.email,
                 display_name = excluded.display_name,
                 given_name = excluded.given_name,
                 family_name = excluded.family_name,
                 password_hash = excluded.password_hash""",
            (
                user["id"],
                user["tenant_id"],
                user["upn"],
                user["email"],
                user["display_name"],
                user["given_name"],
                user["family_name"],
                password_hash,
            ),
        )

        # Seed user groups
        for group in user.get("groups", []):
            conn.execute(
                """INSERT INTO groups (id, tenant_id, name)
                   VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name = excluded.name""",
                (group["id"], user["tenant_id"], group["name"]),
            )
            conn.execute(
                """INSERT INTO user_groups (user_id, group_id)
                   VALUES (?, ?)
                   ON CONFLICT(user_id, group_id) DO NOTHING""",
                (user["id"], group["id"]),
            )

    # Seed clients
    for client in config.get("clients", []):
        client_secret = client.get("client_secret")
        # Store client_secret as a hash for confidential clients
        secret_hash = (
            generate_password_hash(client_secret) if client_secret else None
        )

        client_token_lifetimes = json.dumps(client["token_lifetimes"]) if client.get("token_lifetimes") else None
        conn.execute(
            """INSERT INTO clients (client_id, tenant_id, display_name,
                                    client_secret, client_type, redirect_uris,
                                    front_channel_logout_uri, allowed_scopes,
                                    application_id_uri, token_lifetimes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(client_id) DO UPDATE SET
                 tenant_id = excluded.tenant_id,
                 display_name = excluded.display_name,
                 client_secret = excluded.client_secret,
                 client_type = excluded.client_type,
                 redirect_uris = excluded.redirect_uris,
                 front_channel_logout_uri = excluded.front_channel_logout_uri,
                 allowed_scopes = excluded.allowed_scopes,
                 application_id_uri = excluded.application_id_uri,
                 token_lifetimes = excluded.token_lifetimes""",
            (
                client["client_id"],
                client["tenant_id"],
                client["display_name"],
                secret_hash,
                client.get("client_type", "confidential"),
                json.dumps(client.get("redirect_uris", [])),
                client.get("front_channel_logout_uri"),
                json.dumps(client.get("allowed_scopes", [])),
                client.get("application_id_uri"),
                client_token_lifetimes,
            ),
        )

    conn.commit()
    conn.close()


# --- Helper queries ---


def get_tenant(conn, tenant_identifier):
    """Resolve a tenant by GUID or domain.

    Returns tenant row or None.
    Handles special aliases: common, organizations, consumers.
    """
    if tenant_identifier in ("common", "organizations"):
        # Return the first tenant (for single-tenant dev setups)
        return conn.execute("SELECT * FROM tenants LIMIT 1").fetchone()
    if tenant_identifier == "consumers":
        return None  # Not supported

    # Try GUID first
    row = conn.execute(
        "SELECT * FROM tenants WHERE id = ?", (tenant_identifier,)
    ).fetchone()
    if row:
        return row

    # Try domain
    return conn.execute(
        "SELECT * FROM tenants WHERE domain = ?", (tenant_identifier,)
    ).fetchone()


def get_client(conn, client_id):
    """Get a client by client_id."""
    return conn.execute(
        "SELECT * FROM clients WHERE client_id = ?", (client_id,)
    ).fetchone()


def get_user_by_upn(conn, upn, tenant_id=None):
    """Get a user by UPN, optionally filtered by tenant."""
    if tenant_id:
        return conn.execute(
            "SELECT * FROM users WHERE upn = ? AND tenant_id = ?",
            (upn, tenant_id),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM users WHERE upn = ?", (upn,)
    ).fetchone()


def get_user_by_email(conn, email, tenant_id=None):
    """Get a user by email, optionally filtered by tenant."""
    if tenant_id:
        return conn.execute(
            "SELECT * FROM users WHERE email = ? AND tenant_id = ?",
            (email, tenant_id),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()


def get_user_by_id(conn, user_id):
    """Get a user by ID."""
    return conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def get_user_groups(conn, user_id):
    """Get all groups for a user."""
    return conn.execute(
        """SELECT g.id as group_id, g.name as group_name
           FROM user_groups ug JOIN groups g ON ug.group_id = g.id
           WHERE ug.user_id = ?""",
        (user_id,),
    ).fetchall()


def get_effective_lifetimes(app, tenant_id, client_id):
    """Merge token lifetimes: global defaults -> tenant overrides -> client overrides."""
    config = app.config["ENTRA_CONFIG"]
    lifetimes = dict(config["token_lifetimes"])

    conn = get_db(app)
    tenant = conn.execute(
        "SELECT token_lifetimes FROM tenants WHERE id = ?", (tenant_id,)
    ).fetchone()
    if tenant and tenant["token_lifetimes"]:
        lifetimes.update(json.loads(tenant["token_lifetimes"]))

    client = conn.execute(
        "SELECT token_lifetimes FROM clients WHERE client_id = ?", (client_id,)
    ).fetchone()
    if client and client["token_lifetimes"]:
        lifetimes.update(json.loads(client["token_lifetimes"]))

    conn.close()
    return lifetimes


def get_tenant_salt(conn, tenant_id):
    """Get the pairwise subject salt for a tenant."""
    row = conn.execute(
        "SELECT salt FROM tenants WHERE id = ?", (tenant_id,)
    ).fetchone()
    return row["salt"] if row else None
