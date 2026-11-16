//! Lecture autonome de NUP, sans charger l'ensemble d'un gros paquet en mémoire.
//!
//! Seules la TOC et l'entrée demandée sont lues. Les chemins du paquet ne sont
//! jamais utilisés comme chemins de sortie : l'appelant choisit explicitement un
//! fichier de destination. Les empreintes SHA-256 présentes sont vérifiées.

use std::collections::HashSet;
use std::fmt;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use hmac_sha256::Hash;

use crate::zs::{self, Dictionaries};

const MAX_TOC: usize = 64 * 1024 * 1024;
const MAX_NAME: usize = 4096;
const MAX_PATH: usize = 4096;
const MAX_PATHS_TOTAL: usize = 64 * 1024 * 1024;
const MAX_DEPTH: usize = 256;
const NO_NODE: u32 = u32::MAX;
const MAGIC: &[u8; 4] = b"NUP\x01";
const TOC_MAGIC: &[u8; 4] = b"TOC\x01";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NupError(pub String);

impl fmt::Display for NupError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for NupError {}

fn error(message: impl Into<String>) -> NupError {
    NupError(message.into())
}

fn read_u32(data: &[u8], offset: usize) -> Result<u32, NupError> {
    let slice = data
        .get(
            offset
                ..offset
                    .checked_add(4)
                    .ok_or_else(|| error("Offset NUP trop grand."))?,
        )
        .ok_or_else(|| error("Table NUP tronquée."))?;
    Ok(u32::from_le_bytes(slice.try_into().unwrap()))
}

fn read_u64(data: &[u8], offset: usize) -> Result<u64, NupError> {
    let slice = data
        .get(
            offset
                ..offset
                    .checked_add(8)
                    .ok_or_else(|| error("Offset NUP trop grand."))?,
        )
        .ok_or_else(|| error("Table NUP tronquée."))?;
    Ok(u64::from_le_bytes(slice.try_into().unwrap()))
}

fn read_at<R: Read + Seek>(
    source: &mut R,
    physical_size: u64,
    offset: u64,
    length: u64,
    limit: usize,
) -> Result<Vec<u8>, NupError> {
    let end = offset
        .checked_add(length)
        .ok_or_else(|| error("Données NUP hors limites."))?;
    if end > physical_size {
        return Err(error("Données NUP hors du fichier ou paquet tronqué."));
    }
    let length = usize::try_from(length).map_err(|_| error("Données NUP trop volumineuses."))?;
    if length > limit {
        return Err(error(format!(
            "Lecture NUP supérieure à la limite de {limit} octets."
        )));
    }
    let mut data = Vec::new();
    data.try_reserve_exact(length)
        .map_err(|_| error("Mémoire insuffisante pour lire le paquet NUP."))?;
    data.resize(length, 0);
    source
        .seek(SeekFrom::Start(offset))
        .and_then(|_| source.read_exact(&mut data))
        .map_err(|err| error(format!("Lecture NUP impossible : {err}")))?;
    Ok(data)
}

#[derive(Clone, Debug)]
pub struct Title {
    pub id: u64,
    pub version: u32,
    pub root: u32,
    pub exefs: Option<u32>,
    pub romfs: Option<u32>,
}

#[derive(Clone, Debug)]
struct Directory {
    name_offset: u32,
    name_length: u32,
    first_child: u32,
    child_count: u32,
}

#[derive(Clone, Debug)]
pub struct FileEntry {
    name_offset: u32,
    name_length: u32,
    pub size: u64,
    pub offset: u64,
    pub stored_size: u64,
    pub sha256: [u8; 32],
    pub codec: u8,
    pub flags: u8,
}

#[derive(Clone, Debug)]
pub struct Member {
    pub path: String,
    pub file_index: usize,
    pub size: u64,
    pub stored_size: u64,
    pub codec: u8,
}

