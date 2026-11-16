//! Rastériseur à balayage de lignes, avec z-buffer et anticrénelage.
//!
//! La projection, le tri, le culling et les couleurs viennent du moteur HookyMod
//! existant. On porte ici le travail par pixel, sans toucher aux maillages ni
//! aux exports. L'ordre et les arrondis suivent le rastériseur Python.

use crate::CoreResult;

const TRIANGLE_BYTES: usize = 9 * 8 + 4;
// Évite une allocation native imprévue sur des dimensions corrompues. Le
// serveur possède en plus son propre budget de mémoire pour les aperçus.
const MAX_PIXELS: usize = 64 * 1024 * 1024;

#[derive(Clone, Copy)]
struct Point {
    x: f64,
    y: f64,
    z: f64,
}

fn read_f64(buf: &[u8], offset: usize) -> Result<f64, ()> {
    let bytes: [u8; 8] = buf
        .get(offset..offset + 8)
        .ok_or(())?
        .try_into()
        .map_err(|_| ())?;
    let value = f64::from_le_bytes(bytes);
    if value.is_finite() {
        Ok(value)
    } else {
        Err(())
    }
}

fn rasterize(
    triangles: &[u8],
    pixels: &mut [u8],
    width: usize,
    height: usize,
    background: [u8; 4],
) -> CoreResult {
    let count = width.checked_mul(height).ok_or(())?;
    if count > MAX_PIXELS || pixels.len() != count.checked_mul(4).ok_or(())? {
        return Err(());
    }
    let mut depth = Vec::new();
    depth.try_reserve_exact(count).map_err(|_| ())?;
    depth.resize(count, f64::NEG_INFINITY);
    for p in pixels.chunks_exact_mut(4) {
        p.copy_from_slice(&background);
    }
    for tri in triangles.chunks_exact(TRIANGLE_BYTES) {
        let mut p = [
            Point {
                x: read_f64(tri, 0)?,
                y: read_f64(tri, 8)?,
                z: read_f64(tri, 16)?,
            },
            Point {
                x: read_f64(tri, 24)?,
                y: read_f64(tri, 32)?,
                z: read_f64(tri, 40)?,
            },
            Point {
                x: read_f64(tri, 48)?,
                y: read_f64(tri, 56)?,
                z: read_f64(tri, 64)?,
            },
        ];
        let area =
            ((p[1].x - p[0].x) * (p[2].y - p[0].y)) - ((p[2].x - p[0].x) * (p[1].y - p[0].y));
        if !area.is_finite() {
            return Err(());
        }
        if area > -1e-9 && area < 1e-9 {
            continue;
        }
        let color = &tri[72..76];
        if p[0].y > p[1].y {
            p.swap(0, 1);
        }
        if p[1].y > p[2].y {
            p.swap(1, 2);
        }
        if p[0].y > p[1].y {
            p.swap(0, 1);
        }
        let (a, b, c) = (p[0], p[1], p[2]);

        // Python : max(0, -(-int((ay - .5) * 2) // 2)), avec int tronqué
        // vers zéro et division entière arrondie vers -∞.
        let doubled = (a.y - 0.5) * 2.0;
        if !doubled.is_finite() {
            return Err(());
        }
        let ay_int = doubled as i64;
        let ylo = (ay_int.div_euclid(2) + ay_int.rem_euclid(2)).max(0);
        let yhi = ((c.y - 0.5) as i64).min(height as i64 - 1);
        if ylo > yhi {
            continue;
        }
        let x_min3 = a.x.min(b.x).min(c.x);
        let x_max3 = a.x.max(b.x).max(c.x);
        let span_ac = c.y - a.y;
        if !span_ac.is_finite() {
            return Err(());
        }
        if span_ac < 1e-12 {
            continue;
        }
        let dx_ac = (c.x - a.x) / span_ac;
        let dz_ac = (c.z - a.z) / span_ac;
        let span_ab = b.y - a.y;
        let span_bc = c.y - b.y;
        let dx_ab = if span_ab > 1e-12 {
            (b.x - a.x) / span_ab
        } else {
            0.0
        };
        let dz_ab = if span_ab > 1e-12 {
            (b.z - a.z) / span_ab
        } else {
            0.0
        };
        let dx_bc = if span_bc > 1e-12 {
            (c.x - b.x) / span_bc
        } else {
            0.0
        };
        let dz_bc = if span_bc > 1e-12 {
            (c.z - b.z) / span_bc
        } else {
            0.0
        };

        for y in ylo..=yhi {
            let py = y as f64 + 0.5;
            let (mut x_left, mut z_left) = if py < b.y {
                (a.x + (py - a.y) * dx_ab, a.z + (py - a.y) * dz_ab)
            } else {
                (b.x + (py - b.y) * dx_bc, b.z + (py - b.y) * dz_bc)
            };
            let mut x_right = a.x + (py - a.y) * dx_ac;
            let mut z_right = a.z + (py - a.y) * dz_ac;
            if !x_left.is_finite() || !x_right.is_finite() {
                return Err(());
            }
            if x_left < x_min3 {
                x_left = x_min3;
            } else if x_left > x_max3 {
                x_left = x_max3;
            }
            if x_right > x_max3 {
                x_right = x_max3;
            } else if x_right < x_min3 {
                x_right = x_min3;
            }
            if x_left > x_right {
                std::mem::swap(&mut x_left, &mut x_right);
                std::mem::swap(&mut z_left, &mut z_right);
            }
            let span_width = x_right - x_left;
            if span_width < 1e-9 {
                continue;
            }
            let start_x = ((x_left + 0.999) as i64).max(0);
            let end_x = (x_right as i64).min(width as i64 - 1);
            if start_x > end_x {
                continue;
            }
            let step = (z_right - z_left) / span_width;
            let mut z = z_left + (start_x as f64 + 0.5 - x_left) * step;
            let first_slot = y as usize * width + start_x as usize;
            let last_slot = y as usize * width + end_x as usize;
            for slot in first_slot..=last_slot {
                if z > depth[slot] {
                    depth[slot] = z;
                    pixels[slot * 4..slot * 4 + 4].copy_from_slice(color);
                }
                z += step;
            }
        }
    }
    Ok(())
}

