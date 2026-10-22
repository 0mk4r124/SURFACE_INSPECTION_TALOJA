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
let rdFilterSync = () => {};   // set in DOMContentLoaded (run-details filter chips)
// ---- defect carousel / details panel ----
const FRAME_WIDTH_MM   = 400;   // MUST match backend FRAME_WIDTH_MM (was hardcoded 300)
const CLICK_NAV_ENABLED = true; // left-click = prev, right-click = next on the image
const MAP_BUCKET_M     = 1;     // coil map: 1 dot per type per metre at 1x zoom (bucket = 1 m / zoom)
const MAP_WIDTH_BIN_MM = 10;    // coil map: same-type defects within a 10 mm width band share that dot

// coil map: per-type minimum confidence (%) to be shown. Types not listed show at any score.
// Also used for every defect COUNT (live tiles, details modal, history table).
// MUST match MAP_MIN_CONF in api/views.py (history table + CSV) - change both together.
const MAP_MIN_CONF = { W: 90, O: 90 };

function passesMapConf(d) {
    const min = MAP_MIN_CONF[d.type];
    if (min == null) return true;
    let c = Number(d.confidence);
    if (isNaN(c)) return false;
    if (c <= 1) c *= 100;          // tolerate 0-1 scores from older JSONs
    return c >= min;
}

// Dashboard tiles: count defects per type using the SAME rule as the coil map.
// Per-defect arrays (frame.W = [{c, ...}]) carry the confidence; frame.t does
// not, so it is only used for old JSONs that have no arrays (counted as-is).
function countVisibleDefects(frames) {
    const counts = { O: 0, W: 0, D: 0, S: 0, C: 0 };
    let defFrames = 0;
    (frames || []).forEach(frame => {
        let inFrame = 0;
        let hasArrays = false;
        Object.keys(counts).forEach(type => {
            if (!Array.isArray(frame[type])) return;
            hasArrays = true;
            frame[type].forEach(d => {
                if (passesMapConf({ type, confidence: d.c })) { counts[type]++; inFrame++; }
            });
        });
        if (!hasArrays && frame.t && typeof frame.t === "object") {
            Object.entries(frame.t).forEach(([k, v]) => {
                if (counts[k] !== undefined) { counts[k] += Number(v) || 0; inFrame += Number(v) || 0; }
            });
        }
        if (inFrame > 0) defFrames++;
    });
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    return { counts, total, defFrames };
}
let defectNavList  = [];
let defectNavIndex = -1;
let defectNavCtx   = { coilId: "", cycleUid: "" };
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
        resetZoomAndPosition(); 
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
    if (!isDragging || scale <= 1) return;    // <-- add scale check
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
    // detach FIRST so clearing src cannot trigger a stale onerror
    modalImg.onload = null;
    modalImg.onerror = null;

    if (modalImg.src && modalImg.src.startsWith("blob:")) {
        URL.revokeObjectURL(modalImg.src);
    }
    if (this.dataset.src && this.dataset.src.startsWith("blob:")) {
        URL.revokeObjectURL(this.dataset.src);
    }
    this.dataset.src = "";

    modalImg.removeAttribute("src");   // no page-URL refetch, no error event
    modalImg.style.display = "none";
    imageLoader.style.display = "none";
    modalOpen = false;
    isImageLoading = false;
    defectNavList  = [];
    defectNavIndex = -1;
    setDefectNavUI(false);
});

// Open / update image modal (reuses existing modal if already open)
function openImageModal(imgUrl, title) {
    const modalEl = document.getElementById("imageModal");
    if (!modalEl) return;
    if (title) document.getElementById("vehicle_number").innerHTML = title;

    const prev = modalEl.dataset.src;
    if (prev && prev !== imgUrl && prev.startsWith("blob:")) {
        URL.revokeObjectURL(prev);
    }
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

// ======================== DEFECT CAROUSEL + DETAILS ========================
function clearDefectNav() {
    defectNavList  = [];
    defectNavIndex = -1;
    setDefectNavUI(false);
}

function openDefectCarousel(list, current, ctx) {
    defectNavList = (Array.isArray(list) && list.length) ? list : (current ? [current] : []);
    defectNavCtx  = ctx || { coilId: "", cycleUid: "" };

    let idx = defectNavList.indexOf(current);           // same-reference case
    if (idx < 0 && current) {
        idx = defectNavList.findIndex(d =>
            (current._uid != null && d._uid === current._uid) ||
            (d.frame === current.frame &&
             d.type  === current.type  &&
             String(d.box) === String(current.box))
        );
    }
    if (idx < 0) {
        console.warn("Clicked defect not found in filtered list", current);
        idx = 0;
    }

    defectNavIndex = idx;
    showDefectAt(defectNavIndex);
}

function showDefectAt(idx) {
    if (!defectNavList.length) return;
    if (idx < 0) idx = defectNavList.length - 1;              // wrap
    if (idx >= defectNavList.length) idx = 0;
    defectNavIndex = idx;
    const point = defectNavList[idx];
    setDefectNavUI(true);
    renderDefectDetails(point);
    openDefectImage(point);
}

function stepDefect(delta) {
    if (defectNavList.length < 2) return;
    showDefectAt(defectNavIndex + delta);
}

// ======================== DEFECT IMAGE DOWNLOAD (image + details pane) ========================
function _rectOverlap(a, b) {   // rects as [x, y, w, h]
    const w = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]);
    const h = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
    return (w > 0 && h > 0) ? w * h : 0;
}

