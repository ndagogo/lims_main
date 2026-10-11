# Railway pre-production checklist

Use this before routing any real patient or financial data through the service.

## GitHub / secrets hygiene

- [ ] Repository is private (recommended).
- [ ] `git status` never shows `backend/.env`, `.env`, `*.sqlite3`, `media/`, or `.venv/`.
- [ ] Production secrets exist only in Railway Variables (not in GitHub Actions secrets unless needed for CI — CI uses disposable test values).
- [ ] Local `.env` and Railway `DJANGO_SECRET_KEY` are different values.

## Railway service

- [ ] GitHub repo connected; production deploys from `main` only.
- [ ] PostgreSQL attached; `DATABASE_URL` uses the private service reference.
- [ ] `DJANGO_DEBUG=False` and a unique production `DJANGO_SECRET_KEY` set.
- [ ] Public domain generated; `/healthz` returns HTTP 200.
- [ ] Volume mounted at `/app/media` **or** private S3 (`AWS_*`) configured with `django-storages`.
- [ ] All migrations reviewed, committed, and applied cleanly on PostgreSQL.
- [ ] `python manage.py check --deploy` reviewed (warnings resolved or documented).
- [ ] Roles seeded (`seed_roles`) and notification templates seeded.
- [ ] Production admin created with a unique password; least-privilege centre access reviewed.

## Quality / ops

- [ ] GitHub Actions `Django CI / test` required on `main`.
- [ ] Automated tests passed against PostgreSQL (centre isolation, billing, LIMS release, RIS controls).
- [ ] Database backups enabled; restore drill completed.
- [ ] Application logs/alerts configured; no patient data or credentials in logs.
- [ ] CORS, CSRF origins, and allowed hosts match real domains.
- [ ] Analyzer gateway is not publicly exposed.
- [ ] Clinical, privacy, security, and UAT sign-off before live use.
