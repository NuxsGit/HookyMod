"""Bounded reader for skeletal animation clips in BFRES 10.x (FSKA).

This reader covers the cubic scalar curves and Euler rotations observed in the
10.4 files supplied for testing.  It exports actual keyframes, values and
slopes; it does not infer missing bones or rewrite game files.  Unsupported
curve/rotation encodings are rejected rather than silently invented.

Layout and interpolation cross-checked with NupExplorer's MIT-licensed
``nupx/anim.py`` (Copyright 2026 lunixxx37); see docs/PROVENANCE.md.
"""
from __future__ import annotations

import math
import struct
from typing import Any

from .util import HookyError

MAX_BYTES = 64 * 1024 * 1024
MAX_CLIPS = 4096
MAX_BONES = 4096
MAX_CURVES = 32768
MAX_KEYS = 250_000
MAX_FRAMES = 100_000
FPS = 30  # playback convention; BFRES does not encode a frame rate here
_TARGETS = {
    0x04: "scale.x", 0x08: "scale.y", 0x0c: "scale.z",
    0x10: "translation.x", 0x14: "translation.y", 0x18: "translation.z",
    0x20: "rotation.x", 0x24: "rotation.y", 0x28: "rotation.z", 0x2c: "rotation.w",
}


class AnimationError(HookyError):
    """Truncated, unsupported or excessive animation data."""


def _region(data: bytes, offset: int, size: int) -> None:
    if offset <= 0 or size < 0 or offset + size > len(data):
        raise AnimationError("Table d'animation absente ou hors limites.")


def _name(data: bytes, offset: int) -> str:
    _region(data, offset, 2)
    length = struct.unpack_from("<H", data, offset)[0]
    if not 0 < length <= 1024:
        raise AnimationError("Nom d'animation ou d'os invalide.")
    _region(data, offset + 2, length)
    try:
        name = data[offset + 2:offset + 2 + length].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnimationError("Nom d'animation non UTF-8.") from exc
    if not name.isprintable() or "\x00" in name:
        raise AnimationError("Nom d'animation invalide.")
    return name


def _table(data: bytes) -> tuple[int, int]:
    if (len(data) > MAX_BYTES or len(data) < 0x70 or data[:4] != b"FRES"
            or data[0x0c:0x0e] != b"\xff\xfe" or data[0x0a] != 10):
        raise AnimationError("BFRES 10.x little-endian attendu (64 Mio maximum).")
    table, dictionary = struct.unpack_from("<QQ", data, 0x58)
    if not table and not dictionary:
        return 0, 0
    _region(data, dictionary, 8)
    count, = struct.unpack_from("<I", data, dictionary + 4)
    if not 0 < count <= MAX_CLIPS:
        raise AnimationError("Nombre de clips squelettiques invalide.")
    _region(data, table, count * 0x50)
    return table, count


def _clip_header(data: bytes, offset: int, index: int) -> dict[str, Any]:
    if data[offset:offset + 4] != b"FSKA":
        raise AnimationError("Table FSKA invalide.")
    flags, = struct.unpack_from("<I", data, offset + 4)
    name_ptr, = struct.unpack_from("<Q", data, offset + 8)
    frames, curves = struct.unpack_from("<ii", data, offset + 0x40)
    bones, = struct.unpack_from("<H", data, offset + 0x4c)
    if not 0 <= frames <= MAX_FRAMES or not 0 <= curves <= MAX_CURVES or bones > MAX_BONES:
        raise AnimationError("Nombre d'images, d'os ou de courbes trop élevé.")
    return {"index": index, "name": _name(data, name_ptr), "frames": frames,
            "fps": FPS, "loop": bool(flags & 4), "bone_count": bones,
            "curve_count": curves, "rotation_mode": flags & 0x7000,
            "supported": (flags & 0x7000) == 0x1000}


def list_clips(data: bytes) -> list[dict[str, Any]]:
    table, count = _table(data)
    return [_clip_header(data, table + index * 0x50, index) for index in range(count)]


