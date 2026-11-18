"""Match complete GPS journeys once; absence requires coverage of the whole fleet."""
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
DEPARTURE_WINDOW = 45 * 60
MAX_TRAVEL = 90 * 60
MAX_GPS_GAP = 180
MAX_IDLE_GAP = 600
MIN_ZONE_SECONDS = 20
GPS_VERSION = 3

EXECUTION_LABELS = {
    "performed": "Состоялся", "not_performed": "Не состоялся",
    "planned": "Предстоящий", "in_progress": "В пути",
    "checking": "Проверяется", "unknown": "Недостаточно GPS", "ambiguous": "Неоднозначно",
}
EXECUTION_REASONS = {
    "complete_route": "Подтверждены выезд и прибытие того же автобуса на КПП АККУЮ",
    "no_departure": "Ни один автобус не выехал в окне рейса; GPS-покрытие полное",
    "no_complete_route": "Ни один автобус не завершил маршрут в окне рейса; GPS-покрытие полное",
    "not_due": "Рейс ещё не начался", "awaiting_arrival": "Есть выезд; ожидается подтверждение прибытия",
    "window_open": "Окно GPS-проверки ещё не завершилось",
    "gps_loading": "История GPS ещё загружается", "gps_error": "Часть истории GPS недоступна",
    "gps_gaps": "Недостаточное GPS-покрытие хотя бы одного автобуса",
    "no_fleet": "Нет автобусов для проверки", "ambiguous": "Проезд подходит нескольким рейсам",
}


def covered(segments, start, end):
    return any(a <= start and b >= end for a, b in segments)


def attach_execution(trips, events, readiness, telemetry=None, fleet=None, current=None):
    current = current or datetime.now(TZ)
    now = current.timestamp()
    telemetry = telemetry or {}
    fleet = sorted(set(fleet or []))
    unique = {}
    for event in events:
        key = (event["plate"], event["direction"], event["stamp"])
        if key not in unique or event.get("route_complete"):
            unique[key] = event
    candidates = defaultdict(list)
    full_candidates = defaultdict(list)
    assigned = defaultdict(list)
    for event in unique.values():
        if event["stamp"] > now:
            continue
        options = []
        for trip in trips:
            planned = datetime.fromisoformat(trip["planned_at"]).timestamp()
            if trip["direction"] != event["direction"] or abs(event["stamp"] - planned) > DEPARTURE_WINDOW:
                continue
            candidates[trip["key"]].append(event)
            confirmed = event.get("arrival_confirmed_at")
            arrived = event.get("arrived_at")
            if not (event.get("route_complete") and arrived is not None and confirmed is not None
                    and event["stamp"] < arrived <= event["stamp"] + MAX_TRAVEL and arrived+MIN_ZONE_SECONDS <= confirmed <= now):
                continue
            full_candidates[trip["key"]].append(event)
            hints = trip.get("_hints", [])
            options.append((0 if event["plate"] in hints else 1, abs(event["stamp"] - planned), trip))
        if options:
            best = min((priority, distance) for priority, distance, _ in options)
            nearest = [trip for priority, distance, trip in options if (priority, distance) == best]
            if len(nearest) == 1:
                assigned[nearest[0]["key"]].append(event)
    for trip in trips:
        planned = datetime.fromisoformat(trip["planned_at"]).timestamp()
        deadline = planned + DEPARTURE_WINDOW + MAX_TRAVEL + MAX_IDLE_GAP + MIN_ZONE_SECONDS
        trip.update(execution_status="unknown", execution_reason="gps_gaps",
                    arrival_at=None, duration_minutes=None, execution_vehicle="",
                    execution_deadline=datetime.fromtimestamp(deadline, TZ).isoformat())
        matches = assigned[trip["key"]]
        if matches:
            hints = trip.get("_hints", [])
            event = min(matches, key=lambda item: (
                0 if item["plate"] in hints else 1, abs(item["stamp"] - planned), item["stamp"], item["plate"]))
            delta = round((event["stamp"] - planned) / 60, 1)
            trip.update(execution_status="performed", execution_reason="complete_route",
                        arrival_at=datetime.fromtimestamp(event["arrived_at"], TZ).isoformat(),
                        duration_minutes=round((event["arrived_at"] - event["stamp"]) / 60, 1),
                        execution_vehicle=event["plate"], vehicle=event["plate"],
                        actual_at=datetime.fromtimestamp(event["stamp"], TZ).isoformat(),
                        delta_minutes=delta, status="late" if delta > 0 else "early" if delta < 0 else "on_time",
                        reason="wialon_zone_exit")
            continue
        if full_candidates[trip["key"]]:
            trip.update(execution_status="ambiguous", execution_reason="ambiguous")
            continue
        if planned > now and not candidates[trip["key"]]:
            trip.update(execution_status="planned", execution_reason="not_due")
            continue
        departures = candidates[trip["key"]]
        if not departures:
            deadline = planned + DEPARTURE_WINDOW + MAX_GPS_GAP + MIN_ZONE_SECONDS
            trip["execution_deadline"] = datetime.fromtimestamp(deadline, TZ).isoformat()
        if now < deadline:
            trip.update(execution_status="in_progress" if departures else "checking",
                        execution_reason="awaiting_arrival" if departures else "window_open")
            continue
        if not fleet:
            trip["execution_reason"] = "no_fleet"
            continue
        start = planned - DEPARTURE_WINDOW - MAX_GPS_GAP
        states = []
        complete = True
        for bus in fleet:
            rows = [value for (day, plate), value in telemetry.items() if plate == bus]
            ready = [row for row in rows if row.get("status") == "ready" and row.get("version") == GPS_VERSION]
            states.extend(row.get("status") for row in rows)
            segments = sorted(segment for row in ready for segment in row.get("coverage", []))
            merged = []
            for a, b in segments:
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            if not covered(merged, start, deadline):
                complete = False
        if complete:
            trip.update(execution_status="not_performed",
                        execution_reason="no_complete_route" if departures else "no_departure")
        elif "error" in states:
            trip["execution_reason"] = "gps_error"
        elif readiness.get(trip["date"], "loading") == "loading" or not states:
            trip["execution_reason"] = "gps_loading"
