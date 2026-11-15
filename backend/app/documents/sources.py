"""Observed public HTML adapters. No e-disclosure scraping."""
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Protocol
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.config import Settings
from app.documents.http import SourceHTTP
from app.errors import ProcessingError
from app.iss.client import get_issue


@dataclass
class Identity:
    isin: str
    inn: str | None = None
    name: str | None = None
    ogrn: str | None = None
    registration_number: str | None = None
    issue_name: str | None = None


@dataclass
class Candidate:
    source: str
    title: str
    source_url: str
    download_url: str
    published_at: str | None = None
    type: str = "other"
    requires_review: bool = False


@dataclass
class Discovery:
    source: str
    status: str
    documents: list[Candidate] = field(default_factory=list)
    message: str | None = None


class DocumentSource(Protocol):
    def discover(self, identity: Identity) -> Discovery: ...
    def fetch(self, candidate: Candidate) -> bytes: ...


def document_type(title):
    text = title.lower()
    if "изменени" in text:
        return "amendment"
    if "проспект" in text:
        return "prospectus"
    if "решение" in text or "условия размещения" in text or "дсур" in text:
        return "decision"
    return "other"


def publication_date(text):
    text = text.strip().lower()
    try:
        return datetime.strptime(text, "%d.%m.%Y").date().isoformat()
    except ValueError:
        months = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]
        match = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", text)
        if match:
            month = next((i for i, name in enumerate(months, 1) if match[2].startswith(name)), None)
            if month:
                try:
                    return datetime(int(match[3]), month, int(match[1])).date().isoformat()
                except ValueError:
                    pass
    return None


def matches_issue(text: str, identity: Identity) -> bool:
    compact = re.sub(r"[^\w]", "", text).casefold().replace("p", "р")
    for key in [identity.isin, identity.registration_number]:
        if key and re.sub(r"[^\w]", "", key).casefold().replace("p", "р") in compact:
            return True
    # Series often appears instead of ISIN in the listing; require exact series token.
    if identity.issue_name:
        series = re.search(r"(?:[ПPРР]?\d{1,3}[РP]?[- ]?(?:БО[- ]?)?\d{1,3}|БО[- ]?\d{1,3})$", identity.issue_name, re.I)
        if series:
            token = re.sub(r"[^\w]", "", series.group()).casefold().replace("p", "р")
            if len(token) >= 4 and token in compact:
                return True
    return False


class WebSource:
    name = ""
    host = ""

    def __init__(self, settings: Settings, http=None):
        self.settings = settings
        self.http = http or SourceHTTP(settings, {self.host})

    def fetch(self, candidate):
        return self.http.request(candidate.download_url).content


class PrimeSource(WebSource):
    name, host = "prime", "disclosure.1prime.ru"

    def discover(self, identity):
        if identity.inn:
            url = f"https://{self.host}/Portal/Default.aspx?emId={identity.inn}"
        else:
            search = f"https://{self.host}/portal/emitents.aspx"
            soup = BeautifulSoup(self.http.request(search).text, "html.parser")
            data = {item["name"]: item.get("value", "") for item in soup.select('input[type="hidden"][name]')}
            data.update({"ctl00$Main$SearchOGRN": identity.ogrn or "", "ctl00$Main$SearchName": identity.name or "", "ctl00$Main$SearchBut": "Искать"})
            soup = BeautifulSoup(self.http.request(search, "POST", urlencode(data, encoding="cp1251", errors="replace").encode(), {"Content-Type": "application/x-www-form-urlencoded"}).text, "html.parser")
            links = [a for a in soup.select('a[href]') if "emid=" in a["href"].lower() and identity.name and identity.name.casefold() in a.get_text().casefold()]
            if len(links) != 1:
                return Discovery(self.name, "empty", message="Не найден однозначный эмитент; загрузите PDF")
            url = urljoin(search, links[0]["href"])
        response = self.http.request(url)
        soup = BeautifulSoup(response.text, "html.parser")
        inn_element = soup.select_one("#ctl00_Main_inn")
        if identity.inn and (not inn_element or inn_element.get_text(strip=True) != identity.inn):
            return Discovery(self.name, "empty", message="ИНН страницы не совпал")
        options = soup.select('select[name$="DdlCategories"] option')
        documents = self.parse_documents(response.text, url, identity)
        # ASP.NET categories are POSTed with current hidden state, not guessed query parameters.
        for option in options:
            if documents:
                break  # All category tables are already embedded on many issuer pages.
            value = option.get("value", "")
            if value == "signevent":
                continue
            if not any(k in option.get_text().lower() for k in ["эмис", "ценны", "проспект", "решени", "облигац"]):
                continue
            data = {e["name"]: e.get("value", "") for e in soup.select('input[type="hidden"][name]')}
            category = soup.select_one('select[name$="DdlCategories"]')["name"]
            data.update({"__EVENTTARGET": category, category: value})
            page = self.http.request(url, "POST", urlencode(data, encoding="cp1251", errors="replace").encode(), {"Content-Type": "application/x-www-form-urlencoded"})
            documents.extend(self.parse_documents(page.text, url, identity))
        documents = list({d.download_url: d for d in documents}.values())
        return Discovery(self.name, "succeeded" if documents else "empty", documents[:self.settings.collector_max_documents],
                         None if documents else "В доступных категориях нет PDF выбранного выпуска; у ряда эмитентов ПРАЙМ публикует только сообщения")

    @staticmethod
    def parse_documents(text, url, identity):
        soup = BeautifulSoup(text, "html.parser")
        result = []
        for a in soup.select('a[href]'):
            href = a["href"]
            if not any(k in href.lower() for k in ["getfile", "/files/", "getdocument", ".pdf", ".zip", ".rar"]):
                continue
            row = a.find_parent("tr") or a.parent
            title = row.get_text(" ", strip=True)
            if not matches_issue(title, identity):
                continue
            dates = re.findall(r"\b\d{2}\.\d{2}\.\d{4}\b", title)
            result.append(Candidate("prime", title[:500], url, urljoin(url, href), publication_date(dates[-1]) if dates else None, document_type(title)))
        return result


