"""Exercise deletion, retained history, active bookings and booking/delete races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
import requests

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-delete-test-') as tmp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    dbpath = str(Path(tmp) / 'test.sqlite')
    env = os.environ.copy()
    env.update(BUS_DB=dbpath, BUS_ADMIN_PASSWORD='test-only-password',
               BUS_FERNET_KEY=Fernet.generate_key().decode(), BUS_INITIAL_CARDS='123456')
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                             '--port', str(port), '--no-access-log'], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f'http://127.0.0.1:{port}/bus'
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
        page = requests.get(base + '/admin', auth=auth, timeout=5).text
        assert 'deleteRoute(' in page and 'routeActionError' in page
        javascript = page.split('<script>', 1)[1].split('</script>', 1)[0]
        if node := shutil.which('node'):
            path = Path(tmp) / 'admin.js'
            path.write_text(javascript, encoding='utf-8')
            subprocess.run([node, '--check', str(path)], check=True)
        day = (datetime.now(ZoneInfo('Europe/Istanbul')).date() + timedelta(days=1)).isoformat()
        def add(name):
            r = requests.post(base + '/api/admin/routes', auth=auth, timeout=5,
                              json={'name':name,'origin':'Камп','destination':'Магазин','direction':'forward',
                                    'forward_time':'14:30','reverse_time':'14:30','capacity':50,'service_date':day})
            r.raise_for_status()
            return max(x['id'] for x in requests.get(base + '/api/admin/routes', auth=auth, timeout=5).json())
        first = add('Empty')
        assert requests.delete(base + f'/api/admin/routes/{first}', timeout=5).status_code == 401
        assert requests.delete(base + f'/api/admin/routes/{first}', auth=auth, timeout=5).status_code == 200
        assert all(x['id'] != first for x in requests.get(base + '/api/admin/routes', auth=auth, timeout=5).json())
        assert requests.get(base + '/api/trips', params={'date':day}, timeout=5).json() == []
        second = add('Booked')
        assert second > first
        book = requests.post(base + '/api/book', timeout=5,
                             json={'card':'123456','date':day,'forward':second,'direction':'forward'})
        book.raise_for_status()
        ticket = book.json()['url']
        denied = requests.delete(base + f'/api/admin/routes/{second}', auth=auth, timeout=5)
        assert denied.status_code == 409 and requests.get(f'http://127.0.0.1:{port}' + ticket, timeout=5).status_code == 200
        with sqlite3.connect(dbpath) as db:
            past = (datetime.now(ZoneInfo('Europe/Istanbul')).date() - timedelta(days=1)).isoformat()
            db.execute('UPDATE legs SET date=? WHERE route_id=?', (past, second))
            db.commit()
        assert requests.delete(base + f'/api/admin/routes/{second}', auth=auth, timeout=5).status_code == 200
        assert requests.get(f'http://127.0.0.1:{port}' + ticket, timeout=5).status_code == 200
        history = requests.get(base + '/api/admin/history', auth=auth, params={'route_id':second}, timeout=5).json()
        assert len(history) == 1
        options = requests.get(base + '/api/admin/route-options', auth=auth, timeout=5).json()
        assert any(x['id'] == second and x['deleted_at'] for x in options)
        assert requests.delete(base + f'/api/admin/routes/{second}', auth=auth, timeout=5).status_code == 404
        assert requests.post(base + '/api/book', timeout=5,
                             json={'card':'123456','date':day,'forward':second,'direction':'forward'}).status_code == 400
        third = add('Race')
        def make_booking():
            return requests.post(base + '/api/book', timeout=10,
                                 json={'card':'123456','date':day,'forward':third,'direction':'forward'}).status_code
        def remove_route():
            return requests.delete(base + f'/api/admin/routes/{third}', auth=auth, timeout=10).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1, f2 = pool.submit(make_booking), pool.submit(remove_route)
            result = (f1.result(), f2.result())
        assert result in ((200, 409), (400, 200)), result
        print('PASS: deletion, future booking protection, retained confirmation/history, stable IDs and race')
    finally:
        proc.terminate()
        proc.wait(timeout=10)
