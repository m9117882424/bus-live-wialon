import os
import json
import time
import math
from datetime import datetime, date, timedelta
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

# In-memory daily progress.
# No database. Resets automatically by date and outside active route window.
ROUTE_PROGRESS = {
    "date": None,
    "last_passed_index": -1,
    "current_index": None,
}


class WialonError(Exception):
    pass


class WialonClient:
    def __init__(self, host: str, token: str):
        self.host = host.rstrip("/")
        self.token = token
        self.sid: Optional[str] = None
        self.sid_created_at: Optional[float] = None

    def ensure_login(self) -> None:
        if self.sid and self.sid_created_at and time.time() - self.sid_created_at < 1800:
            return
        self.login()

    def call(self, svc: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if params is None:
            params = {}

        request_params = {
            "svc": svc,
            "params": json.dumps(params, ensure_ascii=False),
        }

        if self.sid:
            request_params["sid"] = self.sid

        response = requests.get(
            f"{self.host}/wialon/ajax.html",
            params=request_params,
            timeout=30,
        )

        try:
            data = response.json()
        except Exception as exc:
            raise WialonError(
                f"Wialon returned non-JSON response. HTTP {response.status_code}: {response.text[:500]}"
            ) from exc

        if isinstance(data, dict) and "error" in data:
            raise WialonError(f"Wialon API error in {svc}: {data}")

        return data

    def login(self) -> None:
        if not self.token:
            raise WialonError("WIALON_TOKEN is empty")

        data = self.call("token/login", {"token": self.token})
        sid = data.get("eid")

        if not sid:
            raise WialonError(f"Login failed, no eid in response: {data}")

        self.sid = sid
        self.sid_created_at = time.time()

    def search_items(
        self,
        items_type: str,
        prop_name: str = "sys_name",
        prop_value_mask: str = "*",
        flags: int = 1,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        self.ensure_login()

        params = {
            "spec": {
                "itemsType": items_type,
                "propName": prop_name,
                "propValueMask": prop_value_mask,
                "sortType": prop_name,
            },
            "force": 1,
            "flags": flags,
            "from": 0,
            "to": limit,
        }

        data = self.call("core/search_items", params)
        return data.get("items", [])

    def get_units(self) -> List[Dict[str, Any]]:
        return self.search_items("avl_unit", flags=1 | 1024)

    def get_zones_by_unit(self, unit_id: int, zone_id_map: Dict[str, List[int]]) -> Dict[str, Any]:
        self.ensure_login()

        params = {
            "spec": {
                "zoneId": zone_id_map,
                "units": [unit_id],
                "time": 0,
            }
        }

        return self.call("resource/get_zones_by_unit", params)


def load_route_config() -> Dict[str, Any]:
    with open(ROUTE_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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
    today_str = date.today().isoformat()

    if ROUTE_PROGRESS["date"] != today_str:
        ROUTE_PROGRESS["date"] = today_str
        ROUTE_PROGRESS["last_passed_index"] = -1
        ROUTE_PROGRESS["current_index"] = None

    if not route_active:
        ROUTE_PROGRESS["last_passed_index"] = -1
        ROUTE_PROGRESS["current_index"] = None


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

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def build_eta_info(
    route_config: Dict[str, Any],
    bus_position: Dict[str, Any],
    next_stop: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not next_stop:
        return None

    bus_lat = bus_position.get("y")
    bus_lon = bus_position.get("x")

    stop_lat = next_stop.get("lat")
    stop_lon = next_stop.get("lon")

    if bus_lat is None or bus_lon is None or stop_lat is None or stop_lon is None:
        return None

    speed_kmh = float(route_config.get("eta_speed_kmh", 60))

    if speed_kmh <= 0:
        speed_kmh = 60

    distance_km = haversine_km(
        float(bus_lat),
        float(bus_lon),
        float(stop_lat),
        float(stop_lon),
    )

    eta_minutes = int(round((distance_km / speed_kmh) * 60))
    eta_dt = datetime.now() + timedelta(minutes=eta_minutes)

    planned_dt = parse_hhmm(next_stop["planned_time"])
    eta_delay_minutes = int((eta_dt - planned_dt).total_seconds() // 60)

    if eta_delay_minutes > 0:
        delay_text = f"+{eta_delay_minutes} мин"
    elif eta_delay_minutes < 0:
        delay_text = f"{eta_delay_minutes} мин"
    else:
        delay_text = "по расписанию"

    return {
        "speed_kmh": speed_kmh,
        "distance_km": round(distance_km, 2),
        "eta_minutes": eta_minutes,
        "eta_time": eta_dt.strftime("%H:%M"),
        "planned_time": next_stop["planned_time"],
        "eta_delay_minutes": eta_delay_minutes,
        "eta_delay_text": delay_text,
    }


def get_config_bus_id(route_config: Dict[str, Any]) -> Optional[int]:
    tracked_bus = route_config.get("tracked_bus") or {}
    unit_id = tracked_bus.get("unit_id")

    if unit_id is None:
        return None

    try:
        return int(unit_id)
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
                if int(unit.get("id")) == target_id:
                    return unit
            except Exception:
                continue

    bus_name = TRACKED_BUS_NAME_ENV or ((route_config.get("tracked_bus") or {}).get("name") or "")
    if bus_name:
        needle = normalize_text(bus_name)
        matches = [
            unit for unit in units
            if needle in normalize_text(str(unit.get("nm", "")))
        ]

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            return {
                "error": "multiple_bus_matches",
                "matches": [
                    {
                        "id": u.get("id"),
                        "name": u.get("nm"),
                    }
                    for u in matches
                ],
            }

    return None


def build_zone_id_map(route_stops: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    result: Dict[str, List[int]] = {}

    for stop in route_stops:
        resource_id = stop.get("resource_id")
        zone_id = stop.get("zone_id")

        if resource_id is None or zone_id is None:
            continue

        key = str(resource_id)
        result.setdefault(key, [])

        if int(zone_id) not in result[key]:
            result[key].append(int(zone_id))

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


def get_hit_stop_indices(
    route_stops: List[Dict[str, Any]],
    current_zone_pairs: List[Tuple[int, int]],
) -> List[int]:
    hit_indices: List[int] = []

    for idx, stop in enumerate(route_stops):
        if get_stop_zone_pair(stop) in current_zone_pairs:
            hit_indices.append(idx)

    return hit_indices


def update_ordered_progress(
    route_stops: List[Dict[str, Any]],
    current_zone_pairs: List[Tuple[int, int]],
) -> Dict[str, Any]:
    """
    Ordered progress with skip-forward rule:
    - Before the first stop is visited, ONLY stop #1 can start the route.
    - After stop #1 is visited, the bus may jump to any later stop.
    - If it jumps forward, all skipped intermediate stops are marked as passed.
      Example: last_passed=0 and current zone is stop #3 => stops #2 and #3 become passed/current.
    - It never moves backward.
    - No DB. Progress is kept in memory for the current day/process.
    """
    if not route_stops:
        return {
            "current_stop_index": None,
            "next_stop_index": None,
            "last_passed_index": -1,
            "ignored_zone_pairs": current_zone_pairs,
            "hit_indices": [],
            "finished": False,
        }

    last_passed = int(ROUTE_PROGRESS.get("last_passed_index", -1))
    hit_indices = get_hit_stop_indices(route_stops, current_zone_pairs)

    current_stop_index = None
    ignored_zone_pairs = list(current_zone_pairs)

    if last_passed < 0:
        # Route has not started yet. Only first stop can start it.
        if 0 in hit_indices:
            current_stop_index = 0
            last_passed = 0
            ROUTE_PROGRESS["last_passed_index"] = 0
            ROUTE_PROGRESS["current_index"] = 0
            ignored_zone_pairs = [
                pair for pair in current_zone_pairs
                if pair != get_stop_zone_pair(route_stops[0])
            ]
        else:
            ROUTE_PROGRESS["current_index"] = None

    else:
        # Route already started. Allow jump to any later stop.
        forward_hits = [idx for idx in hit_indices if idx > last_passed]

        if forward_hits:
            # Use the farthest later stop if several zones are active simultaneously.
            # This prevents getting stuck in overlapping geozones.
            current_stop_index = max(forward_hits)
            last_passed = current_stop_index
            ROUTE_PROGRESS["last_passed_index"] = last_passed
            ROUTE_PROGRESS["current_index"] = current_stop_index

            accepted_pair = get_stop_zone_pair(route_stops[current_stop_index])
            ignored_zone_pairs = [
                pair for pair in current_zone_pairs
                if pair != accepted_pair
            ]

        elif last_passed in hit_indices:
            # Still in the latest accepted stop.
            current_stop_index = last_passed
            ROUTE_PROGRESS["current_index"] = current_stop_index

            accepted_pair = get_stop_zone_pair(route_stops[current_stop_index])
            ignored_zone_pairs = [
                pair for pair in current_zone_pairs
                if pair != accepted_pair
            ]

        else:
            # Between stops.
            ROUTE_PROGRESS["current_index"] = None

    next_stop_index = last_passed + 1
    if next_stop_index >= len(route_stops):
        next_stop_index = None

    return {
        "current_stop_index": current_stop_index,
        "next_stop_index": next_stop_index,
        "last_passed_index": last_passed,
        "ignored_zone_pairs": ignored_zone_pairs,
        "hit_indices": hit_indices,
        "finished": next_stop_index is None and current_stop_index is None,
    }


def calculate_status_by_progress(
    route_config: Dict[str, Any],
    route_stops: List[Dict[str, Any]],
    progress: Dict[str, Any],
) -> Dict[str, Any]:
    warning = int(route_config.get("delay_warning_minutes", 3))
    critical = int(route_config.get("delay_critical_minutes", 7))

    current_stop_index = progress.get("current_stop_index")
    next_stop_index = progress.get("next_stop_index")
    finished = progress.get("finished", False)

    if not route_stops:
        return {
            "status": "no_route",
            "status_text": "Нет остановок в конфиге",
            "delay_minutes": None,
            "current_stop": None,
            "next_stop": None,
        }

    if finished:
        return {
            "status": "finished",
            "status_text": "Маршрут завершён",
            "delay_minutes": None,
            "current_stop": None,
            "next_stop": None,
        }

    if current_stop_index is not None:
        current_stop = route_stops[current_stop_index]
        planned_dt = parse_hhmm(current_stop["planned_time"])
        delay_minutes = int((datetime.now() - planned_dt).total_seconds() // 60)

        if delay_minutes <= warning:
            status = "ok"
            status_text = "По расписанию"
        elif delay_minutes <= critical:
            status = "warning"
            status_text = f"Небольшое опоздание: +{delay_minutes} мин"
        else:
            status = "critical"
            status_text = f"Опаздывает: +{delay_minutes} мин"

        next_stop = None
        if current_stop_index + 1 < len(route_stops):
            next_stop = route_stops[current_stop_index + 1]

        return {
            "status": status,
            "status_text": status_text,
            "delay_minutes": delay_minutes,
            "current_stop": current_stop,
            "next_stop": next_stop,
        }

    next_stop = route_stops[next_stop_index] if next_stop_index is not None else None

    return {
        "status": "moving",
        "status_text": "Автобус в пути",
        "delay_minutes": None,
        "current_stop": None,
        "next_stop": next_stop,
    }


def render_stop_states_by_progress(
    route_stops: List[Dict[str, Any]],
    progress: Optional[Dict[str, Any]],
    route_active: bool = True,
) -> List[Dict[str, Any]]:
    rendered = []

    if not route_active or progress is None:
        return [{**stop, "state": "pending"} for stop in route_stops]

    last_passed_index = int(progress.get("last_passed_index", -1))
    current_stop_index = progress.get("current_stop_index")

    for idx, stop in enumerate(route_stops):
        state = "pending"

        if current_stop_index is not None and idx == current_stop_index:
            state = "current"
        elif idx <= last_passed_index:
            state = "passed"

        rendered.append({**stop, "state": state})

    return rendered


app = FastAPI(title="Bus Live Wialon MVP")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
client = WialonClient(WIALON_HOST, WIALON_TOKEN)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "wialon_host": WIALON_HOST,
        "route_config": ROUTE_CONFIG_PATH,
        "route_progress": ROUTE_PROGRESS,
    }


@app.post("/api/reset-progress")
def reset_progress():
    ROUTE_PROGRESS["date"] = date.today().isoformat()
    ROUTE_PROGRESS["last_passed_index"] = -1
    ROUTE_PROGRESS["current_index"] = None

    return {
        "ok": True,
        "message": "Route progress reset",
        "route_progress": ROUTE_PROGRESS,
    }


@app.get("/api/bus-status")
def bus_status():
    if not WIALON_TOKEN:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": "WIALON_TOKEN не задан в .env"},
        )

    try:
        route_config = load_route_config()
        route_stops = route_config.get("stops", [])
        route_active = is_route_active(route_config)
        reset_progress_if_needed(route_active)

        bus = find_tracked_bus(client, route_config)

        if not bus:
            return {
                "ok": False,
                "error": "Автобус не найден. Укажи TRACKED_BUS_ID в .env или tracked_bus.unit_id в route_config.json.",
                "route_name": route_config.get("route_name"),
                "stops": render_stop_states_by_progress(route_stops, None, route_active),
            }

        if bus.get("error") == "multiple_bus_matches":
            return {
                "ok": False,
                "error": "По названию найдено несколько автобусов. Укажи TRACKED_BUS_ID.",
                "matches": bus.get("matches"),
            }

        unit_id = int(bus["id"])
        unit_name = bus.get("nm")
        position = bus.get("pos") or {}

        if not route_active:
            return {
                "ok": True,
                "route_name": route_config.get("route_name"),
                "direction": route_config.get("direction"),
                "refresh_seconds": route_config.get("refresh_seconds", 10),
                "eta": None,
                "bus": {
                    "unit_id": unit_id,
                    "name": unit_name,
                    "last_position": {
                        "lat": position.get("y"),
                        "lon": position.get("x"),
                        "speed": position.get("s"),
                        "course": position.get("c"),
                        "time": position.get("t"),
                    },
                },
                "status": "inactive",
                "status_text": "Маршрут сейчас не активен",
                "delay_minutes": None,
                "current_stop": None,
                "next_stop": None,
                "stops": render_stop_states_by_progress(route_stops, None, False),
                "debug": {
                    "active_from": route_config.get("active_from"),
                    "active_to": route_config.get("active_to"),
                    "route_progress": ROUTE_PROGRESS,
                },
                "updated_at": datetime.now().strftime("%H:%M:%S"),
            }

        zone_id_map = build_zone_id_map(route_stops)
        zones_by_unit_raw = {}

        if zone_id_map:
            zones_by_unit_raw = client.get_zones_by_unit(unit_id, zone_id_map)

        current_zone_pairs = get_current_zone_ids(zones_by_unit_raw, unit_id)
        progress = update_ordered_progress(route_stops, current_zone_pairs)

        status_info = calculate_status_by_progress(route_config, route_stops, progress)
        eta_info = build_eta_info(
            route_config=route_config,
            bus_position=position,
            next_stop=status_info.get("next_stop"),
        )
        rendered_stops = render_stop_states_by_progress(route_stops, progress, True)

        return {
            "ok": True,
            "route_name": route_config.get("route_name"),
            "direction": route_config.get("direction"),
            "refresh_seconds": route_config.get("refresh_seconds", 10),
            "eta": eta_info,
            "bus": {
                "unit_id": unit_id,
                "name": unit_name,
                "last_position": {
                    "lat": position.get("y"),
                    "lon": position.get("x"),
                    "speed": position.get("s"),
                    "course": position.get("c"),
                    "time": position.get("t"),
                },
            },
            "status": status_info.get("status"),
            "status_text": status_info.get("status_text"),
            "delay_minutes": status_info.get("delay_minutes"),
            "current_stop": status_info.get("current_stop"),
            "next_stop": status_info.get("next_stop"),
            "stops": rendered_stops,
            "debug": {
                "zone_id_map": zone_id_map,
                "zones_by_unit_raw": zones_by_unit_raw,
                "current_zone_pairs": current_zone_pairs,
                "route_progress": progress,
                "memory_progress": ROUTE_PROGRESS,
            },
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }

    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(exc),
            },
        )


@app.get("/api/config")
def config():
    try:
        route_config = load_route_config()
        return {"ok": True, "config": route_config}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
