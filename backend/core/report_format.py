"""Centre report formatting and authenticator signature snapshots."""
from __future__ import annotations

from core.models import CentreReportFormat


def get_report_format(centre) -> CentreReportFormat:
    fmt, _ = CentreReportFormat.objects.get_or_create(centre=centre)
    return fmt


def unverified_watermark_text(centre) -> str:
    """Watermark on signed (FINAL) copies awaiting approval."""
    text = (get_report_format(centre).watermark_text or '').strip()
    return text or 'UNVERIFIED'


def approved_watermark_text(centre=None) -> str:
    """Watermark on approved copies ready for (or used during) dispatch."""
    return 'APPROVED'


def status_watermark_text(status, centre) -> str:
    """Status watermark for finished PDFs. Released copies have no status mark."""
    if status == 'FINAL':
        return unverified_watermark_text(centre)
    if status == 'APPROVED':
        return approved_watermark_text(centre)
    return ''


def format_snapshot(centre) -> dict:
    fmt = get_report_format(centre)
    logo_path = ''
    if fmt.show_logo and centre.logo:
        logo_path = centre.logo.name
    return {
        'centre_code': centre.code,
        'centre_name': centre.name,
        'centre_address': centre.address if fmt.show_centre_address else '',
        'centre_phone': centre.phone if fmt.show_centre_contact else '',
        'centre_email': centre.email if fmt.show_centre_contact else '',
        'logo_path': logo_path,
        'title_lab': fmt.title_lab,
        'title_radiology': fmt.title_radiology,
        'header_text': fmt.header_text,
        'footer_text': fmt.footer_text,
        'show_logo': fmt.show_logo,
        'layout_style': fmt.layout_style,
        'font_family': fmt.font_family,
        'body_font_size': fmt.body_font_size,
        'margin_mm': fmt.margin_mm,
        'primary_color': fmt.primary_color,
        'accent_color': fmt.accent_color,
        'show_authenticator_signature': fmt.show_authenticator_signature,
        'signature_label': fmt.signature_label,
        'watermark_text': fmt.watermark_text,
    }


def signature_snapshot(user) -> dict:
    if user is None:
        return {}
    path = ''
    if getattr(user, 'signature', None):
        try:
            path = user.signature.name or ''
        except ValueError:
            path = ''
    title = ''
    if hasattr(user, 'professional_title'):
        title = user.professional_title
    else:
        title = (getattr(user, 'job_title', None) or getattr(user, 'signature_title', None) or '').strip()
    return {
        'user_id': str(user.pk),
        'name': str(user),
        'title': title,
        'qualifications': (getattr(user, 'qualifications', None) or '').strip(),
        'license_number': (getattr(user, 'license_number', None) or '').strip(),
        'department': (getattr(user, 'department', None) or '').strip(),
        'specialty': (getattr(user, 'specialty', None) or '').strip(),
        'signature_path': path,
        'has_signature': bool(path),
    }


def apply_format_and_signature(snapshot: dict, *, centre, signer=None, kind='lab') -> dict:
    """Mutate/return snapshot with frozen format + authenticator signature."""
    data = dict(snapshot or {})
    data['format'] = format_snapshot(centre)
    data['report_kind'] = kind
    if signer is not None:
        data['authenticator'] = signature_snapshot(signer)
    return data
