"""Seed Normal / Microbiology / Cytology / Histology workflow templates (publishable versions)."""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTransition
from workflows.services import publish_workflow

# Step tuple: code, name, sample_status, result_status, sort, department, roles, required_fields,
# required_docs, tat_minutes, escalation_minutes, skip_allowed, instructions

PHLEB = ['Phlebotomist', 'Laboratory Scientist', 'Lab Manager', 'Platform Administrator']
LAB = ['Laboratory Scientist', 'Lab Manager', 'Platform Administrator']
MGR = ['Lab Manager', 'Platform Administrator']
DESK = ['Front Desk', 'Platform Administrator']
ACCT = ['Accounts', 'Cashier', 'Platform Administrator']

NORMAL_STEPS = [
    ('ORDERED', 'Order placed', 'PENDING_COLLECTION', '', 10, 'Registration', DESK, [], [], 15, 60, False, 'Visit registered and test ordered.'),
    ('PAYMENT_CLEARED', 'Payment confirmed', 'PENDING_COLLECTION', '', 20, 'Accounts', ACCT, [], [], 30, 120, False, 'Cash paid or approved credit.'),
    ('COLLECTED', 'Sample collected', 'COLLECTED', '', 30, 'Phlebotomy', PHLEB, ['collector'], [], 20, 60, False, 'Collect specimen and generate barcode.'),
    ('BATCHED', 'Batched for dispatch', 'BATCHED', '', 40, 'Phlebotomy', PHLEB, [], [], 30, 90, False, 'Add sample to a batch and dispatch.'),
    ('RECEIVED', 'Department receiving', 'RECEIVED', '', 50, 'Pathology', LAB, [], [], 30, 90, False, 'Department acknowledges receipt.'),
    ('PROCESSING', 'Sample processing', 'PROCESSING', 'DRAFT', 60, 'Pathology', LAB, [], [], 120, 240, False, 'Process sample and log activities.'),
    ('RESULT_ENTRY', 'Result entry', 'RESULT_ENTERED', 'DRAFT', 70, 'Pathology', LAB, ['narrative_or_values'], [], 60, 180, False, 'Enter results against the template.'),
    ('REVIEW', 'First review / validation', 'UNDER_REVIEW', 'REVIEWED', 80, 'Pathology', LAB, [], [], 60, 180, False, 'Independent first review.'),
    ('AUTHENTICATED', 'Result authentication', 'AUTHENTICATED', 'AUTHENTICATED', 90, 'Pathology', MGR, [], [], 30, 120, False, 'Authorised personnel authenticate.'),
    ('RELEASED', 'Controlled release', 'RELEASED', 'RELEASED', 100, 'Pathology', MGR, [], [], 15, 60, False, 'Release after eligibility checks.'),
]

MICRO_STEPS = [
    ('ORDERED', 'Order placed', 'PENDING_COLLECTION', '', 10, 'Registration', DESK, [], [], 15, 60, False, ''),
    ('PAYMENT_CLEARED', 'Payment confirmed', 'PENDING_COLLECTION', '', 20, 'Accounts', ACCT, [], [], 30, 120, False, ''),
    ('COLLECTED', 'Sample collected', 'COLLECTED', '', 30, 'Phlebotomy', PHLEB, ['collector'], [], 20, 60, False, ''),
    ('BATCHED', 'Batched for dispatch', 'BATCHED', '', 40, 'Phlebotomy', PHLEB, [], [], 30, 90, False, ''),
    ('RECEIVED', 'Department receiving', 'RECEIVED', '', 50, 'Microbiology', LAB, [], [], 30, 90, False, ''),
    ('CULTURE', 'Culture processing', 'PROCESSING', 'DRAFT', 60, 'Microbiology', LAB, [], [], 240, 480, False, 'Inoculation and culture setup.'),
    ('INCUBATION', 'Incubation & observations', 'PROCESSING', 'DRAFT', 70, 'Microbiology', LAB, ['observation'], [], 1440, 2880, False, 'Configurable incubation window and repeat observations.'),
    ('RESULT_ENTRY', 'Result entry', 'RESULT_ENTERED', 'DRAFT', 80, 'Microbiology', LAB, ['narrative_or_values'], [], 60, 180, False, ''),
    ('REVIEW', 'First review / validation', 'UNDER_REVIEW', 'REVIEWED', 90, 'Microbiology', LAB, [], [], 60, 180, False, ''),
    ('AUTHENTICATED', 'Result authentication', 'AUTHENTICATED', 'AUTHENTICATED', 100, 'Microbiology', MGR, [], [], 30, 120, False, ''),
    ('RELEASED', 'Controlled release', 'RELEASED', 'RELEASED', 110, 'Microbiology', MGR, [], [], 15, 60, False, ''),
]

