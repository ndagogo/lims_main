"""Password reset and optional MFA enrolment views."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.access import is_platform_admin
from core.mfa import generate_mfa_secret, verify_totp
from core.password_reset import request_password_reset, reset_password_with_token


@require_http_methods(['GET', 'POST'])
def password_reset_request(request):
    if request.method == 'POST':
        request_password_reset(request.POST.get('email'), request=request)
        messages.success(request, 'If that email is registered, a reset link has been sent.')
        return redirect('login')
    return render(request, 'core/password_reset_request.html')


@require_http_methods(['GET', 'POST'])
def password_reset_confirm(request, uidb64, token):
    error = None
    if request.method == 'POST':
        user, error = reset_password_with_token(uidb64, token, request.POST.get('password'))
        if user:
            messages.success(request, 'Password updated. You can sign in now.')
            return redirect('login')
    return render(request, 'core/password_reset_confirm.html', {'error': error, 'uidb64': uidb64, 'token': token})


@login_required
@require_http_methods(['GET', 'POST'])
def mfa_setup(request):
    if not is_platform_admin(request.user) and not request.user.is_superuser:
        messages.error(request, 'MFA setup is limited to platform administrators.')
        return redirect('dashboard')
    user = request.user
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'enable':
            if not user.mfa_secret:
                user.mfa_secret = generate_mfa_secret()
                user.save(update_fields=['mfa_secret'])
            token = request.POST.get('token')
            if verify_totp(user.mfa_secret, token):
                user.mfa_enabled = True
                user.save(update_fields=['mfa_enabled'])
                messages.success(request, 'MFA enabled.')
                return redirect('dashboard')
            messages.error(request, 'Invalid authenticator code.')
        elif action == 'disable':
            user.mfa_enabled = False
            user.mfa_secret = ''
            user.save(update_fields=['mfa_enabled', 'mfa_secret'])
            messages.success(request, 'MFA disabled.')
            return redirect('dashboard')
    if not user.mfa_secret:
        user.mfa_secret = generate_mfa_secret()
        user.save(update_fields=['mfa_secret'])
    return render(request, 'core/mfa_setup.html', {'secret': user.mfa_secret, 'enabled': user.mfa_enabled})
