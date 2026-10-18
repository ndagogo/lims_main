from django.core.management.base import BaseCommand

from communications.messaging import DEFAULT_BODIES, DEFAULT_SUBJECTS, template_key, validate_template_text
from communications.models import NotificationTemplate

CHANNELS = ('SMS', 'EMAIL', 'WHATSAPP')
LABELS = {
    'registration-success': 'Registration success',
    'referral-received': 'Referral received',
    'payment-receipt': 'Payment receipt',
    'discount-requested': 'Discount requested',
    'discount-decision': 'Discount decision',
    'sample-recollection': 'Sample recollection',
    'result-authenticated': 'Result authenticated',
    'result-ready': 'Result ready',
    'referral-withdrawal': 'Referral withdrawal',
    'referral-payout': 'Referral payout settled',
    'integration-failure': 'Integration failure',
}


class Command(BaseCommand):
    help = 'Seed notification templates for SMS, email, and WhatsApp.'

    def handle(self, *args, **options):
        created = 0
        for event_key, body in DEFAULT_BODIES.items():
            validate_template_text(body)
            validate_template_text(DEFAULT_SUBJECTS.get(event_key, ''))
            for channel in CHANNELS:
                key = template_key(event_key, channel)
                _, was_created = NotificationTemplate.objects.update_or_create(
                    key=key,
                    defaults={
                        'name': f'{LABELS.get(event_key, event_key)} ({channel})',
                        'channel': channel,
                        'subject_template': DEFAULT_SUBJECTS.get(event_key, '') if channel == 'EMAIL' else '',
                        'body_template': body,
                        'is_active': True,
                    },
                )
                if was_created:
                    created += 1
                self.stdout.write(self.style.SUCCESS(key))
        self.stdout.write(self.style.SUCCESS(f'Notification templates ready ({created} new).'))
