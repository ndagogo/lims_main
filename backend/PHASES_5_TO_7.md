# HFDN Platform — Remaining Phases 5–7

## Phase 5 — Analyzer integration gateway foundation
- Analyzer inventory per centre, protocol metadata, analyzer-to-test/parameter mappings, inbound message queue and a basic HL7 v2 OBX parser.
- Incoming messages remain in a review queue. Parsing **does not** write values directly into clinical results or release them automatically.
- Protocol adapters are not one-size-fits-all. ASTM framing, vendor-specific HL7 profiles, serial/TCP transport, acknowledgements, sample matching, duplicate-message handling and device-level validation must be implemented and validated for each instrument model in a controlled test environment.

## Phase 6 — Referral management, communications, reporting
- Referral doctor directory and visit attribution.
- Append-only commission ledger, withdrawal requests, approval permissions and proof-of-payment requirement before settlement.
- SMS/email/WhatsApp notification outbox and editable templates. Provider delivery workers and credentials are intentionally not included; configure providers using secret management and add retry/idempotency handling before sending real messages.
- Operations summary API with centre-scoped counts and payment totals for the previous 30 days.

## Phase 7 — deployment/security foundations
- Production-only HTTPS/cookie/security headers, upload size controls, environment-based secret key and host allowlist.
- Review `.env.example`, reverse proxy, TLS, PostgreSQL backups, object storage, monitoring and restore tests before deployment.

## Generate migrations and validate
Run from `backend/` after installing requirements in an environment with package access:

```bash
python manage.py check
python manage.py makemigrations core patients laboratory billing radiology integrations referrals communications
python manage.py migrate
python manage.py seed_roles
python manage.py test core patients billing laboratory radiology integrations referrals communications
```

Then run an integration/UAT plan with synthetic patients and instrument simulators. These steps have **not** been run in this environment because Django is not installed and package downloads are unavailable. Do not use with live patient data until clinical workflow, privacy, access-control, data-protection and security reviews are complete.

## Critical production gaps
1. Per-model/field-level permissions need formal role matrix and tests; the present API foundation mostly requires authentication plus centre filtering.
2. Commission accrual rules must be approved by Finance/Legal and linked to eligible paid invoices, cancellations and reversals. No automatic accrual job is enabled.
3. Notification providers and async workers are not connected; queued notifications are not sent by this package alone.
4. Analyzer transports, ASTM parser, instrument acknowledgements, mapping, sample-ID reconciliation and clinical result approval are not production complete.
5. Secure upload validation, antivirus scanning, retention, backups, monitoring, rate-limit review, penetration testing and disaster recovery remain deployment gates.
