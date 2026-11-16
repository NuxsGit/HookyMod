"""A locally generated VP8 WebM for browser and desktop smoke tests.

The two 32x32 frames are a flat colour and a simple rectangle; no external
media or game files were used. The fixture is embedded so tests do not need an
encoder. It was generated with FFmpeg n9.0.2-17-g2a571b6068-20260930 and
libvpx 1.17.0; encoder versions may produce different bytes::

    ffmpeg -f lavfi -i 'color=c=0x29537a:s=32x32:r=2:d=1' \
      -vf "drawbox=x=8:y=8:w=16:h=16:color=0x72e0aa:t=fill:enable='gte(n,1)'" \
      -frames:v 2 -c:v libvpx -deadline best -b:v 48k -an -map_metadata -1 \
      -fflags +bitexact -flags:v +bitexact -f webm synthetic.webm

SHA-256: df0670266ba2488c92e453ffb633c2f5c4abbeead2636467b973c2b29daf3f13
"""
from __future__ import annotations

import base64


def make_webm() -> bytes:
    return base64.b64decode(
        "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAIdEU2bdLpNu4tT"
        "q4QVSalmU6yBoU27i1OrhBZUrmtTrIHGTbuMU6uEElTDZ1OsggEiTbuMU6uEHFO7a1OsggIH7AEA"
        "AAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmoCrXsYMPQkBNgIRMYXZm"
        "V0GETGF2ZkSJiECPQAAAAAAAFlSua9euAQAAAAAAAE7XgQFzxYgAAAAAAAAAAZyBACK1nIN1bmSI"
        "gQCGhVZfVlA4g4EBI+ODhB3NZQDgkLCBILqBIJqBAlWwhFW5gQFV7oEA7AEAAAAAAAACAAASVMNn"
        "0HNzzWPAi2PFiAAAAAAAAAABZ8iYRaOHRU5DT0RFUkSHi0xhdmMgbGlidnB4Z8ihRaOIRFVSQVRJ"
        "T05Eh5MwMDowMDowMS4wMDAwMDAwMDAAH0O2dUCK54EAo7qBAACAEAMAnQEqIAAgAABHCIWFiIWE"
        "iAICAnWqA/gD+AIIWQy9AP79hBj/1v/qCA/Ts/+pwYOfPx/go8mBAfQAkQQAARAQABgKw5Qv4D+A"
        "OqFnz/4N9Fn0pT5mHvdvKV3AiAcmsw9g/v9ji5//7OBnCbOBhkAxb/z6gm7UUB0iq4Rl6eAAHFO7"
        "a5G7j7OBALeK94EB8YIBd/CBAw=="
    )
