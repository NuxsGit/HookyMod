//! Commandes autonomes Rust en cours de portage. Elles ne remplacent pas
//! encore toutes les commandes Python ni l'interface HookyCreator.

use std::ffi::{OsStr, OsString};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use hookymod_core::nup::Package;
use hookymod_core::restbl::ResourceSizeTable;
use hookymod_core::sarc::{Archive, Entry};
use hookymod_core::zs::{self, Dictionaries};

const MAX_INPUT: usize = 1024 * 1024 * 1024;
const MAX_DICTIONARY_INPUT: usize = 64 * 1024 * 1024;
const MAX_CONFIGURED_OUTPUT: u64 = 2 * 1024 * 1024 * 1024;

const HELP: &str = "HookyMod Rust — commandes autonomes en cours de portage\n\n\
Usage : hookymod-rs [options] <commande>\n\n\
Options (avant la commande) :\n  \
  --romfs <dossier>        charger Pack/ZsDic.pack.zs du dump\n  \
  --dict <fichier>         charger un dictionnaire ou ZsDic.pack[.zs]\n  \
  --force-dict <id>        utiliser explicitement cet id pour les trames sans id\n  \
  --max-output <octets>    limite de sortie (défaut : 536870912)\n\n\
Commandes :\n  \
  hookymod-rs zs decompress <fichier.zs> <nouveau-fichier>\n  \
  hookymod-rs zs compress <fichier> <nouveau-fichier.zs> --no-dict\n  \
  hookymod-rs sarc list <archive[.zs]> [--json]\n  \
  hookymod-rs sarc extract <archive[.zs]> <entrée> <nouveau-fichier>\n  \
  hookymod-rs sarc replace <archive[.zs]> <entrée> <fichier> <nouvelle-archive>\n  \
  hookymod-rs sarc add <archive[.zs]> <entrée> <fichier> <nouvelle-archive>\n  \
  hookymod-rs sarc remove <archive[.zs]> <entrée> <nouvelle-archive>\n  \
  hookymod-rs restbl info <table[.zs]> [--json]\n  \
  hookymod-rs restbl get <table[.zs]> <chemin-romfs>\n  \
  hookymod-rs restbl ensure <table[.zs]> <chemin-romfs> <octets> <nouvelle-table>\n  \
  hookymod-rs nup info <paquet> [--json]\n  \
  hookymod-rs nup list <paquet> [--title <index>] [--only romfs|exefs] [--json]\n  \
  hookymod-rs nup verify <paquet> [--title <index>] [--only romfs|exefs] [--deep] [--json]\n  \
  hookymod-rs nup extract <paquet> <chemin-interne> <nouveau-fichier> [--title <index>]\n\n\
Les sorties SARC et RESTBL modifiées sont non compressées. La compression\n\
avec dictionnaire n'est pas encore disponible ; --no-dict est explicite et\n\
ne garantit pas la compatibilité avec le jeu. Aucun fichier existant n'est\n\
écrasé. Le reste de HookyMod dépend toujours de Python.";

fn text<'a>(arg: &'a OsStr, what: &str) -> Result<&'a str, String> {
    arg.to_str()
        .ok_or_else(|| format!("{what} n'est pas une chaîne Unicode valide."))
}

#[derive(Debug)]
struct Options {
    dictionary_paths: Vec<PathBuf>,
    force_dictionary: Option<u32>,
    max_output: usize,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            dictionary_paths: Vec::new(),
            force_dictionary: None,
            max_output: zs::DEFAULT_MAX_OUTPUT,
        }
    }
}

