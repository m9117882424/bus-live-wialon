let map = null;
let busMarker = null;
let stopMarkers = [];
let routeLine = null;
let firstMapFitDone = false;

function initMap() {
  map = L.map("map").setView([36.36, 33.91], 12);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap"
  }).addTo(map);
}

function statusClass(status) {
  if (status === "ok") return "ok";
  if (status === "warning") return "warning";
  if (status === "critical") return "critical";
  if (status === "moving") return "moving";
  if (status === "inactive") return "neutral";
  if (status === "finished") return "neutral";
  return "neutral";
}

function stopIcon(state) {
  if (state === "passed") return "✓";
  if (state === "current") return "🚌";
  if (state === "time_passed") return "•";
  return "○";
}

function busDivIcon() {
  return L.divIcon({
    html: `<div style="
      width:38px;
      height:38px;
      border-radius:999px;
      display:flex;
      align-items:center;
      justify-content:center;
      background:#0f766e;
      color:white;
      box-shadow:0 8px 24px rgba(15,23,42,.35);
      border:3px solid white;
      font-size:20px;">🚌</div>`,
    className: "",
    iconSize: [38, 38],
    iconAnchor: [19, 19]
  });
}

function renderSchedule(stops) {
  const root = document.getElementById("schedule");
  root.innerHTML = "";

  stops.forEach(stop => {
    const row = document.createElement("div");
    row.className = `stop ${stop.state || "pending"}`;

    row.innerHTML = `
      <div class="time">${stop.planned_time}</div>
      <div class="dot">${stopIcon(stop.state)}</div>
      <div>
        <div class="stop-name">${stop.name}</div>
        <div class="zone-name">${stop.zone_name || ""}</div>
      </div>
    `;

    root.appendChild(row);
  });
}

function renderMap(data) {
  if (!map) return;

  const points = [];

  stopMarkers.forEach(marker => map.removeLayer(marker));
  stopMarkers = [];

  (data.stops || []).forEach(stop => {
    if (stop.lat && stop.lon) {
      const latlng = [stop.lat, stop.lon];
      points.push(latlng);

      const marker = L.circleMarker(latlng, {
        radius: stop.state === "current" ? 11 : 8,
        weight: 3,
        opacity: 0.9,
        fillOpacity: 0.85
      }).addTo(map);

      marker.bindPopup(`${stop.planned_time}<br>${stop.name}<br>${stop.zone_name || ""}`);
      stopMarkers.push(marker);
    }
  });

  if (routeLine) {
    map.removeLayer(routeLine);
    routeLine = null;
  }

  if (data.show_route_line === true && points.length >= 2) {
    routeLine = L.polyline(points, {
      weight: 5,
      opacity: 0.65
    }).addTo(map);
  }

  const pos = data.bus && data.bus.last_position;

  if (pos && pos.lat && pos.lon) {
    const latlng = [pos.lat, pos.lon];

    if (!busMarker) {
      busMarker = L.marker(latlng, {
        title: data.bus.name,
        icon: busDivIcon()
      }).addTo(map);
    } else {
      busMarker.setLatLng(latlng);
    }

    busMarker.bindPopup(`🚌 ${data.bus.name}<br>Скорость: ${pos.speed || 0} км/ч`);
    points.push(latlng);
  }

  if (!firstMapFitDone && points.length > 0) {
    const bounds = L.latLngBounds(points);
    map.fitBounds(bounds, { padding: [36, 36] });
    firstMapFitDone = true;
  }
}

function renderEta(data) {
  const etaText = document.getElementById("etaText");
  if (!etaText) return;

  if (data.status === "inactive") {
    etaText.textContent = "—";
    return;
  }

  if (data.eta) {
    etaText.textContent = `${data.eta.eta_time} · ${data.eta.eta_delay_text}`;
  } else {
    etaText.textContent = "—";
  }
}

async function loadStatus() {
  try {
    const response = await fetch("/api/bus-status");
    const data = await response.json();

    if (!data.ok) {
      document.getElementById("routeName").textContent = data.route_name || "Маршрут";
      document.getElementById("busName").textContent = data.error || "Ошибка";
      document.getElementById("statusBadge").textContent = "Проверь настройки";
      document.getElementById("statusBadge").className = "status-badge critical";
      renderSchedule(data.stops || []);
      renderEta(data);
      setTimeout(loadStatus, 10000);
      return;
    }

    document.getElementById("routeName").textContent = data.route_name || "Маршрут";
    document.getElementById("routeDirection").textContent = data.direction || "";
    document.getElementById("busName").textContent = data.bus.name || `Unit ${data.bus.unit_id}`;

    const badge = document.getElementById("statusBadge");
    badge.textContent = data.status_text || "—";
    badge.className = `status-badge ${statusClass(data.status)}`;

    document.getElementById("currentStop").textContent =
      data.current_stop
        ? data.current_stop.name
        : data.status === "inactive"
          ? "Маршрут не активен"
          : "В пути";

    document.getElementById("nextStop").textContent =
      data.next_stop
        ? `${data.next_stop.name} · план ${data.next_stop.planned_time}`
        : data.status === "inactive"
          ? "Маршрут не активен"
          : "Финиш";

    renderEta(data);

    const speed = data.bus.last_position && data.bus.last_position.speed;
    document.getElementById("speedText").textContent =
      speed !== null && speed !== undefined ? `${speed} км/ч` : "— км/ч";

    renderSchedule(data.stops || []);
    renderMap(data);

    const refresh = Number(data.refresh_seconds || 10) * 1000;
    setTimeout(loadStatus, refresh);

  } catch (error) {
    document.getElementById("statusBadge").textContent = "Ошибка загрузки";
    document.getElementById("statusBadge").className = "status-badge critical";
    setTimeout(loadStatus, 10000);
  }
}

initMap();
loadStatus();
