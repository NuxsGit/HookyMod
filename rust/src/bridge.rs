//! Encodages internes bornés des métadonnées SARC/RESTBL pour l'ABI C.
//!
//! Les octets des entrées lues restent dans l'entrée d'origine : le résultat
//! ne contient que des offsets vérifiés. À la reconstruction, les contenus
//! immuables sont empruntés à l'appelant pendant toute l'opération.

use std::collections::HashMap;

use crate::restbl;
use crate::sarc::{Archive, Endian, Entry};

const MAX_METADATA: usize = 128 * 1024 * 1024;
const MAX_RESTBL_METADATA: usize = 64 * 1024 * 1024;
const MAX_RESTBL_OUTPUT: usize = 512 * 1024 * 1024;
pub(crate) const MAX_BUILD_INPUT: usize = 512 * 1024 * 1024;
const MAX_BUILD_OUTPUT: usize = 512 * 1024 * 1024;

fn append(out: &mut Vec<u8>, bytes: &[u8], limit: usize) -> Result<(), ()> {
    let size = out.len().checked_add(bytes.len()).ok_or(())?;
    if size > limit {
        return Err(());
    }
    out.try_reserve(bytes.len()).map_err(|_| ())?;
    out.extend_from_slice(bytes);
    Ok(())
}

fn append_u32(out: &mut Vec<u8>, value: u32) -> Result<(), ()> {
    append(out, &value.to_le_bytes(), MAX_METADATA)
}

fn append_u64(out: &mut Vec<u8>, value: u64) -> Result<(), ()> {
    append(out, &value.to_le_bytes(), MAX_METADATA)
}

/// `HSP1`, endian u8, version u16, multiplicateur u32, nombres u32 ;
/// puis (longueur nom u32, alignement u32, hash u32, début u64, taille u64,
/// nom UTF-8), puis avertissements (longueur u32, texte UTF-8).
pub fn sarc_parse(input: &[u8]) -> Result<Vec<u8>, ()> {
    let archive = Archive::parse(input).map_err(|_| ())?;
    let mut result = Vec::new();
    append(&mut result, b"HSP1", MAX_METADATA)?;
    append(
        &mut result,
        &[u8::from(archive.endian == Endian::Big)],
        MAX_METADATA,
    )?;
    append(&mut result, &archive.version.to_le_bytes(), MAX_METADATA)?;
    append_u32(&mut result, archive.hash_mult)?;
    append_u32(
        &mut result,
        u32::try_from(archive.entries.len()).map_err(|_| ())?,
    )?;
    append_u32(
        &mut result,
        u32::try_from(archive.warnings.len()).map_err(|_| ())?,
    )?;
    for entry in &archive.entries {
        let start = (entry.data.as_ptr() as usize)
            .checked_sub(input.as_ptr() as usize)
            .ok_or(())?;
        if start
            .checked_add(entry.data.len())
            .is_none_or(|end| end > input.len())
        {
            return Err(());
        }
        append_u32(
            &mut result,
            u32::try_from(entry.name.len()).map_err(|_| ())?,
        )?;
        append_u32(&mut result, u32::try_from(entry.alignment).map_err(|_| ())?)?;
        append_u32(&mut result, entry.hash)?;
        append_u64(&mut result, u64::try_from(start).map_err(|_| ())?)?;
        append_u64(
            &mut result,
            u64::try_from(entry.data.len()).map_err(|_| ())?,
        )?;
        append(&mut result, entry.name.as_bytes(), MAX_METADATA)?;
    }
    for warning in &archive.warnings {
        append_u32(&mut result, u32::try_from(warning.len()).map_err(|_| ())?)?;
        append(&mut result, warning.as_bytes(), MAX_METADATA)?;
    }
    Ok(result)
}

/// Reconstruit des entrées déjà validées par l'ABI, sans recopier les contenus
/// dans un tampon intermédiaire. Le résultat, lui, appartient à Rust.
pub fn sarc_build(
    entries: Vec<Entry<'_>>,
    endian: Endian,
    hash_mult: u32,
    version: u16,
    verify: bool,
) -> Result<Vec<u8>, ()> {
    let archive = Archive {
        entries,
        endian,
        hash_mult,
        version,
        warnings: Vec::new(),
    };
    archive
        .build_with_limit(verify, MAX_BUILD_OUTPUT)
        .map_err(|_| ())
}

