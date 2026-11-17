"""Content hashing (BLAKE2b-256), meant to run in a thread pool."""

from __future__ import annotations

import hashlib

CHUNK = 1 << 20


def hash_file(path: str) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def read_head(path: str, max_bytes: int) -> tuple[bytes, int]:
    """Return ``(first max_bytes bytes, total size)``."""
    with open(path, "rb") as f:
        data = f.read(max_bytes)
        f.seek(0, 2)
        size = f.tell()
    return data, size
