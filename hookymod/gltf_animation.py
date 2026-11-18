"""Export d'un skin BFRES validé et de clips Euler/quaternion vers glTF 2.0.

Les positions reçues sont déjà en espace modèle dans la pose de repos. Les
joints, matrices inverses et poids décrivent donc la même déformation que
l'aperçu WebGL. Les courbes cubiques sont échantillonnées en LINEAR pour éviter
la perte de tangentes à l'import de certains logiciels ; les rotations Euler
sont converties en quaternions. Les courbes quaternion sont aussi subdivisées
pour limiter l'erreur des consommateurs qui interpolent leurs composantes
linéairement. Les clés au même instant restent dans la courbe source ; un saut
est approché par une brève transition puisque glTF impose des temps distincts.
Les variantes non représentables ne sont jamais remplacées par une animation
statique silencieuse. Ce module n'écrit aucun fichier du jeu.
"""
from __future__ import annotations

import bisect
import itertools
import math
import struct
import sys
from array import array

from . import animation as animation_mod
from .util import HookyError

# Limites indépendantes de la taille du fichier source : la conversion des
# rotations ne doit pas développer indéfiniment une courbe très oscillante.
MAX_SAMPLES = 1_000_000
# Un grand nombre de canaux d'une seule clé coûte beaucoup de JSON/accessors,
# même lorsque le budget des échantillons est respecté.
MAX_CHANNELS = 100_000
MAX_SUBDIVISION = 20
ROTATION_ERROR = 0.0001  # radians, contrôlé à 1/4, 1/2 et 3/4 de chaque intervalle
VECTOR_ERROR = 0.00001  # erreur relative (au moins 1 unité), mêmes points de contrôle
MAX_ROTATION_STEP = math.pi / 2
# glTF exige des temps strictement croissants et ne peut pas exprimer un saut
# instantané au milieu d'un sampler LINEAR. Deux millisecondes séparent aussi
# assez les clés pour que Blender 4.2 ne les fusionne pas à 24 images/s.
JUMP_WINDOW_SECONDS = 0.002


class ExportAnimationError(HookyError):
    """Skin ou animation non représentable par cet exporteur glTF."""


def _inverse_scale(scale):
    """Retourne l'inverse d'une échelle à trois composantes non singulières."""
    if any(not math.isfinite(value) or abs(value) < 1e-8 for value in scale):
        return None
    return tuple(1.0 / value for value in scale)


def _uniform_inverse(scale):
    """Retourne 1/s si les trois composantes représentent une échelle uniforme."""
    value = scale[0]
    if abs(value) < 1e-8 or any(not math.isclose(v, value, rel_tol=1e-9, abs_tol=1e-12) for v in scale[1:]):
        return None
    return 1.0 / value


