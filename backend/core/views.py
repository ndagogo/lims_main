import uuid

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from billing.registration_billing import process_registration
from billing.workspace import (
    build_accounts_context, build_accountant_context, export_accountant_report,
    handle_accounts_post, receipt_payment,
)
from communications.reports import build_operations_summary, build_work_queues
from core.access import allowed_workspaces, is_accountant, is_platform_admin, require_workspace
from core.administration import build_role_context, build_user_context, handle_role_post, handle_user_post, superuser_only
from core.auth_services import attempt_login
from core.catalog import (
    build_centres_context, build_clients_context, build_doctors_context, build_hub_context,
    build_imaging_context, build_service_groups_context, build_tariff_detail_context,
    build_tariffs_context, build_tests_context, handle_centres_post, handle_clients_post,
    handle_doctors_post, handle_imaging_post, handle_service_groups_post,
    handle_tariff_detail_post, handle_tariffs_post, handle_tests_post, platform_admin_only,
)
from core.models import AuditEvent, Centre, UserCentreAccess
from core.paging import PAGE_SIZES, paginate
from core.uploads import validate_upload
from laboratory.workspace import build_laboratory_context, build_phlebotomy_context, handle_laboratory_post, handle_phlebotomy_post
from patients.models import CHANNEL_CHOICES, HOW_HEARD_CHOICES, OrganisationClient, Patient, PatientDocument, RACE_CHOICES, SEX_CHOICES, STATE_CHOICES, TITLE_CHOICES, Visit
from laboratory.models import Tariff, TestPrice
from communications.messaging import EVENT_REGISTRATION_SUCCESS, notify_patient
from patients.registration import (
    PHONE_CODES, apply_notification_prefs, apply_visit_details, patient_values,
    validate_local_phone, validate_optional_email,
)
from patients.timeline import build_patient_timeline
from radiology.models import ImagingPrice
from referrals.models import ReferralDoctor
from radiology.workspace import build_radiology_context, handle_radiology_post


def _safe_next(request):
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return ''


def _flash(request, handler, redirect_name):
    ok, text = handler(request)
    if ok:
        messages.success(request, text)
    else:
        messages.error(request, text)
    return redirect(redirect_name)


def login_page(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    error = None
    if request.method == 'POST':
        user, error = attempt_login(request.POST.get('username'), request.POST.get('password'))
        if user is not None:
            login(request, user)
            AuditEvent.objects.create(actor=user, action='LOGIN_SUCCESS', object_type='AUTH', path=request.path, method='POST', ip_address=getattr(request, 'audit_ip', None), browser=getattr(request, 'audit_browser', ''))
            return redirect(_safe_next(request) or 'dashboard')
        AuditEvent.objects.create(actor=None, action='LOGIN_FAILED', object_type='AUTH', path=request.path, method='POST', ip_address=getattr(request, 'audit_ip', None), browser=getattr(request, 'audit_browser', ''), detail={'username': (request.POST.get('username') or '')[:150]})
    return render(request, 'core/login.html', {'error': error, 'next': request.GET.get('next', '')})


@require_POST
def logout_page(request):
    logout(request)
    return redirect('login')


@login_required
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        user.must_change_password = False
        user.last_password_change = timezone.now()
        user.save(update_fields=['must_change_password', 'last_password_change'])
        update_session_auth_hash(request, user)
        messages.success(request, 'Password updated.')
        return redirect('dashboard')
    return render(request, 'core/password_change.html', {'form': form})


@login_required
def dashboard(request):
    user = request.user
    accesses = UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_view=True).select_related('centre')
    centres = list(Centre.objects.filter(is_active=True) if user.is_superuser else [row.centre for row in accesses])
    summary = build_operations_summary(user)
    summary['sample_peak'] = max((row['count'] for row in summary['sample_pipeline']), default=1) or 1
    summary['imaging_peak'] = max((row['count'] for row in summary['imaging_pipeline']), default=1) or 1
    queues = build_work_queues(user)
    queues['recent_events'] = paginate(request, _events_for_role(queues['recent_events'], allowed_workspaces(user)), 'epage')
    queues['unpaid_invoices'] = paginate(request, queues['unpaid_invoices'], 'qpage')
    queues['active_samples'] = paginate(request, queues['active_samples'], 'sampage')
    queues['open_imaging'] = paginate(request, queues['open_imaging'], 'ipage')
    context = {
        'centres': centres,
        'centre_accesses': accesses,
        'summary': summary,
        'queues': queues,
        'is_admin': user.is_staff,
        'greeting_name': (user.display_name or user.first_name or user.username or 'there').split()[0],
    }
    if is_platform_admin(user):
        context['catalog_counts'] = build_hub_context(request)['counts']
    return render(request, 'core/dashboard.html', context)


