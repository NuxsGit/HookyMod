"""Export temporaire du lock Tauri du premier build CI sous forme d'annotations."""
import base64
import sys
import zlib
from pathlib import Path

text = base64.b64encode(zlib.compress(Path("src-tauri/Cargo.lock").read_bytes(), 9)).decode()
size = 3000
part = int(sys.argv[1])
first = part * 10
for index in range(first, min(first + 10, (len(text) + size - 1) // size)):
    print(f"::notice title=Cargo-lock-{index:02d}::{text[index * size:(index + 1) * size]}")
