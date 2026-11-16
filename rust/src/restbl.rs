//! Table RESTBL : lecture, CRC-32 des chemins et reconstruction autonome.
//!
//! Les entrées nommées inconnues sont conservées octet pour octet à la fin du
//! fichier. Les entrées hachées sont triées avant l'écriture comme en Python.

use std::collections::BTreeMap;
use std::fmt;

pub(crate) const HEADER_SIZE: usize = 22;
// Une table réelle compte environ 220 000 entrées ; borner aussi le coût des
// structures en mémoire, plus élevé que les 8 octets par entrée sur disque.
pub(crate) const MAX_HASHED_ENTRIES: usize = 4_000_000;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RestblError(pub String);

impl fmt::Display for RestblError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RestblError {}

fn read_u32(data: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(data[offset..offset + 4].try_into().unwrap())
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Header {
    pub version: u32,
    pub name_length: u32,
    pub hashed: usize,
    pub named_count: u32,
    pub end: usize,
}

/// Valide l'en-tête et les bornes des entrées sans modifier les données.
pub(crate) fn parse_header(data: &[u8]) -> Result<Header, RestblError> {
    if data.len() < HEADER_SIZE || !data.starts_with(b"RESTBL") {
        return Err(RestblError("Ce n'est pas une table RESTBL valide.".into()));
    }
    let version = read_u32(data, 6);
    let name_length = read_u32(data, 10);
    let hashed = read_u32(data, 14) as usize;
    let named_count = read_u32(data, 18);
    if hashed > MAX_HASHED_ENTRIES {
        return Err(RestblError(
            "Trop d'entrées hachées RESTBL (limite : 4 millions).".into(),
        ));
    }
    let end = hashed
        .checked_mul(8)
        .and_then(|n| HEADER_SIZE.checked_add(n))
        .ok_or_else(|| RestblError("Table RESTBL trop grande.".into()))?;
    if end > data.len() {
        return Err(RestblError(
            "Table RESTBL tronquée (entrées hachées).".into(),
        ));
    }
    Ok(Header {
        version,
        name_length,
        hashed,
        named_count,
        end,
    })
}

/// Clé du jeu : CRC-32 du chemin avec séparateurs normalisés et suffixes .zs ôtés.
pub fn key_for(path: &str) -> u32 {
    let mut path = path.replace('\\', "/");
    while path.len() >= 3 {
        let suffix = &path.as_bytes()[path.len() - 3..];
        if suffix[0] == b'.'
            && suffix[1].eq_ignore_ascii_case(&b'z')
            && suffix[2].eq_ignore_ascii_case(&b's')
        {
            path.truncate(path.len() - 3);
        } else {
            break;
        }
    }
    let mut crc = 0xffff_ffffu32;
    for byte in path.bytes() {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xedb8_8320 & 0u32.wrapping_sub(crc & 1));
        }
    }
    !crc
}

#[derive(Debug)]
pub struct ResourceSizeTable<'a> {
    pub version: u32,
    pub name_length: u32,
    pub sizes: BTreeMap<u32, u32>,
    pub tail: &'a [u8],
    pub named_count: u32,
}

impl<'a> ResourceSizeTable<'a> {
    pub fn parse(data: &'a [u8]) -> Result<Self, RestblError> {
        let header = parse_header(data)?;
        let mut sizes = BTreeMap::new();
        for entry in data[HEADER_SIZE..header.end].chunks_exact(8) {
            sizes.insert(read_u32(entry, 0), read_u32(entry, 4));
        }
        Ok(Self {
            version: header.version,
            name_length: header.name_length,
            sizes,
            tail: &data[header.end..],
            named_count: header.named_count,
        })
    }

    pub fn get(&self, path: &str) -> Option<u32> {
        self.sizes.get(&key_for(path)).copied()
    }

