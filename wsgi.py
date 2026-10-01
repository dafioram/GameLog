"""WSGI entry point for a real server (gunicorn in Docker) and the Flask CLI:

    gunicorn wsgi:app
    flask --app wsgi prune-covers
"""
import config

config.load_dotenv()

from app import create_app  # noqa: E402  (env must be loaded first)

app = create_app()
