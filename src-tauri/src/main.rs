#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod downloads;

use serde::Deserialize;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{
    menu::{Menu, MenuItem, Submenu},
    webview::{DownloadEvent, NewWindowResponse, Webview},
    AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder, Wry,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

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

fn shutdown(shared: &Shared) {
    let (active, pending) = {
        let mut session = shared.lock().unwrap();
        session.closed = true;
        session.generation = session.generation.wrapping_add(1);
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
        session.pending.take()
    };
    if let Some((_, child)) = pending {
        stop_child(child);
    }
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

fn notify_download(webview: &Webview<Wry>, state: &str, path: &str, message: &str) {
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
                            "hookycreator-blender".into()
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
            let dir = match app.path().download_dir() {
                Ok(path) => path.join("HookyCreator"),
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
            if success {
                // WKWebView ne fournit pas le chemin au callback Finished.
                let shown = path.or_else(|| {
                    app.path()
                        .download_dir()
                        .ok()
                        .map(|p| p.join("HookyCreator"))
                });
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
    let (pending, old) = {
        let mut session = shared.lock().unwrap();
        if session.closed || session.generation != generation || !session.opening {
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
                report_error(app, &format!("Impossible d'ouvrir le projet : {error}"));
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
        report_error(app, &format!("Impossible d'afficher le projet : {error}"));
    } else if let Some(old) = old {
        stop_child(old.child);
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
    let generation = {
        let mut session = shared.lock().unwrap();
        if session.closed || session.opening {
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
    std::thread::spawn(move || {
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
            if session.closed || session.generation != generation || !session.opening {
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
        if session.closed || session.picker_open {
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
            "quit" => app.exit(0),
            _ => {}
        })
        .setup(move |app| {
            let nav_state = setup_state.clone();
            let download_state = setup_state.clone();
            let download_app = app.handle().clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("HookyCreator")
                .inner_size(1440.0, 900.0)
                .min_inner_size(800.0, 600.0)
                .initialization_script(
                    "if (location.protocol === 'http:' && location.hostname === '127.0.0.1') window.__HOOKY_DESKTOP__ = true;"
                )
                .on_navigation(move |url| allowed_navigation(url, &nav_state))
                .on_new_window(|_, _| NewWindowResponse::Deny)
                .on_download(move |webview, event| on_download(&download_app, &download_state, webview, event))
                .build()?;
            // Permet le parcours automatisé de la vraie webview en CI, sans
            // rendre le choix de dossier accessible au JavaScript distant.
            #[cfg(debug_assertions)]
            if let Some(root) = std::env::var_os("HOOKY_DESKTOP_TEST_PROJECT") {
                start_project(app.handle().clone(), setup_state.clone(), root.into());
                return Ok(());
            }
            choose_project(app.handle().clone(), setup_state.clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Impossible de démarrer la fenêtre HookyCreator");
    app.run(move |_app, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            shutdown(&shared);
        }
    });
}
