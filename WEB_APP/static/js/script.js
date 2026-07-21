// ======================== GLOBALS ========================
let currentPage = 1;
let scale = 1;
let isDragging = false;
let startX, startY, imgX = 0, imgY = 0;
let isImageLoading = false;
let statusCheckInterval = null;
let systemData = {};  
let modalOpen = false; 
let rdCoilMap = null;
let rdPendingData = null; 
const DEFECT_NAMES = {
    'O': 'Oil Stain',
    'W': 'White Spot',
    'D': 'Dent',
    'S': 'Scoring',
    'C': 'Scratch'
};

// DOM elements
const modalImg = document.getElementById("modalImage");
const zoomWrapper = document.getElementById("zoomWrapper");
const imageLoader = document.getElementById("imageLoader");

// Defect colour mapping
const defectColors = {
    O: "#0000ff", // OIL_STAIN (BGR: 255,0,0 -> RGB: 0,0,255) Blue
    W: "#808080", // WHITE_SPOT (128,128,128) Gray
    S: "#ffff00", // SCORING (0,255,255) Yellow
    D: "#ff0000", // DENT (0,0,255) Red
    C: "#00ff00"  // SCRATCH (0,255,0) Green
};

// ======================== CSRF TOKEN ========================
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
        const cookies = document.cookie.split(";");
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + "=")) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}
const csrftoken = getCookie("csrftoken");

// ======================== IMAGE MODAL (ZOOM + DRAG) ========================
function resetZoomAndPosition() {
    scale = 1;
    imgX = imgY = 0;
    modalImg.style.transform = `translate(0px, 0px) scale(1)`;
}

// Reset zoom when modal opens (keep this)
document.getElementById("imageModal").addEventListener("shown.bs.modal", function () {
    resetZoomAndPosition();   // your existing zoom reset function

    // LOAD THE IMAGE FROM dataset.src
    const src = this.dataset.src;
    if (!src) return;

    isImageLoading = true;
    imageLoader.style.display = "block";
    modalImg.style.display = "none";

    modalImg.onload = function () {
        imageLoader.style.display = "none";
        modalImg.style.display = "block";
        isImageLoading = false;
    };
    modalImg.onerror = function () {
        imageLoader.style.display = "none";
        modalImg.style.display = "none";
        isImageLoading = false;
        console.error("Failed to load image:", src);
    };
    modalImg.src = src;
    modalOpen = true;
});

zoomWrapper.addEventListener("wheel", (e) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.1 : -0.1;
    scale = Math.min(Math.max(0.5, scale + delta), 4);
    modalImg.style.transform = `translate(${imgX}px, ${imgY}px) scale(${scale})`;
});

zoomWrapper.addEventListener("mousedown", (e) => {
    isDragging = true;
    zoomWrapper.style.cursor = "grabbing";
    startX = e.clientX - imgX;
    startY = e.clientY - imgY;
});
zoomWrapper.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    imgX = e.clientX - startX;
    imgY = e.clientY - startY;
    modalImg.style.transform = `translate(${imgX}px, ${imgY}px) scale(${scale})`;
});
zoomWrapper.addEventListener("mouseup", () => {
    isDragging = false;
    zoomWrapper.style.cursor = "grab";
});
zoomWrapper.addEventListener("mouseleave", () => {
    isDragging = false;
});

// Clean up when modal is hidden
document.getElementById("imageModal").addEventListener("hidden.bs.modal", function () {
    if (modalImg.src && modalImg.src.startsWith("blob:")) {
        URL.revokeObjectURL(modalImg.src);
    }
    modalImg.src = "";
    modalImg.style.display = "none";
    imageLoader.style.display = "none";
    modalOpen = false;
    isImageLoading = false;
});

// Open / update image modal (reuses existing modal if already open)
function openImageModal(imgUrl, title) {
    const modalEl = document.getElementById("imageModal");
    if (!modalEl) return;
    if (title) document.getElementById("vehicle_number").innerHTML = title;
    modalEl.dataset.src = imgUrl || "";

    if (!modalOpen) {
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    } else {
        // If already open, reload image manually (without waiting for 'shown')
        if (!imgUrl) return;
        isImageLoading = true;
        imageLoader.style.display = "block";
        modalImg.style.display = "none";
        modalImg.onload = function () {
            imageLoader.style.display = "none";
            modalImg.style.display = "block";
            isImageLoading = false;
        };
        modalImg.onerror = function () {
            imageLoader.style.display = "none";
            modalImg.style.display = "none";
            isImageLoading = false;
        };
        modalImg.src = imgUrl;
    }
}

