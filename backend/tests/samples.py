"""Minimal but structurally valid sample files for upload tests."""

import io
import zipfile

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
TXT = b"Employees receive 18 days of annual paid leave.\n"
MARKDOWN = "# Handbook\n\nCafé policy — *always* be kind.\n".encode()


def docx(text: str = "Hello from Word") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


DOCX = docx()
