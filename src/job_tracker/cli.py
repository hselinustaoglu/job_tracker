"""Command-line entrypoint for the job tracker."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import DEFAULT_STATE_PATH
from .notifications import EmailConfigError, maybe_send_email_alert
from .models import JobPosting
from .scraper import fetch_matching_jobs
from .storage import load_previous_snapshot, save_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track new Impactpool jobs by title keywords.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matching jobs without saving them as seen.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Maximum number of search pages to scan.",
    )
    parser.add_argument(
        "--state-path",
        default=DEFAULT_STATE_PATH,
        help="Path to the JSON file used for deduplication state.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Delete the saved deduplication state before running.",
    )
    parser.add_argument(
        "--report-path",
        default="data/latest_report.md",
        help="Path to the Markdown report written on each run.",
    )
    parser.add_argument(
        "--summary-path",
        default="data/today_new_jobs.md",
        help="Path to the Markdown summary of today's new jobs.",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Send an email summary for today's new jobs using SMTP environment variables.",
    )
    return parser


def render_report(current: list[JobPosting], new: list[JobPosting], removed: list[JobPosting]) -> str:
    lines = ["# Impactpool Job Tracker Report", ""]

    lines.append(f"Current matching jobs: {len(current)}")
    lines.append(f"New since last run: {len(new)}")
    lines.append(f"Removed since last run: {len(removed)}")
    lines.append("")

    lines.extend(_render_section("New jobs", new))
    lines.extend(_render_section("Removed jobs", removed))
    lines.extend(_render_section("Current active jobs", current))

    return "\n".join(lines).rstrip()


def render_new_jobs_summary(new: list[JobPosting], removed: list[JobPosting]) -> str:
    lines = ["# Today's Job Tracker Summary", ""]
    lines.append(f"New jobs today: {len(new)}")
    lines.append(f"Removed since last run: {len(removed)}")
    lines.append("")
    lines.extend(_render_section("New jobs today", new))
    return "\n".join(lines).rstrip()


def _render_section(title: str, postings: list[JobPosting]) -> list[str]:
    lines = [f"## {title}", ""]
    if not postings:
        lines.append("None")
        lines.append("")
        return lines

    for posting in postings:
        lines.append(f"- {posting.title}")
        lines.append(f"  Organization: {posting.organization or 'Unknown'}")
        lines.append(f"  Location: {posting.location or 'Unknown'}")
        lines.append(f"  Posting date: {posting.posting_date or 'Unknown'}")
        lines.append(f"  Application deadline: {posting.application_deadline or 'Unknown'}")
        lines.append(f"  Contract type: {posting.contract_type or 'Unknown'}")
        lines.append(f"  Scope: {posting.recruitment_scope or 'Unknown'}")
        lines.append(f"  Grade/level: {posting.grade_level or 'Unknown'}")
        lines.append(f"  Remote: {posting.remote_status or 'Unknown'}")
        lines.append(f"  Link: {posting.url}")
        lines.append("")
    return lines


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    state_path = Path(args.state_path)
    report_path = Path(args.report_path)
    summary_path = Path(args.summary_path)
    if args.reset_state and state_path.exists():
        state_path.unlink()

    previous_snapshot = load_previous_snapshot(state_path)
    current_matches = fetch_matching_jobs(
        max_pages=args.max_pages,
        previous_snapshot=previous_snapshot,
    )
    current_by_id = {posting.job_id: posting for posting in current_matches}
    new_matches = [posting for posting in current_matches if posting.job_id not in previous_snapshot]
    removed_matches = [
        posting for job_id, posting in previous_snapshot.items() if job_id not in current_by_id
    ]

    report_text = render_report(
        current=current_matches,
        new=new_matches,
        removed=removed_matches,
    )
    summary_text = render_new_jobs_summary(new_matches, removed_matches)

    print(report_text)

    if not args.dry_run:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary_text, encoding="utf-8")
        save_snapshot(state_path, current_matches)
        if args.send_email:
            try:
                maybe_send_email_alert(new_matches, removed_matches, summary_text)
            except EmailConfigError as exc:
                print(f"Email alert skipped: {exc}")


if __name__ == "__main__":
    main()