// Helper for defect clicks
// Helper for defect clicks
function openDefectImage(point) {
    const rawPath = point.rawImg || "";
    const fallback = point.img || point.image || point.image_url || "";
    const frameNum = point.frame || point.r || "";

    const title = frameNum
        ? `Frame ${frameNum} | ${point.length != null ? Math.floor(point.length / 1000) : ""} m`
        : (DEFECT_NAMES[point.type] || point.type || "Defect");

    console.log("openDefectImage called with:", { point, rawPath, fallback, title });

    if (rawPath && point.box) {
        const rawUrl = "/api/serve-file?file=" + encodeURIComponent(rawPath);
        const tmpImg = new Image();
        tmpImg.crossOrigin = "anonymous";

        tmpImg.onload = function () {
            const canvas = document.createElement("canvas");
            canvas.width = tmpImg.naturalWidth;
            canvas.height = tmpImg.naturalHeight;

            const ctx = canvas.getContext("2d");
            ctx.drawImage(tmpImg, 0, 0);

            const imgW = tmpImg.naturalWidth;
            const centerX = imgW / 2;
            const mmPerPixel = 300 / imgW;

            // box format: [x1, x2, y1, y2]
            const [bx1, bx2, by1, by2] = point.box;

            const defectCenterX = (bx1 + bx2) / 2;
            const offsetPx = defectCenterX - centerX;
            const offsetMm = Math.abs(offsetPx * mmPerPixel).toFixed(1);

            const side = offsetPx < 0 ? "OS" : "DS";

            const defectName = (DEFECT_NAMES[point.type] || point.type || "DEFECT").toUpperCase();
            const label = `${side} ${defectName} ${offsetMm}mm`;

            const color = defectColors[point.type] || "#ffffff";

            // Draw bounding box
            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.strokeRect(
                bx1,
                by1,
                bx2 - bx1,
                by2 - by1
            );

            // Draw label box
            ctx.font = "bold 18px monospace";
            const textWidth = ctx.measureText(label).width;
            const textHeight = 26;

            let labelX = bx1;
            let labelY = by1 - 8;

            // Prevent overflow at top
            if (labelY < textHeight) {
                labelY = by1 + textHeight;
            }

            ctx.fillStyle = color;
            ctx.fillRect(
                labelX,
                labelY - textHeight,
                textWidth + 12,
                textHeight
            );

            // Draw label text
            ctx.fillStyle = "#000000";
            ctx.fillText(label, labelX + 6, labelY - 6);

            // openImageModal(canvas.toDataURL("image/jpeg", 0.92), title);
            canvas.toBlob(function (blob) {
                if (!blob) {
                    console.error("BOX DEBUG -> toBlob failed, canvas too large:", canvas.width, canvas.height);
                    if (fallback) {
                        openImageModal("/api/serve-file?file=" + encodeURIComponent(fallback), title);
                    }
                    return;
                }
                const blobUrl = URL.createObjectURL(blob);
                openImageModal(blobUrl, title);
            }, "image/jpeg", 0.92);
        };

        tmpImg.onerror = function () {
            if (!fallback) return;
            openImageModal(
                "/api/serve-file?file=" + encodeURIComponent(fallback),
                title
            );
        };

        tmpImg.src = rawUrl;
        return;
    }

    // fallback
    if (!fallback) {
        console.warn("No image path for point", point);
        return;
    }

    openImageModal(
        "/api/serve-file?file=" + encodeURIComponent(fallback),
        title
    );
}

// ======================== DASHBOARD: CoilMap (optimised) ========================
class CoilMap {
    constructor(canvasId, tooltipId, startLabelId = "coilStart", endLabelId = "coilEnd") {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext("2d");
        this.tooltip = document.getElementById(tooltipId);
        this.defects = [];
        this.minLen = 0;
        this.maxLen = 1;
        this.scaleX = 1;
        this.offsetX = 0;
        this.dragging = false;
        this.dragStartX = 0;
        this.hasMoved = false;
        this.filters = { W: true,  O: true,  D: true, S: true, C: true };
        this.visiblePoints = [];
        this.startLabelId = startLabelId;
        this.endLabelId = endLabelId;

        this.resize();
        window.addEventListener("resize", () => this.resize());
        this.bindEvents();
    }

