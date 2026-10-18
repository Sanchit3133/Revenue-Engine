"""Flask application factory with environment-sensitive security defaults."""

from flask import Flask, jsonify

from app.config import settings


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    is_production = settings.environment == "production"

    if test_config and test_config.get("SECRET_KEY"):
        secret_key = test_config["SECRET_KEY"]
    else:
        secret_key = settings.flask_secret_key

    if is_production and not secret_key:
        raise RuntimeError("FLASK_SECRET_KEY must be configured in production")
    app.secret_key = secret_key or "development-only-secret-change-me"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=is_production,
        MAX_CONTENT_LENGTH=1_000_000,
    )
    if test_config:
        app.config.update(test_config)

    from app.web.routes import web_bp
    from app.web.webhooks import webhooks_bp
    from app.web.client_requests import client_requests_bp
    from app.web.assistant import assistant_bp
    from app.web.admin import admin_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(client_requests_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(admin_bp)

    @app.get("/healthz")
    def healthz():
        """Minimal process health endpoint; never exposes configuration or database details."""
        return jsonify({"status": "ok"}), 200

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"error": "request_too_large"}), 413

    return app
