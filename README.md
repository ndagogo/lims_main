# HFDN Enterprise Healthcare Platform — Consolidated Development Build

This repository consolidates the current Phase 1–7 development foundations into one project tree. Railway deployment configuration is included, but the application is **not yet certified or verified as a production-ready clinical information system**.

## Current modules in this build

- Django custom user model, JWT authentication, groups/permissions, multi-centre access grants, and audit metadata.
- Patient master records, duplicate checks, UHID generation, centre-specific visit IDs, visit history, and document uploads.
- Test catalogue, tariffs and test prices, lab orders, invoices, invoice items, payments/receipts, and discount approval requests.
- Sample collection and recollection, Code 128 barcode endpoint, sample batching/dispatch/receiving, processing activities, result templates/parameters, and separate review/authentication/release actions.
- Radiology worklist foundation: modality configuration, centre-scoped imaging orders, radiographer assignment and start/end acquisition, report drafting/finalisation, versioned controlled overrides, and a Bootstrap worklist screen.
- Bootstrap-oriented Django screens, a separate dashboard shell, and a Flutter authentication/profile foundation.

## Roadmap alignment

See [`docs/ROADMAP_ALIGNMENT.md`](docs/ROADMAP_ALIGNMENT.md) for the module matrix against the Enterprise LIS+RIS architecture PDF. This build now includes password reset / optional admin MFA, bill amendments & refunds, versioned workflow templates, QC API, Orthanc PACS hooks, patient/physician/corporate portals, gateway queue + local config UI + ASTM skeleton, Celery/Redis wiring, inventory, and a management reporting dashboard.

## Important limitations

Not yet production-certified: per-instrument ASTM/TCP vendor certification, drag-and-drop workflow builder, full DICOM viewer, NDPA/legal sign-off, and penetration testing. Do not use real patient data until formal security, privacy, clinical workflow, and data-protection reviews are complete.

## Local setup

Requirements: Python 3.12+ (3.11+ usually works). SQLite is used locally when `DJANGO_DEBUG=True` and `DATABASE_URL` is empty. PostgreSQL is required for Railway and recommended for production-like local tests.

From the project root:

1. Create and activate a virtual environment (example: `py -m venv .venv` then `.\.venv\Scripts\Activate.ps1`).
2. Install dependencies: `pip install -r backend/requirements.txt`.
3. Copy `backend/.env.example` → `backend/.env` and set a unique local `DJANGO_SECRET_KEY`. **Never commit `.env`.**
4. `cd backend`
5. `python manage.py migrate`
6. `python manage.py seed_roles` and `python manage.py seed_notification_templates` and `python manage.py seed_workflows`
7. `python manage.py createsuperuser`
8. `python manage.py test`
9. `python manage.py runserver`

Optional local Postgres + Redis: `docker compose -f docker-compose.dev.yml up -d db redis`, then set `DATABASE_URL` and optionally `CELERY_BROKER_URL=redis://127.0.0.1:6379/0` in `.env`.

Before opening a pull request, also run `python manage.py makemigrations --check --dry-run` and `python manage.py check`.

## Initial configuration after login

- Create centres and assign users centre access with appropriate `can_view`, `can_register`, `can_edit`, and `can_approve` grants.
- Create a `SampleCodeConfig` for each centre; the one-digit centre code must be unique.
- Configure tests, tariffs, `TestPrice` rows, test parameters, and result templates.
- Ensure the required referral or registration form is attached to a visit before releasing results.

## API and UI

- API root: `http://127.0.0.1:8000/api/`
- Django admin: `http://127.0.0.1:8000/admin/`
- API login: `POST /api/auth/login/`
- Current profile: `GET /api/auth/me/`
- Patient registration screen: `/patients/register/`
- Accounts workspace: `/accounts/`
- Laboratory workspace: `/laboratory/`
- Radiology workspace: `/radiology/`
- Portals: `/portals/patient/`, `/portals/physician/`, `/portals/corporate/`
- Management dashboard: `/reporting/management/`
- Integration review: `/integrations/review/`
- Inventory: `/inventory/`
- Local gateway config UI: `python gateway/local_ui/app.py` (127.0.0.1 only)

The Flutter app is under `mobile/`. Run `flutter pub get`, then run the app. The default API host (`10.0.2.2`) is for an Android emulator. For a physical device, pass `--dart-define=API_BASE_URL=http://YOUR-LAN-IP:8000/api`; use HTTPS for any non-local deployment.

## Security and operational notes

- Production requires `DJANGO_DEBUG=False` and a strong `DJANGO_SECRET_KEY`; the settings now fail closed if the key is missing in production mode.
- Configure `DJANGO_ALLOWED_HOSTS`, HTTPS, trusted origins, database credentials, secret storage, backups, monitoring, malware scanning for uploads, and access reviews before deployment.
- Audit events record request metadata and status, not request bodies. Reverse-proxy IP headers must only be trusted when the proxy is controlled and `AUDIT_TRUST_X_FORWARDED_FOR=True` is explicitly configured.
- Financial corrections should use a controlled reversal/adjustment process rather than deleting confirmed payments or altering closed financial records.
- The visit and sample counters use database transactions, but concurrency behavior must be verified against the target PostgreSQL deployment.


## Remaining hardening
Historical notes remain in `PHASES_5_TO_7.md`. Current gap tracking lives in `docs/ROADMAP_ALIGNMENT.md`. Instrument vendor certification and full notification provider runtime tests remain outstanding.


## GitHub + Railway deployment

Safe workflow (local secrets stay local; production secrets stay in Railway):

1. Keep working with `backend/.env` (gitignored).
2. Commit and push code to a private GitHub repo.
3. Connect Railway to that GitHub repo (`main`).
4. Set production variables from `railway.variables.example` in the Railway dashboard.
5. Attach Postgres, mount `/app/media` (or configure S3), deploy.

Details: [`RAILWAY_DEPLOYMENT.md`](RAILWAY_DEPLOYMENT.md) · checklist: [`RAILWAY_CHECKLIST.md`](RAILWAY_CHECKLIST.md)

Runtime on Railway: Docker build runs `collectstatic` → pre-deploy runs `migrate` → gunicorn serves on `$PORT` → health check `/healthz`.

**Do not use real patient data until migrations and automated tests have been run successfully against PostgreSQL and security, clinical workflow, upload-storage, and backup/restore reviews are complete.**
