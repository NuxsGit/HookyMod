//! Petites règles de noms pour les téléchargements de la webview native.
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// Ne conserve jamais de séparateur ni de caractère interdit sous Windows.
pub(crate) fn safe_stem(input: &str) -> String {
    let name = input.rsplit(['/', '\\']).next().unwrap_or("");
    let lower = name.to_ascii_lowercase();
    // Longer compound extensions must be checked before their suffixes.
    let size = [
        ".animation.bfres.zs",
        ".animation.bfres",
        ".anim.bfres.zs",
        ".anim.bfres",
        ".bwav.zs",
        ".bwav",
        ".webm",
    ]
    .iter()
    .find(|suffix| lower.ends_with(**suffix))
    .map_or(name.len(), |suffix| name.len() - suffix.len());
    let stem: String = name[..size]
        .chars()
        .take(80)
        .map(|c| {
            if c.is_alphanumeric() || matches!(c, '-' | '_' | '.' | ' ' | '\'') {
                c
            } else {
                '_'
            }
        })
        .collect();
    let stem = stem.trim_matches([' ', '.']);
    if stem.is_empty() {
        "son".to_owned()
    } else {
        stem.to_owned()
    }
}

/// Map only supported backend downloads to fixed extensions. Never use a
/// query parameter as an unchecked filename extension.
pub(crate) fn download_target(
    route: &str,
    source: &str,
    format: &str,
    kind: &str,
) -> Option<(String, &'static str)> {
    match route {
        "/api/export/download" => match kind {
            "" => Some(("hookycreator-modele".into(), "zip")),
            "selection" => Some(("hookycreator-selection".into(), "zip")),
            "glb" => Some(("hookycreator-animations".into(), "glb")),
            _ => None,
        },
        "/api/sound/ogg" => Some((safe_stem(source), "opus")),
        "/api/sound/convert" => {
            let extension = match format.to_ascii_lowercase().as_str() {
                "" | "wav" => "wav",
                "flac" => "flac",
                "mp3" => "mp3",
                "ogg" => "ogg",
                "opus" => "opus",
                _ => return None,
            };
            Some((safe_stem(source), extension))
        }
        "/api/video/stream" => Some((safe_stem(source), "webm")),
        "/api/animation/clip" => Some((safe_stem(source), "json")),
        _ => None,
    }
}

/// Génère un nom inédit dans un dossier absolu, sans remplacer un ancien export.
/// Le suffixe temporel et le compteur évitent aussi les collisions simultanées.
pub(crate) fn destination(dir: &Path, stem: &str, extension: &str) -> io::Result<PathBuf> {
    if !dir.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "dossier non absolu",
        ));
    }
    std::fs::create_dir_all(dir)?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    for _ in 0..64 {
        let count = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = dir.join(format!("{}--{stamp}-{count}.{extension}", safe_stem(stem)));
        if !path.exists() {
            return Ok(path);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "nom de téléchargement indisponible",
    ))
}

pub(crate) fn valid_token(token: &str) -> bool {
    (43..=128).contains(&token.len())
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filename_cannot_escape_or_use_windows_separators() {
        assert_eq!(safe_stem("../../Voix d'Épona.bwav.zs"), "Voix d'Épona");
        assert_eq!(safe_stem(r"C:\\test\\..\\a:b?.bwav"), "a_b_");
        assert_eq!(safe_stem("..."), "son");
        assert_eq!(safe_stem("x\n\".bwav"), "x__");
        assert_eq!(safe_stem(&"a".repeat(120)).chars().count(), 80);
    }

    #[test]
    fn media_downloads_use_fixed_safe_extensions() {
        assert_eq!(
            download_target("/api/video/stream", "../../Movie.webm", "", ""),
            Some(("Movie".into(), "webm"))
        );
        assert_eq!(
            download_target("/api/animation/clip", "Heros.anim.bfres.zs", "", ""),
            Some(("Heros".into(), "json"))
        );
        assert_eq!(
            download_target("/api/animation/clip", "Heros.animation.bfres", "", ""),
            Some(("Heros".into(), "json"))
        );
        for format in ["wav", "flac", "mp3", "ogg", "opus"] {
            assert_eq!(
                download_target("/api/sound/convert", "Voix.bwav.zs", format, ""),
                Some(("Voix".into(), format))
            );
        }
        assert!(download_target("/api/sound/convert", "x.bwav", "../../exe", "").is_none());
        assert!(download_target("/api/studio/export", "../../evil.exe", "exe", "").is_none());
        assert!(download_target("/api/unknown", "x", "wav", "").is_none());
    }

    #[test]
    fn model_downloads_use_fixed_extensions_for_each_kind() {
        assert_eq!(
            download_target("/api/export/download", "../../bad.exe", "", ""),
            Some(("hookycreator-modele".into(), "zip"))
        );
        assert_eq!(
            download_target("/api/export/download", "", "", "selection"),
            Some(("hookycreator-selection".into(), "zip"))
        );
        assert_eq!(
            download_target("/api/export/download", "../../bad.exe", "exe", "glb"),
            Some(("hookycreator-animations".into(), "glb"))
        );
        assert!(download_target("/api/export/download", "", "", "../../exe").is_none());
    }

    #[test]
    fn successive_destinations_differ_and_stay_in_the_target() {
        let dir = std::env::temp_dir().join(format!("hooky-download-test-{}", std::process::id()));
        let first = destination(&dir, "../unsafe", "zip").unwrap();
        let second = destination(&dir, "../unsafe", "zip").unwrap();
        assert_ne!(first, second);
        assert_eq!(first.parent(), Some(dir.as_path()));
        assert!(first.extension().unwrap() == "zip");
        let _ = std::fs::remove_dir(dir);
        assert!(destination(Path::new("relative"), "name", "zip").is_err());
    }

    #[test]
    fn tokens_are_ascii_url_safe_and_long_enough() {
        assert!(valid_token(&"a".repeat(43)));
        assert!(valid_token(&"_-".repeat(32)));
        assert!(!valid_token("short"));
        assert!(!valid_token(&"é".repeat(43)));
        assert!(!valid_token(&format!("{}=", "a".repeat(43))));
    }
}
