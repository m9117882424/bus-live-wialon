"""Isolated HTTP test for the rolling configurable bus-booking horizon."""
import os
import sys
import socket
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isolated_server import start_server, unused_port
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
TZ = ZoneInfo("Europe/Istanbul")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_for_server(origin):
    for _ in range(100):
        try:
            response = requests.get(origin + "/bus/", timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.1)
    raise AssertionError("Staging server did not become available")


with tempfile.TemporaryDirectory(prefix="bus-booking-horizon-") as temp_dir:
    database = Path(temp_dir) / "isolated.sqlite"
    port = unused_port()
    origin = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        BUS_DB=str(database),
        BUS_ADMIN_PASSWORD="isolated-test-only",
        BUS_FERNET_KEY=Fernet.generate_key().decode(),
        BUS_PUBLIC_URL=origin,
    )
    auth = requests.auth.HTTPBasicAuth("admin", "isolated-test-only")
    process = None

    def launch():
        return start_server(
            [PYTHON, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def get(path, **kwargs):
        return requests.get(origin + path, timeout=5, **kwargs)

    def post(path, payload, **kwargs):
        return requests.post(origin + path, json=payload, timeout=5, **kwargs)

    def put(path, payload, **kwargs):
        return requests.put(origin + path, json=payload, timeout=5, **kwargs)

    try:
        process = launch()
        wait_for_server(origin)

        home = get("/bus/")
        check('id="horizonHint"' in home.text, "Passenger horizon hint missing")
        check("refreshBookingWindow()" in home.text, "Passenger horizon refresh missing")
        check("OUTSIDE_BOOKING_WINDOW" in home.text, "Passenger error translation handling missing")
        check("🇹🇷 Türkçe" in home.text and "🇷🇺 Русский" in home.text, "Language selector changed")

        admin_unauthorized = get("/bus/api/admin/settings")
        check(admin_unauthorized.status_code == 401, "Settings must require admin auth")
        admin_page = get("/bus/admin", auth=auth)
        check(admin_page.status_code == 200, "Admin page not available")
        check('id="settingsTab"' in admin_page.text, "Settings tab missing")
        check('id="bookingHorizonHours"' in admin_page.text, "Settings input missing")
        check('id="abForm"' in admin_page.text, "Dispatcher booking form was removed")

        window = get("/bus/api/booking-window").json()
        check(window["hours"] == 48, f"Default must be 48h: {window}")
        observed = (
            datetime.fromisoformat(window["cutoff"]) -
            datetime.fromisoformat(window["now"])
        ).total_seconds()
        check(abs(observed - 48 * 3600) < 0.1, "Cutoff must be rolling 48h")

        inside = datetime.now(TZ) + timedelta(hours=47, minutes=45)
        outside = datetime.now(TZ) + timedelta(hours=48, minutes=15)
        inside_date, outside_date = inside.date().isoformat(), outside.date().isoformat()

        with sqlite3.connect(database) as db:
            forward_id = db.execute(
                """INSERT INTO routes
                   (name,origin,destination,forward_time,reverse_time,capacity,active,direction,weekday_mask)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                ("Inside horizon", "Камп", "Магазин", inside.strftime("%H:%M:%S"),
                 inside.strftime("%H:%M:%S"), 41, 1, "forward", 127),
            ).lastrowid
            reverse_id = db.execute(
                """INSERT INTO routes
                   (name,origin,destination,forward_time,reverse_time,capacity,active,direction,weekday_mask)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                ("Outside horizon", "Магазин", "Камп", outside.strftime("%H:%M:%S"),
                 outside.strftime("%H:%M:%S"), 41, 1, "reverse", 127),
            ).lastrowid
            for card in ("001101", "001102", "001103", "001104", "001105", "001106"):
                db.execute("INSERT INTO allowed_cards(card) VALUES(?)", (card,))
            db.commit()

        forward_trips = get("/bus/api/trips", params={"date": inside_date, "direction": "forward"}).json()
        reverse_trips = get("/bus/api/trips", params={"date": outside_date, "direction": "reverse"}).json()
        check(any(x["id"] == forward_id for x in forward_trips), "47h trip must appear")
        check(not any(x["id"] == reverse_id for x in reverse_trips), "48h+ trip must be hidden")

        out_request = {
            "card": "001101", "date": outside_date, "forward": reverse_id,
            "direction": "reverse", "lang": "en"
        }
        out_response = post("/bus/api/book", out_request)
        check(out_response.status_code == 409, f"Outside passenger booking accepted: {out_response.status_code}")
        check(out_response.json()["detail"]["code"] == "OUTSIDE_BOOKING_WINDOW", "Wrong rejection code")

        roundtrip = {
            "card": "001102", "date": inside_date, "forward": forward_id,
            "reverse": reverse_id, "reverse_date": outside_date,
            "direction": "forward", "lang": "ky"
        }
        failed_return = post("/bus/api/book", roundtrip)
        check(failed_return.status_code == 409, "Out-of-window return leg must reject entire booking")
        with sqlite3.connect(database) as db:
            check(db.execute("SELECT COUNT(*) FROM bookings WHERE card='001102'").fetchone()[0] == 0,
                  "Failed roundtrip must not create partial booking")

        dispatcher_out = post("/bus/api/admin/book", {**out_request, "card": "001103"}, auth=auth)
        check(dispatcher_out.status_code == 409, "Dispatcher booking must obey same limit")

        valid_request = {
            "card": "001104", "date": inside_date, "forward": forward_id,
            "direction": "forward", "lang": "fr"
        }
        valid = post("/bus/api/book", valid_request)
        check(valid.status_code == 200, f"Inside booking must succeed: {valid.status_code}, {valid.text[:200]}")
        ticket_url = valid.json()["url"]
        check(get(ticket_url).status_code == 200, "Existing ticket unavailable")

        update = put("/bus/api/admin/settings", {"hours": 72}, auth=auth)
        check(update.status_code == 200 and update.json()["hours"] == 72, "72h setting not saved")
        check(get("/bus/api/booking-window").json()["hours"] == 72, "Public window not updated")
        reverse_trips = get("/bus/api/trips", params={"date": outside_date, "direction": "reverse"}).json()
        check(any(x["id"] == reverse_id for x in reverse_trips), "Expanded horizon must show 48h+ trip")
        valid_out = post("/bus/api/admin/book", {**out_request, "card": "001105"}, auth=auth)
        check(valid_out.status_code == 200, f"Expanded horizon must allow booking: {valid_out.text[:160]}")

        for wrong in (0, -1, 721):
            bad = put("/bus/api/admin/settings", {"hours": wrong}, auth=auth)
            check(bad.status_code == 422, f"Invalid horizon {wrong} accepted")
        check(get("/bus/api/admin/settings", auth=auth).json()["hours"] == 72,
              "Invalid input altered saved value")

        short = put("/bus/api/admin/settings", {"hours": 1}, auth=auth)
        check(short.status_code == 200, "Short horizon setting failed")
        check(not get("/bus/api/trips", params={"date": inside_date, "direction": "forward"}).json(),
              "Short horizon must hide distant trip")
        found = post("/bus/api/find-booking", {"card": "001104"})
        check(found.status_code == 200 and found.json()["items"], "Existing booking must survive horizon change")
        check(get(ticket_url).status_code == 200, "Existing ticket must survive horizon change")

        # Verify the configured value survives the app's startup migration.
        process.terminate()
        process.wait(timeout=5)
        process = launch()
        wait_for_server(origin)
        check(get("/bus/api/admin/settings", auth=auth).json()["hours"] == 1,
              "Saved horizon was reset after restart")

        final = put("/bus/api/admin/settings", {"hours": 48}, auth=auth)
        check(final.status_code == 200, "Restoring test default failed")
        print("PASS: rolling 48h default, trips/return legs/dispatcher enforcement, 72h settings, invalid values, persistent settings, existing tickets, admin auth")
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
