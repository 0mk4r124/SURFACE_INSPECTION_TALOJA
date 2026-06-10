let currentPage = 1;
let scale = 1;
let isDragging = false;
let startX, startY, imgX = 0, imgY = 0;
let currentOpenedAnodeId = null;
let currentOpenedBunch = null;
let isImageLoading = false;
let modalOpen = false;

// State management variables
let currentUID = null;
let vehicleDetailsModalOpen = false;
let emergencyModalOpen = false;
let autoManualModalOpen = false;
let statusCheckInterval = null;

// Print job state
let pendingPrintRow    = null;
let printModalInstance = null;
const defectColors = {
    "V": "#007bff",
    "D": "#dc3545",
    "S": "#28a745",
    "C": "#fd7e14"
};

const img = document.getElementById("modalImage");
const wrapper = document.getElementById("zoomWrapper");

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
        const cookies = document.cookie.split(";");
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + "=")) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie("csrftoken");

// Reset zoom & position whenever modal opens
document.getElementById("imageModal").addEventListener("shown.bs.modal", () => {
    scale = 1;
    imgX = imgY = 0;
    img.style.transform = `translate(0px, 0px) scale(1)`;
});

// Mouse wheel zoom
wrapper.addEventListener("wheel", (e) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.1 : -0.1;
    scale = Math.min(Math.max(0.5, scale + delta), 4); // limit zoom 0.5x – 4x
    img.style.transform = `translate(${imgX}px, ${imgY}px) scale(${scale})`;
});

// Drag image
wrapper.addEventListener("mousedown", (e) => {
    isDragging = true;
    wrapper.style.cursor = "grabbing";
    startX = e.clientX - imgX;
    startY = e.clientY - imgY;
});

wrapper.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    imgX = e.clientX - startX;
    imgY = e.clientY - startY;
    img.style.transform = `translate(${imgX}px, ${imgY}px) scale(${scale})`;
});

wrapper.addEventListener("mouseup", () => {
    isDragging = false;
    wrapper.style.cursor = "grab";
});

wrapper.addEventListener("mouseleave", () => {
    isDragging = false;
});

document.addEventListener("DOMContentLoaded", function () {
    // Initialize daterangepicker with default last 7 days
    $('#date_range').daterangepicker({
        autoUpdateInput: true,
        startDate: moment().subtract(0, 'days'),
        endDate: moment(),
        locale: {
            format: "YYYY-MM-DD",
            cancelLabel: "Clear",
            applyLabel: "Apply"
        },
        opens: "right",
        drops: "down",
        autoApply: false,
        buttonClasses: ['btn', 'btn-sm', 'btn-primary'],
    }, function (start, end) {
        $('#date_range').val(start.format("YYYY-MM-DD") + " - " + end.format("YYYY-MM-DD"));
    });

    // Apply event
    $('#date_range').on("apply.daterangepicker", function (ev, picker) {
        const start = picker.startDate.format("YYYY-MM-DD");
        const end = picker.endDate.format("YYYY-MM-DD");
        $(this).val(start === end ? start : start + " - " + end);
    });

    // Cancel event
    $('#date_range').on("cancel.daterangepicker", function () {
        $(this).val("");
    });
});

function fetchHistoryData(page=1) {
    const dateRange = document.getElementById('date_range').value;
    if (!dateRange) {
        alert("Please select a date range");
        return;
    }
    if (dateRange.includes(" - ")) {
        [startDate, endDate] = dateRange.split(" - ").map(s => s.trim());
    } else {
        startDate = endDate = dateRange.trim();
    }
    
    const vehicleNumber = document.getElementById('vehicle_number_search').value.trim();
    const vendorNumber = document.getElementById('vendor_name_search').value.trim();
    let url = `/api/fetch_history_data/?start_date=${startDate}&end_date=${endDate}&page=${page}`;
    if (vehicleNumber) {
        url += `&vehicle_number=${encodeURIComponent(vehicleNumber)}`;
    }
    if (vendorNumber) {
        url += `&vendor_name=${encodeURIComponent(vendorNumber)}`;
    }
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            populateTable(data.data, 'History_body');
            setupPagination(data.total, data.page, data.per_page, "history");
        })
        .catch(err => console.error(err));
}

function populateTable(data, table_name) {
    const tbody = document.getElementById(table_name);
    tbody.innerHTML = '';

    if (table_name === 'History_body'){
        data.forEach(row => {
            const tr = document.createElement('tr');
            const DetailsButton = `
            <button
                class="btn btn-primary btn-sm view-run-details"
                data-cycle="${row.cycle_uid}">
                View Details
            </button>
            `;

            const DownloadButton = `
            <button
                class="btn btn-success btn-sm download-run-data"
                data-cycle="${row.cycle_uid}">
                CSV
            </button>
            `;

            tr.innerHTML = `
            <td>${row.sno}</td>
            <td>${row.datetime}</td>
            <td>${row.coil_id}</td>
            <td>${row.total_defects}</td>
            <td>${row.total_frames}</td>
            <td>${row.total_defect_frames}</td>
            <td>${row.defect_summary}</td>
            <td>${DetailsButton}</td>
            <td>${DownloadButton}</td>
            `;
            tbody.appendChild(tr);
        });
    }
}

