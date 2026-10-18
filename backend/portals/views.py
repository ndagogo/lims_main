"""Patient, physician, and corporate portal screens."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from billing.models import Invoice, Payment
from laboratory.models import LabResult
from patients.models import OrganisationClient, Patient, Visit
from patients.registration import apply_notification_prefs, normalize_phone_digits, patient_is_minor
from portals.models import PhysicianInvite, PortalAccessToken, PortalRequest
from radiology.models import RadiologyReport
from referrals.models import CommissionLedgerEntry, ReferralDoctor


def _token_from_request(request):
    raw = request.session.get('portal_token') or request.GET.get('token') or ''
    if not raw:
        return None
    token = PortalAccessToken.objects.filter(token=raw, is_active=True).select_related(
        'patient', 'doctor', 'organisation',
    ).first()
    if not token or not token.is_valid():
        return None
    request.session['portal_token'] = token.token
    return token


def _patient_from_request(request):
    token = _token_from_request(request)
    if not token or token.portal != 'PATIENT' or not token.patient_id:
        return None
    return token.patient


def _establish_patient_session(request, patient):
    token = PortalAccessToken.issue('PATIENT', patient=patient, hours=2)
    request.session['portal_token'] = token.token
    return token


@require_http_methods(['GET', 'POST'])
def patient_login(request):
    """UHID plus date of birth. A phone number never opens every matching record."""
    error = None
    if request.method == 'POST':
        uhid = (request.POST.get('uhid') or '').strip()
        phone_only = (request.POST.get('phone_number') or '').strip()
        if phone_only and not uhid:
            error = 'A phone number alone cannot open results. Enter the patient UHID and date of birth.'
        elif not uhid:
            error = 'Enter the patient UHID and date of birth.'
        else:
            patient = Patient.objects.filter(uhid__iexact=uhid).first()
            dob = parse_date(request.POST.get('date_of_birth') or '')
            if patient is None or not patient.date_of_birth or dob != patient.date_of_birth:
                error = 'UHID and date of birth do not match.'
            elif patient_is_minor(patient.date_of_birth):
                supplied = normalize_phone_digits(request.POST.get('guardian_phone') or '')
                registered = normalize_phone_digits(patient.guardian_phone or '')
                if not supplied or supplied != registered:
                    error = 'This record is for a minor. Enter the registered guardian phone. A shared phone does not open other patients.'
                else:
                    _establish_patient_session(request, patient)
                    return redirect('portal-patient-home')
            else:
                _establish_patient_session(request, patient)
                return redirect('portal-patient-home')
    return render(request, 'portals/patient_login.html', {'error': error})


@require_http_methods(['GET'])
def patient_token_access(request, access_token):
    """Short-lived link from a notification. It opens one patient, not a phone match."""
    token = PortalAccessToken.objects.filter(token=access_token, portal='PATIENT', is_active=True).select_related('patient').first()
    if not token or not token.is_valid() or not token.patient_id:
        messages.error(request, 'This secure link has expired. Sign in with your UHID and date of birth.')
        return redirect('portal-patient-login')
    request.session['portal_token'] = token.token
    return redirect('portal-patient-home')


def patient_home(request):
    patient = _patient_from_request(request)
    if patient is None:
        return redirect('portal-patient-login')
    visits = Visit.objects.filter(patient=patient).select_related('centre').order_by('-created_at')[:20]
    invoices = Invoice.objects.filter(visit__patient=patient).select_related('visit').order_by('-created_at')[:20]
    receipts = Payment.objects.filter(invoice__visit__patient=patient, status='CONFIRMED').select_related('invoice').order_by('-created_at')[:20]
    results = LabResult.objects.filter(
        sample__order__visit__patient=patient, status__in=('RELEASED', 'AMENDED'),
    ).select_related('sample__order__test').order_by('-released_at')[:20]
    imaging = RadiologyReport.objects.filter(
        exam__order__visit__patient=patient, status__in=('RELEASED', 'AMENDED'),
    ).select_related('exam__order__procedure').order_by('-updated_at')[:20]
    requests = patient.portal_requests.all()[:10]
    return render(request, 'portals/patient_home.html', {
        'patient': patient, 'visits': visits, 'invoices': invoices, 'receipts': receipts,
        'results': results, 'imaging': imaging, 'portal_requests': requests,
        'is_minor': patient_is_minor(patient.date_of_birth),
    })


@require_http_methods(['POST'])
def patient_preferences(request):
    patient = _patient_from_request(request)
    if patient is None:
        return redirect('portal-patient-login')
    try:
        apply_notification_prefs(patient, request.POST)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, 'Notification preferences saved.')
    return redirect('portal-patient-home')


@require_http_methods(['POST'])
def patient_request(request):
    patient = _patient_from_request(request)
    if patient is None:
        return redirect('portal-patient-login')
    kind = (request.POST.get('kind') or 'SUPPORT').strip().upper()
    if kind not in dict(PortalRequest.KINDS):
        kind = 'SUPPORT'
    message = (request.POST.get('message') or '').strip()
    if not message:
        messages.error(request, 'Describe the support or correction request.')
        return redirect('portal-patient-home')
    visit = Visit.objects.filter(pk=request.POST.get('visit') or None, patient=patient).first()
    PortalRequest.objects.create(patient=patient, visit=visit, kind=kind, message=message[:4000])
    messages.success(request, 'Your request was sent to the centre. It does not change the clinical record.')
    return redirect('portal-patient-home')


def _released_lab_result(patient, result_id):
    result = LabResult.objects.filter(
        pk=result_id, sample__order__visit__patient=patient, status__in=('RELEASED', 'AMENDED'),
    ).select_related('sample__order__test', 'sample__order__visit').first()
    if result is None:
        raise Http404
    return result


def patient_result(request, result_id):
    patient = _patient_from_request(request)
    if patient is None:
        return redirect('portal-patient-login')
    result = _released_lab_result(patient, result_id)
    return render(request, 'portals/patient_result.html', {'patient': patient, 'result': result})


def patient_result_pdf(request, result_id):
    patient = _patient_from_request(request)
    if patient is None:
        return redirect('portal-patient-login')
    result = _released_lab_result(patient, result_id)
    from laboratory.reports import build_result_pdf
    payload = build_result_pdf(result)
    response = HttpResponse(payload, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{result.sample.sample_id}.pdf"'
    return response


def _released_imaging(patient, report_id):
    report = RadiologyReport.objects.filter(
        pk=report_id, exam__order__visit__patient=patient, status__in=('RELEASED', 'AMENDED'),
    ).select_related('exam__order__procedure').first()
    if report is None:
        raise Http404
    return report


def patient_imaging(request, report_id):
    patient = _patient_from_request(request)
    if patient is None:
        return redirect('portal-patient-login')
    report = _released_imaging(patient, report_id)
    return render(request, 'portals/patient_imaging.html', {'patient': patient, 'report': report})


def patient_imaging_pdf(request, report_id):
    patient = _patient_from_request(request)
    if patient is None:
        return redirect('portal-patient-login')
    report = _released_imaging(patient, report_id)
    from radiology.reports import build_radiology_report_pdf
    payload = build_radiology_report_pdf(report)
    response = HttpResponse(payload, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="imaging-{report.pk}.pdf"'
    return response


@require_http_methods(['GET', 'POST'])
def physician_invite_accept(request, invite_token):
    invite = get_object_or_404(PhysicianInvite, invite_token=invite_token)
    if invite.accepted_at or invite.expires_at < timezone.now():
        messages.error(request, 'This invitation is no longer valid.')
        return redirect('portal-physician-login')
    if request.method == 'POST':
        invite.accepted_at = timezone.now()
        invite.save(update_fields=['accepted_at', 'updated_at'])
        token = PortalAccessToken.issue('PHYSICIAN', doctor=invite.doctor, hours=8)
        request.session['portal_token'] = token.token
        return redirect('portal-physician-home')
    return render(request, 'portals/physician_invite.html', {'invite': invite})


@require_http_methods(['GET', 'POST'])
def physician_login(request):
    error = None
    if request.method == 'POST':
        code = (request.POST.get('doctor_code') or '').strip()
        doctor = ReferralDoctor.objects.filter(doctor_code__iexact=code, is_active=True).first()
        if not doctor:
            error = 'Doctor not found.'
        else:
            token = PortalAccessToken.issue('PHYSICIAN', doctor=doctor, hours=8)
            request.session['portal_token'] = token.token
            return redirect('portal-physician-home')
    return render(request, 'portals/physician_login.html', {'error': error})


def physician_home(request):
    token = _token_from_request(request)
    if not token or token.portal != 'PHYSICIAN' or not token.doctor_id:
        return redirect('portal-physician-login')
    doctor = token.doctor
    balance = doctor.ledger_entries.aggregate(total=Sum('amount'))['total'] or 0
    ledger = CommissionLedgerEntry.objects.filter(doctor=doctor).order_by('-created_at')[:30]
    return render(request, 'portals/physician_home.html', {
        'doctor': doctor, 'balance': balance, 'ledger': ledger,
    })


@require_http_methods(['GET', 'POST'])
def corporate_login(request):
    error = None
    if request.method == 'POST':
        name = (request.POST.get('org_code') or '').strip()
        org = OrganisationClient.objects.filter(name__iexact=name, is_active=True).first()
        if org is None:
            org = OrganisationClient.objects.filter(name__icontains=name, is_active=True).first()
        if not org:
            error = 'Organisation not found.'
        else:
            token = PortalAccessToken.issue('CORPORATE', organisation=org, hours=8)
            request.session['portal_token'] = token.token
            return redirect('portal-corporate-home')
    return render(request, 'portals/corporate_login.html', {'error': error})


def corporate_home(request):
    token = _token_from_request(request)
    if not token or token.portal != 'CORPORATE' or not token.organisation_id:
        return redirect('portal-corporate-login')
    org = token.organisation
    visits = Visit.objects.filter(client=org).select_related('patient', 'centre').order_by('-created_at')[:100]
    patients = Patient.objects.filter(id__in=visits.values_list('patient_id', flat=True)).distinct()[:100]
    invoices = Invoice.objects.filter(visit__client=org).select_related('visit').order_by('-created_at')[:50]
    return render(request, 'portals/corporate_home.html', {
        'organisation': org, 'patients': patients, 'invoices': invoices, 'visits': visits,
    })

def portal_logout(request):
    request.session.pop('portal_token', None)
    return redirect('portal-patient-login')