pub(crate) fn render(
    triangles: &[u8],
    out: &mut [u8],
    width: u32,
    height: u32,
    supersample: u32,
    background: [u8; 4],
) -> CoreResult {
    if !triangles.len().is_multiple_of(TRIANGLE_BYTES) || !(1..=4).contains(&supersample) {
        return Err(());
    }
    let width = width as usize;
    let height = height as usize;
    let factor = supersample as usize;
    if width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(out.len()) {
        return Err(());
    }
    let rw = width.checked_mul(factor).ok_or(())?;
    let rh = height.checked_mul(factor).ok_or(())?;
    let hi_count = rw.checked_mul(rh).ok_or(())?;
    if hi_count > MAX_PIXELS {
        return Err(());
    }
    if hi_count == 0 {
        return Ok(());
    }
    if factor == 1 {
        return rasterize(triangles, out, rw, rh, background);
    }
    let mut pixels = Vec::new();
    let size = hi_count.checked_mul(4).ok_or(())?;
    pixels.try_reserve_exact(size).map_err(|_| ())?;
    pixels.resize(size, 0u8);
    rasterize(triangles, &mut pixels, rw, rh, background)?;
    let sample_count = (factor * factor) as u32;
    for y in 0..height {
        for x in 0..width {
            let mut rgb = [0u32; 3];
            for dy in 0..factor {
                let base = ((y * factor + dy) * rw + x * factor) * 4;
                for dx in 0..factor {
                    let pos = base + dx * 4;
                    for channel in 0..3 {
                        rgb[channel] += pixels[pos + channel] as u32;
                    }
                }
            }
            let dst = (y * width + x) * 4;
            out[dst] = (rgb[0] / sample_count) as u8;
            out[dst + 1] = (rgb[1] / sample_count) as u8;
            out[dst + 2] = (rgb[2] / sample_count) as u8;
            out[dst + 3] = 255;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn triangle(points: [f64; 9], rgba: [u8; 4]) -> Vec<u8> {
        let mut data = Vec::new();
        for value in points {
            data.extend_from_slice(&value.to_le_bytes());
        }
        data.extend_from_slice(&rgba);
        data
    }

    #[test]
    fn draws_and_depth_tests() {
        let back = [1.0, 1.0, 0.0, 6.0, 1.0, 0.0, 1.0, 6.0, 0.0];
        let front = [1.0, 1.0, 1.0, 6.0, 1.0, 1.0, 1.0, 6.0, 1.0];
        let mut input = triangle(front, [200, 0, 0, 255]);
        input.extend(triangle(back, [0, 0, 200, 255]));
        let mut out = vec![0; 8 * 8 * 4];
        render(&input, &mut out, 8, 8, 1, [1, 2, 3, 4]).unwrap();
        assert_eq!(
            &out[(2 * 8 + 2) * 4..(2 * 8 + 2) * 4 + 4],
            &[200, 0, 0, 255]
        );
        assert_eq!(&out[0..4], &[1, 2, 3, 4]);
    }

    #[test]
    fn rejects_bad_lengths_and_non_finite_coordinates() {
        let mut out = vec![0; 8 * 8 * 4];
        assert!(render(&[0; 5], &mut out, 8, 8, 1, [0; 4]).is_err());
        let mut p = [0.0; 9];
        p[0] = f64::NAN;
        assert!(render(&triangle(p, [255; 4]), &mut out, 8, 8, 1, [0; 4]).is_err());
    }

    #[test]
    fn supersampling_keeps_opaque_downsample() {
        let mut out = vec![0; 2 * 2 * 4];
        render(&[], &mut out, 2, 2, 2, [1, 2, 3, 7]).unwrap();
        assert!(out.chunks_exact(4).all(|p| p == [1, 2, 3, 255]));
    }
}