pub struct Package<R: Read + Seek> {
    source: R,
    pub physical_size: u64,
    pub declared_size: u64,
    pub blob_offset: u64,
    pub toc_hash_present: bool,
    pub titles: Vec<Title>,
    directories: Vec<Directory>,
    files: Vec<FileEntry>,
    children: Vec<u32>,
    strings: Vec<u8>,
}

impl Package<File> {
    pub fn open(path: &Path) -> Result<Self, NupError> {
        let file = File::open(path).map_err(|err| {
            error(format!(
                "Ouverture NUP impossible : {} ({err}).",
                path.display()
            ))
        })?;
        Self::parse(file)
    }
}

impl<R: Read + Seek> Package<R> {
    pub fn parse(mut source: R) -> Result<Self, NupError> {
        let physical_size = source
            .seek(SeekFrom::End(0))
            .map_err(|err| error(format!("Taille NUP inaccessible : {err}")))?;
        if physical_size < 0x80 {
            return Err(error("Fichier trop court pour un paquet NUP."));
        }
        let header = read_at(&mut source, physical_size, 0, 0x80, 0x80)?;
        if !header.starts_with(MAGIC) {
            return Err(error("Ce fichier n'est pas un paquet NUP."));
        }
        let toc_offset = read_u64(&header, 0x08)?;
        let toc_size = read_u64(&header, 0x10)?;
        let toc_hash: [u8; 32] = header[0x18..0x38].try_into().unwrap();
        let blob_offset = read_u64(&header, 0x38)?;
        let declared_size = read_u64(&header, 0x40)?;
        if declared_size != 0 && physical_size < declared_size {
            return Err(error(
                "Paquet NUP incomplet : taille physique inférieure à la taille déclarée.",
            ));
        }
        if toc_size < 0x30 || toc_offset < 0x80 {
            return Err(error("Emplacement ou taille de la TOC NUP invalide."));
        }
        let toc = read_at(&mut source, physical_size, toc_offset, toc_size, MAX_TOC)?;
        if !toc.starts_with(TOC_MAGIC) {
            return Err(error("En-tête de TOC NUP absent."));
        }
        let toc_hash_present = toc_hash.iter().any(|byte| *byte != 0);
        if toc_hash_present && Hash::hash(&toc) != toc_hash {
            return Err(error("SHA-256 de la TOC NUP incorrect."));
        }
        let title_count = read_u32(&toc, 4)? as usize;
        let dir_count = usize::try_from(read_u64(&toc, 8)?)
            .map_err(|_| error("Trop de dossiers dans la TOC NUP."))?;
        let file_count = usize::try_from(read_u64(&toc, 16)?)
            .map_err(|_| error("Trop de fichiers dans la TOC NUP."))?;
        let strings_offset = usize::try_from(read_u64(&toc, 24)?)
            .map_err(|_| error("Table des noms NUP hors limites."))?;
        let strings_size = usize::try_from(read_u64(&toc, 32)?)
            .map_err(|_| error("Table des noms NUP trop volumineuse."))?;
        let titles_at = 0x30usize;
        let dirs_at = title_count
            .checked_mul(0x40)
            .and_then(|n| titles_at.checked_add(n))
            .ok_or_else(|| error("Table des titres NUP trop grande."))?;
        let files_at = dir_count
            .checked_mul(24)
            .and_then(|n| dirs_at.checked_add(n))
            .ok_or_else(|| error("Table des dossiers NUP trop grande."))?;
        let children_at = file_count
            .checked_mul(80)
            .and_then(|n| files_at.checked_add(n))
            .ok_or_else(|| error("Table des fichiers NUP trop grande."))?;
        let strings_end = strings_offset
            .checked_add(strings_size)
            .ok_or_else(|| error("Table des noms NUP trop grande."))?;
        if children_at > strings_offset
            || strings_end > toc.len()
            || !(strings_offset - children_at).is_multiple_of(4)
        {
            return Err(error("Tables de la TOC NUP incohérentes ou tronquées."));
        }
        let mut titles = Vec::new();
        let mut directories = Vec::new();
        let mut files = Vec::new();
        let mut children = Vec::new();
        titles
            .try_reserve_exact(title_count)
            .map_err(|_| error("Trop de titres NUP."))?;
        directories
            .try_reserve_exact(dir_count)
            .map_err(|_| error("Trop de dossiers NUP."))?;
        files
            .try_reserve_exact(file_count)
            .map_err(|_| error("Trop de fichiers NUP."))?;
        let child_count = (strings_offset - children_at) / 4;
        children
            .try_reserve_exact(child_count)
            .map_err(|_| error("Trop d'enfants NUP."))?;
        for index in 0..title_count {
            let base = titles_at + index * 0x40;
            let exefs = read_u32(&toc, base + 12)?;
            let romfs = read_u32(&toc, base + 16)?;
            titles.push(Title {
                id: read_u64(&toc, base)?,
                root: read_u32(&toc, base + 8)?,
                exefs: (exefs != NO_NODE).then_some(exefs),
                romfs: (romfs != NO_NODE).then_some(romfs),
                version: read_u32(&toc, base + 20)?,
            });
        }
        for index in 0..dir_count {
            let base = dirs_at + index * 24;
            directories.push(Directory {
                name_offset: read_u32(&toc, base)?,
                name_length: read_u32(&toc, base + 4)?,
                first_child: read_u32(&toc, base + 8)?,
                child_count: read_u32(&toc, base + 12)?,
            });
        }
        for index in 0..file_count {
            let base = files_at + index * 80;
            let sha256 = toc[base + 32..base + 64].try_into().unwrap();
            files.push(FileEntry {
                name_offset: read_u32(&toc, base)?,
                name_length: read_u32(&toc, base + 4)?,
                size: read_u64(&toc, base + 8)?,
                offset: read_u64(&toc, base + 16)?,
                stored_size: read_u64(&toc, base + 24)?,
                sha256,
                codec: toc[base + 64],
                flags: toc[base + 65],
            });
        }
        for index in 0..child_count {
            children.push(read_u32(&toc, children_at + index * 4)?);
        }
        let mut strings = Vec::new();
        strings
            .try_reserve_exact(strings_size)
            .map_err(|_| error("Noms NUP trop volumineux."))?;
        strings.extend_from_slice(&toc[strings_offset..strings_end]);
        Ok(Self {
            source,
            physical_size,
            declared_size,
            blob_offset,
            toc_hash_present,
            titles,
            directories,
            files,
            children,
            strings,
        })
    }

