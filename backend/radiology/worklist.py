"""Modality worklist: paid radiology orders become DICOM MWL rows for a workstation.

The modality queries this LIMS (C-FIND). A C-ECHO from Platform controls checks that
the workstation at the saved AE title, IP address, and port accepts a connection.
"""
import logging
import threading

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_server = None
_server_lock = threading.Lock()

DICOM_MODALITY = {
    'XRAY': 'CR',
    'CT': 'CT',
    'MRI': 'MR',
    'US': 'US',
    'MAMMO': 'MG',
    'ECG': 'ECG',
    'OTHER': 'OT',
}
SEX = {'Male': 'M', 'Female': 'F', 'Other': 'O'}
OPEN_ORDER = ('ORDERED', 'SCHEDULED', 'IN_PROGRESS')
OPEN_MWL = ('SCHEDULED', 'IN_PROGRESS')


def clean_ae(value):
    if isinstance(value, bytes):
        value = value.decode('ascii', 'ignore')
    return (value or '').strip().upper()[:16]


def worklist_accession(order):
    visit_id = order.visit.visit_id or ''
    code = ''
    if order.procedure_id and order.procedure and order.procedure.code:
        code = order.procedure.code
    raw = f'{visit_id}{code}' if code else visit_id
    return raw.replace(' ', '')[:16]


def study_instance_uid(exam):
    return f'2.25.{exam.pk.int}'


def publish_exam(exam):
    """Place a paid study on the modality worklist. Unpaid studies stay off it."""
    from billing.visibility import imaging_payment_complete

    order = exam.order
    if order.status not in OPEN_ORDER or exam.status == 'CANCELLED':
        return False
    if exam.mwl_status in ('COMPLETED', 'CANCELLED'):
        return False
    if not imaging_payment_complete(order_id=order.pk):
        return False
    modality = order.modality
    if not modality.is_active or not modality.worklist_enabled or not clean_ae(modality.ae_title):
        return False
    if exam.mwl_status not in OPEN_MWL:
        exam.mwl_status = 'SCHEDULED'
        exam.save(update_fields=['mwl_status', 'updated_at'])
    return True


def publish_paid_invoice(invoice):
    from radiology.models import RadiologyExam

    order_ids = list(
        invoice.items.exclude(imaging_order_id=None).values_list('imaging_order_id', flat=True)
    )
    if not order_ids:
        return 0
    count = 0
    exams = RadiologyExam.objects.filter(order_id__in=order_ids).select_related(
        'order__modality', 'order__visit', 'order__procedure',
    )
    for exam in exams:
        if publish_exam(exam):
            count += 1
    return count


def republish_modality(modality):
    from radiology.models import RadiologyExam

    exams = RadiologyExam.objects.filter(order__modality=modality).select_related(
        'order__modality', 'order__visit', 'order__procedure',
    ).exclude(order__status__in=['COMPLETED', 'CANCELLED']).exclude(mwl_status__in=['COMPLETED', 'CANCELLED'])
    count = 0
    for exam in exams:
        if publish_exam(exam):
            count += 1
    return count


def withdraw_exam(exam):
    if exam.mwl_status == 'COMPLETED':
        return
    exam.mwl_status = 'CANCELLED'
    exam.save(update_fields=['mwl_status', 'updated_at'])


def scheduled_exams(station_ae, patient_id='', accession=''):
    from billing.visibility import settled
    from radiology.models import RadiologyExam

    station = clean_ae(station_ae)
    if not station:
        return []
    qs = RadiologyExam.objects.filter(
        mwl_status__in=OPEN_MWL,
        order__status__in=OPEN_ORDER,
        order__modality__is_active=True,
        order__modality__worklist_enabled=True,
        order__modality__ae_title__iexact=station,
    ).select_related(
        'order__visit__patient', 'order__modality', 'order__procedure',
    )
    qs = settled(qs, 'imaging_order_id', outer='order_id')
    if patient_id:
        qs = qs.filter(order__visit__patient__uhid__iexact=patient_id.strip())
    rows = list(qs.order_by('order__scheduled_at', 'created_at'))
    if accession:
        wanted = accession.strip()
        rows = [exam for exam in rows if worklist_accession(exam.order) == wanted]
    return rows


