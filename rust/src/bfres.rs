//! Noyaux et lecture des tables de formes BFRES 10.x.
//!
//! La recherche des formes suit les mêmes heuristiques que le lecteur Python ;
//! les décisions géométriques et le parsing complet restent dans l'application.

use std::collections::HashSet;

use crate::CoreResult;

pub(crate) const MAX_SHAPE_METADATA: usize = 64 * 1024 * 1024;
const MAX_SHAPES: usize = 250_000;
const SHAPE_RECORD_SIZE: usize = 52;
const ATTRIBUTE_RECORD_SIZE: usize = 20;

fn read_u16(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

fn read_u32(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap())
}

fn write_u32(bytes: &mut [u8], offset: usize, value: u32) -> CoreResult {
    let slot = bytes.get_mut(offset..offset + 4).ok_or(())?;
    slot.copy_from_slice(&value.to_le_bytes());
    Ok(())
}

fn read_u64(bytes: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap())
}

#[derive(Debug)]
struct Attribute {
    name_start: u32,
    name_len: u32,
    format: u32,
    offset: u32,
    size: u32,
}

#[derive(Debug)]
struct Shape {
    offset: usize,
    stride: u64,
    buffer_offset: u32,
    vertex_offset: usize,
    vertex_count: usize,
    data_end: u64,
    flags: u32,
    attributes: Vec<Attribute>,
}

/// Reproduit `_string_at` : ASCII imprimable, 1 à 63 octets, longueur u16.
fn attribute_name(data: &[u8], reference: u32) -> Option<(usize, usize)> {
    let reference = reference as usize;
    if reference == 0 || reference.checked_add(2)? >= data.len() {
        return None;
    }
    let length = usize::from(read_u16(data, reference));
    let start = reference + 2;
    if length == 0 || length >= 64 || start.checked_add(length)? > data.len() {
        return None;
    }
    if !data[start..start + length]
        .iter()
        .all(|byte| (0x20..=0x7e).contains(byte))
    {
        return None;
    }
    Some((start, length))
}

fn is_position(name: &[u8], format: u32) -> bool {
    if name.starts_with(b"_p") {
        return true;
    }
    let has_other_kind = [b"_n", b"_t", b"_u", b"_c", b"_i", b"_w"]
        .iter()
        .any(|prefix| name.starts_with(*prefix));
    !has_other_kind && matches!(format, 0x1805 | 0x1505 | 0x1802 | 0x1502)
}

fn format_size(format: u32) -> u32 {
    match format {
        0x1805 | 0x1802 => 12,
        0x1505 | 0x1502 => 6,
        0x0b01 | 0x0901 => 2,
        _ => 4,
    }
}

