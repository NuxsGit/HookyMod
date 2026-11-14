//! Lecteur et reconstructeur SARC autonome.
//!
//! Cette implémentation reprend la logique du lecteur HookyMod existant :
//! boutisme, hash SFAT, chaînes UTF-8, alignements, avertissements et ordre de
//! reconstruction. Les données des entrées sont empruntées au tampon d'origine
//! et la reconstruction est relue avant d'être rendue à l'appelant.

use std::fmt;

const MAGIC: &[u8; 4] = b"SARC";
const NAME_LIMIT: usize = 4096;
pub(crate) const MAX_NAMES_BYTES: usize = 64 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Endian {
    Little,
    Big,
}

impl Endian {
    fn read_u16(self, data: &[u8], offset: usize) -> Option<u16> {
        let bytes: [u8; 2] = data.get(offset..offset.checked_add(2)?)?.try_into().ok()?;
        Some(match self {
            Self::Little => u16::from_le_bytes(bytes),
            Self::Big => u16::from_be_bytes(bytes),
        })
    }

    fn read_u32(self, data: &[u8], offset: usize) -> Option<u32> {
        let bytes: [u8; 4] = data.get(offset..offset.checked_add(4)?)?.try_into().ok()?;
        Some(match self {
            Self::Little => u32::from_le_bytes(bytes),
            Self::Big => u32::from_be_bytes(bytes),
        })
    }

    fn u16_bytes(self, value: u16) -> [u8; 2] {
        match self {
            Self::Little => value.to_le_bytes(),
            Self::Big => value.to_be_bytes(),
        }
    }

