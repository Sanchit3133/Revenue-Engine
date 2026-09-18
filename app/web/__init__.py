import os

from flask import Flask


def create_app():
    app = Flask(__name__)

    app.secret_key = os.getenv(
        "FLASK_SECRET_KEY",
        "development-only-secret-change-me",
    )

    from app.web.routes import web_bp

    app.register_blueprint(web_bp)

    return app