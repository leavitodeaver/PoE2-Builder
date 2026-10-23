"""PoB build codes: URL-safe base64 of zlib-compressed build XML."""

import base64
import zlib


def decode(code: str) -> str:
    """Return the build XML inside a PoB import code."""
    code = code.strip()
    padded = code + "=" * (-len(code) % 4)
    return zlib.decompress(base64.urlsafe_b64decode(padded)).decode("utf-8")


def encode(xml: str) -> str:
    """Return a PoB import code for build XML, in the same form PoB itself produces."""
    return base64.urlsafe_b64encode(zlib.compress(xml.encode("utf-8"), 9)).decode("ascii")
