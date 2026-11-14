//! Noyaux natifs et outils autonomes HookyMod ; dépendances Rust incluses localement.
//!
//! L'ABI C est versionnée et ne stocke aucun état global. Les fonctions ne
//! conservent jamais les pointeurs d'entrée. Certaines sorties de taille
//! variable sont détenues par Rust jusqu'à leur libération explicite ; les
//! autres tampons sont alloués par l'appelant. Le traitement utilise des
//! tranches sûres.

mod bcn;
mod bfres;
mod bridge;
mod encode;
mod geometry;
pub mod nup;
mod raster;
pub mod restbl;
pub mod sarc;
mod swizzle;
pub mod zs;

use std::ffi::c_void;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::slice;

const ABI_VERSION: u32 = 5;
const MAX_INPUT: usize = 1024 * 1024 * 1024;

type CoreResult = Result<(), ()>;

fn guarded(f: impl FnOnce() -> CoreResult) -> i32 {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(Ok(())) => 0,
        Ok(Err(())) => 1,
        Err(_) => 2,
    }
}

/// # Safety
/// Les pointeurs non nuls doivent désigner des tampons valides pour leurs
/// longueurs respectives, qui ne se chevauchent pas et restent vivants pendant
/// tout l'appel. Cette condition est assurée par `hookymod/native.py`.
unsafe fn buffers<'a>(
    source: *const u8,
    source_len: usize,
    target: *mut u8,
    target_len: usize,
) -> Result<(&'a [u8], &'a mut [u8]), ()> {
    if source_len > isize::MAX as usize || target_len > isize::MAX as usize {
        return Err(());
    }
    if (source_len != 0 && source.is_null()) || (target_len != 0 && target.is_null()) {
        return Err(());
    }
    let src = if source_len == 0 {
        &[]
    } else {
        // SAFETY: les préconditions sont vérifiées par l'appelant de l'ABI.
        unsafe { slice::from_raw_parts(source, source_len) }
    };
    let dst = if target_len == 0 {
        &mut []
    } else {
        // SAFETY: idem ; la sortie ne chevauche pas la source.
        unsafe { slice::from_raw_parts_mut(target, target_len) }
    };
    Ok((src, dst))
}

#[no_mangle]
pub extern "C" fn hookymod_core_abi() -> u32 {
    ABI_VERSION
}

/// Décompresse une ou plusieurs trames zstd avec un dictionnaire facultatif.
/// Zéro = succès ; tout autre résultat demande à l'appelant de prendre son
/// repli. Le résultat ne dépasse pas 512 Mio ; le dictionnaire est limité par
/// `Dictionaries`. Le pointeur de données reste valide jusqu'à la libération
/// du handle ; aucun pointeur d'entrée n'est conservé.
///
/// # Safety
/// Les deux entrées doivent désigner des tampons valides en lecture pour leurs
/// longueurs et ne pas être mutées pendant l'appel. Les trois pointeurs de
/// sortie doivent être non nuls, valides en écriture et sans alias avec les
/// entrées ou entre eux. Le handle réussi doit être libéré exactement une fois
/// par `hookymod_core_zstd_free` ; les données ne doivent pas être modifiées.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_zstd_decompress(
    source: *const u8,
    source_len: usize,
    dictionary: *const u8,
    dictionary_len: usize,
    dictionary_id: u32,
    force_dictionary: u8,
    max_output: usize,
    result_data: *mut *const u8,
    result_len: *mut usize,
    result_handle: *mut *mut c_void,
) -> i32 {
    if result_data.is_null() || result_len.is_null() || result_handle.is_null() {
        return 1;
    }
    // SAFETY: les trois champs de sortie sont valides et distincts selon le
    // contrat de l'appelant. Les remettre à zéro même si le décodage échoue.
    unsafe {
        *result_data = ptr::null();
        *result_len = 0;
        *result_handle = ptr::null_mut();
    }
    guarded(|| {
        if source_len > MAX_INPUT
            || source_len > isize::MAX as usize
            || dictionary_len > 16 * 1024 * 1024
            || dictionary_len > isize::MAX as usize
            || max_output > zs::DEFAULT_MAX_OUTPUT
            || (source_len != 0 && source.is_null())
            || (dictionary_len != 0 && dictionary.is_null())
            || (dictionary_len == 0 && force_dictionary != 0)
        {
            return Err(());
        }
        // SAFETY: les pointeurs sont valides en lecture selon le contrat et
        // leurs longueurs sont vérifiées ; les tranches vides n'ont pas besoin
        // de pointeur non nul.
        let source = if source_len == 0 {
            &[]
        } else {
            unsafe { slice::from_raw_parts(source, source_len) }
        };
        let dictionary = if dictionary_len == 0 {
            &[]
        } else {
            unsafe { slice::from_raw_parts(dictionary, dictionary_len) }
        };
        let mut dictionaries = zs::Dictionaries::new();
        if !dictionary.is_empty() {
            dictionaries
                .add_with_id(dictionary_id, dictionary)
                .map_err(|_| ())?;
        }
        let force = (force_dictionary != 0).then_some(dictionary_id);
        let decoded = zs::decompress(source, &dictionaries, force, max_output).map_err(|_| ())?;
        let owned = Box::new(decoded);
        let data = owned.as_ptr();
        let len = owned.len();
        let handle = Box::into_raw(owned).cast::<c_void>();
        // SAFETY: les pointeurs de sortie ont été validés et sont distincts.
        unsafe {
            *result_data = data;
            *result_len = len;
            *result_handle = handle;
        }
        Ok(())
    })
}

