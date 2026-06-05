import sys
import os
import psutil
import subprocess
import shlex
import sys
import time
import cv2

import numpy as np

from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QWidget, QTabWidget, QVBoxLayout, QPushButton,
    QPlainTextEdit, QLabel, QHBoxLayout, QMainWindow, QFrame,
    QGridLayout, QGroupBox, QSizePolicy, QScrollArea
)
from PyQt5.QtCore import QProcess, QTimer, QProcessEnvironment, Qt
from PyQt5.QtGui import QFont, QPixmap, QImage

from DEPENDANT.MQTT import MQTT

from tendo import singleton
me = singleton.SingleInstance()

# BASE_FILE_PATH = r"D:\INSIGHTZZ\PROJECTS\STANDARD_TEMPLATE\DJANGO_SCRIPTS_FRAMEWORK\SURFACE_INSPECTION_TALOJA\\"
# SERVICES = {
#     "PLC": fr"{BASE_FILE_PATH}SCRIPTS\PLC_COMM.py",
#     "Frame Capture": fr"{BASE_FILE_PATH}SCRIPTS\FRAME_CAPTURE.py",
#     "Algorithm": fr"{BASE_FILE_PATH}SCRIPTS\ALGORITHM.py",
#     "Django": fr"{BASE_FILE_PATH}WEB_APP\manage.py runserver"
# }
# PYTHON_EXE = r"C:\Users\ADMIN\miniconda3\envs\detectron2\python.exe"
# CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"  # Adjust if different

BASE_DIR = Path(__file__).resolve().parent.parent
BASE_FILE_PATH = BASE_DIR

db_user = "root"
db_pass = "insightzz@123"
db_host = "127.0.0.1"
db_name = "SURFACE_INSPECTION_TALOJA"

SERVICES = {
    "Capture": f"{BASE_FILE_PATH}/SCRIPTS/CAPTURE.py",
    "Trigger": f"{BASE_FILE_PATH}/SCRIPTS/TRIGGER.py",
    "Writer": f"{BASE_FILE_PATH}/SCRIPTS/WRITER.py",
    "Logic": f"{BASE_FILE_PATH}/SCRIPTS/LOGIC.py",
    # "Web App": f"{BASE_FILE_PATH}/WEB_APP/manage.py runserver",
    # "Health Conn.": f"{BASE_FILE_PATH}/SCRIPTS/HEALTH_CHECKER.py",
}

PYTHON_EXE = sys.executable
CHROME_PATH = "/usr/bin/google-chrome"  # Adjust if different

def set_status_color(label: QLabel, status: str, name: str = ""):
    """Helper to color status labels."""
    if "Running" in status:
        label.setStyleSheet("color: lightgreen; font-weight: bold;")
    elif "Stopped" in status:
        label.setStyleSheet("color: red; font-weight: bold;")
    elif "Exited" in status:
        label.setStyleSheet("color: orange; font-weight: bold;")
    else:
        label.setStyleSheet("color: gray; font-weight: bold;")
    label.setText(f"{name}: {status}")

