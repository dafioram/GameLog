"""Content-addressed file storage for user uploads (save files).

A stored file's name is derived from the SHA-256 of its bytes, so identical
uploads are kept once and a stored file is never overwritten with different
content. Uploads are streamed to disk in chunks while being hashed, so a
large memory-card image never has to sit in memory all at once.
"""
import hashlib
import os
import re
import secrets
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
HASH_NAME_LEN = 16          # hex chars of the sha256 used in the stored name
_EXT_RE = re.compile(r"^[a-z0-9]{1,10}$")


def safe_display_name(filename, fallback="save.bin"):
    """The uploader's file name, reduced to a bare, printable basename:
    no directories, no control characters. Used only as the download name."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip().strip(".")
    return name[:200] or fallback


def _extension(filename):
    if "." not in filename:
        return ""
    ext = filename.rsplit(".", 1)[-1].lower()
    return f".{ext}" if _EXT_RE.match(ext) else ""


def store_upload(file_storage, dest_dir):
    """Save an uploaded werkzeug FileStorage into dest_dir under a
    hash-derived name. Returns (stored_name, sha256_hex, size_bytes).
    Raises ValueError for an empty upload."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".upload-{secrets.token_hex(8)}"
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = file_storage.stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
        if size == 0:
            raise ValueError("That file is empty.")
        sha = digest.hexdigest()
        stored_name = sha[:HASH_NAME_LEN] + _extension(safe_display_name(file_storage.filename))
        final = dest_dir / stored_name
        if final.exists():
            tmp.unlink()            # identical bytes already stored
        else:
            os.replace(tmp, final)  # atomic: never a half-written save
        return stored_name, sha, size
    finally:
        if tmp.exists():
            tmp.unlink()


def remove_if_unreferenced(dest_dir, stored_name, still_referenced):
    """Delete a stored file once nothing points at it any more."""
    if stored_name and not still_referenced:
        path = Path(dest_dir) / stored_name
        if path.is_file():
            path.unlink()
