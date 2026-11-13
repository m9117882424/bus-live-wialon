"""Exercise the PWA and public QR verification with an isolated running server."""
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import io,os,socket,sqlite3,subprocess,sys,tempfile,time
import requests,zxingcpp
from PIL import Image
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-scanner-http-') as temp:
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 dbpath=str(Path(temp)/'data.sqlite');env=os.environ.copy()
 env.pop('BUS_PUBLIC_URL',None);env.update(BUS_DB=dbpath,BUS_ADMIN_PASSWORD='test-only-password',BUS_FERNET_KEY=Fernet.generate_key().decode(),BUS_INITIAL_CARDS='123456')
 proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 host=f'http://127.0.0.1:{port}';base=host+'/bus';auth=('admin','test-only-password')
 try:
  for _ in range(100):
   try:
    if requests.get(base+'/',timeout=1).status_code==200:break
   except requests.RequestException:pass
   time.sleep(.1)
  else:raise RuntimeError('Test server did not start')
  page=requests.get(base+'/scanner/',timeout=5);page.raise_for_status();assert 'id="video"' in page.text and 'jsQR.js' in page.text
  manifest=requests.get(base+'/scanner/manifest.webmanifest',timeout=5).json();assert manifest['start_url']=='/bus/scanner/' and manifest['display']=='standalone'
  for size in (192,512):
   r=requests.get(base+f'/scanner/icon-{size}.png',timeout=5);r.raise_for_status();assert Image.open(io.BytesIO(r.content)).size==(size,size)
  assert requests.get(base+'/scanner/jsQR.js',timeout=5).status_code==200
  worker=requests.get(base+'/scanner/sw.js',timeout=5);worker.raise_for_status();assert 'fetch(e.request)' in worker.text
  current=datetime.now(ZoneInfo('Europe/Istanbul'));departure=current+timedelta(minutes=10);day=departure.date().isoformat();clock=departure.strftime('%H:%M')
  route=requests.post(base+'/api/admin/routes',auth=auth,json={'name':'Synthetic','origin':'Камп','destination':'Магазин','direction':'forward','forward_time':clock,'reverse_time':clock,'capacity':50,'service_date':day},timeout=5);route.raise_for_status()
  rid=requests.get(base+'/api/admin/routes',auth=auth,timeout=5).json()[0]['id']
  booking=requests.post(base+'/api/book',json={'card':'123456','date':day,'forward':rid,'direction':'forward'},timeout=5);booking.raise_for_status();token=booking.json()['url'].rsplit('/',1)[1]
  png=requests.get(host+booking.json()['url']+'/qr',timeout=10);png.raise_for_status();qr=zxingcpp.read_barcode(Image.open(io.BytesIO(png.content))).text
  def scan(value):
   r=requests.post(base+'/api/scanner/verify',json={'token':value},timeout=10);r.raise_for_status();return r.json()
  result=scan(qr);assert result['allowed'] and result['card']=='123456' and result['legs'][0]['origin']=='Камп'
  assert 'fortune' not in result and 'token' not in result
  assert requests.post(base+'/api/scanner/verify',headers={'Origin':'https://untrusted.example'},json={'token':qr},timeout=5).status_code==403
  assert not scan('not-valid')['allowed']
  def set_departure(offset):
   target=datetime.now(ZoneInfo('Europe/Istanbul'))+timedelta(minutes=offset)
   with sqlite3.connect(dbpath) as db:
    db.execute('UPDATE legs SET date=?,departure=? WHERE booking_id=(SELECT id FROM bookings WHERE token=?)',(target.date().isoformat(),target.strftime('%H:%M'),token))
    db.execute('UPDATE bookings SET expires=? WHERE token=?',((target+timedelta(minutes=30)).isoformat(),token));db.commit()
  set_departure(23);assert scan(qr)['reason']=='outside_window'
  set_departure(-23);assert scan(qr)['reason']=='outside_window'
  set_departure(10);r=requests.post(base+'/api/ticket/'+token+'/cancel',json={'confirm':True},timeout=5);r.raise_for_status()
  assert scan(qr)['reason']=='cancelled'
  print('PASS: public scanner HTTP, real QR, timing, cancellation, install manifest and assets')
 finally:proc.terminate();proc.wait(timeout=10)
