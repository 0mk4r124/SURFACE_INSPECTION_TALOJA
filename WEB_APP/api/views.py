import json
import os
import shutil
import csv

from django.contrib.auth.decorators import login_required
from django.db.models import F, Func, Max, Subquery, OuterRef, Value, DateTimeField, CharField
from django.http import JsonResponse, HttpResponse, FileResponse
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt
from django.db.models.functions import Greatest, Concat
from django.utils.timezone import is_aware, make_naive, make_aware
from django.contrib import messages

from api.models import *
from accounts.decorators import password_expiry_required
from datetime import datetime
from urllib.parse import unquote, urlparse

# Create your views here.

def to_naive(dt):
    if dt is None:
        return None
    return make_naive(dt) if is_aware(dt) else dt

def max_datetime(a, b):
    a = to_naive(a)
    b = to_naive(b)

    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return a if a > b else b

def parse_last_ping(value):
    if not value:
        return None
    try:
        dt = make_naive(value) if is_aware(value) else value
        return dt.strftime("%d-%m-%Y %I:%M%p")
    except ValueError:
        return None
    
@method_decorator([login_required], name='dispatch') # password_expiry_required
class APIDashboardView(TemplateView):
    template_name = 'dashboards/api_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['message'] = 'Welcome to the API Dashboard'
        return context
    
def serve_file(request):
    file_param = request.GET.get('file')
    if not file_param:
        return HttpResponse("File parameter missing", status=400)

    # Direct decode (NO urlparse)
    file_path = unquote(file_param)

    # Fix malformed paths
    file_path = file_path.replace("c://", "c:/")
    file_path = file_path.replace("//", "/")
    file_path = os.path.normpath(file_path)

    print("FINAL PATH:", file_path)  # debug

    if os.path.exists(file_path):
        try:
            ext = os.path.splitext(file_path)[1].lower()
            content_type = 'image/jpeg' if ext in ['.jpg', '.jpeg'] else 'application/octet-stream'
            return FileResponse(open(file_path, 'rb'), content_type=content_type)
        except Exception as e:
            return HttpResponse(f"Error reading file: {str(e)}", status=500)
    else:
        return HttpResponse(
            f"Image file not found on this server: {file_path}",
            status=404,
            content_type='text/plain'
        )
    
def live_ip_camera(request):
    try: 
        camera_paths = [
            "/home/omkar/INSIGHTZZ/PROJECTS/TALOJA/SURFACE_INSPECTION_TALOJA/WEB_APP/static/img/nocam.jpg",
        ]
        
        cameras = []
        for i, path in enumerate(camera_paths, start=1):
            if os.path.exists(path):
                cameras.append({
                    "camera_id": i,
                    "camera_name": f"Camera {i}",
                    "image_url": f'/api/serve-file?file={path}&t={timezone.now().timestamp()}',
                    "has_image": True,
                    "status": "online"
                })
            else:
                cameras.append({
                    "camera_id": i,
                    "camera_name": f"Camera {i}",
                    "image_url": "",
                    "has_image": False,
                    "status": "no_image"
                })
        
        return JsonResponse({
            "status": "success", 
            "cameras": cameras,
            "timestamp": timezone.now().isoformat()
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    
@csrf_exempt
def fetch_history_data(request):

    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 10))

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    coil_id = request.GET.get("coil_id")

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except Exception:
        return JsonResponse(
            {"error": "Invalid date range"},
            status=400
        )

    queryset = MILL_RUNS.objects.filter(
        created_at__date__gte=start_dt,
        created_at__date__lte=end_dt
    ).order_by("-created_at")

    if coil_id:
        queryset = queryset.filter(
            coil_id__icontains=coil_id
        )

    total = queryset.count()

    start = (page - 1) * per_page
    end = start + per_page

    queryset = queryset[start:end]

    results = []

    for idx, run in enumerate(queryset, start=start+1):

        defects = DEFECTS_HISTORY.objects.filter(
            cycle_uid=run.cycle_uid
        )

        total_frames = defects.count()

        total_defects = 0
        total_defect_frames = 0

        defect_summary = {}

        for row in defects:

            try:

                if not row.defect_summary:
                    continue

                summary = json.loads(row.defect_summary)

                frame_has_defect = False

                for defect_name, count in summary.items():

                    total_defects += count

                    defect_summary[defect_name] = (
                        defect_summary.get(defect_name, 0)
                        + count
                    )

                    if count > 0:
                        frame_has_defect = True

                if frame_has_defect:
                    total_defect_frames += 1

            except:
                pass

        summary_text = ", ".join(
            [
                f"{k}:{v}"
                for k, v in defect_summary.items()
            ]
        )

        results.append({

            "sno": idx,

            "datetime":
                run.created_at.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),

            "coil_id":
                run.coil_id,

            "cycle_uid":
                run.cycle_uid,

            "total_defects":
                total_defects,

            "total_frames":
                total_frames,

            "total_defect_frames":
                total_defect_frames,

            "defect_summary":
                summary_text[:60] +
                ("..." if len(summary_text) > 60 else ""),

            "json_path":
                getattr(run, "json_path", None)
        })

    return JsonResponse({

        "data": results,

        "total": total,

        "page": page,

        "per_page": per_page
    })
    
