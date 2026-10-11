# Local → GitHub → Railway (secrets-safe)

This repo is set up so you can:

1. develop on your machine with `backend/.env` (never committed),
2. push source code to GitHub,
3. deploy from GitHub to Railway with secrets stored only in Railway Variables.

Railway builds from the **repository root** using `Dockerfile` + `railway.toml`. The Django app in `backend/` is copied into the image; runtime `WORKDIR` is `/app` (where `manage.py` lives).

## Secrets rule (read this first)

| Where | What belongs there |
|---|---|
| `backend/.env` (local only) | Local `DJANGO_SECRET_KEY`, optional local DB URL, provider test keys |
| Railway → Variables | Production `DJANGO_SECRET_KEY`, `DATABASE_URL`, SMS/email/S3 keys |
| GitHub | Source code only — **no** `.env`, no real secrets |
| Committed templates | `backend/.env.example`, `railway.variables.example` (placeholders only) |

Never paste a production secret into a commit, PR, chat log, or screenshot.

---

## 1. Local development

```powershell
cd backend
py -m venv ..\.venv
..\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env: set a unique DJANGO_SECRET_KEY for local use
python manage.py migrate
python manage.py seed_roles
python manage.py seed_notification_templates
# optional demo data (DEBUG only):
# python manage.py seed_synthetic
python manage.py createsuperuser
python manage.py check
python manage.py test
python manage.py runserver 127.0.0.1:8000
```

Notes:

- With `DJANGO_DEBUG=True` and empty `DATABASE_URL`, Django uses local SQLite.
- For Postgres locally: `docker compose -f docker-compose.dev.yml up -d db`, then set `DATABASE_URL` in `.env` as shown in `.env.example`.
- `backend/.env` is gitignored. If it ever appears in `git status`, stop and fix `.gitignore` before committing.

Generate a strong local secret:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

---

## 2. First-time GitHub push

From the repository root (after files are committed):

```powershell
# If the repo is not initialized yet:
git init
git branch -M main
git add .
git status   # confirm .env, media/, db.sqlite3, .venv/ are NOT listed
git commit -m "Initial HFDN health platform import"

# Create an empty private GitHub repo, then:
git remote add origin https://github.com/<your-org-or-user>/<repo-name>.git
git push -u origin main
```

Prefer a **private** GitHub repository for clinical software.

Ongoing work:

```powershell
git checkout -b feature/short-name
# ... edit code ...
git add .
git status
git commit -m "Describe why the change exists"
git push -u origin HEAD
# open a PR into main; wait for Django CI to pass
```

---

## 3. One-time Railway setup

1. In Railway: **New Project → Deploy from GitHub repo** → select this repo / `main`.
2. Add a **PostgreSQL** service in the same project.
3. On the Django/web service, open **Variables** and set values from `railway.variables.example`.
   Minimum required:
   - `DJANGO_DEBUG=False`
   - `DJANGO_SECRET_KEY` = new random secret (not the local one)
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (use your Postgres service name)
   - `DJANGO_ALLOWED_HOSTS` = `healthcheck.railway.app,${{RAILWAY_PUBLIC_DOMAIN}}` (or paste the public hostname)
   - `SECURE_SSL_REDIRECT=True`
   - `MEDIA_ROOT=/app/media`
4. Generate a public domain (Networking). Railway injects `RAILWAY_PUBLIC_DOMAIN`; settings add it to allowed hosts / CSRF origins.
5. Mount a **Volume** at `/app/media` **or** configure private S3 (`AWS_*` vars). Do not enable real patient uploads until storage is durable and backed up.
6. Confirm `railway.toml` is used: build via root `Dockerfile`, `preDeployCommand` runs `migrate --noinput`, health check hits `/healthz`.

Runtime chain:

1. **Build:** `collectstatic` (Dockerfile)
2. **Pre-deploy:** `python manage.py migrate --noinput`
3. **Start:** gunicorn on `0.0.0.0:$PORT`

After the first successful deploy:

```text
railway run python manage.py createsuperuser
# or use Railway shell / one-off command
python manage.py seed_roles
python manage.py seed_notification_templates
```

---

## 4. Environment variables

### Local (`backend/.env`)

See `backend/.env.example`.

### Railway

See `railway.variables.example`. Summary:

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL URL from Railway Postgres |
| `DJANGO_SECRET_KEY` | Yes | Unique production signing key |
| `DJANGO_DEBUG` | Yes | Must be `False` |
| `DJANGO_ALLOWED_HOSTS` | Yes | Hostnames including `healthcheck.railway.app` |
| `RAILWAY_PUBLIC_DOMAIN` | Provided by Railway | Auto-added to hosts/CSRF |
| `SECURE_SSL_REDIRECT` | Yes | `True` behind Railway HTTPS |
| `MEDIA_ROOT` | Recommended | `/app/media` with a volume |
| `AWS_STORAGE_BUCKET_NAME` | Optional | Enables private S3 media storage |
| `AWS_S3_REGION_NAME` | With S3 | Region for the bucket |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | With S3 | Bucket credentials |
| `WEB_CONCURRENCY` / `GUNICORN_TIMEOUT` | Optional | Gunicorn tuning |
| Notification / email / SMS / WhatsApp vars | Optional | Patient messaging providers |

---

## 5. Production caveats

- Containers are ephemeral: uploads need a volume or S3.
- Backups and restore drills are separate from “deploy succeeded”.
- Commit migration files before deploy; do not auto-generate migrations in production start.
- The analyzer gateway under `gateway/` stays on the centre network — not public.
- This prepares deployment wiring; it does **not** certify clinical production readiness.

---

## 6. CI

`.github/workflows/django-ci.yml` runs on pushes/PRs to `main` and `develop`: install deps, `check`, migration dry-run, migrate on Postgres, full test suite.

Protect `main` so the `Django CI / test` check must pass before merge. Point Railway production deploys at `main` only.
