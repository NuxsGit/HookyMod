//! Encodage des pixels et génération d'un mip BNTX, à parité avec Python.
//!
//! Les choix de couleurs, les égalités et les arrondis sont volontairement
//! identiques au code existant : un mod reconstruit ne change pas d'octets
//! simplement parce que le moteur natif est présent.

use crate::CoreResult;

type Pixel = [u8; 4];

fn rgb565(r: u8, g: u8, b: u8) -> u16 {
    ((r as u16 >> 3) << 11) | ((g as u16 >> 2) << 5) | (b as u16 >> 3)
}

fn expand565(v: u16) -> [u8; 3] {
    let r = ((v >> 11) & 31) as u8;
    let g = ((v >> 5) & 63) as u8;
    let b = (v & 31) as u8;
    [
        (r << 3) | (r >> 2),
        (g << 2) | (g >> 4),
        (b << 3) | (b >> 2),
    ]
}

fn channel_block(block: &[Pixel; 16], channel: usize) -> [u8; 8] {
    let mut low = 255u8;
    let mut high = 0u8;
    for pixel in block {
        low = low.min(pixel[channel]);
        high = high.max(pixel[channel]);
    }
    let mut out = [0u8; 8];
    out[0] = high;
    out[1] = low;
    if low == high {
        return out;
    }
    let mut palette = [0u8; 8];
    palette[0] = high;
    palette[1] = low;
    for i in 1..7 {
        let interpolated = high as f64 + ((low as i32 - high as i32) * i as i32) as f64 / 7.0;
        palette[i + 1] = if interpolated <= 0.0 {
            0
        } else if interpolated >= 255.0 {
            255
        } else {
            (interpolated + 0.5) as u8
        };
    }
    let mut bits = 0u64;
    for (i, pixel) in block.iter().enumerate() {
        let value = pixel[channel] as i32;
        let mut best = 0usize;
        let mut best_error = i32::MAX;
        for (k, entry) in palette.iter().enumerate() {
            let error = (*entry as i32 - value).abs();
            if error < best_error {
                best = k;
                best_error = error;
            }
        }
        bits |= (best as u64) << (3 * i);
    }
    out[2..8].copy_from_slice(&bits.to_le_bytes()[..6]);
    out
}

fn luminance(pixel: Pixel) -> u32 {
    pixel[0] as u32 * 299 + pixel[1] as u32 * 587 + pixel[2] as u32 * 114
}

fn color_block(block: &[Pixel; 16], allow_transparent: bool) -> [u8; 8] {
    let has_opaque = block.iter().any(|p| p[3] >= 128);
    let transparent = allow_transparent && block.iter().any(|p| p[3] < 128);
    let mut low = block[0];
    let mut high = block[0];
    let mut low_lum = u32::MAX;
    let mut high_lum = 0;
    let mut found = false;
    for pixel in block {
        if has_opaque && pixel[3] < 128 {
            continue;
        }
        let lum = luminance(*pixel);
        // `sorted(...)[0]` choisit le premier minimum et `[-1]` le dernier
        // maximum lorsque plusieurs pixels ont la même luminance.
        if !found || lum < low_lum {
            low = *pixel;
            low_lum = lum;
        }
        if !found || lum >= high_lum {
            high = *pixel;
            high_lum = lum;
        }
        found = true;
    }
    let mut c0 = rgb565(high[0], high[1], high[2]);
    let mut c1 = rgb565(low[0], low[1], low[2]);
    if c0 == c1 {
        c1 = c0.saturating_sub(1);
    }
    if c0 < c1 {
        std::mem::swap(&mut c0, &mut c1);
    }
    let mut palette = [[0u8; 3]; 4];
    palette[0] = expand565(c0);
    palette[1] = expand565(c1);
    let first = palette[0];
    let second = palette[1];
    for (c, (a, b)) in first.into_iter().zip(second).enumerate() {
        let a = a as u16;
        let b = b as u16;
        palette[2][c] = ((2 * a + b) / 3) as u8;
        if !transparent {
            palette[3][c] = ((a + 2 * b) / 3) as u8;
        }
    }
    let mut bits = 0u32;
    for (i, pixel) in block.iter().enumerate() {
        let choice = if transparent && pixel[3] < 128 {
            3
        } else {
            let mut best = 0usize;
            let mut best_error = u32::MAX;
            for (k, entry) in palette.iter().enumerate() {
                let mut error = 0u32;
                for c in 0..3 {
                    let diff = entry[c] as i32 - pixel[c] as i32;
                    error += (diff * diff) as u32;
                }
                if error < best_error {
                    best_error = error;
                    best = k;
                }
            }
            if transparent && best == 3 {
                0
            } else {
                best
            }
        };
        bits |= (choice as u32) << (2 * i);
    }
    let mut out = [0u8; 8];
    out[..2].copy_from_slice(&c0.to_le_bytes());
    out[2..4].copy_from_slice(&c1.to_le_bytes());
    out[4..].copy_from_slice(&bits.to_le_bytes());
    out
}

