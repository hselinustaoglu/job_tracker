"""Data models used by the scraper."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class JobPosting:
    job_id: str
    title: str
    url: str
    organization: str | None = None
    location: str | None = None
    posting_date: str | None = None
    application_deadline: str | None = None
    contract_type: str | None = None
    recruitment_scope: str | None = None
    grade_level: str | None = None
    remote_status: str | None = None
    source: str = "Impactpool"

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)