def _events_for_role(events, allowed):
    if 'audit' in allowed:
        return events
    prefixes = []
    if 'registration' in allowed:
        prefixes.extend(('PATIENT_', 'VISIT_', 'REFERRAL_'))
    if 'accounts' in allowed:
        prefixes.extend(('INVOICE', 'PAYMENT', 'DISCOUNT'))
    if 'laboratory' in allowed or 'phlebotomy' in allowed:
        prefixes.append('LAB_')
    if 'radiology' in allowed:
        prefixes.extend(('RADIOLOGY_', 'IMAGING_'))
    if not prefixes:
        return events.none()
    allowed_actions = Q()
    for prefix in prefixes:
        allowed_actions |= Q(action__startswith=prefix)
    return events.filter(allowed_actions)


@login_required
def user_management(request):
    superuser_only(request)
    if request.method == 'POST':
        return handle_user_post(request)
    return render(request, 'core/users.html', build_user_context(request))


@login_required
def role_management(request):
    superuser_only(request)
    if request.method == 'POST':
        return handle_role_post(request)
    return render(request, 'core/roles.html', build_role_context(request))


@login_required
def catalog_hub(request):
    platform_admin_only(request)
    return render(request, 'catalog/hub.html', build_hub_context(request))


@login_required
def catalog_tariffs(request):
    platform_admin_only(request)
    if request.method == 'POST':
        return handle_tariffs_post(request)
    return render(request, 'catalog/tariffs.html', build_tariffs_context(request))


@login_required
def catalog_tariff_detail(request, pk):
    platform_admin_only(request)
    if request.method == 'POST':
        return handle_tariff_detail_post(request, pk)
    return render(request, 'catalog/tariff_detail.html', build_tariff_detail_context(request, pk))


@login_required
def catalog_tests(request):
    platform_admin_only(request)
    if request.method == 'POST':
        return handle_tests_post(request)
    return render(request, 'catalog/tests.html', build_tests_context(request))


@login_required
def catalog_service_groups(request):
    platform_admin_only(request)
    if request.method == 'POST':
        return handle_service_groups_post(request)
    return render(request, 'catalog/service_groups.html', build_service_groups_context(request))


@login_required
def catalog_doctors(request):
    platform_admin_only(request)
    if request.method == 'POST':
        return handle_doctors_post(request)
    return render(request, 'catalog/doctors.html', build_doctors_context(request))


@login_required
def catalog_imaging(request):
    platform_admin_only(request)
    if request.method == 'POST':
        return handle_imaging_post(request)
    return render(request, 'catalog/imaging.html', build_imaging_context(request))


@login_required
def catalog_centres(request):
    platform_admin_only(request)
    if request.method == 'POST':
        return handle_centres_post(request)
    return render(request, 'catalog/centres.html', build_centres_context(request))


@login_required
def catalog_clients(request):
    platform_admin_only(request)
    if request.method == 'POST':
        return handle_clients_post(request)
    return render(request, 'catalog/clients.html', build_clients_context(request))


