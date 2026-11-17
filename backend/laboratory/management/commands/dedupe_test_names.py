"""Remove duplicate laboratory test names (case-insensitive)."""
from django.core.management.base import BaseCommand

from laboratory.test_names import dedupe_duplicate_test_names


class Command(BaseCommand):
    help = 'Collapse duplicate Test.name rows onto one keeper. Safe to re-run.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report duplicates without deleting.',
        )

    def handle(self, *args, **options):
        report = dedupe_duplicate_test_names(dry_run=options['dry_run'])
        if not report:
            self.stdout.write(self.style.SUCCESS('No duplicate test names found.'))
            return
        for row in report:
            removed = ', '.join(f"{item['code']}#{item['pk']}" for item in row['removed']) or '—'
            verb = 'would keep' if options['dry_run'] else 'kept'
            self.stdout.write(
                f"{row['name']}: {verb} {row['keeper_code']}#{row['keeper_pk']}; removed {removed}"
            )
        label = 'Would resolve' if options['dry_run'] else 'Resolved'
        self.stdout.write(self.style.SUCCESS(f'{label} {len(report)} duplicate name group(s).'))