function setupPagination(total, current, perPage, tab) {
    const totalPages = Math.ceil(total / perPage);

    const pagination = {
        total_records: total,
        current_page: current,
        has_previous: current > 1,
        has_next: current < totalPages,
        start_index: (current - 1) * perPage + 1,
        end_index: Math.min(current * perPage, total),
        custom_page_range: getCustomPageRange(current, totalPages)
    };

    renderPagination(pagination, tab);
}

function getCustomPageRange(current, totalPages) {
    const delta = 2;
    const range = [];
    for (let i = Math.max(1, current - delta); i <= Math.min(totalPages, current + delta); i++) {
        range.push(i);
    }
    if (range[0] > 1) {
        if (range[0] > 2) range.unshift('...');
        range.unshift(1);
    }
    if (range[range.length - 1] < totalPages) {
        if (range[range.length - 1] < totalPages - 1) range.push('...');
        range.push(totalPages);
    }
    return range;
}

function renderPagination(pagination, tab) {
    const container = document.getElementById(`pagination-${tab}`);
    if (!pagination) return;

    let html = `
        <div>
            <p class="mb-0 text-muted small">
                Showing ${pagination.start_index}–${pagination.end_index} of ${pagination.total_records} entries
            </p>
        </div>

        <div>
            <nav>
                <ul class="pagination mb-0">
                ${pagination.has_previous ?
                    `<li class="page-item"><a class="page-link" href="#" data-page="${pagination.current_page - 1}">Previous</a></li>` :
                    `<li class="page-item disabled"><span class="page-link">Previous</span></li>`}
    `;

    pagination.custom_page_range.forEach(num => {
        if (num === '...') {
            html += `<li class="page-item disabled"><span class="page-link">…</span></li>`;
        } else if (num === pagination.current_page) {
            html += `<li class="page-item active"><span class="page-link">${num}</span></li>`;
        } else {
            html += `<li class="page-item"><a class="page-link" href="#" data-page="${num}">${num}</a></li>`;
        }
    });

    html += `
                ${pagination.has_next ?
                    `<li class="page-item"><a class="page-link" href="#" data-page="${pagination.current_page + 1}">Next</a></li>` :
                    `<li class="page-item disabled"><span class="page-link">Next</span></li>`}
                </ul>
            </nav>
        </div>
    `;

    container.innerHTML = html;

    container.querySelectorAll(".page-link[data-page]").forEach(link => {
        link.addEventListener("click", function (e) {
            e.preventDefault();
            const page = parseInt(this.getAttribute("data-page"));
            if (tab === 'history') {
                if (!isNaN(page)) fetchHistoryData(page);
            }
        });
    });
}

document.addEventListener("click", async function(e){

    const btn =
        e.target.closest(".view-run-details");

// Open live camera modal when clicking on the camera slot
document.addEventListener('click', function(e) {
    const camSlot = e.target.closest('#camera-slot-1');
    if (!camSlot) return;

    const img = camSlot.querySelector('img');
    if (!img || !img.src) return;

    document.getElementById("vehicle_number").innerHTML = `Live Cam`;
    const modalEl = document.getElementById('imageModal');
    modalEl.dataset.src = img.src;
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
});

    if(!btn) return;

    const cycleUid =
        btn.dataset.cycle;

    const response =
        await fetch(
            `/api/get_run_details/?cycle_uid=${cycleUid}`
        );

    const data =
        await response.json();

    renderDefectGraph(data.result);

    updateDefectSummary(data);

    const modal =
        new bootstrap.Modal(
            document.getElementById(
                "runDetailsModal"
            )
        );

    modal.show();
});

document.addEventListener('click', function (e) {
    const btn = e.target.closest('.view-image-btn');
    if (!btn) return;

    const vehicle_number = btn.dataset.vehicle_number;
    const imgSrc = btn.dataset.src;
    document.getElementById("vehicle_number").innerHTML = `Vehicle Number: <b>${vehicle_number}</b>`;

    const modalEl = document.getElementById('imageModal');
    modalEl.dataset.src = imgSrc;

    const modal = new bootstrap.Modal(modalEl);
    modal.show();
});

document.getElementById("imageModal")
    .addEventListener("hidden.bs.modal", function () {
        const img = document.getElementById("modalImage");
        const loader = document.getElementById("imageLoader");

        isImageLoading = false;
        img.src = "";
        img.style.display = "none";
        loader.style.display = "none";
});

document.getElementById("imageModal")
.addEventListener("shown.bs.modal", function () {

    const img = document.getElementById("modalImage");
    const loader = document.getElementById("imageLoader");
    const src = this.dataset.src;

    if (!src) return;

    isImageLoading = true;

    loader.style.display = "block";
    img.style.display = "none";

    img.onload = function () {
        loader.style.display = "none";
        img.style.display = "block";
        isImageLoading = false;
    };

    img.onerror = function () {
        loader.style.display = "none";
        img.style.display = "none";
        isImageLoading = false;
    };

    img.src = src;
});

