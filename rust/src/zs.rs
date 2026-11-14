//! Décompression Zstandard autonome, avec dictionnaires et limites explicites.
//!
//! `ruzstd` et `twox-hash` sont inclus dans `vendor/`. Le décodeur amont ne
//! vérifie pas automatiquement la somme de contrôle : elle est donc comparée
//! ici, pour chaque trame. Les tailles annoncées sont également vérifiées.
//! La compression actuelle ne prend **pas** les dictionnaires en charge.

use std::borrow::Cow;
use std::collections::BTreeMap;
use std::fmt;
use std::io::Read;
use std::panic::{catch_unwind, AssertUnwindSafe};

use ruzstd::decoding::{BlockDecodingStrategy, Dictionary, FrameDecoder};
use ruzstd::encoding::{compress_to_vec, CompressionLevel};

use crate::sarc::Archive;

pub const DEFAULT_MAX_OUTPUT: usize = 512 * 1024 * 1024;
pub const MAX_WINDOW: u64 = 128 * 1024 * 1024;
const MAX_DICT_SIZE: usize = 16 * 1024 * 1024;
const MAX_DICT_TOTAL: usize = 64 * 1024 * 1024;
const MAX_DICT_COUNT: usize = 64;
const MAX_PACK_OUTPUT: usize = 64 * 1024 * 1024;
const MAGIC: &[u8; 4] = b"\x28\xb5\x2f\xfd";
const DICT_MAGIC: &[u8; 4] = b"\x37\xa4\x30\xec";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ZstdError(pub String);

impl fmt::Display for ZstdError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for ZstdError {}

fn error(message: impl Into<String>) -> ZstdError {
    ZstdError(message.into())
}

/// Une trame standard ou une trame zstd « skippable ».
pub fn is_zstd(data: &[u8]) -> bool {
    data.starts_with(MAGIC) || skip_magic(data)
}

fn skip_magic(data: &[u8]) -> bool {
    data.len() >= 4
        && (0x184d_2a50..=0x184d_2a5f).contains(&u32::from_le_bytes(data[..4].try_into().unwrap()))
}

#[derive(Debug, PartialEq, Eq)]
struct FrameHeader {
    dictionary_id: u32,
    content_size: Option<u64>,
}

/// Lit uniquement les champs nécessaires au contrôle, sans allouer.
fn frame_header(data: &[u8]) -> Result<FrameHeader, ZstdError> {
    if !data.starts_with(MAGIC) || data.len() < 5 {
        return Err(error("En-tête de trame zstd absent ou tronqué."));
    }
    let descriptor = data[4];
    if descriptor & 0x08 != 0 {
        return Err(error("Bit réservé de l'en-tête zstd non nul."));
    }
    let single_segment = descriptor & 0x20 != 0;
    let dict_bytes = [0, 1, 2, 4][usize::from(descriptor & 3)];
    let size_bytes = match descriptor >> 6 {
        0 => usize::from(single_segment),
        1 => 2,
        2 => 4,
        _ => 8,
    };
    let mut cursor = 5 + usize::from(!single_segment);
    let read_le = |start: usize, len: usize| -> Result<u64, ZstdError> {
        let end = start
            .checked_add(len)
            .ok_or_else(|| error("En-tête zstd trop grand."))?;
        let bytes = data
            .get(start..end)
            .ok_or_else(|| error("En-tête de trame zstd tronqué."))?;
        Ok(bytes
            .iter()
            .enumerate()
            .fold(0u64, |acc, (i, byte)| acc | (u64::from(*byte) << (i * 8))))
    };
    let dictionary_id = read_le(cursor, dict_bytes)? as u32;
    cursor += dict_bytes;
    let content_size = if size_bytes == 0 {
        // Vérifie aussi la présence du descripteur de fenêtre, s'il y en a un.
        read_le(cursor, 0)?;
        None
    } else {
        let value = read_le(cursor, size_bytes)?;
        Some(value + if size_bytes == 2 { 256 } else { 0 })
    };
    Ok(FrameHeader {
        dictionary_id,
        content_size,
    })
}