    pub fn directory_count(&self) -> usize {
        self.directories.len()
    }

    pub fn file_count(&self) -> usize {
        self.files.len()
    }

    fn name(&self, offset: u32, length: u32) -> Result<String, NupError> {
        let offset = offset as usize;
        let length = length as usize;
        if length > MAX_NAME {
            return Err(error("Nom NUP supérieur à 4096 octets."));
        }
        let end = offset
            .checked_add(length)
            .ok_or_else(|| error("Nom NUP hors limites."))?;
        let bytes = self
            .strings
            .get(offset..end)
            .ok_or_else(|| error("Nom hors de la table NUP."))?;
        Ok(String::from_utf8_lossy(bytes).into_owned())
    }

    /// Liste les fichiers d'un titre et d'une partie, avec détection de cycles.
    /// `part` vaut `None`, `Some("romfs")` ou `Some("exefs")`.
    pub fn members(&self, title_index: usize, part: Option<&str>) -> Result<Vec<Member>, NupError> {
        let title = self
            .titles
            .get(title_index)
            .ok_or_else(|| error(format!("Titre NUP #{title_index} absent.")))?;
        if part.is_some_and(|value| value != "romfs" && value != "exefs") {
            return Err(error("Partie NUP inconnue : choisir romfs ou exefs."));
        }
        let mut members = Vec::new();
        let mut total_path_bytes = 0usize;
        for (label, node) in [("exefs", title.exefs), ("romfs", title.romfs)] {
            if part.is_some_and(|wanted| wanted != label) {
                continue;
            }
            let Some(node) = node else { continue };
            let mut seen = HashSet::new();
            let mut stack = vec![(node as usize, format!("{label}/"), 0usize)];
            while let Some((index, prefix, depth)) = stack.pop() {
                if !seen.insert(index) {
                    return Err(error("Boucle ou dossier répété dans l'arborescence NUP."));
                }
                let dir = self
                    .directories
                    .get(index)
                    .ok_or_else(|| error("Index de dossier NUP invalide."))?;
                let start = dir.first_child as usize;
                let end = start
                    .checked_add(dir.child_count as usize)
                    .ok_or_else(|| error("Liste d'enfants NUP trop grande."))?;
                let children = self
                    .children
                    .get(start..end)
                    .ok_or_else(|| error("Liste d'enfants NUP incohérente."))?;
                for child in children {
                    let child = *child as usize;
                    if child < self.directories.len() {
                        if depth >= MAX_DEPTH {
                            return Err(error("Arborescence NUP trop profonde."));
                        }
                        let dir = &self.directories[child];
                        let name = self.name(dir.name_offset, dir.name_length)?;
                        let path = format!("{prefix}{name}/");
                        account_path(&path, &mut total_path_bytes)?;
                        stack.push((child, path, depth + 1));
                    } else {
                        let file_index = child - self.directories.len();
                        let file = self
                            .files
                            .get(file_index)
                            .ok_or_else(|| error("Index d'enfant NUP invalide."))?;
                        let name = self.name(file.name_offset, file.name_length)?;
                        let path = format!("{prefix}{name}");
                        account_path(&path, &mut total_path_bytes)?;
                        members.push(Member {
                            path,
                            file_index,
                            size: file.size,
                            stored_size: file.stored_size,
                            codec: file.codec,
                        });
                    }
                }
            }
        }
        Ok(members)
    }