    /// Agrandit une réservation. Une nouvelle entrée reçoit la marge de 64 o.
    pub fn ensure(&mut self, path: &str, size: u32) -> Result<bool, RestblError> {
        let key = key_for(path);
        match self.sizes.get_mut(&key) {
            Some(current) if *current >= size => Ok(false),
            Some(current) => {
                *current = size;
                Ok(true)
            }
            None => {
                let with_margin = size.checked_add(64).ok_or_else(|| {
                    RestblError("Taille RESTBL hors limites avec la marge de 64 octets.".into())
                })?;
                self.sizes.insert(key, with_margin);
                Ok(true)
            }
        }
    }

    pub fn build(&self) -> Result<Vec<u8>, RestblError> {
        self.build_with_limit(usize::MAX)
    }

    pub fn build_with_limit(&self, max_output: usize) -> Result<Vec<u8>, RestblError> {
        let count = u32::try_from(self.sizes.len())
            .map_err(|_| RestblError("Trop d'entrées RESTBL.".into()))?;
        let capacity = self
            .sizes
            .len()
            .checked_mul(8)
            .and_then(|n| HEADER_SIZE.checked_add(n))
            .and_then(|n| n.checked_add(self.tail.len()))
            .ok_or_else(|| RestblError("Table RESTBL trop grande.".into()))?;
        if capacity > max_output {
            return Err(RestblError(format!(
                "Table RESTBL supérieure à la limite de {max_output} octets."
            )));
        }
        let mut out = Vec::new();
        out.try_reserve_exact(capacity)
            .map_err(|_| RestblError("Mémoire insuffisante pour la table RESTBL.".into()))?;
        out.extend_from_slice(b"RESTBL");
        for value in [self.version, self.name_length, count, self.named_count] {
            out.extend_from_slice(&value.to_le_bytes());
        }
        for (key, size) in &self.sizes {
            out.extend_from_slice(&key.to_le_bytes());
            out.extend_from_slice(&size.to_le_bytes());
        }
        out.extend_from_slice(self.tail);
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc_and_suffixes() {
        assert_eq!(key_for("123456789"), 0xcbf4_3926);
        assert_eq!(
            key_for("Model\\modèle.bfres.ZS.zs"),
            key_for("Model/modèle.bfres")
        );
        assert_ne!(key_for("A"), key_for("a"));
    }

    #[test]
    fn preserves_tail_and_sorts_keys() {
        let table = ResourceSizeTable {
            version: 3,
            name_length: 64,
            sizes: [(999, 20), (11, 4)].into_iter().collect(),
            tail: "nommé\0".as_bytes(),
            named_count: 1,
        };
        let data = table.build().unwrap();
        assert!(table.build_with_limit(data.len() - 1).is_err());
        assert_eq!(table.build_with_limit(data.len()).unwrap(), data);
        let mut parsed = ResourceSizeTable::parse(&data).unwrap();
        assert_eq!(parsed.tail, "nommé\0".as_bytes());
        assert_eq!(read_u32(&data, HEADER_SIZE), 11);
        assert!(parsed.ensure("new.bfres.zs", 100).unwrap());
        assert_eq!(parsed.get("new.bfres"), Some(164));
        assert!(!parsed.ensure("new.bfres", 163).unwrap());
        assert!(parsed.ensure("new.bfres", 200).unwrap());
        let output = parsed.build().unwrap();
        assert_eq!(
            ResourceSizeTable::parse(&output).unwrap().get("new.bfres"),
            Some(200)
        );
    }

    #[test]
    fn rejects_truncation_and_size_overflow() {
        assert!(ResourceSizeTable::parse(b"RESTBL").is_err());
        let mut malformed = b"RESTBL".to_vec();
        malformed.extend_from_slice(&[0; 8]);
        malformed.extend_from_slice(&u32::MAX.to_le_bytes());
        malformed.extend_from_slice(&[0; 4]);
        assert!(ResourceSizeTable::parse(&malformed).is_err());
        let mut table = ResourceSizeTable {
            version: 0,
            name_length: 0,
            sizes: BTreeMap::new(),
            tail: b"",
            named_count: 0,
        };
        assert!(table.ensure("overflow", u32::MAX).is_err());
        assert!(table.sizes.is_empty());
    }
}
