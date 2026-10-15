"""Run the Flask development server locally; production must use wsgi:app."""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import settings
from app.web import create_app

if settings.environment == "production":
    raise SystemExit("Do not use the Flask development server in production. Use a WSGI server with wsgi:app.")

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
