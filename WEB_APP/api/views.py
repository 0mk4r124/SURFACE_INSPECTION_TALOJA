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
DEFECT_LABELS = {
    "O": "Oil Stain",
    "W": "White Spot",
    "S": "Scoring",
    "D": "Dent",
    "C": "Scratch",
    "V": "V",          # keep for legacy rows written before the rename
}

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

    file_path = unquote(file_param)
    file_path = os.path.normpath(file_path)

    # print("REQUESTED:", file_path)
    # print("EXISTS:", os.path.exists(file_path))

    if os.path.exists(file_path):
        try:
            file_size = os.path.getsize(file_path)
            print("SIZE:", file_size)

            if file_size == 0:
                return HttpResponse("Empty file", status=404)

            ext = os.path.splitext(file_path)[1].lower()

            content_type_map = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png'
            }

            return FileResponse(
                open(file_path, 'rb'),
                content_type=content_type_map.get(ext, 'application/octet-stream')
            )

        except Exception as e:
            return HttpResponse(
                f"Error reading file: {str(e)}",
                status=500
            )

    return HttpResponse(
        f"File not found: {file_path}",
        status=404
    )
    
def live_ip_camera(request):
    try: 
        camera_paths = [
            # "/home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA/DEBUGS/TEMP_IMG/TALOJA/24521939_debug.jpg",
            # "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/DEBUGS/TEMP_IMG/TALOJA/24521939_debug.jpg",
            "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/DEBUGS/TEMP_IMG/TALOJA/24521939.jpg",
            # "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/DEBUGS/RAW_IMG/",
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

    page     = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 15))

    start_date = request.GET.get("start_date")
    end_date   = request.GET.get("end_date")
    coil_id    = request.GET.get("coil_id")

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date,   "%Y-%m-%d")
    except Exception:
        return JsonResponse({"error": "Invalid date range"}, status=400)

    # Primary source: DEFECTS_HISTORY by create_time
    # One row per cycle_uid (LOGIC.py upserts), contains defect_details JSON
    queryset = DEFECTS_HISTORY.objects.filter(
        create_time__date__gte=start_dt,
        create_time__date__lte=end_dt
    ).order_by("-create_time")

    if coil_id:
        queryset = queryset.filter(coil_id__icontains=coil_id)

    total   = queryset.count()
    start   = (page - 1) * per_page
    end_idx = start + per_page
    page_qs = queryset[start:end_idx]

    results = []

    for idx, row in enumerate(page_qs, start=start + 1):

        total_frames        = 0
        total_defects       = 0
        total_defect_frames = 0
        defect_summary      = {}

        if row.defect_details:
            try:
                # defect_details written by LOGIC.py as json.dumps(defect_summary)
                # keys: total_frames, total_defects, total_defective_frames,
                #       defect_frames, V, D, S, C, ...
                summary = json.loads(row.defect_details)
                total_frames = int(summary.get("total_frames", 0) or 0)
                total_defects = int(summary.get("total_defects", 0) or 0)
                total_defect_frames = int(
                    summary.get("total_defective_frames", summary.get("defect_frames", 0)) or 0
                )

                defect_summary = {}
                for key in DEFECT_LABELS:
                    val = int(summary.get(key, 0) or 0)
                    if val > 0:
                        defect_summary[key] = val

            except Exception:
                pass

        summary_text = ", ".join(
            f"{DEFECT_LABELS[k]}: {v}" for k, v in defect_summary.items()
        )

        results.append({
            "sno":               idx,
            "datetime":          row.create_time.strftime("%Y-%m-%d %H:%M:%S"),
            "coil_id":           row.coil_id,
            "cycle_uid":         row.cycle_uid,
            "total_defects":     total_defects,
            "total_frames":      total_frames,
            "total_defect_frames": total_defect_frames,
            "defect_summary":    summary_text,
        })

    return JsonResponse({
        "data":     results,
        "total":    total,
        "page":     page,
        "per_page": per_page,
    })
    
@csrf_exempt
def get_run_details(request):

    cycle_uid = request.GET.get("cycle_uid")

    if not cycle_uid:
        return JsonResponse({"error": "cycle_uid required"}, status=400)

    defect_row = DEFECTS_HISTORY.objects.filter(cycle_uid=cycle_uid).first()

    if not defect_row:
        return JsonResponse({"error": "No DB record found for this cycle."}, status=404)

    json_path = defect_row.json_path or ""

    if not json_path or not os.path.exists(json_path):
        return JsonResponse({
            "error": f"JSON file not available at: {json_path or '(no path recorded)'}"
        }, status=404)

    try:
        with open(json_path, "r") as fp:
            data = json.load(fp)
    except Exception as e:
        return JsonResponse({"error": f"Failed to read JSON: {str(e)}"}, status=500)

    # --- Inject top-level stats if absent (cycle JSON only has cycle_uid/coil_id/result) ---
    result_list = data.get("result", [])

    if "total_frames" not in data:
        data["total_frames"] = len(result_list)

    if "total_defects" not in data:
        td = 0
        for frame in result_list:
            for v in (frame.get("t") or {}).values():
                try:
                    td += int(v)
                except Exception:
                    pass
        data["total_defects"] = td

    if "total_defective_frames" not in data:
        tdf = 0
        for frame in result_list:
            t = frame.get("t") or {}
            if any(int(v) > 0 for v in t.values() if str(v).isdigit() or isinstance(v, (int, float))):
                tdf += 1
        data["total_defective_frames"] = tdf

    # coil_length_mm absent ? client computes from max l value
    data.setdefault("coil_length_mm", 0)

    return JsonResponse(data)
        