function updateDefectMap() {
    const defectData = {
        "defect_1": {
            "type": "CAM",
            "ip": "192.168.1.11",
            "cam_serial": "1232345",
            "status": "ACTIVE",
            "last_ping": "09-06-2026 12:33PM",
            "top": 20,
            "left": 20
        }
    }
    const cameraContainer = document.getElementById('defect-indicators');
    
    cameraContainer.innerHTML = '';

    // Create invisible location info dots
    Object.entries(defectData).forEach(([key, info]) => {
        const dot = document.createElement('div');
        let statusClass = '';

        if (info.type && info.type == 'info') {
            dot.className = 'fs-5 location-dot';
        }
        else {
            if (info.status) statusClass = info.status.toLowerCase();
            dot.className = `cam-dot ${statusClass}`;
        }
        
        dot.style.top = `${info.top}%`;
        dot.style.left = `${info.left}%`;

        let tooltipText = `${key}\n`;

        if (info.name) tooltipText += `Name: ${info.name}\n`;

        if (info.current_stem) tooltipText += `Current Stem: ${info.current_stem}\n`;
        if (info.current_bunch) tooltipText += `Current Bunch: ${info.current_bunch}\n`;
        if (info.anodes) tooltipText += `Current Anodes: ${info.anodes}\n`;

        if (info.status) tooltipText += `Status: ${info.status}\n`;
        if (info.ip) tooltipText += `IP: ${info.ip}\n`;
        if (info.cam_serial) tooltipText += `Cam Serial: ${info.cam_serial}\n`;

        if (info.last_ping) tooltipText += `Last Ping: ${info.last_ping}\n`;

        dot.dataset.tooltip = tooltipText.trim();
        cameraContainer.appendChild(dot);
    });
}

function updateSystemStatus() {
    const cameraContainer = document.getElementById('camera-indicators');
    const locationContainer = document.getElementById('location-dots');
    
    cameraContainer.innerHTML = '';
    locationContainer.innerHTML = '';

    // Create invisible location info dots
    // console.log(systemData);
    Object.entries(systemData).forEach(([key, info]) => {
        const dot = document.createElement('div');
        let statusClass = '';

        if (info.type && info.type == 'info') {
            dot.className = 'fs-5 location-dot';
        }
        else {
            if (info.status) statusClass = info.status.toLowerCase();
            dot.className = `cam-dot ${statusClass}`;
        }
        
        dot.style.top = `${info.top}%`;
        dot.style.left = `${info.left}%`;

        let tooltipText = `${key}\n`;

        if (info.name) tooltipText += `Name: ${info.name}\n`;

        if (info.current_stem) tooltipText += `Current Stem: ${info.current_stem}\n`;
        if (info.current_bunch) tooltipText += `Current Bunch: ${info.current_bunch}\n`;
        if (info.anodes) tooltipText += `Current Anodes: ${info.anodes}\n`;

        if (info.status) tooltipText += `Status: ${info.status}\n`;
        if (info.ip) tooltipText += `IP: ${info.ip}\n`;
        if (info.cam_serial) tooltipText += `Cam Serial: ${info.cam_serial}\n`;

        if (info.last_ping) tooltipText += `Last Ping: ${info.last_ping}\n`;

        dot.dataset.tooltip = tooltipText.trim();
        locationContainer.appendChild(dot);
    });
}

// Fetch from API (commented out for testing)
async function fetchSystemStatus() {
    try {
        // Uncomment when API is ready
        const response = await fetch('/api/health_status/');
        const data = await response.json();
        systemData = data.data;
        
        updateSystemStatus();
    } catch (error) {
        console.error('Error fetching system status:', error);
    }
}

function updateCameraImages() {
    const url = `/api/live-ip-camera`;
    fetch(url)
        .then(res => res.json())
        .then(data => {
            if (data.status === "success") {
                data.cameras.forEach((cam, idx) => {
                    const slot = document.getElementById(`camera-slot-${idx + 1}`);
                    if (!slot) return;
                    let img = slot.querySelector("img");

                    if (cam.image_url) {
                        if (!img) {
                            // Create img once if it doesn't exist
                            img = document.createElement("img");
                            slot.innerHTML = "";  // clear slot only first time
                            slot.appendChild(img);
                        }
                        // Update src without creating new element → no blink
                        img.src = `${cam.image_url}?t=${Date.now()}`;
                    } else {
                        slot.innerHTML = "No Camera Selected";
                    }
                });
            }
        })
        .catch(err => console.error("Error fetching camera images:", err));
}

async function fetchCurrentStatus() {

    try {

        const response = await fetch("/api/get_current_status/");
        const data = await response.json();

            // If the UI already shows 'PROCESSING', do not overwrite/update
            const currentStateEl = document.getElementById("currentState");
            if (currentStateEl) {
                const visibleState = (currentStateEl.innerText || "").trim().toLowerCase();
                if (visibleState === "processing") {
                    // skip applying updates while currently processing
                    return;
                }
            }

            document.getElementById("currentState").innerText =
                data.status.toUpperCase();

        document.getElementById("coil_id").innerText =
            data.coil_id;

        document.getElementById("cycle_uid").innerText =
            data.cycle_uid;

        updateDefectSummary(data);

        renderDefectGraph(data.result);

    }
    catch(err){
        console.error(err);
    }
}