class ServiceTab(QWidget):
    def __init__(self, name, command):
        super().__init__()
        self.name = name
        self.command = command
        self.process = None
        self.user_requested_stop = False

        # NEW — watchdog timestamp
        self.last_output_time = None

        layout = QVBoxLayout()

        # Disk + status info
        top_layout = QHBoxLayout()
        self.disk_label = QLabel("Disk: -- | Free: --")
        self.status_label = QLabel("Status: Inactive")
        set_status_color(self.status_label, "Inactive", "Status")
        top_layout.addWidget(self.disk_label)
        top_layout.addWidget(self.status_label)
        layout.addLayout(top_layout)

        # Log area
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)

        # Consolas maybe not present on linux; fallback handled by Qt automatically.
        self.log_area.setFont(QFont("Consolas", 10))
        self.log_area.setStyleSheet("background-color: #1e1e1e; color: #dcdcdc; padding: 6px; border-radius: 6px;")
        layout.addWidget(self.log_area)

        # Buttons
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.restart_btn = QPushButton("Restart")

        for btn in (self.start_btn, self.stop_btn, self.restart_btn):
            btn.setStyleSheet(
                "QPushButton { padding: 6px; border-radius: 6px; background-color: #333; color: white; }"
                "QPushButton:hover { background-color: #555; }"
            )

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addWidget(self.restart_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

        # Connect signals
        self.start_btn.clicked.connect(self.start_service)
        self.stop_btn.clicked.connect(self.stop_service)
        self.restart_btn.clicked.connect(self.restart_service)

        # Timer to refresh disk info
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_disk_status)
        self.timer.start(3000)  # every 3s

        # Auto clear logs every hour
        self.clear_timer = QTimer()
        self.clear_timer.timeout.connect(self.clear_logs)
        self.clear_timer.start(3600 * 1000)  # 1 hour

        # NEW — WATCHDOG TIMER
        self.watchdog_timer = QTimer()
        self.watchdog_timer.timeout.connect(self.check_output_timeout)
        self.watchdog_timer.start(10000)  # check every 10 seconds

        # Auto start services
        # if "Web" not in self.name: self.start_service()
        self.start_service()

    def append_log(self, text: str):
        """Safe append to log area and auto-scroll."""
        if not text:
            return
        self.log_area.appendPlainText(text)
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def start_service(self):
        self.user_requested_stop = False

        # Prevent double-starts
        if self.process is not None:
            self.append_log("[INFO] Service already running.")
            return

        # Validate python exe
        if not os.path.isfile(PYTHON_EXE) or not os.access(PYTHON_EXE, os.X_OK):
            self.append_log(f"[ERROR] PYTHON_EXE not found or not executable: {PYTHON_EXE}")
            set_status_color(self.status_label, "Stopped", "Status")
            return

        # Create process
        self.process = QProcess(self)
        
        # Set environment variables for the process
        env = QProcessEnvironment.systemEnvironment()  # <-- CRITICAL
        env.insert("BASE_FILE_PATH", str(BASE_FILE_PATH))
        env.insert("db_user", db_user)
        env.insert("db_pass", db_pass)
        env.insert("db_host", db_host)
        env.insert("db_name", db_name)

        self.process.setProcessEnvironment(env)
        # merge channels so stdout/stderr come together
        try:
            self.process.setProcessChannelMode(QProcess.MergedChannels)
        except Exception:
            pass

        # Connect signals for debugging
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.service_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.started.connect(self.process_started)

        # Build args safely using shlex.split
        args = ["-u"] + shlex.split(self.command)

        # For Django, set working dir to project root
        if "Django" in self.name:
            # project root is BASE_FILE_PATH (strip trailing slash)
            project_root = BASE_FILE_PATH.rstrip("/\\")
            if os.path.isdir(project_root):
                self.process.setWorkingDirectory(project_root)
            else:
                # fallback: dirname of manage.py if present
                candidate = os.path.dirname(self.command.split("manage.py")[0])
                if candidate:
                    self.process.setWorkingDirectory(candidate)

        # Log what we are about to run (very useful for debugging)
        self.append_log(f"[INFO] Starting service '{self.name}'")
        self.append_log(f"[DEBUG] PYTHON_EXE: {PYTHON_EXE}")
        self.append_log(f"[DEBUG] WorkingDir: {self.process.workingDirectory()}")
        self.append_log(f"[DEBUG] Command args: {args}")

        # Start the process
        try:
            self.process.start(PYTHON_EXE, args)
            # don't assume started immediately; wait is not blocking here
        except Exception as e:
            self.append_log(f"[EXCEPTION] start() failed: {e}")
            set_status_color(self.status_label, "Stopped", "Status")
            self.process = None
            return

        set_status_color(self.status_label, "Starting...", "Status")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.restart_btn.setEnabled(True)

    def stop_service(self):
        if not self.process:
            self.append_log("[INFO] service not running")
            return

        self.append_log(f"[INFO] Stopping service '{self.name}' ...")
        self.user_requested_stop = True

        self.process.terminate()
        # give some time to quit gracefully; if not, kill
        if not self.process.waitForFinished(3000):
            self.append_log("[WARN] terminate timeout — killing process")
            self.process.kill()
            self.process.waitForFinished(2000)

        self.process = None
        set_status_color(self.status_label, "Stopped", "Status")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def restart_service(self):
        self.append_log(f"[INFO] Restarting service '{self.name}' ...")
        self.stop_service()
        QTimer.singleShot(1000, self.start_service)

    def read_stdout(self):
        if not self.process:
            return
        try:
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="ignore")
        except Exception:
            text = "<could not decode stdout>"
        if text.strip():
            self.last_output_time = time.time()     # NEW
            self.append_log(text.rstrip())

    def read_stderr(self):
        if not self.process:
            return
        try:
            text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="ignore")
        except Exception:
            text = "<could not decode stderr>"
        if text.strip():
            self.last_output_time = time.time()     # NEW
            self.append_log("[STDERR] " + text.rstrip())

    def process_error(self, error):
        # QProcess.ProcessError enumerations -> convert to text
        try:
            err_text = str(error)
        except Exception:
            err_text = "Unknown QProcess error"
        self.append_log(f"[QPROCESS ERROR] {err_text}")
        set_status_color(self.status_label, "Stopped", "Status")
        self.process = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def process_started(self):
        self.append_log("[INFO] process started")
        self.last_output_time = time.time() # NEW
        set_status_color(self.status_label, "Running", "Status")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def service_finished(self):
        self.append_log("[INFO] process finished")

        # If stop button was pressed -> don't restart
        if self.user_requested_stop:
            set_status_color(self.status_label, "Stopped", "Status")
            self.process = None
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.user_requested_stop = False
            return

        # Otherwise: crash detected -> auto restart
        set_status_color(self.status_label, "Exited", "Status")
        self.append_log("[WARN] Service crashed — Restarting in 5 seconds...")
        self.process = None
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

        # Auto restart after 5 sec
        QTimer.singleShot(5000, self.start_service)

    def update_disk_status(self):
        #  cross-platform root path
        root_path = "/" if os.name != "nt" else "C:\\"
        try:
            usage = psutil.disk_usage(root_path)
            self.disk_label.setText(
                f"Disk: {usage.total // (1024**3)} GB | Free: {usage.free // (1024**3)} GB"
            )
        except Exception as e:
            self.disk_label.setText(f"Disk: error ({e})")

    def clear_logs(self):
        self.log_area.clear()
        self.append_log("[Logs cleared automatically]")

    def check_output_timeout(self):
        """NEW — Restart service if no output for 5 minutes."""
        if not self.process:
            return

        if self.last_output_time is None:
            return

        silence = time.time() - self.last_output_time
        if "Web" in self.name or "Data" in self.name:
            if silence > 60*60*8:  # 8 hours
                self.append_log("[WATCHDOG] No output for 8 hours — Restarting...")
                self.restart_service()
        else:
            if silence > 60*60*1:  # 6 minutes
                self.append_log("[WATCHDOG] No output for 1 hour — Restarting...")
                self.restart_service()

    def open_chrome(self, url="http://127.0.0.1:8000"):
        # attempt to open browser; log error if fails
        try:
            subprocess.Popen([CHROME_PATH, "--kiosk", url])
            self.append_log(f"[INFO] Opened browser to {url}")
        except Exception as e:
            self.append_log(f"[ERROR] Could not open Chrome: {e}")