    /// Métadonnées d'une entrée, sans lire son contenu.
    pub fn file_entry(&self, index: usize) -> Result<&FileEntry, NupError> {
        self.files
            .get(index)
            .ok_or_else(|| error("Index de fichier NUP invalide."))
    }

    /// Vérifie le codec, l'alignement et les bornes physiques sans lire l'entrée.
    pub fn check_member(&self, index: usize) -> Result<(), NupError> {
        let entry = self.file_entry(index)?;
        if entry.offset < self.blob_offset || !entry.offset.is_multiple_of(16) {
            return Err(error("Position de fichier NUP invalide ou non alignée."));
        }
        if entry.codec > 1 {
            return Err(error(format!(
                "Codec NUP {} non pris en charge.",
                entry.codec
            )));
        }
        if entry
            .offset
            .checked_add(entry.stored_size)
            .is_none_or(|end| end > self.physical_size)
        {
            return Err(error("Données NUP hors du fichier ou paquet tronqué."));
        }
        Ok(())
    }

    /// Lit une entrée en décompressant uniquement la couche NUP. Une ressource
    /// `.zs` déjà présente dans romfs est donc conservée telle quelle.
    pub fn read_member(
        &mut self,
        index: usize,
        max_input: usize,
        max_output: usize,
    ) -> Result<Vec<u8>, NupError> {
        self.check_member(index)?;
        let entry = self.file_entry(index)?.clone();
        if entry.size > max_output as u64 {
            return Err(error(format!(
                "Fichier NUP supérieur à la limite de {max_output} octets."
            )));
        }
        let raw = read_at(
            &mut self.source,
            self.physical_size,
            entry.offset,
            entry.stored_size,
            max_input,
        )?;
        // Le codec 0 est normalement brut. Si les tailles ne correspondent pas
        // et que l'en-tête confirme la taille, on accepte la compression NUP
        // implicite reconnue par le lecteur Python. Une ressource .zs brute
        // dont la taille stockée correspond est toujours préservée.
        let implicit = entry.codec == 0
            && entry.size != 0
            && raw.len() as u64 != entry.size
            && raw.starts_with(b"\x28\xb5\x2f\xfd")
            && zs::frame_content_size(&raw).is_none_or(|size| size == entry.size);
        let data = if entry.codec == 1 || implicit {
            zs::decompress(&raw, &Dictionaries::new(), None, max_output)
                .map_err(|err| error(format!("Décompression d'entrée NUP impossible : {err}")))?
        } else {
            raw
        };
        if data.len() > max_output || (entry.size != 0 && data.len() as u64 != entry.size) {
            return Err(error(format!(
                "Taille de fichier NUP incohérente : {} au lieu de {}.",
                data.len(),
                entry.size
            )));
        }
        // Le compteur de bits de la dépendance SHA doit rester représentable
        // également sur les architectures 32 bits.
        if data.len() > usize::MAX / 8 {
            return Err(error(
                "Fichier trop grand pour vérifier SHA-256 sur cette architecture.",
            ));
        }
        if entry.sha256.iter().any(|byte| *byte != 0) && Hash::hash(&data) != entry.sha256 {
            return Err(error("SHA-256 de l'entrée NUP incorrect."));
        }
        Ok(data)
    }
}

