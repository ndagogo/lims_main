from django.urls import path

from portals import views

urlpatterns = [
    path('patient/login/', views.patient_login, name='portal-patient-login'),
    path('patient/access/<str:access_token>/', views.patient_token_access, name='portal-patient-access'),
    path('patient/', views.patient_home, name='portal-patient-home'),
    path('patient/preferences/', views.patient_preferences, name='portal-patient-preferences'),
    path('patient/requests/', views.patient_request, name='portal-patient-requests'),
    path('patient/results/<uuid:result_id>/', views.patient_result, name='portal-patient-result'),
    path('patient/results/<uuid:result_id>/pdf/', views.patient_result_pdf, name='portal-patient-result-pdf'),
    path('patient/imaging/<uuid:report_id>/', views.patient_imaging, name='portal-patient-imaging'),
    path('patient/imaging/<uuid:report_id>/pdf/', views.patient_imaging_pdf, name='portal-patient-imaging-pdf'),
    path('physician/login/', views.physician_login, name='portal-physician-login'),
    path('physician/invite/<str:invite_token>/', views.physician_invite_accept, name='portal-physician-invite'),
    path('physician/', views.physician_home, name='portal-physician-home'),
    path('corporate/login/', views.corporate_login, name='portal-corporate-login'),
    path('corporate/', views.corporate_home, name='portal-corporate-home'),
    path('logout/', views.portal_logout, name='portal-logout'),
]
