"""Impactpool scraping and filtering logic."""

from __future__ import annotations

import json
import re
import socket
import sys
import time
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import (
    AB_ILAN_NGO_CAREERS_URL,
    BASE_SEARCH_URL,
    BASE_SITE_URL,
    EEAS_TURKIYE_URL,
    RELIEFWEB_REMOTE_JOBS_URL,
    DEFAULT_MAX_PAGES,
    DEFAULT_PER_PAGE,
    DEFAULT_QUERY_MAX_PAGES,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    EXCLUDED_TITLE_TERMS,
    TITLE_KEYWORDS,
)
from .models import JobPosting

JOB_PATH_PATTERN = re.compile(r"/jobs/(?P<job_id>\d+)")
JSON_LD_SCRIPT_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(?P<content>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
TITLE_TAG_PATTERN = re.compile(r"<title[^>]*>(?P<content>.*?)</title>", re.IGNORECASE | re.DOTALL)
META_TAG_PATTERN = re.compile(r"<meta\s+(?P<attrs>[^>]*?)>", re.IGNORECASE | re.DOTALL)
HEADER_BLOCK_PATTERN = re.compile(
    r"</h1>(?P<content>.*?)(?:Application deadline:|Summary by Impactpool)",
    re.IGNORECASE | re.DOTALL,
)
DEADLINE_PATTERN = re.compile(
    r"Application deadline:\s*(?P<deadline>[^<\n\r]+)",
    re.IGNORECASE,
)
ATTRIBUTE_PATTERN = re.compile(
    r'(?P<name>[^\s=/>]+)\s*=\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\')',
    re.DOTALL,
)
CONTRACT_HINT_PATTERN = re.compile(
    r"(Consultant\s*-\s*Contractors Agreement\s*-\s*Consultancy|"
    r"Internship\s*-\s*Internship|"
    r"Volunteer\s*-\s*Volunteer|"
    r"Contract Agent|"
    r"Temporary Appointment|Fixed Term|FTA|"
    r"IPSA[-\s]?\d+|LICA[-\s]?\d+|NPSA[-\s]?\d+|"
    r"Consultancy|National Professional Officer|"
    r"International Professional|Locally recruited position|"
    r"Administrative support)",
    re.IGNORECASE,
)
GRADE_PATTERN = re.compile(
    r"\b(P-\d|D-\d|NO-[A-Z]|NO[A-Z]|G-\d|FS-\d|FG\s?[IVX]+|I?PSA[-\s]?\d+|NPSA[-\s]?\d+|LICA[-\s]?\d+|SC-\d|SB-\d|ICS\s?\d+|ICSC-\d+)\b",
    re.IGNORECASE,
)
REMOTE_PATTERN = re.compile(
    r"\b(remote|home[-\s]?based|home based|telework|work from home|fully remote|remote eligible)\b",
    re.IGNORECASE,
)
DEADLINE_DATE_PATTERN = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
AB_ILAN_FIELD_LABELS = {
    "job_title": "Ilan Basligi",
    "job_title_tr": "Ilan Basligi".casefold(),
    "job_title_unicode": "İlan Başlığı".casefold(),
    "location": "Lokasyon".casefold(),
    "deadline": "Son Başvuru Tarihi".casefold(),
}


def normalize_title(title: str) -> str:
    return " ".join(title.casefold().split())


def title_matches_keywords(title: str, keywords: tuple[str, ...] = TITLE_KEYWORDS) -> bool:
    normalized = normalize_title(title)
    normalized_words = f" {normalized} "

    for keyword in keywords:
        term = normalize_title(keyword)
        if " " in term or "&" in term:
            if term in normalized:
                return True
            continue

        if term == "ai":
            if re.search(r"\bai\b", normalized):
                return True
            continue

        if term == "im":
            if re.search(r"\bim\b", normalized):
                return True
            continue

        if f" {term} " in normalized_words:
            return True

    return False


def title_has_excluded_terms(title: str, excluded_terms: tuple[str, ...] = EXCLUDED_TITLE_TERMS) -> bool:
    normalized = normalize_title(title)

    for excluded_term in excluded_terms:
        term = normalize_title(excluded_term)
        if not term:
            continue
        if re.search(rf"\b{re.escape(term)}s?\b", normalized):
            return True

    return False


def posting_matches_filters(posting: JobPosting) -> bool:
    if posting.source == "ReliefWeb":
        return _reliefweb_posting_matches_filters(posting)
    if posting.source == "AB-ilan":
        return _ab_ilan_posting_matches_filters(posting)
    if posting.source == "EEAS":
        return _eeas_posting_matches_filters(posting)

    if not title_matches_keywords(posting.title):
        return False

    if title_has_excluded_terms(posting.title):
        return False

    if posting.recruitment_scope == "National":
        location = normalize_title(posting.location or "")
        if "ankara" not in location:
            return False

    return True


def _eeas_posting_matches_filters(posting: JobPosting) -> bool:
    if title_has_excluded_terms(posting.title):
        return False

    location = normalize_title(posting.location or "")
    if not any(marker in location for marker in ("ankara", "istanbul", "turkiye", "türkiye", "turkey")):
        return False

    deadline = normalize_title(posting.application_deadline or "")
    if "expired" in deadline:
        return False

    return True


def _ab_ilan_posting_matches_filters(posting: JobPosting) -> bool:
    if not title_matches_keywords(posting.title):
        return False

    if title_has_excluded_terms(posting.title):
        return False

    location = normalize_title(posting.location or "")
    if not any(marker in location for marker in ("ankara", "uzaktan")):
        return False

    if _is_past_deadline(posting.application_deadline):
        return False

    return True


def _reliefweb_posting_matches_filters(posting: JobPosting) -> bool:
    if not title_matches_keywords(posting.title):
        return False

    if title_has_excluded_terms(posting.title):
        return False

    if _is_past_deadline(posting.application_deadline):
        return False

    return True


def extract_job_links(search_html: str) -> list[tuple[str, str, str]]:
    parser = _AnchorExtractor()
    parser.feed(search_html)
    jobs: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()

    for href, text in parser.links:
        match = JOB_PATH_PATTERN.search(href)
        if not match or not text:
            continue

        job_id = match.group("job_id")
        if job_id in seen_ids:
            continue

        seen_ids.add(job_id)
        jobs.append((job_id, urljoin(BASE_SITE_URL, href), text))

    return jobs


def parse_job_detail(job_id: str, url: str, html: str, search_card_text: str | None = None) -> JobPosting:
    contract_type = _parse_contract_type(search_card_text)
    header_summary = _extract_header_summary(html)
    recruitment_scope = _parse_recruitment_scope(header_summary)
    grade_level = _parse_grade_level(header_summary) or _parse_grade_level(search_card_text)
    remote_status = None
    application_deadline = _parse_application_deadline(html)

    schema_job = _extract_job_schema(html)
    if schema_job is not None:
        title = schema_job.get("title") or _fallback_title(html) or f"Job {job_id}"
        organization = _parse_organization(schema_job)
        location = _parse_location(schema_job)
        posting_date = schema_job.get("datePosted")
        remote_status = _parse_remote_status(schema_job, header_summary, search_card_text, html)
        return JobPosting(
            job_id=job_id,
            title=title.strip(),
            url=url,
            organization=organization,
            location=location,
            posting_date=posting_date,
            application_deadline=application_deadline,
            contract_type=contract_type,
            recruitment_scope=recruitment_scope,
            grade_level=grade_level,
            remote_status=remote_status,
        )

    fallback_description = _fallback_location(html)
    organization, location = _parse_description_fields(fallback_description)

    return JobPosting(
        job_id=job_id,
        title=_fallback_title(html) or f"Job {job_id}",
        url=url,
        organization=organization or _fallback_organization(html),
        location=location or fallback_description,
        posting_date=None,
        application_deadline=application_deadline,
        contract_type=contract_type,
        recruitment_scope=recruitment_scope,
        grade_level=grade_level,
        remote_status=_parse_remote_status(None, header_summary, search_card_text, html),
    )


def fetch_matching_jobs(
    max_pages: int = DEFAULT_MAX_PAGES,
    per_page: int = DEFAULT_PER_PAGE,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    previous_snapshot: dict[str, JobPosting] | None = None,
) -> list[JobPosting]:
    matches: list[JobPosting] = []
    seen_job_ids: set[str] = set()

    search_specs = _build_search_specs(max_pages=max_pages, per_page=per_page)
    for search_url in search_specs:
        try:
            search_html = _fetch_text(search_url, timeout_seconds=timeout_seconds)
        except Exception as exc:
            _warn_fetch_failure("search page", search_url, exc)
            continue
        job_links = extract_job_links(search_html)
        if not job_links:
            continue

        for job_id, job_url, search_card_text in job_links:
            if job_id in seen_job_ids:
                continue

            seen_job_ids.add(job_id)
            try:
                detail_html = _fetch_text(job_url, timeout_seconds=timeout_seconds)
            except Exception as exc:
                _warn_fetch_failure(f"job detail {job_id}", job_url, exc)
                continue
            posting = parse_job_detail(
                job_id=job_id,
                url=job_url,
                html=detail_html,
                search_card_text=search_card_text,
            )

            if posting_matches_filters(posting):
                matches.append(posting)

    matches.extend(fetch_eeas_jobs(timeout_seconds=timeout_seconds))
    matches.extend(fetch_ab_ilan_jobs(timeout_seconds=timeout_seconds))
    matches.extend(fetch_reliefweb_jobs(timeout_seconds=timeout_seconds))

    if previous_snapshot:
        _merge_still_active_missing_jobs(
            matches=matches,
            previous_snapshot=previous_snapshot,
            timeout_seconds=timeout_seconds,
        )

    return matches


def fetch_eeas_jobs(timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[JobPosting]:
    try:
        eeas_html = _fetch_text(EEAS_TURKIYE_URL, timeout_seconds=timeout_seconds)
    except Exception as exc:
        _warn_fetch_failure("EEAS vacancy list", EEAS_TURKIYE_URL, exc)
        return []
    eeas_parser = _EeasVacancyListParser()
    eeas_parser.feed(eeas_html)
    eeas_parser.close()

    postings: list[JobPosting] = []
    for raw_vacancy in eeas_parser.vacancies:
        posting = _build_eeas_posting(raw_vacancy)
        if posting and posting_matches_filters(posting):
            postings.append(posting)

    return postings


def fetch_ab_ilan_jobs(timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[JobPosting]:
    try:
        ab_ilan_html = _fetch_text(AB_ILAN_NGO_CAREERS_URL, timeout_seconds=timeout_seconds)
    except Exception as exc:
        _warn_fetch_failure("AB-ilan NGO careers list", AB_ILAN_NGO_CAREERS_URL, exc)
        return []
    parser = _AbIlanListParser()
    parser.feed(ab_ilan_html)
    parser.close()

    postings: list[JobPosting] = []
    for row in parser.rows:
        posting = _build_ab_ilan_posting(row)
        if posting and posting_matches_filters(posting):
            postings.append(posting)
    return postings


def fetch_reliefweb_jobs(timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[JobPosting]:
    try:
        reliefweb_html = _fetch_text(RELIEFWEB_REMOTE_JOBS_URL, timeout_seconds=timeout_seconds)
    except Exception as exc:
        _warn_fetch_failure("ReliefWeb remote jobs list", RELIEFWEB_REMOTE_JOBS_URL, exc)
        return []
    parser = _ReliefWebListParser()
    parser.feed(reliefweb_html)
    parser.close()

    postings: list[JobPosting] = []
    for item in parser.items:
        posting = _build_reliefweb_posting(item)
        if posting and posting_matches_filters(posting):
            postings.append(posting)
    return postings


def _build_search_specs(max_pages: int, per_page: int) -> list[str]:
    urls: list[str] = []

    for page in range(1, max_pages + 1):
        urls.append(f"{BASE_SEARCH_URL}?{urlencode({'page': page, 'per_page': per_page})}")

    targeted_terms = [
        "data",
        "ai",
        "analysis",
        "analyst",
        "analytics",
        "artificial intelligence",
        "monitoring",
        "evaluation",
        "information management",
        "meal",
        "m&e",
        "assessment",
    ]
    for term in targeted_terms:
        for page in range(1, DEFAULT_QUERY_MAX_PAGES + 1):
            urls.append(
                f"{BASE_SEARCH_URL}?{urlencode({'q': term, 'page': page, 'per_page': per_page})}"
            )

    return urls


def _extract_job_schema(html: str) -> dict[str, object] | None:
    for match in JSON_LD_SCRIPT_PATTERN.finditer(html):
        content = unescape(match.group("content")).strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue

        schema = _find_job_posting_schema(parsed)
        if schema is not None:
            return schema

    return None


def _fetch_text(url: str, timeout_seconds: int) -> str:
    request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except (TimeoutError, socket.timeout, URLError, HTTPError) as exc:
            if attempt == attempts:
                raise
            time.sleep(min(attempt, 3))
    raise RuntimeError(f"Failed to fetch {url}")


def _warn_fetch_failure(kind: str, url: str, exc: Exception) -> None:
    print(f"Warning: failed to fetch {kind} {url}: {exc}", file=sys.stderr)


def _find_job_posting_schema(payload: object) -> dict[str, object] | None:
    if isinstance(payload, dict):
        if payload.get("@type") == "JobPosting":
            return payload

        graph_items = payload.get("@graph")
        if isinstance(graph_items, list):
            for item in graph_items:
                schema = _find_job_posting_schema(item)
                if schema is not None:
                    return schema

    if isinstance(payload, list):
        for item in payload:
            schema = _find_job_posting_schema(item)
            if schema is not None:
                return schema

    return None


def _parse_organization(schema_job: dict[str, object]) -> str | None:
    organization = schema_job.get("hiringOrganization")
    if isinstance(organization, dict):
        name = organization.get("name")
        if isinstance(name, str):
            return name.strip()
    return None


def _parse_location(schema_job: dict[str, object]) -> str | None:
    job_location = schema_job.get("jobLocation")

    if isinstance(job_location, list) and job_location:
        locations = [_parse_location_entry(item) for item in job_location]
        filtered = [item for item in locations if item]
        if filtered:
            return " | ".join(filtered)

    if isinstance(job_location, dict):
        return _parse_location_entry(job_location)

    job_location_type = schema_job.get("jobLocationType")
    if isinstance(job_location_type, str) and "remote" in job_location_type.casefold():
        return "Remote"

    return None


def _parse_location_entry(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None

    address = entry.get("address")
    if not isinstance(address, dict):
        return None

    parts: list[str] = []
    for field in ("addressLocality", "addressRegion", "addressCountry"):
        value = address.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    return ", ".join(parts) or None


def _fallback_title(html: str) -> str | None:
    h1_parser = _FirstTagTextExtractor("h1")
    h1_parser.feed(html)
    if h1_parser.text:
        return h1_parser.text

    match = TITLE_TAG_PATTERN.search(html)
    if match:
        return _clean_html_text(match.group("content"))
    return None


def _fallback_organization(html: str) -> str | None:
    return _extract_meta_content(html, "property", "og:site_name")


def _fallback_location(html: str) -> str | None:
    for field_type, field_name in (("name", "description"), ("property", "og:description")):
        content = _extract_meta_content(html, field_type, field_name)
        if content:
            return content
    return None


def _extract_meta_content(html: str, attr_name: str, attr_value: str) -> str | None:
    for match in META_TAG_PATTERN.finditer(html):
        attrs = _parse_attributes(match.group("attrs"))
        if attrs.get(attr_name) == attr_value and attrs.get("content"):
            return attrs["content"].strip()
    return None


def _parse_attributes(raw_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in ATTRIBUTE_PATTERN.finditer(raw_attrs):
        value = match.group("dq") if match.group("dq") is not None else match.group("sq")
        attrs[match.group("name").casefold()] = unescape(value or "").strip()
    return attrs


def _clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(text).split())


def _parse_application_deadline(html: str) -> str | None:
    match = DEADLINE_PATTERN.search(html)
    if not match:
        return None
    deadline = " ".join(match.group("deadline").split())
    return deadline or None


def _extract_header_summary(html: str) -> str | None:
    match = HEADER_BLOCK_PATTERN.search(html)
    if not match:
        return None
    cleaned = _clean_html_text(match.group("content"))
    return cleaned or None


def _parse_description_fields(description: str | None) -> tuple[str | None, str | None]:
    if not description:
        return None, None

    match = re.match(r"(?P<org>.+?)\s+vacancy:\s+.+\s+in\s+(?P<location>.+)", description)
    if not match:
        return None, None

    organization = match.group("org").strip()
    location = match.group("location").strip()
    return organization or None, location or None


def _parse_contract_type(search_card_text: str | None) -> str | None:
    if not search_card_text:
        return None
    match = CONTRACT_HINT_PATTERN.search(search_card_text)
    if not match:
        return None
    return " ".join(match.group(0).split())


def _parse_recruitment_scope(header_summary: str | None) -> str | None:
    if not header_summary:
        return None
    lowered = header_summary.casefold()
    if "internationally recruited position" in lowered:
        return "International"
    if "locally recruited position" in lowered:
        return "National"
    if re.search(r"\bnational\b\s+(?:[a-z]{1,6}-[a-z]|\w+\s+officer|speaks\b|level\b)", lowered):
        return "National"
    if re.search(
        r"\binternational\b\s+(?:[a-z]{1,6}-[a-z]|\w+\s+professional|speaks\b|level\b)",
        lowered,
    ):
        return "International"
    return None


def _parse_grade_level(text: str | None) -> str | None:
    if not text:
        return None
    match = GRADE_PATTERN.search(text)
    if not match:
        return None
    return match.group(0).upper().replace(" ", "")


def _parse_remote_status(
    schema_job: dict[str, object] | None,
    header_summary: str | None,
    search_card_text: str | None,
    html: str,
) -> str | None:
    if schema_job:
        job_location_type = schema_job.get("jobLocationType")
        if isinstance(job_location_type, str):
            normalized_type = job_location_type.casefold()
            if "remote" in normalized_type or "telecommute" in normalized_type:
                return "Remote"

    for text in (header_summary, search_card_text, _fallback_location(html), _fallback_title(html)):
        if not text:
            continue
        if REMOTE_PATTERN.search(text):
            return "Remote"

    return "On-site/Hybrid"


def _build_eeas_posting(raw_vacancy: dict[str, str | None]) -> JobPosting | None:
    title = (raw_vacancy.get("title") or "").strip()
    url = (raw_vacancy.get("url") or "").strip()
    details_text = (raw_vacancy.get("details") or "").strip()
    if not title or not url or "deadline" not in details_text.casefold():
        return None
    if re.search(r"\bexpired\b", details_text, re.IGNORECASE):
        return None

    status = _extract_eeas_field(details_text, "status")
    if status and "expired" in status.casefold():
        return None

    location = _extract_eeas_field(details_text, "location")
    teaser = _extract_eeas_field(details_text, "teaser")
    if not _is_turkiye_eeas_job(title=title, location=location, teaser=teaser):
        return None

    category = _extract_eeas_field(details_text, "category")
    deadline = _extract_eeas_field(details_text, "deadline")
    recruitment_scope = _infer_eeas_scope(category, teaser)

    return JobPosting(
        job_id=f"eeas:{url}",
        title=title,
        url=urljoin("https://www.eeas.europa.eu", url),
        organization=_infer_eeas_organization(title, teaser),
        location=location,
        application_deadline=deadline,
        contract_type=category,
        recruitment_scope=recruitment_scope,
        remote_status="Unknown",
        source="EEAS",
    )


def _extract_ab_ilan_fields(details_text: str) -> dict[str, str]:
    normalized = " ".join(details_text.split())
    extracted: dict[str, str] = {}
    patterns = {
        "job_title": r"(?:İlan Başlığı|Ilan Basligi)\s*:\s*(?P<value>.*?)(?=\s+(?:Lokasyon|Son Başvuru Tarihi)\s*:|$)",
        "location": r"Lokasyon\s*:\s*(?P<value>.*?)(?=\s+(?:İlan Başlığı|Ilan Basligi|Son Başvuru Tarihi)\s*:|$)",
        "deadline": r"Son Başvuru Tarihi\s*:\s*(?P<value>.*?)(?=\s+(?:İlan Başlığı|Ilan Basligi|Lokasyon)\s*:|$)",
    }
    for field_name, pattern in patterns.items():
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            extracted[field_name] = match.group("value").strip()

    return extracted


def _build_ab_ilan_posting(row: dict[str, str | None]) -> JobPosting | None:
    title = (row.get("title") or "").strip()
    url = (row.get("url") or "").strip()
    if not title or not url:
        return None

    location = (row.get("location") or "").strip() or None
    deadline = (row.get("deadline") or "").strip() or None

    return JobPosting(
        job_id=f"ab-ilan:{url}",
        title=title,
        url=urljoin("https://ab-ilan.com", url),
        organization=(row.get("organization") or "").strip() or "Unknown",
        location=location,
        application_deadline=deadline,
        remote_status="Remote" if location and "uzaktan" in normalize_title(location) else "On-site/Hybrid",
        source="AB-ilan",
    )


def _build_reliefweb_posting(item: dict[str, str | None]) -> JobPosting | None:
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    if not title or not url:
        return None

    closing_date = (item.get("closing_date") or "").strip() or None
    return JobPosting(
        job_id=f"reliefweb:{url}",
        title=title,
        url=url,
        organization=(item.get("organization") or "").strip() or "Unknown",
        posting_date=(item.get("posted_date") or "").strip() or None,
        application_deadline=closing_date,
        location="Remote / Roster / Roving",
        remote_status="Remote",
        source="ReliefWeb",
    )


def _is_past_deadline(deadline: str | None, today: date | None = None) -> bool:
    if not deadline:
        return False

    active_today = today or date.today()
    normalized = deadline.strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(normalized, fmt).date()
            return parsed < active_today
        except ValueError:
            continue
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            parsed = datetime.strptime(normalized, fmt).date()
            return parsed < active_today
        except ValueError:
            continue
    return False


def _extract_eeas_field(details_text: str, field_name: str) -> str | None:
    label_order = ["status", "teaser", "location", "category", "deadline"]
    label_display = {
        "status": ("New opportunity", "Expiring soon", "Expired"),
        "teaser": ("Teaser",),
        "location": ("Location",),
        "category": ("Category",),
        "deadline": ("Deadline",),
    }

    start_positions: list[tuple[int, str]] = []
    for label in label_order:
        for display in label_display[label]:
            index = details_text.find(display)
            if index >= 0:
                start_positions.append((index, label))
                break

    start_positions.sort()
    field_map: dict[str, str] = {}
    for idx, (start, label) in enumerate(start_positions):
        end = start_positions[idx + 1][0] if idx + 1 < len(start_positions) else len(details_text)
        chunk = details_text[start:end].strip()
        for display in label_display[label]:
            if chunk.startswith(display):
                value = chunk[len(display) :].strip(" :\n")
                field_map[label] = " ".join(value.split())
                break

    return field_map.get(field_name)


def _is_turkiye_eeas_job(title: str, location: str | None, teaser: str | None) -> bool:
    combined = normalize_title(" ".join(part for part in (title, location or "", teaser or "") if part))
    turkiye_markers = (
        "turkiye",
        "türkiye",
        "turkey",
        "ankara",
        "istanbul",
        "delegation to türkiye",
        "delegation to turkey",
    )
    return any(marker in combined for marker in turkiye_markers)


def _infer_eeas_scope(category: str | None, teaser: str | None) -> str | None:
    normalized_category = normalize_title(category or "")
    normalized_teaser = normalize_title(teaser or "")
    if "local agent" in normalized_category or "local agent" in normalized_teaser:
        return "National"
    if "international position" in normalized_category:
        return "International"
    return None


def _infer_eeas_organization(title: str, teaser: str | None) -> str:
    combined = normalize_title(" ".join(part for part in (title, teaser or "") if part))
    if "delegation" in combined:
        return "EEAS - EU Delegation"
    return "EEAS"


def _merge_still_active_missing_jobs(
    matches: list[JobPosting],
    previous_snapshot: dict[str, JobPosting],
    timeout_seconds: int,
) -> None:
    current_by_id = {posting.job_id: posting for posting in matches}

    for job_id, previous_posting in previous_snapshot.items():
        if job_id in current_by_id:
            continue
        if previous_posting.source != "Impactpool":
            continue

        try:
            detail_html = _fetch_text(previous_posting.url, timeout_seconds=timeout_seconds)
        except Exception:
            continue

        detail_posting = parse_job_detail(
            job_id=job_id,
            url=previous_posting.url,
            html=detail_html,
        )
        if not posting_matches_filters(detail_posting):
            continue
        if _is_job_closed(detail_html) and not detail_posting.application_deadline:
            continue

        matches.append(detail_posting)


def _is_job_closed(html: str) -> bool:
    if _parse_application_deadline(html):
        return False

    cleaned_html = _clean_html_text(html)
    header_prefix = cleaned_html[:500]
    return bool(re.search(r"\bClosed\b", header_prefix, re.IGNORECASE))


class _AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return

        attr_map = {name.casefold(): value for name, value in attrs}
        href = attr_map.get("href")
        if href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._current_href is None:
            return

        text = " ".join(" ".join(self._current_text).split())
        self.links.append((self._current_href, text))
        self._current_href = None
        self._current_text = []


class _FirstTagTextExtractor(HTMLParser):
    def __init__(self, target_tag: str) -> None:
        super().__init__()
        self._target_tag = target_tag.casefold()
        self._capturing = False
        self._parts: list[str] = []
        self.text: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.text is None and tag.casefold() == self._target_tag:
            self._capturing = True

    def handle_data(self, data: str) -> None:
        if self._capturing and self.text is None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capturing and tag.casefold() == self._target_tag:
            self.text = " ".join(" ".join(self._parts).split()) or None
            self._capturing = False


class _EeasVacancyListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.vacancies: list[dict[str, str | None]] = []
        self._in_heading = False
        self._heading_level = None
        self._current_href: str | None = None
        self._heading_parts: list[str] = []
        self._details_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.casefold()
        if tag_name in {"h2", "h3", "h4"}:
            self._flush_current()
            self._in_heading = True
            self._heading_level = tag_name
            self._current_href = None
            self._heading_parts = []
            self._details_parts = []
            return

        if self._in_heading and tag_name == "a":
            attr_map = {name.casefold(): value for name, value in attrs}
            href = attr_map.get("href")
            if href:
                self._current_href = href

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_heading:
            self._heading_parts.append(cleaned)
        elif self._heading_parts:
            self._details_parts.append(cleaned)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.casefold()
        if self._in_heading and tag_name == self._heading_level:
            self._in_heading = False
            self._heading_level = None

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _flush_current(self) -> None:
        title = " ".join(self._heading_parts).strip()
        details = " ".join(self._details_parts).strip()
        if title and self._current_href:
            self.vacancies.append(
                {
                    "title": title,
                    "url": self._current_href,
                    "details": details,
                }
            )
        self._current_href = None
        self._heading_parts = []
        self._details_parts = []


class _AbIlanListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str | None]] = []
        self._in_row = False
        self._current_cell_parts: list[str] = []
        self._current_cells: list[dict[str, str | None]] = []
        self._current_href: str | None = None
        self._cell_has_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.casefold()
        if tag_name == "tr":
            self._in_row = True
            self._current_cells = []
            self._current_cell_parts = []
            self._current_href = None
            self._cell_has_link = False
            return

        if not self._in_row:
            return

        if tag_name == "td":
            self._current_cell_parts = []
            self._current_href = None
            self._cell_has_link = False
            return

        if tag_name == "a":
            attr_map = {name.casefold(): value for name, value in attrs}
            href = attr_map.get("href")
            if href:
                self._current_href = href
                self._cell_has_link = True

    def handle_data(self, data: str) -> None:
        if not self._in_row:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self._current_cell_parts.append(cleaned)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.casefold()
        if not self._in_row:
            return

        if tag_name == "td":
            text = " ".join(self._current_cell_parts).strip()
            self._current_cells.append({"text": text, "href": self._current_href})
            self._current_cell_parts = []
            self._current_href = None
            self._cell_has_link = False
            return

        if tag_name == "tr":
            self._commit_row()
            self._in_row = False

    def _commit_row(self) -> None:
        texts = [cell.get("text") or "" for cell in self._current_cells]
        if len(texts) < 6:
            return
        if normalize_title(texts[2]) in {"ilan basligi", "job announcements"}:
            return

        href = self._current_cells[2].get("href") or self._current_cells[0].get("href")
        if not href:
            return

        self.rows.append(
            {
                "published_at": texts[1],
                "title": texts[2],
                "organization": texts[3],
                "location": texts[4],
                "deadline": texts[5],
                "url": href,
            }
        )


class _ReliefWebListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, str | None]] = []
        self._in_article = False
        self._current: dict[str, str | None] | None = None
        self._capture_title = False
        self._capture_org = False
        self._capture_time_kind: str | None = None
        self._last_dt_label: str | None = None
        self._org_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.casefold()
        attr_map = {name.casefold(): value or "" for name, value in attrs}

        if tag_name == "article" and "rw-river-article--job" in attr_map.get("class", ""):
            self._in_article = True
            self._current = {}
            self._org_parts = []
            return

        if not self._in_article or self._current is None:
            return

        if tag_name == "h3" and "rw-river-article__title" in attr_map.get("class", ""):
            self._capture_title = True
            return

        if self._capture_title and tag_name == "a":
            href = attr_map.get("href")
            if href:
                self._current["url"] = href
            return

        if tag_name == "dt" and "rw-entity-meta__tag-label" in attr_map.get("class", ""):
            self._last_dt_label = ""
            return

        if tag_name == "dd" and "rw-entity-meta__tag-value--source" in attr_map.get("class", ""):
            self._capture_org = True
            self._org_parts = []
            return

        if tag_name == "time":
            if self._last_dt_label == "posted":
                self._capture_time_kind = "posted_date"
            elif self._last_dt_label == "closing-date":
                self._capture_time_kind = "closing_date"

    def handle_data(self, data: str) -> None:
        if not self._in_article or self._current is None:
            return

        cleaned = " ".join(data.split())
        if not cleaned:
            return

        if self._capture_title:
            self._current["title"] = ((self._current.get("title") or "") + " " + cleaned).strip()
            return

        if self._capture_org:
            self._org_parts.append(cleaned)
            return

        if self._capture_time_kind:
            self._current[self._capture_time_kind] = cleaned
            self._capture_time_kind = None
            return

        if self._last_dt_label is not None:
            normalized = normalize_title(cleaned)
            if normalized == "organization":
                self._last_dt_label = "source"
            elif normalized == "posted":
                self._last_dt_label = "posted"
            elif normalized == "closing date":
                self._last_dt_label = "closing-date"

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.casefold()
        if not self._in_article:
            return

        if tag_name == "h3":
            self._capture_title = False
            return

        if tag_name == "dd" and self._capture_org and self._current is not None:
            self._current["organization"] = " ".join(self._org_parts).strip()
            self._capture_org = False
            self._org_parts = []
            return

        if tag_name == "dt":
            return

        if tag_name == "article" and self._current:
            self.items.append(self._current)
            self._current = None
            self._in_article = False
            self._last_dt_label = None