function updateDefectSummary(data){

    let defectTypes = {};

    data.result.forEach(frame => {

        Object.entries(frame.t || {}).forEach(([type,count]) => {

            defectTypes[type] =
                (defectTypes[type] || 0) + count;
        });
    });

    let html = `
        <strong>TOTAL DEFECTS : ${data.total_defects}</strong><br>
        <strong>TOTAL FRAMES : ${data.total_frames}</strong><br>
        <strong>TOTAL DEFECTIVE FRAMES : ${data.total_defective_frames}</strong><br>
    `;

    Object.entries(defectTypes).forEach(([k,v]) => {

        html += `
            <strong>${k} : ${v}</strong><br>
        `;
    });

    document.getElementById("defectsSummary").innerHTML = html;
}

function renderDefectGraph(results){

    let traces = {};

    results.forEach(item => {

        const lengthPos = Number(item.l);

        Object.entries(item.t || {}).forEach(([defect,count]) => {

            if (!traces[defect]) {

                traces[defect] = {
                    x: [],
                    y: [],
                    customdata: [],
                    mode: "markers",
                    type: "scatter",
                    name: defect,
                    marker: {
                        size: 10,
                        color: defectColors[defect] || "#555"
                    }
                };
            }

            for (let i=0; i<count; i++) {

                traces[defect].x.push(lengthPos);

                traces[defect].y.push(defect);

                traces[defect].customdata.push({
                    frame: item.r,
                    severity: item.s,
                    defect: defect,
                    length: item.l,
                    image: item.img
                });
            }
        });
    });

    const plotData = Object.values(traces);

    const layout = {

        margin: {
            l: 60,
            r: 20,
            t: 10,
            b: 50
        },

        xaxis: {
            title: "Length (mm)"
        },

        yaxis: {
            title: "Defect Type"
        },

        hovermode: "closest",

        height: 300
    };

    Plotly.newPlot(
        "defectGraph",
        plotData,
        layout,
        {
            responsive: true,
            displaylogo: false
        }
    );

    const graph =
        document.getElementById("defectGraph");

    graph.on("plotly_hover", function(){});

    graph.on("plotly_click", function(data){

        const point =
            data.points[0].customdata;

        openDefectImage(point);
    });

    plotData.forEach(trace => {

        trace.hovertemplate =
            "<b>Defect:</b> %{customdata.defect}<br>" +
            "<b>Frame:</b> %{customdata.frame}<br>" +
            "<b>Severity:</b> %{customdata.severity}<br>" +
            "<b>Length:</b> %{customdata.length}<br>" +
            "<extra></extra>";
    });
}

function openDefectImage(point){

    const srcPath = point.image || point.img || point.image_url || point.imgPath || '';
    if (!srcPath) {
        console.warn('No image path for point', point);
        return;
    }

    const imgUrl = "/api/serve-file?file=" + encodeURIComponent(srcPath);

    const frameNum = point.frame || point.r || '';
    const title = frameNum ? `Frame ${frameNum} | ${point.defect || ''}` : (point.defect || 'Defect');
    document.getElementById("vehicle_number").innerHTML = title;

    const modal = document.getElementById("imageModal");
    modal.dataset.src = imgUrl;
    new bootstrap.Modal(modal).show();
}

// Update on user activity
function resetActivityTimer() {
    lastActivityTime = Date.now();
}

// Listen to common user actions
['click', 'mousemove', 'keydown', 'scroll', 'touchstart'].forEach(event => {
    document.addEventListener(event, resetActivityTimer, true);
});

// Initialize
document.addEventListener('DOMContentLoaded', function() {

    // First run
    resetActivityTimer();
    fetchSystemStatus();
    fetchCurrentStatus();
    updateCameraImages();

    // Polling loop
    setInterval(fetchSystemStatus, 5000);
    setInterval(fetchCurrentStatus, 5000);
    setInterval(updateCameraImages, 2000);

    // Smart refresh
    setInterval(() => {
        const now = Date.now();
        const inactivityDuration = now - lastActivityTime;
    
        const REFRESH_TIMEOUT = 30 * 60 * 1000;
    
        if (inactivityDuration > REFRESH_TIMEOUT) {
            console.log("Auto-refreshing due to inactivity");
            window.location.reload();
        }
    }, 10 * 1000);

});

/* ---- CoilMap class ---- */
class SICoilMap {
    constructor(canvasId, tooltipId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        this.tooltip = document.getElementById(tooltipId);

        this.results = [];
        this.coilLengthMm = 8500000;
        this.currentPosMm = 0;
        this.zoom = 1.0;
        this.panX = 0;

        this.isDragging = false;
        this.dragStartX = 0;
        this.dragStartPan = 0;
        this.hoveredItem = null;

        this.COLORS = { V: '#00b4d8', D: '#ff4757', S: '#2ed573', C: '#ffa502' };
            this.filterTypes = { V: true, D: true, S: true, C: true };

        this._cssW = 0;
        this._cssH = 150;

        const resize = () => {
            const r = this.canvas.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            this._cssW = r.width;
            this._cssH = r.height || 150;
            this.canvas.width  = r.width  * dpr;
            this.canvas.height = this._cssH * dpr;
            this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        };

        const ro = new ResizeObserver(resize);
        ro.observe(this.canvas);
        resize();

        this._bindEvents();
        this._loop();
    }

