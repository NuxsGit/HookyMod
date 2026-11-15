#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod downloads;
mod recent;

use serde::Deserialize;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem, Submenu},
    webview::{DownloadEvent, NewWindowResponse, Webview},
    AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder, Wry,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

const BACKEND_NAME: &str = if cfg!(windows) {
    "hookycreator-backend.exe"
} else {
    "hookycreator-backend"
};

struct Running {
    child: Child,
    generation: u64,
    root: PathBuf,
    port: u16,
}

#[derive(Default)]
struct Session {
    generation: u64,
    picker_open: bool,
    opening: bool,
    closed: bool,
    quitting: bool,
    checkpoint_serial: u64,
    checkpoint: Option<(u64, mpsc::Sender<bool>)>,
    active: Option<Running>,
    pending: Option<(u64, Child)>,
}

type Shared = Arc<Mutex<Session>>;

#[derive(Deserialize)]
struct StartReply {
    port: Option<u16>,
    token: Option<String>,
    error: Option<String>,
}

struct Ready {
    port: u16,
    token: String,
}

/// L'unique message du backend sur stdout : borné et validé avant navigation.
fn read_ready(reader: &mut impl BufRead) -> Result<Ready, String> {
    let mut line = Vec::new();
    (&mut *reader)
        .take(16_385)
        .read_until(b'\n', &mut line)
        .map_err(|_| "Impossible de lire la réponse du moteur.".to_string())?;
    if !line.ends_with(b"\n") || line.len() > 16_384 {
        return Err("Le moteur s'est arrêté ou n'a pas envoyé de réponse valide.".into());
    }
    let reply: StartReply = serde_json::from_slice(&line)
        .map_err(|_| "Réponse de démarrage du moteur invalide.".to_string())?;
    if let Some(error) = reply.error {
        return Err(error);
    }
    match (reply.port, reply.token) {
        (Some(port), Some(token)) if port != 0 && downloads::valid_token(&token) => {
            Ok(Ready { port, token })
        }
        _ => Err("Session locale invalide.".into()),
    }
}

fn status(app: &AppHandle, message: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let value = serde_json::to_string(message).unwrap_or_else(|_| "\"\"".into());
        let script = format!(
            "if (document.getElementById('desktop-status')) \
             document.getElementById('desktop-status').textContent = {value}; \
             window.dispatchEvent(new CustomEvent('hooky:status', {{detail: {value}}}));"
        );
        let _ = window.eval(script);
    }
}

fn report_error(app: &AppHandle, message: &str) {
    status(app, message);
    // En CI WebDriver, laisser le message lisible sans dialogue modal bloquant.
    #[cfg(debug_assertions)]
    if std::env::var_os("HOOKY_DESKTOP_TEST_PROJECT").is_some() {
        return;
    }
    app.dialog()
        .message(message)
        .title("HookyCreator")
        .kind(MessageDialogKind::Error)
        .show(|_| {});
}

/// Fermer stdin déclenche l'arrêt gracieux, même pendant le scan initial.
/// Ne jamais attendre le processus sur le thread de la fenêtre.
fn stop_child(mut child: Child) {
    drop(child.stdin.take());
    std::thread::spawn(move || {
        let _ = child.wait();
    });
}

/// Le dernier projet est indépendant du port aléatoire et du dossier du dump.
fn recent_file(app: &AppHandle) -> Result<PathBuf, String> {
    // Le test WebDriver isole sa configuration et peut redémarrer sans sélecteur.
    #[cfg(debug_assertions)]
    if let Some(directory) = std::env::var_os("HOOKY_DESKTOP_TEST_CONFIG") {
        return Ok(PathBuf::from(directory).join("recent-project.json"));
    }
    app.path()
        .app_config_dir()
        .map(|path| path.join("recent-project.json"))
        .map_err(|error| error.to_string())
}

