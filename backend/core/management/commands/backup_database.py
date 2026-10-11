import json
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Write a database backup. PostgreSQL uses pg_dump when it is installed; every database also gets a JSON dump.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True, help='Directory or file prefix for the backup.')

    def handle(self, *args, **options):
        target = Path(options['output'])
        target.mkdir(parents=True, exist_ok=True)
        json_path = target / 'hfdn-dump.json'
        with json_path.open('w', encoding='utf-8') as handle:
            call_command('dumpdata', '--natural-foreign', '--natural-primary', '--indent', '2', stdout=handle, exclude=['contenttypes', 'auth.permission', 'sessions', 'admin.logentry'])
        self.stdout.write(self.style.SUCCESS(f'JSON backup written to {json_path}'))
        engine = settings.DATABASES['default']['ENGINE']
        if 'postgresql' not in engine:
            return
        url = os.getenv('DATABASE_URL', '').strip()
        if not url or shutil.which('pg_dump') is None:
            self.stdout.write('pg_dump is not available. The JSON dump is the backup from this run.')
            return
        sql_path = target / 'hfdn-dump.sql'
        completed = subprocess.run(['pg_dump', '--no-owner', '--file', str(sql_path), url], check=False)
        if completed.returncode != 0:
            raise CommandError('pg_dump failed. The JSON dump was still written.')
        self.stdout.write(self.style.SUCCESS(f'SQL backup written to {sql_path}'))
