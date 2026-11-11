"""Petites fonctions utilitaires partagées (aucune dépendance GUI)."""

from __future__ import annotations

import os
import re
import struct
import tempfile
import zlib
from pathlib import Path


class HookyError(Exception):
    """Erreur « attendue » : le message est destiné à l'utilisateur."""


class FormatError(HookyError):
    """Le fichier ne respecte pas le format attendu."""


class DictMissing(HookyError):
    """Un dictionnaire zstd requis est absent."""


# --------------------------------------------------------------------------- maths
def align(value: int, alignment: int) -> int:
    """Aligne ``value`` sur un multiple de ``alignment`` (puissance de 2 ou non)."""
    alignment = max(1, int(alignment))
    return (value + alignment - 1) // alignment * alignment


def is_pow2(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


def hex2(value: int) -> str:
    return f"0x{value:02X}"


def human_size(n: int) -> str:
    """4096 -> '4.0 Ko' (unités françaises)."""
    step = 1024.0
    value = float(n)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(value) < step or unit == "To":
            if unit == "o":
                return f"{int(value)} o"
            return f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} To"


# --------------------------------------------------------------------------- chemins
_INVALID = re.compile(r'[\x00-\x1f<>:"|?*]')


def safe_child(base: Path, name: str) -> Path | None:
    """Retourne ``base/name`` si le résultat reste **dans** ``base``.

    Protège contre les chemins ``../`` et les noms absolus que l'on trouve dans
    certaines archives malformées.  ``None`` signifie « refuse ce nom ».
    """
    base = Path(base).resolve()
    rel = Path(name.replace("\\", "/"))
    if rel.is_absolute() or any(part == ".." for part in rel.parts):
        return None
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


def sanitize_name(name: str) -> str:
    """Nettoie un nom d'entrée pour l'écrire sur le disque."""
    name = name.replace("\\", "/").lstrip("/")
    name = _INVALID.sub("_", name)
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    return "/".join(parts) or "sans_nom"


def write_atomic(path: Path, data: bytes) -> None:
    """Écrit ``data`` dans ``path`` de façon atomique (fichier temporaire + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".hookymod-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- binaire
def u16(data: bytes, off: int, endian: str = "<") -> int:
    return struct.unpack_from(endian + "H", data, off)[0]


def u32(data: bytes, off: int, endian: str = "<") -> int:
    return struct.unpack_from(endian + "I", data, off)[0]


def s32(data: bytes, off: int, endian: str = "<") -> int:
    return struct.unpack_from(endian + "i", data, off)[0]


def u64(data: bytes, off: int, endian: str = "<") -> int:
    return struct.unpack_from(endian + "Q", data, off)[0]


def need(data: bytes, off: int, size: int, what: str = "données") -> bytes:
    """Lit une tranche en vérifiant les bornes avec un message clair."""
    end = off + size
    if off < 0 or size < 0 or end > len(data):
        raise FormatError(
            f"Fichier tronqué : {what} demandé à 0x{off:X} (+{size}) "
            f"alors que le fichier fait {len(data)} octets."
        )
    return data[off:end]


def cstring(data: bytes, off: int, encoding: str = "utf-8", limit: int = 4096) -> str:
    """Lit une chaîne C terminée par NUL."""
    if off < 0 or off >= len(data):
        return ""
    end = data.find(b"\x00", off, min(len(data), off + limit))
    if end < 0:
        end = min(len(data), off + limit)
    try:
        return data[off:end].decode(encoding)
    except UnicodeDecodeError:
        return data[off:end].decode("utf-8", "replace")


# --------------------------------------------------------------------------- aperçu texte
def hexdump(data: bytes, limit: int = 1024, base: int = 0, width: int = 16) -> list[str]:
    lines = []
    for i in range(0, min(len(data), limit), width):
        chunk = data[i:i + width]
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{base + i:08X}  {chunk.hex(' '):<{width * 3 - 1}}  |{asc}|")
    if len(data) > limit:
        lines.append(f"… {len(data) - limit:,} octets supplémentaires")
    return lines


_ASCII_RUN = re.compile(rb"[\x20-\x7e]{5,}")


def ascii_strings(data: bytes, limit: int = 64, min_len: int = 5,
                  scan: int = 4 * 1024 * 1024) -> list[str]:
    """Extrait les chaînes ASCII imprimables (noms internes, chemins, …)."""
    seen: set[bytes] = set()
    out: list[str] = []
    for match in _ASCII_RUN.finditer(data[:scan]):
        raw = match.group()
        if len(raw) < min_len or raw in seen:
            continue
        seen.add(raw)
        out.append(raw.decode("ascii", "replace"))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- PNG
def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    """Encode un buffer RGBA en PNG (zlib uniquement, pas de Pillow)."""
    if width <= 0 or height <= 0:
        raise ValueError("dimensions PNG invalides")
    expected = width * height * 4
    if len(rgba) < expected:
        raise ValueError(f"buffer RGBA trop court ({len(rgba)} < {expected})")
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filtre None
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def decode_png(path_or_bytes) -> tuple[int, int, bytes]:
    """Décode un PNG 8 bits RGB/RGBA (utilisé pour remplacer une texture).

    Retourne ``(largeur, hauteur, rgba)``.  Lève :class:`FormatError` si le
    fichier n'est pas un PNG géré.
    """
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
    else:
        data = Path(path_or_bytes).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise FormatError("Ce n'est pas un PNG.")
    pos, width, height, depth = 8, 0, 0, 0
    idat = bytearray()
    palette = b""
    trns = b""
    color_type = 6
    while pos + 8 <= len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, color_type = struct.unpack_from(">IIBB", payload, 0)
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"tRNS":
            trns = payload
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    if depth != 8:
        raise FormatError(f"PNG {depth} bits non géré (8 bits requis).")
    if not width or not height:
        raise FormatError("PNG sans en-tête IHDR valide.")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise FormatError(f"PNG corrompu : {exc}") from exc
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise FormatError(f"Type de couleur PNG {color_type} non géré.")
    stride = width * channels
    out = bytearray(height * width * 4)
    prev = bytearray(stride)
    offset = 0
    for y in range(height):
        if offset >= len(raw):
            raise FormatError("PNG tronqué.")
        ftype = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        if len(line) < stride:
            raise FormatError("PNG tronqué.")
        offset += stride
        if ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif ftype != 0:
            raise FormatError(f"Filtre PNG {ftype} inconnu.")
        for x in range(width):
            dst = (y * width + x) * 4
            if color_type == 6:
                out[dst:dst + 4] = line[x * 4:x * 4 + 4]
            elif color_type == 2:
                r, g, b = line[x * 3:x * 3 + 3]
                out[dst:dst + 4] = bytes((r, g, b, 255))
            elif color_type == 0:
                v = line[x]
                out[dst:dst + 4] = bytes((v, v, v, 255))
            elif color_type == 4:
                v, a = line[x * 2:x * 2 + 2]
                out[dst:dst + 4] = bytes((v, v, v, a))
            else:  # palette
                idx = line[x]
                if idx * 3 + 2 < len(palette):
                    r, g, b = palette[idx * 3:idx * 3 + 3]
                    a = trns[idx] if idx < len(trns) else 255
                    out[dst:dst + 4] = bytes((r, g, b, a))
        prev = line
    return width, height, bytes(out)


def open_in_explorer(path: Path) -> None:
    """Ouvre un dossier/fichier dans le gestionnaire de fichiers du système."""
    import subprocess
    import sys
    path = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass
