//! Désentrelacement block-linear Tegra X1 / Orin et surfaces linéaires.

use crate::CoreResult;

#[derive(Clone, Copy)]
pub(crate) struct Layout {
    pub width: u32,
    pub height: u32,
    pub block_width: u32,
    pub block_height: u32,
    pub bytes_per_block: u32,
    pub tile_mode: u32,
    pub height_log2: i32,
    pub round_pitch: bool,
    pub orin: bool,
}

fn ceil_div(a: usize, b: usize) -> usize {
    a / b + usize::from(!a.is_multiple_of(b))
}

fn align_up(a: usize, b: usize) -> Result<usize, ()> {
    ceil_div(a, b).checked_mul(b).ok_or(())
}

fn gob_offset(x: usize, y: usize, orin: bool) -> usize {
    if orin {
        (x % 64 / 32) * 256 + (y % 8 / 4) * 128 + (x % 32 / 16) * 64 + (y % 4) * 16 + x % 16
    } else {
        (x % 64 / 32) * 256 + (y % 8 / 2) * 64 + (x % 32 / 16) * 32 + (y % 2) * 16 + x % 16
    }
}

pub(crate) fn deswizzle(input: &[u8], out: &mut [u8], p: Layout) -> CoreResult {
    if p.block_width == 0 || p.block_height == 0 || p.bytes_per_block == 0 {
        return Err(());
    }
    let width = p.width as usize;
    let height = p.height as usize;
    let bpp = p.bytes_per_block as usize;
    let wb = ceil_div(width, p.block_width as usize);
    let hb = ceil_div(height, p.block_height as usize);
    let row = wb.checked_mul(bpp).ok_or(())?;
    let needed = row.checked_mul(hb).ok_or(())?;
    if out.len() != needed {
        return Err(());
    }
    out.fill(0);
    if needed == 0 {
        return Ok(());
    }
    if p.tile_mode == 1 {
        let pitch = if p.round_pitch {
            align_up(row, 32)?
        } else {
            row
        };
        for y in 0..hb {
            let src = y.checked_mul(pitch).ok_or(())?;
            if src >= input.len() {
                break;
            }
            let count = row.min(input.len() - src);
            let dst = y * row;
            out[dst..dst + count].copy_from_slice(&input[src..src + count]);
        }
        return Ok(());
    }

    let block_height = 1usize << p.height_log2.clamp(0, 5);
    let gob_rows = 8 * block_height;
    let width_in_gobs = ceil_div(row, 64);
    let gob_stride = 512usize.checked_mul(block_height).ok_or(())?;
    let group_stride = gob_stride.checked_mul(width_in_gobs).ok_or(())?;
    let per_chunk = (16 / bpp).max(1);
    for y in 0..hb {
        let gob_y = (y / gob_rows)
            .checked_mul(group_stride)
            .and_then(|v| v.checked_add((y % gob_rows / 8) * 512))
            .ok_or(())?;
        let row_dst = y * row;
        for x in (0..wb).step_by(per_chunk) {
            let xb = x * bpp;
            let src = (xb / 64)
                .checked_mul(gob_stride)
                .and_then(|v| v.checked_add(gob_y))
                .and_then(|v| v.checked_add(gob_offset(xb, y, p.orin)))
                .ok_or(())?;
            // La boucle Python s'arrête à la première adresse hors tampon,
            // même si les données sont tronquées au milieu d'une rangée.
            if src >= input.len() {
                break;
            }
            let count = 16.min(row - xb).min(input.len() - src);
            let dst = row_dst + xb;
            out[dst..dst + count].copy_from_slice(&input[src..src + count]);
        }
    }
    Ok(())
}

