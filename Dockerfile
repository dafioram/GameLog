# Retro Archive — container image.
# Build/run through docker compose (see docker-compose.yml and .env.example).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata so TZ=America/New_York (etc.) works — the "date completed" field
# defaults to *today*, which should be your today, not UTC's.
# util-linux provides setpriv, used by the entrypoint to drop from root to
# PUID:PGID (normally already present; listed so it's guaranteed).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt "gunicorn>=22,<24"

COPY . .
# sed strips Windows line endings in case the repo was checked out with CRLF.
RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod 755 docker-entrypoint.sh

# Inside the container the server must listen on all interfaces so Docker's
# port mapping can reach it. What the *host machine* exposes is decided by
# RETRO_BIND_ADDRESS / RETRO_PORT in .env (see docker-compose.yml).
ENV RETRO_HOST=0.0.0.0 \
    RETRO_PORT=5050 \
    RETRO_DATA_DIR=/data

VOLUME ["/data"]
EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"RETRO_PORT\", \"5050\")}/healthz', timeout=4)" || exit 1

# Starts as root only to fix ownership of /data, then runs the app as
# PUID:PGID (see docker-entrypoint.sh).
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
