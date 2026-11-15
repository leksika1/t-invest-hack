import io
from pathlib import Path
import stat
import zipfile

import pymupdf
import pytest

from app.config import Settings
from app.documents.parser import chunks, parse_pdf
from app.documents.storage import archive_pdfs, save_pdf, validate_pdf
from app.errors import ProcessingError


def test_russian_pdf_and_clauses(tmp_path, pdf_bytes):
    settings = Settings(files_dir=str(tmp_path), _env_file=None)
    assert validate_pdf(pdf_bytes, settings) == 1
    digest, path = save_pdf(pdf_bytes, settings)
    pages = parse_pdf(path, settings)
    assert pages[0].number == 1 and not pages[0].ocr
    assert "ковенанты" in pages[0].text
    fragments, clause = chunks(pages[0].text)
    assert [f["clause"] for f in fragments] == ["9.5.1", "9.5.2"]
    assert clause == "9.5.2"
    assert save_pdf(pdf_bytes, settings)[0] == digest


@pytest.mark.parametrize("value,code", [(b"hello", "not_pdf"), (b"%PDF-garbage", "invalid_pdf")])
def test_bad_pdf(value, code):
    with pytest.raises(ProcessingError) as error:
        validate_pdf(value, Settings(_env_file=None))
    assert error.value.code == code


def test_size_limit_before_parser(pdf_bytes):
    with pytest.raises(ProcessingError, match="лимит"):
        validate_pdf(pdf_bytes, Settings(max_upload_bytes=10, _env_file=None))


def test_ocr_blank_page_and_error(tmp_path):
    doc = pymupdf.open(); doc.new_page(); path = tmp_path / "scan.pdf"; doc.save(path); doc.close()
    settings = Settings(_env_file=None)
    result = parse_pdf(str(path), settings, ocr=lambda p, s: "9.5.1. Ковенант из скана")
    assert result[0].ocr and "Ковенант" in result[0].text
    assert parse_pdf(str(path), settings, ocr=lambda p, s: "")[0].error == "ocr_empty"
    def failed(*args):
        raise ProcessingError("ocr_failed", "failure")
    assert parse_pdf(str(path), settings, ocr=failed)[0].error == "ocr_failed"


def zip_bytes(name, data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    return buf.getvalue()


def test_valid_zip_and_unsupported_rar(pdf_bytes):
    settings = Settings(_env_file=None)
    assert archive_pdfs(zip_bytes("sub/decision.pdf", pdf_bytes), settings) == [("sub/decision.pdf", pdf_bytes)]
    with pytest.raises(ProcessingError) as error:
        archive_pdfs(b"Rar!fake", settings)
    assert error.value.code == "rar_unsupported"


@pytest.mark.parametrize("name", ["../escape.pdf", "/absolute.pdf", "C:/escape.pdf", "dir/../../escape.pdf", "nested.zip"])
def test_unsafe_zip_names(name, pdf_bytes):
    with pytest.raises(ProcessingError):
        archive_pdfs(zip_bytes(name, pdf_bytes), Settings(_env_file=None))


def test_zip_bomb_and_symlink():
    with pytest.raises(ProcessingError) as error:
        archive_pdfs(zip_bytes("bomb.pdf", b"0" * 100000), Settings(max_archive_ratio=2, _env_file=None))
    assert error.value.code == "archive_limits"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("link.pdf"); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "target")
    with pytest.raises(ProcessingError) as error:
        archive_pdfs(buf.getvalue(), Settings(_env_file=None))
    assert error.value.code == "unsafe_archive_entry"


def test_chunk_overlap_and_page_continuation():
    result, clause = chunks("9.5.1. " + "abc " * 1000, size=100, overlap=10)
    assert len(result) > 2 and all(len(c["text"]) <= 100 for c in result)
    assert chunks("Продолжение пункта", clause)[0][0]["clause"] == "9.5.1"


def test_ocr_subprocess_timeout_is_explicit(tmp_path, monkeypatch):
    from app.documents.parser import tesseract
    import subprocess
    doc = pymupdf.open(); page = doc.new_page()
    def timed_out(*args, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired("tesseract", 60)
    monkeypatch.setattr(subprocess, "run", timed_out)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    with pytest.raises(ProcessingError) as error:
        tesseract(page, Settings(_env_file=None))
    assert error.value.code == "ocr_failed"
    doc.close()
