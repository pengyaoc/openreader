"""On-disk cache for the image proxy (api/images.py), added 2026-10-27.

Without it, every image view not covered by the browser's own cache (a new
device, a cleared cache, anything older than a day) paid a full fetch from
the origin, plus the SSRF DNS check, before its first byte reached the
reader. Images at a given URL practically never change, so the first fetch
is kept on disk and later loads are served from there.

One file per URL, named by the URL's SHA-256: the content type, a newline,
then the body. Size-bounded: once the directory grows past max_bytes, the
least recently used entries (mtime, bumped on every hit) are removed until
it's back under 80% of the cap. The data disk on the VM is small, so the
cap is enforced rather than left to grow.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

# A prune lists the whole directory, so it runs every few writes rather than
# after each one. The directory can overshoot the cap by at most this many
# images in between.
_PRUNE_EVERY = 32


class ImageCache:
    def __init__(self, directory: Path, max_bytes: int) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._writes_since_prune = 0

    def _path(self, url: str) -> Path:
        return self.directory / hashlib.sha256(url.encode("utf-8")).hexdigest()

    def get(self, url: str) -> tuple[str, bytes] | None:
        path = self._path(url)
        try:
            data = path.read_bytes()
        except OSError:
            return None
        content_type, sep, body = data.partition(b"\n")
        if not sep:
            return None
        try:
            os.utime(path)  # recency for the LRU prune
        except OSError:
            pass
        return content_type.decode("ascii", "replace"), body

    def put(self, url: str, content_type: str, body: bytes) -> None:
        if len(body) > self.max_bytes:
            return
        path = self._path(url)
        tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(content_type.encode("ascii", "replace") + b"\n" + body)
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            return
        with self._lock:
            self._writes_since_prune += 1
            if self._writes_since_prune < _PRUNE_EVERY:
                return
            self._writes_since_prune = 0
        self.prune()

    def prune(self) -> None:
        with self._lock:
            entries = []
            total = 0
            try:
                with os.scandir(self.directory) as it:
                    for entry in it:
                        if entry.name.endswith(".tmp"):
                            continue
                        try:
                            st = entry.stat()
                        except OSError:
                            continue
                        entries.append((st.st_mtime, st.st_size, entry.path))
                        total += st.st_size
            except OSError:
                return
            if total <= self.max_bytes:
                return
            target = self.max_bytes * 0.8
            for _, size, path in sorted(entries):
                if total <= target:
                    break
                try:
                    os.unlink(path)
                except OSError:
                    continue
                total -= size