def bind(skin: dict, clip: dict) -> dict:
    """Associe les pistes par nom et refuse les compensations incompatibles."""
    bones, meshes = skin.get("bones"), skin.get("meshes")
    if not isinstance(bones, list) or not bones or len(bones) > 4096 or not isinstance(meshes, list):
        raise ExportAnimationError("Squelette de l'export invalide.")
    names = {}
    for i, bone in enumerate(bones):
        name, parent = bone.get("name"), bone.get("parent")
        if (not isinstance(name, str) or not name or name in names or type(parent) is not int
                or parent < -1 or parent >= len(bones) or parent == i):
            raise ExportAnimationError("Noms ou hiérarchie du squelette invalides.")
        for key, length in (("scale", 3), ("rotation", 4), ("translation", 3), ("inverse", 12)):
            values = bone.get(key)
            if not isinstance(values, (list, tuple)) or len(values) != length or not all(
                    isinstance(value, (int, float)) and math.isfinite(value) for value in values):
                raise ExportAnimationError("Transformation de repos invalide.")
        if type(bone.get("ssc")) is not bool:
            raise ExportAnimationError("Compensation d'échelle du squelette invalide.")
        mode = bone.get("rotation_mode", "euler")
        if mode not in ("euler", "quaternion"):
            raise ExportAnimationError(f"Mode de rotation inconnu pour l'os « {name} ».")
        if mode == "quaternion" and not 0.999 <= math.hypot(*bone["rotation"]) <= 1.001:
            raise ExportAnimationError(f"Quaternion de repos non unitaire pour l'os « {name} ».")
        names[name] = i
    done = set()
    for index in range(len(bones)):
        current, seen = index, set()
        while current != -1 and current not in done:
            if current in seen:
                raise ExportAnimationError("Cycle dans le squelette.")
            seen.add(current)
            current = bones[current]["parent"]
        done.update(seen)
    if clip.get("scale_mode") == 0x300:
        raise ExportAnimationError("Mode d'échelle Softimage FSKA non pris en charge.")
    frames, fps, clip_bones = clip.get("frames"), clip.get("fps"), clip.get("bones")
    if (not isinstance(frames, (int, float)) or not math.isfinite(frames) or not 0 <= frames <= 100_000
            or not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 0 < fps <= 240
            or not isinstance(clip_bones, list)):
        raise ExportAnimationError("Durée du clip invalide.")
    tracks = [None] * len(bones)
    missing = []
    matched = animated = repeated_keys = 0
    for track in clip_bones:
        name = track.get("name")
        if name not in names:
            missing.append(name)
            continue
        index = names[name]
        if tracks[index] is not None or not isinstance(track.get("base"), dict) or not isinstance(track.get("curves"), list):
            raise ExportAnimationError("Piste d'animation invalide ou dupliquée.")
        for key, n in (("scale", 3), ("rotation", 4), ("translation", 3)):
            values = track["base"].get(key)
            if values is not None and (not isinstance(values, (list, tuple)) or len(values) != n
                                       or not all(math.isfinite(v) for v in values)):
                raise ExportAnimationError("Pose d'animation invalide.")
        mode = track.get("rotation_mode", "euler")
        if mode not in ("euler", "quaternion"):
            raise ExportAnimationError(f"Mode de rotation de l'os « {name} » inconnu.")
        if mode == "quaternion" and "rotation" in track["base"] and not (
                0.999 <= math.hypot(*track["base"]["rotation"]) <= 1.001):
            raise ExportAnimationError(f"Quaternion de base non unitaire pour l'os « {name} ».")
        targets = set()
        for curve in track["curves"]:
            target = curve.get("target")
            quaternion = target == "rotation" and curve.get("interpolation") == "slerp" and mode == "quaternion"
            scalar = (target in {f"{path}.{axis}" for path in ("translation", "rotation", "scale") for axis in "xyz"}
                      and curve.get("interpolation") == "cubic"
                      and (mode != "quaternion" or not target.startswith("rotation.")))
            if (not quaternion and not scalar) or target in targets:
                raise ExportAnimationError("Courbe ou composante de rotation non prise en charge.")
            targets.add(target)
            keys, values, slopes = curve.get("frames"), curve.get("values"), curve.get("slopes")
            if (not isinstance(keys, list) or not keys or not isinstance(values, list)
                    or len(keys) != len(values)
                    or (scalar and (not isinstance(slopes, list) or len(keys) != len(slopes)))):
                raise ExportAnimationError(f"Données d'images-clés incomplètes pour l'os « {name} ».")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in keys):
                raise ExportAnimationError(f"Image-clé ou valeur non finie pour l'os « {name} ».")
            if scalar and not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (*values, *slopes)):
                raise ExportAnimationError(f"Image-clé ou valeur non finie pour l'os « {name} ».")
            if quaternion and any(not isinstance(q, (list, tuple)) or len(q) != 4
                                  or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in q)
                                  or not 0.999 <= math.hypot(*q) <= 1.001 for q in values):
                raise ExportAnimationError(f"Quaternion invalide pour l'os « {name} ».")
            if any(a > b for a, b in itertools.pairwise(keys)):
                raise ExportAnimationError(f"Images-clés non triées pour l'os « {name} ».")
            # Deux valeurs à la même image peuvent décrire un saut. On les
            # conserve dans la courbe source pour évaluer les deux côtés.
            repeated_keys += sum(a == b for a, b in itertools.pairwise(keys))
        rotations = {target for target in targets if target.startswith("rotation.")}
        if (rotations and len(rotations) < 3 and "rotation" not in track["base"]
                and bones[index].get("rotation_mode", "euler") == "quaternion"):
            raise ExportAnimationError(
                f"Rotation Euler partielle sans base pour l'os quaternion « {name} ».")
        tracks[index] = track
        matched += 1
        animated += bool(track["curves"])
    if not matched or not animated:
        raise ExportAnimationError("Aucun os animé de ce clip ne correspond au modèle.")

    # Une compensation présente dans le FSKL est représentée par deux nœuds
    # TRS : T * inverse(S_parent) puis R * S. Elle reste exacte même si S_parent
    # n'est pas uniforme, tant que son échelle est constante pendant le clip.
    # Une compensation ajoutée uniquement par le clip garde l'ancien traitement
    # scalaire, compatible avec l'export progressif de plusieurs clips : dans
    # ce cas inverse(S_parent) doit être uniforme pour commuter avec R.
    factors = [1.0] * len(bones)
    pivot_scales = [None] * len(bones)
    for i, bone in enumerate(bones):
        parent, track = bone["parent"], tracks[i]
        if parent < 0 or not (bone["ssc"] or (track and track.get("ssc"))):
            continue
        parent_track = tracks[parent]
        effective = list(parent_track["base"].get("scale", bones[parent]["scale"])
                         if parent_track else bones[parent]["scale"])
        variable = False
        for curve in parent_track["curves"] if parent_track else ():
            if curve["target"].startswith("scale."):
                value = curve["values"][0]
                if any(v != value for v in curve["values"]) or any(v != 0 for v in curve["slopes"]):
                    variable = True
                    break
                effective["xyz".index(curve["target"][-1])] = value
        inverse = _inverse_scale(effective)
        if variable or inverse is None:
            raise ExportAnimationError(
                f"Compensation d'échelle de l'os « {bone['name']} » : le parent doit avoir "
                "une échelle constante et non nulle pour cet export glTF.")
        if bone["ssc"]:
            if _inverse_scale(bones[parent]["scale"]) is None:
                raise ExportAnimationError(f"Compensation d'échelle de repos impossible pour « {bone['name']} ».")
            pivot_scales[i] = inverse
        else:
            factor = _uniform_inverse(effective)
            if factor is None:
                raise ExportAnimationError(
                    f"Compensation d'échelle de l'os « {bone['name']} » présente seulement dans le clip : "
                    "le parent doit avoir une échelle uniforme et constante pour cet export glTF.")
            factors[i] = factor
    return {"skin": skin, "clip": clip, "tracks": tracks, "matched": matched,
            "missing": missing, "repeated_keys": repeated_keys, "ssc_factors": factors,
            "ssc_pivot_scales": pivot_scales}


