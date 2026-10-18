"""Radiology report template publish/clone and sign-off snapshots."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from radiology.models import RadiologyReportTemplate


def publish_template(template: RadiologyReportTemplate, user=None) -> RadiologyReportTemplate:
    if template.status == 'PUBLISHED':
        return template
    qs = RadiologyReportTemplate.objects.filter(
        modality_type=template.modality_type, name=template.name, status='PUBLISHED',
        scope=template.scope,
    ).exclude(pk=template.pk)
    if template.owner_id:
        qs = qs.filter(owner_id=template.owner_id)
    else:
        qs = qs.filter(owner__isnull=True)
    qs.update(status='RETIRED', is_active=False)
    template.status = 'PUBLISHED'
    template.is_active = True
    template.published_at = timezone.now()
    template.published_by = user if getattr(user, 'is_authenticated', False) else None
    template.save(update_fields=['status', 'is_active', 'published_at', 'published_by', 'updated_at'])
    return template


@transaction.atomic
def clone_template_version(template: RadiologyReportTemplate, *, user=None, name=None, personal=False):
    """Next draft version, or a personal variant owned by ``user``."""
    owner = user if personal else template.owner
    scope = 'PERSONAL' if personal else template.scope
    name = name or template.name
    latest = RadiologyReportTemplate.objects.filter(
        modality_type=template.modality_type, name=name,
    )
    if owner:
        latest = latest.filter(owner=owner)
    else:
        latest = latest.filter(owner__isnull=True)
    latest = latest.order_by('-version').first()
    next_version = (latest.version if latest else template.version) + 1
    return RadiologyReportTemplate.objects.create(
        modality_type=template.modality_type,
        name=name,
        variant_name=template.variant_name,
        body_part=template.body_part,
        visibility='PRIVATE' if personal else template.visibility,
        version=next_version,
        status='DRAFT',
        scope=scope,
        owner=owner,
        procedure=template.procedure,
        instructions=template.instructions,
        report_header=template.report_header,
        body_html=template.body_html,
        indication_boilerplate=template.indication_boilerplate,
        technique_boilerplate=template.technique_boilerplate,
        findings_boilerplate=template.findings_boilerplate,
        impression_boilerplate=template.impression_boilerplate,
        recommendations_boilerplate=template.recommendations_boilerplate,
        report_footer=template.report_footer,
        extra_fields=list(template.extra_fields or []),
        is_active=True,
        cloned_from=template,
    )


def resolve_report_templates(modality_type, user=None):
    """Published platform templates for modality plus the user's active personal ones."""
    platform = RadiologyReportTemplate.objects.filter(
        modality_type=modality_type, scope='PLATFORM', status='PUBLISHED', is_active=True, owner__isnull=True,
    ).order_by('name', '-version')
    personal = RadiologyReportTemplate.objects.none()
    if user and getattr(user, 'is_authenticated', False):
        personal = RadiologyReportTemplate.objects.filter(
            modality_type=modality_type, scope='PERSONAL', owner=user, is_active=True,
            status__in=['PUBLISHED', 'DRAFT'],
        ).order_by('name', '-version')
    return list(platform) + list(personal)


def freeze_report_configuration(report, *, signer=None) -> dict:
    from core.report_format import apply_format_and_signature

    template = report.template
    snapshot = {
        'frozen_at': timezone.now().isoformat(),
        'template': None,
        'indication': report.indication,
        'technique': report.technique,
        'findings': report.findings,
        'impression': report.impression,
        'recommendations': report.recommendations,
        'extra_values': dict(report.extra_values or {}),
        'addendum': report.addendum,
    }
    if template:
        snapshot['template'] = {
            'id': template.pk,
            'name': template.name,
            'version': template.version,
            'modality_type': template.modality_type,
            'scope': template.scope,
            'report_header': template.report_header,
            'report_footer': template.report_footer,
            'indication_boilerplate': template.indication_boilerplate,
            'technique_boilerplate': template.technique_boilerplate,
            'findings_boilerplate': template.findings_boilerplate,
            'impression_boilerplate': template.impression_boilerplate,
            'recommendations_boilerplate': template.recommendations_boilerplate,
            'body_html': template.body_html,
            'variant_name': template.variant_name,
            'body_part': template.body_part,
            'extra_fields': list(template.extra_fields or []),
        }
        report.template_version = template.version
    snapshot = apply_format_and_signature(
        snapshot, centre=report.centre, signer=signer, kind='radiology',
    )
    report.configuration_snapshot = snapshot
    report.save(update_fields=['template', 'template_version', 'configuration_snapshot', 'updated_at'])
    return snapshot
