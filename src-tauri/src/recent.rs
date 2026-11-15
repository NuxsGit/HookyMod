//! Dernier projet choisi dans la fenêtre native, sans jeton ni données du dump.
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const MAX_BYTES: u64 = 32 * 1024;
static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

#[derive(Serialize, Deserialize)]
struct RecentProject {
    version: u8,
    project: PathBuf,
}

/// Un fichier absent, modifié ou un dossier disparu ne bloque jamais le démarrage.
pub fn load(path: &Path) -> Option<PathBuf> {
    let meta = fs::symlink_metadata(path).ok()?;
    if !meta.is_file() || meta.file_type().is_symlink() || meta.len() > MAX_BYTES {
        return None;
    }
    let mut bytes = Vec::new();
    fs::File::open(path)
        .ok()?
        .take(MAX_BYTES + 1)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.len() as u64 > MAX_BYTES {
        return None;
    }
    let saved: RecentProject = serde_json::from_slice(&bytes).ok()?;
    if saved.version != 1 || !saved.project.is_absolute() || !saved.project.is_dir() {
        return None;
    }
    Some(saved.project)
}

/// L'ancien fichier reste intact en cas d'écriture ou de remplacement raté.
pub fn save(path: &Path, project: &Path) -> io::Result<()> {
    if !project.is_absolute() || !project.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Projet inaccessible",
        ));
    }
    let data = serde_json::to_vec(&RecentProject {
        version: 1,
        project: project.to_path_buf(),
    })
    .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    if data.len() as u64 > MAX_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Chemin trop long",
        ));
    }
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Dossier absent"))?;
    fs::create_dir_all(parent)?;
    let temp = parent.join(format!(
        ".recent-{}-{}.tmp",
        std::process::id(),
        NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temp)?;
        file.write_all(&data)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temp, path)
    })();
    let _ = fs::remove_file(temp);
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_missing_or_corrupt_project_fall_back() {
        let base = std::env::temp_dir().join(format!(
            "hooky-recent-test-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        let project = base.join("projet é");
        let state = base.join("config/recent.json");
        fs::create_dir_all(&project).unwrap();
        save(&state, &project).unwrap();
        assert_eq!(load(&state), Some(project.clone()));
        // Replacing an existing file must work on Windows too.
        save(&state, &project).unwrap();
        assert_eq!(load(&state), Some(project.clone()));
        fs::remove_dir(&project).unwrap();
        assert_eq!(load(&state), None);
        fs::write(&state, b"{ incomplete").unwrap();
        assert_eq!(load(&state), None);
        fs::write(&state, vec![b'x'; MAX_BYTES as usize + 1]).unwrap();
        assert_eq!(load(&state), None);
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn relative_paths_and_wrong_versions_are_not_reopened() {
        let base = std::env::temp_dir().join(format!(
            "hooky-recent-test-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&base).unwrap();
        let state = base.join("recent.json");
        assert!(save(&state, Path::new("relative")).is_err());
        fs::write(&state, br#"{"version":1,"project":"relative"}"#).unwrap();
        assert_eq!(load(&state), None);
        let wrong = serde_json::json!({"version": 2, "project": base});
        fs::write(&state, serde_json::to_vec(&wrong).unwrap()).unwrap();
        assert_eq!(load(&state), None);
        fs::remove_dir_all(base).unwrap();
    }
}