class DashboardTab(QWidget):

    TEMP_IMG_PATH   = str(BASE_FILE_PATH) + "/DEBUGS/TEMP_IMG/TALOJA/24521939.jpg"
    LOGIC_IMG_PATH   = str(BASE_FILE_PATH) + "/DEBUGS/TEMP_IMG/TALOJA/24521939_debug.jpg"

    MQTT_TRIGGER = "main/trigger"
    MQTT_LOGIC   = "main/logic"

    def __init__(self, service_tabs):
        super().__init__()
        self.service_tabs = service_tabs

        # ── MQTT listener ──────────────────────────────────────────────────────
        self._mqtt = MQTT("main_capture")
        self._mqtt.subscribe(self.MQTT_TRIGGER)
        self._mqtt.subscribe(self.MQTT_LOGIC)

        # ── root layout: top status bar  +  two columns ───────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # ── top bar: disk + service statuses (original content, preserved) ────
        top_bar = QHBoxLayout()

        self.disk_label = QLabel("Disk: -- | Free: --")
        self.disk_label.setStyleSheet("color: #aaa; font-size: 11px;")
        top_bar.addWidget(self.disk_label)

        top_bar.addStretch()

        # self.status_labels = {}
        # for name in service_tabs:
        #     lbl = QLabel(f"{name}: Inactive")
        #     set_status_color(lbl, "Inactive", name)
        #     lbl.setStyleSheet(lbl.styleSheet() + " font-size: 11px; margin-left: 12px;")
        #     top_bar.addWidget(lbl)
        #     self.status_labels[name] = lbl

        root.addLayout(top_bar)

        # thin divider
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #444;")
        root.addWidget(line)

        # ── two-column body ────────────────────────────────────────────────────
        cols = QHBoxLayout()
        cols.setSpacing(10)
        cols.addWidget(self._build_left_panel(),  stretch=1)
        cols.addWidget(self._build_right_panel(), stretch=1)
        root.addLayout(cols)

        # ── timers ─────────────────────────────────────────────────────────────
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_dashboard)
        self.timer.start(1000)          # 1 s — matches requirement

    # ── panel builders ─────────────────────────────────────────────────────────

    def _panel(self, title):
        """Styled QGroupBox used for both panels."""
        gb = QGroupBox(title)
        gb.setStyleSheet(
            "QGroupBox { color: #aaa; font-size: 10px; font-weight: bold; "
            "  border: 1px solid #444; border-radius: 6px; "
            "  margin-top: 8px; padding-top: 4px; background: #252526; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        return gb

    def _cam_frame(self):
        """Black placeholder label used as a camera image display."""
        lbl = QLabel()
        lbl.setFixedHeight(500)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("background: #111; border-radius: 4px; color: #555; font-size: 11px;")
        lbl.setText("No image")
        lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return lbl

    def _kv_row(self, grid, row, key, value_text="—", value_color="#e0e0e0"):
        """Add a key-value pair row to a QGridLayout, return the value label."""
        k = QLabel(key.upper())
        k.setStyleSheet("color: #777; font-size: 10px; font-weight: bold; background: transparent;")
        v = QLabel(value_text)
        v.setStyleSheet(f"color: {value_color}; font-size: 12px; font-weight: bold; background: transparent;")
        grid.addWidget(k, row, 0)
        grid.addWidget(v, row, 1)
        return v

    def _build_left_panel(self):
        panel = self._panel("Trigger — Live")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(8)

        # Camera image
        self.left_cam = self._cam_frame()
        layout.addWidget(self.left_cam)

        # Coil ID — big, amber, most prominent
        coil_box = QFrame()
        coil_box.setStyleSheet("background: #1a1400; border: 1px solid #8a6000; border-radius: 5px; padding: 4px;")
        coil_layout = QHBoxLayout(coil_box)
        coil_layout.setContentsMargins(8, 4, 8, 4)
        coil_lbl = QLabel("COIL / ROLL")
        coil_lbl.setStyleSheet("color: #888; font-size: 10px; font-weight: bold; background: transparent;")
        self.left_roll = QLabel("—")
        self.left_roll.setStyleSheet("color: #ffb300; font-size: 16px; font-weight: bold; background: transparent;")
        self.left_roll.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        coil_layout.addWidget(coil_lbl)
        coil_layout.addStretch()
        coil_layout.addWidget(self.left_roll)
        layout.addWidget(coil_box)

        # Trigger details grid
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setColumnMinimumWidth(0, 110)
        self.t_diameter  = self._kv_row(grid, 0, "Diameter")
        self.t_speed     = self._kv_row(grid, 1, "Mill Speed")
        self.t_eg        = self._kv_row(grid, 2, "EG Setpoint")
        self.t_width     = self._kv_row(grid, 3, "Width")
        self.t_length    = self._kv_row(grid, 4, "Length")
        self.t_mill_run  = self._kv_row(grid, 5, "Mill Run",  value_color="#aaa")
        self.t_ironing   = self._kv_row(grid, 6, "Ironing Raised", value_color="#aaa")
        self.t_status    = self._kv_row(grid, 7, "Capture Status", value_color="#aaa")
        layout.addLayout(grid)
        layout.addStretch()

        return panel

    def _build_right_panel(self):
        panel = self._panel("Logic — Defect Summary")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(8)

        # Camera image (last result)
        self.right_cam = self._cam_frame()
        layout.addWidget(self.right_cam)

        # Coil ID — same amber treatment
        coil_box = QFrame()
        coil_box.setStyleSheet("background: #1a1400; border: 1px solid #8a6000; border-radius: 5px; padding: 4px;")
        coil_layout = QHBoxLayout(coil_box)
        coil_layout.setContentsMargins(8, 4, 8, 4)
        coil_lbl = QLabel("COIL / ROLL")
        coil_lbl.setStyleSheet("color: #888; font-size: 10px; font-weight: bold; background: transparent;")
        self.right_roll = QLabel("—")
        self.right_roll.setStyleSheet("color: #ffb300; font-size: 16px; font-weight: bold; background: transparent;")
        self.right_roll.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        coil_layout.addWidget(coil_lbl)
        coil_layout.addStretch()
        coil_layout.addWidget(self.right_roll)
        layout.addWidget(coil_box)

        # Severity — large colored badge
        sev_box = QFrame()
        sev_box.setStyleSheet("background: #1a1a1a; border: 1px solid #444; border-radius: 5px;")
        sev_layout = QHBoxLayout(sev_box)
        sev_layout.setContentsMargins(8, 4, 8, 4)
        sev_lbl = QLabel("SEVERITY")
        sev_lbl.setStyleSheet("color: #888; font-size: 10px; font-weight: bold; background: transparent;")
        self.r_severity = QLabel("—")
        self.r_severity.setStyleSheet("color: #aaa; font-size: 15px; font-weight: bold; background: transparent;")
        self.r_severity.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sev_layout.addWidget(sev_lbl)
        sev_layout.addStretch()
        sev_layout.addWidget(self.r_severity)
        layout.addWidget(sev_box)

        # Defect counts grid
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setColumnMinimumWidth(0, 110)
        self.r_total   = self._kv_row(grid, 0, "Total Defects", value_color="#f44336")
        self.r_dent    = self._kv_row(grid, 1, "Dent (D)",      value_color="#ef9a9a")
        self.r_vibr    = self._kv_row(grid, 2, "Vibration (V)", value_color="#90caf9")
        self.r_silv    = self._kv_row(grid, 3, "Silver (S)",    value_color="#ffcc80")
        self.r_frames  = self._kv_row(grid, 4, "Total Frames")
        self.r_dframes = self._kv_row(grid, 5, "Defect Frames", value_color="#ef9a9a")
        self.r_proc_at = self._kv_row(grid, 6, "Processed At",  value_color="#aaa")
        layout.addLayout(grid)
        layout.addStretch()

        return panel

    # ── update (called every 1 s) ───────────────────────────────────────────────

    def update_dashboard(self):
        self._update_top_bar()
        self._update_left()
        self._update_right()

    def _update_top_bar(self):
        # Disk
        root_path = "/" if os.name != "nt" else "C:\\"
        try:
            usage = psutil.disk_usage(root_path)
            self.disk_label.setText(
                f"Disk: {usage.total // (1024**3)} GB | Free: {usage.free // (1024**3)} GB"
            )
        except Exception as e:
            self.disk_label.setText(f"Disk: error ({e})")

        # Service statuses — original logic, unchanged
        # for name, tab in self.service_tabs.items():
        #     status = tab.status_label.text().replace(f"{name}: ", "").replace("Status: ", "")
        #     set_status_color(self.status_labels[name], status, name)

    def _load_image(self, path, label):
        try:
            if not os.path.isfile(path):
                label.setText("No image")
                return
            img = cv2.imread(path)
            if img is None:
                label.setText("Unreadable")
                return
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, ch = img.shape
            qt_img = QImage(img.data, w, h, ch * w, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qt_img).scaled(
                label.width() or 480, label.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            label.setPixmap(pix)
            label.setText("")
        except Exception:
            label.setText("Load error")

    def _find_latest_result_image(self):
        """Walk RESULT_PATH and return the most recently modified .jpg."""
        try:
            best_t, best_p = 0, None
            for root, _, files in os.walk(self.RESULT_IMG_PATH):
                for f in files:
                    if f.lower().endswith(".jpg"):
                        p = os.path.join(root, f)
                        t = os.path.getmtime(p)
                        if t > best_t:
                            best_t, best_p = t, p
            return best_p
        except Exception:
            return None

    def _update_left(self):
        # Live temp image
        self._load_image(self.TEMP_IMG_PATH, self.left_cam)

        # MQTT data from TRIGGER / CAPTURE
        d = self._mqtt.pop(self.MQTT_TRIGGER)

        if d:

            roll = d.get("roll_num") or d.get("roll_id") or "—"
            self.left_roll.setText(roll)

            self.t_diameter.setText(f"{d.get('diameter', '—')} mm"  if d.get('diameter') is not None else "—")
            self.t_speed.setText(f"{d.get('mill_speed', '—')} rpm"  if d.get('mill_speed') is not None else "—")
            self.t_eg.setText(str(d.get('eg_setpoint', '—')))
            self.t_width.setText(f"{d.get('width', '—')} mm"        if d.get('width') is not None else "—")
            self.t_length.setText(f"{d.get('length', '—')} mm"       if d.get('length') is not None else "—")

            # mill_run / ironing
            mill_run = d.get("mill_run")
            ironing  = d.get("ironing_raised")
            self.t_mill_run.setText("● RUNNING" if mill_run in (1, "1", True) else "○ STOPPED")
            self.t_mill_run.setStyleSheet(
                f"color: {'lightgreen' if mill_run in (1, '1', True) else '#f44336'}; "
                "font-size: 12px; font-weight: bold; background: transparent;"
            )
            self.t_ironing.setText("● RAISED" if ironing in (1, "1", True) else "○ DOWN")
            self.t_ironing.setStyleSheet(
                f"color: {'#ffb300' if ironing in (1, '1', True) else '#aaa'}; "
                "font-size: 12px; font-weight: bold; background: transparent;"
            )

            action = d.get("action", "")
            self.t_status.setText(action.upper() if action else "IDLE")
            self.t_status.setStyleSheet(
                f"color: {'lightgreen' if action == 'start' else ('#f44336' if action == 'stop' else '#aaa')}; "
                "font-size: 12px; font-weight: bold; background: transparent;"
            )

    def _update_right(self):
        # Latest result image
        self._load_image(self.LOGIC_IMG_PATH, self.right_cam)

        # MQTT data from LOGIC
        d = self._mqtt.pop(self.MQTT_LOGIC)

        if d:
            roll = d.get("cycle_uid") or d.get("roll_num") or "—"
            self.right_roll.setText(roll)

            total  = d.get("total_defects", 0)
            sev    = d.get("severity", "LOW")

            sev_color = {"HIGH": "#f44336", "MEDIUM": "#ffb300", "LOW": "lightgreen"}.get(sev, "#aaa")
            self.r_severity.setText(sev)
            self.r_severity.setStyleSheet(
                f"color: {sev_color}; font-size: 15px; font-weight: bold; background: transparent;"
            )

            self.r_total.setText(str(total) if total is not None else "—")
            self.r_dent.setText(str(d.get("D", "—")))
            self.r_vibr.setText(str(d.get("V", "—")))
            self.r_silv.setText(str(d.get("S", "—")))
            self.r_frames.setText(str(d.get("total_frames", "—")))
            self.r_dframes.setText(str(d.get("defect_frames", "—")))
            proc_at = d.get("processed_at", "")
            self.r_proc_at.setText(proc_at[:19] if proc_at else "—")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🚀 Service Manager")
        self.setStyleSheet("background-color: #2b2b2b; color: white;")

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #444; }"
            "QTabBar::tab { background: #333; padding: 6px; }"
            "QTabBar::tab:selected { background: #555; }"
        )

        self.service_tabs = {}

        # Create service tabs
        for name, cmd in SERVICES.items():
            tab = ServiceTab(name, cmd)
            self.service_tabs[name] = tab
            self.tabs.addTab(tab, name)

        # Dashboard
        dashboard = DashboardTab(self.service_tabs)
        self.tabs.insertTab(0, dashboard, "Dashboard")

        self.setCentralWidget(self.tabs)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1000, 650)
    window.show()
    sys.exit(app.exec_())