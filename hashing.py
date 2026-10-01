import hashlib
import zlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024  # 1 MB, so large files never load fully into memory


def hash_bytes(data):
    """Return the sha256 hex digest of an in-memory blob (e.g. an
    uploaded cover image small enough to read into memory at once)."""
    return hashlib.sha256(data).hexdigest()


def hash_file(path):
    """Return (sha256_hex, crc32_hex, size_bytes) for the file at path.
    Raises FileNotFoundError if it isn't a real file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(path)

    sha256 = hashlib.sha256()
    crc32 = 0
    size = 0
    with open(p, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
            crc32 = zlib.crc32(chunk, crc32)
            size += len(chunk)

    return sha256.hexdigest(), format(crc32 & 0xFFFFFFFF, "08x"), size