    resize() {
        this.canvas.width = this.canvas.offsetWidth;
        this.canvas.height = 250;
        this.render();
    }

    update(data) {
        this.rawData = data.result || [];
        this.prepareData();
        this.render();
    }

    prepareData() {
        this.defects = [];
        (this.rawData || []).forEach(frame => {
                ["O", "W", "D", "S", "C"].forEach(type => {
                if (frame[type]) {
                        frame[type].forEach(defect => {
                            this.defects.push({
                                type,
                                frame: frame.r,
                            length: parseInt(frame.l || 0),
                                widthPos: defect.pos_mm || 0,
                                confidence: defect.c || 0,
                                img: frame.img || null,
                                rawImg: frame.raw_img || null,
                                box: defect.b || null,
                            });
                        });
                    }
                });
        });
        if (this.defects.length === 0) {
            this.minLen = 0;
            this.maxLen = 1;
        } else {
            this.minLen = Math.min(...this.defects.map(d => d.length));
            this.maxLen = Math.max(...this.defects.map(d => d.length));
        }
        const _s = document.getElementById(this.startLabelId);
        const _e = document.getElementById(this.endLabelId);
        if (_s) _s.innerText = `${this.minLen != null ? Math.floor(this.minLen / 1000) : "0"} m`
        if (_e) _e.innerText = `${this.maxLen != null ? Math.floor(this.maxLen / 1000) : "1"} m`
    }

    getColor(type) {
        return defectColors[type] || "#aaa";
    }

    mapPoint(defect) {
        const normX = (defect.length - this.minLen) / (this.maxLen - this.minLen || 1);
        const x = (normX * this.canvas.width * this.scaleX) + this.offsetX;
        const normY = (defect.widthPos + 150) / 300;
        const y = this.canvas.height - (normY * this.canvas.height);
        return { x, y };
    }

    render() {
        const ctx = this.ctx;
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.fillStyle = "#0f172a";
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

        this.visiblePoints = [];

        const R = 3;              // radius 3 -> 2 (diameter reduced by 2 units: 6px -> 4px)
        const CELL = R * 2;       // grid cell = point diameter, guarantees no overlap
        const cols = Math.ceil(this.canvas.width / CELL) + 4;
        const occupied = new Set();

        for (let defect of this.defects) {
            if (!this.filters[defect.type]) continue;
            const p = this.mapPoint(defect);
            if (p.x < -10 || p.x > this.canvas.width + 10) continue;

            // Snap to a collision grid; if the cell is taken, spiral outward
            // to the nearest free cell so points never overlap.
            let cx = Math.round(p.x / CELL);
            let cy = Math.round(p.y / CELL);
            let key = cy * cols + cx;

            if (occupied.has(key)) {
                let placed = false;
                for (let ring = 1; ring <= 8 && !placed; ring++) {
                    for (let dy = -ring; dy <= ring && !placed; dy++) {
                        for (let dx = -ring; dx <= ring && !placed; dx++) {
                            if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
                            const ny = cy + dy, nx = cx + dx;
                            const py = ny * CELL;
                            if (py < R || py > this.canvas.height - R) continue;
                            const nKey = ny * cols + nx;
                            if (!occupied.has(nKey)) {
                                cx = nx; cy = ny; key = nKey; placed = true;
                            }
                        }
                    }
                }
            }
            occupied.add(key);

            const fx = cx * CELL;
            const fy = Math.min(Math.max(cy * CELL, R), this.canvas.height - R);

            this.visiblePoints.push({ ...defect, x: fx, y: fy });
            ctx.beginPath();
            ctx.arc(fx, fy, R, 0, Math.PI * 2);
            ctx.fillStyle = this.getColor(defect.type);
            ctx.fill();
        }
    }

