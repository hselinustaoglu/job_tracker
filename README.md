# job_tracker

Python MVP for tracking humanitarian sector jobs from Impactpool.

## What it does

- Scrapes `https://www.impactpool.org/search`
- Follows job links to collect richer metadata when available
- Filters by title keywords relevant to data and humanitarian analytics roles
- Compares each run with the previous snapshot
- Flags new jobs and removes jobs that disappeared from Impactpool
- Prints a Markdown report and saves the latest report to a file

## Current title filters

- `data`
- `ai`
- `analysis`
- `analyst`
- `artificial intelligence`
- `analytics`
- `monitoring`
- `evaluation`
- `information management`
- `meal`
- `m&e`
- `im`
- `assessment`

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
job-tracker
```

## Useful commands

Run the scraper:

```bash
job-tracker
```

Preview without saving new jobs as seen:

```bash
job-tracker --dry-run
```

Write the report and snapshot state:

```bash
job-tracker
```

Write the report and send an email summary:

```bash
PYTHONPATH=src python3 -m job_tracker --send-email
```

You can also put SMTP settings in a local `.env` file. An example is included in `.env.example`.

Reset the local deduplication state:

```bash
job-tracker --reset-state
```

## Data files

- Snapshot state is stored in `data/seen_jobs.json`
- The latest Markdown report is written to `data/latest_report.md`
- The latest new-jobs summary is written to `data/today_new_jobs.md`
- Both files are created automatically on first run

## GitHub Actions automation

The project includes a GitHub Actions workflow at `.github/workflows/job_tracker.yml`.

What it does:

- Runs daily on a schedule
- Runs the test suite first
- Executes the real tracker run with email enabled
- Commits updated files in `data/` back to the repository so the next run can compare against the latest snapshot

GitHub repository secrets you need to add:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `ALERT_FROM_EMAIL`
- `ALERT_TO_EMAIL`
- `SMTP_STARTTLS`

For Gmail, these values are typically:

- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USERNAME=your_gmail@gmail.com`
- `SMTP_PASSWORD=your_16_digit_app_password`
- `ALERT_FROM_EMAIL=your_gmail@gmail.com`
- `ALERT_TO_EMAIL=your_gmail@gmail.com`
- `SMTP_STARTTLS=true`

The current workflow cron is `0 6 * * *`, which corresponds to `09:00` in Istanbul during standard UTC+3 time.
