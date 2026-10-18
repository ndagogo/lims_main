# Roadmap alignment — HFDN Enterprise LIS + RIS

Source of truth: `HFDN_Enterprise_LIS_RIS_Architecture_and_Roadmap.pdf` (§17 phases).

## Module matrix

| Roadmap area | Codebase | Status |
|---|---|---|
| accounts | `core` User, login, lockout, password reset, MFA | Hardened |
| organization | `Centre`, `UserCentreAccess` | Done |
| rbac | Groups + `core.access` + DRF permission classes | Hardened |
| patients / visits | `patients` | Done |
| catalog | `core.catalog`, laboratory/radiology catalog | Done |
| orders | `LabOrder`, `ImagingOrder` | Partial+ |
| billing | invoices, payments, discounts, refunds, amendments | Hardened |
| samples | Sample, batch, recollection | Done |
| workflows | `workflows` app — versioned templates | Added |
| laboratory | results + QC API/UI | Hardened |
| radiology | worklists, exams, reports | Done |
| pacs | Orthanc client + study fields | Added |
| referrals | doctors, ledger, withdrawals | Hardened |
| portals | `portals` patient/physician/corporate — identity, receipts, released reports, preferences, support | Done |
| notifications | email/SMS/WhatsApp outbox, consented channels, event catalogue, template variable checks | Done |
| integrations | analyzers + gateway review UI | Hardened |
| inventory | `inventory` reagents/stock | Added |
| reporting | management dashboard + catalogue + audited exports (`reporting` app) | Done |
| audit | `AuditEvent` | Done |
| Celery/Redis | `config.celery` + tasks | Added |
| gateway | queue, local UI, ASTM stub | Hardened |

## Phase acceptance checklist

### Phase 01 — Foundation
- [x] Granular permission helpers on critical APIs
- [x] Email password-reset flow
- [x] Optional TOTP MFA for platform admins
- [x] Cross-centre / unauthorised-release tests

### Phase 02 — Registration & billing
- [x] Bill amendment records
- [x] Refund model (distinct from reversal)
- [x] Missing-tariff-price guard
- [x] Discount approver notification
- [x] Shared release-eligibility service

### Phase 03 — Core LIS workflow
- [x] Versioned workflow definitions + seeded templates
- [x] Server-enforced transitions
- [x] QC API + workspace UI
- [x] TAT / overdue helpers

### Phase 04 — RIS & PACS
- [x] Orthanc settings and study metadata
- [x] Reconciliation / failure queue

### Phase 05 — Portals and notifications
- [x] Patient portal (profile, visits, invoices, receipts, released results, downloads)
- [x] Portal login is UHID + date of birth; minors need the guardian phone; a phone number does not open every match
- [x] Notification preferences and support/correction requests
- [x] Physician portal (invite, ledger)
- [x] Corporate portal (statements)
- [x] Consented SMS, email, and WhatsApp delivery
- [x] Registration, referral, receipt, discount, recollection, authentication, result-ready, withdrawal, payout, and integration-failure events
- [x] Template variables validated before publish; messages omit passwords and result values and use a short-lived result link

### Phase 06 — Gateway
- [x] Gateway registration + API key
- [x] SQLite durable queue
- [x] Localhost config UI
- [x] Integration review screen
- [x] ASTM adapter skeleton

### Phase 07 — Enterprise
- [x] Celery/Redis wiring
- [x] Management reporting dashboard (precise KPIs, period/centre filters, 4 panels)
- [x] Report catalogue (dimensional revenue, commission, HMO, demographics, radiology, inventory)
- [x] Audited Excel/PDF exports; background large-export writes files
- [x] Inventory app
- [x] README alignment