/// Taille annoncée par une trame standard, si elle est présente et valide.
pub fn frame_content_size(data: &[u8]) -> Option<u64> {
    frame_header(data).ok()?.content_size
}

/// Identifiant de convention du jeu pour les dictionnaires sans en-tête zstd.
pub fn dictionary_name_id(name: &str) -> u32 {
    let base = name.rsplit(['/', '\\']).next().unwrap_or(name);
    let mut stem = base.trim().to_lowercase();
    while let Some(suffix) = [".zsdic", ".zs", ".dict", ".bin"]
        .iter()
        .find(|suffix| stem.ends_with(**suffix))
    {
        stem.truncate(stem.len() - suffix.len());
    }
    match stem.as_str() {
        "zs" => 1,
        "bkprb" => 2,
        "bcett.byml" => 3,
        "pack" => 4,
        _ => 0,
    }
}

fn looks_like_dict_name(name: &str) -> bool {
    let mut lower = name.to_ascii_lowercase();
    if lower.ends_with(".zs") {
        lower.truncate(lower.len() - 3);
    }
    dictionary_name_id(name) != 0
        || lower.ends_with(".zsdic")
        || lower.ends_with(".dict")
        || lower.ends_with(".bin")
}

#[derive(Clone, Debug)]
struct StoredDictionary {
    bytes: Vec<u8>,
    tagged: bool,
}

/// Dictionnaires mémorisés par identifiant. Les doublons différents sont
/// refusés au lieu de sélectionner silencieusement le mauvais contenu.
#[derive(Clone, Debug, Default)]
pub struct Dictionaries {
    entries: BTreeMap<u32, StoredDictionary>,
    total_bytes: usize,
}

