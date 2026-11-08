# Job Alert Engine

A lightweight Python service that monitors job listings every 30 minutes, filters them against user rules, and sends email alerts through Gmail SMTP.

For a full guide on choosing categories, locations, experience ranges, and keywords for different professions, see [GUIDE.md](GUIDE.md).

## Key Features

- Low memory usage (~30-45 MB peak RAM). No headless browser is used.
- SQLite database (WAL mode) tracks seen jobs and sent notifications so the same job is never sent twice to the same user.
- Duplicate vacancies re-posted under a new ID are detected by content hash and skipped.
- Rich user filtering: categories, locations (with automatic Dhaka/Chattogram neighborhood expansion and exclusions), experience range, OR/AND/exclude keywords, job types, work modes (`office`, `remote`, `hybrid`), minimum salary, company allow/block lists, and posting age.
- Supports both instant alerts and scheduled daily digests per user.
- Includes `manage.py` for adding/editing/removing users and managing the database from the terminal.
- Automatically reloads `config.json` and `.env` when modified.
- Fault-isolated runtime: network outages, malformed API items, SMTP failures, and bad config edits are logged and retried on subsequent cycles without crashing the daemon.
- Classic desktop-style tabular HTML email layout (with plaintext fallback) compatible with Yahoo Mail, Gmail, and Outlook.

## Project Structure

```text
job-alert-engine/
  job_notifier/
    config.py      - Loads and validates .env and config.json
    scraper.py     - Fetches job listings from the JSON API with retry and backoff
    matcher.py     - Matches categories, locations, keywords, experience, salary, and work mode
    db.py          - SQLite storage for users, jobs, and notification history
    templates.py   - HTML and plaintext email builders
    mailer.py      - Gmail SMTP client (SSL or STARTTLS) with connection reuse
    engine.py      - Coordinates polling, matching, and email dispatch
    scheduler.py   - Background loop for 30-minute polls and daily digests
    cli.py         - Service runner CLI (--run, --test-fetch, --test-email)
    manage.py      - Administrative CLI (users, database, and inspection)
  deploy/
    job-notifier.service - systemd unit file with 80 MB memory cap
    install.sh           - Installer script for Linux
  tests/
    test_core.py   - Offline unit tests
  ecosystem.config.js - PM2 process manager configuration
  config.example.json
  .env.example
  requirements.txt
  GUIDE.md         - Comprehensive role selection and user configuration guide
  main.py          - Service runner entry point
  manage.py        - Administrative CLI entry point
```

## Setup

1. Create the virtual environment and install dependencies:

```bash
cd job-alert-engine
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

2. Initialize `config.json`, `.env`, and the database:

```bash
.venv/bin/python manage.py init
```

3. Configure Gmail credentials in `.env`:
   - Enable 2-Step Verification on your Google account.
   - Generate a 16-character App Password under Google Account -> Security -> App passwords.
   - Set `GMAIL_USER` and `GMAIL_APP_PASSWORD` in `.env`.
   - Default connection is SSL on port 465. To use STARTTLS on port 587, set `SMTP_SECURITY=starttls` and `SMTP_PORT=587`.

## Administrative CLI (`manage.py`)

Use `manage.py` to manage users and the SQLite database. Changes to users are saved atomically to `config.json` and synced immediately to the database.

```bash
# View system status, database size, and notification counts
.venv/bin/python manage.py status

# List all configured users and their alert counts
.venv/bin/python manage.py list-users

# Add a user interactively (step-by-step prompts)
.venv/bin/python manage.py add-user

# Add a user with flags
.venv/bin/python manage.py add-user \
  --name "Rahim" \
  --email "rahim@example.com" \
  --categories "IT/Telecommunication" \
  --locations "Dhaka" \
  --exclude-locations "Savar,Ashulia,Gazipur,Narayanganj" \
  --min-years 0 \
  --max-years 1 \
  --keywords "Python,Backend,Django,FastAPI" \
  --exclude-keywords "Senior,Lead,Manager" \
  --job-types "FullTime,Intern" \
  --min-salary 25000 \
  --mode instant

# Edit an existing user (interactive if no update flags are passed, or via flags)
.venv/bin/python manage.py edit-user rahim@example.com --locations "Dhaka" --min-salary 30000
.venv/bin/python manage.py edit-user rahim@example.com --mode digest --digest-time 09:00,18:00

# Enable, disable, or remove a user
.venv/bin/python manage.py disable-user rahim@example.com
.venv/bin/python manage.py enable-user rahim@example.com
.venv/bin/python manage.py remove-user rahim@example.com

