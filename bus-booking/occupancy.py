"""Read-only operational seat counts for active booking dates."""
from datetime import date, datetime, timedelta


def occupancy_report(connection, current, hours, requested_date=""):
    if requested_date:
        try:
            if date.fromisoformat(requested_date).isoformat() != requested_date:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Некорректная дата / Geçersiz tarih") from None
    cutoff = current + timedelta(hours=hours)
    routes = connection.execute("""
        SELECT r.*, p.starts_on, p.ends_on FROM routes r
        LEFT JOIN route_service_periods p ON p.route_id=r.id
        WHERE r.active=1 AND r.deleted_at IS NULL
    """).fetchall()
    counts = {
        (row["route_id"], row["direction"], row["date"]): row["booked"]
        for row in connection.execute("""
            SELECT l.route_id, l.direction, l.date, COUNT(*) booked
            FROM legs l JOIN bookings b ON b.id=l.booking_id
            WHERE b.cancelled_at IS NULL AND l.date BETWEEN ? AND ?
            GROUP BY l.route_id, l.direction, l.date
        """, (current.date().isoformat(), cutoff.date().isoformat()))
    }
    by_day = {}
    day = current.date()
    while day <= cutoff.date():
        value = day.isoformat()
        items = []
        for route in routes:
            if route["service_date"] and route["service_date"] != value:
                continue
            if not route["weekday_mask"] & (1 << day.weekday()):
                continue
            if route["starts_on"] and not route["starts_on"] <= value <= route["ends_on"]:
                continue
            directions = ("forward", "reverse") if route["direction"] == "both" else (route["direction"],)
            for direction in directions:
                departure = route["forward_time"] if direction == "forward" else route["reverse_time"]
                stamp = datetime.fromisoformat(value + "T" + departure).replace(tzinfo=current.tzinfo)
                if stamp > cutoff:
                    continue
                booked = counts.get((route["id"], direction, value), 0)
                reverse = direction == "reverse" and route["direction"] == "both"
                items.append({
                    "route_id": route["id"], "direction": direction, "name": route["name"],
                    "origin": route["destination"] if reverse else route["origin"],
                    "destination": route["origin"] if reverse else route["destination"],
                    "time": departure, "departure_at": stamp.isoformat(), "past": stamp <= current,
                    "capacity": route["capacity"], "booked": booked,
                    "free": max(0, route["capacity"] - booked),
                })
        if items:
            by_day[value] = sorted(items, key=lambda item: (item["direction"], item["time"], item["route_id"]))
        day += timedelta(days=1)
    dates = list(by_day)
    selected = requested_date if requested_date in by_day else (dates[0] if dates else None)
    items = by_day.get(selected, [])
    return {
        "dates": dates, "date": selected, "now": current.isoformat(),
        "cutoff": cutoff.isoformat(), "horizon_hours": hours, "trips": items,
        "totals": {
            "trips": len(items),
            **{key: sum(item[key] for item in items) for key in ("booked", "free", "capacity")},
        },
    }