    bindEvents() {
        this.canvas.addEventListener("wheel", (e) => {
            e.preventDefault();
            const zoomFactor = e.deltaY < 0 ? 1.15 : 0.85;
            this.scaleX = Math.max(1, Math.min(20, this.scaleX * zoomFactor));
            this.render();
        });
        this.canvas.addEventListener("mousedown", (e) => {
            this.dragging = true;
            this.hasMoved = false;
            this.dragStartX = e.clientX - this.offsetX;
        });
        this.canvas.addEventListener("mousemove", (e) => {
            if (this.dragging) {
                const newOffset = e.clientX - this.dragStartX;
                if (Math.abs(newOffset - this.offsetX) > 2) this.hasMoved = true;
                this.offsetX = newOffset;
                this.render();
                return;
            }
            const rect = this.canvas.getBoundingClientRect();
            const mx = e.clientX - rect.left;
            const my = e.clientY - rect.top;
            let found = false;
            for (let defect of this.visiblePoints) {
                const dx = mx - defect.x, dy = my - defect.y;
                if (Math.hypot(dx, dy) < 8) {
                    this.tooltip.style.display = "block";
                    // Use mouse coordinates relative to viewport (fixed positioning)
                    this.tooltip.style.left = (e.clientX + 15) + "px";
                    this.tooltip.style.top  = (e.clientY + 15) + "px";
                    const defectFullName = DEFECT_NAMES[defect.type] || defect.type;
                    this.tooltip.innerHTML = `<b>${defectFullName}</b><br>Frame: ${defect.frame}<br>Length: ${defect.length} mm<br>Width: ${defect.widthPos} mm`;
                    found = true;
                    break;
                }
            }
            if (!found) this.tooltip.style.display = "none";
        });
        this.canvas.addEventListener("mouseup", () => { this.dragging = false; });
        this.canvas.addEventListener("mouseleave", () => { this.dragging = false; });
        this.canvas.addEventListener("click", (e) => {
            if (this.hasMoved) return;
            const rect = this.canvas.getBoundingClientRect();
            const mx = e.clientX - rect.left;
            const my = e.clientY - rect.top;
            for (let defect of this.visiblePoints) {
                if (Math.hypot(mx - defect.x, my - defect.y) < 8 && defect.img) {
                    openDefectImage(defect);
                    break;
                }
            }
        });
    }

    resetZoom() {
        this.scaleX = 1;
        this.offsetX = 0;
        this.render();
    }

    toggleFilter(type) {
        this.filters[type] = !this.filters[type];
        this.render();
    }
}

let coilMap = null;

// ======================== DASHBOARD: LIVE STATUS UPDATE ========================
function updateSIDashboard(data) {

    // Status & coil ID
    const statusEl = document.getElementById("siStatus");
    if (statusEl) {
        statusEl.textContent = (data.status || "IDLE").toUpperCase();
        statusEl.className = "si-chip-value si-mono";
        const clsMap = { processing: "si-status-processing", idle: "si-status-idle", complete: "si-status-complete", error: "si-status-error" };
        if (clsMap[(data.status || "").toLowerCase()]) statusEl.classList.add(clsMap[(data.status || "").toLowerCase()]);
    }
    document.getElementById("siCoilId").innerText = data.coil_id || "?";

    // ----- FIXED TOTAL LENGTH DISPLAY -----
    let totalLengthKm = 0;
    let coilLengthMm = Number(data.coil_length_mm);
    
    if (!isNaN(coilLengthMm) && coilLengthMm > 0) {
        totalLengthKm = coilLengthMm / 1000000;
    } else {
        // Fallback: compute max length from defect positions (field 'l')
        let maxPos = 0;
        if (Array.isArray(data.result)) {
            data.result.forEach(item => {
                let pos = Number(item.l);
                if (!isNaN(pos) && pos > maxPos) maxPos = pos;
            });
        }
        totalLengthKm = maxPos / 1000000;
    }
    
    const lengthElem = document.getElementById("siCoilLen");
    if (lengthElem) {
        lengthElem.innerText = `${totalLengthKm.toFixed(1)} km total`;
    }

    // Speed & position
    let speedText = "?";
    if (data.total_frames && data.start_time) {
        const elapsed = Math.max(1, (Date.now() - new Date(data.start_time).getTime()) / 1000);
        speedText = `${(data.total_frames / elapsed).toFixed(2)} fps`;
    } else if (data.speed_mpm !== undefined) speedText = `${data.speed_mpm} m/min`;
    // document.getElementById("siSpeed").innerText = speedText;
    document.getElementById("siPosition").innerText = `${((data.current_position_mm || 0) / 1000).toFixed(0)} m`;
    document.getElementById("siUpdated").innerText = data.last_updated ? new Date(data.last_updated).toLocaleTimeString() : "--";

    // Stats
    document.getElementById("siStatFrames").innerText = data.total_frames || 0;
    document.getElementById("siStatDefects").innerText = data.total_defects || 0;
    document.getElementById("siStatDefFrames").innerText = data.total_defective_frames || 0;

    // Defect breakdown
    const typeCounts = { O: 0,  W: 0, D: 0, S: 0, C: 0 };
    (data.result || []).forEach(frame => {
        Object.entries(frame.t || {}).forEach(([k, v]) => { if (typeCounts[k] !== undefined) typeCounts[k] += v; });
    });
    document.getElementById("siStatO").innerText = typeCounts.O;
    document.getElementById("siStatW").innerText = typeCounts.W;
    document.getElementById("siStatD").innerText = typeCounts.D;
    document.getElementById("siStatS").innerText = typeCounts.S;
    document.getElementById("siStatC").innerText = typeCounts.C;

    // Update coil map
    if (coilMap) coilMap.update(data);
}

