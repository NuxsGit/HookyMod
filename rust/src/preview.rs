//! Table des couleurs d'aperçu : moyenne RGBA par cellule, alpha nul ignoré.
//!
//! Les bornes, divisions entières et arrondis reproduisent `_make_sampler` en
//! Python, y compris quand l'image est plus petite que la grille.

use crate::CoreResult;

const MAX_PIXELS: usize = 64 * 1024 * 1024;
const MAX_GRID: usize = 256;

pub(crate) fn sample_table(
    rgba: &[u8],
    out: &mut [u8],
    width: u32,
    height: u32,
    size: u32,
) -> CoreResult {
    let (width, height, size) = (width as usize, height as usize, size as usize);
    if width == 0 || height == 0 || size == 0 || size > MAX_GRID {
        return Err(());
    }
    let pixels = width.checked_mul(height).ok_or(())?;
    let cells = size.checked_mul(size).ok_or(())?;
    if pixels > MAX_PIXELS
        || pixels.checked_mul(4) != Some(rgba.len())
        || cells.checked_mul(3) != Some(out.len())
    {
        return Err(());
    }
    // Les produits taille×dimension peuvent dépasser usize sur une cible 32 bits.
    let split = |index: usize, dimension: usize| -> usize {
        (index as u64 * dimension as u64 / size as u64) as usize
    };
    for y in 0..size {
        let top = split(y, height);
        let bottom = (top + 1).max(split(y + 1, height));
        for x in 0..size {
            let left = split(x, width);
            let right = (left + 1).max(split(x + 1, width));
            let (mut red, mut green, mut blue, mut count) = (0u64, 0u64, 0u64, 0u64);
            for row in top..bottom {
                let start = (row * width + left) * 4;
                let end = (row * width + right) * 4;
                for pixel in rgba[start..end].chunks_exact(4) {
                    if pixel[3] != 0 {
                        red += u64::from(pixel[0]);
                        green += u64::from(pixel[1]);
                        blue += u64::from(pixel[2]);
                        count += 1;
                    }
                }
            }
            let pos = (y * size + x) * 3;
            out[pos] = red.checked_div(count).unwrap_or(180) as u8;
            out[pos + 1] = green.checked_div(count).unwrap_or(180) as u8;
            out[pos + 2] = blue.checked_div(count).unwrap_or(180) as u8;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transparent_pixels_and_overlapping_small_cells() {
        let pixels = [255, 0, 0, 255, 0, 0, 255, 0];
        let mut one = [0; 3];
        sample_table(&pixels, &mut one, 2, 1, 1).unwrap();
        assert_eq!(one, [255, 0, 0]);
        let mut four = [0; 12];
        sample_table(&pixels, &mut four, 2, 1, 2).unwrap();
        assert_eq!(four, [255, 0, 0, 180, 180, 180, 255, 0, 0, 180, 180, 180]);
    }

    #[test]
    fn invalid_dimensions_and_lengths_are_rejected() {
        let mut target = [0; 3];
        assert!(sample_table(&[0; 4], &mut target, 0, 1, 1).is_err());
        assert!(sample_table(&[0; 4], &mut target, 1, 1, 0).is_err());
        assert!(sample_table(&[0; 4], &mut target, 1, 1, 257).is_err());
        assert!(sample_table(&[0; 3], &mut target, 1, 1, 1).is_err());
        assert!(sample_table(&[0; 4], &mut target[..2], 1, 1, 1).is_err());
        assert!(sample_table(&[], &mut target, u32::MAX, u32::MAX, 1).is_err());
    }
}