/// Libère un tampon de sortie variable retourné par cette bibliothèque.
///
/// # Safety
/// `handle` doit être nul ou être un handle encore vivant, retourné par un
/// appel réussi de cette version de la bibliothèque ; le libérer une seule fois.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_buffer_free(handle: *mut c_void) {
    if !handle.is_null() {
        // SAFETY: le contrat garantit qu'il s'agit du Box<Vec<u8>> d'origine.
        unsafe { drop(Box::from_raw(handle.cast::<Vec<u8>>())) };
    }
}

/// Alias historique pour libérer un résultat Zstd.
///
/// # Safety
/// Même contrat que `hookymod_core_buffer_free` ; ne libérer le handle qu'une fois.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_zstd_free(handle: *mut c_void) {
    // SAFETY: les deux fonctions utilisent le même type de handle.
    unsafe { hookymod_core_buffer_free(handle) };
}

/// Lit les formes BFRES 10.x par les heuristiques existantes. Zéro = succès ;
/// toute autre valeur demande le repli Python. Les métadonnées sont bornées.
///
/// # Safety
/// L'entrée et les champs de sortie doivent respecter le contrat de
/// `hookymod_core_zstd_decompress` ; libérer le handle avec `buffer_free`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_bfres_find_shapes(
    source: *const u8,
    source_len: usize,
    buffer_base: u64,
    result_data: *mut *const u8,
    result_len: *mut usize,
    result_handle: *mut *mut c_void,
) -> i32 {
    if result_data.is_null() || result_len.is_null() || result_handle.is_null() {
        return 1;
    }
    // SAFETY: les champs de sortie sont valides et distincts selon le contrat.
    unsafe {
        *result_data = ptr::null();
        *result_len = 0;
        *result_handle = ptr::null_mut();
    }
    guarded(|| {
        if source_len > MAX_INPUT
            || source_len > isize::MAX as usize
            || (source_len != 0 && source.is_null())
        {
            return Err(());
        }
        // SAFETY: le tampon est valide et immuable pendant l'appel.
        let source = if source_len == 0 {
            &[]
        } else {
            unsafe { slice::from_raw_parts(source, source_len) }
        };
        let owned = Box::new(bfres::find_shapes(source, buffer_base)?);
        let data = owned.as_ptr();
        let len = owned.len();
        let handle = Box::into_raw(owned).cast::<c_void>();
        // SAFETY: champs de sortie distincts garantis par l'appelant.
        unsafe {
            *result_data = data;
            *result_len = len;
            *result_handle = handle;
        }
        Ok(())
    })
}

