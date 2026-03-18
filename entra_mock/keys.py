"""RSA key generation, storage, JWKS builder, and JWT signing."""

import base64
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwcrypto import jwk

from entra_mock.db import get_db


def _generate_rsa_keypair():
    """Generate a 2048-bit RSA key pair, return (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return private_pem, public_pem


def ensure_signing_key(app):
    """Ensure at least one active signing key exists. Generate if needed."""
    conn = get_db(app)
    row = conn.execute(
        "SELECT kid FROM signing_keys WHERE active = 1"
    ).fetchone()

    if row is None:
        kid = str(uuid.uuid4())
        private_pem, public_pem = _generate_rsa_keypair()
        conn.execute(
            """INSERT INTO signing_keys (kid, private_key_pem, public_key_pem,
                                         created_at, active)
               VALUES (?, ?, ?, ?, 1)""",
            (kid, private_pem, public_pem, time.time()),
        )
        conn.commit()

    conn.close()


def get_active_key(app):
    """Return (kid, private_key_pem) for the active signing key."""
    conn = get_db(app)
    row = conn.execute(
        "SELECT kid, private_key_pem FROM signing_keys WHERE active = 1"
    ).fetchone()
    conn.close()
    if row is None:
        raise RuntimeError("No active signing key found")
    return row["kid"], row["private_key_pem"]


def get_all_keys(app):
    """Return all signing keys (active and rotated) for JWKS."""
    conn = get_db(app)
    rows = conn.execute(
        "SELECT kid, public_key_pem, active FROM signing_keys ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return rows


def build_jwks(app, issuer=None):
    """Build a JWKS (JSON Web Key Set) from all stored public keys.

    Returns a dict suitable for JSON serialization.
    """
    keys = []
    for row in get_all_keys(app):
        key = jwk.JWK.from_pem(row["public_key_pem"].encode("utf-8"))
        key_dict = key.export(as_dict=True)
        key_dict["use"] = "sig"
        key_dict["kid"] = row["kid"]
        if issuer:
            key_dict["issuer"] = issuer
        keys.append(key_dict)

    return {"keys": keys}


def sign_jwt(app, payload, kid=None):
    """Sign a JWT payload using the active RSA key.

    Args:
        app: Flask app (for DB access).
        payload: dict of JWT claims.
        kid: Optional key ID override. Uses active key if None.

    Returns:
        Encoded JWT string.
    """
    import jwt as pyjwt

    if kid is None:
        kid, private_pem = get_active_key(app)
    else:
        conn = get_db(app)
        row = conn.execute(
            "SELECT private_key_pem FROM signing_keys WHERE kid = ?", (kid,)
        ).fetchone()
        conn.close()
        if row is None:
            raise ValueError(f"Key {kid} not found")
        private_pem = row["private_key_pem"]

    headers = {
        "typ": "JWT",
        "alg": "RS256",
        "kid": kid,
    }

    return pyjwt.encode(payload, private_pem, algorithm="RS256", headers=headers)


def _base64url_encode(data):
    """Base64url encode bytes without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