impl Dictionaries {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn ids(&self) -> impl Iterator<Item = u32> + '_ {
        self.entries.keys().copied()
    }

    /// Ajoute un dictionnaire à en-tête (id interne) ou de contenu brut (id
    /// déduit du nom, ou 0 pour un nom inconnu). L'id 0 doit être forcé
    /// explicitement lors de la décompression d'une trame sans identifiant.
    pub fn add(&mut self, name: &str, data: &[u8]) -> Result<bool, ZstdError> {
        self.add_inner(name, None, data)
    }

    /// Associe explicitement un identifiant à un dictionnaire de contenu brut.
    /// Pour un dictionnaire à en-tête, l'identifiant doit correspondre à celui
    /// déclaré dans le dictionnaire : un alias silencieux serait dangereux.
    pub fn add_with_id(&mut self, id: u32, data: &[u8]) -> Result<bool, ZstdError> {
        self.add_inner("", Some(id), data)
    }

    fn add_inner(
        &mut self,
        name: &str,
        explicit_id: Option<u32>,
        data: &[u8],
    ) -> Result<bool, ZstdError> {
        if data.is_empty() || data.len() > MAX_DICT_SIZE {
            return Err(error("Dictionnaire vide ou supérieur à 16 Mio."));
        }
        let tagged = data.starts_with(DICT_MAGIC);
        let id = if tagged {
            let parsed = catch_unwind(AssertUnwindSafe(|| Dictionary::decode_dict(data)))
                .map_err(|_| error("Dictionnaire zstd invalide : erreur du décodeur."))?
                .map_err(|err| error(format!("Dictionnaire zstd invalide : {err}")))?;
            if parsed.id == 0 {
                return Err(error("Dictionnaire zstd : identifiant nul interdit."));
            }
            if explicit_id.is_some_and(|expected| expected != parsed.id) {
                return Err(error("Identifiant du dictionnaire zstd incohérent."));
            }
            parsed.id
        } else {
            explicit_id.unwrap_or_else(|| dictionary_name_id(name))
        };
        if let Some(old) = self.entries.get(&id) {
            if old.bytes == data && old.tagged == tagged {
                return Ok(false);
            }
            return Err(error(format!(
                "Dictionnaires différents avec le même id {id}."
            )));
        }
        if self.entries.len() >= MAX_DICT_COUNT
            || self.total_bytes.checked_add(data.len()).is_none()
            || self.total_bytes + data.len() > MAX_DICT_TOTAL
        {
            return Err(error("Collection de dictionnaires trop volumineuse."));
        }
        let mut bytes = Vec::new();
        bytes
            .try_reserve_exact(data.len())
            .map_err(|_| error("Mémoire insuffisante pour le dictionnaire."))?;
        bytes.extend_from_slice(data);
        self.total_bytes += bytes.len();
        self.entries.insert(id, StoredDictionary { bytes, tagged });
        Ok(true)
    }

    /// Charge un fichier dictionnaire ou un SARC de dictionnaires, comprimé ou
    /// non. Les entrées zstd du SARC sont traitées dans une seconde passe. En
    /// cas d'erreur, aucun dictionnaire partiellement chargé n'est conservé.
    pub fn load(&mut self, name: &str, data: &[u8]) -> Result<usize, ZstdError> {
        let previous: Vec<u32> = self.entries.keys().copied().collect();
        let previous_bytes = self.total_bytes;
        match self.load_inner(name, data) {
            Ok(added) => Ok(added),
            Err(err) => {
                self.entries
                    .retain(|id, _| previous.binary_search(id).is_ok());
                self.total_bytes = previous_bytes;
                Err(err)
            }
        }
    }

    fn load_inner(&mut self, name: &str, data: &[u8]) -> Result<usize, ZstdError> {
        let raw = if is_zstd(data) {
            Cow::Owned(decompress(data, self, None, MAX_PACK_OUTPUT)?)
        } else {
            Cow::Borrowed(data)
        };
        let mut added = 0;
        if raw.starts_with(b"SARC") {
            if raw.len() > MAX_PACK_OUTPUT {
                return Err(error("Archive de dictionnaires supérieure à 64 Mio."));
            }
            let archive = Archive::parse(&raw).map_err(|err| error(err.to_string()))?;
            let mut recognized = 0usize;
            let mut compressed = Vec::new();
            for entry in &archive.entries {
                if entry.data.is_empty() {
                    continue;
                }
                if is_zstd(entry.data) {
                    if looks_like_dict_name(&entry.name) {
                        recognized += 1;
                        compressed.push(entry);
                    }
                } else if entry.data.starts_with(DICT_MAGIC) || looks_like_dict_name(&entry.name) {
                    recognized += 1;
                    added += usize::from(self.add(&entry.name, entry.data)?);
                }
            }
            for entry in compressed {
                let decoded = decompress(entry.data, self, None, MAX_DICT_SIZE)?;
                added += usize::from(self.add(&entry.name, &decoded)?);
            }
            if recognized == 0 {
                return Err(error("Aucun dictionnaire reconnu dans l'archive SARC."));
            }
        } else {
            added += usize::from(self.add(name, &raw)?);
        }
        Ok(added)
    }

    fn decoder(&self) -> Result<FrameDecoder, ZstdError> {
        let mut decoder = FrameDecoder::new();
        decoder.set_max_window_size(MAX_WINDOW);
        for (&id, item) in &self.entries {
            let dictionary = if item.tagged {
                Dictionary::decode_dict(&item.bytes)
                    .map_err(|err| error(format!("Dictionnaire zstd invalide : {err}")))?
            } else {
                // Dictionnaire de contenu brut : pas de tables d'entropie,
                // historique des offsets initial défini par la norme zstd.
                Dictionary {
                    id,
                    fse: Default::default(),
                    huf: Default::default(),
                    dict_content: item.bytes.clone(),
                    offset_hist: [1, 4, 8],
                }
            };
            decoder
                .add_dict(dictionary)
                .map_err(|err| error(format!("Dictionnaire zstd inutilisable : {err}")))?;
        }
        Ok(decoder)
    }
}

/// Décompresse toutes les trames et contrôle les checksums présents. Les trames
/// skippables sont ignorées. `max_output` borne la sortie totale ; la fenêtre
/// interne du décodeur reste bornée séparément à 128 Mio. Sans identifiant dans
/// une trame, `force_dictionary` est nécessaire pour un dictionnaire implicite.
pub fn decompress(
    data: &[u8],
    dictionaries: &Dictionaries,
    force_dictionary: Option<u32>,
    max_output: usize,
) -> Result<Vec<u8>, ZstdError> {
    // Certains décodeurs tiers peuvent paniquer sur des données malformées ;
    // ne jamais produire un fichier de sortie partiel dans ce cas.
    catch_unwind(AssertUnwindSafe(|| {
        decompress_inner(data, dictionaries, force_dictionary, max_output)
    }))
    .map_err(|_| error("Flux zstd invalide : erreur interne du décodeur."))?
}

