import sys
import os
import psutil
import subprocess
import shlex
import sys
import time
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QWidget, QTabWidget, QVBoxLayout, QPushButton,
    QPlainTextEdit, QLabel, QHBoxLayout, QMainWindow, QFrame, QMessageBox
)
from PyQt5.QtCore import QProcess, QTimer, QProcessEnvironment
from PyQt5.QtGui import QFont

from tendo import singleton
me = singleton.SingleInstance()

from DEPENDANT import DISK_CLEANER

# BASE_FILE_PATH = r"D:\INSIGHTZZ\PROJECTS\STANDARD_TEMPLATE\DJANGO_SCRIPTS_FRAMEWORK\SURFACE_INSPECTION_TALOJA\\"
# SERVICES = {
#     "PLC": fr"{BASE_FILE_PATH}SCRIPTS\PLC_COMM.py",
#     "Frame Capture": fr"{BASE_FILE_PATH}SCRIPTS\FRAME_CAPTURE.py",
#     "Algorithm": fr"{BASE_FILE_PATH}SCRIPTS\ALGORITHM.py",
#     "Django": fr"{BASE_FILE_PATH}WEB_APP\manage.py runserver"
# }
# PYTHON_EXE = r"C:\Users\ADMIN\miniconda3\envs\detectron2\python.exe"
# CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"  # Adjust if different

# if getattr(sys, "frozen", False):
#     BASE_DIR = Path(sys.argv[0]).resolve().parent.parent.parent
# else:
#     BASE_DIR = Path(__file__).resolve().parent.parent

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
    "Health": f"{BASE_FILE_PATH}/SCRIPTS/HEALTH_MONITOR.py",
    "Logic (Orphan)": f"{BASE_FILE_PATH}/SCRIPTS/LOGIC_ORPHAN.py",
    "Logic (Live)": f"{BASE_FILE_PATH}/SCRIPTS/LOGIC_LIVE.py",
    "Web App": f"{BASE_FILE_PATH}/WEB_APP/manage.py runserver 0.0.0.0:8000",
}

# SERVICES = {
#     "Capture": f"{BASE_FILE_PATH}/SCRIPTS/SCRIPTS/CAPTURE.py",
#     "Trigger": f"{BASE_FILE_PATH}/SCRIPTS/SCRIPTS/TRIGGER.py",
#     "Writer": f"{BASE_FILE_PATH}/SCRIPTS/SCRIPTS/WRITER.py",
#     "Health": f"{BASE_FILE_PATH}/SCRIPTS/SCRIPTS/HEALTH_MONITOR.py",
#     "Logic": f"{BASE_FILE_PATH}/SCRIPTS/SCRIPTS/LOGIC.py",
#     "Web App": f"{BASE_FILE_PATH}/WEB_APP/WEB_APP/manage.py runserver 0.0.0.0:8000",
# }

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

        # NEW ? watchdog timestamp
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

        # NEW ? WATCHDOG TIMER
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
        # env.insert(
        #     "PYTHONPATH",
        #     f"{BASE_FILE_PATH}/BIN/SCRIPTS"
        # )

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
        # args = shlex.split(self.command)

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
            # self.process.start("/usr/bin/python3", args)
            self.process.start(PYTHON_EXE, args)
            # self.process.start(args[0], args[1:])
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
            self.append_log("[WARN] terminate timeout ? killing process")
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
        self.append_log("[WARN] Service crashed ? Restarting in 5 seconds...")
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
        """NEW ? Restart service if no output for 5 minutes."""
        if not self.process:
            return

        if self.last_output_time is None:
            return

        silence = time.time() - self.last_output_time
        if "Web" in self.name or "Data" in self.name:
            if silence > 60*60*8:  # 8 hours
                self.append_log("[WATCHDOG] No output for 8 hours ? Restarting...")
                self.restart_service()
        else:
            if silence > 60*60*1:  # 6 minutes
                self.append_log("[WATCHDOG] No output for 1 hour ? Restarting...")
                self.restart_service()

    def open_chrome(self, url="http://127.0.0.1:8000"):
        # attempt to open browser; log error if fails
        try:
            subprocess.Popen([CHROME_PATH, "--kiosk", url])
            self.append_log(f"[INFO] Opened browser to {url}")
        except Exception as e:
            self.append_log(f"[ERROR] Could not open Chrome: {e}")


class DashboardTab(QWidget):
    def __init__(self, service_tabs):
        super().__init__()
        self.service_tabs = service_tabs
        self._disk_warned = False   # tracks whether the 85% popup has been shown

        layout = QVBoxLayout()
        self.disk_label = QLabel("Disk: -- | Free: -- | Used: --%")
        layout.addWidget(self.disk_label)

        # Divider line
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        # Service statuses
        self.status_labels = {}
        for name in service_tabs:
            lbl = QLabel(f"{name}: Inactive")
            set_status_color(lbl, "Inactive", name)
            layout.addWidget(lbl)
            self.status_labels[name] = lbl

        self.setLayout(layout)

        # Timer to update info
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_dashboard)
        self.timer.start(3000)

    def update_dashboard(self):
        root_path = "/" if os.name != "nt" else "C:\\"
        try:
            usage = psutil.disk_usage(root_path)
            pct   = usage.percent

            # Colour the disk label based on usage level
            if pct >= 95:
                colour = "red"
            elif pct >= 85:
                colour = "orange"
            else:
                colour = "white"

            self.disk_label.setText(
                f"Disk: {usage.total // (1024**3)} GB | "
                f"Free: {usage.free // (1024**3)} GB | "
                f"Used: {pct:.1f}%"
            )
            self.disk_label.setStyleSheet(f"color: {colour}; font-weight: bold;")

            # ── 85 % popup warning ────────────────────────────────────────────
            if pct >= 85 and not self._disk_warned:
                self._disk_warned = True
                msg = QMessageBox(self)
                msg.setWindowTitle("⚠ Disk Space Warning")
                msg.setIcon(QMessageBox.Warning)
                msg.setText(
                    f"<b>Disk usage is at {pct:.1f}%</b><br><br>"
                    "Storage is running low.<br>"
                    "Auto-cleanup will trigger at <b>95%</b>.<br>"
                    "Consider freeing space manually."
                )
                msg.setStyleSheet(
                    "QMessageBox { background-color: #2b2b2b; color: white; }"
                    "QLabel { color: white; }"
                    "QPushButton { background-color: #444; color: white; padding: 4px 12px; }"
                )
                msg.exec_()

            # Reset warn flag once disk drops back below 80 % so it can fire again
            if pct < 80:
                self._disk_warned = False

        except Exception as e:
            self.disk_label.setText(f"Disk: error ({e})")

        for name, tab in self.service_tabs.items():
            status = tab.status_label.text().replace(f"{name}: ", "")
            set_status_color(self.status_labels[name], status, name)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Service Manager")
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

        # Start disk auto-cleaner daemon
        DISK_CLEANER.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1000, 650)
    window.show()
    sys.exit(app.exec_())