from datetime import timedelta
from django.utils import timezone
from django.shortcuts import redirect
from django.conf import settings
from django.contrib import messages

def password_expired(user):
    """Check if the user's password has expired."""
    date_joined = getattr(user, "date_joined", None)
    if date_joined and ((timezone.now() - date_joined) > timedelta(days=30)):
        print("Yes")
        return True  

    expiry_days = getattr(settings, "PASSWORD_EXPIRY_DAYS", 90)
    return timezone.now() > (date_joined + timedelta(days=expiry_days))


def password_expiry_required(view_func):
    """Decorator to check if password expired before accessing a view."""
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated and password_expired(request.user):
            messages.success(request, "System is down. Kindly contact Insightzz Admin.")
            return redirect("logout")  # redirect to password reset page
        return view_func(request, *args, **kwargs)
    return _wrapped_view
