"""Register all route blueprints."""

from entra_mock.routes.discovery import bp as discovery_bp
from entra_mock.routes.jwks import bp as jwks_bp
from entra_mock.routes.authorize import bp as authorize_bp
from entra_mock.routes.token import bp as token_bp
from entra_mock.routes.userinfo import bp as userinfo_bp
from entra_mock.routes.logout import bp as logout_bp
from entra_mock.routes.admin_api import bp as admin_bp
from entra_mock.routes.landing import bp as landing_bp


def register_routes(app):
    """Register all blueprints with the Flask app."""
    app.register_blueprint(landing_bp)
    app.register_blueprint(discovery_bp)
    app.register_blueprint(jwks_bp)
    app.register_blueprint(authorize_bp)
    app.register_blueprint(token_bp)
    app.register_blueprint(userinfo_bp)
    app.register_blueprint(logout_bp)
    app.register_blueprint(admin_bp)
