from django.views.generic import TemplateView
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
import os

from accounts.decorators import password_expiry_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from datetime import datetime
from .models import HEALTH_STATUS, DEFECTS_HISTORY
from urllib.parse import unquote, urlparse
from django.http import JsonResponse, HttpResponse, FileResponse

# Create your views here.

@method_decorator([login_required, password_expiry_required], name='dispatch')
class APIDashboardView(TemplateView):
    template_name = 'dashboards/api_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['message'] = 'Welcome to the API Dashboard'
        return context
    

def serve_file(request):
    # Get the full URL parameter
    file_param = request.GET.get('file')
    if not file_param:
        return HttpResponse("File parameter missing", status=400)

    # Remove query string if any (like ?t=...)
    parsed = urlparse(file_param)
    file_path = unquote(parsed.path)  # decode spaces and URL chars

    # Check if file exists
    if os.path.exists(file_path):
        try:
            # Determine MIME type based on extension
            ext = os.path.splitext(file_path)[1].lower()
            content_type = 'image/jpeg' if ext in ['.jpg', '.jpeg'] else 'application/octet-stream'
            return FileResponse(open(file_path, 'rb'), content_type=content_type)
        except Exception as e:
            return HttpResponse(f"Error reading file: {str(e)}", status=500)
    else:
        # Return a placeholder response instead of 404
        return HttpResponse(f"Image file not found on this server: {os.path.basename(file_path)}", 
                        status=404, content_type='text/plain')


# @csrf_exempt
# def fetch_history_data(request):
#     start_date = request.GET.get('start_date')
#     end_date = request.GET.get('end_date')
    
#     try:
#         start_dt = datetime.strptime(start_date, "%Y-%m-%d")
#         end_dt = datetime.strptime(end_date, "%Y-%m-%d")
#         print("Start Date:", start_dt)
#         print("End Date:", end_dt)      
#     except:
#         return JsonResponse({'error': 'Invalid date format'}, status=400)
  
#     queryset = DEFECTS_HISTORY.objects.filter(
#         create_time__date__gte=start_dt,
#         create_time__date__lte=end_dt,
#     ).order_by("create_time")

#     print("filtered queryset count:", queryset.count())


#     results = []
#     for idx, row in enumerate(queryset, start=1):
#         results.append({
#             "sno": idx,
#             "defect_name": row.defect_name,
#             "defect_size": row.defect_size,
#             "cycle_uid": row.cycle_uid,
#             "img_path": row.img_path,
#             "view_image": f"/api/serve-file?file={row.img_path}" if row.img_path else None,
#             'create_time': row.create_time.strftime("%d-%m-%Y %I:%M%p") if row.create_time else None,
#         })

#     return JsonResponse({
#         "data": results,
#     })


@csrf_exempt
def fetch_history_data(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    page = int(request.GET.get('page', 1))
    per_page = 10  # records per page
    
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except:
        return JsonResponse({'error': 'Invalid date format'}, status=400)
  
    queryset = DEFECTS_HISTORY.objects.filter(
        create_time__date__gte=start_dt,
        create_time__date__lte=end_dt,
    ).order_by("create_time")

    total = queryset.count()
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_queryset = queryset[start_idx:end_idx]

    results = []
    for idx, row in enumerate(paginated_queryset, start=start_idx + 1):
        results.append({
            "sno": idx,
            "defect_name": row.defect_name,
            "defect_size": row.defect_size,
            "cycle_uid": row.cycle_uid,
            "img_path": row.img_path,
            "view_image": f"/api/serve-file?file={row.img_path}" if row.img_path else None,
            'create_time': row.create_time.strftime("%d-%m-%Y %I:%M%p") if row.create_time else None,
        })

    return JsonResponse({
        "data": results,
        "total": total,           # Total records count
        "page": page,             # Current page
        "per_page": per_page,     # Records per page
    })


@csrf_exempt
def last_ten_entries(request):
    """
    API endpoint to fetch the last 10 defect history entries
    ordered by most recent first
    """
    try:
        queryset = DEFECTS_HISTORY.objects.all().order_by("-create_time")[:10]
        
        results = []
        for idx, row in enumerate(queryset, start=1):
            results.append({
                "sno": idx,
                "defect_name": row.defect_name,
                "defect_size": row.defect_size,
                "cycle_uid": row.cycle_uid,
                "img_path": row.img_path,
                "view_image": f"/api/serve-file?file={row.img_path}" if row.img_path else None,
                'create_time': row.create_time.strftime("%d-%m-%Y %I:%M%p") if row.create_time else None,
            })

            # print("Fetched entry:", results)  
        
        return JsonResponse({
            "data": results,
            "count": len(results),  
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