/// `RSP1`, version, taille des noms, nombre d'entrées nommées, nombre
/// d'enregistrements initiaux, nombre d'entrées uniques (u32), offset de la
/// queue (u64), mode (u8). En mode 0 les paires triées sont dans l'entrée ; en
/// mode 1 suivent les paires uniques (u32, u32) dans leur ordre d'insertion.
/// Les doublons gardent la première position et la dernière valeur, comme dict.
pub fn restbl_parse(input: &[u8]) -> Result<Vec<u8>, ()> {
    let header = restbl::parse_header(input).map_err(|_| ())?;
    let records = &input[restbl::HEADER_SIZE..header.end];
    let mut previous = None;
    let mut sorted = true;
    for record in records.chunks_exact(8) {
        let key = u32::from_le_bytes(record[..4].try_into().map_err(|_| ())?);
        if previous.is_some_and(|old| key <= old) {
            sorted = false;
            break;
        }
        previous = Some(key);
    }
    let mut unique = Vec::<(u32, u32)>::new();
    if !sorted {
        let mut indexes = HashMap::<u32, usize>::new();
        for record in records.chunks_exact(8) {
            let key = u32::from_le_bytes(record[..4].try_into().map_err(|_| ())?);
            let size = u32::from_le_bytes(record[4..].try_into().map_err(|_| ())?);
            if let Some(index) = indexes.get(&key) {
                unique[*index].1 = size;
            } else {
                if unique.len() == unique.capacity() {
                    unique.try_reserve(1).map_err(|_| ())?;
                }
                if indexes.len() == indexes.capacity() {
                    indexes.try_reserve(1).map_err(|_| ())?;
                }
                indexes.insert(key, unique.len());
                unique.push((key, size));
            }
        }
    }
    let count = if sorted { header.hashed } else { unique.len() };
    let pairs_len = if sorted {
        0
    } else {
        count.checked_mul(8).ok_or(())?
    };
    let result_len = 33usize.checked_add(pairs_len).ok_or(())?;
    if result_len > MAX_RESTBL_METADATA {
        return Err(());
    }
    let mut result = Vec::new();
    result.try_reserve_exact(result_len).map_err(|_| ())?;
    result.extend_from_slice(b"RSP1");
    for value in [
        header.version,
        header.name_length,
        header.named_count,
        u32::try_from(header.hashed).map_err(|_| ())?,
        u32::try_from(count).map_err(|_| ())?,
    ] {
        result.extend_from_slice(&value.to_le_bytes());
    }
    result.extend_from_slice(&u64::try_from(header.end).map_err(|_| ())?.to_le_bytes());
    result.push(u8::from(!sorted));
    for (key, size) in unique {
        result.extend_from_slice(&key.to_le_bytes());
        result.extend_from_slice(&size.to_le_bytes());
    }
    Ok(result)
}

/// Trie les paires fournies par le dictionnaire Python et préserve la queue
/// opaque. Les clés répétées sont refusées : le dictionnaire n'en contient pas.
pub fn restbl_build(
    pairs: &[u8],
    tail: &[u8],
    version: u32,
    name_length: u32,
    named_count: u32,
) -> Result<Vec<u8>, ()> {
    if !pairs.len().is_multiple_of(8) || pairs.len() / 8 > restbl::MAX_HASHED_ENTRIES {
        return Err(());
    }
    let total = restbl::HEADER_SIZE
        .checked_add(pairs.len())
        .and_then(|n| n.checked_add(tail.len()))
        .ok_or(())?;
    if total > MAX_RESTBL_OUTPUT {
        return Err(());
    }
    let mut entries = Vec::new();
    entries.try_reserve_exact(pairs.len() / 8).map_err(|_| ())?;
    for record in pairs.chunks_exact(8) {
        let key = u32::from_le_bytes(record[..4].try_into().map_err(|_| ())?);
        let size = u32::from_le_bytes(record[4..].try_into().map_err(|_| ())?);
        entries.push((key, size));
    }
    entries.sort_unstable_by_key(|(key, _)| *key);
    if entries.windows(2).any(|window| window[0].0 == window[1].0) {
        return Err(());
    }
    let mut result = Vec::new();
    result.try_reserve_exact(total).map_err(|_| ())?;
    result.extend_from_slice(b"RESTBL");
    for value in [
        version,
        name_length,
        u32::try_from(entries.len()).map_err(|_| ())?,
        named_count,
    ] {
        result.extend_from_slice(&value.to_le_bytes());
    }
    for (key, size) in entries {
        result.extend_from_slice(&key.to_le_bytes());
        result.extend_from_slice(&size.to_le_bytes());
    }
    result.extend_from_slice(tail);
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_invalid_sarc() {
        for data in [&b""[..], &b"SARC"[..], &[0; 32][..]] {
            assert!(sarc_parse(data).is_err());
        }
    }

    #[test]
    fn restbl_preserves_insertion_order_last_value_and_opaque_tail() {
        let mut input = b"RESTBL".to_vec();
        for value in [3u32, 64, 4, 1] {
            input.extend_from_slice(&value.to_le_bytes());
        }
        for (key, size) in [(9u32, 2u32), (1, 3), (9, 5), (2, 7)] {
            input.extend_from_slice(&key.to_le_bytes());
            input.extend_from_slice(&size.to_le_bytes());
        }
        input.extend_from_slice(b"opaque\0tail");
        let metadata = restbl_parse(&input).unwrap();
        assert_eq!(&metadata[..4], b"RSP1");
        assert_eq!(metadata[32], 1);
        assert_eq!(u32::from_le_bytes(metadata[20..24].try_into().unwrap()), 3);
        let decoded: Vec<_> = metadata[33..]
            .chunks_exact(8)
            .map(|record| {
                (
                    u32::from_le_bytes(record[..4].try_into().unwrap()),
                    u32::from_le_bytes(record[4..].try_into().unwrap()),
                )
            })
            .collect();
        assert_eq!(decoded, [(9, 5), (1, 3), (2, 7)]);
        let output = restbl_build(&metadata[33..], b"opaque\0tail", 3, 64, 1).unwrap();
        let rebuilt = restbl::ResourceSizeTable::parse(&output).unwrap();
        assert_eq!(
            rebuilt.sizes.into_iter().collect::<Vec<_>>(),
            [(1, 3), (2, 7), (9, 5)]
        );
        assert_eq!(rebuilt.tail, b"opaque\0tail");
        assert_eq!(restbl_parse(&output).unwrap()[32], 0); // déjà trié et unique
        assert!(restbl_build(&[0; 7], b"", 0, 0, 0).is_err());
        assert!(restbl_build(&[0; 16], b"", 0, 0, 0).is_err()); // doublons
        assert!(restbl_parse(b"RESTBL").is_err());
    }
}
