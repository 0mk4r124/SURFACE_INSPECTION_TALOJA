let currentPage = 1;
let scale = 1;
let isDragging = false;
let startX, startY, imgX = 0, imgY = 0;
let currentOpenedAnodeId = null;
let currentOpenedBunch = null;
let isImageLoading = false;

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
    $('#date_range-abf').daterangepicker({
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
        $('#date_range-abf').val(start.format("YYYY-MM-DD") + " - " + end.format("YYYY-MM-DD"));
    });

    // Apply event
    $('#date_range-abf').on("apply.daterangepicker", function (ev, picker) {
        const start = picker.startDate.format("YYYY-MM-DD");
        const end = picker.endDate.format("YYYY-MM-DD");
        $(this).val(start === end ? start : start + " - " + end);
    });

    // Cancel event
    $('#date_range-abf').on("cancel.daterangepicker", function () {
        $(this).val("");
    });
});

document.addEventListener("DOMContentLoaded", function () {
    // Initialize daterangepicker with default last 7 days
    $('#date_range-huh').daterangepicker({
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
        $('#date_range-huh').val(start.format("YYYY-MM-DD") + " - " + end.format("YYYY-MM-DD"));
    });

    // Apply event
    $('#date_range-huh').on("apply.daterangepicker", function (ev, picker) {
        const start = picker.startDate.format("YYYY-MM-DD");
        const end = picker.endDate.format("YYYY-MM-DD");
        $(this).val(start === end ? start : start + " - " + end);
    });

    // Cancel event
    $('#date_range-huh').on("cancel.daterangepicker", function () {
        $(this).val("");
    });
});

function fetchLast10Entries() {
    const url = `/api/last-10-entries/`;
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            print("Last 10 entries:", data.data);
            populateTable(data.data, 'HUH_body');
            document.getElementById("total_stem_count").textContent = data.count;
        })
        .catch(err => console.error("Error fetching last 10 entries:", err));
}


function fetchLastConfigurations() {
    fetch('/api/last-10-entries/')
      .then(res => {
        if (!res.ok) {
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.json();
      })
      .then(data => {
        console.log("Fetched stone configurations:", data);
         populateTable(data.data, 'ABF_body');
      })
      .catch(err => {
        console.error('Error fetching stone configurations:', err);
      });
  }


function fetchABFData(page=1) {
    const dateRange = document.getElementById('date_range-abf').value;
    if (!dateRange) {
        alert("Please select a date range");
        return;
    }
    if (dateRange.includes(" - ")) {
        [startDate, endDate] = dateRange.split(" - ").map(s => s.trim());
    } else {
        // Single date selected
        startDate = endDate = dateRange.trim();
    }
    
    // Get anode number search value
    const anodeNumber = document.getElementById('anode_number_search-abf').value.trim();
    let url = `/api/fetch_abf_data/?start_date=${startDate}&end_date=${endDate}&page=${page}`;
    if (anodeNumber) {
        url += `&anode_number=${encodeURIComponent(anodeNumber)}`;
    }
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            populateTable(data.data, 'ABF_body');
            setupPagination(data.total, data.page, data.per_page, "abf");
            document.getElementById("total_bunch_count").textContent = data.total;
        })
        .catch(err => console.error(err));
}

function fetchHUHData(page=1) {
    const dateRange = document.getElementById('date_range-huh').value;
    if (!dateRange) {
        alert("Please select a date range");
        return;
    }

    if (dateRange.includes(" - ")) {
        [startDate, endDate] = dateRange.split(" - ").map(s => s.trim());
    } else {
        // Single date selected
        startDate = endDate = dateRange.trim();
    }

    // Get anode number search value
    const anodeNumber = document.getElementById('anode_number_search-huh').value.trim();
    let url = `/api/fetch_history_data/?start_date=${startDate}&end_date=${endDate}&page=${page}`;
    if (anodeNumber) {
        url += `&anode_number=${encodeURIComponent(anodeNumber)}`;
    }
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            console.log(data.data);
            populateTable(data.data, 'HUH_body');
            setupPagination(data.total, data.page, data.per_page, "huh");
            document.getElementById("total_stem_count").textContent = data.total;
        })
        .catch(err => console.error(err));
}

function fetchBunchData(bunch_id, loc) {

    let url = "";

    if (loc === "abf") {
        url = `/api/fetch_bunch_data/?bunch_id=${bunch_id}`;
    } else if (loc === "fta") {
        url = `/api/fetch_fta_bunch_data/?bunch_id=${bunch_id}`;
    }
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            populateTable(data.data, 'Bunch_body', loc);
            document.getElementById("Bunch_title").innerHTML = `Bunch Number: <b>${bunch_id}</b>`;
        })
     
        .catch(err => console.error(err));
}

