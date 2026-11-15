#!/usr/bin/env python3
"""Mesure le backend desktop et la livraison HTTP d'exports individuels.

Exemple : python tools/benchmark_workflow.py /chemin/romfs Model/MonModele.bfres.zs
Ajoute --blend pour mesurer le vrai appel à Blender quand il est installé. Le
travail est temporaire, les sources ne sont pas modifiées. La fenêtre Tauri et
son affichage ne sont pas mesurés ; le cache du système n'est pas vidé.
"""
from __future__ import annotations

import argparse
import gzip
import http.cookiejar
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def request_json(opener, url: str, *, payload=None, origin: str = "", gzip_ok: bool = False):
    headers = {"Accept-Encoding": "gzip"} if gzip_ok else {}
    if payload is not None:
        headers.update({"Content-Type": "application/json", "Origin": origin})
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers=headers)
    try:
        with opener.open(request, timeout=180) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} : {exc.read().decode('utf-8', 'replace')}") from exc


def verify_archive(file, result: dict) -> None:
    """Contrôle après chronométrage, sans extraire de chemin fourni par l'archive."""
    file.seek(0)
    with zipfile.ZipFile(file) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise RuntimeError("ZIP invalide ou entrées dupliquées")
        gltfs = [name for name in names if name.endswith(".gltf")]
        if len(gltfs) != 1:
            raise RuntimeError("glTF absent ou ambigu dans le ZIP")
        gltf = gltfs[0]
        prefix = gltf.rpartition("/")[0] + "/"
        document = json.loads(archive.read(gltf))
        for entry in document.get("buffers", []) + document.get("images", []):
            name = prefix + entry["uri"]
            if name not in names:
                raise RuntimeError(f"Ressource glTF absente du ZIP : {name}")
        actual = {name for name in names if name.startswith(prefix)}
        expected = {prefix + name for name in result["files"]}
        if actual != expected:
            raise RuntimeError("Le ZIP ne correspond pas aux fichiers annoncés")
        if result.get("method") == "blender" and not any(name.endswith(".blend") for name in names):
            raise RuntimeError("Le .blend annoncé est absent du ZIP")


def run(args) -> dict:
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("dossier de projet introuvable")
    models = []
    for model in args.models:
        candidate = Path(model).expanduser()
        full = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            rel = full.relative_to(root).as_posix()
        except ValueError:
            raise ValueError(f"modèle hors du projet : {model}") from None
        if not full.is_file():
            raise ValueError(f"modèle introuvable : {model}")
        models.append(rel)

    with tempfile.TemporaryDirectory(prefix="hooky-workflow-benchmark-") as work, tempfile.TemporaryFile() as log:
        command = ([str(Path(args.backend).expanduser().resolve())] if args.backend
                   else [sys.executable, "-m", "hookycreator.desktop"])
        command += [str(root), "--work", work]
        started = time.perf_counter()
        process = subprocess.Popen(command, cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=log, env={**os.environ, "PYTHONUTF8": "1"})
        try:
            replies = queue.Queue()
            threading.Thread(target=lambda: replies.put(process.stdout.readline(16385)), daemon=True).start()
            try:
                line = replies.get(timeout=120)
            except queue.Empty:
                raise RuntimeError("le backend n'est pas prêt après 120 secondes") from None
            if not line or not line.endswith(b"\n") or len(line) > 16384:
                raise RuntimeError("le backend n'a pas envoyé de réponse de démarrage valide")
            ready = json.loads(line)
            if "error" in ready:
                raise RuntimeError(ready["error"])
            backend_ready = time.perf_counter()
            origin = f"http://127.0.0.1:{int(ready['port'])}"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                 urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            request_json(opener, origin + "/api/desktop/session",
                         payload={"token": ready["token"]}, origin=origin)
            state = request_json(opener, origin + "/api/state?quick=1", gzip_ok=True)["project"]
            usable = time.perf_counter()
            engine = request_json(opener, origin + "/api/system")["engine"]
            report = {
                "platform": platform.platform(), "python": platform.python_version(),
                "engine": engine, "assets": len(state["assets"]),
                "startup": {"process_to_backend_seconds": round(backend_ready - started, 4),
                            "auth_and_state_seconds": round(usable - backend_ready, 4),
                            "process_to_state_seconds": round(usable - started, 4)},
                "exports": [],
                "limitations": ["Fenêtre Tauri, affichage et cache OS non mesurés / non vidés.",
                                "Premier export : cache applicatif neuf mais partagé entre les modèles.",
                                "La fidélité en jeu ne peut pas être déduite d'un ZIP valide."],
            }
            route = "/api/model/blend" if args.blend else "/api/model/export"
            for rel in models:
                for attempt in range(args.repeat):
                    before = time.perf_counter()
                    result = request_json(opener, origin + route,
                                          payload={"path": rel, "textures": not args.no_textures},
                                          origin=origin)
                    prepared = time.perf_counter()
                    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as archive:
                        with opener.open(origin + result["download_url"], timeout=180) as response:
                            shutil.copyfileobj(response, archive, length=1024 * 1024)
                        downloaded = time.perf_counter()
                        size = archive.tell()
                        verify_archive(archive, result)
                    report["exports"].append({
                        "model": rel, "attempt": attempt + 1,
                        "post_seconds": round(prepared - before, 4),
                        "download_seconds": round(downloaded - prepared, 4),
                        "total_seconds": round(downloaded - before, 4),
                        "zip_bytes": size, "faces": result["faces"], "maps": result["textures"],
                        "method": result.get("method", "gltf/obj"),
                        "message": result.get("message", ""), "warnings": result["warnings"],
                    })
            return report
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="dossier du jeu extrait")
    parser.add_argument("models", nargs="*", help="chemins des modèles relatifs au projet")
    parser.add_argument("--repeat", type=int, default=2, help="exports par modèle (défaut : 2)")
    parser.add_argument("--blend", action="store_true", help="appeler Blender pour un vrai .blend")
    parser.add_argument("--no-textures", action="store_true", help="désactiver l'export des cartes")
    parser.add_argument("--backend", help="sidecar autonome à mesurer, au lieu de Python")
    parser.add_argument("--json", action="store_true", help="rapport structuré sur stdout")
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 10:
        parser.error("--repeat doit être compris entre 1 et 10")
    try:
        report = run(args)
    except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile) as exc:
        print(f"Mesure impossible : {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Backend : {report['startup']['process_to_backend_seconds']:.3f} s ; "
              f"authentification + état : {report['startup']['auth_and_state_seconds']:.3f} s ; "
              f"total : {report['startup']['process_to_state_seconds']:.3f} s")
        print(f"Inventaire : {report['assets']} fichiers ; Rust : "
              f"{'actif' if report['engine']['rust']['available'] else 'inactif'}")
        for entry in report["exports"]:
            print(f"{entry['model']} #{entry['attempt']} : préparation {entry['post_seconds']:.3f} s + "
                  f"téléchargement {entry['download_seconds']:.3f} s = {entry['total_seconds']:.3f} s ; "
                  f"{entry['faces']} faces, {entry['maps']} cartes, {entry['zip_bytes']} octets, "
                  f"méthode {entry['method']}")
            if entry["message"]:
                print(f"  résultat : {entry['message']}")
            for warning in entry["warnings"]:
                print(f"  remarque : {warning}")
        for limitation in report["limitations"]:
            print(f"Limite : {limitation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
