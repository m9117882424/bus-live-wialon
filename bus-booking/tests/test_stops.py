"""Isolated acceptance test; uses only synthetic passengers and a temporary DB."""
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import zxingcpp
from PIL import Image
from cryptography.fernet import Fernet
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-booking-test-') as tmp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(BUS_DB=str(Path(tmp) / 'test.sqlite'), BUS_ADMIN_PASSWORD='test-only-password',
               BUS_FERNET_KEY=Fernet.generate_key().decode(), BUS_INITIAL_CARDS='123456')
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                             '--port', str(port), '--no-access-log'], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f'http://127.0.0.1:{port}/bus-test'
    auth = ('admin', 'test-only-password')
    try:
        for _ in range(100):
            try:
                if requests.get(base + '/', timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError('Test server did not start')
        assert requests.get(base + '/api/admin/stops', timeout=5).status_code == 401
        stops = requests.get(base + '/api/stops', timeout=5).json()
        assert [(s['name_ru'], s['name_tr']) for s in stops] == [('Камп', 'Kamp'), ('Магазин', 'Mağaza')]
        html = requests.get(base + '/admin', auth=auth, timeout=5).text
        assert 'id="stopsPanel"' in html and 'id="origin" onchange=' in html and 'id="destination" onchange=' in html
        if shutil.which('node'):
            sample = Path(tmp) / 'admin.html'
            sample.write_text(html, encoding='utf-8')
            subprocess.run(['node', str(ROOT / 'tests' / 'test_points.js'), str(sample)], check=True)
        stop = requests.post(base + '/api/admin/stops', auth=auth,
                             json={'name_ru': 'Тестовая остановка', 'name_tr': 'Test durağı', 'active': True}, timeout=5)
        stop.raise_for_status()
        stop_id = stop.json()['id']
        duplicate = requests.post(base + '/api/admin/stops', auth=auth,
                                  json={'name_ru': 'ТЕСТОВАЯ ОСТАНОВКА', 'name_tr': 'Test', 'active': True}, timeout=5)
        assert duplicate.status_code == 409
        day = (datetime.now(ZoneInfo('Europe/Istanbul')).date() + timedelta(days=1)).isoformat()
        for name, origin, dest, direction, departure in [
            ('Туда', 'Камп', 'Тестовая остановка', 'forward', '14:30'),
            ('Обратно', 'Тестовая остановка', 'Камп', 'reverse', '15:00'),
            ('Другой маршрут', 'Магазин', 'Камп', 'reverse', '15:10'),
        ]:
            r = requests.post(base + '/api/admin/routes', auth=auth, timeout=5,
                              json={'name': name, 'origin': origin, 'destination': dest, 'direction': direction,
                                    'forward_time': departure, 'reverse_time': departure, 'capacity': 50, 'service_date': day})
            r.raise_for_status()
        invalid = requests.post(base + '/api/book', timeout=5, json={'card': '123456', 'date': day,
                                'forward': 1, 'reverse': 3, 'reverse_date': day, 'direction': 'forward'})
        assert invalid.status_code == 400
        booking = requests.post(base + '/api/book', timeout=5, json={'card': '123456', 'date': day,
                                'forward': 1, 'reverse': 2, 'reverse_date': day, 'direction': 'forward', 'lang': 'tr'})
        booking.raise_for_status()
        token = booking.json()['url'].rsplit('/', 1)[1]
        r = requests.put(base + f'/api/admin/stops/{stop_id}', auth=auth, timeout=5,
                         json={'name_ru': 'Новая остановка', 'name_tr': 'Yeni durak', 'active': True})
        r.raise_for_status()
        assert requests.get(base + '/api/admin/routes', auth=auth, timeout=5).json()[0]['destination'] == 'Новая остановка'
        ticket = requests.get(base + '/ticket/' + token, timeout=5)
        assert ticket.status_code == 200 and 'Тестовая остановка' in ticket.text and 'Test durağı' in ticket.text
        listing = requests.get(base + '/api/admin/qrcodes', auth=auth, timeout=5).json()['items'][0]
        assert listing['decoded']['card'] == '123456' and len(listing['decoded']['legs']) == 2
        for fmt in ('jpg', 'pdf'):
            response = requests.get(base + '/ticket/' + token + '/download/' + fmt, timeout=10)
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)) if fmt == 'jpg' else PdfReader(io.BytesIO(response.content)).pages[0].images[0].image
            assert image.size == (1080, 1920)
            qr = zxingcpp.read_barcode(image)
            assert qr and json.loads(Fernet(env['BUS_FERNET_KEY']).decrypt(qr.text.encode()))['card'] == '123456'
        r = requests.put(base + f'/api/admin/stops/{stop_id}', auth=auth, timeout=5,
                         json={'name_ru': 'Новая остановка', 'name_tr': 'Yeni durak', 'active': False})
        r.raise_for_status()
        assert all(s['name_ru'] != 'Новая остановка' for s in requests.get(base + '/api/stops', timeout=5).json())
        assert requests.get(base + '/ticket/' + token, timeout=5).status_code == 200
        print('PASS: stops, routes, historical names, round trip, QR and mobile exports')
    finally:
        proc.terminate()
        proc.wait(timeout=10)