/// Lit les métadonnées SARC. Zéro = succès ; sinon repli Python requis.
/// Le résultat et son handle suivent le même contrat que la sortie Zstd.
///
/// # Safety
/// L'entrée et les champs de sortie doivent respecter le contrat de
/// `hookymod_core_zstd_decompress` ; libérer le handle avec `buffer_free`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_sarc_parse(
    source: *const u8,
    source_len: usize,
    result_data: *mut *const u8,
    result_len: *mut usize,
    result_handle: *mut *mut c_void,
) -> i32 {
    if result_data.is_null() || result_len.is_null() || result_handle.is_null() {
        return 1;
    }
    // SAFETY: les champs de sortie sont valides et distincts selon le contrat.
    unsafe {
        *result_data = ptr::null();
        *result_len = 0;
        *result_handle = ptr::null_mut();
    }
    guarded(|| {
        if source_len > MAX_INPUT
            || source_len > isize::MAX as usize
            || (source_len != 0 && source.is_null())
        {
            return Err(());
        }
        // SAFETY: le tampon est valide et immuable pendant l'appel.
        let source = if source_len == 0 {
            &[]
        } else {
            unsafe { slice::from_raw_parts(source, source_len) }
        };
        let owned = Box::new(bridge::sarc_parse(source)?);
        let data = owned.as_ptr();
        let len = owned.len();
        let handle = Box::into_raw(owned).cast::<c_void>();
        // SAFETY: champs de sortie distincts garantis par l'appelant.
        unsafe {
            *result_data = data;
            *result_len = len;
            *result_handle = handle;
        }
        Ok(())
    })
}

/// Descripteur d'une entrée SARC ; les deux tampons sont empruntés en lecture
/// uniquement pendant l'appel de `hookymod_core_sarc_build`.
#[repr(C)]
pub struct SarcBuildInput {
    pub name: *const u8,
    pub name_len: usize,
    pub data: *const u8,
    pub data_len: usize,
    pub alignment: u32,
}

/// Reconstruit une archive SARC depuis des tampons immuables, éventuellement
/// avec relecture de vérification. Zéro = succès ; sinon repli Python requis.
///
/// # Safety
/// Si non nul, `source` doit désigner `count` descripteurs correctement alignés
/// et vivants pendant l'appel. Chaque pointeur de nom et de contenu non nul
/// doit désigner un tampon valide et immuable de la longueur associée. Les
/// pointeurs nuls pour des longueurs non nulles sont refusés. Les trois champs
/// de sortie non nuls sont valides, distincts et sans alias avec les entrées.
/// Le handle retourné doit être libéré une seule fois avec `buffer_free`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_sarc_build(
    source: *const SarcBuildInput,
    count: usize,
    endian: u8,
    version: u16,
    hash_mult: u32,
    verify: u8,
    result_data: *mut *const u8,
    result_len: *mut usize,
    result_handle: *mut *mut c_void,
) -> i32 {
    if result_data.is_null() || result_len.is_null() || result_handle.is_null() {
        return 1;
    }
    // SAFETY: les champs de sortie sont valides et distincts selon le contrat.
    unsafe {
        *result_data = ptr::null();
        *result_len = 0;
        *result_handle = ptr::null_mut();
    }
    guarded(|| {
        if count == 0
            || count > u16::MAX as usize
            || source.is_null()
            || !(source as usize).is_multiple_of(std::mem::align_of::<SarcBuildInput>())
        {
            return Err(());
        }
        let endian = match endian {
            0 => sarc::Endian::Little,
            1 => sarc::Endian::Big,
            _ => return Err(()),
        };
        // SAFETY: les descripteurs sont valides, alignés et immuables d'après
        // le contrat ; le nombre borné garantit une taille inférieure à isize.
        let source = unsafe { slice::from_raw_parts(source, count) };
        let mut entries = Vec::new();
        entries.try_reserve_exact(count).map_err(|_| ())?;
        let mut input_bytes = 0usize;
        let mut name_bytes = 0usize;
        for item in source {
            name_bytes = name_bytes
                .checked_add(item.name_len)
                .and_then(|n| n.checked_add(4))
                .ok_or(())?;
            input_bytes = input_bytes
                .checked_add(item.name_len)
                .and_then(|n| n.checked_add(item.data_len))
                .and_then(|n| n.checked_add(16))
                .ok_or(())?;
            if input_bytes > bridge::MAX_BUILD_INPUT
                || name_bytes > sarc::MAX_NAMES_BYTES
                || item.name_len > isize::MAX as usize
                || item.data_len > isize::MAX as usize
                || (item.name_len != 0 && item.name.is_null())
                || (item.data_len != 0 && item.data.is_null())
            {
                return Err(());
            }
            // SAFETY: les deux plages sont valides et immuables d'après le
            // contrat ; les tranches vides ne nécessitent pas de pointeur.
            let name = if item.name_len == 0 {
                &[]
            } else {
                unsafe { slice::from_raw_parts(item.name, item.name_len) }
            };
            let data = if item.data_len == 0 {
                &[]
            } else {
                unsafe { slice::from_raw_parts(item.data, item.data_len) }
            };
            let text = std::str::from_utf8(name).map_err(|_| ())?;
            let mut name = String::new();
            name.try_reserve_exact(text.len()).map_err(|_| ())?;
            name.push_str(text);
            entries.push(sarc::Entry {
                name,
                data,
                alignment: item.alignment as usize,
                hash: 0,
            });
        }
        let owned = Box::new(bridge::sarc_build(
            entries,
            endian,
            hash_mult,
            version,
            verify != 0,
        )?);
        let data = owned.as_ptr();
        let len = owned.len();
        let handle = Box::into_raw(owned).cast::<c_void>();
        // SAFETY: champs de sortie distincts garantis par l'appelant.
        unsafe {
            *result_data = data;
            *result_len = len;
            *result_handle = handle;
        }
        Ok(())
    })
}