/// Inverse du désentrelacement pour préparer une texture à réinjecter.
/// Une source incomplète est refusée : l'appelant utilise alors le repli Python.
pub(crate) fn swizzle(input: &[u8], out: &mut [u8], p: Layout) -> CoreResult {
    if p.block_width == 0 || p.block_height == 0 || p.bytes_per_block == 0 {
        return Err(());
    }
    let wb = ceil_div(p.width as usize, p.block_width as usize);
    let hb = ceil_div(p.height as usize, p.block_height as usize);
    let bpp = p.bytes_per_block as usize;
    let row = wb.checked_mul(bpp).ok_or(())?;
    let needed = row.checked_mul(hb).ok_or(())?;
    if input.len() < needed {
        return Err(());
    }
    if needed == 0 {
        return if out.is_empty() { Ok(()) } else { Err(()) };
    }
    if p.tile_mode == 1 {
        let pitch = if p.round_pitch {
            align_up(row, 32)?
        } else {
            row
        };
        if pitch.checked_mul(hb).ok_or(())? != out.len() {
            return Err(());
        }
        out.fill(0);
        for y in 0..hb {
            let src = y * row;
            let dst = y * pitch;
            out[dst..dst + row].copy_from_slice(&input[src..src + row]);
        }
        return Ok(());
    }
    let block_height = 1usize << p.height_log2.clamp(0, 5);
    let gob_rows = 8 * block_height;
    let surface = align_up(row, 64)?
        .checked_mul(align_up(hb, gob_rows)?)
        .ok_or(())?;
    if surface != out.len() {
        return Err(());
    }
    out.fill(0);
    let width_in_gobs = ceil_div(row, 64);
    let gob_stride = 512usize.checked_mul(block_height).ok_or(())?;
    let group_stride = gob_stride.checked_mul(width_in_gobs).ok_or(())?;
    let per_chunk = (16 / bpp).max(1);
    for y in 0..hb {
        let gob_y = (y / gob_rows)
            .checked_mul(group_stride)
            .and_then(|v| v.checked_add((y % gob_rows / 8) * 512))
            .ok_or(())?;
        let row_src = y * row;
        for x in (0..wb).step_by(per_chunk) {
            let xb = x * bpp;
            let dst = (xb / 64)
                .checked_mul(gob_stride)
                .and_then(|v| v.checked_add(gob_y))
                .and_then(|v| v.checked_add(gob_offset(xb, y, p.orin)))
                .ok_or(())?;
            let count = 16.min(row - xb);
            if count == 0 || dst >= out.len() {
                break;
            }
            let count = count.min(out.len() - dst);
            let src = row_src + xb;
            out[dst..dst + count].copy_from_slice(&input[src..src + count]);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gob_layout_matches_tegra_tables() {
        let x1: Vec<_> = (0..8).map(|y| gob_offset(0, y, false)).collect();
        let orin: Vec<_> = (0..8).map(|y| gob_offset(0, y, true)).collect();
        assert_eq!(x1, [0, 16, 64, 80, 128, 144, 192, 208]);
        assert_eq!(orin, [0, 16, 32, 48, 128, 144, 160, 176]);
        assert_eq!(gob_offset(32, 0, false), 256);
        assert_eq!(gob_offset(32, 0, true), 256);
    }

    #[test]
    fn swizzle_roundtrip_both_gpu_layouts() {
        for orin in [false, true] {
            let p = Layout {
                width: 17,
                height: 9,
                block_width: 1,
                block_height: 1,
                bytes_per_block: 4,
                tile_mode: 0,
                height_log2: 2,
                round_pitch: true,
                orin,
            };
            let input: Vec<u8> = (0..17 * 9 * 4).map(|i| (i * 37) as u8).collect();
            let mut packed = vec![0; align_up(17 * 4, 64).unwrap() * align_up(9, 8 * 4).unwrap()];
            swizzle(&input, &mut packed, p).unwrap();
            let mut output = vec![0; input.len()];
            deswizzle(&packed, &mut output, p).unwrap();
            assert_eq!(input, output);
            assert!(swizzle(&input[..10], &mut packed, p).is_err());
        }
    }

    #[test]
    fn linear_padding_and_truncation() {
        let p = Layout {
            width: 2,
            height: 2,
            block_width: 1,
            block_height: 1,
            bytes_per_block: 1,
            tile_mode: 1,
            height_log2: 0,
            round_pitch: true,
            orin: false,
        };
        let mut src = vec![0; 33];
        src[0] = 1;
        src[1] = 2;
        src[32] = 3;
        let mut out = vec![9; 4];
        deswizzle(&src, &mut out, p).unwrap();
        assert_eq!(out, [1, 2, 3, 0]);
        assert!(deswizzle(&src, &mut [0; 3], p).is_err());
    }
}