def _quat(rotation):
    x, y, z = (value * 0.5 for value in rotation[:3])
    sx, cx, sy, cy, sz, cz = math.sin(x), math.cos(x), math.sin(y), math.cos(y), math.sin(z), math.cos(z)
    return (sx*cy*cz - cx*sy*sz, cx*sy*cz + sx*cy*sz,
            cx*cy*sz - sx*sy*cz, cx*cy*cz + sx*sy*sz)


def _rest_quat(bone):
    if bone.get("rotation_mode", "euler") == "quaternion":
        length = math.hypot(*bone["rotation"])
        return tuple(value / length for value in bone["rotation"])
    return _quat(bone["rotation"])


def _slerp(a, b, t):
    dot = sum(x*y for x, y in zip(a, b))
    if dot < 0:
        b, dot = tuple(-v for v in b), -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        q = tuple(x + t*(y-x) for x, y in zip(a, b))
        length = math.sqrt(sum(v*v for v in q))
        return tuple(v/length for v in q)
    theta = math.acos(dot)
    divisor = math.sin(theta)
    return tuple((math.sin((1-t)*theta)*x + math.sin(t*theta)*y)/divisor
                 for x, y in zip(a, b))


def _nlerp(a, b, t):
    """Interpolation linéaire normalisée, utilisée par certains importeurs."""
    if sum(x*y for x, y in zip(a, b)) < 0:
        b = tuple(-v for v in b)
    result = tuple(x + t*(y-x) for x, y in zip(a, b))
    length = math.hypot(*result)
    if not math.isfinite(length) or length < 1e-8:
        raise ExportAnimationError("Quaternion d'animation invalide.")
    return tuple(value / length for value in result)


def _angle(a, b):
    return 2 * math.acos(min(1.0, max(0.0, abs(sum(x*y for x, y in zip(a, b))))))


def _derivative_bound(curve, start, end):
    """Borne exacte de |d(angle)/d(image)| sur un sous-intervalle d'un segment."""
    frames, values, slopes = curve["frames"], curve["values"], curve["slopes"]
    midpoint = (start + end) / 2
    if len(frames) == 1 or midpoint <= frames[0] or midpoint >= frames[-1]:
        return 0.0
    i = bisect.bisect_right(frames, midpoint) - 1
    d = frames[i+1] - frames[i]
    a = 2*values[i] - 2*values[i+1] + d*(slopes[i] + slopes[i+1])
    b = -3*values[i] + 3*values[i+1] - d*(2*slopes[i] + slopes[i+1])
    c = d*slopes[i]
    left, right = (start-frames[i])/d, (end-frames[i])/d
    points = [left, right]
    if a:
        extremum = -b / (3*a)
        if left < extremum < right:
            points.append(extremum)
    return max(abs((3*a*u*u + 2*b*u + c)/d) for u in points)


def _bytes(values: array) -> bytes:
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def _add_accessor(doc, binary, values, component, kind, count, *, target=None, minimum=None, maximum=None):
    try:
        if component == 5126 and not all(math.isfinite(v) for v in values):
            raise ExportAnimationError("Valeur glTF non finie.")
        payload = _bytes(values)
    except (OverflowError, ValueError) as exc:
        raise ExportAnimationError("Valeur d'animation trop grande pour glTF.") from exc
    entry = {"bufferView": binary.add(payload, target), "componentType": component,
             "count": count, "type": kind}
    if minimum is not None:
        entry["min"] = minimum
        entry["max"] = maximum
    doc["accessors"].append(entry)
    return len(doc["accessors"]) - 1