fn options(args: &[OsString]) -> Result<(Options, &[OsString]), String> {
    let mut opts = Options::default();
    let mut index = 0;
    while let Some(arg) = args.get(index) {
        if arg == "--" {
            index += 1;
            break;
        }
        if !["--dict", "--romfs", "--force-dict", "--max-output"]
            .iter()
            .any(|option| arg == option)
        {
            break;
        }
        let value = args
            .get(index + 1)
            .ok_or_else(|| format!("Valeur manquante après {}.", arg.to_string_lossy()))?;
        match arg.to_str().unwrap_or("") {
            "--dict" => opts.dictionary_paths.push(PathBuf::from(value)),
            "--romfs" => opts
                .dictionary_paths
                .push(Path::new(value).join("Pack").join("ZsDic.pack.zs")),
            "--force-dict" => {
                if opts.force_dictionary.is_some() {
                    return Err("--force-dict ne peut être fourni qu'une fois.".into());
                }
                opts.force_dictionary = Some(
                    text(value, "L'identifiant du dictionnaire")?
                        .parse::<u32>()
                        .map_err(|_| {
                            "L'identifiant du dictionnaire doit être un entier de 0 à 4294967295."
                                .to_string()
                        })?,
                );
            }
            "--max-output" => {
                let size = text(value, "La limite de sortie")?
                    .parse::<u64>()
                    .map_err(|_| {
                        "La limite de sortie doit être un nombre entier d'octets.".to_string()
                    })?;
                if size > MAX_CONFIGURED_OUTPUT || size > isize::MAX as u64 {
                    return Err("La limite de sortie ne peut pas dépasser 2 Gio ni la capacité de cette architecture.".into());
                }
                opts.max_output = size as usize;
            }
            _ => unreachable!(),
        }
        index += 2;
    }
    Ok((opts, &args[index..]))
}

/// Lecture bornée même si un fichier s'agrandit après l'inspection de sa taille.
fn read_limited(path: &Path, limit: usize) -> Result<Vec<u8>, String> {
    let mut file = File::open(path)
        .map_err(|err| format!("Lecture impossible : {} ({err}).", path.display()))?;
    if let Ok(meta) = file.metadata() {
        if meta.len() > limit as u64 {
            return Err(format!(
                "{} dépasse la limite de lecture de {limit} octets.",
                path.display()
            ));
        }
    }
    let mut data = Vec::new();
    let mut chunk = [0u8; 64 * 1024];
    loop {
        let count = file
            .read(&mut chunk)
            .map_err(|err| format!("Lecture impossible : {} ({err}).", path.display()))?;
        if count == 0 {
            break;
        }
        if count > limit.saturating_sub(data.len()) {
            return Err(format!(
                "{} dépasse la limite de lecture de {limit} octets.",
                path.display()
            ));
        }
        data.try_reserve(count)
            .map_err(|_| format!("Mémoire insuffisante pour lire {}.", path.display()))?;
        data.extend_from_slice(&chunk[..count]);
    }
    Ok(data)
}

struct Context {
    dictionaries: Dictionaries,
    force_dictionary: Option<u32>,
    max_output: usize,
}

impl Context {
    fn new(opts: Options) -> Result<Self, String> {
        let mut dictionaries = Dictionaries::new();
        for path in opts.dictionary_paths {
            let data = read_limited(&path, MAX_DICTIONARY_INPUT)?;
            let name = path.file_name().unwrap_or(OsStr::new("")).to_string_lossy();
            dictionaries
                .load(&name, &data)
                .map_err(|err| format!("Dictionnaire {} : {err}", path.display()))?;
        }
        Ok(Self {
            dictionaries,
            force_dictionary: opts.force_dictionary,
            max_output: opts.max_output,
        })
    }

    fn read(&self, path: &OsStr) -> Result<Vec<u8>, String> {
        let data = read_limited(Path::new(path), MAX_INPUT)?;
        if zs::is_zstd(&data) {
            zs::decompress(
                &data,
                &self.dictionaries,
                self.force_dictionary,
                self.max_output,
            )
            .map_err(|err| err.to_string())
        } else if data.len() > self.max_output {
            Err(format!(
                "Fichier supérieur à la limite de sortie de {} octets.",
                self.max_output
            ))
        } else {
            Ok(data)
        }
    }
}

