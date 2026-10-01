"""Gunicorn settings for the Docker image (also usable natively:
`gunicorn -c gunicorn.conf.py wsgi:app`)."""
# Aliased: gunicorn treats every top-level name here as a setting, and
# "config" is one of its own setting names.
import config as _retro

_retro.load_dotenv()

bind = f"{_retro.host()}:{_retro.port()}"

# One process: SQLite is single-writer anyway, and the flash-message signing
# key is generated per process unless RETRO_SECRET_KEY is set. Threads give
# concurrency for page loads while a long hash runs.
workers = 1
worker_class = "gthread"
threads = 4

# Hashing a multi-GB file over a NAS mount can take minutes; don't let the
# request be killed mid-hash.
timeout = 3600
graceful_timeout = 30

accesslog = "-"
errorlog = "-"
