# Heroku runs the explicit additive schema migration before releasing the new web dyno.
release: python -m scripts.init_db --allow-production
web: gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 45 wsgi:app