/// Lit les métadonnées RESTBL et préserve l'ordre de première apparition des
/// clés. Zéro = succès ; sinon l'appelant utilise son repli Python.
///
/// # Safety
/// L'entrée et les champs de sortie doivent respecter le contrat de
/// `hookymod_core_zstd_decompress` ; libérer le handle avec `buffer_free`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_restbl_parse(
    source: *const u8,
    source_len: usize,
    result_data: *mut *const u8,
    result_len: *mut usize,
    result_handle: *mut *mut c_void,
) -> i32 {
    if result_data.is_null() || result_len.is_null() || result_handle.is_null() {
        return 1;
    }
    // SAFETY: les champs de sortie sont valides et distincts selon le contrat.
    unsafe {
        *result_data = ptr::null();
        *result_len = 0;
        *result_handle = ptr::null_mut();
    }
    guarded(|| {
        if source_len > MAX_INPUT
            || source_len > isize::MAX as usize
            || (source_len != 0 && source.is_null())
        {
            return Err(());
        }
        // SAFETY: le tampon est valide et immuable pendant l'appel.
        let source = if source_len == 0 {
            &[]
        } else {
            unsafe { slice::from_raw_parts(source, source_len) }
        };
        let owned = Box::new(bridge::restbl_parse(source)?);
        let data = owned.as_ptr();
        let len = owned.len();
        let handle = Box::into_raw(owned).cast::<c_void>();
        // SAFETY: champs de sortie distincts garantis par l'appelant.
        unsafe {
            *result_data = data;
            *result_len = len;
            *result_handle = handle;
        }
        Ok(())
    })
}

