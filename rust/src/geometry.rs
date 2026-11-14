//! Filtre des triangles aberrants utilisé par le lecteur BFRES 10.x.
//!
//! Les seuils et leur estimation restent dans le lecteur Python : ce noyau
//! calcule seulement le même masque d'arêtes. Il ne construit ni n'invente de
//! géométrie ; les indices hors limites sont refusés exactement comme avant.

use crate::CoreResult;

const RECORD: usize = 24; // trois f64, ou trois i64 little-endian

fn read_i64(data: &[u8], offset: usize) -> i64 {
    i64::from_le_bytes(data[offset..offset + 8].try_into().unwrap())
}

fn point(positions: &[u8], index: i64) -> Option<[f64; 3]> {
    let count = positions.len() / RECORD;
    let resolved = if index < 0 {
        (count as i128) + (index as i128)
    } else {
        index as i128
    };
    if resolved < 0 || resolved >= count as i128 {
        return None;
    }
    let start = resolved as usize * RECORD;
    let mut p = [0.0; 3];
    for (i, value) in p.iter_mut().enumerate() {
        *value = f64::from_le_bytes(
            positions[start + i * 8..start + i * 8 + 8]
                .try_into()
                .ok()?,
        );
    }
    Some(p)
}

fn dist2(a: [f64; 3], b: [f64; 3]) -> f64 {
    let dx = a[0] - b[0];
    let dy = a[1] - b[1];
    let dz = a[2] - b[2];
    dx * dx + dy * dy + dz * dz
}

pub(crate) fn filter(
    positions: &[u8],
    triangles: &[u8],
    mask: &mut [u8],
    limit2: f64,
) -> CoreResult {
    if !positions.len().is_multiple_of(RECORD)
        || !triangles.len().is_multiple_of(RECORD)
        || triangles.len() / RECORD != mask.len()
    {
        return Err(());
    }
    for (tri, keep) in triangles.chunks_exact(RECORD).zip(mask.iter_mut()) {
        let pa = point(positions, read_i64(tri, 0));
        let pb = point(positions, read_i64(tri, 8));
        let pc = point(positions, read_i64(tri, 16));
        *keep = match (pa, pb, pc) {
            (Some(a), Some(b), Some(c)) => {
                u8::from(!(dist2(a, b) > limit2 || dist2(b, c) > limit2 || dist2(a, c) > limit2))
            }
            _ => 0,
        };
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn floats(points: &[[f64; 3]]) -> Vec<u8> {
        points
            .iter()
            .flat_map(|p| p.iter())
            .flat_map(|v| v.to_le_bytes())
            .collect()
    }
    fn indices(faces: &[[i64; 3]]) -> Vec<u8> {
        faces
            .iter()
            .flat_map(|f| f.iter())
            .flat_map(|v| v.to_le_bytes())
            .collect()
    }

    #[test]
    fn bounds_negative_indices_and_exact_threshold() {
        let p = floats(&[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [5.0, 0.0, 0.0]]);
        let t = indices(&[[0, 1, -3], [0, 1, 2], [0, 1, 3], [0, 1, -4]]);
        let mut out = [0; 4];
        filter(&p, &t, &mut out, 1.0).unwrap();
        assert_eq!(out, [1, 0, 0, 0]);
    }

    #[test]
    fn rejects_invalid_buffer_lengths() {
        assert!(filter(&[0; 2], &[], &mut [], 0.0).is_err());
        assert!(filter(&[], &[0; 24], &mut [], 0.0).is_err());
    }
}
