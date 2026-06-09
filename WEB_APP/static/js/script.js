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

    const imgUrl =
        "/api/serve-file?file=" +
        encodeURIComponent(point.image);

    document.getElementById("vehicle_number").innerHTML =
        `Frame ${point.frame} | ${point.defect}`;

    const modal =
        document.getElementById("imageModal");

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