fn account_path(path: &str, total: &mut usize) -> Result<(), NupError> {
    if path.len() > MAX_PATH {
        return Err(error("Chemin NUP supérieur à 4096 octets."));
    }
    *total = total
        .checked_add(path.len())
        .ok_or_else(|| error("Chemins NUP trop volumineux."))?;
    if *total > MAX_PATHS_TOTAL {
        return Err(error("Chemins NUP supérieurs à 64 Mio."));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn large_packages_only_read_the_header_and_toc() {
        use std::cell::Cell;
        use std::rc::Rc;

        struct VirtualLargeFile {
            data: Cursor<Vec<u8>>,
            physical_size: u64,
            bytes_read: Rc<Cell<usize>>,
        }
        impl Read for VirtualLargeFile {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                let read = self.data.read(buffer)?;
                self.bytes_read.set(self.bytes_read.get() + read);
                Ok(read)
            }
        }
        impl Seek for VirtualLargeFile {
            fn seek(&mut self, position: SeekFrom) -> std::io::Result<u64> {
                match position {
                    SeekFrom::End(0) => self.data.seek(SeekFrom::Start(self.physical_size)),
                    other => self.data.seek(other),
                }
            }
        }
        let mut bytes = vec![0u8; 0x80 + 0x30];
        bytes[..4].copy_from_slice(MAGIC);
        bytes[8..16].copy_from_slice(&0x80u64.to_le_bytes());
        bytes[16..24].copy_from_slice(&0x30u64.to_le_bytes());
        bytes[0x80..0x84].copy_from_slice(TOC_MAGIC);
        bytes[0x80 + 24..0x80 + 32].copy_from_slice(&0x30u64.to_le_bytes());
        let bytes_read = Rc::new(Cell::new(0));
        let source = VirtualLargeFile {
            data: Cursor::new(bytes),
            physical_size: 16 * 1024 * 1024 * 1024,
            bytes_read: Rc::clone(&bytes_read),
        };
        let package = Package::parse(source).unwrap();
        assert_eq!(package.physical_size, 16 * 1024 * 1024 * 1024);
        assert_eq!(bytes_read.get(), 0x80 + 0x30);
        assert_eq!(package.file_count(), 0);
    }

    #[test]
    fn sha256_and_invalid_headers() {
        assert_eq!(
            Hash::hash(b"abc"),
            [
                0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae,
                0x22, 0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61,
                0xf2, 0x00, 0x15, 0xad,
            ]
        );
        assert!(Package::parse(Cursor::new(b"NUP\x01".to_vec())).is_err());
        assert!(Package::parse(Cursor::new(vec![0; 0x80])).is_err());
    }
}
