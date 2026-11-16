"""Parcours d'une vraie webview Tauri v2 via tauri-driver (Linux/Windows).

Construire d'abord l'application avec ``npm run tauri -- build --debug --no-bundle``.
Il faut tauri-driver et WebKitWebDriver (Linux) ou msedgedriver (Windows). La
racine auto-ouverte est une fixture synthétique ; aucun dialogue n'est simulé
par le contenu web, et le mode de test n'est pas compilé en release.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from desktop_fixture import make_desktop_fixture  # noqa: E402
from synth_animation import fska_two_clips_with_jump, named_rigid_model  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class WebDriverError(RuntimeError):
    pass


def wait_for(description, check, timeout=45):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            result = check()
            if result:
                return result
        except (urllib.error.URLError, OSError, ValueError, KeyError, WebDriverError) as exc:
            last = exc
        time.sleep(0.25)
    raise TimeoutError(f"{description} : délai dépassé ({last or 'condition non remplie'})")


class Driver:
    def __init__(self, port: int, application: Path):
        self.origin = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.started = time.perf_counter()
        result = self.call("POST", "/session", {
            "capabilities": {"alwaysMatch": {
                "browserName": "wry", "tauri:options": {
                    "application": str(application.resolve()), "webviewOptions": {},
                }}}})
        self.session = result["sessionId"]

    def call(self, method: str, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.origin + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=60) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            raise WebDriverError(f"WebDriver HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc
        value = body.get("value")
        if isinstance(value, dict) and value.get("error"):
            raise WebDriverError(f"WebDriver : {value['error']} — {value.get('message', '')}")
        return value

    def script(self, script: str, *args):
        return self.call("POST", f"/session/{self.session}/execute/sync", {"script": script, "args": args})

    def query(self, script: str, *args):
        """Lecture seule : réessayer une coupure de connexion, jamais une action."""
        for attempt in range(3):
            try:
                return self.script(script, *args)
            except ConnectionResetError as exc:
                if attempt == 2:
                    raise
                print(f"Lecture WebDriver interrompue, nouvelle tentative : {exc}",
                      file=sys.stderr, flush=True)
                time.sleep(0.5)
        raise AssertionError("Nombre de tentatives inattendu")

    def click(self, selector: str) -> None:
        element = self.call("POST", f"/session/{self.session}/element", {"using": "css selector", "value": selector})
        identity = element["element-6066-11e4-a52e-4f735466cecf"]
        self.call("POST", f"/session/{self.session}/element/{identity}/click", {})

    def click_scrolled(self, selector: str) -> None:
        # Les commandes Studio vivent dans #views, un conteneur défilant.
        # WebKitWebDriver ne le fait pas toujours défiler avant son clic natif.
        # Le clic reste un vrai clic WebDriver, pas un element.click() simulé.
        self.script("""
            const element = document.querySelector(arguments[0]);
            if (!element) throw new Error('Élément absent : ' + arguments[0]);
            element.scrollIntoView({block: 'center', inline: 'nearest'});
        """, selector)
        self.click(selector)

    def wait(self, description: str, script: str, timeout=45):
        return wait_for(description, lambda: self.script(script), timeout)

    def close(self) -> None:
        self.call("DELETE", f"/session/{self.session}")


def completed_download(driver: Driver, previous: str = "") -> Path:
    path = wait_for("téléchargement natif terminé", lambda: driver.script("""
        const title = document.querySelector('#native-download-title');
        const path = document.querySelector('#native-download-path');
        return title && title.textContent === 'Téléchargement terminé'
          && path && path.textContent !== arguments[0] && path.textContent;
    """, previous), timeout=90)
    destination = Path(path)
    assert destination.is_absolute() and destination.is_file(), path
    assert destination.parent.name == "HookyCreator", path
    return destination


def close_panels(driver: Driver) -> None:
    for panel, button in (("native-download-result", "native-download-dismiss"),
                          ("export-result", "export-dismiss")):
        if driver.query("const el = document.getElementById(arguments[0]); return !!el && !el.hidden", panel):
            driver.click("#" + button)


def launch_driver(application: Path, env: dict, log) -> tuple[subprocess.Popen, Driver]:
    port, native_port = free_port(), free_port()
    while port == native_port:
        native_port = free_port()
    command = ["tauri-driver", "--port", str(port), "--native-port", str(native_port)]
    if env.get("HOOKY_WEBDRIVER_BINARY"):
        command.extend(["--native-driver", env["HOOKY_WEBDRIVER_BINARY"]])
    process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
    try:
        def driver_ready():
            if process.poll() is not None:
                raise RuntimeError("tauri-driver s'est arrêté avant de démarrer.")
            return listening(port)
        wait_for("démarrage tauri-driver", driver_ready, timeout=30)
        wait_for("démarrage du WebDriver natif", lambda: listening(native_port), timeout=30)
        return process, Driver(port, application)
    except BaseException:
        stop_driver(process)
        raise


def stop_driver(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def run(application: Path) -> None:
    if not application.is_file():
        raise SystemExit(f"Application debug introuvable : {application}")
    with tempfile.TemporaryDirectory(prefix="hooky-webview-test-") as tmp:
        root = make_desktop_fixture(Path(tmp) / "dump")
        env = {**os.environ, "HOOKY_DESKTOP_TEST_PROJECT": str(root), "PYTHONUTF8": "1",
               "HOOKY_DESKTOP_TEST_CONFIG": str(Path(tmp) / "config")}
        env.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
        downloads: list[Path] = []
        driver = None
        process = None
        backend_port = None
        with tempfile.TemporaryFile() as log:
            try:
                process, driver = launch_driver(application, env, log)
                driver.wait("chargement de l'atelier", """
                    const el = document.querySelector('#home-sub');
                    return !!(window.__HOOKY_DESKTOP__ && el && !el.textContent.includes('Chargement'));
                """, timeout=120)
                print(f"Démarrage fenêtre + WebDriver (fixture synthétique) : {time.perf_counter() - driver.started:.3f} s")
                origin = driver.query("return location.origin")
                backend_port = urlparse(origin).port
                assert urlparse(origin).hostname == "127.0.0.1" and backend_port
                assert not driver.query("return location.hash")
                assert "hooky_session" not in driver.query("return document.cookie")

                driver.click('button[data-view="models"]')
                driver.wait("validation des modèles", """
                    const count = document.querySelector('#model-count').textContent;
                    return count.includes('1 modèle(s) vérifié(s)') && count.includes('1 non pris en charge');
                """)
                assert driver.query("return document.querySelectorAll('#model-grid .tile:not([hidden])').length") == 1
                driver.click('#model-grid .tile:not([hidden])')
                driver.wait("fiche du modèle", "return !!document.querySelector('#btn-export-model')")
                # Environnement sans WebGL2 : le repli PNG doit rester visible.
                # Sinon, le modèle complet et le pipeline FSR sont exercés dans
                # la vraie WebView ; un refus FSR doit être affiché explicitement.
                driver.wait("vue 3D ou repli", """
                    const status = document.querySelector('#model-view-status')?.textContent || '';
                    return status.includes('triangles') || status.includes('Vue 3D indisponible');
                """, timeout=60)
                if driver.query("return !!modelViewport && modelViewport.ready"):
                    assert driver.query("return modelViewport.model.triangles") == 1
                    driver.script("""
                        const select = document.querySelector('#model-upscale');
                        select.value = 'fsr'; select.dispatchEvent(new Event('change', {bubbles: true}));
                    """)
                    driver.wait("FSR ou repli explicite", """
                        const status = document.querySelector('#model-view-status')?.textContent || '';
                        return (!!modelViewport && modelViewport.mode === 'fsr' && !!modelViewport.targets)
                          || status.includes('FSR 1 indisponible');
                    """, timeout=60)
                    print('Vue native : ' + driver.query("return document.querySelector('#model-view-status').textContent"))
                else:
                    assert driver.query("return !document.querySelector('#model-shot').hidden")
                    print('Vue native : repli PNG (WebGL indisponible)')
                driver.click('#btn-export-model')
                first = completed_download(driver)
                downloads.append(first)
                folder = Path(driver.query("return document.querySelector('#export-result-folder').textContent"))
                assert folder.is_absolute() and (folder / 'Heros.gltf').is_file(), folder
                with zipfile.ZipFile(first) as archive:
                    names = archive.namelist()
                    assert "Heros/Heros.gltf" in names and "Heros/Heros.bin" in names
                    assert len([name for name in names if name.endswith('.png')]) == 2

                # Le bouton de secours télécharge bien une seconde archive distincte.
                driver.click('#export-download')
                retry = completed_download(driver, str(first))
                downloads.append(retry)
                assert retry != first
                # Le résultat persistant ne doit pas masquer la fermeture de la fiche.
                driver.click('#inspector-close')
                close_panels(driver)
                # Le bouton de sélection est transparent jusqu'au survol. La
                # barre animée doit être centrée et réellement cliquable même
                # si les notifications des téléchargements sont encore visibles.
                driver.script("document.querySelector('#model-grid .tile:not([hidden]) .pick').click()")
                driver.wait("barre de sélection cliquable", """
                    const bar = document.querySelector('#selection-bar');
                    const button = document.querySelector('#selection-export');
                    if (!bar || bar.hidden || !button) return false;
                    const box = button.getBoundingClientRect();
                    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
                    const animations = bar.getAnimations ? bar.getAnimations() : [];
                    return box.width > 0 && box.height > 0 && (hit === button || button.contains(hit))
                      && animations.every(a => a.playState !== 'running');
                """)
                driver.click('#selection-export')
                selected = completed_download(driver, str(retry))
                downloads.append(selected)
                assert selected.name.startswith('hookycreator-selection--')
                with zipfile.ZipFile(selected) as archive:
                    assert "modeles/Heros/Heros.gltf" in archive.namelist()
                close_panels(driver)
                driver.click('#selection-clear')

                driver.click('button[data-view="textures"]')
                driver.wait("validation des textures", """
                    const count = document.querySelector('#texture-count').textContent;
                    return count.includes('2 texture(s) vérifié(s)') && count.includes('1 non pris en charge');
                """)
                assert driver.query("return document.querySelectorAll('#texture-grid .tile:not([hidden])').length") == 2
                driver.click('button[data-view="sounds"]')
                driver.wait("validation des sons", """
                    const count = document.querySelector('#sound-count').textContent;
                    return count.includes('1 son(s) vérifié(s)') && count.includes('1 non pris en charge');
                """)
                assert driver.query("return document.querySelectorAll('#sound-grid .tile:not([hidden])').length") == 1
                driver.click('#sound-grid .tile:not([hidden])')
                driver.wait("lecteur de son", "return !!document.querySelector('#sound-player')")
                assert driver.query("return !!document.querySelector('#sound-player').canPlayType('audio/ogg; codecs=opus')")
                driver.script("const p = document.querySelector('#sound-player'); p.preload = 'auto'; p.load()")
                driver.wait("décodage des métadonnées audio", """
                    const p = document.querySelector('#sound-player');
                    return p && (p.readyState >= 1 || p.error);
                """)
                assert not driver.query("return !!document.querySelector('#sound-player').error")
                driver.click('#inspector-body a[href*="/api/sound/convert"][href*="format=opus"]')
                audio = completed_download(driver, str(selected))
                downloads.append(audio)
                assert audio.name.startswith('Silence--') and audio.suffix == '.opus'
                assert audio.read_bytes().startswith(b'OggS')
                close_panels(driver)
                driver.click('#inspector-body a[href*="/api/sound/convert"][href*="format=wav"]')
                wav = completed_download(driver, str(audio))
                downloads.append(wav)
                assert wav.name.startswith('Silence--') and wav.suffix == '.wav'
                assert wav.read_bytes().startswith(b'RIFF')

                close_panels(driver)
                driver.click('button[data-view="videos"]')
                driver.wait("liste des vidéos", "return document.querySelectorAll('#video-grid .tile').length === 1")
                driver.click('#video-grid .tile')
                driver.wait("lecteur vidéo", "return !!document.querySelector('#video-player')")
                driver.script("const p = document.querySelector('#video-player'); p.preload = 'auto'; p.load()")
                driver.wait("métadonnées vidéo ou refus du codec", """
                    const p = document.querySelector('#video-player');
                    return p && (p.readyState >= 1 || p.error);
                """)
                if driver.query("return !!document.querySelector('#video-player').error"):
                    driver.wait("message de codec non pris en charge", """
                        return document.querySelector('#video-status').textContent.includes('Lecture impossible');
                    """)
                    print("WebM : codec indisponible dans cette WebView ; message de repli affiché")
                else:
                    assert driver.query("return document.querySelector('#video-player').videoWidth") == 32
                    assert driver.query("return document.querySelector('#video-player').videoHeight") == 32
                driver.click('#inspector-body a[href*="/api/video/stream"][download]')
                movie = completed_download(driver, str(wav))
                downloads.append(movie)
                assert movie.name.startswith('Synthetic--') and movie.suffix == '.webm'
                assert movie.read_bytes() == (root / 'Model' / 'Synthetic.webm').read_bytes()

                close_panels(driver)
                driver.click('button[data-view="animations"]')
                driver.wait("liste des animations", "return document.querySelectorAll('#animation-grid .tile').length === 1")
                driver.click('#animation-grid .tile')
                driver.wait("fiche d'animation", "return !!document.querySelector('#animation-download')?.href")
                driver.click('#animation-download')
                clip = completed_download(driver, str(movie))
                downloads.append(clip)
                assert clip.name.startswith('Heros--') and clip.suffix == '.json'
                assert json.loads(clip.read_text(encoding='utf-8'))['name'] == 'Walk'

                # Ajouter un modèle skinné sans changer les comptes du scan testé
                # ci-dessus. Le fichier sélectionné explicitement fait partie de
                # l'export groupé même s'il n'est pas dans l'index par nom.
                (root / 'Model' / 'Rigid.bfres').write_bytes(named_rigid_model())
                (root / 'Model' / 'Rigid.anim.bfres').write_bytes(fska_two_clips_with_jump())
                close_panels(driver)
                driver.script("void openModel('Model/Rigid.bfres', {animationRel: 'Model/Rigid.anim.bfres', animationIndex: 0})")
                driver.wait("modèle animé ouvert", """
                    return !!document.querySelector('#btn-export-animations')
                      && document.querySelector('#model-animation-file')?.value === 'Model/Rigid.anim.bfres';
                """, timeout=60)
                driver.click_scrolled('#btn-export-animations')
                grouped = completed_download(driver, str(clip))
                downloads.append(grouped)
                assert grouped.name.startswith('hookycreator-animations--') and grouped.suffix == '.glb'
                data = grouped.read_bytes()
                magic, version, total = struct.unpack_from('<4sII', data)
                assert (magic, version, total) == (b'glTF', 2, len(data))
                size, kind = struct.unpack_from('<I4s', data, 12)
                assert kind == b'JSON'
                document = json.loads(data[20:20 + size])
                assert [item['name'] for item in document['animations']] == ['Walk', 'Run']
                assert len(document['skins']) == 1
                print("Webview native : session, ZIP, audio, vidéo, JSON et GLB multi-clips OK")

                # L'archive .hooky utilise le véritable gestionnaire de
                # téléchargement Tauri, pas seulement le backend HTTP.
                close_panels(driver)
                driver.click('button[data-view="studio"]')
                driver.script("""
                    const name = document.querySelector('#studio-new-name');
                    name.value = 'Création synthétique';
                    document.querySelector('#studio-create-form').requestSubmit();
                """)
                driver.wait("projet HookyMod's prêt", """
                    return !document.querySelector('#studio-workspace').hidden
                      && !document.querySelector('#studio-controls').disabled;
                """)
                driver.click_scrolled('[data-studio-kind="item"]')
                driver.script("""
                    const name = document.querySelector('#studio-element-name');
                    name.value = 'Objet synthétique';
                    name.dispatchEvent(new Event('input', { bubbles: true }));
                """)
                driver.click_scrolled('[data-studio-block="when_use"]')
                driver.click_scrolled('[data-studio-block="give_item"]')
                driver.script("""
                    const value = document.querySelector('[data-block-value="1"]');
                    value.value = 'Clé imaginaire';
                    value.dispatchEvent(new Event('input', { bubbles: true }));
                    const event = document.querySelector('#studio-simulation-event');
                    event.value = 'when_use';
                    event.dispatchEvent(new Event('change', { bubbles: true }));
                """)
                driver.click_scrolled('#studio-simulate')
                driver.wait("simulation hors jeu", """
                    const result = document.querySelector('#studio-simulation-result');
                    return result && !result.hidden && result.textContent.includes('Action simulée')
                      && result.textContent.includes('Clé imaginaire');
                """)
                driver.click_scrolled('#studio-export')
                design = completed_download(driver, str(clip))
                downloads.append(design)
                assert design.name.startswith('hookymods--') and design.suffix == '.hooky'
                with zipfile.ZipFile(design) as archive:
                    assert set(archive.namelist()) == {'manifest.json', 'workspace.json'}
                    assert json.loads(archive.read('manifest.json'))['playable'] is False
                    assert json.loads(archive.read('workspace.json'))['elements'][0]['name'] == 'Objet synthétique'
                close_panels(driver)
                # Laisser un brouillon non enregistré : le checkpoint natif
                # doit le sauver même après avoir changé de vue.
                driver.script("""
                    const description = document.querySelector('#studio-description');
                    description.value = 'Brouillon HookyMod conservé';
                    description.dispatchEvent(new Event('input', { bubbles: true }));
                """)

                # Une sauvegarde finale > 64 Kio et artificiellement retardée
                # doit être confirmée avant l'arrêt, pas confiée à keepalive.
                close_panels(driver)
                driver.click('button[data-view="models"]')
                driver.wait("modèle revalidé", "return !!document.querySelector('#model-grid .tile:not([hidden])')")
                driver.click('#model-grid .tile:not([hidden])')
                driver.wait("fiche rouverte", "return !!document.querySelector('#btn-export-model')")
                driver.wait("session prête", "return !!uiSessionWriter && uiSessionWriter.enabled")
                driver.script("""
                    const original = uiSessionWriter.fetcher;
                    uiSessionWriter.fetcher = async (...args) => {
                      await new Promise(resolve => setTimeout(resolve, 1600));
                      return original(...args);
                    };
                    state.selection.add('model:Model/Heros.bfres');
                    for (let i = 0; i < 800; i++) state.selection.add('model:missing-' + i + '-' + 'x'.repeat(120));
                    const name = document.querySelector('#mod-name');
                    name.value = 'Brouillon conservé';
                    name.dispatchEvent(new Event('input', { bubbles: true }));
                    const search = document.querySelector('#search');
                    search.value = 'Heros';
                    search.dispatchEvent(new Event('input', { bubbles: true }));
                    document.querySelector('#win-close').click();
                """)
                time.sleep(1.1)
                assert listening(backend_port), "le backend s'est arrêté avant la confirmation de sauvegarde"
                wait_for("arrêt du backend après sauvegarde", lambda: not listening(backend_port), timeout=25)
                with contextlib.suppress(Exception):
                    driver.close()
                driver = None
                stop_driver(process)
                process = None
                sessions = list((root / "hookycreator_work" / "sessions").glob("*.json"))
                assert len(sessions) == 1 and sessions[0].stat().st_size > 64 * 1024, sessions
                saved = json.loads(sessions[0].read_text(encoding="utf-8"))["state"]
                assert saved["fields"]["mod-name"] == "Brouillon conservé"
                assert saved["fields"]["search"] == "Heros"
                assert "model:Model/Heros.bfres" in saved["selection"]
                designs = list((root / "hookycreator_work" / "hookymods").glob("*.json"))
                assert len(designs) == 1 and json.loads(designs[0].read_text(encoding="utf-8"))["description"] == "Brouillon HookyMod conservé"
                recent = json.loads((Path(tmp) / "config" / "recent-project.json").read_text(encoding="utf-8"))
                assert Path(recent["project"]) == root

                # Redémarrage sans la variable de sélection de la fixture : la
                # vraie fenêtre doit rouvrir le projet et ignorer les fichiers
                # manquants de l'ancienne sélection.
                env = dict(env)
                env.pop("HOOKY_DESKTOP_TEST_PROJECT")
                process, driver = launch_driver(application, env, log)
                driver.wait("reprise du projet et de la fiche", """
                    const models = document.querySelector('#view-models');
                    return !!(models && !models.hidden && document.querySelector('#btn-export-model')
                      && document.querySelector('#search').value === 'Heros'
                      && document.querySelector('#mod-name').value === 'Brouillon conservé');
                """, timeout=120)
                assert driver.query("return [...state.selection]") == ["model:Model/Heros.bfres"]
                assert driver.query("return !document.querySelector('#inspector').hidden")
                new_origin = driver.query("return location.origin")
                new_port = urlparse(new_origin).port
                assert urlparse(new_origin).hostname == "127.0.0.1" and new_port

                # Cette fois, fermer la fenêtre elle-même (et non le bouton de
                # l'interface) immédiatement après la dernière modification.
                driver.script("""
                    const name = document.querySelector('#mod-name');
                    name.value = 'Après redémarrage';
                    name.dispatchEvent(new Event('input', { bubbles: true }));
                """)
                with contextlib.suppress(WebDriverError, urllib.error.URLError, OSError):
                    driver.call("DELETE", f"/session/{driver.session}/window")
                wait_for("arrêt après fermeture de fenêtre", lambda: not listening(new_port), timeout=25)
                with contextlib.suppress(Exception):
                    driver.close()
                driver = None
                saved = json.loads(sessions[0].read_text(encoding="utf-8"))["state"]
                assert saved["fields"]["mod-name"] == "Après redémarrage"
                print("Reprise native, fermeture confirmée et grande session : OK")
            except Exception as error:
                # Afficher d'abord l'erreur précise : les annotations de CI
                # peuvent tronquer les longs journaux de la webview.
                print(f"Échec du parcours : {error!r}", file=sys.stderr, flush=True)
                if driver is not None:
                    with contextlib.suppress(Exception):
                        snapshot = driver.script("""
                            const node = id => document.getElementById(id);
                            const text = id => node(id)?.textContent || '';
                            const button = node('selection-export');
                            const box = button?.getBoundingClientRect();
                            const hit = box && document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
                            return {
                              url: location.pathname,
                              selectionVisible: !!node('selection-bar') && !node('selection-bar').hidden,
                              selectionCount: text('selection-count'),
                              selectionHit: hit ? `${hit.tagName}#${hit.id}.${hit.className}` : '',
                              exportVisible: !!node('export-result') && !node('export-result').hidden,
                              exportTitle: text('export-result-title'), exportNote: text('export-result-note'),
                              exportFolder: text('export-result-folder'),
                              nativeVisible: !!node('native-download-result') && !node('native-download-result').hidden,
                              nativeTitle: text('native-download-title'), nativePath: text('native-download-path'),
                              studioDisabled: !!node('studio-controls')?.disabled,
                              studioItem: (() => {
                                const el = document.querySelector('[data-studio-kind="item"]');
                                if (!el) return null;
                                const r = el.getBoundingClientRect();
                                return {x: r.x, y: r.y, width: r.width, height: r.height,
                                        hit: document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.outerHTML.slice(0, 200)};
                              })(),
                              toasts: text('toasts'), log: text('console-log').slice(-3000),
                            };
                        """)
                        print("État de la fenêtre : " + json.dumps(snapshot, ensure_ascii=False), file=sys.stderr)
                log.seek(0)
                print("Journal tauri-driver :\n" + log.read().decode('utf-8', errors='replace'), file=sys.stderr)
                raise
            finally:
                if driver is not None:
                    with contextlib.suppress(Exception):
                        driver.close()
                if process is not None:
                    stop_driver(process)
                for path in downloads:
                    with contextlib.suppress(OSError):
                        path.unlink()


def main() -> None:
    suffix = ".exe" if sys.platform == "win32" else ""
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "src-tauri" / "target" / "debug" / f"hookycreator-desktop{suffix}"
    run(app)


if __name__ == "__main__":
    main()
