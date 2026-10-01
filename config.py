"""Runtime settings, read from RETRO_* environment variables.

Everything here has a safe local default, so `python3 app.py` works with no
configuration at all. A `.env` file in the project root (see .env.example)
is read by the entry points (app.py, wsgi.py) via load_dotenv() — values
already present in the real environment always win, so Docker Compose's
`environment:` block and a plain `.env` never fight each other.
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"


def load_dotenv(path=None):
    """Minimal .env reader (KEY=VALUE lines, # comments, optional quotes).
    Avoids a python-dotenv dependency for the handful of settings we have."""
    path = Path(path) if path else BASE_DIR / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    value = os.environ.get(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError:
        return default


def data_dir():
    value = os.environ.get("RETRO_DATA_DIR", "").strip()
    return Path(value).expanduser().resolve() if value else DEFAULT_DATA_DIR


def secret_key():
    # Only used to sign the flash-message cookie. A random per-process key is
    # fine for a single-user app (the only cost is losing an unread flash
    # message across a restart); set RETRO_SECRET_KEY to make it stable.
    return os.environ.get("RETRO_SECRET_KEY", "").strip() or secrets.token_hex(32)


def host():
    # Native default is localhost-only. Set RETRO_HOST=0.0.0.0 to let other
    # machines on your network connect. (In Docker the server always binds
    # 0.0.0.0 *inside* the container; RETRO_BIND_ADDRESS in .env decides
    # what the host machine exposes.)
    return os.environ.get("RETRO_HOST", "").strip() or "127.0.0.1"


def port():
    return env_int("RETRO_PORT", 5050)


def debug():
    return env_bool("RETRO_DEBUG", False)