    /* ---- Public API ---- */
    update(data) {
        this.results       = Array.isArray(data.result) ? data.result.flat(Infinity).filter(Boolean) : (data.result || []);
        this.coilLengthMm  = data.coil_length_mm || 8500000;
        this.currentPosMm  = data.current_position_mm || 0;
    }

    resetZoom() {
        this.zoom = 1.0;
        this.panX = 0;
    }

    /* ---- Coordinate helpers ---- */
    _mmToX(mm) {
        return (mm / this.coilLengthMm) * this._cssW * this.zoom - this.panX;
    }

    _xToMm(x) {
        return ((x + this.panX) / (this._cssW * this.zoom)) * this.coilLengthMm;
    }

    _clampPan() {
        this.panX = Math.max(0, Math.min(this._cssW * (this.zoom - 1), this.panX));
    }

    /* ---- Rendering ---- */
    _render() {
        if (!this._cssW) return;
        const ctx = this.ctx;
        const W   = this._cssW;
        const H   = this._cssH;

        ctx.clearRect(0, 0, W, H);

        /* dark background */
        ctx.fillStyle = '#090c12';
        ctx.fillRect(0, 0, W, H);

        /* subtle dot grid */
        ctx.fillStyle = 'rgba(255,255,255,0.025)';
        for (let x = 0; x < W; x += 30) {
            for (let y = 0; y < H; y += 20) {
                ctx.fillRect(x, y, 1, 1);
            }
        }

        const TRACK_TOP = 42;
        const TRACK_H   = 14;
        const TRACK_BOT = TRACK_TOP + TRACK_H;
        const SCALE_Y   = H - 18;
        const MAX_BAR_H = SCALE_Y - TRACK_BOT - 4;

        /* full coil track (uninspected / background) */
        const trkX0 = Math.max(0, this._mmToX(0));
        const trkX1 = Math.min(W, this._mmToX(this.coilLengthMm));
        if (trkX1 > trkX0) {
            ctx.fillStyle = '#1c2128';
            this._rr(ctx, trkX0, TRACK_TOP, trkX1 - trkX0, TRACK_H, 3);
            ctx.fill();

            /* metallic sheen */
            ctx.fillStyle = 'rgba(255,255,255,0.04)';
            ctx.fillRect(trkX0, TRACK_TOP + 1, trkX1 - trkX0, 3);
        }

        /* inspected portion overlay */
        if (this.currentPosMm > 0) {
            const ix  = Math.max(0, trkX0);
            const iEnd = Math.min(W, this._mmToX(this.currentPosMm));
            const iw  = iEnd - ix;
            if (iw > 0) {
                ctx.fillStyle = 'rgba(0,201,167,0.16)';
                this._rr(ctx, ix, TRACK_TOP, iw, TRACK_H, 3);
                ctx.fill();

                /* leading-edge highlight */
                ctx.fillStyle = 'rgba(0,201,167,0.55)';
                ctx.fillRect(Math.min(iEnd - 1, W - 1), TRACK_TOP, 1.5, TRACK_H);
            }
        }

        /* defect spikes (hang down from track) */
        this.results.forEach(item => {
            if (!item.t || Object.keys(item.t).length === 0) return;
            const x = this._mmToX(Number(item.l));
            if (x < -6 || x > W + 6) return;

            const isHov = (this.hoveredItem === item);
            let yOff = TRACK_BOT + 1;

            Object.entries(item.t).forEach(([type, count]) => {
                if (!this.filterTypes[type]) return; // skip filtered-out types
                if (count <= 0) return;
                const color = this.COLORS[type] || '#8b949e';
                const barH  = Math.min(count * 11 + 6, MAX_BAR_H * 0.78);

                if (isHov) { ctx.shadowColor = color; ctx.shadowBlur = 12; }

                ctx.fillStyle = color;
                ctx.globalAlpha = isHov ? 1.0 : 0.80;

                /* bar */
                ctx.fillRect(x - 1.5, yOff, 3, barH);

                /* tip dot */
                ctx.beginPath();
                ctx.arc(x, yOff + barH, isHov ? 4.5 : 2.5, 0, Math.PI * 2);
                ctx.fill();

                ctx.shadowBlur = 0;
                ctx.globalAlpha = 1.0;
                yOff += barH + 2;
            });
        });

        /* scanner beam at current position */
        const scanX = this._mmToX(this.currentPosMm);
        if (this.currentPosMm > 0 && scanX >= 0 && scanX <= W) {
            const t = Date.now() / 750;
            const a = 0.5 + 0.5 * Math.sin(t);

            /* soft halo */
            ctx.fillStyle = `rgba(0,201,167,${a * 0.18})`;
            ctx.fillRect(Math.max(0, scanX - 8), 0, 16, SCALE_Y);

            /* solid beam line */
            ctx.strokeStyle = `rgba(0,201,167,${a * 0.9 + 0.1})`;
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(scanX, 0);
            ctx.lineTo(scanX, SCALE_Y);
            ctx.stroke();

            /* ? pointer above track */
            ctx.fillStyle = `rgba(0,201,167,${a})`;
            ctx.font = '700 10px "Courier New"';
            ctx.textAlign = 'center';
            ctx.fillText('?', scanX, TRACK_TOP - 5);
            ctx.textAlign = 'left';
        }

        /* scale */
        this._drawScale(SCALE_Y, W);
    }

