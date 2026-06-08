"""Static configuration for the job tracker MVP."""

from __future__ import annotations

BASE_SEARCH_URL = "https://www.impactpool.org/search"
BASE_SITE_URL = "https://www.impactpool.org"
EEAS_TURKIYE_URL = "https://www.eeas.europa.eu/eeas/vacancies_en?f%5B0%5D=vacancy_site%3AT%C3%BCrkiye"
AB_ILAN_NGO_CAREERS_URL = "https://ab-ilan.com/ngocareers/"
RELIEFWEB_REMOTE_JOBS_URL = "https://reliefweb.int/jobs?view=unspecified-location"
DEFAULT_PER_PAGE = 40
DEFAULT_MAX_PAGES = 20
DEFAULT_QUERY_MAX_PAGES = 5
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_STATE_PATH = "data/seen_jobs.json"
DEFAULT_USER_AGENT = "job_tracker/0.1 (+https://www.impactpool.org/search)"

TITLE_KEYWORDS = (
    "data",
    "ai",
    "analysis",
    "analyst",
    "artificial intelligence",
    "analytics",
    "monitoring",
    "evaluation",
    "information management",
    "meal",
    "m&e",
    "im",
    "assessment",
)

EXCLUDED_TITLE_TERMS = (
    "intern",
    "internship",
)