def _test_catalog():
    rows = []
    lab_prices = TestPrice.objects.filter(tariff__is_active=True, test__is_active=True).select_related('test', 'tariff')
    for price in lab_prices:
        rows.append({
            'kind': 'laboratory', 'id': str(price.test_id), 'tariff': str(price.tariff_id),
            'code': price.test.code, 'name': price.test.name, 'price': f'{price.price:.2f}',
        })
    imaging_prices = ImagingPrice.objects.filter(tariff__is_active=True, procedure__is_active=True).select_related('procedure', 'tariff')
    for price in imaging_prices:
        rows.append({
            'kind': 'radiology', 'id': str(price.procedure_id), 'tariff': str(price.tariff_id),
            'code': price.procedure.code, 'name': price.procedure.name, 'price': f'{price.price:.2f}',
        })
    rows.sort(key=lambda row: (row['kind'], row['name'].lower()))
    return rows


def _register_centres(user):
    accesses = UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_register=True).select_related('centre')
    if user.is_superuser:
        return Centre.objects.filter(is_active=True)
    return [row.centre for row in accesses]


def _filter_visible_visits(visits, request):
    text = (request.GET.get('vq') or '').strip()
    if text:
        visits = visits.filter(
            Q(visit_id__icontains=text)
            | Q(patient__uhid__icontains=text)
            | Q(patient__first_name__icontains=text)
            | Q(patient__last_name__icontains=text)
            | Q(patient__other_names__icontains=text)
            | Q(patient__phone_number__icontains=text)
            | Q(client_name__icontains=text)
            | Q(trf_number__icontains=text)
            | Q(referral_attribution__doctor__first_name__icontains=text)
            | Q(referral_attribution__doctor__last_name__icontains=text)
            | Q(referral_attribution__institutions__name__icontains=text)
        )
    centre = (request.GET.get('vcentre') or '').strip()
    if centre:
        try:
            uuid.UUID(centre)
        except ValueError:
            visits = visits.none()
        else:
            visits = visits.filter(centre_id=centre)
    status = (request.GET.get('vstatus') or '').strip()
    if status:
        visits = visits.filter(status=status)
    channel = (request.GET.get('vchannel') or '').strip()
    if channel:
        visits = visits.filter(referral_source=channel)
    pay = (request.GET.get('vpay') or '').strip()
    if pay == 'NONE':
        visits = visits.filter(invoices__isnull=True)
    elif pay:
        visits = visits.filter(invoices__status=pay)
    start = parse_date(request.GET.get('vfrom') or '')
    end = parse_date(request.GET.get('vto') or '')
    if start:
        visits = visits.filter(created_at__date__gte=start)
    if end:
        visits = visits.filter(created_at__date__lte=end)
    return visits.distinct()


def _visible_patients(user):
    patients = Patient.objects.all()
    if user.is_superuser:
        return patients
    return patients.filter(visits__centre__user_access__user=user, visits__centre__user_access__can_view=True, visits__centre__is_active=True).distinct()


