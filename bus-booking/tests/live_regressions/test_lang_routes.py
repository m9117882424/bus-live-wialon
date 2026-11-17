import os
import sys
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import time
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isolated_server import start_server, unused_port
from zoneinfo import ZoneInfo
from datetime import datetime

import requests
from cryptography.fernet import Fernet

stage = Path(__file__).resolve().parents[2]
python = sys.executable
with tempfile.TemporaryDirectory(prefix="bus-langs-") as temporary:
    database = Path(temporary) / "test.sqlite"
    port = unused_port()
    origin = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({
        "BUS_DB": str(database),
        "BUS_ADMIN_PASSWORD": "test-only",
        "BUS_FERNET_KEY": Fernet.generate_key().decode(),
        "BUS_PUBLIC_URL": origin,
        "PYTHONPATH": str(stage),
    })
    proc = start_server(
        [python, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        cwd=str(stage), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                if requests.get(origin + "/bus/", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError("Staged app did not start")

        home = requests.get(origin + "/bus/", timeout=5)
        assert home.status_code == 200
        assert 'id="languageSelect"' in home.text
        assert home.text.index('value="tr"') < home.text.index('value="ru"')
        assert '<script src="/bus/passenger-i18n.js?v=20261117-zh"></script>' in home.text
        js = requests.get(origin + "/bus/passenger-i18n.js", timeout=5)
        assert js.status_code == 200
        assert "BusPassengerL10n" in js.text
        assert "Кыргызча" in js.text

        tomorrow = (datetime.now(ZoneInfo("Europe/Istanbul")) + timedelta(days=1)).date().isoformat()
        locales = ["ky", "uz", "tg", "kk", "tk", "en", "zh"]
        with sqlite3.connect(database) as connection:
            route_id = connection.execute(
                "INSERT INTO routes(name,origin,destination,forward_time,reverse_time,capacity,active) VALUES(?,?,?,?,?,?,?)",
                ("Test", "İSPANYOL KAMP", "ŞOK MARKET", "12:00:00", "18:00:00", 41, 1),
            ).lastrowid
            for index, code in enumerate(locales):
                connection.execute("INSERT INTO allowed_cards(card) VALUES(?)", (f"00120{index}",))
            connection.commit()

        for index, code in enumerate(locales):
            card = f"00120{index}"
            booking = requests.post(origin + "/bus/api/book", json={
                "card": card, "date": tomorrow, "forward": route_id,
                "lang": code, "direction": "forward",
            }, timeout=8)
            assert booking.status_code == 200, (code, booking.status_code, booking.text[:300])
            ticket = requests.get(origin + booking.json()["url"], timeout=5)
            assert ticket.status_code == 200
            assert f'"lang": "{code}"' in ticket.text, code
            assert "setTicketLang" in ticket.text
            if code in ("ky", "kk", "tk", "zh"):
                pdf = requests.get(origin + booking.json()["url"] + "/download/pdf?lang=" + code, timeout=8)
                jpg = requests.get(origin + booking.json()["url"] + "/download/jpg?lang=" + code, timeout=8)
                assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), (pdf.status_code, pdf.text[:180])
                assert jpg.status_code == 200 and jpg.content.startswith(b"\xff\xd8"), (jpg.status_code, jpg.text[:180])
            lookup = requests.post(origin + "/bus/api/find-booking", json={"card": card}, timeout=5)
            assert lookup.status_code == 200 and len(lookup.json()["items"]) == 1
            check = requests.get(origin + lookup.json()["items"][0]["url"], timeout=5)
            assert check.status_code == 200
            assert 'id="checkCardLabel"' in check.text
            assert 'id="languageSelect"' in check.text
            assert 'passenger-i18n.js' in check.text
            assert f'checkBookingLang="{code}"' in check.text

        trips = requests.get(origin + "/bus/api/trips", params={
            "date": tomorrow, "direction": "forward"
        }, timeout=5).json()
        assert len(trips) == 1
        assert trips[0]["origin"] == "İSPANYOL KAMP"
        assert trips[0]["destination"] == "ŞOK MARKET"
        assert requests.get(origin + "/bus/scanner/", timeout=5).status_code == 200
        print("PASS: staged HTTP home/ticket/check, all seven persisted locales, flag picker, unchanged stops, scanner")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
