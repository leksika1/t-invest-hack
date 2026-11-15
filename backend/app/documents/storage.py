import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

import pymupdf

from app.config import Settings
from app.errors import ProcessingError


def validate_pdf(content: bytes, settings: Settings) -> int:
    if len(content) > settings.max_upload_bytes:
        raise ProcessingError("file_too_large", "PDF превышает лимит размера", 413)
    if not content.startswith(b"%PDF-"):
        raise ProcessingError("not_pdf", "Допускаются только PDF", 415)
    try:
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            if doc.needs_pass or not 1 <= doc.page_count <= settings.max_pdf_pages:
                raise ProcessingError("pdf_limits", "PDF зашифрован, пуст или содержит слишком много страниц")
            for page in doc:
                width, height = page.rect.width, page.rect.height
                if width <= 0 or height <= 0 or width * height * (settings.ocr_dpi / 72) ** 2 > settings.max_page_pixels:
                    raise ProcessingError("page_too_large", "Размер страницы PDF превышает лимит OCR")
            return doc.page_count
    except ProcessingError:
        raise
    except Exception as exc:
        raise ProcessingError("invalid_pdf", "PDF не открывается парсером", 415) from exc


def clean_filename(name: str) -> str:
    return Path(name.replace("\\", "/")).name[:255] or "document.pdf"


def save_pdf(content: bytes, settings: Settings) -> tuple[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    root = Path(settings.files_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / (digest + ".pdf")
    if not target.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as f:
                temporary = Path(f.name)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return digest, str(target)


def archive_pdfs(content: bytes, settings: Settings) -> list[tuple[str, bytes]]:
    if len(content) > settings.max_archive_bytes:
        raise ProcessingError("archive_too_large", "Архив превышает лимит", 413)
    if content.startswith(b"Rar!"):
        raise ProcessingError("rar_unsupported", "RAR не поддерживается; загрузите PDF вручную", 415)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > settings.max_archive_entries:
                raise ProcessingError("archive_limits", "Слишком много элементов ZIP")
            total = 0
            result = []
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
                    raise ProcessingError("unsafe_archive_path", "Небезопасный путь внутри ZIP")
                if stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                    raise ProcessingError("unsafe_archive_entry", "Ссылки и зашифрованные ZIP не поддерживаются")
                total += entry.file_size
                if total > settings.max_archive_unpacked_bytes or entry.file_size > settings.max_upload_bytes or entry.file_size / max(entry.compress_size, 1) > settings.max_archive_ratio:
                    raise ProcessingError("archive_limits", "Превышены лимиты распаковки ZIP")
                if entry.is_dir():
                    continue
                if name.lower().endswith((".zip", ".rar", ".7z")):
                    raise ProcessingError("nested_archive", "Вложенные архивы не поддерживаются")
                if not name.lower().endswith(".pdf"):
                    continue
                with archive.open(entry) as stream:
                    data = stream.read(settings.max_upload_bytes + 1)
                if data.startswith((b"PK\x03\x04", b"Rar!")):
                    raise ProcessingError("nested_archive", "Вложенный архив вместо PDF")
                validate_pdf(data, settings)
                result.append((name, data))
            if not result:
                raise ProcessingError("no_pdf_in_archive", "В ZIP нет поддерживаемых PDF", 415)
            return result
    except ProcessingError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise ProcessingError("invalid_archive", "ZIP не читается или метод сжатия не поддерживается", 415) from exc
