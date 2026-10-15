"""WSGI entry point for production servers (for example gunicorn wsgi:app)."""
from app.web import create_app

app = create_app()