    _drawScale(y, W) {
        const ctx = this.ctx;
        const visibleMm = (W / (this._cssW * this.zoom)) * this.coilLengthMm;

        const steps = [50000, 100000, 200000, 500000, 1000000, 2000000, 5000000];
        let interval = steps[steps.length - 1];
        for (const s of steps) {
            if (visibleMm / s <= 14) { interval = s; break; }
        }

        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();

        const startMm = Math.floor(this._xToMm(0) / interval) * interval;
        ctx.font = '9px "Courier New"';
        ctx.textAlign = 'center';

        for (let mm = startMm; mm <= this.coilLengthMm + interval; mm += interval) {
            if (mm < 0) continue;
            const x = this._mmToX(mm);
            if (x < 0 || x > W) continue;

            ctx.strokeStyle = 'rgba(255,255,255,0.12)';
            ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y + 5); ctx.stroke();

            const lbl = mm >= 1000000
                ? (mm / 1000000).toFixed(mm % 1000000 === 0 ? 0 : 1) + ' km'
                : (mm / 1000).toFixed(0) + ' m';
            ctx.fillStyle = '#3d444d';
            ctx.fillText(lbl, x, y + 14);
        }
        ctx.textAlign = 'left';
    }

    _rr(ctx, x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + w - r, y);
        ctx.arcTo(x + w, y, x + w, y + r, r);
        ctx.lineTo(x + w, y + h - r);
        ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
        ctx.lineTo(x + r, y + h);
        ctx.arcTo(x, y + h, x, y + h - r, r);
        ctx.lineTo(x, y + r);
        ctx.arcTo(x, y, x + r, y, r);
        ctx.closePath();
    }

    _loop() {
        this._render();
        requestAnimationFrame(() => this._loop());
    }

    /* ---- Interaction ---- */
    _hitTest(mx, my) {
        const TRACK_BOT = 42 + 14;
        const SCALE_Y   = this._cssH - 18;
        const MAX_BAR_H = SCALE_Y - TRACK_BOT - 4;
        const RADIUS    = 10;

        for (const item of this.results) {
            if (!item.t || Object.keys(item.t).length === 0) continue;
            const x = this._mmToX(Number(item.l));
            if (Math.abs(mx - x) > RADIUS) continue;

            const totalBarH = Object.values(item.t).reduce((acc, count) => {
                return acc + Math.min(count * 11 + 6, MAX_BAR_H * 0.78) + 2;
            }, 0);

            if (my >= TRACK_BOT - 6 && my <= TRACK_BOT + totalBarH + 12) {
                return item;
            }
        }
        return null;
    }

    _bindEvents() {
        /* zoom on scroll */
        this.canvas.addEventListener('wheel', (e) => {
            e.preventDefault();
            const rect = this.canvas.getBoundingClientRect();
            const mx   = e.clientX - rect.left;
            const mmAt = this._xToMm(mx);
            const factor = e.deltaY < 0 ? 1.3 : 1 / 1.3;
            this.zoom = Math.max(1, Math.min(300, this.zoom * factor));
            this.panX = (mmAt / this.coilLengthMm) * this._cssW * this.zoom - mx;
            this._clampPan();
        }, { passive: false });

        /* drag pan */
        this.canvas.addEventListener('mousedown', (e) => {
            this.isDragging   = true;
            this.dragStartX   = e.clientX;
            this.dragStartPan = this.panX;
            this.canvas.style.cursor = 'grabbing';
        });
        document.addEventListener('mousemove', (e) => {
            if (!this.isDragging) return;
            this.panX = this.dragStartPan + (this.dragStartX - e.clientX);
            this._clampPan();
        });
        document.addEventListener('mouseup', () => {
            if (this.isDragging) {
                this.isDragging = false;
                this.canvas.style.cursor = 'crosshair';
            }
        });

        /* hover tooltip */
        this.canvas.addEventListener('mousemove', (e) => {
            if (this.isDragging) return;
            const rect = this.canvas.getBoundingClientRect();
            const mx = e.clientX - rect.left;
            const my = e.clientY - rect.top;
            const hit = this._hitTest(mx, my);
            this.hoveredItem = hit;

            if (hit) {
                const tx = Math.min(mx + 14, this._cssW - 165);
                const ty = Math.max(my - 90, 2);
                this.tooltip.style.left    = tx + 'px';
                this.tooltip.style.top     = ty + 'px';
                this.tooltip.style.display = 'block';

                const defStr = Object.entries(hit.t || {})
                    .map(([k, v]) => `<span style="color:${this.COLORS[k] || '#aaa'}">${k}</span>: ${v}`)
                    .join('&nbsp; ');
                const sevCls = (hit.s || '').toLowerCase();

                this.tooltip.innerHTML = `
                    <div class="tt-frame">Frame #${hit.r}</div>
                    <div class="tt-pos">${(Number(hit.l) / 1000).toFixed(1)} m from start</div>
                    <div>Severity: <span class="sev-${sevCls}">${hit.s || '?'}</span></div>
                    <div style="margin-top:3px">${defStr}</div>
                    <div class="tt-click">Click to view image</div>
                `;
                this.canvas.style.cursor = 'pointer';
            } else {
                this.tooltip.style.display = 'none';
                if (!this.isDragging) this.canvas.style.cursor = 'crosshair';
            }
        });

        this.canvas.addEventListener('mouseleave', () => {
            this.hoveredItem = null;
            this.tooltip.style.display = 'none';
        });

        this.canvas.addEventListener('click', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const hit  = this._hitTest(e.clientX - rect.left, e.clientY - rect.top);
            if (hit) openDefectImage(hit);
        });
    }
}