fn shape_at(data: &[u8], offset: usize, base: usize) -> Result<Option<Shape>, ()> {
    // Toutes les lectures de l'en-tête FVTX sont incluses dans ces 0x50 octets.
    if offset.checked_add(0x50).is_none_or(|end| end > data.len()) {
        return Ok(None);
    }
    let Ok(array) = usize::try_from(read_u64(data, offset + 0x08)) else {
        return Ok(None);
    };
    if array == 0 || array.checked_add(16).is_none_or(|end| end >= data.len()) {
        return Ok(None);
    }
    let flags = read_u32(data, offset + 0x4c);
    let declared = (flags & 0xff) as usize;
    let buffer_offset = read_u32(data, offset + 0x48);
    let Some(vertex_offset) = base.checked_add(buffer_offset as usize) else {
        return Ok(None);
    };
    if buffer_offset == 0 || vertex_offset >= data.len() {
        return Ok(None);
    }
    let limit = if declared == 0 { 32 } else { declared.min(32) };
    let mut attributes: Vec<Attribute> = Vec::new();
    attributes.try_reserve_exact(limit).map_err(|_| ())?;
    let mut has_position = false;
    for index in 0..limit {
        let Some(entry) = array.checked_add(index * 16) else {
            break;
        };
        if entry.checked_add(16).is_none_or(|end| end > data.len()) {
            break;
        }
        let reference = read_u32(data, entry);
        let Some((name_start, name_len)) = attribute_name(data, reference) else {
            break;
        };
        let format = read_u32(data, entry + 8);
        let attribute_offset = read_u32(data, entry + 12);
        if let Some(previous) = attributes.last_mut() {
            let Some(size) = attribute_offset.checked_sub(previous.offset) else {
                return Ok(None);
            };
            if size == 0 {
                return Ok(None);
            }
            previous.size = size;
        }
        has_position |= is_position(&data[name_start..name_start + name_len], format);
        attributes.push(Attribute {
            name_start: u32::try_from(name_start).map_err(|_| ())?,
            name_len: name_len as u32,
            format,
            offset: attribute_offset,
            size: 0,
        });
    }
    if attributes.len() < 2 || !has_position || (declared != 0 && attributes.len() != declared) {
        return Ok(None);
    }
    let last = attributes.last_mut().ok_or(())?;
    last.size = format_size(last.format);
    let end = attributes
        .iter()
        .map(|attribute| u64::from(attribute.offset) + u64::from(attribute.size))
        .max()
        .ok_or(())?;
    let stride = end.div_ceil(4) * 4;
    if stride < 4 {
        return Ok(None);
    }
    Ok(Some(Shape {
        offset,
        stride,
        buffer_offset,
        vertex_offset,
        vertex_count: 0,
        data_end: read_u64(data, offset + 0x18),
        flags,
        attributes,
    }))
}

/// Recherche et valide les formes avec les heuristiques du lecteur Python.
///
/// Le résultat `FSP1` contient un nombre u32, puis pour chaque forme :
/// offset u64, stride u64, offset relatif u32, offset absolu u64, nombre de
/// sommets u64, fin de données u64, flags u32, nombre d'attributs u32 ; chaque
/// attribut contient l'offset et la longueur du nom, le format, l'offset et la
/// taille (cinq u32). Les noms restent dans l'entrée, qui est empruntée.
pub(crate) fn find_shapes(data: &[u8], base: u64) -> Result<Vec<u8>, ()> {
    let base = usize::try_from(base).map_err(|_| ())?;
    let mut shapes: Vec<Shape> = Vec::new();
    let mut seen = HashSet::new();
    let mut output_len = 8usize;
    // `FVTX` ne possède aucun préfixe égal à un suffixe ; un candidat non
    // aligné ne peut donc pas chevaucher une signature alignée. Examiner les
    // mots alignés équivaut au `find(..., start)` puis au test `% 4` de Python.
    for (index, chunk) in data.chunks_exact(4).enumerate() {
        if chunk != b"FVTX" {
            continue;
        }
        let Some(shape) = shape_at(data, index * 4, base)? else {
            continue;
        };
        if seen.contains(&shape.vertex_offset) {
            continue;
        }
        output_len = output_len
            .checked_add(SHAPE_RECORD_SIZE)
            .and_then(|size| size.checked_add(shape.attributes.len() * ATTRIBUTE_RECORD_SIZE))
            .ok_or(())?;
        if shapes.len() >= MAX_SHAPES || output_len > MAX_SHAPE_METADATA {
            return Err(());
        }
        if seen.len() == seen.capacity() {
            seen.try_reserve(1).map_err(|_| ())?;
        }
        if shapes.len() == shapes.capacity() {
            shapes.try_reserve(1).map_err(|_| ())?;
        }
        seen.insert(shape.vertex_offset);
        shapes.push(shape);
    }
    shapes.sort_unstable_by_key(|shape| shape.vertex_offset);
    for index in 0..shapes.len() {
        let next = shapes
            .get(index + 1)
            .map_or(data.len(), |shape| shape.vertex_offset);
        let available = next - shapes[index].vertex_offset;
        shapes[index].vertex_count =
            usize::try_from((available as u64) / shapes[index].stride).map_err(|_| ())?;
    }
    let mut output = Vec::new();
    output.try_reserve_exact(output_len).map_err(|_| ())?;
    output.extend_from_slice(b"FSP1");
    output.extend_from_slice(&(shapes.len() as u32).to_le_bytes());
    for shape in shapes {
        output.extend_from_slice(&(shape.offset as u64).to_le_bytes());
        output.extend_from_slice(&shape.stride.to_le_bytes());
        output.extend_from_slice(&shape.buffer_offset.to_le_bytes());
        output.extend_from_slice(&(shape.vertex_offset as u64).to_le_bytes());
        output.extend_from_slice(&(shape.vertex_count as u64).to_le_bytes());
        output.extend_from_slice(&shape.data_end.to_le_bytes());
        output.extend_from_slice(&shape.flags.to_le_bytes());
        output.extend_from_slice(&(shape.attributes.len() as u32).to_le_bytes());
        for attribute in shape.attributes {
            for value in [
                attribute.name_start,
                attribute.name_len,
                attribute.format,
                attribute.offset,
                attribute.size,
            ] {
                output.extend_from_slice(&value.to_le_bytes());
            }
        }
    }
    debug_assert_eq!(output.len(), output_len);
    Ok(output)
}