/// N'ouvre jamais un fichier existant en écriture et retire la sortie partielle
/// si l'écriture échoue. Le chemin est explicite, pas un nom venu du SARC.
fn write_new(path: &OsStr, data: &[u8]) -> Result<(), String> {
    let path = Path::new(path);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|err| {
            format!(
                "Création impossible : {} ({err}). Aucun fichier existant n'est écrasé.",
                path.display()
            )
        })?;
    if let Err(err) = file.write_all(data).and_then(|()| file.flush()) {
        drop(file);
        let _ = fs::remove_file(path);
        return Err(format!("Écriture impossible : {} ({err}).", path.display()));
    }
    Ok(())
}

fn require_plain_output(path: &OsStr) -> Result<(), String> {
    if Path::new(path)
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("zs"))
    {
        return Err("La sortie est non compressée : choisis un nom sans extension .zs. La compression avec dictionnaire n'est pas encore portée.".into());
    }
    Ok(())
}

fn json_string(input: &str) -> String {
    let mut out = String::with_capacity(input.len() + 2);
    out.push('"');
    for ch in input.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if c < '\u{20}' => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn zs_decompress(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.len() != 2 {
        return Err(
            "Usage : hookymod-rs [options] zs decompress <fichier.zs> <nouveau-fichier>".into(),
        );
    }
    require_plain_output(&args[1])?;
    let input = read_limited(Path::new(&args[0]), MAX_INPUT)?;
    let output = zs::decompress(
        &input,
        &ctx.dictionaries,
        ctx.force_dictionary,
        ctx.max_output,
    )
    .map_err(|err| err.to_string())?;
    write_new(&args[1], &output)
}

fn zs_compress(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.len() != 3 || args[2] != "--no-dict" {
        return Err(
            "Usage : hookymod-rs zs compress <fichier> <nouveau-fichier.zs> --no-dict".into(),
        );
    }
    if ctx.force_dictionary.is_some() {
        return Err("La compression avec dictionnaire n'est pas encore disponible en Rust.".into());
    }
    let input = read_limited(Path::new(&args[0]), ctx.max_output.min(MAX_INPUT))?;
    let output = zs::compress_without_dictionary(&input).map_err(|err| err.to_string())?;
    if output.len() > MAX_INPUT {
        return Err(format!(
            "Trame zstd supérieure à la limite de {MAX_INPUT} octets."
        ));
    }
    write_new(&args[1], &output)?;
    eprintln!("Compression sans dictionnaire ; compatibilité avec le jeu non garantie.");
    Ok(())
}

fn sarc_list(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.len() != 1 && (args.len() != 2 || args[1] != "--json") {
        return Err("Usage : hookymod-rs sarc list <archive> [--json]".into());
    }
    let data = ctx.read(&args[0])?;
    let archive = Archive::parse(&data).map_err(|err| err.to_string())?;
    if args.len() == 2 {
        print!(
            "{{\"format\":\"SARC\",\"endian\":{},\"version\":{},\"hash_mult\":{},\"warnings\":[",
            json_string(archive.endian.name()),
            archive.version,
            archive.hash_mult
        );
        for (i, warning) in archive.warnings.iter().enumerate() {
            if i != 0 {
                print!(",");
            }
            print!("{}", json_string(warning));
        }
        print!("],\"entries\":[");
        for (i, entry) in archive.entries.iter().enumerate() {
            if i != 0 {
                print!(",");
            }
            print!(
                "{{\"name\":{},\"size\":{},\"alignment\":{},\"hash\":{}}}",
                json_string(&entry.name),
                entry.data.len(),
                entry.alignment,
                entry.hash
            );
        }
        println!("]}}");
    } else {
        println!(
            "SARC {} : {} entrée(s)",
            archive.endian.name(),
            archive.entries.len()
        );
        for entry in &archive.entries {
            println!("{:10} o  {}", entry.data.len(), entry.name);
        }
        for warning in &archive.warnings {
            eprintln!("Avertissement : {warning}");
        }
    }
    Ok(())
}

fn sarc_extract(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.len() != 3 {
        return Err("Usage : hookymod-rs sarc extract <archive> <entrée> <nouveau-fichier>".into());
    }
    let data = ctx.read(&args[0])?;
    let archive = Archive::parse(&data).map_err(|err| err.to_string())?;
    let name = text(&args[1], "Le nom de l'entrée")?;
    let entry = archive
        .get(name)
        .ok_or_else(|| format!("Entrée « {name} » absente de l'archive."))?;
    write_new(&args[2], entry.data)
}

fn sarc_change(ctx: &Context, action: &str, args: &[OsString]) -> Result<(), String> {
    let expected = if action == "remove" { 3 } else { 4 };
    if args.len() != expected {
        return Err(format!(
            "Usage : hookymod-rs sarc {action} <archive> <entrée> {}<nouvelle-archive>",
            if action == "remove" { "" } else { "<fichier> " }
        ));
    }
    require_plain_output(&args[expected - 1])?;
    let data = ctx.read(&args[0])?;
    let replacement = if action == "remove" {
        None
    } else {
        Some(read_limited(
            Path::new(&args[2]),
            ctx.max_output.min(MAX_INPUT),
        )?)
    };
    let name = text(&args[1], "Le nom de l'entrée")?;
    let mut archive = Archive::parse(&data).map_err(|err| err.to_string())?;
    match action {
        "replace" => archive
            .replace(name, replacement.as_deref().unwrap())
            .map_err(|err| err.to_string())?,
        "add" => {
            if archive.get(name).is_some() {
                return Err(format!("« {name} » existe déjà dans l'archive."));
            }
            archive
                .entries
                .push(Entry::new(name, replacement.as_deref().unwrap()));
        }
        "remove" => {
            let index = archive
                .entries
                .iter()
                .position(|entry| entry.name == name)
                .ok_or_else(|| format!("Entrée « {name} » absente de l'archive."))?;
            archive.entries.remove(index);
        }
        _ => return Err("Commande SARC inconnue.".into()),
    }
    let output = archive
        .build_with_limit(true, ctx.max_output)
        .map_err(|err| err.to_string())?;
    write_new(&args[expected - 1], &output)?;
    println!("Archive SARC non compressée créée.");
    Ok(())
}

fn restbl_info(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.len() != 1 && (args.len() != 2 || args[1] != "--json") {
        return Err("Usage : hookymod-rs restbl info <table> [--json]".into());
    }
    let data = ctx.read(&args[0])?;
    let table = ResourceSizeTable::parse(&data).map_err(|err| err.to_string())?;
    if args.len() == 2 {
        println!("{{\"format\":\"RESTBL\",\"version\":{},\"name_length\":{},\"hashed\":{},\"named\":{},\"tail_bytes\":{}}}",
                 table.version, table.name_length, table.sizes.len(), table.named_count, table.tail.len());
    } else {
        println!(
            "RESTBL v{} : {} entrée(s) hachée(s), {} nommée(s), {} octet(s) conservé(s).",
            table.version,
            table.sizes.len(),
            table.named_count,
            table.tail.len()
        );
    }
    Ok(())
}

fn restbl_get(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.len() != 2 {
        return Err("Usage : hookymod-rs restbl get <table> <chemin-romfs>".into());
    }
    let data = ctx.read(&args[0])?;
    let table = ResourceSizeTable::parse(&data).map_err(|err| err.to_string())?;
    let name = text(&args[1], "Le chemin")?;
    match table.get(name) {
        Some(size) => {
            println!("{size}");
            Ok(())
        }
        None => Err(format!("Aucune réservation pour « {name} ».")),
    }
}

fn restbl_ensure(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.len() != 4 {
        return Err(
            "Usage : hookymod-rs restbl ensure <table> <chemin-romfs> <octets> <nouvelle-table>"
                .into(),
        );
    }
    require_plain_output(&args[3])?;
    let data = ctx.read(&args[0])?;
    let mut table = ResourceSizeTable::parse(&data).map_err(|err| err.to_string())?;
    let name = text(&args[1], "Le chemin")?;
    let size = text(&args[2], "La taille")?
        .parse::<u32>()
        .map_err(|_| "La taille doit être un entier entre 0 et 4294967295.".to_string())?;
    let changed = table.ensure(name, size).map_err(|err| err.to_string())?;
    let output = table
        .build_with_limit(ctx.max_output)
        .map_err(|err| err.to_string())?;
    write_new(&args[3], &output)?;
    println!(
        "{} : {} octets réservés{} ; table non compressée.",
        name,
        table.get(name).unwrap_or(0),
        if changed {
            " (mise à jour)"
        } else {
            " (inchangé)"
        }
    );
    Ok(())
}

fn nup_info(args: &[OsString]) -> Result<(), String> {
    if args.len() != 1 && (args.len() != 2 || args[1] != "--json") {
        return Err("Usage : hookymod-rs nup info <paquet> [--json]".into());
    }
    let package = Package::open(Path::new(&args[0])).map_err(|err| err.to_string())?;
    if args.len() == 2 {
        print!("{{\"format\":\"NUP\",\"bytes\":{},\"declared_bytes\":{},\"directories\":{},\"files\":{},\"toc_sha256\":{},\"titles\":[",
               package.physical_size, package.declared_size, package.directory_count(),
               package.file_count(), package.toc_hash_present);
        for (index, title) in package.titles.iter().enumerate() {
            if index != 0 {
                print!(",");
            }
            print!(
                "{{\"index\":{},\"id\":\"{:016X}\",\"version\":{},\"romfs\":{},\"exefs\":{}}}",
                index,
                title.id,
                title.version,
                title.romfs.is_some(),
                title.exefs.is_some()
            );
        }
        println!("]}}");
    } else {
        println!(
            "NUP : {} octets, {} dossier(s), {} fichier(s), {} titre(s).",
            package.physical_size,
            package.directory_count(),
            package.file_count(),
            package.titles.len()
        );
        println!(
            "SHA-256 TOC : {}",
            if package.toc_hash_present {
                "vérifié"
            } else {
                "absent"
            }
        );
        for (index, title) in package.titles.iter().enumerate() {
            println!(
                "  titre #{index} {:016X} v{} (romfs : {}, exefs : {})",
                title.id,
                title.version,
                title.romfs.is_some(),
                title.exefs.is_some()
            );
        }
    }
    Ok(())
}

#[derive(Default)]
struct NupSelection<'a> {
    title: Option<usize>,
    part: Option<&'a str>,
    json: bool,
    deep: bool,
}

