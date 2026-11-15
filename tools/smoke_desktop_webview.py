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
sys.path.insert(0, str(ROOT / "tests"))
from desktop_fixture import make_desktop_fixture  # noqa: E402


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

    def click(self, selector: str) -> None:
        element = self.call("POST", f"/session/{self.session}/element", {"using": "css selector", "value": selector})
        identity = element["element-6066-11e4-a52e-4f735466cecf"]
        self.call("POST", f"/session/{self.session}/element/{identity}/click", {})

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
        if driver.script("const el = document.getElementById(arguments[0]); return !!el && !el.hidden", panel):
            driver.click("#" + button)


def run(application: Path) -> None:
    if not application.is_file():
        raise SystemExit(f"Application debug introuvable : {application}")
    with tempfile.TemporaryDirectory(prefix="hooky-webview-test-") as tmp:
        root = make_desktop_fixture(Path(tmp) / "dump")
        port, native_port = free_port(), free_port()
        while port == native_port:
            native_port = free_port()
        env = {**os.environ, "HOOKY_DESKTOP_TEST_PROJECT": str(root), "PYTHONUTF8": "1"}
        env.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
        downloads: list[Path] = []
        driver = None
        backend_port = None
        with tempfile.TemporaryFile() as log:
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
                # Le proxy tauri-driver écoute avant que le WebDriver natif ait
                # fini son démarrage ; attendre les deux évite un faux échec.
                wait_for("démarrage du WebDriver natif", lambda: listening(native_port), timeout=30)
                driver = Driver(port, application)
                driver.wait("chargement de l'atelier", """
                    const el = document.querySelector('#home-sub');
                    return !!(window.__HOOKY_DESKTOP__ && el && !el.textContent.includes('Chargement'));
                """, timeout=120)
                origin = driver.script("return location.origin")
                backend_port = urlparse(origin).port
                assert urlparse(origin).hostname == "127.0.0.1" and backend_port
                assert not driver.script("return location.hash")
                assert "hooky_session" not in driver.script("return document.cookie")

                driver.click('button[data-view="models"]')
                driver.wait("validation des modèles", """
                    const count = document.querySelector('#model-count').textContent;
                    return count.includes('1 modèle(s) vérifié(s)') && count.includes('1 non pris en charge');
                """)
                assert driver.script("return document.querySelectorAll('#model-grid .tile:not([hidden])').length") == 1
                driver.click('#model-grid .tile:not([hidden])')
                driver.wait("fiche du modèle", "return !!document.querySelector('#btn-blender')")
                driver.click('#btn-blender')
                first = completed_download(driver)
                downloads.append(first)
                folder = Path(driver.script("return document.querySelector('#export-result-folder').textContent"))
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
                assert driver.script("return document.querySelectorAll('#texture-grid .tile:not([hidden])').length") == 2
                driver.click('button[data-view="sounds"]')
                driver.wait("validation des sons", """
                    const count = document.querySelector('#sound-count').textContent;
                    return count.includes('1 son(s) vérifié(s)') && count.includes('1 non pris en charge');
                """)
                assert driver.script("return document.querySelectorAll('#sound-grid .tile:not([hidden])').length") == 1
                driver.click('#sound-grid .tile:not([hidden])')
                driver.wait("lecteur de son", "return !!document.querySelector('#sound-player')")
                assert driver.script("return !!document.querySelector('#sound-player').canPlayType('audio/ogg; codecs=opus')")
                driver.script("const p = document.querySelector('#sound-player'); p.preload = 'auto'; p.load()")
                driver.wait("décodage des métadonnées audio", """
                    const p = document.querySelector('#sound-player');
                    return p && (p.readyState >= 1 || p.error);
                """)
                assert not driver.script("return !!document.querySelector('#sound-player').error")
                driver.click('#inspector-body a[download]')
                audio = completed_download(driver, str(selected))
                downloads.append(audio)
                assert audio.name.startswith('Silence--') and audio.suffix == '.opus'
                assert audio.read_bytes().startswith(b'OggS')
                print("Webview native : session, filtres, ZIP, sélection et son OK")
                driver.close()
                driver = None
                wait_for("arrêt du backend après fermeture", lambda: not listening(backend_port), timeout=20)
                print("Arrêt du backend avec la fenêtre : OK")
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
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                for path in downloads:
                    with contextlib.suppress(OSError):
                        path.unlink()


def main() -> None:
    suffix = ".exe" if sys.platform == "win32" else ""
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "src-tauri" / "target" / "debug" / f"hookycreator-desktop{suffix}"
    run(app)


if __name__ == "__main__":
    main()
