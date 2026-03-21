"""State persistence for snapshots across runs."""

from __future__ import annotations

import json
from pathlib import Path

from .models import JobPosting


def load_previous_snapshot(state_path: Path) -> dict[str, JobPosting]:
    if not state_path.exists():
        return {}

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    jobs = payload.get("jobs", [])
    snapshot: dict[str, JobPosting] = {}
    for item in jobs:
        if not isinstance(item, dict):
            continue
        job_id = item.get("job_id")
        title = item.get("title")
        url = item.get("url")
        if not isinstance(job_id, str) or not isinstance(title, str) or not isinstance(url, str):
            continue
        snapshot[job_id] = JobPosting(
            job_id=job_id,
            title=title,
            url=url,
            organization=item.get("organization"),
            location=item.get("location"),
            posting_date=item.get("posting_date"),
            application_deadline=item.get("application_deadline"),
            contract_type=item.get("contract_type"),
            recruitment_scope=item.get("recruitment_scope"),
            grade_level=item.get("grade_level"),
            remote_status=item.get("remote_status"),
            source=item.get("source", "Impactpool"),
        )
    return snapshot


def save_snapshot(state_path: Path, jobs: list[JobPosting]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"jobs": [job.to_dict() for job in jobs]}
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
