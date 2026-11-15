"""Isolated PDF/OCR subprocess: one bounded document, JSON result over stdout."""
import dataclasses
import json
import sys

from app.config import get_settings
from app.documents.parser import parse_pdf

if __name__ == "__main__":
    settings = get_settings()
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    # Родитель читает stdout как UTF-8: пишем байты сами, не завися от кодировки консоли (cp1251 на Windows)
    payload = json.dumps([dataclasses.asdict(page) for page in parse_pdf(sys.argv[1], settings)], ensure_ascii=False)
    sys.stdout.buffer.write(payload.encode("utf-8"))
    sys.stdout.buffer.flush()
