from django.contrib.auth.views import LoginView, LogoutView
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import View
from django.conf import settings

from accounts.decorators import password_expiry_required

# Create your views here.

class CustomLoginView(LoginView):
    template_name = 'login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        email = form.cleaned_data.get('username')  # Form field 'username' contains email
        password = form.cleaned_data.get('password')
        user = authenticate(self.request, username=email, password=password)
        if user is not None:
            login(self.request, user)
            next_url = self.request.POST.get('next', self.request.GET.get('next', ''))
            return redirect(next_url or reverse_lazy('dashboard'))
        messages.error(self.request, "Invalid email or password.")
        return self.form_invalid(form)

    def form_invalid(self, form):
        messages.error(self.request, "Invalid email or password.")
        return super().form_invalid(form)

class CustomLogoutView(View):
    redirect_url = settings.LOGOUT_REDIRECT_URL or '/login/'

    def get(self, request):
        logout(request)
        messages.success(self.request, "You have been logged out.")
        return redirect(self.redirect_url)

@method_decorator([login_required], name='dispatch') # password_expiry_required
class DashboardView(LoginRequiredMixin, TemplateView):
    def get_template_names(self):
        user = self.request.user
        if user.is_superuser:
            return ['dashboards/api_dashboard.html']
            # return ['dashboards/admin_dashboard.html']
        elif user.is_staff:
            return ['dashboards/staff_dashboard.html']
        else:
            return ['dashboards/employee_dashboard.html']