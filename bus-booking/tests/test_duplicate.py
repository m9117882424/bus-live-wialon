"""Active booking lookup, protection from duplicates and authorized cancellation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import os, shutil, socket, sqlite3, subprocess, sys, tempfile, time
import requests
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-duplicate-') as temp:
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
 dbpath=str(Path(temp)/'data.sqlite')
 env=os.environ.copy();env.update(BUS_DB=dbpath,BUS_ADMIN_PASSWORD='test-only-password',BUS_FERNET_KEY=Fernet.generate_key().decode(),BUS_INITIAL_CARDS='123456,654321')
 proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 host=f'http://127.0.0.1:{port}';base=host+'/bus';auth=('admin','test-only-password')
 try:
  for _ in range(100):
   try:
    if requests.get(base+'/',timeout=1).status_code==200:break
   except requests.RequestException:pass
   time.sleep(.1)
  else:raise RuntimeError('Test server did not start')
  home=requests.get(base+'/',timeout=5).text
  assert 'id="lookupCard"' in home and 'findBooking()' in home and 'ACTIVE_BOOKING_EXISTS' in home
  day=(datetime.now(ZoneInfo('Europe/Istanbul')).date()+timedelta(days=1)).isoformat()
  def add(name,direction,origin,destination,t):
   r=requests.post(base+'/api/admin/routes',auth=auth,json={'name':name,'direction':direction,'origin':origin,'destination':destination,'forward_time':t,'reverse_time':t,'capacity':50,'service_date':day},timeout=5);r.raise_for_status()
   return max(x['id'] for x in requests.get(base+'/api/admin/routes',auth=auth,timeout=5).json())
  out=add('Туда','forward','Камп','Магазин','14:30');alt=add('Туда 2','forward','Камп','Магазин','15:00');back=add('Обратно','reverse','Магазин','Камп','16:00')
  def book(rid,reverse=None):return requests.post(base+'/api/book',json={'card':'123456','date':day,'forward':rid,'reverse':reverse,'reverse_date':day,'direction':'forward'},timeout=5)
  def find(card='123456'):return requests.post(base+'/api/find-booking',json={'card':card},timeout=5)
  assert find().json()=={'items':[]}
  with ThreadPoolExecutor(max_workers=2) as pool:
   a=pool.submit(book,out);b=pool.submit(book,alt);responses=[a.result(),b.result()]
  assert sorted(r.status_code for r in responses)==[200,409]
  duplicate=next(r for r in responses if r.status_code==409).json()['detail'];assert duplicate['code']=='ACTIVE_BOOKING_EXISTS'
  successful=next(r for r in responses if r.status_code==200).json();token=successful['url'].rsplit('/',1)[1]
  items=find().json()['items'];assert len(items)==1 and token not in str(items) and items[0]['url'].startswith('/bus/check/')
  code=items[0]['code'];page=requests.get(host+items[0]['url'],timeout=5);page.raise_for_status();assert token not in page.text and '123456' in page.text
  if node:=shutil.which('node'):
   sample=Path(temp)/'check.html';sample.write_text(page.text,encoding='utf-8');subprocess.run([node,str(ROOT/'tests/test_duplicate_ui.js'),str(sample)],check=True)
   home_sample=Path(temp)/'home.html';home_sample.write_text(home,encoding='utf-8');subprocess.run([node,str(ROOT/'tests/test_duplicate_home.js'),str(home_sample)],check=True)
   for index,markup in enumerate((home,page.text)):
    js=markup.split('<script>',1)[1].split('</script>',1)[0];p=Path(temp)/f'script-{index}.js';p.write_text(js,encoding='utf-8');subprocess.run([node,'--check',str(p)],check=True)
  cancel=base+'/api/ticket/'+token+'/cancel-for-card'
  assert requests.post(cancel,json={'confirm':True,'card':'654321','code':code},timeout=5).status_code==404
  assert requests.post(cancel,json={'confirm':True,'card':'123456','code':'BUS-0000000000'},timeout=5).status_code==404
  assert len(find().json()['items'])==1
  assert requests.post(cancel,json={'confirm':True,'card':'123456','code':code},timeout=5).status_code==200
  assert not find().json()['items']
  trip=book(out,back);trip.raise_for_status();token2=trip.json()['url'].rsplit('/',1)[1]
  with sqlite3.connect(dbpath) as db:
   yesterday=(datetime.now(ZoneInfo('Europe/Istanbul')).date()-timedelta(days=1)).isoformat()
   db.execute('UPDATE legs SET date=? WHERE booking_id=(SELECT id FROM bookings WHERE token=?) AND route_id=?',(yesterday,token2,out));db.commit()
  assert book(alt).json()['detail']['code']=='ACTIVE_BOOKING_EXISTS'
  assert len(find().json()['items'])==1
  assert requests.post(base+'/api/ticket/'+token2+'/cancel',json={'confirm':True},timeout=5).status_code==200
  assert not find().json()['items']
  fresh=book(alt);fresh.raise_for_status();token3=fresh.json()['url'].rsplit('/',1)[1]
  with sqlite3.connect(dbpath) as db:
   db.execute('UPDATE legs SET date=? WHERE booking_id=(SELECT id FROM bookings WHERE token=?)',(yesterday,token3));db.commit()
  # The still-valid confirmation remains discoverable after departure, but no longer blocks a new trip.
  assert len(find().json()['items'])==1
  assert book(out).status_code==200
  assert len(find().json()['items'])==2  # A recent and a newly booked confirmation can coexist.
  print('PASS: atomic duplicate prevention, lookup, secure cancellation and completed-trip handling')
 finally:
  proc.terminate();proc.wait(timeout=10)
