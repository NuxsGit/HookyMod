"""Réserver les dossiers d'export à leur source, même après un nouveau scan.

Un export antérieur ou un dossier utilisateur sans marqueur est considéré
comme occupé. Le marqueur reste local au dossier et n'entre pas dans les ZIP.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from hookymod.util import KeyedLocks, write_atomic

OWNER_FILE = ".hookycreator-source.json"
_OWNER_LIMIT = 64 * 1024
_RESERVATIONS = KeyedLocks()


class ExportFolders:
    """Attribution stable et conservative de noms plats à des sources.

    Le dossier est créé avant la publication du marqueur : s'il existe déjà,
    mais que sa provenance est inconnue ou illisible, il n'est pas réutilisé.
    Le ``mkdir`` exclusif évite aussi de réclamer un dossier créé entre-temps
    par une autre instance. L'appelant fournit des noms déjà assainis.
    """

    def __init__(self, base: Path, root: Path, kind: str) -> None:
        self.base = Path(base)
        self.root = os.path.normcase(str(Path(root).resolve()))
        self.kind = kind
        self._lock = threading.RLock()
        self._occupied: set[str] | None = None
        self._owned: dict[str, str] = {}

    def _read_owner(self, folder: Path) -> dict | None:
        try:
            marker = folder / OWNER_FILE
            if (folder.is_symlink() or not folder.is_dir() or marker.is_symlink()
                    or not marker.is_file()):
                return None
            with marker.open("rb") as stream:
                raw = stream.read(_OWNER_LIMIT + 1)
            if len(raw) > _OWNER_LIMIT:
                return None
            data = json.loads(raw)
            if (not isinstance(data, dict) or data.get("version") != 1
                    or data.get("folder") != folder.name
                    or not isinstance(data.get("source"), str)):
                return None
            return data
        except (OSError, ValueError, UnicodeError):
            return None

    def owns(self, folder: Path, source: str) -> bool:
        """Le marqueur doit toujours correspondre avant de remplacer un fichier."""
        data = self._read_owner(folder)
        return bool(data and data.get("root") == self.root
                    and data.get("kind") == self.kind and data["source"] == source)

    def _load(self) -> None:
        if self._occupied is not None:
            return
        self.base.mkdir(parents=True, exist_ok=True)
        occupied: set[str] = set()
        owned: dict[str, str] = {}
        with os.scandir(self.base) as entries:
            for entry in entries:
                occupied.add(entry.name.casefold())
                try:
                    is_directory = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if not is_directory:
                    continue
                data = self._read_owner(self.base / entry.name)
                if data and data.get("root") == self.root and data.get("kind") == self.kind:
                    owned.setdefault(data["source"], entry.name)
        self._occupied, self._owned = occupied, owned

    def claim(self, source: str, preferred: str, fallback: str, *,
              reserved: set[str] | frozenset[str] = frozenset(),
              preferred_is_reserved: bool = False) -> Path:
        """Retourne et réserve un dossier ; ``reserved`` contient des noms casefoldés.

        ``preferred_is_reserved`` autorise un nom déjà réservé à cette source
        par l'inventaire. Les autres noms réservés sont laissés aux autres actifs.
        """
        for name in (preferred, fallback):
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                raise ValueError("Nom de dossier d'export invalide.")
        # Deux instances Project dans le même processus peuvent partager la
        # destination : elles ne doivent pas créer deux marqueurs simultanés.
        key = os.path.normcase(str(self.base.resolve()))
        with _RESERVATIONS.hold(key), self._lock:
            self._load()
            assert self._occupied is not None
            old = self._owned.get(source)
            if old:
                folder = self.base / old
                if self.owns(folder, source):
                    return folder
                # Un dossier supprimé ou dont le marqueur a changé ne doit
                # jamais être remplacé sur la foi d'un ancien cache en mémoire.
                self._owned.pop(source, None)

            name, number = preferred, 1
            while True:
                key = name.casefold()
                reserved_for_other = key in reserved and not (
                    preferred_is_reserved and key == preferred.casefold())
                if key not in self._occupied and not reserved_for_other:
                    folder = self.base / name
                    try:
                        folder.mkdir(exist_ok=False)
                    except FileExistsError:
                        # Une autre instance a réservé le nom depuis le scan.
                        self._occupied.add(key)
                        if self.owns(folder, source):
                            self._owned[source] = name
                            return folder
                    else:
                        try:
                            payload = {"version": 1, "root": self.root, "folder": name,
                                       "kind": self.kind, "source": source}
                            # L'encodage ASCII avec échappements conserve aussi
                            # les noms de fichiers POSIX contenant des octets
                            # non UTF-8 représentés par surrogateescape.
                            write_atomic(folder / OWNER_FILE,
                                         (json.dumps(payload) + "\n").encode("ascii"))
                        except BaseException:
                            try:
                                folder.rmdir()  # seulement si le dossier est encore vide
                            except OSError:
                                pass
                            raise
                        self._occupied.add(key)
                        self._owned[source] = name
                        return folder
                name = fallback if number == 1 else f"{fallback}-{number}"
                number += 1