def _times(doc, binary, frames, fps):
    try:
        values = array("f", (frame / fps for frame in frames))
    except (OverflowError, ValueError) as exc:
        raise ExportAnimationError("Temps d'animation hors limites glTF.") from exc
    if any(a >= b for a, b in itertools.pairwise(values)):
        raise ExportAnimationError("Images-clés trop proches pour les temps glTF float32.")
    return _add_accessor(doc, binary, values, 5126, "SCALAR", len(values),
                         minimum=[values[0]], maximum=[values[-1]])


def _float32_ulp(value):
    """Distance au prochain float32 positif, pour les temps et les images."""
    try:
        if not math.isfinite(value) or value < 0:
            raise ValueError
        # Les deux zéros IEEE représentent le même instant ; -0.0 est légal.
        value = 0.0 if value == 0 else value
        bits, = struct.unpack("<I", struct.pack("<f", value))
        if bits >= 0x7f7fffff:
            raise ValueError
        current, = struct.unpack("<f", struct.pack("<I", bits))
        following, = struct.unpack("<f", struct.pack("<I", bits + 1))
        return following - current
    except (OverflowError, ValueError) as exc:
        raise ExportAnimationError("Temps d'animation hors limites glTF.") from exc


def _sampling_anchors(curves, duration, fps, remaining):
    """Encadre les sauts avant échantillonnage, sans trier ni modifier la source.

    Une série de clés au même temps peut décrire des valeurs distinctes avant
    et après ce temps. glTF interdit les temps égaux dans un sampler : un saut
    est approché par une courte transition LINEAR, signalée dans le rapport.
    Les clés de même valeur mais de pentes différentes restent continues.
    """
    anchors = sorted({0.0, float(duration), *(t for c in curves.values()
                                               for t in c["frames"] if 0 < t < duration)})
    if len(anchors) > remaining:
        raise ExportAnimationError("Trop d'images-clés pour l'animation glTF.")
    before, after = set(), set()
    for curve in curves.values():
        keys, values = curve["frames"], curve["values"]
        i = 0
        while i < len(keys):
            j = i + 1
            while j < len(keys) and keys[j] == keys[i]:
                j += 1
            if j - i > 1 and values[i] != values[j - 1]:
                t = keys[i]
                # À la première clé, evaluate(t) renvoie la première valeur ;
                # pour les suivantes il renvoie la dernière (saut à droite).
                if i == 0 and 0 <= t < duration:
                    after.add(t)
                elif i > 0 and 0 < t <= duration:
                    before.add(t)
            i = j

    extra, transitions = set(), set()
    for t in before | after:
        index = bisect.bisect_left(anchors, t)
        # Garder des temps distincts aussi après la conversion en secondes
        # float32 et après l'import en images float32 dans Blender.
        window = max(JUMP_WINDOW_SECONDS * fps, 4 * _float32_ulp(t),
                     4 * fps * _float32_ulp(t / fps))
        if t in before:
            previous = anchors[index - 1]
            point = t - min(window, (t - previous) / 4)
            if not previous < point < t:
                raise ExportAnimationError("Saut d'animation trop proche d'une autre image-clé.")
            extra.add(point)
            transitions.add((point, t))
        if t in after:
            following = anchors[index + 1]
            point = t + min(window, (following - t) / 4)
            if not t < point < following:
                raise ExportAnimationError("Saut d'animation trop proche d'une autre image-clé.")
            extra.add(point)
            transitions.add((t, point))
    result = sorted(set(anchors) | extra)
    if len(result) > remaining:
        raise ExportAnimationError("Trop d'images-clés pour l'animation glTF.")
    return result, transitions


