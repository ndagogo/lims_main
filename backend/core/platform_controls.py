"""Sensitive platform configuration hub — not the day-to-day dashboard."""


def platform_control_sections():
    """Grouped shortcuts that stay inside the platform UI (never Django admin)."""
    return [
        {
            'title': 'Catalogue & organisation',
            'items': [
                ('Catalog & pricing', '/catalog/', 'bi-journal-bookmark', 'Tariffs, tests, service groups, imaging, centres, clients'),
                ('Centres & result formatting', '/catalog/centres/', 'bi-building', 'Sites, logos, sample digits, lab/radiology print layout'),
                ('Referral doctors', '/catalog/doctors/', 'bi-person-badge', 'Doctors, institutions, commission setup'),
                ('Organisation clients', '/catalog/clients/', 'bi-briefcase', 'HMO and corporate accounts'),
            ],
        },
        {
            'title': 'People & access',
            'items': [
                ('User management', '/users/', 'bi-people', 'Create staff, centre access, password reset'),
                ('Roles & privileges', '/roles/', 'bi-shield-lock', 'Create roles, set privileges, assign members'),
                ('MFA setup', '/account/mfa/', 'bi-phone', 'Optional authenticator for platform admins'),
                ('Audit trail', '/platform/audit/', 'bi-shield-check', 'Search and export the enterprise audit trail'),
                ('Modify demographics', '/patients/demographics/', 'bi-person-gear', 'Permissioned demographic changes with audit'),
            ],
        },
        {
            'title': 'Workflows, templates & QC',
            'items': [
                ('Workflow templates', '/platform/workflows/', 'bi-diagram-2', 'Normal, micro, cyto, and histo'),
                ('TAT rules & pauses', '/platform/tat-rules/', 'bi-stopwatch', 'Per-test TAT, pause reasons, escalation'),
                ('Result templates', '/platform/result-templates/', 'bi-file-earmark-text', 'Versioned clinical report layouts'),
                ('Radiology report templates', '/platform/radiology-templates/', 'bi-file-medical', 'CT, MRI, US, and X-ray layouts'),
                ('Report accept window', '/platform/radiology-accept/', 'bi-hourglass', 'Minutes before an unaccepted study can be reassigned'),
                ('QC materials', '/platform/qc-materials/', 'bi-clipboard2-pulse', 'Lots and targets'),
                ('QC runs', '/platform/qc-runs/', 'bi-activity', 'Acceptable and out-of-range runs'),
            ],
        },
        {
            'title': 'Instruments & inventory',
            'items': [
                ('Analyzers', '/platform/analyzers/', 'bi-cpu', 'Instrument profiles and protocols'),
                ('Analyzer mappings', '/platform/analyzer-mappings/', 'bi-diagram-3', 'Instrument codes to tests'),
                ('Analyzer messages', '/platform/analyzer-messages/', 'bi-envelope-paper', 'Raw instrument traffic'),
                ('Gateway registration', '/platform/gateways/', 'bi-router', 'Local gateway API keys'),
                ('Modality workstations', '/platform/modalities/', 'bi-broadcast-pin', 'AE title, IP, port, and worklist connection'),
                ('PACS failures', '/platform/pacs-failures/', 'bi-exclamation-diamond', 'Orthanc reconcile queue'),
                ('Sample code config', '/platform/sample-codes/', 'bi-123', 'Per-centre sample digit'),
                ('Inventory SKUs', '/platform/inventory-skus/', 'bi-capsule', 'Reagent catalogue'),
            ],
        },
        {
            'title': 'Finance',
            'items': [
                ('Bill amendments', '/platform/bill-amendments/', 'bi-pencil-square', 'Audited invoice adjustments'),
                ('Cancel / refund tests', '/accounts/refunds/', 'bi-arrow-counterclockwise', 'Per-test cancel and refund history'),
                ('Bill edit', '/accounts/bill-edit/', 'bi-receipt-cutoff', 'Remove or replace billed tests'),
                ('Refunds history', '/platform/refunds/', 'bi-cash-stack', 'Explicit refund tracking'),
            ],
        },
        {
            'title': 'Notifications & portals',
            'items': [
                ('Notification templates', '/platform/notification-templates/', 'bi-chat-left-text', 'SMS, email, and WhatsApp copy'),
                ('Notification outbox', '/platform/notification-outbox/', 'bi-send', 'Queued and sent messages'),
                ('Patient portal', '/portals/patient/login/', 'bi-person-vcard', 'UHID and verified channel login'),
                ('Physician portal', '/portals/physician/login/', 'bi-person-workspace', 'Doctor code and invite onboarding'),
                ('Corporate portal', '/portals/corporate/login/', 'bi-building', 'Organisation statements'),
                ('Physician invites', '/platform/physician-invites/', 'bi-envelope-check', 'Invite tokens for doctors'),
            ],
        },
        {
            'title': 'Workspaces',
            'items': [
                ('Management dashboard', '/reporting/management/', 'bi-graph-up-arrow', 'Finance, TAT, and referral KPIs'),
                ('Laboratory workspace', '/laboratory/', 'bi-droplet', 'Workflow stages, results, and QC'),
                ('TAT & timelines', '/laboratory/tat/', 'bi-hourglass-split', 'Stage times, overdue, and escalations'),
                ('Integration review', '/integrations/review/', 'bi-hdd-network', 'Quarantined analyzer messages'),
                ('Inventory', '/inventory/', 'bi-box-seam', 'Reagents, stock, and expiry alerts'),
            ],
        },
    ]
