from django.urls import path
from .views import *

urlpatterns = [
    path('dashboard/', APIDashboardView.as_view(), name='api_dashboard'),
    path('serve-file/', serve_file, name='serve-file'),

    path('fetch_history_data/', fetch_history_data, name='fetch_history_data'),
    path('download_history_data/', download_history_data, name='download_history_data'),
    path('get_run_details/', get_run_details, name='get_run_details'),
    path('download_run_json/', download_run_json, name='download_run_json'), 
    path('get_current_status/', get_current_status, name='get_current_status'),

    path('health_status/', health_status, name='health_status'),
    path('live-ip-camera', live_ip_camera, name='live-ip-camera'),
]