CYTO_STEPS = [
    ('ORDERED', 'Order placed', 'PENDING_COLLECTION', '', 10, 'Registration', DESK, [], [], 15, 60, False, ''),
    ('PAYMENT_CLEARED', 'Payment confirmed', 'PENDING_COLLECTION', '', 20, 'Accounts', ACCT, [], [], 30, 120, False, ''),
    ('COLLECTED', 'Sample collected', 'COLLECTED', '', 30, 'Phlebotomy', PHLEB, ['collector'], [], 20, 60, False, ''),
    ('BATCHED', 'Batched for dispatch', 'BATCHED', '', 40, 'Phlebotomy', PHLEB, [], [], 30, 90, False, ''),
    ('RECEIVED', 'Department receiving', 'RECEIVED', '', 50, 'Cytology', LAB, [], [], 30, 90, True, 'May be skipped only when a skip transition is configured.'),
    ('CYTO_PENDING', 'Cytology pending dashboard', 'PROCESSING', 'DRAFT', 60, 'Cytology', LAB, [], [], 120, 360, False, ''),
    ('CYTO_PROCESSING', 'Cytology processing', 'PROCESSING', 'DRAFT', 70, 'Cytology', LAB, [], [], 180, 360, False, ''),
    ('RESULT_ENTRY', 'Result entry', 'RESULT_ENTERED', 'DRAFT', 80, 'Cytology', LAB, ['narrative_or_values'], [], 60, 180, False, ''),
    ('REVIEW', 'First review / validation', 'UNDER_REVIEW', 'REVIEWED', 90, 'Cytology', LAB, [], [], 60, 180, False, ''),
    ('AUTHENTICATED', 'Result authentication', 'AUTHENTICATED', 'AUTHENTICATED', 100, 'Cytology', MGR, [], [], 30, 120, False, ''),
    ('RELEASED', 'Controlled release', 'RELEASED', 'RELEASED', 110, 'Cytology', MGR, [], [], 15, 60, False, ''),
]

HISTO_STEPS = [
    ('ORDERED', 'Order placed', 'PENDING_COLLECTION', '', 10, 'Registration', DESK, [], [], 15, 60, False, ''),
    ('PAYMENT_CLEARED', 'Payment confirmed', 'PENDING_COLLECTION', '', 20, 'Accounts', ACCT, [], [], 30, 120, False, ''),
    ('COLLECTED', 'Specimen collection', 'COLLECTED', '', 30, 'Phlebotomy', PHLEB, ['collector'], [], 20, 60, False, ''),
    ('ACCESSIONING', 'Accessioning', 'RECEIVED', '', 40, 'Histology', LAB, ['accession'], [], 60, 180, False, ''),
    ('GROSSING', 'Grossing & tissue processing', 'PROCESSING', 'DRAFT', 50, 'Histology', LAB, [], [], 240, 480, False, ''),
    ('EMBEDDING', 'Embedding / sectioning', 'PROCESSING', 'DRAFT', 60, 'Histology', LAB, [], [], 240, 480, False, ''),
    ('MICROSCOPY', 'Microscopy', 'PROCESSING', 'DRAFT', 70, 'Histology', LAB, [], [], 180, 360, False, ''),
    ('RESULT_ENTRY', 'Report entry', 'RESULT_ENTERED', 'DRAFT', 80, 'Histology', LAB, ['narrative_or_values'], [], 120, 360, False, ''),
    ('REVIEW', 'First review / validation', 'UNDER_REVIEW', 'REVIEWED', 90, 'Histology', LAB, [], [], 60, 180, False, ''),
    ('AUTHENTICATED', 'Result authentication', 'AUTHENTICATED', 'AUTHENTICATED', 100, 'Histology', MGR, [], [], 30, 120, False, ''),
    ('RELEASED', 'Controlled release', 'RELEASED', 'RELEASED', 110, 'Histology', MGR, [], [], 15, 60, False, ''),
]

