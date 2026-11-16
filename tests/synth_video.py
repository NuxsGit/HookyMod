"""A tiny synthetic VP8 WebM used for desktop/browser smoke tests.

Generated from two 32×32 solid-colour frames; it contains no game data. The
fixture is embedded as base64 so the test suite needs no video encoder.
"""
from __future__ import annotations

import base64


def make_webm() -> bytes:
    return base64.b64decode(
        "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAICEU2bdLpNu4tT"
        "q4QVSalmU6yBoU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggEZTbuMU6uEHFO7a1OsggHs7AEA"
        "AAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZm"
        "NjEuNy4xMDBXQYxMYXZmNjEuNy4xMDBEiYhAj0AAAAAAABZUrmu+rgEAAAAAAAA114EBc8WIDFEd"
        "QrRGHD6cgQAitZyDdW5kiIEAhoVWX1ZQOIOBASPjg4QdzWUA4IawgSC6gSASVMNn13Nzn2PAgGfI"
        "mUWjh0VOQ09ERVJEh4xMYXZmNjEuNy4xMDBzc7JjwItjxYgMUR1CtEYcPmfIoUWjiERVUkFUSU9O"
        "RIeTMDA6MDA6MDEuMDAwMDAwMDAwAB9DtnXy54EAo7yBAACA0AIAnQEqIAAgAABHCIWFiIWEiAIC"
        "AnWqA/gCCCEIPQD++ZNv/z8zu3F/Vb/+XH/y4/+XH//LRACjr4EB9ADRAQABEBAAGAAeV/QMAA+Y"
        "AP78jYf/4C//AX/4C//8Bf/+5APZAPZAP37gHFO7a5G7j7OBALeK94EB8YIBdfCBAw=="
    )
