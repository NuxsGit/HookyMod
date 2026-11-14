"""Cache disque des vignettes et mémoire du processus.

Sans cache, ouvrir la vue « Modèles » redécompresse **et** rasterise chaque
modèle à chaque affichage : sur un dossier de plusieurs centaines de fichiers,
c'est ce qui rendait l'atelier lent. Les vignettes sont donc écrites une fois
sur disque, servies avec un ``ETag`` (le navigateur garde la sienne), et
l'index des maillages décodés garde un budget mémoire explicite.

Rien n'est deviné : la clé de cache contient la taille et la date du fichier
source **et** les réglages de rendu, donc modifier un modèle ou changer
l'anticrénelage invalide la vignette tout seul.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
import threading
from collections import OrderedDict
from pathlib import Path

from hookymod.util import KeyedLocks, write_atomic

CACHE_MIN_MB = 0
CACHE_MAX_MB = 4096
CACHE_DEFAULT_MB = 256


def digest(*parts) -> str:
    """Empreinte stable d'un tuple de valeurs (clé de cache)."""
    hasher = hashlib.sha1()
    for part in parts:
        hasher.update(repr(part).encode("utf-8", "replace"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()[:24]


def file_stamp(path: Path) -> tuple[int, int]:
    """(taille, date en nanosecondes), ou (0, 0) si le fichier a disparu."""
    try:
        stat = Path(path).stat()
    except OSError:
        return 0, 0
    return stat.st_size, stat.st_mtime_ns


class ImageCache:
    """PNG sur disque : index LRU incrémental, écritures atomiques et verrou.

    Le premier accès à l'index lit le dossier une fois. Les insertions suivantes
    sont O(1) tant que le plafond n'est pas atteint, au lieu de rescanner et
    trier toutes les images après chaque rendu. Un rafraîchissement périodique
    reprend aussi les changements effectués par un autre processus.
    """

    _REFRESH_SECONDS = 30.0

    def __init__(self, folder: Path, limit_mb: int = CACHE_DEFAULT_MB) -> None:
        self.folder = Path(folder)
        self.limit_bytes = max(0, int(limit_mb)) * 1024 * 1024
        self.hits = 0
        self.misses = 0
        self._lock = threading.RLock()
        self._entries: OrderedDict[Path, int] | None = None
        self._bytes = 0
        self._refreshed_at = 0.0

    def _path(self, key: str) -> Path:
        return self.folder / key[:2] / f"{key}.png"

    def files(self) -> list[tuple[float, int, Path]]:
        """Inventaire physique, également utilisé pour réconcilier l'index."""
        found: list[tuple[float, int, Path]] = []
        if not self.folder.is_dir():
            return found
        for path in self.folder.rglob("*.png"):
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append((stat.st_mtime, stat.st_size, path))
        return found

    def _index(self, force: bool = False) -> None:
        if (not force and self._entries is not None
                and time.monotonic() - self._refreshed_at < self._REFRESH_SECONDS):
            return
        entries = sorted(self.files())
        self._entries = OrderedDict((path, size) for _, size, path in entries)
        self._bytes = sum(self._entries.values())
        self._refreshed_at = time.monotonic()

    def _evict(self) -> int:
        assert self._entries is not None
        if self._bytes <= self.limit_bytes:
            return 0
        removed = 0
        for path in list(self._entries):
            if self._bytes <= self.limit_bytes:
                break
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            self._bytes -= self._entries.pop(path)
            removed += 1
        return removed

    # ------------------------------------------------------------------ accès
    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        with self._lock:
            if self.limit_bytes <= 0:
                self.misses += 1
                return None
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            if not data:
                self.misses += 1
                if self._entries is not None and path in self._entries:
                    self._bytes -= self._entries.pop(path)
                return None
            self.hits += 1
            try:
                os.utime(path, None)
            except OSError:
                pass
            if self._entries is not None:
                old = self._entries.pop(path, 0)
                self._entries[path] = len(data)
                self._bytes += len(data) - old
            return data

    def put(self, key: str, data: bytes) -> bytes:
        with self._lock:
            if self.limit_bytes <= 0 or not data or len(data) > self.limit_bytes:
                return data
            self._index()
            assert self._entries is not None
            path = self._path(key)
            try:
                # Un lecteur, même dans un autre processus, ne voit jamais un
                # PNG partiellement écrit. Les noms temporaires sont uniques.
                write_atomic(path, data)
            except OSError:
                return data
            old = self._entries.pop(path, 0)
            self._entries[path] = len(data)
            self._bytes += len(data) - old
            self._evict()
            return data

    # ------------------------------------------------------------------ entretien
    def set_limit_mb(self, limit_mb: int) -> int:
        """Change le plafond sous le même verrou que les lectures et écritures."""
        with self._lock:
            self.limit_bytes = max(0, int(limit_mb)) * 1024 * 1024
            if not self.limit_bytes:
                return 0
            self._index(force=True)
            return self._evict()

    def trim(self) -> int:
        """Réconcilie le dossier et élague les plus anciennes au-delà du plafond.

        Un plafond nul désactive les lectures/écritures sans effacer le cache.
        """
        with self._lock:
            if self.limit_bytes <= 0:
                return 0
            self._index(force=True)
            return self._evict()

    def stats(self) -> dict:
        with self._lock:
            self._index()
            assert self._entries is not None
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "limit_bytes": self.limit_bytes,
                "hits": self.hits,
                "misses": self.misses,
                "folder": str(self.folder),
            }

    def clear(self) -> dict:
        with self._lock:
            self._index(force=True)
            before_entries = len(self._entries or ())
            before_bytes = self._bytes
            if self.folder.is_dir():
                shutil.rmtree(self.folder, ignore_errors=True)
            self.folder.mkdir(parents=True, exist_ok=True)
            # Si la suppression a échoué partiellement, les chiffres restent exacts.
            self._index(force=True)
            self.hits = self.misses = 0
            return {"removed_entries": before_entries - len(self._entries or ()),
                    "freed_bytes": before_bytes - self._bytes}


# --------------------------------------------------------------------------- mémoire
def rss_bytes() -> int | None:
    """Mémoire résidente du processus, en octets (``None`` si inconnue)."""
    if sys.platform.startswith("win"):                     # pragma: no cover - Windows
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
            return None
        except Exception:
            return None
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        pass
    try:                                                   # pragma: no cover - macOS
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value) * (1 if sys.platform == "darwin" else 1024)
    except Exception:
        return None


def mesh_bytes(meshes) -> int:
    """Estimation de la mémoire occupée par des maillages décodés."""
    total = 0
    for mesh in meshes:
        total += 32 * len(mesh.positions) + 48 * len(mesh.faces)
        for extra in (mesh.normals, mesh.uvs, mesh.colors):
            if extra:
                total += 16 * len(extra)
    return total


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