/// Le contenu web n'a pas accès aux commandes Tauri. La réponse passe par une
/// navigation locale interceptée et annulée, associée à une seule attente.
fn begin_checkpoint(app: &AppHandle, shared: &Shared) -> Option<(u64, mpsc::Receiver<bool>)> {
    let window = app.get_webview_window("main")?;
    let (id, port, receiver) = {
        let mut session = shared.lock().unwrap();
        let port = session.active.as_ref()?.port;
        if session.closed {
            return None;
        }
        session.checkpoint_serial = session.checkpoint_serial.wrapping_add(1);
        let id = session.checkpoint_serial;
        let (sender, receiver) = mpsc::channel();
        session.checkpoint = Some((id, sender)); // annule l'éventuelle attente précédente
        (id, port, receiver)
    };
    // Avant le chargement d'app.js il n'y a encore aucun état modifié à sauver.
    let script = format!(
        "if (typeof window.__hookyCheckpoint === 'function') window.__hookyCheckpoint({id}); \
         else location.assign('http://127.0.0.1:{port}/__hooky/checkpoint?id={id}&saved=1');"
    );
    if window.eval(script).is_err() {
        let mut session = shared.lock().unwrap();
        if session
            .checkpoint
            .as_ref()
            .is_some_and(|(current, _)| *current == id)
        {
            session.checkpoint = None;
        }
    }
    Some((id, receiver))
}

fn wait_checkpoint(shared: &Shared, checkpoint: Option<(u64, mpsc::Receiver<bool>)>) -> bool {
    let Some((id, receiver)) = checkpoint else {
        return true;
    };
    // Ne pas bloquer la fenêtre ni quitter silencieusement si l'écriture est
    // lente ou impossible. Les requêtes ne sont pas limitées à 64 Ko par keepalive.
    let saved = receiver
        .recv_timeout(Duration::from_secs(10))
        .unwrap_or(false);
    let mut session = shared.lock().unwrap();
    if session
        .checkpoint
        .as_ref()
        .is_some_and(|(current, _)| *current == id)
    {
        session.checkpoint = None;
    }
    saved
}

fn resume_page(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval("if (window.__hookyResume) window.__hookyResume();");
    }
}

/// La fermeture normale attend l'accusé de réception de l'écriture atomique.
/// Une fermeture forcée ou une panne électrique restent nécessairement best-effort.
fn request_quit(app: AppHandle, shared: Shared) {
    let (active, pending) = {
        let mut session = shared.lock().unwrap();
        if session.closed || session.quitting {
            return;
        }
        session.quitting = true;
        session.generation = session.generation.wrapping_add(1);
        session.opening = false;
        (session.active.is_some(), session.pending.take())
    };
    if let Some((_, child)) = pending {
        stop_child(child);
    }
    if !active {
        app.exit(0);
        return;
    }
    let checkpoint = begin_checkpoint(&app, &shared);
    std::thread::spawn(move || {
        let saved = wait_checkpoint(&shared, checkpoint);
        if shared.lock().unwrap().closed {
            return;
        }
        if saved {
            app.exit(0);
        } else {
            let callback_app = app.clone();
            app.dialog()
                .message("La session ou les réglages n'ont pas pu être enregistrés. Quitter quand même ?")
                .title("HookyCreator")
                .kind(MessageDialogKind::Warning)
                .buttons(MessageDialogButtons::YesNo)
                .show(move |quit| {
                    let mut session = shared.lock().unwrap();
                    if session.closed || !session.quitting {
                        return;
                    }
                    if !quit {
                        session.quitting = false;
                    }
                    drop(session);
                    if quit {
                        callback_app.exit(0);
                    } else {
                        resume_page(&callback_app);
                    }
                });
        }
    });
}

fn shutdown(shared: &Shared) {
    let (active, pending) = {
        let mut session = shared.lock().unwrap();
        session.closed = true;
        session.generation = session.generation.wrapping_add(1);
        session.checkpoint = None;
        (session.active.take(), session.pending.take())
    };
    if let Some(running) = active {
        stop_child(running.child);
    }
    if let Some((_, child)) = pending {
        stop_child(child);
    }
}

fn cancel_opening(app: &AppHandle, shared: &Shared) {
    let pending = {
        let mut session = shared.lock().unwrap();
        if !session.opening {
            return;
        }
        session.generation = session.generation.wrapping_add(1);
        session.opening = false;
        session.checkpoint = None;
        session.pending.take()
    };
    if let Some((_, child)) = pending {
        stop_child(child);
    }
    resume_page(app);
    status(
        app,
        "Ouverture annulée. Utilise Projet → Ouvrir un projet… pour réessayer.",
    );
}

fn local_origin(url: &Url, port: Option<u16>) -> bool {
    url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port() == port
        && port.is_some()
        && url.username().is_empty()
        && url.password().is_none()
}