async function fetchCurrentStatus() {
    try {
        const resp = await fetch("/api/get_current_status/");
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const data = await resp.json();
        updateSIDashboard(data);
        // Legacy hidden fields (for any old code)
        document.getElementById("currentState").innerText = (data.status || "IDLE").toUpperCase();
        document.getElementById("coil_id").innerText = data.coil_id || "";
        document.getElementById("cycle_uid").innerText = data.cycle_uid || "";
    } catch (err) {
        console.error("fetchCurrentStatus error:", err);
    }
}

// ======================== DASHBOARD: LIVE CAMERA FEED ========================
function updateCameraImages() {
    fetch("/api/live-ip-camera")
        .then(res => res.json())
        .then(data => {
            if (data.status === "success") {
                data.cameras.forEach((cam, idx) => {
                    const slot = document.getElementById(`camera-slot-${idx + 1}`);
                    if (!slot) return;
                    let img = slot.querySelector("img");
                    if (cam.image_url) {
                        if (!img) {
                            img = document.createElement("img");
                            slot.innerHTML = "";
                            slot.appendChild(img);
                        }
                        img.src = `${cam.image_url}?t=${Date.now()}`;
                    } else {
                        slot.innerHTML = "No Camera Selected";
                    }
                });
            }
        })
        .catch(err => console.error("Camera feed error:", err));
}

// Return the Taloja server's health status: "ACTIVE" / "INACTIVE" / "DISABLED" / null
function getTalojaServerStatus() {
    if (!systemData || typeof systemData !== "object") return null;
    for (const [key, info] of Object.entries(systemData)) {
        const isServer = (info.type || "").toUpperCase() === "SERVER";
        const isTaloja = key.toUpperCase().includes("TALOJA");
        if (isServer && isTaloja) return (info.status || "").toUpperCase();
    }
    return null;
}

// Dashboard LIVE badge: green "LIVE" when server ACTIVE, blue "Idle" otherwise
function updateLiveBadge() {
    const badge = document.getElementById("siBadge");
    if (!badge) return;
    const textEl = badge.querySelector(".si-live-text");
    const isActive = getTalojaServerStatus() === "ACTIVE";
    if (isActive) {
        badge.classList.remove("idle");
        if (textEl) textEl.textContent = "LIVE";
    } else {
        badge.classList.add("idle");
        if (textEl) textEl.textContent = "IDLE";
    }
}

// ======================== HEALTH TAB ========================
function updateSystemStatus() {
    const cameraContainer = document.getElementById("camera-indicators");
    const locationContainer = document.getElementById("location-dots");
    if (!cameraContainer || !locationContainer) return;
    cameraContainer.innerHTML = "";
    locationContainer.innerHTML = "";

    for (const [key, info] of Object.entries(systemData)) {
        const dot = document.createElement("div");
        if (info.type === "info") dot.className = "fs-5 location-dot";
        else dot.className = `cam-dot ${(info.status || "").toLowerCase()}`;
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

        if (info.type === "info") locationContainer.appendChild(dot);
        else cameraContainer.appendChild(dot);
    }
}

async function fetchSystemStatus() {
    try {
        const resp = await fetch("/api/health_status/");
        const data = await resp.json();
        systemData = data.data;
        updateSystemStatus();
        updateLiveBadge();
    } catch (err) {
        console.error("fetchSystemStatus error:", err);
    }
}

// ======================== HISTORY TAB ========================
// REPLACE the existing fetchHistoryData function with:
function fetchHistoryData(page = 1) {
    const dateRange = document.getElementById("date_range").value;
    if (!dateRange) { alert("Please select a date range"); return; }
    let startDate, endDate;
    if (dateRange.includes(" - ")) [startDate, endDate] = dateRange.split(" - ").map(s => s.trim());
    else startDate = endDate = dateRange.trim();

    const coilId = document.getElementById("coil_id_search").value.trim();
    let url = `/api/fetch_history_data/?start_date=${startDate}&end_date=${endDate}&page=${page}`;
    if (coilId) url += `&coil_id=${encodeURIComponent(coilId)}`;

    fetch(url)
        .then(r => r.json())
        .then(data => {
            populateHistoryTable(data.data);
            setupPagination(data.total, data.page, data.per_page, "history");
        })
        .catch(console.error);
}