/// Sortie : nombre de suites u32, puis (forme, position en mots, triangles)
/// sous forme de trois u32 pour chaque suite. Les triangles eux-mêmes restent
/// dans l'entrée et peuvent ainsi être récupérés sans changer leur ordre.
pub(crate) fn segment_indices(
    words: &[u8],
    bounds: &[u8],
    out: &mut [u8],
    minimum: u32,
) -> CoreResult {
    if !words.len().is_multiple_of(2) || !bounds.len().is_multiple_of(4) || out.len() < 4 {
        return Err(());
    }
    let total = words.len() / 2;
    let count = bounds.len() / 4;
    if total > u32::MAX as usize || count > u32::MAX as usize {
        return Err(());
    }
    let mut ordered = Vec::new();
    ordered.try_reserve_exact(count).map_err(|_| ())?;
    for (index, value) in bounds.chunks_exact(4).enumerate() {
        ordered.push((read_u32(value, 0), index as u32));
    }
    // La référence trie par borne et conserve l'ordre des formes en cas
    // d'égalité ; l'indice d'origine sert donc explicitement de second critère.
    ordered.sort_unstable();

    let mut cursor = 0usize;
    let mut runs = 0usize;
    while cursor + 2 < total {
        let mut accepted = None;
        for &(bound, shape) in &ordered {
            if bound < 3 {
                continue;
            }
            let valid = |position: usize| {
                let a = read_u16(words, position * 2);
                let b = read_u16(words, (position + 1) * 2);
                let c = read_u16(words, (position + 2) * 2);
                u32::from(a) < bound
                    && u32::from(b) < bound
                    && u32::from(c) < bound
                    && a != b
                    && b != c
                    && a != c
            };
            if !valid(cursor) {
                continue;
            }
            let mut end = cursor + 3;
            while end + 2 < total && valid(end) {
                end += 3;
            }
            let triangles = (end - cursor) / 3;
            if triangles >= minimum as usize {
                accepted = Some((shape, end, triangles));
                break;
            }
        }
        if let Some((shape, end, triangles)) = accepted {
            let record = 4usize
                .checked_add(runs.checked_mul(12).ok_or(())?)
                .ok_or(())?;
            write_u32(out, record, shape)?;
            write_u32(out, record + 4, cursor as u32)?;
            write_u32(out, record + 8, triangles as u32)?;
            runs += 1;
            cursor = end;
        } else {
            cursor += 1;
        }
    }
    write_u32(out, 0, runs as u32)
}