function downloadDefectImage() {
    if (isImageLoading || !modalImg.naturalWidth) return;

    const W = modalImg.naturalWidth, H = modalImg.naturalHeight;
    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(modalImg, 0, 0);   // already has box + label drawn

    const point = (defectNavIndex >= 0) ? defectNavList[defectNavIndex] : null;
    const panel = document.getElementById("defectDetailsPanel");
    const rows = (panel && panel.style.display !== "none")
        ? [...document.querySelectorAll("#defectDetailsBody .im-row")].map(r => [
              (r.querySelector(".im-key")?.innerText || "").trim(),
              (r.querySelector(".im-val")?.innerText || "").trim()
          ])
        : [];

    if (rows.length) {
        let fs = Math.max(12, Math.round(W / 80));           // scale text to image size
        const pad = Math.round(fs * 0.8), margin = Math.round(fs * 0.6);
        const measure = (f) => {
            ctx.font = `bold ${f}px monospace`;
            return [Math.max(...rows.map(r => ctx.measureText(r[0]).width)),
                    Math.max(...rows.map(r => ctx.measureText(r[1]).width))];
        };
        let [keyW, valW] = measure(fs);
        while (keyW + valW + pad * 3 > W * 0.45 && fs > 10) { fs--; [keyW, valW] = measure(fs); }

        const lineH  = Math.round(fs * 1.55);
        const panelW = keyW + valW + pad * 3;
        const panelH = pad * 2 + lineH * (rows.length + 1);

        // regions the pane must not cover: defect box + its label
        const avoid = [];
        if (point && Array.isArray(point.box)) {
            const [x1, x2, y1, y2] = point.box;
            avoid.push([x1, y1, x2 - x1, y2 - y1]);
        }
        if (point && point._labelRect) avoid.push(point._labelRect);

        // right first, else left; bottom corners only if both top corners collide
        const cands = [
            [W - panelW - margin, margin],
            [margin, margin],
            [W - panelW - margin, H - panelH - margin],
            [margin, H - panelH - margin]
        ].map(([x, y]) => [Math.max(0, x), Math.max(0, y), panelW, panelH]);

        let best = cands[0], bestOv = Infinity;
        for (const c of cands) {
            const ov = avoid.reduce((s, a) => s + _rectOverlap(c, a), 0);
            if (ov < bestOv) { best = c; bestOv = ov; }
            if (ov === 0) break;
        }
        const [px, py] = best;

        ctx.fillStyle = "rgba(255,255,255,0.92)";
        ctx.fillRect(px, py, panelW, panelH);
        ctx.strokeStyle = "#00c9a7";
        ctx.lineWidth = Math.max(1, Math.round(fs / 8));
        ctx.strokeRect(px, py, panelW, panelH);

        ctx.textBaseline = "middle";
        ctx.fillStyle = "#00a88c";
        ctx.fillText("DEFECT DETAILS", px + pad, py + pad + lineH / 2);
        rows.forEach(([k, v], i) => {
            const y = py + pad + lineH * (i + 1.5);
            ctx.fillStyle = "#7a838f"; ctx.fillText(k, px + pad, y);
            ctx.fillStyle = "#16323b"; ctx.fillText(v, px + pad * 2 + keyW, y);
        });
    }

    const safe = s => String(s ?? "").replace(/[^\w.-]+/g, "_");
    const name = point
        ? `${safe(defectNavCtx.coilId || "coil")}_F${safe(point.frame)}_${safe(point.type)}_` +
          `${Math.floor((Number(point.length) || 0) / 1000)}m.jpg`
        : `defect_${Date.now()}.jpg`;

    canvas.toBlob(blob => {
        if (!blob) { console.error("download: toBlob failed", W, H); return; }
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, "image/jpeg", 0.95);
}

document.getElementById("defectDownloadBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    downloadDefectImage();
});

function setDefectNavUI(show) {
    const panel = document.getElementById("defectDetailsPanel");
    const prev  = document.getElementById("defectPrevBtn");
    const next  = document.getElementById("defectNextBtn");
    const cnt   = document.getElementById("defectNavCounter");
    const multi = defectNavList.length > 1;

    if (panel) panel.style.display = show ? "block" : "none";
    if (prev)  prev.style.display  = (show && multi) ? "flex" : "none";
    if (next)  next.style.display  = (show && multi) ? "flex" : "none";
    if (cnt) {
        cnt.style.display = (show && defectNavList.length) ? "inline-block" : "none";
        cnt.innerText = `${defectNavIndex + 1} / ${defectNavList.length}`;
    }
}