TRANSITIONS = {
    'NORMAL': [
        ('ORDERED', 'PAYMENT_CLEARED', 'confirm_payment', 'Confirm payment', {'require_paid': True}),
        # Shortcut: collection after payment without a separate confirm click.
        ('ORDERED', 'COLLECTED', 'collect', 'Collect (payment cleared)', {'require_paid': True}),
        ('PAYMENT_CLEARED', 'COLLECTED', 'collect', 'Collect sample', {}),
        ('COLLECTED', 'BATCHED', 'batch', 'Add to batch', {}),
        ('BATCHED', 'RECEIVED', 'receive', 'Department receive', {}),
        ('RECEIVED', 'PROCESSING', 'start', 'Start processing', {}),
        ('PROCESSING', 'RESULT_ENTRY', 'enter_result', 'Enter results', {}),
        ('RESULT_ENTRY', 'REVIEW', 'submit', 'Submit for review', {}),
        ('REVIEW', 'AUTHENTICATED', 'authenticate', 'Authenticate', {}),
        ('AUTHENTICATED', 'RELEASED', 'release', 'Release', {}),
    ],
    'MICROBIOLOGY': [
        ('ORDERED', 'PAYMENT_CLEARED', 'confirm_payment', 'Confirm payment', {'require_paid': True}),
        ('ORDERED', 'COLLECTED', 'collect', 'Collect (payment cleared)', {'require_paid': True}),
        ('PAYMENT_CLEARED', 'COLLECTED', 'collect', 'Collect sample', {}),
        ('COLLECTED', 'BATCHED', 'batch', 'Add to batch', {}),
        ('BATCHED', 'RECEIVED', 'receive', 'Department receive', {}),
        ('RECEIVED', 'CULTURE', 'start', 'Start culture', {}),
        ('CULTURE', 'INCUBATION', 'incubate', 'Begin incubation', {}),
        ('INCUBATION', 'RESULT_ENTRY', 'enter_result', 'Enter results', {}),
        ('RESULT_ENTRY', 'REVIEW', 'submit', 'Submit for review', {}),
        ('REVIEW', 'AUTHENTICATED', 'authenticate', 'Authenticate', {}),
        ('AUTHENTICATED', 'RELEASED', 'release', 'Release', {}),
    ],
    'CYTOLOGY': [
        ('ORDERED', 'PAYMENT_CLEARED', 'confirm_payment', 'Confirm payment', {'require_paid': True}),
        ('ORDERED', 'COLLECTED', 'collect', 'Collect (payment cleared)', {'require_paid': True}),
        ('PAYMENT_CLEARED', 'COLLECTED', 'collect', 'Collect sample', {}),
        ('COLLECTED', 'BATCHED', 'batch', 'Add to batch', {}),
        ('BATCHED', 'RECEIVED', 'receive', 'Department receive', {}),
        # Explicit skip of department receiving when leadership configures this path.
        ('BATCHED', 'CYTO_PENDING', 'skip_receive', 'Skip receiving (configured)', {'flag_skip_receive': True}),
        ('RECEIVED', 'CYTO_PENDING', 'to_pending', 'Cytology pending', {}),
        ('CYTO_PENDING', 'CYTO_PROCESSING', 'start', 'Start cytology processing', {}),
        ('CYTO_PROCESSING', 'RESULT_ENTRY', 'enter_result', 'Enter results', {}),
        ('RESULT_ENTRY', 'REVIEW', 'submit', 'Submit for review', {}),
        ('REVIEW', 'AUTHENTICATED', 'authenticate', 'Authenticate', {}),
        ('AUTHENTICATED', 'RELEASED', 'release', 'Release', {}),
    ],
    'HISTOLOGY': [
        ('ORDERED', 'PAYMENT_CLEARED', 'confirm_payment', 'Confirm payment', {'require_paid': True}),
        ('ORDERED', 'COLLECTED', 'collect', 'Collect (payment cleared)', {'require_paid': True}),
        ('PAYMENT_CLEARED', 'COLLECTED', 'collect', 'Collect specimen', {}),
        ('COLLECTED', 'ACCESSIONING', 'accession', 'Accession', {}),
        ('ACCESSIONING', 'GROSSING', 'gross', 'Grossing', {}),
        ('GROSSING', 'EMBEDDING', 'embed', 'Embedding / sectioning', {}),
        ('EMBEDDING', 'MICROSCOPY', 'microscopy', 'Microscopy', {}),
        ('MICROSCOPY', 'RESULT_ENTRY', 'enter_result', 'Report entry', {}),
        ('RESULT_ENTRY', 'REVIEW', 'submit', 'Submit for review', {}),
        ('REVIEW', 'AUTHENTICATED', 'authenticate', 'Authenticate', {}),
        ('AUTHENTICATED', 'RELEASED', 'release', 'Release', {}),
    ],
}


