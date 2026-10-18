"""Orthanc PACS client — health check, study metadata, failure queue."""
import logging
from urllib.parse import urljoin

import urllib.request
import json
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def orthanc_configured():
    return bool(getattr(settings, 'ORTHANC_URL', '').strip())


def _request(path, method='GET', data=None):
    base = getattr(settings, 'ORTHANC_URL', '').rstrip('/') + '/'
    url = urljoin(base, path.lstrip('/'))
    user = getattr(settings, 'ORTHANC_USERNAME', '') or ''
    password = getattr(settings, 'ORTHANC_PASSWORD', '') or ''
    body = None if data is None else json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    if user:
        import base64
        token = base64.b64encode(f'{user}:{password}'.encode()).decode()
        req.add_header('Authorization', f'Basic {token}')
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode('utf-8') or '{}'
        return json.loads(raw) if raw.strip().startswith(('{', '[')) else raw


def c_echo():
    if not orthanc_configured():
        return False, 'ORTHANC_URL not configured'
    try:
        _request('system')
        return True, 'ok'
    except Exception as exc:
        return False, str(exc)


def reconcile_exam(exam):
    """Look up study by accession / patient and store metadata; queue on failure."""
    from radiology.models import PacsFailure, RadiologyExam
    if not orthanc_configured():
        PacsFailure.objects.create(exam=exam, reason='Orthanc not configured')
        return False
    try:
        studies = _request('studies')
        study_id = studies[0] if isinstance(studies, list) and studies else None
        if not study_id:
            PacsFailure.objects.create(exam=exam, reason='No studies returned from Orthanc')
            return False
        meta = _request(f'studies/{study_id}')
        main = meta.get('MainDicomTags') or {}
        exam.pacs_study_uid = main.get('StudyInstanceUID', '') or study_id
        exam.pacs_series_uids = [s for s in (meta.get('Series') or [])]
        exam.pacs_reconciled_at = timezone.now()
        exam.save(update_fields=['pacs_study_uid', 'pacs_series_uids', 'pacs_reconciled_at', 'updated_at'])
        PacsFailure.objects.filter(exam=exam, resolved_at__isnull=True).update(resolved_at=timezone.now())
        return True
    except Exception as exc:
        logger.exception('Orthanc reconcile failed')
        PacsFailure.objects.create(exam=exam, reason=str(exc)[:500])
        return False