def _vector_channel(doc, binary, curves, base, frames, fps, factor, remaining):
    # Blender 4.2 ignore certaines tangentes CUBICSPLINE sur les os. Échantillonner
    # de façon adaptative préserve le mouvement dans les consommateurs LINEAR.
    anchors, transitions = _sampling_anchors(curves, frames, fps, remaining)

    def vector(frame):
        result = tuple((animation_mod.evaluate(curves[i], frame) if i in curves else base[i]) * factor
                       for i in range(3))
        if not all(math.isfinite(v) for v in result):
            raise ExportAnimationError("Translation ou échelle d'animation non finie.")
        return result

    times, samples = [anchors[0]], [vector(anchors[0])]

    def split(a, va, b, vb, depth):
        span = b - a
        needs_split = False
        for ratio in (.25, .5, .75):
            actual = vector(a + span*ratio)
            if any(abs(actual[i] - (va[i] + ratio*(vb[i]-va[i])))
                   > VECTOR_ERROR * max(1.0, abs(actual[i]), abs(va[i]), abs(vb[i])) for i in range(3)):
                needs_split = True
                break
        if needs_split:
            if depth >= MAX_SUBDIVISION or (a+b)/2 in (a, b):
                raise ExportAnimationError("Courbe trop irrégulière pour l'export glTF.")
            middle = (a+b) / 2
            vm = vector(middle)
            split(a, va, middle, vm, depth + 1)
            split(middle, vm, b, vb, depth + 1)
        else:
            if len(times) >= remaining:
                raise ExportAnimationError("Trop d'échantillons pour l'animation glTF.")
            times.append(b)
            samples.append(vb)

    for a, b in itertools.pairwise(anchors):
        if (a, b) in transitions:
            # LINEAR ne représente pas un saut instantané : limiter le mélange
            # aux deux échantillons qui l'encadrent au lieu de subdiviser sans fin.
            if len(times) >= remaining:
                raise ExportAnimationError("Trop d'échantillons pour l'animation glTF.")
            times.append(b)
            samples.append(vector(b))
        else:
            split(a, vector(a), b, vector(b), 0)
    inp = _times(doc, binary, times, fps)
    try:
        values = array("f", (v for sample in samples for v in sample))
    except (OverflowError, ValueError) as exc:
        raise ExportAnimationError("Valeur d'animation trop grande pour glTF.") from exc
    out = _add_accessor(doc, binary, values, 5126, "VEC3", len(times))
    return inp, out, "LINEAR", len(times)


def _rotation_channel(doc, binary, curves, base, frames, fps, remaining):
    anchors, transitions = _sampling_anchors(curves, frames, fps, remaining)
    if curves and sum(math.ceil(b-a) for a, b in itertools.pairwise(anchors)) + 1 > remaining:
        raise ExportAnimationError("Animation trop longue pour l'échantillonnage des rotations.")

    def quaternion(frame):
        euler = [animation_mod.evaluate(curves[i], frame) if i in curves else base[i] for i in range(3)]
        if not all(math.isfinite(v) for v in euler):
            raise ExportAnimationError("Rotation d'animation non finie.")
        return _quat(euler)

    times = [anchors[0]]
    quats = [quaternion(anchors[0])]
    if not curves and frames:
        if remaining < 2:
            raise ExportAnimationError("Trop d'échantillons pour l'animation glTF.")
        times.append(float(frames))
        quats.append(quats[0])

    def split(a, qa, b, qb, depth):
        span = b - a
        bound = sum(_derivative_bound(c, a, b) for c in curves.values()) * span
        needs_split = span > 1.0 or not math.isfinite(bound) or bound > MAX_ROTATION_STEP
        if not needs_split:
            for ratio in (.25, .5, .75):
                actual = quaternion(a + span*ratio)
                # glTF specifies SLERP; Blender 4.2 imports the quaternion
                # components as linear F-curves. Fast Euler turns need the same
                # bound for both interpretations as native quaternion curves.
                if (_angle(actual, _slerp(qa, qb, ratio)) > ROTATION_ERROR
                        or _angle(actual, _nlerp(qa, qb, ratio)) > ROTATION_ERROR):
                    needs_split = True
                    break
        if needs_split:
            if depth >= MAX_SUBDIVISION or (a+b)/2 in (a, b):
                raise ExportAnimationError("Rotation trop rapide ou irrégulière pour l'export glTF.")
            middle = (a+b) / 2
            qm = quaternion(middle)
            split(a, qa, middle, qm, depth + 1)
            split(middle, qm, b, qb, depth + 1)
        else:
            if len(times) >= remaining:
                raise ExportAnimationError("Trop d'échantillons pour l'animation glTF.")
            if sum(x*y for x, y in zip(quats[-1], qb)) < 0:
                qb = tuple(-v for v in qb)
            times.append(b)
            quats.append(qb)

    if curves:
        for a, b in itertools.pairwise(anchors):
            if (a, b) in transitions:
                if len(times) >= remaining:
                    raise ExportAnimationError("Trop d'échantillons pour l'animation glTF.")
                qb = quaternion(b)
                if sum(x*y for x, y in zip(quats[-1], qb)) < 0:
                    qb = tuple(-v for v in qb)
                times.append(b)
                quats.append(qb)
            else:
                split(a, quaternion(a), b, quaternion(b), 0)
    inp = _times(doc, binary, times, fps)
    try:
        values = array("f", (v for q in quats for v in q))
    except (OverflowError, ValueError) as exc:
        raise ExportAnimationError("Rotation trop grande pour glTF.") from exc
    out = _add_accessor(doc, binary, values, 5126, "VEC4", len(times))
    return inp, out, "LINEAR", len(times)


