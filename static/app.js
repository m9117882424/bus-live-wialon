let map = null;
let busMarker = null;
let stopMarkers = [];
let routeLine = null;
let firstMapFitDone = false;
let latestData = null;

const LANG_KEY = "bus_live_lang";
const DEFAULT_LANG = "tr";

const i18n = {
  tr: {
    pageTitle: "Servis Takibi",
    eyebrow: "Servis online",
    routeFallback: "Güzergah",
    loading: "Yükleniyor...",
    loadingShort: "Yükleniyor",
    currentStopLabel: "Mevcut durak",
    nextStopLabel: "Sonraki durak",
    etaLabel: "Tahmini varış",
    scheduleTitle: "Saatler",
    mapTitle: "Harita",
    morning: "Sabah",
    evening: "Akşam",
    unit: "Unit",
    speedUnit: "km/sa",
    speedLabel: "Hız",
    checkSettings: "Ayarları kontrol edin",
    error: "Hata",
    loadingError: "Yükleme hatası",
    ok: "Zamanında",
    warning: "Kısa gecikme",
    critical: "Gecikiyor",
    moving: "Servis yolda",
    inactive: "Güzergah şu anda aktif değil",
    inactiveShort: "Güzergah aktif değil",
    finished: "Güzergah tamamlandı",
    noRoute: "Güzergah ayarlanmamış",
    onRoute: "Yolda",
    finish: "Bitiş",
    plan: "plan",
    onTime: "zamanında",
    minute: "dk"
  },
  ru: {
    pageTitle: "Автобус онлайн",
    eyebrow: "Автобус онлайн",
    routeFallback: "Маршрут",
    loading: "Загрузка...",
    loadingShort: "Загрузка",
    currentStopLabel: "Текущая остановка",
    nextStopLabel: "Следующая остановка",
    etaLabel: "Прогноз прибытия",
    scheduleTitle: "Расписание",
    mapTitle: "Карта",
    morning: "Утро",
    evening: "Вечер",
    unit: "Unit",
    speedUnit: "км/ч",
    speedLabel: "Скорость",
    checkSettings: "Проверь настройки",
    error: "Ошибка",
    loadingError: "Ошибка загрузки",
    ok: "По расписанию",
    warning: "Небольшое опоздание",
    critical: "Опаздывает",
    moving: "Автобус в пути",
    inactive: "Маршрут сейчас не активен",
    inactiveShort: "Маршрут не активен",
    finished: "Маршрут завершён",
    noRoute: "Маршрут не настроен",
    onRoute: "В пути",
    finish: "Финиш",
    plan: "план",
    onTime: "по расписанию",
    minute: "мин"
  }
};

function getLang() {
  const saved = localStorage.getItem(LANG_KEY);
  return saved === "ru" || saved === "tr" ? saved : DEFAULT_LANG;
}

function setLang(lang) {
  if (lang !== "ru" && lang !== "tr") return;
  localStorage.setItem(LANG_KEY, lang);
  applyStaticTranslations();
  if (latestData) renderData(latestData);
}

function t(key) {
  return i18n[getLang()][key] || i18n[DEFAULT_LANG][key] || key;
}

function applyStaticTranslations() {
  const lang = getLang();
  document.documentElement.lang = lang;
  document.title = t("pageTitle");

  const elements = {
    eyebrow: "eyebrow",
    currentStopLabel: "currentStopLabel",
    nextStopLabel: "nextStopLabel",
    etaLabel: "etaLabel",
    scheduleTitle: "scheduleTitle",
    mapTitle: "mapTitle"
  };

  Object.entries(elements).forEach(([id, key]) => {
    const el = document.getElementById(id);
    if (el) el.textContent = t(key);
  });

  const langRu = document.getElementById("langRu");
  const langTr = document.getElementById("langTr");

  if (langRu) langRu.classList.toggle("active", lang === "ru");
  if (langTr) langTr.classList.toggle("active", lang === "tr");
}

