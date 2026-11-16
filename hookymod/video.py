"""Bounded WebM container checks, not a codec or game compatibility validator."""
from __future__ import annotations

from .util import HookyError

MAX_HEADER = 65536
MAX_IMPORT = 32 * 1024 * 1024
MAX_STREAM = 1024 * 1024 * 1024


class VideoError(HookyError):
    """Unsupported, malformed or excessive video."""


def _vint(data: bytes, offset: int, *, element_id: bool = False) -> tuple[int, int, bool]:
    if offset >= len(data) or data[offset] == 0:
        raise VideoError("En-tête EBML tronqué ou invalide.")
    first = data[offset]
    marker = 0x80
    length = 1
    while not first & marker:
        marker >>= 1
        length += 1
    if length > (4 if element_id else 8) or offset + length > len(data):
        raise VideoError("Entier EBML hors limites.")
    value = first if element_id else first & (marker - 1)
    for byte in data[offset + 1:offset + length]:
        value = (value << 8) | byte
    unknown = not element_id and value == (1 << (7 * length)) - 1
    return value, offset + length, unknown


def check_header(head: bytes, total_size: int) -> None:
    """Check EBML DocType=webm and a bounded/present Segment header.

    This deliberately does not claim that tracks, codecs, frames, audio,
    dimensions, NDVE or game playback are valid. Browsers may refuse the file.
    """
    if not 16 <= total_size <= MAX_STREAM:
        raise VideoError("Vidéo vide ou trop volumineuse (1 Gio maximum).")
    element, pos, _ = _vint(head, 0, element_id=True)
    if element != 0x1a45dfa3:
        raise VideoError("En-tête EBML WebM absent.")
    size, pos, unknown = _vint(head, pos)
    end = pos + size
    if unknown or size > MAX_HEADER - 16 or end > len(head) or end > total_size:
        raise VideoError("En-tête WebM incomplet ou trop volumineux.")
    doctype = None
    while pos < end:
        child, pos, _ = _vint(head, pos, element_id=True)
        length, pos, child_unknown = _vint(head, pos)
        if child_unknown or pos + length > end:
            raise VideoError("Élément d'en-tête WebM invalide.")
        if child == 0x4282:
            if doctype is not None:
                raise VideoError("Type de document WebM dupliqué.")
            doctype = head[pos:pos + length]
        pos += length
    if doctype != b"webm":
        raise VideoError("Le conteneur doit déclarer le type WebM.")
    segment, pos, _ = _vint(head, pos, element_id=True)
    if segment != 0x18538067:
        raise VideoError("Segment WebM absent.")
    segment_size, pos, unknown = _vint(head, pos)
    if not unknown and pos + segment_size > total_size:
        raise VideoError("Segment WebM tronqué.")