fn allowed_navigation(url: &Url, shared: &Shared) -> bool {
    // La page de démarrage est empaquetée, pas servie par un site externe.
    let bundled = (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost"));
    if bundled {
        return url.username().is_empty() && url.password().is_none();
    }
    let port = shared.lock().unwrap().active.as_ref().map(|r| r.port);
    local_origin(url, port)
}

fn on_navigation(app: &AppHandle, shared: &Shared, url: &Url) -> bool {
    let port = shared
        .lock()
        .unwrap()
        .active
        .as_ref()
        .map(|running| running.port);
    if local_origin(url, port) && url.path() == "/__hooky/checkpoint" {
        let mut id = None;
        let mut saved = None;
        let mut valid = true;
        for (key, value) in url.query_pairs() {
            match key.as_ref() {
                "id" if id.is_none() => id = value.parse::<u64>().ok(),
                "saved" if saved.is_none() => {
                    saved = match value.as_ref() {
                        "1" => Some(true),
                        "0" => Some(false),
                        _ => None,
                    }
                }
                _ => valid = false,
            }
        }
        if valid {
            if let (Some(id), Some(saved)) = (id, saved) {
                let sender = {
                    let mut session = shared.lock().unwrap();
                    if session
                        .checkpoint
                        .as_ref()
                        .is_some_and(|(current, _)| *current == id)
                    {
                        session.checkpoint.take().map(|(_, sender)| sender)
                    } else {
                        None
                    }
                };
                if let Some(sender) = sender {
                    let _ = sender.send(saved);
                }
            }
        }
        return false;
    }
    if local_origin(url, port) && url.path() == "/__hooky/quit" {
        let next_app = app.clone();
        let next_shared = shared.clone();
        let _ = app.run_on_main_thread(move || request_quit(next_app, next_shared));
        return false;
    }
    allowed_navigation(url, shared)
}

fn download_directory(app: &AppHandle) -> tauri::Result<PathBuf> {
    // Les environnements Linux minimaux ne configurent pas toujours
    // XDG_DOWNLOAD_DIR ; garder une destination prévisible dans le home.
    app.path()
        .download_dir()
        .or_else(|_| app.path().home_dir().map(|home| home.join("Downloads")))
        .map(|directory| directory.join("HookyCreator"))
}

fn notify_download(webview: &Webview<Wry>, state: &str, path: &str, message: &str) {
    #[cfg(debug_assertions)]
    eprintln!("Hooky téléchargement : {state} / {path} / {message}");
    let detail = serde_json::json!({"status": state, "path": path, "message": message});
    let _ = webview.eval(format!(
        "window.dispatchEvent(new CustomEvent('hooky:download', {{detail: {detail}}}));"
    ));
}

fn on_download(
    app: &AppHandle,
    shared: &Shared,
    webview: Webview<Wry>,
    event: DownloadEvent<'_>,
) -> bool {
    match event {
        DownloadEvent::Requested { url, destination } => {
            #[cfg(debug_assertions)]
            eprintln!("Hooky demande de téléchargement : {}", url.path());
            let port = shared.lock().unwrap().active.as_ref().map(|r| r.port);
            if !local_origin(&url, port) {
                notify_download(&webview, "error", "", "Origine de téléchargement refusée.");
                return false;
            }
            let (stem, extension) = match url.path() {
                "/api/export/download" => {
                    let selection = url
                        .query_pairs()
                        .any(|(k, v)| k == "kind" && v == "selection");
                    (
                        if selection {
                            "hookycreator-selection".into()
                        } else {
                            "hookycreator-modele".into()
                        },
                        "zip",
                    )
                }
                "/api/sound/ogg" => {
                    let path = url
                        .query_pairs()
                        .find(|(k, _)| k == "path")
                        .map(|(_, v)| v.into_owned())
                        .unwrap_or_default();
                    (downloads::safe_stem(&path), "opus")
                }
                _ => {
                    notify_download(
                        &webview,
                        "error",
                        "",
                        "Ce téléchargement n'est pas pris en charge.",
                    );
                    return false;
                }
            };
            let dir = match download_directory(app) {
                Ok(path) => path,
                Err(_) => {
                    notify_download(
                        &webview,
                        "error",
                        "",
                        "Dossier Téléchargements introuvable.",
                    );
                    return false;
                }
            };
            match downloads::destination(&dir, &stem, extension) {
                Ok(path) => {
                    *destination = path.clone();
                    notify_download(&webview, "started", &path.to_string_lossy(), "");
                    true
                }
                Err(error) => {
                    notify_download(
                        &webview,
                        "error",
                        "",
                        &format!("Impossible d'écrire dans Téléchargements : {error}"),
                    );
                    false
                }
            }
        }
        DownloadEvent::Finished { path, success, .. } => {
            #[cfg(debug_assertions)]
            eprintln!("Hooky téléchargement terminé : {success}, {path:?}");
            if success {
                // WKWebView ne fournit pas le chemin au callback Finished ;
                // WebKitGTK peut fournir une URI file: plutôt qu'un chemin.
                let shown = path
                    .and_then(|path| {
                        let text = path.to_string_lossy();
                        if text.starts_with("file:") {
                            Url::parse(&text).ok()?.to_file_path().ok()
                        } else {
                            Some(path)
                        }
                    })
                    .or_else(|| download_directory(app).ok());
                notify_download(
                    &webview,
                    "ok",
                    &shown
                        .map(|p| p.to_string_lossy().into_owned())
                        .unwrap_or_default(),
                    "",
                );
            } else {
                notify_download(
                    &webview,
                    "error",
                    "",
                    "Le transfert a échoué. Réessaie avec le lien d'export.",
                );
            }
            true
        }
        _ => true,
    }
}

fn backend_ready(
    app: &AppHandle,
    shared: &Shared,
    generation: u64,
    root: PathBuf,
    result: Result<Ready, String>,
) {
    let recent_root = root.clone();
    let (pending, old) = {
        let mut session = shared.lock().unwrap();
        if session.closed
            || session.quitting
            || session.generation != generation
            || !session.opening
        {
            return;
        }
        session.opening = false;
        let pending = session.pending.take().map(|(_, child)| child);
        match (result, pending) {
            (Ok(ready), Some(child)) => {
                let old = session.active.replace(Running {
                    child,
                    generation,
                    root,
                    port: ready.port,
                });
                (Some(ready), old)
            }
            (Err(error), child) => {
                drop(session);
                if let Some(child) = child {
                    stop_child(child);
                }
                resume_page(app);
                report_error(app, &format!(
                    "Impossible d'ouvrir le projet : {error}\nUtilise Projet → Ouvrir un projet… pour en choisir un autre."
                ));
                return;
            }
            (Ok(_), None) => return,
        }
    };
    let ready = pending.expect("la session existe si le démarrage a réussi");
    let address = format!(
        "http://127.0.0.1:{}/desktop-login#token={}",
        ready.port, ready.token
    );
    // Le fragment n'est jamais envoyé au serveur ; la page d'ouverture l'efface
    // avant de demander le cookie HttpOnly. Aucun jeton sur la ligne de commande.
    let navigation = app
        .get_webview_window("main")
        .ok_or_else(|| "Fenêtre fermée.".to_string())
        .and_then(|window| {
            address
                .parse::<Url>()
                .map_err(|e| e.to_string())
                .and_then(|url| window.navigate(url).map_err(|e| e.to_string()))
        });
    if let Err(error) = navigation {
        let current = {
            let mut session = shared.lock().unwrap();
            let current = session.active.take();
            session.active = old;
            current
        };
        if let Some(current) = current {
            stop_child(current.child);
        }
        resume_page(app);
        report_error(app, &format!("Impossible d'afficher le projet : {error}"));
    } else {
        let remembered = recent_file(app)
            .and_then(|path| recent::save(&path, &recent_root).map_err(|error| error.to_string()));
        if let Err(error) = remembered {
            report_error(
                app,
                &format!(
                "Projet ouvert, mais sa reprise automatique n'a pas pu être enregistrée : {error}"
            ),
            );
        }
        if let Some(old) = old {
            // L'ancien projet a accusé réception de sa sauvegarde avant même
            // le démarrage du nouveau ; il peut maintenant être arrêté.
            stop_child(old.child);
        }
    }
}

fn backend_exited(app: &AppHandle, shared: &Shared, generation: u64) {
    let stopped = {
        let mut session = shared.lock().unwrap();
        if session.closed {
            return;
        }
        if session
            .active
            .as_ref()
            .is_some_and(|r| r.generation == generation)
        {
            session.active.take().map(|r| r.child)
        } else {
            None
        }
    };
    if let Some(child) = stopped {
        stop_child(child);
        report_error(
            app,
            "Le moteur local s'est arrêté. Utilise Projet → Ouvrir un projet… pour reprendre.",
        );
    }
}

fn start_project(app: AppHandle, shared: Shared, root: PathBuf) {
    let root = if root.is_absolute() {
        root
    } else {
        std::env::current_dir()
            .map(|directory| directory.join(&root))
            .unwrap_or(root)
    };
    let generation = {
        let mut session = shared.lock().unwrap();
        if session.closed || session.quitting || session.opening || session.picker_open {
            return;
        }
        if session.active.as_ref().is_some_and(|r| r.root == root) {
            drop(session);
            status(&app, "Ce projet est déjà ouvert.");
            return;
        }
        session.generation = session.generation.wrapping_add(1);
        session.opening = true;
        session.generation
    };
    status(
        &app,
        "Ouverture du projet et scan des ressources… cela peut prendre du temps.",
    );
    let checkpoint = begin_checkpoint(&app, &shared);
    std::thread::spawn(move || {
        if !wait_checkpoint(&shared, checkpoint) {
            let cloned_app = app.clone();
            let cloned_shared = shared.clone();
            let _ = app.run_on_main_thread(move || {
                let mut session = cloned_shared.lock().unwrap();
                if session.closed || session.quitting || session.generation != generation || !session.opening {
                    return;
                }
                session.opening = false;
                drop(session);
                resume_page(&cloned_app);
                report_error(&cloned_app, "La session du projet actuel n'a pas pu être enregistrée. \
                    Le changement de projet est annulé ; réessaie après avoir vérifié le dossier de travail.");
            });
            return;
        }
        {
            let session = shared.lock().unwrap();
            if session.closed
                || session.quitting
                || session.generation != generation
                || !session.opening
            {
                return;
            }
        }
        let exe = std::env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(Path::to_path_buf))
            .map(|dir| dir.join(BACKEND_NAME));
        // Uniquement pour les tests WebDriver : ne permet aucun IPC depuis le contenu.
        #[cfg(debug_assertions)]
        let exe = std::env::var_os("HOOKY_DESKTOP_TEST_BACKEND")
            .map(PathBuf::from)
            .or(exe);
        let result = exe
            .ok_or_else(|| "Chemin du moteur introuvable.".to_string())
            .and_then(|exe| {
                let mut command = Command::new(exe);
                command
                    .arg(&root)
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::null())
                    .env("PYTHONUTF8", "1");
                #[cfg(windows)]
                {
                    use std::os::windows::process::CommandExt;
                    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
                }
                command
                    .spawn()
                    .map_err(|e| format!("Démarrage du moteur impossible : {e}"))
            });
        let mut child = match result {
            Ok(child) => child,
            Err(error) => {
                let cloned_app = app.clone();
                let cloned_shared = shared.clone();
                let _ = app.run_on_main_thread(move || {
                    backend_ready(&cloned_app, &cloned_shared, generation, root, Err(error))
                });
                return;
            }
        };
        let stdout = child.stdout.take().expect("stdout est configuré en tube");
        {
            let mut session = shared.lock().unwrap();
            if session.closed
                || session.quitting
                || session.generation != generation
                || !session.opening
            {
                drop(session);
                stop_child(child);
                return;
            }
            session.pending = Some((generation, child));
        }
        let mut reader = BufReader::new(stdout);
        let ready = read_ready(&mut reader);
        let cloned_app = app.clone();
        let cloned_shared = shared.clone();
        let _ = app.run_on_main_thread(move || {
            backend_ready(&cloned_app, &cloned_shared, generation, root, ready)
        });
        // Éviter qu'une sortie ultérieure éventuelle remplisse le tube. EOF
        // signale aussi un arrêt inattendu du moteur déjà ouvert.
        let _ = std::io::copy(&mut reader, &mut std::io::sink());
        let cloned_app = app.clone();
        let _ = app.run_on_main_thread(move || backend_exited(&cloned_app, &shared, generation));
    });
}

