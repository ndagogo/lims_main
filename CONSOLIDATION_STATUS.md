# Consolidation and validation status

Date: 2026-10-09 (updated after RIS foundation work)

## Consolidation performed

- Used the cumulative Phase 3 source tree as the baseline for Phases 1–3.
- Removed generated Python bytecode/cache directories from the deliverable.
- Fixed a missing `LabOrder` import in the invoice `add_test` action.
- Tightened result-release gating so an arbitrary `OTHER` attachment alone does not satisfy the required referral/registration-form check.
- Added a serializer for the audit-event detail endpoint, which otherwise had `serializer_class=None`.
- Made production settings fail closed when `DJANGO_DEBUG=False` and no `DJANGO_SECRET_KEY` is configured.
- Updated setup instructions to use existing migrations and documented missing modules and production limitations.
- Added a Phase 4 RIS foundation: modality records, centre-scoped imaging orders, radiographer assignment/start/end workflow, radiology reports, finalisation, revision snapshots, audited controlled overrides, API routes, a Bootstrap worklist screen, and initial workflow tests.

## Checks completed in this environment

- Python source compilation: passed (`compileall`).
- ZIP archive integrity: passed after packaging (the consolidated ZIP is rechecked for each updated build).
- Dependency installation: blocked because this environment cannot resolve package-index DNS; Django runtime checks, migrations, and database-backed tests could not be executed here.

## Required before pilot or production

1. Install dependencies in a network-enabled environment.
2. Run `python manage.py check` and `python manage.py makemigrations --check --dry-run`.
3. Run `python manage.py migrate` against a clean test database, including the new `radiology` app migration.
4. Run `python manage.py test core patients billing laboratory radiology` and resolve every failure.
5. Add integration tests for permissions, centre isolation, financial reversals, concurrent identifiers, result-state transitions, required-document release gating, and radiology worklist/report lifecycle.
6. Complete security review, privacy/data-protection review, clinical validation, backup/restore rehearsal, and deployment hardening.

This repository is a consolidated development build, not a certification of clinical or production readiness.

## Railway deployment preparation (2026-10-09)

- Added repository-root Dockerfile for Railway builds while keeping Django source in `backend/`.
- Added `railway.toml` with pre-deploy migrations, healthcheck, and restart policy.
- Added `/healthz` endpoint that checks database connectivity without returning connection details.
- Production settings now default `DJANGO_DEBUG` to false, require `DJANGO_SECRET_KEY` and `DATABASE_URL` outside debug mode, support Railway's public domain, and configure WhiteNoise/static assets and HTTPS proxy settings.
- Added initial migration files for integrations, referrals and communications, whose migration directories previously contained no initial migrations.
- Added Git ignore rules, Docker ignore rules, and Railway deployment and pre-production checklists.
- Static checks: Python syntax compilation, TOML parsing and archive integrity only. Django is unavailable in the build environment because package index DNS resolution fails, so `manage.py check`, `check --deploy`, `makemigrations --check`, PostgreSQL migrations, test suite and Docker image build are still unverified. Do not treat this as a production-readiness certification until these checks pass in a connected local or CI environment.
