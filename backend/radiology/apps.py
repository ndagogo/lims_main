import os
import sys

from django.apps import AppConfig


class RadiologyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'radiology'

    def ready(self):
        from django.conf import settings
        if not getattr(settings, 'RIS_MWL_ENABLED', False):
            return
        if any(arg in sys.argv for arg in ('test', 'migrate', 'makemigrations', 'collectstatic')):
            return
        if 'runserver' in sys.argv and os.environ.get('RUN_MAIN') != 'true':
            return
        from radiology.worklist import start_mwl_server
        start_mwl_server()