@csrf_exempt
def download_history_data(request):
    start_date = request.GET.get("start_date")
    end_date   = request.GET.get("end_date")
    coil_id    = request.GET.get("coil_id")

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date, "%Y-%m-%d")
    except Exception:
        return HttpResponse("Invalid date range", status=400)

    # Same source as fetch_history_data
    queryset = DEFECTS_HISTORY.objects.filter(
        create_time__date__gte=start_dt,
        create_time__date__lte=end_dt
    ).order_by("-create_time")

    if coil_id:
        queryset = queryset.filter(coil_id__icontains=coil_id)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="defects_history.csv"'

    writer = csv.writer(response)

    # Same columns as old download_history_data
    writer.writerow([
        "DATETIME",
        "COIL_ID",
        "CYCLE_UID",
        "TOTAL_FRAMES",
        "TOTAL_DEFECT_FRAMES",
        "TOTAL_DEFECTS",
        "V",
        "D",
        "S",
        "C"
    ])

    for row in queryset:
        total_frames = 0
        total_defects = 0
        total_defect_frames = 0
        counts = {"V": 0, "D": 0, "S": 0, "C": 0}

        if row.defect_details:
            try:
                summary = json.loads(row.defect_details)

                total_frames = int(summary.get("total_frames", 0) or 0)
                total_defects = int(summary.get("total_defects", 0) or 0)
                total_defect_frames = int(
                    summary.get(
                        "total_defective_frames",
                        summary.get("defect_frames", 0)
                    ) or 0
                )

                for key in ["V", "D", "S", "C"]:
                    counts[key] = int(summary.get(key, 0) or 0)

            except Exception:
                pass

        writer.writerow([
            row.create_time.strftime("%Y-%m-%d %H:%M:%S"),
            row.coil_id,
            row.cycle_uid,
            total_frames,
            total_defect_frames,
            total_defects,
            counts["V"],
            counts["D"],
            counts["S"],
            counts["C"]
        ])

    return response

@csrf_exempt
def download_run_json(request):
    cycle_uid = request.GET.get("cycle_uid")
    if not cycle_uid:
        return JsonResponse({"error": "cycle_uid required"}, status=400)

    defect_row = DEFECTS_HISTORY.objects.filter(
        cycle_uid=cycle_uid
    ).first()

    if not defect_row:
        return JsonResponse({"error": "data not found"}, status=404)

    try:
        filename = os.path.basename(defect_row.json_path)
        response = FileResponse(
            open(defect_row.json_path, "rb"),
            content_type="application/json"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

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
    # Prefer live.json from DEBUGS/TEMP_JSON if available
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        live_path = os.path.join(root, "DEBUGS", "TEMP_JSON", "live.json")
        if os.path.exists(live_path):
            with open(live_path, "r") as fp:
                data = json.load(fp)

            # Ensure required keys and defaults
            data.setdefault("coil_length_mm", 12000000)
            data.setdefault("current_position_mm", 0)
            data.setdefault("speed_mpm", 0)
            data.setdefault("total_defects", 0)
            data.setdefault("total_frames", 0)
            data.setdefault("total_defective_frames", 0)
            data.setdefault("status", "idle")
            data.setdefault("result", [])

            return JsonResponse(data)

    except Exception as e:
        return JsonResponse({
            "error": str(e),
            "coil_length_mm": 12000000,
            "current_position_mm": 0,
            "speed_mpm": 0,
            "total_defects": 0,
            "total_frames": 0,
            "total_defective_frames": 0,
            "status": "error",
            "result": []
        }, status=500)

    # Fallback sample data
    # sample = {
    #     "status": "processing",
    #     "cycle_uid": "ROLL_SAMPLE",
    #     "coil_id": "COIL_SAMPLE",
    #     "coil_length_mm": 12000000,
    #     "current_position_mm": 0,
    #     "speed_mpm": 0,
    #     "total_defects": 0,
    #     "total_frames": 0,
    #     "total_defective_frames": 0,
    #     "result": []
    # }

    # return JsonResponse(sample)