    fn u32_bytes(self, value: u32) -> [u8; 4] {
        match self {
            Self::Little => value.to_le_bytes(),
            Self::Big => value.to_be_bytes(),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Little => "little",
            Self::Big => "big",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SarcError(pub String);

impl fmt::Display for SarcError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for SarcError {}

fn error(message: impl Into<String>) -> SarcError {
    SarcError(message.into())
}

/// Hash SFAT du nom UTF-8, avec arithmétique non signée sur 32 bits.
pub fn hash_name(name: &str, multiplier: u32) -> u32 {
    name.as_bytes().iter().fold(0u32, |value, byte| {
        value
            .wrapping_mul(multiplier)
            .wrapping_add(u32::from(*byte))
    })
}

/// Alignement appliqué par le lecteur historique aux entrées GPU et simples.
pub fn guess_alignment(data: &[u8], name: &str, absolute_offset: usize) -> usize {
    let lower = name.to_lowercase();
    let gpu = data.starts_with(b"FRES")
        || data.starts_with(b"BNTX")
        || data.starts_with(b"BNSH")
        || [".bfres", ".sbfres", ".bntx", ".bnsh", ".bnch", ".pack"]
            .iter()
            .any(|ext| lower.ends_with(ext));
    let mut base = if gpu { 0x2000 } else { 4 };
    if absolute_offset != 0 {
        let detected = absolute_offset & absolute_offset.wrapping_neg();
        if detected > base {
            base = detected.min(0x10000);
        }
    }
    base
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Entry<'a> {
    pub name: String,
    pub data: &'a [u8],
    pub alignment: usize,
    pub hash: u32,
}

impl<'a> Entry<'a> {
    pub fn new(name: impl Into<String>, data: &'a [u8]) -> Self {
        let name = name.into();
        Self {
            hash: hash_name(&name, 0x65),
            alignment: guess_alignment(data, &name, 0),
            name,
            data,
        }
    }
}

#[derive(Debug)]
pub struct Archive<'a> {
    pub entries: Vec<Entry<'a>>,
    pub endian: Endian,
    pub hash_mult: u32,
    pub version: u16,
    pub warnings: Vec<String>,
}

impl<'a> Archive<'a> {
    pub fn parse(data: &'a [u8]) -> Result<Self, SarcError> {
        if !data.starts_with(MAGIC) {
            return Err(error(
                "Ce n'est pas une archive SARC (magic « SARC » absent).",
            ));
        }
        if data.len() < 0x20 {
            return Err(error("Archive SARC tronquée (en-tête incomplet)."));
        }
        let endian = match &data[6..8] {
            b"\xff\xfe" => Endian::Little,
            b"\xfe\xff" => Endian::Big,
            _ => return Err(error("Archive SARC : BOM inconnu.")),
        };
        let header_size = endian
            .read_u16(data, 4)
            .ok_or_else(|| error("En-tête SARC illisible."))? as usize;
        let data_off = endian
            .read_u32(data, 0x0c)
            .ok_or_else(|| error("En-tête SARC illisible."))? as usize;
        let version = endian
            .read_u16(data, 0x10)
            .ok_or_else(|| error("En-tête SARC illisible."))?;
        if header_size < 0x14 || header_size + 0x0c > data.len() {
            return Err(error(format!(
                "Archive SARC : taille d'en-tête invalide (0x{header_size:X})."
            )));
        }
        if data_off > data.len() {
            return Err(error("Archive SARC : offset de données hors du fichier."));
        }
        if &data[header_size..header_size + 4] != b"SFAT" {
            return Err(error("Archive SARC : bloc SFAT absent."));
        }
        let sfat_size = endian.read_u16(data, header_size + 4).unwrap() as usize;
        let count = endian.read_u16(data, header_size + 6).unwrap() as usize;
        let mult = endian.read_u32(data, header_size + 8).unwrap();
        if sfat_size < 0x0c {
            return Err(error("Archive SARC : bloc SFAT invalide."));
        }
        let nodes_off = header_size
            .checked_add(sfat_size)
            .ok_or_else(|| error("SFAT trop grand."))?;
        let names_block = nodes_off
            .checked_add(
                count
                    .checked_mul(16)
                    .ok_or_else(|| error("SFAT trop grand."))?,
            )
            .ok_or_else(|| error("SFAT trop grand."))?;
        let names_off = names_block
            .checked_add(8)
            .ok_or_else(|| error("SFNT trop grand."))?;
        if names_off > data.len() {
            return Err(error("Archive SARC : table des nœuds tronquée."));
        }
        if &data[names_block..names_block + 4] != b"SFNT" {
            return Err(error("Archive SARC : bloc SFNT absent."));
        }
        let mut entries = Vec::new();
        entries
            .try_reserve_exact(count)
            .map_err(|_| error("Mémoire insuffisante pour les entrées SARC."))?;
        let mut warnings = Vec::new();
        let mut name_bytes = 0usize;
        for i in 0..count {
            let node = nodes_off + i * 16;
            let node_hash = endian
                .read_u32(data, node)
                .ok_or_else(|| error(format!("Archive SARC : nœud {i} illisible.")))?;
            let attrs = endian.read_u32(data, node + 4).unwrap();
            let start = endian.read_u32(data, node + 8).unwrap() as usize;
            let end = endian.read_u32(data, node + 12).unwrap() as usize;
            let fallback = || format!("0x{node_hash:08x}.bin");
            let name = if attrs & 0x0100_0000 == 0 {
                fallback()
            } else {
                let name_off = names_off.checked_add((attrs as usize & 0x00ff_ffff) * 4);
                match name_off {
                    Some(off) if off < data.len() => {
                        let limit = data.len().min(off.saturating_add(NAME_LIMIT));
                        let bytes = &data[off..limit];
                        let length = bytes
                            .iter()
                            .position(|byte| *byte == 0)
                            .unwrap_or(bytes.len());
                        let decoded = String::from_utf8_lossy(&bytes[..length]).replace('\\', "/");
                        if decoded.is_empty() {
                            warnings.push(format!("nœud {i} : nom vide, renommé."));
                            fallback()
                        } else {
                            decoded
                        }
                    }
                    _ => {
                        warnings.push(format!("nœud {i} : nom hors fichier, renommé."));
                        fallback()
                    }
                }
            };
            name_bytes = name_bytes
                .checked_add(name.len())
                .ok_or_else(|| error("Noms SARC trop volumineux."))?;
            if name_bytes > MAX_NAMES_BYTES {
                return Err(error("Noms SARC trop volumineux."));
            }
            let absolute_end = data_off.checked_add(end);
            if start > end || absolute_end.is_none() || absolute_end.unwrap() > data.len() {
                warnings.push(format!(
                    "« {name} » : bornes invalides (0x{start:X}-0x{end:X}), ignoré."
                ));
                continue;
            }
            let absolute_start = data_off
                .checked_add(start)
                .ok_or_else(|| error("Offset SARC invalide."))?;
            let blob = &data[absolute_start..absolute_end.unwrap()];
            entries.push(Entry {
                alignment: guess_alignment(blob, &name, absolute_start),
                hash: if node_hash == 0 {
                    hash_name(&name, 0x65)
                } else {
                    node_hash
                },
                name,
                data: blob,
            });
        }
        Ok(Self {
            entries,
            endian,
            hash_mult: mult,
            version,
            warnings,
        })
    }

    pub fn get(&self, name: &str) -> Option<&Entry<'a>> {
        self.entries.iter().find(|entry| entry.name == name)
    }

    /// Remplace la première entrée portant ce nom, comme la recherche Python.
    pub fn replace(&mut self, name: &str, data: &'a [u8]) -> Result<(), SarcError> {
        let entry = self
            .entries
            .iter_mut()
            .find(|entry| entry.name == name)
            .ok_or_else(|| error(format!("Entrée « {name} » absente de l'archive.")))?;
        entry.data = data;
        entry.alignment = guess_alignment(data, &entry.name, 0);
        Ok(())
    }

    /// Reconstruit le SARC et contrôle les noms et contenus après relecture.
    pub fn build(&self, verify: bool) -> Result<Vec<u8>, SarcError> {
        self.build_with_limit(verify, u32::MAX as usize)
    }

    /// Comme `build`, mais refuse une sortie plus grande que `max_output`
    /// avant l'allocation (utile pour des archives ou alignements non fiables).
    pub fn build_with_limit(&self, verify: bool, max_output: usize) -> Result<Vec<u8>, SarcError> {
        if self.entries.is_empty() {
            return Err(error("Impossible de construire une archive SARC vide."));
        }
        let count = u16::try_from(self.entries.len()).map_err(|_| error("Trop d'entrées SARC."))?;
        // Calculer le hash une seule fois par entrée, comme la clé du tri
        // Python : le recalculer à chaque comparaison coûte très cher pour les
        // archives nombreuses ou les noms longs.
        let mut items = Vec::new();
        items
            .try_reserve_exact(self.entries.len())
            .map_err(|_| error("Mémoire insuffisante pour trier le SARC."))?;
        items.extend(
            self.entries
                .iter()
                .map(|entry| (hash_name(&entry.name, self.hash_mult), entry)),
        );
        items.sort_by(|(a_hash, a), (b_hash, b)| {
            a_hash.cmp(b_hash).then_with(|| a.name.cmp(&b.name))
        });
        let mut names = Vec::new();
        let mut offsets = Vec::new();
        for (_, item) in &items {
            let word_offset = names.len() / 4;
            if word_offset > 0x00ff_ffff {
                return Err(error("Table des noms SARC trop grande."));
            }
            offsets.push(word_offset as u32);
            let new_len = names
                .len()
                .checked_add(item.name.len())
                .and_then(|n| n.checked_add(4))
                .ok_or_else(|| error("Table des noms SARC trop grande."))?;
            if new_len > MAX_NAMES_BYTES {
                return Err(error("Table des noms SARC trop grande."));
            }
            names
                .try_reserve(item.name.len() + 4)
                .map_err(|_| error("Mémoire insuffisante pour les noms SARC."))?;
            names.extend_from_slice(item.name.as_bytes());
            names.push(0);
            while names.len() % 4 != 0 {
                names.push(0);
            }
        }
        let maximum_alignment = items
            .iter()
            .map(|(_, entry)| entry.alignment)
            .max()
            .unwrap_or(4)
            .max(4);
        let header_len = 0x14usize + 0x0c + usize::from(count) * 16 + 8 + names.len();
        let data_off = align(header_len, maximum_alignment)?;
        let mut position = 0usize;
        let mut nodes = Vec::new();
        for ((hash, item), name_offset) in items.iter().zip(offsets) {
            position = align(position, item.alignment)?;
            let start = position;
            position = position
                .checked_add(item.data.len())
                .ok_or_else(|| error("Archive SARC trop grande."))?;
            let start = u32::try_from(start).map_err(|_| error("Archive SARC dépasse 4 Gio."))?;
            let end = u32::try_from(position).map_err(|_| error("Archive SARC dépasse 4 Gio."))?;
            nodes.push((*hash, 0x0100_0000 | name_offset, start, end));
        }
        let total = data_off
            .checked_add(position)
            .ok_or_else(|| error("Archive SARC trop grande."))?;
        if total > max_output {
            return Err(error(format!(
                "Archive SARC supérieure à la limite de {max_output} octets."
            )));
        }
        let total_u32 = u32::try_from(total).map_err(|_| error("Archive SARC dépasse 4 Gio."))?;
        let data_off_u32 =
            u32::try_from(data_off).map_err(|_| error("Archive SARC dépasse 4 Gio."))?;
        let mut out = Vec::new();
        out.try_reserve_exact(total)
            .map_err(|_| error("Mémoire insuffisante pour reconstruire le SARC."))?;
        out.extend_from_slice(MAGIC);
        out.extend_from_slice(&self.endian.u16_bytes(0x14));
        out.extend_from_slice(match self.endian {
            Endian::Little => b"\xff\xfe",
            Endian::Big => b"\xfe\xff",
        });
        out.extend_from_slice(&self.endian.u32_bytes(total_u32));
        out.extend_from_slice(&self.endian.u32_bytes(data_off_u32));
        out.extend_from_slice(&self.endian.u16_bytes(self.version));
        out.extend_from_slice(&self.endian.u16_bytes(0));
        out.extend_from_slice(b"SFAT");
        out.extend_from_slice(&self.endian.u16_bytes(0x0c));
        out.extend_from_slice(&self.endian.u16_bytes(count));
        out.extend_from_slice(&self.endian.u32_bytes(self.hash_mult));
        for (hash, attrs, start, end) in &nodes {
            for value in [hash, attrs, start, end] {
                out.extend_from_slice(&self.endian.u32_bytes(*value));
            }
        }
        out.extend_from_slice(b"SFNT");
        out.extend_from_slice(&self.endian.u16_bytes(8));
        out.extend_from_slice(&self.endian.u16_bytes(0));
        out.extend_from_slice(&names);
        if out.len() > data_off {
            return Err(error("Erreur interne : en-tête SARC plus grand que prévu."));
        }
        out.resize(data_off, 0);
        for ((_, item), (_, _, start, _)) in items.iter().zip(nodes) {
            let start = data_off + start as usize;
            if out.len() < start {
                out.resize(start, 0);
            }
            out.extend_from_slice(item.data);
        }
        if verify {
            let check = Archive::parse(&out)?;
            if check.entries.len() != items.len() {
                return Err(error("Reconstruction SARC incohérente (nombre d'entrées)."));
            }
            for ((_, expected), got) in items.iter().zip(&check.entries) {
                if expected.name != got.name || expected.data != got.data {
                    return Err(error(format!(
                        "Reconstruction SARC incohérente pour « {} ».",
                        expected.name
                    )));
                }
            }
        }
        Ok(out)
    }
}

fn align(value: usize, alignment: usize) -> Result<usize, SarcError> {
    let alignment = alignment.max(1);
    value
        .checked_add(alignment - 1)
        .map(|value| value / alignment * alignment)
        .ok_or_else(|| error("Alignement SARC trop grand."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_little_and_big_endian() {
        for endian in [Endian::Little, Endian::Big] {
            let mut archive = Archive {
                entries: vec![
                    Entry::new("Tex/été.bntx", b"BNTX1234"),
                    Entry::new("dir/hello.txt", b"hello"),
                ],
                endian,
                hash_mult: 0x65,
                version: 0x100,
                warnings: vec![],
            };
            let data = archive.build(true).unwrap();
            let parsed = Archive::parse(&data).unwrap();
            assert_eq!(parsed.endian, endian);
            assert_eq!(parsed.get("dir/hello.txt").unwrap().data, b"hello");
            assert!(parsed.get("Tex/été.bntx").unwrap().alignment >= 0x2000);
            archive.replace("dir/hello.txt", b"modified").unwrap();
            let changed = archive.build(true).unwrap();
            assert_eq!(
                Archive::parse(&changed)
                    .unwrap()
                    .get("dir/hello.txt")
                    .unwrap()
                    .data,
                b"modified"
            );
        }
    }

    #[test]
    fn collision_sorting_and_zero_hash() {
        // Avec un multiplicateur nul, seul le dernier octet contribue : ces
        // deux noms entrent réellement en collision et doivent être triés.
        assert_eq!(hash_name("az", 0), hash_name("bz", 0));
        assert_eq!(hash_name("ab", 1), hash_name("ba", 1));
        let archive = Archive {
            entries: vec![Entry::new("bz", b"one"), Entry::new("az", b"two")],
            endian: Endian::Little,
            hash_mult: 0,
            version: 0x100,
            warnings: vec![],
        };
        let mut built = archive.build(true).unwrap();
        assert!(archive.build_with_limit(true, built.len() - 1).is_err());
        assert_eq!(archive.build_with_limit(true, built.len()).unwrap(), built);
        let parsed = Archive::parse(&built).unwrap();
        assert_eq!(
            parsed
                .entries
                .iter()
                .map(|entry| entry.name.as_str())
                .collect::<Vec<_>>(),
            ["az", "bz"]
        );
        // Un hash stocké nul déclenche le calcul de repli du lecteur Python.
        built[0x20..0x24].copy_from_slice(&0u32.to_le_bytes());
        let parsed = Archive::parse(&built).unwrap();
        assert_eq!(parsed.entries[0].hash, hash_name("az", 0x65));
    }

    #[test]
    fn rejects_truncated_and_inconsistent_data() {
        for bytes in [b"nope".as_slice(), b"SARC".as_slice(), &[0u8; 32]] {
            assert!(Archive::parse(bytes).is_err());
        }
        let archive = Archive {
            entries: vec![Entry::new("broken\0name", b"data")],
            endian: Endian::Little,
            hash_mult: 0x65,
            version: 0x100,
            warnings: vec![],
        };
        assert!(archive.build(true).is_err());
    }
}
