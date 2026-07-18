"""Create deterministic CSV and PNG outputs for the Foundation live smoke.

This fixture intentionally uses only the Python standard library. The
deterministic test model supplies it to OpenCode's write tool and the selected
Research Agent executes it through bash, proving the real agent/tool loop can
leave previewable artifacts without external model access.
"""

from __future__ import annotations

import binascii
import csv
import struct
import zlib
from pathlib import Path


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    """Encode one PNG chunk."""

    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def tiny_png() -> bytes:
    """Return a valid, deterministic 2x2 RGB PNG."""

    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    # Each scanline starts with filter byte 0, followed by two RGB pixels.
    pixels = b"\x00\x10\x70\xd0\xd0\x70\x10" + b"\x00\xd0\x70\x10\x10\x70\xd0"
    return (
        signature
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(pixels, level=9))
        + png_chunk(b"IEND", b"")
    )


def main() -> None:
    output = Path("outputs")
    output.mkdir(exist_ok=True)

    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["group", "value"])
        writer.writerows([("control", 1.0), ("treatment", 1.5), ("treatment", 1.8)])

    (output / "figure.png").write_bytes(tiny_png())
    print("created outputs/summary.csv and outputs/figure.png")


if __name__ == "__main__":
    main()