/// Reconstruit une table RESTBL à partir de paires u32 little-endian et d'une
/// queue opaque, triées et bornées avant allocation du résultat.
///
/// # Safety
/// Les deux entrées et les champs de sortie doivent respecter le contrat de
/// `hookymod_core_zstd_decompress` ; libérer le handle avec `buffer_free`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_restbl_build(
    pairs: *const u8,
    pairs_len: usize,
    tail: *const u8,
    tail_len: usize,
    version: u32,
    name_length: u32,
    named_count: u32,
    result_data: *mut *const u8,
    result_len: *mut usize,
    result_handle: *mut *mut c_void,
) -> i32 {
    if result_data.is_null() || result_len.is_null() || result_handle.is_null() {
        return 1;
    }
    // SAFETY: les champs de sortie sont valides et distincts selon le contrat.
    unsafe {
        *result_data = ptr::null();
        *result_len = 0;
        *result_handle = ptr::null_mut();
    }
    guarded(|| {
        if pairs_len > restbl::MAX_HASHED_ENTRIES * 8
            || tail_len > zs::DEFAULT_MAX_OUTPUT
            || pairs_len > isize::MAX as usize
            || tail_len > isize::MAX as usize
            || (pairs_len != 0 && pairs.is_null())
            || (tail_len != 0 && tail.is_null())
        {
            return Err(());
        }
        // SAFETY: les deux tampons sont valides et immuables pendant l'appel.
        let pairs = if pairs_len == 0 {
            &[]
        } else {
            unsafe { slice::from_raw_parts(pairs, pairs_len) }
        };
        let tail = if tail_len == 0 {
            &[]
        } else {
            unsafe { slice::from_raw_parts(tail, tail_len) }
        };
        let owned = Box::new(bridge::restbl_build(
            pairs,
            tail,
            version,
            name_length,
            named_count,
        )?);
        let data = owned.as_ptr();
        let len = owned.len();
        let handle = Box::into_raw(owned).cast::<c_void>();
        // SAFETY: champs de sortie distincts garantis par l'appelant.
        unsafe {
            *result_data = data;
            *result_len = len;
            *result_handle = handle;
        }
        Ok(())
    })
}

/// Désentrelace une surface BNTX. Zéro = succès, autre valeur = repli requis.
///
/// # Safety
/// Les deux tampons doivent respecter le contrat de `buffers`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_deswizzle(
    source: *const u8,
    source_len: usize,
    target: *mut u8,
    target_len: usize,
    width: u32,
    height: u32,
    block_width: u32,
    block_height: u32,
    bytes_per_block: u32,
    tile_mode: u32,
    height_log2: i32,
    round_pitch: u8,
    orin: u8,
) -> i32 {
    guarded(|| {
        let (src, dst) = unsafe { buffers(source, source_len, target, target_len)? };
        swizzle::deswizzle(
            src,
            dst,
            swizzle::Layout {
                width,
                height,
                block_width,
                block_height,
                bytes_per_block,
                tile_mode,
                height_log2,
                round_pitch: round_pitch != 0,
                orin: orin != 0,
            },
        )
    })
}

/// Prépare une surface linéaire à son écriture dans un BNTX.
///
/// # Safety
/// Les deux tampons doivent respecter le contrat de `buffers`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_swizzle(
    source: *const u8,
    source_len: usize,
    target: *mut u8,
    target_len: usize,
    width: u32,
    height: u32,
    block_width: u32,
    block_height: u32,
    bytes_per_block: u32,
    tile_mode: u32,
    height_log2: i32,
    round_pitch: u8,
    orin: u8,
) -> i32 {
    guarded(|| {
        let (src, dst) = unsafe { buffers(source, source_len, target, target_len)? };
        swizzle::swizzle(
            src,
            dst,
            swizzle::Layout {
                width,
                height,
                block_width,
                block_height,
                bytes_per_block,
                tile_mode,
                height_log2,
                round_pitch: round_pitch != 0,
                orin: orin != 0,
            },
        )
    })
}

/// Décode les formats BC1 à BC5 en RGBA. BC6/BC7/ASTC restent au décodeur C.
///
/// # Safety
/// Les deux tampons doivent respecter le contrat de `buffers`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_decode_bc(
    source: *const u8,
    source_len: usize,
    target: *mut u8,
    target_len: usize,
    width: u32,
    height: u32,
    format: u32,
    signed: u8,
) -> i32 {
    guarded(|| {
        let (src, dst) = unsafe { buffers(source, source_len, target, target_len)? };
        bcn::decode(src, dst, width, height, format, signed != 0)
    })
}