function populateHistoryTable(data) {
    const tbody = document.getElementById("History_body");
    tbody.innerHTML = "";
    data.forEach(row => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${row.sno}</td>
            <td>${row.datetime}</td>
            <td>${row.coil_id}</td>
            <td>${row.total_defects}</td>
            <td>${row.total_frames}</td>
            <td>${row.total_defect_frames}</td>
            <td>${row.defect_summary}</td>
            <td>
                <button class="btn btn-primary btn-sm view-run-details"
                    data-cycle="${row.cycle_uid}"
                    data-coil="${row.coil_id}"
                    data-datetime="${row.datetime}">View Details</button>
            </td>
            <td>
                <button class="btn btn-success btn-sm download-run-data"
                    data-cycle="${row.cycle_uid}">Download JSON</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
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
    const range = [];
    for (let i = Math.max(1, current - 2); i <= Math.min(totalPages, current + 2); i++) range.push(i);
    if (range[0] > 1) { if (range[0] > 2) range.unshift("..."); range.unshift(1); }
    if (range[range.length - 1] < totalPages) { if (range[range.length - 1] < totalPages - 1) range.push("..."); range.push(totalPages); }
    return range;
}

function renderPagination(pagination, tab) {
    const container = document.getElementById(`pagination-${tab}`);
    if (!container) return;
    let html = `<div><p class="mb-0 text-muted small">Showing ${pagination.start_index}?${pagination.end_index} of ${pagination.total_records} entries</p></div>
                <div><nav><ul class="pagination mb-0">`;
    if (pagination.has_previous) html += `<li class="page-item"><a class="page-link" href="#" data-page="${pagination.current_page - 1}">Previous</a></li>`;
    else html += `<li class="page-item disabled"><span class="page-link">Previous</span></li>`;
    pagination.custom_page_range.forEach(num => {
        if (num === "...") html += `<li class="page-item disabled"><span class="page-link">?</span></li>`;
        else if (num === pagination.current_page) html += `<li class="page-item active"><span class="page-link">${num}</span></li>`;
        else html += `<li class="page-item"><a class="page-link" href="#" data-page="${num}">${num}</a></li>`;
    });
    if (pagination.has_next) html += `<li class="page-item"><a class="page-link" href="#" data-page="${pagination.current_page + 1}">Next</a></li>`;
    else html += `<li class="page-item disabled"><span class="page-link">Next</span></li>`;
    html += `</ul></nav></div>`;
    container.innerHTML = html;
    container.querySelectorAll(".page-link[data-page]").forEach(link => {
        link.addEventListener("click", (e) => {
            e.preventDefault();
            const page = parseInt(link.getAttribute("data-page"));
            if (!isNaN(page) && tab === "history") fetchHistoryData(page);
        });
    });
}

function showRunDetails(data, meta) {
    // Header chips
    document.getElementById("rdCoilId").innerText   = data.coil_id   || meta.coil_id   || "-";
    document.getElementById("rdCycleUid").innerText = data.cycle_uid || meta.cycle_uid || "-";
    document.getElementById("rdDatetime").innerText = meta.datetime  || "-";

    // Coil length header
    let rdTotalKm = (Number(data.coil_length_mm) || 0) / 1000000;
    if (rdTotalKm === 0 && Array.isArray(data.result) && data.result.length > 0) {
        let maxL = 0;
        data.result.forEach(frame => {
            const l = parseInt(frame.l) || 0;
            if (l > maxL) maxL = l;
        });
        rdTotalKm = maxL / 1000000;
    }
    const rdLenEl = document.getElementById("rdCoilLen");
    if (rdLenEl) rdLenEl.innerText = `${rdTotalKm.toFixed(1)} km total`;

    // Stats tiles
    document.getElementById("rdStatFrames").innerText   = data.total_frames            || 0;
    document.getElementById("rdStatDefects").innerText  = data.total_defects           || 0;
    document.getElementById("rdStatDefFrames").innerText = data.total_defective_frames || 0;

    // Per-type counts from frame.t summaries
    const tc = { O: 0, W: 0, D: 0, S: 0, C: 0 };
    (data.result || []).forEach(frame => {
        // Format 1: frame.t = { V: 3, D: 1 }
        if (frame.t && typeof frame.t === "object") {
            Object.entries(frame.t).forEach(([k, v]) => {
                if (tc[k] !== undefined) tc[k] += Number(v) || 0;
            });
        }
        // Format 2: frame["V"] = [{pos_mm, c}, ...] ? same as live.json / prepareData() reads
        else {
            ["O", "W", "D", "S", "C"].forEach(type => {
                if (frame[type] && Array.isArray(frame[type])) {
                    tc[type] += frame[type].length;
                }
            });
        }
    });
    document.getElementById("rdStatO").innerText = tc.O;
    document.getElementById("rdStatW").innerText = tc.W;
    document.getElementById("rdStatD").innerText = tc.D;
    document.getElementById("rdStatS").innerText = tc.S;
    document.getElementById("rdStatC").innerText = tc.C;

    // Reset filter button states
    document.querySelectorAll(".rd-filter-btn").forEach(b => {
        b.classList.add("active");
        b.style.background = b.dataset.origColor || b.style.background;
        b.style.color      = '#fff';
        b.style.boxShadow  = '';
    });
    if (rdCoilMap) rdCoilMap.filters = { W: true,  O: true,  D: true, S: true, C: true };

    // Stage data for CoilMap ? applied after modal finishes showing
    rdPendingData = data;

    new bootstrap.Modal(document.getElementById("runDetailsModal")).show();
}

function downloadHistoryData() {
    const dateRange = document.getElementById("date_range").value;
    if (!dateRange) { alert("Please select a date range"); return; }
    let startDate, endDate;
    if (dateRange.includes(" - ")) [startDate, endDate] = dateRange.split(" - ").map(s => s.trim());
    else startDate = endDate = dateRange.trim();
    window.location.href = `/api/download_history_data/?start_date=${startDate}&end_date=${endDate}`;
}

// View run details (modal
document.addEventListener("click", async (e) => {

    // View run details
    const viewBtn = e.target.closest(".view-run-details");
    if (viewBtn) {
        const cycleUid = viewBtn.dataset.cycle;
        const coilId   = viewBtn.dataset.coil     || "";
        const datetime = viewBtn.dataset.datetime  || "";
        try {
            const resp = await fetch(`/api/get_run_details/?cycle_uid=${cycleUid}`);
            const data = await resp.json();

            if (!resp.ok) {
                // Populate header chips so user knows which cycle failed
                document.getElementById("rdCoilId").innerText    = coilId   || "-";
                document.getElementById("rdCycleUid").innerText  = cycleUid || "-";
                document.getElementById("rdDatetime").innerText  = datetime  || "-";
                document.getElementById("rdCoilLen").innerText   = "";
                ["rdStatFrames","rdStatDefects","rdStatDefFrames",
                 "rdStatV","rdStatD","rdStatS","rdStatC"]
                    .forEach(id => { const el = document.getElementById(id); if(el) el.innerText = "-"; });
                // Show error banner ? create once, reuse
                let errBanner = document.getElementById("rdErrorBanner");
                if (!errBanner) {
                    errBanner = document.createElement("div");
                    errBanner.id = "rdErrorBanner";
                    errBanner.className = "alert alert-warning mb-3";
                    errBanner.style.fontFamily = "monospace";
                    const body = document.querySelector("#runDetailsModal .modal-body");
                    if (body) body.prepend(errBanner);
                }
                errBanner.style.display = "block";
                errBanner.innerHTML = `<i class="fas fa-exclamation-triangle me-2"></i>${data.error || "Data unavailable."}`;
                rdPendingData = null;
                new bootstrap.Modal(document.getElementById("runDetailsModal")).show();
            } else {
                // Hide stale error banner if present
                const errBanner = document.getElementById("rdErrorBanner");
                if (errBanner) errBanner.style.display = "none";
                showRunDetails(data, { coil_id: coilId, cycle_uid: cycleUid, datetime });
            }
        } catch (err) {
            console.error("Error fetching run details:", err);
            alert("Network error ? could not load run details.");
        }
        return;
    }

    // Download run JSON
    const dlBtn = e.target.closest(".download-run-data");
    if (dlBtn) {
        const cycleUid = dlBtn.dataset.cycle;
        window.location.href = `/api/download_run_json/?cycle_uid=${cycleUid}`;
    }
});

// ======================== INITIALIZATION ========================
document.addEventListener("DOMContentLoaded", () => {
    // Date range picker
    $('#date_range').daterangepicker({
        autoUpdateInput: true,
        startDate: moment().subtract(0, 'days'),
        endDate: moment(),
        locale: { format: "YYYY-MM-DD", cancelLabel: "Clear", applyLabel: "Apply" },
        opens: "right", drops: "down", autoApply: false,
        buttonClasses: ['btn', 'btn-sm', 'btn-primary'],
    }, (start, end) => $('#date_range').val(start.format("YYYY-MM-DD") + " - " + end.format("YYYY-MM-DD")));
    $('#date_range').on("apply.daterangepicker", (ev, picker) => {
        const s = picker.startDate.format("YYYY-MM-DD"), e = picker.endDate.format("YYYY-MM-DD");
        $(this).val(s === e ? s : s + " - " + e);
    });
    $('#date_range').on("cancel.daterangepicker", () => $(this).val(""));

    // Coil map init
    coilMap = new CoilMap("coilMap", "mapTooltip");
    window.coilMap = coilMap; // for filter buttons (onclick)
    document.getElementById("siZoomReset")?.addEventListener("click", () => coilMap.resetZoom());
    const filterBtns = document.querySelectorAll('.defect-filter-btn:not(.rd-filter-btn)');
    filterBtns.forEach(btn => {
        btn.dataset.origColor = btn.style.background;  // save before any change
        btn.addEventListener('click', () => {
            const type = btn.dataset.type;
            btn.classList.toggle('active');
            if (btn.classList.contains('active')) {
                btn.style.background = btn.dataset.origColor;
                btn.style.color      = '#fff';
                btn.style.boxShadow  = '';
            } else {
                btn.style.background = 'transparent';
                btn.style.color      = btn.dataset.origColor;
                btn.style.boxShadow  = 'inset 0 0 0 2px ' + btn.dataset.origColor;
            }
            if (coilMap) coilMap.toggleFilter(type);
        });
    });

    const cameraSlots = document.querySelectorAll('[id^="camera-slot-"]');
    cameraSlots.forEach(slot => {
        slot.addEventListener('click', () => {
            const img = slot.querySelector('img');
            if (img && img.src) {
                openImageModal(img.src, 'Live Camera Feed');
            } else {
                console.warn('No image in camera slot');
            }
        });
    });

    // Initial data fetch
    fetchSystemStatus();
    fetchCurrentStatus();
    updateCameraImages();

    // Polling
    setInterval(fetchSystemStatus, 5000);
    setInterval(fetchCurrentStatus, 5000);
    setInterval(updateCameraImages, 2000);

    // Auto-refresh after 30min inactivity
    let lastActivity = Date.now();
    const resetTimer = () => lastActivity = Date.now();
    ["click", "mousemove", "keydown", "scroll", "touchstart"].forEach(ev => document.addEventListener(ev, resetTimer));
    setInterval(() => { if (Date.now() - lastActivity > 30 * 60 * 1000) location.reload(); }, 10000);
    
    document.getElementById("runDetailsModal").addEventListener("shown.bs.modal", function () {
        if (!rdCoilMap) {
            rdCoilMap = new CoilMap("rdCoilMap", "rdMapTooltip", "rdCoilStart", "rdCoilEnd");
            document.querySelectorAll(".rd-filter-btn").forEach(btn => {
                btn.dataset.origColor = btn.style.background;  // save before any change
                btn.addEventListener("click", () => {
                    btn.classList.toggle("active");
                    if (btn.classList.contains('active')) {
                        btn.style.background = btn.dataset.origColor;
                        btn.style.color      = '#fff';
                        btn.style.boxShadow  = '';
                    } else {
                        btn.style.background = 'transparent';
                        btn.style.color      = btn.dataset.origColor;
                        btn.style.boxShadow  = 'inset 0 0 0 2px ' + btn.dataset.origColor;
                    }
                    if (rdCoilMap) rdCoilMap.toggleFilter(btn.dataset.type);
                });
            });
        } else {
            rdCoilMap.resize();
        }
        if (rdPendingData) {
            rdCoilMap.update(rdPendingData);
            rdPendingData = null;
        }
    });

    // Clean up rdCoilMap state on modal close
    document.getElementById("runDetailsModal").addEventListener("hidden.bs.modal", function () {
        rdPendingData = null;
    });
});