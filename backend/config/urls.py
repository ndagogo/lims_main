from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView
from core.api import CentreViewSet, UserCentreAccessViewSet, AuditEventViewSet, login_view, me_view
from core.auth_views import password_reset_request, password_reset_confirm, mfa_setup
from core.views import (
    dashboard, patient_registration, patient_timeline, patient_phone_lookup, patient_demographics,
    referring_doctor_remap,
    accounts_workspace, accounts_reports, accounts_export, accounts_refunds, accounts_bill_edit,
    payment_receipt, laboratory_workspace, laboratory_tat, sample_timeline,
    phlebotomy_workspace, radiology_workspace, radiology_templates, radiology_template_builder, healthz,
    login_page, logout_page, password_change, account_profile, account_signature, download_patient_document, download_settlement_proof,
    user_management, role_management, catalog_hub, catalog_tariffs, catalog_tariff_detail,
    catalog_tests, catalog_service_groups, catalog_doctors, catalog_imaging, catalog_centres,
    catalog_clients,
)
from core.platform_views import (
    platform_analyzers, platform_analyzer_mappings, platform_analyzer_messages, platform_audit,
    platform_bill_amendments, platform_gateways, platform_hub, platform_inventory_skus,
    platform_modalities, platform_notification_outbox, platform_notification_templates,
    platform_pacs_failures, platform_physician_invites,
    platform_qc_materials, platform_qc_runs, platform_radiology_accept,
    platform_radiology_template_builder, platform_radiology_templates, platform_refunds,
    platform_result_template_builder, platform_result_templates, platform_sample_codes,
    platform_tat_rules, platform_workflows,
)
from patients.api import PatientViewSet, VisitViewSet
from laboratory.api import (
    TestViewSet, TariffViewSet, TestPriceViewSet, LabOrderViewSet, SampleViewSet, SampleBatchViewSet,
    TestParameterViewSet, ResultTemplateViewSet, LabResultViewSet, SampleActivityViewSet,
    QcMaterialViewSet, QcRunViewSet, TatDashboardView, TatRuleViewSet, TatPauseReasonViewSet,
    TatEscalationViewSet,
)
from billing.api import (
    InvoiceViewSet, InvoiceItemViewSet, PaymentViewSet, DiscountRequestViewSet,
    BillAmendmentViewSet, RefundViewSet, ReleaseEligibilityView,
)
from radiology.api import (
    ImagingModalityViewSet, ImagingOrderViewSet, RadiologyExamViewSet,
    RadiologyReportViewSet, RadiologyReportTemplateViewSet,
)
from integrations.api import AnalyzerViewSet, AnalyzerTestMappingViewSet, AnalyzerMessageViewSet
from integrations.views import integration_review, integration_review_action
from referrals.api import ReferralDoctorViewSet, ReferralAttributionViewSet, CommissionLedgerViewSet, WithdrawalRequestViewSet
from communications.api import NotificationOutboxViewSet, NotificationTemplateViewSet
from communications.reports import OperationsSummaryView
from reporting.views import management_dashboard, report_catalogue, report_export
from patients.api import PatientDocumentViewSet
from inventory.views import inventory_workspace
from laboratory.labels import sample_label, sample_barcode_svg
router=DefaultRouter()
router.register('centres',CentreViewSet,basename='centre'); router.register('centre-access',UserCentreAccessViewSet,basename='centre-access'); router.register('audit-events',AuditEventViewSet,basename='audit-event')
router.register('patients',PatientViewSet,basename='patient'); router.register('visits',VisitViewSet,basename='visit')
router.register('tests',TestViewSet,basename='test'); router.register('sample-batches',SampleBatchViewSet,basename='sample-batch'); router.register('test-parameters',TestParameterViewSet,basename='test-parameter'); router.register('result-templates',ResultTemplateViewSet,basename='result-template'); router.register('lab-results',LabResultViewSet,basename='lab-result'); router.register('sample-activities',SampleActivityViewSet,basename='sample-activity'); router.register('tariffs',TariffViewSet,basename='tariff'); router.register('test-prices',TestPriceViewSet,basename='test-price'); router.register('lab-orders',LabOrderViewSet,basename='lab-order'); router.register('samples',SampleViewSet,basename='sample')
router.register('qc-materials',QcMaterialViewSet,basename='qc-material'); router.register('qc-runs',QcRunViewSet,basename='qc-run')
router.register('tat-rules',TatRuleViewSet,basename='tat-rule'); router.register('tat-pause-reasons',TatPauseReasonViewSet,basename='tat-pause-reason'); router.register('tat-escalations',TatEscalationViewSet,basename='tat-escalation')
router.register('analyzers',AnalyzerViewSet,basename='analyzer'); router.register('analyzer-test-mappings',AnalyzerTestMappingViewSet,basename='analyzer-test-mapping'); router.register('analyzer-messages',AnalyzerMessageViewSet,basename='analyzer-message')
router.register('referral-doctors',ReferralDoctorViewSet,basename='referral-doctor'); router.register('referral-attributions',ReferralAttributionViewSet,basename='referral-attribution'); router.register('commission-ledger',CommissionLedgerViewSet,basename='commission-ledger'); router.register('withdrawal-requests',WithdrawalRequestViewSet,basename='withdrawal-request')
router.register('notification-outbox',NotificationOutboxViewSet,basename='notification-outbox'); router.register('notification-templates',NotificationTemplateViewSet,basename='notification-template')
router.register('imaging-modalities',ImagingModalityViewSet,basename='imaging-modality'); router.register('imaging-orders',ImagingOrderViewSet,basename='imaging-order'); router.register('radiology-exams',RadiologyExamViewSet,basename='radiology-exam'); router.register('radiology-reports',RadiologyReportViewSet,basename='radiology-report'); router.register('radiology-report-templates',RadiologyReportTemplateViewSet,basename='radiology-report-template')
router.register('invoices',InvoiceViewSet,basename='invoice'); router.register('invoice-items',InvoiceItemViewSet,basename='invoice-item'); router.register('payments',PaymentViewSet,basename='payment'); router.register('discount-requests',DiscountRequestViewSet,basename='discount-request')
router.register('bill-amendments',BillAmendmentViewSet,basename='bill-amendment'); router.register('refunds',RefundViewSet,basename='refund')
router.register('patient-documents',PatientDocumentViewSet,basename='patient-document')
urlpatterns=[
    path('healthz', healthz, name='healthz'),
    path('login/', login_page, name='login'),
    path('logout/', logout_page, name='logout'),
    path('account/password/', password_change, name='password-change'),
    path('account/profile/', account_profile, name='account-profile'),
    path('account/signature/', account_signature, name='account-signature'),
    path('account/password-reset/', password_reset_request, name='password-reset'),
    path('account/password-reset/confirm/<uidb64>/<token>/', password_reset_confirm, name='password-reset-confirm'),
    path('account/mfa/', mfa_setup, name='mfa-setup'),
    path('', dashboard, name='dashboard'),
    path('users/', user_management, name='user-management'),
    path('roles/', role_management, name='role-management'),
    path('catalog/', catalog_hub, name='catalog-hub'),
    path('catalog/tariffs/', catalog_tariffs, name='catalog-tariffs'),
    path('catalog/tariffs/<int:pk>/', catalog_tariff_detail, name='catalog-tariff-detail'),
    path('catalog/tests/', catalog_tests, name='catalog-tests'),
    path('catalog/service-groups/', catalog_service_groups, name='catalog-service-groups'),
    path('catalog/doctors/', catalog_doctors, name='catalog-doctors'),
    path('catalog/imaging/', catalog_imaging, name='catalog-imaging'),
    path('catalog/centres/', catalog_centres, name='catalog-centres'),
    path('catalog/clients/', catalog_clients, name='catalog-clients'),
    path('platform/', platform_hub, name='platform-hub'),
    path('platform/audit/', platform_audit, name='platform-audit'),
    path('platform/workflows/', platform_workflows, name='platform-workflows'),
    path('platform/qc-materials/', platform_qc_materials, name='platform-qc-materials'),
    path('platform/qc-runs/', platform_qc_runs, name='platform-qc-runs'),
    path('platform/result-templates/', platform_result_templates, name='platform-result-templates'),
    path('platform/result-templates/<int:pk>/', platform_result_template_builder, name='platform-result-template-builder'),
    path('platform/radiology-templates/', platform_radiology_templates, name='platform-radiology-templates'),
    path('platform/radiology-accept/', platform_radiology_accept, name='platform-radiology-accept'),
    path('platform/radiology-templates/<int:pk>/', platform_radiology_template_builder, name='platform-radiology-template-builder'),
    path('platform/analyzers/', platform_analyzers, name='platform-analyzers'),
    path('platform/analyzer-mappings/', platform_analyzer_mappings, name='platform-analyzer-mappings'),
    path('platform/analyzer-messages/', platform_analyzer_messages, name='platform-analyzer-messages'),
    path('platform/gateways/', platform_gateways, name='platform-gateways'),
    path('platform/pacs-failures/', platform_pacs_failures, name='platform-pacs-failures'),
    path('platform/modalities/', platform_modalities, name='platform-modalities'),
    path('platform/sample-codes/', platform_sample_codes, name='platform-sample-codes'),
    path('platform/bill-amendments/', platform_bill_amendments, name='platform-bill-amendments'),
    path('platform/refunds/', platform_refunds, name='platform-refunds'),
    path('platform/notification-templates/', platform_notification_templates, name='platform-notification-templates'),
    path('platform/notification-outbox/', platform_notification_outbox, name='platform-notification-outbox'),
    path('platform/physician-invites/', platform_physician_invites, name='platform-physician-invites'),
    path('platform/inventory-skus/', platform_inventory_skus, name='platform-inventory-skus'),
    path('patients/register/', patient_registration, name='patient-registration'),
    path('patients/register/phone-lookup/', patient_phone_lookup, name='patient-phone-lookup'),
    path('patients/demographics/', patient_demographics, name='patient-demographics'),
    path('patients/referrals/remap/', referring_doctor_remap, name='referring-doctor-remap'),
    path('patients/<uuid:pk>/timeline/', patient_timeline, name='patient-timeline'),
    path('patients/documents/<int:pk>/download/', download_patient_document, name='patient-document-download'),
    path('referrals/withdrawals/<uuid:pk>/proof/', download_settlement_proof, name='settlement-proof-download'),
    path('accounts/', accounts_workspace, name='accounts-workspace'),
    path('accounts/reports/', accounts_reports, name='accounts-reports'),
    path('accounts/export/', accounts_export, name='accounts-export'),
    path('accounts/refunds/', accounts_refunds, name='accounts-refunds'),
    path('accounts/bill-edit/', accounts_bill_edit, name='accounts-bill-edit'),
    path('accounts/receipts/<int:pk>/', payment_receipt, name='payment-receipt'),
    path('reporting/management/', management_dashboard, name='management-dashboard'),
    path('reporting/catalogue/', report_catalogue, name='report-catalogue'),
    path('reporting/export/', report_export, name='report-export'),
    path('laboratory/', laboratory_workspace, name='laboratory-workspace'),
    path('laboratory/tat/', laboratory_tat, name='laboratory-tat'),
    path('phlebotomy/', phlebotomy_workspace, name='phlebotomy-workspace'),
    path('samples/<int:pk>/label/', sample_label, name='sample-label'),
    path('samples/<int:pk>/barcode.svg', sample_barcode_svg, name='sample-barcode-svg'),
    path('samples/<int:pk>/timeline/', sample_timeline, name='sample-timeline'),
    path('platform/tat-rules/', platform_tat_rules, name='platform-tat-rules'),
    path('radiology/', radiology_workspace, name='radiology-workspace'),
    path('radiology/templates/', radiology_templates, name='radiology-templates'),
    path('radiology/templates/<int:pk>/', radiology_template_builder, name='radiology-template-builder'),
    path('inventory/', inventory_workspace, name='inventory-workspace'),
    path('integrations/review/', integration_review, name='integration-review'),
    path('integrations/review/<uuid:pk>/', integration_review_action, name='integration-review-action'),
    path('portals/', include('portals.urls')),
    path('admin/', admin.site.urls),
    path('api/auth/login/', login_view, name='api-login'),
    path('api/auth/me/', me_view, name='api-me'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('api/auth/token/verify/', TokenVerifyView.as_view(), name='token-verify'),
    path('api/reports/operations-summary/', OperationsSummaryView.as_view(), name='operations-summary'),
    path('api/reports/tat/', TatDashboardView.as_view(), name='tat-dashboard'),
    path('api/billing/release-eligibility/', ReleaseEligibilityView.as_view(), name='release-eligibility'),
    path('api/', include(router.urls)),
    path('api-auth/', include('rest_framework.urls')),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
