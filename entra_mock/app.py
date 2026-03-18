"""Flask app factory."""

import os
import secrets

from flask import Flask

from entra_mock.config import load_config
from entra_mock.db import init_db, seed_db
from entra_mock.keys import ensure_signing_key


def create_app(config_path=None):
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # Load config
    config = load_config(config_path)
    app.config["ENTRA_CONFIG"] = config

    # Set database path
    db_path = os.environ.get("ENTRA_MOCK_DB", "data/entra_mock.db")
    app.config["DATABASE"] = db_path

    # Flask secret key for session cookies
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

    # Initialize database and seed from config
    init_db(app)
    seed_db(config, app)

    # Ensure signing key exists
    ensure_signing_key(app)

    # Register routes (import here to avoid circular imports)
    from entra_mock.routes import register_routes
    register_routes(app)

    return app