fn choose_project(app: AppHandle, shared: Shared) {
    {
        let mut session = shared.lock().unwrap();
        if session.closed || session.quitting || session.picker_open {
            return;
        }
        if session.opening {
            drop(session);
            status(
                &app,
                "Une ouverture est en cours. Projet → Annuler l'ouverture permet de l'arrêter.",
            );
            return;
        }
        session.picker_open = true;
    }
    let callback_app = app.clone();
    app.dialog()
        .file()
        .set_title("Choisir le dossier du projet")
        .pick_folder(move |selection| {
            shared.lock().unwrap().picker_open = false;
            if let Some(selected) = selection {
                match selected.into_path() {
                    Ok(root) => start_project(callback_app, shared, root),
                    Err(error) => {
                        report_error(&callback_app, &format!("Dossier inaccessible : {error}"))
                    }
                }
            }
        });
}

fn reopen_or_choose(app: AppHandle, shared: Shared) {
    status(&app, "Recherche du dernier projet…");
    let path = recent_file(&app).ok();
    // Un disque externe ou réseau peut ne plus répondre ; ne jamais bloquer
    // le thread de la fenêtre pendant le contrôle du dossier sauvegardé.
    std::thread::spawn(move || {
        let root = path.as_deref().and_then(recent::load);
        let next_app = app.clone();
        let _ = app.run_on_main_thread(move || match root {
            Some(root) => start_project(next_app, shared, root),
            None => {
                status(&next_app, "Choisis le dossier du projet à ouvrir.");
                choose_project(next_app, shared);
            }
        });
    });
}