function defectMetrics(point) {
    const imgW    = Number(point.imgW) || Number(point._natW) || 0;
    const mmPerPx = imgW > 0 ? (FRAME_WIDTH_MM / imgW) : 0;
    const box     = (Array.isArray(point.box) && point.box.length === 4) ? point.box : null;

    let wMm = null, hMm = null, offMm = null, side = "-";

    if (box && mmPerPx) {
        const [x1, x2, y1, y2] = box;
        wMm = Math.abs(x2 - x1) * mmPerPx;
        hMm = Math.abs(y2 - y1) * mmPerPx;
        const off = ((x1 + x2) / 2) - (imgW / 2);
        offMm = Math.abs(off * mmPerPx);
        side  = off < 0 ? "OS" : (off > 0 ? "DS" : "CENTER");
    }

    // backend values win when present
    if (point.wMm != null) wMm = Number(point.wMm);
    if (point.hMm != null) hMm = Number(point.hMm);
    const p = Number(point.widthPos);
    if (!isNaN(p) && (p !== 0 || offMm == null)) {
        offMm = Math.abs(p);
        side  = p < 0 ? "OS" : (p > 0 ? "DS" : "CENTER");
    }

    const lenMm = Number(point.length) || 0;
    return {
        wMm, hMm, offMm, side, lenMm,
        areaMm: (wMm != null && hMm != null) ? (wMm * hMm) : null
    };
}

function renderDefectDetails(point) {
    const body = document.getElementById("defectDetailsBody");
    if (!body || !point) return;

    const m     = defectMetrics(point);
    const name  = DEFECT_NAMES[point.type] || point.type || "-";
    const color = defectColors[point.type] || "#333333";
    const n     = (v, d = 1) => (v == null || isNaN(v)) ? "-" : Number(v).toFixed(d);

    const rows = [
        ["COIL ID",    defectNavCtx.coilId   || "-"],
        ["CYCLE UID",  defectNavCtx.cycleUid || "-"],
        ["DEFECT",     `<span class="im-dot" style="background:${color}"></span>${name} (${point.type || "-"})`],
        ["CONFIDENCE", point.confidence != null ? `${point.confidence} %` : "-"],
        ["FRAME",      point.frame != null ? point.frame : "-"],
        ["LENGTH",     `${(m.lenMm / 1000).toFixed(2)} m <span class="im-sub">(${m.lenMm} mm)</span>`],
        ["POSITION",   m.offMm == null ? "-" : `${m.side} ${n(m.offMm)} mm`],
        ["WIDTH",      `${n(m.wMm)} mm`],
        ["HEIGHT",     `${n(m.hMm)} mm`],
        ["AREA",       m.areaMm == null ? "-" : `${n(m.areaMm, 0)} mm&sup2;`],
        ["SEVERITY",   point.severity || "-"],
        ["DIAMETER",   point.diameter   != null ? point.diameter   : "-"],
        ["EG SETPOINT", point.egSetpoint != null ? point.egSetpoint : "-"],
        ["BOX (px)",   Array.isArray(point.box)
                        ? `${point.box[0]}, ${point.box[2]} &rarr; ${point.box[1]}, ${point.box[3]}`
                        : "-"],
        ["FRAME WIDTH", `${FRAME_WIDTH_MM} mm`]
    ];

    // white-spot shape gate verdict (backend WS_SHAPE), when present
    const sh = point.shape;
    if (sh && sh.shape) {
        const okCol = sh.ok ? "#16a34a" : "#dc2626";
        rows.push(["SHAPE", `<span style="color:${okCol};font-weight:700">${sh.shape}</span>`
                            + (sh.why ? ` <span class="im-sub">(${sh.why})</span>` : "")]);
        if (sh.maj_mm != null) rows.push(["SPOT SIZE", `${n(sh.maj_mm)} &times; ${n(sh.min_mm)} mm`]);
        if (sh.ar != null)     rows.push(["ASPECT", `${n(sh.ar, 2)} <span class="im-sub">(max ${n(sh.ar_max, 1)}${sh.clean ? ", clean" : ""})</span>`]);
        if (sh.iou != null)    rows.push(["ELLIPSE IOU", `${n(sh.iou, 2)} <span class="im-sub">sol ${n(sh.sol, 2)}</span>`]);
        if (Array.isArray(sh.sym)) rows.push(["SYMMETRY", `${n(sh.sym[0], 2)} / ${n(sh.sym[1], 2)}`]);
        if (Array.isArray(sh.ct))  rows.push(["CONTRAST", `${n(sh.ct[2])} <span class="im-sub">(in ${n(sh.ct[0])} / ring ${n(sh.ct[1])})</span>`]);
    }

    body.innerHTML = rows
        .map(([k, v]) => `<div class="im-row"><div class="im-key">${k}</div><div class="im-val">${v}</div></div>`)
        .join("");
}

