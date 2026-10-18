"""Async notification and export tasks (Celery when available; sync fallback)."""


def dispatch_notifications(limit=50):
    from communications.services import dispatch_pending
    return len(dispatch_pending(limit=limit))


try:
    from celery import shared_task

    @shared_task(name='communications.dispatch_pending_notifications')
    def dispatch_pending_notifications(limit=50):
        return dispatch_notifications(limit)

    @shared_task(name='communications.run_large_export')
    def run_large_export(export_type, user_id, params=None):
        """Delegate to reporting.tasks so background exports actually write files."""
        from reporting.tasks import _write_export_file
        from core.audit import record_audit
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=user_id).first()
        record_audit(
            actor=user,
            action='EXPORT_QUEUED',
            object_type='Export',
            object_id=str(export_type)[:100],
            detail={'export_type': export_type, 'params': params or {}},
        )
        return _write_export_file(export_type, user_id, params)

except ImportError:
    def dispatch_pending_notifications(limit=50):
        return dispatch_notifications(limit)

    def run_large_export(export_type, user_id, params=None):
        from reporting.tasks import _write_export_file
        return _write_export_file(export_type, user_id, params)