fn nup_selection(args: &[OsString], allow_deep: bool) -> Result<NupSelection<'_>, String> {
    let mut selection = NupSelection::default();
    let mut index = 0;
    while index < args.len() {
        if args[index] == "--json" && !selection.json {
            selection.json = true;
            index += 1;
        } else if args[index] == "--deep" && allow_deep && !selection.deep {
            selection.deep = true;
            index += 1;
        } else if args[index] == "--title" && selection.title.is_none() {
            let value = args.get(index + 1).ok_or("Index manquant après --title.")?;
            selection.title = Some(
                text(value, "L'index du titre")?
                    .parse::<usize>()
                    .map_err(|_| "L'index du titre doit être un entier positif.".to_string())?,
            );
            index += 2;
        } else if args[index] == "--only" && selection.part.is_none() {
            let value = args
                .get(index + 1)
                .ok_or("Partie manquante après --only.")?;
            let part = text(value, "La partie")?;
            if part != "romfs" && part != "exefs" {
                return Err("Partie NUP inconnue : choisir romfs ou exefs.".into());
            }
            selection.part = Some(part);
            index += 2;
        } else {
            return Err(format!(
                "Option NUP inconnue ou répétée : {}.",
                args[index].to_string_lossy()
            ));
        }
    }
    Ok(selection)
}