/// Encode des pixels RGBA dans l'un des formats BNTX déjà écrits par HookyMod.
///
/// # Safety
/// Les deux tampons doivent respecter le contrat de `buffers`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_encode_pixels(
    source: *const u8,
    source_len: usize,
    target: *mut u8,
    target_len: usize,
    width: u32,
    height: u32,
    format: u32,
) -> i32 {
    guarded(|| {
        let (src, dst) = unsafe { buffers(source, source_len, target, target_len)? };
        encode::encode(src, dst, width, height, format)
    })
}

/// Produit un niveau de mip RGBA par réduction 2×2.
///
/// # Safety
/// Les deux tampons doivent respecter le contrat de `buffers`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_downsample_mip(
    source: *const u8,
    source_len: usize,
    target: *mut u8,
    target_len: usize,
    width: u32,
    height: u32,
) -> i32 {
    guarded(|| {
        let (src, dst) = unsafe { buffers(source, source_len, target, target_len)? };
        encode::downsample(src, dst, width, height)
    })
}

/// Découpe un tampon d'indices BFRES en suites et retourne leurs emplacements.
///
/// # Safety
/// Les trois tampons doivent être valides et non chevauchants pendant l'appel.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_segment_indices(
    words: *const u8,
    words_len: usize,
    bounds: *const u8,
    bounds_len: usize,
    target: *mut u8,
    target_len: usize,
    minimum: u32,
) -> i32 {
    guarded(|| {
        let (words, out) = unsafe { buffers(words, words_len, target, target_len)? };
        if bounds_len > isize::MAX as usize || (bounds_len != 0 && bounds.is_null()) {
            return Err(());
        }
        let bounds = if bounds_len == 0 {
            &[]
        } else {
            // SAFETY: le contrat de l'ABI garantit la validité et l'absence d'alias.
            unsafe { slice::from_raw_parts(bounds, bounds_len) }
        };
        bfres::segment_indices(words, bounds, out, minimum)
    })
}

/// Lit des positions BFRES f32/f16 avec les mêmes garde-fous que le lecteur.
///
/// # Safety
/// Les deux tampons doivent respecter le contrat de `buffers`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_decode_positions(
    source: *const u8,
    source_len: usize,
    target: *mut u8,
    target_len: usize,
    start: u32,
    count: u32,
    stride: u32,
    offset: u32,
    is_f32: u8,
) -> i32 {
    guarded(|| {
        let (data, out) = unsafe { buffers(source, source_len, target, target_len)? };
        bfres::decode_positions(data, out, start, count, stride, offset, is_f32 != 0)
    })
}

/// Marque les triangles dont les trois arêtes restent sous le seuil BFRES.
/// Positions = 3 f64 et indices = 3 i64 little-endian ; un octet de sortie
/// par triangle (0 écarté, 1 conservé).
///
/// # Safety
/// Les trois tampons doivent être valides et non chevauchants pendant l'appel.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_filter_triangles(
    positions: *const u8,
    positions_len: usize,
    triangles: *const u8,
    triangles_len: usize,
    target: *mut u8,
    target_len: usize,
    limit2: f64,
) -> i32 {
    guarded(|| {
        let (points, mask) = unsafe { buffers(positions, positions_len, target, target_len)? };
        if triangles_len > isize::MAX as usize || (triangles_len != 0 && triangles.is_null()) {
            return Err(());
        }
        let faces = if triangles_len == 0 {
            &[]
        } else {
            // SAFETY: le contrat de l'ABI garantit la validité et l'absence d'alias.
            unsafe { slice::from_raw_parts(triangles, triangles_len) }
        };
        geometry::filter(points, faces, mask, limit2)
    })
}

/// Rastérise des triangles déjà projetés et réduit l'image si nécessaire.
/// Un enregistrement = 9 f64 little-endian suivis de 4 octets RGBA.
///
/// # Safety
/// Les deux tampons doivent respecter le contrat de `buffers`.
#[no_mangle]
pub unsafe extern "C" fn hookymod_core_rasterize(
    source: *const u8,
    source_len: usize,
    target: *mut u8,
    target_len: usize,
    width: u32,
    height: u32,
    supersample: u32,
    background_rgba: u32,
) -> i32 {
    guarded(|| {
        let (src, dst) = unsafe { buffers(source, source_len, target, target_len)? };
        raster::render(
            src,
            dst,
            width,
            height,
            supersample,
            background_rgba.to_le_bytes(),
        )
    })
}

