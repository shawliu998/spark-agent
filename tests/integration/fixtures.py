from __future__ import annotations


PAPER_TEXT = (
    "Brain computer interfaces improve communication for people with severe motor "
    "impairments using verified neural signals."
)

DATASET_CSV = b"group,value\nalpha,10\nbeta,20\ngamma,30\n"


def build_pdf() -> bytes:
    """Build a deterministic, one-page PDF without third-party dependencies."""

    escaped_text = (
        PAPER_TEXT.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    )
    content_stream = (
        "BT\n"
        "/F1 8 Tf\n"
        "72 720 Td\n"
        f"({escaped_text}) Tj\n"
        "ET\n"
    ).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("ascii")
            + content_stream
            + b"endstream"
        ),
    ]

    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{object_number} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")

    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(document)