fn nup_list(args: &[OsString]) -> Result<(), String> {
    if args.is_empty() {
        return Err(
            "Usage : hookymod-rs nup list <paquet> [--title <index>] [--only romfs|exefs] [--json]"
                .into(),
        );
    }
    let selection = nup_selection(&args[1..], false)?;
    let package = Package::open(Path::new(&args[0])).map_err(|err| err.to_string())?;
    let members = package
        .members(selection.title.unwrap_or(0), selection.part)
        .map_err(|err| err.to_string())?;
    if selection.json {
        print!("{{\"format\":\"NUP\",\"entries\":[");
        for (index, member) in members.iter().enumerate() {
            if index != 0 {
                print!(",");
            }
            print!(
                "{{\"path\":{},\"size\":{},\"stored_size\":{},\"codec\":{}}}",
                json_string(&member.path),
                member.size,
                member.stored_size,
                member.codec
            );
        }
        println!("]}}");
    } else {
        for member in &members {
            println!(
                "{:12} o  {} (codec {})",
                member.size, member.path, member.codec
            );
        }
        println!("{} fichier(s).", members.len());
    }
    Ok(())
}

fn nup_verify(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.is_empty() {
        return Err("Usage : hookymod-rs nup verify <paquet> [--title <index>] [--only romfs|exefs] [--deep] [--json]".into());
    }
    let selection = nup_selection(&args[1..], true)?;
    let mut package = Package::open(Path::new(&args[0])).map_err(|err| err.to_string())?;
    // Sans --title, vérifier tous les titres plutôt que seulement le premier.
    let titles: Vec<usize> = match selection.title {
        Some(index) => vec![index],
        None => (0..package.titles.len()).collect(),
    };
    let mut files = 0usize;
    let mut hashes_absent = 0usize;
    let mut hashes_verified = 0usize;
    let mut bytes_read = 0u64;
    let mut problem_count = 0usize;
    let mut problems: Vec<(usize, String, String)> = Vec::new();
    const MAX_PROBLEMS: usize = 1000;
    for &title in &titles {
        let members = package
            .members(title, selection.part)
            .map_err(|err| err.to_string())?;
        for member in members {
            files += 1;
            let hash_present = package
                .file_entry(member.file_index)
                .map_err(|err| err.to_string())?
                .sha256
                .iter()
                .any(|byte| *byte != 0);
            hashes_absent += usize::from(!hash_present);
            let checked = package.check_member(member.file_index).and_then(|()| {
                if !selection.deep {
                    return Ok(());
                }
                let data = package.read_member(member.file_index, MAX_INPUT, ctx.max_output)?;
                bytes_read = bytes_read.saturating_add(data.len() as u64);
                hashes_verified += usize::from(hash_present);
                if member.path.ends_with(".zs") && !data.starts_with(b"\x28\xb5\x2f\xfd") {
                    return Err(hookymod_core::nup::NupError(
                        "Ressource .zs sans en-tête zstd.".into(),
                    ));
                }
                Ok(())
            });
            if let Err(err) = checked {
                problem_count = problem_count.saturating_add(1);
                if problems.len() < MAX_PROBLEMS {
                    problems.push((title, member.path, err.to_string()));
                }
            }
        }
    }
    if files == 0 {
        problem_count += 1;
        problems.push((
            selection.title.unwrap_or(0),
            String::new(),
            "Aucun fichier trouvé pour la sélection.".into(),
        ));
    }
    let omitted = problem_count.saturating_sub(problems.len());
    if selection.json {
        print!("{{\"format\":\"NUP\",\"deep\":{},\"titles\":{},\"files\":{},\"toc_sha256\":{},\"hashes_verified\":{},\"hashes_absent\":{},\"bytes_read\":{},\"problem_count\":{},\"omitted\":{},\"problems\":[",
            selection.deep, titles.len(), files, package.toc_hash_present,
            hashes_verified, hashes_absent, bytes_read, problem_count, omitted);
        for (index, (title, path, message)) in problems.iter().enumerate() {
            if index != 0 {
                print!(",");
            }
            print!(
                "{{\"title\":{},\"path\":{},\"error\":{}}}",
                title,
                json_string(path),
                json_string(message)
            );
        }
        println!("]}}");
    } else {
        println!("NUP : {files} fichier(s) examiné(s), {problem_count} anomalie(s).");
        if selection.deep {
            println!("{hashes_verified} SHA-256 vérifié(s), {hashes_absent} absent(s) ; {bytes_read} octets lus.");
        } else {
            println!(
                "Contrôle des métadonnées uniquement ; utilise --deep pour lire les contenus."
            );
        }
        for (title, path, message) in &problems {
            eprintln!("  titre #{title} {path} : {message}");
        }
        if omitted != 0 {
            eprintln!("  … {omitted} autre(s) anomalie(s) non affichée(s).");
        }
    }
    if problem_count != 0 {
        return Err(format!("Vérification NUP : {problem_count} anomalie(s)."));
    }
    Ok(())
}