#[cfg(test)]
mod ffi_tests {
    use super::*;

    #[test]
    fn bfres_shape_ffi_owns_metadata_and_clears_failures() {
        let mut data = ptr::null();
        let mut length = 0usize;
        let mut handle: *mut c_void = ptr::null_mut();
        // SAFETY: une entrée vide peut être nulle ; les champs sont distincts.
        let result = unsafe {
            hookymod_core_bfres_find_shapes(ptr::null(), 0, 0, &mut data, &mut length, &mut handle)
        };
        assert_eq!(result, 0);
        assert!(!handle.is_null());
        // SAFETY: le handle maintient les octets vivants jusqu'à libération.
        assert_eq!(
            unsafe { slice::from_raw_parts(data, length) },
            b"FSP1\0\0\0\0"
        );
        // SAFETY: le handle retourné n'est libéré qu'une fois.
        unsafe { hookymod_core_buffer_free(handle) };
        data = ptr::dangling();
        length = 42;
        handle = ptr::dangling_mut::<c_void>();
        // SAFETY: les sorties sont valides ; l'entrée nulle non vide est refusée.
        let result = unsafe {
            hookymod_core_bfres_find_shapes(ptr::null(), 1, 0, &mut data, &mut length, &mut handle)
        };
        assert_ne!(result, 0);
        assert!(data.is_null());
        assert_eq!(length, 0);
        assert!(handle.is_null());
    }

    #[test]
    fn zstd_result_has_explicit_lifetime_and_failures_clear_outputs() {
        let frame = zs::compress_without_dictionary(b"bonjour en Rust").unwrap();
        let mut data = ptr::null();
        let mut length = 0usize;
        let mut handle: *mut c_void = ptr::null_mut();
        // SAFETY: toutes les entrées et sorties sont valides et distinctes.
        let result = unsafe {
            hookymod_core_zstd_decompress(
                frame.as_ptr(),
                frame.len(),
                ptr::null(),
                0,
                0,
                0,
                1024,
                &mut data,
                &mut length,
                &mut handle,
            )
        };
        assert_eq!(result, 0);
        assert!(!handle.is_null());
        // SAFETY: le handle maintient la sortie vivante jusqu'à sa libération.
        assert_eq!(
            unsafe { slice::from_raw_parts(data, length) },
            b"bonjour en Rust"
        );
        // SAFETY: libération exactement une fois du handle retourné.
        unsafe { hookymod_core_zstd_free(handle) };

        data = ptr::dangling();
        length = 42;
        handle = ptr::dangling_mut::<c_void>();
        // SAFETY: les champs de sortie sont valides ; la limite provoque un refus.
        let result = unsafe {
            hookymod_core_zstd_decompress(
                frame.as_ptr(),
                frame.len(),
                ptr::null(),
                0,
                0,
                0,
                1,
                &mut data,
                &mut length,
                &mut handle,
            )
        };
        assert_ne!(result, 0);
        assert!(data.is_null());
        assert_eq!(length, 0);
        assert!(handle.is_null());
    }