# Clear notification history for one user so jobs can match again
.venv/bin/python manage.py reset-user rahim@example.com

# Clear the entire database (or --notifications-only to keep stored jobs)
.venv/bin/python manage.py clear-db
.venv/bin/python manage.py clear-db --notifications-only

# Reset failed notifications back to pending, or purge old jobs
.venv/bin/python manage.py retry-failed
.venv/bin/python manage.py purge-old --days 90

# Inspect stored jobs and notifications
.venv/bin/python manage.py list-jobs --limit 20 --search Python
.venv/bin/python manage.py list-notifications --limit 25
.venv/bin/python manage.py categories
```

## Service Runner (`main.py` / `job_notifier`)

```bash
# Fetch listings once and print matches to the terminal (no emails sent)
.venv/bin/python main.py --test-fetch

# Send a sample email to verify SMTP settings
.venv/bin/python main.py --test-email you@example.com
.venv/bin/python main.py --test-email you@example.com --template digest

# Start the continuous background scheduler
.venv/bin/python main.py --run

# Run offline unit tests
.venv/bin/python -m unittest discover -s tests -v
```

## Configuration Reference (`config.json`)

Each entry in `users` supports the following fields (see [GUIDE.md](GUIDE.md) for detailed examples):

- `name`: Recipient display name.
- `email`: Recipient email address.
- `categories`: List of category names or numeric IDs (for example `["IT/Telecommunication"]` or `[8]`). Empty list matches all categories.
- `locations`: List of target cities, districts, or neighborhoods (for example `["Dhaka"]` or `["Uttara", "Banani"]`). Empty list matches all locations.
- `exclude_locations`: List of locations to reject even if they match `locations` (for example `["Savar", "Gazipur"]`).
- `include_anywhere_in_bd`: Whether to include `"Anywhere in Bangladesh"` jobs when `locations` is set (default `true`).
- `experience.min_years`: Minimum years of experience (integer, default `0`).
- `experience.max_years`: Maximum years of experience (integer, or `null` for no upper limit).
- `experience.include_unspecified`: Whether to include listings with `"NA"`, `"Negotiable"`, or `"Not Required"` experience (default `true`).
- `keywords`: OR-matched keywords (at least one must match when non-empty).
- `required_keywords`: AND-matched keywords (every keyword in this list must match).
- `exclude_keywords`: Words that reject a job if found in its title (for example `["Senior", "Lead"]`).
- `keyword_scope`: `"all"` (title, company, and description snippet) or `"title"` (job title only).
- `job_types`: Allowed employment types (`"FullTime"`, `"PartTime"`, `"Contract"`, `"Intern"`, `"Freelance"`).
- `workplaces`: Allowed work modes (`"office"`, `"remote"`, `"hybrid"`).
- `min_salary`: Minimum monthly salary in BDT (or `null` for no minimum).
- `include_negotiable_salary`: Whether to allow undisclosed/negotiable salaries when `min_salary` is set (default `true`).
- `companies` / `exclude_companies`: Optional company allowlist and blocklist.
- `max_age_days`: Only match jobs published within the last `N` days (or `null` for any active listing).
- `schedule.mode`: `"instant"` or `"digest"`.
- `schedule.digest_time`: `"09:00"` or `["09:00", "18:00"]` (24-hour format, required when mode is `"digest"`).
- `enabled`: Set to `false` to pause notifications for a user without deleting their history.

## Deployment with PM2

The repository includes [ecosystem.config.js](ecosystem.config.js), which automatically detects `.venv/bin/python`, caps memory at 80 MB (`max_memory_restart: "80M"`), and runs a single fork instance with unbuffered log output.

```bash
# 1. Ensure the virtual environment is installed
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Start the service with PM2
pm2 start ecosystem.config.js

# 3. Persist across reboots
pm2 save
pm2 startup

# 4. Monitor logs and status
pm2 status
pm2 logs job-notifier
pm2 restart job-notifier
```

You can also start `main.py` directly with PM2 (`pm2 start main.py --interpreter ./.venv/bin/python --name job-notifier`); when launched under PM2 without flags, `main.py` automatically defaults to `--run`.

## Linux Deployment (systemd)

To install and run as a `systemd` background service on Linux:

```bash
sudo apt install -y python3 python3-venv rsync
sudo ./deploy/install.sh
sudo nano /opt/job-alert-engine/.env
sudo -u jobnotifier /opt/job-alert-engine/.venv/bin/python /opt/job-alert-engine/manage.py add-user
sudo systemctl restart job-notifier
```

Check logs and service status with:

```bash
systemctl status job-notifier
journalctl -u job-notifier -f
```