// Helper for defect clicks
function openDefectImage(point) {
    const rawPath = point.rawImg || "";
    const fallback = point.img || point.image || point.image_url || "";
    const frameNum = point.frame || point.r || "";

    const title = frameNum
        ? `Frame ${frameNum} | ${point.length != null ? Math.floor(point.length / 1000) : ""} m`
        : (DEFECT_NAMES[point.type] || point.type || "Defect");

    // console.log("openDefectImage called with:", { point, rawPath, fallback, title });

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
            const mmPerPixel = FRAME_WIDTH_MM / imgW;

            // older JSONs have no compressed_width -> fill it in and redraw the panel
            if (!point.imgW) { point._natW = imgW; renderDefectDetails(point); }

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

            const labelW = textWidth + 12;

            // keep the label fully inside the image (defects at OS/DS edges)
            let labelX = Math.max(0, Math.min(bx1, canvas.width - labelW));
            let labelY = by1 - 8;                                   // bottom of label box
            if (labelY - textHeight < 0) {                          // no room above the box
                labelY = (by2 + 8 + textHeight <= canvas.height)
                    ? by2 + 8 + textHeight                          // below the box
                    : Math.max(by1, 0) + textHeight;                // inside, top of box
            }
            point._labelRect = [labelX, labelY - textHeight, labelW, textHeight];  // used by download

            ctx.fillStyle = color;
            ctx.fillRect(
                labelX,
                labelY - textHeight,
                labelW,
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
    constructor(canvasId, tooltipId, startLabelId = "coilStart", endLabelId = "coilEnd", opts = {}) {
        this.height = Number(opts.height) || 250;   // dashboard maps pass 170; run-details modal keeps 250
        this.ctxInfo = null;                        // {coilId, cycleUid} set by the dashboard per map
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
        this.canvas.height = this.height;
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
                                severity: frame.s || null,
                                diameter: frame.d != null ? frame.d : null,
                                egSetpoint: frame.e != null ? frame.e : null,
                                imgW: frame.compressed_width  || null,
                                imgH: frame.compressed_height || null,
                                wMm: defect.w_mm != null ? defect.w_mm : null,
                                hMm: defect.h_mm != null ? defect.h_mm : null,
                                shape: defect.shape || null,
                            });
                        });
                    }
                });
        });
                
        // stable identity - visiblePoints holds spread-copies, not references
        this.defects.forEach((d, i) => { d._uid = i; });

        // --- NEW: fallback when 'l' (distance) is missing/flat for the whole roll ---
        if (this.defects.length > 0) {
            const lens = this.defects.map(d => d.length);
            const flat = Math.min(...lens) === Math.max(...lens);
            if (flat) {
                this.defects.forEach(d => { d.length = d.frame || 0; }); // use frame rank instead
            }
        }

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
        const normY = (defect.widthPos + FRAME_WIDTH_MM / 2) / FRAME_WIDTH_MM;
        const y = this.canvas.height - (normY * this.canvas.height);
        return { x, y };
    }

    render() {
        const ctx = this.ctx;
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.fillStyle = "#0f172a";
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        this.drawGrid();

        this.visiblePoints = [];

        const R = 3;              // radius 3 -> 2 (diameter reduced by 2 units: 6px -> 4px)
        const CELL = R * 2;       // grid cell = point diameter, guarantees no overlap
        const cols = Math.ceil(this.canvas.width / CELL) + 4;
        const occupied = new Set();

        // ---- aggregate: 1 dot per (type, length bucket, width band) ----
        // bucket shrinks with zoom so zooming in splits dots back out
        const bucketMm = (MAP_BUCKET_M * 1000) / this.scaleX;
        const buckets = new Map();
        for (const d of this.defects) {
            if (!this.filters[d.type] || !passesMapConf(d)) continue;
            const key = `${d.type}|${Math.floor(d.length / bucketMm)}|${Math.round(d.widthPos / MAP_WIDTH_BIN_MM)}`;
            const cur = buckets.get(key);
            if (!cur) {
                buckets.set(key, { rep: d, count: 1 });
            } else {
                cur.count++;
                if ((d.confidence || 0) > (cur.rep.confidence || 0)) cur.rep = d;   // highest confidence represents
            }
        }

        for (const { rep: defect, count } of buckets.values()) {
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

            this.visiblePoints.push({ ...defect, x: fx, y: fy, count });
            ctx.beginPath();
            ctx.arc(fx, fy, R, 0, Math.PI * 2);
            ctx.fillStyle = this.getColor(defect.type);
            ctx.fill();
        }
        this.updateAxis();
    }

    // Guide lines under the dots: quarter-length gridlines, FOV-centre line,
    // and tick marks along the top/bottom edges (long every 1/4, short every 1/20).
    drawGrid() {
        const ctx = this.ctx, W = this.canvas.width, H = this.canvas.height;
        if (!W || !H) return;
        ctx.save();
        ctx.lineWidth = 1;
        ctx.strokeStyle = "rgba(148, 163, 184, 0.20)";
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        for (let i = 1; i < 4; i++) {
            const x = Math.round(W * i / 4) + 0.5;
            ctx.moveTo(x, 0); ctx.lineTo(x, H);
        }
        const cy = Math.round(H / 2) + 0.5;           // FOV centre (OS below / DS above)
        ctx.moveTo(0, cy); ctx.lineTo(W, cy);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.strokeStyle = "rgba(148, 163, 184, 0.55)";
        ctx.beginPath();
        for (let i = 1; i < 20; i++) {
            const x = Math.round(W * i / 20) + 0.5;
            const len = (i % 5 === 0) ? 8 : 4;
            ctx.moveTo(x, 0); ctx.lineTo(x, len);
            ctx.moveTo(x, H); ctx.lineTo(x, H - len);
        }
        ctx.stroke();
        ctx.restore();
    }

    // Axis under the map: 5 labels (0, 1/4, 1/2, 3/4, end of the VISIBLE range),
    // lined up with the gridlines and kept correct while zooming / panning.
    updateAxis() {
        const s = document.getElementById(this.startLabelId);
        const e = document.getElementById(this.endLabelId);
        if (!s || !e || s.parentElement !== e.parentElement) return;
        const axis = s.parentElement;
        let mids = axis.querySelectorAll(".cm-tick-mid");
        if (mids.length !== 3) {
            mids.forEach(m => m.remove());
            for (let i = 0; i < 3; i++) {
                const sp = document.createElement("span");
                sp.className = "cm-tick-mid";
                axis.insertBefore(sp, e);
            }
            mids = axis.querySelectorAll(".cm-tick-mid");
        }
        const W = this.canvas.width || 1;
        const span = (this.maxLen - this.minLen) || 1;
        const lenAt = x => this.minLen + ((x - this.offsetX) / (W * this.scaleX)) * span;
        const fmt = mm => `${Math.max(0, Math.round(mm / 1000)).toLocaleString("en-IN")} m`;
        s.textContent = fmt(lenAt(0));
        e.textContent = fmt(lenAt(W));
        mids.forEach((m, i) => { m.textContent = fmt(lenAt(W * (i + 1) / 4)); });
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
                    this.tooltip.innerHTML = `<b>${defectFullName}</b><br>Frame: ${defect.frame}<br>Length: ${defect.length} mm<br>Width: ${defect.widthPos} mm` +
                        (defect.count > 1 ? `<br><i>${defect.count} defects here - zoom in to split</i>` : "");
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
                    openDefectCarousel(this.getFilteredDefects(), defect, this.getContext());
                    break;
                }
            }
        });
    }
        
    // All defects passing the CURRENT filter buttons, ordered along the coil
    getFilteredDefects() {
        return this.defects
            .filter(d => this.filters[d.type] && d.img && passesMapConf(d))
            .sort((a, b) => (a.length - b.length) || ((a.frame || 0) - (b.frame || 0)));
    }

    getContext() {
        if (this.ctxInfo) return this.ctxInfo;     // dashboard maps: each knows its own coil
        const isRd = this.canvas && this.canvas.id === "rdCoilMap";
        const g = id => (document.getElementById(id)?.innerText || "").trim();
        return {
            coilId:   isRd ? g("rdCoilId")   : (g("siCoilId")   || g("coil_id")),
            cycleUid: isRd ? g("rdCycleUid") : (g("siCycleUid") || g("cycle_uid"))
        };
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

let coilMap = null;    // top map    (current coil, or last completed coil when idle)
let coilMap2 = null;   // bottom map (previous completed coil, or the one before it when idle)

// ======================== DASHBOARD: DUAL COIL VIEW ========================
// Red blinking alarm on a defect tile when its (visible) count is OVER the limit.
// Counts use the same MAP_MIN_CONF rule as the map dots and the history table.
const ALARM_LIMITS = { O:100000, W: 300, S: 5000, D: 5000, C: 5000 };
const DUAL_TYPES   = ["O", "W", "S", "D", "C"];
const LIVE_STATUSES = ["inprogress", "processing", "running"];

const dashSlotKey = { 1: null, 2: null };   // which coil each map is currently showing
const runDataCache = new Map();             // cycle_uid -> Promise<run JSON | null>
let dashBusy = false;

function fmtMetres(mm) {
    return `${Math.round((Number(mm) || 0) / 1000).toLocaleString("en-IN")} m`;
}

function fmtClock(iso) {
    if (!iso) return "--";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "--" : d.toLocaleTimeString();
}

function maxFrameLengthMm(data) {
    let maxL = 0;
    (data && Array.isArray(data.result) ? data.result : []).forEach(f => {
        const l = Number(f.l);
        if (!isNaN(l) && l > maxL) maxL = l;
    });
    return maxL;
}

// Full defect map of a completed coil (get_run_details), fetched once per cycle_uid.
function loadRunData(cycleUid) {
    if (!runDataCache.has(cycleUid)) {
        const p = fetch(`/api/get_run_details/?cycle_uid=${encodeURIComponent(cycleUid)}`)
            .then(r => (r.ok ? r.json() : null))
            .catch(() => null)
            .then(d => { if (!d) runDataCache.delete(cycleUid); return d; });   // retry next poll
        runDataCache.set(cycleUid, p);
        while (runDataCache.size > 4) runDataCache.delete(runDataCache.keys().next().value);
    }
    return runDataCache.get(cycleUid);
}

// Fill one coil card. info = null -> empty card.
function renderCoilSlot(n, info, map) {
    const $ = id => document.getElementById(id);
    if (!$(`cmCard${n}`) || !map) return;

    const tagEl = $(`cmTag${n}`);
    const emptyEl = $(`cmEmpty${n}`);
    const headEl = $(`cmHead${n}`);

    if (!info) {
        tagEl.textContent = "NO DATA";
        tagEl.className = "si-coil-tag si-tag-none";
        $(`cmCoilId${n}`).textContent = "--";
        $(`cmMeta${n}`).textContent = "";
        $(`cmLen${n}`).textContent = "0 m";
        $(`cmTotal${n}`).textContent = "0";
        DUAL_TYPES.forEach(t => {
            $(`cm${t}${n}`).textContent = "0";
            $(`cmTile${t}${n}`).classList.remove("si-ct-alarm");
            $(`cmTile${t}${n}`).querySelector(".si-ct-limit").textContent = "";
        });
        headEl.style.display = "none";
        emptyEl.textContent = n === 1 ? "Waiting for coil data" : "No previous coil";
        emptyEl.style.display = "flex";
        map.ctxInfo = null;
        if (dashSlotKey[n] !== null) { map.update({ result: [] }); dashSlotKey[n] = null; }
        return;
    }

    tagEl.textContent = info.tag;
    tagEl.className = `si-coil-tag ${info.tagClass}`;
    $(`cmCoilId${n}`).textContent = info.coilId || "?";
    $(`cmMeta${n}`).textContent = info.meta || "";
    $(`cmLen${n}`).textContent = fmtMetres(info.lengthMm);
    $(`cmTotal${n}`).textContent = info.total;

    DUAL_TYPES.forEach(t => {
        const v = Number(info.counts[t]) || 0;
        const over = v > ALARM_LIMITS[t];
        const tile = $(`cmTile${t}${n}`);
        $(`cm${t}${n}`).textContent = v;
        tile.classList.toggle("si-ct-alarm", over);
        tile.querySelector(".si-ct-limit").textContent = over ? ` · LIMIT ${ALARM_LIMITS[t]}` : "";
    });

    headEl.style.display = info.live ? "block" : "none";
    if (info.live) $(`cmHeadLbl${n}`).textContent = `SCANNING · ${fmtMetres(info.lengthMm)}`;

    map.ctxInfo = { coilId: info.coilId || "", cycleUid: info.cycleUid || "" };

    if (info.mapData) {
        emptyEl.style.display = "none";
        // live map refreshes every poll (as before); a completed coil only when it changes
        if (info.live || dashSlotKey[n] !== info.key) {
            map.update(info.mapData);
            dashSlotKey[n] = info.key;
        }
    } else {
        emptyEl.textContent = info.mapMissing ? "Defect map file not available" : "Loading defect map...";
        emptyEl.style.display = "flex";
        if (dashSlotKey[n] !== null) { map.update({ result: [] }); dashSlotKey[n] = null; }
    }
}

// Card info for a completed coil from get_recent_coils (+ its run JSON for the map).
// Tile counts come from the run JSON with the JS MAP_MIN_CONF rule, so they always
// match the dots on the map (server counts are only the fallback).
async function completedCoilInfo(coil, tag, tagClass) {
    if (!coil) return null;
    const run = coil.json_available ? await loadRunData(coil.cycle_uid) : null;
    const vis = run ? countVisibleDefects(run.result) : null;
    return {
        tag, tagClass,
        coilId: coil.coil_id,
        cycleUid: coil.cycle_uid,
        meta: `Completed ${fmtClock(coil.completed_at)}`,
        lengthMm: run ? (Number(run.coil_length_mm) || maxFrameLengthMm(run)) : 0,
        counts: vis ? vis.counts : (coil.counts || {}),
        total: vis ? vis.total : (Number(coil.total) || 0),
        live: false,
        mapData: run,
        mapMissing: !coil.json_available,
        key: `done:${coil.cycle_uid}`
    };
}

// The coil in live.json after it has finished: it stays on the TOP map until
// the next coil starts. Full map from its cycle JSON; live.json as fallback.
async function finishedLiveCoilInfo(data) {
    const run = await loadRunData(data.cycle_uid);
    const src = run || data;
    const vis = countVisibleDefects(src.result);
    return {
        tag: "COMPLETED", tagClass: "si-tag-done",
        coilId: data.coil_id || (run && run.coil_id),
        cycleUid: data.cycle_uid,
        meta: `Completed ${fmtClock(data.last_updated)}`,
        lengthMm: run ? (Number(run.coil_length_mm) || maxFrameLengthMm(run))
                      : (Number(data.current_position_mm) || maxFrameLengthMm(data)),
        counts: vis.counts, total: vis.total,
        live: false,
        mapData: src,
        key: run ? `done:${data.cycle_uid}` : `livejson:${data.cycle_uid}:${data.last_updated || ""}`
    };
}

function setStatusBar(data, isLive, top, bottom) {
    const statusEl = document.getElementById("siStatus");
    if (statusEl) {
        const st = String(data.status || "").toLowerCase();
        statusEl.className = "si-chip-value si-mono";
        if (isLive) {
            statusEl.textContent = (data.status || "INPROGRESS").toUpperCase();
            statusEl.classList.add("si-status-processing");
        } else if (st === "error") {
            statusEl.textContent = "ERROR";
            statusEl.classList.add("si-status-error");
        } else {
            statusEl.textContent = "WAITING FOR COIL";
            statusEl.classList.add("si-status-idle");
        }
    }

    const idLabel = document.getElementById("siIdLabel");
    const idEl = document.getElementById("siCoilId");
    if (idLabel) idLabel.textContent = isLive ? "COIL ID" : "LAST COMPLETED";
    if (idEl) idEl.textContent = (top && top.coilId) || "--";

    const sideLabel = document.getElementById("siSideLabel");
    const sideId = document.getElementById("siSideId");
    const sideMeta = document.getElementById("siSideMeta");
    if (sideLabel) sideLabel.textContent = isLive ? "PREVIOUS COIL" : "LAST COIL";
    if (sideId) sideId.textContent = bottom ? (bottom.coilId || "?") : "--";
    if (sideMeta) sideMeta.textContent = bottom ? `${bottom.meta} · ${bottom.total} defects` : "";
}

// recent = completed coils from get_recent_coils, EXCLUDING the live.json coil.
//   processing           -> top: live coil            bottom: recent[0] (previous)
//   finished, live.json  -> top: that finished coil    bottom: recent[0] (last coil)
//   no coil in live.json -> top: recent[0]            bottom: recent[1]
// So a finished coil stays on the top map until the next coil starts.
async function updateSIDashboard(data, recent, isLive) {
    recent = Array.isArray(recent) ? recent : [];
    let top, bottom;
    if (isLive) {
        // same confidence rule as the coil map (MAP_MIN_CONF): tiles always match the dots
        const vis = countVisibleDefects(data.result);
        top = {
            tag: "● CURRENT · IN PROGRESS", tagClass: "si-tag-live",
            coilId: data.coil_id, cycleUid: data.cycle_uid,
            meta: `Updated ${fmtClock(data.last_updated)}`,
            lengthMm: Number(data.current_position_mm) || 0,
            counts: vis.counts, total: vis.total,
            live: true, mapData: data, key: `live:${data.cycle_uid || ""}`
        };
        bottom = await completedCoilInfo(recent[0], "PREVIOUS · COMPLETED", "si-tag-done");
    } else if (data.cycle_uid) {
        top = await finishedLiveCoilInfo(data);
        bottom = await completedCoilInfo(recent[0], "LAST COIL", "si-tag-last");
    } else {
        top = await completedCoilInfo(recent[0], "COMPLETED", "si-tag-done");
        bottom = await completedCoilInfo(recent[1], "LAST COIL", "si-tag-last");
    }

    setStatusBar(data, isLive, top, bottom);
    renderCoilSlot(1, top, coilMap);
    renderCoilSlot(2, bottom, coilMap2);
}

async function fetchCurrentStatus() {
    if (dashBusy) return;            // previous poll still running (slow JSON load)
    dashBusy = true;
    try {
        const resp = await fetch("/api/get_current_status/");
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const data = await resp.json();
        const isLive = LIVE_STATUSES.includes(String(data.status || "").toLowerCase());

        let recent = [];
        try {
            let url = "/api/get_recent_coils/?limit=2";
            if (data.cycle_uid) url += `&exclude=${encodeURIComponent(data.cycle_uid)}`;
            const r = await fetch(url);
            if (r.ok) recent = (await r.json()).coils || [];
        } catch (e) {
            console.error("get_recent_coils error:", e);
        }

        await updateSIDashboard(data, recent, isLive);
        // Legacy hidden fields (for any old code)
        document.getElementById("currentState").innerText = (data.status || "IDLE").toUpperCase();
        document.getElementById("coil_id").innerText = data.coil_id || "";
        document.getElementById("cycle_uid").innerText = data.cycle_uid || "";
    } catch (err) {
        console.error("fetchCurrentStatus error:", err);
    } finally {
        dashBusy = false;
    }
}

// Filter chips (All / O / W / S / D / C) bound to one CoilMap. getMap() may
// return null until the map exists (run-details modal). Returns sync().
function bindChipFilters(box, getMap) {
    if (!box) return () => {};
    const sync = () => {
        const map = getMap();
        box.querySelectorAll(".si-fchip[data-type]").forEach(b => {
            const t = b.dataset.type;
            const on = !map ? true
                : (t === "ALL" ? DUAL_TYPES.every(k => map.filters[k]) : !!map.filters[t]);
            b.classList.toggle("active", on);
            b.setAttribute("aria-pressed", on ? "true" : "false");
        });
    };
    box.addEventListener("click", e => {
        const b = e.target.closest(".si-fchip");
        const map = getMap();
        if (!b || !map) return;
        const t = b.dataset.type;
        if (t === "ALL") {
            DUAL_TYPES.forEach(k => { map.filters[k] = true; });
            map.render();
        } else {
            map.toggleFilter(t);
        }
        sync();
    });
    sync();
    return sync;
}

// Per-map filter chips on the dashboard: each map keeps its own filters.
function bindDualFilters(n, map) {
    return bindChipFilters(document.querySelector(`.si-coil-filters[data-map="${n}"]`), () => map);
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
                        // skip while the previous frame is still loading (no request pile-up)
                        if (img.dataset.loading === "1") return;
                        img.dataset.loading = "1";
                        img.onload = img.onerror = () => { img.dataset.loading = "0"; };
                        img.src = `${cam.image_url}&t=${Date.now()}`;
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
        // White spots get their own column; pink-red when over the dashboard alarm limit
        const ws = Number(row.white_spots) || 0;
        const wsOver = ws > ALARM_LIMITS.W;
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${row.sno}</td>
            <td>${row.datetime}</td>
            <td>${row.coil_id}</td>
            <td>${row.total_defects}</td>
            <td class="si-ws-cell${wsOver ? " si-ws-over" : ""}"${wsOver ? ` title="Above limit ${ALARM_LIMITS.W}"` : ""}>${ws}</td>
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

    // Stats tiles + per-type counts: same confidence rule as the coil map
    // (MAP_MIN_CONF), so the tiles match the map and the history table
    const rdVis = countVisibleDefects(data.result);
    const tc = rdVis.counts;
    document.getElementById("rdStatFrames").innerText   = data.total_frames || 0;
    document.getElementById("rdStatDefects").innerText  = rdVis.total;
    document.getElementById("rdStatDefFrames").innerText = rdVis.defFrames;
    document.getElementById("rdStatO").innerText = tc.O;
    document.getElementById("rdStatW").innerText = tc.W;
    document.getElementById("rdStatD").innerText = tc.D;
    document.getElementById("rdStatS").innerText = tc.S;
    document.getElementById("rdStatC").innerText = tc.C;

    // Reset filters: every run opens with all defect types shown
    if (rdCoilMap) rdCoilMap.filters = { W: true,  O: true,  D: true, S: true, C: true };
    rdFilterSync();

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

    // Dashboard: two stacked coil maps, each with its own filter chips
    coilMap  = new CoilMap("cmCanvas1", "cmTip1", "cmStart1", "cmEnd1", { height: 170 });
    coilMap2 = new CoilMap("cmCanvas2", "cmTip2", "cmStart2", "cmEnd2", { height: 170 });
    window.coilMap  = coilMap;
    window.coilMap2 = coilMap2;
    bindDualFilters(1, coilMap);
    bindDualFilters(2, coilMap2);
    // run-details modal: same chips; its CoilMap is created when the modal first opens
    rdFilterSync = bindChipFilters(document.querySelector(".rd-coil-filters"), () => rdCoilMap);

    // Camera feed panel was removed from the dashboard -> updateCameraImages()
    // is no longer polled (the /api/live-ip-camera endpoint is left in place).

    // Initial data fetch
    fetchSystemStatus();
    fetchCurrentStatus();

    // Polling
    setInterval(fetchSystemStatus, 5000);
    setInterval(fetchCurrentStatus, 5000);

    // Auto-refresh after 30min inactivity
    let lastActivity = Date.now();
    const resetTimer = () => lastActivity = Date.now();
    ["click", "mousemove", "keydown", "scroll", "touchstart"].forEach(ev => document.addEventListener(ev, resetTimer));
    setInterval(() => { if (Date.now() - lastActivity > 30 * 60 * 1000) location.reload(); }, 10000);
    
    document.getElementById("runDetailsModal").addEventListener("shown.bs.modal", function () {
        if (!rdCoilMap) {
            rdCoilMap = new CoilMap("rdCoilMap", "rdMapTooltip", "rdCoilStart", "rdCoilEnd");
            rdFilterSync();
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

// ======================== DEFECT CAROUSEL BINDINGS ========================
(function bindDefectCarousel() {
    const prevBtn = document.getElementById("defectPrevBtn");
    const nextBtn = document.getElementById("defectNextBtn");
    if (prevBtn) prevBtn.addEventListener("click", e => { e.stopPropagation(); stepDefect(-1); });
    if (nextBtn) nextBtn.addEventListener("click", e => { e.stopPropagation(); stepDefect(1); });

    document.addEventListener("keydown", e => {
        if (!modalOpen || defectNavList.length < 2) return;
        if (e.key === "ArrowLeft")  { e.preventDefault(); stepDefect(-1); }
        if (e.key === "ArrowRight") { e.preventDefault(); stepDefect(1);  }
    });

    if (CLICK_NAV_ENABLED && typeof zoomWrapper !== "undefined" && zoomWrapper) {
        let downX = 0, downY = 0;
        zoomWrapper.addEventListener("mousedown", e => { downX = e.clientX; downY = e.clientY; });
        zoomWrapper.addEventListener("click", e => {
            if (defectNavList.length < 2 || scale > 1) return;                     // don't fight pan/zoom
            if (Math.hypot(e.clientX - downX, e.clientY - downY) > 4) return;      // it was a drag
            stepDefect(-1);
        });
        zoomWrapper.addEventListener("contextmenu", e => {
            if (defectNavList.length < 2) return;
            e.preventDefault();
            stepDefect(1);
        });
    }
})();