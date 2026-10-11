from django.core.management.base import BaseCommand

from communications.messaging import (
    DEFAULT_BODIES, DEFAULT_SUBJECTS, EVENT_REGISTRATION_SUCCESS, EVENT_RESULT_READY, template_key,
)
from communications.models import NotificationTemplate

EVENTS = (
    (EVENT_REGISTRATION_SUCCESS, 'Registration success'),
    (EVENT_RESULT_READY, 'Result ready'),
)
CHANNELS = ('SMS', 'EMAIL', 'WHATSAPP')


class Command(BaseCommand):
    help = 'Seed patient notification templates for SMS, email, and WhatsApp.'

    def handle(self, *args, **options):
        created = 0
        for event_key, label in EVENTS:
            for channel in CHANNELS:
                key = template_key(event_key, channel)
                _, was_created = NotificationTemplate.objects.update_or_create(
                    key=key,
                    defaults={
                        'name': f'{label} ({channel})',
                        'channel': channel,
                        'subject_template': DEFAULT_SUBJECTS[event_key] if channel == 'EMAIL' else '',
                        'body_template': DEFAULT_BODIES[event_key],
                        'is_active': True,
                    },
                )
                if was_created:
                    created += 1
                self.stdout.write(self.style.SUCCESS(key))
        self.stdout.write(self.style.SUCCESS(f'Notification templates ready ({created} new).'))