def _quaternion_channel(doc, binary, curve, base, frames, fps, remaining):
    # glTF LINEAR rotation uses SLERP, but Blender 4.2 imports quaternion
    # components as LINEAR F-curves (normalized linear interpolation). Subdivide
    # until both interpretations closely follow the source, without Euler
    # conversion. A jump remains a deliberately short, reported transition.
    anchors, transitions = _sampling_anchors({"q": curve} if curve else {}, frames, fps, remaining)

    def quaternion(frame):
        q = animation_mod.evaluate_quaternion(curve, frame) if curve else tuple(base)
        length = math.hypot(*q)
        if not math.isfinite(length) or length < 1e-8:
            raise ExportAnimationError("Quaternion d'animation invalide.")
        return tuple(v / length for v in q)

    times, quats = [anchors[0]], [quaternion(anchors[0])]

    def append(frame, q):
        if len(times) >= remaining:
            raise ExportAnimationError("Trop d'échantillons pour l'animation glTF.")
        if sum(x*y for x, y in zip(quats[-1], q)) < 0:
            q = tuple(-v for v in q)
        times.append(frame)
        quats.append(q)

    def split(a, qa, b, qb, depth):
        if sum(x*y for x, y in zip(qa, qb)) < 0:
            qb = tuple(-v for v in qb)
        span = b - a
        needs_split = False
        for ratio in (.25, .5, .75):
            actual = quaternion(a + span*ratio)
            if (_angle(actual, _slerp(qa, qb, ratio)) > ROTATION_ERROR
                    or _angle(actual, _nlerp(qa, qb, ratio)) > ROTATION_ERROR):
                needs_split = True
                break
        if needs_split:
            if depth >= MAX_SUBDIVISION or (a+b)/2 in (a, b):
                raise ExportAnimationError("Rotation trop rapide ou irrégulière pour l'export glTF.")
            middle = (a+b) / 2
            qm = quaternion(middle)
            split(a, qa, middle, qm, depth + 1)
            split(middle, qm, b, qb, depth + 1)
        else:
            append(b, qb)

    for a, b in itertools.pairwise(anchors):
        if (a, b) in transitions:
            append(b, quaternion(b))
        else:
            split(a, quaternion(a), b, quaternion(b), 0)
    inp = _times(doc, binary, times, fps)
    try:
        values = array("f", (v for q in quats for v in q))
    except (OverflowError, ValueError) as exc:
        raise ExportAnimationError("Rotation trop grande pour glTF.") from exc
    out = _add_accessor(doc, binary, values, 5126, "VEC4", len(times))
    return inp, out, "LINEAR", len(times)


def add_to_gltf(doc: dict, binary, meshes, binding: dict, scale: float = 1.0) -> None:
    """Ajoute un squelette et un clip, pour les appelants historiques."""
    add_animations_to_gltf(doc, binary, meshes, (binding,), scale)