def _finite(values) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def _read_curve(data: bytes, offset: int) -> tuple[dict[str, Any], int]:
    _region(data, offset, 0x30)
    frame_ptr, key_ptr = struct.unpack_from("<QQ", data, offset)
    flags, count, target = struct.unpack_from("<HHI", data, offset + 0x10)
    start, end, frame_scale, value_scale, value_offset = struct.unpack_from("<5f", data, offset + 0x18)
    frame_type, key_type, curve_type = flags & 3, (flags >> 2) & 3, (flags >> 4) & 7
    if (not count or target not in _TARGETS or flags & 0x80
            or curve_type != 0 or frame_type not in (0, 1, 2) or key_type not in (0, 1, 2)
            or not _finite((start, end, frame_scale, value_scale, value_offset))):
        raise AnimationError("Courbe d'animation ou rotation non prise en charge.")
    frame_fmt, frame_size = {0: ("f", 4), 1: ("H", 2), 2: ("B", 1)}[frame_type]
    _region(data, frame_ptr, count * frame_size)
    frames = list(struct.unpack_from(f"<{count}{frame_fmt}", data, frame_ptr))
    if frame_type:
        scale = frame_scale or (1.0 / 32.0 if frame_type == 1 else 1.0)
        frames = [float(value) * scale for value in frames]
    if not _finite(frames) or any(a > b for a, b in zip(frames, frames[1:])):
        raise AnimationError("Images-clés d'animation invalides ou non triées.")
    quantized = key_type != 0 or bool(flags & 0x0800)
    key_fmt, key_size = {0: ("f", 4), 1: ("H", 2), 2: ("B", 1)}[key_type]
    header = 8 if quantized else 0
    _region(data, key_ptr, header + count * key_size * 2)
    if quantized:
        slope_scale, slope_offset = struct.unpack_from("<2f", data, key_ptr)
        if not _finite((slope_scale, slope_offset)):
            raise AnimationError("Échelle de courbe invalide.")
        if key_type == 0:
            value_scale = value_scale or 1.0
            slope_scale = slope_scale or 1.0
    else:
        slope_scale, slope_offset = 1.0, 0.0
    keys = struct.unpack_from(f"<{count * 2}{key_fmt}", data, key_ptr + header)
    values = [float(keys[i * 2]) * (value_scale if quantized else 1.0)
              + (value_offset if quantized else 0.0) for i in range(count)]
    slopes = [float(keys[i * 2 + 1]) * slope_scale + slope_offset for i in range(count)]
    if not _finite(values) or not _finite(slopes):
        raise AnimationError("Valeurs d'animation non finies.")
    return {"target": _TARGETS[target], "interpolation": "cubic",
            "frames": frames, "values": values, "slopes": slopes}, count


def read_clip(data: bytes, index: int) -> dict[str, Any]:
    table, count = _table(data)
    if type(index) is not int or not 0 <= index < count:
        raise AnimationError("Index de clip d'animation hors limites.")
    offset = table + index * 0x50
    result = _clip_header(data, offset, index)
    if not result["supported"]:
        raise AnimationError("Seules les animations à rotations Euler sont prises en charge.")
    bone_table, = struct.unpack_from("<Q", data, offset + 0x28)
    if result["bone_count"]:
        _region(data, bone_table, result["bone_count"] * 0x38)
    bones = []
    names = set()
    total_curves = total_keys = 0
    for i in range(result["bone_count"]):
        p = bone_table + i * 0x38
        name_ptr, curve_ptr, base_ptr = struct.unpack_from("<3Q", data, p)
        flags, = struct.unpack_from("<I", data, p + 0x28)
        ncurve = data[p + 0x2e]
        name = _name(data, name_ptr)
        if name in names or flags & 0x00100000:
            raise AnimationError("Os dupliqué ou rotation quaternion non prise en charge.")
        names.add(name)
        total_curves += ncurve
        if total_curves > MAX_CURVES:
            raise AnimationError("Trop de courbes d'animation.")
        if ncurve:
            _region(data, curve_ptr, ncurve * 0x30)
        base: dict[str, Any] = {}
        cursor = base_ptr
        for flag, key, n in ((8, "scale", 3), (16, "rotation", 4), (32, "translation", 3)):
            if flags & flag:
                _region(data, cursor, n * 4)
                values = list(struct.unpack_from(f"<{n}f", data, cursor))
                if not _finite(values):
                    raise AnimationError("Transformation de base non finie.")
                base[key] = values
                cursor += n * 4
        curves = []
        targets = set()
        for j in range(ncurve):
            curve, keys = _read_curve(data, curve_ptr + j * 0x30)
            total_keys += keys
            if total_keys > MAX_KEYS:
                raise AnimationError("Trop d'images-clés pour un clip.")
            if curve["target"] in targets:
                raise AnimationError("Courbes dupliquées pour la même composante d'os.")
            targets.add(curve["target"])
            curves.append(curve)
        bones.append({"name": name, "base": base, "ssc": bool(flags & 0x00800000),
                      "curves": curves})
    if total_curves != result["curve_count"]:
        raise AnimationError("Nombre de courbes FSKA incohérent.")
    result.update({"bones": bones, "key_count": total_keys,
                   "note": "30 images/s par convention ; lecture expérimentale, non validée en jeu."})
    return result


def evaluate(curve: dict, frame: float) -> float:
    """Evaluate a supported cubic Hermite curve, including duplicate keys."""
    frames, values, slopes = curve["frames"], curve["values"], curve["slopes"]
    if not math.isfinite(frame) or not frames or len(frames) != len(values) or len(frames) != len(slopes):
        raise AnimationError("Courbe ou temps d'animation invalide.")
    if len(frames) == 1 or frame <= frames[0]:
        return values[0]
    if frame >= frames[-1]:
        return values[-1]
    import bisect
    i = min(len(frames) - 2, bisect.bisect_right(frames, frame) - 1)
    duration = frames[i + 1] - frames[i]
    if duration == 0:
        return values[i + 1]
    u = (frame - frames[i]) / duration
    return ((2*u**3 - 3*u**2 + 1) * values[i]
            + (u**3 - 2*u**2 + u) * duration * slopes[i]
            + (-2*u**3 + 3*u**2) * values[i + 1]
            + (u**3 - u**2) * duration * slopes[i + 1])