fn nup_extract(ctx: &Context, args: &[OsString]) -> Result<(), String> {
    if args.len() != 3 && (args.len() != 5 || args[3] != "--title") {
        return Err("Usage : hookymod-rs nup extract <paquet> <chemin-interne> <nouveau-fichier> [--title <index>]".into());
    }
    let title = if args.len() == 5 {
        text(&args[4], "L'index du titre")?
            .parse::<usize>()
            .map_err(|_| "L'index du titre doit être un entier positif.".to_string())?
    } else {
        0
    };
    let wanted = text(&args[1], "Le chemin interne")?;
    let mut package = Package::open(Path::new(&args[0])).map_err(|err| err.to_string())?;
    let members = package
        .members(title, None)
        .map_err(|err| err.to_string())?;
    let mut found = members.iter().filter(|member| member.path == wanted);
    let member = found
        .next()
        .ok_or_else(|| format!("Entrée NUP « {wanted} » introuvable."))?;
    if found.next().is_some() {
        return Err(format!(
            "Entrée NUP ambiguë (plusieurs fichiers « {wanted} »)."
        ));
    }
    let data = package
        .read_member(member.file_index, MAX_INPUT, ctx.max_output)
        .map_err(|err| err.to_string())?;
    write_new(&args[2], &data)?;
    println!("Entrée NUP extraite ; SHA-256 vérifié s'il est présent.");
    Ok(())
}