/// Convertit exactement un binaire16 IEEE en f64, comme struct.unpack("e").
fn half_to_f64(value: u16) -> f64 {
    let negative = value & 0x8000 != 0;
    let exponent = (value >> 10) & 0x1f;
    let mantissa = value & 0x03ff;
    if exponent == 0 {
        // Les sous-normaux sont des multiples exacts de 2^-24.
        let magnitude = f64::from(mantissa) * (1.0 / 16_777_216.0);
        return if negative { -magnitude } else { magnitude };
    }
    let sign = u32::from(value & 0x8000) << 16;
    let bits = if exponent == 0x1f {
        sign | 0x7f80_0000 | (u32::from(mantissa) << 13)
    } else {
        sign | ((u32::from(exponent) + 112) << 23) | (u32::from(mantissa) << 13)
    };
    f64::from(f32::from_bits(bits))
}

/// Sortie : nombre de positions u32, puis trois f64 little-endian par sommet.
/// Le chemin f32 aligné reproduit la limite en enregistrements complets du
/// lecteur Python ; le chemin générique accepte aussi un dernier sommet partiel.
pub(crate) fn decode_positions(
    data: &[u8],
    out: &mut [u8],
    start: u32,
    count: u32,
    stride: u32,
    offset: u32,
    is_f32: bool,
) -> CoreResult {
    if out.len() < 4 || stride == 0 {
        return Err(());
    }
    let (start, count, stride, offset) = (
        start as usize,
        count as usize,
        stride as usize,
        offset as usize,
    );
    let component_size = if is_f32 { 4 } else { 2 };
    let record_size = component_size * 3;
    let fast = is_f32
        && stride % 4 == 0
        && offset % 4 == 0
        && stride >= 12
        && count >= 1
        && start
            .checked_add(stride)
            .is_some_and(|end| end <= data.len());
    let (limit, window_end) = if fast {
        let records = count.min((data.len() - start) / stride);
        (records, start + records * stride)
    } else {
        let first_end = start
            .checked_add(offset)
            .and_then(|n| n.checked_add(record_size));
        let records = match first_end {
            Some(end) if end <= data.len() => count.min(1 + (data.len() - end) / stride),
            _ => 0,
        };
        (records, data.len())
    };
    let mut reference = 0.0_f64;
    let mut written = 0usize;
    for index in 0..limit {
        let position = start
            .checked_add(index.checked_mul(stride).ok_or(())?)
            .and_then(|n| n.checked_add(offset))
            .ok_or(())?;
        if position.checked_add(record_size).ok_or(())? > window_end {
            break;
        }
        let mut point = [0.0; 3];
        for (component, slot) in point.iter_mut().enumerate() {
            let pos = position + component * component_size;
            *slot = if is_f32 {
                f64::from(f32::from_bits(read_u32(data, pos)))
            } else {
                half_to_f64(read_u16(data, pos))
            };
        }
        if !point.iter().all(|value| value.is_finite()) {
            break;
        }
        let scale = point[0].abs().max(point[1].abs()).max(point[2].abs());
        if index < 128 {
            if scale > reference {
                reference = scale;
            }
        } else if scale > (reference * 50.0).max(1e-4) {
            break;
        }
        let pos = 4usize
            .checked_add(written.checked_mul(24).ok_or(())?)
            .ok_or(())?;
        let destination = out.get_mut(pos..pos + 24).ok_or(())?;
        for (bytes, value) in destination.chunks_exact_mut(8).zip(point) {
            bytes.copy_from_slice(&value.to_le_bytes());
        }
        written += 1;
    }
    write_u32(out, 0, written as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shapes_follow_attribute_offsets_and_deduplicate_buffers() {
        let mut data = vec![0u8; 512];
        let fvtx = 16usize;
        data[fvtx..fvtx + 4].copy_from_slice(b"FVTX");
        data[fvtx + 8..fvtx + 16].copy_from_slice(&128u64.to_le_bytes());
        data[fvtx + 24..fvtx + 32].copy_from_slice(&512u64.to_le_bytes());
        data[fvtx + 0x48..fvtx + 0x4c].copy_from_slice(&128u32.to_le_bytes());
        data[fvtx + 0x4c..fvtx + 0x50].copy_from_slice(&2u32.to_le_bytes());
        for (at, name, format, offset) in [
            (128, 192u32, 0x1805u32, 0u32),
            (144, 200u32, 0x1201u32, 12u32),
        ] {
            data[at..at + 4].copy_from_slice(&name.to_le_bytes());
            data[at + 8..at + 12].copy_from_slice(&format.to_le_bytes());
            data[at + 12..at + 16].copy_from_slice(&offset.to_le_bytes());
        }
        data[192..197].copy_from_slice(b"\x03\0_p0");
        data[200..205].copy_from_slice(b"\x03\0_u0");
        // Un second FVTX valide vise le même tampon : le premier reste retenu.
        data.copy_within(fvtx..fvtx + 0x50, 256);
        let result = find_shapes(&data, 256).unwrap();
        assert_eq!(&result[..4], b"FSP1");
        assert_eq!(read_u32(&result, 4), 1);
        assert_eq!(read_u64(&result, 8), 16); // offset
        assert_eq!(read_u64(&result, 16), 16); // stride
        assert_eq!(read_u64(&result, 28), 384); // vertex offset
        assert_eq!(read_u64(&result, 36), 8); // nombre de sommets
        assert_eq!(read_u32(&result, 56), 2); // attributs
                                              // Des offsets d'attributs égaux rendent les deux candidats invalides.
        data[156..160].copy_from_slice(&0u32.to_le_bytes());
        assert_eq!(read_u32(&find_shapes(&data, 256).unwrap(), 4), 0);
    }

    #[test]
    fn segments_skip_padding_and_keep_first_tied_shape() {
        let input = [0u16, 0, 0, 1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 8, 0, 0];
        let words: Vec<u8> = input.iter().flat_map(|v| v.to_le_bytes()).collect();
        let bounds: Vec<u8> = [20u32, 10, 10]
            .iter()
            .flat_map(|v| v.to_le_bytes())
            .collect();
        let mut out = [0u8; 128];
        segment_indices(&words, &bounds, &mut out, 4).unwrap();
        assert_eq!(read_u32(&out, 0), 1);
        assert_eq!(
            (read_u32(&out, 4), read_u32(&out, 8), read_u32(&out, 12)),
            (1, 2, 4)
        );
    }

    #[test]
    fn positions_stop_at_nonfinite_and_runaway_values() {
        let mut input = Vec::new();
        for _ in 0..150 {
            input.extend_from_slice(&1.0_f32.to_le_bytes());
            input.extend_from_slice(&(-0.0_f32).to_le_bytes());
            input.extend_from_slice(&0.5_f32.to_le_bytes());
        }
        input.extend_from_slice(&1000.0_f32.to_le_bytes());
        input.extend_from_slice(&[0; 8]);
        let mut out = vec![0; 4 + 151 * 24];
        decode_positions(&input, &mut out, 0, 151, 12, 0, true).unwrap();
        assert_eq!(read_u32(&out, 0), 150);
        assert_eq!(
            f64::from_le_bytes(out[12..20].try_into().unwrap()).to_bits(),
            (-0.0_f64).to_bits()
        );
        input[12..16].copy_from_slice(&f32::NAN.to_le_bytes());
        decode_positions(&input, &mut out, 0, 151, 12, 0, true).unwrap();
        assert_eq!(read_u32(&out, 0), 1);
    }

    #[test]
    fn half_conversion_handles_subnormals_and_negative_zero() {
        assert_eq!(half_to_f64(0x0001), 1.0 / 16_777_216.0);
        assert_eq!(half_to_f64(0x3c00), 1.0);
        assert_eq!(half_to_f64(0x8000).to_bits(), (-0.0_f64).to_bits());
        assert!(half_to_f64(0x7c00).is_infinite());
        assert!(half_to_f64(0x7e00).is_nan());
    }
}