    #[test]
    fn restbl_ffi_preserves_tail_and_clears_results_on_refusal() {
        let pairs = [9u32, 4, 1, 3]
            .into_iter()
            .flat_map(u32::to_le_bytes)
            .collect::<Vec<_>>();
        let tail = b"nomme\0\xff";
        let mut data = ptr::null();
        let mut length = 0usize;
        let mut handle: *mut c_void = ptr::null_mut();
        // SAFETY: les entrées immuables et les champs de sortie sont valides.
        let result = unsafe {
            hookymod_core_restbl_build(
                pairs.as_ptr(),
                pairs.len(),
                tail.as_ptr(),
                tail.len(),
                3,
                64,
                1,
                &mut data,
                &mut length,
                &mut handle,
            )
        };
        assert_eq!(result, 0);
        assert!(!handle.is_null());
        // SAFETY: le handle maintient les octets de sortie vivants.
        let bytes = unsafe { slice::from_raw_parts(data, length) };
        let table = restbl::ResourceSizeTable::parse(bytes).unwrap();
        assert_eq!(
            table.sizes.into_iter().collect::<Vec<_>>(),
            [(1, 3), (9, 4)]
        );
        assert_eq!(table.tail, tail);
        // SAFETY: ce handle vivant est libéré une seule fois.
        unsafe { hookymod_core_buffer_free(handle) };
        data = ptr::dangling();
        length = 42;
        handle = ptr::dangling_mut::<c_void>();
        // SAFETY: les champs de sortie sont valides ; une entrée nulle est refusée.
        let result = unsafe {
            hookymod_core_restbl_build(
                ptr::null(),
                8,
                tail.as_ptr(),
                tail.len(),
                3,
                64,
                1,
                &mut data,
                &mut length,
                &mut handle,
            )
        };
        assert_ne!(result, 0);
        assert!(data.is_null());
        assert_eq!(length, 0);
        assert!(handle.is_null());
    }

    #[test]
    fn sarc_ffi_borrows_entries_and_clears_results_on_refusal() {
        let name = "dossier/été.txt".as_bytes();
        let content = b"\0abc\0\xff";
        let input = SarcBuildInput {
            name: name.as_ptr(),
            name_len: name.len(),
            data: content.as_ptr(),
            data_len: content.len(),
            alignment: 3,
        };
        let mut data = ptr::null();
        let mut length = 0usize;
        let mut handle: *mut c_void = ptr::null_mut();
        // SAFETY: le descripteur, ses deux tampons et les sorties sont valides.
        let result = unsafe {
            hookymod_core_sarc_build(
                &input,
                1,
                1,
                0x100,
                0x65,
                1,
                &mut data,
                &mut length,
                &mut handle,
            )
        };
        assert_eq!(result, 0);
        assert!(!handle.is_null());
        // SAFETY: le handle conserve la sortie jusqu'à sa libération.
        let bytes = unsafe { slice::from_raw_parts(data, length) };
        let archive = sarc::Archive::parse(bytes).unwrap();
        assert_eq!(archive.endian, sarc::Endian::Big);
        assert_eq!(archive.entries[0].name, "dossier/été.txt");
        assert_eq!(archive.entries[0].data, content);
        // SAFETY: handle retourné par l'appel précédent, libéré une seule fois.
        unsafe { hookymod_core_zstd_free(handle) };

        for broken in [
            SarcBuildInput {
                name: ptr::null(),
                name_len: 1,
                ..input
            },
            SarcBuildInput {
                data: ptr::null(),
                data_len: 1,
                ..input
            },
            SarcBuildInput {
                name: b"\xff".as_ptr(),
                name_len: 1,
                ..input
            },
            SarcBuildInput {
                data: ptr::null(),
                data_len: bridge::MAX_BUILD_INPUT + 1,
                ..input
            },
        ] {
            data = ptr::dangling();
            length = 42;
            handle = ptr::dangling_mut::<c_void>();
            // SAFETY: les pointeurs non nuls et les champs de sortie sont valides ;
            // les pointeurs nuls non vides et les tailles excessives sont refusés.
            let result = unsafe {
                hookymod_core_sarc_build(
                    &broken,
                    1,
                    0,
                    0x100,
                    0x65,
                    1,
                    &mut data,
                    &mut length,
                    &mut handle,
                )
            };
            assert_ne!(result, 0);
            assert!(data.is_null());
            assert_eq!(length, 0);
            assert!(handle.is_null());
        }
        // SAFETY: les champs de sortie sont valides ; un pointeur nul est refusé.
        assert_ne!(
            unsafe {
                hookymod_core_sarc_parse(ptr::null(), 1, &mut data, &mut length, &mut handle)
            },
            0
        );
        assert!(data.is_null());
        assert_eq!(length, 0);
        assert!(handle.is_null());
    }
}