class SkrinSource(WebSource):
    name, host = "skrin", "disclosure.skrin.ru"

    def discover(self, identity):
        if identity.inn:
            profile = f"https://{self.host}/disclosure/{identity.inn}"
        else:
            url = f"https://{self.host}/Firms?" + urlencode({"q": identity.ogrn or identity.name or ""})
            soup = BeautifulSoup(self.http.request(url).text, "html.parser")
            links = {urljoin(url, a["href"]) for a in soup.select('a[href]') if re.fullmatch(r"/disclosure/\d+", a["href"])}
            if len(links) != 1:
                return Discovery(self.name, "empty", message="Не найден однозначный эмитент")
            profile = links.pop()
        soup = BeautifulSoup(self.http.request(profile).text, "html.parser")
        main = soup.select_one("main")
        if not main or (identity.inn and not re.search(r"ИНН\s*:?\s*" + re.escape(identity.inn) + r"\b", main.get_text(" ", strip=True))):
            return Discovery(self.name, "empty", message="Не подтверждён ИНН страницы эмитента")
        page_url = next((urljoin(profile, a["href"]) for a in main.select('a[href]') if "Выпуски ценных бумаг" in a.get_text()), None)
        if not page_url:
            return Discovery(self.name, "empty", message="Нет раздела выпусков ценных бумаг")
        documents, visited = [], set()
        while page_url and len(visited) < self.settings.collector_max_pages:
            visited.add(page_url)
            page = self.http.request(page_url)
            documents.extend(self.parse_documents(page.text, page_url, identity))
            soup = BeautifulSoup(page.text, "html.parser")
            # Only pagination of this issuer's securities section.
            section_path = urlsplit(page_url).path
            page_url = next((urljoin(page_url, a["href"]) for a in soup.select('a[href]')
                             if urlsplit(urljoin(page_url, a["href"])).path == section_path and "page=" in a["href"] and urljoin(page_url, a["href"]) not in visited), None)
            if len(documents) >= self.settings.collector_max_documents:
                break
        documents = list({d.download_url: d for d in documents}.values())
        return Discovery(self.name, "succeeded" if documents else "empty", documents[:self.settings.collector_max_documents],
                         "Поиск ограничен числом страниц; отсутствие результата не доказывает отсутствие документов" if page_url else None)

    @staticmethod
    def parse_documents(text, url, identity):
        soup = BeautifulSoup(text, "html.parser")
        result = []
        for a in soup.select('a[href]'):
            if "/disclosure_docs/" not in a["href"]:
                continue
            row = a.find_parent("tr") or a.find_parent("article") or a.parent.parent
            title = row.get_text(" ", strip=True) + " " + a["href"].rsplit("/", 1)[-1]
            if not matches_issue(title, identity):
                continue
            cells = row.find_all("td", recursive=False)
            published = publication_date(cells[3].get_text(" ", strip=True)) if len(cells) > 3 else None
            result.append(Candidate("skrin", title[:500], url, urljoin(url, a["href"]), published, document_type(title)))
        return result


class EDisclosureSource:
    def __init__(self, settings: Settings):
        self.settings = settings

    def discover(self, identity):
        if self.settings.edisclosure_api_url and self.settings.edisclosure_api_key:
            return Discovery("edisclosure", "adapter_not_implemented", message="Нужна спецификация официального договорного API")
        return Discovery("edisclosure", "official_access_required", message="Нужен официальный доступ")

    def fetch(self, candidate):
        raise ProcessingError("official_access_required", "Нужен официальный доступ")


class UploadSource:
    name = "upload"

    @staticmethod
    def prepare(content, filename, document_type, settings):
        """Push adapter: validate the full batch before common persistent ingest."""
        from app.documents.storage import validate_pdf
        validate_pdf(content, settings)
        return content, filename, document_type

    def discover(self, identity):
        return Discovery(self.name, "empty", message="PDF загружаются пользователем")

    def fetch(self, candidate):
        raise ProcessingError("upload_required", "Используйте multipart-загрузку PDF")


class MoexIssSource:
    def resolve(self, isin):
        return get_issue(isin)


def assisted(identity: Identity):
    # Stable public search landing; no unverified query parameter or CAPTCHA calls.
    return {"source": "edisclosure", "search_url": "https://www.e-disclosure.ru/poisk-po-kompaniyam",
            "query": identity.inn or identity.name or identity.isin, "mode": "manual_query",
            "message": "Откройте поиск, вставьте ИНН/название, скачайте PDF и загрузите; капча не обходится"}