fn decompress_inner(
    data: &[u8],
    dictionaries: &Dictionaries,
    force_dictionary: Option<u32>,
    max_output: usize,
) -> Result<Vec<u8>, ZstdError> {
    if !is_zstd(data) {
        return Err(error("Ce fichier n'est pas une trame zstd (.zs)."));
    }
    if let Some(id) = force_dictionary {
        if !dictionaries.entries.contains_key(&id) {
            return Err(error(format!("Dictionnaire forcé id {id} introuvable.")));
        }
    }
    let mut decoder = dictionaries.decoder()?;
    let mut input = data;
    let mut output = Vec::new();
    let mut frame_count = 0usize;
    while !input.is_empty() {
        if skip_magic(input) {
            if input.len() < 8 {
                return Err(error("Trame zstd skippable tronquée."));
            }
            let length = u32::from_le_bytes(input[4..8].try_into().unwrap()) as usize;
            let total = 8usize
                .checked_add(length)
                .ok_or_else(|| error("Trame zstd skippable trop grande."))?;
            input = input
                .get(total..)
                .ok_or_else(|| error("Trame zstd skippable tronquée."))?;
            continue;
        }
        let header = frame_header(input)?;
        if header.dictionary_id != 0 && !dictionaries.entries.contains_key(&header.dictionary_id) {
            return Err(error(format!(
                "Dictionnaire zstd id {} introuvable ; fournis ZsDic.pack.zs avec --dict ou --romfs.",
                header.dictionary_id
            )));
        }
        if let Some(forced) = force_dictionary {
            if header.dictionary_id != 0 && header.dictionary_id != forced {
                return Err(error(format!(
                    "La trame demande le dictionnaire {}, pas le dictionnaire forcé {forced}.",
                    header.dictionary_id
                )));
            }
        }
        if let Some(size) = header.content_size {
            let remaining = max_output.saturating_sub(output.len());
            if size > remaining as u64 {
                return Err(error(format!(
                    "Sortie zstd supérieure à la limite de {max_output} octets."
                )));
            }
        }
        let previous_input_len = input.len();
        decoder
            .init(&mut input)
            .map_err(|err| error(format!("En-tête zstd invalide : {err}")))?;
        if header.dictionary_id == 0 {
            if let Some(id) = force_dictionary {
                decoder
                    .force_dict(id)
                    .map_err(|err| error(format!("Dictionnaire zstd inutilisable : {err}")))?;
            }
        }
        let start = output.len();
        while !decoder.is_finished() {
            decoder
                .decode_blocks(&mut input, BlockDecodingStrategy::UptoBlocks(1))
                .map_err(|err| error(format!("Flux zstd invalide : {err}")))?;
            while decoder.can_collect() != 0 {
                let mut chunk = [0u8; 64 * 1024];
                let count = decoder
                    .read(&mut chunk)
                    .map_err(|err| error(format!("Lecture zstd impossible : {err}")))?;
                if count == 0 {
                    return Err(error("Le décodeur zstd ne progresse pas."));
                }
                if count > max_output.saturating_sub(output.len()) {
                    return Err(error(format!(
                        "Sortie zstd supérieure à la limite de {max_output} octets."
                    )));
                }
                output
                    .try_reserve(count)
                    .map_err(|_| error("Mémoire insuffisante pour la sortie zstd."))?;
                output.extend_from_slice(&chunk[..count]);
            }
        }
        if let Some(size) = header.content_size {
            if (output.len() - start) as u64 != size {
                return Err(error(format!(
                    "Taille zstd incohérente : {size} annoncés, {} décodés.",
                    output.len() - start
                )));
            }
        }
        if let Some(expected) = decoder.get_checksum_from_data() {
            let actual = decoder
                .get_calculated_checksum()
                .ok_or_else(|| error("Somme de contrôle zstd indisponible."))?;
            if expected != actual {
                return Err(error("Somme de contrôle zstd incorrecte."));
            }
        }
        if input.len() >= previous_input_len {
            return Err(error("Le décodeur zstd ne progresse pas."));
        }
        frame_count += 1;
    }
    if frame_count == 0 {
        return Err(error("Aucune trame zstd de données."));
    }
    Ok(output)
}

