import json
import math
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

WIALON_HOST = os.getenv("WIALON_HOST", "https://hst-api.wialon.com").rstrip("/")
WIALON_TOKEN = os.getenv("WIALON_TOKEN", "").strip()
TRACKED_BUS_ID_ENV = os.getenv("TRACKED_BUS_ID", "").strip()
TRACKED_BUS_NAME_ENV = os.getenv("TRACKED_BUS_NAME", "").strip()
ROUTE_CONFIG_PATH = os.getenv("ROUTE_CONFIG", "route_config.json")
STATIC_DIR = "static"

ROUTE_PROGRESS = {
    "date": None,
    "last_passed_index": -1,
    "current_index": None,
    "recent_hits": {},
}


class WialonError(Exception):
    pass


class WialonClient:
    def __init__(self, host: str, token: str):
        self.host = host
        self.token = token
        self.sid: Optional[str] = None
        self.sid_created_at: Optional[float] = None

    def ensure_login(self) -> None:
        if self.sid and self.sid_created_at and time.time() - self.sid_created_at < 1800:
            return
        self.login()

    def call(
        self,
        svc: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        attach_sid: bool = True,
        retry_on_sid_error: bool = True,
    ) -> Any:
        request_params = {"svc": svc, "params": json.dumps(params or {}, ensure_ascii=False)}

        if attach_sid and self.sid:
            request_params["sid"] = self.sid

        response = requests.get(f"{self.host}/wialon/ajax.html", params=request_params, timeout=30)
        try:
            data = response.json()
        except Exception as exc:
            raise WialonError(f"Wialon returned non-JSON response. HTTP {response.status_code}: {response.text[:500]}") from exc

        if isinstance(data, dict) and "error" in data:
            if data.get("error") == 1 and svc != "token/login" and retry_on_sid_error:
                self.sid = None
                self.sid_created_at = None
                self.login()
                return self.call(svc, params, attach_sid=True, retry_on_sid_error=False)

            raise WialonError(f"Wialon API error in {svc}: {data}")

        return data

    def login(self) -> None:
        if not self.token:
            raise WialonError("WIALON_TOKEN is empty")

        self.sid = None
        self.sid_created_at = None

        data = self.call(
            "token/login",
            {"token": self.token},
            attach_sid=False,
            retry_on_sid_error=False,
        )
        sid = data.get("eid")
        if not sid:
            raise WialonError(f"Login failed, no eid in response: {data}")
        self.sid = sid
        self.sid_created_at = time.time()

    def search_items(self, items_type: str, flags: int, limit: int = 10000) -> List[Dict[str, Any]]:
        self.ensure_login()
        data = self.call(
            "core/search_items",
            {
                "spec": {
                    "itemsType": items_type,
                    "propName": "sys_name",
                    "propValueMask": "*",
                    "sortType": "sys_name",
                },
                "force": 1,
                "flags": flags,
                "from": 0,
                "to": limit,
            },
        )
        return data.get("items", [])

    def get_units(self) -> List[Dict[str, Any]]:
        return self.search_items("avl_unit", flags=1 | 1024)

    def get_zones_by_unit(self, unit_id: int, zone_id_map: Dict[str, List[int]]) -> Dict[str, Any]:
        self.ensure_login()
        return self.call("resource/get_zones_by_unit", {"spec": {"zoneId": zone_id_map, "units": [unit_id], "time": 0}})


