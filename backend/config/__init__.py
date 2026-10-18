try:
    from .celery import app as celery_app
except ImportError:  # Celery optional until installed
    celery_app = None

__all__ = ('celery_app',)
