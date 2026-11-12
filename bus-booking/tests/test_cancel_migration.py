"""Upgrade a pre-cancellation database without losing an existing reservation."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

from cryptography.fernet import Fernet
import requests

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-cancel-migration-') as tmp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    dbpath=str(Path(tmp)/'legacy.sqlite')
    current=datetime.now(ZoneInfo('Europe/Istanbul'))
    day=(current.date()+timedelta(days=1)).isoformat()
    expiry=(current+timedelta(days=2)).isoformat()
    token='synthetic-legacy-link'
    with sqlite3.connect(dbpath) as c:
        c.executescript('''
CREATE TABLE routes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, forward_time TEXT NOT NULL, reverse_time TEXT NOT NULL, capacity INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, deleted_at TEXT, direction TEXT NOT NULL DEFAULT 'both', service_date TEXT NOT NULL DEFAULT '', comment TEXT NOT NULL DEFAULT '');
CREATE TABLE bookings(id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, card TEXT NOT NULL, fortune TEXT NOT NULL, token TEXT UNIQUE, expires TEXT NOT NULL, created TEXT NOT NULL, lang TEXT NOT NULL);
CREATE TABLE legs(id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE, route_id INTEGER NOT NULL, direction TEXT NOT NULL, date TEXT NOT NULL, departure TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, sequence INTEGER NOT NULL, capacity INTEGER NOT NULL, route_name TEXT NOT NULL, card TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', UNIQUE(route_id,direction,date,card), UNIQUE(route_id,direction,date,sequence));
CREATE TABLE allowed_cards(card TEXT PRIMARY KEY);
''')
        c.execute('INSERT INTO routes(id,name,origin,destination,forward_time,reverse_time,capacity,direction,service_date) VALUES(1,?,?,?,?,?,?,?,?)',('Туда','Камп','Магазин','14:30','14:30',50,'forward',day))
        c.execute('INSERT INTO bookings(id,code,card,fortune,token,expires,created,lang) VALUES(1,?,?,?,?,?,?,?)',('BUS-ABCDEF0000','123456','Пусть дорога будет лёгкой!',token,expiry,current.isoformat(),'ru'))
        c.execute('INSERT INTO legs(id,booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment) VALUES(1,1,1,?,?,?,?,?,?,?,?,?,?)',('forward',day,'14:30','Камп','Магазин',1,50,'Туда','123456',''))
        c.execute('INSERT INTO allowed_cards(card) VALUES(?)',('123456',));c.commit()
    key=Fernet.generate_key().decode()
    env=os.environ.copy();env.update(BUS_DB=dbpath,BUS_ADMIN_PASSWORD='test-only-password',BUS_FERNET_KEY=key)
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    base=f'http://127.0.0.1:{port}/bus';auth=('admin','test-only-password')
    try:
        for _ in range(100):
            try:
                if requests.get(base+'/',timeout=1).status_code==200:break
            except requests.RequestException:pass
            time.sleep(.1)
        else:raise RuntimeError('Test server did not start')
        ticket=requests.get(base+'/ticket/'+token,timeout=5);ticket.raise_for_status()
        assert 'BUS-ABCDEF0000' in ticket.text and '"can_cancel": true' in ticket.text
        assert requests.get(base+'/api/admin/whitelist',auth=auth,timeout=5).json()['cards']==['123456']
        payload={'booking':'BUS-ABCDEF0000','card':'123456','fortune':'Пусть дорога будет лёгкой!',
                 'legs':[{'date':day,'time':'14:30','number':1,'capacity':50}]}
        encoded=Fernet(key).encrypt(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()).decode()
        assert requests.post(base+'/api/admin/verify',auth=auth,json={'token':encoded},timeout=5).status_code==200
        r=requests.post(base+'/api/ticket/'+token+'/cancel',json={'confirm':True},timeout=5);r.raise_for_status()
        assert requests.post(base+'/api/admin/verify',auth=auth,json={'token':encoded},timeout=5).status_code==410
        assert len(requests.get(base+'/api/admin/history',auth=auth,params={'status':'cancelled'},timeout=5).json())==1
        print('PASS: legacy database migration, original confirmation and QR, cancellation and history')
    finally:
        proc.terminate();proc.wait(timeout=10)