/* ---- Dashboard update function ---- */
let siCoilMap = null;

function updateSIDashboard(data) {
    const statusEl = document.getElementById('siStatus');
    if (statusEl) {
        statusEl.textContent  = (data.status || 'IDLE').toUpperCase();
        statusEl.className    = 'si-chip-value si-mono';
        const MAP = { processing: 'si-status-processing', idle: 'si-status-idle', complete: 'si-status-complete', error: 'si-status-error' };
        const cls = MAP[(data.status || '').toLowerCase()];
        if (cls) statusEl.classList.add(cls);
    }

    const coilLen = data.coil_length_mm || 8500000;
    const curPos  = data.current_position_mm || 0;

    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

    set('siCoilId',    data.coil_id   || '?');
    set('siCycleUid',  (data.cycle_uid || '?').slice(-16));  /* truncate long UIDs */
    set('siSpeed',     data.speed_mpm ? `${data.speed_mpm} m/min` : '? m/min');
    set('siPosition',  (curPos / 1000000).toFixed(3) + ' km');
    set('siCoverage',  coilLen > 0 ? ((curPos / coilLen) * 100).toFixed(1) + '%' : '0.0%');
    set('siCoilLen',   (coilLen / 1000000).toFixed(1) + ' km total');
    set('siUpdated',   data.last_updated ? new Date(data.last_updated).toLocaleTimeString() : '?');

    /* stats */
    set('siStatFrames',    data.total_frames              || 0);
    set('siStatDefects',   data.total_defects             || 0);
    set('siStatDefFrames', data.total_defective_frames    || 0);

    /* defect type breakdown */
    const types  = {};
    const COLORS = { V: '#00b4d8', D: '#ff4757', S: '#2ed573', C: '#ffa502' };
    (data.result || []).forEach(frame => {
        Object.entries(frame.t || {}).forEach(([k, v]) => { types[k] = (types[k] || 0) + v; });
    });

    const typesEl = document.getElementById('siStatTypes');
    if (typesEl) {
        if (Object.keys(types).length === 0) {
            typesEl.innerHTML = '<div class="si-stat-lbl" style="margin:auto 0">NO DEFECTS</div>';
        } else {
            typesEl.innerHTML = Object.entries(types)
                .map(([k, v]) => `
                    <div class="si-type-row">
                        <span class="si-type-dot" style="background:${COLORS[k] || '#8b949e'}"></span>
                        <span class="si-type-name">${k}</span>
                        <span class="si-type-count">${v}</span>
                    </div>`)
                .join('');
        }
    }

    /* coil map */
    if (siCoilMap) {
        siCoilMap.update(data);
        // populate insights
        try {
            const results = data.result || [];
            const totals = {};
            let lastPos = '-';
            const sevMap = { HIGH: 3, MEDIUM: 2, LOW: 1 };
            let sevSum = 0, sevCount = 0;
            results.forEach(fr => {
                Object.entries(fr.t || {}).forEach(([k, v]) => { totals[k] = (totals[k] || 0) + v; });
                if (fr.l) lastPos = (Number(fr.l) / 1000).toFixed(1) + ' m';
                if (fr.s && sevMap[fr.s.toUpperCase()]) { sevSum += sevMap[fr.s.toUpperCase()]; sevCount++; }
            });
            const topType = Object.entries(totals).sort((a,b)=>b[1]-a[1])[0];
            document.getElementById('ins-top-type').textContent = topType ? `Top: ${topType[0]} (${topType[1]})` : 'Top: -';
            document.getElementById('ins-last-pos').textContent = lastPos;
            document.getElementById('ins-avg-sev').textContent = sevCount ? ( (sevSum / sevCount) .toFixed(2) ) : '-';
        } catch(e) { console.warn('insights:', e); }
    }

    /* Plotly scatter (dark theme) */
    renderDefectGraphDark(data.result || []);
}

