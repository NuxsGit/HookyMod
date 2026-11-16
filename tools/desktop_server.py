"""Entrée PyInstaller du backend Tauri (pas de lancement navigateur)."""
import os

from hookycreator.desktop import main

if __name__ == "__main__":
    # The smoke test captures stderr if a request hangs. A watchdog dump makes
    # frozen Windows sidecar stalls diagnosable without exposing any data in a
    # normal application run.
    if os.environ.get("HOOKY_SMOKE_TRACEBACKS") == "1":
        import faulthandler
        faulthandler.dump_traceback_later(15, repeat=True)
    raise SystemExit(main())