def load_route_config() -> Dict[str, Any]:
    with open(ROUTE_CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def parse_hhmm(value: str) -> datetime:
    today = date.today()
    hour, minute = map(int, value.split(":"))
    return datetime(today.year, today.month, today.day, hour, minute)


def is_route_active(route_config: Dict[str, Any]) -> bool:
    active_from = route_config.get("active_from")
    active_to = route_config.get("active_to")
    if not active_from or not active_to:
        return True
    now = datetime.now()
    start = parse_hhmm(active_from)
    end = parse_hhmm(active_to)
    if end < start:
        return now >= start or now <= end
    return start <= now <= end


def reset_progress_if_needed(route_active: bool) -> None:
    today_key = date.today().isoformat()
    if ROUTE_PROGRESS["date"] != today_key:
        ROUTE_PROGRESS.update({"date": today_key, "last_passed_index": -1, "current_index": None, "recent_hits": {}})
    if not route_active:
        ROUTE_PROGRESS.update({"last_passed_index": -1, "current_index": None, "recent_hits": {}})


def normalize_text(value: str) -> str:
    return (
        value.lower()
        .replace("ı", "i")
        .replace("İ", "i")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
        .strip()
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_config_bus_id(route_config: Dict[str, Any]) -> Optional[int]:
    unit_id = (route_config.get("tracked_bus") or {}).get("unit_id")
    try:
        return int(unit_id) if unit_id is not None else None
    except Exception:
        return None


def find_tracked_bus(client: WialonClient, route_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    units = client.get_units()
    target_id: Optional[int] = None
    if TRACKED_BUS_ID_ENV:
        try:
            target_id = int(TRACKED_BUS_ID_ENV)
        except ValueError:
            target_id = None
    if target_id is None:
        target_id = get_config_bus_id(route_config)
    if target_id is not None:
        for unit in units:
            try:
                if int(unit.get("id", -1)) == target_id:
                    return unit
            except Exception:
                continue

    bus_name = TRACKED_BUS_NAME_ENV or ((route_config.get("tracked_bus") or {}).get("name") or "")
    if bus_name:
        needle = normalize_text(bus_name)
        matches = [unit for unit in units if needle in normalize_text(str(unit.get("nm", "")))]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return {"error": "multiple_bus_matches", "matches": [{"id": u.get("id"), "name": u.get("nm")} for u in matches]}
    return None


def build_zone_id_map(stops: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    result: Dict[str, List[int]] = {}
    for stop in stops:
        resource_id = stop.get("resource_id")
        zone_id = stop.get("zone_id")
        if resource_id is None or zone_id is None:
            continue
        result.setdefault(str(resource_id), [])
        if int(zone_id) not in result[str(resource_id)]:
            result[str(resource_id)].append(int(zone_id))
    return result


def get_current_zone_ids(zones_by_unit_response: Dict[str, Any], unit_id: int) -> List[Tuple[int, int]]:
    found: List[Tuple[int, int]] = []
    if not isinstance(zones_by_unit_response, dict):
        return found
    for resource_id_str, zones_dict in zones_by_unit_response.items():
        if not isinstance(zones_dict, dict):
            continue
        for zone_id_str, unit_ids in zones_dict.items():
            if isinstance(unit_ids, list) and unit_id in unit_ids:
                found.append((int(resource_id_str), int(zone_id_str)))
    return found


def get_stop_zone_pair(stop: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    return stop.get("resource_id"), stop.get("zone_id")


def get_hit_stop_indices(stops: List[Dict[str, Any]], zone_pairs: List[Tuple[int, int]]) -> List[int]:
    return [idx for idx, stop in enumerate(stops) if get_stop_zone_pair(stop) in zone_pairs]


def get_distance_hit_indices(stops: List[Dict[str, Any]], bus_position: Dict[str, Any], default_radius_meters: float) -> List[int]:
    bus_lat = bus_position.get("y")
    bus_lon = bus_position.get("x")
    if bus_lat is None or bus_lon is None:
        return []
    hits: List[int] = []
    for idx, stop in enumerate(stops):
        if stop.get("lat") is None or stop.get("lon") is None:
            continue
        radius_meters = float(stop.get("radius_meters") or default_radius_meters)
        distance_m = haversine_km(float(bus_lat), float(bus_lon), float(stop["lat"]), float(stop["lon"])) * 1000
        if distance_m <= radius_meters:
            hits.append(idx)
    return hits


def build_effective_zone_pairs(route_config: Dict[str, Any], stops: List[Dict[str, Any]], bus_position: Dict[str, Any], wialon_zone_pairs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    default_radius_meters = float(route_config.get("stop_match_radius_meters", 100))
    grace_seconds = float(route_config.get("stop_hit_grace_seconds", 10))
    now_ts = time.time()
    instant_hits = set(get_hit_stop_indices(stops, wialon_zone_pairs))
    instant_hits.update(get_distance_hit_indices(stops, bus_position, default_radius_meters))

    recent_hits = ROUTE_PROGRESS.setdefault("recent_hits", {})
    for idx in instant_hits:
        recent_hits[str(idx)] = now_ts

    effective_indices = set()
    for key in list(recent_hits.keys()):
        try:
            idx = int(key)
            hit_ts = float(recent_hits[key])
        except Exception:
            recent_hits.pop(key, None)
            continue
        if now_ts - hit_ts <= grace_seconds:
            effective_indices.add(idx)
        else:
            recent_hits.pop(key, None)

    pairs: List[Tuple[int, int]] = []
    for idx in sorted(effective_indices):
        if 0 <= idx < len(stops):
            pair = get_stop_zone_pair(stops[idx])
            if pair[0] is not None and pair[1] is not None:
                pairs.append(pair)
    return pairs


def update_ordered_progress(stops: List[Dict[str, Any]], current_zone_pairs: List[Tuple[int, int]]) -> Dict[str, Any]:
    if not stops:
        return {"current_stop_index": None, "next_stop_index": None, "last_passed_index": -1, "hit_indices": [], "finished": False}

    last_passed = int(ROUTE_PROGRESS.get("last_passed_index", -1))
    hit_indices = get_hit_stop_indices(stops, current_zone_pairs)
    current_stop_index = None

    if last_passed < 0:
        if 0 in hit_indices:
            current_stop_index = 0
            last_passed = 0
            ROUTE_PROGRESS["last_passed_index"] = 0
            ROUTE_PROGRESS["current_index"] = 0
        else:
            ROUTE_PROGRESS["current_index"] = None
    else:
        forward_hits = [idx for idx in hit_indices if idx > last_passed]
        if forward_hits:
            current_stop_index = max(forward_hits)
            last_passed = current_stop_index
            ROUTE_PROGRESS["last_passed_index"] = last_passed
            ROUTE_PROGRESS["current_index"] = current_stop_index
        elif last_passed in hit_indices:
            current_stop_index = last_passed
            ROUTE_PROGRESS["current_index"] = current_stop_index
        else:
            ROUTE_PROGRESS["current_index"] = None

    next_stop_index = last_passed + 1
    if next_stop_index >= len(stops):
        next_stop_index = None

    return {
        "current_stop_index": current_stop_index,
        "next_stop_index": next_stop_index,
        "last_passed_index": last_passed,
        "hit_indices": hit_indices,
        "finished": next_stop_index is None and current_stop_index is None,
    }


def build_eta_info(route_config: Dict[str, Any], bus_position: Dict[str, Any], next_stop: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not next_stop:
        return None
    bus_lat = bus_position.get("y")
    bus_lon = bus_position.get("x")
    if bus_lat is None or bus_lon is None or next_stop.get("lat") is None or next_stop.get("lon") is None:
        return None
    speed_kmh = float(route_config.get("eta_speed_kmh", 60)) or 60
    distance_km = haversine_km(float(bus_lat), float(bus_lon), float(next_stop["lat"]), float(next_stop["lon"]))
    eta_minutes = int(round((distance_km / speed_kmh) * 60))
    eta_dt = datetime.now() + timedelta(minutes=eta_minutes)
    planned_dt = parse_hhmm(next_stop["planned_time"])
    eta_delay_minutes = int((eta_dt - planned_dt).total_seconds() // 60)
    if eta_delay_minutes > 0:
        delay_text = f"gecikme: {abs(eta_delay_minutes)} dk"
    elif eta_delay_minutes < 0:
        delay_text = f"erken: {abs(eta_delay_minutes)} dk"
    else:
        delay_text = "zamanında"
    return {
        "speed_kmh": speed_kmh,
        "distance_km": round(distance_km, 2),
        "eta_minutes": eta_minutes,
        "eta_time": eta_dt.strftime("%H:%M"),
        "planned_time": next_stop["planned_time"],
        "eta_delay_minutes": eta_delay_minutes,
        "eta_delay_text": delay_text,
    }


def calculate_status_by_progress(route_config: Dict[str, Any], stops: List[Dict[str, Any]], progress: Dict[str, Any]) -> Dict[str, Any]:
    warning = int(route_config.get("delay_warning_minutes", 3))
    critical = int(route_config.get("delay_critical_minutes", 7))
    current_idx = progress.get("current_stop_index")
    next_idx = progress.get("next_stop_index")

    if not stops:
        return {"status": "no_route", "status_text": "Güzergah ayarlanmamış", "delay_minutes": None, "current_stop": None, "next_stop": None}
    if progress.get("finished"):
        return {"status": "finished", "status_text": "Güzergah tamamlandı", "delay_minutes": None, "current_stop": None, "next_stop": None}
    if current_idx is not None:
        current_stop = stops[current_idx]
        delay_minutes = int((datetime.now() - parse_hhmm(current_stop["planned_time"])).total_seconds() // 60)
        if delay_minutes <= warning:
            status, status_text = "ok", "Zamanında"
        elif delay_minutes <= critical:
            status, status_text = "warning", f"Kısa gecikme: {abs(delay_minutes)} dk"
        else:
            status, status_text = "critical", f"Gecikme: {abs(delay_minutes)} dk"
        next_stop = stops[current_idx + 1] if current_idx + 1 < len(stops) else None
        return {"status": status, "status_text": status_text, "delay_minutes": delay_minutes, "current_stop": current_stop, "next_stop": next_stop}

    next_stop = stops[next_idx] if next_idx is not None else None
    return {"status": "moving", "status_text": "Servis yolda", "delay_minutes": None, "current_stop": None, "next_stop": next_stop}


def render_stop_states(stops: List[Dict[str, Any]], progress: Optional[Dict[str, Any]], route_active: bool = True) -> List[Dict[str, Any]]:
    if not route_active or progress is None:
        return [{**stop, "state": "pending"} for stop in stops]
    last_passed = int(progress.get("last_passed_index", -1))
    current_idx = progress.get("current_stop_index")
    result = []
    for idx, stop in enumerate(stops):
        state = "pending"
        if current_idx is not None and idx == current_idx:
            state = "current"
        elif idx <= last_passed:
            state = "passed"
        result.append({**stop, "state": state})
    return result


app = FastAPI(title="Bus Live Wialon MVP")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
client = WialonClient(WIALON_HOST, WIALON_TOKEN)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/health")
def health():
    return {"ok": True, "wialon_host": WIALON_HOST, "route_config": ROUTE_CONFIG_PATH, "route_progress": ROUTE_PROGRESS}


@app.post("/api/reset-progress")
def reset_progress():
    ROUTE_PROGRESS.update({"date": date.today().isoformat(), "last_passed_index": -1, "current_index": None, "recent_hits": {}})
    return {"ok": True, "message": "Route progress reset", "route_progress": ROUTE_PROGRESS}


@app.post("/api/set-progress/{last_passed_index}")
def set_progress(last_passed_index: int):
    route_config = load_route_config()
    stops = route_config.get("stops", [])
    if not stops:
        return JSONResponse(status_code=400, content={"ok": False, "error": "No stops configured"})
    last_passed_index = max(-1, min(last_passed_index, len(stops) - 1))
    ROUTE_PROGRESS.update({"date": date.today().isoformat(), "last_passed_index": last_passed_index, "current_index": None, "recent_hits": {}})
    next_stop = stops[last_passed_index + 1] if last_passed_index + 1 < len(stops) else None
    return {"ok": True, "message": "Route progress updated", "last_passed_index": last_passed_index, "next_stop": next_stop, "route_progress": ROUTE_PROGRESS}


def base_response(route_config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "route_name": route_config.get("route_name"),
        "route_name_ru": route_config.get("route_name_ru"),
        "route_name_tr": route_config.get("route_name_tr"),
        "direction": route_config.get("direction"),
        "refresh_seconds": route_config.get("refresh_seconds", 10),
    }


@app.get("/api/bus-status")
def bus_status():
    if not WIALON_TOKEN:
        return JSONResponse(status_code=500, content={"ok": False, "error": "WIALON_TOKEN is not set in .env"})
    try:
        route_config = load_route_config()
        stops = route_config.get("stops", [])
        route_active = is_route_active(route_config)
        reset_progress_if_needed(route_active)
        response_base = base_response(route_config)

        bus = find_tracked_bus(client, route_config)
        if not bus:
            return {**response_base, "ok": False, "error": "Servis bulunamadı. TRACKED_BUS_ID veya tracked_bus.unit_id kontrol edin.", "stops": render_stop_states(stops, None, route_active)}
        if bus.get("error") == "multiple_bus_matches":
            return {**response_base, "ok": False, "error": "Birden fazla servis bulundu. TRACKED_BUS_ID belirtin.", "matches": bus.get("matches")}

        unit_id = int(bus["id"])
        position = bus.get("pos") or {}
        bus_payload = {
            "unit_id": unit_id,
            "name": bus.get("nm"),
            "last_position": {"lat": position.get("y"), "lon": position.get("x"), "speed": position.get("s"), "course": position.get("c"), "time": position.get("t")},
        }

        if not route_active:
            return {
                **response_base,
                "ok": True,
                "eta": None,
                "bus": bus_payload,
                "status": "inactive",
                "status_text": "Güzergah şu anda aktif değil",
                "delay_minutes": None,
                "current_stop": None,
                "next_stop": None,
                "stops": render_stop_states(stops, None, False),
                "updated_at": datetime.now().strftime("%H:%M:%S"),
            }

        zone_id_map = build_zone_id_map(stops)
        zones_raw = client.get_zones_by_unit(unit_id, zone_id_map) if zone_id_map else {}
        wialon_pairs = get_current_zone_ids(zones_raw, unit_id)
        effective_pairs = build_effective_zone_pairs(route_config, stops, position, wialon_pairs)
        progress = update_ordered_progress(stops, effective_pairs)
        status_info = calculate_status_by_progress(route_config, stops, progress)

        return {
            **response_base,
            "ok": True,
            "eta": build_eta_info(route_config, position, status_info.get("next_stop")),
            "bus": bus_payload,
            "status": status_info.get("status"),
            "status_text": status_info.get("status_text"),
            "delay_minutes": status_info.get("delay_minutes"),
            "current_stop": status_info.get("current_stop"),
            "next_stop": status_info.get("next_stop"),
            "stops": render_stop_states(stops, progress, True),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/api/config")
def config():
    try:
        return {"ok": True, "config": load_route_config()}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