@csrf_exempt
def get_run_details(request):

    cycle_uid = request.GET.get("cycle_uid")

    if not cycle_uid:
        return JsonResponse(
            {"error": "cycle_uid required"},
            status=400
        )

    defect_row = DEFECTS_HISTORY.objects.filter(
        cycle_uid=cycle_uid
    ).first()

    if not defect_row:
        return JsonResponse(
            {"error": "data not found"},
            status=404
        )

    try:

        with open(defect_row.json_path, "r") as fp:
            data = json.load(fp)

        return JsonResponse(data)

    except Exception as e:

        return JsonResponse(
            {"error": str(e)},
            status=500
        )
        
@csrf_exempt
def download_history_data(request):

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    try:

        start_dt = datetime.strptime(
            start_date,
            "%Y-%m-%d"
        )

        end_dt = datetime.strptime(
            end_date,
            "%Y-%m-%d"
        )

    except:

        return HttpResponse(
            "Invalid Date Range",
            status=400
        )

    queryset = MILL_RUNS.objects.filter(
        created_at__date__gte=start_dt,
        created_at__date__lte=end_dt
    ).order_by("-created_at")

    response = HttpResponse(
        content_type="text/csv"
    )

    response[
        "Content-Disposition"
    ] = 'attachment; filename="mill_history.csv"'

    writer = csv.writer(response)

    writer.writerow([

        "DATETIME",
        "COIL_ID",
        "CYCLE_UID",
        "TOTAL_FRAMES",
        "TOTAL_DEFECT_FRAMES",
        "TOTAL_DEFECTS",
        "DEFECT_SUMMARY"

    ])

    for run in queryset:

        defects = DEFECTS_HISTORY.objects.filter(
            cycle_uid=run.cycle_uid
        )

        total_frames = defects.count()

        total_defect_frames = 0

        total_defects = 0

        summary = {}

        for row in defects:

            try:

                defect_data = json.loads(
                    row.defect_summary
                )

                frame_has_defect = False

                for defect_name, count in defect_data.items():

                    summary[defect_name] = (
                        summary.get(defect_name, 0)
                        + count
                    )

                    total_defects += count

                    if count > 0:
                        frame_has_defect = True

                if frame_has_defect:
                    total_defect_frames += 1

            except:
                pass

        writer.writerow([

            run.created_at.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

            run.coil_id,

            run.cycle_uid,

            total_frames,

            total_defect_frames,

            total_defects,

            json.dumps(summary)

        ])

    return response

@csrf_exempt
def health_status(request):
    queryset = (
        HEALTH_STATUS.objects.filter()
        .values()
        .order_by("ip")
    )
    results = {}
    devices = {}
    for idx, row in enumerate(queryset, start=1):
        if row["location"] not in list(devices.keys()):
            devices[row["location"]] = {}
            if row["device_type"] not in devices[row["location"]]:
                devices[row["location"]][row["device_type"]] = [row["ip"]]
            else:
                devices[row["location"]][row["device_type"]].append(row["ip"])
        else:
            if row["device_type"] not in devices[row["location"]]:
                devices[row["location"]][row["device_type"]] = [row["ip"]]
            else:
                devices[row["location"]][row["device_type"]].append(row["ip"])

        results[f'{row["location"]} {row["device_type"]} {len(devices[row["location"]][row["device_type"]])}'] = {
            "type": row.get("device_type"),
            "ip": row.get("ip"),
            "cam_serial": row.get("camera_serial_number") or None,
            "status": row.get("status"),
            "last_ping": parse_last_ping(row.get("last_ping")),
            "top": row.get("top"),
            "left": row.get("left"),
        }

    return JsonResponse({
        "data": results,
    })

@csrf_exempt
def get_current_status(request):

    data = {
        "status": "processing",
        "cycle_uid": "ROLL_20260605190003",
        "coil_id": "COIL_123",

        "total_defects": 8,
        "total_frames": 20,
        "total_defective_frames": 4,

        "result": [

            {
                "r": 1,
                "l": "346951",
                "s": "LOW",
                "t": {},
                "img": "/home/omkar/INSIGHTZZ/PROJECTS/TALOJA/SURFACE_INSPECTION_TALOJA/WEB_APP/static/img/nocam.jpg"
            },

            {
                "r": 2,
                "l": "347946",
                "s": "MEDIUM",
                "t": {"V": 3},
                "img": "/home/omkar/INSIGHTZZ/PROJECTS/TALOJA/SURFACE_INSPECTION_TALOJA/WEB_APP/static/img/nocam.jpg"
            },

            {
                "r": 3,
                "l": "348100",
                "s": "HIGH",
                "t": {
                    "V": 2,
                    "D": 4
                },
                "img": "/home/omkar/INSIGHTZZ/PROJECTS/TALOJA/SURFACE_INSPECTION_TALOJA/WEB_APP/static/img/nocam.jpg"
            },

            {
                "r": 4,
                "l": "349000",
                "s": "LOW",
                "t": {
                    "D": 1
                },
                "img": "/home/omkar/INSIGHTZZ/PROJECTS/TALOJA/SURFACE_INSPECTION_TALOJA/WEB_APP/static/img/nocam.jpg"
            }
        ]
    }

    return JsonResponse(data)