fn pixel_block(input: &[u8], width: usize, height: usize, bx: usize, by: usize) -> [Pixel; 16] {
    let mut block = [[0u8; 4]; 16];
    for y in 0..4 {
        for x in 0..4 {
            let sx = (bx * 4 + x).min(width - 1);
            let sy = (by * 4 + y).min(height - 1);
            let src = (sy * width + sx) * 4;
            block[y * 4 + x].copy_from_slice(&input[src..src + 4]);
        }
    }
    block
}

/// Codes 1/3/4/5 = BCn ; 10..19 = formats de pixels simples.
pub(crate) fn encode(
    input: &[u8],
    out: &mut [u8],
    width: u32,
    height: u32,
    format: u32,
) -> CoreResult {
    let width = width as usize;
    let height = height as usize;
    let pixels = width.checked_mul(height).ok_or(())?;
    if pixels.checked_mul(4).ok_or(())? > input.len() {
        return Err(());
    }
    let bw = width / 4 + usize::from(!width.is_multiple_of(4));
    let bh = height / 4 + usize::from(!height.is_multiple_of(4));
    let output_size = match format {
        1 | 4 => bw.checked_mul(bh).and_then(|n| n.checked_mul(8)),
        3 | 5 => bw.checked_mul(bh).and_then(|n| n.checked_mul(16)),
        10 => Some(pixels),
        11 | 14..=19 => pixels.checked_mul(2),
        12 | 13 => pixels.checked_mul(4),
        _ => return Err(()),
    }
    .ok_or(())?;
    if out.len() != output_size {
        return Err(());
    }
    if pixels == 0 {
        return Ok(());
    }
    if (1..=5).contains(&format) {
        let stride = if format == 1 || format == 4 { 8 } else { 16 };
        for by in 0..bh {
            for bx in 0..bw {
                let block = pixel_block(input, width, height, bx, by);
                let dst = (by * bw + bx) * stride;
                match format {
                    1 => out[dst..dst + 8].copy_from_slice(&color_block(&block, true)),
                    3 => {
                        out[dst..dst + 8].copy_from_slice(&channel_block(&block, 3));
                        out[dst + 8..dst + 16].copy_from_slice(&color_block(&block, false));
                    }
                    4 => out[dst..dst + 8].copy_from_slice(&channel_block(&block, 0)),
                    5 => {
                        out[dst..dst + 8].copy_from_slice(&channel_block(&block, 0));
                        out[dst + 8..dst + 16].copy_from_slice(&channel_block(&block, 1));
                    }
                    _ => return Err(()),
                }
            }
        }
        return Ok(());
    }
    let stride = match format {
        10 => 1,
        11 | 14..=19 => 2,
        _ => 4,
    };
    for i in 0..pixels {
        let src = i * 4;
        let dst = i * stride;
        let (r, g, b, a) = (input[src], input[src + 1], input[src + 2], input[src + 3]);
        match format {
            10 => out[dst] = r,
            11 => out[dst..dst + 2].copy_from_slice(&[r, g]),
            12 => out[dst..dst + 4].copy_from_slice(&[r, g, b, a]),
            13 => out[dst..dst + 4].copy_from_slice(&[b, g, r, a]),
            14..=19 => {
                let word = match format {
                    14 => rgb565(r, g, b),
                    15 => rgb565(b, g, r),
                    16 => {
                        ((r as u16 >> 4) << 12)
                            | ((g as u16 >> 4) << 8)
                            | ((b as u16 >> 4) << 4)
                            | (a as u16 >> 4)
                    }
                    17 => {
                        ((b as u16 >> 4) << 12)
                            | ((g as u16 >> 4) << 8)
                            | ((r as u16 >> 4) << 4)
                            | (a as u16 >> 4)
                    }
                    18 => {
                        ((r as u16 >> 3) << 11)
                            | ((g as u16 >> 3) << 6)
                            | ((b as u16 >> 3) << 1)
                            | u16::from(a >= 128)
                    }
                    19 => {
                        ((b as u16 >> 3) << 11)
                            | ((g as u16 >> 3) << 6)
                            | ((r as u16 >> 3) << 1)
                            | u16::from(a >= 128)
                    }
                    _ => return Err(()),
                };
                out[dst..dst + 2].copy_from_slice(&word.to_le_bytes());
            }
            _ => return Err(()),
        }
    }
    Ok(())
}

