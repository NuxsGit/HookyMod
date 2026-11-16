//! Décodeurs BC1–BC5 du repli intégré, en RGBA.
//!
//! Ils reproduisent les octets du repli Python de HookyMod, y compris pour
//! les images partielles, les dimensions non multiples de 4 et BC4/5 signés.
//! Le décodeur `texture2ddecoder`, s'il est présent, reste prioritaire pour
//! les formats qu'il traite déjà, afin de conserver son rendu existant.

use crate::CoreResult;

fn rgb565(value: u16) -> [u8; 3] {
    let r = ((value >> 11) & 31) as u8;
    let g = ((value >> 5) & 63) as u8;
    let b = (value & 31) as u8;
    [
        (r << 3) | (r >> 2),
        (g << 2) | (g >> 4),
        (b << 3) | (b >> 2),
    ]
}

fn color_block(block: &[u8]) -> ([[u8; 4]; 4], u32) {
    let c0 = u16::from_le_bytes([block[0], block[1]]);
    let c1 = u16::from_le_bytes([block[2], block[3]]);
    let bits = u32::from_le_bytes([block[4], block[5], block[6], block[7]]);
    let a = rgb565(c0);
    let b = rgb565(c1);
    let mut palette = [[0u8; 4]; 4];
    palette[0] = [a[0], a[1], a[2], 255];
    palette[1] = [b[0], b[1], b[2], 255];
    if c0 > c1 {
        for c in 0..3 {
            palette[2][c] = ((2 * a[c] as u16 + b[c] as u16) / 3) as u8;
            palette[3][c] = ((a[c] as u16 + 2 * b[c] as u16) / 3) as u8;
        }
        palette[2][3] = 255;
        palette[3][3] = 255;
    } else {
        for c in 0..3 {
            palette[2][c] = ((a[c] as u16 + b[c] as u16) / 2) as u8;
        }
        palette[2][3] = 255;
        // indice 3 = noir transparent, comme dans le décodeur Python.
    }
    (palette, bits)
}

fn channel_block(block: &[u8], signed: bool) -> ([u8; 8], u64) {
    let (a, b) = if signed {
        (block[0] as i8 as i32, block[1] as i8 as i32)
    } else {
        (block[0] as i32, block[1] as i32)
    };
    let mut values = [0i32; 8];
    values[0] = a;
    values[1] = b;
    if a > b {
        for (k, value) in values.iter_mut().enumerate().skip(2) {
            // Python // arrondit vers -∞ même pour les valeurs signées.
            *value = ((8 - k as i32) * a + (k as i32 - 1) * b).div_euclid(7);
        }
    } else {
        for (k, value) in values.iter_mut().enumerate().take(6).skip(2) {
            *value = ((6 - k as i32) * a + (k as i32 - 1) * b).div_euclid(5);
        }
        values[6] = if signed { -128 } else { 0 };
        values[7] = if signed { 127 } else { 255 };
    }
    let mut palette = [0u8; 8];
    for (out, value) in palette.iter_mut().zip(values) {
        *out = (value + if signed { 128 } else { 0 }).clamp(0, 255) as u8;
    }
    let mut bits = 0u64;
    for i in 0..6 {
        bits |= (block[2 + i] as u64) << (i * 8);
    }
    (palette, bits)
}

