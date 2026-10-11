from django.core.management.base import BaseCommand

from communications.services import dispatch_pending


class Command(BaseCommand):
    help = 'Send queued notifications that have a configured provider. Result text is queued only after release.'

    def handle(self, *args, **options):
        notes = dispatch_pending()
        sent = sum(1 for note in notes if note.status == 'SENT')
        self.stdout.write(self.style.SUCCESS(f'Checked {len(notes)} notification(s); {sent} accepted by a provider.'))
