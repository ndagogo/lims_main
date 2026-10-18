from django.contrib import admin

from .models import PhysicianInvite, PortalAccessToken


@admin.register(PortalAccessToken)
class PortalAccessTokenAdmin(admin.ModelAdmin):
    list_display = ('portal', 'token', 'patient', 'doctor', 'organisation', 'expires_at', 'is_active')
    list_filter = ('portal', 'is_active')
    search_fields = ('token', 'patient__uhid', 'doctor__doctor_code')
    readonly_fields = ('token',)


@admin.register(PhysicianInvite)
class PhysicianInviteAdmin(admin.ModelAdmin):
    list_display = ('doctor', 'email', 'invite_token', 'expires_at', 'accepted_at')
    search_fields = ('email', 'doctor__doctor_code', 'invite_token')
    readonly_fields = ('invite_token',)
