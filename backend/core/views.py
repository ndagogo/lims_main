import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from billing.accounts_adjust import (
    build_bill_edit_context, build_refunds_context, handle_bill_edit_post, handle_refund_post,
)
from billing.registration_billing import process_registration
from billing.workspace import (
    build_accounts_context, build_accountant_context, export_accountant_report,
    handle_accounts_post, receipt_payment,
)
from communications.reports import build_operations_summary, build_work_queues
from core.access import (
    allowed_workspaces, can_access_platform_controls, can_change_referring_doctor,
    can_edit_bill, can_modify_patient_data, can_refund_payment, is_accountant,
    is_platform_admin, require_workspace,
)
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
from laboratory.labels import sample_label as _unused_sample_label  # noqa: F401 — imported via urls
from laboratory.api import scoped_samples
from laboratory.models import Sample, TatPauseReason
from laboratory.tat import sample_timeline_payload, tat_dashboard_payload
from patients.demographics import apply_demographic_updates, demographics_form_choices, search_patients_for_demographics
from patients.models import CHANNEL_CHOICES, HOW_HEARD_CHOICES, OrganisationClient, Patient, PatientDocument, RACE_CHOICES, SEX_CHOICES, STATE_CHOICES, TITLE_CHOICES, Visit
from referrals.remap import (
    REQUEST_FORM_MESSAGE, SAME_DOCTOR_MESSAGE, attributions_for_patient,
    real_doctors_for_centre, remap_history, remap_referring_doctor,
    search_patients_for_remap, visit_has_request_form,
)
from laboratory.models import StatType, Tariff, TestPrice
from communications.messaging import EVENT_REGISTRATION_SUCCESS, notify_patient, notify_referral_received
from patients.registration import (
    PHONE_CODES, apply_guardian, apply_notification_prefs, apply_visit_details, normalize_phone_digits,
    patient_values, validate_local_phone, validate_optional_email,
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
        from core.access import staff_home_url_name
        return redirect(staff_home_url_name(request.user))
    error = None
    if request.method == 'POST':
        user, error = attempt_login(request.POST.get('username'), request.POST.get('password'))
        if user is not None:
            login(request, user)
            from core.audit import record_audit
            record_audit(
                request=request, actor=user, action='LOGIN_SUCCESS', object_type='AUTH',
                category='AUTH', department=getattr(user, 'department', '') or '',
            )
            from core.access import staff_home_url_name
            return redirect(_safe_next(request) or staff_home_url_name(user))
        from core.audit import record_audit
        record_audit(
            request=request, actor=None, action='LOGIN_FAILED', object_type='AUTH',
            category='AUTH', detail={'username': (request.POST.get('username') or '')[:150]},
        )
    return render(request, 'core/login.html', {'error': error, 'next': request.GET.get('next', '')})


@require_POST
def logout_page(request):
    from core.audit import record_audit
    if request.user.is_authenticated:
        record_audit(
            request=request, actor=request.user, action='LOGOUT', object_type='AUTH',
            category='AUTH', department=getattr(request.user, 'department', '') or '',
        )
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
def account_profile(request):
    """Enterprise staff profile: identity, credentials, photo, and report signature."""
    User = get_user_model()
    user = request.user
    if request.method == 'POST':
        action = request.POST.get('action') or 'save'
        try:
            if action == 'clear_avatar':
                if user.avatar:
                    user.avatar.delete(save=False)
                    user.avatar = None
                    user.save(update_fields=['avatar'])
                messages.success(request, 'Profile photo removed.')
                return redirect('account-profile')
            if action == 'clear_signature':
                if user.signature:
                    user.signature.delete(save=False)
                    user.signature = None
                    user.save(update_fields=['signature'])
                messages.success(request, 'Signature removed.')
                return redirect('account-profile')

            email = (request.POST.get('email') or '').strip()
            if not email:
                raise ValidationError('Email is required.')
            if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                raise ValidationError('That email is already used by another account.')

            user.first_name = (request.POST.get('first_name') or '').strip()[:150]
            user.last_name = (request.POST.get('last_name') or '').strip()[:150]
            user.display_name = (request.POST.get('display_name') or '').strip()[:180]
            user.email = email
            user.job_title = (request.POST.get('job_title') or '').strip()[:120]
            user.signature_title = user.job_title  # keep report snapshot field in sync
            user.staff_id = (request.POST.get('staff_id') or '').strip()[:40]
            user.department = (request.POST.get('department') or '').strip()[:120]
            user.specialty = (request.POST.get('specialty') or '').strip()[:120]
            user.qualifications = (request.POST.get('qualifications') or '').strip()[:255]
            user.license_number = (request.POST.get('license_number') or '').strip()[:80]
            user.license_authority = (request.POST.get('license_authority') or '').strip()[:120]
            user.phone_work = (request.POST.get('phone_work') or '').strip()[:30]
            user.phone_mobile = (request.POST.get('phone_mobile') or '').strip()[:30]
            user.office_extension = (request.POST.get('office_extension') or '').strip()[:20]
            user.bio = (request.POST.get('bio') or '').strip()

            avatar = request.FILES.get('avatar')
            if avatar:
                validate_upload(avatar, kinds=('jpeg', 'png'), max_bytes=2 * 1024 * 1024)
                if user.avatar:
                    user.avatar.delete(save=False)
                user.avatar = avatar
            signature = request.FILES.get('signature')
            if signature:
                validate_upload(signature, kinds=('jpeg', 'png'), max_bytes=2 * 1024 * 1024)
                if user.signature:
                    user.signature.delete(save=False)
                user.signature = signature

            user.save()
            AuditEvent.objects.create(
                actor=user, action='PROFILE_UPDATED', object_type='User', object_id=str(user.pk),
                path=request.path, method='POST',
                ip_address=getattr(request, 'audit_ip', None),
                browser=getattr(request, 'audit_browser', ''),
                detail={'has_avatar': bool(user.avatar), 'has_signature': bool(user.signature)},
            )
            messages.success(request, 'Profile saved.')
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc))
        return redirect('account-profile')

    centres = UserCentreAccess.objects.filter(
        user=user, centre__is_active=True, can_view=True,
    ).select_related('centre').order_by('centre__name')
    return render(request, 'core/account_profile.html', {
        'profile_user': user,
        'avatar_url': user.avatar.url if user.avatar else '',
        'signature_url': user.signature.url if user.signature else '',
        'roles': list(user.groups.values_list('name', flat=True)),
        'centre_access': centres,
        'mfa_enabled': user.mfa_enabled,
    })


