FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-worker.txt ./
RUN python -m pip install --no-cache-dir -r requirements-worker.txt

COPY . .

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin revenue \
    && mkdir -p /app/data \
    && chown -R revenue:revenue /app

USER revenue

EXPOSE 5000

# The hosting platform injects PORT. Keep a single worker as the safe starting
# point for a small instance; increase only after measuring memory/traffic.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers ${WEB_CONCURRENCY:-1} --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-45} wsgi:app"]
