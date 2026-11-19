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
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
TZ = ZoneInfo("Europe/Istanbul")


def require(value, reason):
    if not value:
        raise AssertionError(reason)


with tempfile.TemporaryDirectory(prefix="bus-booking-rules-") as temp:
    dbfile = Path(temp) / "rules.sqlite"
    port = unused_port()
    origin = f"http://127.0.0.1:{port}"
    env = dict(os.environ, BUS_DB=str(dbfile), BUS_ADMIN_PASSWORD="test-only-rules",
               BUS_FERNET_KEY=Fernet.generate_key().decode(), BUS_PUBLIC_URL=origin)
    auth = requests.auth.HTTPBasicAuth("admin", "test-only-rules")
    server = start_server(
        [PYTHON, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    def get(path, **kw):
        return requests.get(origin + path, timeout=5, **kw)

    def post(path, payload, **kw):
        return requests.post(origin + path, json=payload, timeout=5, **kw)

    def code(response):
        data = response.json()
        return data.get("detail", {}).get("code") if isinstance(data.get("detail"), dict) else None

    def route(db, label, direction, stamp):
        origin_stop, destination_stop = (
            ("Камп", "Магазин") if direction == "forward" else ("Магазин", "Камп")
        )
        return db.execute(
            """INSERT INTO routes(name,origin,destination,forward_time,reverse_time,capacity,active,direction,weekday_mask)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (label, origin_stop, destination_stop, stamp.strftime("%H:%M:%S"),
             stamp.strftime("%H:%M:%S"), 41, 1, direction, 127),
        ).lastrowid

    def make_roundtrip(card, forward, reverse, first_time, second_time):
        data = {
            "card": card, "date": first_time.date().isoformat(),
            "forward": forward, "reverse": reverse,
            "reverse_date": second_time.date().isoformat(), "direction": "forward",
            "lang": "ru",
        }
        resp = post("/bus/api/book", data)
        require(resp.status_code == 200, f"Cannot book roundtrip for {card}: {resp.text}")
        return resp.json()["url"]

    def leg_rows(card):
        with sqlite3.connect(dbfile) as db:
            return db.execute(
                "SELECT l.id,l.direction,l.date,l.departure FROM legs l JOIN bookings b ON b.id=l.booking_id WHERE b.card=? ORDER BY l.id",
                (card,),
            ).fetchall()

    def mark_past(leg_id):
        past = datetime.now(TZ) - timedelta(hours=1)
        with sqlite3.connect(dbfile) as db:
            db.execute("UPDATE legs SET date=?,departure=? WHERE id=?",
                       (past.date().isoformat(), past.strftime("%H:%M:%S"), leg_id))
            db.commit()

    try:
        for attempt in range(100):
            try:
                if get("/bus/").status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("Staging app did not start")

        clock = datetime.now(TZ)
        fw_time = clock + timedelta(minutes=65)
        rev_time = clock + timedelta(minutes=105)
        next_time = clock + timedelta(minutes=145)
        scanner_time = clock + timedelta(minutes=10)
        overlap_forward_time = clock + timedelta(minutes=5)
        overlap_reverse_time = clock + timedelta(minutes=13)
        with sqlite3.connect(dbfile) as db:
            forward = route(db, "Forward leg", "forward", fw_time)
            reverse = route(db, "Reverse leg", "reverse", rev_time)
            next_forward = route(db, "Next forward", "forward", next_time)
            near = route(db, "Boarding now", "forward", scanner_time)
            overlap_forward = route(db, "Overlapping outbound", "forward", overlap_forward_time)
            overlap_reverse = route(db, "Overlapping return", "reverse", overlap_reverse_time)
            for card in ("000101", "000102", "000103", "000104", "000105"):
                db.execute("INSERT INTO allowed_cards(card) VALUES(?)", (card,))
            db.commit()

        tickets = {}
        for card in ("000101", "000102", "000103"):
            tickets[card] = make_roundtrip(card, forward, reverse, fw_time, rev_time)

        # A booked future leg prevents another booking in the same direction.
        denied = post("/bus/api/book", {
            "card": "000101", "date": next_time.date().isoformat(),
            "forward": next_forward, "direction": "forward",
        })
        require(denied.status_code == 409 and code(denied) == "ACTIVE_BOOKING_EXISTS",
                "A second future same-direction reservation must be refused")

        # Outbound traveled, return cancelled: release the pass for another booking.
        forward_id, reverse_id = [row[0] for row in leg_rows("000101")]
        mark_past(forward_id)
        cancelled_return = post("/bus/api/admin/bookings/INVALID/cancel", {"confirm": True}, auth=auth)
        require(cancelled_return.status_code == 404, "Unknown booking must not be cancellable")
        with sqlite3.connect(dbfile) as db:
            booking_code = db.execute("SELECT code FROM bookings WHERE card='000101'").fetchone()[0]
        response = post(tickets["000101"].replace('/bus/ticket/','/bus/api/ticket/') + "/cancel",
                        {"confirm": True, "leg_id": reverse_id})
        require(response.status_code == 200 and response.json()["cancelled_legs"] == [reverse_id],
                "Return leg cancellation failed")
        again = post("/bus/api/book", {"card": "000101", "date": next_time.date().isoformat(),
                                       "forward": next_forward, "direction": "forward"})
        require(again.status_code == 200, "Cannot rebook after outbound traveled and return cancelled")

        # Mirrored case: outbound cancelled, return traveled, rebook allowed.
        outward, backward = [row[0] for row in leg_rows("000102")]
        # A fresh browser gets permission from pass lookup for those bookings only.
        owner = requests.Session()
        lookup = owner.post(origin + "/bus/api/find-booking", json={"card": "000102"}, timeout=5)
        lookup.raise_for_status()
        cookie = lookup.headers["Set-Cookie"]
        require(all(part in cookie for part in ("HttpOnly", "SameSite=strict", "Path=/bus/check/", "Max-Age=1800")),
                "Lookup permission must be short-lived and scoped to the confirmation routes")
        confirmation = lookup.json()["items"][0]["url"]
        require(tickets["000102"].split("/")[-1] not in lookup.text, "Lookup exposed the private ticket")
        unauthenticated = get(confirmation)
        require('id="cancelOpen" hidden' in unauthenticated.text, "A QR link alone must be read-only")
        page = owner.get(origin + confirmation, timeout=5)
        require(page.status_code == 200 and 'id="cancelOpen" onclick=' in page.text
                and 'id="cancelLink"' not in page.text and tickets["000102"].split("/")[-1] not in page.text,
                "A fresh browser that looked up this pass must see the simple cancellation control")
        cancel_url = confirmation + "/cancel"
        cancel_leg = {"confirm": True, "leg_id": outward}
        require(post(cancel_url, cancel_leg).status_code == 403, "A bare QR must not authorize cancellation")
        another_pass = requests.Session()
        another_pass.post(origin + "/bus/api/find-booking", json={"card": "000103"}, timeout=5).raise_for_status()
        require(another_pass.post(origin + cancel_url, json=cancel_leg, timeout=5).status_code == 403,
                "Lookup permission was accepted for a different booking")
        require(owner.post(origin + cancel_url, json=cancel_leg, headers={"Origin":"https://different.example"},
                           timeout=5).status_code == 403, "A cross-origin page could cancel")
        require(owner.post(origin + cancel_url, json={"confirm": False}, timeout=5).status_code == 400,
                "Server accepted a cancellation without confirmation")
        response = owner.post(origin + cancel_url, json=cancel_leg, timeout=5)
        require(response.status_code == 200 and response.json()["cancelled_legs"] == [outward],
                "Outbound cancellation from lookup failed")
        page = owner.get(origin + confirmation, timeout=5)
        require('id="cancelOpen" onclick=' in page.text, "The remaining leg must still be cancellable")
        owner.close()
        another_pass.close()
        still_pending = post("/bus/api/book", {"card": "000102", "date": next_time.date().isoformat(),
                                                   "forward": next_forward, "direction": "forward"})
        require(still_pending.status_code == 409 and code(still_pending) == "ACTIVE_BOOKING_EXISTS",
                "A remaining future return leg must keep the pass blocked")
        mark_past(backward)
        again = post("/bus/api/book", {"card": "000102", "date": next_time.date().isoformat(),
                                       "forward": next_forward, "direction": "forward"})
        require(again.status_code == 200, "Cannot rebook after return traveled and outbound cancelled")

        # Both trips completed: rebooking is permitted, history is preserved.
        both = [row[0] for row in leg_rows("000103")]
        for leg_id in both:
            mark_past(leg_id)
        again = post("/bus/api/book", {"card": "000103", "date": next_time.date().isoformat(),
                                       "forward": next_forward, "direction": "forward"})
        require(again.status_code == 200, "Cannot rebook after both legs have been taken")

        # Boarding may only be recorded with a driver session, once per leg.
        ticket = post("/bus/api/book", {"card": "000104", "date": scanner_time.date().isoformat(),
                                        "forward": near, "direction": "forward"}).json()["url"]
        found = post("/bus/api/find-booking", {"card": "000104"}).json()["items"]
        signed_url = origin + found[0]["url"]
        preview = post("/bus/api/scanner/verify", {"token": signed_url, "record": False})
        require(preview.status_code == 200 and preview.json()["allowed"],
                "Driver preview must be allowed during boarding window")
        with sqlite3.connect(dbfile) as db:
            require(db.execute("SELECT COUNT(*) FROM boardings").fetchone()[0] == 0,
                    "Read-only preview recorded a boarding")
        unauthorized = post("/bus/api/scanner/verify", {"token": signed_url})
        require(unauthorized.status_code == 401 and code(unauthorized) == "DRIVER_LOGIN_REQUIRED",
                "Public QR must not be able to record boarding")
        created = post('/bus/api/admin/drivers', {'login': 'rules-driver', 'display_name': 'Rules bus', 'password': 'test-only-driver-password'}, auth=auth)
        require(created.status_code == 201, created.text)
        driver = requests.Session()
        login = driver.post(origin+'/bus/api/scanner/login', json={'login': 'rules-driver', 'password': 'test-only-driver-password'}, timeout=5)
        require(login.status_code == 200, login.text)
        recorded = post("/bus/api/scanner/verify", {"token": signed_url, "source": "manual"},
                        cookies=driver.cookies)
        require(recorded.json()["allowed"], f"Boarding not recorded: {recorded.text}")
        duplicate = post("/bus/api/scanner/verify", {"token": signed_url},
                         cookies=driver.cookies)
        require(not duplicate.json()["allowed"] and duplicate.json()["reason"] == "already_boarded",
                "Repeat boarding must be clearly flagged")
        with sqlite3.connect(dbfile) as db:
            require(db.execute("SELECT COUNT(*) FROM boardings WHERE card='000104'").fetchone()[0] == 1,
                    "Duplicate boarding row recorded")
        leg_id = leg_rows("000104")[0][0]
        refused = post(ticket.replace('/bus/ticket/','/bus/api/ticket/') + "/cancel", {"confirm": True, "leg_id": leg_id})
        require(refused.status_code == 409 and code(refused) == "LEG_NOT_CANCELLABLE",
                "Recorded boarding must not be cancellable")

        # Overlapping boarding windows must require an explicit leg choice.
        make_roundtrip('000105', overlap_forward, overlap_reverse, overlap_forward_time, overlap_reverse_time)
        overlap_url = origin + post('/bus/api/find-booking', {'card': '000105'}).json()['items'][0]['url']
        choice = post('/bus/api/scanner/verify', {'token': overlap_url, 'record': False}).json()
        require(choice['reason'] == 'choose_trip' and len(choice['choices']) == 2,
                'Overlapping windows require a trip selector')
        selected_leg = choice['choices'][1]['id']
        chosen_boarding = post('/bus/api/scanner/verify', {'token': overlap_url, 'leg_id': selected_leg},
                               cookies=driver.cookies).json()
        require(chosen_boarding['allowed'] and chosen_boarding['legs'][0]['id'] == selected_leg,
                'Manual leg selection did not record the selected trip')
        with sqlite3.connect(dbfile) as db:
            rows = db.execute("SELECT leg_id FROM boardings WHERE card='000105'").fetchall()
            require(rows == [(selected_leg,)], 'Overlap must record exactly one selected leg')

        # Split cancellations remain in booking history and admin view.
        history = get("/bus/api/admin/history", auth=auth).json()
        require(any(x["card"] == "000101" and x["cancelled"] == 1 for x in history),
                "Partly cancelled return not shown as cancelled in history")
        require(any(x["card"] == "000104" and x["boarded_at"] for x in history),
                "Boarding timestamp missing from history")
        ticket_html = get(ticket).text
        require("cancelLeg(" in ticket_html, "Ticket page must expose per-leg cancellation")
        require('check-cancel-one' in get(urlsplit(signed_url).path).text,
                "Public confirmation page must support per-leg cancellation")
        require('cancelAdminLeg' in get('/bus/admin', auth=auth).text,
                "Dispatcher dashboard must support per-leg cancellation")
        translated = get('/bus/passenger-i18n.js').text
        require('CARD_NOT_ALLOWED' in translated and 'NO_CANCELLABLE_LEGS' in translated,
                "Short error explanations missing from passenger translations")
        scanner_html = get("/bus/scanner/").text
        require("record:false" in scanner_html and "credentials:'same-origin'" in scanner_html and "X-Scanner-Code" not in scanner_html,
                "Scanner UI must distinguish preview from authorized recording")
        print("PASS: both mixed-direction rebooking scenarios, finished roundtrip, one pending booking, per-leg cancellation, secure/idempotent driver boarding, recorded history")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
