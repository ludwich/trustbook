"""A small text PDF built by hand, used to measure document-reading speed (calibration)."""

from __future__ import annotations

_WORDS = (
    "The audit reviewed supplier contracts, delivery timelines and invoice controls for the "
    "quarter. Each finding lists the evidence, the owner and the agreed remediation date."
).split()


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _page_text(number: int, lines: int = 40) -> str:
    rows = [f"Section {number}: Review notes"]
    for i in range(lines):
        start = (number * 7 + i * 5) % len(_WORDS)
        rows.append(" ".join(_WORDS[(start + j) % len(_WORDS)] for j in range(12)))
    body = "\n".join(f"({_escape(row)}) '" for row in rows)
    return f"BT /F1 10 Tf 14 TL 60 760 Td\n{body}\nET"


def sample_pdf(pages: int) -> bytes:
    """A valid PDF with ``pages`` pages of plain text in Helvetica."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # pages tree, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids: list[str] = []
    for number in range(1, pages + 1):
        stream = _page_text(number).encode("latin-1")
        content_id = len(objects) + 1
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        page_id = len(objects) + 1
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content_id
        )
        kids.append(f"{page_id} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {pages} >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
