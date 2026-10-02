import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import click
from flask import current_app, g
from flask.cli import with_appcontext

import config
from seed_platforms import DEFAULT_PLATFORMS

BASE_DIR = config.BASE_DIR
DATA_DIR = config.DEFAULT_DATA_DIR
DEFAULT_DB_PATH = DATA_DIR / "retro_archive.db"
DB_FILENAME = DEFAULT_DB_PATH.name

# Bump SCHEMA_VERSION and add an entry to MIGRATIONS whenever the schema
# changes after this point. Each migration takes the connection and brings
# the database from (version - 1) to version. schema.sql always describes
# the *latest* schema and is only executed for brand-new databases.
SCHEMA_VERSION = 2


def _migrate_2_save_vault(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS saves (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            copy_id         INTEGER NOT NULL,
            completion_id   INTEGER,
            label           TEXT NOT NULL,
            notes           TEXT,
            filename        TEXT NOT NULL,
            stored_name     TEXT NOT NULL,
            sha256          TEXT NOT NULL,
            size            INTEGER NOT NULL,
            uploaded_at     TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (copy_id) REFERENCES copies(id) ON DELETE CASCADE,
            FOREIGN KEY (completion_id) REFERENCES completions(id) ON DELETE SET NULL
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_saves_copy ON saves(copy_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_saves_completion ON saves(completion_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_saves_stored_name ON saves(stored_name)")


# version -> function bringing the database from (version - 1) to version.
MIGRATIONS = {
    2: _migrate_2_save_vault,
}


class IncompatibleDatabaseError(RuntimeError):
    pass


class DataDirNotWritableError(RuntimeError):
    pass


def _whoami():
    uid = getattr(os, "getuid", lambda: None)()
    gid = getattr(os, "getgid", lambda: None)()
    return f"uid {uid}, gid {gid}" if uid is not None else "the current user"


def check_writable(db_path):
    """Fail at startup with an actionable message, instead of SQLite's bare
    "unable to open database file", when the data folder or database file
    can't be written by the user the app runs as."""
    db_path = Path(db_path)
    folder = db_path.parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass  # reported below
    problem = None
    if not folder.is_dir() or not os.access(folder, os.W_OK | os.X_OK):
        problem = folder
    elif db_path.exists() and not os.access(db_path, os.R_OK | os.W_OK):
        problem = db_path
    if problem is None:
        return
    try:
        st = problem.stat()
        owner = f"it is owned by uid {st.st_uid}, gid {st.st_gid}"
    except OSError:
        owner = "it doesn't exist and couldn't be created"
    raise DataDirNotWritableError(
        f"Retro Archive can't write to {problem}: the app runs as {_whoami()}, "
        f"but {owner}.\n"
        "  Docker: set PUID/PGID in .env to the output of `id -u` / `id -g` and run\n"
        "          `sudo chown -R $(id -u):$(id -g) data` in the project folder.\n"
        "  Native: make the data folder (RETRO_DATA_DIR, default ./data) writable by\n"
        "          the user running the app."
    )


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction():
    """Group several writes so they all land or none do.

        with transaction() as db:
            db.execute(...)
            db.execute(...)
    """
    db = get_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


def init_db():
    """Create a fresh database, or upgrade an existing one through
    MIGRATIONS. Refuses to touch a database from the pre-versioning
    prototype (user_version 0 with tables already present)."""
    check_writable(current_app.config["DATABASE"])
    db = get_db()
    version = db.execute("PRAGMA user_version").fetchone()[0]
    has_tables = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'games'"
    ).fetchone() is not None
    db_path = current_app.config["DATABASE"]

    if has_tables and version == 0:
        raise IncompatibleDatabaseError(
            f"{db_path} was created by an early prototype of Retro Archive and "
            "isn't compatible with this version's schema. Move or delete that "
            "file (and data/assets/covers/ if you like) to start fresh."
        )
    if version > SCHEMA_VERSION:
        raise IncompatibleDatabaseError(
            f"{db_path} is schema version {version}, newer than this app "
            f"understands ({SCHEMA_VERSION}). Update the app."
        )

    if not has_tables:
        with open(BASE_DIR / "schema.sql", "r", encoding="utf-8") as f:
            db.executescript(f.read())
        db.executemany(
            "INSERT OR IGNORE INTO platforms (name) VALUES (?)",
            [(name,) for name in DEFAULT_PLATFORMS],
        )
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.commit()
        return

    # Each step runs in an explicit transaction (Python's sqlite3 would
    # otherwise auto-commit DDL statement by statement), so a failed step
    # leaves the database exactly at the previous version.
    for target in range(version + 1, SCHEMA_VERSION + 1):
        db.commit()
        db.execute("BEGIN")
        try:
            MIGRATIONS[target](db)
            db.execute(f"PRAGMA user_version = {target}")
            db.commit()
        except Exception:
            db.rollback()
            raise


def referenced_cover_files():
    rows = get_db().execute(
        "SELECT DISTINCT cover_image_path FROM games WHERE cover_image_path IS NOT NULL"
    ).fetchall()
    return {r["cover_image_path"] for r in rows}


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(prune_covers_command)


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create/upgrade the database: flask --app wsgi init-db"""
    init_db()
    click.echo("Database ready.")


@click.command("prune-covers")
@click.option("--dry-run", is_flag=True, help="List what would be deleted without deleting.")
@with_appcontext
def prune_covers_command(dry_run):
    """Delete cover image files no game points at any more.

    Clearing or replacing a cover only unlinks it (another game may share the
    same hash-named file), so unused images can pile up. This is the safe
    cleanup: it computes the referenced set from the database first."""
    covers_dir = Path(current_app.config["COVERS_DIR"])
    if not covers_dir.is_dir():
        click.echo("No covers folder yet.")
        return
    keep = referenced_cover_files()
    removed = 0
    for f in sorted(covers_dir.iterdir()):
        if f.is_file() and not f.name.startswith(".") and f.name not in keep:
            click.echo(("would delete " if dry_run else "deleting ") + f.name)
            if not dry_run:
                f.unlink()
            removed += 1
    click.echo(f"{removed} unused cover file(s) {'found' if dry_run else 'removed'}.")
