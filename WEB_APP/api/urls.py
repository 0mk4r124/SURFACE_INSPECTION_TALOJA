from django.urls import path
from .views import APIDashboardView

urlpatterns = [
    path('dashboard/', APIDashboardView.as_view(), name='api_dashboard'),
]