fn run(args: &[OsString]) -> Result<(), String> {
    let (opts, args) = options(args)?;
    if args.is_empty() || (args.len() == 1 && (args[0] == "--help" || args[0] == "-h")) {
        println!("{HELP}");
        return Ok(());
    }
    if args.len() < 2 {
        return Err(format!("Commande incomplète.\n\n{HELP}"));
    }
    let family = text(&args[0], "La commande")?;
    let action = text(&args[1], "L'action")?;
    let rest = &args[2..];
    let ctx = Context::new(opts)?;
    match (family, action) {
        ("zs", "decompress") => zs_decompress(&ctx, rest),
        ("zs", "compress") => zs_compress(&ctx, rest),
        ("sarc", "list") => sarc_list(&ctx, rest),
        ("sarc", "extract") => sarc_extract(&ctx, rest),
        ("sarc", "replace" | "add" | "remove") => sarc_change(&ctx, action, rest),
        ("restbl", "info") => restbl_info(&ctx, rest),
        ("restbl", "get") => restbl_get(&ctx, rest),
        ("restbl", "ensure") => restbl_ensure(&ctx, rest),
        ("nup", "info") => nup_info(rest),
        ("nup", "list") => nup_list(rest),
        ("nup", "verify") => nup_verify(&ctx, rest),
        ("nup", "extract") => nup_extract(&ctx, rest),
        _ => Err(format!("Commande inconnue : {family} {action}.\n\n{HELP}")),
    }
}

fn main() {
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    if let Err(err) = run(&args) {
        eprintln!("Erreur : {err}");
        std::process::exit(2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_json_names() {
        assert_eq!(
            json_string("abc\"\\\n\t\u{01}é"),
            "\"abc\\\"\\\\\\n\\t\\u0001é\""
        );
    }

    #[test]
    fn options_and_output_extensions_are_checked() {
        let args = [
            "--force-dict",
            "0",
            "--max-output",
            "123",
            "zs",
            "decompress",
        ]
        .map(OsString::from);
        let (opts, rest) = options(&args).unwrap();
        assert_eq!(opts.force_dictionary, Some(0));
        assert_eq!(opts.max_output, 123);
        assert_eq!(rest.len(), 2);
        assert!(require_plain_output(OsStr::new("mod.ZS")).is_err());
        assert!(require_plain_output(OsStr::new("mod.sarc")).is_ok());
        assert!(options(&[OsString::from("--dict")]).is_err());
    }
}