pub(crate) fn decode(
    input: &[u8],
    out: &mut [u8],
    width: u32,
    height: u32,
    format: u32,
    signed: bool,
) -> CoreResult {
    if !(1..=5).contains(&format) {
        return Err(());
    }
    let width = width as usize;
    let height = height as usize;
    let pixels = width.checked_mul(height).ok_or(())?;
    if pixels.checked_mul(4).ok_or(())? != out.len() {
        return Err(());
    }
    out.fill(0);
    // Le repli BC5 fixe l'alpha de toute l'image à 255, même si le fichier
    // est tronqué (les canaux manquants restent noirs).
    if format == 5 {
        for rgba in out.chunks_exact_mut(4) {
            rgba[3] = 255;
        }
    }
    if pixels == 0 {
        return Ok(());
    }
    let bw = width / 4 + usize::from(!width.is_multiple_of(4));
    let bh = height / 4 + usize::from(!height.is_multiple_of(4));
    let stride = if format == 1 || format == 4 { 8 } else { 16 };

    for by in 0..bh {
        for bx in 0..bw {
            let off = by
                .checked_mul(bw)
                .and_then(|x| x.checked_add(bx))
                .and_then(|x| x.checked_mul(stride))
                .ok_or(())?;
            let has_first = off.checked_add(8).is_some_and(|end| end <= input.len());
            let has_full = off
                .checked_add(stride)
                .is_some_and(|end| end <= input.len());
            if !has_first {
                break;
            }
            if format != 2 && !has_full {
                break;
            }
            let color = match format {
                1 => Some(color_block(&input[off..off + 8])),
                2 | 3 if has_full => Some(color_block(&input[off + 8..off + 16])),
                _ => None,
            };
            let channel = match format {
                3 => Some(channel_block(&input[off..off + 8], false)),
                4 | 5 if has_full => Some(channel_block(&input[off..off + 8], signed)),
                _ => None,
            };
            let green = if format == 5 && has_full {
                Some(channel_block(&input[off + 8..off + 16], signed))
            } else {
                None
            };
            let alpha_bits = if format == 2 {
                u64::from_le_bytes(input[off..off + 8].try_into().map_err(|_| ())?)
            } else {
                0
            };
            for py in 0..4 {
                let y = by * 4 + py;
                if y >= height {
                    break;
                }
                for px in 0..4 {
                    let x = bx * 4 + px;
                    if x >= width {
                        break;
                    }
                    let index = py * 4 + px;
                    let dst = (y * width + x) * 4;
                    match format {
                        1 => {
                            let (palette, bits) = color.as_ref().ok_or(())?;
                            let c = ((bits >> (2 * index)) & 3) as usize;
                            out[dst..dst + 4].copy_from_slice(&palette[c]);
                        }
                        2 => {
                            if let Some((palette, bits)) = color.as_ref() {
                                let c = ((bits >> (2 * index)) & 3) as usize;
                                out[dst..dst + 4].copy_from_slice(&palette[c]);
                            }
                            out[dst + 3] = (((alpha_bits >> (4 * index)) & 15) as u8) * 17;
                        }
                        3 => {
                            let (palette, bits) = color.as_ref().ok_or(())?;
                            let c = ((bits >> (2 * index)) & 3) as usize;
                            let (alpha, abits) = channel.as_ref().ok_or(())?;
                            let a = ((abits >> (3 * index)) & 7) as usize;
                            out[dst..dst + 4].copy_from_slice(&palette[c]);
                            out[dst + 3] = alpha[a];
                        }
                        4 => {
                            let (palette, bits) = channel.as_ref().ok_or(())?;
                            let value = palette[((bits >> (3 * index)) & 7) as usize];
                            out[dst..dst + 4].copy_from_slice(&[value, value, value, 255]);
                        }
                        5 => {
                            let (red, rbits) = channel.as_ref().ok_or(())?;
                            let (green, gbits) = green.as_ref().ok_or(())?;
                            let r = red[((rbits >> (3 * index)) & 7) as usize];
                            let g = green[((gbits >> (3 * index)) & 7) as usize];
                            out[dst..dst + 4].copy_from_slice(&[r, g, r, 255]);
                        }
                        _ => unreachable!(),
                    }
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bc1_red_and_truncated_image() {
        // RGB565 rouge, tous les indices à zéro.
        let mut out = vec![0; 4 * 4 * 4];
        decode(&[0x00, 0xf8, 0, 0, 0, 0, 0, 0], &mut out, 4, 4, 1, false).unwrap();
        assert!(out.chunks_exact(4).all(|p| p == [255, 0, 0, 255]));
        decode(&[], &mut out, 4, 4, 1, false).unwrap();
        assert!(out.iter().all(|b| *b == 0));
    }

    #[test]
    fn bc2_preserves_alpha_from_a_partial_block() {
        let mut out = vec![0; 4 * 4 * 4];
        decode(&[255; 8], &mut out, 4, 4, 2, false).unwrap();
        assert!(out.chunks_exact(4).all(|p| p == [0, 0, 0, 255]));
    }

    #[test]
    fn signed_interpolation_floors_negative_numbers() {
        let (values, _) = channel_block(&[0xfe, 0xfc, 0, 0, 0, 0, 0, 0], true);
        assert_eq!(values[2], 125); // -16 / 7 → -3, puis +128
        assert_eq!(values[7], 124);
    }
}
