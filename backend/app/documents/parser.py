import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.config import Settings
from app.errors import ProcessingError


@dataclass
class ParsedPage:
    number: int
    text: str
    ocr: bool
    error: str | None = None


def tesseract(page, settings: Settings) -> str:
    with tempfile.TemporaryDirectory() as temp:
        image = Path(temp) / "page.png"
        page.get_pixmap(dpi=settings.ocr_dpi).save(image)
        try:
            response = subprocess.run(["tesseract", str(image), "stdout", "-l", "rus+eng"],
                                      capture_output=True, timeout=settings.ocr_timeout_seconds, check=True)
            return response.stdout.decode("utf-8").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProcessingError("ocr_failed", "OCR недоступен, завершился ошибкой или превысил таймаут") from exc


def parse_pdf(path: str, settings: Settings, ocr=tesseract) -> list[ParsedPage]:
    pages = []
    with pymupdf.open(path) as document:
        if document.needs_pass or not 1 <= document.page_count <= settings.max_pdf_pages:
            raise ProcessingError("pdf_limits", "PDF недоступен или превышает лимит страниц")
        for i, page in enumerate(document, 1):
            if page.rect.width * page.rect.height * (settings.ocr_dpi / 72) ** 2 > settings.max_page_pixels:
                raise ProcessingError("page_too_large", "Страница превышает лимит OCR")
            text = page.get_text("text", sort=True).strip()
            if text:
                pages.append(ParsedPage(i, text, False))
            else:
                try:
                    text = ocr(page, settings)
                    pages.append(ParsedPage(i, text, True, None if text else "ocr_empty"))
                except ProcessingError as exc:
                    pages.append(ParsedPage(i, "", True, exc.code))
    return pages


CLAUSE = re.compile(r"(?m)^\s*(\d{1,3}(?:\.\d{1,3}){1,6})\.?\s+")


def chunks(text: str, previous_clause: str | None = None, size: int = 1500, overlap: int = 150) -> tuple[list[dict], str | None]:
    if size <= overlap or overlap < 0:
        raise ValueError("Invalid chunk size/overlap")
    matches = list(CLAUSE.finditer(text))
    sections = []
    start, clause = 0, previous_clause
    for match in matches:
        if match.start() > start:
            sections.append((text[start:match.start()], clause))
        start, clause = match.start(), match.group(1)
    sections.append((text[start:], clause))
    result = []
    for section, code in sections:
        for offset in range(0, len(section), size - overlap):
            part = section[offset:offset + size].strip()
            if part:
                result.append({"text": part, "clause": code})
            if offset + size >= len(section):
                break
    return result, clause