/// Réduction 2×2 avec réplication des bords et arrondi à l'entier supérieur
/// pour les demi-valeurs, comme `_clamp8(total / 4)` en Python.
pub(crate) fn downsample(input: &[u8], out: &mut [u8], width: u32, height: u32) -> CoreResult {
    let width = width as usize;
    let height = height as usize;
    if width == 0 || height == 0 {
        return Err(());
    }
    if width
        .checked_mul(height)
        .and_then(|n| n.checked_mul(4))
        .ok_or(())?
        > input.len()
    {
        return Err(());
    }
    let nw = (width / 2).max(1);
    let nh = (height / 2).max(1);
    if nw.checked_mul(nh).and_then(|n| n.checked_mul(4)) != Some(out.len()) {
        return Err(());
    }
    for y in 0..nh {
        for x in 0..nw {
            let mut sum = [0u32; 4];
            for dy in 0..2 {
                for dx in 0..2 {
                    let sx = (x * 2 + dx).min(width - 1);
                    let sy = (y * 2 + dy).min(height - 1);
                    let src = (sy * width + sx) * 4;
                    for c in 0..4 {
                        sum[c] += input[src + c] as u32;
                    }
                }
            }
            let dst = (y * nw + x) * 4;
            for c in 0..4 {
                out[dst + c] = ((sum[c] + 2) / 4) as u8;
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn solid_bc4_and_bc1() {
        let input = [255, 0, 0, 255].repeat(16);
        let mut bc4 = [0u8; 8];
        encode(&input, &mut bc4, 4, 4, 4).unwrap();
        assert_eq!(bc4, [255, 255, 0, 0, 0, 0, 0, 0]);
        let mut bc1 = [0u8; 8];
        encode(&input, &mut bc1, 4, 4, 1).unwrap();
        assert_eq!(u16::from_le_bytes([bc1[0], bc1[1]]), 0xf800);
        assert_eq!(&bc1[4..8], &[0, 0, 0, 0]);
    }

    #[test]
    fn mip_half_up_and_repeated_edge() {
        let input = [0, 0, 0, 0, 1, 1, 1, 1];
        let mut out = [0u8; 4];
        downsample(&input, &mut out, 2, 1).unwrap();
        assert_eq!(out, [1, 1, 1, 1]);
    }
}
