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
from pathlib import Path

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
    """(taille, date) d'un fichier, ou (0, 0) s'il a disparu."""
    try:
        stat = Path(path).stat()
    except OSError:
        return 0, 0
    return stat.st_size, int(stat.st_mtime)


class ImageCache:
    """Vignettes PNG sur disque, avec plafond et statistiques."""

    def __init__(self, folder: Path, limit_mb: int = CACHE_DEFAULT_MB) -> None:
        self.folder = Path(folder)
        self.limit_bytes = max(0, int(limit_mb)) * 1024 * 1024
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------ accès
    def _path(self, key: str) -> Path:
        return self.folder / key[:2] / f"{key}.png"

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            data = path.read_bytes()
        except OSError:
            self.misses += 1
            return None
        if not data:
            self.misses += 1
            return None
        self.hits += 1
        try:                                  # garde la vignette « fraîche » pour l'élagage
            os.utime(path, None)
        except OSError:
            pass
        return data

    def put(self, key: str, data: bytes) -> bytes:
        if self.limit_bytes <= 0 or not data:
            return data
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            return data
        self.trim()
        return data

    # ------------------------------------------------------------------ entretien
    def files(self) -> list[tuple[float, int, Path]]:
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

    def trim(self) -> int:
        """Élague les vignettes les plus anciennes au-delà du plafond."""
        if self.limit_bytes <= 0:
            return 0
        entries = sorted(self.files())
        total = sum(size for _, size, _ in entries)
        removed = 0
        for _mtime, size, path in entries:
            if total <= self.limit_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed

    def stats(self) -> dict:
        entries = self.files()
        return {
            "entries": len(entries),
            "bytes": sum(size for _, size, _ in entries),
            "limit_bytes": self.limit_bytes,
            "hits": self.hits,
            "misses": self.misses,
            "folder": str(self.folder),
        }

    def clear(self) -> dict:
        before = self.stats()
        if self.folder.is_dir():
            shutil.rmtree(self.folder, ignore_errors=True)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.hits = self.misses = 0
        return {"removed_entries": before["entries"], "freed_bytes": before["bytes"]}


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