def _create_definition(code, name, steps, version, *, publish=True):
    wf = WorkflowDefinition.objects.create(
        code=code, name=name, version=version, status='DRAFT', is_active=False,
        description=f'{name} template for HFDN LIS. Leadership must approve before activation.',
        expected_tat_minutes=sum((s[9] or 0) for s in steps),
        notes='Seeded starting template — confirm with laboratory leadership before relying on it clinically.',
    )
    by_code = {}
    for row in steps:
        code_s, name_s, sample_st, result_st, order, dept, roles, fields, docs, tat, esc, skip, instr = row
        by_code[code_s] = WorkflowStep.objects.create(
            workflow=wf, code=code_s, name=name_s, sort_order=order,
            sample_status=sample_st, result_status=result_st, required=True,
            department=dept, allowed_roles=list(roles), required_fields=list(fields),
            required_documents=list(docs), expected_tat_minutes=tat,
            escalation_after_minutes=esc, escalation_roles=list(MGR),
            skip_allowed=skip, instructions=instr,
        )
    for frm, to, action, label, condition in TRANSITIONS[code]:
        WorkflowTransition.objects.create(
            workflow=wf, from_step=by_code[frm], to_step=by_code[to],
            action=action, label=label, condition=condition, require_fields_complete=True,
        )
    if publish:
        publish_workflow(wf, notes='Seeded and published as starting template.')
    return wf


class Command(BaseCommand):
    help = 'Seed versioned laboratory workflow templates (Normal / Micro / Cyto / Histo).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--republish', action='store_true',
            help='Create a new version with the latest template and publish it (does not rewrite in-progress orders).',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        specs = [
            ('NORMAL', 'Normal laboratory', NORMAL_STEPS),
            ('MICROBIOLOGY', 'Microbiology', MICRO_STEPS),
            ('CYTOLOGY', 'Cytology', CYTO_STEPS),
            ('HISTOLOGY', 'Histology', HISTO_STEPS),
        ]

        for code, name, steps in specs:
            latest = WorkflowDefinition.objects.filter(code=code).order_by('-version').first()
            if latest and not options['republish']:
                # Ensure at least one published/active version exists for binding.
                if not WorkflowDefinition.objects.filter(code=code, status='PUBLISHED', is_active=True).exists():
                    if latest.status == 'DRAFT':
                        publish_workflow(latest)
                        self.stdout.write(self.style.SUCCESS(f'Published existing draft {code} v{latest.version}'))
                    else:
                        latest.is_active = True
                        latest.status = 'PUBLISHED'
                        latest.published_at = latest.published_at or timezone.now()
                        latest.save(update_fields=['is_active', 'status', 'published_at', 'updated_at'])
                        self.stdout.write(self.style.SUCCESS(f'Re-activated {code} v{latest.version}'))
                else:
                    self.stdout.write(f'{code} already present (use --republish for a new version)')
                continue
            version = (latest.version + 1) if latest else 1
            wf = _create_definition(code, name, steps, version, publish=True)
            self.stdout.write(self.style.SUCCESS(f'Seeded and published {code} v{wf.version}'))