fn main() {
    let shared: Shared = Arc::new(Mutex::new(Session::default()));
    let menu_state = shared.clone();
    let setup_state = shared.clone();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .menu(|handle| {
            let open = MenuItem::with_id(handle, "open-project", "Ouvrir un projet…", true, Some("CmdOrCtrl+O"))?;
            let cancel = MenuItem::with_id(handle, "cancel-open", "Annuler l'ouverture", true, None::<&str>)?;
            let quit = MenuItem::with_id(handle, "quit", "Quitter", true, Some("CmdOrCtrl+Q"))?;
            Menu::with_items(handle, &[&Submenu::with_items(handle, "Projet", true, &[&open, &cancel, &quit])?])
        })
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open-project" => choose_project(app.clone(), menu_state.clone()),
            "cancel-open" => cancel_opening(app, &menu_state),
            "quit" => request_quit(app.clone(), menu_state.clone()),
            _ => {}
        })
        .setup(move |app| {
            let nav_state = setup_state.clone();
            let nav_app = app.handle().clone();
            let download_state = setup_state.clone();
            let close_state = setup_state.clone();
            let close_app = app.handle().clone();
            let download_app = app.handle().clone();
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("HookyCreator")
                .inner_size(1440.0, 900.0)
                .min_inner_size(800.0, 600.0)
                .initialization_script(
                    "if (location.protocol === 'http:' && location.hostname === '127.0.0.1') window.__HOOKY_DESKTOP__ = true;"
                )
                .on_navigation(move |url| on_navigation(&nav_app, &nav_state, url))
                .on_new_window(|_, _| NewWindowResponse::Deny)
                .on_download(move |webview, event| on_download(&download_app, &download_state, webview, event))
                .build()?;
            window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    request_quit(close_app.clone(), close_state.clone());
                }
            });
            // Permet le parcours automatisé de la vraie webview en CI, sans
            // rendre le choix de dossier accessible au JavaScript distant.
            #[cfg(debug_assertions)]
            if let Some(root) = std::env::var_os("HOOKY_DESKTOP_TEST_PROJECT") {
                start_project(app.handle().clone(), setup_state.clone(), root.into());
                return Ok(());
            }
            reopen_or_choose(app.handle().clone(), setup_state.clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Impossible de démarrer la fenêtre HookyCreator");
    app.run(move |app, event| match event {
        // Le menu système peut demander la sortie sans fermer la fenêtre
        // auparavant (notamment sur macOS).
        RunEvent::ExitRequested { code, api, .. } => {
            let active = shared.lock().unwrap().active.is_some();
            if code.is_none() && active {
                api.prevent_exit();
                request_quit(app.clone(), shared.clone());
            } else {
                shutdown(&shared);
            }
        }
        RunEvent::Exit => shutdown(&shared),
        _ => {}
    });
}
