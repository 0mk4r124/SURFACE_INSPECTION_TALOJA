from django.urls import path
from .views import *

urlpatterns = [
    path('dashboard/', APIDashboardView.as_view(), name='api_dashboard'),
    path('fetch_history_data/', fetch_history_data, name='fetch_history_data'),
    path('serve-file/', serve_file, name='serve_file'),
    path('last-10-entries/', last_ten_entries, name='last_10_entries'),

]