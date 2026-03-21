"""Impactpool scraping and filtering logic."""

from __future__ import annotations

import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .config import (
    BASE_SEARCH_URL,
    BASE_SITE_URL,
    DEFAULT_MAX_PAGES,
    DEFAULT_PER_PAGE,
    DEFAULT_QUERY_MAX_PAGES,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
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


def posting_matches_filters(posting: JobPosting) -> bool:
    if not title_matches_keywords(posting.title):
        return False

    if posting.recruitment_scope == "National":
        location = normalize_title(posting.location or "")
        if "ankara" not in location:
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
        search_html = _fetch_text(search_url, timeout_seconds=timeout_seconds)
        job_links = extract_job_links(search_html)
        if not job_links:
            continue

        for job_id, job_url, search_card_text in job_links:
            if job_id in seen_job_ids:
                continue

            seen_job_ids.add(job_id)
            detail_html = _fetch_text(job_url, timeout_seconds=timeout_seconds)
            posting = parse_job_detail(
                job_id=job_id,
                url=job_url,
                html=detail_html,
                search_card_text=search_card_text,
            )

            if posting_matches_filters(posting):
                matches.append(posting)

    if previous_snapshot:
        _merge_still_active_missing_jobs(
            matches=matches,
            previous_snapshot=previous_snapshot,
            timeout_seconds=timeout_seconds,
        )

    return matches


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
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")


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


def _merge_still_active_missing_jobs(
    matches: list[JobPosting],
    previous_snapshot: dict[str, JobPosting],
    timeout_seconds: int,
) -> None:
    current_by_id = {posting.job_id: posting for posting in matches}

    for job_id, previous_posting in previous_snapshot.items():
        if job_id in current_by_id:
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