/* ---- Dark Plotly override ---- */
function renderDefectGraphDark(results) {
    /* reuse the existing trace-building logic but with dark layout */
    const traces = {};

    // flatten nested results (some cycles return nested arrays)
    const flat = Array.isArray(results) ? results.flat(Infinity) : [];

    flat.forEach(item => {
        // for each defect class, prefer per-box pos_mm if available
        ['V','D','S','C'].forEach(defect => {
            const boxes = item[defect] || [];
            if (!boxes || boxes.length === 0) return;

            if (!traces[defect]) {
                traces[defect] = { x: [], y: [], customdata: [], mode: 'markers', type: 'scatter', name: defect, marker: { size: 9, color: defectColors[defect] || '#8b949e' } };
            }

            boxes.forEach(box => {
                const pos = (box.pos_mm !== undefined) ? Number(box.pos_mm) : (Number(item.l) || 0);
                traces[defect].x.push(pos);
                traces[defect].y.push(defect);
                traces[defect].customdata.push({ frame: item.r, severity: item.s, defect, length: pos, image: item.img });
            });
        });
    });

    Object.values(traces).forEach(t => {
        t.hovertemplate =
            '<b>Defect:</b> %{customdata.defect}<br>' +
            '<b>Frame:</b> %{customdata.frame}<br>' +
            '<b>Severity:</b> %{customdata.severity}<br>' +
            '<b>Position:</b> %{customdata.length} mm<br><extra></extra>';
    });

    const plotData = Object.values(traces);

    // set height to camera frame width to give a square-ish visual as requested
    const cameraEl = document.getElementById('camera-slot-1');
    const camW = cameraEl ? cameraEl.clientWidth : 400;

    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor:  '#0a0d12',
        font:          { color: '#7b8794', family: '"Courier New", monospace', size: 11 },
        margin:        { l: 50, r: 14, t: 8, b: 44 },
        height:        camW,
        xaxis: {
            title: { text: 'Position (mm)', font: { color: '#485060', size: 10 } },
            gridcolor:  '#161b22',
            linecolor:  '#21262d',
            tickcolor:  '#21262d',
            zerolinecolor: '#21262d',
            color: '#485060',
        },
        yaxis: {
            title: { text: 'Type', font: { color: '#485060', size: 10 } },
            gridcolor:  '#161b22',
            linecolor:  '#21262d',
            tickcolor:  '#21262d',
            color: '#485060',
        },
        legend: {
            bgcolor: 'transparent',
            font:    { color: '#7b8794', size: 10 },
        },
        hovermode: 'closest',
    };

    const graphEl = document.getElementById('defectGraph');
    if (!graphEl) return;

    Plotly.react('defectGraph', plotData, layout, { responsive: true, displaylogo: false });

    graphEl.on && graphEl.on('plotly_click', function(evtData) {
        const point = evtData.points[0].customdata;
        openDefectImage(point);
    });
}

/* ---- Override fetchCurrentStatus to hit live dashboard ---- */
async function fetchCurrentStatus() {
    try {
        const response = await fetch('/api/get_current_status/');
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const data = await response.json();

        /* update new SI dashboard */
        updateSIDashboard(data);

        /* keep legacy hidden elements in sync */
        const safe = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        safe('currentState', (data.status || 'IDLE').toUpperCase());
        safe('coil_id',      data.coil_id   || '');
        safe('cycle_uid',    data.cycle_uid  || '');

    } catch (err) {
        let a;
        // console.error('[fetchCurrentStatus]', err);
    }
}

/* ---- Initialise on DOM ready ---- */
document.addEventListener('DOMContentLoaded', function () {
    /* instantiate the coil map after a short tick so canvas has layout size */
    setTimeout(() => {
        const canvas = document.getElementById('siCoilCanvas');
        if (canvas) {
            siCoilMap = new SICoilMap('siCoilCanvas', 'siCoilTooltip');
        }

        /* wire reset button */
        const resetBtn = document.getElementById('siZoomReset');
        if (resetBtn) resetBtn.addEventListener('click', () => { if (siCoilMap) siCoilMap.resetZoom(); });

        /* wire filter buttons */
        const filterBtns = document.querySelectorAll('.si-filter-btn');
        filterBtns.forEach(btn => {
            btn.addEventListener('click', (ev) => {
                const type = btn.dataset.type;
                if (!siCoilMap) return;

                if (type === 'ALL') {
                    const setActive = !btn.classList.contains('active');
                    filterBtns.forEach(b => {
                        b.classList.toggle('active', setActive);
                    });
                    // set all types
                    const on = setActive;
                    siCoilMap.filterTypes = { V: on, D: on, S: on, C: on };
                } else {
                    const isActive = btn.classList.toggle('active');
                    // ensure ALL button reflects whether all specific types are active
                    const specific = Array.from(document.querySelectorAll('.si-filter-btn[data-type]'))
                        .filter(b => b.dataset.type !== 'ALL');
                    const allOn = specific.every(b => b.classList.contains('active'));
                    const allBtn = document.querySelector('.si-filter-btn[data-type="ALL"]');
                    if (allBtn) allBtn.classList.toggle('active', allOn);

                    // update map filter
                    siCoilMap.filterTypes = {
                        V: document.querySelector('.si-filter-btn[data-type="V"]').classList.contains('active'),
                        D: document.querySelector('.si-filter-btn[data-type="D"]').classList.contains('active'),
                        S: document.querySelector('.si-filter-btn[data-type="S"]').classList.contains('active'),
                        C: document.querySelector('.si-filter-btn[data-type="C"]').classList.contains('active')
                    };
                }
            });
        });

        /* activate DASHBOARD tab by default if none is active */
        const firstBtn = document.querySelector('.nav-link[data-bs-target="#DASHBOARD"]');
        const firstPane = document.getElementById('DASHBOARD');
        if (firstBtn && !document.querySelector('.nav-link.active')) {
            firstBtn.classList.add('active');
        }
        if (firstPane && !firstPane.classList.contains('active')) {
            firstPane.classList.add('active');
        }

    }, 120);
});