def add_animations_to_gltf(doc: dict, binary, meshes, bindings, scale: float = 1.0) -> None:
    """Ajoute les clips d'un itérable à un seul skin, avec un budget commun.

    Les bindings sont consommés un par un : même un grand conteneur FSKA ne
    nécessite pas de garder tous ses clips décodés en mémoire. Une erreur de
    conversion interrompt l'export avant toute écriture du document final.
    """
    iterator = iter(bindings)
    try:
        first = next(iterator)
    except StopIteration:
        raise ExportAnimationError("Aucun clip compatible à exporter.") from None
    skin = first["skin"]
    bones = skin["bones"]
    if len(meshes) != len(skin["meshes"]) or len(doc["meshes"]) != len(meshes):
        raise ExportAnimationError("La géométrie exportée ne correspond pas au skin.")
    if not math.isfinite(scale) or scale <= 0:
        raise ExportAnimationError("Échelle de modèle invalide.")
    first_node = len(doc["nodes"])
    for bone in bones:
        doc["nodes"].append({"name": bone["name"],
                             "translation": [v * scale for v in bone["translation"]],
                             "rotation": list(_rest_quat(bone)),
                             "scale": list(bone["scale"])})
    # Split a compensated joint's local transform into two valid TRS nodes.
    # The helper is not a skin joint; original joint indices and names stay
    # unchanged. Its translation is animated instead of the joint's one.
    pivots = {}
    used_names = {node.get("name") for node in doc["nodes"] if node.get("name")}
    for i, bone in enumerate(bones):
        if bone["parent"] < 0 or not bone["ssc"]:
            continue
        rest_inverse = _inverse_scale(bones[bone["parent"]]["scale"])
        if rest_inverse is None:
            raise ExportAnimationError(f"Compensation d'échelle de repos impossible pour « {bone['name']} ».")
        base_name = f"__HookySSC_{i}_{bone['name']}"
        name, suffix = base_name, 0
        while name in used_names:
            suffix += 1
            name = f"{base_name}_{suffix}"
        used_names.add(name)
        pivots[i] = len(doc["nodes"])
        doc["nodes"].append({"name": name, "translation": [v * scale for v in bone["translation"]],
                             "scale": list(rest_inverse), "children": [first_node + i]})
        doc["nodes"][first_node + i]["translation"] = [0., 0., 0.]
    roots = []
    for i, bone in enumerate(bones):
        index = first_node + i
        if bone["parent"] < 0:
            roots.append(index)
        else:
            parent = doc["nodes"][first_node + bone["parent"]]
            parent.setdefault("children", []).append(pivots.get(i, index))
    # glTF exige un ancêtre commun même si le FSKL possède plusieurs racines.
    # Un conteneur identité ne change pas les matrices ni les indices de joints.
    if len(roots) == 1:
        skeleton_root = roots[0]
    else:
        skeleton_root = len(doc["nodes"])
        doc["nodes"].append({"name": "Skeleton", "children": roots})
    doc["scenes"][0]["nodes"].append(skeleton_root)
    inverses = array("f")
    try:
        for bone in bones:
            m = bone["inverse"]
            inverses.extend((m[0], m[4], m[8], 0., m[1], m[5], m[9], 0.,
                             m[2], m[6], m[10], 0., m[3]*scale, m[7]*scale, m[11]*scale, 1.))
    except (OverflowError, ValueError) as exc:
        raise ExportAnimationError("Matrice inverse trop grande pour glTF.") from exc
    ibm = _add_accessor(doc, binary, inverses, 5126, "MAT4", len(bones))
    doc["skins"] = [{"name": "Skin", "joints": list(range(first_node, first_node + len(bones))),
                     "skeleton": skeleton_root, "inverseBindMatrices": ibm}]
    for index, (mesh, data) in enumerate(zip(meshes, skin["meshes"])):
        count = len(mesh.positions)
        if (data["name"] != mesh.name or data["vertices"] != count
                or len(data["joints"]) != count * 16 or len(data["weights"]) != count * 8):
            raise ExportAnimationError("Les sommets du skin et du maillage ne correspondent pas.")
        joint_sets = [array("H"), array("H")]
        weight_sets = [array("f"), array("f")]
        second = False
        for vertex in range(count):
            joints = struct.unpack_from("<8H", data["joints"], vertex * 16)
            weights = data["weights"][vertex*8:vertex*8+8]
            # Regrouper les références répétées au même os : aucun poids n'est
            # perdu et les validateurs glTF refusent les joints dupliqués.
            merged = {}
            for joint, weight in zip(joints, weights):
                if weight:
                    if joint >= len(bones):
                        raise ExportAnimationError("Indice d'os hors limites.")
                    merged[joint] = merged.get(joint, 0) + weight
            total = sum(merged.values())
            if not total:
                raise ExportAnimationError("Sommet sans poids de skin.")
            pairs = list(merged.items())
            second = second or len(pairs) > 4
            pairs += [(0, 0)] * (8 - len(pairs))
            for group in range(2):
                chunk = pairs[group*4:group*4+4]
                joint_sets[group].extend(j for j, _w in chunk)
                weight_sets[group].extend(w / total for _j, w in chunk)
        primitive = doc["meshes"][index]["primitives"][0]
        for group in range(2 if second else 1):
            primitive["attributes"][f"JOINTS_{group}"] = _add_accessor(
                doc, binary, joint_sets[group], 5123, "VEC4", count, target=34962)
            primitive["attributes"][f"WEIGHTS_{group}"] = _add_accessor(
                doc, binary, weight_sets[group], 5126, "VEC4", count, target=34962)
        doc["nodes"][index]["skin"] = 0

    budget, channel_budget = MAX_SAMPLES, MAX_CHANNELS
    animations = []
    for binding in itertools.chain((first,), iterator):
        if binding["skin"] is not skin:
            raise ExportAnimationError("Les clips ne partagent pas le même squelette.")
        clip = binding["clip"]
        try:
            item, budget, channel_budget = _add_clip(
                doc, binary, bones, first_node, pivots, binding, scale, budget, channel_budget)
        except ExportAnimationError as exc:
            source = f" dans {clip['rel']} (index {clip['index']})" if "rel" in clip and "index" in clip else ""
            raise ExportAnimationError(f"Clip « {clip.get('name', 'Animation')} »{source} : {exc}") from exc
        animations.append(item)
    _isolate_animations(doc, binary, animations, budget, channel_budget)
    doc["animations"] = animations


