"""Check scanner validity, exact time boundaries, old QR compatibility and PWA assets."""
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo
import json,os,shutil,subprocess,sys,tempfile
import qrcode
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-scanner-') as temp:
 key=Fernet.generate_key().decode()
 os.environ.update(BUS_DB=str(Path(temp)/'db.sqlite'),BUS_ADMIN_PASSWORD='test-only-password',BUS_FERNET_KEY=key,
                   BUS_PUBLIC_URL='https://bus.example.test',BUS_QR_ALLOWED_ORIGINS='https://old.example.test')
 sys.path.insert(0,str(ROOT));import app
 app.init();tz=ZoneInfo('Europe/Istanbul');departure=datetime(2026,11,14,14,30,tzinfo=tz);request=SimpleNamespace(base_url='https://bus.example.test/')
 with app.db() as db:
  bid=db.execute('INSERT INTO bookings(code,card,fortune,token,expires,created,lang) VALUES(?,?,?,?,?,?,?)',('BUS-ABCDEF0000','123456',app.FORTUNES[0],'private-token-fixture',(departure+timedelta(minutes=60)).isoformat(),departure.isoformat(),'ru')).lastrowid
  for rid,direction,clock,origin,dest in [(1,'forward','14:30','Камп','Магазин'),(2,'reverse','15:00','Магазин','Камп')]:
   db.execute('INSERT INTO legs(booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(bid,rid,direction,'2026-11-14',clock,origin,dest,1,50,'Рейс','123456',''))
  db.commit(); booking=dict(db.execute('SELECT * FROM bookings WHERE id=?',(bid,)).fetchone());legs=[dict(x) for x in db.execute('SELECT * FROM legs WHERE booking_id=? ORDER BY date,departure',(bid,))]
 signed=app.qr_url(request,booking);legacy=Fernet(key.encode()).encrypt(json.dumps(app.qrdata(booking,legs),ensure_ascii=False,separators=(',',':')).encode()).decode()
 def scan(value,at):
  with patch.object(app,'now',return_value=at):return app.scanner_verify(app.Verify(token=value),request)
 assert scan(signed,departure-timedelta(minutes=20,seconds=1))['reason']=='outside_window'
 start=scan(signed,departure-timedelta(minutes=20));assert start['allowed'] and start['card']=='123456' and len(start['legs'])==1
 assert start['legs'][0]['origin_tr']=='Kamp'
 overlap=scan(signed,departure+timedelta(minutes=20));assert overlap['allowed'] and len(overlap['legs'])==2
 assert scan(legacy,departure+timedelta(minutes=20))['allowed']
 assert scan(signed.replace('https://bus.example.test','https://old.example.test'),departure)['allowed']
 assert scan(signed.replace('https://bus.example.test','https://bad.example.test'),departure)['reason']=='invalid'
 assert scan(signed[:-1]+('A' if signed[-1]!='A' else 'B'),departure)['reason']=='invalid'
 assert scan('not-a-qr',departure)['reason']=='invalid'
 assert scan(signed,departure+timedelta(minutes=50))['allowed']
 assert scan(signed,departure+timedelta(minutes=50,seconds=1))['reason']=='outside_window'
 with app.db() as db:db.execute('UPDATE bookings SET cancelled_at=? WHERE id=?',(departure.isoformat(),bid));db.commit()
 assert scan(signed,departure)['reason']=='cancelled'
 with app.db() as db:db.execute('UPDATE bookings SET cancelled_at=NULL WHERE id=?',(bid,));db.commit()
 assert scan(signed,departure+timedelta(minutes=60))['reason']=='expired'
 assert app.scanner_manifest().status_code==200 and b'192x192' in app.scanner_manifest().body and b'512x512' in app.scanner_manifest().body
 assert app.scanner_icon(192).media_type=='image/png' and app.scanner_library().media_type=='text/javascript'
 assert app.scanner_worker().media_type=='text/javascript' and 'serviceWorker' in app.scanner_page().body.decode()
 if node:=shutil.which('node'):
  subprocess.run([node,str(ROOT/'tests/test_scanner_ui.js'),str(ROOT/'static/scanner.html')],check=True)
  image=qrcode.make(signed).convert('RGBA');size=image.width;raw=Path(temp)/'qr.rgba';raw.write_bytes(image.tobytes())
  subprocess.run([node,str(ROOT/'tests/test_scanner_decode.js'),str(raw),str(size),signed],check=True)
 print('PASS: scanner signed and legacy QR, both legs, exact 20-minute windows, cancellation, expiry and PWA')
