from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView
from core.api import CentreViewSet, UserCentreAccessViewSet, AuditEventViewSet, login_view, me_view
from core.views import (
    dashboard, patient_registration, patient_timeline, accounts_workspace, accounts_reports, accounts_export,
    payment_receipt, laboratory_workspace, phlebotomy_workspace, radiology_workspace, healthz,
    login_page, logout_page, password_change, download_patient_document, download_settlement_proof,
    user_management, role_management, catalog_hub, catalog_tariffs, catalog_tariff_detail,
    catalog_tests, catalog_service_groups, catalog_doctors, catalog_imaging, catalog_centres,
    catalog_clients,
)
from patients.api import PatientViewSet, VisitViewSet
from laboratory.api import TestViewSet, TariffViewSet, TestPriceViewSet, LabOrderViewSet, SampleViewSet, SampleBatchViewSet, TestParameterViewSet, ResultTemplateViewSet, LabResultViewSet, SampleActivityViewSet
from billing.api import InvoiceViewSet, InvoiceItemViewSet, PaymentViewSet, DiscountRequestViewSet
from radiology.api import ImagingModalityViewSet, ImagingOrderViewSet, RadiologyExamViewSet, RadiologyReportViewSet
from integrations.api import AnalyzerViewSet, AnalyzerTestMappingViewSet, AnalyzerMessageViewSet
from referrals.api import ReferralDoctorViewSet, ReferralAttributionViewSet, CommissionLedgerViewSet, WithdrawalRequestViewSet
from communications.api import NotificationOutboxViewSet, NotificationTemplateViewSet
from communications.reports import OperationsSummaryView
from patients.api import PatientDocumentViewSet
router=DefaultRouter()
router.register('centres',CentreViewSet,basename='centre'); router.register('centre-access',UserCentreAccessViewSet,basename='centre-access'); router.register('audit-events',AuditEventViewSet,basename='audit-event')
router.register('patients',PatientViewSet,basename='patient'); router.register('visits',VisitViewSet,basename='visit')
router.register('tests',TestViewSet,basename='test'); router.register('sample-batches',SampleBatchViewSet,basename='sample-batch'); router.register('test-parameters',TestParameterViewSet,basename='test-parameter'); router.register('result-templates',ResultTemplateViewSet,basename='result-template'); router.register('lab-results',LabResultViewSet,basename='lab-result'); router.register('sample-activities',SampleActivityViewSet,basename='sample-activity'); router.register('tariffs',TariffViewSet,basename='tariff'); router.register('test-prices',TestPriceViewSet,basename='test-price'); router.register('lab-orders',LabOrderViewSet,basename='lab-order'); router.register('samples',SampleViewSet,basename='sample')
router.register('analyzers',AnalyzerViewSet,basename='analyzer'); router.register('analyzer-test-mappings',AnalyzerTestMappingViewSet,basename='analyzer-test-mapping'); router.register('analyzer-messages',AnalyzerMessageViewSet,basename='analyzer-message')
router.register('referral-doctors',ReferralDoctorViewSet,basename='referral-doctor'); router.register('referral-attributions',ReferralAttributionViewSet,basename='referral-attribution'); router.register('commission-ledger',CommissionLedgerViewSet,basename='commission-ledger'); router.register('withdrawal-requests',WithdrawalRequestViewSet,basename='withdrawal-request')
router.register('notification-outbox',NotificationOutboxViewSet,basename='notification-outbox'); router.register('notification-templates',NotificationTemplateViewSet,basename='notification-template')
router.register('imaging-modalities',ImagingModalityViewSet,basename='imaging-modality'); router.register('imaging-orders',ImagingOrderViewSet,basename='imaging-order'); router.register('radiology-exams',RadiologyExamViewSet,basename='radiology-exam'); router.register('radiology-reports',RadiologyReportViewSet,basename='radiology-report')
router.register('invoices',InvoiceViewSet,basename='invoice'); router.register('invoice-items',InvoiceItemViewSet,basename='invoice-item'); router.register('payments',PaymentViewSet,basename='payment'); router.register('discount-requests',DiscountRequestViewSet,basename='discount-request'); router.register('patient-documents',PatientDocumentViewSet,basename='patient-document')
urlpatterns=[
    path('healthz', healthz, name='healthz'),
    path('login/', login_page, name='login'),
    path('logout/', logout_page, name='logout'),
    path('account/password/', password_change, name='password-change'),
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
    path('patients/register/', patient_registration, name='patient-registration'),
    path('patients/<uuid:pk>/timeline/', patient_timeline, name='patient-timeline'),
    path('patients/documents/<int:pk>/download/', download_patient_document, name='patient-document-download'),
    path('referrals/withdrawals/<uuid:pk>/proof/', download_settlement_proof, name='settlement-proof-download'),
    path('accounts/', accounts_workspace, name='accounts-workspace'),
    path('accounts/reports/', accounts_reports, name='accounts-reports'),
    path('accounts/export/', accounts_export, name='accounts-export'),
    path('accounts/receipts/<int:pk>/', payment_receipt, name='payment-receipt'),
    path('laboratory/', laboratory_workspace, name='laboratory-workspace'),
    path('phlebotomy/', phlebotomy_workspace, name='phlebotomy-workspace'),
    path('radiology/', radiology_workspace, name='radiology-workspace'),
    path('admin/', admin.site.urls),
    path('api/auth/login/', login_view, name='api-login'),
    path('api/auth/me/', me_view, name='api-me'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('api/auth/token/verify/', TokenVerifyView.as_view(), name='token-verify'),
    path('api/reports/operations-summary/', OperationsSummaryView.as_view(), name='operations-summary'),
    path('api/', include(router.urls)),
    path('api-auth/', include('rest_framework.urls')),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