def _isolate_animations(doc, binary, animations, budget, channel_budget):
    """Rétablit la pose de repos des canaux absents d'une action groupée.

    Certains lecteurs, notamment Blender lors d'un changement d'action,
    conservent les derniers canaux évalués plutôt que de revenir aux valeurs
    des nœuds glTF. Chaque clip doit donc aussi fixer au repos les canaux
    animés par un autre clip, sans dupliquer les animations ni leur cycle.
    Les accesseurs constants sont partagés, et les canaux supplémentaires
    consomment les mêmes budgets globaux que les autres.
    """
    if len(animations) < 2:
        return
    targets = [{(channel["target"]["node"], channel["target"]["path"])
                for channel in item["channels"]} for item in animations]
    union = set().union(*targets)
    missing = sum(len(union) - len(current) for current in targets)
    if missing > channel_budget:
        raise ExportAnimationError("Trop de canaux pour isoler les poses des animations glTF.")
    if missing > budget:
        raise ExportAnimationError("Trop d'images-clés pour isoler les poses des animations glTF.")
    if not missing:
        return
    time = _times(doc, binary, [0.0], 1.0)
    rest = {}
    for item, current in zip(animations, targets):
        for node, path in sorted(union - current):
            key = (node, path)
            if key not in rest:
                try:
                    values = array("f", doc["nodes"][node][path])
                except (OverflowError, ValueError) as exc:
                    raise ExportAnimationError("Pose de repos trop grande pour glTF.") from exc
                rest[key] = _add_accessor(doc, binary, values, 5126,
                                          "VEC4" if path == "rotation" else "VEC3", 1)
            item["samplers"].append({"input": time, "output": rest[key], "interpolation": "LINEAR"})
            item["channels"].append({"sampler": len(item["samplers"]) - 1,
                                     "target": {"node": node, "path": path}})


def _add_clip(doc, binary, bones, first_node, pivots, binding, scale, budget, channel_budget):
    clip, tracks = binding["clip"], binding["tracks"]
    samplers, channels = [], []
    for i, track in enumerate(tracks):
        track = track or {"base": {}, "curves": []}
        factor = binding["ssc_factors"][i]
        pivot = pivots.get(i)
        if pivot is not None:
            inverse = binding["ssc_pivot_scales"][i]
            if tuple(inverse) != tuple(doc["nodes"][pivot]["scale"]):
                if budget <= 0:
                    raise ExportAnimationError("Trop d'images-clés pour l'ensemble des animations glTF.")
                if channel_budget <= 0:
                    raise ExportAnimationError("Trop de canaux pour l'ensemble des animations glTF.")
                inp, out, interpolation, used = _vector_channel(
                    doc, binary, {}, inverse, clip["frames"], clip["fps"], 1.0, budget)
                budget -= used
                channel_budget -= 1
                samplers.append({"input": inp, "output": out, "interpolation": interpolation})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": pivot, "path": "scale"}})
        for path in ("translation", "rotation", "scale"):  # the joint's original channels
            curves = {"xyz".index(c["target"][-1]): c for c in track["curves"]
                      if c["target"].startswith(path + ".")}
            quat_curve = next((c for c in track["curves"] if c["target"] == "rotation"), None) if path == "rotation" else None
            compensation_changed = path == "scale" and factor != 1.0
            if not curves and not quat_curve and path not in track["base"] and not compensation_changed:
                continue
            if budget <= 0:
                raise ExportAnimationError("Trop d'images-clés pour l'ensemble des animations glTF.")
            if channel_budget <= 0:
                raise ExportAnimationError("Trop de canaux pour l'ensemble des animations glTF.")
            channel_budget -= 1
            base = track["base"].get(path, bones[i][path])
            if path == "rotation" and track.get("rotation_mode", "euler") == "quaternion":
                inp, out, interpolation, used = _quaternion_channel(
                    doc, binary, quat_curve, base, clip["frames"], clip["fps"], budget)
            elif path == "rotation":
                inp, out, interpolation, used = _rotation_channel(
                    doc, binary, curves, base, clip["frames"], clip["fps"], budget)
            else:
                inp, out, interpolation, used = _vector_channel(
                    doc, binary, curves, base, clip["frames"], clip["fps"],
                    scale if path == "translation" else factor, budget)
            budget -= used
            if budget < 0:
                raise ExportAnimationError("Trop d'images-clés pour l'ensemble des animations glTF.")
            samplers.append({"input": inp, "output": out, "interpolation": interpolation})
            node = pivot if path == "translation" and pivot is not None else first_node + i
            channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
    extras = {"sourceFPSConvention": clip["fps"], "sourceLoop": bool(clip.get("loop"))}
    if "rel" in clip and "index" in clip:
        extras.update({"sourceFile": clip["rel"], "sourceClipIndex": clip["index"],
                       "sourceClipName": clip.get("name", "Animation")})
    return ({"name": binding.get("export_name", clip.get("name", "Animation")),
             "samplers": samplers, "channels": channels, "extras": extras}, budget, channel_budget)