@login_required
def patient_registration(request):
    require_workspace(request.user, 'registration')
    centres = list(_register_centres(request.user))
    if request.method == 'POST':
        action = request.POST.get('action') or 'register'
        if action == 'attach_document':
            return _attach_document(request)
        centre = next((row for row in centres if str(row.pk) == request.POST.get('centre')), None)
        if centre is None:
            messages.error(request, 'Select a centre where you have registration permission.')
            return redirect('patient-registration')
        if action == 'new_visit':
            patient = _visible_patients(request.user).filter(pk=request.POST.get('patient')).first()
            if patient is None:
                messages.error(request, 'That patient is not available in your centres.')
                return redirect('patient-registration')
            try:
                with transaction.atomic():
                    apply_notification_prefs(patient, request.POST)
                    visit = Visit.objects.create(patient=patient, centre=centre, registered_by=request.user)
                    apply_visit_details(visit, request.POST)
                    invoice = process_registration(visit, request.user, request.POST)
                    _store_document(request, visit)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
                return redirect('patient-registration')
            AuditEvent.objects.create(actor=request.user, action='VISIT_OPENED', object_type='Visit', object_id=str(visit.pk), path=request.path, method='POST', centre=centre, detail={'uhid': patient.uhid, 'visit_id': visit.visit_id, 'invoice': invoice.invoice_number, 'status': visit.status})
            notify_patient(
                event_key=EVENT_REGISTRATION_SUCCESS,
                visit=visit,
                user=request.user,
                idempotency_key=f'registration-success:{visit.pk}',
                extra={'invoice_number': invoice.invoice_number},
            )
            messages.success(request, f'Visit {visit.visit_id} saved as Processed. Outstanding due {invoice.invoice_number} is ₦{invoice.total:.2f}.')
            return redirect('patient-registration')
        first = request.POST.get('first_name', '').strip()
        last = request.POST.get('last_name', '').strip()
        dob = parse_date(request.POST.get('date_of_birth') or '')
        if not first or not last:
            messages.error(request, 'First name, last name and phone number are required.')
            return redirect('patient-registration')
        try:
            phone = validate_local_phone(request.POST.get('phone_number'), field_label='Mobile')
            validate_optional_email(request.POST.get('email'))
            values = patient_values(request.POST)
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect('patient-registration')
        duplicate = Patient.objects.filter(first_name__iexact=first, last_name__iexact=last, phone_number=phone)
        if dob:
            duplicate = duplicate.filter(date_of_birth=dob)
        if duplicate.exists():
            messages.error(request, f'Possible duplicate patient found: {duplicate.first().uhid}. Open a visit for that record instead of creating another.')
            return redirect(f'/patients/register/?q={duplicate.first().uhid}')
        try:
            with transaction.atomic():
                patient = Patient.objects.create(first_name=first, last_name=last, phone_number=phone, date_of_birth=dob, **values)
                visit = Visit.objects.create(patient=patient, centre=centre, registered_by=request.user)
                apply_visit_details(visit, request.POST)
                invoice = process_registration(visit, request.user, request.POST)
                _store_document(request, visit)
                AuditEvent.objects.create(actor=request.user, action='PATIENT_REGISTERED', object_type='Visit', object_id=str(visit.pk), path=request.path, method='POST', centre=centre, detail={'uhid': patient.uhid, 'visit_id': visit.visit_id, 'invoice': invoice.invoice_number, 'status': visit.status})
            notify_patient(
                event_key=EVENT_REGISTRATION_SUCCESS,
                visit=visit,
                user=request.user,
                idempotency_key=f'registration-success:{visit.pk}',
                extra={'invoice_number': invoice.invoice_number},
            )
            messages.success(request, f'Processed. UHID {patient.uhid}, visit {visit.visit_id}. Outstanding due {invoice.invoice_number} is ₦{invoice.total:.2f}.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('patient-registration')
    query = (request.GET.get('q') or '').strip()
    matches = _visible_patients(request.user)
    if query:
        matches = matches.filter(Q(uhid__iexact=query) | Q(phone_number__icontains=query) | Q(alt_phone__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(other_names__icontains=query))
    else:
        matches = matches.none()
    visits = Visit.objects.select_related('patient', 'centre', 'client').prefetch_related('documents', 'invoices', 'referral_attribution__doctor', 'referral_attribution__institutions').order_by('-created_at')
    if not request.user.is_superuser:
        visits = visits.filter(centre__user_access__user=request.user, centre__user_access__can_view=True)
    visits = _filter_visible_visits(visits, request)
    centre_ids = [row.pk for row in centres]
    doctors = ReferralDoctor.objects.filter(is_active=True, centre_id__in=centre_ids).prefetch_related('institutions').select_related('centre').order_by('last_name', 'first_name')
    clients = OrganisationClient.objects.filter(is_active=True, centre_id__in=centre_ids).order_by('name')
    channel_catalog = {
        'doctors': [
            {
                'id': str(doctor.pk),
                'centre': str(doctor.centre_id),
                'name': f'{doctor.title} {doctor.first_name} {doctor.last_name}'.strip(),
                'institutions': [
                    {'id': str(institution.pk), 'name': institution.name}
                    for institution in doctor.institutions.all() if institution.is_active
                ],
            }
            for doctor in doctors
        ],
        'clients': [
            {'id': str(client.pk), 'centre': str(client.centre_id), 'kind': client.kind, 'name': client.name}
            for client in clients
        ],
    }
    selected = None
    selected_id = request.GET.get('patient')
    if selected_id:
        selected = matches.filter(pk=selected_id).first() if query else _visible_patients(request.user).filter(pk=selected_id).first()
    visit_filters = {
        'q': (request.GET.get('vq') or '').strip(),
        'centre': request.GET.get('vcentre') or '',
        'status': request.GET.get('vstatus') or '',
        'channel': request.GET.get('vchannel') or '',
        'pay': request.GET.get('vpay') or '',
        'start': request.GET.get('vfrom') or '',
        'end': request.GET.get('vto') or '',
    }
    return render(request, 'patients/registration.html', {
        'centres': centres,
        'visits': paginate(request, visits, 'vpage'),
        'matches': paginate(request, matches.order_by('last_name', 'first_name'), 'mpage'),
        'query': query,
        'visit_filters': visit_filters,
        'visit_statuses': [('REGISTERED', 'Registered'), ('PROCESSED', 'Processed')],
        'paypoint_statuses': [('UNPAID', 'Unpaid'), ('PART_PAID', 'Part paid'), ('PAID', 'Paid'), ('CANCELLED', 'Cancelled'), ('NONE', 'No invoice')],
        'page_sizes': PAGE_SIZES,
        'selected_patient': selected,
        'doctors': doctors,
        'channel_catalog': channel_catalog,
        'titles': TITLE_CHOICES,
        'sexes': SEX_CHOICES,
        'races': RACE_CHOICES,
        'states': STATE_CHOICES,
        'channels': CHANNEL_CHOICES,
        'how_heard': HOW_HEARD_CHOICES,
        'phone_codes': PHONE_CODES,
        'tariffs': Tariff.objects.filter(is_active=True).order_by('name'),
        'test_catalog': _test_catalog(),
    })


@login_required
def patient_timeline(request, pk):
    require_workspace(request.user, 'registration', 'accounts', 'laboratory', 'radiology', 'phlebotomy')
    patient = get_object_or_404(Patient, pk=pk)
    if not request.user.is_superuser and not _visible_patients(request.user).filter(pk=patient.pk).exists():
        raise Http404()
    return render(request, 'patients/timeline.html', {
        'patient': patient,
        'timeline': build_patient_timeline(request.user, patient),
    })


def _attach_document(request):
    visit = Visit.objects.filter(pk=request.POST.get('visit')).select_related('centre', 'patient').first()
    allowed = visit and (
        request.user.is_superuser
        or UserCentreAccess.objects.filter(user=request.user, centre=visit.centre, centre__is_active=True, can_register=True).exists()
    )
    if not allowed:
        messages.error(request, 'That visit is not available for filing.')
        return redirect('patient-registration')
    if not request.FILES.get('document'):
        messages.error(request, 'Choose a PDF, JPEG, or PNG to file on the visit.')
        return redirect('patient-registration')
    try:
        _store_document(request, visit)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('patient-registration')
    AuditEvent.objects.create(actor=request.user, action='DOCUMENT_FILED', object_type='Visit', object_id=str(visit.pk), path=request.path, method='POST', centre=visit.centre, detail={'visit_id': visit.visit_id, 'document_type': request.POST.get('document_type', 'REGISTRATION')})
    messages.success(request, f'Document filed on {visit.visit_id}.')
    return redirect('patient-registration')


def _store_document(request, visit):
    uploaded = request.FILES.get('document')
    if not uploaded:
        return
    validate_upload(uploaded)
    PatientDocument.objects.create(visit=visit, document_type=request.POST.get('document_type', 'REGISTRATION'), file=uploaded, uploaded_by=request.user)


@login_required
def download_patient_document(request, pk):
    document = get_object_or_404(PatientDocument.objects.select_related('visit__centre'), pk=pk)
    allowed = request.user.is_superuser or UserCentreAccess.objects.filter(user=request.user, centre=document.visit.centre, centre__is_active=True, can_view=True).exists()
    if not allowed:
        raise Http404()
    name = document.file.name.rsplit('/', 1)[-1]
    return FileResponse(document.file.open('rb'), as_attachment=True, filename=name)


@login_required
def download_settlement_proof(request, pk):
    from referrals.models import WithdrawalRequest
    row = get_object_or_404(WithdrawalRequest.objects.select_related('doctor__centre'), pk=pk)
    if not row.proof_of_payment:
        raise Http404()
    allowed = request.user.is_superuser or UserCentreAccess.objects.filter(user=request.user, centre=row.doctor.centre, centre__is_active=True, can_view=True).exists()
    if not allowed:
        raise Http404()
    name = row.proof_of_payment.name.rsplit('/', 1)[-1]
    return FileResponse(row.proof_of_payment.open('rb'), as_attachment=True, filename=name)


@login_required
def accounts_workspace(request):
    require_workspace(request.user, 'accounts')
    if request.method == 'POST':
        result = handle_accounts_post(request)
        ok, text = result[0], result[1]
        if ok:
            messages.success(request, text)
        else:
            messages.error(request, text)
        if ok and len(result) > 2 and result[2]:
            return redirect('payment-receipt', pk=result[2])
        return redirect('accounts-workspace')
    return render(request, 'billing/accounts.html', build_accounts_context(request))


@login_required
def accounts_reports(request):
    require_workspace(request.user, 'accounts')
    if not is_accountant(request.user):
        messages.error(request, 'Only an accountant can open financial reports.')
        return redirect('accounts-workspace')
    if request.method == 'POST':
        result = handle_accounts_post(request)
        ok, text = result[0], result[1]
        if ok:
            messages.success(request, text)
        else:
            messages.error(request, text)
        return redirect(request.get_full_path() if request.GET else 'accounts-reports')
    return render(request, 'billing/accountant.html', build_accountant_context(request))


@login_required
def accounts_export(request):
    require_workspace(request.user, 'accounts')
    return export_accountant_report(request)


@login_required
def payment_receipt(request, pk):
    require_workspace(request.user, 'accounts')
    payment = receipt_payment(request.user, pk)
    return render(request, 'billing/receipt.html', {'payment': payment, 'invoice': payment.invoice})


@login_required
def laboratory_workspace(request):
    require_workspace(request.user, 'laboratory')
    if request.method == 'POST':
        return _flash(request, handle_laboratory_post, 'laboratory-workspace')
    return render(request, 'laboratory/workspace.html', build_laboratory_context(request))


@login_required
def phlebotomy_workspace(request):
    require_workspace(request.user, 'phlebotomy')
    if request.method == 'POST':
        return _flash(request, handle_phlebotomy_post, 'phlebotomy-workspace')
    return render(request, 'laboratory/phlebotomy.html', build_phlebotomy_context(request))


@login_required
def radiology_workspace(request):
    require_workspace(request.user, 'radiology')
    if request.method == 'POST':
        return _flash(request, handle_radiology_post, 'radiology-workspace')
    return render(request, 'radiology/workspace.html', build_radiology_context(request))


def healthz(request):
    """Minimal Railway health endpoint; returns no secrets or database details."""
    from django.db import connections
    from django.db.utils import OperationalError
    try:
        connections['default'].ensure_connection()
    except OperationalError:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
