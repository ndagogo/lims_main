"""Seed registration channels and a default Self tariff for every active centre."""
from django.core.management.base import BaseCommand

from patients.channel_seed import seed_all_centres


class Command(BaseCommand):
    help = (
        'Ensure Client types, registration channels, and a default Self tariff '
        'exist for each active centre. Safe to re-run.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-self-tariff',
            action='store_true',
            help='Create channels only; do not create or map a Self tariff.',
        )

    def handle(self, *args, **options):
        results = seed_all_centres(seed_self_tariff=not options['no_self_tariff'])
        if not results:
            self.stdout.write(self.style.WARNING('No active centres found.'))
            return
        for row in results:
            self.stdout.write(
                f"{row['centre']}: channels+{row['channels_created']}, "
                f"Self tariff={row['self_tariff'] or '—'}, "
                f"mapped={'yes' if row['self_mapped'] else 'kept'}"
            )
        self.stdout.write(self.style.SUCCESS(f'Seeded {len(results)} centre(s).'))