@login_required
def account_signature(request):
    """Back-compat redirect — signature lives on the profile page."""
    return redirect('account-profile')


@login_required
def dashboard(request):
    from core.access import staff_home_url_name
    user = request.user
    if staff_home_url_name(user) == 'radiology-workspace':
        return redirect('radiology-workspace')
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
    context['show_platform_controls_entry'] = can_access_platform_controls(user)
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
        existing_id = (request.POST.get('patient') or request.POST.get('existing_patient') or '').strip()
        # One registration form: returning patients post with existing patient id.
        if action in ('new_visit', 'register') and existing_id:
            patient = _visible_patients(request.user).filter(pk=existing_id).first()
            if patient is None:
                messages.error(request, 'That patient is not available in your centres.')
                return redirect('patient-registration')
            try:
                with transaction.atomic():
                    apply_notification_prefs(patient, request.POST)
                    apply_guardian(patient, request.POST)
                    visit = Visit.objects.create(patient=patient, centre=centre, registered_by=request.user)
                    apply_visit_details(visit, request.POST)
                    invoice = process_registration(visit, request.user, request.POST)
                    _store_document(request, visit)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
                return redirect('patient-registration')
            from patients.visit_audit import record_visit_audit
            record_visit_audit(
                request, visit, action='VISIT_OPENED',
                detail={
                    'uhid': patient.uhid, 'visit_id': visit.visit_id,
                    'invoice': invoice.invoice_number, 'status': visit.status,
                    'total': str(invoice.total),
                },
                new_values={'status': visit.status, 'invoice': invoice.invoice_number},
            )
            notify_patient(
                event_key=EVENT_REGISTRATION_SUCCESS,
                visit=visit,
                user=request.user,
                idempotency_key=f'registration-success:{visit.pk}',
                extra={'invoice_number': invoice.invoice_number},
            )
            notify_referral_received(visit, user=request.user)
            messages.success(request, f'Visit {visit.visit_id} saved as Processed. Outstanding due {invoice.invoice_number} is ₦{invoice.total:.2f}.')
            return redirect('patient-registration')
        if action != 'register':
            messages.error(request, 'Choose a registration action.')
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
        # Prefer phone match so the returning-patient path is used instead of a second UHID.
        phone_match = _visible_patients(request.user).filter(phone_number=phone).first()
        if phone_match:
            messages.error(request, f'This phone already belongs to {phone_match.uhid}. Confirm the returning-patient alert and continue on that record.')
            return redirect('patient-registration')
        duplicate = Patient.objects.filter(first_name__iexact=first, last_name__iexact=last, phone_number=phone)
        if dob:
            duplicate = duplicate.filter(date_of_birth=dob)
        if duplicate.exists():
            messages.error(request, f'Possible duplicate patient found: {duplicate.first().uhid}. Continue with that record from the phone alert.')
            return redirect('patient-registration')
        try:
            with transaction.atomic():
                patient = Patient.objects.create(first_name=first, last_name=last, phone_number=phone, date_of_birth=dob, **values)
                apply_guardian(patient, request.POST)
                visit = Visit.objects.create(patient=patient, centre=centre, registered_by=request.user)
                apply_visit_details(visit, request.POST)
                invoice = process_registration(visit, request.user, request.POST)
                _store_document(request, visit)
                from patients.visit_audit import record_visit_audit
                record_visit_audit(
                    request, visit, action='PATIENT_REGISTERED',
                    detail={
                        'uhid': patient.uhid, 'visit_id': visit.visit_id,
                        'invoice': invoice.invoice_number, 'status': visit.status,
                        'total': str(invoice.total),
                    },
                    new_values={'uhid': patient.uhid, 'visit_id': visit.visit_id, 'status': visit.status},
                )
            notify_patient(
                event_key=EVENT_REGISTRATION_SUCCESS,
                visit=visit,
                user=request.user,
                idempotency_key=f'registration-success:{visit.pk}',
                extra={'invoice_number': invoice.invoice_number},
            )
            notify_referral_received(visit, user=request.user)
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
    from referrals.services import doctor_ids_serving
    doctors = ReferralDoctor.objects.filter(
        pk__in=doctor_ids_serving(centre_ids), is_active=True, is_default_placeholder=False,
    ).prefetch_related('institutions', 'centres').select_related('centre').order_by('last_name', 'first_name')
    clients = OrganisationClient.objects.filter(is_active=True, centre_id__in=centre_ids).order_by('name')
    channel_catalog = {
        'doctors': [
            {
                'id': str(doctor.pk),
                'centre': str(doctor.centre_id),
                'centres': list(dict.fromkeys(
                    [str(doctor.centre_id)] + [str(row.pk) for row in doctor.centres.all()]
                )),
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
        'stat_types': [
            {
                'id': row.pk, 'name': row.name, 'color': row.color,
                'text_color': row.text_color, 'is_default': row.is_default,
            }
            for row in StatType.objects.filter(is_active=True).order_by('sort_order', 'name')
        ],
    })


@login_required
def patient_timeline(request, pk):
    require_workspace(request.user, 'registration', 'accounts', 'laboratory', 'radiology', 'phlebotomy')
    patient = get_object_or_404(Patient, pk=pk)
    if not request.user.is_superuser and not _visible_patients(request.user).filter(pk=patient.pk).exists():
        raise Http404()
    from patients.models import Visit
    visits = Visit.objects.filter(patient=patient).order_by('-created_at')
    if not request.user.is_superuser:
        visits = visits.filter(
            centre__user_access__user=request.user,
            centre__user_access__can_view=True,
            centre__is_active=True,
        ).distinct()
    return render(request, 'patients/timeline.html', {
        'patient': patient,
        'timeline': build_patient_timeline(request.user, patient),
        'visits': visits[:50],
    })


@login_required
def visit_audit_trail(request, visit_id):
    """Enterprise per-registration audit trail with actor, IP, browser, and network location."""
    require_workspace(request.user, 'registration', 'accounts', 'laboratory', 'radiology', 'phlebotomy', 'audit')
    from patients.models import Visit
    from patients.visit_audit import build_visit_audit_trail, user_may_view_visit
    visit = get_object_or_404(
        Visit.objects.select_related('patient', 'centre', 'registered_by'),
        visit_id=visit_id,
    )
    if not user_may_view_visit(request.user, visit):
        raise Http404()
    return render(request, 'patients/visit_audit.html', {
        'visit': visit,
        'patient': visit.patient,
        'events': build_visit_audit_trail(request.user, visit),
    })


@login_required
def patient_phone_lookup(request):
    """JSON lookup used by registration when the 10-digit mobile is entered."""
    require_workspace(request.user, 'registration')
    phone = normalize_phone_digits(request.GET.get('phone') or '')
    if len(phone) != 10:
        return JsonResponse({'found': False, 'patients': []})
    rows = list(_visible_patients(request.user).filter(phone_number=phone).order_by('-updated_at')[:5])
    patients = [
        {
            'id': str(row.pk),
            'uhid': row.uhid,
            'title': row.title or '',
            'first_name': row.first_name,
            'last_name': row.last_name,
            'other_names': row.other_names or '',
            'sex': row.sex or '',
            'date_of_birth': row.date_of_birth.isoformat() if row.date_of_birth else '',
            'phone_country_code': row.phone_country_code or '+234',
            'phone_number': row.phone_number,
            'alt_phone': row.alt_phone or '',
            'email': row.email or '',
            'address': row.address or '',
            'city': row.city or '',
            'state': row.state or '',
            'race': row.race or '',
            'health_card_number': row.health_card_number or '',
            'passport_number': row.passport_number or '',
            'notify_sms': row.notify_sms,
            'notify_email': row.notify_email,
            'notify_whatsapp': row.notify_whatsapp,
            'display_name': f'{row.title} {row.first_name} {row.last_name}'.strip(),
        }
        for row in rows
    ]
    return JsonResponse({'found': bool(patients), 'patients': patients})


@login_required
def patient_demographics(request):
    if not can_modify_patient_data(request.user):
        messages.error(request, 'Demographic changes require can_modify_patient_data privilege.')
        return redirect('dashboard')
    query = (request.GET.get('q') or '').strip()
    matches = search_patients_for_demographics(request.user, query) if query else Patient.objects.none()
    selected = None
    selected_id = (request.GET.get('patient') or '').strip()
    if selected_id:
        selected = _visible_patients(request.user).filter(pk=selected_id).first()
    if request.method == 'POST':
        patient = _visible_patients(request.user).filter(pk=request.POST.get('patient')).first()
        if patient is None:
            messages.error(request, 'Select a patient that belongs to your centres.')
            return redirect('patient-demographics')
        try:
            patient, changed = apply_demographic_updates(request.user, patient, request.POST, request=request)
            messages.success(request, f'Demographics updated for {patient.uhid} ({len(changed)} field(s)).')
            return redirect(f"{request.path}?q={patient.uhid}&patient={patient.pk}")
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect(f"{request.path}?q={query or patient.uhid}&patient={patient.pk}")
    context = {
        'query': query,
        'matches': paginate(request, matches, 'mpage'),
        'selected_patient': selected,
        'phone_codes': PHONE_CODES,
        **demographics_form_choices(),
    }
    return render(request, 'patients/demographics.html', context)


@login_required
def referring_doctor_remap(request):
    """Change referring doctor on any attributed visit (DEFAULT or a real doctor)."""
    if not can_change_referring_doctor(request.user):
        messages.error(request, 'Changing a referring doctor requires change_referring_doctor privilege.')
        return redirect('dashboard')
    query = (request.GET.get('q') or '').strip()
    matches = search_patients_for_remap(request.user, query) if query else Patient.objects.none()
    selected = None
    selected_id = (request.GET.get('patient') or '').strip()
    if selected_id:
        selected = _visible_patients(request.user).filter(pk=selected_id).first()
    attributed_visits = attributions_for_patient(request.user, selected) if selected else []
    doctor_catalog = {}
    for visit in attributed_visits:
        current = visit.referral_attribution.doctor
        rows = []
        for doctor in real_doctors_for_centre(visit.centre):
            rows.append({
                'id': str(doctor.pk),
                'doctor_code': doctor.doctor_code,
                'name': f'{doctor.title} {doctor.first_name} {doctor.last_name}'.strip(),
                'institutions': [
                    {'id': str(inst.pk), 'name': inst.name}
                    for inst in doctor.institutions.all() if inst.is_active
                ],
            })
        doctor_catalog[str(visit.pk)] = {
            'centre': visit.centre.name,
            'current_doctor_id': str(current.pk) if not current.is_default_placeholder else '',
            'current_doctor_code': '' if current.is_default_placeholder else current.doctor_code,
            'current_label': (
                'DEFAULT DOCTOR' if current.is_default_placeholder
                else f'{current.title} {current.first_name} {current.last_name}'.strip()
            ),
            'has_request_form': visit_has_request_form(visit),
            'doctors': rows,
        }

    if request.method == 'POST':
        visit = (
            Visit.objects.select_related('patient', 'centre', 'referral_attribution__doctor')
            .filter(pk=request.POST.get('visit'))
            .first()
        )
        if visit is None:
            messages.error(request, 'Select a visit with a referring doctor to remap.')
            return redirect('referring-doctor-remap')
        try:
            attribution = remap_referring_doctor(request.user, visit, request.POST)
            messages.success(
                request,
                f'Visit {visit.visit_id} remapped to '
                f'{attribution.doctor.first_name} {attribution.doctor.last_name}.',
            )
            return redirect(
                f"{request.path}?q={visit.patient.uhid}&patient={visit.patient_id}"
            )
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect(
                f"{request.path}?q={query or visit.patient.uhid}&patient={visit.patient_id}"
            )
        except PermissionDenied as exc:
            messages.error(request, str(exc))
            return redirect('dashboard')

    history_query = (request.GET.get('hq') or '').strip()
    history_from = parse_date(request.GET.get('hfrom') or '')
    history_to = parse_date(request.GET.get('hto') or '')
    history_kwargs = {
        'query': history_query,
        'date_from': history_from,
        'date_to': history_to,
    }
    if history_query or history_from or history_to:
        history_rows = remap_history(request.user, **history_kwargs)
    elif selected:
        history_rows = remap_history(request.user, patient=selected)
        if not history_rows.exists():
            history_rows = remap_history(request.user)
    else:
        history_rows = remap_history(request.user)
    return render(request, 'patients/referring_doctor_remap.html', {
        'query': query,
        'history_query': history_query,
        'history_from': request.GET.get('hfrom') or '',
        'history_to': request.GET.get('hto') or '',
        'matches': paginate(request, matches, 'mpage'),
        'selected_patient': selected,
        'attributed_visits': attributed_visits,
        'doctor_catalog': doctor_catalog,
        'remap_history': paginate(request, history_rows, 'hpage'),
        'request_form_message': REQUEST_FORM_MESSAGE,
        'same_doctor_message': SAME_DOCTOR_MESSAGE,
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
    from patients.visit_audit import record_visit_audit
    record_visit_audit(
        request, visit, action='DOCUMENT_FILED',
        detail={'visit_id': visit.visit_id, 'document_type': request.POST.get('document_type', 'REGISTRATION')},
        new_values={'document_type': request.POST.get('document_type', 'REGISTRATION')},
    )
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
    inline = request.GET.get('inline') == '1'
    return FileResponse(document.file.open('rb'), as_attachment=not inline, filename=name)


@login_required
def download_settlement_proof(request, pk):
    from referrals.models import WithdrawalRequest
    row = get_object_or_404(WithdrawalRequest.objects.select_related('doctor__centre'), pk=pk)
    if not row.proof_of_payment:
        raise Http404()
    from referrals.services import doctor_ids_serving
    visible_centres = UserCentreAccess.objects.filter(user=request.user, centre__is_active=True, can_view=True).values('centre_id')
    allowed = request.user.is_superuser or doctor_ids_serving(visible_centres).filter(pk=row.doctor_id).exists()
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
def accounts_refunds(request):
    require_workspace(request.user, 'accounts')
    if not can_refund_payment(request.user):
        messages.error(request, 'payment_refund privilege is required for test cancel/refund.')
        return redirect('accounts-workspace')
    if request.method == 'POST':
        ok, text = handle_refund_post(request)
        if ok:
            messages.success(request, text)
        else:
            messages.error(request, text)
        visit = request.POST.get('visit') or request.GET.get('visit') or ''
        q = request.POST.get('q') or request.GET.get('q') or ''
        return redirect(f"/accounts/refunds/?q={q}&visit={visit}" if visit or q else 'accounts-refunds')
    return render(request, 'billing/refunds.html', build_refunds_context(request))


@login_required
def accounts_bill_edit(request):
    require_workspace(request.user, 'accounts')
    if not can_edit_bill(request.user):
        messages.error(request, 'bill_edit / bill_modify privilege is required.')
        return redirect('accounts-workspace')
    if request.method == 'POST':
        ok, text = handle_bill_edit_post(request)
        if ok:
            messages.success(request, text)
        else:
            messages.error(request, text)
        visit = request.POST.get('visit') or request.GET.get('visit') or ''
        q = request.POST.get('q') or request.GET.get('q') or ''
        return redirect(f"/accounts/bill-edit/?q={q}&visit={visit}" if visit or q else 'accounts-bill-edit')
    return render(request, 'billing/bill_edit.html', build_bill_edit_context(request))


@login_required
def payment_receipt(request, pk):
    require_workspace(request.user, 'accounts')
    payment = receipt_payment(request.user, pk)
    return render(request, 'billing/receipt.html', {'payment': payment, 'invoice': payment.invoice})


@login_required
def invoice_reprint(request, pk):
    """Printable invoice sheet for users with reprint_invoice privilege."""
    require_workspace(request.user, 'accounts')
    from billing.workspace import invoice_for_reprint
    from core.audit import record_audit
    invoice = invoice_for_reprint(request.user, pk)
    visit = invoice.visit
    record_audit(
        request=request,
        actor=request.user,
        action='INVOICE_REPRINTED',
        object_type='Invoice',
        object_id=str(invoice.pk),
        centre=visit.centre,
        patient_ref=getattr(visit.patient, 'uhid', '') or '',
        visit_ref=visit.visit_id,
        detail={
            'invoice': invoice.invoice_number,
            'visit_id': visit.visit_id,
            'status': invoice.status,
            'total': str(invoice.total),
        },
        category='BILLING',
    )
    return render(request, 'billing/invoice_print.html', {'invoice': invoice})


def _lab_flash(request, handler, redirect_name, *, stage_param='stage'):
    """Flash success/error; after collect/recollect, open the barcode label."""
    result = handler(request)
    sample_pk = None
    if isinstance(result, tuple) and len(result) == 3:
        ok, text, sample_pk = result
    else:
        ok, text = result
    if ok:
        messages.success(request, text)
    else:
        messages.error(request, text)
    if ok and sample_pk:
        from django.urls import reverse
        return redirect(f"{reverse('sample-label', kwargs={'pk': sample_pk})}?print=1")
    stage = (request.POST.get(stage_param) or request.GET.get(stage_param) or '').strip()
    if stage and redirect_name == 'laboratory-workspace':
        from django.urls import reverse
        return redirect(f"{reverse(redirect_name)}?stage={stage}")
    return redirect(redirect_name)


@login_required
def laboratory_workspace(request):
    require_workspace(request.user, 'laboratory')
    if request.method == 'POST':
        return _lab_flash(request, handle_laboratory_post, 'laboratory-workspace')
    return render(request, 'laboratory/workspace.html', build_laboratory_context(request))


@login_required
def laboratory_tat(request):
    require_workspace(request.user, 'laboratory')
    from laboratory.tat import tat_dashboard_payload
    if request.method == 'POST':
        return _lab_flash(request, handle_laboratory_post, 'laboratory-tat')
    return render(request, 'laboratory/tat_dashboard.html', {
        'tat': tat_dashboard_payload(request.user),
    })


@login_required
def sample_timeline(request, pk):
    require_workspace(request.user, 'laboratory', 'phlebotomy')
    from laboratory.api import scoped_samples
    from laboratory.models import TatPauseReason
    from laboratory.tat import build_unified_timeline, sample_timeline_payload
    sample = get_object_or_404(
        scoped_samples(request.user).select_related(
            'centre', 'order__test', 'order__visit__patient', 'collected_by',
        ).prefetch_related('activities', 'tat_pauses__reason', 'processing_records'),
        pk=pk,
    )
    if request.method == 'POST':
        result = handle_laboratory_post(request)
        ok, text = result[0], result[1]
        if ok:
            messages.success(request, text)
        else:
            messages.error(request, text)
        return redirect('sample-timeline', pk=pk)
    payload = sample_timeline_payload(sample)
    return render(request, 'laboratory/sample_timeline.html', {
        'sample': sample,
        'payload': payload,
        'events': build_unified_timeline(sample),
        'milestones': payload['milestones'],
        'pause_reasons': TatPauseReason.objects.filter(is_active=True),
        'open_pause': sample.tat_pauses.filter(ended_at__isnull=True).exists(),
    })


@login_required
def phlebotomy_workspace(request):
    require_workspace(request.user, 'phlebotomy')
    if request.method == 'POST':
        return _lab_flash(request, handle_phlebotomy_post, 'phlebotomy-workspace')
    return render(request, 'laboratory/phlebotomy.html', build_phlebotomy_context(request))


@login_required
def radiology_workspace(request):
    require_workspace(request.user, 'radiology')
    if request.method == 'POST':
        ok, text = handle_radiology_post(request)
        if ok:
            messages.success(request, text)
        else:
            messages.error(request, text)
        from urllib.parse import urlencode
        from django.urls import reverse
        from radiology.models import RadiologyExam
        params = {}
        study = request.POST.get('order') or request.GET.get('study')
        exam_id = request.POST.get('exam')
        if not study and exam_id:
            study = RadiologyExam.objects.filter(pk=exam_id).values_list('order_id', flat=True).first()
        if study:
            params['study'] = study
        for key in ('modality', 'when', 'desk', 'date_from', 'date_to', 'visit', 'status', 'patient_bill_id'):
            if request.GET.get(key):
                params[key] = request.GET.get(key)
        target = reverse('radiology-workspace')
        if params:
            target = f'{target}?{urlencode(params)}'
        return redirect(target)
    return render(request, 'radiology/workspace.html', build_radiology_context(request))


@login_required
def radiology_performance(request):
    from core.paging import paginate
    from core.platform_admin import require_platform_controls
    from radiology.desk import performance_export_rows, radiologist_performance
    from reporting.exports import tables_export
    from urllib.parse import urlencode

    require_platform_controls(request)
    payload = radiologist_performance(request.GET)
    export_fmt = (request.GET.get('export') or '').lower()
    if export_fmt in ('csv', 'pdf', 'xlsx', 'excel'):
        if export_fmt == 'excel':
            export_fmt = 'xlsx'
        summary_rows = [
            [row['display_name'], row['accepted'], row['missed'], row['total']]
            for row in payload['summary']
        ]
        return tables_export(
            'Radiologist performance',
            [
                {
                    'title': 'Summary',
                    'headers': ['Radiologist', 'Accepted', 'Not accepted in time', 'Assignments'],
                    'rows': summary_rows,
                },
                {
                    'title': 'Activity',
                    'headers': ['When', 'Radiologist', 'Visit', 'Study', 'Outcome', 'Window (min)'],
                    'rows': performance_export_rows(payload['activity']),
                },
            ],
            fmt=export_fmt,
            basename='radiologist-performance',
        )
    activity_page = paginate(request, payload['activity'], 'page')
    filters = {key: value for key, value in payload['filters'].items() if not key.startswith('_')}
    list_query = urlencode({key: value for key, value in filters.items() if value})
    return render(request, 'radiology/performance.html', {
        'summary': payload['summary'],
        'activity': activity_page,
        'filters': filters,
        'radiologist_choices': payload['radiologist_choices'],
        'totals': payload['totals'],
        'list_query': list_query,
    })


@login_required
def radiology_templates(request):
    require_workspace(request.user, 'radiology')
    from radiology.workspace import build_radiology_templates_context, handle_radiology_templates_post
    if request.method == 'POST':
        return handle_radiology_templates_post(request, platform=False)
    return render(request, 'platform/radiology_templates.html', build_radiology_templates_context(request, platform=False))


@login_required
def radiology_template_builder(request, pk):
    require_workspace(request.user, 'radiology')
    from radiology.workspace import (
        build_radiology_template_builder_context, handle_radiology_template_builder_post,
    )
    if request.method == 'POST':
        return handle_radiology_template_builder_post(request, pk, platform=False)
    return render(
        request, 'platform/radiology_template_builder.html',
        build_radiology_template_builder_context(request, pk, platform=False),
    )


def healthz(request):
    """Minimal Railway health endpoint; returns no secrets or database details."""
    from django.db import connections
    from django.db.utils import OperationalError
    try:
        connections['default'].ensure_connection()
    except OperationalError:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