def worklist_record(exam):
    """Plain patient row the modality workstation receives."""
    order = exam.order
    patient = order.visit.patient
    modality = order.modality
    when = order.scheduled_at or order.created_at or timezone.now()
    procedure = order.procedure.name if order.procedure_id else modality.get_modality_type_display()
    return {
        'patient_name': f'{patient.last_name}^{patient.first_name}^{patient.other_names}'.strip('^'),
        'patient_id': patient.uhid,
        'birth_date': patient.date_of_birth.strftime('%Y%m%d') if patient.date_of_birth else '',
        'sex': SEX.get(patient.sex, ''),
        'accession': worklist_accession(order),
        'study_uid': study_instance_uid(exam),
        'procedure': procedure,
        'modality': DICOM_MODALITY.get(modality.modality_type, 'OT'),
        'station_ae': clean_ae(modality.ae_title),
        'station_name': modality.name,
        'step_id': exam.pk.hex[:16],
        'date': timezone.localtime(when).strftime('%Y%m%d'),
        'time': timezone.localtime(when).strftime('%H%M%S'),
        'visit_id': order.visit.visit_id,
    }


def mwl_dataset(exam):
    from pydicom.dataset import Dataset

    row = worklist_record(exam)
    ds = Dataset()
    ds.SpecificCharacterSet = 'ISO_IR 100'
    ds.PatientName = row['patient_name']
    ds.PatientID = row['patient_id']
    ds.PatientBirthDate = row['birth_date']
    ds.PatientSex = row['sex']
    ds.AccessionNumber = row['accession']
    ds.StudyInstanceUID = row['study_uid']
    ds.RequestedProcedureDescription = row['procedure'][:64]
    ds.RequestedProcedureID = row['step_id']
    step = Dataset()
    step.Modality = row['modality']
    step.ScheduledStationAETitle = row['station_ae']
    step.ScheduledStationName = row['station_name'][:16]
    step.ScheduledProcedureStepDescription = row['procedure'][:64]
    step.ScheduledProcedureStepID = row['step_id']
    step.ScheduledProcedureStepStartDate = row['date']
    step.ScheduledProcedureStepStartTime = row['time']
    ds.ScheduledProcedureStepSequence = [step]
    return ds


def _station_from_event(event):
    identifier = getattr(event, 'identifier', None)
    sequence = list(getattr(identifier, 'ScheduledProcedureStepSequence', []) or [])
    if sequence:
        station = clean_ae(getattr(sequence[0], 'ScheduledStationAETitle', ''))
        if station:
            return station
    requestor = getattr(getattr(event, 'assoc', None), 'requestor', None)
    return clean_ae(getattr(requestor, 'ae_title', ''))


def handle_find(event):
    """DICOM Modality Worklist C-FIND. Yields pending matches, then success."""
    from django.db import close_old_connections

    close_old_connections()
    try:
        station = _station_from_event(event)
        identifier = getattr(event, 'identifier', None)
        patient_id = str(getattr(identifier, 'PatientID', '') or '').replace('*', '').strip()
        accession = str(getattr(identifier, 'AccessionNumber', '') or '').replace('*', '').strip()
        for exam in scheduled_exams(station, patient_id=patient_id, accession=accession):
            yield 0xFF00, mwl_dataset(exam)
        yield 0x0000, None
    except Exception:
        logger.exception('Modality worklist query failed')
        yield 0xC000, None
    finally:
        close_old_connections()


def listener_status():
    ae = clean_ae(getattr(settings, 'RIS_MWL_AE_TITLE', 'HFDN_MWL')) or 'HFDN_MWL'
    port = int(getattr(settings, 'RIS_MWL_PORT', 11112))
    return {
        'ae_title': ae,
        'port': port,
        'calling_ae': clean_ae(getattr(settings, 'RIS_CALLING_AE', 'HFDN_SCU')) or 'HFDN_SCU',
        'running': _server is not None,
    }


def start_mwl_server():
    """Listen for modality worklist queries. Safe to call more than once."""
    global _server
    with _server_lock:
        if _server is not None:
            status = listener_status()
            return True, f'Worklist listener is already running as {status["ae_title"]} on port {status["port"]}.'
        try:
            from pynetdicom import AE, evt
            from pynetdicom.sop_class import ModalityWorklistInformationFind
        except ImportError:
            return False, 'pynetdicom is not installed, so the worklist listener cannot start.'
        status = listener_status()
        ae = AE(ae_title=status['ae_title'])
        ae.add_supported_context(ModalityWorklistInformationFind)
        try:
            _server = ae.start_server(
                ('0.0.0.0', status['port']),
                block=False,
                evt_handlers=[(evt.EVT_C_FIND, handle_find)],
            )
        except Exception as exc:
            _server = None
            logger.exception('Could not start the modality worklist listener')
            return False, f'Could not listen on port {status["port"]}: {exc}'
        return True, f'Worklist listener started as {status["ae_title"]} on port {status["port"]}.'
