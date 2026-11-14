//! Noyaux de lecture des tampons BFRES 10.x.
//!
//! Les tables et les décisions de validation restent dans le lecteur existant.
//! Ici nous ne faisons que reproduire son découpage en sous-maillages et sa
//! lecture bornée des positions, avant les contrôles géométriques habituels.

use crate::CoreResult;

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