/// Compression zstd rapide sans dictionnaire. La sortie est relue et comparée
/// avant d'être rendue ; la compatibilité avec les contraintes propres au jeu
/// n'est pas garantie. La compression avec dictionnaire n'est pas implémentée.
pub fn compress_without_dictionary(data: &[u8]) -> Result<Vec<u8>, ZstdError> {
    let encoded = catch_unwind(AssertUnwindSafe(|| {
        compress_to_vec(data, CompressionLevel::Fastest)
    }))
    .map_err(|_| error("Compression zstd impossible : erreur du compresseur."))?;
    let decoded = decompress(&encoded, &Dictionaries::new(), None, data.len())?;
    if decoded != data {
        return Err(error("Compression zstd incohérente après relecture."));
    }
    Ok(encoded)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_frame_metadata_and_dictionary_names() {
        let empty = b"\x28\xb5\x2f\xfd\x20\x00\x01\x00\x00";
        assert_eq!(
            frame_header(empty).unwrap(),
            FrameHeader {
                dictionary_id: 0,
                content_size: Some(0)
            }
        );
        let id_size = b"\x28\xb5\x2f\xfd\x61\x04\x12\x00";
        assert_eq!(
            frame_header(id_size).unwrap(),
            FrameHeader {
                dictionary_id: 4,
                content_size: Some(0x112)
            }
        );
        assert!(frame_header(b"\x28\xb5\x2f\xfd\x08").is_err());
        assert!(frame_header(b"\x28\xb5\x2f\xfd\x41").is_err());
        assert_eq!(dictionary_name_id("Pack\\ZS.zsdic"), 1);
        assert_eq!(dictionary_name_id("bcett.byml.zsdic.zs"), 3);
        assert_eq!(dictionary_name_id("autre.dict"), 0);
    }

    #[test]
    fn compressed_roundtrip_and_output_limits() {
        let data = "une trame zstd testée, avec somme de contrôle"
            .as_bytes()
            .repeat(3000);
        let encoded = compress_without_dictionary(&data).unwrap();
        assert_eq!(
            decompress(&encoded, &Dictionaries::new(), None, data.len()).unwrap(),
            data
        );
        assert!(decompress(&encoded, &Dictionaries::new(), None, data.len() - 1).is_err());
        let mut damaged = encoded;
        *damaged.last_mut().unwrap() ^= 1;
        assert!(decompress(&damaged, &Dictionaries::new(), None, data.len()).is_err());
    }

    #[test]
    fn reserved_sequence_mode_bits_are_not_ignored() {
        // Flux synthétique avec checksum valide : les deux bits de poids
        // faible du mode de séquence à l'offset 53 sont réservés.
        let original = [
            0x28, 0xb5, 0x2f, 0xfd, 0x04, 0x20, 0x95, 0x01, 0x00, 0x94, 0x02, 0x6f, 0xf1, 0x38,
            0xa0, 0xd2, 0xd2, 0x2d, 0x51, 0x12, 0xd2, 0x3c, 0xd1, 0xd2, 0x9e, 0x3d, 0xf9, 0x9b,
            0x82, 0x6c, 0x88, 0x2f, 0xb6, 0x92, 0xa1, 0x9b, 0xfd, 0x7a, 0xf3, 0xf5, 0x5a, 0xca,
            0x3b, 0xed, 0x96, 0x31, 0x73, 0x62, 0x76, 0x9d, 0xa1, 0x9b, 0x01, 0x00, 0xd9, 0x38,
            0x8c, 0x57, 0x65, 0xc9, 0x06, 0x31, 0x52,
        ];
        assert_eq!(
            decompress(&original, &Dictionaries::new(), None, 20000)
                .unwrap()
                .len(),
            10055
        );
        for reserved in 1..=3 {
            let mut damaged = original;
            damaged[53] |= reserved;
            assert!(decompress(&damaged, &Dictionaries::new(), None, 20000).is_err());
        }
    }

    #[test]
    fn malformed_block_growth_is_rejected_before_decoding() {
        fn frame(body: &[u8]) -> Vec<u8> {
            // Fenêtre 1 Kio, taille de contenu inconnue, dernier bloc compressé.
            let mut frame = b"\x28\xb5\x2f\xfd\x00\x00".to_vec();
            let header = ((body.len() as u32) << 3) | 5;
            frame.extend_from_slice(&header.to_le_bytes()[..3]);
            frame.extend_from_slice(body);
            frame
        }
        fn literals(size: u32, sequences: &[u8]) -> Vec<u8> {
            let header = (size << 4) | 13; // littéral RLE, taille sur 20 bits
            let mut body = header.to_le_bytes()[..3].to_vec();
            body.push(b'x');
            body.extend_from_slice(sequences);
            body
        }
        let exact = frame(&literals(1024, &[0]));
        assert_eq!(
            decompress(&exact, &Dictionaries::new(), None, 1024).unwrap(),
            vec![b'x'; 1024]
        );
        let cases = [
            frame(&literals(1025, &[0])),
            frame(&literals(1_048_575, &[0])),
            frame(&literals(0, &[255, 255, 255, 0])), // nombre de séquences impossible
            frame(&vec![0; 1025]),                    // corps compressé supérieur à la fenêtre
        ];
        for input in &cases {
            let err = decompress(input, &Dictionaries::new(), None, 2 * 1024 * 1024).unwrap_err();
            assert!(
                err.to_string().contains("DecompressedSizeTooLarge"),
                "{err}"
            );
        }
        // Séquence RLE : le minimum (littéraux + 3) tient, mais la longueur
        // réelle du match ou les littéraux restants dépassent la fenêtre.
        let exact = frame(&literals(1020, &[1, 0x54, 1, 0, 1, 1]));
        assert_eq!(
            decompress(&exact, &Dictionaries::new(), None, 1024).unwrap(),
            vec![b'x'; 1024]
        );
        for body in [
            literals(1021, &[1, 0x54, 1, 0, 1, 1]),  // reste de littéraux
            literals(1, &[1, 0x54, 1, 0, 46, 0, 4]), // match trop grand
        ] {
            let err = decompress(&frame(&body), &Dictionaries::new(), None, 200_000).unwrap_err();
            assert!(err.to_string().contains("TooManyBytesGenerated"), "{err}");
        }
    }

    #[test]
    fn dictionary_pack_loading_is_atomic() {
        use crate::sarc::{Endian, Entry};
        let mut dicts = Dictionaries::new();
        dicts.add("zs.zsdic", b"existing raw dictionary").unwrap();
        let archive = Archive {
            // Les deux noms ont le même dernier octet : avec mult=0,
            // a.zsdic est chargé avant le dictionnaire z.zsdic invalide.
            entries: vec![
                Entry::new("a.zsdic", b"raw dictionary"),
                Entry::new("z.zsdic", b"\x37\xa4\x30\xec\x01"),
            ],
            endian: Endian::Little,
            hash_mult: 0,
            version: 0x100,
            warnings: vec![],
        };
        let data = archive.build(true).unwrap();
        assert!(dicts.load("dicts.pack", &data).is_err());
        assert_eq!(dicts.ids().collect::<Vec<_>>(), [1]);
        assert_eq!(dicts.total_bytes, b"existing raw dictionary".len());
    }

    #[test]
    fn multiple_and_skippable_frames() {
        let one = compress_without_dictionary(b"bonjour").unwrap();
        let two = compress_without_dictionary(b" monde").unwrap();
        let mut input = one;
        input.extend_from_slice(b"\x5a\x2a\x4d\x18\x03\x00\x00\x00xyz");
        input.extend_from_slice(&two);
        assert_eq!(
            decompress(&input, &Dictionaries::new(), None, 13).unwrap(),
            b"bonjour monde"
        );
        input.pop();
        assert!(decompress(&input, &Dictionaries::new(), None, 100).is_err());
    }
}
