"""Exercise preview and import of a 3000-row workbook on an isolated DB."""
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from cryptography.fernet import Fernet
from openpyxl import Workbook
import requests

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-excel-test-') as tmp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(BUS_DB=str(Path(tmp) / 'test.sqlite'), BUS_ADMIN_PASSWORD='test-only-password',
               BUS_FERNET_KEY=Fernet.generate_key().decode(), BUS_INITIAL_CARDS='999999')
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
        wb = Workbook()
        ws = wb.active
        ws.append(['Номер пропуска', 'Имя'])
        for i in range(3000):
            ws.append([200000 + i, 'test'])
        ws.append([200000, 'duplicate'])
        ws.append(['INVALID', 'invalid'])
        ws.append(['000123', 'text'])
        cell = ws.cell(row=3005, column=1, value=123)
        cell.number_format = '000000'
        buf = io.BytesIO()
        wb.save(buf)
        data = buf.getvalue()
        headers = {'Content-Type': 'application/octet-stream'}
        params = {'sheet': 0, 'column': 1, 'first_row': 2}
        assert requests.post(base + '/api/admin/whitelist/excel/inspect', data=data, headers=headers, timeout=5).status_code == 401
        r = requests.post(base + '/api/admin/whitelist/excel/inspect', data=data, headers=headers, auth=auth, timeout=10)
        r.raise_for_status()
        assert r.json()['sheets'][0]['columns'][0]['first_is_header']
        r = requests.post(base + '/api/admin/whitelist/excel/preview', data=data, headers=headers, auth=auth, params=params, timeout=10)
        r.raise_for_status()
        preview = r.json()
        assert (preview['valid_rows'], preview['unique'], preview['duplicates'], preview['invalid']) == (3003, 3001, 2, 1)
        r = requests.post(base + '/api/admin/whitelist/excel/import', data=data, headers=headers, auth=auth,
                          params={**params, 'mode': 'append'}, timeout=10)
        r.raise_for_status()
        cards = requests.get(base + '/api/admin/whitelist', auth=auth, timeout=5).json()['cards']
        assert len(cards) == 3002 and '999999' in cards and '000123' in cards
        r = requests.post(base + '/api/admin/whitelist/excel/import', data=data, headers=headers, auth=auth,
                          params={**params, 'mode': 'replace'}, timeout=10)
        r.raise_for_status()
        assert len(requests.get(base + '/api/admin/whitelist', auth=auth, timeout=5).json()['cards']) == 3001
        assert requests.post(base + '/api/admin/whitelist/excel/import', data=b'not-excel', headers=headers,
                             auth=auth, params=params, timeout=5).status_code == 400
        print('PASS: Excel preview, 3000 rows, duplicates, invalid entries, leading zeroes, append and replace')
    finally:
        proc.terminate()
        proc.wait(timeout=10)