function routeNameForLang(data) {
  const lang = getLang();
  if (lang === "ru") return data.route_name_ru || data.route_name || t("routeFallback");
  return data.route_name_tr || data.route_name || t("routeFallback");
}

function initLanguageSwitcher() {
  const langRu = document.getElementById("langRu");
  const langTr = document.getElementById("langTr");

  if (langRu) langRu.addEventListener("click", () => setLang("ru"));
  if (langTr) langTr.addEventListener("click", () => setLang("tr"));

  applyStaticTranslations();
}

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

function statusText(data) {
  if (data.status === "ok") return t("ok");
  if (data.status === "warning") return `${t("warning")}: +${data.delay_minutes || 0} ${t("minute")}`;
  if (data.status === "critical") return `${t("critical")}: +${data.delay_minutes || 0} ${t("minute")}`;
  if (data.status === "moving") return t("moving");
  if (data.status === "inactive") return t("inactive");
  if (data.status === "finished") return t("finished");
  if (data.status === "no_route") return t("noRoute");
  return data.status_text || "—";
}

function directionText(direction) {
  if (direction === "morning") return t("morning");
  if (direction === "evening") return t("evening");
  return direction || "";
}

function etaDelayText(eta) {
  if (!eta) return "—";
  const delay = Number(eta.eta_delay_minutes || 0);
  if (delay > 0) return `+${delay} ${t("minute")}`;
  if (delay < 0) return `${delay} ${t("minute")}`;
  return t("onTime");
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

    busMarker.bindPopup(`🚌 ${data.bus.name}<br>${t("speedLabel")}: ${pos.speed || 0} ${t("speedUnit")}`);
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
    etaText.textContent = `${data.eta.eta_time} · ${etaDelayText(data.eta)}`;
  } else {
    etaText.textContent = "—";
  }
}

function renderData(data) {
  latestData = data;

  if (!data.ok) {
    document.getElementById("routeName").textContent = data.route_name || t("routeFallback");
    document.getElementById("busName").textContent = data.error || t("error");
    document.getElementById("statusBadge").textContent = t("checkSettings");
    document.getElementById("statusBadge").className = "status-badge critical";
    renderSchedule(data.stops || []);
    renderEta(data);
    return;
  }

  document.getElementById("routeName").textContent = routeNameForLang(data);
  document.getElementById("routeDirection").textContent = directionText(data.direction);
  document.getElementById("busName").textContent = data.bus.name || `${t("unit")} ${data.bus.unit_id}`;

  const badge = document.getElementById("statusBadge");
  badge.textContent = statusText(data);
  badge.className = `status-badge ${statusClass(data.status)}`;

  document.getElementById("currentStop").textContent =
    data.current_stop
      ? data.current_stop.name
      : data.status === "inactive"
        ? t("inactiveShort")
        : t("onRoute");

  document.getElementById("nextStop").textContent =
    data.next_stop
      ? `${data.next_stop.name} · ${t("plan")} ${data.next_stop.planned_time}`
      : data.status === "inactive"
        ? t("inactiveShort")
        : t("finish");

  renderEta(data);

  const speed = data.bus.last_position && data.bus.last_position.speed;
  document.getElementById("speedText").textContent =
    speed !== null && speed !== undefined ? `${speed} ${t("speedUnit")}` : `— ${t("speedUnit")}`;

  renderSchedule(data.stops || []);
  renderMap(data);
}

async function loadStatus() {
  try {
    const response = await fetch("/api/bus-status");
    const data = await response.json();
    renderData(data);

    const refresh = Number(data.refresh_seconds || 10) * 1000;
    setTimeout(loadStatus, refresh);

  } catch (error) {
    document.getElementById("statusBadge").textContent = t("loadingError");
    document.getElementById("statusBadge").className = "status-badge critical";
    setTimeout(loadStatus, 10000);
  }
}

initLanguageSwitcher();
initMap();
loadStatus();
