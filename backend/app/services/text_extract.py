from __future__ import annotations

from io import BytesIO


class TextExtractError(Exception):
    pass


SUPPORTED_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".txt"})


def _ext(filename: str) -> str:
    name = (filename or "").lower().strip()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def extract_text(*, filename: str, data: bytes) -> str:
    ext = _ext(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise TextExtractError(f"unsupported file type: {ext or 'unknown'}")
    if not data:
        raise TextExtractError("empty file")

    if ext == ".txt":
        return _extract_txt(data)
    if ext == ".pdf":
        return _extract_pdf(data)
    if ext == ".docx":
        return _extract_docx(data)
    if ext == ".doc":
        raise TextExtractError(
            "legacy .doc is not supported for auto extract; "
            "convert to .docx/.pdf or enter text manually"
        )
    raise TextExtractError(f"unsupported file type: {ext}")


def _extract_txt(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="ignore")
    cleaned = text.replace("\x00", "").strip()
    if not cleaned:
        raise TextExtractError("extracted text is empty")
    return cleaned


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise TextExtractError("pypdf is not installed") from exc

    try:
        reader = PdfReader(BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        raise TextExtractError(f"pdf extract failed: {exc}") from exc
    if not text:
        raise TextExtractError(
            "pdf text is empty (scanned PDF may need OCR before upload)"
        )
    return text


def _extract_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise TextExtractError("python-docx is not installed") from exc

    try:
        document = Document(BytesIO(data))
        parts = [p.text for p in document.paragraphs if p.text and p.text.strip()]
        text = "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        raise TextExtractError(f"docx extract failed: {exc}") from exc
    if not text:
        raise TextExtractError("docx text is empty")
    return text
