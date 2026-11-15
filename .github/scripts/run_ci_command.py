"""Lance une commande CI et expose sa fin comme annotation en cas d'échec."""
from __future__ import annotations

import collections
import os
import shutil
import subprocess
import sys


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        raise SystemExit("Commande manquante")
    executable = shutil.which(args[0]) or args[0]
    command = [executable, *args[1:]]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", subprocess.list2cmdline(command)]
    print("Exécution :", " ".join(args), flush=True)
    lines = collections.deque(maxlen=60)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace", bufsize=1) as process:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        result = process.wait()
    if result:
        # GitHub tronque les messages d'annotation à environ 4 096 caractères.
        # Découper la fin préserve à la fois le contexte et le traceback final.
        tail = "".join(lines)[-12000:]
        chunks = [tail[i:i + 2500] for i in range(0, len(tail), 2500)] or [""]
        for index, chunk in enumerate(chunks, 1):
            escaped = chunk.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=Échec de la commande {index}/{len(chunks)}::{escaped}", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