function fetchStemData(stem_id) {

    const url = `/api/fetch_stem_data/?stem_id=${stem_id}`;
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            populateTable(data.anodes_data, 'Anodes_body');
            populateTable(data.pr_data, 'Remarks_body');
            document.getElementById("Stem_title").innerHTML = `Stem: <b>${stem_id}</b>`;
        })
        .catch(err => console.error(err));
}

function populateTable(data, table_name, loc='') {
    const tbody = document.getElementById(table_name);
    tbody.innerHTML = '';

    if (table_name === 'ABF_body'){
        data.forEach(row => {
            const tr = document.createElement('tr');

            const Img1Button = row.view_image
            ? `<button class="btn btn-sm btn-primary view-image-btn" data-src="${row.view_image}">Image View</button>`
            : "";

            tr.innerHTML = `
                <td>${row.sno}</td>
                <td>${row.defect_name}</td>
                <td>${row.defect_size} mm</td>
                <td>${row.cycle_uid}</td>
                <td>${row.create_time}</td>
                <td>${ Img1Button }</td>
                `;
            tbody.appendChild(tr);
        });
    }
    else if (table_name === 'Bunch_body') {
        data.forEach(row => {
            const tr = document.createElement('tr');

            tr.innerHTML = `
                <td>${row.sno}</td>
                <td>${row.anode_number}</td>
                <td>${row.capture_time}</td>
                <td>
                ${row.view_image 
                    ? `<button class="btn btn-sm btn-primary view-image-btn" data-loc="${loc}" data-anodeid="${row.anode_number}" data-bunch_name="${row.bunch_name}" data-src="${row.view_image}">Image View</button>` 
                    : 'No Image'}
                </td>
            `;
            tbody.appendChild(tr);
        });
    }
    else if (table_name === 'HUH_body') {
        data.forEach(row => {
            const tr = document.createElement('tr');

            const Img1Button = row.view_image
            ? `<button class="btn btn-sm btn-primary view-image-btn" data-src="${row.view_image}">Image View</button>`
            : "";

            tr.innerHTML = `
                <td>${row.sno}</td>
                <td>${row.defect_name}</td>
                <td>${row.defect_size} mm</td>
                <td>${row.cycle_uid}</td>
                <td>${row.create_time}</td>
                <td>${ Img1Button }</td>
                `;
            tbody.appendChild(tr);
        });
    }
    else if (table_name === 'Anodes_body') {
        data.forEach(row => {
            const tr = document.createElement('tr');

            tr.innerHTML = `
                <td>${row.sno}</td>
                <td>${row.anode_number}</td>
                <td>${row.position}</td>
                <td>${row.capture_time}</td>
                <td>
                ${row.view_image 
                    ? `<button class="btn btn-sm btn-primary view-stem-image-btn" data-anodeid="${row.anode_number}" data-stemid="${row.stemid}" data-src="${row.view_image}">Image View</button>` 
                    : 'No Image'}
                </td>
            `;
            tbody.appendChild(tr);
        });
    }
    else if (table_name === 'Remarks_body') {
        data.forEach(row => {
            const tr = document.createElement('tr');

            tr.innerHTML = `
                <td>${row.sno}</td>
                <td>${row.zone}</td>
                <td>${row.pot_number}</td>
                <td>${row.capture_time}</td>
                <td>${row.remark}</td>
                <td>
                ${row.view_image 
                    ? `<button class="btn btn-sm btn-primary view-stem-image-btn" data-stemid="${row.stem_number}" data-src="${row.view_image}">Image View</button>` 
                    : 'No Image'}
                </td>
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
            if (tab === 'abf') {if (!isNaN(page)) fetchABFData(page);}
            if (tab === 'huh') {if (!isNaN(page)) fetchHUHData(page);}
        });
    });
}

document.getElementById('updateImageBtn').addEventListener("click", function () {
    const loc = document.getElementById("loc_hidden").value;
    const old_anode = document.getElementById("anodeId_hidden").value;
    const newAnodeId = document.getElementById("editAnodeInput").value;
    const bunchId = document.getElementById("bunchId_hidden").value;
    const stemid = document.getElementById("stemid_hidden").value;

    const payload = {
        loc: loc,
        old_anode: old_anode,
        new_anode: newAnodeId,
    };
    
    if (bunchId) payload.bunch = bunchId;
    if (stemid) payload.stemid = stemid;

    fetch(`/api/update_anode/`, {
        method: "POST",
        headers: { 
            "X-CSRFToken": csrftoken,
            "Content-Type": "application/json", },
        body: JSON.stringify(payload),
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            bootstrap.Modal.getInstance(document.getElementById("imageModal")).hide();
            if (bunchId) {fetchBunchData(bunchId, loc);}
            else if (stemid) {fetchStemData(stemid);}
        }
        else alert("Failed to update")
    })
    .catch(err => console.error(err));
});

// document.addEventListener('click', function (e) {
//     const btn = e.target.closest('.view-image-btn');
//     if (!btn) return;

//     const loc = btn.dataset.loc;
//     const imgSrc = btn.dataset.src;
//     const bunch_id = btn.dataset.bunch_name;
//     const anode_id = btn.dataset.anodeid;

//     document.getElementById("loc_hidden").value = loc;
//     document.getElementById("anodeId_hidden").value = anode_id;
//     document.getElementById("bunchId_hidden").value = bunch_id;
//     document.getElementById("stemid_hidden").value = "";

//     document.getElementById("BunchID_title").innerHTML = `Bunch Number: <b>${bunch_id}</b>`;
//     document.getElementById("editAnodeInput").value = anode_id;

//     const modal = new bootstrap.Modal(document.getElementById('imageModal'));
//     modal.show();

//     // attach once
//     document.getElementById("imageModal").dataset.src = imgSrc;
// });

document.addEventListener('click', function (e) {
    const btn = e.target.closest('.view-stem-image-btn');
    if (!btn) return;

    const imgSrc = btn.dataset.src;
    const stemid = btn.dataset.stemid;
    const anode_id = btn.dataset.anodeid;

    document.getElementById("anodeId_hidden").value = anode_id;
    document.getElementById("stemid_hidden").value = stemid;
    document.getElementById("bunchId_hidden").value = "";

    document.getElementById("BunchID_title").innerHTML = `Stem Number: <b>${stemid}</b>`;
    document.getElementById("editAnodeInput").value = anode_id;

    const modal = new bootstrap.Modal(document.getElementById('imageModal'));
    modal.show();

    document.getElementById("imageModal").dataset.src = imgSrc;
});

document.addEventListener('click', function(e) {

    const btn = e.target.closest('.view-table-btn');
    if (!btn) return;
    e.preventDefault();

    // Detect which button was clicked
    const abf_id = btn.getAttribute('data-bunchid');
    const fta_id = btn.getAttribute('data-ftabunchid');

    if (abf_id) {
        fetchBunchData(abf_id, "abf");
    }
    else if (fta_id) {
        fetchBunchData(fta_id, "fta");
    }

    // Show modal
    const tableModal = new bootstrap.Modal(document.getElementById('tableModal'));
    tableModal.show();
});

document.addEventListener('click', function(e) {

    // Check if a "View" button was clicked
    const btn = e.target.closest('.view-tab-btn');
    if (!btn) return;
    e.preventDefault(); // prevent default action

    const bunch_id = btn.getAttribute('data-stemnumber');
    fetchStemData(bunch_id)

    // Show the modal
    const tabModal = new bootstrap.Modal(document.getElementById('tabModal'));
    tabModal.show();
});

document.getElementById('tabModal')
    .addEventListener('shown.bs.modal', function () {

        const firstTab = this.querySelector('.nav-link');
        bootstrap.Tab.getOrCreateInstance(firstTab).show();
});

document.getElementById("modalImage").addEventListener("load", function () {

    isImageLoading = false;
    document.getElementById("imageLoader").style.display = "none";
    this.style.display = "block";
});

document.getElementById("modalImage").addEventListener("error", function () {
    if (!isImageLoading) return;   // ignore modal close/reset

    isImageLoading = false;
    document.getElementById("imageLoader").style.display = "none";
    alert("Failed to load image");
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

        // Force browser paint before loading
        requestAnimationFrame(() => {
            img.src = "";
            img.src = src;
        });
});




// Fetch and update camera statuses

// const systemData = {
//         'ABF Camera 1': {
//             type: 'CAM',
//             status: 'ACTIVE',
//             ip: '192.168.1.10',
//             cam_serial: '123456',
//             last_ping: '20-01-2025 12:42PM',
//             top: 27,
//             left: 14
//         },
//         'ABF Camera 2': {
//             type: 'CAM',
//             status: 'ACTIVE',
//             ip: '192.168.1.11',
//             cam_serial: '234567',
//             last_ping: '20-01-2025 12:42PM',
//             top: 27,
//             left: 27
//         },
//         'FTA Crane 1': {
//             type: 'NUC',
//             status: 'INACTIVE',
//             ip: '192.168.1.20',
//             cam_serial: '',
//             last_ping: '20-01-2025 12:42PM',
//             top: 65,
//             left: 9
//         },
//         'FTA Crane 2': {
//             type: 'NUC',
//             status: 'ACTIVE',
//             ip: '192.168.1.21',
//             cam_serial: '',
//             last_ping: '20-01-2025 12:42PM',
//             top: 65,
//             left: 28
//         },
//         'HUH Camera 1': {
//             type: 'CAM',
//             status: 'ACTIVE',
//             ip: '192.168.1.30',
//             cam_serial: '345678',
//             last_ping: '20-01-2025 12:42PM',
//             top: 68,
//             left: 51
//         },
//         'HUH Camera 2': {
//             type: 'CAM',
//             status: 'INACTIVE',
//             ip: '192.168.1.31',
//             cam_serial: '456789',
//             last_ping: '20-01-2025 12:42PM',
//             top: 68,
//             left: 65
//         },
//         'Shotblast Camera 1': {
//             type: 'CAM',
//             status: 'DISABLED',
//             ip: '192.168.1.40',
//             cam_serial: '567890',
//             last_ping: '',
//             top: 24,
//             left: 74
//         },
//         'Shotblast Camera 2': {
//             type: 'CAM',
//             status: 'DISABLED',
//             ip: '192.168.1.41',
//             cam_serial: '678901',
//             last_ping: '',
//             top: 24,
//             left: 87
//         },
//         'Web Server': {
//             type: 'SERVER',
//             status: 'ACTIVE',
//             ip: '192.168.1.100',
//             last_ping: '20-01-2025 12:42PM',
//             top: 15,
//             left: 50
//         },
//         'Mobile Server': {
//             type: 'SERVER',
//             status: 'ACTIVE',
//             ip: '192.168.1.101',
//             last_ping: '20-01-2025 12:42PM',
//             top: 20,
//             left: 50
//         },
//         'ABF': {
//             type: 'LOC',
//             name: 'Anode Baking Furnace',
//             current_stem: '',
//             current_bunch: 'B001',
//             anodes: ['A1', 'A2'],
//             top: 30,
//             left: 20
//         },
//         'FTA': {
//             type: 'LOC',
//             name: 'Furnace Tending Area',
//             current_stem: '',
//             current_bunch: 'B001',
//             anodes: ['A1', 'A2'],
//             top: 60,
//             left: 20
//         },
//         'HUH': {
//             type: 'LOC',
//             name: 'Hooking Un-Hooking',
//             current_stem: 'S001',
//             current_bunch: 'B001',
//             anodes: ['A1', 'A2'],
//             top: 60,
//             left: 60
//         },
//         'SHOTBLAST': {
//             type: 'LOC',
//             name: 'Shotblast',
//             current_stem: 'S001',
//             current_bunch: '',
//             anodes: [],
//             top: 30,
//             left: 80
//         },
//         'POTROOM': {
//             type: 'LOC',
//             name: 'Potroom',
//             current_stem: 'S001',
//             current_bunch: '',
//             anodes: [],
//             top: 60,
//             left: 80
//         }
// };

function updateSystemStatus() {
    const cameraContainer = document.getElementById('camera-indicators');
    const locationContainer = document.getElementById('location-dots');
    
    cameraContainer.innerHTML = '';
    locationContainer.innerHTML = '';

    // Create invisible location info dots
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

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    // fetchSystemStatus();
    // Update every 10 seconds
    // setInterval(fetchSystemStatus, 10000);
    // setInterval(fetchLast10Entries, 10000);
    fetchLastConfigurations()
  
});









document.addEventListener('click', function (e) {
    const btn = e.target.closest('.view-image-btn');
    if (!btn) return;
    document.getElementById("vehicle_number").innerHTML = `<b>Defect Image</b>`;

    const modal = new bootstrap.Modal(document.getElementById('imageModal'));
    modal.show();
    document.getElementById("imageModal").dataset.src = btn.dataset.src;
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

        // Force browser paint before loading
        requestAnimationFrame(() => {
            img.src = "";
            img.onload = function() {
                loader.style.display = "none";
                img.style.display = "block";
                isImageLoading = false;
            };
            img.onerror = function() {
                loader.style.display = "none";
                img.style.display = "block";
                isImageLoading = false;
                // alert("Failed to load image");
            };
            img.src = src;
        });
});